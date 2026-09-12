from aiogram import types
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.dispatcher.handler import CancelHandler, current_handler


def is_admin(user_id):
    from data.config import ADMIN

    return user_id in ADMIN


class AccessMiddleware(BaseMiddleware):
    async def _check(self, event):
        handler = current_handler.get()
        message = event.message if isinstance(event, types.CallbackQuery) else event
        if message is None or message.chat.type != "private":
            if isinstance(event, types.CallbackQuery):
                await event.answer("Откройте личный чат с ботом.", show_alert=True)
            raise CancelHandler()
        if handler.__module__.startswith("handlers.admin.") and not is_admin(event.from_user.id):
            if isinstance(event, types.CallbackQuery):
                await event.answer("Доступ только для администратора.", show_alert=True)
            else:
                await event.answer("Доступ только для администратора.")
            raise CancelHandler()
        if (
            isinstance(event, types.CallbackQuery)
            and handler.__module__ == "handlers.users.find_movie"
            and handler.__name__ not in ("find_movie_button_handler", "find_series_button_handler")
        ):
            from services.subscriptions import check_subscription

            if not await check_subscription(event.from_user.id):
                await event.answer("Для доступа подпишитесь на все каналы.", show_alert=True)
                raise CancelHandler()

    async def on_process_message(self, message, data):
        await self._check(message)

    async def on_process_callback_query(self, callback_query, data):
        await self._check(callback_query)
