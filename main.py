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

# === Завантаження змінних середовища ===
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
                user_id INTEGER,
                user_message_id INTEGER
            )
        """)
        await db.commit()


async def save_link(group_message_id: int, user_id: int, user_message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO message_links (group_message_id, user_id, user_message_id) VALUES (?, ?, ?)",
            (group_message_id, user_id, user_message_id)
        )
        await db.commit()


async def get_user_by_group_message(group_message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM message_links WHERE group_message_id = ?", (group_message_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


# === Команда /start ===
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


# === Повідомлення від користувача ===
@dp.message(F.chat.type == "private", F.content_type.in_({
    ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.VOICE
}))
async def forward_to_group(message: Message):
    user = message.from_user
    user_id = user.id
    full_name = user.full_name or "Невідомо"
    username = f"(<a href='https://t.me/{user.username}'>@{user.username}</a>)" if user.username else ""

    user_info = f"👤 Повідомлення від <b>{full_name}</b> {username} (ID: <code>{user_id}</code>):"

    sent = None
    if message.text:
        caption = f"{user_info}\n\n{message.text}"
        sent = await bot.send_message(GROUP_CHAT_ID, caption, parse_mode="HTML", disable_web_page_preview=True)
    elif message.photo:
        caption = f"{user_info}\n\n🖼 Фото"
        sent = await bot.send_photo(GROUP_CHAT_ID, message.photo[-1].file_id, caption=caption, parse_mode="HTML")
    elif message.video:
        caption = f"{user_info}\n\n🎥 Відео"
        sent = await bot.send_video(GROUP_CHAT_ID, message.video.file_id, caption=caption, parse_mode="HTML")
    elif message.voice:
        caption = f"{user_info}\n\n🎙 Голосове повідомлення"
        sent = await bot.send_voice(GROUP_CHAT_ID, message.voice.file_id, caption=caption, parse_mode="HTML")

    if sent:
        await save_link(sent.message_id, user_id, message.message_id)
        logging.info(f"➡️ Збережено: group_msg={sent.message_id} ← user_id={user_id}, user_msg={message.message_id}")


# === Відповідь із групи ===
@dp.message(F.chat.id == GROUP_CHAT_ID)
async def handle_group_reply(message: Message):
    # Має бути reply на повідомлення бота
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return
    if message.reply_to_message.from_user.id != (await bot.me()).id:
        return

    replied_group_msg_id = message.reply_to_message.message_id
    user_id = await get_user_by_group_message(replied_group_msg_id)

    if not user_id:
        replied_text = message.reply_to_message.caption or message.reply_to_message.text or ""
        match = re.search(r"ID:\s*(\d+)", replied_text)
        if match:
            user_id = int(match.group(1))

    if not user_id:
        await bot.send_message(GROUP_CHAT_ID, "⚠️ Не вдалося знайти користувача для цього повідомлення.")
        return

    admin_name = message.from_user.full_name or "Адміністратор TOTIS"
    admin_username = f" (@{message.from_user.username})" if message.from_user.username else ""

    reply_text = message.text or "(без тексту)"
    formatted_reply = (
        f"💬 Відповідь від <b>{admin_name}</b>{admin_username}:\n\n{reply_text}"
    )

    try:
        await bot.send_message(user_id, formatted_reply, parse_mode="HTML")
        await bot.send_message(GROUP_CHAT_ID, f"✅ Відповідь доставлено користувачу {user_id}")
    except Exception as e:
        await bot.send_message(GROUP_CHAT_ID, f"⚠️ Не вдалося надіслати користувачу {user_id}\n{e}")


# === Основна функція ===
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main()) 

# Запуск бота