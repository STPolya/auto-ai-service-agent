from aiogram.fsm.state import State, StatesGroup


class Diagnostics(StatesGroup):
    waiting_for_problem_description = State()
