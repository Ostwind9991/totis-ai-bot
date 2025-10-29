import logging
import asyncio
import json
import os
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# === ЛОГИ ===
logging.basicConfig(level=logging.INFO)

# === КОНСТАНТИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
GOOGLE_KEY_JSON = os.getenv("GOOGLE_KEY_JSON")

# === ІНІЦІАЛІЗАЦІЯ ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === GOOGLE SHEETS ===
def init_gsheets():
    try:
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_KEY_JSON))
        gc = gspread.authorize(creds)
        sh = gc.open("TOTIS_AI_BOT_LOGS").sheet1
        return sh
    except Exception as e:
        logging.error(f"Помилка підключення до Google Sheets: {e}")
        return None

sheet = init_gsheets()

# === SQLITE ===
def init_db():
    conn = sqlite3.connect("messages.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            username TEXT,
            message_type TEXT,
            message_text TEXT,
            media_file_id TEXT,
            group_message_id INTEGER,
            timestamp TEXT,
            phone TEXT,
            reply_text TEXT,
            replied_by TEXT,
            reply_timestamp TEXT,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

async def save_message(message: Message, msg_type="text", media_file_id=None):
    user = message.from_user
    conn = sqlite3.connect("messages.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO feedback_messages (user_id, user_name, username, message_type, message_text, media_file_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        user.id,
        user.full_name,
        user.username,
        msg_type,
        message.text or "",
        media_file_id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()

    # === ЕКСПОРТ В GOOGLE SHEETS ===
    if sheet:
        try:
            sheet.append_row([
                user.id, user.full_name, user.username,
                msg_type, message.text or "", media_file_id or "",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])
        except Exception as e:
            logging.warning(f"Помилка автологування в Google Sheets: {e}")

    # === ДУБЛЮЄМО В ГРУПУ ===
    msg = f"<b>Нове повідомлення</b>\n👤 <b>{user.full_name}</b> (@{user.username})\n🆔 <code>{user.id}</code>\n\n{message.text or ''}"
    await bot.send_message(GROUP_CHAT_ID, msg, parse_mode="HTML")

# === ОБРОБНИКИ ===

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("👋 Вітаю! Напишіть своє запитання або залиште номер телефону для зв'язку.")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    conn = sqlite3.connect("messages.db")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM feedback_messages")
    count = cur.fetchone()[0]
    conn.close()
    await message.answer(f"📊 Всього отримано повідомлень: <b>{count}</b>", parse_mode="HTML")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if str(message.from_user.id) != os.getenv("ADMIN_ID"):
        await message.answer("⛔ Немає доступу.")
        return

    await message.answer("Введіть текст для масової розсилки:")
    dp.message.register(handle_broadcast_text)

async def handle_broadcast_text(message: Message):
    text = message.text
    conn = sqlite3.connect("messages.db")
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT user_id FROM feedback_messages")
    users = cur.fetchall()
    conn.close()

    success, fail = 0, 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, text)
            success += 1
        except Exception:
            fail += 1
            continue

    await message.answer(f"✅ Успішно: {success}\n❌ Помилки: {fail}")
    dp.message.unregister(handle_broadcast_text)

# === Всі типи повідомлень ===

@dp.message()
async def handle_all(message: Message):
    if message.text and message.text.isdigit() and len(message.text) >= 9:
        await save_message(message, msg_type="text_phone")
    elif message.photo:
        await save_message(message, msg_type="photo", media_file_id=message.photo[-1].file_id)
    elif message.document:
        await save_message(message, msg_type="file", media_file_id=message.document.file_id)
    else:
        await save_message(message)

# === ЗАПУСК ===
async def main():
    logging.info("🚀 Бот запущено.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
