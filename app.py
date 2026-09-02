"""
Самбери: Мониторинг ценников конкурентов
Современный красивый интерфейс на Streamlit с Google Gemini Vision AI
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

DEFAULT_GEMINI_KEY = os.getenv("GEMINI_API_KEY") or (
    st.secrets.get("GEMINI_API_KEY", "AQ.Ab8RN6IDk5YuonlD9QV_bFxAg0TVY_ofWJKSTOk7Q0eUnv7Yeg")
    if hasattr(st, "secrets") else "AQ.Ab8RN6IDk5YuonlD9QV_bFxAg0TVY_ofWJKSTOk7Q0eUnv7Yeg"
)
MATCH_THRESHOLD = 65.0

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from core.vision_extractor import PriceTagExtractor
from core.matcher import CatalogMatcher
from core.analytics import calculate_price_metrics, summarize_price_index
from core.exporter import export_comparison_to_excel

st.set_page_config(
    page_title="Самбери — Мониторинг ценников",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif !important;
    }

    /* === ОБЩИЙ ФОН === */
    .stApp {
        background: linear-gradient(135deg, #0F172A 0%, #1E293B 50%, #0F172A 100%);
        min-height: 100vh;
    }

    /* === ШАПКА === */
    .hero-block {
        background: linear-gradient(135deg, #1E3A8A 0%, #1D4ED8 50%, #2563EB 100%);
        border-radius: 20px;
        padding: 40px 48px;
        margin-bottom: 32px;
        position: relative;
        overflow: hidden;
        box-shadow: 0 25px 60px rgba(29, 78, 216, 0.4);
    }
    .hero-block::before {
        content: "";
        position: absolute;
        top: -60px; right: -60px;
        width: 250px; height: 250px;
        border-radius: 50%;
        background: rgba(255,255,255,0.05);
    }
    .hero-block::after {
        content: "";
        position: absolute;
        bottom: -80px; left: -40px;
        width: 300px; height: 300px;
        border-radius: 50%;
        background: rgba(255,255,255,0.03);
    }
    .hero-title {
        font-size: 2.6rem;
        font-weight: 800;
        color: #FFFFFF;
        letter-spacing: -0.5px;
        margin: 0 0 10px 0;
        line-height: 1.1;
    }
    .hero-subtitle {
        font-size: 1.05rem;
        color: rgba(255,255,255,0.75);
        margin: 0;
        font-weight: 400;
    }
    .hero-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: rgba(255,255,255,0.15);
        border: 1px solid rgba(255,255,255,0.2);
        border-radius: 20px;
        padding: 6px 14px;
        font-size: 0.8rem;
        color: #fff;
        font-weight: 500;
        margin-bottom: 16px;
    }

    /* === КАРТОЧКИ === */
    .card {
        background: linear-gradient(145deg, #1E293B, #162032);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        padding: 28px;
        height: 100%;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        transition: all 0.2s ease;
    }
    .card:hover {
        border-color: rgba(99, 102, 241, 0.3);
        box-shadow: 0 12px 40px rgba(0,0,0,0.4);
    }
    .card-title {
        font-size: 1rem;
        font-weight: 700;
        color: #E2E8F0;
        margin: 0 0 6px 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .card-subtitle {
        font-size: 0.82rem;
        color: #64748B;
        margin: 0 0 20px 0;
        line-height: 1.5;
    }

    /* === ЗОНА ЗАГРУЗКИ (Upload) === */
    [data-testid="stFileUploaderDropzone"] {
        background: linear-gradient(145deg, rgba(30,58,138,0.15), rgba(37,99,235,0.08)) !important;
        border: 2px dashed rgba(99,102,241,0.4) !important;
        border-radius: 14px !important;
        transition: all 0.25s ease !important;
        padding: 28px !important;
    }
    [data-testid="stFileUploaderDropzone"]:hover {
        background: linear-gradient(145deg, rgba(30,58,138,0.25), rgba(37,99,235,0.15)) !important;
        border-color: rgba(99,102,241,0.7) !important;
    }
    [data-testid="stFileUploaderDropzone"] > div > span {
        color: #94A3B8 !important;
        font-size: 0.9rem !important;
    }

    /* === КНОПКИ === */
    .stButton > button {
        border-radius: 12px !important;
        font-weight: 600 !important;
        letter-spacing: 0.2px !important;
        transition: all 0.2s ease !important;
        border: none !important;
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #2563EB, #4F46E5) !important;
        color: white !important;
        padding: 14px 28px !important;
        font-size: 1rem !important;
        box-shadow: 0 8px 25px rgba(79, 70, 229, 0.4) !important;
    }
    .stButton > button[kind="primary"]:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 12px 35px rgba(79, 70, 229, 0.55) !important;
    }
    .stButton > button[kind="primary"]:disabled {
        opacity: 0.4 !important;
        transform: none !important;
    }
    .stButton > button[kind="secondary"] {
        background: rgba(99,102,241,0.12) !important;
        color: #A5B4FC !important;
        border: 1px solid rgba(99,102,241,0.25) !important;
    }

    /* === МЕТРИКИ KPI === */
    .kpi-card {
        background: linear-gradient(145deg, #1E293B, #162032);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 16px;
        padding: 22px 24px;
        text-align: center;
        box-shadow: 0 8px 32px rgba(0,0,0,0.25);
        position: relative;
        overflow: hidden;
    }
    .kpi-card::before {
        content: "";
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
    }
    .kpi-blue::before { background: linear-gradient(90deg, #3B82F6, #6366F1); }
    .kpi-green::before { background: linear-gradient(90deg, #10B981, #34D399); }
    .kpi-red::before { background: linear-gradient(90deg, #EF4444, #F97316); }
    .kpi-yellow::before { background: linear-gradient(90deg, #F59E0B, #EF4444); }

    .kpi-label {
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: #475569;
        margin-bottom: 8px;
    }
    .kpi-value {
        font-size: 2.2rem;
        font-weight: 800;
        line-height: 1;
        margin-bottom: 6px;
    }
    .kpi-blue .kpi-value { color: #60A5FA; }
    .kpi-green .kpi-value { color: #34D399; }
    .kpi-red .kpi-value { color: #F87171; }
    .kpi-yellow .kpi-value { color: #FBBF24; }
    .kpi-caption {
        font-size: 0.78rem;
        color: #475569;
    }

    /* === СТАТУС БЕЙДЖИ === */
    .badge-green {
        background: rgba(16,185,129,0.15);
        color: #34D399;
        border: 1px solid rgba(16,185,129,0.3);
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 0.78rem;
        font-weight: 600;
        white-space: nowrap;
    }
    .badge-red {
        background: rgba(239,68,68,0.15);
        color: #F87171;
        border: 1px solid rgba(239,68,68,0.3);
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 0.78rem;
        font-weight: 600;
    }
    .badge-blue {
        background: rgba(59,130,246,0.15);
        color: #60A5FA;
        border: 1px solid rgba(59,130,246,0.3);
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 0.78rem;
        font-weight: 600;
    }
    .badge-yellow {
        background: rgba(245,158,11,0.15);
        color: #FBBF24;
        border: 1px solid rgba(245,158,11,0.3);
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 0.78rem;
        font-weight: 600;
    }

    /* === ТАБЛИЦА === */
    .stDataFrame {
        border-radius: 14px !important;
        overflow: hidden !important;
        border: 1px solid rgba(255,255,255,0.07) !important;
    }
    [data-testid="stDataFrameResizable"] {
        border-radius: 14px !important;
    }

    /* === ПРОГРЕСС БАР === */
    .stProgress > div > div > div {
        background: linear-gradient(90deg, #3B82F6, #6366F1) !important;
        border-radius: 10px !important;
    }

    /* === ВКЛАДКИ === */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background: rgba(255,255,255,0.04) !important;
        border-radius: 14px;
        padding: 4px !important;
        border: 1px solid rgba(255,255,255,0.07) !important;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px !important;
        padding: 10px 20px !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
        color: #64748B !important;
        background: transparent !important;
        transition: all 0.2s ease !important;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #2563EB, #4F46E5) !important;
        color: white !important;
        box-shadow: 0 4px 15px rgba(79,70,229,0.35) !important;
    }

    /* === DIVIDER === */
    hr {
        border: none !important;
        border-top: 1px solid rgba(255,255,255,0.06) !important;
        margin: 24px 0 !important;
    }

    /* === EXPANDER === */
    .streamlit-expanderHeader {
        background: rgba(255,255,255,0.04) !important;
        border-radius: 10px !important;
        color: #94A3B8 !important;
        font-weight: 600 !important;
    }

    /* === ПРОГРЕСС СТАТУС === */
    .status-block {
        background: linear-gradient(145deg, rgba(30,58,138,0.2), rgba(37,99,235,0.1));
        border: 1px solid rgba(99,102,241,0.2);
        border-radius: 14px;
        padding: 20px 24px;
        margin: 16px 0;
    }

    /* === SUCCESS BLOCK === */
    .success-block {
        background: linear-gradient(145deg, rgba(16,185,129,0.15), rgba(5,150,105,0.08));
        border: 1px solid rgba(16,185,129,0.3);
        border-radius: 14px;
        padding: 20px 24px;
        margin: 16px 0;
    }

    /* Скрытие sidebar */
    [data-testid="stSidebar"], [data-testid="stSidebarNav"] { display: none !important; }

    /* Скрытие лишних элементов Streamlit */
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding-top: 24px !important; max-width: 1400px !important; }
    
    /* Текстовые элементы */
    p, li, span { color: #94A3B8 !important; }
    h1, h2, h3, h4, h5, h6 { color: #E2E8F0 !important; }
    
    /* selectbox, text_input */
    [data-baseweb="select"] {
        background: rgba(255,255,255,0.06) !important;
        border-radius: 10px !important;
    }
    [data-baseweb="input"] > div {
        background: rgba(255,255,255,0.06) !important;
        border-radius: 10px !important;
    }
    
    /* download button */
    .stDownloadButton > button {
        border-radius: 12px !important;
        font-weight: 700 !important;
        background: linear-gradient(135deg, #059669, #10B981) !important;
        color: white !important;
        border: none !important;
        font-size: 1rem !important;
        padding: 14px 28px !important;
        box-shadow: 0 8px 25px rgba(16,185,129,0.35) !important;
        transition: all 0.2s ease !important;
    }
    .stDownloadButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 12px 35px rgba(16,185,129,0.5) !important;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# HERO HEADER
# ==========================================
st.markdown("""
<div class="hero-block">
    <div class="hero-badge">🤖 Google Gemini 1.5 Flash • Vision AI</div>
    <div class="hero-title">🛒 Самбери: Мониторинг ценников</div>
    <div class="hero-subtitle">Автоматическое распознавание ценников конкурентов • Сопоставление с базой Самбери • Price Index в реальном времени</div>
</div>
""", unsafe_allow_html=True)

# Session state
for key in ["catalog_df", "processed_results", "uploaded_images_cache"]:
    if key not in st.session_state:
        default = pd.DataFrame() if key == "catalog_df" else ([] if key == "processed_results" else {})
        st.session_state[key] = default

# ==========================================
# ВКЛАДКИ
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs([
    "  📸  Загрузка и анализ",
    "  📋  Сравнительная таблица",
    "  📊  Аналитика",
    "  📥  Выгрузка Excel"
])


# ─────────────────────────────────────────
# ВКЛАДКА 1: ЗАГРУЗКА
# ─────────────────────────────────────────
with tab1:
    col_left, col_right = st.columns(2, gap="large")

    # ── Левая колонка: Каталог Самбери ──
    with col_left:
        st.markdown("""
        <div class="card">
            <div class="card-title">📂 1. Ваш справочник цен Самбери</div>
            <div class="card-subtitle">Загрузите файл Excel (.xlsx) или CSV с номенклатурой Самбери, ценами закупки, продажи и промо.</div>
        </div>
        """, unsafe_allow_html=True)
        
        uploaded_catalog = st.file_uploader(
            "Перетащите файл Excel или CSV сюда",
            type=["xlsx", "xls", "csv"],
            key="catalog_uploader",
            label_visibility="collapsed"
        )

        if uploaded_catalog:
            try:
                df = pd.read_csv(uploaded_catalog) if uploaded_catalog.name.endswith(".csv") else pd.read_excel(uploaded_catalog)
                st.session_state.catalog_df = df
                st.success(f"✅ Загружено **{len(df)} SKU** из файла «{uploaded_catalog.name}»")
            except Exception as e:
                st.error(f"Ошибка загрузки: {e}")

        if not st.session_state.catalog_df.empty:
            sku_count = len(st.session_state.catalog_df)
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:10px;margin:12px 0 4px 0;">
                <span style="font-size:1.5rem;">📦</span>
                <div>
                    <div style="color:#34D399;font-weight:700;font-size:1.1rem;">{sku_count} SKU загружено</div>
                    <div style="color:#475569;font-size:0.8rem;">Ваш справочник готов к сопоставлению</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander("👁️ Предпросмотр загруженного справочника", expanded=False):
                st.dataframe(
                    st.session_state.catalog_df.head(8),
                    use_container_width=True,
                    hide_index=True
                )
        else:
            st.markdown("""
            <div style="background:rgba(255,255,255,0.03);border:1px dashed rgba(255,255,255,0.1);
                        border-radius:12px;padding:16px;margin-top:12px;text-align:center;">
                <div style="color:#64748B;font-size:0.85rem;">⚪ Ожидается загрузка файла справочника Самбери</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Правая колонка: Фото ценников ──
    with col_right:
        st.markdown("""
        <div class="card">
            <div class="card-title">📸 2. Фотографии ценников конкурента</div>
            <div class="card-subtitle">Загрузите пачку фото ценников (JPG, PNG) или ZIP-архив с фото. Поддерживается до 100+ файлов за один раз.</div>
        </div>
        """, unsafe_allow_html=True)

        uploaded_photos = st.file_uploader(
            "Перетащите фото ценников или ZIP-архив сюда",
            type=["jpg", "jpeg", "png", "zip"],
            accept_multiple_files=True,
            key="photos_uploader",
            label_visibility="collapsed"
        )

        if uploaded_photos:
            photo_count = len(uploaded_photos)
            has_zip = any(f.name.lower().endswith(".zip") for f in uploaded_photos)
            st.markdown(f"""
            <div style="display:flex;align-items:center;gap:10px;margin:12px 0 4px 0;">
                <span style="font-size:1.5rem;">✅</span>
                <div>
                    <div style="color:#34D399;font-weight:700;font-size:1.1rem;">
                        {"ZIP-архив с фото" if has_zip else f"{photo_count} фото выбрано"}
                    </div>
                    <div style="color:#475569;font-size:0.8rem;">Готово к распознаванию через Google Gemini Vision AI</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:rgba(255,255,255,0.03);border:1px dashed rgba(255,255,255,0.1);
                        border-radius:12px;padding:16px;margin-top:12px;text-align:center;">
                <div style="color:#64748B;font-size:0.85rem;">⚪ Ожидается загрузка фотографий ценников</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Панель запуска ──
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    
    has_photos = bool(uploaded_photos and len(uploaded_photos) > 0)
    has_catalog = not st.session_state.catalog_df.empty
    ready_to_run = has_photos and has_catalog

    if not has_catalog and not has_photos:
        st.markdown("""
        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);
                    border-radius:12px;padding:14px 18px;text-align:center;margin-bottom:8px;">
            <span style="color:#94A3B8;font-size:0.9rem;">Загрузите файл вашего справочника Самбери и выберите фото ценников для запуска</span>
        </div>
        """, unsafe_allow_html=True)
    elif not has_catalog:
        st.markdown("""
        <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);
                    border-radius:12px;padding:14px 18px;text-align:center;margin-bottom:8px;">
            <span style="color:#F87171;font-weight:600;">⚠️ Загрузите файл справочника Самбери для сопоставления</span>
        </div>
        """, unsafe_allow_html=True)
    elif not has_photos:
        st.markdown("""
        <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);
                    border-radius:12px;padding:14px 18px;text-align:center;margin-bottom:8px;">
            <span style="color:#F87171;font-weight:600;">⚠️ Выберите фотографии ценников для распознавания</span>
        </div>
        """, unsafe_allow_html=True)

    run_btn = st.button(
        "🚀  НАЧАТЬ МОНИТОРИНГ И РАСЧЁТ PRICE INDEX",
        type="primary",
        use_container_width=True,
        disabled=not ready_to_run
    )

    if run_btn:
        images_to_process = []
        for f in uploaded_photos:
            if f.name.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(f) as z:
                        for zi in z.infolist():
                            if not zi.is_dir() and any(zi.filename.lower().endswith(e) for e in [".jpg",".jpeg",".png"]):
                                d = z.read(zi.filename)
                                n = os.path.basename(zi.filename)
                                images_to_process.append({"data": d, "filename": n, "mime": "image/jpeg"})
                                st.session_state.uploaded_images_cache[n] = d
                except Exception as e:
                    st.error(f"Ошибка ZIP: {e}")
            else:
                d = f.read()
                images_to_process.append({"data": d, "filename": f.name, "mime": f.type or "image/jpeg"})
                st.session_state.uploaded_images_cache[f.name] = d

        if not images_to_process:
            st.error("Не найдено подходящих фотографий.")
        else:
            total = len(images_to_process)
            
            status_box = st.empty()
            progress_bar = st.progress(0.0)
            detail_text = st.empty()

            status_box.markdown(f"""
            <div class="status-block">
                <div style="color:#60A5FA;font-weight:700;font-size:1rem;margin-bottom:4px;">
                    ⚡ Запуск Google Gemini Vision AI
                </div>
                <div style="color:#475569;font-size:0.87rem;">Отправляю {total} фотографий на параллельное распознавание...</div>
            </div>
            """, unsafe_allow_html=True)

            t0 = time.time()
            extractor = PriceTagExtractor(provider="gemini", api_key=DEFAULT_GEMINI_KEY)

            def on_progress(done, total_n, last_res):
                pct = done / total_n
                progress_bar.progress(pct)
                name = last_res.get("product_name", "")[:45]
                status_box.markdown(f"""
                <div class="status-block">
                    <div style="color:#60A5FA;font-weight:700;font-size:1rem;margin-bottom:4px;">
                        ⚡ Распознавание: {done} / {total_n} ценников
                    </div>
                    <div style="color:#475569;font-size:0.87rem;">Последнее: {name}</div>
                </div>
                """, unsafe_allow_html=True)

            recognized = extractor.extract_batch(images_to_process, max_workers=8, on_progress=on_progress)
            t_vision = round(time.time() - t0, 1)

            detail_text.markdown(f"""
            <div style="color:#475569;font-size:0.82rem;margin:4px 0 12px 0;">
                🔗 Матчинг с базой Самбери (порог точности {int(MATCH_THRESHOLD)}%)...
            </div>""", unsafe_allow_html=True)

            matcher = CatalogMatcher(st.session_state.catalog_df)
            matched = []
            for item in recognized:
                mi = matcher.match_item(item.get("product_name", ""), threshold=MATCH_THRESHOLD)
                matched.append({**item, **mi})

            processed = [calculate_price_metrics(it) for it in matched]
            st.session_state.processed_results = processed

            progress_bar.progress(1.0)
            matched_count = sum(1 for r in processed if r.get("matched_sku"))
            status_box.markdown(f"""
            <div class="success-block">
                <div style="color:#34D399;font-weight:800;font-size:1.15rem;margin-bottom:8px;">
                    🎉 Анализ завершён за {t_vision} сек.
                </div>
                <div style="display:flex;gap:24px;flex-wrap:wrap;">
                    <div>
                        <span style="color:#34D399;font-weight:700;">{total}</span>
                        <span style="color:#475569;font-size:0.85rem;"> ценников распознано</span>
                    </div>
                    <div>
                        <span style="color:#60A5FA;font-weight:700;">{matched_count}</span>
                        <span style="color:#475569;font-size:0.85rem;"> позиций сопоставлено с базой Самбери</span>
                    </div>
                </div>
                <div style="color:#475569;font-size:0.83rem;margin-top:8px;">
                    ➡️ Перейдите во вкладку «Сравнительная таблица» или «Аналитика»
                </div>
            </div>
            """, unsafe_allow_html=True)
            detail_text.empty()
            st.balloons()


# ─────────────────────────────────────────
# ВКЛАДКА 2: ТАБЛИЦА
# ─────────────────────────────────────────
with tab2:
    if not st.session_state.processed_results:
        st.markdown("""
        <div style="text-align:center;padding:60px 20px;">
            <div style="font-size:4rem;margin-bottom:16px;">📋</div>
            <div style="color:#E2E8F0;font-size:1.3rem;font-weight:700;margin-bottom:8px;">Нет данных для отображения</div>
            <div style="color:#475569;font-size:0.95rem;">Загрузите базу Самбери и фото ценников, затем нажмите «Начать распознавание»</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        results = st.session_state.processed_results

        # Фильтры
        fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 1, 1])
        with fcol1:
            status_filter = st.selectbox(
                "Фильтр по статусу",
                ["🔍 Все статусы", "✅ Самбери дешевле", "❌ Конкурент дешевле", "⚖️ Паритет цен (±2%)", "⚠️ Только ДЕМПИНГ"],
                label_visibility="collapsed"
            )
        with fcol2:
            search_q = st.text_input("Поиск по наименованию...", label_visibility="collapsed", placeholder="🔍 Поиск по наименованию...")
        with fcol3:
            st.markdown(f"""
            <div style="text-align:center;background:rgba(99,102,241,0.1);border:1px solid rgba(99,102,241,0.2);
                        border-radius:10px;padding:9px 4px;">
                <div style="color:#A5B4FC;font-weight:800;font-size:1.4rem;line-height:1;">{len(results)}</div>
                <div style="color:#475569;font-size:0.7rem;margin-top:2px;">позиций</div>
            </div>""", unsafe_allow_html=True)
        with fcol4:
            matched_n = sum(1 for r in results if r.get("matched_sku"))
            st.markdown(f"""
            <div style="text-align:center;background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.2);
                        border-radius:10px;padding:9px 4px;">
                <div style="color:#34D399;font-weight:800;font-size:1.4rem;line-height:1;">{matched_n}</div>
                <div style="color:#475569;font-size:0.7rem;margin-top:2px;">матчей</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        rows = []
        for r in results:
            st_val = r.get("status", "")
            if "Самбери дешевле" in status_filter and "Самбери" not in st_val: continue
            if "Конкурент дешевле" in status_filter and "Конкурент" not in st_val: continue
            if "Паритет" in status_filter and "Паритет" not in st_val: continue
            if "ДЕМПИНГ" in status_filter and not r.get("alert"): continue

            if search_q:
                q = search_q.lower()
                n1 = (r.get("matched_name") or "").lower()
                n2 = (r.get("product_name") or "").lower()
                if q not in n1 and q not in n2: continue

            rows.append({
                "Код товара": r.get("matched_sku") or "—",
                "Наименование товара (Самбери)": r.get("matched_name") or r.get("product_name", ""),
                "Распознано с ценника": r.get("product_name", ""),
                "Цена закупки ₽": r.get("our_purchase_price"),
                "Цена продажи ₽": r.get("our_sale_price"),
                "Промо Самбери ₽": r.get("our_promo_price"),
                "Цена конкурента ₽": r.get("comp_regular_price"),
                "Промо конкурента ₽": r.get("comp_promo_price"),
                "Разница ₽": r.get("effective_diff_rub"),
                "PI %": r.get("price_index_effective"),
                "Статус": st_val,
                "Предупреждения": r.get("alert") or "",
                "Файл": r.get("filename", "")
            })

        df_disp = pd.DataFrame(rows)

        if df_disp.empty:
            st.warning("По выбранным фильтрам позиций не найдено.")
        else:
            col_cfg = {
                "Код товара": st.column_config.TextColumn("Код товара", width=100),
                "Наименование товара (Самбери)": st.column_config.TextColumn("Наименование (Самбери)", width=250),
                "Распознано с ценника": st.column_config.TextColumn("С ценника", width=180),
                "Цена закупки ₽": st.column_config.NumberColumn("Закупка ₽", format="%.2f"),
                "Цена продажи ₽": st.column_config.NumberColumn("Продажа ₽", format="%.2f"),
                "Промо Самбери ₽": st.column_config.NumberColumn("Промо Смб ₽", format="%.2f"),
                "Цена конкурента ₽": st.column_config.NumberColumn("Цена конк. ₽", format="%.2f"),
                "Промо конкурента ₽": st.column_config.NumberColumn("Промо конк. ₽", format="%.2f"),
                "Разница ₽": st.column_config.NumberColumn("Разница ₽", format="%.2f"),
                "PI %": st.column_config.NumberColumn("PI %", format="%.1f%%"),
                "Статус": st.column_config.TextColumn("Статус", width=160),
                "Предупреждения": st.column_config.TextColumn("Предупреждения", width=200),
                "Файл": st.column_config.TextColumn("Файл", width=140),
            }
            st.dataframe(df_disp, column_config=col_cfg, use_container_width=True, height=500, hide_index=True)

        # ── Детальный просмотр ──
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("""<div style="color:#E2E8F0;font-weight:700;font-size:1.05rem;margin-bottom:12px;">
            🔍 Детальный просмотр и корректировка матчинга</div>""", unsafe_allow_html=True)

        fnames = [r.get("filename") for r in results if r.get("filename")]
        selected_file = st.selectbox("Выберите ценник:", fnames, label_visibility="collapsed")
        target = next((r for r in results if r.get("filename") == selected_file), None)

        if target:
            di1, di2 = st.columns([1, 2], gap="large")
            with di1:
                img_bytes = st.session_state.uploaded_images_cache.get(selected_file)
                if img_bytes:
                    st.image(img_bytes, use_container_width=True,
                             caption=f"📸 {selected_file}")
                else:
                    st.markdown(f"""
                    <div style="background:rgba(255,255,255,0.04);border:1px dashed rgba(255,255,255,0.1);
                                border-radius:12px;padding:40px;text-align:center;">
                        <div style="font-size:3rem;">🖼️</div>
                        <div style="color:#475569;font-size:0.85rem;margin-top:8px;">{selected_file}</div>
                    </div>""", unsafe_allow_html=True)

            with di2:
                # Распознанная информация
                conf = target.get("confidence", 0)
                conf_color = "#34D399" if conf >= 0.9 else ("#FBBF24" if conf >= 0.7 else "#F87171")
                st.markdown(f"""
                <div style="background:rgba(255,255,255,0.04);border-radius:12px;padding:18px;margin-bottom:16px;">
                    <div style="color:#64748B;font-size:0.75rem;font-weight:600;text-transform:uppercase;
                                letter-spacing:1px;margin-bottom:10px;">Распознано с ценника</div>
                    <div style="color:#E2E8F0;font-weight:700;font-size:1.05rem;margin-bottom:8px;">
                        {target.get('product_name', '—')}
                    </div>
                    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:10px;">
                        <div><span style="color:#64748B;font-size:0.8rem;">Бренд: </span>
                             <span style="color:#94A3B8;font-size:0.85rem;">{target.get('brand') or '—'}</span></div>
                        <div><span style="color:#64748B;font-size:0.8rem;">Фасовка: </span>
                             <span style="color:#94A3B8;font-size:0.85rem;">{target.get('weight_volume') or '—'}</span></div>
                    </div>
                    <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;">
                        <div style="background:rgba(99,102,241,0.15);border-radius:8px;padding:8px 14px;">
                            <div style="color:#64748B;font-size:0.7rem;">Цена конкурента</div>
                            <div style="color:#A5B4FC;font-weight:800;font-size:1.2rem;">
                                {target.get('comp_regular_price') or '—'} ₽</div>
                        </div>
                        {f'<div style="background:rgba(245,158,11,0.12);border-radius:8px;padding:8px 14px;"><div style="color:#64748B;font-size:0.7rem;">Промо конкурента</div><div style="color:#FBBF24;font-weight:800;font-size:1.2rem;">{target.get("comp_promo_price")} ₽</div></div>' if target.get("comp_promo_price") else ""}
                        <div style="margin-left:auto;">
                            <div style="color:#64748B;font-size:0.7rem;margin-bottom:2px;">Уверенность AI</div>
                            <div style="color:{conf_color};font-weight:700;font-size:1rem;">
                                {int(conf*100)}%</div>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # Матчинг
                cands = target.get("candidates", [])
                if cands:
                    st.markdown("""<div style="color:#64748B;font-size:0.75rem;font-weight:600;
                                    text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">
                        Сопоставление с базой Самбери</div>""", unsafe_allow_html=True)
                    
                    opts = [f"[{c['score']}%] {c['sku']} — {c['name']}" for c in cands]
                    sel = st.selectbox("Выбрать SKU:", opts, label_visibility="collapsed")
                    
                    if st.button("✅ Применить выбранный SKU", key="apply_sku"):
                        sku = sel.split("—")[0].split("]")[1].strip()
                        chosen = next((c for c in cands if str(c['sku']) == sku), None)
                        if chosen:
                            target.update({
                                "matched_sku": chosen["sku"],
                                "matched_name": chosen["name"],
                                "our_purchase_price": chosen["purchase_price"],
                                "our_sale_price": chosen["sale_price"],
                                "our_promo_price": chosen["promo_price"]
                            })
                            upd = calculate_price_metrics(target)
                            target.update(upd)
                            st.success(f"SKU {sku} применён!")
                            st.rerun()


# ─────────────────────────────────────────
# ВКЛАДКА 3: АНАЛИТИКА
# ─────────────────────────────────────────
with tab3:
    if not st.session_state.processed_results:
        st.markdown("""
        <div style="text-align:center;padding:60px 20px;">
            <div style="font-size:4rem;margin-bottom:16px;">📊</div>
            <div style="color:#E2E8F0;font-size:1.3rem;font-weight:700;margin-bottom:8px;">Нет данных для анализа</div>
            <div style="color:#475569;">Сначала проведите распознавание ценников</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        results = st.session_state.processed_results
        s = summarize_price_index(results)

        # KPI карточки
        kc1, kc2, kc3, kc4 = st.columns(4, gap="medium")
        with kc1:
            delta = round(s['avg_price_index'] - 100, 1)
            delta_sign = "+" if delta > 0 else ""
            st.markdown(f"""
            <div class="kpi-card kpi-blue">
                <div class="kpi-label">Средний Price Index</div>
                <div class="kpi-value">{s['avg_price_index']}%</div>
                <div class="kpi-caption">Отклонение от Самбери: {delta_sign}{delta}%</div>
            </div>""", unsafe_allow_html=True)

        with kc2:
            st.markdown(f"""
            <div class="kpi-card kpi-blue">
                <div class="kpi-label">Корзинный PI</div>
                <div class="kpi-value">{s['basket_price_index']}%</div>
                <div class="kpi-caption">Конк. {s['total_comp_basket']} ₽ vs Смб. {s['total_our_basket']} ₽</div>
            </div>""", unsafe_allow_html=True)

        with kc3:
            pct_cheaper = round(s['samberi_cheaper_count'] / s['total_items'] * 100, 0) if s['total_items'] > 0 else 0
            st.markdown(f"""
            <div class="kpi-card kpi-green">
                <div class="kpi-label">Самбери дешевле</div>
                <div class="kpi-value">{s['samberi_cheaper_count']}</div>
                <div class="kpi-caption">{int(pct_cheaper)}% позиций • паритет: {s['parity_count']}</div>
            </div>""", unsafe_allow_html=True)

        with kc4:
            st.markdown(f"""
            <div class="kpi-card {'kpi-yellow' if s['dumping_alerts_count'] > 0 else 'kpi-red'}">
                <div class="kpi-label">{'⚠️ Алерты демпинга' if s['dumping_alerts_count'] > 0 else 'Конкурент дешевле'}</div>
                <div class="kpi-value">{s['dumping_alerts_count'] if s['dumping_alerts_count'] > 0 else s['competitor_cheaper_count']}</div>
                <div class="kpi-caption">{'Цена конкурента ниже закупки Самбери!' if s['dumping_alerts_count'] > 0 else 'позиций уступаем конкуренту'}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # Графики
        gc1, gc2 = st.columns(2, gap="large")

        with gc1:
            cnts = pd.DataFrame([
                {"Статус": "✅ Самбери дешевле", "Кол-во": s['samberi_cheaper_count'], "color": "#34D399"},
                {"Статус": "❌ Конкурент дешевле", "Кол-во": s['competitor_cheaper_count'], "color": "#F87171"},
                {"Статус": "⚖️ Паритет (±2%)", "Кол-во": s['parity_count'], "color": "#60A5FA"},
            ])
            cnts = cnts[cnts["Кол-во"] > 0]
            if not cnts.empty:
                fig = px.pie(cnts, names="Статус", values="Кол-во", hole=0.55,
                             color="Статус",
                             color_discrete_map={
                                 "✅ Самбери дешевле": "#34D399",
                                 "❌ Конкурент дешевле": "#F87171",
                                 "⚖️ Паритет (±2%)": "#60A5FA"
                             })
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#94A3B8", family="Inter"),
                    title=dict(text="Распределение ценовых позиций", font=dict(color="#E2E8F0", size=14)),
                    legend=dict(font=dict(color="#94A3B8", size=11)),
                    margin=dict(l=0, r=0, t=40, b=0),
                    height=340
                )
                fig.update_traces(textfont_color="#E2E8F0")
                st.plotly_chart(fig, use_container_width=True)

        with gc2:
            diff_data = [
                {"Товар": (r.get("matched_name") or r.get("product_name",""))[:30],
                 "Разница ₽": r.get("effective_diff_rub")}
                for r in results if r.get("effective_diff_rub") is not None
            ]
            if diff_data:
                dd = pd.DataFrame(diff_data).sort_values("Разница ₽")
                dd["Цвет"] = dd["Разница ₽"].apply(lambda x: "#F87171" if x < 0 else "#34D399")
                fig2 = go.Figure(go.Bar(
                    x=dd["Разница ₽"],
                    y=dd["Товар"].str[:25],
                    orientation="h",
                    marker=dict(color=dd["Цвет"], opacity=0.85),
                    text=dd["Разница ₽"].apply(lambda x: f"{x:+.2f} ₽"),
                    textfont=dict(color="#E2E8F0", size=10),
                    textposition="outside"
                ))
                fig2.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#94A3B8", family="Inter"),
                    title=dict(text="Разница цен по позициям (₽)", font=dict(color="#E2E8F0", size=14)),
                    xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)", color="#475569"),
                    yaxis=dict(color="#94A3B8"),
                    margin=dict(l=0, r=80, t=40, b=0),
                    height=340
                )
                st.plotly_chart(fig2, use_container_width=True)


# ─────────────────────────────────────────
# ВКЛАДКА 4: ЭКСПОРТ
# ─────────────────────────────────────────
with tab4:
    if not st.session_state.processed_results:
        st.markdown("""
        <div style="text-align:center;padding:60px 20px;">
            <div style="font-size:4rem;margin-bottom:16px;">📥</div>
            <div style="color:#E2E8F0;font-size:1.3rem;font-weight:700;margin-bottom:8px;">Нет данных для экспорта</div>
            <div style="color:#475569;">Сначала проведите распознавание ценников во вкладке 1</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        ec1, ec2 = st.columns([2, 1], gap="large")
        with ec1:
            st.markdown("""
            <div style="margin-bottom:20px;">
                <div style="color:#E2E8F0;font-weight:800;font-size:1.4rem;margin-bottom:6px;">
                    📥 Выгрузка итогового отчета
                </div>
                <div style="color:#475569;font-size:0.9rem;">
                    Excel-файл с профессиональным оформлением, цветовой подсветкой и всеми расчётами
                </div>
            </div>
            """, unsafe_allow_html=True)

            excel_bytes = export_comparison_to_excel(st.session_state.processed_results)
            fname = f"Monitoring_Samberi_{time.strftime('%Y%m%d_%H%M')}.xlsx"

            st.download_button(
                label=f"⬇️  Скачать Excel-отчёт  ({len(st.session_state.processed_results)} позиций)",
                data=excel_bytes,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

            st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
            st.markdown("""
            <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);
                        border-radius:14px;padding:20px 24px;">
                <div style="color:#E2E8F0;font-weight:700;margin-bottom:14px;">📋 Состав отчёта</div>
            """, unsafe_allow_html=True)

            cols_info = [
                ("🔑 Код нашего товара", "SKU из базы Самбери"),
                ("📝 Наименование товара", "Официальное название по номенклатуре"),
                ("🏷️ Распознано с ценника", "Точный текст ценника конкурента"),
                ("💰 Цена закупки товара", "Себестоимость — для контроля демпинга"),
                ("🏪 Цена продажи товара", "Текущая розничная цена Самбери"),
                ("🎯 Цена на промо у товара", "Акционная цена Самбери"),
                ("🏬 Текущая цена конкурента", "Регулярная цена на ценнике"),
                ("⚡ Цена на промо у конкурента", "Акционная цена / по карте лояльности"),
                ("📊 Разница цен (₽)", "Эффективная разница цен с учётом промо"),
                ("📈 Price Index (PI %)", "Соотношение цен конкурента к Самбери"),
                ("🟢🔴 Статус выгодности", "Цветовая подсветка строк в Excel"),
                ("⚠️ Предупреждения", "Алерты демпинга ниже себестоимости"),
            ]
            for icon_name, desc in cols_info:
                st.markdown(f"""
                <div style="display:flex;align-items:flex-start;gap:10px;padding:7px 0;
                            border-bottom:1px solid rgba(255,255,255,0.04);">
                    <span style="color:#A5B4FC;font-weight:600;font-size:0.85rem;min-width:220px;">{icon_name}</span>
                    <span style="color:#475569;font-size:0.82rem;">{desc}</span>
                </div>
                """, unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

        with ec2:
            s = summarize_price_index(st.session_state.processed_results)
            st.markdown(f"""
            <div style="background:linear-gradient(145deg,#162032,#1E293B);border:1px solid rgba(255,255,255,0.07);
                        border-radius:16px;padding:24px;box-shadow:0 8px 32px rgba(0,0,0,0.3);">
                <div style="color:#E2E8F0;font-weight:700;font-size:1rem;margin-bottom:18px;">📊 Сводка отчёта</div>
                
                <div style="border-bottom:1px solid rgba(255,255,255,0.05);padding:10px 0;
                            display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#64748B;font-size:0.85rem;">Позиций в отчёте</span>
                    <span style="color:#A5B4FC;font-weight:700;">{s['total_items']}</span>
                </div>
                <div style="border-bottom:1px solid rgba(255,255,255,0.05);padding:10px 0;
                            display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#64748B;font-size:0.85rem;">Сопоставлено с базой</span>
                    <span style="color:#34D399;font-weight:700;">{s['matched_items']}</span>
                </div>
                <div style="border-bottom:1px solid rgba(255,255,255,0.05);padding:10px 0;
                            display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#64748B;font-size:0.85rem;">Средний Price Index</span>
                    <span style="color:#60A5FA;font-weight:700;">{s['avg_price_index']}%</span>
                </div>
                <div style="border-bottom:1px solid rgba(255,255,255,0.05);padding:10px 0;
                            display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#64748B;font-size:0.85rem;">Самбери дешевле</span>
                    <span style="color:#34D399;font-weight:700;">{s['samberi_cheaper_count']} поз.</span>
                </div>
                <div style="border-bottom:1px solid rgba(255,255,255,0.05);padding:10px 0;
                            display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#64748B;font-size:0.85rem;">Конкурент дешевле</span>
                    <span style="color:#F87171;font-weight:700;">{s['competitor_cheaper_count']} поз.</span>
                </div>
                <div style="padding:10px 0;display:flex;justify-content:space-between;align-items:center;">
                    <span style="color:#64748B;font-size:0.85rem;">⚠️ Алерты демпинга</span>
                    <span style="color:{'#FBBF24' if s['dumping_alerts_count']>0 else '#475569'};font-weight:700;">
                        {s['dumping_alerts_count']}
                    </span>
                </div>
            </div>
            """, unsafe_allow_html=True)
