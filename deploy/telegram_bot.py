"""
Телеграм-бот для мобильного мониторинга ценников "Самбери".
Сотрудник отправляет пачку фото ценников прямо из торгового зала,
бот распознает их и присылает готовый Excel с расчетом Price Index.
"""

import os
import sys
import io
import time
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.vision_extractor import PriceTagExtractor
from core.matcher import CatalogMatcher
from core.analytics import calculate_price_metrics, summarize_price_index
from core.exporter import export_comparison_to_excel

# Загружаем каталог Самбери по умолчанию
CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "samples", "samberi_catalog_sample.xlsx")

def run_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[!] Внимание: TELEGRAM_BOT_TOKEN не задан в .env файле.")
        print("Для работы бота укажите TELEGRAM_BOT_TOKEN и GEMINI_API_KEY в .env")
        return

    try:
        from telegram import Update
        from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
    except ImportError:
        print("[!] Библиотека python-telegram-bot не установлена. Установите: pip install python-telegram-bot")
        return

    # Память сессий пользователей: user_id -> list of photos
    user_sessions = {}

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🛒 *Самбери: Бот мониторинга ценников*\n\n"
            "Отправляйте мне фотографии ценников конкурентов (по одной или пачкой).\n"
            "Когда закончите отправку, напишите команду /finish — я сформирую готовый Excel-отчет с расчетом Price Index!",
            parse_mode="Markdown"
        )

    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in user_sessions:
            user_sessions[user_id] = []

        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        idx = len(user_sessions[user_id]) + 1
        user_sessions[user_id].append({
            "data": bytes(photo_bytes),
            "filename": f"tag_{user_id}_{idx}.jpg",
            "mime": "image/jpeg"
        })
        
        await update.message.reply_text(f"📸 Ценник #{idx} принят. (Всего: {len(user_sessions[user_id])}). Отправьте еще или напишите /finish")

    async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        photos = user_sessions.get(user_id, [])
        if not photos:
            await update.message.reply_text("Вы еще не отправили ни одного фото ценника.")
            return

        msg = await update.message.reply_text(f"⏳ Начинаю распознавание {len(photos)} ценников через Vision AI...")
        
        # 1. Vision AI
        extractor = PriceTagExtractor(provider="gemini" if os.getenv("GEMINI_API_KEY") else "mock")
        recognized = extractor.extract_batch(photos, max_workers=6)

        # 2. Matcher
        catalog_df = pd.read_excel(CATALOG_PATH) if os.path.exists(CATALOG_PATH) else pd.DataFrame()
        matcher = CatalogMatcher(catalog_df)
        matched = matcher.match_all(recognized)

        # 3. Analytics
        processed = [calculate_price_metrics(it) for it in matched]
        summary = summarize_price_index(processed)

        # 4. Excel
        excel_bytes = export_comparison_to_excel(processed, competitor_name="Конкурент")

        # Отправка сводки и файла
        report_text = (
            f"📊 *Результаты мониторинга:*\n"
            f"• Обработано: {summary['total_items']} ценников\n"
            f"• Сопоставлено с базой Самбери: {summary['matched_items']}\n"
            f"• *Средний Price Index:* {summary['avg_price_index']}%\n"
            f"• ✅ Самбери дешевле: {summary['samberi_cheaper_count']} поз.\n"
            f"• ❌ Конкурент дешевле: {summary['competitor_cheaper_count']} поз.\n"
            f"• ⚠️ Алерты демпинга: {summary['dumping_alerts_count']}\n"
        )
        await update.message.reply_text(report_text, parse_mode="Markdown")

        excel_file = io.BytesIO(excel_bytes)
        excel_file.name = f"Monitoring_Samberi_{time.strftime('%Y%m%d_%H%M')}.xlsx"
        await update.message.reply_document(document=excel_file, caption="📥 Итоговый отчет в Excel")

        # Очищаем сессию
        user_sessions[user_id] = []

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("finish", finish))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("[+] Телеграм-бот запущен и ожидает фото ценников...")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
