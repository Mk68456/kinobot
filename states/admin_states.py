from aiogram.dispatcher.filters.state import State,StatesGroup



class Admin_(StatesGroup):
    add_new_cod = State()
    send_ = State()
    add_channel_link = State()
    add_channel = State()
    delete_channel = State()