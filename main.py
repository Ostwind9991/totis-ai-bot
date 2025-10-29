import os
import json
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, BufferedInputFile
from aiogram.exceptions import TelegramMigrateToChat

import gspread
from google.oauth2 import service_account


# ----------------------- CONFIG / ENV -----------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# Може бути як int, так і str у Railway — нормалізуємо в int
_GROUP_CHAT_ID_ENV = os.getenv("GROUP_CHAT_ID", "").strip()
if not _GROUP_CHAT_ID_ENV:
    raise RuntimeError("GROUP_CHAT_ID is not set")
GROUP_CHAT_ID: int = int(_GROUP_CHAT_ID_ENV)

# Список адмінідів через кому
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

# Google Sheets
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
if not GOOGLE_SHEET_ID:
    raise RuntimeError("GOOGLE_SHEET_ID is not set")

# Секрет з Railway Variables: GOOGLE_KEY_JSON = весь JSON ключа сервісного акаунта (одним рядком)
GOOGLE_KEY_JSON = os.getenv("GOOGLE_KEY_JSON", "").strip()
if not GOOGLE_KEY_JSON:
    raise RuntimeError("GOOGLE_KEY_JSON is not set")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("totis-bot")

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()


# ----------------------- GOOGLE SHEETS -----------------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _open_sheets():
    """Authorize and open spreadsheet + ensure worksheets exist."""
    info = json.loads(GOOGLE_KEY_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)

    def get_or_create(ws_title: str, header: list[str]):
        try:
            ws = sh.worksheet(ws_title)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=ws_title, rows=1, cols=max(10, len(header)))
            ws.append_row(header)
        # якщо порожній — додамо заголовок
        if ws.row_count == 1 and (not ws.get_values("1:1")):
            ws.append_row(header)
        return ws

    messages_ws = get_or_create(
        "messages",
        [
            "id",               # автоінкремент (рядок у таблиці)
            "user_id",
            "user_fullname",
            "username",
            "message_type",
            "message_text",
            "media_file_id",
            "group_message_id",
            "timestamp_iso",
        ],
    )

    subscribers_ws = get_or_create(
        "subscribers",
        ["user_id", "username", "user_fullname", "first_seen_iso"],
    )

    return sh, messages_ws, subscribers_ws

# Ліниво ініціалізуємо при старті
SPREADSHEET, MESSAGES_WS, SUBS_WS = _open_sheets()

# Кеш сабскрайберів у пам'яті (щоб швидко розсилати)
_subscribers: set[int] = set()

def _load_subscribers_into_cache():
    global _subscribers
    try:
        values = SUBS_WS.get_all_records()
        _subscribers = {int(r["user_id"]) for r in values if str(r.get("user_id", "")).strip().isdigit()}
        log.info("Loaded %d subscribers from sheet", len(_subscribers))
    except Exception as e:
        log.warning("Failed to load subscribers: %s", e)
        _subscribers = set()

def _add_subscriber_if_new(user_id: int, username: str | None, fullname: str):
    """Додає користувача в аркуш subscribers, якщо його там ще нема."""
    if user_id in _subscribers:
        return
    when = datetime.now(timezone.utc).isoformat()
    SUBS_WS.append_row([user_id, (username or ""), fullname, when])
    _subscribers.add(user_id)
    log.info("New subscriber added: %s (%s)", user_id, username or "-")

def _append_message_row(
    user_id: int,
    fullname: str,
    username: str | None,
    message_type: str,
    message_text: str,
    media_file_id: str | None,
    group_message_id: int | None,
):
    try:
        ts = datetime.now(timezone.utc).isoformat()
        row = [
            "",  # id — просто порядковий номер рядка з боку Sheets
            user_id,
            fullname,
            (username or ""),
            message_type,
            message_text,
            (media_file_id or ""),
            (group_message_id or ""),
            ts,
        ]
        MESSAGES_WS.append_row(row)
    except Exception as e:
        log.warning("Google Sheets append error: %s", e)


# ----------------------- HELPERS -----------------------

async def _send_to_group_safely(text: str = "", photo_id: str | None = None,
                                document_id: str | None = None, caption: str | None = None) -> int | None:
    """
    Надсилає повідомлення у групу. Якщо групу апгрейдили до супер-групи —
    перехоплюємо TelegramMigrateToChat і надсилаємо вже в новий chat_id.
    Повертає message_id відправленого повідомлення або None.
    """
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
        # Оновлюємо group id і пробуємо ще раз
        new_id = e.params.migrate_to_chat_id
        log.warning("Group migrated to supergroup: %s -> %s", GROUP_CHAT_ID, new_id)
        GROUP_CHAT_ID = new_id
        if photo_id:
            sent = await bot.send_photo(GROUP_CHAT_ID, photo_id, caption=caption or text)
        elif document_id:
            sent = await bot.send_document(GROUP_CHAT_ID, document_id, caption=caption or text)
        else:
            sent = await bot.send_message(GROUP_CHAT_ID, text)
        return sent.message_id


def _fmt_user(user) -> tuple[int, str, str | None]:
    uid = user.id
    fullname = user.full_name
    uname = f"@{user.username}" if user.username else None
    return uid, fullname, uname


def _fmt_header(fullname: str, username: str | None, user_id: int) -> str:
    uname = username or "—"
    return (
        "<b>Тестування штучного інтелекту в застосунку TOTIS</b>\n"
        "<b>Нове повідомлення</b>\n"
        f"👤 {fullname} ({uname})\n"
        f"🆔 <code>{user_id}</code>\n\n"
    )


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ----------------------- COMMANDS -----------------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Вітання + реєстрація сабскрайбера."""
    if message.chat.type != ChatType.PRIVATE:
        return

    me = await bot.me()
    # Ігноруємо власні повідомлення бота, якщо хтось додав у групу і т.д.
    if message.from_user and message.from_user.id == me.id:
        return

    uid, fullname, uname = _fmt_user(message.from_user)
    _add_subscriber_if_new(uid, uname, fullname)

    await message.answer(
        "Привіт! Надішліть текст/фото/файл — я переадресую його адміністраторам.\n"
        "Команди:\n"
        "• /stats — статистика (адміни)\n"
        "• /export — лінк на Google Sheet (адміни)\n"
        "• /broadcast_all <текст> — розсилка всім (адміни)\n"
        "• /broadcast_phone — попросити клієнтів написати номер (адміни)\n"
        "• /broadcast_to <user_id> <текст> — персонально (адміни)"
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(message.from_user.id):
        return

    try:
        total = max(0, len(MESSAGES_WS.get_all_values()) - 1)  # мінус хедер
    except Exception as e:
        log.warning("Stats read error: %s", e)
        total = -1
    await message.answer(f"📊 Всього отримано повідомлень: <b>{total}</b>")


@dp.message(Command("export"))
async def cmd_export(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(message.from_user.id):
        return
    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
    await message.answer(f"🔗 Google Sheets: {url}")


@dp.message(Command("broadcast_all"))
async def cmd_broadcast_all(message: Message, command: CommandObject):
    if message.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(message.from_user.id):
        return

    text = (command.args or "").strip()
    if not text:
        await message.answer("Приклад: <code>/broadcast_all Текст для всіх</code>")
        return

    sent_ok = 0
    sent_fail = 0
    for uid in list(_subscribers):
        try:
            await bot.send_message(uid, text)
            sent_ok += 1
        except Exception as e:
            log.warning("Broadcast to %s failed: %s", uid, e)
            sent_fail += 1
    await message.answer(f"✅ Розсилка завершена: успішно {sent_ok}, помилок {sent_fail}.")


@dp.message(Command("broadcast_phone"))
async def cmd_broadcast_phone(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(message.from_user.id):
        return

    txt = (
        "Доброго дня! 🙌\n\n"
        "Будь ласка, напишіть у відповідь <b>номер телефону</b>, на який ви зареєстровані в застосунку "
        "<b>TOTIS з ФАРМА</b>. Ми закріпимо його за вашим акаунтом для підтримки та сервісу.\n\n"
        "Формат: <code>+380...</code> (або інший міжнародний формат)"
    )
    sent_ok = 0
    sent_fail = 0
    for uid in list(_subscribers):
        try:
            await bot.send_message(uid, txt)
            sent_ok += 1
        except Exception as e:
            log.warning("Broadcast(phone) to %s failed: %s", uid, e)
            sent_fail += 1
    await message.answer(f"📣 Запит номерів відправлено: успішно {sent_ok}, помилок {sent_fail}.")


@dp.message(Command("broadcast_to"))
async def cmd_broadcast_to(message: Message, command: CommandObject):
    if message.chat.type != ChatType.PRIVATE:
        return
    if not _is_admin(message.from_user.id):
        return

    args = (command.args or "").strip()
    # формат: /broadcast_to <user_id> <текст>
    if not args or " " not in args:
        await message.answer("Приклад: <code>/broadcast_to 406786709 Привіт!</code>")
        return

    uid_str, text = args.split(" ", 1)
    try:
        target_id = int(uid_str)
    except ValueError:
        await message.answer("user_id має бути числом.")
        return

    try:
        await bot.send_message(target_id, text)
        await message.answer("✅ Надіслано.")
    except Exception as e:
        await message.answer(f"❌ Помилка надсилання: {e}")


# ----------------------- MESSAGE HANDLERS -----------------------

@dp.message(F.chat.type == ChatType.PRIVATE, F.text)
async def on_text(message: Message):
    """ТЕКСТ у приваті → у групу + лог у Sheets."""
    me = await bot.me()
    if message.from_user and message.from_user.id == me.id:
        return

    uid, fullname, uname = _fmt_user(message.from_user)
    _add_subscriber_if_new(uid, uname, fullname)

    header = _fmt_header(fullname, uname, uid)
    body = message.text or "(без тексту)"
    gid = await _send_to_group_safely(text=f"{header}{body}")

    _append_message_row(
        user_id=uid,
        fullname=fullname,
        username=uname,
        message_type="text",
        message_text=body,
        media_file_id=None,
        group_message_id=gid,
    )


@dp.message(F.chat.type == ChatType.PRIVATE, F.photo)
async def on_photo(message: Message):
    """ФОТО (з/без підпису) → у групу + лог."""
    me = await bot.me()
    if message.from_user and message.from_user.id == me.id:
        return

    uid, fullname, uname = _fmt_user(message.from_user)
    _add_subscriber_if_new(uid, uname, fullname)

    header = _fmt_header(fullname, uname, uid)
    caption = message.caption or ""
    photo = message.photo[-1]  # найбільша
    file_id = photo.file_id

    gid = await _send_to_group_safely(photo_id=file_id, caption=f"{header}{caption}".strip())

    _append_message_row(
        user_id=uid,
        fullname=fullname,
        username=uname,
        message_type="photo",
        message_text=caption,
        media_file_id=file_id,
        group_message_id=gid,
    )


@dp.message(F.chat.type == ChatType.PRIVATE, F.document)
async def on_document(message: Message):
    """ДОКУМЕНТ (файл) → у групу + лог. Працює з підписом."""
    me = await bot.me()
    if message.from_user and message.from_user.id == me.id:
        return

    uid, fullname, uname = _fmt_user(message.from_user)
    _add_subscriber_if_new(uid, uname, fullname)

    header = _fmt_header(fullname, uname, uid)
    caption = message.caption or ""
    file_id = message.document.file_id

    gid = await _send_to_group_safely(document_id=file_id, caption=f"{header}{caption}".strip())

    _append_message_row(
        user_id=uid,
        fullname=fullname,
        username=uname,
        message_type="document",
        message_text=caption,
        media_file_id=file_id,
        group_message_id=gid,
    )


@dp.message(F.chat.type == ChatType.PRIVATE, F.photo == None, F.document == None, F.content_type.in_({"video", "voice", "audio", "video_note"}))
async def on_other_media(message: Message):
    """Інші типи — просто текстом в групу + лог (ід файлу теж пишемо)."""
    me = await bot.me()
    if message.from_user and message.from_user.id == me.id:
        return

    uid, fullname, uname = _fmt_user(message.from_user)
    _add_subscriber_if_new(uid, uname, fullname)

    header = _fmt_header(fullname, uname, uid)
    media_type = message.content_type
    caption = getattr(message, "caption", None) or ""
    # file_id
    file_id = None
    try:
        obj = getattr(message, media_type)
        file_id = getattr(obj, "file_id", None)
    except Exception:
        pass

    # Відправляємо як текст: інструктивний префікс
    text = f"{header}[{media_type}] {caption}".strip()
    gid = await _send_to_group_safely(text=text)

    _append_message_row(
        user_id=uid,
        fullname=fullname,
        username=uname,
        message_type=media_type,
        message_text=caption,
        media_file_id=file_id,
        group_message_id=gid,
    )


# ----------------------- STARTUP -----------------------

async def on_startup():
    _load_subscribers_into_cache()
    me = await bot.me()
    log.info("Bot started as @%s (%s)", me.username, me.id)


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
