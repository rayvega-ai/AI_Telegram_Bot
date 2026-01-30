import os
import asyncio
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv
import google.generativeai as genai

# 1. Настройка окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
# Впиши свой ID цифрами (увидишь его в терминале при первом сообщении)
MY_ID = 6055791149

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# 2. Единая Система (Инструкции)
SYSTEM_PROMPT = (
    "Максим — твой создатель. Обращайся к нему только по имени в начале предложений.\n"
    "Твои правила:\n"
    "1. ФОРМАТ: Используй жирный текст для акцентов и списки для перечислений.\n"
    "2. СТИЛЬ: Деловой, без воды. Если задача простая — отвечай коротко.\n"
    "3. ПАМЯТЬ: Анализируй историю переписки, чтобы не переспрашивать.\n"
    "4. ГРАФИКА: Используй MarkdownV2 (но aiogram сделает это сам через обычный текст)."
)

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel(
    model_name="models/gemini-flash-latest",
    system_instruction=SYSTEM_PROMPT
)

HISTORY_FILE = "memory.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            if os.path.getsize(HISTORY_FILE) > 0:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except: return {}
    return {}

def save_history(history_data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=4)

user_histories = load_history()

@dp.message(Command("start"))
async def start(message: types.Message):
    if message.from_user.id != MY_ID:
        return # Защита: чужим не отвечаем
    await message.answer("Максим, система готова. Память загружена. Жду указаний.")

@dp.message()
async def talk(message: types.Message):
    uid_int = message.from_user.id
    uid = str(uid_int)

    # ЛОГ: выводит в консоль ID того, кто пишет (поможет тебе узнать свой ID)
    print(f"Сообщение от ID: {uid_int} ({message.from_user.full_name})")

    # ЗАЩИТА: Если пишет не Максим — игнорируем
    if uid_int != MY_ID:
        await message.answer("Доступ ограничен. Я работаю только с создателем.")
        return

    current_history = user_histories.get(uid, [])
    chat = model.start_chat(history=current_history)
    
    try:
        response = chat.send_message(message.text)
        
        # Обновление истории
        updated_history = []
        for content in chat.history:
            updated_history.append({
                "role": content.role,
                "parts": [part.text for part in content.parts]
            })
        
        user_histories[uid] = updated_history
        save_history(user_histories)
        
        await message.answer(response.text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"Ошибка системы: {e}")

async def run():
    print("--- БОТ ЗАПУЩЕН ---")
    print(f"Режим защиты: Включен (Только для ID: {MY_ID})")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(run())