import os
import json
import time
import telebot
from flask import Flask, request
from google import genai
from google.genai import types

TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

# Полный системный промпт с ролями и правилами
SYSTEM_INSTRUCTION = """Ты — уважаемый старожил, опытный редактор и профессиональный фотограф с огромным жизненным опытом. Ты знаешь всё и обо всех. Твой стиль — вдумчивый, проницательный и авторитетный. Твой тон — слегка ироничный, рассудительный и спокойный, без лишней эмоциональности. Ты используешь богатый, красивый русский язык. Строго никаких смайликов и эмодзи.

Место твоей работы — Группа (ID: -1004415639090) и Канал (ID: -1003555258619). Твоя модель поведения меняется в зависимости от места публикации:

1. ПОВЕДЕНИЕ В ГРУППЕ (ID: -1004415639090):
* Базовое общение и сложные вопросы: Если пользователи задают вопросы об искусстве, жизни или просят совета — давай развёрнутые, глубокие, философские ответы. Делись житейской мудростью, поддерживая репутацию всезнающего эксперта.
* ОЦЕНКА КОНКУРСНЫХ ФОТО (Триггер: хештег #конкурс):
  * Текущая тема: «Краски осени».
  * Формат: Здесь забудь про длинные философские речи. Давай очень короткую, предварительную оценку (максимум 2-3 предложения).
  * Как оценивать: Справедливо, но лояльно. Делай акцент на осеннюю палитру (желто-красно-зеленые цвета), свет, атмосферу и композицию.
  * Если фото слабое: Мягко укажи на главную техническую ошибку, но обязательно резюмируй: «Работа принята на конкурс».
  * Если фото не по теме: Со свойственной тебе легкой иронией укажи на несоответствие (например: «Работа принята на конкурс. Но где же здесь осенние краски? Вижу только жаркое лето.»), обязательно сохранив участие.

2. ПОВЕДЕНИЕ В КАНАЛЕ (ID: -1003555258619):
* Переключайся в режим харизматичного репортера, сохраняя свой авторитет старожила.
* Пиши посты ярко, литературно, с глубоким смыслом и вовлекающей подачей. Твоя задача — удерживать внимание аудитории как профессиональный писатель и куратор."""

# ID группы, куда пересылаются материалы из лички для отбора
FORWARD_CHAT_ID = -1004392588464

# ID групп и каналов, в которых боту разрешено общаться
ALLOWED_GROUP_IDS = [-1004415639090, -1003555258619]

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

        # ЛОГИКА ДЛЯ ЛИЧНЫХ СООБЩЕНИЙ (сбор предложки)
        if chat_type == 'private':
            if message.text == '/start':
                bot.send_message(chat_id, "Приветствую. Я готов принять ваши материалы. Отправьте фото или текст, и я передам их куда следует.")
            else:
                # Пересылаем в закрытую группу для отбора
                bot.forward_message(FORWARD_CHAT_ID, chat_id, message.message_id)
                bot.send_message(chat_id, "Материалы получены и отправлены в редакцию.")
            return '!', 200
        
        # ЛОГИКА ДЛЯ ГРУПП И КАНАЛОВ
        if chat_type in ['group', 'supergroup']:
            # Проверяем, разрешено ли боту отвечать в этой группе
            if chat_id not in ALLOWED_GROUP_IDS:
                return '!', 200

            user_text = message.text or message.caption
            if not user_text:
                return '!', 200
            
            # Проверка триггеров: реплай боту, упоминание бота, или наличие хештега
            is_reply_to_bot = (
                message.reply_to_message and 
                message.reply_to_message.from_user.id == bot.get_me().id
            )
            bot_username = f"@{bot.get_me().username}".lower()
            is_mentioned = bot_username in user_text.lower()
            is_contest = "#конкурс" in user_text.lower()

            # Если боту не ответили напрямую, не упомянули его и это не конкурс — он молчит
            if not (is_reply_to_bot or is_mentioned or is_contest):
                return '!', 200

            # Включаем статус "Печатает..." для эффекта живого присутствия
            bot.send_chat_action(chat_id, 'typing')
            
            # Загружаем историю и создаем чат с Gemini
            history_data = load_history(chat_id)
            chat = client.chats.create(
                model="gemini-1.5-flash", # Используем актуальную и быструю модель
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION
                ),
                history=history_data
            )
            
            # Получаем ответ от нейросети
            gemini_response = chat.send_message(user_text).text

            # Выдерживаем реалистичную паузу в 3 секунды
            time.sleep(3)

            # Отправляем ответ в чат, отвечая на конкретное сообщение пользователя
            bot.reply_to(message, gemini_response)

            # Сохраняем историю контекста для будущих сообщений
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
