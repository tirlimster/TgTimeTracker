import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import CommandStart
from aiogram.types import ChatMemberUpdated, Message
from dotenv import load_dotenv

dp = Dispatcher()

GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
PRESENT_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
ABSENT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


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
    await event.bot.send_message(
        event.chat.id,
        f"Hello, {event.chat.title}! Thanks for adding me — I'm ready to track time.",
    )


async def main() -> None:
    load_dotenv()
    token = os.environ["TG_TOKEN"]
    bot = Bot(token=token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
