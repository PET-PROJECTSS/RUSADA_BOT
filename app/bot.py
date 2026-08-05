import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import Settings
from app.logging_setup import setup_logger
from app.rusada import RusadaBot

settings = Settings()
settings.validate()

logger = setup_logger("RUSADA", settings.log_level, settings.logs_dir)

storage = MemoryStorage()
bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=storage)
router = Router()

_ACCOUNT_WAITERS: dict[int, dict] = {}


class TestFlow(StatesGroup):
    waiting_email = State()
    waiting_password = State()


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начать прохождение", callback_data="start_test")],
            [InlineKeyboardButton(text="Помощь", callback_data="help")],
        ]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel")]]
    )


def is_allowed(user_id: int) -> bool:
    return not settings.allowed_users or user_id in settings.allowed_users


class TelegramEditLogger(logging.Handler):
    def __init__(self, status_msg: Message):
        super().__init__()
        self.status_msg = status_msg
        self.last_text = ""

    def emit(self, record):
        if "RUSADA" not in record.name:
            return
        log_entry = self.format(record)
        try:
            clean_text = log_entry.split("|")[-1].strip()
        except Exception:
            clean_text = log_entry

        new_text = f"<b>Выполняется...</b>\n\n<code>{clean_text}</code>"
        if new_text == self.last_text:
            return
        self.last_text = new_text
        asyncio.create_task(self._safe_edit(new_text))

    async def _safe_edit(self, text: str):
        try:
            await self.status_msg.edit_text(text, reply_markup=None)
        except Exception:
            pass


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_allowed(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await message.answer("RUSADA AutoBot\nБот проходит курс за вас.", reply_markup=main_kb())


@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    await call.answer()
    await call.message.edit_text(
        "Нажмите 'Начать прохождение' и введите email и пароль от портала РУСАДА.",
        reply_markup=main_kb(),
    )


@router.callback_query(F.data == "start_test")
async def cb_start_test(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TestFlow.waiting_email)
    await call.message.edit_text("Введите Email", reply_markup=cancel_kb())


@router.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    if call.message.chat.id in _ACCOUNT_WAITERS:
        _ACCOUNT_WAITERS[call.message.chat.id]["fut"].cancel()
        _ACCOUNT_WAITERS.pop(call.message.chat.id, None)
    await call.answer()
    await state.clear()
    await call.message.edit_text("Отменено.", reply_markup=main_kb())


@router.message(StateFilter(TestFlow.waiting_email), F.text)
async def on_email(message: Message, state: FSMContext):
    await state.update_data(email=message.text.strip())
    await state.set_state(TestFlow.waiting_password)
    await message.answer("Введите Пароль", reply_markup=cancel_kb())


@router.message(StateFilter(TestFlow.waiting_password), F.text)
async def on_password(message: Message, state: FSMContext):
    data = await state.get_data()
    email = data.get("email")
    password = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass

    status_msg = await message.answer("Запуск...")
    asyncio.create_task(run_rusada_task(status_msg, email, password, message.chat.id))
    await state.clear()


@router.callback_query(F.data.startswith("sel:"))
async def cb_selection(call: CallbackQuery):
    waiter = _ACCOUNT_WAITERS.get(call.message.chat.id)
    if not waiter:
        return await call.answer("Опоздали.")

    parts = call.data.split(":")
    mode = parts[1]

    if mode == "self":
        waiter["fut"].set_result({"type": "self"})
        await call.answer("Выбран родитель")
    elif mode == "child":
        idx = int(parts[2])
        children = waiter["children"]
        if 0 <= idx < len(children):
            child = children[idx]
            waiter["fut"].set_result(
                {"type": "child", "name": child["name"], "href": child["href"]}
            )
            await call.answer(f"Выбран {child['name']}")


async def run_rusada_task(status_msg: Message, email: str, password: str, chat_id: int):
    rusada = RusadaBot(settings)
    tg_handler = TelegramEditLogger(status_msg)
    tg_handler.setFormatter(logging.Formatter("%(message)s"))
    rusada.log.addHandler(tg_handler)

    async def account_selector_callback(parent_name: str, children: list[dict]):
        rusada.log.removeHandler(tg_handler)
        await asyncio.sleep(1)

        rows = [[InlineKeyboardButton(text=f"{parent_name} (Родитель)", callback_data="sel:self")]]
        for i, child in enumerate(children):
            rows.append(
                [InlineKeyboardButton(text=child["name"], callback_data=f"sel:child:{i}")]
            )
        kb = InlineKeyboardMarkup(inline_keyboard=rows)

        try:
            await status_msg.edit_text("Выберите, за кого проходить тест:", reply_markup=kb)
        except Exception as exc:
            logger.error("Keyboard error: %s", exc)
            rusada.log.addHandler(tg_handler)
            return None

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        _ACCOUNT_WAITERS[chat_id] = {"fut": fut, "children": children}

        try:
            result = await asyncio.wait_for(fut, timeout=300)
            selected_name = (
                result.get("name", parent_name) if result.get("type") == "child" else parent_name
            )
            await status_msg.edit_text(f"Выбран: {selected_name}\n\nПродолжаю прохождение...", reply_markup=None)
            rusada.log.addHandler(tg_handler)
            return result
        except asyncio.TimeoutError:
            logger.warning("Account selection timeout")
            rusada.log.addHandler(tg_handler)
            return None
        except Exception as exc:
            logger.error("Account selection error: %s", exc)
            rusada.log.addHandler(tg_handler)
            return None
        finally:
            _ACCOUNT_WAITERS.pop(chat_id, None)

    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        url = await rusada.run(email, password, select_account_cb=account_selector_callback)

        if url:
            full_url = url if url.startswith("http") else f"{settings.rusada_url}{url}"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Скачать", url=full_url)],
                    [InlineKeyboardButton(text="В меню", callback_data="cancel")],
                ]
            )
            await status_msg.edit_text(
                f"Сертификат готов!\n\n<code>{full_url}</code>",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        else:
            await status_msg.edit_text("Не удалось получить сертификат.", reply_markup=main_kb())
    except Exception as exc:
        logger.error("Critical error in run_rusada_task: %s", exc, exc_info=True)
        await status_msg.edit_text(f"Ошибка: {exc}", reply_markup=main_kb())
    finally:
        handlers = rusada.log.handlers[:]
        for handler in handlers:
            if isinstance(handler, TelegramEditLogger):
                rusada.log.removeHandler(handler)


async def _health_server(port: int):
    from aiohttp import web

    async def health(_request):
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health server on :%s", port)


async def on_shutdown():
    logger.info("Shutting down...")
    await bot.session.close()


async def main():
    dp.include_router(router)
    dp.shutdown.register(on_shutdown)

    if settings.db_configured:
        try:
            db = RusadaBot(settings).db
            db.init_schema()
        except Exception as exc:
            logger.error("DB init failed: %s", exc)

    asyncio.create_task(_health_server(settings.health_port))

    logger.info("Bot started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
