"""Streamlit-интерфейс системы мониторинга ценников Самбери."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.analytics import calculate_price_metrics, summarize_price_index
from core.exporter import export_comparison_to_excel
from core.input_validation import (
    CATALOG_UPLOAD_EXTENSIONS,
    MAX_CATALOG_BYTES,
    MAX_IMAGES,
    MAX_TOTAL_PREVIEW_BYTES,
    MAX_TOTAL_UPLOAD_BYTES,
    MIB,
    InputValidationError,
    collect_uploaded_images,
    create_image_preview,
    load_catalog_file,
    validate_upload_manifest,
)
from core.matcher import CatalogMatcher, CatalogSchemaError
from core.pipeline import process_monitoring_batch
from core.rate_limit import get_application_guardrails
from core.settings import AppSettings, load_settings
from core.vision_extractor import (
    ExtractorConfigurationError,
    PriceTagExtractor,
    ResponseValidationError,
    validate_price_tag_payload,
)

_GUARDRAILS = get_application_guardrails()

st.set_page_config(
    page_title="Самбери — Мониторинг ценников",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .stApp { background: linear-gradient(135deg, #0f172a, #172033 55%, #0f172a); }
      .block-container { max-width: 1400px; padding-top: 1.5rem; }
      h1, h2, h3 { letter-spacing: -0.02em; }
      [data-testid="stMetric"] {
        background: rgba(30, 41, 59, 0.72);
        border: 1px solid rgba(148, 163, 184, 0.16);
        border-radius: 14px;
        padding: 16px;
      }
      [data-testid="stFileUploaderDropzone"] {
        background: rgba(30, 58, 138, 0.13);
        border-radius: 14px;
      }
      .stButton > button, .stDownloadButton > button { border-radius: 10px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _streamlit_secrets() -> dict[str, Any]:
    try:
        return dict(st.secrets)
    except (FileNotFoundError, KeyError, OSError):
        return {}


def _parse_catalog(data: bytes, filename: str) -> pd.DataFrame:
    return load_catalog_file(data, filename)


def _upload_fingerprint(files: list[Any] | None) -> str | None:
    if not files:
        return None
    validate_upload_manifest(files)
    digest = hashlib.sha256()
    for uploaded in files:
        digest.update(str(uploaded.name).encode("utf-8", errors="replace"))
        size = int(getattr(uploaded, "size", 0) or 0)
        digest.update(size.to_bytes(8, "big"))
        digest.update(str(getattr(uploaded, "file_id", "")).encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _invalidate_results() -> None:
    st.session_state["processed_results"] = []
    st.session_state["uploaded_images_cache"] = {}
    st.session_state["analysis_fingerprint"] = None
    st.session_state["analysis_finished_at"] = None


def _clear_catalog_state() -> None:
    st.session_state["catalog_df"] = None
    st.session_state["catalog_fingerprint"] = None
    st.session_state["catalog_filename"] = None
    st.session_state["catalog_matcher"] = None
    _invalidate_results()


def _reset_analysis() -> None:
    for key in (
        "catalog_df",
        "catalog_fingerprint",
        "catalog_filename",
        "catalog_matcher",
        "photo_fingerprint",
        "processed_results",
        "uploaded_images_cache",
        "analysis_fingerprint",
        "analysis_finished_at",
        "catalog_uploader",
        "photos_uploader",
    ):
        st.session_state.pop(key, None)


def _initialize_state() -> None:
    defaults = {
        "catalog_df": None,
        "catalog_fingerprint": None,
        "catalog_filename": None,
        "catalog_matcher": None,
        "photo_fingerprint": None,
        "processed_results": [],
        "uploaded_images_cache": {},
        "analysis_fingerprint": None,
        "analysis_finished_at": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_upload(settings: AppSettings) -> None:
    st.subheader("Загрузка и новый анализ")
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### 1. Справочник Самбери")
        st.caption(
            "CSV/TSV или Excel (XLS, XLSX, XLSM, XLSB, шаблоны и ODS): "
            "нужны SKU, наименование и регулярная или промо-цена; закупочная необязательна. "
            "Справочник нормализуется локально, Gemini получает только фотографии."
        )
        catalog_file = st.file_uploader(
            "Справочник",
            type=list(CATALOG_UPLOAD_EXTENSIONS),
            key="catalog_uploader",
            label_visibility="collapsed",
        )
        if catalog_file is not None:
            declared_size = int(getattr(catalog_file, "size", 0) or 0)
            if declared_size > MAX_CATALOG_BYTES:
                st.error(f"Справочник больше {MAX_CATALOG_BYTES // (1024 * 1024)} МБ.")
                _clear_catalog_state()
                data = b""
            else:
                data = bytes(catalog_file.getvalue())
                if not data:
                    st.error("Файл справочника пуст.")
                    _clear_catalog_state()
            fingerprint = hashlib.sha256(
                str(catalog_file.name).encode("utf-8", errors="replace") + b"\0" + data
            ).hexdigest()
            if data and fingerprint != st.session_state.catalog_fingerprint:
                try:
                    frame = _parse_catalog(data, catalog_file.name)
                    matcher = CatalogMatcher(frame)  # Проверяем схему до дорогостоящего OCR.
                except (InputValidationError, CatalogSchemaError) as exc:
                    _clear_catalog_state()
                    st.error(str(exc))
                else:
                    st.session_state["catalog_df"] = frame
                    st.session_state["catalog_fingerprint"] = fingerprint
                    st.session_state["catalog_filename"] = catalog_file.name
                    st.session_state["catalog_matcher"] = matcher
                    _invalidate_results()
            frame = st.session_state.catalog_df
            matcher = st.session_state.get("catalog_matcher")
            if isinstance(frame, pd.DataFrame) and isinstance(matcher, CatalogMatcher):
                status = f"Принято товарных позиций: {len(matcher.catalog_records):,}"
                if matcher.catalog_skipped_rows:
                    status += (
                        " · пропущено строк без цен/повторных заголовков: "
                        f"{matcher.catalog_skipped_rows:,}"
                    )
                st.success(status)
                if matcher.catalog_header_rows_skipped:
                    st.caption(
                        "Шапка таблицы найдена автоматически; до начала данных пропущено строк: "
                        f"{matcher.catalog_header_rows_skipped:,}."
                    )
                with st.expander("Предпросмотр справочника"):
                    preview_columns = [
                        "код_товара",
                        "наименование_товара",
                        "цена_закупки",
                        "цена_продажи",
                        "цена_на_промо",
                    ]
                    st.dataframe(
                        matcher.catalog_df[preview_columns].head(10),
                        width="stretch",
                        hide_index=True,
                    )
        elif st.session_state.catalog_fingerprint is not None:
            _clear_catalog_state()

    with right:
        st.markdown("#### 2. Фотографии ценников")
        st.caption(
            f"JPEG, PNG или ZIP. До {MAX_IMAGES} изображений и "
            f"{MAX_TOTAL_UPLOAD_BYTES // MIB} МБ за запуск; содержимое проверяется до OCR."
        )
        photo_files = st.file_uploader(
            "Фотографии",
            type=["jpg", "jpeg", "png", "zip"],
            accept_multiple_files=True,
            key="photos_uploader",
            label_visibility="collapsed",
        )
        upload_error: str | None = None
        try:
            current_photo_fingerprint = _upload_fingerprint(photo_files)
        except InputValidationError as exc:
            current_photo_fingerprint = None
            upload_error = str(exc)
            st.error(upload_error)
        if current_photo_fingerprint != st.session_state.photo_fingerprint:
            st.session_state["photo_fingerprint"] = current_photo_fingerprint
            _invalidate_results()
        if photo_files:
            st.success(f"Выбрано файлов: {len(photo_files)}")

    catalog_matcher = st.session_state.get("catalog_matcher")
    catalog_ready = isinstance(st.session_state.catalog_df, pd.DataFrame) and isinstance(
        catalog_matcher, CatalogMatcher
    )
    photos_ready = bool(photo_files) and upload_error is None
    api_ready = bool(settings.gemini_api_key)
    if not api_ready:
        st.error("GEMINI_API_KEY не настроен. Синтетические данные вместо OCR не используются.")

    action_left, action_right = st.columns([4, 1])
    with action_left:
        run = st.button(
            "🚀 Начать мониторинг и расчёт Price Index",
            type="primary",
            width="stretch",
            disabled=not (catalog_ready and photos_ready and api_ready),
        )
    with action_right:
        if st.button("Очистить", width="stretch"):
            _reset_analysis()
            st.rerun()

    if not run:
        return

    allowed, retry_after = _GUARDRAILS.analysis_starts.try_acquire()
    if not allowed:
        st.error(f"Лимит запусков исчерпан. Повторите через {int(retry_after) + 1} сек.")
        return
    if not _GUARDRAILS.concurrent_analyses.acquire(blocking=False):
        st.error("Сервер уже обрабатывает максимальное число пакетов. Повторите позже.")
        return

    try:
        try:
            images = collect_uploaded_images(photo_files or [])
            extractor = PriceTagExtractor(
                provider="gemini",
                api_key=settings.gemini_api_key,
                model_name=settings.gemini_model,
            )
        except (InputValidationError, CatalogSchemaError, ExtractorConfigurationError) as exc:
            st.error(str(exc))
            return
        except Exception:
            st.error("Не удалось безопасно подготовить загруженные файлы.")
            return

        progress = st.progress(0.0)
        status = st.empty()

        def on_progress(done: int, total: int, result: dict[str, Any]) -> None:
            progress.progress(done / total)
            label = result.get("product_name") or "ошибка распознавания"
            status.caption(f"Обработано {done} из {total}: {str(label)[:80]}")

        started = time.monotonic()
        try:
            processed = process_monitoring_batch(
                catalog_matcher,
                images,
                extractor,
                match_threshold=settings.match_threshold,
                min_confidence=settings.min_recognition_confidence,
                max_workers=settings.vision_workers,
                on_progress=on_progress,
            )
            preview_cache: dict[str, bytes] = {}
            preview_total = 0
            for image in images:
                try:
                    preview = create_image_preview(image["data"])
                except InputValidationError:
                    continue
                if preview_total + len(preview) > MAX_TOTAL_PREVIEW_BYTES:
                    break
                preview_cache[image["filename"]] = preview
                preview_total += len(preview)
        except ExtractorConfigurationError:
            st.error(
                "Gemini отклонил ключ или модель. Проверьте GEMINI_API_KEY и "
                "GEMINI_MODEL в Streamlit Secrets."
            )
            return
        except Exception:
            st.error("Анализ не завершён из-за внутренней ошибки. Данные и ключи не выведены.")
            return
    finally:
        _GUARDRAILS.concurrent_analyses.release()

    st.session_state["processed_results"] = processed
    st.session_state["uploaded_images_cache"] = preview_cache
    st.session_state["analysis_fingerprint"] = hashlib.sha256(
        f"{st.session_state.catalog_fingerprint}:{st.session_state.photo_fingerprint}".encode()
    ).hexdigest()
    st.session_state["analysis_finished_at"] = time.time()
    elapsed = time.monotonic() - started
    summary = summarize_price_index(processed)
    status.empty()
    progress.empty()
    st.success(
        f"Готово за {elapsed:.1f} сек.: распознано {summary['successful_recognitions']} "
        f"из {summary['total_items']}, сопоставлено {summary['matched_items']}."
    )
    if summary["failed_recognitions"]:
        st.warning(
            f"Не удалось распознать: {summary['failed_recognitions']}. Эти строки не участвовали в PI."
        )


def _filtered_rows(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    filter_col, search_col = st.columns(2)
    with filter_col:
        status_filter = st.selectbox(
            "Статус",
            ["Все", "Самбери дешевле", "Конкурент дешевле", "Паритет", "Демпинг", "Не определен"],
        )
    with search_col:
        query = st.text_input("Поиск", placeholder="Наименование или SKU").casefold().strip()

    selected: list[dict[str, Any]] = []
    for item in results:
        status = str(item.get("status") or "")
        if status_filter == "Самбери дешевле" and "Самбери дешевле" not in status:
            continue
        if status_filter == "Конкурент дешевле" and "Конкурент дешевле" not in status:
            continue
        if status_filter == "Паритет" and "Паритет" not in status:
            continue
        if status_filter == "Демпинг" and not item.get("is_dumping"):
            continue
        if status_filter == "Не определен" and status != "Не определен":
            continue
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("matched_sku", "matched_name", "product_name", "filename")
        ).casefold()
        if query and query not in haystack:
            continue
        selected.append(item)

    rows = [
        {
            "SKU": item.get("matched_sku"),
            "Наименование Самбери": item.get("matched_name"),
            "Распознано": item.get("product_name"),
            "Закупка ₽": item.get("our_purchase_price"),
            "Цена Самбери ₽": item.get("our_effective_price"),
            "Обычная цена конкурента ₽": item.get("regular_price"),
            "Промо конкурента ₽": item.get("promo_price"),
            "Цена конкурента ₽": item.get("comp_effective_price"),
            "Разница ₽": item.get("effective_diff_rub"),
            "PI %": item.get("price_index_effective"),
            "Матч %": item.get("match_score"),
            "OCR уверенность": item.get("confidence"),
            "Единица": item.get("unit"),
            "Условие промо": item.get("promo_condition"),
            "OCR статус": item.get("extraction_status"),
            "Примечание OCR": item.get("notes"),
            "Статус": item.get("status"),
            "Предупреждение": item.get("alert"),
            "Файл": item.get("filename"),
        }
        for item in selected
    ]
    return selected, pd.DataFrame(rows)


def _render_analysis_context() -> None:
    filename = st.session_state.get("catalog_filename")
    finished_at = st.session_state.get("analysis_finished_at")
    details: list[str] = []
    if filename:
        details.append(f"справочник: {filename}")
    if finished_at:
        details.append(time.strftime("анализ: %d.%m.%Y %H:%M", time.localtime(finished_at)))
    if details:
        st.caption(" · ".join(details) + " · результаты хранятся только в текущей сессии")


def _catalog_matcher() -> CatalogMatcher | None:
    matcher = st.session_state.get("catalog_matcher")
    if isinstance(matcher, CatalogMatcher):
        return matcher
    catalog = st.session_state.get("catalog_df")
    if not isinstance(catalog, pd.DataFrame):
        return None
    try:
        matcher = CatalogMatcher(catalog)
    except CatalogSchemaError:
        return None
    st.session_state["catalog_matcher"] = matcher
    return matcher


def _apply_catalog_candidate(
    target: dict[str, Any], candidate: dict[str, Any], *, reason: str
) -> dict[str, Any]:
    return calculate_price_metrics(
        {
            **target,
            "matched_sku": candidate["sku"],
            "matched_name": candidate["name"],
            "our_purchase_price": candidate["purchase_price"],
            "our_sale_price": candidate["sale_price"],
            "our_promo_price": candidate["promo_price"],
            "match_score": candidate.get("score", 100.0),
            "match_reason": reason,
        }
    )


def _render_table(settings: AppSettings) -> None:
    results = st.session_state.processed_results
    if not results:
        st.info("Сначала выполните анализ во вкладке «Загрузка».")
        return
    st.subheader("Сравнительная таблица")
    _render_analysis_context()
    st.caption(
        "Фильтр ниже изменяет только отображение таблицы; аналитика и Excel включают всю партию."
    )
    selected, frame = _filtered_rows(results)
    if frame.empty:
        st.warning("По выбранным фильтрам ничего не найдено.")
    else:
        st.dataframe(frame, width="stretch", hide_index=True, height=480)

    st.divider()
    st.markdown("#### Проверка OCR и ручная корректировка")
    filenames = [item.get("filename") for item in selected if item.get("filename")]
    if not filenames:
        st.caption("Нет строк для проверки.")
        return
    chosen_filename = st.selectbox("Ценник", filenames)
    result_index = next(
        index for index, item in enumerate(results) if item.get("filename") == chosen_filename
    )
    target = results[result_index]
    matcher = _catalog_matcher()
    widget_suffix = hashlib.sha256(str(chosen_filename).encode()).hexdigest()[:12]
    image_col, details_col = st.columns([1, 2], gap="large")
    with image_col:
        image_bytes = st.session_state.uploaded_images_cache.get(chosen_filename)
        if image_bytes:
            st.image(image_bytes, caption=chosen_filename, width="stretch")
    with details_col:
        st.write("**Распознано:**", target.get("product_name") or "—")
        st.write("**Бренд:**", target.get("brand") or "—")
        st.write("**Фасовка:**", target.get("weight_volume") or "—")
        st.write(
            "**Цены:**",
            f"обычная {target.get('regular_price') or '—'} ₽ · "
            f"промо {target.get('promo_price') or '—'} ₽ · единица {target.get('unit') or '—'}",
        )
        st.write(
            "**OCR:**",
            f"{target.get('extraction_status') or '—'} · уверенность "
            f"{target.get('confidence') if target.get('confidence') is not None else '—'}",
        )
        if target.get("notes"):
            st.write("**Примечание OCR:**", target["notes"])
        st.write("**Причина матчинга:**", target.get("match_reason") or "—")

        with st.expander("Исправить распознанные данные"):
            units = ["шт", "кг", "100г", "упак", "л"]
            current_unit = target.get("unit") if target.get("unit") in units else "шт"
            with st.form(f"ocr_correction_{widget_suffix}"):
                corrected_name = st.text_input(
                    "Наименование",
                    value=str(target.get("product_name") or ""),
                    max_chars=300,
                )
                corrected_brand = st.text_input(
                    "Бренд",
                    value=str(target.get("brand") or ""),
                    max_chars=120,
                )
                corrected_weight = st.text_input(
                    "Фасовка",
                    value=str(target.get("weight_volume") or ""),
                    max_chars=80,
                )
                regular_price = st.number_input(
                    "Обычная цена, ₽",
                    min_value=0.01,
                    max_value=10_000_000.0,
                    value=float(target.get("regular_price") or 0.01),
                    step=0.01,
                )
                has_promo = st.checkbox(
                    "Есть промо-цена",
                    value=target.get("promo_price") is not None,
                )
                promo_price = st.number_input(
                    "Промо-цена, ₽",
                    min_value=0.01,
                    max_value=10_000_000.0,
                    value=float(target.get("promo_price") or regular_price),
                    step=0.01,
                )
                promo_condition = st.text_input(
                    "Условие промо",
                    value=str(target.get("promo_condition") or ""),
                    max_chars=200,
                )
                unit = st.selectbox("Единица цены", units, index=units.index(current_unit))
                correction_submitted = st.form_submit_button(
                    "Сохранить и пересопоставить",
                    type="primary",
                    width="stretch",
                )
            if correction_submitted:
                try:
                    corrected = validate_price_tag_payload(
                        {
                            "product_name": corrected_name,
                            "brand": corrected_brand or None,
                            "weight_volume": corrected_weight or None,
                            "regular_price": regular_price,
                            "promo_price": promo_price if has_promo else None,
                            "promo_condition": promo_condition if has_promo else None,
                            "unit": unit,
                            "confidence": 1.0,
                            "notes": "Проверено и исправлено вручную.",
                        }
                    )
                except ResponseValidationError as exc:
                    st.error(str(exc))
                else:
                    corrected["filename"] = chosen_filename
                    match = (
                        matcher.match_item(
                            corrected["product_name"],
                            threshold=settings.match_threshold,
                            brand=corrected.get("brand"),
                            weight_volume=corrected.get("weight_volume"),
                        )
                        if matcher is not None
                        else CatalogMatcher.empty_match("Справочник недоступен")
                    )
                    results[result_index] = calculate_price_metrics(
                        {**target, **corrected, **match}
                    )
                    st.session_state["processed_results"] = results
                    st.rerun()

        candidates = target.get("candidates") or []
        if candidates and matcher is not None:
            candidate_index = st.selectbox(
                "Кандидаты автоматического сопоставления",
                range(len(candidates)),
                format_func=lambda index: (
                    f"[{candidates[index]['score']}%] {candidates[index]['sku']} — "
                    f"{candidates[index]['name']}"
                ),
                key=f"candidate_{widget_suffix}",
            )
            if st.button(
                "Применить выбранный SKU",
                type="primary",
                key=f"apply_candidate_{widget_suffix}",
            ):
                candidate = candidates[candidate_index]
                results[result_index] = _apply_catalog_candidate(
                    target, candidate, reason="Ручное подтверждение"
                )
                st.session_state["processed_results"] = results
                st.rerun()

        if matcher is not None:
            manual_sku = st.text_input(
                "Или укажите точный SKU из справочника",
                key=f"manual_sku_{widget_suffix}",
                max_chars=120,
            ).strip()
            manual_col, reset_col = st.columns(2)
            with manual_col:
                apply_manual = st.button(
                    "Применить SKU",
                    key=f"apply_manual_sku_{widget_suffix}",
                    width="stretch",
                    disabled=not manual_sku,
                )
            with reset_col:
                reset_match = st.button(
                    "Снять сопоставление",
                    key=f"reset_match_{widget_suffix}",
                    width="stretch",
                    disabled=target.get("matched_sku") is None,
                )
            if apply_manual:
                record = next(
                    (
                        item
                        for item in matcher.catalog_records
                        if str(item["код_товара"]).casefold() == manual_sku.casefold()
                    ),
                    None,
                )
                if record is None:
                    st.error("Такой SKU не найден в загруженном справочнике.")
                else:
                    candidate = {
                        "sku": record["код_товара"],
                        "name": record["наименование_товара"],
                        "purchase_price": record["цена_закупки"],
                        "sale_price": record["цена_продажи"],
                        "promo_price": record["цена_на_промо"],
                        "score": 100.0,
                    }
                    results[result_index] = _apply_catalog_candidate(
                        target, candidate, reason="SKU указан вручную"
                    )
                    st.session_state["processed_results"] = results
                    st.rerun()
            if reset_match:
                results[result_index] = calculate_price_metrics(
                    {
                        **target,
                        **matcher.empty_match("Сопоставление снято вручную"),
                    }
                )
                st.session_state["processed_results"] = results
                st.rerun()


def _metric_value(value: float | None, suffix: str = "%") -> str:
    return "Н/Д" if value is None else f"{value:.1f}{suffix}"


def _render_analytics() -> None:
    results = st.session_state.processed_results
    if not results:
        st.info("Нет данных для аналитики.")
        return
    st.subheader("Аналитика")
    _render_analysis_context()
    summary = summarize_price_index(results)
    columns = st.columns(5)
    columns[0].metric("Средний PI", _metric_value(summary["avg_price_index"]))
    columns[1].metric("Корзинный PI", _metric_value(summary["basket_price_index"]))
    columns[2].metric("Сопоставлено", f"{summary['matched_items']} / {summary['total_items']}")
    columns[3].metric("Сравнимых цен", summary["comparable_items"])
    columns[4].metric("Демпинг-алерты", summary["dumping_alerts_count"])

    left, right = st.columns(2, gap="large")
    with left:
        distribution = pd.DataFrame(
            [
                {"Статус": "Самбери дешевле", "Количество": summary["samberi_cheaper_count"]},
                {"Статус": "Конкурент дешевле", "Количество": summary["competitor_cheaper_count"]},
                {"Статус": "Паритет", "Количество": summary["parity_count"]},
            ]
        )
        distribution = distribution[distribution["Количество"] > 0]
        if not distribution.empty:
            figure = px.pie(
                distribution,
                names="Статус",
                values="Количество",
                hole=0.5,
                color="Статус",
                color_discrete_map={
                    "Самбери дешевле": "#34D399",
                    "Конкурент дешевле": "#F87171",
                    "Паритет": "#60A5FA",
                },
            )
            figure.update_layout(title="Распределение позиций", height=380)
            st.plotly_chart(figure, width="stretch")
    with right:
        differences = [
            {
                "Товар": str(item.get("matched_name") or item.get("product_name") or "")[:45],
                "Разница ₽": item.get("effective_diff_rub"),
            }
            for item in results
            if item.get("effective_diff_rub") is not None
        ]
        if differences:
            frame = pd.DataFrame(differences)
            frame["Абс"] = frame["Разница ₽"].abs()
            frame = frame.nlargest(20, "Абс").sort_values("Разница ₽")
            figure = go.Figure(
                go.Bar(
                    x=frame["Разница ₽"],
                    y=frame["Товар"],
                    orientation="h",
                    marker_color=[
                        "#F87171" if value < 0 else "#34D399" for value in frame["Разница ₽"]
                    ],
                )
            )
            figure.update_layout(title="20 крупнейших отклонений", height=500)
            st.plotly_chart(figure, width="stretch")


def _render_export() -> None:
    results = st.session_state.processed_results
    if not results:
        st.info("Нет данных для экспорта.")
        return
    st.subheader("Выгрузка Excel")
    _render_analysis_context()
    left, right = st.columns(2)
    with left:
        competitor = st.text_input("Название конкурента", value="Конкурент", max_chars=80)
    with right:
        category = st.text_input("Категория", value="Все категории", max_chars=120)
    excel_bytes = export_comparison_to_excel(
        results,
        competitor_name=competitor,
        category_name=category,
    )
    filename = f"Monitoring_Samberi_{time.strftime('%Y%m%d_%H%M')}.xlsx"
    st.download_button(
        "⬇️ Скачать проверенный Excel-отчёт",
        data=excel_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="stretch",
    )
    summary = summarize_price_index(results)
    st.caption(
        f"В отчёте: {summary['total_items']} строк, {summary['comparable_items']} сопоставимых цен, "
        "12 стандартных колонок и отдельный лист со сводкой."
    )


try:
    settings = load_settings(_streamlit_secrets())
except (TypeError, ValueError, OverflowError):
    st.error("Конфигурация приложения некорректна. Проверьте переменные окружения и Secrets.")
    st.stop()
_initialize_state()

st.title("🛒 Самбери: Мониторинг ценников")
st.caption(f"Gemini Vision · {settings.gemini_model} · строгий матчинг фасовок · проверяемый Excel")

upload_tab, table_tab, analytics_tab, export_tab = st.tabs(
    ["📸 Загрузка", "📋 Таблица", "📊 Аналитика", "📥 Excel"]
)

with upload_tab:
    _render_upload(settings)
with table_tab:
    _render_table(settings)
with analytics_tab:
    _render_analytics()
with export_tab:
    _render_export()
