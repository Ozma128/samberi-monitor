"""Высокоточное сопоставление распознанных ценников с каталогом Самбери."""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter, defaultdict
from typing import Any

import pandas as pd
from rapidfuzz import fuzz

DEFAULT_MATCH_THRESHOLD = 72.0
DEFAULT_MIN_MARGIN = 3.0
MAX_CANDIDATE_POOL = 500
MAX_TOKEN_BUCKETS = 8_192
MAX_TOKEN_POSTINGS = 2_048
MAX_INDEX_TOKENS_PER_NAME = 24
MAX_QUERY_TOKEN_BUCKETS = 24
MAX_NGRAM_BUCKETS = 8_192
MAX_NGRAM_POSTINGS = 2_048
MAX_INDEX_NGRAMS_PER_NAME = 12
MAX_QUERY_NGRAMS = 16
MAX_NUMERIC_TOKEN_LENGTH = 32
MAX_PHYSICAL_MEASURE = 10_000_000.0


def _character_trigrams(value: str) -> set[str]:
    compact = re.sub(r"\s+", " ", value.strip())
    if len(compact) < 3:
        return set()
    return {compact[index : index + 3] for index in range(len(compact) - 2)}


def _trigram_hash(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF


class CatalogSchemaError(ValueError):
    """Справочник не содержит однозначной и корректной схемы."""


ABBREVIATIONS_MAP = {
    r"(?<!\w)(?:мол\.?|м-ко)(?!\w)": "молоко",
    r"(?<!\w)(?:масл\.?|м-ло)(?!\w)": "масло",
    r"(?<!\w)слив(?:\.|очн)?(?!\w)": "сливочное",
    r"(?<!\w)(?:у/паст\.?|у/п\.?|ультрапаст\.?|ульт\.)(?!\w)": "ультрапастеризованное",
    r"(?<!\w)(?:паст\.?|пастериз)(?!\w)": "пастеризованное",
    r"(?<!\w)(?:твор\.?|творож)(?!\w)": "творог",
    r"(?<!\w)смет\.?(?!\w)": "сметана",
    r"(?<!\w)колб\.?(?!\w)": "колбаса",
    r"(?<!\w)(?:с/к|сырокопч)(?!\w)": "сырокопченая",
    r"(?<!\w)(?:в/к|варенокопч)(?!\w)": "варено копченая",
    r"(?<!\w)(?:п/к|полукопч)(?!\w)": "полукопченая",
    r"(?<!\w)(?:пл/бут|пл\.бут|пэт)(?!\w)": "пэт бутылка",
    r"(?<!\w)(?:т/пак|тетра|тетрапак)(?!\w)": "тетрапак",
    r"(?<!\w)(?:ст/б|ст\.б|стекло)(?!\w)": "стекло",
    r"(?<!\w)(?:ж/б|ж\.б|жесть)(?!\w)": "жесть",
    r"(?<!\w)(?:упак\.?|уп\.)(?!\w)": "упаковка",
    r"(?<!\w)(?:пак\.?|пакетик(?:ов)?)(?!\w)": "пакет",
    r"(?<!\w)(?:в/с|высш\.с)(?!\w)": "высший сорт",
    r"(?<!\w)сублим(?:ир)?\.?(?!\w)": "сублимированный",
    r"(?<!\w)раств\.?(?!\w)": "растворимый",
    r"(?<!\w)брусок(?!\w)": "брус",
    r"(?<!\w)фас\.?(?!\w)": "фасованный",
}

BRAND_ALIASES = {
    "nescafe": "нескафе",
    "greenfield": "гринфилд",
    "ritter sport": "риттер спорт",
    "ritter": "риттер",
    "makfa": "макфа",
    "uvelka": "увелка",
    "prostokvashino": "простоквашино",
    "jacobs": "якобс",
    "tess": "тесс",
    "richard": "ричард",
    "lipton": "липтон",
    "barilla": "барилла",
    "milka": "милка",
    "alpen gold": "альпен гольд",
    "hochland": "хохланд",
    "president": "президент",
    "danone": "данон",
    "vyazanka": "вязанка",
    "dobry": "добрый",
}

COLUMN_ALIASES = {
    "код_товара": {
        "код товара",
        "код",
        "sku",
        "артикул",
        "product code",
        "product id",
        "item id",
        "code",
    },
    "наименование_товара": {
        "наименование товара",
        "наименование",
        "название товара",
        "товар",
        "номенклатура",
        "product name",
        "item name",
        "name",
    },
    "цена_закупки": {
        "цена закупки",
        "закупочная цена",
        "себестоимость",
        "себес",
        "purchase price",
        "purchaseprice",
        "cost price",
        "cost",
    },
    "цена_продажи": {
        "цена продажи",
        "розничная цена",
        "регулярная цена",
        "цена",
        "sale price",
        "saleprice",
        "retail price",
        "regular price",
        "price",
    },
    "цена_на_промо": {
        "цена на промо",
        "промо цена",
        "акционная цена",
        "цена по акции",
        "promo price",
        "promoprice",
        "discount price",
    },
}

REQUIRED_COLUMNS = {"код_товара", "наименование_товара", "цена_продажи"}
OPTIONAL_COLUMNS = {"цена_закупки", "цена_на_промо"}
INDEX_STOPWORDS = {
    "товар",
    "продукт",
    "упаковка",
    "фасованный",
    "высший",
    "сорт",
    "шт",
}


def _normalize_header(value: Any) -> str:
    text = str(value).strip().casefold().replace("ё", "е")
    text = re.sub(r"[_\-./]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _canonical_header(value: Any) -> str | None:
    normalized = _normalize_header(value)
    for canonical, aliases in COLUMN_ALIASES.items():
        if normalized == _normalize_header(canonical) or normalized in aliases:
            return canonical
    return None


def _normalize_sku(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            return str(int(value))
    text = str(value).strip()
    return text or None


def _decimal_text(text: str) -> str:
    text = text.casefold().replace("ё", "е")
    text = re.sub(r"(?<=\d)[,](?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s+(?=\d{3}(?:\D|$))", "", text)
    return text


def _localized_number(value: Any) -> Any:
    """Подготовить строковую цену с русскими разделителями для ``to_numeric``."""

    if not isinstance(value, str):
        return value
    text = re.sub(r"[\s\u00a0\u202f]+", "", value.strip())
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "")
    return text.replace(",", ".")


def _bounded_positive_number(raw_value: str, maximum: float) -> float | None:
    """Безопасно разобрать короткое положительное число в заданном диапазоне."""

    if len(raw_value) > MAX_NUMERIC_TOKEN_LENGTH:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or value <= 0 or value > maximum:
        return None
    return value


def _measure_in_base_units(raw_value: str, unit: str) -> float | None:
    value = _bounded_positive_number(raw_value, MAX_PHYSICAL_MEASURE)
    if value is None:
        return None
    factor = 1000.0 if unit in {"кг", "kg", "л", "l"} else 1.0
    scaled = value * factor
    if not math.isfinite(scaled) or scaled > MAX_PHYSICAL_MEASURE:
        return None
    return scaled


def extract_attributes(text: str) -> dict[str, Any]:
    """Извлечь жирность, физическую фасовку и число единиц."""

    attrs = {"fat": None, "volume_ml": None, "weight_g": None, "count": None}
    if not isinstance(text, str) or not text.strip():
        return attrs

    clean = _decimal_text(text)

    # 100% у соков и составов не считается жирностью; ритейл-жирность <= 90%.
    for match in re.finditer(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*%", clean):
        value = _bounded_positive_number(match.group(1), 90.0)
        if value is not None:
            attrs["fat"] = value
            break

    multipacks = list(
        re.finditer(
            r"(?<!\d)(\d{1,3})\s*[xх×]\s*(\d+(?:\.\d+)?)\s*(кг|kg|мл|ml|л|l|гр|г|g)\b",
            clean,
        )
    )
    weight_values: list[float] = []
    volume_values: list[float] = []
    for match in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(кг|kg|гр|г|g)\b", clean):
        unit = match.group(2)
        value = _measure_in_base_units(match.group(1), unit)
        if value is not None:
            weight_values.append(value)
    for match in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(мл|ml|л|l)\b", clean):
        unit = match.group(2)
        value = _measure_in_base_units(match.group(1), unit)
        if value is not None:
            volume_values.append(value)

    # Явный итог в «5x80г (400г)» будет максимальным; без итога вычисляем его.
    if multipacks:
        first = multipacks[0]
        count = int(first.group(1))
        unit = first.group(3)
        amount = _measure_in_base_units(first.group(2), unit)
        total = amount * count if amount is not None else None
        if total is not None and math.isfinite(total) and total <= MAX_PHYSICAL_MEASURE:
            attrs["count"] = count
            if unit in {"кг", "kg", "гр", "г", "g"}:
                weight_values.append(total)
            else:
                volume_values.append(total)

    if weight_values:
        attrs["weight_g"] = int(round(max(weight_values)))
    if volume_values:
        attrs["volume_ml"] = int(round(max(volume_values)))

    if attrs["count"] is None:
        count_match = re.search(
            r"(?<!\d)(\d{1,4})\s*(?:шт(?:ук)?|пак(?:етик(?:ов)?)?|таб(?:леток)?|капсул)(?!\w)",
            clean,
        )
        if count_match:
            attrs["count"] = int(count_match.group(1))
    return attrs


def _canonicalize_units(text: str) -> str:
    def liters(match: re.Match[str]) -> str:
        value = _measure_in_base_units(match.group(1), "л")
        return match.group(0) if value is None else f"{int(round(value))}мл"

    def kilograms(match: re.Match[str]) -> str:
        value = _measure_in_base_units(match.group(1), "кг")
        return match.group(0) if value is None else f"{int(round(value))}г"

    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:л|l)\b", liters, text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:кг|kg)\b", kilograms, text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:мл|ml)\b", r"\1мл", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(?:гр|г|g)\b", r"\1г", text)
    return text


def normalize_product_text(text: str) -> str:
    """Нормализовать название, сохраняя десятичные числа и фасовку."""

    if not isinstance(text, str) or not text.strip():
        return ""
    clean = _decimal_text(text)
    for pattern, replacement in ABBREVIATIONS_MAP.items():
        clean = re.sub(pattern, replacement, clean)
    for alias, canonical in sorted(BRAND_ALIASES.items(), key=lambda pair: -len(pair[0])):
        clean = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", canonical, clean)
    clean = _canonicalize_units(clean)
    clean = re.sub(r"[,/\\()\[\]{}\"'«»№#;:_+—–-]", " ", clean)
    clean = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1%", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _brand_is_present(explicit_brand: str, candidate_norm: str) -> bool:
    """Проверить, что все значимые токены явно распознанного бренда есть в SKU."""

    brand_norm = normalize_product_text(explicit_brand)
    brand_tokens = {token for token in brand_norm.split() if len(token) >= 2}
    if not brand_tokens:
        return True
    return brand_tokens.issubset(set(candidate_norm.split()))


def _attribute_conflict(query: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    q_fat, c_fat = query.get("fat"), candidate.get("fat")
    if q_fat is not None and c_fat is not None and not math.isclose(q_fat, c_fat, abs_tol=0.05):
        return "fat"

    q_weight, c_weight = query.get("weight_g"), candidate.get("weight_g")
    q_volume, c_volume = query.get("volume_ml"), candidate.get("volume_ml")
    if q_weight is not None and c_volume is not None:
        return "dimension"
    if q_volume is not None and c_weight is not None:
        return "dimension"
    if q_weight is not None and c_weight is not None and abs(q_weight - c_weight) > 1:
        return "weight"
    if q_volume is not None and c_volume is not None and abs(q_volume - c_volume) > 1:
        return "volume"

    q_count, c_count = query.get("count"), candidate.get("count")
    if q_count is not None and c_count is not None and q_count != c_count:
        return "count"
    return None


class CatalogMatcher:
    """Матчер с жёсткими физическими ограничениями и контролем неоднозначности."""

    def __init__(self, catalog_df: pd.DataFrame | None = None):
        self.catalog_df = pd.DataFrame()
        self.catalog_records: list[dict[str, Any]] = []
        self.normalized_catalog_names: list[str] = []
        self.catalog_attributes: list[dict[str, Any]] = []
        self._token_index: dict[str, set[int]] = defaultdict(set)
        if catalog_df is not None:
            self.load_catalog(catalog_df)

    @staticmethod
    def empty_match(reason: str = "Соответствие не найдено") -> dict[str, Any]:
        return {
            "matched_sku": None,
            "matched_name": None,
            "our_purchase_price": None,
            "our_sale_price": None,
            "our_promo_price": None,
            "match_score": 0.0,
            "match_reason": reason,
            "candidates": [],
        }

    def load_catalog(self, source: pd.DataFrame) -> None:
        if not isinstance(source, pd.DataFrame) or source.empty:
            raise CatalogSchemaError("Справочник пуст.")
        if source.columns.duplicated().any():
            raise CatalogSchemaError("В справочнике есть колонки с одинаковыми названиями.")

        frame = source.copy()
        mapping: dict[Any, str] = {}
        targets: dict[str, list[Any]] = defaultdict(list)
        for column in frame.columns:
            target = _canonical_header(column)
            if target:
                targets[target].append(column)
        ambiguous = {target: cols for target, cols in targets.items() if len(cols) > 1}
        if ambiguous:
            details = "; ".join(
                f"{target}: {', '.join(map(str, cols))}" for target, cols in ambiguous.items()
            )
            raise CatalogSchemaError(f"Неоднозначные колонки справочника: {details}")
        for target, columns in targets.items():
            mapping[columns[0]] = target
        frame.rename(columns=mapping, inplace=True)

        missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
        if missing:
            raise CatalogSchemaError("Не найдены обязательные колонки: " + ", ".join(missing))
        for optional in OPTIONAL_COLUMNS:
            if optional not in frame.columns:
                frame[optional] = None

        # Unknown source columns are not needed for matching and can multiply
        # the Python object graph when records are materialized.
        frame = frame[
            [
                "код_товара",
                "наименование_товара",
                "цена_закупки",
                "цена_продажи",
                "цена_на_промо",
            ]
        ].copy()

        frame = frame.dropna(axis=0, how="all").copy()
        frame["код_товара"] = frame["код_товара"].map(_normalize_sku)
        frame["наименование_товара"] = frame["наименование_товара"].map(
            lambda value: "" if pd.isna(value) else str(value).strip()[:500]
        )
        invalid_identity = frame["код_товара"].isna() | frame["наименование_товара"].eq("")
        if invalid_identity.any():
            raise CatalogSchemaError(
                f"У {int(invalid_identity.sum())} строк нет SKU или наименования."
            )
        if frame["код_товара"].duplicated().any():
            duplicates = frame.loc[frame["код_товара"].duplicated(), "код_товара"].head(5)
            raise CatalogSchemaError(
                "В справочнике повторяются SKU: " + ", ".join(duplicates.astype(str))
            )

        for column in ("цена_закупки", "цена_продажи", "цена_на_промо"):
            frame[column] = pd.to_numeric(frame[column].map(_localized_number), errors="coerce")
            finite_mask = frame[column].map(
                lambda value: pd.isna(value) or math.isfinite(float(value))
            )
            if not finite_mask.all():
                raise CatalogSchemaError(f"Колонка «{column}» содержит бесконечные значения.")
            if (frame[column].dropna() < 0).any():
                raise CatalogSchemaError(f"Колонка «{column}» содержит отрицательные цены.")
            frame.loc[frame[column] == 0, column] = None
        if frame["цена_продажи"].isna().any():
            raise CatalogSchemaError("У части товаров отсутствует положительная цена продажи.")

        frame["_norm_name"] = frame["наименование_товара"].map(normalize_product_text)
        frame["_attrs"] = frame["наименование_товара"].map(extract_attributes)
        self.catalog_df = frame.reset_index(drop=True)
        self.catalog_records = self.catalog_df.to_dict(orient="records")
        self.normalized_catalog_names = self.catalog_df["_norm_name"].tolist()
        self.catalog_attributes = self.catalog_df["_attrs"].tolist()

        self._token_buckets: list[list[int]] = [[] for _ in range(MAX_TOKEN_BUCKETS)]
        self._ngram_buckets: list[list[int]] = [[] for _ in range(MAX_NGRAM_BUCKETS)]
        for index, normalized in enumerate(self.normalized_catalog_names):
            selected_tokens = sorted(
                {
                    token
                    for token in normalized.split()
                    if len(token) >= 3 and token not in INDEX_STOPWORDS
                },
                key=lambda value: (_trigram_hash(value), value),
            )[:MAX_INDEX_TOKENS_PER_NAME]
            for bucket_id in {
                _trigram_hash(token) % MAX_TOKEN_BUCKETS for token in selected_tokens
            }:
                postings = self._token_buckets[bucket_id]
                if len(postings) < MAX_TOKEN_POSTINGS:
                    postings.append(index)
            selected_trigrams = sorted(
                _character_trigrams(normalized), key=lambda value: (_trigram_hash(value), value)
            )[:MAX_INDEX_NGRAMS_PER_NAME]
            for bucket_id in {
                _trigram_hash(trigram) % MAX_NGRAM_BUCKETS for trigram in selected_trigrams
            }:
                postings = self._ngram_buckets[bucket_id]
                if len(postings) < MAX_NGRAM_POSTINGS:
                    postings.append(index)

    def _candidate_indices(self, query_norm: str) -> list[int]:
        counts: Counter[int] = Counter()
        query_token_buckets = {
            _trigram_hash(token) % MAX_TOKEN_BUCKETS
            for token in query_norm.split()
            if len(token) >= 3 and token not in INDEX_STOPWORDS
        }
        token_buckets = sorted(
            ((len(self._token_buckets[bucket_id]), bucket_id) for bucket_id in query_token_buckets),
            key=lambda entry: (entry[0], entry[1]),
        )
        for posting_count, bucket_id in token_buckets[:MAX_QUERY_TOKEN_BUCKETS]:
            if posting_count:
                counts.update(self._token_buckets[bucket_id])
        if counts:
            ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
            return [index for index, _ in ranked[:MAX_CANDIDATE_POOL]]

        # A full-catalog fuzzy scan is an easy CPU DoS at 100k SKUs × 200 tags.
        # Use fixed hash buckets for a memory-bounded typo-tolerant fallback.
        query_buckets = {
            _trigram_hash(trigram) % MAX_NGRAM_BUCKETS
            for trigram in _character_trigrams(query_norm)
        }
        available = sorted(
            ((len(self._ngram_buckets[bucket_id]), bucket_id) for bucket_id in query_buckets),
            key=lambda entry: (entry[0], entry[1]),
        )
        for posting_count, bucket_id in available[:MAX_QUERY_NGRAMS]:
            if posting_count:
                counts.update(self._ngram_buckets[bucket_id])
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [index for index, _ in ranked[:MAX_CANDIDATE_POOL]]

    def compute_match_score(
        self,
        query_raw: str,
        query_norm: str,
        query_attrs: dict[str, Any],
        cand_raw: str,
        cand_norm: str,
        cand_attrs: dict[str, Any],
    ) -> float:
        del query_raw, cand_raw
        if _attribute_conflict(query_attrs, cand_attrs):
            return 0.0
        sort_score = fuzz.token_sort_ratio(query_norm, cand_norm)
        set_score = fuzz.token_set_ratio(query_norm, cand_norm)
        score = 0.45 * sort_score + 0.55 * set_score

        for key, bonus in (("fat", 7.0), ("weight_g", 7.0), ("volume_ml", 7.0), ("count", 4.0)):
            query_value = query_attrs.get(key)
            candidate_value = cand_attrs.get(key)
            if query_value is not None and candidate_value is not None:
                score += bonus
            elif query_value is not None and candidate_value is None:
                score -= 8.0
        return round(max(0.0, min(100.0, score)), 1)

    def match_item(
        self,
        recognized_name: str,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        top_k: int = 3,
        *,
        brand: str | None = None,
        weight_volume: str | None = None,
        min_margin: float = DEFAULT_MIN_MARGIN,
    ) -> dict[str, Any]:
        if not self.catalog_records or not str(recognized_name or "").strip():
            return self.empty_match()
        top_k = max(1, min(int(top_k), 10))
        query_parts = [str(recognized_name)]
        if brand and str(brand).casefold() not in str(recognized_name).casefold():
            query_parts.append(str(brand))
        if weight_volume:
            query_parts.append(str(weight_volume))
        query_raw = " ".join(query_parts)
        query_norm = normalize_product_text(query_raw)
        query_attrs = extract_attributes(query_raw)
        explicit_brand = str(brand or "").strip()

        candidates: list[dict[str, Any]] = []
        for index in self._candidate_indices(query_norm):
            record = self.catalog_records[index]
            candidate_norm = self.normalized_catalog_names[index]
            if explicit_brand and not _brand_is_present(explicit_brand, candidate_norm):
                continue
            score = self.compute_match_score(
                query_raw,
                query_norm,
                query_attrs,
                record["наименование_товара"],
                candidate_norm,
                self.catalog_attributes[index],
            )
            if score <= 0:
                continue
            candidates.append(
                {
                    "sku": record["код_товара"],
                    "name": record["наименование_товара"],
                    "purchase_price": record["цена_закупки"],
                    "sale_price": record["цена_продажи"],
                    "promo_price": record["цена_на_промо"],
                    "score": score,
                }
            )
        candidates.sort(key=lambda item: (-item["score"], item["sku"]))
        top = candidates[:top_k]
        if not top or top[0]["score"] < threshold:
            result = self.empty_match("Недостаточная точность сопоставления")
            result["match_score"] = top[0]["score"] if top else 0.0
            result["candidates"] = top
            return result
        if len(top) > 1 and top[0]["score"] - top[1]["score"] < min_margin:
            result = self.empty_match("Неоднозначное сопоставление — требуется проверка")
            result["match_score"] = top[0]["score"]
            result["candidates"] = top
            return result

        best = top[0]
        return {
            "matched_sku": best["sku"],
            "matched_name": best["name"],
            "our_purchase_price": best["purchase_price"],
            "our_sale_price": best["sale_price"],
            "our_promo_price": best["promo_price"],
            "match_score": best["score"],
            "match_reason": "Автоматическое сопоставление",
            "candidates": top,
        }

    def match_all(
        self,
        recognized_items: list[dict[str, Any]],
        threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> list[dict[str, Any]]:
        results = []
        for item in recognized_items:
            match = self.match_item(
                str(item.get("product_name") or ""),
                threshold=threshold,
                brand=item.get("brand"),
                weight_volume=item.get("weight_volume"),
            )
            results.append({**item, **match})
        return results
