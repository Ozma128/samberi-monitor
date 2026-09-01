"""
Автоматизированный тест пайплайна мониторинга ценников:
1. Распознавание (Mock/Vision)
2. Матчинг с каталогом Самбери
3. Расчет метрик и Price Index
4. Экспорт в форматированный Excel
"""

import os
import sys

# Настройка UTF-8 для консоли Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from core.vision_extractor import PriceTagExtractor
from core.matcher import CatalogMatcher
from core.analytics import calculate_price_metrics, summarize_price_index
from core.exporter import export_comparison_to_excel


def test_full_pipeline():
    print("=== ТЕСТ 1: Загрузка каталога Самбери ===")
    catalog_path = "data/samples/samberi_catalog_sample.xlsx"
    assert os.path.exists(catalog_path), f"Файл не найден: {catalog_path}"
    
    catalog_df = pd.read_excel(catalog_path)
    matcher = CatalogMatcher(catalog_df)
    assert len(matcher.catalog_records) > 0
    print(f"Загружено позиций каталога: {len(matcher.catalog_records)}")

    print("\n=== ТЕСТ 2: Распознавание ценников (Mock Mode) ===")
    extractor = PriceTagExtractor(provider="mock")
    
    test_images = [
        {"data": b"mock_img_1", "filename": "tag_moloko_domik.jpg"},
        {"data": b"mock_img_2", "filename": "tag_maslo_prostokvashino.jpg"},
        {"data": b"mock_img_3", "filename": "tag_syr_brest.jpg"},
        {"data": b"mock_img_4", "filename": "tag_grechka_uvelka.jpg"}
    ]
    
    recognized_items = extractor.extract_batch(test_images, max_workers=4)
    assert len(recognized_items) == 4
    print(f"Распознано ценников: {len(recognized_items)}")
    for r in recognized_items:
        print(f" -> {r['filename']}: {r['product_name']} | Рег: {r['regular_price']} руб | Промо: {r['promo_price']} руб")

    print("\n=== ТЕСТ 3: Нечеткий матчинг номенклатуры ===")
    # Проверим ручной сложный запрос с сокращениями
    match_res = matcher.match_item("Мол. ультрапаст. Домик в дер. 3,2% 0,93л пл/бут")
    print(f"Запрос: 'Мол. ультрапаст. Домик в дер. 3,2% 0,93л пл/бут'")
    print(f"Результат матчинга: SKU {match_res['matched_sku']} - {match_res['matched_name']} (Скор: {match_res['match_score']}%)")
    assert match_res["matched_sku"] == "104921", f"Ожидался SKU 104921, получено: {match_res['matched_sku']}"

    matched_items = matcher.match_all(recognized_items)
    assert len(matched_items) == len(recognized_items)

    print("\n=== ТЕСТ 4: Аналитический расчет Price Index и разниц ===")
    processed_items = [calculate_price_metrics(it) for it in matched_items]
    
    for item in processed_items:
        print(f"Товар: {item.get('matched_name') or item.get('product_name')}")
        print(f"  Цена Самбери: {item['our_sale_price']} (промо: {item['our_promo_price']})")
        print(f"  Цена Конкурента: {item['comp_regular_price']} (промо: {item['comp_promo_price']})")
        print(f"  Разница: {item['effective_diff_rub']} руб. | Price Index: {item['price_index_effective']}% | Статус: {item['status']}")

    summary = summarize_price_index(processed_items)
    print("\nСводные метрики (KPI):", summary)
    assert summary["total_items"] == 4
    assert summary["matched_items"] >= 3

    print("\n=== ТЕСТ 5: Экспорт в Excel ===")
    output_excel = "data/samples/test_monitoring_output.xlsx"
    excel_bytes = export_comparison_to_excel(
        processed_items,
        competitor_name="Реми",
        category_name="Тестовый аудит",
        output_path=output_excel
    )
    assert len(excel_bytes) > 1000
    assert os.path.exists(output_excel)
    print(f"Excel успешно сгенерирован: {output_excel} ({len(excel_bytes)} байт)")
    print("\n[SUCCESS] Все тесты пайплайна успешно пройдены!")

if __name__ == "__main__":
    test_full_pipeline()
