import os
import threading
import html
import random
import json

from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReactionTypeEmoji
import sqlite3

# ══════════════════════════════════════════════════════════════
#  НАСТРОЙКИ — переменные окружения (Render: Settings → Env Vars)
# ══════════════════════════════════════════════════════════════
BOT_TOKEN      = os.getenv("BOT_TOKEN",      "REPLACE_ME")
ADMIN_ID       = int(os.getenv("ADMIN_ID",   "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))

bot     = telebot.TeleBot(BOT_TOKEN)
db_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════
#  БАЗА — только счётчики. Полные сообщения хранит КАНАЛ.
#
#  Архитектура:
#    Канал (LOG_CHANNEL_ID) = архив всех сообщений.
#                             Поиск — встроенным поиском Telegram.
#    SQLite               = быстрые счётчики для статистики.
# ══════════════════════════════════════════════════════════════
conn   = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.executescript('''
    CREATE TABLE IF NOT EXISTS user_stats (
        chat_id    INTEGER,
        chat_title TEXT,
        user_id    INTEGER,
        username   TEXT,
        msg_count  INTEGER DEFAULT 0,
        mood_pos   INTEGER DEFAULT 0,
        mood_neg   INTEGER DEFAULT 0,
        last_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS reputation (
        chat_id  INTEGER,
        user_id  INTEGER,
        username TEXT,
        score    INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS watchlist (
        user_id  INTEGER PRIMARY KEY,
        username TEXT,
        added_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
''')
conn.commit()


# ══════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ — БД
# ══════════════════════════════════════════════════════════════
def db_get_chats():
    with db_lock:
        return cursor.execute(
            'SELECT chat_id, chat_title, SUM(msg_count) as total '
            'FROM user_stats GROUP BY chat_id ORDER BY total DESC'
        ).fetchall()

def db_top_users(chat_id, limit=5):
    with db_lock:
        return cursor.execute(
            'SELECT username, msg_count FROM user_stats '
            'WHERE chat_id = ? ORDER BY msg_count DESC LIMIT ?',
            (chat_id, limit)
        ).fetchall()

def db_top_rep(chat_id, limit=3):
    with db_lock:
        return cursor.execute(
            'SELECT username, score FROM reputation '
            'WHERE chat_id = ? AND score > 0 ORDER BY score DESC LIMIT ?',
            (chat_id, limit)
        ).fetchall()

def db_chat_mood(chat_id=None):
    """Возвращает (pos_total, neg_total) для чата или всех чатов."""
    q = 'SELECT SUM(mood_pos), SUM(mood_neg) FROM user_stats'
    p = ()
    if chat_id:
        q += ' WHERE chat_id = ?'
        p  = (chat_id,)
    with db_lock:
        row = cursor.execute(q, p).fetchone()
    pos = row[0] or 0
    neg = row[1] or 0
    return pos, neg

def mood_line(chat_id=None) -> str:
    pos, neg = db_chat_mood(chat_id)
    total = pos + neg
    if total == 0:
        return "😶 Настроение: данных нет"
    pct_p = round(pos / total * 100)
    pct_n = 100 - pct_p
    emoji = '😊' if pct_p > 60 else ('😡' if pct_n > 60 else '😐')
    return f"{emoji} {pct_p}% / {pct_n}% (по {total} сообщ.)"


# ══════════════════════════════════════════════════════════════
#  НАСТРОЕНИЕ — разговорный русский с матом
# ══════════════════════════════════════════════════════════════
POSITIVE = [
    'огонь', 'топ', 'круто', 'спасибо', 'красавчик', 'красавица',
    'кайф', 'бомба', 'зачёт', 'зачет', 'лучший', 'обожаю', 'отлично',
    'супер', 'ахах', 'хаха', 'ахахах', 'лол', 'красота', 'молодец',
    'прикол', 'кек', 'хорошо', 'норм', 'четко', 'чётко', 'умница',
    'класс', 'шикарно', 'угар', 'збс', 'заебись', 'пиздато',
    'охуенно', 'охуительно', 'ахуенно',
    '🔥', '👍', '😂', '❤️', '💪', '👏', '🤣', '😍', '💯', '🎉', '🤩',
]

NEGATIVE = [
    'хуево', 'пиздец', 'отстой', 'ужас', 'хуйня', 'говно', 'дерьмо',
    'заебало', 'заебал', 'нахуй', 'похуй', 'бесит', 'тупо', 'кринж',
    'фу', 'ненавижу', 'достало', 'достал', 'плохо', 'ужасно',
    'блять', 'бля', 'сука', 'мразь', 'идиот', 'дебил',
    '💀', '👎', '🤮', '😤', '😡', '🖕', '🤬', '😒',
]

def analyze_mood(text: str):
    if not text:
        return None
    t = text.lower()
    pos = sum(1 for w in POSITIVE if w in t)
    neg = sum(1 for w in NEGATIVE if w in t)
    if pos == 0 and neg == 0:
        return None
    return 'positive' if pos >= neg else 'negative'


# ══════════════════════════════════════════════════════════════
#  ВОТЧЛИСТ
# ══════════════════════════════════════════════════════════════
def is_watched(user_id: int) -> bool:
    with db_lock:
        return bool(cursor.execute(
            'SELECT 1 FROM watchlist WHERE user_id = ?', (user_id,)
        ).fetchone())

def get_watchlist():
    with db_lock:
        return cursor.execute(
            'SELECT user_id, username, added_at FROM watchlist'
        ).fetchall()

def add_to_watch(user_id: int, username: str):
    with db_lock:
        cursor.execute(
            'INSERT OR REPLACE INTO watchlist (user_id, username) VALUES (?, ?)',
            (user_id, username)
        )
        conn.commit()

def remove_from_watch(user_id: int):
    with db_lock:
        cursor.execute('DELETE FROM watchlist WHERE user_id = ?', (user_id,))
        conn.commit()


# ══════════════════════════════════════════════════════════════
#  /watch — в группе: ответь на сообщение + /watch
# ══════════════════════════════════════════════════════════════
@bot.message_handler(
    commands=['watch'],
    func=lambda m: m.chat.type in ['group', 'supergroup']
)
def cmd_watch(message):
    if message.from_user.id != ADMIN_ID:
        return
    if not message.reply_to_message:
        bot.reply_to(message, "⚠️ Ответь на сообщение нужного пользователя")
        return
    target = message.reply_to_message.from_user
    if target.is_bot:
        return
    uname = target.username or target.first_name
    add_to_watch(target.id, uname)
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass
    bot.send_message(
        ADMIN_ID,
        f"👁 <b>@{html.escape(uname)}</b> добавлен.\n"
        f"Чат: <i>{html.escape(message.chat.title or '')}</i>",
        parse_mode='HTML'
    )


# ══════════════════════════════════════════════════════════════
#  ПАНЕЛЬ УПРАВЛЕНИЯ — только в ЛС у админа
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=['panel', 'start'], func=lambda m: m.chat.type == 'private')
def pm_panel(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "👋 Привет! Я бот-помощник для групп.")
        return

    chats = db_get_chats()
    watched = get_watchlist()

    # Строим список чатов
    chats_text = ""
    for chat_id, title, total in chats:
        pos, neg = db_chat_mood(chat_id)
        mood_tot = pos + neg
        mood_str = f"{round(pos/mood_tot*100)}%+" if mood_tot else "—"
        chats_text += f"  🔹 <b>{html.escape(title or str(chat_id))}</b>\n"
        chats_text += f"      {total} сообщ. · настроение {mood_str}\n"

    watch_text = f"{len(watched)} чел." if watched else "пусто"

    # Получаем ссылку на канал
    try:
        channel = bot.get_chat(LOG_CHANNEL_ID)
        channel_link = f"<a href='https://t.me/c/{str(LOG_CHANNEL_ID).replace('-100', '')}/1'>📺 {html.escape(channel.title or 'Канал')}</a>"
    except Exception:
        channel_link = f"<code>{LOG_CHANNEL_ID}</code>"

    text = (
        f"⚙️ <b>Управление</b>\n\n"
        f"📡 <b>Активных чатов:</b> {len(chats)}\n"
        f"{chats_text}\n"
        f"👁 <b>Вотчлист:</b> {watch_text}\n"
        f"🗄 <b>Архив:</b> {channel_link}\n\n"
        f"<i>Поиск по архиву — встроенным поиском Telegram в канале.</i>"
    )

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👁 Вотчлист", callback_data="show_watchlist"),
        InlineKeyboardButton("🎭 Писать от бота", callback_data="face_menu"),
    )
    markup.add(
        InlineKeyboardButton("🏆 Топ активности", callback_data="top_menu"),
    )

    bot.send_message(message.chat.id, text, parse_mode='HTML',
                     reply_markup=markup, disable_web_page_preview=True)


# ── Вотчлист через кнопку ──────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == 'show_watchlist')
def cb_watchlist(call):
    if call.from_user.id != ADMIN_ID:
        return
    rows = get_watchlist()
    bot.answer_callback_query(call.id)
    if not rows:
        bot.send_message(call.message.chat.id, "👁 Вотчлист пуст.")
        return
    markup = InlineKeyboardMarkup()
    text   = "👁 <b>Вотчлист:</b>\n\n"
    for uid, uname, added in rows:
        text   += f"• @{html.escape(uname or '?')} · <code>{uid}</code> · с {added[:10]}\n"
        markup.add(InlineKeyboardButton(
            f"❌ Снять @{uname}", callback_data=f"unwatch_{uid}"
        ))
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="back_panel"))
    bot.send_message(call.message.chat.id, text, parse_mode='HTML', reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith('unwatch_'))
def cb_unwatch(call):
    if call.from_user.id != ADMIN_ID:
        return
    uid = int(call.data.split('_')[1])
    remove_from_watch(uid)
    bot.answer_callback_query(call.id, "Снят.")
    try:
        bot.edit_message_text(
            "✅ Снят с наблюдения.",
            call.message.chat.id, call.message.message_id
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == 'back_panel')
def cb_back(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    # Переиспользуем логику панели
    class FakeMsg:
        chat = call.message.chat
        from_user = call.from_user
    pm_panel(FakeMsg())


# ── Топ активности через кнопку ───────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == 'top_menu')
def cb_top_menu(call):
    if call.from_user.id != ADMIN_ID:
        return
    chats = db_get_chats()
    bot.answer_callback_query(call.id)
    if not chats:
        bot.send_message(call.message.chat.id, "Нет данных.")
        return
    markup = InlineKeyboardMarkup()
    for chat_id, title, total in chats:
        markup.add(InlineKeyboardButton(
            f"📊 {title or chat_id} ({total})",
            callback_data=f"top_{chat_id}"
        ))
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="back_panel"))
    bot.send_message(call.message.chat.id, "Выбери чат:", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith('top_') and not c.data.startswith('top_menu'))
def cb_top_chat(call):
    if call.from_user.id != ADMIN_ID:
        return
    chat_id = int(call.data.split('_')[1])
    bot.answer_callback_query(call.id)

    top_u = db_top_users(chat_id, 10)
    top_r = db_top_rep(chat_id, 5)
    pos, neg = db_chat_mood(chat_id)
    mood_tot = pos + neg

    medals = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
    text = "📊 <b>Активность:</b>\n"
    for i, (uname, cnt) in enumerate(top_u):
        text += f"{medals[i]} @{html.escape(uname or '?')} — {cnt}\n"

    if top_r:
        text += "\n🌟 <b>Репутация:</b>\n"
        for uname, score in top_r:
            text += f"⭐ @{html.escape(uname or '?')} — {score}\n"

    if mood_tot:
        pct = round(pos / mood_tot * 100)
        text += f"\n{mood_line(chat_id)}"

    bot.send_message(call.message.chat.id, text, parse_mode='HTML')


# ── /face через кнопку ────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == 'face_menu')
def cb_face_menu(call):
    if call.from_user.id != ADMIN_ID:
        return
    chats = db_get_chats()
    bot.answer_callback_query(call.id)
    if not chats:
        bot.send_message(call.message.chat.id, "Нет чатов.")
        return
    markup = InlineKeyboardMarkup()
    for chat_id, title, _ in chats:
        markup.add(InlineKeyboardButton(
            f"🗣 {title or chat_id}", callback_data=f"face_{chat_id}"
        ))
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="back_panel"))
    bot.send_message(call.message.chat.id, "🎭 Выбери чат:", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith('face_'))
def cb_face_chat(call):
    if call.from_user.id != ADMIN_ID:
        return
    chat_id = call.data.split('_')[1]
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "✍️ Напиши текст:")
    bot.register_next_step_handler(
        msg,
        lambda m: (
            bot.send_message(chat_id, m.text) if m.text
            else bot.send_message(m.chat.id, "❌ Только текст.")
        )
    )


# ══════════════════════════════════════════════════════════════
#  /gif — гифка для порталов (только в ЛС)
# ══════════════════════════════════════════════════════════════
@bot.message_handler(commands=['gif'], func=lambda m: m.chat.type == 'private')
def cmd_gif(message):
    if message.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(message.chat.id, "🌀 Пришли гифку для порталов:")
    bot.register_next_step_handler(msg, _save_gif)

def _save_gif(message):
    if not message.animation and not message.document:
        bot.send_message(message.chat.id, "❌ Это не гифка.")
        return
    fid = message.animation.file_id if message.animation else message.document.file_id
    with db_lock:
        cursor.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('portal_gif', fid))
        conn.commit()
    bot.send_message(message.chat.id, "✅ Гифка обновлена!")


# ══════════════════════════════════════════════════════════════
#  ПУБЛИЧНЫЕ КОМАНДЫ В ГРУППАХ
# ══════════════════════════════════════════════════════════════

# /whoactive — топ активности (публичная)
@bot.message_handler(commands=['whoactive', 'top'])
def cmd_whoactive(message):
    if message.chat.type == 'private':
        return
    top_u = db_top_users(message.chat.id, 5)
    top_r = db_top_rep(message.chat.id, 3)
    medals = ['🥇','🥈','🥉','🏅','🏅']
    text = "🔥 <b>Топ активности:</b>\n"
    for i, (uname, cnt) in enumerate(top_u):
        text += f"{medals[i]} @{html.escape(uname or '?')} — {cnt} сообщ.\n"
    if top_r:
        text += "\n🌟 <b>Репутация:</b>\n"
        for uname, score in top_r:
            text += f"⭐ @{html.escape(uname or '?')} — {score}\n"
    bot.reply_to(message, text, parse_mode='HTML')


# /info — нейтральный, ничего не палит
@bot.message_handler(commands=['info'])
def cmd_info(message):
    if message.chat.type == 'private':
        # В ЛС у не-админа — ничего интересного
        if message.from_user.id != ADMIN_ID:
            bot.send_message(message.chat.id, "👋 Привет! Используй /panel для управления.")
            return
        # Редирект на панель
        pm_panel(message)
        return

    # В группе — нейтральная статистика без упоминания логирования
    top_u = db_top_users(message.chat.id, 3)
    top_name = f"@{top_u[0][0]}" if top_u else "—"
    with db_lock:
        total_chat = cursor.execute(
            'SELECT SUM(msg_count) FROM user_stats WHERE chat_id = ?',
            (message.chat.id,)
        ).fetchone()[0] or 0
        users_count = cursor.execute(
            'SELECT COUNT(*) FROM user_stats WHERE chat_id = ?',
            (message.chat.id,)
        ).fetchone()[0] or 0

    text = (
        f"🤖 Бот работает в штатном режиме\n\n"
        f"👥 Участников замечено: <b>{users_count}</b>\n"
        f"📨 Сообщений обработано: <b>{total_chat}</b>\n"
        f"🏆 Самый активный: <b>{top_name}</b>"
    )
    bot.reply_to(message, text, parse_mode='HTML')


# /portal — случайная беседа
@bot.message_handler(commands=['portal'])
def cmd_portal(message):
    if message.chat.type == 'private':
        return
    chats = [c for c in db_get_chats() if c[0] != message.chat.id]
    if not chats:
        bot.reply_to(message, "🌀 Некуда открывать портал!")
        return
    target_id, target_title, _ = random.choice(chats)
    portal_text = message.text.replace('/portal', '', 1).strip()

    with db_lock:
        gif_row = cursor.execute(
            'SELECT value FROM settings WHERE key = "portal_gif"'
        ).fetchone()
    anim = gif_row[0] if gif_row else None

    try:
        if anim:
            bot.send_animation(message.chat.id, anim,
                               caption="🌀 <b>Портал открыт!</b>", parse_mode='HTML')
        else:
            bot.reply_to(message, "🌀 <b>Портал открыт!</b>", parse_mode='HTML')
    except Exception:
        pass

    try:
        bot.send_message(
            target_id,
            f"🌀 <b>Портал из «{html.escape(message.chat.title or '?')}»</b>\n"
            f"@{message.from_user.username or message.from_user.first_name}: "
            f"{html.escape(portal_text) if portal_text else 'Приветствуем!'}",
            parse_mode='HTML'
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  ГЛАВНЫЙ ОБРАБОТЧИК — запись, канал, вотчлист, репутация
# ══════════════════════════════════════════════════════════════
@bot.message_handler(
    func=lambda m: m.chat.type in ['group', 'supergroup'],
    content_types=['text','photo','video','voice','document','animation','sticker']
)
def handle_group_message(message):
    if message.text and message.text.startswith('/'):
        return

    chat_id  = message.chat.id
    title    = message.chat.title
    uid      = message.from_user.id
    uname    = message.from_user.username or message.from_user.first_name
    text     = message.text or message.caption or ""
    mood     = analyze_mood(text)
    tl       = text.lower().strip()

    # 0. СЧЁТЧИК ДЛЯ СНАПШОТА
    global _msg_counter
    with _msg_counter_lock:
        _msg_counter += 1
        do_snapshot = (_msg_counter % SNAPSHOT_EVERY == 0)
    if do_snapshot:
        threading.Thread(target=save_snapshot, daemon=True).start()

    # 1. СЧЁТЧИКИ В SQLite
    with db_lock:
        cursor.execute(
            'INSERT OR IGNORE INTO user_stats (chat_id,chat_title,user_id,username) VALUES(?,?,?,?)',
            (chat_id, title, uid, uname)
        )
        # Обновляем имя на случай смены
        cursor.execute(
            'UPDATE user_stats SET msg_count=msg_count+1, username=?, chat_title=?, last_seen=CURRENT_TIMESTAMP '
            'WHERE chat_id=? AND user_id=?',
            (uname, title, chat_id, uid)
        )
        if mood == 'positive':
            cursor.execute('UPDATE user_stats SET mood_pos=mood_pos+1 WHERE chat_id=? AND user_id=?', (chat_id, uid))
        elif mood == 'negative':
            cursor.execute('UPDATE user_stats SET mood_neg=mood_neg+1 WHERE chat_id=? AND user_id=?', (chat_id, uid))
        conn.commit()

    # 2. ФОРВАРД В КАНАЛ (канал = архив)
    try:
        copied = bot.copy_message(LOG_CHANNEL_ID, chat_id, message.message_id)
        bot.send_message(
            LOG_CHANNEL_ID,
            f"📍 {html.escape(title or '?')} · @{html.escape(uname)}",
            reply_to_message_id=copied.message_id
        )
    except Exception:
        pass

    # 3. ВОТЧЛИСТ — push если отслеживаемый
    if is_watched(uid):
        try:
            bot.forward_message(ADMIN_ID, chat_id, message.message_id)
            bot.send_message(
                ADMIN_ID,
                f"👁 <b>@{html.escape(uname)}</b> написал в <i>{html.escape(title or '?')}</i>",
                parse_mode='HTML'
            )
        except Exception:
            pass

    # 4. РЕПУТАЦИЯ
    if message.reply_to_message and tl in ['+', '👍', 'спасибо', 'спс', 'лучший']:
        target = message.reply_to_message.from_user
        if target.id != uid and not target.is_bot:
            tname = target.username or target.first_name
            with db_lock:
                cursor.execute(
                    'INSERT OR IGNORE INTO reputation(chat_id,user_id,username,score) VALUES(?,?,?,0)',
                    (chat_id, target.id, tname)
                )
                cursor.execute(
                    'UPDATE reputation SET score=score+1 WHERE chat_id=? AND user_id=?',
                    (chat_id, target.id)
                )
                conn.commit()
            bot.reply_to(message,
                         f"📈 Репутация <b>{html.escape(tname)}</b> повышена!",
                         parse_mode='HTML')

    # 5. ТРИГГЕРЫ
    elif tl.startswith(('бот, кто', 'бот кто')):
        with db_lock:
            users = [r[0] for r in cursor.execute(
                'SELECT username FROM user_stats WHERE chat_id=? AND username IS NOT NULL', (chat_id,)
            ).fetchall()]
        if users:
            bot.reply_to(message,
                         f"Я думаю, что @{random.choice(users)} "
                         f"{random.choice(['❄️','⛄','🧊','🎿','🏂','🏔'])}")

    elif tl.startswith(('бот, шип', 'бот шип')):
        with db_lock:
            users = [r[0] for r in cursor.execute(
                'SELECT username FROM user_stats WHERE chat_id=? AND username IS NOT NULL', (chat_id,)
            ).fetchall()]
        if len(users) >= 2:
            a, b = random.sample(users, 2)
            bot.reply_to(message,
                         f"🕵️‍♂️ @{a} и @{b} совместимы на {random.randint(0,100)}% 💘")

    # 6. РЕАКЦИИ (10%)
    if random.randint(1, 100) <= 10:
        try:
            bot.set_message_reaction(
                chat_id, message.message_id,
                [ReactionTypeEmoji(random.choice(['👍','🔥','👀','🤣','🤔','😎','🎉']))],
                is_big=False
            )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  СНАПШОТ — сохраняем статистику в канал, восстанавливаем при старте
#
#  Зачем: Render сбрасывает файловую систему при каждом перезапуске.
#  Решение: каждые SNAPSHOT_EVERY сообщений бот сохраняет
#  статистику JSON-сообщением в лог-канал и закрепляет его.
#  При старте — читает закреп и восстанавливает счётчики.
# ══════════════════════════════════════════════════════════════
SNAPSHOT_TAG  = "⚙️#STATS"   # тег по которому находим снапшот
SNAPSHOT_EVERY = 50           # сохранять каждые N сообщений

# Глобальный счётчик сообщений с момента старта
_msg_counter = 0
_msg_counter_lock = threading.Lock()


def save_snapshot():
    """Сериализует user_stats + reputation в JSON, постит в канал и закрепляет."""
    try:
        with db_lock:
            users = cursor.execute('SELECT * FROM user_stats').fetchall()
            reps  = cursor.execute('SELECT * FROM reputation').fetchall()

        data = {
            "u": [[r[0], r[1], r[2], r[3], r[4], r[5], r[6]] for r in users],
            "r": [[r[0], r[1], r[2], r[3]] for r in reps],
        }
        payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))

        # Telegram ограничение — 4096 символов. Если больше, берём топ-100 по count
        if len(payload) + len(SNAPSHOT_TAG) + 2 > 4096:
            top = sorted(data["u"], key=lambda x: x[4], reverse=True)[:100]
            data["u"] = top
            payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))

        text = f"{SNAPSHOT_TAG}\n{payload}"

        # Получаем ID старого закрепа чтобы потом открепить
        old_pin_id = None
        try:
            ch = bot.get_chat(LOG_CHANNEL_ID)
            if ch.pinned_message and ch.pinned_message.text and SNAPSHOT_TAG in ch.pinned_message.text:
                old_pin_id = ch.pinned_message.message_id
        except Exception:
            pass

        # Постим новый снапшот
        msg = bot.send_message(LOG_CHANNEL_ID, text, disable_notification=True)
        bot.pin_chat_message(LOG_CHANNEL_ID, msg.message_id, disable_notification=True)

        # Удаляем старый снапшот из канала (чтобы не засорять)
        if old_pin_id:
            try:
                bot.delete_message(LOG_CHANNEL_ID, old_pin_id)
            except Exception:
                pass

    except Exception as e:
        print(f"[snapshot] save failed: {e}")


def restore_from_snapshot():
    """При старте читает закреплённое сообщение канала и восстанавливает счётчики."""
    try:
        ch = bot.get_chat(LOG_CHANNEL_ID)
        if not ch.pinned_message:
            print("[snapshot] нет закреплённого сообщения, начинаем с нуля")
            return False

        text = ch.pinned_message.text or ""
        if SNAPSHOT_TAG not in text:
            print("[snapshot] закреп не содержит снапшота")
            return False

        payload = text[text.index('\n') + 1:]
        data    = json.loads(payload)

        with db_lock:
            # Восстанавливаем user_stats
            # u: [chat_id, chat_title, user_id, username, msg_count, mood_pos, mood_neg]
            for u in data.get("u", []):
                cursor.execute(
                    'INSERT OR REPLACE INTO user_stats '
                    '(chat_id, chat_title, user_id, username, msg_count, mood_pos, mood_neg) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (u[0], u[1], u[2], u[3], u[4], u[5], u[6])
                )
            # Восстанавливаем reputation
            # r: [chat_id, user_id, username, score]
            for r in data.get("r", []):
                cursor.execute(
                    'INSERT OR REPLACE INTO reputation (chat_id, user_id, username, score) VALUES (?,?,?,?)',
                    (r[0], r[1], r[2], r[3])
                )
            conn.commit()

        total = sum(u[4] for u in data.get("u", []))
        print(f"[snapshot] восстановлено: {len(data.get('u', []))} пользователей, {total} сообщений")
        return True

    except Exception as e:
        print(f"[snapshot] restore failed: {e}")
        return False


def notify_startup():
    """Восстанавливаем снапшот → уведомляем админа."""
    restored = restore_from_snapshot()

    chats = db_get_chats()
    lines = "\n".join(f"🔹 {t or c} — {n} сообщ." for c, t, n in chats) if chats else "Пусто."
    status = "♻️ Статистика восстановлена из канала" if restored else "🆕 Начинаю с нуля"

    try:
        bot.send_message(ADMIN_ID, f"🫡 В строю!\n{status}\n\n{lines}")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)


if __name__ == '__main__':
    notify_startup()
    threading.Thread(target=run_flask, daemon=True).start()
    bot.infinity_polling()
