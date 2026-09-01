"""
Модуль экспорта результатов мониторинга цен в профессионально оформленный Excel-документ (openpyxl).
"""

import io
from typing import List, Dict, Any, Optional
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def export_comparison_to_excel(
    items: List[Dict[str, Any]],
    competitor_name: str = "Конкурент",
    category_name: str = "Все категории",
    output_path: Optional[str] = None
) -> bytes:
    """
    Генерирует форматированный Excel-файл с точной структурой колонок пользователя,
    цветовой индикацией выгодности цен и сводным блоком показателей.
    """
    wb = Workbook()
    
    # --- Лист 1: Сравнение цен ---
    ws = wb.active
    ws.title = "Мониторинг цен"
    ws.views.sheetView[0].showGridLines = True

    # Цветовая палитра
    HEADER_FILL = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid") # Темно-синий
    HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    
    TITLE_FONT = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
    META_FONT = Font(name="Calibri", size=10, italic=True, color="555555")
    
    GREEN_FILL = PatternFill(start_color="E6F4EA", end_color="E6F4EA", fill_type="solid") # Самбери дешевле
    GREEN_FONT = Font(name="Calibri", size=10, bold=True, color="137333")
    
    RED_FILL = PatternFill(start_color="FCE8E6", end_color="FCE8E6", fill_type="solid") # Конкурент дешевле
    RED_FONT = Font(name="Calibri", size=10, bold=True, color="C5221F")
    
    BLUE_FILL = PatternFill(start_color="E8F0FE", end_color="E8F0FE", fill_type="solid") # Паритет
    BLUE_FONT = Font(name="Calibri", size=10, color="1A73E8")

    ALERT_FILL = PatternFill(start_color="FEF7E0", end_color="FEF7E0", fill_type="solid") # Демпинг
    ALERT_FONT = Font(name="Calibri", size=10, bold=True, color="B06000")

    THIN_BORDER = Border(
        left=Side(style="thin", color="D3D3D3"),
        right=Side(style="thin", color="D3D3D3"),
        top=Side(style="thin", color="D3D3D3"),
        bottom=Side(style="thin", color="D3D3D3")
    )

    # Заголовок отчета
    ws["A1"] = "САМБЕРИ: Отчет по мониторингу ценников конкурентов"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Позиций проанализировано: {len(items)}"
    ws["A2"].font = META_FONT

    # Заголовки колонок (согласно структуре пользователя)
    headers = [
        "Код нашего товара",
        "Наименование товара (Самбери)",
        "Распознано на ценнике (Конкурент)",
        "Цена закупки товара",
        "Цена продажи товара",
        "Цена на промо у товара",
        f"Текущая цена {competitor_name}",
        f"Цена на промо {competitor_name}",
        "Разница цен (руб)",
        "Price Index (PI %)",
        "Статус позиционирования",
        "Предупреждения",
        "Файл фото"
    ]

    start_row = 4
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col_idx, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER

    ws.row_dimensions[start_row].height = 28

    # Заполнение данными
    for row_idx, item in enumerate(items, start_row + 1):
        sku = item.get("matched_sku") or "-"
        our_name = item.get("matched_name") or item.get("product_name", "")
        comp_name = item.get("product_name", "")
        our_purchase = item.get("our_purchase_price")
        our_sale = item.get("our_sale_price")
        our_promo = item.get("our_promo_price")
        comp_regular = item.get("comp_regular_price") or item.get("regular_price")
        comp_promo = item.get("comp_promo_price") or item.get("promo_price")
        diff_rub = item.get("effective_diff_rub")
        pi = item.get("price_index_effective")
        status = item.get("status", "")
        alert = item.get("alert") or ""
        filename = item.get("filename", "")

        row_values = [
            sku,
            our_name,
            comp_name,
            our_purchase,
            our_sale,
            our_promo,
            comp_regular,
            comp_promo,
            diff_rub,
            f"{pi}%" if pi is not None else "-",
            status,
            alert,
            filename
        ]

        for col_idx, val in enumerate(row_values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="center")

            # Форматирование цен
            if col_idx in [4, 5, 6, 7, 8, 9] and isinstance(val, (int, float)):
                cell.number_format = '#,##0.00 "₽"'
                cell.alignment = Alignment(horizontal="right", vertical="center")

            # Выравнивание статусов
            if col_idx in [1, 10, 11, 12, 13]:
                cell.alignment = Alignment(horizontal="center", vertical="center")

            # Подсветка статуса
            if col_idx == 11:
                if "Самбери дешевле" in str(val):
                    cell.fill = GREEN_FILL
                    cell.font = GREEN_FONT
                elif "Конкурент дешевле" in str(val):
                    cell.fill = RED_FILL
                    cell.font = RED_FONT
                elif "Паритет" in str(val):
                    cell.fill = BLUE_FILL
                    cell.font = BLUE_FONT

            # Подсветка демпинга
            if col_idx == 12 and alert:
                cell.fill = ALERT_FILL
                cell.font = ALERT_FONT

        ws.row_dimensions[row_idx].height = 20

    # Автоматическая настройка ширины колонок
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row >= start_row and cell.value:
                # Ограничиваем максимальную длину для красивого вида
                lines = str(cell.value).split("\n")
                for line in lines:
                    max_len = max(max_len, len(line))
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

    # Сохранение в буфер байтов
    excel_buffer = io.BytesIO()
    wb.save(excel_buffer)
    excel_bytes = excel_buffer.getvalue()

    if output_path:
        with open(output_path, "wb") as f:
            f.write(excel_bytes)

    return excel_bytes
