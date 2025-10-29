import asyncio
import logging
import os
import re
from datetime import datetime, date
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ContentType
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
)
from dotenv import load_dotenv

# === Ініціалізація ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
PDF_URL = os.getenv("PDF_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_PATH = "links.db"

# === Ініціалізація бази ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
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

# === Запис у базу ===
async def save_feedback(user, message_type, message_text, media_file_id, group_message_id):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO feedback_messages (
                    user_id, user_name, username, phone,
                    message_type, message_text, media_file_id,
                    group_message_id, timestamp, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user.id,
                user.full_name,
                user.username,
                None,
                message_type,
                message_text,
                media_file_id,
                group_message_id,
                datetime.now().isoformat(timespec="seconds"),
                "new"
            ))
            await db.commit()
    except Exception as e:
        logging.error(f"DB error while saving feedback: {e}")

async def update_phone(user_id, phone):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE feedback_messages SET phone=? WHERE user_id=?", (phone, user_id))
        await db.commit()

async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT user_id FROM feedback_messages") as cur:
            users = await cur.fetchall()
            return [u[0] for u in users if u[0] is not None]

# === /start ===
@dp.message(CommandStart())
async def start_handler(message: Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📞 Поділитися номером", request_contact=True))
    welcome_text = (
        "👋 Вітаємо в проєкті <b>«Тестування штучного інтелекту в застосунку TOTIS»</b>!\n\n"
        "🧾 Ознайомтесь з інструкцією за посиланням:\n"
        f"{PDF_URL}\n\n"
        "Після цього можете поділитися своїм номером телефону, щоб ми могли зв’язатися при необхідності, "
        "або просто надішліть своє повідомлення 💬"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=kb)

# === Обробка контактів ===
@dp.message(F.contact)
async def handle_contact(message: Message):
    contact = message.contact
    phone = contact.phone_number
    user_id = contact.user_id or message.from_user.id

    await update_phone(user_id, phone)
    await message.answer(f"✅ Дякуємо! Ваш номер {phone} збережено.", reply_markup=types.ReplyKeyboardRemove())
    await bot.send_message(GROUP_CHAT_ID, f"📞 Користувач {message.from_user.full_name} поділився номером: {phone}")

# === Повідомлення від користувачів ===
@dp.message(F.chat.type == "private", F.content_type.in_({
    ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.VOICE
}))
async def forward_to_group(message: Message):
    user = message.from_user
    username = f"(<a href='https://t.me/{user.username}'>@{user.username}</a>)" if user.username else ""
    user_info = f"👤 <b>{user.full_name}</b> {username}\nID: <code>{user.id}</code>"

    sent = None
    try:
        if message.text:
            caption = f"{user_info}\n\n{message.text}"
            sent = await bot.send_message(GROUP_CHAT_ID, caption, parse_mode="HTML", disable_web_page_preview=True)
            await save_feedback(user, "text", message.text, None, sent.message_id)
        elif message.photo:
            caption = f"{user_info}\n\n🖼 Фото"
            sent = await bot.send_photo(GROUP_CHAT_ID, message.photo[-1].file_id, caption=caption, parse_mode="HTML")
            await save_feedback(user, "photo", None, message.photo[-1].file_id, sent.message_id)
        elif message.video:
            caption = f"{user_info}\n\n🎥 Відео"
            sent = await bot.send_video(GROUP_CHAT_ID, message.video.file_id, caption=caption, parse_mode="HTML")
            await save_feedback(user, "video", None, message.video.file_id, sent.message_id)
        elif message.voice:
            caption = f"{user_info}\n\n🎙 Голосове повідомлення"
            sent = await bot.send_voice(GROUP_CHAT_ID, message.voice.file_id, caption=caption, parse_mode="HTML")
            await save_feedback(user, "voice", None, message.voice.file_id, sent.message_id)
    except Exception as e:
        logging.error(f"Error forwarding user message: {e}")

# === Відповідь із групи ===
@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message, flags={"block": False})
async def reply_from_group(message: Message):
    replied_text = message.reply_to_message.caption or message.reply_to_message.text or ""
    match = re.search(r"ID:\s*(\d+)", replied_text)
    if not match:
        return await bot.send_message(GROUP_CHAT_ID, "⚠️ Не знайдено ID користувача.")
    user_id = int(match.group(1))

    reply_text = message.text or "(без тексту)"
    formatted_reply = f"💬 Відповідь від support.totis:\n\n{reply_text}"

    try:
        await bot.send_message(user_id, formatted_reply, parse_mode="HTML")
        await bot.send_message(GROUP_CHAT_ID, f"✅ Відповідь доставлено користувачу {user_id}")
    except Exception as e:
        await bot.send_message(GROUP_CHAT_ID, f"⚠️ Не вдалося надіслати користувачу {user_id}\n{e}")

# === 1. Розсилка кнопки "Поділитися номером" ===
@dp.message(Command("broadcast_phones"))
async def broadcast_phones(message: Message):
    if message.chat.id != GROUP_CHAT_ID:
        return
    users = await get_all_user_ids()
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📞 Поділитися номером", request_contact=True))

    sent_count, failed = 0, 0
    for uid in users:
        try:
            await bot.send_message(uid, "📞 Будь ласка, поділіться своїм номером телефону", reply_markup=kb)
            sent_count += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Failed to send to {uid}: {e}")

    await message.answer(f"📢 Розіслано {sent_count} користувачам, не доставлено: {failed}")

# === 2. Розсилка кастомного тексту ===
broadcast_text = {}

@dp.message(Command("broadcast_message"))
async def prepare_broadcast_text(message: Message):
    if message.chat.id != GROUP_CHAT_ID:
        return
    broadcast_text["awaiting"] = True
    await message.answer("✏️ Відправ реплаєм повідомлення, яке потрібно розіслати всім користувачам.")

@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def handle_broadcast_reply(message: Message):
    if not broadcast_text.get("awaiting"):
        return
    broadcast_text["awaiting"] = False

    users = await get_all_user_ids()
    sent_count, failed = 0, 0
    for uid in users:
        try:
            await bot.send_message(uid, message.text)
            sent_count += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Failed to send broadcast to {uid}: {e}")

    await message.answer(f"✅ Розіслано {sent_count} користувачам, не доставлено: {failed}")

# === 3. Відправка одному користувачу ===
target_user = {}

@dp.message(Command("send_user"))
async def send_to_specific_user(message: Message):
    if message.chat.id != GROUP_CHAT_ID:
        return
    try:
        user_id = int(message.text.split()[1])
        target_user["id"] = user_id
        await message.answer(f"🟢 Вкажи текст або надішли фото/відео для користувача {user_id}")
    except:
        await message.answer("⚠️ Формат: /send_user <user_id>")

@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message == None)
async def handle_admin_send(message: Message):
    if not target_user.get("id"):
        return
    user_id = target_user.pop("id")
    try:
        if message.text:
            await bot.send_message(user_id, message.text)
        elif message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption)
        elif message.video:
            await bot.send_video(user_id, message.video.file_id, caption=message.caption)
        await message.answer(f"✅ Повідомлення надіслано користувачу {user_id}")
    except Exception as e:
        await message.answer(f"⚠️ Не вдалося надіслати користувачу {user_id}\n{e}")

# === Запуск ===
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
