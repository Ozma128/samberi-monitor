from __future__ import annotations

import pandas as pd
import pytest

from core.matcher import (
    MAX_CANDIDATE_POOL,
    CatalogMatcher,
    CatalogSchemaError,
    extract_attributes,
    normalize_product_text,
)


@pytest.mark.parametrize(
    ("query", "expected_sku"),
    [
        ("Домик в дер. 3,2% мол. 0,93л пл/бут", "104921"),
        ("Молоко 2.5% Домик в деревне 930 мл", "104922"),
        ("Нескафе Голд кофе 190г раств.", "201844"),
        ("Кофе 95г Nescafe Gold", "201845"),
        ("Сыр 45% 200г брусок Брест Литовск российский", "305112"),
        ("82.5% масло сливочн. 180г Простоквашино", "409201"),
    ],
)
def test_complex_matching(catalog_df: pd.DataFrame, query: str, expected_sku: str) -> None:
    assert CatalogMatcher(catalog_df).match_item(query)["matched_sku"] == expected_sku


def test_explicit_physical_conflicts_are_rejected() -> None:
    catalog = pd.DataFrame(
        [
            {"sku": "180", "product_name": "Йогурт Тест 3.2% 180г", "sale_price": 100},
            {"sku": "200", "product_name": "Йогурт Тест 3.2% 200г", "sale_price": 110},
            {"sku": "vol", "product_name": "Йогурт Тест 3.2% 1л", "sale_price": 120},
        ]
    )
    matcher = CatalogMatcher(catalog)
    assert matcher.match_item("Йогурт Тест 3.2% 200г")["matched_sku"] == "200"
    assert matcher.match_item("Йогурт Тест 2.5% 200г")["matched_sku"] is None
    assert matcher.match_item("Йогурт Тест 3.2% 1кг")["matched_sku"] is None


def test_explicit_brand_conflict_forbids_automatic_match() -> None:
    catalog = pd.DataFrame(
        [
            {
                "sku": "wrong-brand",
                "product_name": "Молоко Простоквашино 3.2% 930мл",
                "sale_price": 100,
            }
        ]
    )

    result = CatalogMatcher(catalog).match_item(
        "Молоко Домик в деревне 3.2% 930мл",
        brand="Домик в деревне",
    )

    assert result["matched_sku"] is None


def test_explicit_brand_alias_still_matches_catalog_name() -> None:
    catalog = pd.DataFrame(
        [{"sku": "coffee", "product_name": "Кофе Nescafe Gold 190г", "sale_price": 500}]
    )

    result = CatalogMatcher(catalog).match_item(
        "Кофе Голд 190г",
        brand="Нескафе",
    )

    assert result["matched_sku"] == "coffee"


def test_no_token_overlap_uses_deterministic_bounded_ngram_shortlist() -> None:
    filler_count = MAX_CANDIDATE_POOL + 20
    catalog = pd.DataFrame(
        [
            {
                "sku": f"filler-{index}",
                "product_name": f"Заполнитель каталога номер {index}",
                "sale_price": 100,
            }
            for index in range(filler_count)
        ]
        + [{"sku": "target", "product_name": "Нескафе", "sale_price": 500}]
    )
    matcher = CatalogMatcher(catalog)
    query_norm = normalize_product_text("Нескафэ")

    first = matcher._candidate_indices(query_norm)
    second = matcher._candidate_indices(query_norm)

    assert 1 <= len(first) <= MAX_CANDIDATE_POOL
    assert first == second
    assert filler_count in first
    assert matcher.match_item("Нескафэ", threshold=70)["matched_sku"] == "target"


def test_separate_weight_volume_participates_in_matching() -> None:
    catalog = pd.DataFrame(
        [
            {"sku": "180", "product_name": "Творог Бренд 180г", "sale_price": 100},
            {"sku": "200", "product_name": "Творог Бренд 200г", "sale_price": 105},
        ]
    )
    result = CatalogMatcher(catalog).match_item(
        "Творог Бренд",
        weight_volume="200 г",
    )
    assert result["matched_sku"] == "200"


def test_ambiguous_match_requires_manual_confirmation() -> None:
    catalog = pd.DataFrame(
        [
            {"sku": "a", "product_name": "Сахар Белый 1кг", "sale_price": 100},
            {"sku": "b", "product_name": "Сахар Белый 1кг", "sale_price": 101},
        ]
    )
    result = CatalogMatcher(catalog).match_item("Сахар Белый 1кг")
    assert result["matched_sku"] is None
    assert "Неоднозначное" in result["match_reason"]
    assert len(result["candidates"]) == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Молоко 3,2% 0,93л", {"fat": 3.2, "volume_ml": 930}),
        ("Крупа 5х80г (400г)", {"weight_g": 400, "count": 5}),
        ("Вода 6 x 1 л", {"volume_ml": 6000, "count": 6}),
        ("Мука 1 000 г", {"weight_g": 1000}),
        ("Сок яблочный 100% 1л", {"fat": None, "volume_ml": 1000}),
    ],
)
def test_attribute_parser(text: str, expected: dict[str, object]) -> None:
    attributes = extract_attributes(text)
    for key, value in expected.items():
        assert attributes[key] == value


def test_normalization_preserves_decimal_comma_and_slash_abbreviations() -> None:
    normalized = normalize_product_text("Мол. у/паст. 3,2% 0,93л пл/бут")
    assert "3.2%" in normalized
    assert "930мл" in normalized
    assert "ультрапастеризованное" in normalized
    assert "пэт бутылка" in normalized


def test_oversized_numeric_attribute_tokens_are_ignored_safely() -> None:
    oversized_name = f"Товар {'9' * 400}кг"

    assert extract_attributes(oversized_name) == {
        "fat": None,
        "volume_ml": None,
        "weight_g": None,
        "count": None,
    }
    matcher = CatalogMatcher(
        pd.DataFrame([{"sku": "oversized", "product_name": oversized_name, "sale_price": 10}])
    )
    assert matcher.catalog_records[0]["код_товара"] == "oversized"


def test_catalog_schema_english_aliases_and_sku_normalization() -> None:
    frame = pd.DataFrame(
        [{"product_id": 123.0, "product_name": "Товар", "purchase_price": 10, "sale_price": 20}]
    )
    matcher = CatalogMatcher(frame)
    assert matcher.catalog_records[0]["код_товара"] == "123"
    assert matcher.catalog_records[0]["цена_закупки"] == 10


def test_catalog_rejects_ambiguous_or_missing_columns() -> None:
    with pytest.raises(CatalogSchemaError, match="Неоднозначные"):
        CatalogMatcher(pd.DataFrame([{"sku": "1", "name": "Товар", "price": 10, "sale_price": 11}]))
    with pytest.raises(CatalogSchemaError, match="обязательные"):
        CatalogMatcher(pd.DataFrame([{"sku": "1", "sale_price": 10}]))


def test_x5_headers_and_unpriced_section_rows_are_supported() -> None:
    frame = pd.DataFrame(
        [
            {
                "Код": "100000",
                "Номенклатура": "БАКАЛЕЯ",
                "Закуп": None,
                "Цена продажи": None,
                "Промо": None,
            },
            {
                "Код": "100001",
                "Номенклатура": "Крупа гречневая 800 г",
                "Закуп": 0,
                "Цена продажи": "79,90",
                "Промо": "69,90",
            },
        ]
    )

    matcher = CatalogMatcher(frame)

    assert matcher.catalog_source_rows == 2
    assert matcher.catalog_skipped_rows == 1
    assert matcher.catalog_header_rows_skipped == 0
    assert len(matcher.catalog_records) == 1
    assert matcher.catalog_records[0]["код_товара"] == "100001"
    assert matcher.catalog_records[0]["цена_закупки"] is None
    assert matcher.catalog_records[0]["цена_продажи"] == pytest.approx(79.9)
    assert matcher.catalog_records[0]["цена_на_промо"] == pytest.approx(69.9)


def test_catalog_finds_decorated_embedded_header_and_skips_repeated_header() -> None:
    frame = pd.DataFrame(
        [
            ["Отчёт по магазинам", None, None, None, None],
            ["\ufeffКод", "Номенклатура товара", "Закуп, ₽", "Цена продажи,\nруб.", "Промо ₽"],
            ["Код", "Номенклатура", "Закуп", "Цена продажи", "Промо"],
            ["000123", "Чай чёрный 100 г", "100", "149,90", None],
        ],
        columns=[f"Неизвестная колонка {index}" for index in range(5)],
    )

    matcher = CatalogMatcher(frame)

    assert matcher.catalog_header_rows_skipped == 2
    assert matcher.catalog_source_rows == 2
    assert matcher.catalog_skipped_rows == 1
    assert matcher.catalog_records[0]["код_товара"] == "000123"
    assert matcher.catalog_records[0]["наименование_товара"] == "Чай чёрный 100 г"
    assert matcher.catalog_records[0]["цена_продажи"] == pytest.approx(149.9)


def test_catalog_allows_unknown_duplicate_columns() -> None:
    frame = pd.DataFrame(
        [["1", "Товар", 10, "первый", "второй"]],
        columns=["SKU", "Product_Name", "Sale_Price", "Комментарий", "Комментарий"],
    )

    assert CatalogMatcher(frame).catalog_records[0]["код_товара"] == "1"


def test_catalog_ignores_extra_ordinal_position_column() -> None:
    matcher = CatalogMatcher(
        pd.DataFrame(
            [
                {
                    "Код": "1",
                    "Позиция": 17,
                    "Номенклатура": "Товар",
                    "Цена продажи": 100,
                }
            ]
        )
    )

    assert matcher.catalog_records[0]["наименование_товара"] == "Товар"


def test_catalog_accepts_promo_price_when_regular_price_column_is_absent() -> None:
    matcher = CatalogMatcher(
        pd.DataFrame([{"Код": "1", "Номенклатура": "Товар", "Промо": "49,90"}])
    )

    assert matcher.catalog_records[0]["цена_продажи"] is None
    assert matcher.catalog_records[0]["цена_на_промо"] == pytest.approx(49.9)


@pytest.mark.parametrize("column", ["Цена продажи", "Промо"])
@pytest.mark.parametrize("bad_price", [-1, float("inf"), "по запросу"])
def test_catalog_rejects_explicit_invalid_comparison_prices(column: str, bad_price: object) -> None:
    row = {
        "Код": "1",
        "Номенклатура": "Товар",
        "Цена продажи": 100,
        "Промо": 90,
    }
    row[column] = bad_price
    with pytest.raises(CatalogSchemaError, match="некорректную цену"):
        CatalogMatcher(pd.DataFrame([row]))


@pytest.mark.parametrize(
    ("sale_price", "promo_price", "expected_sale", "expected_promo"),
    [
        (100, 0, 100, None),
        (0, 80, None, 80),
    ],
)
def test_catalog_treats_zero_as_an_absent_price(
    sale_price: float,
    promo_price: float,
    expected_sale: float | None,
    expected_promo: float | None,
) -> None:
    matcher = CatalogMatcher(
        pd.DataFrame(
            [
                {
                    "Код": "1",
                    "Номенклатура": "Товар",
                    "Цена продажи": sale_price,
                    "Промо": promo_price,
                }
            ]
        )
    )

    assert matcher.catalog_records[0]["цена_продажи"] == expected_sale
    assert matcher.catalog_records[0]["цена_на_промо"] == expected_promo


def test_catalog_rejects_row_without_any_positive_comparison_price() -> None:
    with pytest.raises(CatalogSchemaError, match="положительная цена"):
        CatalogMatcher(pd.DataFrame([{"Код": "1", "Номенклатура": "Товар", "Цена продажи": 0}]))


@pytest.mark.parametrize("bad_purchase", [-1, float("inf"), "неизвестно"])
def test_catalog_rejects_explicit_invalid_purchase_price(bad_purchase: object) -> None:
    with pytest.raises(CatalogSchemaError, match="некорректную цену"):
        CatalogMatcher(
            pd.DataFrame(
                [
                    {
                        "Код": "1",
                        "Номенклатура": "Товар",
                        "Закуп": bad_purchase,
                        "Цена продажи": 100,
                    }
                ]
            )
        )


def test_failed_catalog_reload_keeps_previous_records_and_stats() -> None:
    matcher = CatalogMatcher(
        pd.DataFrame(
            [
                {"Код": "10", "Номенклатура": "РАЗДЕЛ", "Цена продажи": None},
                {"Код": "11", "Номенклатура": "Товар", "Цена продажи": 100},
            ]
        )
    )

    with pytest.raises(CatalogSchemaError):
        matcher.load_catalog(
            pd.DataFrame([{"Код": "20", "Номенклатура": "РАЗДЕЛ", "Цена продажи": None}])
        )

    assert [record["код_товара"] for record in matcher.catalog_records] == ["11"]
    assert matcher.catalog_source_rows == 2
    assert matcher.catalog_skipped_rows == 1
    assert matcher.catalog_header_rows_skipped == 0


def test_catalog_rejects_priced_rows_without_identity() -> None:
    with pytest.raises(CatalogSchemaError, match="нет SKU или наименования"):
        CatalogMatcher(pd.DataFrame([{"Код": None, "Номенклатура": "Товар", "Цена продажи": 100}]))


def test_catalog_rejects_catalog_containing_only_section_rows() -> None:
    with pytest.raises(CatalogSchemaError, match="не найдено товарных строк"):
        CatalogMatcher(
            pd.DataFrame([{"Код": "100000", "Номенклатура": "БАКАЛЕЯ", "Цена продажи": None}])
        )


def test_catalog_still_rejects_duplicate_product_skus() -> None:
    with pytest.raises(CatalogSchemaError, match="повторяются SKU"):
        CatalogMatcher(
            pd.DataFrame(
                [
                    {"Код": "1", "Номенклатура": "Первый товар", "Цена продажи": 100},
                    {"Код": "1", "Номенклатура": "Второй товар", "Цена продажи": 110},
                ]
            )
        )


def test_broad_header_names_are_not_guessed() -> None:
    with pytest.raises(CatalogSchemaError, match="обязательные"):
        CatalogMatcher(
            pd.DataFrame(
                [
                    {
                        "Штрихкод": "123",
                        "Код категории": "10",
                        "Название категории": "Чай",
                        "Цена конкурента": 100,
                    }
                ]
            )
        )
