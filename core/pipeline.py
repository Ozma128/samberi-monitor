"""Оркестрация распознавания, матчинга и аналитики без зависимости от UI."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pandas as pd

from .analytics import calculate_price_metrics
from .matcher import CatalogMatcher
from .vision_extractor import PriceTagExtractor


def _parse_confidence(value: Any) -> float | None:
    """Return a bounded finite confidence value, or fail closed."""

    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return None
    return confidence


def process_monitoring_batch(
    catalog: pd.DataFrame,
    images: list[dict[str, Any]],
    extractor: PriceTagExtractor,
    *,
    match_threshold: float = 72.0,
    min_confidence: float = 0.55,
    max_workers: int = 4,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Выполнить полный пайплайн и не смешивать ошибки OCR с товарами."""

    matcher = CatalogMatcher(catalog)
    recognized = extractor.extract_batch(
        images,
        max_workers=max_workers,
        on_progress=on_progress,
    )

    processed: list[dict[str, Any]] = []
    for item in recognized:
        current = dict(item)
        confidence = _parse_confidence(current.get("confidence"))
        if current.get("extraction_status") != "ok":
            match = matcher.empty_match(reason="Ошибка распознавания")
        elif confidence is None:
            current["confidence"] = None
            current["extraction_status"] = "error"
            current["extraction_error"] = "Некорректная уверенность распознавания"
            match = matcher.empty_match(reason="Некорректная уверенность распознавания")
        elif confidence < min_confidence:
            current["confidence"] = confidence
            match = matcher.empty_match(reason="Низкая уверенность распознавания")
        else:
            current["confidence"] = confidence
            match = matcher.match_item(
                str(current.get("product_name") or ""),
                threshold=match_threshold,
                brand=current.get("brand"),
                weight_volume=current.get("weight_volume"),
            )
        processed.append(calculate_price_metrics({**current, **match}))
    return processed
