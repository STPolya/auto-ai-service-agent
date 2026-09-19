from aiogram.fsm.state import State, StatesGroup


class AddVehicle(StatesGroup):
    brand = State()
    model = State()
    year = State()
    license_plate = State()
