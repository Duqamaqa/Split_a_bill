from aiogram import Dispatcher

from .simple import router as simple_router


def include_routers(dispatcher: Dispatcher) -> None:
    dispatcher.include_router(simple_router)
