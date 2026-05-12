import asyncio
import logging
import os
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import CommandStart
from aiogram.types import ChatMemberUpdated, Message
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


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours:02d}:{minutes:02d}"


def render_tracking_message(state: ChatState) -> str:
    lines = [f"{TRACKING_TITLE}:"]
    for name, seconds in state.members.values():
        lines.append(f"{name}: {format_duration(seconds)}")
    return "\n".join(lines)


@dp.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(f"Hello, {message.from_user.full_name}!")


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

    sent = await event.bot.send_message(event.chat.id, render_tracking_message(state))
    state.message_id = sent.message_id
    chat_states[event.chat.id] = state


async def main() -> None:
    load_dotenv()
    token = os.environ["TG_TOKEN"]
    bot = Bot(token=token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
