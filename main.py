import asyncio
import logging
import os
import time
from datetime import datetime
from pathlib import Path

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
from pydantic import BaseModel, Field, TypeAdapter

dp = Dispatcher()

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
PRESENT_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
ABSENT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}

TRACKING_TITLE = "Time tracked"

STATE_FILE = Path("data/chat_states.json")


class MemberState(BaseModel):
    name: str
    total_seconds: int
    last_end_ts: int | None = None


class ChatState(BaseModel):
    message_id: int | None = None
    members: dict[int, MemberState] = Field(default_factory=dict)


chat_states: dict[int, ChatState] = {}
active_sessions: dict[int, set[int]] = {}

_states_adapter: TypeAdapter[dict[int, ChatState]] = TypeAdapter(dict[int, ChatState])


def save_states() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_bytes(_states_adapter.dump_json(chat_states, indent=2))
    tmp.replace(STATE_FILE)


def load_states() -> None:
    if not STATE_FILE.exists():
        return
    chat_states.update(_states_adapter.validate_json(STATE_FILE.read_bytes()))


class StartTracking(CallbackData, prefix="tstart"):
    pass


class StopTracking(CallbackData, prefix="tstop"):
    user_id: int
    start_ts: int


class MenuAction(CallbackData, prefix="m"):
    action: str
    user_id: int
    start_ts: int
    end_ts: int


class StepAction(CallbackData, prefix="s"):
    target: str
    delta: int
    user_id: int
    start_ts: int
    end_ts: int


class StepperSubmit(CallbackData, prefix="sd"):
    target: str
    user_id: int
    start_ts: int
    end_ts: int


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


def working_text(name: str, start_ts: int) -> str:
    return f"User {name} is working since {format_clock(start_ts)}"


def log_text(name: str, start_ts: int, end_ts: int) -> str:
    return (
        f"{name}: {format_clock(start_ts)} - {format_clock(end_ts)} "
        f"({format_elapsed(end_ts - start_ts)})"
    )


def stepper_text(name: str, start_ts: int, end_ts: int, target: str) -> str:
    header = "Editing start" if target == "s" else "Editing end"
    return f"{header}\n{log_text(name, start_ts, end_ts)}"


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


def edit_menu_kb(user_id: int, start_ts: int, end_ts: int) -> InlineKeyboardMarkup:
    def btn(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label,
            callback_data=MenuAction(
                action=action, user_id=user_id, start_ts=start_ts, end_ts=end_ts
            ).pack(),
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Edit start", "es"), btn("Edit end", "ee")],
            [btn("Submit", "su"), btn("Cancel", "ca")],
        ]
    )


def stepper_kb(target: str, user_id: int, start_ts: int, end_ts: int) -> InlineKeyboardMarkup:
    def step(label: str, delta: int) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label,
            callback_data=StepAction(
                target=target, delta=delta, user_id=user_id, start_ts=start_ts, end_ts=end_ts
            ).pack(),
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [step("-1h", -3600), step("-10m", -600), step("-1m", -60)],
            [step("+1h", 3600), step("+10m", 600), step("+1m", 60)],
            [
                InlineKeyboardButton(
                    text="Submit",
                    callback_data=StepperSubmit(
                        target=target, user_id=user_id, start_ts=start_ts, end_ts=end_ts
                    ).pack(),
                )
            ],
        ]
    )


def render_tracking_message(state: ChatState) -> str:
    lines = [f"{TRACKING_TITLE}:"]
    for member in state.members.values():
        lines.append(f"{member.name}: {format_total(member.total_seconds)}")
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

    sent = await event.bot.send_message(
        event.chat.id,
        render_tracking_message(state),
        reply_markup=start_keyboard(),
    )
    state.message_id = sent.message_id
    chat_states[event.chat.id] = state
    save_states()


@dp.callback_query(StartTracking.filter())
async def on_start_tracking(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    user = callback.from_user
    chat_id = callback.message.chat.id
    if user.id in active_sessions.get(chat_id, set()):
        await callback.answer(
            "You already have an ongoing tracking. End it first.", show_alert=True
        )
        return
    start_ts = int(time.time())
    active_sessions.setdefault(chat_id, set()).add(user.id)
    await callback.message.answer(
        working_text(user.full_name, start_ts),
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
    name = callback.from_user.full_name

    await callback.message.edit_text(
        log_text(name, callback_data.start_ts, end_ts),
        reply_markup=edit_menu_kb(callback.from_user.id, callback_data.start_ts, end_ts),
    )
    await callback.answer()


@dp.callback_query(MenuAction.filter())
async def on_menu_action(callback: CallbackQuery, callback_data: MenuAction) -> None:
    if callback.message is None:
        return
    if callback.from_user.id != callback_data.user_id:
        await callback.answer("Only the owner can use these buttons.", show_alert=True)
        return

    name = callback.from_user.full_name
    uid = callback.from_user.id
    s, e = callback_data.start_ts, callback_data.end_ts
    chat_id = callback.message.chat.id
    msg_id = callback.message.message_id

    if callback_data.action == "es":
        await callback.message.edit_text(
            stepper_text(name, s, e, "s"),
            reply_markup=stepper_kb("s", uid, s, e),
        )
    elif callback_data.action == "ee":
        await callback.message.edit_text(
            stepper_text(name, s, e, "e"),
            reply_markup=stepper_kb("e", uid, s, e),
        )
    elif callback_data.action == "su":
        await callback.message.edit_text(log_text(name, s, e))
        await update_total(callback.bot, chat_id, uid, name, s, e)
        active_sessions.get(chat_id, set()).discard(uid)
    elif callback_data.action == "ca":
        await callback.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        active_sessions.get(chat_id, set()).discard(uid)

    await callback.answer()


@dp.callback_query(StepAction.filter())
async def on_step(callback: CallbackQuery, callback_data: StepAction) -> None:
    if callback.message is None:
        return
    if callback.from_user.id != callback_data.user_id:
        await callback.answer("Only the owner can use these buttons.", show_alert=True)
        return

    s, e = callback_data.start_ts, callback_data.end_ts
    now = int(time.time())
    target = callback_data.target
    clamp_msg: str | None = None

    if target == "s":
        new_s = s + callback_data.delta
        chat_state = chat_states.get(callback.message.chat.id)
        member = chat_state.members.get(callback.from_user.id) if chat_state else None
        last_end = member.last_end_ts if member else None
        if last_end is not None and new_s < last_end:
            new_s = last_end
            clamp_msg = (
                f"Can't go earlier than your previous session ended "
                f"({format_clock(last_end)})."
            )
        if new_s >= e or new_s > now:
            await callback.answer("Out of bounds.", show_alert=True)
            return
        s = new_s
    else:
        new_e = e + callback_data.delta
        if new_e <= s or new_e > now:
            await callback.answer("Out of bounds.", show_alert=True)
            return
        e = new_e

    await callback.message.edit_text(
        stepper_text(callback.from_user.full_name, s, e, target),
        reply_markup=stepper_kb(target, callback.from_user.id, s, e),
    )
    if clamp_msg:
        await callback.answer(clamp_msg, show_alert=True)
    else:
        await callback.answer()


@dp.callback_query(StepperSubmit.filter())
async def on_stepper_submit(callback: CallbackQuery, callback_data: StepperSubmit) -> None:
    if callback.message is None:
        return
    if callback.from_user.id != callback_data.user_id:
        await callback.answer("Only the owner can use these buttons.", show_alert=True)
        return

    name = callback.from_user.full_name
    s, e = callback_data.start_ts, callback_data.end_ts
    await callback.message.edit_text(
        log_text(name, s, e),
        reply_markup=edit_menu_kb(callback.from_user.id, s, e),
    )
    await callback.answer()


async def update_total(
    bot: Bot, chat_id: int, user_id: int, name: str, start_ts: int, end_ts: int
) -> None:
    state = chat_states.get(chat_id)
    if state is None:
        state = ChatState()
        sent = await bot.send_message(
            chat_id,
            render_tracking_message(state),
            reply_markup=start_keyboard(),
        )
        state.message_id = sent.message_id
        chat_states[chat_id] = state

    existing = state.members.get(user_id)
    prev_seconds = existing.total_seconds if existing else 0
    prev_formatted = format_total(prev_seconds)
    new_seconds = prev_seconds + (end_ts - start_ts)
    new_formatted = format_total(new_seconds)

    state.members[user_id] = MemberState(
        name=name, total_seconds=new_seconds, last_end_ts=end_ts
    )
    save_states()

    if state.message_id is None:
        return
    if existing is not None and new_formatted == prev_formatted:
        return

    await bot.edit_message_text(
        render_tracking_message(state),
        chat_id=chat_id,
        message_id=state.message_id,
        reply_markup=start_keyboard(),
    )


async def main() -> None:
    load_dotenv()
    load_states()
    token = os.environ["TG_TOKEN"]
    bot = Bot(token=token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
