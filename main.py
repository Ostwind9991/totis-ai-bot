import asyncio
import logging
import os
import re
from datetime import datetime, date
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ContentType
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
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
        # Таблиця зв’язків
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_links (
                group_message_id INTEGER PRIMARY KEY,
                user_id INTEGER,
                user_message_id INTEGER
            )
        """)
        # Таблиця логів
        await db.execute("""
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
                reply_text TEXT,
                replied_by TEXT,
                reply_timestamp TEXT,
                status TEXT
            )
        """)
        await db.commit()


# === Запис у таблицю ===
async def save_feedback(user, message_type, message_text, media_file_id, group_message_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO feedback_messages (
                user_id, user_name, username, message_type,
                message_text, media_file_id, group_message_id, timestamp, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.full_name,
            user.username,
            message_type,
            message_text,
            media_file_id,
            group_message_id,
            datetime.now().isoformat(timespec="seconds"),
            "new"
        ))
        await db.commit()


# === Оновлення відповіді ===
async def update_feedback_reply(group_message_id, reply_text, replied_by, status="replied"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE feedback_messages
            SET reply_text = ?, replied_by = ?, reply_timestamp = ?, status = ?
            WHERE group_message_id = ?
        """, (
            reply_text,
            replied_by,
            datetime.now().isoformat(timespec="seconds"),
            status,
            group_message_id
        ))
        await db.commit()


# === Допоміжні функції ===
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


# === /start ===
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
    username = f"(<a href='https://t.me/{user.username}'>@{user.username}</a>)" if user.username else ""
    user_info = f"👤 Повідомлення від <b>{user.full_name}</b> {username} (ID: <code>{user.id}</code>):"

    sent = None
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

    if sent:
        await save_link(sent.message_id, user.id, message.message_id)
        logging.info(f"➡️ Записано: group_msg={sent.message_id}, user_id={user.id}")


# === Відповідь із групи ===
@dp.message(F.chat.id == GROUP_CHAT_ID)
async def handle_group_reply(message: Message):
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
        await bot.send_message(GROUP_CHAT_ID, "⚠️ Не знайдено користувача для цього повідомлення.")
        return

    reply_text = message.text or "(без тексту)"
    formatted_reply = f"💬 Відповідь від support.totis:\n\n{reply_text}"

    try:
        await bot.send_message(user_id, formatted_reply, parse_mode="HTML")
        await update_feedback_reply(replied_group_msg_id, reply_text, "support.totis", "replied")
        await bot.send_message(GROUP_CHAT_ID, f"✅ Відповідь доставлено користувачу {user_id}")
    except Exception as e:
        await update_feedback_reply(replied_group_msg_id, reply_text, "support.totis", "failed")
        await bot.send_message(GROUP_CHAT_ID, f"⚠️ Не вдалося надіслати користувачу {user_id}\n{e}")


# === /stats ===
@dp.message(Command("stats"))
async def show_stats(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕒 За день", callback_data="stats_day"),
         InlineKeyboardButton(text="📈 Весь час", callback_data="stats_all")],
        [InlineKeyboardButton(text="🔄 Оновити", callback_data="stats_refresh")]
    ])
    await message.answer("Оберіть тип статистики:", reply_markup=kb)


# === Обробка кнопок ===
@dp.callback_query(F.data.startswith("stats"))
async def stats_callback(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        if callback.data == "stats_day":
            today = date.today().isoformat()
            async with db.execute("SELECT COUNT(*) FROM feedback_messages WHERE date(timestamp) = ?", (today,)) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM feedback_messages WHERE date(reply_timestamp) = ?", (today,)) as cur:
                replied = (await cur.fetchone())[0]
            pending = total - replied
            text = f"📊 <b>Статистика за сьогодні:</b>\n\nНових: {total}\nОчікують відповіді: {pending}\nВідповіли: {replied}"

        else:
            async with db.execute("SELECT COUNT(*) FROM feedback_messages") as cur:
                total = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM feedback_messages WHERE status='replied'") as cur:
                replied = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM feedback_messages WHERE status='failed'") as cur:
                failed = (await cur.fetchone())[0]
            pending = total - replied - failed
            text = f"📈 <b>Загальна статистика:</b>\n\nУсього: {total}\nОчікують: {pending}\nВідповіли: {replied}\nНе доставлено: {failed}"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=callback.message.reply_markup)
    await callback.answer("Оновлено ✅")


# === Запуск ===
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
