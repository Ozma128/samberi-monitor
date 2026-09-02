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
