"""Безопасный Telegram-бот для мобильного мониторинга ценников «Самбери»."""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import re
import sys
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Collection, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.analytics import summarize_price_index  # noqa: E402
from core.exporter import export_comparison_to_excel  # noqa: E402
from core.input_validation import (  # noqa: E402
    MAX_IMAGE_BYTES,
    MAX_TOTAL_IMAGE_BYTES,
    InputValidationError,
    load_catalog_file,
    normalize_image,
)
from core.matcher import (  # noqa: E402
    DEFAULT_MATCH_THRESHOLD,
    CatalogMatcher,
    CatalogSchemaError,
)
from core.pipeline import process_monitoring_batch  # noqa: E402
from core.vision_extractor import (  # noqa: E402
    DEFAULT_GEMINI_MODEL,
    PriceTagExtractor,
)

LOGGER = logging.getLogger(__name__)

MAX_SESSION_PHOTOS = 50
MAX_PHOTO_BYTES = min(MAX_IMAGE_BYTES, 12 * 1024 * 1024)
SESSION_TTL_SECONDS = 30 * 60
MAX_ACTIVE_SESSION_BYTES = 256 * 1024 * 1024
MAX_CONCURRENT_BATCHES = 2
MAX_BATCHES_PER_USER_WINDOW = 6
BATCH_QUOTA_WINDOW_SECONDS = 15 * 60
MIN_BATCH_INTERVAL_SECONDS = 30
SESSION_CLEANUP_INTERVAL_SECONDS = 60
PHOTO_INGEST_CONCURRENCY = 4
MAX_CONCURRENT_UPDATES = 16
VISION_WORKERS = 4
MIN_RECOGNITION_CONFIDENCE = 0.55

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_CATALOG_ERROR = 3
EXIT_DEPENDENCY_ERROR = 4
EXIT_RUNTIME_ERROR = 5


class BotConfigurationError(ValueError):
    """Конфигурация Telegram-бота неполна или небезопасна."""


class SessionLimitError(RuntimeError):
    """Добавление фото нарушило один из лимитов активных сессий."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BotConfig:
    token: str = field(repr=False)
    gemini_api_key: str = field(repr=False)
    catalog_path: Path
    allowed_user_ids: frozenset[int]
    gemini_model: str = DEFAULT_GEMINI_MODEL


@dataclass
class _PhotoSession:
    photos: list[dict[str, Any]]
    byte_count: int
    updated_at: float


class ActiveSessionStore:
    """Хранилище фото с TTL и единым бюджетом памяти.

    Пачка, переданная в обработку, остаётся зарезервированной в общем бюджете
    до ``release_processing``. Все методы синхронны и атомарны относительно
    одного asyncio event loop.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        max_photos_per_user: int = MAX_SESSION_PHOTOS,
        max_photo_bytes: int = MAX_PHOTO_BYTES,
        max_session_bytes: int = MAX_TOTAL_IMAGE_BYTES,
        max_total_bytes: int = MAX_ACTIVE_SESSION_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        limits = (
            ttl_seconds,
            max_photos_per_user,
            max_photo_bytes,
            max_session_bytes,
            max_total_bytes,
        )
        if any(limit <= 0 for limit in limits):
            raise ValueError("Лимиты сессий должны быть положительными.")
        if max_session_bytes > max_total_bytes:
            raise ValueError("Лимит одной сессии не может превышать общий лимит.")

        self.ttl_seconds = float(ttl_seconds)
        self.max_photos_per_user = int(max_photos_per_user)
        self.max_photo_bytes = int(max_photo_bytes)
        self.max_session_bytes = int(max_session_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self._clock = clock
        self._pending: dict[int, _PhotoSession] = {}
        self._processing: dict[int, _PhotoSession] = {}
        self._versions: dict[int, int] = {}
        self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def version(self, user_id: int) -> int:
        return self._versions.get(user_id, 0)

    def _bump_version(self, user_id: int) -> None:
        self._versions[user_id] = self.version(user_id) + 1

    def purge_expired(self, *, now: float | None = None) -> set[int]:
        """Удалить только ожидающие, но не обрабатываемые пачки."""

        current_time = self._clock() if now is None else float(now)
        expired = {
            user_id
            for user_id, session in self._pending.items()
            if current_time - session.updated_at >= self.ttl_seconds
        }
        for user_id in expired:
            session = self._pending.pop(user_id)
            self._total_bytes -= session.byte_count
            self._bump_version(user_id)
        return expired

    def photo_count(self, user_id: int, *, now: float | None = None) -> int:
        self.purge_expired(now=now)
        session = self._pending.get(user_id)
        return len(session.photos) if session else 0

    def add_photo(
        self,
        user_id: int,
        photo: dict[str, Any],
        *,
        expected_version: int | None = None,
        now: float | None = None,
    ) -> int:
        """Добавить нормализованное фото либо выдать стабильный код лимита."""

        current_time = self._clock() if now is None else float(now)
        self.purge_expired(now=current_time)
        if expected_version is not None and self.version(user_id) != expected_version:
            raise SessionLimitError("session_closed")
        if user_id in self._processing:
            raise SessionLimitError("session_processing")

        data = photo.get("data")
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("Фото должно содержать байты.")
        safe_data = bytes(data)
        photo_bytes = len(safe_data)
        if photo_bytes <= 0 or photo_bytes > self.max_photo_bytes:
            raise SessionLimitError("photo_bytes")

        session = self._pending.get(user_id)
        current_count = len(session.photos) if session else 0
        current_bytes = session.byte_count if session else 0
        if current_count >= self.max_photos_per_user:
            raise SessionLimitError("photo_count")
        if current_bytes + photo_bytes > self.max_session_bytes:
            raise SessionLimitError("session_bytes")
        if self._total_bytes + photo_bytes > self.max_total_bytes:
            raise SessionLimitError("global_bytes")

        if session is None:
            session = _PhotoSession([], 0, current_time)
            self._pending[user_id] = session
        session.photos.append({**photo, "data": safe_data})
        session.byte_count += photo_bytes
        session.updated_at = current_time
        self._total_bytes += photo_bytes
        return len(session.photos)

    def take_for_processing(
        self,
        user_id: int,
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Передать пачку worker-у, сохранив её байты в глобальном бюджете."""

        self.purge_expired(now=now)
        if user_id in self._processing:
            raise SessionLimitError("session_processing")
        session = self._pending.pop(user_id, None)
        if session is None:
            return []
        self._processing[user_id] = session
        self._bump_version(user_id)
        return session.photos

    def cancel_pending(self, user_id: int, *, now: float | None = None) -> bool:
        self.purge_expired(now=now)
        session = self._pending.pop(user_id, None)
        if session is not None:
            self._total_bytes -= session.byte_count
        self._bump_version(user_id)
        return session is not None

    def release_processing(self, user_id: int) -> bool:
        session = self._processing.pop(user_id, None)
        if session is None:
            return False
        self._total_bytes -= session.byte_count
        return True


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    retry_after_seconds: int = 0


class BatchRateLimiter:
    """Скользящая per-user квота запусков Gemini batch."""

    def __init__(
        self,
        *,
        max_batches: int = MAX_BATCHES_PER_USER_WINDOW,
        window_seconds: float = BATCH_QUOTA_WINDOW_SECONDS,
        min_interval_seconds: float = MIN_BATCH_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            max_batches <= 0
            or window_seconds <= 0
            or min_interval_seconds < 0
            or min_interval_seconds > window_seconds
        ):
            raise ValueError("Параметры квоты имеют неверный диапазон.")
        self.max_batches = int(max_batches)
        self.window_seconds = float(window_seconds)
        self.min_interval_seconds = float(min_interval_seconds)
        self._clock = clock
        self._events: dict[int, deque[float]] = {}

    def _recent_events(self, user_id: int, now: float) -> deque[float]:
        events = self._events.setdefault(user_id, deque())
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            self._events.pop(user_id, None)
            return deque()
        return events

    def check(self, user_id: int, *, now: float | None = None) -> QuotaDecision:
        current_time = self._clock() if now is None else float(now)
        events = self._recent_events(user_id, current_time)
        if events and current_time - events[-1] < self.min_interval_seconds:
            retry = math.ceil(self.min_interval_seconds - (current_time - events[-1]))
            return QuotaDecision(False, max(1, retry))
        if len(events) >= self.max_batches:
            retry = math.ceil(events[0] + self.window_seconds - current_time)
            return QuotaDecision(False, max(1, retry))
        return QuotaDecision(True)

    def acquire(self, user_id: int, *, now: float | None = None) -> QuotaDecision:
        current_time = self._clock() if now is None else float(now)
        decision = self.check(user_id, now=current_time)
        if decision.allowed:
            self._events.setdefault(user_id, deque()).append(current_time)
        return decision


class PhotoAdmissionGate:
    """Сериализовать загрузки пользователя и ограничить общий ingest burst."""

    def __init__(self, max_concurrent: int = PHOTO_INGEST_CONCURRENCY) -> None:
        if max_concurrent <= 0:
            raise ValueError("Лимит параллельной загрузки должен быть положительным.")
        self._global_slots = asyncio.Semaphore(max_concurrent)
        self._user_locks: dict[int, asyncio.Lock] = {}

    @asynccontextmanager
    async def slot(self, user_id: int) -> AsyncIterator[None]:
        user_lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with user_lock:
            async with self._global_slots:
                yield


def parse_allowed_user_ids(raw_value: str | None) -> frozenset[int]:
    """Разобрать обязательный allowlist Telegram user ID.

    Поддерживаются значения, разделённые запятыми, точками с запятой или
    пробелами. Любой некорректный элемент делает всю конфигурацию невалидной.
    """

    if not isinstance(raw_value, str) or not raw_value.strip():
        raise BotConfigurationError("TELEGRAM_ALLOWED_USER_IDS не задан.")

    parts = [part for part in re.split(r"[\s,;]+", raw_value.strip()) if part]
    if not parts or any(not re.fullmatch(r"[1-9]\d*", part) for part in parts):
        raise BotConfigurationError(
            "TELEGRAM_ALLOWED_USER_IDS должен содержать положительные целые ID."
        )

    try:
        allowed = frozenset(int(part) for part in parts)
    except ValueError as exc:
        raise BotConfigurationError(
            "TELEGRAM_ALLOWED_USER_IDS содержит слишком большой ID."
        ) from exc
    if any(user_id > 2**63 - 1 for user_id in allowed):
        raise BotConfigurationError("Telegram user ID находится вне допустимого диапазона.")
    if not allowed:
        raise BotConfigurationError("Allowlist Telegram не может быть пустым.")
    return allowed


def is_authorized(user_id: object, allowed_user_ids: Collection[int]) -> bool:
    """Проверить user ID без неявного приведения строк и bool к int."""

    return (
        isinstance(user_id, int)
        and not isinstance(user_id, bool)
        and user_id > 0
        and user_id in allowed_user_ids
    )


def is_private_chat(chat_type: object) -> bool:
    """Разрешать ценники только из личного диалога с ботом."""

    value = getattr(chat_type, "value", chat_type)
    return isinstance(value, str) and value.casefold() == "private"


def format_price_index(value: object) -> str:
    """Отформатировать PI, не превращая отсутствие данных в ``None%``."""

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "нет данных"
    if not math.isfinite(number):
        return "нет данных"
    return f"{number:.1f}%"


def load_bot_config(environ: Mapping[str, str] | None = None) -> BotConfig:
    """Загрузить обязательную конфигурацию; небезопасных defaults нет."""

    values = os.environ if environ is None else environ

    token = str(values.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    api_key = str(values.get("GEMINI_API_KEY", "") or "").strip()
    catalog_value = str(values.get("SAMBERI_CATALOG_PATH", "") or "").strip()
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("GEMINI_API_KEY", api_key),
            ("SAMBERI_CATALOG_PATH", catalog_value),
        )
        if not value
    ]
    if missing:
        raise BotConfigurationError(
            "Не заданы обязательные переменные: " + ", ".join(missing) + "."
        )

    allowed_user_ids = parse_allowed_user_ids(values.get("TELEGRAM_ALLOWED_USER_IDS"))

    catalog_path = Path(catalog_value).expanduser()
    try:
        catalog_path = catalog_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise BotConfigurationError("SAMBERI_CATALOG_PATH не найден.") from exc
    if not catalog_path.is_file() or catalog_path.suffix.lower() not in {".csv", ".xlsx"}:
        raise BotConfigurationError("SAMBERI_CATALOG_PATH должен указывать на файл CSV или XLSX.")

    model = str(values.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL) or "").strip()
    if not re.fullmatch(r"gemini-[a-z0-9.-]{1,80}", model):
        raise BotConfigurationError("GEMINI_MODEL имеет неверный формат.")

    return BotConfig(
        token=token,
        gemini_api_key=api_key,
        catalog_path=catalog_path,
        allowed_user_ids=allowed_user_ids,
        gemini_model=model,
    )


def _build_report(
    catalog: Any,
    photos: list[dict[str, Any]],
    config: BotConfig,
) -> tuple[dict[str, Any], bytes]:
    """Синхронная тяжёлая часть, предназначенная для ``asyncio.to_thread``."""

    extractor = PriceTagExtractor(
        provider="gemini",
        api_key=config.gemini_api_key,
        model_name=config.gemini_model,
    )
    processed = process_monitoring_batch(
        catalog,
        photos,
        extractor,
        match_threshold=DEFAULT_MATCH_THRESHOLD,
        min_confidence=MIN_RECOGNITION_CONFIDENCE,
        max_workers=VISION_WORKERS,
    )
    summary = summarize_price_index(processed)
    excel_bytes = export_comparison_to_excel(
        processed,
        competitor_name="Конкурент",
    )
    return summary, excel_bytes


def run_bot(environ: Mapping[str, str] | None = None) -> int:
    """Проверить конфигурацию, запустить polling и вернуть process exit code."""

    if environ is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        config = load_bot_config(environ)
    except BotConfigurationError as exc:
        LOGGER.error("Telegram-бот не запущен: %s", exc)
        return EXIT_CONFIG_ERROR

    try:
        with config.catalog_path.open("rb") as source:
            catalog = load_catalog_file(source, config.catalog_path.name)
        CatalogMatcher(catalog)
    except (CatalogSchemaError, InputValidationError, OSError, ValueError):
        LOGGER.error("Telegram-бот не запущен: каталог не прошёл безопасную проверку.")
        return EXIT_CATALOG_ERROR

    try:
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError:
        LOGGER.error("Telegram-бот не запущен: установите зависимость python-telegram-bot.")
        return EXIT_DEPENDENCY_ERROR

    session_store = ActiveSessionStore()
    batch_limiter = BatchRateLimiter()
    processing_users: set[int] = set()
    cancelled_users: set[int] = set()
    pipeline_slots = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)
    photo_admission_gate = PhotoAdmissionGate()
    cleanup_task: asyncio.Task[None] | None = None

    async def cleanup_expired_sessions() -> None:
        while True:
            await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
            expired = session_store.purge_expired()
            if expired:
                LOGGER.info("Удалено просроченных Telegram-сессий: %d.", len(expired))

    async def post_init(application: Any) -> None:
        nonlocal cleanup_task
        cleanup_task = application.create_task(
            cleanup_expired_sessions(),
            name="telegram-session-ttl-cleanup",
        )

    async def post_shutdown(application: Any) -> None:
        del application
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

    async def require_authorized(update: Update) -> int | None:
        message = update.effective_message
        chat_type = getattr(update.effective_chat, "type", None)
        if not is_private_chat(chat_type):
            if message is not None:
                await message.reply_text("Бот работает только в личном чате.")
            LOGGER.warning("Отклонён запрос не из личного Telegram-чата.")
            return None

        user = update.effective_user
        user_id = getattr(user, "id", None)
        if is_authorized(user_id, config.allowed_user_ids):
            return user_id

        if message is not None:
            await message.reply_text("⛔ Доступ к боту запрещён.")
        LOGGER.warning("Отклонён запрос пользователя вне allowlist.")
        return None

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if await require_authorized(update) is None:
            return
        message = update.effective_message
        if message is None:
            return
        await message.reply_text(
            "🛒 *Самбери: Бот мониторинга ценников*\n\n"
            "Отправляйте фотографии ценников конкурентов (до 50 фото, не более "
            "12 МБ каждое). После загрузки используйте /finish. Команда /cancel "
            "очистит текущую сессию.",
            parse_mode="Markdown",
        )

    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        user_id = await require_authorized(update)
        message = update.effective_message
        if user_id is None or message is None:
            return

        async with photo_admission_gate.slot(user_id):
            if user_id in processing_users:
                await message.reply_text(
                    "⏳ Предыдущая пачка уже обрабатывается. Дождитесь результата "
                    "или используйте /cancel."
                )
                return

            if session_store.photo_count(user_id) >= MAX_SESSION_PHOTOS:
                await message.reply_text("Лимит сессии — 50 фото. Используйте /finish или /cancel.")
                return
            if not message.photo:
                return

            session_version = session_store.version(user_id)
            telegram_photo = message.photo[-1]
            declared_size = getattr(telegram_photo, "file_size", None)
            if declared_size is not None and declared_size > MAX_PHOTO_BYTES:
                await message.reply_text("Фото отклонено: размер превышает 12 МБ.")
                return

            if user_id in processing_users or session_store.version(user_id) != session_version:
                await message.reply_text("Фото не добавлено: сессия уже закрыта.")
                return
            try:
                photo_file = await telegram_photo.get_file()
                downloaded = await photo_file.download_as_bytearray()
                photo_bytes = bytes(downloaded)
            except Exception:
                LOGGER.warning("Не удалось скачать фото из Telegram.")
                await message.reply_text("Не удалось загрузить фото. Попробуйте ещё раз.")
                return

            if len(photo_bytes) > MAX_PHOTO_BYTES:
                await message.reply_text("Фото отклонено: размер превышает 12 МБ.")
                return

            if user_id in processing_users or session_store.version(user_id) != session_version:
                await message.reply_text("Фото не добавлено: сессия уже закрыта.")
                return

            filename = f"tag_{user_id}_{time.monotonic_ns()}.jpg"
            try:
                normalized, safe_name, mime = await asyncio.to_thread(
                    normalize_image,
                    photo_bytes,
                    filename,
                )
            except InputValidationError:
                await message.reply_text(
                    "Фото отклонено: нужен корректный JPEG/PNG до 12 МБ и безопасного разрешения."
                )
                return
            except Exception:
                LOGGER.warning("Непредвиденная ошибка проверки изображения.")
                await message.reply_text("Не удалось проверить фото. Попробуйте другое.")
                return

            try:
                count = session_store.add_photo(
                    user_id,
                    {"data": normalized, "filename": safe_name, "mime": mime},
                    expected_version=session_version,
                )
            except SessionLimitError as exc:
                if exc.code == "global_bytes":
                    text = (
                        "Общий лимит памяти бота достигнут. Завершите или отмените другую сессию."
                    )
                elif exc.code in {"photo_count", "session_bytes"}:
                    text = "Лимит текущей сессии достигнут. Используйте /finish или /cancel."
                elif exc.code == "photo_bytes":
                    text = "Фото отклонено: размер после безопасной обработки слишком велик."
                else:
                    text = "Фото не добавлено: сессия уже закрыта."
                await message.reply_text(text)
                return

            await message.reply_text(
                f"📸 Ценник #{count} принят. Отправьте ещё или используйте /finish."
            )

    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        user_id = await require_authorized(update)
        message = update.effective_message
        if user_id is None or message is None:
            return

        had_pending = session_store.cancel_pending(user_id)
        if user_id in processing_users:
            cancelled_users.add(user_id)
            await message.reply_text(
                "Отмена принята. Результат текущей обработки не будет отправлен."
            )
        elif had_pending:
            await message.reply_text("Текущая сессия очищена.")
        else:
            await message.reply_text("Активной сессии нет.")

    async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        user_id = await require_authorized(update)
        message = update.effective_message
        if user_id is None or message is None:
            return
        if user_id in processing_users:
            await message.reply_text("⏳ Эта пачка уже обрабатывается.")
            return

        if session_store.photo_count(user_id) == 0:
            await message.reply_text("Вы ещё не отправили ни одного фото ценника.")
            return

        quota = batch_limiter.acquire(user_id)
        if not quota.allowed:
            retry_minutes = max(1, math.ceil(quota.retry_after_seconds / 60))
            await message.reply_text(
                "Слишком много запусков распознавания. Повторите /finish примерно "
                f"через {retry_minutes} мин. Загруженные фото сохранены."
            )
            return

        photos = session_store.take_for_processing(user_id)
        if not photos:
            await message.reply_text("Сессия уже истекла. Отправьте фотографии заново.")
            return

        # Проверка и добавление выполняются без await, поэтому два /finish не могут
        # одновременно захватить одну и ту же сессию в event loop.
        processing_users.add(user_id)
        cancelled_users.discard(user_id)

        try:
            await message.reply_text(
                f"⏳ Начинаю распознавание {len(photos)} ценников через Vision AI..."
            )
            async with pipeline_slots:
                if user_id in cancelled_users:
                    return
                summary, excel_bytes = await asyncio.to_thread(
                    _build_report,
                    catalog,
                    photos,
                    config,
                )

            if user_id in cancelled_users:
                return

            report_text = (
                "📊 *Результаты мониторинга:*\n"
                f"• Обработано: {summary['total_items']} ценников\n"
                "• Сопоставлено с базой Самбери: "
                f"{summary['matched_items']}\n"
                "• *Средний Price Index:* "
                f"{format_price_index(summary.get('avg_price_index'))}\n"
                f"• ✅ Самбери дешевле: {summary['samberi_cheaper_count']} поз.\n"
                "• ❌ Конкурент дешевле: "
                f"{summary['competitor_cheaper_count']} поз.\n"
                f"• ⚠️ Алерты демпинга: {summary['dumping_alerts_count']}\n"
            )
            await message.reply_text(report_text, parse_mode="Markdown")
            if user_id in cancelled_users:
                return

            excel_file = io.BytesIO(excel_bytes)
            excel_file.name = f"Monitoring_Samberi_{time.strftime('%Y%m%d_%H%M')}.xlsx"
            await message.reply_document(
                document=excel_file,
                caption="📥 Итоговый отчёт в Excel",
            )
        except Exception:
            LOGGER.error("Сбой обработки пачки ценников.")
            if user_id not in cancelled_users:
                await message.reply_text(
                    "Не удалось сформировать отчёт. Сессия очищена; попробуйте позже."
                )
        finally:
            # Сессия очищается при любом исходе; локальные байты освобождаются
            # после завершения worker thread.
            session_store.release_processing(user_id)
            processing_users.discard(user_id)
            cancelled_users.discard(user_id)

    try:
        app = (
            ApplicationBuilder()
            .token(config.token)
            .concurrent_updates(MAX_CONCURRENT_UPDATES)
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("finish", finish))
        app.add_handler(CommandHandler("cancel", cancel))
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

        LOGGER.info("Telegram-бот запущен и ожидает фото ценников.")
        app.run_polling()
    except Exception:
        LOGGER.error("Telegram-бот аварийно остановлен.")
        return EXIT_RUNTIME_ERROR
    return EXIT_OK


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(run_bot())
