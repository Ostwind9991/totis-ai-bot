import asyncio
import logging
import os
import json
import aiosqlite
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from google.oauth2.service_account import Credentials
import gspread
from dotenv import load_dotenv

# ==========================
# 🔹 INIT
# ==========================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
PDF_URL = os.getenv("PDF_URL")
DB_PATH = "links.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==========================
# 🔹 CREATE DB
# ==========================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS feedback_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            username TEXT,
            phone TEXT,
            message_type TEXT,
            message_text TEXT,
            media_file_id TEXT,
            group_message_id INTEGER,
            timestamp TEXT,
            reply_text TEXT,
            replied_by TEXT,
            reply_timestamp TEXT,
            status TEXT
        )
        """)
        await db.commit()

# ==========================
# 🔹 SAVE MESSAGE
# ==========================
async def save_feedback(user, message_type, text=None, media_id=None, group_message_id=None, status="received"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO feedback_messages (
            user_id, user_name, username, message_type, message_text,
            media_file_id, group_message_id, timestamp, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.full_name,
            user.username,
            message_type,
            text,
            media_id,
            group_message_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status
        ))
        await db.commit()

# ==========================
# 🔹 START
# ==========================
@dp.message(CommandStart())
async def start_handler(message: Message):
    welcome_text = (
        "👋 Вітаємо в проєкті <b>«Тестування штучного інтелекту в застосунку TOTIS»</b>!\n\n"
        f"🧾 Ознайомтесь з інструкцією:\n{PDF_URL}\n\n"
        "Після цього можете надіслати своє повідомлення, фото або відео — "
        "воно автоматично потрапить до команди розробників."
    )
    await message.answer(welcome_text, parse_mode="HTML")

# ==========================
# 🔹 USER → GROUP
# ==========================
@dp.message(F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.VOICE}))
async def forward_to_group(message: Message):
    user = message.from_user
    user_link = f"<a href='tg://user?id={user.id}'>{user.full_name}</a>"
    user_info = f"👤 {user_link} (@{user.username})\n🆔 ID: <code>{user.id}</code>"

    sent = None
    text_to_save = None
    media_id = None

    if message.text:
        text_to_save = message.text
        caption = f"✉️ Нове повідомлення\n{user_info}\n\n{message.text}"
        sent = await bot.send_message(GROUP_CHAT_ID, caption, parse_mode="HTML")

    elif message.photo:
        text_to_save = message.caption or ""
        caption = f"🖼 Фото від користувача\n{user_info}"
        if text_to_save:
            caption += f"\n\n{text_to_save}"
        media_id = message.photo[-1].file_id
        sent = await bot.send_photo(GROUP_CHAT_ID, media_id, caption=caption, parse_mode="HTML")

    elif message.video:
        text_to_save = message.caption or ""
        caption = f"🎥 Відео від користувача\n{user_info}"
        if text_to_save:
            caption += f"\n\n{text_to_save}"
        media_id = message.video.file_id
        sent = await bot.send_video(GROUP_CHAT_ID, media_id, caption=caption, parse_mode="HTML")

    elif message.voice:
        caption = f"🎙 Голосове повідомлення\n{user_info}"
        media_id = message.voice.file_id
        sent = await bot.send_voice(GROUP_CHAT_ID, media_id, caption=caption, parse_mode="HTML")

    if sent:
        await save_feedback(user, message.content_type, text_to_save, media_id, sent.message_id)

# ==========================
# 🔹 GROUP → USER (reply)
# ==========================
@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def reply_from_group(message: Message):
    import re
    replied_text = message.reply_to_message.caption or message.reply_to_message.text or ""
    match = re.search(r"ID:\s*(\d+)", replied_text)
    if not match:
        await bot.send_message(GROUP_CHAT_ID, "⚠️ Не вдалося знайти ID користувача.")
        return

    user_id = int(match.group(1))
    reply_text = message.text or "(без тексту)"

    try:
        await bot.send_message(user_id, f"💬 Відповідь від команди support.totis:\n\n{reply_text}")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
            UPDATE feedback_messages
            SET reply_text = ?, replied_by = ?, reply_timestamp = ?, status = ?
            WHERE user_id = ?
            """, (
                reply_text,
                "support.totis",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "replied",
                user_id
            ))
            await db.commit()
        await bot.send_message(GROUP_CHAT_ID, f"✅ Відповідь надіслано користувачу {user_id}")
    except Exception as e:
        await bot.send_message(GROUP_CHAT_ID, f"⚠️ Не вдалося надіслати повідомлення {user_id}\n{e}")

# ==========================
# 🔹 /STATS
# ==========================
@dp.message(Command("stats"))
async def stats_handler(message: Message):
    if message.chat.id != GROUP_CHAT_ID:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM feedback_messages") as c:
            total = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM feedback_messages WHERE date(timestamp)=date('now')") as c:
            today = (await c.fetchone())[0]

    await message.answer(
        f"📊 <b>Статистика повідомлень</b>\n\n"
        f"За сьогодні: <b>{today}</b>\n"
        f"Всього: <b>{total}</b>", parse_mode="HTML"
    )

# ==========================
# 🔹 /EXPORT → GOOGLE SHEETS
# ==========================
async def run_export():
    creds_json = os.getenv("GOOGLE_KEY_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

    gc = gspread.authorize(creds)
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1

    ws.clear()
    ws.append_row([
        "id", "user_id", "user_name", "username", "phone", "message_type",
        "message_text", "media_file_id", "group_message_id",
        "timestamp", "reply_text", "replied_by", "reply_timestamp", "status"
    ])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM feedback_messages") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                ws.append_row([str(x) if x is not None else "" for x in row])

@dp.message(Command("export"))
async def export_to_sheets(message: Message):
    if message.chat.id != GROUP_CHAT_ID:
        return
    await message.answer("📤 Розпочинаю експорт у Google Sheets...")
    try:
        await run_export()
        await message.answer("✅ Експорт завершено успішно!")
    except Exception as e:
        await message.answer(f"⚠️ Помилка експорту:\n<code>{e}</code>", parse_mode="HTML")

# ==========================
# 🔹 RUN
# ==========================
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
