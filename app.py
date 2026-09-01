"""
Главное веб-приложение для мониторинга ценников конкурентов сети "Самбери".
Запуск: streamlit run app.py
"""

import os
import sys
import io
import time
import zipfile
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from PIL import Image
from dotenv import load_dotenv

# Загружаем переменные окружения из .env если есть
load_dotenv()

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.vision_extractor import PriceTagExtractor
from core.matcher import CatalogMatcher
from core.analytics import calculate_price_metrics, summarize_price_index
from core.exporter import export_comparison_to_excel

# Конфигурация страницы Streamlit
st.set_page_config(
    page_title="Самбери: Мониторинг ценников",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Пользовательские стили CSS
st.markdown("""
<style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1.05rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #F8FAFC;
        border-radius: 10px;
        padding: 15px;
        border-left: 5px solid #1E3A8A;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .status-cheaper {
        color: #137333;
        font-weight: bold;
    }
    .status-expensive {
        color: #C5221F;
        font-weight: bold;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #F1F5F9;
        border-radius: 6px 6px 0px 0px;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #1E3A8A !important;
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)

# Инициализация session_state
if "catalog_df" not in st.session_state:
    # Загружаем демонстрационный каталог по умолчанию, если он существует
    default_cat_path = "data/samples/samberi_catalog_sample.xlsx"
    if os.path.exists(default_cat_path):
        st.session_state.catalog_df = pd.read_excel(default_cat_path)
    else:
        st.session_state.catalog_df = pd.DataFrame()

if "processed_results" not in st.session_state:
    st.session_state.processed_results = []

if "uploaded_images_cache" not in st.session_state:
    st.session_state.uploaded_images_cache = {}

# --- Боковая панель (Настройки) ---
with st.sidebar:
    st.image("https://img.icons8.com/color/96/shopping-cart--v1.png", width=64)
    st.markdown("### ⚙️ Настройки аудита")
    
    competitor_name = st.selectbox(
        "🏪 Сеть конкурента",
        ["Реми", "Пятерочка", "Экономыч", "Близкий", "Винлаб", "Бристоль", "Бауцентр", "Другой"],
        index=0
    )
    if competitor_name == "Другой":
        competitor_name = st.text_input("Укажите название конкурента", "Конкурент")

    store_location = st.text_input("📍 Город / Адрес магазина", "Хабаровск, ул. Суворова")
    category_name = st.selectbox(
        "📦 Категория товаров",
        ["Все категории", "Молочный гастроном", "Мясная гастрономия", "Бакалея", "Сыры", "Чай и кофе", "Кондитерские изделия", "Напитки"],
        index=0
    )

    st.divider()
    st.markdown("### 🤖 Модель распознавания (AI)")
    
    ai_provider = st.radio(
        "Движок Vision AI",
        [
            "Google Gemini 1.5 Flash (Прямой API)",
            "OpenRouter.ai (Работает в РФ без VPN)",
            "OpenAI GPT-4o-mini",
            "Тестовый демо-режим (Без API ключа)"
        ],
        index=0
    )

    api_key = ""
    provider_code = "gemini"
    base_url = None
    
    if "Gemini" in ai_provider:
        provider_code = "gemini"
        default_gemini = os.getenv("GEMINI_API_KEY", "")
        api_key = st.text_input("Ключ Gemini API", value=default_gemini, type="password", help="Бесплатный ключ на aistudio.google.com")
        if not api_key:
            st.info("💡 Требуется VPN при получении на ПК, либо используйте вариант OpenRouter/Сервер.")
    elif "OpenRouter" in ai_provider:
        provider_code = "openrouter"
        default_openrouter = os.getenv("OPENROUTER_API_KEY", "")
        api_key = st.text_input("Ключ OpenRouter API", value=default_openrouter, type="password", help="Ключ с openrouter.ai (работает из РФ напрямую)")
        base_url = "https://openrouter.ai/api/v1"
        st.success("✅ Работает с российских IP без VPN")
    elif "OpenAI" in ai_provider:
        provider_code = "openai"
        default_openai = os.getenv("OPENAI_API_KEY", "")
        api_key = st.text_input("Ключ OpenAI API", value=default_openai, type="password")
    else:
        provider_code = "mock"
        st.success("✅ Демо-режим активен. Запросы к API не расходуются.")

    match_threshold = st.slider("Порог точности матчинга (%)", min_value=20, max_value=90, value=40, step=5)


# --- Основной заголовок ---
st.markdown('<div class="main-title">🛒 Самбери: Мониторинг и анализ ценников</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Автоматическое распознавание ценников конкурентов, матчинг с номенклатурой Самбери и расчет Price Index</div>', unsafe_allow_html=True)

# Вкладки интерфейса
tab_upload, tab_table, tab_dashboard, tab_export, tab_server = st.tabs([
    "📸 1. Загрузка и анализ фото",
    "📋 2. Сравнительная таблица",
    "📊 3. Аналитика и Price Index",
    "📥 4. Экспорт в Excel",
    "🚀 5. Сервер 24/7 и Telegram"
])


# ==========================================
# ВКЛАДКА 1: ЗАГРУЗКА И АНАЛИЗ ФОТО
# ==========================================
with tab_upload:
    col_cat, col_photos = st.columns([1, 1], gap="large")

    with col_cat:
        st.markdown("#### 1. Справочник цен Самбери")
        st.caption("Загрузите Excel или используйте встроенный каталог для сопоставления.")
        
        uploaded_catalog_file = st.file_uploader(
            "Загрузить файл каталога Самбери (.xlsx / .csv)",
            type=["xlsx", "xls", "csv"],
            key="catalog_uploader"
        )
        
        if uploaded_catalog_file is not None:
            try:
                if uploaded_catalog_file.name.endswith(".csv"):
                    st.session_state.catalog_df = pd.read_csv(uploaded_catalog_file)
                else:
                    st.session_state.catalog_df = pd.read_excel(uploaded_catalog_file)
                st.success(f"Загружен каталог: {len(st.session_state.catalog_df)} позиций")
            except Exception as e:
                st.error(f"Ошибка загрузки каталога: {e}")

        # Показываем предпросмотр каталога
        if not st.session_state.catalog_df.empty:
            with st.expander(f"👁️ Предпросмотр каталога Самбери ({len(st.session_state.catalog_df)} SKU)", expanded=False):
                st.dataframe(st.session_state.catalog_df.head(10), use_container_width=True)
        else:
            st.warning("⚠️ Каталог Самбери не загружен. Нажмите кнопку ниже для создания образца.")
            if st.button("📦 Использовать демо-каталог Самбери"):
                from data.samples.generate_sample_data import generate_samples
                sample_file = generate_samples()
                st.session_state.catalog_df = pd.read_excel(sample_file)
                st.rerun()

    with col_photos:
        st.markdown("#### 2. Фотографии ценников")
        st.caption("Загрузите до 100+ фото ценников (JPG, PNG) или ZIP-архив с фото.")
        
        uploaded_photos = st.file_uploader(
            "Перетащите фото ценников сюда",
            type=["jpg", "jpeg", "png", "zip"],
            accept_multiple_files=True,
            key="photos_uploader"
        )
        
        demo_btn = st.button("🧪 Загрузить 10 тестовых ценников для демо-проверки")

    st.divider()

    # Кнопка запуска обработки
    run_disabled = (not uploaded_photos and not demo_btn) or (st.session_state.catalog_df.empty)
    
    if st.button("🚀 НАЧАТЬ РАСПОЗНАВАНИЕ И РАСЧЕТ", type="primary", use_container_width=True, disabled=run_disabled):
        images_to_process = []
        
        # Если нажата кнопка демо
        if demo_btn or (uploaded_photos and len(uploaded_photos) == 0):
            demo_names = [
                "tag_moloko_domik_3_2.jpg", "tag_maslo_prostokvashino_82.jpg", "tag_syr_brest_45.jpg",
                "tag_grechka_uvelka.jpg", "tag_makarony_makfa.jpg", "tag_kolbasa_vyazanka.jpg",
                "tag_tea_greenfield_100.jpg", "tag_coffee_nescafe_190.jpg", "tag_choc_ritter_sport.jpg",
                "tag_sok_dobry_apple.jpg"
            ]
            for name in demo_names:
                images_to_process.append({
                    "data": b"sample_mock_bytes",
                    "filename": name,
                    "mime": "image/jpeg"
                })
        else:
            # Обработка загруженных файлов
            for f in uploaded_photos:
                if f.name.lower().endswith(".zip"):
                    try:
                        with zipfile.ZipFile(f) as z:
                            for zip_info in z.infolist():
                                if not zip_info.is_dir() and any(zip_info.filename.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
                                    img_data = z.read(zip_info.filename)
                                    clean_name = os.path.basename(zip_info.filename)
                                    images_to_process.append({"data": img_data, "filename": clean_name, "mime": "image/jpeg"})
                                    st.session_state.uploaded_images_cache[clean_name] = img_data
                    except Exception as e:
                        st.error(f"Ошибка чтения ZIP {f.name}: {e}")
                else:
                    img_data = f.read()
                    images_to_process.append({"data": img_data, "filename": f.name, "mime": f.type or "image/jpeg"})
                    st.session_state.uploaded_images_cache[f.name] = img_data

        if not images_to_process:
            st.error("Не найдено подходящих фотографий для обработки.")
        else:
            total_imgs = len(images_to_process)
            progress_bar = st.progress(0.0)
            status_text = st.empty()
            start_time = time.time()

            extractor = PriceTagExtractor(
                provider=provider_code,
                api_key=api_key,
                base_url=base_url
            )
            
            def progress_callback(completed, total, latest_res):
                progress = completed / total
                progress_bar.progress(progress)
                status_text.text(f"Распознавание Vision AI: {completed}/{total} фото... (последнее: {latest_res.get('product_name', '')[:40]})")

            # 1. Распознавание ценников через Vision AI
            recognized_items = extractor.extract_batch(
                images_to_process,
                max_workers=8 if provider_code != "mock" else 4,
                on_progress=progress_callback
            )
            
            elapsed_vision = round(time.time() - start_time, 1)
            status_text.text(f"Матчинг номенклатуры с каталогом Самбери...")

            # 2. Нечеткий матчинг
            matcher = CatalogMatcher(st.session_state.catalog_df)
            matched_items = matcher.match_all(recognized_items)

            # 3. Расчет Price Index и аналитики
            final_processed = [calculate_price_metrics(it) for it in matched_items]
            st.session_state.processed_results = final_processed
            
            progress_bar.progress(1.0)
            status_text.success(f" Обработка завершена! {total_imgs} ценников обработано за {elapsed_vision} сек. Перейдите во вкладку '2. Сравнительная таблица'.")
            st.balloons()


# ==========================================
# ВКЛАДКА 2: СРАВНИТЕЛЬНАЯ ТАБЛИЦА
# ==========================================
with tab_table:
    if not st.session_state.processed_results:
        st.info("💡 Нет данных для отображения. Загрузите фото и нажмите 'Начать распознавание' во вкладке 1.")
    else:
        results = st.session_state.processed_results
        
        # Фильтры и быстрые действия
        col_f1, col_f2, col_f3 = st.columns([1.5, 1.5, 1])
        with col_f1:
            status_filter = st.selectbox(
                "Фильтр по статусу ценообразования:",
                ["Все статусы", "✅ Самбери дешевле", "❌ Конкурент дешевле", "⚖️ Паритет цен (±2%)", "⚠️ Только ДЕМПИНГ конкурента"]
            )
        with col_f2:
            search_query = st.text_input("🔍 Поиск по названию товара:", "")
        with col_f3:
            st.metric("Всего позиций", len(results))

        # Формируем DataFrame для отображения со структурой пользователя
        table_rows = []
        for r in results:
            # Применение фильтров
            if status_filter == "✅ Самбери дешевле" and r.get("status") != "✅ Самбери дешевле":
                continue
            if status_filter == "❌ Конкурент дешевле" and r.get("status") != "❌ Конкурент дешевле":
                continue
            if status_filter == "⚖️ Паритет цен (±2%)" and r.get("status") != "⚖️ Паритет цен (±2%)":
                continue
            if status_filter == "⚠️ Только ДЕМПИНГ конкурента" and not r.get("alert"):
                continue

            if search_query:
                q = search_query.lower()
                prod_name = (r.get("matched_name") or "").lower()
                rec_name = (r.get("product_name") or "").lower()
                if q not in prod_name and q not in rec_name:
                    continue

            table_rows.append({
                "Код товара": r.get("matched_sku") or "-",
                "Наименование товара (Самбери)": r.get("matched_name") or r.get("product_name", ""),
                "Цена закупки": r.get("our_purchase_price"),
                "Цена продажи": r.get("our_sale_price"),
                "Цена на промо": r.get("our_promo_price"),
                f"Цена {competitor_name}": r.get("comp_regular_price"),
                f"Промо {competitor_name}": r.get("comp_promo_price"),
                "Разница (руб)": r.get("effective_diff_rub"),
                "Price Index (%)": r.get("price_index_effective"),
                "Статус": r.get("status"),
                "Предупреждение": r.get("alert") or "",
                "Распознано с ценника": r.get("product_name", ""),
                "Файл": r.get("filename", "")
            })

        display_df = pd.DataFrame(table_rows)

        if display_df.empty:
            st.warning("По выбранным фильтрам позиции не найдены.")
        else:
            # Настройка форматирования колонок
            column_config = {
                "Код товара": st.column_config.TextColumn("Код товара", width="small"),
                "Наименование товара (Самбери)": st.column_config.TextColumn("Наименование товара", width="large"),
                "Цена закупки": st.column_config.NumberColumn("Цена закупки", format="%.2f ₽"),
                "Цена продажи": st.column_config.NumberColumn("Цена продажи", format="%.2f ₽"),
                "Цена на промо": st.column_config.NumberColumn("Цена на промо", format="%.2f ₽"),
                f"Цена {competitor_name}": st.column_config.NumberColumn(f"Цена {competitor_name}", format="%.2f ₽"),
                f"Промо {competitor_name}": st.column_config.NumberColumn(f"Промо {competitor_name}", format="%.2f ₽"),
                "Разница (руб)": st.column_config.NumberColumn("Разница цен", format="%.2f ₽"),
                "Price Index (%)": st.column_config.NumberColumn("Price Index", format="%.1f%%"),
                "Статус": st.column_config.TextColumn("Статус", width="medium"),
                "Предупреждение": st.column_config.TextColumn("Предупреждение", width="medium"),
            }

            st.dataframe(
                display_df,
                column_config=column_config,
                use_container_width=True,
                height=450
            )

        st.divider()

        # Панель детального просмотра и проверки фото
        st.markdown("#### 🔍 Детальный просмотр ценника и корректировка")
        selected_file = st.selectbox(
            "Выберите ценник для проверки:",
            [r.get("filename") for r in results]
        )
        
        target_item = next((r for r in results if r.get("filename") == selected_file), None)
        
        if target_item:
            c_img, c_details = st.columns([1, 2], gap="large")
            with c_img:
                img_bytes = st.session_state.uploaded_images_cache.get(selected_file)
                if img_bytes:
                    st.image(img_bytes, caption=f"Фото: {selected_file}", use_container_width=True)
                else:
                    st.info(f"🖼️ [Превью ценника: {selected_file}]")
                    st.caption(f"Распознано: {target_item.get('product_name')}")
            
            with c_details:
                st.markdown(f"**Распознанный товар:** `{target_item.get('product_name')}`")
                st.markdown(f"**Бренд:** `{target_item.get('brand') or '-'}` | **Фасовка:** `{target_item.get('weight_volume') or '-'}`")
                st.markdown(f"**Регулярная цена конкурента:** `{target_item.get('comp_regular_price')} ₽`")
                if target_item.get("comp_promo_price"):
                    st.markdown(f"**Промо цена конкурента:** `{target_item.get('comp_promo_price')} ₽` ({target_item.get('promo_condition') or 'акция'})")
                
                st.divider()
                st.markdown("**Сопоставление с каталогом Самбери:**")
                candidates = target_item.get("candidates", [])
                if candidates:
                    candidate_options = [f"{c['sku']} — {c['name']} (Совпадение: {c['score']}%)" for c in candidates]
                    current_idx = 0
                    sel_candidate_str = st.selectbox(
                        "Выбрать правильный SKU Самбери:",
                        candidate_options,
                        index=current_idx
                    )
                    # Кнопка для ручного пересчета при выборе альтернативного кандидата
                    if st.button("Применить выбранный SKU к этой строке"):
                        chosen_sku = sel_candidate_str.split(" — ")[0]
                        chosen_cand = next((c for c in candidates if str(c['sku']) == chosen_sku), None)
                        if chosen_cand:
                            target_item["matched_sku"] = chosen_cand["sku"]
                            target_item["matched_name"] = chosen_cand["name"]
                            target_item["our_purchase_price"] = chosen_cand["purchase_price"]
                            target_item["our_sale_price"] = chosen_cand["sale_price"]
                            target_item["our_promo_price"] = chosen_cand["promo_price"]
                            # Пересчитываем метрики
                            updated = calculate_price_metrics(target_item)
                            for k, v in updated.items():
                                target_item[k] = v
                            st.success(f"Привязан SKU {chosen_sku}!")
                            st.rerun()


# ==========================================
# ВКЛАДКА 3: АНАЛИТИКА И PRICE INDEX
# ==========================================
with tab_dashboard:
    if not st.session_state.processed_results:
        st.info("💡 Нет данных. Сначала проведите распознавание ценников во вкладке 1.")
    else:
        results = st.session_state.processed_results
        summary = summarize_price_index(results)

        # KPI карточки
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.metric("Средний Price Index (PI)", f"{summary['avg_price_index']}%", delta=f"{round(summary['avg_price_index'] - 100, 1)}% vs Самбери")
            st.caption("PI < 100% — конкурент в среднем дешевле")
            st.markdown('</div>', unsafe_allow_html=True)
        
        with k2:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.metric("Корзинный Price Index", f"{summary['basket_price_index']}%")
            st.caption(f"Корзина: Самбери {summary['total_our_basket']} ₽ vs Конк. {summary['total_comp_basket']} ₽")
            st.markdown('</div>', unsafe_allow_html=True)

        with k3:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.metric("Самбери дешевле", f"{summary['samberi_cheaper_count']} поз.", delta=f"{round(summary['samberi_cheaper_count']/summary['total_items']*100, 1)}%")
            st.caption(f"Паритет цен: {summary['parity_count']} поз.")
            st.markdown('</div>', unsafe_allow_html=True)

        with k4:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.metric("Демпинг конкурента", f"{summary['dumping_alerts_count']} алертов", delta="-Риск" if summary['dumping_alerts_count'] > 0 else "0", delta_color="inverse")
            st.caption("Цена конкурента ниже закупки Самбери")
            st.markdown('</div>', unsafe_allow_html=True)

        st.divider()

        # Графики Plotly
        g_col1, g_col2 = st.columns(2)
        
        with g_col1:
            # Круговая диаграмма соотношения цен
            status_counts = pd.DataFrame([
                {"Статус": "Самбери дешевле", "Количество": summary['samberi_cheaper_count'], "Цвет": "#137333"},
                {"Статус": "Конкурент дешевле", "Количество": summary['competitor_cheaper_count'], "Цвет": "#C5221F"},
                {"Статус": "Паритет (±2%)", "Количество": summary['parity_count'], "Цвет": "#1A73E8"}
            ])
            status_counts = status_counts[status_counts["Количество"] > 0]
            
            if not status_counts.empty:
                fig_pie = px.pie(
                    status_counts,
                    names="Статус",
                    values="Количество",
                    title="Ценовое позиционирование (Доли)",
                    color="Статус",
                    color_discrete_map={
                        "Самбери дешевле": "#137333",
                        "Конкурент дешевле": "#C5221F",
                        "Паритет (±2%)": "#1A73E8"
                    },
                    hole=0.4
                )
                fig_pie.update_layout(margin=dict(l=20, r=20, t=40, b=20), height=350)
                st.plotly_chart(fig_pie, use_container_width=True)

        with g_col2:
            # Топ товаров с наибольшей разницей в ценах
            diff_list = []
            for r in results:
                if r.get("effective_diff_rub") is not None:
                    diff_list.append({
                        "Товар": (r.get("matched_name") or r.get("product_name", ""))[:28],
                        "Разница (руб)": r.get("effective_diff_rub"),
                        "Конкурент": competitor_name
                    })
            
            if diff_list:
                diff_df = pd.DataFrame(diff_list).sort_values(by="Разница (руб)", ascending=True)
                fig_bar = px.bar(
                    diff_df.head(10),
                    x="Разница (руб)",
                    y="Товар",
                    orientation="h",
                    title="Топ позиций, где Конкурент дешевле Самбери (руб)",
                    color="Разница (руб)",
                    color_continuous_scale="Reds_r"
                )
                fig_bar.update_layout(margin=dict(l=20, r=20, t=40, b=20), height=350)
                st.plotly_chart(fig_bar, use_container_width=True)


# ==========================================
# ВКЛАДКА 4: ЭКСПОРТ В EXCEL
# ==========================================
with tab_export:
    st.markdown("#### 📥 Выгрузка итогового отчета в Excel")
    st.caption("Файл формируется с точной структурой ваших колонок, цветовой подсветкой выгодных цен и денежным форматированием.")

    if not st.session_state.processed_results:
        st.warning("⚠️ Нет данных для экспорта. Проведите распознавание ценников во вкладке 1.")
    else:
        # Генерируем Excel через наш модуль exporter
        excel_data = export_comparison_to_excel(
            st.session_state.processed_results,
            competitor_name=competitor_name,
            category_name=category_name
        )

        st.download_button(
            label=f"💾 Скачать отчет: Мониторинг_Самбери_vs_{competitor_name}.xlsx",
            data=excel_data,
            file_name=f"Monitoring_Samberi_vs_{competitor_name}_{time.strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )

        st.divider()
        st.markdown("##### 📄 Что включено в выгружаемый Excel:")
        st.markdown(f"""
        1. **Код нашего товара** (SKU Самбери)
        2. **Наименование товара** (по номенклатуре сети)
        3. **Распознано с ценника** (точный текст с ценника {competitor_name})
        4. **Цена закупки товара** (себестоимость)
        5. **Цена продажи товара** (регулярная полка Самбери)
        6. **Цена на промо у товара** (акция Самбери)
        7. **Текущая цена {competitor_name}**
        8. **Цена на промо {competitor_name}**
        9. **Разница цен (руб)**
        10. **Price Index (PI %)**
        11. **Статус выгодности** (🟢 Зеленый = Самбери выгоднее, 🔴 Красный = Конкурент дешевле)
        12. **Предупреждения** (🟡 Желтый = продажа конкурента ниже себестоимости закупки Самбери)
        """)


# ==========================================
# ВКЛАДКА 5: СЕРВЕР 24/7 И TELEGRAM-БОТ
# ==========================================
with tab_server:
    st.markdown("### 🌐 Автономная круглосуточная работа (24/7)")
    st.markdown("""
    Чтобы система работала непрерывно даже при выключенном рабочем компьютере, ее можно развернуть на недорогом облачном сервере (VPS) за **~200–300 рублей в месяц** (TimeWeb Cloud, Beget, Selectel).
    """)

    st.markdown("#### 📲 Варианты использования на сервере:")
    c_s1, c_s2 = st.columns(2)
    
    with c_s1:
        st.markdown("""
        **1. Web-сервис по ссылке (в браузере):**
        * Доступен 24/7 по адресу `https://monitor.ваш-домен.ru` или по IP-адресу.
        * Защищен паролем (вход только для сотрудников отдела ценообразования).
        * Можно открывать как с ПК, так и со смартфона/планшета в торговом зале.
        """)

    with c_s2:
        st.markdown("""
        **2. Мобильный Telegram-бот:**
        * Аудитор в торговом зале конкурента прямо с телефона отправляет пачку из 50-100 фото в Telegram-чат.
        * Бот на сервере в фоновом режиме распознает все ценники.
        * Через 1 минуту бот присылает готовый Excel-файл обратно в Telegram!
        """)

    st.divider()
    st.markdown("#### 🛠️ Пошаговая инструкция по запуску на сервере:")
    st.code("""
# 1. Арендуйте любой Ubuntu VPS (1 CPU, 2GB RAM ~ 200 руб/мес)
# 2. Клонируйте проект и установите зависимости:
git clone <ваш_репозиторий>
cd Мониторинг
pip install -r requirements.txt

# 3. Запустите в фоновом режиме 24/7 через systemd или tmux:
streamlit run app.py --server.port 8501 --server.headless true
    """, language="bash")
