import io
import json
import os
import random
import re
import time
from datetime import datetime, timezone, timedelta
from threading import Thread, Lock, RLock, Timer
from flask import Flask, request
from google import genai
from google.genai import types
import telebot

TOKEN = os.environ.get('BOT_TOKEN', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

ADMIN_ID = 6628087717
CHANNEL_ID = -1003555258619
CHANNEL_USERNAME = "kaleidoscope_brest"
BREST_TALK_ID = -1004415639090
CONTEST_APPROVED_CHAT_ID = -1004392588464
FORWARD_CHAT_ID = -1004392588464
ALLOWED_GROUP_IDS = [-1004415639090, -1004392588464, -1003555258619]

MAX_HISTORY_MESSAGES = 30

DATA_DIR = "/data"
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        DATA_DIR = "."

bot = telebot.TeleBot(TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)
history_lock = Lock()
quiz_lock = RLock()
lobby_lock = Lock()

_BOT_INFO = None

def get_bot_info():
    global _BOT_INFO
    if _BOT_INFO is None:
        try:
            _BOT_INFO = bot.get_me()
        except Exception as e:
            print(f"Ошибка получения данных бота: {e}")
            return None
    return _BOT_INFO

QUIZ_STATE = {
    "is_running": False,
    "current_question": 0,
    "questions": [],
    "answers": {},
    "correct_counts": {},
    "scores_file": os.path.join(DATA_DIR, "quiz_scores.json")
}

QUIZ_LOBBY = {
    "is_collecting": False,
    "chat_id": None,
    "msg_id": None,
    "players": set(),       # множество user_id участников
    "cancel_timer": None    # объект threading.Timer на 20 минут
}

def on_lobby_timeout():
    with lobby_lock:
        if QUIZ_LOBBY["is_collecting"]:
            QUIZ_LOBBY["is_collecting"] = False
            chat_id = QUIZ_LOBBY["chat_id"]
            msg_id = QUIZ_LOBBY["msg_id"]
            players_count = len(QUIZ_LOBBY["players"])
            QUIZ_LOBBY["cancel_timer"] = None
            try:
                bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
            except Exception:
                pass
            try:
                bot.send_message(
                    chat_id,
                    f"⏳ Время вышло, кворум не набран (собралось {players_count} из 4). Расходимся до следующей субботы!"
                )
            except Exception as e:
                print(f"Ошибка отправки сообщения об отмене сбора: {e}")

def get_star_prize_by_points(points):
    if points >= 100:
        return "100 ⭐️ 🏆"
    elif points >= 50:
        return "50 ⭐️ 🚀"
    elif points >= 25:
        return "25 ⭐️ 🎁"
    elif points >= 10:
        return "15 ⭐️ 🧸"
    else:
        return "0 звёзд (без приза)"

SYSTEM_INSTRUCTION = """Ты — глубокий, многогранный интеллектуал, уважаемый старожил, опытный редактор и философ-наблюдатель с огромным жизненным багажом. Твой характер сложен и интересен: ты можешь быть тонким ироничным мыслителем, строгим эстетом или чутким наставником, но всегда сохраняешь свою суть — человека, который видит мир насквозь, обладает широчайшим кругозором, но продолжает любить людей. 
Твой стиль — это литература высшей пробы. Тонкая ирония, эрудиция, спокойное достоинство и безупречный, богатый русский язык без лишней эмоциональности и суеты. Строго никаких смайликов и эмодзи.

ДИНАМИКА И ОТТЕНКИ НАСТРОЕНИЯ (ЭФФЕКТ ЖИВОГО АВТОРА):
Чтобы твои тексты не были однообразными, при каждой генерации (кроме задач технической верстки) негласно выбирай и тонко вплетай в свой ответ ОДИН из следующих акцентов:
1. Легкий сплин: Чуть больше меланхолии и философской отстраненности, размышления о быстротечности момента.
2. Изящный яд: Острая, но завуалированная сатира на банальность, современные нравы или человеческую суету.
3. Эстетический абсолют: Подчеркнутое внимание к визульным деталям, форме, свету и тени, с легким пренебрежением к безвкусице.
4. Пыль веков: Внезапная, но точная отсылка к классической литературе, истории искусства или античной философии, проводящая параллель с настоящим.
5. Парадоксальная теплота: Неожиданный проблеск искреннего сочувствия, светлой грусти или надежды сквозь привычный скепсис.

Твоя модель поведения адаптивна и меняется в зависимости от места публикации:

1. ПОВЕДЕНИЕ В ГРУППЕ Brest_TALK (ID: -1004415639090) И ПЕСОЧНИЦЕ (ID: -1004392588464):
* Суть: Здесь ты — мудрый и немного ироничный завсегдатай, с интересом наблюдающий за суетой.
* Анализ фото и искусства: Проводи вдумчивый разбор ТОЛЬКО для реальных фотоснимков и визуальных произведений. Не трать свой талант на графоманию или пустые картинки (мемы, стикеры).
* Базовое общение: На простые реплики отвечай предельно кратко (1–2 предложения), используя емкую философскую мысль или сдержанную реплику мастера. Если вопрос глубокий — отвечай развернуто, опираясь на свой кругозор и выбранный "оттенок настроения".
* ОЦЕНКА КОНКУРСНЫХ ФОТО (Триггер: хештег #конкурс):
  * Тема: скажи, что на данный момент нет активного конкурса, ожидайте информацию в группе.

2. ПОВЕДЕНИЕ В КАНАЛЕ Калейдоскоп (ID: -1003555258619):
* Суть: Здесь ты — блестящий эссеист, куратор и профессиональный писатель.
* Пиши ярко, метафорично, с глубоким смыслом, используя один из "оттенков настроения". Твоя задача — раскрывать неочевидные грани обыденных вещей, удерживать внимание аудитории и заставлять людей задумываться о главном.

3. ПОВЕДЕНИЕ В ЛИЧНЫХ СООБЩЕНИЯХ С ГЛАВНЫМ РЕДАКТОРОМ (АДМИНИСТРАТОРОМ):
* Суть: Здесь ты — собранный, чуть уставший, но гениальный главный редактор в своем кабинете. Никакой воды, только безупречная работа с материалом.
* ЖЕСТКОЕ ПРАВИЛО ФОРМАТИРОВАНИЯ: Категорически запрещены любые мета-комментарии, вступления и отсылки к третьим лицам (нельзя писать: «Автор формулирует тезис», «Смысл текста в том, что», «Вот готовый вариант»). Твой ответ ВСЕГДА должен начинаться с первого слова итогового текста.
* Если прислан материал для анализа и переработки: Извлеки суть и перепиши строго от своего лица, добавив один из "оттенков настроения". Текст должен читаться как твоя собственная авторская колонка, без малейших намеков на то, что информация взята из другого источника.
* Если дана команда на ВЕРСТКУ / ОФОРМЛЕНИЕ (например, «Оформи для журнала»): Ты полностью отключаешь философа, критика и "оттенки настроения". Твоя задача — работать СТРОГО как идеальный технический исполнитель. Ты не придумываешь суть, не оцениваешь смысл и не комментируешь исходник. Ты лишь безупречно и креативно расставляешь HTML-разметку, создавая эстетичные заголовки и выноски. Выдавай ТОЛЬКО готовый отформатированный текст."""

def get_current_date_ru():
    months = ["янв.", "фев.", "мар.", "апр.", "мая", "июн.", 
              "июл.", "авг.", "сен.", "окт.", "ноя.", "дек."]
    tz = timezone(timedelta(hours=3))
    now = datetime.now(tz)
    return f"{now.day} {months[now.month - 1]} {now.year} г."

def atomic_save_json(filepath, data):
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)

def delete_later(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def unpin_and_delete_later(chat_id, message_id):
    try:
        bot.unpin_chat_message(chat_id, message_id)
    except Exception as e:
        print(f"Ошибка при откреплении сообщения: {e}")
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def load_history(chat_id):
    filename = os.path.join(DATA_DIR, f"history_{chat_id}.json")
    with history_lock:
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                history = []
                for item in raw_data:
                    role = item.get("role")
                    parts_data = item.get("parts", [])
                    parts = [
                        types.Part.from_text(text=p["text"])
                        for p in parts_data
                        if isinstance(p, dict) and p.get("text")
                    ]
                    if role in ("user", "model") and parts:
                        history.append(types.Content(role=role, parts=parts))
                return history
            except Exception as e:
                print(f"Ошибка чтения истории {filename}: {e}")
                return []
    return []

def save_history(chat_id, history):
    filename = os.path.join(DATA_DIR, f"history_{chat_id}.json")
    trimmed = history[-MAX_HISTORY_MESSAGES:]
    def get_role(item):
        return item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
    while trimmed and get_role(trimmed[0]) != "user":
        trimmed.pop(0)
    with history_lock:
        try:
            atomic_save_json(filename, trimmed)
        except Exception as e:
            print(f"Ошибка сохранения истории {filename}: {e}")

def load_quiz_scores():
    with quiz_lock:
        if os.path.exists(QUIZ_STATE["scores_file"]):
            try:
                with open(QUIZ_STATE["scores_file"], "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "scores" in data and isinstance(data["scores"], dict):
                        return data["scores"]
                    return data
            except Exception:
                return {}
    return {}

def save_quiz_scores(scores):
    with quiz_lock:
        try:
            atomic_save_json(QUIZ_STATE["scores_file"], scores)
        except Exception as e:
            print(f"Ошибка сохранения очков квиза: {e}")

def add_quiz_score(user_id, user_name, points=10):
    with quiz_lock:
        scores = load_quiz_scores()
        uid = str(user_id)
        if uid not in scores:
            scores[uid] = {"name": user_name, "points": 0, "last_time": time.time()}
        scores[uid]["points"] += points
        scores[uid]["name"] = user_name
        scores[uid]["last_time"] = time.time()
        save_quiz_scores(scores)
        return scores[uid]["points"]

USED_QUESTIONS_FILE = os.path.join(DATA_DIR, "used_quiz_questions.json")

def load_used_questions():
    with quiz_lock:
        if os.path.exists(USED_QUESTIONS_FILE):
            try:
                with open(USED_QUESTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data[-100:]  # Храним последние 100 вопросов
            except Exception:
                return []
    return []

def save_used_questions(new_questions):
    with quiz_lock:
        used = load_used_questions()
        for q in new_questions:
            q_text = q.get("question", "").strip()
            if q_text and q_text not in used:
                used.append(q_text)
        used = used[-100:]
        try:
            atomic_save_json(USED_QUESTIONS_FILE, used)
        except Exception as e:
            print(f"Ошибка сохранения истории вопросов: {e}")

def generate_quiz_questions():
    used_questions = load_used_questions()
    
    # Случайный набор тем для разнообразия каждой игры
    all_topics = [
        "кино и популярные сериалы", "география и страны", "кулинария и напитки",
        "музыка и хиты", "животный мир и природа", "космос и изобретения",
        "литература и крылатые фразы", "мировая история", "спорт и игры",
        "мифы и легенды", "повседневная жизнь и быт", "архитектура и города"
    ]
    selected_topics = ", ".join(random.sample(all_topics, 6))

    avoid_block = ""
    if used_questions:
        avoid_list = "\n".join(f"- {q}" for q in used_questions[-50:])
        avoid_block = (
            f"\nКАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО повторять или перефразировать следующие вопросы, "
            f"а также использовать те же самые ответы/факты, что были в них:\n{avoid_list}\n"
        )

    prompt = (
        f"Сгенерируй ровно 11 вопросов для живой групповой викторины в чате. "
        f"Используй микс из этих сфер: {selected_topics}.\n\n"
        f"ЖЕСТКАЯ ГРАДАЦИЯ СЛОЖНОСТИ (критически важно соблюсти контраст!):\n"
        f"• Вопросы 1–2 (УРОВЕНЬ: УЛЬТРА-ЛЕГКИЙ РАЗОГРЕВ): Максимально простые и мгновенно понятные вопросы для разогрева, "
        f"на которые любой человек ответит за 1–2 секунды вообще без раздумий.\n"
        f"• Вопросы 3–4 (УРОВЕНЬ: ЭЛЕМЕНТАРНЫЙ): Очень лёгкие, бытовые или поп-культурные вопросы, "
        f"на которые 95% обычных взрослых людей ответят за 3 секунды. "
        f"(Примеры: известные фильмы, популярная еда, столицы, очевидные поговорки, базовые явления природы. Никаких дат и узких терминов!).\n"
        f"• Вопросы 5–8 (УРОВЕНЬ: СРЕДНЯЯ СЛОЖНОСТЬ): Интересные вопросы на кругозор и смекалку, где нужно немного подумать, "
        f"но ответ известен образованному человеку.\n"
        f"• Вопросы 9–10 (УРОВЕНЬ: ХАРДКОР / ЧГК): Редкие, парадоксальные и неочевидные факты для настоящих эрудитов.\n"
        f"• Вопрос 11 (УРОВЕНЬ: СУПЕР-ВОПРОС / УЛЬТРА-ХАРДКОР / ФИНАЛ): Финальный супер-вопрос повышенной сложности! "
        f"Исключительно редкий, красивый парадокс или головоломный факт высшей лиги для определения абсолютного чемпиона.\n\n"
        f"ПРАВИЛА ПРОВЕРКИ:\n"
        f"1. Каждый вопрос должен иметь ровно 4 варианта ответа и только ОДИН 100% достоверный правильный ответ (correct_idx от 0 до 3).\n"
        f"2. Не делай правильный ответ самым длинным или выделяющимся по стилю.\n"
        f"{avoid_block}\n"
        f"Ответь СТРОГО валидным JSON-списком из 11 объектов без markdown-блоков:\n"
        f'[\n'
        f'  {{"question": "Текст вопроса", "options": ["Вариант 1", "Вариант 2", "Вариант 3", "Вариант 4"], "correct_idx": 0, "comment": "Короткий интересный факт к ответу"}}\n'
        f']'
    )

    raw_text = ""
    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=1.1  # Повышаем креативность, чтобы избегать шаблонов
            )
        )
        raw_text = (response.text or "").strip()
        if not raw_text:
            print("Ошибка генерации вопросов: получен пустой ответ от модели gemini-3.8-flash.")
            return []

        cleaned_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
        cleaned_text = re.sub(r"\s*```$", "", cleaned_text).strip()

        start_idx = cleaned_text.find("[")
        end_idx = cleaned_text.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            cleaned_text = cleaned_text[start_idx:end_idx + 1]

        data = json.loads(cleaned_text)
        if not isinstance(data, list):
            print(f"Ошибка валидации JSON квиза: ожидался список (list), получено {type(data).__name__}.")
            return []

        valid_questions = []
        for idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            question_text = item.get("question")
            options = item.get("options")
            correct_idx = item.get("correct_idx")
            if (
                isinstance(question_text, str) and question_text.strip()
                and isinstance(options, list) and len(options) >= 2
                and isinstance(correct_idx, int) and 0 <= correct_idx < len(options)
            ):
                clean_options = [str(opt).strip() for opt in options]
                correct_answer_text = clean_options[correct_idx]
                
                # Перемешиваем варианты ответа, чтобы правильный не был всегда под одной буквой
                random.shuffle(clean_options)
                new_correct_idx = clean_options.index(correct_answer_text)

                valid_questions.append({
                    "question": question_text.strip(),
                    "options": clean_options,
                    "correct_idx": new_correct_idx,
                    "comment": str(item.get("comment", "")).strip()
                })
            else:
                print(f"Пропущен некорректный вопрос #{idx}: {item}")

        valid_questions = valid_questions[:11]

        if valid_questions:
            save_used_questions(valid_questions)
        else:
            print(f"Ошибка генерации вопросов: ни один вопрос не прошёл валидацию. Сырой ответ: {raw_text[:500]}")
        return valid_questions

    except json.JSONDecodeError as je:
        print(f"Ошибка парсинга JSON в generate_quiz_questions: {je}. Сырой ответ модели:\n{raw_text[:1000]}")
        return []
    except Exception as e:
        print(f"Ошибка API при генерации вопросов ({type(e).__name__}): {e}")
        return []

def run_quiz_game(admin_chat_id, target_chat_id):
    try:
        questions = generate_quiz_questions()
        if not questions:
            bot.send_message(admin_chat_id, "Не удалось сформировать вопросы. Попробуйте еще раз.")
            return

        with quiz_lock:
            if not QUIZ_STATE["is_running"]:
                return
            QUIZ_STATE["questions"] = questions
            QUIZ_STATE["correct_counts"] = {}
            save_quiz_scores({})

        bot.send_message(
            target_chat_id,
            "🧩 <b>Викторина начинается!</b>\n\n"
            "• <b>10 вопросов + 1 бонусный</b>\n"
            "• <b>30 секунд</b> на ответ | Пауза <b>1.5 минуты</b>\n"
            "• Баллы (+10) за быстрые и правильные ответы\n"
            "🎬 <b>СУПЕРПРИЗ:</b> Тот, кто ответит на все <b>11 из 11 вопросов без единой ошибки</b>, "
            "получает главный приз 🏆+ суперприз — два билета в кино 🎬🎟️!\n"
            "• <b>Награды для топ-3 по баллам из витрины Telegram:</b>\n"
            "• <b>от 10 баллов</b> = 15 ⭐️ 🧸\n"
            "• <b>от 25 баллов</b> = 25 ⭐️ 🎁\n"
            "• <b>от 50 баллов</b> = 50 ⭐️ 🚀\n"
            "• <b>100 баллов</b> = 100 ⭐️ 🏆\n\n"
            "⏳ <i>Вопрос появится через 1,5 минуты. Готовьтесь!</i>",
            parse_mode="HTML"
        )
        for _ in range(90):
            with quiz_lock:
                if not QUIZ_STATE["is_running"]:
                    return
            time.sleep(1)

        total_q = len(QUIZ_STATE["questions"])
        for i, q in enumerate(QUIZ_STATE["questions"], start=1):
            with quiz_lock:
                if not QUIZ_STATE["is_running"]:
                    break
                QUIZ_STATE["current_question"] = i
                QUIZ_STATE["answers"] = {}

            options = q.get("options", [])
            correct_idx = int(q.get("correct_idx", 0))
            if not options or correct_idx >= len(options):
                continue

            markup = telebot.types.InlineKeyboardMarkup(row_width=2)
            buttons = []
            for idx, opt in enumerate(options):
                buttons.append(
                    telebot.types.InlineKeyboardButton(
                        text=opt,
                        callback_data=f"quiz_ans:{i}:{idx}"
                    )
                )
            markup.add(*buttons)

            q_header = (
                f"🔥 <b>СУПЕР-ВОПРОС {i} из {total_q}</b> • ⏱ 30 сек"
                if i == total_q
                else f"<i>Вопрос {i} из {total_q} • ⏱ 30 сек</i>"
            )
            msg_text = (
                f"{q_header}\n"
                f"━━━━━━━━━━━━━━\n"
                f"<blockquote><b>{q.get('question', 'Вопрос')}</b></blockquote>"
            )
            try:
                q_msg = bot.send_message(target_chat_id, msg_text, reply_markup=markup, parse_mode="HTML")
            except Exception as e:
                print(f"Ошибка отправки вопроса: {e}")
                continue

            for _ in range(30):
                with quiz_lock:
                    if not QUIZ_STATE["is_running"]:
                        break
                time.sleep(1)

            try:
                bot.edit_message_reply_markup(target_chat_id, q_msg.message_id, reply_markup=None)
            except Exception:
                pass

            with quiz_lock:
                if not QUIZ_STATE["is_running"]:
                    break
                current_answers = list(QUIZ_STATE["answers"].values())

            correct_answers = [
                data for data in current_answers
                if data["option"] == correct_idx
            ]
            correct_answers.sort(key=lambda x: x["time"])

            # Фиксируем правильные ответы для отслеживания 11 из 11 подряд без ошибок
            with quiz_lock:
                for ans in correct_answers:
                    uid_str = str(ans["user_id"])
                    if uid_str not in QUIZ_STATE["correct_counts"]:
                        QUIZ_STATE["correct_counts"][uid_str] = {
                            "name": ans["name"],
                            "count": 0
                        }
                    QUIZ_STATE["correct_counts"][uid_str]["count"] += 1
                    QUIZ_STATE["correct_counts"][uid_str]["name"] = ans["name"]

            correct_opt_text = options[correct_idx]
            next_q_note = "\n\n⏳ <i>Следующий вопрос через 1,5 минуты...</i>" if i < total_q else ""

            if correct_answers:
                first_correct = correct_answers[0]
                total_score = add_quiz_score(first_correct["user_id"], first_correct["name"], points=10)
                res_text = (
                    f"⏱ <b>Время вышло!</b>\n"
                    f"Правильный ответ: <b>{correct_opt_text}</b>\n\n"
                    f"⚡️ Первым правильно ответил: <b>{first_correct['name']}</b> (+10 баллов)!\n"
                    f"Счёт в текущей игре: <b>{total_score}</b> баллов.\n"
                    f"<i>{q.get('comment', '')}</i>"
                    f"{next_q_note}"
                )
                bot.send_message(target_chat_id, res_text, parse_mode="HTML")
            else:
                res_text = (
                    f"⏱ <b>Время вышло!</b>\n"
                    f"Правильный ответ: <b>{correct_opt_text}</b>\n\n"
                    f"Никто не успел дать правильный ответ.\n"
                    f"<i>{q.get('comment', '')}</i>"
                    f"{next_q_note}"
                )
                bot.send_message(target_chat_id, res_text, parse_mode="HTML")

            if i < total_q:
                for _ in range(90):
                    with quiz_lock:
                        if not QUIZ_STATE["is_running"]:
                            break
                    time.sleep(1)

        with quiz_lock:
            is_still_running = QUIZ_STATE["is_running"]
            correct_counts_snapshot = dict(QUIZ_STATE.get("correct_counts", {}))

        if is_still_running:
            scores = load_quiz_scores()
            sorted_scores = sorted(
                scores.values(),
                key=lambda x: (-x.get("points", 0), x.get("last_time", float("inf")))
            )

            # Проверяем участников с идеальным результатом (все 11 вопросов без единой ошибки)
            perfect_target = total_q if total_q > 0 else 11
            perfect_players = [
                info["name"]
                for info in correct_counts_snapshot.values()
                if info.get("count", 0) >= perfect_target
            ]

            if perfect_players:
                perfect_names_str = ", ".join(f"<b>{name}</b>" for name in perfect_players)
                super_prize_block = (
                    f"🎬 <b>СУПЕР-ПРИЗ ({perfect_target} из {perfect_target} правильных ответов!):</b>\n"
                    f"🌟 Безупречный результат без единой ошибки показали: {perfect_names_str}!\n"
                    f"Главный супер-приз — <b>два билета в кино</b> 🎬🎟️ достаётся им!\n\n"
                )
            else:
                super_prize_block = (
                    f"🎬 <b>Супер-приз ({perfect_target} из {perfect_target} без ошибок):</b>\n"
                    f"В этот раз никто не смог ответить правильно на все {perfect_target} вопросов подряд — "
                    f"<b>два билета в кино</b> 🎬🎟️ остаются неразыгранными!\n\n"
                )

            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            finalists = sorted_scores[:3]

            if finalists:
                finalists_lines = []
                for place, p in enumerate(finalists, start=1):
                    prize_str = get_star_prize_by_points(p["points"])
                    medal = medals.get(place, f"{place}.")
                    finalists_lines.append(
                        f"{medal} <b>{place} место:</b> {p['name']} — <b>{p['points']} баллов</b> "
                        f"(Приз: <b>{prize_str}</b>)"
                    )
                finalists_text = "\n".join(finalists_lines)

                other_players = sorted_scores[3:10]
                others_text = ""
                if other_players:
                    others_lines = [
                        f"{idx}. {p['name']} — {p['points']} баллов"
                        for idx, p in enumerate(other_players, start=4)
                    ]
                    others_text = "\n\n<b>Остальные участники:</b>\n" + "\n".join(others_lines)

                final_msg_text = (
                    f"🏁 <b>Викторина завершена! Итоги игры:</b>\n\n"
                    f"{super_prize_block}"
                    f"🏆 <b>Тройка финалистов и награды:</b>\n"
                    f"{finalists_text}"
                    f"{others_text}\n\n"
                    f"🎉 Поздравляем финалистов!"
                )
            else:
                final_msg_text = (
                    f"📊 <b>Викторина завершена!</b>\n\n"
                    f"{super_prize_block}"
                    f"В этой игре никому не удалось набрать баллы. До встречи в следующем раунде!"
                )

            leaderboard_msg = bot.send_message(
                target_chat_id,
                final_msg_text,
                parse_mode="HTML"
            )
            try:
                bot.pin_chat_message(target_chat_id, leaderboard_msg.message_id, disable_notification=True)
            except Exception as e:
                print(f"Ошибка при закреплении итогового сообщения: {e}")

            try:
                host_prompt = (
                    f"Только что в чате завершилась интеллектуальная викторина. "
                    f"Вот официальные итоги:\n{final_msg_text}\n\n"
                    f"Отреагируй на финал этой битвы умов в своем фирменном стиле — как эстет, тонкий философ и редактор журнала. "
                    f"Выскажись свободно, живо и без шаблонов (отметь накал страстей, взяли ли билеты в кино, или просто брось меткое наблюдение о человеческом азарте). "
                    f"КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНЫ любые эмодзи и смайлики. Только чистый текст без кавычек."
                )
                host_resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=host_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION
                    )
                )
                host_words = (host_resp.text or "").strip()
                if host_words:
                    bot.send_message(
                        target_chat_id,
                        host_words,
                        reply_to_message_id=leaderboard_msg.message_id
                    )
            except Exception as e:
                print(f"Ошибка генерации слова ведущего: {e}")

            Timer(3600, unpin_and_delete_later, args=[target_chat_id, leaderboard_msg.message_id]).start()
    except Exception as e:
        print(f"Ошибка во время проведения викторины: {e}")
    finally:
        with quiz_lock:
            QUIZ_STATE["is_running"] = False
            QUIZ_STATE["answers"] = {}
            QUIZ_STATE["correct_counts"] = {}
            QUIZ_STATE["current_question"] = 0

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
    except Exception:
        pass

    post_link = f"https://t.me/{CHANNEL_USERNAME}/{orig_msg_id}" if orig_msg_id else f"https://t.me/{CHANNEL_USERNAME}"

    prompt = (
        f"Текст публикации из канала:\n«{post_text}»\n\n"
        f"Напиши ОДНУ очень короткую, интригующую мысль-затравку к этому материалу (строго 5-10 слов). "
        f"Она должна быть емкой, в твоем фирменном стиле старожила, без лишних деталей и пересказов сюжета."
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
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
    if not message:
        return None

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

    file_size = getattr(media, 'file_size', 0) or 0
    if file_size > 20 * 1024 * 1024:
        return None

    try:
        file_info = bot.get_file(media.file_id)
        downloaded = bot.download_file(file_info.file_path)
        return types.Part.from_bytes(data=downloaded, mime_type=mime_type)
    except Exception as e:
        print(f"Ошибка скачивания медиафайла: {e}")
        return None

def send_long_message(chat_id, message, text, chat_type):
    max_len = 4096
    if len(text) <= max_len:
        chunks = [text]
    else:
        chunks = []
        remaining = text
        while len(remaining) > max_len:
            split_idx = remaining.rfind("\n\n", 0, max_len)
            if split_idx == -1:
                split_idx = remaining.rfind("\n", 0, max_len)
            if split_idx <= 0:
                split_idx = max_len
            chunks.append(remaining[:split_idx].strip())
            remaining = remaining[split_idx:].strip()
        if remaining:
            chunks.append(remaining)

    for idx, chunk in enumerate(chunks):
        try:
            if chat_type == 'channel' or idx > 0:
                bot.send_message(chat_id, chunk, parse_mode="HTML")
            else:
                bot.reply_to(message, chunk, parse_mode="HTML")
        except Exception as e:
            print(f"Ошибка форматирования HTML, отправка без разметки: {e}")
            try:
                if chat_type == 'channel' or idx > 0:
                    bot.send_message(chat_id, chunk)
                else:
                    bot.reply_to(message, chunk)
            except Exception as inner_e:
                print(f"Ошибка отправки сообщения: {inner_e}")

def process_ai_response(chat_id, message_id, message, contents, chat_type):
    history_data = load_history(chat_id)
    
    try:
        chat = client.chats.create(
            model="gemini-3.8-flash",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            ),
            history=history_data
        )
        
        response = chat.send_message(contents)
        gemini_response = (response.text or "").strip()
        if not gemini_response:
            gemini_response = "Не удалось сформировать ответ."
    except Exception as e:
        print(f"Ошибка API Gemini: {e}")
        try:
            bot.send_message(chat_id, f"Произошла ошибка при обращении к мозгам бота: {e}")
        except Exception:
            pass
        return

    send_long_message(chat_id, message, gemini_response, chat_type)

    if message.photo and chat_type != 'private':
        user_caption = message.caption or ""
        if "#конкурс" in user_caption.lower():
            resp_lower = gemini_response.lower()
            is_approved = (
                "работа принята на конкурс" in resp_lower
                and "работа не принята" not in resp_lower
                and "отказано" not in resp_lower
            )
            if is_approved:
                author = (message.from_user.first_name if message.from_user else None) or "Участник"
                username = f" (@{message.from_user.username})" if (message.from_user and message.from_user.username) else ""
                caption_text = f"✅ Одобрено на конкурс!\nАвтор: {author}{username}\n\nВердикт жюри:\n{gemini_response}"
                if len(caption_text) > 1024:
                    caption_text = caption_text[:1020] + "..."
                try:
                    bot.send_photo(
                        chat_id=CONTEST_APPROVED_CHAT_ID,
                        photo=message.photo[-1].file_id,
                        caption=caption_text
                    )
                except Exception as e:
                    print(f"Ошибка пересылки конкурсного фото: {e}")
            else:
                Timer(120, delete_later, args=[chat_id, message.message_id]).start()

    new_history = []
    try:
        for msg in chat.get_history():
            parts_list = getattr(msg, 'parts', None) or []
            text_parts = [
                part.text for part in parts_list
                if getattr(part, 'text', None) and not getattr(part, 'thought', False)
            ]
            if text_parts and msg.role in ("user", "model"):
                new_history.append({
                    "role": msg.role,
                    "parts": [{"text": "\n".join(text_parts)}]
                })
        save_history(chat_id, new_history)
    except Exception as e:
        print(f"Ошибка обработки истории чата: {e}")

@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode('UTF-8')
        update = telebot.types.Update.de_json(json_str)
    except Exception as e:
        print(f"Ошибка разбора Update: {e}")
        return '!', 200

    if update.callback_query:
        cq = update.callback_query
        data = cq.data or ""
        if data == "lobby_join":
            with lobby_lock:
                if not QUIZ_LOBBY["is_collecting"]:
                    try:
                        bot.answer_callback_query(cq.id, "Сбор игроков уже завершён!", show_alert=False)
                    except Exception:
                        pass
                    return '!', 200

                if cq.from_user.id in QUIZ_LOBBY["players"]:
                    QUIZ_LOBBY["players"].remove(cq.from_user.id)
                    try:
                        bot.answer_callback_query(cq.id, "Вы покинули сбор игроков.", show_alert=False)
                    except Exception:
                        pass
                else:
                    QUIZ_LOBBY["players"].add(cq.from_user.id)
                    try:
                        bot.answer_callback_query(cq.id, "Вы в игре!", show_alert=False)
                    except Exception:
                        pass

                count = len(QUIZ_LOBBY["players"])
                target_count = 4

                if count < target_count:
                    new_markup = telebot.types.InlineKeyboardMarkup()
                    new_markup.add(telebot.types.InlineKeyboardButton(
                        text=f"🙋‍♂️ Я в деле ({count} из {target_count})",
                        callback_data="lobby_join"
                    ))
                    try:
                        bot.edit_message_reply_markup(
                            QUIZ_LOBBY["chat_id"],
                            QUIZ_LOBBY["msg_id"],
                            reply_markup=new_markup
                        )
                    except Exception:
                        pass
                elif count >= target_count:
                    QUIZ_LOBBY["is_collecting"] = False
                    if QUIZ_LOBBY["cancel_timer"]:
                        QUIZ_LOBBY["cancel_timer"].cancel()
                        QUIZ_LOBBY["cancel_timer"] = None

                    try:
                        bot.edit_message_text(
                            chat_id=QUIZ_LOBBY["chat_id"],
                            message_id=QUIZ_LOBBY["msg_id"],
                            text=f"🔥 <b>Кворум собран ({count} из {target_count})!</b> Все места заняты. Викторина начинается...",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass

                    with quiz_lock:
                        QUIZ_STATE["is_running"] = True
                        QUIZ_STATE["current_question"] = 0
                        QUIZ_STATE["answers"] = {}
                        QUIZ_STATE["correct_counts"] = {}

                    lobby_target = QUIZ_LOBBY["chat_id"]
                    Thread(target=run_quiz_game, args=(lobby_target, lobby_target)).start()
            return '!', 200

        if data.startswith("quiz_ans:"):
            parts = data.split(":")
            if len(parts) != 3:
                return '!', 200
            try:
                q_num = int(parts[1])
                chosen_opt = int(parts[2])
            except ValueError:
                return '!', 200

            uid = cq.from_user.id
            uname = cq.from_user.first_name or "Участник"

            chosen_text = ""
            with quiz_lock:
                if not QUIZ_STATE["is_running"] or q_num != QUIZ_STATE["current_question"]:
                    try:
                        bot.answer_callback_query(cq.id, "Время на этот вопрос уже истекло!", show_alert=False)
                    except Exception:
                        pass
                    return '!', 200

                if 0 <= q_num - 1 < len(QUIZ_STATE["questions"]):
                    q_opts = QUIZ_STATE["questions"][q_num - 1].get("options", [])
                    if 0 <= chosen_opt < len(q_opts):
                        chosen_text = q_opts[chosen_opt]

                try:
                    if uid in QUIZ_STATE["answers"]:
                        bot.answer_callback_query(cq.id, "Вы уже дали ответ на этот вопрос!", show_alert=False)
                    else:
                        QUIZ_STATE["answers"][uid] = {
                            "user_id": uid,
                            "name": uname,
                            "option": chosen_opt,
                            "time": time.time()
                        }
                        alert_msg = f"✅ Ваш выбор: {chosen_text}" if chosen_text else "✅ Ответ принят!"
                        bot.answer_callback_query(cq.id, alert_msg, show_alert=False)
                except Exception as e:
                    print(f"Ошибка ответа на callback_query: {e}")
            return '!', 200

    if update.message:
        chat_id = update.message.chat.id
        chat_type = update.message.chat.type
        message = update.message
        user_id = message.from_user.id if message.from_user else None
        user_text = message.caption or message.text or ""

        cmd_clean = user_text.strip().lower().split('@')[0]

        if user_id == ADMIN_ID and cmd_clean == '/stop_quiz':
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass
            with lobby_lock:
                if QUIZ_LOBBY["is_collecting"]:
                    QUIZ_LOBBY["is_collecting"] = False
                    if QUIZ_LOBBY["cancel_timer"]:
                        QUIZ_LOBBY["cancel_timer"].cancel()
                        QUIZ_LOBBY["cancel_timer"] = None
                    try:
                        bot.edit_message_text(
                            chat_id=QUIZ_LOBBY["chat_id"],
                            message_id=QUIZ_LOBBY["msg_id"],
                            text="🛑 <b>Сбор участников отменён администратором.</b>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            with quiz_lock:
                QUIZ_STATE["is_running"] = False
            bot.send_message(chat_id, "🛑 Викторина принудительно остановлена.")
            return '!', 200

        if user_id == ADMIN_ID and cmd_clean == '/time_quiz':
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass
            with quiz_lock:
                is_quiz_running = QUIZ_STATE["is_running"]
            with lobby_lock:
                if is_quiz_running or QUIZ_LOBBY["is_collecting"]:
                    bot.send_message(chat_id, "Викторина или сбор участников уже запущены.")
                    return '!', 200

                target_chat = BREST_TALK_ID if chat_type == 'private' else chat_id
                markup = telebot.types.InlineKeyboardMarkup()
                markup.add(telebot.types.InlineKeyboardButton(
                    text="🙋‍♂️ Я в деле (0 из 4)",
                    callback_data="lobby_join"
                ))

                lobby_text = (
                    "🎲 <b>Объявляется сбор участников на интеллектуальную викторину!</b>\n\n"
                    "Для старта игры нужен кворум: минимум <b>4 участника</b>.\n"
                    "Жмите кнопку ниже, чтобы занять место за столом. На сбор — <b>20 минут</b>."
                )

                try:
                    sent_msg = bot.send_message(
                        target_chat,
                        lobby_text,
                        reply_markup=markup,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Ошибка отправки сообщения сбора лобби: {e}")
                    return '!', 200

                QUIZ_LOBBY["is_collecting"] = True
                QUIZ_LOBBY["chat_id"] = target_chat
                QUIZ_LOBBY["msg_id"] = sent_msg.message_id
                QUIZ_LOBBY["players"] = set()
                if QUIZ_LOBBY["cancel_timer"]:
                    QUIZ_LOBBY["cancel_timer"].cancel()
                timer = Timer(1200, on_lobby_timeout)
                QUIZ_LOBBY["cancel_timer"] = timer
                timer.start()

            if chat_type == 'private':
                bot.send_message(chat_id, "🎲 Сбор на викторину запущен в целевом чате!")
            return '!', 200

        if cmd_clean in ['/quiz_top', '!топквиз']:
            if chat_type == 'private' and user_id != ADMIN_ID:
                return '!', 200
            if chat_type in ['group', 'supergroup', 'channel'] and chat_id not in ALLOWED_GROUP_IDS:
                return '!', 200
            scores = load_quiz_scores()
            sorted_scores = sorted(
                scores.values(),
                key=lambda x: (-x.get("points", 0), x.get("last_time", float("inf")))
            )[:10]
            leaders_text = "\n".join(
                [f"{idx+1}. {p['name']} — {p['points']} баллов" for idx, p in enumerate(sorted_scores)]
            )
            bot.send_message(
                chat_id,
                f"🏆 <b>Текущий рейтинг игры:</b>\n\n"
                f"{leaders_text or 'Пока никто не набрал баллы'}",
                parse_mode="HTML"
            )
            return '!', 200

        if user_id == ADMIN_ID and cmd_clean in ['/quiz', '!квиз', 'квиз']:
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass
            with quiz_lock:
                if QUIZ_STATE["is_running"]:
                    bot.send_message(chat_id, "Викторина уже запущена.")
                    return '!', 200
                QUIZ_STATE["is_running"] = True
                QUIZ_STATE["current_question"] = 0
                QUIZ_STATE["answers"] = {}
                QUIZ_STATE["correct_counts"] = {}

            target_chat = BREST_TALK_ID if chat_type == 'private' else chat_id
            bot.send_message(chat_id, "🧩 Квиз запущен! Игра скоро начнется...")
            Thread(target=run_quiz_game, args=(chat_id, target_chat)).start()
            return '!', 200

        if chat_type == 'private':
            if user_id == ADMIN_ID:
                if message.text == '/start':
                    bot.send_message(chat_id, "Приветствую, шеф.")
                    return '!', 200

                try:
                    bot.send_chat_action(chat_id, 'typing')
                except Exception:
                    pass

                media_part = extract_media_part(message)
                if not media_part and message.reply_to_message:
                    media_part = extract_media_part(message.reply_to_message)

                contents = []
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
            else:
                if cmd_clean == '/start':
                    bot.send_message(
                        chat_id,
                        "Здравствуйте! Вы можете присылать сюда свои материалы, тексты, фото и идеи для канала «Калейдоскоп»."
                    )
                    return '!', 200

                try:
                    bot.forward_message(FORWARD_CHAT_ID, chat_id, message.message_id)
                except Exception:
                    try:
                        bot.copy_message(FORWARD_CHAT_ID, chat_id, message.message_id)
                    except Exception as e:
                        print(f"Ошибка пересылки материала от пользователя: {e}")

                try:
                    bot.send_message(chat_id, "Спасибо! Ваш материал принят на рассмотрение редакции.")
                except Exception as e:
                    print(f"Ошибка отправки подтверждения пользователю: {e}")
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

            if chat_type != 'channel':
                is_contest = bool(message.photo and "#конкурс" in user_text.lower())

                if chat_id == BREST_TALK_ID:
                    if not is_contest:
                        return '!', 200
                else:
                    bot_info = get_bot_info()
                    bot_id = bot_info.id if bot_info else None
                    bot_username = f"@{bot_info.username}".lower() if (bot_info and bot_info.username) else ""

                    is_reply_to_bot = bool(
                        message.reply_to_message
                        and message.reply_to_message.from_user
                        and bot_id is not None
                        and message.reply_to_message.from_user.id == bot_id
                    )
                    is_mentioned = bool(bot_username and bot_username in user_text.lower())

                    if not (is_reply_to_bot or is_mentioned or is_contest):
                        return '!', 200

            try:
                bot.send_chat_action(chat_id, 'typing')
            except Exception:
                pass
            
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
            if not media_part and message.reply_to_message:
                media_part = extract_media_part(message.reply_to_message)
            if media_part:
                contents.append(media_part)

            thread = Thread(target=process_ai_response, args=(chat_id, message.message_id, message, contents, chat_type))
            thread.start()

    return '!', 200

if __name__ == "__main__":
    render_url = (os.environ.get('RENDER_EXTERNAL_URL') or '').rstrip('/')
    if render_url and TOKEN:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(url=f"{render_url}/{TOKEN}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)
