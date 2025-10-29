import os
import re
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message, User
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

def fmt_header(user: User) -> str:
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

ID_PATTERN = re.compile(r"🆔 <code>(\d+)</code>")

def extract_user_id_from_replied(replied: Message) -> int | None:
    """
    Дістає user_id з тексту або підпису у повідомленні бота,
    на яке відповів адмін у групі.
    """
    payload = (replied.text or "") + "\n" + (replied.caption or "")
    m = ID_PATTERN.search(payload)
    return int(m.group(1)) if m else None


@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.reply_to_message)
async def on_admin_reply(message: Message):
    """
    Адмін відповідає (Reply) у групі на повідомлення бота — бот шле це користувачу.
    Працює і для тексту, і для фото/документа від адміна.
    """
    try:
        me = await bot.me()
        if message.from_user.id == me.id:
            return  # ігноруємо власні

        replied = message.reply_to_message
        if not replied:
            return

        user_id = extract_user_id_from_replied(replied)
        if not user_id:
            await message.reply(
                "⚠️ Не знайшов ID користувача. Відповідайте саме на повідомлення бота з хедером.",
                reply=False,
            )
            return

        # Відправляємо залежно від того, що саме написав/прикріпив адмін
        if message.text:
            await bot.send_message(
                user_id,
                f"💬 <b>Відповідь від адміністратора:</b>\n\n{message.text}",
                parse_mode="HTML",
            )
        elif message.photo:
            await bot.send_photo(
                user_id,
                message.photo[-1].file_id,
                caption=message.caption or "",
            )
        elif message.document:
            await bot.send_document(
                user_id,
                message.document.file_id,
                caption=message.caption or "",
            )
        else:
            # fallback, щоб не “мовчало”, навіть якщо тип ще не покритий
            await bot.send_message(
                user_id,
                "💬 Адміністратор надіслав відповідь.",
            )

        await message.reply("✅ Надіслано користувачу.", reply=False)
        log.info(f"🔁 Admin reply sent to user {user_id}")

    except Exception as e:
        log.warning(f"Admin reply failed: {e}")
        try:
            await message.reply(f"❌ Помилка надсилання користувачу: {e}", reply=False)
        except:
            pass


# ---------------- STARTUP ----------------

async def main():
    me = await bot.me()
    log.info(f"Bot started as @{me.username} ({me.id})")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
