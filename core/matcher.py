"""
Продвинутый модуль сопоставления (матчинга) распознанных ценников конкурентов 
с номенклатурной матрицей сети "Самбери".

Особенности алгоритма:
1. Полная устойчивость к перестановке слов (Token Sort / Token Set Ratio).
2. Автоматическое раскрытие ритейл-сокращений (мол., масл., у/паст., пл/бут., в/с и т.д.).
3. Транслитерация и синонимы брендов (Nescafe <-> Нескафе, Greenfield <-> Гринфилд и др.).
4. Извлечение и валидация ключевых атрибутов:
   - Жирность (3.2%, 2.5%, 82.5%, 45% и др.)
   - Вес / Объем в стандартизированных единицах (0.93л -> 930мл, 1кг -> 1000г)
   - Фасовка / Количество (100 пак., 200г, 190г)
5. Штрафы за несовпадение критичных атрибутов (например, молоко 3.2% никогда не спутается с 2.5%).
"""

import re
from typing import List, Dict, Any, Optional, Tuple, Set
import pandas as pd
from rapidfuzz import fuzz, process

# Словарь типичных сокращений в ритейле РФ (Самбери, Реми, Пятерочка, Магнит и др.)
ABBREVIATIONS_MAP = {
    r"\bмол\b|\bмол\.\b|\bм-ко\b": "молоко",
    r"\bмасл\b|\bмасл\.\b|\bм-ло\b": "масло",
    r"\bслив\b|\bслив\.\b|\bсливочн\b": "сливочное",
    r"\bраст\b|\bраст\.\b|\bрастит\b": "растительное",
    r"\bподсолн\b|\bподс\.\b": "подсолнечное",
    r"\bпаст\b|\bпаст\.\b|\bпастериз\b": "пастеризованное",
    r"\bультрапаст\b|\bу/паст\b|\bу/п\b|\bуп\.\b|\bульт\.\b": "ультрапастеризованное",
    r"\bстерил\b|\bстерил\.\b": "стерилизованное",
    r"\bтворож\b|\bтвор\.\b|\bтвор\b": "творог",
    r"\bсмет\b|\bсмет\.\b": "сметана",
    r"\bсырн\b|\bсыр\.\b": "сыр",
    r"\bколб\b|\bколб\.\b": "колбаса",
    r"\bвар\b|\bвар\.\b|\bварен\b": "вареная",
    r"\bс/к\b|\bсырокопч\b": "сырокопченая",
    r"\bв/к\b|\bваренокопч\b": "варено-копченая",
    r"\bп/к\b|\bполукопч\b": "полукопченая",
    r"\bкопч\b|\bкопч\.\b": "копченый",
    r"\bшокол\b|\bшок\.\b|\bшокл\b": "шоколад",
    r"\bкруп\b|\bкр\.\b": "крупа",
    r"\bгречн\b|\bгреч\.\b|\bгреч\b": "гречневая",
    r"\bмакар\b|\bмак\.\b|\bизд\.\b": "макароны",
    r"\bв/с\b|\bвысш\.с\b|\bвысший сорт\b": "высший сорт",
    r"\b1/с\b|\b1 сорт\b": "первый сорт",
    r"\bпл/бут\b|\bпэт\b|\bбут\b|\bбут\.\b|\bпл\.бут\b": "пэт бутылка",
    r"\bт/пак\b|\bтетра\b|\bтетрапак\b|\bтпак\b": "тетрапак",
    r"\bплен\b|\bпл\.\b|\bпленка\b": "пленка",
    r"\bупак\b|\bуп\b|\bуп\.\b": "упаковка",
    r"\bпак\b|\bпак\.\b|\bпакетик\b|\bпакетиков\b": "пакет",
    r"\bст/б\b|\bстекло\b|\bст\.б\b": "стекло",
    r"\bж/б\b|\bжесть\b|\bж\.б\b": "жесть",
    r"\bбрус\b|\bбрусок\b": "брус",
    r"\bфас\b|\bфас\.\b|\bфасов\b": "фасованный",
    r"\bкусок\b|\bкус\.\b": "кусок",
    r"\bнарез\b|\bнарезка\b|\bсл\.\b": "нарезка",
    r"\bсублим\b|\bсублимир\b": "сублимированный",
    r"\bраствор\b|\bраств\.\b": "растворимый",
    r"\bгран\b|\bгранул\b": "гранулированный",
    r"\bлист\b|\bлистов\b": "листовой",
}

# Двусторонняя таблица синонимов брендов (латиница <-> кириллица)
BRAND_SYNONYMS = {
    "nescafe": "нескафе",
    "нескафе": "nescafe",
    "greenfield": "гринфилд",
    "гринфилд": "greenfield",
    "ritter sport": "риттер спорт",
    "риттер": "ritter",
    "makfa": "макфа",
    "макфа": "makfa",
    "uvelka": "увелка",
    "увелка": "uvelka",
    "prostokvashino": "простоквашино",
    "простоквашино": "prostokvashino",
    "jacobs": "якобс",
    "якобс": "jacobs",
    "tess": "тесс",
    "тесс": "tess",
    "richard": "ричард",
    "ричард": "richard",
    "lipton": "липтон",
    "липтон": "lipton",
    "barilla": "барилла",
    "барилла": "barilla",
    "milka": "милка",
    "милка": "milka",
    "alpen gold": "альпен гольд",
    "hochland": "хохланд",
    "хохланд": "hochland",
    "president": "президент",
    "danone": "данон",
    "данон": "danone",
    "vyazanka": "вязанка",
    "вязанка": "vyazanka",
    "dobry": "добрый",
    "добрый": "dobry",
}


def extract_attributes(text: str) -> Dict[str, Any]:
    """
    Извлекает физические атрибуты товара: жирность (%), вес/объем, штучность.
    Позволяет исключить ложный матчинг одноименных товаров с разными характеристиками (напр. 2.5% и 3.2%).
    """
    if not text:
        return {"fat": None, "volume_ml": None, "weight_g": None, "count": None}

    clean = text.lower().replace(",", ".")

    # 1. Жирность (например: 3.2%, 2.5%, 82.5%, 72.5%, 45%, 15%, 20%)
    fat = None
    fat_match = re.search(r"(\d+[\.]?\d*)\s*%", clean)
    if fat_match:
        try:
            fat = float(fat_match.group(1))
        except ValueError:
            pass

    # 2. Объем в миллилитрах (0.93л, 1л, 500мл, 930мл, 1.5л)
    volume_ml = None
    l_match = re.search(r"(\d+[\.]?\d*)\s*(?:л|l)\b", clean)
    ml_match = re.search(r"(\d+[\.]?\d*)\s*(?:мл|ml)\b", clean)
    if l_match:
        try:
            volume_ml = int(float(l_match.group(1)) * 1000)
        except ValueError:
            pass
    elif ml_match:
        try:
            volume_ml = int(float(ml_match.group(1)))
        except ValueError:
            pass

    # 3. Вес в граммах (1кг, 800г, 200г, 100г, 90г, 190г)
    weight_g = None
    kg_match = re.search(r"(\d+[\.]?\d*)\s*(?:кг|kg)\b", clean)
    g_match = re.search(r"(\d+[\.]?\d*)\s*(?:г|гр|g)\b", clean)
    if kg_match:
        try:
            weight_g = int(float(kg_match.group(1)) * 1000)
        except ValueError:
            pass
    elif g_match:
        try:
            weight_g = int(float(g_match.group(1)))
        except ValueError:
            pass

    # 4. Количество / пакетиков (100 пак, 25 пак, 10 шт, 100х2г)
    count = None
    count_match = re.search(r"(\d+)\s*(?:пак|шт|х\d+г|x\d+g)", clean)
    if count_match:
        try:
            count = int(count_match.group(1))
        except ValueError:
            pass

    return {
        "fat": fat,
        "volume_ml": volume_ml,
        "weight_g": weight_g,
        "count": count
    }


def normalize_product_text(text: str) -> str:
    """
    Глубокая нормализация текста товара:
    1. Приведение к нижнему регистру и замена спецсимволов.
    2. Расшифровка ритейл-сокращений.
    3. Стандартизация написания процентов и единиц измерения.
    4. Обогащение синонимами брендов для кросс-языкового матчинга.
    """
    if not text or not isinstance(text, str):
        return ""

    clean = text.lower()

    # Заменяем разделители и спецсимволы на пробелы
    clean = re.sub(r"[,/\\()\"'«»№#;:_\-\+—–]", " ", clean)

    # Заменяем сокращения на полные слова
    for pattern, replacement in ABBREVIATIONS_MAP.items():
        clean = re.sub(pattern, replacement, clean)

    # Добавляем синонимы брендов (например: если написано nescafe, добавляем нескафе)
    for eng_brand, rus_brand in BRAND_SYNONYMS.items():
        if eng_brand in clean and rus_brand not in clean:
            clean = f"{clean} {rus_brand}"

    # Стандартизируем числа с процентами и объемами (3.2 % -> 3.2%, 930 мл -> 930мл)
    clean = re.sub(r"(\d+[\.,]?\d*)\s*%", r"\1%", clean)
    clean = re.sub(r"(\d+[\.,]?\d*)\s*л\b", r"\1л", clean)
    clean = re.sub(r"(\d+[\.,]?\d*)\s*мл\b", r"\1мл", clean)
    clean = re.sub(r"(\d+[\.,]?\d*)\s*кг\b", r"\1кг", clean)
    clean = re.sub(r"(\d+[\.,]?\d*)\s*(?:г|гр)\b", r"\1г", clean)

    # Схлопываем лишние пробелы
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


class CatalogMatcher:
    """
    Высокоточный матчер номенклатуры Самбери.
    Гарантирует устойчивость к перестановкам слов, разным формулировкам и исключает ошибки сопоставления.
    """

    def __init__(self, catalog_df: Optional[pd.DataFrame] = None):
        self.catalog_df: pd.DataFrame = pd.DataFrame()
        self.catalog_records: List[Dict[str, Any]] = []
        self.normalized_catalog_names: List[str] = []
        self.catalog_attributes: List[Dict[str, Any]] = []

        if catalog_df is not None and not catalog_df.empty:
            self.load_catalog(catalog_df)

    def load_catalog(self, df: pd.DataFrame) -> None:
        """
        Индексирует номенклатурную матрицу сети Самбери с извлечением физических атрибутов.
        """
        df = df.copy()

        # Автоматическое определение колонок
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

        for req_col in ["код_товара", "наименование_товара", "цена_закупки", "цена_продажи", "цена_на_промо"]:
            if req_col not in df.columns:
                df[req_col] = None

        for num_col in ["цена_закупки", "цена_продажи", "цена_на_промо"]:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce")

        df["код_товара"] = df["код_товара"].astype(str)
        df["наименование_товара"] = df["наименование_товара"].fillna("").astype(str)
        
        # Индексируем нормализованные названия и атрибуты
        df["_norm_name"] = df["наименование_товара"].apply(normalize_product_text)
        df["_attrs"] = df["наименование_товара"].apply(extract_attributes)

        self.catalog_df = df
        self.catalog_records = df.to_dict(orient="records")
        self.normalized_catalog_names = df["_norm_name"].tolist()
        self.catalog_attributes = df["_attrs"].tolist()

    def compute_match_score(
        self,
        query_raw: str,
        query_norm: str,
        query_attrs: Dict[str, Any],
        cand_raw: str,
        cand_norm: str,
        cand_attrs: Dict[str, Any]
    ) -> float:
        """
        Вычисляет многофакторную оценку совпадения (0-100%).
        Использует Token Sort Ratio, Token Set Ratio и валидацию атрибутов.
        """
        # 1. Token Sort Ratio: полностью инвариантен к порядку слов!
        sort_score = fuzz.token_sort_ratio(query_norm, cand_norm)

        # 2. Token Set Ratio: учитывает подмножества слов (когда на ценнике есть доп. слова)
        set_score = fuzz.token_set_ratio(query_norm, cand_norm)

        # 3. Базовый скор сопоставления
        base_score = max(sort_score, set_score * 0.95)

        # 4. Проверка и валидация физических атрибутов:
        # А) Жирность (Критически важно! 3.2% vs 2.5% — абсолютно разные товары)
        q_fat = query_attrs.get("fat")
        c_fat = cand_attrs.get("fat")
        if q_fat is not None and c_fat is not None:
            if abs(q_fat - c_fat) < 0.01:
                base_score = min(100.0, base_score + 8.0) # Бонус за точное совпадение жирности
            else:
                base_score -= 40.0 # Жесткий штраф за несовпадение жирности!

        # Б) Вес / объем (например: 190г vs 95г)
        q_w = query_attrs.get("weight_g") or query_attrs.get("volume_ml")
        c_w = cand_attrs.get("weight_g") or cand_attrs.get("volume_ml")
        if q_w is not None and c_w is not None:
            if q_w == c_w:
                base_score = min(100.0, base_score + 6.0)
            elif abs(q_w - c_w) > 50:
                base_score -= 25.0 # Штраф за разную фасовку

        # В) Количество (пакетиков/штук)
        q_cnt = query_attrs.get("count")
        c_cnt = cand_attrs.get("count")
        if q_cnt is not None and c_cnt is not None:
            if q_cnt == c_cnt:
                base_score = min(100.0, base_score + 6.0)
            else:
                base_score -= 25.0

        return max(0.0, min(100.0, round(base_score, 1)))

    def match_item(
        self,
        recognized_name: str,
        threshold: float = 65.0,
        top_k: int = 3
    ) -> Dict[str, Any]:
        """
        Ищет точное совпадение в номенклатуре Самбери независимо от порядка слов и сокращений.
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

        query_norm = normalize_product_text(recognized_name)
        query_attrs = extract_attributes(recognized_name)

        # Вычисляем скоры для всех позиций каталога
        scored_candidates = []
        for idx, rec in enumerate(self.catalog_records):
            cand_raw = rec.get("наименование_товара", "")
            cand_norm = self.normalized_catalog_names[idx]
            cand_attrs = self.catalog_attributes[idx]

            final_score = self.compute_match_score(
                query_raw=recognized_name,
                query_norm=query_norm,
                query_attrs=query_attrs,
                cand_raw=cand_raw,
                cand_norm=cand_norm,
                cand_attrs=cand_attrs
            )

            if final_score >= 30.0:
                scored_candidates.append({
                    "sku": rec["код_товара"],
                    "name": rec["наименование_товара"],
                    "purchase_price": rec["цена_закупки"],
                    "sale_price": rec["цена_продажи"],
                    "promo_price": rec["цена_на_промо"],
                    "score": final_score
                })

        # Сортируем кандидатов по убыванию качества совпадения
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top_candidates = scored_candidates[:top_k]

        best = top_candidates[0] if top_candidates and top_candidates[0]["score"] >= threshold else None

        if best:
            return {
                "matched_sku": best["sku"],
                "matched_name": best["name"],
                "our_purchase_price": best["purchase_price"],
                "our_sale_price": best["sale_price"],
                "our_promo_price": best["promo_price"],
                "match_score": best["score"],
                "candidates": top_candidates
            }
        else:
            return {
                "matched_sku": None,
                "matched_name": "Не найдено точного соответствия",
                "our_purchase_price": None,
                "our_sale_price": None,
                "our_promo_price": None,
                "match_score": top_candidates[0]["score"] if top_candidates else 0.0,
                "candidates": top_candidates
            }

    def match_all(self, recognized_items: List[Dict[str, Any]], threshold: float = 65.0) -> List[Dict[str, Any]]:
        """Сопоставляет весь массив распознанных ценников."""
        matched_results = []
        for item in recognized_items:
            rec_name = item.get("product_name", "")
            match_info = self.match_item(rec_name, threshold=threshold)
            matched_results.append({**item, **match_info})
        return matched_results
