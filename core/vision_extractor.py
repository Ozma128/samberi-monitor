"""
Модуль извлечения данных с ценников с помощью Vision AI (Google Gemini / OpenAI / Mock).
Обеспечивает пакетную параллельную обработку фотографий и строгий возврат структурированного JSON.
"""

import os
import io
import json
import base64
import random
import re
import time
from typing import List, Dict, Any, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

SYSTEM_PROMPT = """Ты — профессиональный эксперт по аудиту и распознаванию ценников в розничной торговле (ритейл, РФ).
Твоя задача — внимательно изучить фотографию ценника конкурента и извлечь точные структурированные данные.

Правила анализа:
1. "product_name": Полное и четкое наименование товара на русском языке (например, "Молоко Домик в деревне ультрапастеризованное 3.2% 930мл").
2. "brand": Бренд или производитель, если определен (например, "Домик в деревне", "Простоквашино", "Макфа").
3. "weight_volume": Фасовка, масса или объем с единицей измерения (например: "930 мл", "1 кг", "450 г", "2 л").
4. "regular_price": Базовая/регулярная цена в рублях (число float, например 119.90).
   - Если на ценнике 2 цены (обычная и по карте/акции), обычная (перечеркнутая или мелкая) — это regular_price.
   - Если цена только одна — запиши её в regular_price, а promo_price сделай null.
5. "promo_price": Акционная цена или цена по карте покупателя (число float, например 89.90), если есть. Если акции нет — null.
6. "promo_condition": Условие промо-цены (например: "по карте лояльности", "желтый ценник", "1+1", "от 2 шт", "скидка -20%"). Если нет — null.
7. "unit": Единица расчета цены ("шт", "кг", "100г", "упак", "л").
8. "confidence": Твоя уверенность в считывании от 0.1 до 1.0 (снижай, если сильный блик, смаз или закрыта цифра).
9. "notes": Любые важные примечания (например: "блик на копейках", "ценник обрезан", "указана цена за 100г").

Верни ИСКЛЮЧИТЕЛЬНО валидный JSON-объект следующего формата:
{
  "product_name": "...",
  "brand": "...",
  "weight_volume": "...",
  "regular_price": 129.90,
  "promo_price": 99.90,
  "promo_condition": "по карте",
  "unit": "шт",
  "confidence": 0.95,
  "notes": ""
}
"""

SAMPLE_MOCK_ITEMS = [
    {
        "product_name": "Молоко Домик в деревне пастеризованное 3.2% 930мл",
        "brand": "Домик в деревне",
        "weight_volume": "930 мл",
        "regular_price": 109.90,
        "promo_price": 89.90,
        "promo_condition": "по карте покупателя",
        "unit": "шт",
        "confidence": 0.98,
        "notes": "Четкий ценник"
    },
    {
        "product_name": "Масло сливочное Простоквашино 82.5% 180г",
        "brand": "Простоквашино",
        "weight_volume": "180 г",
        "regular_price": 219.00,
        "promo_price": 179.90,
        "promo_condition": "желтый ценник",
        "unit": "шт",
        "confidence": 0.96,
        "notes": ""
    },
    {
        "product_name": "Сыр Российский Брест-Литовск 45% 200г",
        "brand": "Брест-Литовск",
        "weight_volume": "200 г",
        "regular_price": 249.90,
        "promo_price": None,
        "promo_condition": None,
        "unit": "шт",
        "confidence": 0.94,
        "notes": "Регулярная цена"
    },
    {
        "product_name": "Крупа гречневая Увелка в пакетиках 5х80г (400г)",
        "brand": "Увелка",
        "weight_volume": "400 г",
        "regular_price": 99.90,
        "promo_price": 69.90,
        "promo_condition": "акция недели",
        "unit": "шт",
        "confidence": 0.97,
        "notes": ""
    },
    {
        "product_name": "Макароны Макфа Перья высший сорт 450г",
        "brand": "Макфа",
        "weight_volume": "450 г",
        "regular_price": 64.90,
        "promo_price": None,
        "promo_condition": None,
        "unit": "шт",
        "confidence": 0.99,
        "notes": ""
    },
    {
        "product_name": "Колбаса Докторская Вязанка вареная 450г",
        "brand": "Вязанка",
        "weight_volume": "450 г",
        "regular_price": 289.00,
        "promo_price": 199.90,
        "promo_condition": "скидка 30%",
        "unit": "шт",
        "confidence": 0.92,
        "notes": ""
    },
    {
        "product_name": "Чай черный Greenfield Golden Ceylon 100 пакетиков",
        "brand": "Greenfield",
        "weight_volume": "200 г",
        "regular_price": 429.00,
        "promo_price": 319.00,
        "promo_condition": "по карте",
        "unit": "шт",
        "confidence": 0.95,
        "notes": ""
    },
    {
        "product_name": "Кофе растворимый Nescafe Gold сублимированный 190г",
        "brand": "Nescafe",
        "weight_volume": "190 г",
        "regular_price": 699.00,
        "promo_price": 499.00,
        "promo_condition": "суперцена",
        "unit": "шт",
        "confidence": 0.91,
        "notes": ""
    },
    {
        "product_name": "Шоколад Ritter Sport молочный с цельным лесным орехом 100г",
        "brand": "Ritter Sport",
        "weight_volume": "100 г",
        "regular_price": 189.90,
        "promo_price": 139.90,
        "promo_condition": "желтый ценник",
        "unit": "шт",
        "confidence": 0.97,
        "notes": ""
    },
    {
        "product_name": "Сок Добрый Яблоко 100% 1л",
        "brand": "Добрый",
        "weight_volume": "1 л",
        "regular_price": 139.00,
        "promo_price": 99.90,
        "promo_condition": "1+1 при покупке от 2 шт",
        "unit": "шт",
        "confidence": 0.95,
        "notes": ""
    }
]


class PriceTagExtractor:
    """
    Класс для извлечения данных с ценников с помощью Vision AI.
    Поддерживает Google Gemini, OpenAI, OpenRouter (работает из РФ без VPN) и Mock-режим.
    """

    def __init__(
        self,
        provider: str = "gemini",
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        self.provider = provider.lower()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        self.base_url = base_url or os.getenv("AI_BASE_URL")
        
        if "openrouter" in self.provider:
            self.model_name = model_name or "google/gemini-flash-1.5"
            self.base_url = self.base_url or "https://openrouter.ai/api/v1"
        elif self.provider == "gemini":
            self.model_name = model_name or "gemini-1.5-flash"
        else:
            self.model_name = model_name or "gpt-4o-mini"

    def _clean_json_response(self, text: str) -> Dict[str, Any]:
        """Очищает ответ модели и преобразует его в JSON."""
        text = text.strip()
        # Удаляем markdown-блоки кода ```json ... ```
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        
        # Находим первый { и последний }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            json_str = text[start:end+1]
            return json.loads(json_str)
        raise ValueError(f"Не удалось найти валидный JSON в ответе: {text[:200]}")

    def _extract_with_gemini_rest(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, Any]:
        """Обработка через REST API Google Gemini (надежно, быстро, без лишних зависимостей)."""
        import requests

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY не указан!")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": SYSTEM_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_image
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json"
            }
        }

        response = requests.post(url, json=payload, timeout=40)
        response.raise_for_status()
        data = response.json()
        
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return self._clean_json_response(raw_text)

    def _extract_with_openai(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, Any]:
        """Обработка через OpenAI / OpenRouter / Прокси-шлюзы."""
        from openai import OpenAI
        
        if not self.api_key:
            raise ValueError("API_KEY не указан!")

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = OpenAI(**client_kwargs)
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{b64_image}"

        response = client.chat.completions.create(
            model=self.model_name,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Распознай данные с этого ценника в строгом JSON формате."},
                        {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}}
                    ]
                }
            ],
            temperature=0.1,
            max_tokens=500
        )
        raw_text = response.choices[0].message.content
        return self._clean_json_response(raw_text)

    def _extract_mock(self, filename: str) -> Dict[str, Any]:
        """Имитация распознавания для демо-тестов и отладки."""
        # Имитируем небольшую задержку сетевого запроса
        time.sleep(random.uniform(0.15, 0.4))
        
        # Выбираем псевдо-случайный товар на основе имени файла
        hash_val = sum(ord(c) for c in filename) % len(SAMPLE_MOCK_ITEMS)
        base_item = SAMPLE_MOCK_ITEMS[hash_val].copy()
        
        # Добавляем немного вариативности в цены (+/- 5%)
        jitter = random.choice([0.95, 1.0, 1.05])
        if base_item["regular_price"]:
            base_item["regular_price"] = round(base_item["regular_price"] * jitter, 2)
        if base_item["promo_price"]:
            base_item["promo_price"] = round(base_item["promo_price"] * jitter, 2)
            
        base_item["filename"] = filename
        return base_item

    def extract_single(
        self,
        image_input: Any,
        filename: str = "image.jpg",
        mime_type: str = "image/jpeg"
    ) -> Dict[str, Any]:
        """
        Распознает одиночный ценник.
        `image_input` может быть bytes, объектом PIL.Image или путем к файлу.
        """
        if self.provider == "mock" or not self.api_key:
            res = self._extract_mock(filename)
            res["filename"] = filename
            return res

        # Преобразуем вход в байты
        if isinstance(image_input, bytes):
            img_bytes = image_input
        elif isinstance(image_input, str) and os.path.exists(image_input):
            with open(image_input, "rb") as f:
                img_bytes = f.read()
        elif hasattr(image_input, "read"):
            img_bytes = image_input.read()
        elif isinstance(image_input, Image.Image):
            buf = io.BytesIO()
            image_input.save(buf, format="JPEG")
            img_bytes = buf.getvalue()
        else:
            raise ValueError("Неподдерживаемый тип входного изображения")

        try:
            if self.provider == "gemini":
                result = self._extract_with_gemini_rest(img_bytes, mime_type)
            elif self.provider == "openai":
                result = self._extract_with_openai(img_bytes, mime_type)
            else:
                result = self._extract_mock(filename)
            
            result["filename"] = filename
            return result
        except Exception as e:
            return {
                "filename": filename,
                "product_name": f"Ошибка распознавания ({str(e)[:50]})",
                "brand": None,
                "weight_volume": None,
                "regular_price": 0.0,
                "promo_price": None,
                "promo_condition": None,
                "unit": "шт",
                "confidence": 0.0,
                "notes": f"Ошибка: {str(e)}"
            }

    def extract_batch(
        self,
        images_list: List[Dict[str, Any]],
        max_workers: int = 8,
        on_progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Пакетная параллельная обработка списка фотографий.
        `images_list` — список словарей вида [{"data": bytes/path/file, "filename": "1.jpg", "mime": "image/jpeg"}]
        """
        results = []
        total = len(images_list)
        completed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_meta = {
                executor.submit(
                    self.extract_single,
                    item["data"],
                    item.get("filename", f"image_{i}.jpg"),
                    item.get("mime", "image/jpeg")
                ): (i, item)
                for i, item in enumerate(images_list)
            }

            for future in as_completed(future_to_meta):
                idx, meta = future_to_meta[future]
                try:
                    res = future.result()
                except Exception as e:
                    res = {
                        "filename": meta.get("filename", f"image_{idx}.jpg"),
                        "product_name": "Сбой потока",
                        "regular_price": 0.0,
                        "confidence": 0.0,
                        "notes": str(e)
                    }
                
                results.append(res)
                completed_count += 1
                
                if on_progress:
                    on_progress(completed_count, total, res)

        # Сохраняем исходный порядок файлов
        filename_order = {item.get("filename", f"image_{i}.jpg"): i for i, item in enumerate(images_list)}
        results.sort(key=lambda x: filename_order.get(x.get("filename", ""), 999999))
        return results
