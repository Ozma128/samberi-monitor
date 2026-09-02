"""
Пакет ядра системы мониторинга ценников "Самбери".
Включает модули Vision-распознавания, нечеткого матчинга, аналитики и экспорта в Excel.
"""

from .analytics import calculate_price_metrics, summarize_price_index
from .exporter import export_comparison_to_excel
from .input_validation import (
    InputValidationError,
    collect_uploaded_images,
    load_catalog_file,
)
from .matcher import CatalogMatcher
from .pipeline import process_monitoring_batch
from .settings import AppSettings, load_settings
from .vision_extractor import PriceTagExtractor

__all__ = [
    "PriceTagExtractor",
    "CatalogMatcher",
    "calculate_price_metrics",
    "summarize_price_index",
    "export_comparison_to_excel",
    "InputValidationError",
    "collect_uploaded_images",
    "load_catalog_file",
    "process_monitoring_batch",
    "AppSettings",
    "load_settings",
]
