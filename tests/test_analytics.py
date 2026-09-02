from __future__ import annotations

import math

import pytest

from core.analytics import (
    STATUS_COMPETITOR_CHEAPER,
    STATUS_PARITY,
    STATUS_SAMBERI_CHEAPER,
    calculate_price_metrics,
    summarize_price_index,
)


def _item(**overrides):
    base = {
        "matched_sku": "1",
        "matched_name": "Товар 500г",
        "our_purchase_price": 300,
        "our_sale_price": 400,
        "our_promo_price": None,
        "regular_price": 100,
        "promo_price": None,
        "unit": "100г",
        "extraction_status": "ok",
    }
    return {**base, **overrides}


def test_price_per_100g_is_normalized_to_pack() -> None:
    result = calculate_price_metrics(_item())
    assert result["comp_effective_price"] == 500
    assert result["price_index_effective"] == 125.0
    assert result["status"] == STATUS_SAMBERI_CHEAPER


@pytest.mark.parametrize(
    ("matched_name", "unit", "price", "expected"),
    [
        ("Сыр 500г", "кг", 800, 400),
        ("Сок 930мл", "л", 100, 93),
        ("Товар 1шт", "шт", 120, 120),
    ],
)
def test_unit_normalization(matched_name: str, unit: str, price: float, expected: float) -> None:
    result = calculate_price_metrics(
        _item(matched_name=matched_name, unit=unit, regular_price=price)
    )
    assert result["comp_regular_price"] == expected


@pytest.mark.parametrize(
    ("competitor_price", "expected"),
    [
        (102.04, STATUS_SAMBERI_CHEAPER),
        (102.0, STATUS_PARITY),
        (98.0, STATUS_PARITY),
        (97.96, STATUS_COMPETITOR_CHEAPER),
    ],
)
def test_status_uses_unrounded_price_index(competitor_price: float, expected: str) -> None:
    result = calculate_price_metrics(
        _item(
            matched_name="Товар",
            unit="шт",
            our_sale_price=100,
            regular_price=competitor_price,
        )
    )
    assert result["status"] == expected


def test_invalid_and_more_expensive_promo_are_not_used() -> None:
    invalid = calculate_price_metrics(
        _item(matched_name="Товар", unit="шт", regular_price=math.inf)
    )
    assert invalid["price_index_effective"] is None

    expensive = calculate_price_metrics(
        _item(matched_name="Товар", unit="шт", regular_price=100, promo_price=120)
    )
    assert expensive["comp_effective_price"] == 100
    assert expensive["comp_promo_applied"] is False
    assert expensive["data_quality_warnings"]


def test_multibuy_promo_is_not_treated_as_unconditional() -> None:
    result = calculate_price_metrics(
        _item(
            matched_name="Товар",
            unit="шт",
            regular_price=100,
            promo_price=50,
            promo_condition="1+1 при покупке двух",
        )
    )
    assert result["conditional_promo"] is True
    assert result["comp_effective_price"] == 100


def test_summary_is_robust_and_no_data_is_not_false_parity() -> None:
    empty = summarize_price_index([])
    assert empty["avg_price_index"] is None
    assert empty["basket_price_index"] is None
    assert empty["comparable_items"] == 0

    minimal = summarize_price_index([{"matched_sku": "nan"}, {"matched_sku": ""}])
    assert minimal["matched_items"] == 0
    assert minimal["avg_price_index"] is None


@pytest.mark.parametrize("untrusted_raw", [float("nan"), float("inf"), -50, 9999])
def test_summary_recomputes_untrusted_raw_index(untrusted_raw: float) -> None:
    summary = summarize_price_index(
        [
            {
                "matched_sku": "1",
                "our_effective_price": 100,
                "comp_effective_price": 125,
                "price_index_effective": 1,
                "price_index_effective_raw": untrusted_raw,
                "extraction_status": "ok",
            }
        ]
    )

    assert summary["comparable_items"] == 1
    assert summary["avg_price_index"] == 125.0
    assert summary["basket_price_index"] == 125.0


def test_dumping_uses_normalized_effective_price() -> None:
    result = calculate_price_metrics(_item(our_purchase_price=550, regular_price=100))
    assert result["comp_effective_price"] == 500
    assert result["is_dumping"] is True
