import logging
from aiogram.utils.exceptions import TelegramAPIError
from database.roles import bypasses_subscription
from database.channels import get_all_channels_cod

logger = logging.getLogger(__name__)


async def check_subscription(user_id, bot=None):
    if bypasses_subscription(user_id):
        return True
    if bot is None:
        from loader import bot
    for (channel_id,) in get_all_channels_cod() or []:
        try:
            member = await bot.get_chat_member(channel_id, user_id=user_id)
        except TelegramAPIError:
            logger.warning(
                "Subscription check failed for channel %s, user %s", channel_id, user_id, exc_info=True
            )
            return False
        if member.status in ("left", "kicked"):
            return False
        if member.status == "restricted" and not member.is_member:
            return False
        if member.status not in ("creator", "administrator", "member", "restricted"):
            return False
    return True
