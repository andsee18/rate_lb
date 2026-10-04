import asyncio
import contextlib
import hashlib
import hmac
import io
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ChatMemberUpdated, Message
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.config import Settings, load_settings
from backend.db import Database
from backend.rating import get_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rate-lb")
settings: Settings = load_settings()
database = Database(settings.database_url)
bot = Bot(settings.bot_token)
dispatcher = Dispatcher(storage=MemoryStorage())
router = Router()


class RateRequest(BaseModel):
    telegram_id: int
    score: float = Field(ge=0, le=8)
    nickname: str | None = Field(default=None, max_length=80)


class AddAvatarState(StatesGroup):
    waiting_for_photo = State()


def user_payload(row: dict[str, Any]) -> dict[str, Any]:
    score = row["score"]
    status = get_status(score) if score is not None else None
    updated_at = row["updated_at"]
    cache_version = (
        updated_at.isoformat()
        if hasattr(updated_at, "isoformat")
        else str(updated_at).replace(" ", "T")
    )
    return {
        "id": row["telegram_id"],
        "telegram_id": row["telegram_id"],
        "username": row["username"],
        "name": row["first_name"],
        "nickname": row["nickname"],
        "score": round(score, 2) if score is not None else None,
        "status": status.label if status else "Пока не оценён",
        "status_class": status.class_name if status else "status-unrated",
        "avatar_url": (
            f"{settings.public_api_url}/api/avatar/{row['telegram_id']}"
            f"?v={cache_version}"
        ) if row["avatar_file_id"] else None,
        "details": None,
    }


def telegram_webapp_user(init_data: str) -> dict[str, Any]:
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing Telegram init data hash")
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        settings.bot_token.encode(),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")
    try:
        user = json.loads(fields["user"])
    except (KeyError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=401, detail="Invalid Telegram user data") from error
    if not user.get("id") or not user.get("first_name"):
        raise HTTPException(status_code=401, detail="Incomplete Telegram user data")
    return user


async def save_telegram_user(user: Any) -> None:
    avatar_file_id = None
    try:
        for attempt in range(1, 4):
            photos = await bot.get_user_profile_photos(user.id, limit=1)
            logger.info(
                "Profile photos for Telegram user %s: total_count=%s attempt=%s",
                user.id,
                photos.total_count,
                attempt,
            )
            if photos.total_count and photos.photos:
                avatar_file_id = photos.photos[0][-1].file_id
                logger.info(
                    "Saved profile photo for Telegram user %s: %s",
                    user.id,
                    avatar_file_id,
                )
                break
            if attempt < 3:
                await asyncio.sleep(0.4)
        if avatar_file_id is None:
            logger.warning("Telegram API returned no profile photo for user %s", user.id)
    except Exception:
        logger.exception("Could not load avatar for Telegram user %s", user.id)
    database.upsert_user(
        user.id,
        user.username,
        user.first_name,
        avatar_file_id,
        user.is_bot,
    )


async def is_group_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    if user_id in settings.admin_ids:
        return True
    member = await bot.get_chat_member(settings.group_chat_id, user_id)
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


async def scan_known_users() -> None:
    administrators = await bot.get_chat_administrators(settings.group_chat_id)
    known_users = {member.user.id: member.user for member in administrators}
    for row in database.list_users():
        telegram_id = row["telegram_id"]
        if telegram_id in known_users:
            continue
        try:
            member = await bot.get_chat_member(settings.group_chat_id, telegram_id)
            if member.status not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                known_users[telegram_id] = member.user
        except Exception:
            logger.exception("Could not refresh group member %s", telegram_id)
    for user in known_users.values():
        await save_telegram_user(user)


@router.message(
    Command("start"),
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def start_command(message: Message) -> None:
    await message.answer(help_text("Привет! Я бот Rate LB."))


def help_text(prefix: str = "") -> str:
    return (
        f"{prefix}\n\n"
        "Доступные команды:\n"
        "/start — запустить бота\n"
        "/help — показать эту справку\n"
        "/scan — обновить участников и аватарки\n"
        "/rate @username 3.90 Кличка — выставить оценку\n"
        "/unrate @username — снять оценку и вернуть в «Пока не оценён»\n"
        "/add nickname @username — добавить или изменить кличку\n"
        "/add image @username — заменить аватарку (затем отправить фото)\n"
        "/cancel — отменить ожидание фотографии"
    )


@router.message(
    Command("help", ignore_case=True),
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def help_command(message: Message) -> None:
    await message.answer(help_text())


@router.message(F.chat.id == settings.group_chat_id, F.new_chat_members)
async def on_new_members(message: Message) -> None:
    for user in message.new_chat_members:
        await save_telegram_user(user)
    await message.answer("Новые участники добавлены в Rate LB.")


@router.chat_member(F.chat.id == settings.group_chat_id)
async def on_chat_member(update: ChatMemberUpdated) -> None:
    user = update.new_chat_member.user
    if update.new_chat_member.status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }:
        await save_telegram_user(user)
    elif update.new_chat_member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        database.mark_inactive(user.id)


@router.message(
    Command("scan", ignore_case=True),
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def scan_command(message: Message) -> None:
    logger.info(
        "Received /scan in chat_id=%s chat_type=%s from_user=%s sender_chat=%s",
        message.chat.id,
        message.chat.type,
        message.from_user.id if message.from_user else None,
        message.sender_chat.id if message.sender_chat else None,
    )
    is_private_scan = message.chat.type == ChatType.PRIVATE
    if is_private_scan:
        if not message.from_user or message.from_user.id not in settings.admin_ids:
            await message.answer("Личное сканирование доступно только владельцу Rate LB.")
            return
        try:
            member = await bot.get_chat_member(settings.group_chat_id, message.from_user.id)
            if member.status not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                await save_telegram_user(member.user)
        except Exception:
            logger.exception(
                "Could not add private scan author %s to group users",
                message.from_user.id if message.from_user else None,
            )
    elif message.chat.id != settings.group_chat_id:
        await message.answer(
            "Эта команда не разрешена в данной группе.\n\n"
            f"Фактический GROUP_CHAT_ID этой группы: `{message.chat.id}`\n"
            "Укажи это значение в Render → Environment → GROUP_CHAT_ID "
            "и перезапусти сервис."
        )
        logger.warning(
            "Ignored /scan because chat_id=%s differs from configured GROUP_CHAT_ID=%s",
            message.chat.id,
            settings.group_chat_id,
        )
        return
    anonymous_admin = (
        not is_private_scan
        and message.from_user is None
        and message.sender_chat is not None
        and message.sender_chat.id == message.chat.id
    )
    if not is_private_scan and not anonymous_admin and not await is_group_admin(
        message.from_user.id if message.from_user else None
    ):
        await message.answer("Команда доступна только администраторам.")
        return
    if message.from_user and not message.from_user.is_bot:
        await save_telegram_user(message.from_user)
    await message.answer("Сканирование запущено!")
    await scan_known_users()
    await message.answer(
        "Сканирование завершено. Telegram Bot API не отдаёт полный список участников "
        "группы, поэтому остальные пользователи появятся при вступлении или активности."
    )


@router.message(
    Command("rate", ignore_case=True),
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def rate_command(message: Message, command: CommandObject) -> None:
    is_private_rate = message.chat.type == ChatType.PRIVATE
    if is_private_rate:
        authorized = bool(
            message.from_user and message.from_user.id in settings.admin_ids
        )
    elif message.chat.id == settings.group_chat_id:
        authorized = bool(
            message.from_user
            and await is_group_admin(message.from_user.id)
        )
    else:
        authorized = False
    if not authorized:
        await message.answer("Оценивать участников могут только администраторы.")
        return
    args = (command.args or "").strip()
    match = re.match(
        r"^@?([A-Za-z0-9_]{5,32})\s+([0-9]+(?:[.,][0-9]+)?)(?:\s+(.+))?$",
        args,
    )
    if not match:
        await message.answer("Формат: /rate @username 3.90 Саня Машина")
        return
    username, raw_score, nickname = match.groups()
    score = float(raw_score.replace(",", "."))
    if not 0 <= score <= 8:
        await message.answer("Оценка должна быть от 0.00 до 8.00.")
        return
    user = database.find_user_by_username(username)
    if not user:
        await message.answer(
            f"Пользователь @{username} не найден в базе. "
            "Попроси его написать сообщение в группе и повтори команду."
        )
        return
    if not database.rate_user(user["telegram_id"], score, nickname):
        await message.answer("Не удалось сохранить оценку пользователя.")
        return
    await message.answer(f"{user['first_name']} получил оценку {score:.2f} ({get_status(score).label}).")


@router.message(
    Command("unrate", ignore_case=True),
    F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def unrate_command(message: Message, command: CommandObject) -> None:
    is_private_unrate = message.chat.type == ChatType.PRIVATE
    if is_private_unrate:
        authorized = bool(
            message.from_user and message.from_user.id in settings.admin_ids
        )
    elif message.chat.id == settings.group_chat_id:
        authorized = bool(
            message.from_user
            and await is_group_admin(message.from_user.id)
        )
    else:
        authorized = False
    if not authorized:
        await message.answer("Снимать оценку могут только администраторы.")
        return

    username = (command.args or "").strip()
    if not re.fullmatch(r"@?[A-Za-z0-9_]{5,32}", username):
        await message.answer("Формат: /unrate @username")
        return
    username = username.lstrip("@")
    user = database.find_user_by_username(username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден в базе.")
        return
    if not database.unrate_user(user["telegram_id"]):
        await message.answer("Не удалось снять оценку пользователя.")
        return
    await message.answer(
        f"Оценка {user['first_name']} снята. "
        "Пользователь возвращён в список «Пока не оценён»."
    )


@router.message(
    Command("add", ignore_case=True),
    F.chat.type == ChatType.PRIVATE,
)
async def add_command(
    message: Message,
    command: CommandObject,
    state: FSMContext,
) -> None:
    if not message.from_user or message.from_user.id not in settings.admin_ids:
        await message.answer("Изменять данные могут только администраторы.")
        return
    args = (command.args or "").strip()
    image_match = re.fullmatch(
        r"image\s+@?([A-Za-z0-9_]{5,32})",
        args,
        re.IGNORECASE,
    )
    nickname_match = re.fullmatch(r"(.+?)\s+@?([A-Za-z0-9_]{5,32})", args)
    if image_match:
        username = image_match.group(1)
    elif nickname_match and nickname_match.group(1).strip().lower() != "image":
        nickname, username = nickname_match.group(1).strip(), nickname_match.group(2)
        if len(nickname) > 80:
            await message.answer("Кличка не должна быть длиннее 80 символов.")
            return
        user = database.find_user_by_username(username)
        if not user:
            await message.answer(f"Пользователь @{username} не найден в базе.")
            return
        if not database.set_nickname(user["telegram_id"], nickname):
            await message.answer("Не удалось сохранить кличку.")
            return
        await message.answer(
            f"Кличка «{nickname}» сохранена для {user['first_name']}. "
            "Оценка и вкладка пользователя не изменены."
        )
        return
    else:
        await message.answer(
            "Форматы:\n"
            "/add nickname @username\n"
            "/add image @username"
        )
        return

    user = database.find_user_by_username(username)
    if not user:
        await message.answer(
            f"Пользователь @{username} не найден в базе. "
            "Сначала синхронизируй его через сообщение в группе."
        )
        return
    await state.set_state(AddAvatarState.waiting_for_photo)
    await state.update_data(target_telegram_id=user["telegram_id"])
    await message.answer(
        f"Пришлите фотографию для @{user['username'] or username}. "
        "Отправьте её следующим сообщением."
    )


@router.message(
    AddAvatarState.waiting_for_photo,
    F.chat.type == ChatType.PRIVATE,
    F.photo,
)
async def save_manual_avatar(
    message: Message,
    state: FSMContext,
) -> None:
    if not message.from_user or message.from_user.id not in settings.admin_ids:
        await state.clear()
        await message.answer("Добавлять аватарки могут только администраторы.")
        return
    data = await state.get_data()
    telegram_id = data.get("target_telegram_id")
    if not telegram_id:
        await state.clear()
        await message.answer("Сессия добавления аватарки истекла. Повторите /add image @username.")
        return
    photo_file_id = message.photo[-1].file_id
    database.update_avatar(telegram_id, photo_file_id)
    await state.clear()
    await message.answer("Аватарка сохранена. Она появится в Mini App после обновления списка.")


@router.message(
    AddAvatarState.waiting_for_photo,
    F.chat.type == ChatType.PRIVATE,
    ~F.photo,
)
async def reject_manual_avatar_input(message: Message) -> None:
    if message.from_user and message.from_user.id in settings.admin_ids:
        await message.answer("Ожидаю фотографию. Для отмены отправьте /cancel.")


@router.message(Command("cancel", ignore_case=True), F.chat.type == ChatType.PRIVATE)
async def cancel_avatar_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Добавление аватарки отменено.")


@router.message(
    F.chat.id == settings.group_chat_id,
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.from_user,
)
async def track_group_message(message: Message) -> None:
    if message.from_user.is_bot:
        return
    await save_telegram_user(message.from_user)


# Register every handler before polling starts. Keeping this close to startup makes it
# explicit that no update can arrive before the router is attached to the dispatcher.
dispatcher.include_router(router)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot.delete_webhook(drop_pending_updates=True)

    async def run_polling() -> None:
        try:
            await dispatcher.start_polling(bot, handle_signals=False)
        except TelegramConflictError:
            logger.error(
                "Telegram polling conflict: another process is using this bot token. "
                "Stop the other instance and run exactly one polling worker."
            )

    polling_task = asyncio.create_task(run_polling())
    try:
        yield
    finally:
        if not polling_task.done():
            await dispatcher.stop_polling()
        with contextlib.suppress(asyncio.CancelledError):
            await polling_task
        await bot.session.close()


app = FastAPI(title="Rate LB API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.webapp_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/users/me")
async def sync_mini_app_user(
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram WebApp data required")
    user = telegram_webapp_user(x_telegram_init_data)
    await save_telegram_user(
        type(
            "TelegramUser",
            (),
            {
                "id": user["id"],
                "username": user.get("username"),
                "first_name": user["first_name"],
                "is_bot": user.get("is_bot", False),
            },
        )()
    )
    row = database.get_user(user["id"])
    return user_payload(database.row_to_dict(row))


@app.get("/api/users")
def users() -> dict[str, list[dict[str, Any]]]:
    rated: list[dict[str, Any]] = []
    unrated: list[dict[str, Any]] = []
    for raw_row in database.list_users():
        if raw_row["is_bot"]:
            continue
        payload = user_payload(database.row_to_dict(raw_row))
        (unrated if payload["score"] is None else rated).append(payload)
    rated.sort(key=lambda item: item["score"], reverse=True)
    return {"rated": rated, "unrated": unrated}


@app.post("/api/rate")
async def rate_user(
    request: RateRequest,
    x_admin_id: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        admin_id = int(x_admin_id or "")
    except ValueError:
        admin_id = 0
    if admin_id not in settings.admin_ids:
        raise HTTPException(status_code=403, detail="Admin access required")
    if not database.rate_user(request.telegram_id, request.score, request.nickname):
        raise HTTPException(status_code=404, detail="User not found")
    row = database.get_user(request.telegram_id)
    return user_payload(database.row_to_dict(row))


@app.get("/api/avatar/{telegram_id}")
async def avatar(telegram_id: int) -> Response:
    row = database.get_user(telegram_id)
    if not row or not row["avatar_file_id"]:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    file = await bot.get_file(row["avatar_file_id"])
    content = io.BytesIO()
    await bot.download_file(file.file_path, destination=content)
    if not content.getvalue():
        raise HTTPException(status_code=502, detail="Telegram returned an empty avatar")
    return Response(
        content=content.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
