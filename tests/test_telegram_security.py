import asyncio
import time
from pathlib import Path

import pandas as pd
import pytest

from deploy.telegram_bot import (
    EXIT_CATALOG_ERROR,
    EXIT_CONFIG_ERROR,
    ActiveSessionStore,
    BatchRateLimiter,
    BotConfigurationError,
    PhotoAdmissionGate,
    SessionLimitError,
    format_price_index,
    is_authorized,
    is_private_chat,
    load_bot_config,
    parse_allowed_user_ids,
    run_bot,
)


def test_parse_allowed_user_ids_accepts_supported_separators_and_deduplicates():
    assert parse_allowed_user_ids("123, 456;789\n123") == {123, 456, 789}


@pytest.mark.parametrize(
    "raw",
    [None, "", "  ", "0", "-1", "1,two", "1.5", True, str(2**63)],
)
def test_parse_allowed_user_ids_rejects_empty_or_invalid_values(raw):
    with pytest.raises(BotConfigurationError):
        parse_allowed_user_ids(raw)


def test_is_authorized_requires_exact_positive_integer_membership():
    allowed = frozenset({123, 456})

    assert is_authorized(123, allowed)
    assert not is_authorized(789, allowed)
    assert not is_authorized("123", allowed)
    assert not is_authorized(True, allowed)
    assert not is_authorized(None, allowed)


@pytest.mark.parametrize("chat_type", [None, "", "group", "supergroup", "channel"])
def test_private_chat_enforcement_rejects_non_private_chat_types(chat_type):
    assert not is_private_chat(chat_type)


def test_private_chat_enforcement_accepts_private_case_insensitively():
    class EnumLikePrivate:
        value = "private"

    assert is_private_chat("private")
    assert is_private_chat("PRIVATE")
    assert is_private_chat(EnumLikePrivate())


@pytest.mark.parametrize(
    "missing_name",
    ["TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "SAMBERI_CATALOG_PATH"],
)
def test_load_bot_config_fails_closed_when_required_value_is_missing(
    tmp_path: Path, missing_name: str
):
    catalog = tmp_path / "catalog.xlsx"
    catalog.touch()
    environ = {
        "TELEGRAM_BOT_TOKEN": "token",
        "GEMINI_API_KEY": "key",
        "SAMBERI_CATALOG_PATH": str(catalog),
        "TELEGRAM_ALLOWED_USER_IDS": "123",
    }
    del environ[missing_name]

    with pytest.raises(BotConfigurationError):
        load_bot_config(environ)


def test_load_bot_config_requires_allowlist(tmp_path: Path):
    catalog = tmp_path / "catalog.xlsx"
    catalog.touch()

    with pytest.raises(BotConfigurationError):
        load_bot_config(
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "GEMINI_API_KEY": "key",
                "SAMBERI_CATALOG_PATH": str(catalog),
            }
        )


@pytest.mark.parametrize(
    "suffix",
    [".csv", ".tsv", ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm", ".ods"],
)
def test_load_bot_config_accepts_explicit_catalog_and_allowlist(tmp_path: Path, suffix: str):
    catalog = tmp_path / f"catalog{suffix}"
    catalog.touch()

    config = load_bot_config(
        {
            "TELEGRAM_BOT_TOKEN": "token",
            "GEMINI_API_KEY": "key",
            "SAMBERI_CATALOG_PATH": str(catalog),
            "TELEGRAM_ALLOWED_USER_IDS": "123,456",
        }
    )

    assert config.catalog_path == catalog.resolve()
    assert config.allowed_user_ids == {123, 456}


def test_session_store_expires_idle_session_and_invalidates_inflight_upload():
    clock = [0.0]
    store = ActiveSessionStore(
        ttl_seconds=10,
        max_photos_per_user=3,
        max_photo_bytes=10,
        max_session_bytes=20,
        max_total_bytes=30,
        clock=lambda: clock[0],
    )
    initial_version = store.version(101)
    store.add_photo(101, {"data": b"12345"})

    clock[0] = 9.9
    assert store.purge_expired() == set()
    assert store.total_bytes == 5

    clock[0] = 10.0
    assert store.purge_expired() == {101}
    assert store.photo_count(101) == 0
    assert store.total_bytes == 0
    assert store.version(101) > initial_version
    with pytest.raises(SessionLimitError, match="session_closed"):
        store.add_photo(
            101,
            {"data": b"1"},
            expected_version=initial_version,
        )


def test_global_byte_budget_includes_batch_while_it_is_processing():
    store = ActiveSessionStore(
        ttl_seconds=60,
        max_photos_per_user=3,
        max_photo_bytes=10,
        max_session_bytes=10,
        max_total_bytes=10,
    )
    store.add_photo(101, {"data": b"123456"})
    photos = store.take_for_processing(101)

    assert photos == [{"data": b"123456"}]
    assert store.total_bytes == 6
    assert store.purge_expired(now=time.monotonic() + 1_000) == set()
    assert store.total_bytes == 6
    with pytest.raises(SessionLimitError, match="global_bytes"):
        store.add_photo(202, {"data": b"12345"})

    assert store.release_processing(101)
    assert store.total_bytes == 0
    assert store.add_photo(202, {"data": b"12345"}) == 1


def test_photo_admission_gate_serializes_each_user_and_bounds_global_work():
    async def exercise_gate() -> tuple[int, bool]:
        gate = PhotoAdmissionGate(max_concurrent=2)
        active_total = 0
        max_active_total = 0
        active_users: set[int] = set()
        same_user_overlap = False

        async def worker(user_id: int) -> None:
            nonlocal active_total, max_active_total, same_user_overlap
            async with gate.slot(user_id):
                if user_id in active_users:
                    same_user_overlap = True
                active_users.add(user_id)
                active_total += 1
                max_active_total = max(max_active_total, active_total)
                await asyncio.sleep(0.01)
                active_total -= 1
                active_users.remove(user_id)

        await asyncio.gather(worker(101), worker(101), worker(202), worker(303))
        return max_active_total, same_user_overlap

    max_active_total, same_user_overlap = asyncio.run(exercise_gate())

    assert max_active_total == 2
    assert not same_user_overlap


def test_batch_rate_limiter_is_per_user_and_enforces_interval_and_window():
    limiter = BatchRateLimiter(
        max_batches=2,
        window_seconds=60,
        min_interval_seconds=10,
    )

    assert limiter.acquire(101, now=0).allowed
    too_fast = limiter.acquire(101, now=5)
    assert not too_fast.allowed
    assert too_fast.retry_after_seconds == 5
    assert limiter.acquire(101, now=10).allowed

    quota_reached = limiter.check(101, now=20)
    assert not quota_reached.allowed
    assert quota_reached.retry_after_seconds == 40
    assert limiter.acquire(202, now=20).allowed
    assert limiter.acquire(101, now=61).allowed


def test_run_bot_returns_nonzero_for_missing_required_configuration():
    assert run_bot({}) == EXIT_CONFIG_ERROR


def test_run_bot_returns_nonzero_for_invalid_catalog_without_network(tmp_path: Path):
    invalid_catalog = tmp_path / "catalog.xlsx"
    invalid_catalog.touch()

    exit_code = run_bot(
        {
            "TELEGRAM_BOT_TOKEN": "token",
            "GEMINI_API_KEY": "key",
            "SAMBERI_CATALOG_PATH": str(invalid_catalog),
            "TELEGRAM_ALLOWED_USER_IDS": "123",
        }
    )

    assert exit_code == EXIT_CATALOG_ERROR


def test_run_bot_rejects_valid_xlsx_with_wrong_catalog_schema(tmp_path: Path):
    invalid_catalog = tmp_path / "wrong-schema.xlsx"
    pd.DataFrame({"неизвестная колонка": ["товар"]}).to_excel(
        invalid_catalog,
        index=False,
    )

    exit_code = run_bot(
        {
            "TELEGRAM_BOT_TOKEN": "token",
            "GEMINI_API_KEY": "key",
            "SAMBERI_CATALOG_PATH": str(invalid_catalog),
            "TELEGRAM_ALLOWED_USER_IDS": "123",
        }
    )

    assert exit_code == EXIT_CATALOG_ERROR


@pytest.mark.parametrize("value", [None, "", float("nan"), float("inf")])
def test_format_price_index_never_renders_none_percent(value):
    rendered = format_price_index(value)

    assert rendered == "нет данных"
    assert "None%" not in rendered


def test_format_price_index_renders_number():
    assert format_price_index(101.25) == "101.2%"
