# RUSADA_BOT

Telegram-бот, который автоматизирует прохождение курса на портале РУСАДА
(login, выбор аккаунта родителя/ребёнка, прохождение вопросов с подсказками из БД,
получение ссылки на сертификат) и разметку правильных ответов в базе данных.

## Структура

- `app/bot.py` — основной Telegram-бот (прохождение курса), точка входа `python -m app.bot`
- `app/marking_bot.py` — бот для разметки правильных ответов, `python -m app.marking_bot`
- `app/scraper.py` — сбор вопросов и ответов в БД, `python -m app.scraper --headless`
- `app/rusada.py` — автоматизация портала на Playwright
- `app/db.py` — доступ к PostgreSQL (Neon), работа с вопросами/ответами
- `app/config.py` — настройки из переменных окружения

## Требования

- Python 3.12
- PostgreSQL (Neon) со схемой `rusada` (создаётся автоматически при старте бота)

## Настройка

1. Скопировать `.env.example` в `.env` и заполнить:
   - `BOT_TOKEN` — токен Telegram-бота
   - `RUSADA_EMAIL` / `RUSADA_PASSWORD` — учётные данные портала
   - `RUSADA_DB_*` — параметры PostgreSQL (Neon)
2. Установить зависимости и браузер Playwright:

   ```bash
   pip install -r requirements.txt
   playwright install --with-deps chromium
   ```

3. Запустить бота:

   ```bash
   python -m app.bot
   ```

   Опционально ограничить доступ пользователями: `ALLOWED_USERS=123,456`.

## Деплой на сервер

Репозиторий деплоится через общий reusable-workflow
`PET-PROJECTSS/PET_PROJECTS.ACTIONS` при каждом пуше в `main`.

Перед первым деплоем на сервере создать `/opt/projects/rusada-bot/.env`
(копия `.env.example` с реальными значениями). Файл `.env` в git не коммитится.

Сервер сам соберёт образ (внутри устанавливается Chromium Playwright) и запустит
контейнер с healthcheck'ом на `/health` (порт 8000, без внешней публикации).

## Предупреждение

Файл `.env` содержит чувствительные данные (токен бота, пароль, доступ к БД).
Никогда не коммитьте его. Если он попадал в историю git — смените все секреты.
