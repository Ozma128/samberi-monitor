"""Расчёт сопоставимых цен, Price Index и устойчивых сводных KPI."""

from __future__ import annotations

import math
import re
from typing import Any

from .matcher import extract_attributes

PARITY_LOWER = 98.0
PARITY_UPPER = 102.0
MAX_PRICE = 10_000_000.0
STATUS_SAMBERI_CHEAPER = "✅ Самбери дешевле"
STATUS_COMPETITOR_CHEAPER = "❌ Конкурент дешевле"
STATUS_PARITY = "⚖️ Паритет цен (±2%)"
STATUS_UNKNOWN = "Не определен"
DUMPING_ALERT = "⚠️ ДЕМПИНГ (Конкурент ниже закупки Самбери)"

MULTIBUY_PATTERN = re.compile(
    r"(?:\bот\s*\d+|\d+\s*[+хx×]\s*\d+|при\s+покупке|за\s*\d+\s*шт|\d+\s*по\s*цене)",
    re.IGNORECASE,
)


def _positive_finite(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0 or number > MAX_PRICE:
        return None
    return number


def _is_valid_sku(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().casefold()
    return text not in {"", "-", "nan", "none", "null", "<na>"}


def _competitor_pack_factor(item: dict[str, Any]) -> tuple[float | None, str | None]:
    """Привести цену за кг/100г/л к физической фасовке сопоставленного SKU."""

    unit = str(item.get("unit") or "шт").strip().casefold().replace(" ", "")
    aliases = {"упаковка": "упак", "уп": "упак", "100гр": "100г"}
    unit = aliases.get(unit, unit)
    if unit in {"шт", "упак"}:
        return 1.0, None

    physical_text = " ".join(
        str(value or "") for value in (item.get("matched_name"), item.get("weight_volume"))
    )
    attrs = extract_attributes(physical_text)
    if unit == "100г":
        weight = attrs.get("weight_g")
        return (
            (weight / 100.0, None) if weight else (None, "Не найдена масса SKU для цены за 100 г")
        )
    if unit == "кг":
        weight = attrs.get("weight_g")
        return (weight / 1000.0, None) if weight else (None, "Не найдена масса SKU для цены за кг")
    if unit == "л":
        volume = attrs.get("volume_ml")
        return (volume / 1000.0, None) if volume else (None, "Не найден объём SKU для цены за литр")
    return None, f"Неподдерживаемая единица цены: {unit}"


def _effective_price(
    regular: float | None,
    promo: float | None,
    *,
    conditional: bool = False,
) -> tuple[float | None, bool, str | None]:
    if regular is None:
        if promo is None or conditional:
            return None, False, None
        return promo, True, None
    if promo is None or conditional:
        return regular, False, None
    if promo > regular:
        return regular, False, "Промо-цена выше регулярной и не применена"
    return promo, True, None


def calculate_price_metrics(item: dict[str, Any]) -> dict[str, Any]:
    """Рассчитать показатели только по валидным положительным сопоставимым ценам."""

    warnings: list[str] = list(item.get("data_quality_warnings") or [])
    our_purchase = _positive_finite(item.get("our_purchase_price"))
    our_sale = _positive_finite(item.get("our_sale_price"))
    our_promo = _positive_finite(item.get("our_promo_price"))
    comp_regular_raw = _positive_finite(
        item.get("regular_price", item.get("comp_regular_unit_price"))
    )
    comp_promo_raw = _positive_finite(item.get("promo_price", item.get("comp_promo_unit_price")))

    factor, factor_warning = _competitor_pack_factor(item)
    if factor_warning and _is_valid_sku(item.get("matched_sku")):
        warnings.append(factor_warning)
    comp_regular = round(comp_regular_raw * factor, 2) if comp_regular_raw and factor else None
    comp_promo = round(comp_promo_raw * factor, 2) if comp_promo_raw and factor else None

    promo_condition = str(item.get("promo_condition") or "")
    conditional_promo = bool(MULTIBUY_PATTERN.search(promo_condition))
    if conditional_promo and comp_promo is not None:
        warnings.append("Условное мультипромо не включено в эффективную цену")

    our_eff, our_promo_applied, our_warning = _effective_price(our_sale, our_promo)
    comp_eff, comp_promo_applied, comp_warning = _effective_price(
        comp_regular,
        comp_promo,
        conditional=conditional_promo,
    )
    if our_warning:
        warnings.append(f"Самбери: {our_warning}")
    if comp_warning:
        warnings.append(f"Конкурент: {comp_warning}")

    regular_diff = (
        round(comp_regular - our_sale, 2)
        if comp_regular is not None and our_sale is not None
        else None
    )
    promo_diff = (
        round(comp_promo - our_promo, 2)
        if comp_promo is not None and our_promo is not None
        else None
    )
    effective_diff = (
        round(comp_eff - our_eff, 2) if comp_eff is not None and our_eff is not None else None
    )

    pi_regular_raw = (
        comp_regular / our_sale * 100.0
        if comp_regular is not None and our_sale is not None
        else None
    )
    pi_effective_raw = (
        comp_eff / our_eff * 100.0 if comp_eff is not None and our_eff is not None else None
    )

    status = STATUS_UNKNOWN
    if pi_effective_raw is not None:
        if pi_effective_raw > PARITY_UPPER:
            status = STATUS_SAMBERI_CHEAPER
        elif pi_effective_raw < PARITY_LOWER:
            status = STATUS_COMPETITOR_CHEAPER
        else:
            status = STATUS_PARITY

    is_dumping = bool(comp_eff is not None and our_purchase is not None and comp_eff < our_purchase)
    alert = DUMPING_ALERT if is_dumping else None

    return {
        **item,
        "our_purchase_price": our_purchase,
        "our_sale_price": our_sale,
        "our_promo_price": our_promo,
        "comp_regular_unit_price": comp_regular_raw,
        "comp_promo_unit_price": comp_promo_raw,
        "comp_pack_factor": round(factor, 6) if factor is not None else None,
        "comp_regular_price": comp_regular,
        "comp_promo_price": comp_promo,
        "comp_effective_price": comp_eff,
        "our_effective_price": our_eff,
        "our_promo_applied": our_promo_applied,
        "comp_promo_applied": comp_promo_applied,
        "conditional_promo": conditional_promo,
        "regular_diff_rub": regular_diff,
        "promo_diff_rub": promo_diff,
        "effective_diff_rub": effective_diff,
        "price_index_regular": round(pi_regular_raw, 1) if pi_regular_raw is not None else None,
        "price_index_effective": round(pi_effective_raw, 1)
        if pi_effective_raw is not None
        else None,
        "price_index_effective_raw": pi_effective_raw,
        "status": status,
        "alert": alert,
        "is_dumping": is_dumping,
        "data_quality_warnings": list(dict.fromkeys(warnings)),
    }


def summarize_price_index(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Сформировать сводку без ложного PI=100 при отсутствии сравнений."""

    total = len(items)
    matched_items = sum(_is_valid_sku(item.get("matched_sku")) for item in items)
    successful_recognitions = sum(item.get("extraction_status", "ok") == "ok" for item in items)
    comparable: list[tuple[dict[str, Any], float, float, float]] = []
    for item in items:
        our_price = _positive_finite(item.get("our_effective_price"))
        competitor_price = _positive_finite(item.get("comp_effective_price"))
        if our_price is None or competitor_price is None:
            continue
        # Recompute the raw index from trusted price inputs. Imported/exported
        # summaries may contain stale, rounded, NaN or otherwise inconsistent PI.
        comparable.append((item, our_price, competitor_price, competitor_price / our_price * 100.0))

    indices = [entry[3] for entry in comparable]
    avg_pi = round(sum(indices) / len(indices), 1) if indices else None
    our_basket = sum(entry[1] for entry in comparable)
    comp_basket = sum(entry[2] for entry in comparable)
    basket_pi = (
        round(comp_basket / our_basket * 100.0, 1) if comparable and our_basket > 0 else None
    )

    return {
        "total_items": total,
        "successful_recognitions": successful_recognitions,
        "failed_recognitions": total - successful_recognitions,
        "matched_items": matched_items,
        "match_rate": round(matched_items / total * 100.0, 1) if total else 0.0,
        "comparable_items": len(comparable),
        "avg_price_index": avg_pi,
        "samberi_cheaper_count": sum(
            item.get("status") == STATUS_SAMBERI_CHEAPER for item in items
        ),
        "competitor_cheaper_count": sum(
            item.get("status") == STATUS_COMPETITOR_CHEAPER for item in items
        ),
        "parity_count": sum(item.get("status") == STATUS_PARITY for item in items),
        "dumping_alerts_count": sum(item.get("is_dumping") is True for item in items),
        "total_our_basket": round(our_basket, 2),
        "total_comp_basket": round(comp_basket, 2),
        "basket_price_index": basket_pi,
    }
