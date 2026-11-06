"""
Telegram-бот, который присылает вам удалённые сообщения из ваших чатов.

Работает через официальную функцию Telegram Business: вы сами подключаете
бота к СВОЕМУ аккаунту (нужен Telegram Premium). После этого Telegram шлёт
боту события о сообщениях в ваших чатах, включая событие об удалении.

Бот логирует входящие/исходящие сообщения и, когда сообщение удаляют,
присылает вам сохранённую копию в личку.

Подробности и настройка — см. README.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from html import escape
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    Chat,
    Message,
)
from dotenv import load_dotenv

from storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("deleted-messages-bot")

router = Router()
storage: Optional[Storage] = None


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
def chat_title(chat: Chat) -> str:
    return chat.title or chat.full_name or (f"id{chat.id}")


def sender_name(message: Message) -> str:
    if message.from_user:
        name = message.from_user.full_name
        if message.from_user.username:
            name += f" (@{message.from_user.username})"
        return name
    if message.sender_chat:
        return message.sender_chat.title or f"chat {message.sender_chat.id}"
    return "неизвестно"


def extract_media(message: Message) -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (тип, file_id) для медиа в сообщении, либо (None, None)."""
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.animation:  # проверяем раньше document — у анимации тоже есть document
        return "animation", message.animation.file_id
    if message.video:
        return "video", message.video.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.document:
        return "document", message.document.file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    return None, None


def media_label(media_type: Optional[str]) -> str:
    return {
        "photo": "🖼 Фото",
        "animation": "🎞 GIF",
        "video": "🎬 Видео",
        "video_note": "⭕️ Видеосообщение",
        "voice": "🎤 Голосовое",
        "audio": "🎵 Аудио",
        "document": "📎 Документ",
        "sticker": "🩵 Стикер",
    }.get(media_type or "", "")


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")


async def resend_media(
    bot: Bot, chat_id: int, media_type: str, file_id: str, caption: str
) -> bool:
    """Пробует переслать сохранённое медиа. True — если удалось."""
    try:
        if media_type == "photo":
            await bot.send_photo(chat_id, file_id, caption=caption)
        elif media_type == "animation":
            await bot.send_animation(chat_id, file_id, caption=caption)
        elif media_type == "video":
            await bot.send_video(chat_id, file_id, caption=caption)
        elif media_type == "video_note":
            await bot.send_message(chat_id, caption)
            await bot.send_video_note(chat_id, file_id)
        elif media_type == "voice":
            await bot.send_voice(chat_id, file_id, caption=caption)
        elif media_type == "audio":
            await bot.send_audio(chat_id, file_id, caption=caption)
        elif media_type == "document":
            await bot.send_document(chat_id, file_id, caption=caption)
        elif media_type == "sticker":
            await bot.send_message(chat_id, caption)
            await bot.send_sticker(chat_id, file_id)
        else:
            return False
        return True
    except Exception as exc:  # медиа могли удалить с серверов Telegram
        logger.warning("Не удалось переслать медиа (%s): %s", media_type, exc)
        return False


# --------------------------------------------------------------------------- #
# Обработчики команд (личный чат с ботом)
# --------------------------------------------------------------------------- #
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Я сохраняю сообщения из ваших чатов и присылаю их обратно, "
        "если собеседник их удалит.\n\n"
        "Чтобы я заработал, подключите меня к своему аккаунту:\n"
        "<b>Настройки → Telegram для бизнеса → Чат-боты</b> — и выберите этого бота.\n"
        "(Нужен Telegram Premium.)\n\n"
        "После подключения я буду молча логировать сообщения в ваших чатах и "
        "сообщу сюда, как только что-то удалят.\n\n"
        "Команды: /status — статистика, /help — помощь."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "ℹ️ <b>Как это работает</b>\n\n"
        "Я получаю события только по чатам <b>вашего</b> аккаунта и только после того, "
        "как вы подключите меня в разделе Telegram для бизнеса. Я не вижу чужие "
        "переписки и ничего, что происходит вне ваших чатов.\n\n"
        "Когда приходит сообщение — я сохраняю его копию. Когда его удаляют — "
        "присылаю копию вам сюда.\n\n"
        "⚠️ Я не могу восстановить сообщения, отправленные <i>до</i> подключения.\n"
        "⚠️ Если медиа удалено с серверов Telegram, пришлю хотя бы текст/подпись.\n\n"
        "Отключить: <b>Настройки → Telegram для бизнеса → Чат-боты</b> → убрать бота."
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    assert storage is not None
    # Ищем подключение, где владелец — этот пользователь.
    # Упрощённо: считаем сообщения по всем подключениям этого chat_id как владельца.
    total = 0
    conn = None
    async with storage.db.execute(
        "SELECT * FROM connections WHERE owner_user_id = ?",
        (message.from_user.id,),
    ) as cur:
        async for row in cur:
            conn = row
            total += await storage.count_messages(row["business_connection_id"])
    if conn is None:
        await message.answer(
            "❌ Вы ещё не подключили бота к своему аккаунту.\n"
            "Откройте <b>Настройки → Telegram для бизнеса → Чат-боты</b> и выберите этого бота."
        )
        return
    state = "включено ✅" if conn["is_enabled"] else "выключено ⛔️"
    await message.answer(
        f"📊 <b>Статус</b>\n\n"
        f"Подключение: {state}\n"
        f"Сообщений в логе: <b>{total}</b>"
    )


# --------------------------------------------------------------------------- #
# Бизнес-события
# --------------------------------------------------------------------------- #
@router.business_connection()
async def on_business_connection(conn: BusinessConnection, bot: Bot) -> None:
    assert storage is not None
    await storage.save_connection(
        business_connection_id=conn.id,
        owner_user_id=conn.user.id,
        user_chat_id=conn.user_chat_id,
        is_enabled=conn.is_enabled,
    )
    logger.info(
        "Business connection %s от пользователя %s (enabled=%s)",
        conn.id, conn.user.id, conn.is_enabled,
    )
    try:
        if conn.is_enabled:
            await bot.send_message(
                conn.user_chat_id,
                "✅ Бот подключён к вашему аккаунту. Теперь я буду сохранять "
                "сообщения в ваших чатах и сообщать об удалённых.",
            )
        else:
            await bot.send_message(
                conn.user_chat_id,
                "⛔️ Бот отключён от вашего аккаунта. Логирование остановлено.",
            )
    except Exception as exc:
        logger.warning("Не удалось отправить уведомление о подключении: %s", exc)


@router.business_message()
async def on_business_message(message: Message) -> None:
    assert storage is not None
    bc_id = message.business_connection_id
    if not bc_id:
        return

    conn = await storage.get_connection(bc_id)
    owner_id = conn.owner_user_id if conn else None
    is_outgoing = bool(
        message.from_user and owner_id and message.from_user.id == owner_id
    )

    media_type, file_id = extract_media(message)
    await storage.save_message(
        business_connection_id=bc_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_name(message),
        chat_title=chat_title(message.chat),
        is_outgoing=is_outgoing,
        text=message.text or message.caption,
        media_type=media_type,
        file_id=file_id,
        msg_date=int(message.date.timestamp()),
    )


@router.edited_business_message()
async def on_edited_business_message(message: Message) -> None:
    assert storage is not None
    bc_id = message.business_connection_id
    if not bc_id:
        return
    conn = await storage.get_connection(bc_id)
    owner_id = conn.owner_user_id if conn else None
    is_outgoing = bool(
        message.from_user and owner_id and message.from_user.id == owner_id
    )
    media_type, file_id = extract_media(message)
    # Сохраняем актуальную версию (с пометкой "изменено").
    await storage.save_message(
        business_connection_id=bc_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_name(message),
        chat_title=chat_title(message.chat),
        is_outgoing=is_outgoing,
        text=message.text or message.caption,
        media_type=media_type,
        file_id=file_id,
        msg_date=int(message.date.timestamp()),
        edited=True,
    )


@router.deleted_business_messages()
async def on_deleted(event: BusinessMessagesDeleted, bot: Bot) -> None:
    assert storage is not None
    conn = await storage.get_connection(event.business_connection_id)
    if conn is None:
        logger.info("Удаление по неизвестному подключению %s", event.business_connection_id)
        return

    target = conn.user_chat_id

    for mid in event.message_ids:
        stored = await storage.get_message(
            event.business_connection_id, event.chat.id, mid
        )
        if stored is None:
            try:
                await bot.send_message(
                    target,
                    "🗑 В чате <b>{}</b> удалено сообщение, но его нет в логе "
                    "(вероятно, оно пришло до подключения бота).".format(
                        escape(chat_title(event.chat))
                    ),
                )
            except Exception as exc:
                logger.warning("send_message failed: %s", exc)
            continue

        direction = "➡️ исходящее" if stored.is_outgoing else "⬅️ входящее"
        parts = [
            "🗑 <b>Удалённое сообщение</b>",
            "",
            f"💬 Чат: <b>{escape(stored.chat_title)}</b>",
            f"👤 Отправитель: {escape(stored.sender_name)}",
            f"🧭 Направление: {direction}",
            f"🕒 Отправлено: {fmt_date(stored.msg_date)}",
        ]
        if stored.edited:
            parts.append("✏️ (сообщение было изменено)")
        if stored.media_type:
            parts.append(f"📁 Вложение: {media_label(stored.media_type)}")
        parts.append("")
        parts.append("📝 Текст:")
        parts.append(escape(stored.text) if stored.text else "<i>(без текста)</i>")
        caption = "\n".join(parts)

        try:
            sent = False
            if stored.media_type and stored.file_id:
                sent = await resend_media(
                    bot, target, stored.media_type, stored.file_id, caption
                )
            if not sent:
                await bot.send_message(target, caption)
        except Exception as exc:
            logger.warning("Не удалось отправить уведомление об удалении: %s", exc)


# --------------------------------------------------------------------------- #
# Фоновая очистка старых логов
# --------------------------------------------------------------------------- #
async def retention_loop(retention_days: int) -> None:
    assert storage is not None
    if retention_days <= 0:
        return
    while True:
        try:
            removed = await storage.purge_older_than(retention_days)
            if removed:
                logger.info("Очистка логов: удалено %s старых записей", removed)
        except Exception as exc:
            logger.warning("Ошибка очистки логов: %s", exc)
        await asyncio.sleep(6 * 3600)


# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #
async def main() -> None:
    global storage

    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен от @BotFather."
        )
    db_path = os.getenv("DB_PATH", "messages.db")
    retention_days = int(os.getenv("RETENTION_DAYS", "30"))

    storage = Storage(db_path)
    await storage.connect()

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    asyncio.create_task(retention_loop(retention_days))

    logger.info("Бот запущен. Ожидаю события…")
    try:
        await dp.start_polling(
            bot, allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        await storage.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
