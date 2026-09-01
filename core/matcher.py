"""
Модуль сопоставления (матчинга) распознанных товаров с внутренней номенклатурой сети "Самбери".
Использует нечеткое сопоставление (RapidFuzz), очистку ритейл-сокращений и ранжирование кандидатов.
"""

import re
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from rapidfuzz import fuzz, process

# Словарь типичных сокращений в торговых сетях (Самбери, Реми, Пятерочка и др.)
ABBREVIATIONS_MAP = {
    r"\bмол\b|\bмол\.\b": "молоко",
    r"\bмасл\b|\bмасл\.\b": "масло",
    r"\bслив\b|\bслив\.\b": "сливочное",
    r"\bраст\b|\bраст\.\b": "растительное",
    r"\bпаст\b|\bпаст\.\b": "пастеризованное",
    r"\bультрапаст\b|\bу/паст\b|\bуп\.\b": "ультрапастеризованное",
    r"\bтворож\b|\bтвор\.\b": "творог",
    r"\bсмет\b|\bсмет\.\b": "сметана",
    r"\bсырн\b|\bсыр\.\b": "сыр",
    r"\bколб\b|\bколб\.\b": "колбаса",
    r"\bвар\b|\bвар\.\b": "вареная",
    r"\bс/к\b": "сырокопченая",
    r"\bв/к\b": "варено-копченая",
    r"\bп/к\b": "полукопченая",
    r"\bшокол\b|\bшок\.\b": "шоколад",
    r"\bкруп\b|\bкр\.\b": "крупа",
    r"\bгречн\b|\bгреч\.\b": "гречневая",
    r"\bмакар\b|\bмак\.\b": "макароны",
    r"\bв/с\b|\bвысш\.с\b": "высший сорт",
    r"\bпл/бут\b|\bпэт\b|\bбут\b|\bбут\.\b": "бутылка",
    r"\bплен\b|\bпл\.\b": "пленка",
    r"\bупак\b|\bуп\b|\bуп\.\b": "упаковка",
    r"\bпак\b|\bпак\.\b": "пакет",
    r"\bг\b|\bгр\b|\bгр\.\b": "г",
    r"\bкг\b|\bкг\.\b": "кг",
    r"\bмл\b|\bмл\.\b": "мл",
    r"\bл\b|\bл\.\b": "л",
}


def normalize_product_text(text: str) -> str:
    """Очищает и нормализует строку с названием товара для точного сравнения."""
    if not text or not isinstance(text, str):
        return ""
    
    clean = text.lower()
    
    # Заменяем знаки препинания и спецсимволы на пробелы
    clean = re.sub(r"[,/\\()\"'«»№#;:_\-\+]", " ", clean)
    
    # Заменяем сокращения
    for pattern, replacement in ABBREVIATIONS_MAP.items():
        clean = re.sub(pattern, replacement, clean)
        
    # Стандартизируем числа с процентами и объемами (3.2% -> 3.2 %, 930мл -> 930 мл)
    clean = re.sub(r"(\d+[\.,]?\d*)\s*%", r"\1%", clean)
    clean = re.sub(r"(\d+)\s*(мл|г|кг|л|шт)", r"\1 \2", clean)
    
    # Схлопываем лишние пробелы
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


class CatalogMatcher:
    """
    Класс для сопоставления товаров конкурентов с каталогом номенклатуры "Самбери".
    """

    def __init__(self, catalog_df: Optional[pd.DataFrame] = None):
        self.catalog_df: pd.DataFrame = pd.DataFrame()
        self.catalog_records: List[Dict[str, Any]] = []
        self.normalized_catalog_names: List[str] = []
        
        if catalog_df is not None and not catalog_df.empty:
            self.load_catalog(catalog_df)

    def load_catalog(self, df: pd.DataFrame) -> None:
        """
        Загружает и индексирует каталог Самбери.
        Ожидает колонки (с авто-детекцией синонимов):
        - код_товара / sku / code
        - наименование_товара / name / товар
        - цена_закупки / prime_cost / purchase_price
        - цена_продажи / price / regular_price
        - цена_на_промо / promo_price / promo
        """
        df = df.copy()
        
        # Нормализация имен колонок
        col_map = {}
        for col in df.columns:
            c_lower = str(col).lower().strip()
            if any(k in c_lower for k in ["код", "sku", "артикул", "code", "id"]):
                col_map[col] = "код_товара"
            elif any(k in c_lower for k in ["наименование", "название", "товар", "name", "номенклатура"]):
                col_map[col] = "наименование_товара"
            elif any(k in c_lower for k in ["закупк", "себестоим", "cost", "себес"]):
                col_map[col] = "цена_закупки"
            elif any(k in c_lower for k in ["промо", "акци", "promo", "скидк"]):
                col_map[col] = "цена_на_промо"
            elif any(k in c_lower for k in ["продаж", "цена", "регуляр", "retail", "price"]):
                col_map[col] = "цена_продажи"
                
        df.rename(columns=col_map, inplace=True)
        
        # Гарантируем наличие ключевых колонок
        for req_col in ["код_товара", "наименование_товара", "цена_закупки", "цена_продажи", "цена_на_промо"]:
            if req_col not in df.columns:
                df[req_col] = None

        # Преобразуем числовые поля
        for num_col in ["цена_закупки", "цена_продажи", "цена_на_промо"]:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce")

        df["код_товара"] = df["код_товара"].astype(str)
        df["наименование_товара"] = df["наименование_товара"].fillna("").astype(str)
        df["_norm_name"] = df["наименование_товара"].apply(normalize_product_text)

        self.catalog_df = df
        self.catalog_records = df.to_dict(orient="records")
        self.normalized_catalog_names = df["_norm_name"].tolist()

    def match_item(
        self,
        recognized_name: str,
        threshold: float = 40.0,
        top_k: int = 3
    ) -> Dict[str, Any]:
        """
        Ищет наилучшее соответствие для распознанного товара среди позиций каталога Самбери.
        Возвращает лучший результат и список Top-K кандидатов.
        """
        if not self.catalog_records or not recognized_name:
            return {
                "matched_sku": None,
                "matched_name": None,
                "our_purchase_price": None,
                "our_sale_price": None,
                "our_promo_price": None,
                "match_score": 0.0,
                "candidates": []
            }

        norm_query = normalize_product_text(recognized_name)
        
        # Используем комбинированный скоринг RapidFuzz
        # process.extract возвращает список (choice, score, index)
        matches = process.extract(
            norm_query,
            self.normalized_catalog_names,
            scorer=fuzz.token_sort_ratio,
            limit=top_k
        )

        candidates = []
        for match_tuple in matches:
            norm_name, score, idx = match_tuple
            # Дополнительный скоринг partial_ratio для учета вложенных названий
            partial_sc = fuzz.partial_ratio(norm_query, norm_name)
            combined_score = round(score * 0.7 + partial_sc * 0.3, 1)
            
            rec = self.catalog_records[idx]
            candidates.append({
                "sku": rec["код_товара"],
                "name": rec["наименование_товара"],
                "purchase_price": rec["цена_закупки"],
                "sale_price": rec["цена_продажи"],
                "promo_price": rec["цена_на_промо"],
                "score": combined_score
            })

        # Сортируем кандидатов по скору
        candidates.sort(key=lambda x: x["score"], reverse=True)
        
        best = candidates[0] if candidates and candidates[0]["score"] >= threshold else None
        
        if best:
            return {
                "matched_sku": best["sku"],
                "matched_name": best["name"],
                "our_purchase_price": best["purchase_price"],
                "our_sale_price": best["sale_price"],
                "our_promo_price": best["promo_price"],
                "match_score": best["score"],
                "candidates": candidates
            }
        else:
            return {
                "matched_sku": None,
                "matched_name": "Не найдено соответствие",
                "our_purchase_price": None,
                "our_sale_price": None,
                "our_promo_price": None,
                "match_score": candidates[0]["score"] if candidates else 0.0,
                "candidates": candidates
            }

    def match_all(self, recognized_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Сопоставляет целый список распознанных товаров с каталогом."""
        matched_results = []
        for item in recognized_items:
            rec_name = item.get("product_name", "")
            match_info = self.match_item(rec_name)
            
            combined = {**item, **match_info}
            matched_results.append(combined)
        return matched_results
