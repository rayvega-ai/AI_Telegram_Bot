import os
import asyncio
import json
import logging
from typing import Optional, Iterable

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BotCommand
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------------------
# 1. Загрузка окружения
# ---------------------------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
MY_ID = int(os.getenv("MY_ID", "6055791149"))
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL")  # опционально, например "gemini-2.0"

if not TELEGRAM_TOKEN or not GEMINI_KEY:
    raise RuntimeError("TELEGRAM_TOKEN or GEMINI_API_KEY not set in .env")

# ---------------------------
# 2. Логирование и бот
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

HISTORY_FILE = "memory.json"
HISTORY_LIMIT = 10

# ---------------------------
# 3. Gemini init + выбор модели
# ---------------------------
genai.configure(api_key=GEMINI_KEY)

def choose_available_model(models: Iterable, preferred_keywords=("gemini", "flash", "2.0", "3.0")) -> Optional[str]:
    """
    Выбирает первую подходящую модель из iterable models.
    Ищем модель по ключевым словам и поддержке методов генерации.
    """
    available = []
    for m in models:
        name = getattr(m, "name", None) or getattr(m, "model", None) or None
        methods = getattr(m, "supported_generation_methods", None) or []
        available.append({"name": name, "methods": methods})

    logger.info("Найдено моделей: %d", len(available))

    # Первичный выбор: имя содержит ключевое слово и поддерживает generateContent/chat
    for entry in available:
        name = entry["name"]
        methods = entry["methods"]
        if not name:
            continue
        name_low = name.lower()
        if any(k in name_low for k in preferred_keywords) and any(
            m in methods for m in ("generateContent", "chat", "sendMessage", "send_message")
        ):
            logger.info("Выбрана модель: %s (методы: %s)", name, methods)
            return name

    # Fallback: любая модель с generateContent
    for entry in available:
        name = entry["name"]
        methods = entry["methods"]
        if name and "generateContent" in methods:
            logger.info("Fallback выбор модели: %s", name)
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

    # Если ничего не найдено — используем FALLBACK_MODEL или подсказываем в ошибке
    if FALLBACK_MODEL:
        logger.warning("Автовыбор модели не удался — использую FALLBACK_MODEL из окружения: %s", FALLBACK_MODEL)
        return FALLBACK_MODEL

    # Явная ошибка с инструкцией, чтобы пользователь не получил silent 404
    raise RuntimeError(
        "Не удалось автоматически подобрать модель Gemini. "
        "Установи переменную окружения FALLBACK_MODEL или проверь права API-ключа."
    )

SELECTED_MODEL = detect_model()
logger.info("SELECTED_MODEL = %s", SELECTED_MODEL)

def get_model(is_admin: bool = False):
    base_prompt = "Ты — ИИ Gemini. Отвечай чётко, кратко и без воды."
    system_instruction = f"Максим — твой создатель. {base_prompt}" if is_admin else f"{base_prompt} Ты создан Максимом."
    try:
        return genai.GenerativeModel(model_name=SELECTED_MODEL, system_instruction=system_instruction)
    except Exception as e:
        logger.exception("Ошибка создания GenerativeModel с system_instruction: %s", e)
        # Пробуем без system_instruction
        try:
            return genai.GenerativeModel(model_name=SELECTED_MODEL)
        except Exception as e2:
            logger.exception("Повторная ошибка создания GenerativeModel: %s", e2)
            raise

# ---------------------------
# 4. Работа с историей (json)
# ---------------------------
def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.exception("Не удалось загрузить историю: %s", e)
            return {}
    return {}

def save_history(data: dict):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.exception("Не удалось сохранить историю: %s", e)

# ---------------------------
# 5. Хендлеры
# ---------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я OrionX Assistant. Напиши задачу — постараюсь помочь.")

@dp.message(F.text)
async def handle_message(message: types.Message):
    uid = str(message.from_user.id)

    # Показываем статус "печатает"
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception:
        # если не удалось отправить chat action — не критично
        pass

    try:
        histories = load_history()
        user_history = histories.get(uid, [])

        model = get_model(is_admin=(int(uid) == MY_ID))
        chat = model.start_chat(history=user_history)

        # Выполняем блокирующий вызов в отдельном потоке
        response = await asyncio.to_thread(chat.send_message, message.text)

        # Сохраняем последние HISTORY_LIMIT элементов истории
        new_history = []
        for content in getattr(chat, "history", [])[-HISTORY_LIMIT:]:
            parts_texts = []
            for p in getattr(content, "parts", []):
                text = getattr(p, "text", None)
                if text:
                    parts_texts.append(text)
            new_history.append({"role": getattr(content, "role", None), "parts": parts_texts})

        histories[uid] = new_history
        save_history(histories)

        # Отправляем ответ (без parse_mode чтобы не ломать спецсимволы)
        await message.answer(response.text)

    except Exception as e:
        logger.exception("Ошибка чата: %s", e)
        await message.answer("⚠️ Ошибка. Проверь API-ключ Gemini и сетевое соединение.")

# ---------------------------
# 6. Запуск
# ---------------------------
async def main():
    # Регистрируем команды (опционально)
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Запуск бота"),
        ])
    except Exception as e:
        logger.warning("Не удалось установить команды: %s", e)

    logger.info("Запуск бота. Выбранная модель: %s", SELECTED_MODEL)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
