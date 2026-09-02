from __future__ import annotations

import pytest

from core.settings import AppSettings, load_settings

SETTING_NAMES = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "MATCH_THRESHOLD",
    "VISION_WORKERS",
    "MIN_RECOGNITION_CONFIDENCE",
)


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.settings.load_dotenv", lambda *_args, **_kwargs: False)
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_environment_overrides_secrets_and_repr_redacts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "environment-key")

    settings = load_settings({"GEMINI_API_KEY": "secret-key"})

    assert settings.gemini_api_key == "environment-key"
    assert "environment-key" not in repr(settings)


def test_defaults_do_not_invent_secrets() -> None:
    settings = load_settings({})

    assert settings.gemini_api_key == ""


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MATCH_THRESHOLD", "49"),
        ("MATCH_THRESHOLD", "nan"),
        ("VISION_WORKERS", "0"),
        ("VISION_WORKERS", "1.5"),
        ("MIN_RECOGNITION_CONFIDENCE", "inf"),
        ("GEMINI_MODEL", "../../other-model"),
    ],
)
def test_invalid_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises((ValueError, OverflowError)):
        load_settings({})


def test_app_settings_secret_fields_are_not_in_repr() -> None:
    settings = AppSettings(gemini_api_key="top-secret-key")
    assert "top-secret" not in repr(settings)
