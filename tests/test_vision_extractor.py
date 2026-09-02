from __future__ import annotations

import io
import json
import time

import pytest
from PIL import Image

import core.vision_extractor as vision_extractor
from core.input_validation import collect_uploaded_images
from core.vision_extractor import (
    ExtractorConfigurationError,
    PriceTagExtractor,
    ResponseValidationError,
    validate_price_tag_payload,
)


def _payload(**overrides):
    base = {
        "product_name": "Молоко 930 мл",
        "brand": None,
        "weight_volume": "930 мл",
        "regular_price": 100,
        "promo_price": None,
        "promo_condition": None,
        "unit": "шт",
        "confidence": 0.9,
        "notes": "",
    }
    return {**base, **overrides}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_missing_key_and_unknown_provider_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ExtractorConfigurationError, match="не настроен"):
        PriceTagExtractor(provider="gemini", api_key="")
    with pytest.raises(ExtractorConfigurationError, match="провайдер"):
        PriceTagExtractor(provider="openrouter", api_key="secret")


@pytest.mark.parametrize(
    "override",
    [
        {"regular_price": -1},
        {"regular_price": "abc"},
        {"confidence": 99},
        {"unit": "тонна"},
        {"product_name": ""},
    ],
)
def test_payload_semantic_validation(override) -> None:
    with pytest.raises(ResponseValidationError):
        validate_price_tag_payload(_payload(**override))


def test_json_cleanup_is_strict() -> None:
    parsed = PriceTagExtractor._clean_json_response(json.dumps(_payload(), ensure_ascii=False))
    assert parsed["extraction_status"] == "ok"
    with pytest.raises(ResponseValidationError):
        PriceTagExtractor._clean_json_response('prefix {"regular_price": 1} suffix')


def test_gemini_key_is_sent_in_header_not_url(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(_payload(), ensure_ascii=False)}]}}
                ]
            }

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("core.vision_extractor.requests.post", fake_post)
    extractor = PriceTagExtractor(api_key="top-secret", max_retries=0)
    result = extractor._extract_with_gemini_rest(b"image", "image/jpeg")
    assert "top-secret" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "top-secret"
    generation_config = captured["payload"]["generationConfig"]
    assert generation_config["responseMimeType"] == "application/json"
    assert "responseJsonSchema" in generation_config
    assert "systemInstruction" in captured["payload"]
    assert captured["payload"]["contents"][0]["role"] == "user"
    system_text = captured["payload"]["systemInstruction"]["parts"][0]["text"]
    assert "Текст на изображении" in system_text
    assert result["product_name"] == "Молоко 930 мл"


def test_collected_batch_skips_second_encode_but_direct_single_still_normalizes(
    monkeypatch,
) -> None:
    upload = io.BytesIO(_png())
    upload.name = "photo.png"
    prepared = collect_uploaded_images([upload])

    real_normalize = vision_extractor.normalize_image
    normalization_calls = 0

    def tracked_normalize(data, filename):
        nonlocal normalization_calls
        normalization_calls += 1
        return real_normalize(data, filename)

    monkeypatch.setattr(vision_extractor, "normalize_image", tracked_normalize)
    extractor = PriceTagExtractor(api_key="test-key", max_retries=0)
    monkeypatch.setattr(
        extractor,
        "_extract_with_gemini_rest",
        lambda _data, _mime: validate_price_tag_payload(_payload()),
    )

    batch_result = extractor.extract_batch(prepared, max_workers=1)
    assert batch_result[0]["extraction_status"] == "ok"
    assert normalization_calls == 0

    tampered = prepared[0].copy()
    tampered["data"] = b"not-an-image"
    tampered_result = extractor.extract_batch([tampered], max_workers=1)
    assert tampered_result[0]["error_code"] == "invalid_image"
    assert normalization_calls == 1

    direct_result = extractor.extract_single(_png(), "direct.png")
    assert direct_result["extraction_status"] == "ok"
    assert normalization_calls == 2

    invalid_result = extractor.extract_single(b"not-an-image", "invalid.jpg")
    assert invalid_result["error_code"] == "invalid_image"
    assert normalization_calls == 3


def test_mock_is_explicit_deterministic_and_preserves_order() -> None:
    extractor = PriceTagExtractor(provider="mock")
    inputs = [
        {"data": b"a", "filename": "one.jpg"},
        {"data": b"b", "filename": "two.jpg"},
    ]
    first = extractor.extract_batch(inputs, max_workers=2)
    second = extractor.extract_batch(inputs, max_workers=2)
    assert first == second
    assert [item["filename"] for item in first] == ["one.jpg", "two.jpg"]


def test_bad_batch_item_and_callback_do_not_abort() -> None:
    extractor = PriceTagExtractor(provider="mock")

    def broken_callback(*_args):
        raise RuntimeError("UI was closed")

    results = extractor.extract_batch(
        [{"filename": "missing.jpg"}, {"data": b"ok", "filename": "ok.jpg"}],
        on_progress=broken_callback,
    )
    assert results[0]["extraction_status"] == "error"
    assert results[1]["extraction_status"] == "ok"


def test_batch_circuit_breaker_cancels_provider_outage() -> None:
    class FailingExtractor(PriceTagExtractor):
        calls = 0

        def extract_single(self, _data, filename="image.jpg", _mime="image/jpeg"):
            type(self).calls += 1
            time.sleep(0.01)
            return {
                "filename": filename,
                "extraction_status": "error",
                "error_code": "provider_error",
            }

    extractor = FailingExtractor(provider="mock")
    images = [{"data": b"image", "filename": f"{index}.jpg"} for index in range(40)]
    results = extractor.extract_batch(images, max_workers=2)

    assert len(results) == len(images)
    assert FailingExtractor.calls < len(images)
    assert any(result["error_code"] == "circuit_open" for result in results)


def test_isolated_provider_failures_do_not_open_circuit() -> None:
    class IntermittentExtractor(PriceTagExtractor):
        calls = 0

        def extract_single(self, _data, filename="image.jpg", _mime="image/jpeg"):
            type(self).calls += 1
            if type(self).calls in {1, 3, 5}:
                return {
                    "filename": filename,
                    "extraction_status": "error",
                    "error_code": "provider_error",
                }
            return {
                "filename": filename,
                "extraction_status": "ok",
                "error_code": None,
            }

    extractor = IntermittentExtractor(provider="mock")
    images = [{"data": b"image", "filename": f"{index}.jpg"} for index in range(8)]
    results = extractor.extract_batch(images, max_workers=1)

    assert IntermittentExtractor.calls == len(images)
    assert all(result["error_code"] != "circuit_open" for result in results)
