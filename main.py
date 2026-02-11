# main.py — production-focused OrionX Assistant (compact & robust)
import os
import asyncio
import json
import logging
import textwrap
from typing import Optional, Iterable
from tempfile import NamedTemporaryFile
from time import time

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BotCommand
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------------------
# 0. Инструкция по .env
# ---------------------------
# TELEGRAM_TOKEN=...
# GEMINI_API_KEY=...   # ключ из Google AI Studio (aistudio)
# MY_ID=6055791149     # твой Telegram ID (опционально)
# FALLBACK_MODEL=gemini-2.0-flash   # опция, если list_models недоступен

# ---------------------------
# 1. Загрузка окружения и проверки
# ---------------------------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
MY_ID = int(os.getenv("MY_ID", "6055791149"))
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL")  # опционально

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не задан в .env")
if not GEMINI_KEY:
    raise RuntimeError("GEMINI_API_KEY не задан в .env (создавай ключ в AI Studio)")

# ---------------------------
# 2. Логирование и бот
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("orionx")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

HISTORY_FILE = "memory.json"
HISTORY_LIMIT = 10
TELEGRAM_MESSAGE_LIMIT = 4000  # безопасный предел для одного сообщения

# ---------------------------
# 3. Помощники для safe json write
# ---------------------------
def atomic_write_json(path: str, data):
    dirpath = os.path.dirname(os.path.abspath(path)) or "."
    with NamedTemporaryFile("w", delete=False, dir=dirpath, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tmpname = tf.name
    os.replace(tmpname, path)

def load_json_safe(path: str):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Не удалось прочитать %s: %s", path, e)
        return {}

# ---------------------------
# 4. Инициализация Gemini и выбор модели
# ---------------------------
genai.configure(api_key=GEMINI_KEY)

def choose_available_model(models: Iterable, preferred_keywords=("gemini", "flash", "2.0", "3.0")) -> Optional[str]:
    available = []
    for m in models:
        name = getattr(m, "name", None) or getattr(m, "model", None) or None
        methods = getattr(m, "supported_generation_methods", None) or []
        available.append({"name": name, "methods": methods})

    logger.info("Найдено моделей: %d", len(available))
    # первичный выбор
    for entry in available:
        name = entry["name"]
        methods = entry["methods"]
        if not name:
            continue
        nl = name.lower()
        if any(k in nl for k in preferred_keywords) and any(
            m in methods for m in ("generateContent", "chat", "sendMessage", "send_message")
        ):
            logger.info("Выбрана модель: %s (методы: %s)", name, methods)
            return name
    # fallback
    for entry in available:
        name = entry["name"]
        methods = entry["methods"]
        if name and "generateContent" in methods:
            logger.info("Fallback модель: %s", name)
            return name
    return None

def detect_model() -> str:
    try:
        models = genai.list_models()
    except Exception as e:
        logger.exception("genai.list_models() не сработал: %s", e)
        models = []

    selected = choose_available_model(models)
    if selected:
        return selected

    if FALLBACK_MODEL:
        logger.warning("Автовыбор модели не удался — использую FALLBACK_MODEL: %s", FALLBACK_MODEL)
        return FALLBACK_MODEL

    raise RuntimeError(
        "Не удалось подобрать модель автоматически. "
        "Укажи FALLBACK_MODEL в .env (например gemini-2.0-flash) или запусти сервер в поддерживаемом регионе."
    )

SELECTED_MODEL = detect_model()
logger.info("SELECTED_MODEL = %s", SELECTED_MODEL)

def get_model(is_admin: bool = False):
    base_prompt = "Ты — ИИ Gemini. Отвечай чётко, кратко и без воды."
    system_instruction = f"Максим — твой создатель. {base_prompt}" if is_admin else f"{base_prompt} Ты создан Максимом."
    try:
        return genai.GenerativeModel(model_name=SELECTED_MODEL, system_instruction=system_instruction)
    except Exception:
        logger.exception("Ошибка создания GenerativeModel с system_instruction, пробую без system_instruction")
        return genai.GenerativeModel(model_name=SELECTED_MODEL)

# ---------------------------
# 5. Работа с историей
# ---------------------------
def load_history() -> dict:
    return load_json_safe(HISTORY_FILE)

def save_history(h: dict):
    try:
        atomic_write_json(HISTORY_FILE, h)
    except Exception as e:
        logger.exception("Ошибка сохранения истории: %s", e)

# ---------------------------
# 6. Вспомогательные функции
# ---------------------------
async def send_long_message(dest: types.Chat | int, text: str):
    # Разбиваем длинные ответы на куски <= TELEGRAM_MESSAGE_LIMIT
    for chunk in textwrap.wrap(text, TELEGRAM_MESSAGE_LIMIT, replace_whitespace=False):
        await bot.send_message(chat_id=dest, text=chunk)

# ---------------------------
# 7. Команды (минимальный набор)
# ---------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = (
        "Привет! Я OrionX Assistant — практичный AI-ассистент.\n"
        "Помогаю с задачами, автоматизацией и личной продуктивностью.\n"
        "Напиши, что нужно сделать. /help — список команд."
    )
    await message.answer(text)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "/start — запуск\n"
        "/help — подсказка\n"
        "/clear_history — удалить твою историю (безопасно)\n"
    )
    if message.from_user.id == MY_ID:
        text += "/admin — статистика (владелец)\n"
    await message.answer(text)

@dp.message(Command("clear_history"))
async def cmd_clear_history(message: types.Message):
    uid = str(message.from_user.id)
    histories = load_history()
    if uid in histories:
        histories.pop(uid, None)
        save_history(histories)
        await message.answer("Ваша история очищена.")
    else:
        await message.answer("У вас нет сохранённой истории.")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != MY_ID:
        return
    histories = load_history()
    total_users = len(histories)
    await message.answer(f"Статей пользователей в памяти: {total_users}\nМодель: {SELECTED_MODEL}")

# ---------------------------
# 8. Основной хендлер сообщений
# ---------------------------
@dp.message(F.text)
async def handle_message(message: types.Message):
    uid = str(message.from_user.id)

    # show typing
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:
        pass

    try:
        histories = load_history()
        user_history = histories.get(uid, [])

        model = get_model(is_admin=(int(uid) == MY_ID))
        chat = model.start_chat(history=user_history)

        # heavy call in thread
        response = await asyncio.to_thread(chat.send_message, message.text)

        # save last HISTORY_LIMIT turns
        new_history = []
        for content in getattr(chat, "history", [])[-HISTORY_LIMIT:]:
            parts_texts = []
            for p in getattr(content, "parts", []):
                t = getattr(p, "text", None)
                if t:
                    parts_texts.append(t)
            new_history.append({"role": getattr(content, "role", None), "parts": parts_texts})

        histories[uid] = new_history
        save_history(histories)

        # send the response safely (split long messages)
        await send_long_message(message.chat.id, response.text or "—")

    except Exception as e:
        logger.exception("Ошибка чата: %s", e)
        await message.answer("⚠️ Внутренняя ошибка при обращении к ИИ. Попробуйте позже.")

# ---------------------------
# 9. Запуск
# ---------------------------
async def main():
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Запуск бота"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="clear_history", description="Очистить историю"),
        ])
    except Exception as e:
        logger.warning("Не удалось установить команды: %s", e)

    logger.info("Бот запущен. Модель: %s", SELECTED_MODEL)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
