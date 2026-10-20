import os
import io
import re
import asyncio
from collections import deque
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from google import genai
from google.genai import types as genai_types

# Загрузка токенов из переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Поддержка обоих вариантов названия ключа для удобства
GEMINI_KEY = os.environ.get("GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_KEY)

# Память для контекста диалогов
chat_buffers = {}

# Твой промпт
SYSTEM_PROMPT = """Ты креативный журналист и обозреватель Telegram-канала "Калейдоскоп" (г. Брест). 
Твой стиль — яркий, увлекательный и нестандартный. Ты помогаешь превращать сухие факты из интернета в цепляющие посты, сохраняя легкую творческую атмосферу. 
Отвечай на любые сообщения строго в этой манере, помогая пользователю с его задачами."""

# Настройки ИИ (включен поиск в интернете)
config = genai_types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    tools=[{"google_search": {}}]
)

def add_to_buffer(chat_id: int, user_name: str, text: str):
    if not text:
        return
    if chat_id not in chat_buffers:
        chat_buffers[chat_id] = deque(maxlen=10) # Запоминает последние 10 сообщений
    chat_buffers[chat_id].append(f"{user_name}: {text}")

def get_chat_context(chat_id: int) -> str:
    if chat_id not in chat_buffers or not chat_buffers[chat_id]:
        return ""
    return "\n".join(chat_buffers[chat_id])

@dp.message(CommandStart())
async def handle_start(message: types.Message):
    await message.answer(
        "Привет! Я — креативный журналист и твой ИИ-соавтор для канала «Калейдоскоп». 📝\n"
        "Присылай мне сухие факты, новости, идеи или фото, а я превращу их в яркие и увлекательные посты!"
    )

def should_respond(message: types.Message, bot_username: str) -> tuple[bool, str]:
    if message.chat.type == "private":
        return True, message.text or message.caption or ""
        
    text = message.text or message.caption or ""
    
    # Ответ на сообщение самого бота
    if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
        return True, text
        
    # Упоминание через @
    if f"@{bot_username}".lower() in text.lower():
        cleaned_text = re.sub(f"@{bot_username}", "", text, flags=re.IGNORECASE).strip()
        return True, cleaned_text
        
    # Обращение "Бот, ..."
    text_lower = text.lower().strip()
    if text_lower.startswith("бот,") or text_lower.startswith("бот "):
        cleaned_text = text[4:].strip()
        return True, cleaned_text
        
    return False, ""

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    bot_info = await bot.get_me()
    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Автор"
    caption_text = message.caption or ""
    
    need_answer, prompt = should_respond(message, bot_info.username)
    if not need_answer:
        if caption_text:
            add_to_buffer(chat_id, user_name, f"[Фото] {caption_text}")
        return
        
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    base_prompt = prompt if prompt else "Напиши яркий пост для канала на основе этой фотографии."
    context = get_chat_context(chat_id)
    
    final_prompt = f"Контекст:\n{context}\n\nЗапрос от {user_name}: {base_prompt}" if context else f"Запрос от {user_name}: {base_prompt}"
        
    try:
        photo = message.photo[-1]
        file_io = io.BytesIO()
        await bot.download(photo, destination=file_io)
        image_bytes = file_io.getvalue()
        
        response = ai_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                final_prompt
            ],
            config=config
        )
        answer_text = response.text or "Не смог сгенерировать текст."
        add_to_buffer(chat_id, user_name, f"[Фото] {base_prompt}")
        add_to_buffer(chat_id, "Журналист", answer_text)
        await message.reply(answer_text, parse_mode="Markdown")
    except Exception as e:
        await message.reply(f"Ошибка связи: {e}")

@dp.message(F.text)
async def handle_text(message: types.Message):
    bot_info = await bot.get_me()
    chat_id = message.chat.id
    user_name = message.from_user.first_name or "Автор"
    text = message.text.strip()
    
    need_answer, prompt = should_respond(message, bot_info.username)
    if not need_answer:
        add_to_buffer(chat_id, user_name, text)
        return
        
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    context = get_chat_context(chat_id)
    
    reply_text = ""
    # Захват текста, на который отвечает пользователь (удобно для рерайта статей)
    if message.reply_to_message:
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        
    reply_block = f"\n[Исходный текст для обработки: «{reply_text}»]" if reply_text else ""
        
    final_prompt = f"Контекст:\n{context}\n{reply_block}\nЗадание от {user_name}: {prompt}".strip()
        
    try:
        response = ai_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=final_prompt,
            config=config
        )
        answer_text = response.text or "Не смог сгенерировать текст."
        add_to_buffer(chat_id, user_name, text)
        add_to_buffer(chat_id, "Журналист", answer_text)
        await message.reply(answer_text, parse_mode="Markdown")
    except Exception as e:
        await message.reply(f"Ошибка связи: {e}")

# Веб-сервер-заглушка для корректной работы на Render
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Kaleidoscope Bot is running!"))
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
