import os
import asyncio
import json
import logging
from typing import Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BotCommand
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv
import google.generativeai as genai

# --- 1. НАСТРОЙКИ ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
MY_ID = 6055791149

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. ЛОГИКА АВТОПОДБОРА МОДЕЛИ (ТВОЙ КОД) ---
genai.configure(api_key=GEMINI_KEY)

async def list_models_safe():
    try:
        # Для локального запуска используем loop.run_in_executor или просто прямой вызов, 
        # так как в библиотеке он обычно синхронный
        return genai.list_models()
    except Exception as e:
        logger.exception("Не удалось получить список моделей: %s", e)
        return []

def choose_available_model(models_iterable, preferred_keywords=("gemini", "flash", "2.0", "3.0")) -> Optional[str]:
    available = []
    for m in models_iterable:
        name = getattr(m, "name", None) or getattr(m, "model", None) or None
        methods = getattr(m, "supported_generation_methods", None) or []
        available.append({"name": name, "methods": methods})

    logger.info("Найдено моделей: %d", len(available))

    for entry in available:
        name = entry["name"]
        methods = entry["methods"]
        if not name: continue
        if any(k in name.lower() for k in preferred_keywords) and any(
            m in methods for m in ("generateContent", "chat", "sendMessage", "send_message")
        ):
            logger.info("Выбрана лучшая модель: %s", name)
            return name

    for entry in available:
        name = entry["name"]
        if name and "generateContent" in entry["methods"]:
            return name
    return None

# Инициализация модели при старте
try:
    # Пытаемся получить список моделей
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    models_list = list(genai.list_models())
    SELECTED_MODEL = choose_available_model(models_list)
except Exception as e:
    logger.error(f"Ошибка при поиске моделей: {e}")
    SELECTED_MODEL = "models/gemini-1.5-flash-latest"

if not SELECTED_MODEL:
    SELECTED_MODEL = "models/gemini-1.5-flash-latest"
    logger.warning("Использую fallback: %s", SELECTED_MODEL)

def get_model(is_admin: bool = False):
    base_prompt = "Ты — ИИ Gemini. Отвечай чётко, кратко и без воды."
    system_instruction = f"Максим — твой создатель. {base_prompt}" if is_admin else f"{base_prompt} Ты создан Максимом."
    try:
        return genai.GenerativeModel(model_name=SELECTED_MODEL, system_instruction=system_instruction)
    except Exception as e:
        logger.error(f"Ошибка GenerativeModel: {e}")
        return genai.GenerativeModel(model_name=SELECTED_MODEL)

# --- 3. ФАЙЛЫ И БОТ ---
HISTORY_FILE = "memory.json"
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_history(data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- 4. ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Максим, система готова. Модель выбрана автоматически.")

@dp.message(F.text)
async def handle_message(message: types.Message):
    uid = str(message.from_user.id)
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        try:
            histories = load_history()
            user_history = histories.get(uid, [])
            
            model = get_model(is_admin=(int(uid) == MY_ID))
            chat = model.start_chat(history=user_history)
            
            response = await asyncio.to_thread(chat.send_message, message.text)

            # Сохраняем историю (кратко)
            new_history = []
            for content in chat.history[-10:]:
                new_history.append({"role": content.role, "parts": [p.text for p in content.parts]})
            
            histories[uid] = new_history
            save_history(histories)

            await message.answer(response.text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка чата: {e}")
            await message.answer("⚠️ Ошибка. Проверь API ключ или соединение.")

# --- 5. ЗАПУСК ---
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print(f"🚀 ЗАПУСК. ВЫБРАННАЯ МОДЕЛЬ: {SELECTED_MODEL}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
