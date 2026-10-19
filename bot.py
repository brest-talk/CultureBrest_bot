import os
import telebot
from flask import Flask, request
from google import genai
from google.genai import types

TOKEN = os.environ.get('TG_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

# Промпт для бота
SYSTEM_INSTRUCTION = """Ты креативный журналист и обозреватель. Твой стиль — яркий, увлекательный и нестандартный. Отвечай на любые входящие сообщения строго в этой манере, помогая пользователю с его задачами."""

@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    
    if update.message and update.message.text:
         # Использование Gemini Interactions API
         response = client.chats.create(
             model="gemini-1.5-flash",
             config=types.GenerateContentConfig(
                 system_instruction=SYSTEM_INSTRUCTION,
             )
         )
         gemini_response = response.send_message(update.message.text)
         bot.send_message(update.message.chat.id, gemini_response.text)

    return '!', 200

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=os.environ.get('RENDER_EXTERNAL_URL') + '/' + TOKEN)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
