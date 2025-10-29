import os
import re
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramMigrateToChat


# ---------------- CONFIG ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не вказано в середовищі!")

GROUP_CHAT_ID_ENV = os.getenv("GROUP_CHAT_ID", "").strip()
if not GROUP_CHAT_ID_ENV:
    raise RuntimeError("❌ GROUP_CHAT_ID не вказано в середовищі!")

GROUP_CHAT_ID = int(GROUP_CHAT_ID_ENV)

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("totis-bot")

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()


# ---------------- HELPERS ----------------

def fmt_header(user: Message.from_user) -> str:
    """Створює хедер для повідомлення в групу."""
    fullname = user.full_name
    uname = f"@{user.username}" if user.username else "—"
    return (
        "<b>Тестування штучного інтелекту в застосунку TOTIS</b>\n"
        "<b>Нове повідомлення</b>\n"
        f"👤 {fullname} ({uname})\n"
        f"🆔 <code>{user.id}</code>\n\n"
    )


async def send_to_group_safe(text: str = "", photo_id: str = None, document_id: str = None, caption: str = None):
    """Надсилає повідомлення в групу. Обробляє випадок, коли групу апгрейдили в супергрупу."""
    global GROUP_CHAT_ID
    try:
        if photo_id:
            sent = await bot.send_photo(GROUP_CHAT_ID, photo_id, caption=caption or text)
        elif document_id:
            sent = await bot.send_document(GROUP_CHAT_ID, document_id, caption=caption or text)
        else:
            sent = await bot.send_message(GROUP_CHAT_ID, text)
        return sent.message_id
    except TelegramMigrateToChat as e:
        new_id = e.params.migrate_to_chat_id
        GROUP_CHAT_ID = new_id
        log.warning(f"Група мігрувала в супергрупу: {new_id}")
        if photo_id:
            sent = await bot.send_photo(GROUP_CHAT_ID, photo_id, caption=caption or text)
        elif document_id:
            sent = await bot.send_document(GROUP_CHAT_ID, document_id, caption=caption or text)
        else:
            sent = await bot.send_message(GROUP_CHAT_ID, text)
        return sent.message_id
    except Exception as e:
        log.warning(f"Помилка надсилання в групу: {e}")


# ---------------- COMMANDS ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Привітання користувача."""
    if message.chat.type != ChatType.PRIVATE:
        return
    await message.answer(
        "Привіт 👋\nНадішліть текст, фото або документ — я передам його адміністраторам TOTIS."
    )


# ---------------- USER → GROUP ----------------

@dp.message(F.chat.type == ChatType.PRIVATE, F.text)
async def user_text(message: Message):
    """Текст користувача → група."""
    me = await bot.me()
    if message.from_user.id == me.id:
        return

    header = fmt_header(message.from_user)
    body = message.text
    await send_to_group_safe(text=f"{header}{body}")


@dp.message(F.chat.type == ChatType.PRIVATE, F.photo)
async def user_photo(message: Message):
    """Фото користувача → група."""
    me = await bot.me()
    if message.from_user.id == me.id:
        return

    header = fmt_header(message.from_user)
    caption = message.caption or ""
    photo = message.photo[-1]
    await send_to_group_safe(photo_id=photo.file_id, caption=f"{header}{caption}")


@dp.message(F.chat.type == ChatType.PRIVATE, F.document)
async def user_document(message: Message):
    """Документ користувача → група."""
    me = await bot.me()
    if message.from_user.id == me.id:
        return

    header = fmt_header(message.from_user)
    caption = message.caption or ""
    await send_to_group_safe(document_id=message.document.file_id, caption=f"{header}{caption}")


# ---------------- GROUP → USER (Reply handler) ----------------

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.reply_to_message)
async def on_admin_reply(message: Message):
    """
    Якщо адміністратор відповідає (Reply) у групі на повідомлення бота,
    бот надсилає цей текст користувачу, ID якого було в оригінальному повідомленні.
    """
    try:
        replied = message.reply_to_message
        if not replied or not replied.text:
            return

        # Парсимо user_id з оригінального повідомлення
        match = re.search(r"🆔 <code>(\d+)</code>", replied.text)
        if not match:
            return

        user_id = int(match.group(1))
        me = await bot.me()
        if user_id == me.id:
            return

        text = message.text or "(без тексту)"
        await bot.send_message(
            user_id,
            f"💬 <b>Відповідь від адміністратора:</b>\n\n{text}",
            parse_mode="HTML",
        )
        await message.reply("✅ Надіслано користувачу.", reply=False)
        log.info(f"🔁 Admin reply sent to user {user_id}")

    except Exception as e:
        log.warning(f"Admin reply failed: {e}")
        await message.reply(f"❌ Помилка надсилання користувачу: {e}", reply=False)


# ---------------- STARTUP ----------------

async def main():
    me = await bot.me()
    log.info(f"Bot started as @{me.username} ({me.id})")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
