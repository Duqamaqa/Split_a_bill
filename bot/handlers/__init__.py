from aiogram import Dispatcher

from .callbacks import router as callbacks_router
from .fallback import router as fallback_router
from .invite import router as invite_router
from .ledger import router as ledger_router
from .remind import router as remind_router
from .start import router as start_router


def include_routers(dispatcher: Dispatcher) -> None:
    dispatcher.include_router(start_router)
    dispatcher.include_router(invite_router)
    dispatcher.include_router(ledger_router)
    dispatcher.include_router(remind_router)
    dispatcher.include_router(callbacks_router)
    dispatcher.include_router(fallback_router)
