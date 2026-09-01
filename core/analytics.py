"""
Модуль расчета ценовых метрик, разницы цен, Price Index (PI) и аналитических сводок.
"""

from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np


def calculate_price_metrics(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Рассчитывает все аналитические поля для одной строки мониторинга:
    - Разница цен (руб.)
    - Price Index (%)
    - Оценка демпинга (цена конкурента ниже себестоимости закупки)
    - Статус ценового паритета
    """
    our_purchase = item.get("our_purchase_price")
    our_sale = item.get("our_sale_price")
    our_promo = item.get("our_promo_price")
    
    comp_regular = item.get("regular_price")
    comp_promo = item.get("promo_price")
    
    # Приводим к float если возможно
    def to_float(val):
        try:
            return float(val) if val is not None and not pd.isna(val) else None
        except (ValueError, TypeError):
            return None

    our_purchase = to_float(our_purchase)
    our_sale = to_float(our_sale)
    our_promo = to_float(our_promo)
    comp_regular = to_float(comp_regular)
    comp_promo = to_float(comp_promo)

    # 1. Разница регулярных цен (в рублях: Конкурент - Самбери)
    # Положительная разница: у конкурента дороже (Самбери дешевле)
    # Отрицательная разница: у конкурента дешевле (Самбери дороже)
    regular_diff_rub = None
    if comp_regular is not None and our_sale is not None:
        regular_diff_rub = round(comp_regular - our_sale, 2)

    # 2. Разница промо-цен (в рублях)
    promo_diff_rub = None
    if comp_promo is not None and our_promo is not None:
        promo_diff_rub = round(comp_promo - our_promo, 2)

    # 3. Эффективные цены (с учетом действующих промо-акций)
    comp_eff = comp_promo if (comp_promo is not None and comp_promo > 0) else comp_regular
    our_eff = our_promo if (our_promo is not None and our_promo > 0) else our_sale

    effective_diff_rub = None
    if comp_eff is not None and our_eff is not None:
        effective_diff_rub = round(comp_eff - our_eff, 2)

    # 4. Регулярный Price Index (PI % = Цена Конкурента / Цена Самбери * 100)
    pi_regular = None
    if comp_regular is not None and our_sale is not None and our_sale > 0:
        pi_regular = round((comp_regular / our_sale) * 100.0, 1)

    # 5. Эффективный Price Index (с учетом промо)
    pi_effective = None
    if comp_eff is not None and our_eff is not None and our_eff > 0:
        pi_effective = round((comp_eff / our_eff) * 100.0, 1)

    # 6. Определение статуса и алертов
    status = "Не определен"
    alert = None

    # Проверка на продажу ниже закупки Самбери (Демпинг конкурента)
    if comp_eff is not None and our_purchase is not None and our_purchase > 0:
        if comp_eff < our_purchase:
            alert = "⚠️ ДЕМПИНГ (Конкурент ниже закупки Самбери)"

    if pi_effective is not None:
        if pi_effective > 102.0:
            status = "✅ Самбери дешевле"
        elif pi_effective < 98.0:
            status = "❌ Конкурент дешевле"
        else:
            status = "⚖️ Паритет цен (±2%)"

    return {
        **item,
        "our_purchase_price": our_purchase,
        "our_sale_price": our_sale,
        "our_promo_price": our_promo,
        "comp_regular_price": comp_regular,
        "comp_promo_price": comp_promo,
        "comp_effective_price": comp_eff,
        "our_effective_price": our_eff,
        "regular_diff_rub": regular_diff_rub,
        "promo_diff_rub": promo_diff_rub,
        "effective_diff_rub": effective_diff_rub,
        "price_index_regular": pi_regular,
        "price_index_effective": pi_effective,
        "status": status,
        "alert": alert
    }


def summarize_price_index(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Формирует общую сводку и KPI по всей выборке промоделированных ценников.
    """
    if not items:
        return {
            "total_items": 0,
            "matched_items": 0,
            "match_rate": 0.0,
            "avg_price_index": 100.0,
            "samberi_cheaper_count": 0,
            "competitor_cheaper_count": 0,
            "parity_count": 0,
            "dumping_alerts_count": 0,
            "total_our_basket": 0.0,
            "total_comp_basket": 0.0,
            "basket_price_index": 100.0
        }

    df = pd.DataFrame(items)
    
    total = len(df)
    matched = df["matched_sku"].notna().sum() if "matched_sku" in df.columns else 0
    match_rate = round((matched / total * 100.0), 1) if total > 0 else 0.0

    valid_pi = df[df["price_index_effective"].notna()]
    avg_pi = round(valid_pi["price_index_effective"].mean(), 1) if not valid_pi.empty else 100.0

    samberi_cheaper = (df["status"] == "✅ Самбери дешевле").sum() if "status" in df.columns else 0
    comp_cheaper = (df["status"] == "❌ Конкурент дешевле").sum() if "status" in df.columns else 0
    parity = (df["status"] == "⚖️ Паритет цен (±2%)").sum() if "status" in df.columns else 0
    dumping = df["alert"].notna().sum() if "alert" in df.columns else 0

    # Корзинный индекс цен (Сумма цен конкурента / Сумма цен Самбери)
    basket_df = df[df["our_effective_price"].notna() & df["comp_effective_price"].notna()]
    our_basket = basket_df["our_effective_price"].sum() if not basket_df.empty else 0.0
    comp_basket = basket_df["comp_effective_price"].sum() if not basket_df.empty else 0.0
    
    basket_pi = round((comp_basket / our_basket * 100.0), 1) if our_basket > 0 else 100.0

    return {
        "total_items": int(total),
        "matched_items": int(matched),
        "match_rate": float(match_rate),
        "avg_price_index": float(avg_pi),
        "samberi_cheaper_count": int(samberi_cheaper),
        "competitor_cheaper_count": int(comp_cheaper),
        "parity_count": int(parity),
        "dumping_alerts_count": int(dumping),
        "total_our_basket": round(float(our_basket), 2),
        "total_comp_basket": round(float(comp_basket), 2),
        "basket_price_index": float(basket_pi)
    }
