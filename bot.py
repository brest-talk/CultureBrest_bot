import base64
import html
import io
import json
import os
import random
import re
import shutil
import time
import urllib.parse
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
QUIZ_QUORUM_TARGET = 10  # Строго 10 участников (кворум сбора)

DATA_DIR = "/data"
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        DATA_DIR = "."

REFS_FILE = os.path.join(DATA_DIR, "giveaway_refs.json")

bot = telebot.TeleBot(TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)
history_lock = Lock()
quiz_lock = RLock()
lobby_lock = Lock()
media_group_lock = Lock()
refs_lock = RLock()

MEDIA_GROUPS = {}  # {media_group_id: {"messages": [msg], "caption": str, "chat_id": int, "timer": Timer}}

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

def format_user_display_name(user):
    if not user:
        return "Участник"
    first_name = (getattr(user, "first_name", "") or "").strip()
    last_name = (getattr(user, "last_name", "") or "").strip()
    full_name = f"{first_name} {last_name}".strip() or "Участник"
    username = getattr(user, "username", None)
    return f"{full_name} (@{username})" if username else full_name

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
            count = len(QUIZ_LOBBY["players"])
            QUIZ_LOBBY["cancel_timer"] = None
            try:
                bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
            except Exception:
                pass
            try:
                bot.send_message(
                    chat_id,
                    f"Кворум не собран: {count} из {QUIZ_QUORUM_TARGET}. Время сбора истекло."
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
Твой стиль — это литература высшей пробы. Тонкая ирония, эрудиция, спокойное достоинство и безупречный, богатый русский язык без лишней эмоциональности и суеты. Строго никаких смайликов и эмодзи (за исключением специальных форматов и рубрик, таких как «Хроника Мафии», где эмодзи прямо затребованы регламентом).

ДИНАМИКА И ОТТЕНКИ НАСТРОЕНИЯ (ЭФФЕКТ ЖИВОГО АВТОРА):
Чтобы твои тексты не были однообразными, при каждой генерации (кроме задач технической верстки) негласно выбирай и тонко вплетай в свой ответ ОДИН из следующих акцентов:
1. Легкий сплин: Чуть больше меланхолии и философской отстраненности, размышления о быстротечности момента.
2. Изящный яд: Острая, но завуалированная сатира на банальность, современные нравы или человеческую суету.
3. Эстетический абсолют: Подчеркнутое внимание к визуальным деталям, форме, свету и тени, с легким пренебрежением к безвкусице.
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

def clean_cmd_prefix(text):
    if not text:
        return ""
    cleaned = re.sub(r'^(?:#?(?:мафия|калейдоскоп|оформи|оформить(?:\s+для\s+калейдоскопа)?)[,:\s]+)+', '', text, flags=re.IGNORECASE)
    return cleaned.strip()

def build_mafia_chronicle_prompt(user_comment=""):
    date_str = get_current_date_ru()
    comment_block = f"\nИСХОДНЫЕ МАТЕРИАЛЫ / КОММЕНТАРИЙ РЕДАКТОРА:\n«{user_comment}»\n" if user_comment else ""
    return (
        f"Ты — утонченный сатирик, драматург и главный эссеист брестского журнала «Калейдоскоп». "
        f"Перед тобой скриншоты или протокол ночной психологической баталии «Мафия» в чате Brest_TALK. "
        f"Твоя задача — превратить эту сухую партию в захватывающий литературно-сатирический этюд.\n\n"
        f"{comment_block}\n"
        f"ЖЕСТКИЙ ЛИМИТ ОБЪЕМА:\n"
        f"Весь итоговый пост (от первого слова лида до подписи даты) должен составлять СТРОГО ДО 1100 СИМВОЛОВ С ПРОБЕЛАМИ. "
        f"Текст должен читаться ровно за 40–50 секунд, быть максимально плотным, сочным, без воды и длинных предисловий, идеально помещаясь в один экран телефона.\n\n"
        f"ПРАВИЛА ДРАМАТУРГИИ И СТИЛЯ:\n"
        f"1. РЕЗКИЕ СЮЖЕТНЫЕ ПОВОРОТЫ: Выстраивай рассказ как напряженную микро-пьесу. Завязка (обманчивый покой, маски невинности) -> Развитие интриги -> Внезапный слом (акцентируй внимание на минутах и секундах партии: «Ровно в 01:14 хрупкий мир рухнул...», «Когда до рассвета оставались считанные мгновения, один неосторожный взгляд перечеркнул всё») -> Роковая ошибка или триумф блефа.\n"
        f"2. ДЕЙСТВУЮЩИЕ ЛИЦА: Обязательно вплетай реальные имена игроков со скриншотов или протокола. С тонкой сатирой подмечай психологию блефа и подозрений: кто клялся в честности, кто устроил излишнюю драму, чье алиби рассыпалось, и кто в итоге хладнокровно переиграл стол.\n"
        f"3. ОРИГИНАЛЬНОСТЬ И ВАРЬИРОВАНИЕ ЗАГОЛОВКОВ: Партии идут каждый вечер, поэтому штампы ЗАПРЕЩЕНЫ. Никаких «Итогов игры» или скучных отчетов! Заголовок должен быть метафоричным, хлестким (1–3 слова) и отражать суть конкретной драмы.\n"
        f"4. ВРЕМЕННЫЕ МАРКЕРЫ: Фразы «вчерашняя игра», «минувшей ночью» или «под покровом сумерек» используй редко, ненавязчиво и разнообразно.\n"
        f"5. ЭМОДЗИ (ЯВНОЕ ИСКЛЮЧЕНИЕ ИЗ СИСТЕМНОГО ЗАПРЕТА): В этой рубрике РАЗРЕШЕНО и НУЖНО использовать ровно 2–3 выразительных атмосферных эмодзи (например: 🎭, 🍷, 🕯️, 🎲), органично вплетая их в заголовок или текст.\n"
        f"6. МУДРОСТЬ НА ПОЛЯХ: В финале дай философский срез человеческой натуры — ироничный или глубокий афоризм о людях и масках (1 предложение).\n\n"
        f"СТРОГАЯ СТРУКТУРА ВЕРСТКИ (ТОЛЬКО HTML, НИКАКИХ ЗВЕЗДОЧЕК *):\n"
        f"<i>Короткий интригующий лид курсивом (1 предложение).</i>\n"
        f"<b>ОРИГИНАЛЬНЫЙ ЗАГОЛОВОК (1-3 слова)</b>\n\n"
        f"<blockquote expandable>\n"
        f"Основной текст хроники: завязка, накал страстей с именами, кульминация с минутами и финал. Единым блоком без разбивки.\n"
        f"</blockquote>\n\n"
        f"<b>Мысли на полях:</b>\n"
        f"<blockquote expandable><i>Ироничный или глубокий афоризм о людях и масках (1 предложение).</i></blockquote>\n\n"
        f"Kaleidoscope | Brest 🎨\n"
        f"{date_str}"
    )

def build_kaleidoscope_prompt(text_content, user_comment=""):
    date_str = get_current_date_ru()
    comment_line = f"\nДополнительный комментарий автора: {user_comment}\n" if user_comment else ""
    return (
        f"Тебе передан текст для публикации в журнале «Калейдоскоп»:\n«{text_content}»\n"
        f"{comment_line}\n"
        f"ТВОЯ ЗАДАЧА: Выступить исключительно в роли технического верстальщика и оформителя. "
        f"КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ искажать смысл текста, удалять важные мысли или дописывать отсебятину.\n\n"
        f"ИНСТРУКЦИЯ ПО ОФОРМЛЕНИЮ (используй ТОЛЬКО HTML-теги для Telegram):\n"
        f"0. ВАЖНО: Полностью удали все символы звездочек (*). Не используй Markdown (**текст**), только чистый HTML.\n"
        f"1. <i>Короткое, но цепляющее вступление курсивом (1 предложение-лид).</i>\n"
        f"2. <b>Жирный заголовок</b> (Максимально короткий — 1-3 слова, хлесткий, ироничный или парадоксальный).\n"
        f"3. Весь основной текст помести строго в ОДНУ раскрывающуюся цитату: <blockquote expandable>весь основной текст одним блоком</blockquote>.\n"
        f"4. Секция: <b>Мысли на полях:</b>\n"
        f"5. Вывод оберни во ВТОРУЮ цитату и курсив: <blockquote expandable><i>короткий емкий афоризм или вывод</i></blockquote>\n"
        f"6. Подпись строго в две строки:\nKaleidoscope | Brest 🎨\n{date_str}"
    )

def atomic_save_json(filepath, data):
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(filepath):
        try:
            if os.path.getsize(filepath) > 0:
                shutil.copy2(filepath, filepath + ".bak")
        except Exception as e:
            print(f"Ошибка создания резервной копии .bak для {filepath}: {e}")
    os.replace(tmp_path, filepath)

def _normalize_refs_data(raw_data):
    if not isinstance(raw_data, dict):
        return {"users": {}, "links": {}}
    users_raw = raw_data.get("users")
    links_raw = raw_data.get("links")
    norm_users = {}
    norm_links = {}

    if isinstance(users_raw, dict):
        for uid, uinfo in users_raw.items():
            if not isinstance(uinfo, dict):
                continue
            uid_str = str(uid)
            raw_refs = uinfo.get("referrals", [])
            dedup_refs = []
            if isinstance(raw_refs, list):
                for r in raw_refs:
                    r_str = str(r)
                    if r_str and r_str not in dedup_refs:
                        dedup_refs.append(r_str)
            invite_link = uinfo.get("invite_link") or ""
            norm_users[uid_str] = {
                "name": str(uinfo.get("name") or "Участник"),
                "invite_link": str(invite_link),
                "referrals": dedup_refs,
                "active": bool(uinfo.get("active", False))
            }
            if invite_link:
                norm_links[str(invite_link)] = uid_str

    if isinstance(links_raw, dict):
        for link_str, creator_id in links_raw.items():
            if link_str and creator_id is not None:
                norm_links[str(link_str)] = str(creator_id)

    return {"users": norm_users, "links": norm_links}

def load_refs_data():
    with refs_lock:
        if os.path.exists(REFS_FILE):
            try:
                with open(REFS_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return _normalize_refs_data(raw)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[REFS WARNING] Основной файл {REFS_FILE} поврежден ({e}). Чтение из резервной копии .bak...")
                bak_file = REFS_FILE + ".bak"
                if os.path.exists(bak_file):
                    try:
                        with open(bak_file, "r", encoding="utf-8") as bf:
                            raw_bak = json.load(bf)
                        restored = _normalize_refs_data(raw_bak)
                        try:
                            atomic_save_json(REFS_FILE, restored)
                        except Exception:
                            pass
                        return restored
                    except Exception as bak_err:
                        print(f"[REFS ERROR] Не удалось прочитать резервную копию {bak_file}: {bak_err}")
        return {"users": {}, "links": {}}

def save_refs_data(data):
    with refs_lock:
        normalized = _normalize_refs_data(data)
        try:
            atomic_save_json(REFS_FILE, normalized)
        except Exception as e:
            print(f"[REFS ERROR] Ошибка сохранения базы рефералов: {e}")

def mark_user_active_in_brest_talk(user):
    if not user or getattr(user, "is_bot", False):
        return
    uid_str = str(user.id)
    uname = format_user_display_name(user)
    with refs_lock:
        data = load_refs_data()
        users = data["users"]
        if uid_str not in users:
            users[uid_str] = {
                "name": uname,
                "invite_link": "",
                "referrals": [],
                "active": True
            }
            save_refs_data(data)
        else:
            changed = False
            if not users[uid_str].get("active"):
                users[uid_str]["active"] = True
                changed = True
            if uname and users[uid_str].get("name") != uname:
                users[uid_str]["name"] = uname
                changed = True
            if changed:
                save_refs_data(data)

def build_ref_cabinet_payload(user):
    uid_str = str(user.id)
    uname = format_user_display_name(user)

    with refs_lock:
        data = load_refs_data()
        if uid_str not in data["users"]:
            data["users"][uid_str] = {
                "name": uname,
                "invite_link": "",
                "referrals": [],
                "active": False
            }
        else:
            if uname:
                data["users"][uid_str]["name"] = uname

        user_data = data["users"][uid_str]
        invite_link = user_data.get("invite_link", "")

        if not invite_link:
            try:
                invite = bot.create_chat_invite_link(
                    chat_id=BREST_TALK_ID,
                    name=f"Ref_{uid_str}"
                )
                invite_link = invite.invite_link
                user_data["invite_link"] = invite_link
                data["links"][invite_link] = uid_str
            except Exception as e:
                print(f"[REFS ERROR] Ошибка создания invite_link для {uid_str}: {e}")

        # Живая проверка рефералов ТОЛЬКО этого конкретного пользователя
        valid_refs = []
        refs_changed = False
        for r_id in list(user_data.get("referrals", [])):
            try:
                cm = bot.get_chat_member(BREST_TALK_ID, int(r_id))
                if cm.status in ['member', 'administrator', 'creator', 'restricted']:
                    if str(r_id) not in valid_refs:
                        valid_refs.append(str(r_id))
                else:
                    refs_changed = True
            except Exception as e:
                err_lower = str(e).lower()
                if any(phrase in err_lower for phrase in ("user not found", "chat not found", "participant_id_invalid", "user_id_invalid")):
                    refs_changed = True
                else:
                    # При временном сетевом сбое сохраняем реферала
                    if str(r_id) not in valid_refs:
                        valid_refs.append(str(r_id))

        if refs_changed or len(valid_refs) != len(user_data.get("referrals", [])):
            user_data["referrals"] = valid_refs

        save_refs_data(data)
        count = len(user_data.get("referrals", []))
        is_active = bool(user_data.get("active", False))

    link_display = invite_link if invite_link else "Не удалось создать ссылку (проверьте права администратора у бота в Brest_TALK)"
    activity_status = "✅ Выполнено" if is_active else "❌ Напишите хотя бы 1 сообщение в чате Brest_TALK"
    qualified_status = "✅ Вы в игре!" if (count >= 3 and is_active) else "⏳ Выполните все условия выше"

    text = (
        f"🔥 <b>МЕГА-РОЗЫГРЫШ 2 ФИТНЕС-БРАСЛЕТОВ MI BAND 11!</b> 🎁⌚️\n\n"
        f"📅 <b>Сроки проведения:</b> с сегодняшнего дня по 29 ноября.\n"
        f"Для попадания в барабан нужно минимум <b>3 приглашенных друга</b> (3 билета). "
        f"Хотите увеличить шансы выигрыша — приглашайте больше друзей и получайте больше билетов! "
        f"Приглашенные участники должны оставаться в чате на момент розыгрыша, а вам нужно написать хотя бы 1 комментарий в чате.\n\n"
        f"🔗 <b>Ваша персональная ссылка для приглашения друзей:</b>\n"
        f"<code>{html.escape(link_display)}</code>\n\n"
        f"📊 <b>Ваш статус участника:</b>\n"
        f"🎟 <b>Ваши билеты:</b> {count} (Минимум для входа: 3)\n"
        f"💬 <b>Активность в чате (мин. 1 сообщение):</b> {activity_status}\n"
        f"🎯 <b>Статус допуска в барабан:</b> {qualified_status}"
    )

    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    if invite_link:
        share_text = "Залетай в живой чат Бреста Brest_TALK! Тут уютно, игры по вечерам и розыгрыш двух Mi Band 11 🔥"
        share_url = (
            f"https://t.me/share/url?url={urllib.parse.quote(invite_link)}"
            f"&text={urllib.parse.quote(share_text)}"
        )
        markup.add(
            telebot.types.InlineKeyboardButton(
                text="📤 Поделиться ссылкой с друзьями",
                url=share_url
            )
        )
    markup.add(
        telebot.types.InlineKeyboardButton(
            text="🔄 Обновить мою статистику",
            callback_data="refresh_ref_stats"
        )
    )
    return text, markup

def send_or_edit_ref_cabinet(chat_id, user, edit_msg_id=None, callback_query_id=None):
    text, markup = build_ref_cabinet_payload(user)
    if edit_msg_id is not None:
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_msg_id,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            if callback_query_id:
                bot.answer_callback_query(callback_query_id, "✅ Статистика обновлена!", show_alert=False)
            return
        except Exception as e:
            if "message is not modified" in str(e).lower():
                if callback_query_id:
                    try:
                        bot.answer_callback_query(callback_query_id, "Актуальные данные уже на экране 👌", show_alert=False)
                    except Exception:
                        pass
                return
            print(f"Ошибка редактирования карточки рефералов: {e}")

    try:
        bot.send_message(
            chat_id,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"Ошибка отправки карточки рефералов: {e}")

def verify_and_build_giveaway_pool():
    """
    Проверяет ВСЕ условия в реальном времени через Telegram API:
    1. Участник сам состоит в чате BREST_TALK_ID.
    2. Участник проявил активность (active == True).
    3. Каждый приглашенный друг реально находится в чате BREST_TALK_ID на момент проверки.
    4. Число живых рефералов >= 3.
    Возвращает кортеж: (qualified_list, all_stats_list)
    """
    with refs_lock:
        data = load_refs_data()
        users = data.get("users", {})
        qualified = []
        all_stats = []
        db_modified = False

        for uid_str, uinfo in users.items():
            name = uinfo.get("name") or f"ID {uid_str}"
            is_active = bool(uinfo.get("active", False))
            raw_refs = list(uinfo.get("referrals", []))

            # 1. Проверяем присутствие самого участника в чате
            in_chat = False
            try:
                member = bot.get_chat_member(BREST_TALK_ID, int(uid_str))
                if member.status in ['member', 'administrator', 'creator', 'restricted']:
                    in_chat = True
            except Exception as e:
                print(f"[VERIFY] Не удалось проверить участника {uid_str}: {e}")
                in_chat = False

            # 2. Живая проверка каждого приглашенного друга в чате
            alive_refs = []
            for ref_id in raw_refs:
                try:
                    ref_member = bot.get_chat_member(BREST_TALK_ID, int(ref_id))
                    if ref_member.status in ['member', 'administrator', 'creator', 'restricted']:
                        if str(ref_id) not in alive_refs:
                            alive_refs.append(str(ref_id))
                    else:
                        db_modified = True
                except Exception as e:
                    err_lower = str(e).lower()
                    if any(phrase in err_lower for phrase in ("user not found", "chat not found", "participant_id_invalid", "user_id_invalid")):
                        print(f"[VERIFY] Реферал {ref_id} не найден ({e}), удаляем из базы.")
                        db_modified = True
                    else:
                        print(f"[VERIFY] Временная ошибка сети при проверке {ref_id} ({e}), сохраняем.")
                        if str(ref_id) not in alive_refs:
                            alive_refs.append(str(ref_id))

            if len(alive_refs) != len(raw_refs):
                uinfo["referrals"] = alive_refs
                db_modified = True

            tickets_count = len(alive_refs)
            is_qualified = in_chat and is_active and (tickets_count >= 3)

            entry = {
                "user_id": str(uid_str),
                "name": name,
                "tickets": tickets_count,
                "active": is_active,
                "in_chat": in_chat,
                "qualified": is_qualified
            }
            all_stats.append(entry)
            if is_qualified:
                qualified.append(entry)

        if db_modified:
            save_refs_data(data)

    qualified.sort(key=lambda x: (-x["tickets"], x["name"]))
    all_stats.sort(key=lambda x: (-int(x["qualified"]), -x["tickets"], x["name"]))
    return qualified, all_stats

def process_ref_top_async(chat_id, status_msg_id):
    try:
        qualified, all_stats = verify_and_build_giveaway_pool()
        if not all_stats:
            empty_text = (
                "📊 <b>Таблица участников розыгрыша Mi Band 11 пуста.</b>\n"
                "Пока никто не создал реферальную ссылку."
            )
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=empty_text,
                parse_mode="HTML"
            )
            return

        lines = []
        for idx, item in enumerate(all_stats[:30], start=1):
            act_icon = "💬✅" if item["active"] else "💬❌"
            qual_icon = "🎯 Допущен" if item["qualified"] else "⏳ Не допущен"
            lines.append(
                f"{idx}. <b>{html.escape(item['name'])}</b> — 🎟 <b>{item['tickets']}</b> бил. | {act_icon} | {qual_icon}"
            )

        total_qual_tickets = sum(p["tickets"] for p in qualified)
        stats_text = (
            f"🏆 <b>Рейтинг участников розыгрыша Mi Band 11 (проверено в реальном времени):</b>\n\n"
            + "\n".join(lines)
            + f"\n\n✅ <b>Квалифицировано в барабан:</b> {len(qualified)} чел. (Всего билетов: <b>{total_qual_tickets}</b>)"
        )
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=stats_text,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Ошибка в фоновом потоке process_ref_top_async: {e}")

def process_start_giveaway_async(chat_id, status_msg_id):
    try:
        qualified, _ = verify_and_build_giveaway_pool()
        total_tickets = sum(p["tickets"] for p in qualified)

        if not qualified:
            no_qual_text = (
                "⚠️ <b>Проверка завершена:</b> На данный момент нет ни одного участника, "
                "выполнившего все условия (минимум 3 живых реферала в чате + минимум 1 сообщение в чате)."
            )
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=no_qual_text,
                parse_mode="HTML"
            )
            return

        part_lines = [
            f"• <b>{html.escape(p['name'])}</b> — 🎟 <b>{p['tickets']}</b> билет(ов)"
            for p in qualified
        ]
        preview_text = (
            f"🔥 <b>ФИНАЛ МЕГА-РОЗЫГРЫША 2 ФИТНЕС-БРАСЛЕТОВ XIAOMI MI BAND 11!</b> 🎁⌚️\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ <b>Все условия проверены в реальном времени!</b>\n"
            f"Участники в чате, активность подтверждена, присутствие каждого приглашенного друга проверено.\n\n"
            f"👥 <b>Допущенные участники в барабане ({len(qualified)}):</b>\n"
            + "\n".join(part_lines)
            + f"\n━━━━━━━━━━━━━━\n"
            f"🎟 <b>Всего билетов в барабане:</b> {total_tickets}\n"
            f"🏆 <b>Призовой фонд:</b> 2 x Xiaomi Mi Band 11\n\n"
            f"👇 <i>Нажмите кнопку ниже для запуска барабана в прямом эфире!</i>"
        )
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton(
                text="🚀 КРУТИТЬ БАРАБАН (В ПРЯМОМ ЭФИРЕ)",
                callback_data="run_live_drum"
            )
        )
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=preview_text,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Ошибка в фоновом потоке process_start_giveaway_async: {e}")

def run_drum_animation_thread(chat_id, message_id, participants, is_test=False):
    try:
        try:
            bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        except Exception:
            pass

        if not participants:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ <b>Нет квалифицированных участников для запуска барабана.</b>",
                parse_mode="HTML"
            )
            return

        # Формируем пул билетов из кортежей (user_id, name, tickets)
        ticket_pool = []
        for idx, p in enumerate(participants):
            p_uid = str(p.get("user_id") or f"uid_{idx}")
            p_name = str(p.get("name") or "Участник")
            p_tickets = int(p.get("tickets", 0))
            for _ in range(p_tickets):
                ticket_pool.append((p_uid, p_name, p_tickets))

        random.shuffle(ticket_pool)
        total_tickets = len(ticket_pool)

        if total_tickets == 0:
            return

        delays = [1.3, 1.5, 1.7, 2.0, 2.3, 2.6, 3.0]
        mode_prefix = "🧪 <b>[ТЕСТОВЫЙ РЕЖИМ]</b>\n" if is_test else ""

        # РАУНД 1: Приз #1 — первый Mi Band 11
        for step_idx, delay in enumerate(delays):
            time.sleep(delay)
            above = html.escape(random.choice(ticket_pool)[1])
            current = html.escape(random.choice(ticket_pool)[1])
            below = html.escape(random.choice(ticket_pool)[1])
            spin_dots = "." * ((step_idx % 3) + 1)

            frame_text = (
                f"{mode_prefix}"
                f"🎰 <b>РОЗЫГРЫШ ПРИЗА #1: Xiaomi Mi Band 11</b> ⌚️\n"
                f"━━━━━━━━━━━━━━\n"
                f"🔄 Барабан крутится{spin_dots}\n"
                f"   ▫️ {above}\n"
                f"👉 🎯 [ <b>{current}</b> ] 🎯 👈\n"
                f"   ▫️ {below}\n"
                f"━━━━━━━━━━━━━━\n"
                f"🎟 Всего билетов в барабане: <b>{total_tickets}</b>"
            )
            try:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=frame_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                if "message is not modified" not in str(e).lower():
                    print(f"[DRUM R1 STEP ERROR]: {e}")

        time.sleep(1.5)
        winner_1 = random.choice(ticket_pool)
        w1_uid, w1_name, w1_tickets = winner_1
        safe_w1 = html.escape(w1_name)

        r1_done_text = (
            f"{mode_prefix}"
            f"🎉 <b>ПРИЗ #1 (Xiaomi Mi Band 11) РАЗЫГРАН!</b> ⌚️\n"
            f"━━━━━━━━━━━━━━\n"
            f"🏆 <b>Победитель #1:</b> <b>{safe_w1}</b> (билетов в барабане: {w1_tickets})!\n"
            f"━━━━━━━━━━━━━━\n"
            f"⏳ <i>Готовим барабан для розыгрыша Приза #2...</i>"
        )
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r1_done_text,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[DRUM R1 WINNER ERROR]: {e}")

        time.sleep(4.0)

        # РАУНД 2: Приз #2 — второй Mi Band 11 (удаляем все билеты первого победителя строго по user_id)
        pool_round_2 = [item for item in ticket_pool if item[0] != w1_uid]
        random.shuffle(pool_round_2)

        if pool_round_2:
            total_tickets_r2 = len(pool_round_2)
            for step_idx, delay in enumerate(delays):
                time.sleep(delay)
                above = html.escape(random.choice(pool_round_2)[1])
                current = html.escape(random.choice(pool_round_2)[1])
                below = html.escape(random.choice(pool_round_2)[1])
                spin_dots = "." * ((step_idx % 3) + 1)

                frame_r2_text = (
                    f"{mode_prefix}"
                    f"🥇 <b>Победитель #1:</b> {safe_w1} ({w1_tickets} бил.)\n\n"
                    f"🎰 <b>РОЗЫГРЫШ ПРИЗА #2: Xiaomi Mi Band 11</b> ⌚️\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"🔄 Барабан крутится{spin_dots}\n"
                    f"   ▫️ {above}\n"
                    f"👉 🎯 [ <b>{current}</b> ] 🎯 👈\n"
                    f"   ▫️ {below}\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"🎟 Оставшихся билетов в барабане: <b>{total_tickets_r2}</b>"
                )
                try:
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=frame_r2_text,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    if "message is not modified" not in str(e).lower():
                        print(f"[DRUM R2 STEP ERROR]: {e}")

            time.sleep(1.5)
            winner_2 = random.choice(pool_round_2)
            w2_uid, w2_name, w2_tickets = winner_2
            safe_w2 = html.escape(w2_name)
            winner_2_line = f"🥇 <b>Победитель #2 (Mi Band 11):</b> <b>{safe_w2}</b> — {w2_tickets} билет(ов)"
        else:
            winner_2_line = "🥇 <b>Победитель #2 (Mi Band 11):</b> <i>В барабане был только 1 квалифицированный участник</i>"

        final_title = (
            "🧪 <b>ТЕСТОВЫЙ РОЗЫГРЫШ ЗАВЕРШЕН!</b> 🎊"
            if is_test
            else "🔥 <b>ИТОГИ МЕГА-РОЗЫГРЫША 2 ФИТНЕС-БРАСЛЕТОВ XIAOMI MI BAND 11!</b> 🎁⌚️"
        )

        final_text = (
            f"{final_title}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🥇 <b>Победитель #1 (Mi Band 11):</b> <b>{safe_w1}</b> — {w1_tickets} билет(ов)\n"
            f"{winner_2_line}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎟 Всего участвовало билетов: <b>{total_tickets}</b>\n"
            f"👥 Допущенных финалистов: <b>{len(participants)}</b>\n\n"
            f"🥳 Поздравляем счастливчиков! Напишите администратору чата в личные сообщения для получения ваших призов! 🚀"
        )

        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=final_text,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[DRUM FINAL ERROR]: {e}")

        if not is_test:
            try:
                bot.pin_chat_message(chat_id, message_id, disable_notification=False)
            except Exception as e:
                print(f"Ошибка закрепления финального сообщения розыгрыша: {e}")

    except Exception as e:
        print(f"Критическая ошибка в анимации барабана: {e}")

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
                    return data[-100:]
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

def generate_quiz_image(prompt_text: str) -> bytes | None:
    if not prompt_text:
        return None
    try:
        response = client.models.generate_content(
            model='gemini-3.1-flash-image',
            contents=prompt_text,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        if not getattr(response, "candidates", None) or not response.candidates[0].content:
            print(f"[IMAGE ERROR]: Пустой ответ от модели для промпта: {prompt_text}")
            return None

        parts = getattr(response.candidates[0].content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, 'inline_data', None)
            if inline_data and getattr(inline_data, 'data', None):
                raw_data = inline_data.data
                if isinstance(raw_data, str):
                    return base64.b64decode(raw_data)
                elif isinstance(raw_data, (bytes, bytearray)):
                    # Если байты уже являются валидным JPEG/PNG, возвращаем их напрямую, иначе декодируем из base64
                    if raw_data.startswith(b'\xff\xd8\xff') or raw_data.startswith(b'\x89PNG'):
                        return bytes(raw_data)
                    try:
                        return base64.b64decode(raw_data)
                    except Exception:
                        return bytes(raw_data)
    except Exception as e:
        print(f"[IMAGE ERROR]: {e}")
    return None

def generate_quiz_questions():
    used_questions = load_used_questions()
    
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
        f"Сгенерируй ровно 11 вопросов для живой групповой викторины в чате (10 основных + 1 финальный супер-вопрос). "
        f"Используй микс из этих сфер: {selected_topics}.\n\n"
        f"ЖЕСТКАЯ ГРАДАЦИЯ СЛОЖНОСТИ (критически важно соблюсти контраст!):\n"
        f"• Вопросы 1–2 (УРОВЕНЬ: УЛЬТРА-ЛЕГКИЙ РАЗОГРЕВ): Максимально простые вопросы для разогрева, ответ за 1–2 секунды.\n"
        f"• Вопросы 3–4 (УРОВЕНЬ: ЭЛЕМЕНТАРНЫЙ): Очень лёгкие, бытовые или поп-культурные вопросы (фильмы, еда, столицы).\n"
        f"• Вопросы 5–8 (УРОВЕНЬ: СРЕДНЯЯ СЛОЖНОСТЬ): Интересные вопросы на кругозор и смекалку.\n"
        f"• Вопросы 9–10 (УРОВЕНЬ: ХАРДКОР / ЧГК): Редкие, парадоксальные факты для эрудитов.\n"
        f"• Вопрос 11 (УРОВЕНЬ: СУПЕР-ВОПРОС / ФИНАЛ): Финальный супер-вопрос высшей лиги для определения чемпиона.\n\n"
        f"СТРОГОЕ ПРАВИЛО ВИЗУАЛЬНЫХ ЗАГАДОК:\n"
        f"• Вопросы №2, №5 и №8 (нумерация с 1) ОБЯЗАНЫ быть визуальными загадками (метафоры известных фильмов, буквальные визуализации крылатых фраз/идиом, логические арт-загадки). "
        f"В тексте таких вопросов спрашивай, что зашифровано или показано на картинке (но так, чтобы вопрос оставался решаемым и по смыслу), а в поле \"image_prompt\" дай детальное описание на АНГЛИЙСКОМ языке для генератора изображений (без текста и надписей на самой картинке).\n"
        f"• Для всех остальных вопросов (№1, 3, 4, 6, 7, 9, 10, 11) поле \"image_prompt\" СТРОГО должно иметь значение null.\n\n"
        f"ПРАВИЛА ПРОВЕРКИ:\n"
        f"1. Каждый вопрос должен иметь ровно 4 варианта ответа и только ОДИН достоверный правильный ответ (correct_idx от 0 до 3).\n"
        f"2. Не делай правильный ответ самым длинным или выделяющимся по стилю.\n"
        f"3. Используй типографские кавычки «елочки» внутри текстов, чтобы не ломать JSON.\n"
        f"{avoid_block}\n"
        f"Ответь СТРОГО валидным JSON-списком из 11 объектов без markdown-разметки:\n"
        f'[\n'
        f'  {{"question": "Текст вопроса", "options": ["Вариант 1", "Вариант 2", "Вариант 3", "Вариант 4"], "correct_idx": 0, "comment": "Короткий интересный факт к ответу", "image_prompt": null}}\n'
        f']'
    )

    raw_text = ""
    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=1.1
            )
        )
        raw_text = (response.text or "").strip()
        if not raw_text:
            print("Ошибка генерации вопросов: получен пустой ответ от модели.")
            return []

        cleaned_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
        cleaned_text = re.sub(r"\s*```$", "", cleaned_text).strip()

        start_idx = cleaned_text.find("[")
        end_idx = cleaned_text.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            cleaned_text = cleaned_text[start_idx:end_idx + 1]

        data = json.loads(cleaned_text)
        if not isinstance(data, list):
            return []

        valid_questions = []
        for idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            question_text = item.get("question")
            options = item.get("options")
            correct_idx = item.get("correct_idx")
            image_prompt = item.get("image_prompt")
            if isinstance(image_prompt, str):
                image_prompt = image_prompt.strip() or None
            else:
                image_prompt = None

            if (
                isinstance(question_text, str) and question_text.strip()
                and isinstance(options, list) and len(options) >= 2
                and isinstance(correct_idx, int) and 0 <= correct_idx < len(options)
            ):
                clean_options = [str(opt).strip() for opt in options]
                correct_answer_text = clean_options[correct_idx]
                
                random.shuffle(clean_options)
                new_correct_idx = clean_options.index(correct_answer_text)

                valid_questions.append({
                    "question": question_text.strip(),
                    "options": clean_options,
                    "correct_idx": new_correct_idx,
                    "comment": str(item.get("comment", "")).strip(),
                    "image_prompt": image_prompt,
                    "image_bytes": None
                })

        valid_questions = valid_questions[:11]
        if valid_questions:
            save_used_questions(valid_questions)
        return valid_questions

    except Exception as e:
        print(f"Ошибка генерации вопросов квиза: {e}")
        return []

def prefetch_question_image(questions, idx):
    if 0 <= idx < len(questions):
        q = questions[idx]
        prompt_text = q.get("image_prompt")
        if prompt_text and q.get("image_bytes") is None:
            img_bytes = generate_quiz_image(prompt_text)
            if img_bytes:
                with quiz_lock:
                    q["image_bytes"] = img_bytes
                    if QUIZ_STATE["is_running"] and 0 <= idx < len(QUIZ_STATE["questions"]):
                        QUIZ_STATE["questions"][idx]["image_bytes"] = img_bytes

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
            "получает главный приз 🏆+ суперприз — 4 билета в кино 🎬🍿\n"
            "• <b>Награды для топ-3 по баллам из витрины Telegram:</b>\n"
            "• <b>от 10 баллов</b> = 15 ⭐️ 🧸\n"
            "• <b>от 25 баллов</b> = 25 ⭐️ 🎁\n"
            "• <b>от 50 баллов</b> = 50 ⭐️ 🚀\n"
            "• <b>100 баллов</b> = 100 ⭐️ 🏆\n\n"
            "⏳ <i>Вопрос появится через 1,5 минуты. Готовьтесь!</i>",
            parse_mode="HTML"
        )

        # Во время стартовой 1.5-минутной паузы запускаем фоновый поток генерации для первого визуального вопроса
        for idx_check in range(len(questions)):
            if questions[idx_check].get("image_prompt"):
                Thread(target=prefetch_question_image, args=(questions, idx_check), daemon=True).start()
                break

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
                img_bytes = q.get("image_bytes")
                img_prompt = q.get("image_prompt")

            # Если у вопроса есть промпт, а картинка ещё не готова — генерируем синхронно перед отправкой
            if img_prompt and not img_bytes:
                q["image_bytes"] = generate_quiz_image(img_prompt)
                img_bytes = q["image_bytes"]

            if img_prompt and not img_bytes:
                print(f"[QUIZ] Не удалось сгенерировать изображение для вопроса {i}, fallback на текст")

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
            safe_q_text = html.escape(q.get('question', 'Вопрос'))
            msg_text = (
                f"{q_header}\n"
                f"━━━━━━━━━━━━━━\n"
                f"<blockquote><b>{safe_q_text}</b></blockquote>"
            )

            q_msg = None
            if img_bytes:
                try:
                    photo_stream = io.BytesIO(img_bytes)
                    photo_stream.name = f"quiz_{i}.jpg"
                    photo_stream.seek(0)

                    if len(msg_text) <= 1024:
                        q_msg = bot.send_photo(
                            target_chat_id,
                            photo=photo_stream,
                            caption=msg_text,
                            reply_markup=markup,
                            parse_mode="HTML"
                        )
                    else:
                        bot.send_photo(
                            target_chat_id,
                            photo=photo_stream,
                            caption=f"🎨 <b>Иллюстрация к вопросу {i} из {total_q}</b>",
                            parse_mode="HTML"
                        )
                        q_msg = bot.send_message(
                            target_chat_id,
                            msg_text,
                            reply_markup=markup,
                            parse_mode="HTML"
                        )
                except Exception as e:
                    print(f"Ошибка отправки фото-вопроса, фолбэк на текст: {e}")
                    try:
                        photo_stream_retry = io.BytesIO(img_bytes)
                        photo_stream_retry.name = f"quiz_{i}.jpg"
                        photo_stream_retry.seek(0)
                        bot.send_photo(
                            target_chat_id,
                            photo=photo_stream_retry,
                            caption=f"🎨 Загадка #{i}"
                        )
                    except Exception:
                        pass
                    q_msg = None

            if q_msg is None:
                try:
                    q_msg = bot.send_message(target_chat_id, msg_text, reply_markup=markup, parse_mode="HTML")
                except Exception as e:
                    print(f"Ошибка отправки вопроса: {e}")
                    continue

            # Предзагрузка следующей картинки на фоне
            for future_idx in range(i, len(questions)):
                if questions[future_idx].get("image_prompt") and questions[future_idx].get("image_bytes") is None:
                    Thread(target=prefetch_question_image, args=(questions, future_idx), daemon=True).start()
                    break

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

            safe_correct_opt = html.escape(options[correct_idx])
            safe_comment = html.escape(q.get('comment', ''))
            next_q_note = "\n\n⏳ <i>Следующий вопрос через 1,5 минуты...</i>" if i < total_q else ""

            if correct_answers:
                first_correct = correct_answers[0]
                total_score = add_quiz_score(first_correct["user_id"], first_correct["name"], points=10)
                safe_name = html.escape(first_correct['name'])
                res_text = (
                    f"⏱ <b>Время вышло!</b>\n"
                    f"Правильный ответ: <b>{safe_correct_opt}</b>\n\n"
                    f"⚡️ Первым правильно ответил: <b>{safe_name}</b> (+10 баллов)!\n"
                    f"Счёт в текущей игре: <b>{total_score}</b> баллов.\n"
                    f"<i>{safe_comment}</i>"
                    f"{next_q_note}"
                )
                bot.send_message(target_chat_id, res_text, parse_mode="HTML")
            else:
                res_text = (
                    f"⏱ <b>Время вышло!</b>\n"
                    f"Правильный ответ: <b>{safe_correct_opt}</b>\n\n"
                    f"Никто не успел дать правильный ответ.\n"
                    f"<i>{safe_comment}</i>"
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

            perfect_target_questions = total_q if total_q > 0 else 11
            max_possible_points = perfect_target_questions * 10  # 11 вопросов по 10 баллов = 110 баллов

            # Супер-приз выдается ТОЛЬКО тем, кто реально набрал максимальные 110 баллов (11 из 11)
            perfect_players = [
                p["name"]
                for p in sorted_scores
                if p.get("points", 0) >= max_possible_points
            ]

            if perfect_players:
                if len(perfect_players) == 1:
                    perfect_name = f"<b>{html.escape(perfect_players[0])}</b>"
                    super_prize_block = (
                        f"🎬 <b>СУПЕР-ПРИЗ ({perfect_target_questions} из {perfect_target_questions} правильных ответов!):</b>\n"
                        f"🌟 Безупречный результат в {max_possible_points} баллов без единой ошибки показал(а): {perfect_name}!\n"
                        f"Главный супер-приз — <b>4 билета в кино</b> 🎬🎟️ достаётся триумфатору!\n\n"
                    )
                else:
                    perfect_names_str = ", ".join(f"<b>{html.escape(name)}</b>" for name in perfect_players)
                    super_prize_block = (
                        f"🎬 <b>СУПЕР-ПРИЗ ({perfect_target_questions} из {perfect_target_questions} правильных ответов!):</b>\n"
                        f"🌟 Безупречный результат в {max_possible_points} баллов без единой ошибки показали: {perfect_names_str}!\n"
                        f"Главный супер-приз — <b>4 билета в кино</b> 🎬🎟️ достаётся им!\n\n"
                    )
            else:
                super_prize_block = (
                    f"🎬 <b>Супер-приз ({perfect_target_questions} из {perfect_target_questions} без ошибок / {max_possible_points} баллов):</b>\n"
                    f"В этот раз никто не смог набрать максимум в {max_possible_points} баллов — "
                    f"<b>4 билета в кино</b> 🎬🎟️ остаются в банке викторины до следующей игры!\n\n"
                )

            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            finalists = sorted_scores[:3]

            if finalists:
                winner = finalists[0]
                finalists_lines = []
                for place, p in enumerate(finalists, start=1):
                    prize_str = get_star_prize_by_points(p["points"])
                    medal = medals.get(place, f"{place}.")
                    finalists_lines.append(
                        f"{medal} <b>{place} место:</b> {html.escape(p['name'])} — <b>{p['points']} баллов</b> "
                        f"(Приз: <b>{prize_str}</b>)"
                    )
                finalists_text = "\n".join(finalists_lines)

                other_players = sorted_scores[3:10]
                others_text = ""
                if other_players:
                    others_lines = [
                        f"{idx}. {html.escape(p['name'])} — {p['points']} баллов"
                        for idx, p in enumerate(other_players, start=4)
                    ]
                    others_text = "\n\n<b>Остальные участники:</b>\n" + "\n".join(others_lines)

                final_msg_text = (
                    f"🏁 <b>Викторина завершена. Итоги игры:</b>\n\n"
                    f"👑 <b>Победитель и лидер по очкам:</b> {html.escape(winner['name'])} ({winner['points']} баллов)\n\n"
                    f"{super_prize_block}"
                    f"🏆 <b>Топ-3 игроков и награды:</b>\n"
                    f"{finalists_text}"
                    f"{others_text}"
                )
            else:
                final_msg_text = (
                    f"📊 <b>Викторина завершена.</b>\n\n"
                    f"{super_prize_block}"
                    f"Победитель не определен: ни один из участников не набрал баллов."
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
                stats_lines = [
                    f"{idx}. {p['name']} — {p['points']} баллов"
                    for idx, p in enumerate(sorted_scores, start=1)
                ]
                accuracy_lines = [
                    f"- {p['name']}: {p['points'] // 10} из {perfect_target_questions} взятых вопросов ({p['points']} баллов)"
                    for p in sorted_scores
                ]
                raw_game_data = (
                    f"Всего вопросов: {perfect_target_questions}\n"
                    f"Табло по очкам:\n" + ("\n".join(stats_lines) if stats_lines else "Нет игроков с очками") + "\n\n"
                    f"Точность ответов:\n" + ("\n".join(accuracy_lines) if accuracy_lines else "Нет правильных ответов") + "\n\n"
                    f"Обладатели супер-приза: {', '.join(perfect_players) if perfect_players else 'нет'}"
                )

                host_prompt = (
                    f"Проанализируй данные завершившейся викторины и сформируй итоговую речь ведущего.\n\n"
                    f"ДАННЫЕ ИГРЫ:\n{raw_game_data}\n\n"
                    f"ТРЕБОВАНИЯ:\n"
                    f"- Тон: сухой, емкий, деловой.\n"
                    f"- Запрещены: метафоры, пафос, восторженные эпитеты, эмодзи и местоимение «мы».\n"
                    f"- Формат: строго 4–6 предложений с объявлением победителя и счета."
                )
                host_resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=host_prompt
                )
                host_words = (host_resp.text or "").strip().replace("*", "")
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

def flush_media_group(media_group_id):
    with media_group_lock:
        group_data = MEDIA_GROUPS.pop(media_group_id, None)

    if not group_data:
        return

    messages = group_data.get("messages", [])
    caption = group_data.get("caption", "")
    chat_id = group_data.get("chat_id")

    if not messages or not chat_id:
        return

    try:
        bot.send_chat_action(chat_id, 'typing')
    except Exception:
        pass

    photos = []
    for msg in messages:
        part = extract_media_part(msg)
        if part:
            photos.append(part)

    if not photos:
        return

    first_msg = messages[0]
    caption_lower = caption.lower()
    is_mafia_cmd = ("#мафия" in caption_lower) or ("мафия" in caption_lower)
    is_kaleidoscope_cmd = any(word in caption_lower for word in ["калейдоскоп", "оформи"])

    if is_mafia_cmd:
        clean_comment = clean_cmd_prefix(caption)
        prompt = build_mafia_chronicle_prompt(clean_comment)
        contents = [prompt] + photos
        process_ai_response(chat_id, first_msg.message_id, first_msg, contents, 'private', use_history=False)
    elif is_kaleidoscope_cmd:
        clean_comment = clean_cmd_prefix(caption)
        prompt = build_kaleidoscope_prompt("Материал из прикрепленных фото/сканов.", clean_comment)
        contents = [prompt] + photos
        process_ai_response(chat_id, first_msg.message_id, first_msg, contents, 'private', use_history=False)
    else:
        prompt = f"Изучи этот материал. Транскрибируй его смысловое наполнение и перепиши от своего лица. {caption}".strip()
        contents = [prompt] + photos
        process_ai_response(chat_id, first_msg.message_id, first_msg, contents, 'private', use_history=True)

def send_long_message(chat_id, message, text, chat_type):
    text = text.replace("*", "")
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
            print(f"Ошибка отправки HTML, отправка без разметки: {e}")
            plain_chunk = re.sub(r"<[^>]+>", "", chunk)
            try:
                if chat_type == 'channel' or idx > 0:
                    bot.send_message(chat_id, plain_chunk)
                else:
                    bot.reply_to(message, plain_chunk)
            except Exception as inner_e:
                print(f"Ошибка отправки сообщения: {inner_e}")

def process_ai_response(chat_id, message_id, message, contents, chat_type, use_history=True):
    history_data = load_history(chat_id) if use_history else []
    
    try:
        chat = client.chats.create(
            model="gemini-3.8-flash",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            ),
            history=history_data
        )
        
        response = chat.send_message(contents)
        gemini_response = (response.text or "").strip().replace("*", "")
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
            is_rejected = (
                "работа не принята" in resp_lower
                or "отказано" in resp_lower
                or "не соответствует" in resp_lower
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
            elif is_rejected:
                Timer(120, delete_later, args=[chat_id, message.message_id]).start()

    if use_history:
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

    # Обработка вступлений и выходов участников в чате BREST_TALK_ID (включая ранний старт)
    if getattr(update, "chat_member", None):
        cm = update.chat_member
        if cm.chat and cm.chat.id == BREST_TALK_ID:
            old_status = getattr(cm.old_chat_member, "status", "")
            new_status = getattr(cm.new_chat_member, "status", "")
            joined_user = getattr(cm.new_chat_member, "user", None)

            if joined_user and not getattr(joined_user, "is_bot", False):
                joined_uid_str = str(joined_user.id)

                # Вступление в чат
                if old_status in ['left', 'kicked'] and new_status in ['member', 'restricted', 'administrator', 'creator']:
                    invite_obj = getattr(cm, "invite_link", None)
                    invite_link_str = getattr(invite_obj, "invite_link", None) if invite_obj else None

                    if invite_link_str:
                        notify_payload = None
                        with refs_lock:
                            data = load_refs_data()
                            inviter_id = data.get("links", {}).get(invite_link_str)

                            if inviter_id and str(inviter_id) != joined_uid_str and inviter_id in data.get("users", {}):
                                already_counted = any(
                                    joined_uid_str in u_obj.get("referrals", [])
                                    for u_obj in data["users"].values()
                                )
                                if not already_counted:
                                    inviter_data = data["users"][inviter_id]
                                    inviter_data["referrals"].append(joined_uid_str)
                                    save_refs_data(data)
                                    tickets = len(inviter_data["referrals"])
                                    inviter_name = html.escape(inviter_data.get("name") or "Участник")
                                    notify_payload = (inviter_name, tickets)

                        if notify_payload:
                            inviter_name, tickets = notify_payload
                            if tickets < 3:
                                pub_text = (
                                    f"🎉 <b>{inviter_name}</b> пригласил(а) участника! "
                                    f"У тебя уже <b>{tickets} билет(а) из 3</b> минимальных для входа в розыгрыш <b>Mi Band 11</b>! "
                                    f"Хочешь увеличить шансы — увеличивай количество билетов! 🚀"
                                )
                            elif tickets == 3:
                                pub_text = (
                                    f"🔥 <b>{inviter_name}</b> пригласил(а) 3-го участника! "
                                    f"У тебя уже <b>3 билета из 3</b> — ты проходишь в барабан розыгрыша <b>Mi Band 11</b>! 🎟 "
                                    f"Хочешь увеличить шансы выигрыша — получай больше билетов! 🚀"
                                )
                            else:
                                pub_text = (
                                    f"⚡️ <b>{inviter_name}</b> пригласил(а) ещё одного участника! "
                                    f"У тебя уже <b>{tickets} билетов</b> в барабане <b>Mi Band 11</b>! "
                                    f"Шансы на победу растут! 🏆"
                                )

                            bot_info = get_bot_info()
                            bot_username = bot_info.username if (bot_info and bot_info.username) else ""
                            pub_markup = telebot.types.InlineKeyboardMarkup()
                            if bot_username:
                                pub_markup.add(
                                    telebot.types.InlineKeyboardButton(
                                        text="🎁 Получить свою ссылку для розыгрыша",
                                        url=f"https://t.me/{bot_username}?start=ref"
                                    )
                                )
                            try:
                                bot.send_message(
                                    BREST_TALK_ID,
                                    pub_text,
                                    reply_markup=pub_markup if bot_username else None,
                                    parse_mode="HTML"
                                )
                            except Exception as e:
                                print(f"Ошибка отправки реферального уведомления в чат: {e}")

                # Выход или удаление из чата
                elif new_status in ['left', 'kicked']:
                    with refs_lock:
                        data = load_refs_data()
                        modified = False
                        for u_obj in data.get("users", {}).values():
                            if joined_uid_str in u_obj.get("referrals", []):
                                u_obj["referrals"] = [
                                    r for r in u_obj["referrals"] if r != joined_uid_str
                                ]
                                modified = True
                        if modified:
                            save_refs_data(data)
        return '!', 200

    if update.callback_query:
        cq = update.callback_query
        data = cq.data or ""

        if data == "refresh_ref_stats":
            if cq.message and cq.from_user:
                send_or_edit_ref_cabinet(
                    chat_id=cq.message.chat.id,
                    user=cq.from_user,
                    edit_msg_id=cq.message.message_id,
                    callback_query_id=cq.id
                )
            return '!', 200

        if data in ("run_live_drum", "run_test_drum"):
            if cq.from_user.id != ADMIN_ID:
                try:
                    bot.answer_callback_query(cq.id, "Запускать барабан может только администратор!", show_alert=True)
                except Exception:
                    pass
                return '!', 200

            try:
                bot.answer_callback_query(cq.id, "🎰 Запускаем барабан!", show_alert=False)
            except Exception:
                pass

            if data == "run_test_drum":
                demo_participants = [
                    {"user_id": "demo_1", "name": "Андрей (@andrey_brest)", "tickets": 3},
                    {"user_id": "demo_2", "name": "Максим (@max_power)", "tickets": 7},
                    {"user_id": "demo_3", "name": "Елена (@helen_art)", "tickets": 5},
                    {"user_id": "demo_4", "name": "Дмитрий (@dmitry_k)", "tickets": 4}
                ]
                Thread(
                    target=run_drum_animation_thread,
                    args=(cq.message.chat.id, cq.message.message_id, demo_participants, True),
                    daemon=True
                ).start()
            else:
                def _verify_and_spin(c_id, m_id):
                    qualified, _ = verify_and_build_giveaway_pool()
                    run_drum_animation_thread(c_id, m_id, qualified, False)

                Thread(
                    target=_verify_and_spin,
                    args=(cq.message.chat.id, cq.message.message_id),
                    daemon=True
                ).start()
            return '!', 200

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
                target_count = QUIZ_QUORUM_TARGET

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
            first_name = (cq.from_user.first_name or "Участник").strip()
            username = cq.from_user.username
            uname = f"{first_name} (@{username})" if username else first_name

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

    msg = update.message
    if msg:
        chat_id = msg.chat.id
        chat_type = msg.chat.type
        user_id = msg.from_user.id if msg.from_user else None
        user_text = msg.caption or msg.text or ""

        # Фиксация активности любого пользователя в чате BREST_TALK_ID
        if chat_id == BREST_TALK_ID and msg.from_user:
            mark_user_active_in_brest_talk(msg.from_user)

        cmd_token = user_text.strip().split()[0].lower() if user_text.strip() else ""
        cmd_clean = cmd_token.split('@')[0]

        # Команды управления розыгрышем для ADMIN_ID (выполняются в фоновых потоках во избежание таймаута вебхука)
        if user_id == ADMIN_ID and cmd_clean in ['/ref_top', '!рефтоп']:
            try:
                status_msg = bot.send_message(
                    chat_id,
                    "⏳ <b>Проверяю актуальный состав участников и присутствие друзей в чате...</b>",
                    parse_mode="HTML"
                )
                Thread(
                    target=process_ref_top_async,
                    args=(chat_id, status_msg.message_id),
                    daemon=True
                ).start()
            except Exception as e:
                print(f"Ошибка запуска /ref_top: {e}")
            return '!', 200

        if user_id == ADMIN_ID and cmd_clean == '/start_giveaway':
            try:
                bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass

            try:
                status_msg = bot.send_message(
                    chat_id,
                    "⏳ <b>Проверяю актуальный состав участников и присутствие друзей в чате...</b>",
                    parse_mode="HTML"
                )
                Thread(
                    target=process_start_giveaway_async,
                    args=(chat_id, status_msg.message_id),
                    daemon=True
                ).start()
            except Exception as e:
                print(f"Ошибка запуска /start_giveaway: {e}")
            return '!', 200

        if user_id == ADMIN_ID and cmd_clean == '/test_giveaway':
            try:
                bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass

            demo_participants = [
                {"user_id": "demo_1", "name": "Андрей (@andrey_brest)", "tickets": 3},
                {"user_id": "demo_2", "name": "Максим (@max_power)", "tickets": 7},
                {"user_id": "demo_3", "name": "Елена (@helen_art)", "tickets": 5},
                {"user_id": "demo_4", "name": "Дмитрий (@dmitry_k)", "tickets": 4}
            ]
            total_demo_tickets = sum(p["tickets"] for p in demo_participants)
            demo_lines = [
                f"• <b>{html.escape(p['name'])}</b> — 🎟 <b>{p['tickets']}</b> билет(ов)"
                for p in demo_participants
            ]

            test_preview_text = (
                f"🧪 <b>[ТЕСТОВЫЙ РЕЖИМ] РЕПЕТИЦИЯ РОЗЫГРЫША 2 MI BAND 11</b> ⌚️\n"
                f"━━━━━━━━━━━━━━\n"
                f"👥 <b>Демо-участники в барабане ({len(demo_participants)}):</b>\n"
                + "\n".join(demo_lines)
                + f"\n━━━━━━━━━━━━━━\n"
                f"🎟 <b>Всего билетов в барабане:</b> {total_demo_tickets}\n\n"
                f"👇 <i>Нажмите кнопку ниже для проверки живой анимации под запись экрана!</i>"
            )
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton(
                    text="🧪 ТЕСТОВЫЙ ЗАПУСК БАРАБАНА",
                    callback_data="run_test_drum"
                )
            )
            bot.send_message(chat_id, test_preview_text, reply_markup=markup, parse_mode="HTML")
            return '!', 200

        if user_id == ADMIN_ID and cmd_clean == '/stop_quiz':
            try:
                bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
            with lobby_lock:
                if QUIZ_LOBBY["is_collecting"]:
                    QUIZ_LOBBY["is_collecting"] = False
                    count = len(QUIZ_LOBBY["players"])
                    if QUIZ_LOBBY["cancel_timer"]:
                        QUIZ_LOBBY["cancel_timer"].cancel()
                        QUIZ_LOBBY["cancel_timer"] = None
                    try:
                        bot.edit_message_text(
                            chat_id=QUIZ_LOBBY["chat_id"],
                            message_id=QUIZ_LOBBY["msg_id"],
                            text=f"Кворум не собран: {count} из {QUIZ_QUORUM_TARGET}. Сбор отменён администратором.",
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
                bot.delete_message(chat_id, msg.message_id)
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
                    text=f"🙋‍♂️ Я в деле (0 из {QUIZ_QUORUM_TARGET})",
                    callback_data="lobby_join"
                ))

                lobby_text = (
                    f"🎲 <b>Объявляется сбор участников на интеллектуальную викторину!</b>\n\n"
                    f"Для старта игры нужен кворум: минимум <b>{QUIZ_QUORUM_TARGET} участников</b>.\n"
                    f"Жмите кнопку ниже, чтобы занять место за столом. На сбор — <b>20 минут</b>."
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
                [f"{idx+1}. {html.escape(p['name'])} — {p['points']} баллов" for idx, p in enumerate(sorted_scores)]
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
                bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
            with lobby_lock:
                if QUIZ_LOBBY["is_collecting"]:
                    QUIZ_LOBBY["is_collecting"] = False
                    if QUIZ_LOBBY["cancel_timer"]:
                        QUIZ_LOBBY["cancel_timer"].cancel()
                        QUIZ_LOBBY["cancel_timer"] = None
                    try:
                        bot.edit_message_reply_markup(QUIZ_LOBBY["chat_id"], QUIZ_LOBBY["msg_id"], reply_markup=None)
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
            raw_stripped = user_text.strip().lower()

            if user_id != ADMIN_ID:
                if cmd_clean in ('/start', '/ref') and msg.from_user:
                    send_or_edit_ref_cabinet(chat_id, msg.from_user)
                return '!', 200

            # Ветка для ADMIN_ID в личных сообщениях
            if (cmd_clean == '/ref' or (cmd_clean == '/start' and 'ref' in raw_stripped.split())) and msg.from_user:
                send_or_edit_ref_cabinet(chat_id, msg.from_user)
                return '!', 200

            if cmd_clean == '/start':
                bot.send_message(chat_id, "Приветствую, шеф. (Для открытия реферального кабинета используйте /ref)")
                return '!', 200

            if msg.photo and getattr(msg, 'media_group_id', None):
                mg_id = str(msg.media_group_id)
                with media_group_lock:
                    if mg_id not in MEDIA_GROUPS:
                        MEDIA_GROUPS[mg_id] = {
                            "messages": [],
                            "caption": "",
                            "chat_id": chat_id,
                            "timer": None
                        }
                    MEDIA_GROUPS[mg_id]["messages"].append(msg)
                    if msg.caption:
                        MEDIA_GROUPS[mg_id]["caption"] = msg.caption
                    if MEDIA_GROUPS[mg_id]["timer"]:
                        MEDIA_GROUPS[mg_id]["timer"].cancel()
                    timer = Timer(4.0, flush_media_group, args=[mg_id])
                    MEDIA_GROUPS[mg_id]["timer"] = timer
                    timer.start()
                return '!', 200

            try:
                bot.send_chat_action(chat_id, 'typing')
            except Exception:
                pass

            media_part = extract_media_part(msg)
            if not media_part and msg.reply_to_message:
                media_part = extract_media_part(msg.reply_to_message)

            reply_text = ""
            if msg.reply_to_message:
                reply_text = msg.reply_to_message.text or msg.reply_to_message.caption or ""

            contents = []
            user_text_lower = user_text.lower()
            reply_text_lower = reply_text.lower()

            is_mafia_cmd = (
                ("#мафия" in user_text_lower)
                or ("мафия" in user_text_lower)
                or ("#мафия" in reply_text_lower)
                or ("мафия" in reply_text_lower)
            )
            is_kaleidoscope_cmd = any(
                word in user_text_lower or word in reply_text_lower
                for word in ["калейдоскоп", "оформи"]
            )

            if media_part:
                if is_mafia_cmd:
                    combined_comment = clean_cmd_prefix(f"{user_text}\n{reply_text}".strip())
                    prompt = build_mafia_chronicle_prompt(combined_comment)
                    contents = [prompt, media_part]
                    use_hist = False
                elif is_kaleidoscope_cmd:
                    clean_comment = clean_cmd_prefix(user_text)
                    prompt = build_kaleidoscope_prompt("Материал из прикрепленного файла/изображения.", clean_comment)
                    contents = [prompt, media_part]
                    use_hist = False
                else:
                    prompt = "Изучи этот материал. Транскрибируй его смысловое наполнение и перепиши от своего лица."
                    contents = [prompt, media_part]
                    use_hist = True
            else:
                if is_mafia_cmd:
                    raw_text = f"{reply_text}\n{user_text}".strip() if reply_text else user_text
                    prompt = build_mafia_chronicle_prompt(clean_cmd_prefix(raw_text))
                    contents.append(prompt)
                    use_hist = False
                elif is_kaleidoscope_cmd:
                    raw_text = reply_text if reply_text else user_text
                    clean_material = clean_cmd_prefix(raw_text)
                    prompt = build_kaleidoscope_prompt(clean_material)
                    contents.append(prompt)
                    use_hist = False
                else:
                    contents.append(user_text or "Поделись своими мыслями.")
                    use_hist = True

            thread = Thread(target=process_ai_response, args=(chat_id, msg.message_id, msg, contents, chat_type, use_hist))
            thread.start()
            return '!', 200

        if chat_type in ['group', 'supergroup', 'channel']:
            if chat_id not in ALLOWED_GROUP_IDS:
                return '!', 200

            is_auto = getattr(msg, 'is_automatic_forward', False)
            from_channel_id, orig_msg_id = get_forward_info(msg)
            if is_auto and from_channel_id == CHANNEL_ID and chat_id == BREST_TALK_ID:
                thread = Thread(target=process_channel_forward, args=(chat_id, msg, orig_msg_id))
                thread.start()
                return '!', 200

            if chat_type != 'channel':
                is_contest = bool(msg.photo and "#конкурс" in user_text.lower())

                if chat_id == BREST_TALK_ID:
                    if not is_contest:
                        return '!', 200
                else:
                    bot_info = get_bot_info()
                    bot_id = bot_info.id if bot_info else None
                    bot_username = f"@{bot_info.username}".lower() if (bot_info and bot_info.username) else ""

                    is_reply_to_bot = bool(
                        msg.reply_to_message
                        and msg.reply_to_message.from_user
                        and bot_id is not None
                        and msg.reply_to_message.from_user.id == bot_id
                    )
                    is_mentioned = bool(bot_username and bot_username in user_text.lower())

                    if not (is_reply_to_bot or is_mentioned or is_contest):
                        return '!', 200

            try:
                bot.send_chat_action(chat_id, 'typing')
            except Exception:
                pass
            
            reply_text = ""
            if msg.reply_to_message:
                if msg.reply_to_message.text:
                    reply_text = msg.reply_to_message.text
                elif msg.reply_to_message.caption:
                    reply_text = msg.reply_to_message.caption
            
            if reply_text:
                user_text = f"[ВНИМАНИЕ: Пользователь отвечает на этот текст: «{reply_text}»]\n{user_text}".strip()

            contents = []
            if user_text:
                contents.append(user_text)
            else:
                contents.append("Взгляни на это.")

            media_part = extract_media_part(msg)
            if not media_part and msg.reply_to_message:
                media_part = extract_media_part(msg.reply_to_message)
            if media_part:
                contents.append(media_part)

            thread = Thread(target=process_ai_response, args=(chat_id, msg.message_id, msg, contents, chat_type, True))
            thread.start()

    return '!', 200

if __name__ == "__main__":
    render_url = (os.environ.get('RENDER_EXTERNAL_URL') or '').rstrip('/')
    if render_url and TOKEN:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(
            url=f"{render_url}/{TOKEN}",
            allowed_updates=["message", "callback_query", "chat_member"]
        )
    try:
        bot.set_my_commands([
            telebot.types.BotCommand("/start", "🎁 Получить ссылку для розыгрыша Mi Band 11"),
            telebot.types.BotCommand("/ref", "🔗 Моя реферальная ссылка и билеты"),
        ])
    except Exception as e:
        print(f"Ошибка установки команд: {e}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)
