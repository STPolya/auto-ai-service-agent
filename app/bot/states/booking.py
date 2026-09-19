from aiogram.fsm.state import State, StatesGroup


class Booking(StatesGroup):
    vehicle = State()
    service = State()
    date = State()
    time = State()
    confirmation = State()
