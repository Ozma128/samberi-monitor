"""Единая конфигурация приложения без секретов в исходном коде."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .vision_extractor import DEFAULT_GEMINI_MODEL

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Некорректное логическое значение: {value!r}")


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"Значение должно быть от {minimum} до {maximum}.")
    return number


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"Значение должно быть от {minimum} до {maximum}.")
    return number


@dataclass(frozen=True)
class AppSettings:
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = DEFAULT_GEMINI_MODEL
    app_password: str = field(default="", repr=False)
    auth_disabled: bool = False
    match_threshold: float = 72.0
    vision_workers: int = 2
    min_recognition_confidence: float = 0.55


def load_settings(secrets: Mapping[str, Any] | None = None) -> AppSettings:
    """Загрузить `.env`, окружение и Streamlit secrets с безопасными defaults."""

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    secret_values = secrets or {}

    def get(name: str, default: Any = None) -> Any:
        env_value = os.getenv(name)
        if env_value is not None:
            return env_value
        try:
            return secret_values.get(name, default)
        except (AttributeError, FileNotFoundError, KeyError):
            return default

    model = str(get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip()
    if not re.fullmatch(r"gemini-[a-z0-9.-]{1,80}", model):
        raise ValueError("GEMINI_MODEL имеет неверный формат.")

    auth_disabled = _as_bool(get("APP_AUTH_DISABLED", False))
    app_password = str(get("APP_PASSWORD", "") or "")
    if len(app_password) > 256:
        raise ValueError("APP_PASSWORD слишком длинный.")
    if app_password and not auth_disabled and len(app_password) < 12:
        raise ValueError("APP_PASSWORD должен содержать не менее 12 символов.")

    return AppSettings(
        gemini_api_key=str(get("GEMINI_API_KEY", "") or "").strip(),
        gemini_model=model,
        app_password=app_password,
        auth_disabled=auth_disabled,
        match_threshold=_as_float(get("MATCH_THRESHOLD"), 72.0, 50.0, 100.0),
        vision_workers=_as_int(get("VISION_WORKERS"), 2, 1, 4),
        min_recognition_confidence=_as_float(get("MIN_RECOGNITION_CONFIDENCE"), 0.55, 0.0, 1.0),
    )
