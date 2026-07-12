import os
import sqlite3
import json
import threading
import datetime
from flask import Flask, request, jsonify
import telebot

from dotenv import load_dotenv

# Load configuration from .env
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(ENV_PATH)

TOKEN = os.getenv('TELEGRAM_TOKEN')
FAMILY_CHAT_ID = os.getenv('FAMILY_CHAT_ID')
FAMILY_NAME = os.getenv('FAMILY_NAME', 'Семья')
ADMIN_IDS_RAW = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [x.strip() for x in ADMIN_IDS_RAW.split(',') if x.strip()]
POINT_RATE = int(os.getenv('POINT_RATE', 10000))
SECRET_TOKEN = os.getenv('SECRET_TOKEN', 'ChangeMeSuperSecretToken123!')
TEST_MODE = os.getenv('TEST_MODE') == 'true'

def set_env_value(key, value):
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}\n")
    with open(ENV_PATH, 'w', encoding='utf-8') as f:
        f.writelines(lines)

if not TOKEN or ':' not in TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
    print("=" * 60)
    print("❌ ОШИБКА: Неверный токен Telegram-бота в .env!")
    print("Пожалуйста, откройте .env и вставьте ваш токен от @BotFather.")
    print("Пример: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ")
    print("=" * 60)
    import sys
    sys.exit(1)

bot = telebot.TeleBot(TOKEN)

# Verify token validity with Telegram on startup
if not TEST_MODE:
    try:
        print("Checking Telegram Bot token validity...")
        bot_user = bot.get_me()
        print(f"✅ Успешное подключение к Telegram! Бот: @{bot_user.username}")
    except telebot.apihelper.ApiTelegramException as e:
        if e.error_code == 401:
            print("=" * 60)
            print("❌ ОШИБКА: Токен Telegram-бота не авторизован (ошибка 401)!")
            print("Пожалуйста, откройте .env, вставьте правильный токен от @BotFather")
            print("и перезапустите программу.")
            print("=" * 60)
        else:
            print(f"❌ Ошибка Telegram API при проверке токена: {e}")
        import sys
        sys.exit(1)
    except Exception as e:
        print(f"⚠️ Предупреждение: Не удалось проверить токен (нет подключения к сети): {e}")
app = Flask(__name__)
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'family.db')

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    # Members table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS members (
            name TEXT PRIMARY KEY,
            rank TEXT,
            points_day INTEGER,
            points_total INTEGER,
            points_paid INTEGER DEFAULT 0,
            money_paid INTEGER DEFAULT 0,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Payments log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT,
            points INTEGER,
            money INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            comment TEXT,
            FOREIGN KEY(player_name) REFERENCES members(name)
        )
    ''')
    # Scans history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            scanner_name TEXT,
            total_members INTEGER,
            raw_data TEXT
        )
    ''')
    conn.commit()
    conn.close()

def check_auth(req):
    auth_header = req.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return False
    token = auth_header.split(' ')[1]
    return token == SECRET_TOKEN

def send_screening_report(scanner_name, new_points):
    if TEST_MODE:
        print("[TEST] Skipping Telegram screening report.")
        return
    # Sort new_points by points added desc
    new_points = sorted(new_points, key=lambda x: x['added'], reverse=True)
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    
    msg = f"📊 *Скрининг очков семьи {FAMILY_NAME} от {now}*\n"
    msg += f"👤 Ответственный: `{scanner_name}`\n\n"
    
    added_section = ""
    unpaid_section = ""
    total_unpaid_money = 0
    total_unpaid_points = 0
    
    for p in new_points:
        if p['added'] > 0:
            added_section += f"• `{p['name']}`: +{p['added']} оч. (Всего: {p['total']})\n"
        
        if p['unpaid'] > 0:
            money_owed = p['unpaid'] * POINT_RATE
            unpaid_section += f"• `{p['name']}`: {p['unpaid']} оч. (Долг: {money_owed:,}$)\n"
            total_unpaid_points += p['unpaid']
            total_unpaid_money += money_owed
            
    if added_section:
        msg += "📈 *Новые очки за сегодня:*\n" + added_section + "\n"
    else:
        msg += "📈 *Новых очков за сегодня нет.*\n\n"
        
    if unpaid_section:
        msg += "💰 *Текущие долги по выплатам:*\n" + unpaid_section
        msg += f"----------------------------------\n"
        msg += f"💵 *Всего к выплате: {total_unpaid_money:,}$* ({total_unpaid_points} оч.)\n"
    else:
        msg += "💰 *Все долги выплачены!*"
        
    try:
        bot.send_message(FAMILY_CHAT_ID, msg, parse_mode='Markdown')
    except Exception as e:
        print(f"Error sending message to Telegram: {e}")

# API Route: Receive scans from game client
@app.route('/api/scan', methods=['POST'])
def api_scan():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    if not data or 'members' not in data:
        return jsonify({"error": "Invalid payload"}), 400
    
    scanner_name = data.get('scanner_name', 'Unknown')
    members_data = data['members']
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Log raw scan data
    cursor.execute(
        "INSERT INTO scans (scanner_name, total_members, raw_data) VALUES (?, ?, ?)",
        (scanner_name, len(members_data), json.dumps(members_data, ensure_ascii=False))
    )
    
    new_points_list = []
    
    for m in members_data:
        name = m['name']
        rank = m['rank']
        points_day = int(m['points_day'])
        points_total = int(m['points_total'])
        
        # Check if member exists
        cursor.execute("SELECT * FROM members WHERE name = ?", (name,))
        row = cursor.fetchone()
        
        if row is None:
            # New member: unpaid_points should start as points_day
            # unpaid_points = points_total - points_paid -> points_paid = points_total - points_day
            points_paid = max(0, points_total - points_day)
            cursor.execute(
                "INSERT INTO members (name, rank, points_day, points_total, points_paid) VALUES (?, ?, ?, ?, ?)",
                (name, rank, points_day, points_total, points_paid)
            )
            unpaid = points_total - points_paid
            new_points_list.append({
                "name": name,
                "added": points_day,
                "total": points_total,
                "unpaid": unpaid,
                "rank": rank
            })
        else:
            old_total = row['points_total']
            old_paid = row['points_paid']
            
            # Check for points reset (e.g. weekly restart, family recreation)
            if points_total < old_total:
                points_paid = max(0, points_total - points_day)
                cursor.execute(
                    "UPDATE members SET rank = ?, points_day = ?, points_total = ?, points_paid = ?, last_seen = datetime('now','localtime') WHERE name = ?",
                    (rank, points_day, points_total, points_paid, name)
                )
                new_points_list.append({
                    "name": name,
                    "added": points_day,
                    "total": points_total,
                    "unpaid": points_day,
                    "rank": rank
                })
            else:
                added = points_total - old_total
                cursor.execute(
                    "UPDATE members SET rank = ?, points_day = ?, points_total = ?, last_seen = datetime('now','localtime') WHERE name = ?",
                    (rank, points_day, points_total, name)
                )
                unpaid = points_total - old_paid
                new_points_list.append({
                    "name": name,
                    "added": added,
                    "total": points_total,
                    "unpaid": unpaid,
                    "rank": rank
                })
    
    conn.commit()
    conn.close()
    
    # Send report
    send_screening_report(scanner_name, new_points_list)
    
    return jsonify({"status": "success", "processed": len(members_data)})

# API Route: Receive pay notifications from game client (Smart Pay)
@app.route('/api/game-pay', methods=['POST'])
def api_game_pay():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.json
    if not data or 'player_name' not in data or 'amount' not in data:
        return jsonify({"error": "Invalid payload"}), 400
        
    sender = data.get('sender_name', 'Unknown')
    player_name = data['player_name']
    amount = int(data['amount'])
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Case-insensitive member search
    cursor.execute("SELECT * FROM members WHERE LOWER(name) = LOWER(?)", (player_name,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return jsonify({"error": f"Player '{player_name}' not found in database"}), 404
        
    name = row['name']
    unpaid_points = row['points_total'] - row['points_paid']
    
    if unpaid_points <= 0:
        conn.close()
        return jsonify({"status": "ignored", "message": "Player has no debt"}), 200
        
    # Points covered by this payout
    points_covered = amount // POINT_RATE
    if points_covered <= 0:
        conn.close()
        return jsonify({"error": f"Amount {amount:,}$ too small. Minimum rate per point is {POINT_RATE:,}$"}), 400
        
    # Clamp points covered to the unpaid points
    points_to_pay = min(points_covered, unpaid_points)
    money_to_pay = points_to_pay * POINT_RATE
    
    new_paid = row['points_paid'] + points_to_pay
    new_money = row['money_paid'] + money_to_pay
    
    cursor.execute(
        "UPDATE members SET points_paid = ?, money_paid = ? WHERE name = ?",
        (new_paid, new_money, name)
    )
    
    cursor.execute(
        "INSERT INTO payments (player_name, points, money, comment) VALUES (?, ?, ?, ?)",
        (name, points_to_pay, money_to_pay, f"Списание из игры от {sender} (Сумма: {amount:,}$)")
    )
    
    conn.commit()
    conn.close()
    
    # Send report
    msg = f"💵 *[{FAMILY_NAME}] Учет выплаты из игры*\n"
    msg += f"👤 Перевел: `{sender}`\n"
    msg += f"🤝 Получатель: `{name}`\n"
    msg += f"💰 Сумма: {money_to_pay:,}$ ({points_to_pay} оч. списано)\n"
    msg += f"📉 Остаток долга: {max(0, unpaid_points - points_to_pay)} оч. ({max(0, unpaid_points - points_to_pay)*POINT_RATE:,}$)"
    
    if TEST_MODE:
        print("[TEST] Skipping Telegram game-pay report.")
    else:
        try:
            bot.send_message(FAMILY_CHAT_ID, msg, parse_mode='Markdown')
        except Exception as e:
            print(f"Error sending message to Telegram: {e}")
        
    return jsonify({"status": "success", "player": name, "points_covered": points_to_pay, "amount_processed": money_to_pay})

# Telegram Command Handlers
def is_admin(message):
    if message.chat.type == 'private':
        return str(message.from_user.id) in ADMIN_IDS
        
    try:
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ['creator', 'administrator']:
            return True
    except Exception as e:
        print(f"Error checking chat admin status: {e}")
        
    return str(message.from_user.id) in ADMIN_IDS

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message, "👋 Привет! Я бот для управления выплатами семьи.\nИспользуй /help для списка доступных команд.")

@bot.message_handler(commands=['help'])
def cmd_help(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав для использования административных команд.")
        return
        
    help_text = (
        "⚙️ *Доступные команды администратора:*\n\n"
        "📊 `/stats` или `/members` — Список долгов по выплатам.\n"
        "💳 `/pay [Ник_Игрока] [количество]` — Зафиксировать выплату:\n"
        "  • `/pay Nick_Name` — Выплатить весь долг.\n"
        "  • `/pay Nick_Name 10p` — Выплатить 10 очков.\n"
        "  • `/pay Nick_Name 100k` — Выплатить 100,000$ (пересчитается в очки).\n"
        "🏦 `/payall` — Выплатить долг всем игрокам.\n"
        "🔎 `/audit [Ник_Игрока]` — История очков и выплат игрока.\n"
        "📈 `/status` — Общий баланс и статистика семьи.\n"
        "✍️ `/rename [Старый_Ник] [Новый_Ник]` — Смена ника игрока.\n"
        "💸 `/setrate [сумма]` — Установить курс 1 очка в виртах (например, 10000).\n"
        "📢 `/setchat` — Привязать текущий чат для отправки отчетов очков.\n"
        "🏷 `/setfamily [Имя]` — Установить название семьи.\n"
    )
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['setchat'])
def cmd_setchat(message):
    global FAMILY_CHAT_ID
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    if message.chat.type == 'private':
        bot.reply_to(message, "❌ Эту команду нужно вызывать в групповом чате семьи, а не в личке!")
        return
        
    chat_id = str(message.chat.id)
    try:
        set_env_value('FAMILY_CHAT_ID', chat_id)
        FAMILY_CHAT_ID = chat_id
        bot.reply_to(message, f"✅ Чат семьи успешно привязан!\nID чата: `{chat_id}`", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка сохранения чата: {e}")

@bot.message_handler(commands=['setfamily'])
def cmd_setfamily(message):
    global FAMILY_NAME
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, f"ℹ️ Текущее название семьи: *{FAMILY_NAME}*.\nИспользование: `/setfamily [Имя_Семьи]`", parse_mode='Markdown')
        return
        
    new_name = parts[1].strip()
    try:
        set_env_value('FAMILY_NAME', new_name)
        FAMILY_NAME = new_name
        bot.reply_to(message, f"✅ Название семьи успешно установлено: *{new_name}*!", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка сохранения названия: {e}")

@bot.message_handler(commands=['setrate'])
def cmd_setrate(message):
    global POINT_RATE
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, f"ℹ️ Текущий курс очка: {POINT_RATE:,}$.\nИспользование: `/setrate [число]`", parse_mode='Markdown')
        return
        
    try:
        new_rate = int(parts[1])
        if new_rate <= 0:
            raise ValueError
        
        set_env_value('POINT_RATE', new_rate)
            
        POINT_RATE = new_rate
        bot.reply_to(message, f"✅ Новый курс очка успешно установлен: {new_rate:,}$.")
    except ValueError:
        bot.reply_to(message, "❌ Курс должен быть положительным целым числом.")

@bot.message_handler(commands=['stats', 'members'])
def cmd_stats(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM members ORDER BY (points_total - points_paid) DESC")
    rows = cursor.fetchall()
    conn.close()
    
    msg = "📊 *Текущие долги участников семьи:*\n\n"
    total_points = 0
    total_money = 0
    count = 0
    
    for row in rows:
        unpaid = row['points_total'] - row['points_paid']
        if unpaid > 0:
            count += 1
            money = unpaid * POINT_RATE
            total_points += unpaid
            total_money += money
            msg += f"{count}. `{row['name']}`: {unpaid} оч. (Долг: {money:,}$)\n"
            
    if count == 0:
        bot.send_message(message.chat.id, "🎉 *Долгов нет! Все очки выплачены.*", parse_mode='Markdown')
    else:
        msg += f"----------------------------------\n"
        msg += f"💵 *Всего к выплате: {total_money:,}$* ({total_points} оч.)"
        bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def cmd_status(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt, SUM(points_total) as tot, SUM(points_paid) as paid, SUM(money_paid) as money FROM members")
    stats = cursor.fetchone()
    
    cursor.execute("SELECT timestamp FROM scans ORDER BY id DESC LIMIT 1")
    last_scan = cursor.fetchone()
    conn.close()
    
    cnt = stats['cnt'] or 0
    tot = stats['tot'] or 0
    paid = stats['paid'] or 0
    money = stats['money'] or 0
    unpaid = tot - paid
    unpaid_money = unpaid * POINT_RATE
    last_scan_time = last_scan['timestamp'] if last_scan else "Никогда"
    
    msg = (
        "📈 *Общая статистика семьи:*\n\n"
        f"👥 Всего участников в БД: `{cnt}`\n"
        f"💎 Всего заработано очков: `{tot}`\n"
        f"✅ Всего выплачено очков: `{paid}`\n"
        f"💸 Всего выплачено вирт: `{money:,}$`\n"
        f"⏳ Текущий долг к выплате: `{unpaid}` оч. ({unpaid_money:,}$)\n\n"
        f"📅 Последний скрининг: `{last_scan_time}`\n"
        f"💰 Курс 1 очка: `{POINT_RATE:,}$`"
    )
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(commands=['pay'])
def cmd_pay(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ Использование: `/pay [Имя_Игрока] [сумма/очки]`\nПримеры:\n• `/pay Nick_Name` (выплата всего долга)\n• `/pay Nick_Name 10p` (выплатить 10 очков)\n• `/pay Nick_Name 100k` (выплатить 100,000$)", parse_mode='Markdown')
        return
        
    player_name = parts[1]
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM members WHERE LOWER(name) = LOWER(?)", (player_name,))
    row = cursor.fetchone()
    
    if not row:
        bot.reply_to(message, f"❌ Игрок `{player_name}` не найден в базе данных.", parse_mode='Markdown')
        conn.close()
        return
        
    name = row['name']
    unpaid_points = row['points_total'] - row['points_paid']
    
    if unpaid_points <= 0:
        bot.reply_to(message, f"😊 У игрока `{name}` нет долгов.", parse_mode='Markdown')
        conn.close()
        return
        
    pay_points = unpaid_points
    pay_money = unpaid_points * POINT_RATE
    comment = "Полная выплата долга"
    
    if len(parts) >= 3:
        amount_str = parts[2].lower()
        if amount_str.endswith('p') or amount_str.endswith('оч'):
            try:
                val = int(amount_str.replace('p', '').replace('оч', ''))
                if val <= 0:
                    raise ValueError
                pay_points = min(val, unpaid_points)
                pay_money = pay_points * POINT_RATE
                comment = f"Частичная выплата {pay_points} оч."
            except ValueError:
                bot.reply_to(message, "❌ Неверный формат очков. Пример: `10p`")
                conn.close()
                return
        else:
            try:
                multiplier = 1
                if amount_str.endswith('k') or amount_str.endswith('к'):
                    multiplier = 1000
                    amount_str = amount_str.replace('k', '').replace('к', '')
                elif amount_str.endswith('kk') or amount_str.endswith('кк'):
                    multiplier = 1000000
                    amount_str = amount_str.replace('kk', '').replace('кк', '')
                    
                money_val = int(float(amount_str) * multiplier)
                if money_val <= 0:
                    raise ValueError
                    
                pay_money = money_val
                pay_points = money_val // POINT_RATE
                
                if pay_points <= 0:
                    bot.reply_to(message, f"❌ Сумма {money_val:,}$ слишком мала. Минимальная выплата за 1 очко = {POINT_RATE:,}$.")
                    conn.close()
                    return
                    
                if pay_points > unpaid_points:
                    pay_points = unpaid_points
                    pay_money = pay_points * POINT_RATE
                    comment = f"Выплата с округлением {pay_money:,}$"
                else:
                    comment = f"Выплата {pay_money:,}$"
            except ValueError:
                bot.reply_to(message, "❌ Неверный формат суммы. Пример: `100k` или `100000`")
                conn.close()
                return
                
    new_paid = row['points_paid'] + pay_points
    new_money = row['money_paid'] + pay_money
    
    cursor.execute(
        "UPDATE members SET points_paid = ?, money_paid = ? WHERE name = ?",
        (new_paid, new_money, name)
    )
    
    cursor.execute(
        "INSERT INTO payments (player_name, points, money, comment) VALUES (?, ?, ?, ?)",
        (name, pay_points, pay_money, comment)
    )
    
    conn.commit()
    conn.close()
    
    bot.reply_to(
        message,
        f"✅ *Выплата успешно зафиксирована!*\n"
        f"👤 Игрок: `{name}`\n"
        f"💎 Списано: `{pay_points}` оч.\n"
        f"💵 Выплачено: `{pay_money:,}$`\n"
        f"📉 Остаток долга: `{max(0, unpaid_points - pay_points)}` оч. ({max(0, unpaid_points - pay_points) * POINT_RATE:,}$)",
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['payall'])
def cmd_payall(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM members")
    rows = cursor.fetchall()
    
    total_paid = 0
    total_money = 0
    players_count = 0
    
    for row in rows:
        unpaid = row['points_total'] - row['points_paid']
        if unpaid > 0:
            players_count += 1
            money = unpaid * POINT_RATE
            total_paid += unpaid
            total_money += money
            
            cursor.execute(
                "UPDATE members SET points_paid = ?, money_paid = ? WHERE name = ?",
                (row['points_total'], row['money_paid'] + money, row['name'])
            )
            cursor.execute(
                "INSERT INTO payments (player_name, points, money, comment) VALUES (?, ?, ?, ?)",
                (row['name'], unpaid, money, "Групповая выплата всех долгов (/payall)")
            )
            
    conn.commit()
    conn.close()
    
    if players_count == 0:
        bot.reply_to(message, "😊 Долгов нет, выплачивать некому.")
    else:
        bot.reply_to(
            message,
            f"✅ *Групповая выплата завершена!*\n"
            f"👥 Игроков выплачено: `{players_count}`\n"
            f"💎 Всего списано: `{total_paid}` оч.\n"
            f"💵 Всего выплачено: `{total_money:,}$`"
        )

@bot.message_handler(commands=['rename'])
def cmd_rename(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "ℹ️ Использование: `/rename [Старый_Ник] [Новый_Ник]`", parse_mode='Markdown')
        return
        
    old_name = parts[1]
    new_name = parts[2]
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM members WHERE LOWER(name) = LOWER(?)", (old_name,))
    row = cursor.fetchone()
    
    if not row:
        bot.reply_to(message, f"❌ Игрок `{old_name}` не найден в базе данных.", parse_mode='Markdown')
        conn.close()
        return
        
    actual_old_name = row['name']
    
    cursor.execute("SELECT * FROM members WHERE LOWER(name) = LOWER(?)", (new_name,))
    exists_row = cursor.fetchone()
    if exists_row:
        bot.reply_to(message, f"❌ Игрок с ником `{new_name}` уже существует в базе данных.", parse_mode='Markdown')
        conn.close()
        return
        
    try:
        cursor.execute("UPDATE members SET name = ? WHERE name = ?", (new_name, actual_old_name))
        cursor.execute("UPDATE payments SET player_name = ? WHERE player_name = ?", (new_name, actual_old_name))
        conn.commit()
        bot.reply_to(message, f"✅ Игрок `{actual_old_name}` успешно переименован в `{new_name}` во всех таблицах.")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка переименования: {e}")
    finally:
        conn.close()

@bot.message_handler(commands=['audit'])
def cmd_audit(message):
    if not is_admin(message):
        bot.reply_to(message, "❌ У вас нет прав.")
        return
        
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ Использование: `/audit [Имя_Игрока]`", parse_mode='Markdown')
        return
        
    player_name = parts[1]
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM members WHERE LOWER(name) = LOWER(?)", (player_name,))
    member = cursor.fetchone()
    
    if not member:
        bot.reply_to(message, f"❌ Игрок `{player_name}` не найден в базе данных.", parse_mode='Markdown')
        conn.close()
        return
        
    name = member['name']
    cursor.execute("SELECT * FROM payments WHERE player_name = ? ORDER BY id DESC LIMIT 5", (name,))
    payments = cursor.fetchall()
    conn.close()
    
    unpaid = member['points_total'] - member['points_paid']
    
    msg = f"🔎 *Аудит игрока `{name}`:*\n\n"
    msg += f"🎖 Ранг: `{member['rank']}`\n"
    msg += f"💎 Очков всего: `{member['points_total']}`\n"
    msg += f"✅ Оплачено очков: `{member['points_paid']}`\n"
    msg += f"💸 Оплачено вирт: `{member['money_paid']:,}$`\n"
    msg += f"⏳ Текущий долг: `{unpaid}` оч. ({unpaid * POINT_RATE:,}$)\n"
    msg += f"📅 Последняя активность: `{member['last_seen']}`\n\n"
    
    msg += "💳 *Последние выплаты:*\n"
    if not payments:
        msg += "_История выплат пуста_\n"
    else:
        for p in payments:
            msg += f"• {p['timestamp']}: {p['money']:,}$ ({p['points']} оч.) — _{p['comment']}_\n"
            
    bot.send_message(message.chat.id, msg, parse_mode='Markdown')

def run_telebot():
    print("Starting Telegram Bot listener...")
    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"Telebot polling error: {e}")

if __name__ == '__main__':
    init_db()
    
    # Start bot listener in background thread
    bot_thread = threading.Thread(target=run_telebot, daemon=True)
    bot_thread.start()
    
    # Start Flask API server
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    print(f"Starting Flask API Server on {host}:{port}...")
    app.run(host=host, port=port, debug=False)
