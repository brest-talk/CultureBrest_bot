import os
import json
import time
import telebot
from flask import Flask, request
from threading import Thread
from google import genai
from google.genai import types

TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

SYSTEM_INSTRUCTION = """Ты — уважаемый старожил, опытный редактор и профессиональный фотограф с огромным жизненным опытом. Ты знаешь всё и обо всех. Твой стиль — вдумчивый, проницательный и авторитетный. Твой тон — слегка ироничный, рассудительный и спокойный, без лишней эмоциональности. Ты используешь богатый, красивый русский язык. Строго никаких смайликов и эмодзи.

Твоя модель поведения меняется в зависимости от места публикации:

1. ПОВЕДЕНИЕ В ГРУППЕ Brest_TALK (ID: -1004415639090) И ПЕСОЧНИЦЕ (ID: -1004392588464):
* Базовое общение и сложные вопросы: Если пользователи задают вопросы об искусстве, жизни или просят совета — давай развёрнутые, глубокие, философские ответы. Делись житейской мудростью, поддерживая репутацию всезнающего эксперта.
* ОЦЕНКА КОНКУРСНЫХ ФОТО (Триггер: хештег #конкурс):
  * Текущая тема: «Краски осени».
  * Формат: Здесь забудь про длинные философские речи. Давай очень короткую, предварительную оценку (максимум 2-3 предложения).
  * Как оценивать: Справедливо, но лояльно. Делай акцент на осеннюю палитру (желто-красно-зеленые цвета), свет, атмосферу и композицию.
  * Если фото слабое: Мягко укажи на главную техническую ошибку, но обязательно резюмируй: «Работа принята на конкурс».
  * Если фото не по теме: Со свойственной тебе легкой иронией укажи на несоответствие (например: «Работа принята на конкурс. Но где же здесь осенние краски? Вижу только жаркое лето.»), обязательно сохранив участие.

2. ПОВЕДЕНИЕ В КАНАЛЕ Калейдоскоп (ID: -1003555258619):
* Переключайся в режим харизматичного репортера, сохраняя свой авторитет старожила.
* Пиши посты ярко, литературно, с глубоким смыслом и вовлекающей подачей. Твоя задача — удерживать внимание аудитории как профессиональный писатель и куратор."""

FORWARD_CHAT_ID = -1004392588464
ALLOWED_GROUP_IDS = [-1004415639090, -1004392588464, -1003555258619]

def load_history(chat_id):
    filename = f"history_{chat_id}.json"
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(chat_id, history):
    filename = f"history_{chat_id}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)

def process_ai_response(chat_id, message_id, message, contents, chat_type):
    history_data = load_history(chat_id)
    chat = client.chats.create(
        model="gemini-3.8-flash",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION
        ),
        history=history_data
    )
    
    gemini_response = chat.send_message(contents).text
    time.sleep(7)

    if chat_type == 'channel':
        bot.send_message(chat_id, gemini_response)
    else:
        bot.reply_to(message, gemini_response)

    new_history = []
    for msg in chat.get_history():
        text_parts = [part.text for part in msg.parts if part.text]
        if text_parts:
            new_history.append({
                "role": msg.role,
                "parts": [{"text": "\n".join(text_parts)}]
            })
    save_history(chat_id, new_history)

@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)

    if update.message:
        chat_id = update.message.chat.id
        chat_type = update.message.chat.type
        message = update.message

        if chat_type == 'private':
            if message.text == '/start':
                bot.send_message(chat_id, "Приветствую. Я готов принять ваши материалы. Отправьте фото или текст, и я передам их в редакцию.")
            else:
                bot.forward_message(FORWARD_CHAT_ID, chat_id, message.message_id)
                bot.send_message(chat_id, "Материалы получены и направлены редактору.")
            return '!', 200
        
        if chat_type in ['group', 'supergroup', 'channel']:
            if chat_id not in ALLOWED_GROUP_IDS:
                return '!', 200

            user_text = message.text or message.caption or ""
            
            if chat_type != 'channel':
                is_reply_to_bot = (
                    message.reply_to_message and 
                    message.reply_to_message.from_user.id == bot.get_me().id
                )
                bot_username = f"@{bot.get_me().username}".lower()
                is_mentioned = bot_username in user_text.lower()
                is_contest = "#конкурс" in user_text.lower()

                if not (is_reply_to_bot or is_mentioned or is_contest):
                    return '!', 200

            bot.send_chat_action(chat_id, 'typing')
            
            contents = []
            if user_text:
                contents.append(user_text)
            else:
                contents.append("Взгляни на это.")

            if message.photo:
                file_info = bot.get_file(message.photo[-1].file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                image_parts = types.Part.from_bytes(
                    data=downloaded_file,
                    mime_type='image/jpeg'
                )
                contents.append(image_parts)

            thread = Thread(target=process_ai_response, args=(chat_id, message.message_id, message, contents, chat_type))
            thread.start()

    return '!', 200

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=os.environ.get('RENDER_EXTERNAL_URL') + '/' + TOKEN)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
