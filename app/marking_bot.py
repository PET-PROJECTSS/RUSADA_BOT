import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from app.config import Settings
from app.db import Database
from app.logging_setup import setup_logger

settings = Settings()
settings.validate()

logger = setup_logger("RUSADA", settings.log_level, settings.logs_dir)

bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
db = Database(settings)


class MarkingState(StatesGroup):
    waiting_for_answer = State()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Бот разметки правильных ответов.\n\n"
        "Команды:\n"
        "/next - показать следующий неразмеченный вопрос\n"
        "/stats - статистика разметки\n\n"
        "Формат ответа: номера вариантов через пробел или запятую, например: 1 3 или 1,3"
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    marked, unmarked = db.stats()
    await message.answer(
        f"Статистика:\n\n"
        f"Размечено: {marked}\n"
        f"Осталось: {unmarked}\n"
        f"Всего: {marked + unmarked}"
    )


@dp.message(Command("next"))
async def cmd_next(message: Message, state: FSMContext):
    question = db.get_unmarked_question()
    if not question:
        await message.answer("Все вопросы размечены!")
        await state.clear()
        return

    question_id, question_text = question
    answers = db.get_answers(question_id)

    if not answers:
        await message.answer(f"У вопроса ID={question_id} нет вариантов ответа.")
        await state.clear()
        return

    await state.update_data(current_question_id=question_id, current_answers=answers)
    await state.set_state(MarkingState.waiting_for_answer)

    text = f"Вопрос #{question_id}:\n\n{question_text}\n\nВарианты ответов:\n"
    for idx, (_, answer_text) in enumerate(answers, start=1):
        text += f"{idx}. {answer_text}\n"
    text += "\nНапиши номера правильных ответов (например: 1 или 1 3 или 1,3)"
    await message.answer(text)


@dp.message(MarkingState.waiting_for_answer, F.text)
async def handle_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    if "current_question_id" not in data:
        await message.answer("Сначала используй /next.")
        return

    question_id = data["current_question_id"]
    answers = data["current_answers"]

    try:
        numbers_str = re.sub(r"[^\d\s,]", "", message.text)
        numbers_str = re.sub(r"[,\s]+", " ", numbers_str)
        selected = [int(x) for x in numbers_str.split() if x]
    except ValueError:
        await message.answer("Неверный формат. Используй номера через пробел или запятую.")
        return

    if not selected:
        await message.answer("Не удалось распознать номера.")
        return

    if not all(1 <= n <= len(answers) for n in selected):
        await message.answer(f"Неверный номер. Доступны варианты от 1 до {len(answers)}")
        return

    correct_ids = [answers[n - 1][0] for n in selected]
    db.mark_question(question_id, correct_ids)

    chosen = "\n".join(f"  {n}. {answers[n - 1][1]}" for n in selected)
    await message.answer(f"Ответ сохранён:\n{chosen}\n\nИспользуй /next для следующего вопроса.")
    await state.clear()


async def main():
    if settings.db_configured:
        db.init_schema()
    logger.info("Marking bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
