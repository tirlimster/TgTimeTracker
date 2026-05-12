import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from dotenv import load_dotenv

dp = Dispatcher()

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
PRESENT_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
ABSENT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}

TRACKING_TITLE = "Time tracked"


@dataclass
class ChatState:
    message_id: int | None = None
    members: dict[int, tuple[str, int]] = field(default_factory=dict)


chat_states: dict[int, ChatState] = {}


class StartTracking(CallbackData, prefix="tstart"):
    pass


class StopTracking(CallbackData, prefix="tstop"):
    user_id: int
    start_ts: int


def format_total(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours:02d}:{minutes:02d}"


def format_elapsed(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_clock(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Start tracking", callback_data=StartTracking().pack())]]
    )


def stop_keyboard(user_id: int, start_ts: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Stop",
                    callback_data=StopTracking(user_id=user_id, start_ts=start_ts).pack(),
                )
            ]
        ]
    )


def render_tracking_message(state: ChatState) -> str:
    lines = [f"{TRACKING_TITLE}:"]
    for name, seconds in state.members.values():
        lines.append(f"{name}: {format_total(seconds)}")
    return "\n".join(lines)


@dp.my_chat_member()
async def handle_added_to_group(event: ChatMemberUpdated) -> None:
    if event.chat.type not in GROUP_CHAT_TYPES:
        return
    if event.old_chat_member.status not in ABSENT_STATUSES:
        return
    if event.new_chat_member.status not in PRESENT_STATUSES:
        return

    state = ChatState()
    if event.from_user and not event.from_user.is_bot:
        state.members[event.from_user.id] = (event.from_user.full_name, 0)

    sent = await event.bot.send_message(
        event.chat.id,
        render_tracking_message(state),
        reply_markup=start_keyboard(),
    )
    state.message_id = sent.message_id
    chat_states[event.chat.id] = state


@dp.callback_query(StartTracking.filter())
async def on_start_tracking(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    user = callback.from_user
    start_ts = int(time.time())
    await callback.message.answer(
        f"User {user.full_name} is working",
        reply_markup=stop_keyboard(user.id, start_ts),
    )
    await callback.answer()


@dp.callback_query(StopTracking.filter())
async def on_stop_tracking(callback: CallbackQuery, callback_data: StopTracking) -> None:
    if callback.message is None:
        return
    if callback.from_user.id != callback_data.user_id:
        await callback.answer("Only the person who started can stop.", show_alert=True)
        return

    end_ts = int(time.time())
    elapsed = end_ts - callback_data.start_ts
    name = callback.from_user.full_name

    start_formatted = format_clock(callback_data.start_ts)
    end_formatted = format_clock(end_ts)
    elapsed_formatted = format_elapsed(elapsed)
    log_text = f"{name}: {start_formatted} - {end_formatted} ({elapsed_formatted})"

    await callback.message.edit_text(log_text)
    await callback.answer()

    await update_total(callback.bot, callback.message.chat.id, callback.from_user.id, name, elapsed)


async def update_total(bot: Bot, chat_id: int, user_id: int, name: str, elapsed: int) -> None:
    state = chat_states.get(chat_id)
    if state is None:
        logging.warning(f"No state for chat {chat_id}")
        return

    _, prev_seconds = state.members.get(user_id, (name, 0))
    prev_formatted = format_total(prev_seconds)
    new_seconds = prev_seconds + elapsed
    new_formatted = format_total(new_seconds)

    state.members[user_id] = (name, new_seconds)

    if state.message_id is None or new_formatted == prev_formatted:
        return

    await bot.edit_message_text(
        render_tracking_message(state),
        chat_id=chat_id,
        message_id=state.message_id,
        reply_markup=start_keyboard(),
    )


async def main() -> None:
    load_dotenv()
    token = os.environ["TG_TOKEN"]
    bot = Bot(token=token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
