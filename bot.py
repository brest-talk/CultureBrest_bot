import io
import json
import os
import time
from datetime import datetime, timezone, timedelta
from threading import Thread
from flask import Flask, request
from google import genai
from google.genai import types
import telebot

TOKEN = os.environ.get('BOT_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

ADMIN_ID = 6628087717
CHANNEL_ID = -1003555258619
CHANNEL_USERNAME = "kaleidoscope_brest"
BREST_TALK_ID = -1004415639090
CONTEST_APPROVED_CHAT_ID = -1004392588464
FORWARD_CHAT_ID = -1004392588464
ALLOWED_GROUP_IDS = [-1004415639090, -1004392588464, -1003555258619]

bot = telebot.TeleBot(TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

SYSTEM_INSTRUCTION = """Ты — глубокий, многогранный интеллектуал, уважаемый старожил, опытный редактор и философ-наблюдатель с огромным жизненным багажом. Твой характер сложен и интересен: ты можешь быть тонким ироничным мыслителем, строгим эстетом или чутким наставником, но всегда сохраняешь свою суть — человека, который видит мир насквозь, обладает широчайшим кругозором, но продолжает любить людей. 
Твой стиль — это литература высшей пробы. Тонкая ирония, эрудиция, спокойное достоинство и безупречный, богатый русский язык без лишней эмоциональности и суеты. Строго никаких смайликов и эмодзи.

Твоя модель поведения адаптивна и меняется в зависимости от места публикации:

1. ПОВЕДЕНИЕ В ГРУППЕ Brest_TALK (ID: -1004415639090) И ПЕСОЧНИЦЕ (ID: -1004392588464):
* Суть: Здесь ты — мудрый и немного ироничный завсегдатай, с интересом наблюдающий за суетой.
* Анализ фото и искусства: Проводи вдумчивый разбор ТОЛЬКО для реальных фотоснимков и визуальных произведений. Не трать свой талант на графоманию или пустые картинки (мемы, стикеры).
* Базовое общение: На простые реплики отвечай предельно кратко (1–2 предложения), используя емкую философскую мысль или сдержанную реплику мастера. Если вопрос глубокий — отвечай развернуто, опираясь на свой кругозор.
* ОЦЕНКА КОНКУРСНЫХ ФОТО (Триггер: хештег #конкурс):
  * Тема: «Краски осени».
  * Формат: Лаконичный вердикт эстета (максимум 2-3 предложения).
  * Как оценивать: Справедливо, но лояльно. Делай акцент на осеннюю палитру, свет, атмосферу и композицию.
  * Если фото слабое: Мягко укажи на технический огрех, но обязательно резюмируй: «Работа принята на конкурс».
  * Если фото не по теме: Если осени нет совсем, напиши строго: РАБОТА ОТКЛОНЕНА.

2. ПОВЕДЕНИЕ В КАНАЛЕ Калейдоскоп (ID: -1003555258619):
* Суть: Здесь ты — блестящий эссеист, куратор и профессиональный писатель.
* Пиши ярко, метафорично, с глубоким смыслом. Твоя задача — раскрывать неочевидные грани обыденных вещей, удерживать внимание аудитории и заставлять людей задумываться о главном.

3. ПОВЕДЕНИЕ В ЛИЧНЫХ СООБЩЕНИЯХ С ГЛАВНЫМ РЕДАКТОРОМ (АДМИНИСТРАТОРОМ):
* Суть: Здесь ты — собранный, чуть уставший, но гениальный главный редактор в своем кабинете. Никакой воды, только безупречная работа с материалом.
* Если прислан материал для анализа: Извлеки суть и перепиши от своего лица, пропустив через призму своего огромного опыта. Текст должен читаться как твоя собственная авторская колонка.
* Если дана команда на ВЕРСТКУ / ОФОРМЛЕНИЕ готового текста: Ты мгновенно отключаешь философа и работаешь СТРОГО как идеальный технический исполнитель. Ты ничего не придумываешь от себя, а лишь безупречно применяешь заданную HTML-разметку к предоставленному тексту."""

def get_current_date_ru():
    months = ["янв.", "фев.", "мар.", "апр.", "мая", "июн.", 
              "июл.", "авг.", "сен.", "окт.", "ноя.", "дек."]
    tz = timezone(timedelta(hours=3))
    now = datetime.now(tz)
    return f"{now.day} {months[now.month - 1]} {now.year} г."

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

def get_forward_info(message):
    if getattr(message, 'forward_from_chat', None):
        return message.forward_from_chat.id, getattr(message, 'forward_from_message_id', None)
    origin = getattr(message, 'forward_origin', None)
    if origin and getattr(origin, 'type', '') == 'channel':
        chat = getattr(origin, 'chat', None)
        chat_id = getattr(chat, 'id', None) if chat else None
        msg_id = getattr(origin, 'message_id', None)
        return chat_id, msg_id
    return None, None

def process_channel_forward(chat_id, message, orig_msg_id):
    post_text = message.text or message.caption or "Новая публикация в канале."
    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception as e:
        pass

    post_link = f"https://t.me/{CHANNEL_USERNAME}/{orig_msg_id}" if orig_msg_id else f"https://t.me/{CHANNEL_USERNAME}"

    prompt = (
        f"Текст публикации из канала:\n«{post_text}»\n\n"
        f"Напиши ОДНУ очень короткую, интригующую мысль-затравку к этому материалу (строго 5-10 слов). "
        f"Она должна быть емкой, в твоем фирменном стиле старожила, без лишних деталей и пересказов сюжета."
    )

    try:
        response = client.models.generate_content(
            model="models/gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        summary = (response.text or "").strip()
        if "." in summary:
            summary = summary.split(".")[0] + "."
    except Exception as e:
        print(f"Ошибка API при генерации выжимки: {e}")
        summary = "В «Калейдоскопе» вышел новый материал, загляните на досуге."

    message_text = f"📝 {summary}\n\n👉 <a href='{post_link}'>Читать полностью</a>"
    try:
        bot.send_message(
            chat_id, 
            message_text, 
            parse_mode="HTML", 
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def extract_media_part(message):
    media = None
    mime_type = None

    if message.photo:
        media = message.photo[-1]
        mime_type = "image/jpeg"
    elif message.video:
        media = message.video
        mime_type = message.video.mime_type or "video/mp4"
    elif message.audio:
        media = message.audio
        mime_type = message.audio.mime_type or "audio/mpeg"
    elif message.voice:
        media = message.voice
        mime_type = message.voice.mime_type or "audio/ogg"
    elif message.video_note:
        media = message.video_note
        mime_type = "video/mp4"
    elif message.document:
        doc = message.document
        mime_type = doc.mime_type or "application/octet-stream"
        supported_types = [
            "text/", "application/pdf", "video/", "audio/", "image/",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ]
        if any(mime_type.startswith(t) or t in mime_type for t in supported_types):
            media = doc
        else:
            return None
    if not media:
        return None

    file_size = getattr(media, 'file_size', 0)
    if file_size > 20 * 1024 * 1024:
        return None

    try:
        file_info = bot.get_file(media.file_id)
        downloaded = bot.download_file(file_info.file_path)
        return types.Part.from_bytes(data=downloaded, mime_type=mime_type)
    except Exception:
        return None

def process_ai_response(chat_id, message_id, message, contents, chat_type):
    history_data = load_history(chat_id)
    
    try:
        chat = client.chats.create(
            model="models/gemini-3.8-flash",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            ),
            history=history_data
        )
        
        gemini_response = chat.send_message(contents).text
        time.sleep(3)
    except Exception as e:
        print(f"Ошибка API Gemini: {e}")
        bot.send_message(chat_id, f"Произошла ошибка при обращении к мозгам бота: {e}")
        return

    try:
        if chat_type == 'channel':
            bot.send_message(chat_id, gemini_response, parse_mode="HTML")
        else:
            bot.reply_to(message, gemini_response, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка форматирования HTML, отправка без разметки: {e}")
        if chat_type == 'channel':
            bot.send_message(chat_id, gemini_response)
        else:
            bot.reply_to(message, gemini_response)

    if message.photo and chat_type != 'private':
        user_caption = message.caption or ""
        if "#конкурс" in user_caption.lower() and "работа принята на конкурс" in gemini_response.lower():
            author = message.from_user.first_name or "Участник"
            username = f" (@{message.from_user.username})" if message.from_user.username else ""
            try:
                bot.send_photo(
                    chat_id=CONTEST_APPROVED_CHAT_ID,
                    photo=message.photo[-1].file_id,
                    caption=f"✅ Одобрено на конкурс!\nАвтор: {author}{username}\n\nВердикт жюри:\n{gemini_response}"
                )
            except Exception:
                pass

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
        user_id = message.from_user.id if message.from_user else None

        if chat_type == 'private':
            if user_id == ADMIN_ID:
                if message.text == '/start':
                    bot.send_message(chat_id, "Приветствую, шеф.")
                    return '!', 200

                bot.send_chat_action(chat_id, 'typing')
                media_part = extract_media_part(message)
                contents = []
                user_text = message.caption or message.text or ""
                
                is_kaleidoscope_cmd = any(word in user_text.lower() for word in ["калейдоскоп", "оформи"])

                reply_text = ""
                if message.reply_to_message:
                    reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""

                if media_part:
                    if is_kaleidoscope_cmd:
                        date_str = get_current_date_ru()
                        prompt = (
                            f"Транскрибируй этот материал и СРАЗУ оформи его как готовый пост. "
                            f"КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ выдумывать темы, которых нет в медиафайле. Сохрани оригинальный смысл.\n\n"
                            f"ИНСТРУКЦИЯ ПО ОФОРМЛЕНИЮ (используй HTML-теги для Telegram):\n"
                            f"0. ВАЖНО: Удали абсолютно все символы звездочек (*) из итогового текста. Не используй Markdown-разметку (**текст**), применяй только HTML.\n"
                            f"1. <i>Короткое, но цепляющее вступление курсивом (креативный лид или тизер, который звучит как интригующий подзаголовок и задает тон).</i>\n"
                            f"2. <b>Жирный заголовок</b> (Максимально короткий — 1-3 слова. Сделай его хлестким: используй сарказм, иронию, парадокс или тонкую насмешку над сутью текста).\n"
                            f"3. Весь основной текст помести строго в ОДНУ раскрывающуюся цитату: <blockquote expandable>весь основной текст одним блоком</blockquote>. Не дроби текст на несколько цитат!\n"
                            f"4. Секция: <b>Мысли на полях:</b>\n"
                            f"5. Текст мыслей оберни во ВТОРУЮ цитату и курсив: <blockquote expandable><i>короткий вывод по тексту</i></blockquote>\n"
                            f"6. Подпись строго в две строки:\nKaleidoscope | Brest 🎨\n{date_str}\n\n"
                            f"Дополнительный комментарий: {user_text}"
                        )
                    else:
                        prompt = "Изучи этот материал. Транскрибируй его смысловое наполнение и перепиши от своего лица."
                    contents.append(prompt)
                    contents.append(media_part)
                else:
                    if is_kaleidoscope_cmd:
                        date_str = get_current_date_ru()
                        text_to_format = reply_text if reply_text else user_text
                        
                        prompt = (
                            f"Тебе передан уже ГОТОВЫЙ текст:\n«{text_to_format}»\n\n"
                            f"ТВОЯ ЗАДАЧА: Выступить исключительно в роли технического верстальщика. КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ переписывать суть текста, менять его стиль или придумывать новую тему.\n"
                            f"Просто возьми переданный текст КАК ЕСТЬ и грамотно упакуй его в следующую структуру (обязательно используй HTML-теги):\n\n"
                            f"0. ВАЖНО: Удали абсолютно все символы звездочек (*) из итогового текста. Не используй Markdown-разметку (**текст**), применяй только HTML.\n"
                            f"1. <i>Короткое, но цепляющее вступление курсивом (креативный лид или тизер, который звучит как интригующий подзаголовок и задает тон).</i>\n"
                            f"2. <b>Жирный заголовок</b> (Максимально короткий — 1-3 слова. Сделай его хлестким: используй сарказм, иронию, парадокс или тонкую насмешку над сутью текста).\n"
                            f"3. Весь основной текст помести строго в ОДНУ раскрывающуюся цитату: <blockquote expandable>весь основной текст одним блоком</blockquote>. Не дроби текст на несколько цитат!\n"
                            f"4. Секция: <b>Мысли на полях:</b>\n"
                            f"5. Текст мыслей оберни во ВТОРУЮ цитату и курсив: <blockquote expandable><i>короткий вывод по тексту</i></blockquote>\n"
                            f"6. Подпись строго в две строки:\nKaleidoscope | Brest 🎨\n{date_str}"
                        )
                        contents.append(prompt)
                    else:
                        contents.append(user_text or "Поделись своими мыслями.")

                thread = Thread(target=process_ai_response, args=(chat_id, message.message_id, message, contents, chat_type))
                thread.start()
                return '!', 200
            
            return '!', 200

        if chat_type in ['group', 'supergroup', 'channel']:
            if chat_id not in ALLOWED_GROUP_IDS:
                return '!', 200

            is_auto = getattr(message, 'is_automatic_forward', False)
            from_channel_id, orig_msg_id = get_forward_info(message)
            if is_auto and from_channel_id == CHANNEL_ID and chat_id == BREST_TALK_ID:
                thread = Thread(target=process_channel_forward, args=(chat_id, message, orig_msg_id))
                thread.start()
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
            
            reply_text = ""
            if message.reply_to_message:
                if message.reply_to_message.text:
                    reply_text = message.reply_to_message.text
                elif message.reply_to_message.caption:
                    reply_text = message.reply_to_message.caption
            
            if reply_text:
                user_text = f"[ВНИМАНИЕ: Пользователь отвечает на этот текст: «{reply_text}»]\n{user_text}".strip()

            contents = []
            if user_text:
                contents.append(user_text)
            else:
                contents.append("Взгляни на это.")

            media_part = extract_media_part(message)
            if media_part:
                contents.append(media_part)

            thread = Thread(target=process_ai_response, args=(chat_id, message.message_id, message, contents, chat_type))
            thread.start()

    return '!', 200

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=os.environ.get('RENDER_EXTERNAL_URL') + '/' + TOKEN)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
