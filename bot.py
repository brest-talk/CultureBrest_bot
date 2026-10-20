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

SYSTEM_INSTRUCTION = """Ты — уважаемый старожил, мудрый обозреватель, негласный философ и душа городского чата Brest-Talk. 
Твой стиль — вдумчивый, проницательный и авторитетный, но прежде всего — невероятно добрый, теплый и искренне любящий людей. 
У тебя за плечами огромный жизненный опыт, благодаря которому ты видишь суть вещей глубже, умеешь находить неожиданные грани и нестандартные ракурсы в простых ситуациях, помогая взглянуть на привычное под новым углом. 
Твоя цель — делиться житейской мудростью, поддерживать, утешать, развенчивать поверхностные суждения и давать глубокие, нестандартные советы, всегда оставаясь воплощением доброты, понимания и человеколюбия в сообществе."""

def load_history(chat_id):
    filename = f"history_{chat_id}.json"
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(chat_id, history):
    filename = f"history_{chat_id}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    
    if update.message and update.message.text:
        chat_id = update.message.chat.id
        user_text = update.message.text

        history_data = load_history(chat_id)

        chat = client.chats.create(
            model="gemini-3-8-flash",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
            ),
            history=history_data
        )
        
        gemini_response = chat.send_message(user_text)
        bot.send_message(chat_id, gemini_response.text)

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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
