from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "app.py"
SAMPLE_CATALOG_PATH = PROJECT_ROOT / "data" / "samples" / "samberi_catalog_sample.xlsx"


def _configure_test_app(monkeypatch, *, auth_disabled: bool, password: str = "") -> None:
    monkeypatch.setenv("APP_AUTH_DISABLED", "true" if auth_disabled else "false")
    monkeypatch.setenv("APP_PASSWORD", password)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 500), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _gemini_response() -> Mock:
    payload = {
        "product_name": "Молоко Домик в деревне ультрапастеризованное 3.2% 930мл",
        "brand": "Домик в деревне",
        "weight_volume": "930 мл",
        "regular_price": 109.9,
        "promo_price": 89.9,
        "promo_condition": "по карте",
        "unit": "шт",
        "confidence": 0.98,
        "notes": "",
    }
    response = Mock()
    response.status_code = 200
    response.headers = {}
    response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(payload, ensure_ascii=False)}],
                }
            }
        ]
    }
    return response


def _button_by_label(app: AppTest, label_fragment: str):
    return next(button for button in app.button if label_fragment in button.label)


def test_streamlit_full_flow_survives_rerun(monkeypatch) -> None:
    _configure_test_app(monkeypatch, auth_disabled=True)
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()

    assert not app.exception
    assert not app.error
    assert [tab.label for tab in app.tabs] == [
        "📸 Загрузка",
        "📋 Таблица",
        "📊 Аналитика",
        "📥 Excel",
    ]
    assert len(app.tabs[0].file_uploader) == 2

    app.tabs[0].file_uploader[0].upload(
        "catalog.xlsx",
        SAMPLE_CATALOG_PATH.read_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    app.tabs[0].file_uploader[1].upload("tag.jpg", _jpeg_bytes(), "image/jpeg")
    app.run()

    run_button = _button_by_label(app, "Начать мониторинг")
    assert not app.exception
    assert not app.error
    assert run_button.disabled is False

    with patch(
        "core.vision_extractor.requests.post",
        return_value=_gemini_response(),
    ) as gemini_post:
        run_button.click().run()

    assert gemini_post.call_count == 1
    assert not app.exception
    assert not app.error
    assert len(app.session_state["processed_results"]) == 1
    assert app.session_state["processed_results"][0]["matched_sku"] == "104921"
    assert len(app.tabs[1].dataframe) == 1
    assert len(app.tabs[1].dataframe[0].value) == 1
    assert len(app.tabs[2].metric) == 5
    assert len(app.tabs[2].get("plotly_chart")) == 2
    assert len(app.tabs[3].download_button) == 1
    assert app.tabs[3].download_button[0].proto.url.endswith(".xlsx")

    app.run()

    assert not app.exception
    assert not app.error
    assert len(app.session_state["processed_results"]) == 1
    assert app.session_state["processed_results"][0]["matched_sku"] == "104921"
    assert len(app.tabs[1].dataframe) == 1
    assert len(app.tabs[2].metric) == 5
    assert len(app.tabs[2].get("plotly_chart")) == 2
    assert len(app.tabs[3].download_button) == 1


def test_streamlit_fails_closed_without_password(monkeypatch) -> None:
    _configure_test_app(monkeypatch, auth_disabled=False, password="")

    app = AppTest.from_file(APP_PATH, default_timeout=30).run()

    assert not app.exception
    assert any("Доступ закрыт" in error.value for error in app.error)
    assert len(app.tabs) == 0
    assert len(app.get("file_uploader")) == 0
