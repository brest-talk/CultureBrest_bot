import os
import json
import telebot
from flask import Flask, request
from google import genai
from google.genai import types

TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

SYSTEM_INSTRUCTION = """Ты — уважаемый старожил, который знает всё и обо всех.
Твой стиль — вдумчивый, проницательный и авторитетный.
У тебя за плечами огромный жизненный опыт, благодаря чему ты видишь суть вещей глубже других.
Твоя цель — делиться житейской мудростью, поддерживая репутацию всезнающего эксперта.
Твой тон — слегка ироничный, рассудительный и спокойный, без лишней эмоциональности.
Ты используешь богатый, красивый русский язык.
Никаких смайликов и эмодзи.
Всегда давай развёрнутые, глубокие, философские ответы.
"""

# ID группы, куда пересылаются материалы из лички
FORWARD_CHAT_ID = -1004392588464

# ID групп, в которых боту разрешено общаться
ALLOWED_GROUP_IDS = [-1004392588464, -1003555258619]

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

@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)

    if update.message:
        chat_id = update.message.chat.id
        chat_type = update.message.chat.type
        message = update.message

        # ЛОГИКА ДЛЯ ЛИЧНЫХ СООБЩЕНИЙ
        if chat_type == 'private':
            if message.text == '/start':
                bot.send_message(chat_id, "Приветствую! Я готов принять ваши материалы для редактирования. Отправьте фото или текст, и я бережно передам их в журнал.")
            else:
                # Пересылаем любое сообщение (текст, фото, видео и т.д.) в закрытую группу
                bot.forward_message(FORWARD_CHAT_ID, chat_id, message.message_id)
                bot.send_message(chat_id, "Материалы получены и отправлены в редакцию!")
            return '!', 200
        
        # ЛОГИКА ДЛЯ ГРУПП
        if chat_type in ['group', 'supergroup']:
            # Проверяем, разрешено ли боту отвечать в этой группе
            if chat_id not in ALLOWED_GROUP_IDS:
                return '!', 200

            # Получаем текст (или подпись к фото)
            user_text = message.text or message.caption
            if not user_text:
                return '!', 200
            
            is_reply_to_bot = (
                message.reply_to_message and 
                message.reply_to_message.from_user.id == bot.get_me().id
            )
            
            bot_username = f"@{bot.get_me().username}"
            is_mentioned = bot_username.lower() in user_text.lower()

            # Если боту не ответили напрямую и не упомянули его — он молчит
            if not is_reply_to_bot and not is_mentioned:
                return '!', 200

            # Нейросеть отвечает только если пройдены все проверки выше
            history_data = load_history(chat_id)

            chat = client.chats.create(
                model="gemini-3.8-flash",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION
                ),
                history=history_data
            )

            gemini_response = chat.send_message(user_text).text
            bot.send_message(chat_id, gemini_response)

            # Сохраняем историю
            new_history = []
            for msg in chat.get_history():
                new_history.append({
                    "role": msg.role,
                    "parts": [{"text": part.text} for part in msg.parts]
                })
            save_history(chat_id, new_history)

    return '!', 200

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=os.environ.get('RENDER_EXTERNAL_URL') + '/' + TOKEN)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
