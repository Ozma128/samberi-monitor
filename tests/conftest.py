from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def catalog_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sku": "104921",
                "product_name": "Молоко ультрапастеризованное Домик в деревне 3.2% 930мл ПЭТ",
                "purchase_price": 72.0,
                "sale_price": 94.9,
                "promo_price": 84.9,
            },
            {
                "sku": "104922",
                "product_name": "Молоко ультрапастеризованное Домик в деревне 2.5% 930мл ПЭТ",
                "purchase_price": 68.0,
                "sale_price": 89.9,
                "promo_price": None,
            },
            {
                "sku": "201844",
                "product_name": "Кофе растворимый Nescafe Gold стекло 190г",
                "purchase_price": 480.0,
                "sale_price": 649.0,
                "promo_price": 489.0,
            },
            {
                "sku": "201845",
                "product_name": "Кофе растворимый Nescafe Gold пакет 95г",
                "purchase_price": 240.0,
                "sale_price": 329.0,
                "promo_price": None,
            },
            {
                "sku": "305112",
                "product_name": "Сыр Российский Брест-Литовск 45% 200г брус фас.",
                "purchase_price": 175.0,
                "sale_price": 239.0,
                "promo_price": None,
            },
            {
                "sku": "409201",
                "product_name": "Масло сливочное Простоквашино 82.5% 180г фольга",
                "purchase_price": 145.0,
                "sale_price": 189.0,
                "promo_price": 159.0,
            },
        ]
    )
