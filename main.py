import asyncio
import logging
import os
import re
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

# === Налаштування ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
PDF_URL = os.getenv("PDF_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_PATH = "links.db"

# === Ініціалізація бази даних ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_links (
                group_message_id INTEGER PRIMARY KEY,
                user_id INTEGER
            )
        """)
        await db.commit()

async def save_link(group_message_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO message_links VALUES (?, ?)", (group_message_id, user_id))
        await db.commit()

async def get_user_by_group_message(group_message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM message_links WHERE group_message_id = ?", (group_message_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


# === Обробка команд ===
@dp.message(CommandStart())
async def start_handler(message: Message):
    welcome_text = (
        "👋 Вітаємо в проєкті <b>«Тестування штучного інтелекту в застосунку TOTIS»</b>!\n\n"
        "🧾 Ознайомтесь з інструкцією за посиланням:\n"
        f"{PDF_URL}\n\n"
        "Після цього можете надіслати своє повідомлення, фото або відео — "
        "воно автоматично потрапить до команди розробників."
    )
    await message.answer(welcome_text, parse_mode="HTML")


# === Обробка повідомлень користувачів ===
@dp.message(F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.VOICE}))
async def forward_to_group(message: Message):
    user = message.from_user
    user_info = f"👤 Від: {user.full_name or 'Невідомо'} (ID: {user.id})"

    sent = None
    if message.text:
        caption = f"✉️ Нове повідомлення від користувача\n{user_info}\n\n{message.text}"
        sent = await bot.send_message(GROUP_CHAT_ID, caption)
    elif message.photo:
        caption = f"🖼 Фото від користувача\n{user_info}"
        sent = await bot.send_photo(GROUP_CHAT_ID, photo=message.photo[-1].file_id, caption=caption)
    elif message.video:
        caption = f"🎥 Відео від користувача\n{user_info}"
        sent = await bot.send_video(GROUP_CHAT_ID, video=message.video.file_id, caption=caption)
    elif message.voice:
        caption = f"🎙 Голосове повідомлення від користувача\n{user_info}"
        sent = await bot.send_voice(GROUP_CHAT_ID, voice=message.voice.file_id, caption=caption)

    if sent:
        await save_link(sent.message_id, user.id)
        logging.info(f"Збережено зв’язок group_msg={sent.message_id} → user_id={user.id}")


# === Обробка відповідей із групи ===
@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def reply_from_group(message: Message):
    # Переконуємось, що відповідь дана саме на повідомлення бота
    if not message.reply_to_message.from_user or message.reply_to_message.from_user.id != (await bot.me()).id:
        return

    replied_message_id = message.reply_to_message.message_id
    user_id = await get_user_by_group_message(replied_message_id)

    # Якщо не знайшли через базу — fallback: шукаємо ID у тексті/підписі
    if not user_id:
        replied_text = message.reply_to_message.caption or message.reply_to_message.text or ""
        match = re.search(r"ID:\s*(\d+)", replied_text)
        if match:
            user_id = int(match.group(1))

    if not user_id:
        await bot.send_message(GROUP_CHAT_ID, "⚠️ Не вдалося визначити користувача для цього повідомлення.")
        return

    reply_text = message.text or "(без тексту)"

    try:
        await bot.send_message(user_id, f"💬 Відповідь від команди:\n\n{reply_text}")
        await bot.send_message(GROUP_CHAT_ID, f"✅ Відповідь доставлено користувачу {user_id}")
    except Exception as e:
        await bot.send_message(GROUP_CHAT_ID, f"⚠️ Не вдалося доставити повідомлення користувачу {user_id}\n{e}")


# === Головна функція ===
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
