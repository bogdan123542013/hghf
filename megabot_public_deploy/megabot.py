# -*- coding: utf-8 -*-
"""
MEGABOT + WEBSITE
==================
Telegram bot + Flask website + SQLite in ONE Python file.

Install:
    pip install aiogram flask

Run:
    python megabot_web.py

Open website:
    http://127.0.0.1:5000

IMPORTANT:
1. Put your Telegram bot token into BOT_TOKEN.
2. Put your Telegram numeric ID into OWNER_ID.
3. Never publish your bot token.

This is a local/demo administration system.
"""

import asyncio
import html
import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, date
from contextlib import closing
from functools import wraps

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    render_template_string,
    jsonify,
)

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.getenv("OWNER_ID", "123456789"))

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "5000"))
# На хостинге адрес можно задать переменной PUBLIC_URL.
PUBLIC_URL = os.getenv("PUBLIC_URL", f"http://127.0.0.1:{PORT}")

DATABASE = "megabot_web.db"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("MegaBotWeb")

# ============================================================
# APPLICATIONS
# ============================================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Simple temporary conversation states.
states = {}

# ============================================================
# DATABASE
# ============================================================

db_lock = threading.Lock()


def connect_db():
    con = sqlite3.connect(
        DATABASE,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    return con


def db_init():
    with db_lock:
        con = connect_db()
        cur = con.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                balance INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                bonus_date TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                kind TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                price INTEGER NOT NULL DEFAULT 0,
                stock INTEGER NOT NULL DEFAULT -1,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                answer TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                created_at TEXT NOT NULL,
                answered_at TEXT DEFAULT ''
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                reward INTEGER NOT NULL DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(task_id, telegram_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS vip (
                telegram_id INTEGER PRIMARY KEY,
                expires_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS currencies (
                telegram_id INTEGER PRIMARY KEY,
                coins INTEGER NOT NULL DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS site_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                active INTEGER DEFAULT 1
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        cur.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES('maintenance', '0')
        """)

        cur.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES('bonus', '100')
        """)

        cur.execute("""
            INSERT INTO products(name, description, price, stock, active, created_at)
            SELECT 'VIP на 7 дней', 'VIP-статус на 7 дней', 500, -1, 1, ?
            WHERE NOT EXISTS (SELECT 1 FROM products)
        """, (now(),))

        cur.execute("""
            INSERT INTO products(name, description, price, stock, active, created_at)
            SELECT 'VIP на 30 дней', 'VIP-статус на 30 дней', 1500, -1, 1, ?
            WHERE (SELECT COUNT(*) FROM products) = 1
        """, (now(),))

        cur.execute("""
            INSERT INTO tasks(title, description, reward, active)
            SELECT 'Ежедневный бонус', 'Получите ежедневный бонус', 100, 1
            WHERE NOT EXISTS (SELECT 1 FROM tasks)
        """)

        # Automatically make owner admin if account exists.
        cur.execute("""
            UPDATE users
            SET is_admin = 1
            WHERE telegram_id = ?
        """, (OWNER_ID,))

        con.commit()
        con.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_user(tg_id):
    con = connect_db()
    row = con.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (tg_id,)
    ).fetchone()
    con.close()
    return row


def register_user(tg_id, username="", first_name=""):
    username = username or ""
    first_name = first_name or ""

    with db_lock:
        con = connect_db()

        row = con.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (tg_id,)
        ).fetchone()

        if row:
            con.execute("""
                UPDATE users
                SET username = ?, first_name = ?, last_seen = ?
                WHERE telegram_id = ?
            """, (
                username,
                first_name,
                now(),
                tg_id
            ))
        else:
            con.execute("""
                INSERT INTO users(
                    telegram_id,
                    username,
                    first_name,
                    balance,
                    is_admin,
                    is_banned,
                    bonus_date,
                    created_at,
                    last_seen
                )
                VALUES (?, ?, ?, 0, ?, 0, '', ?, ?)
            """, (
                tg_id,
                username,
                first_name,
                1 if tg_id == OWNER_ID else 0,
                now(),
                now()
            ))

        con.commit()
        con.close()

    return get_user(tg_id)


def add_log(tg_id, action):
    with db_lock:
        con = connect_db()
        con.execute("""
            INSERT INTO logs(telegram_id, action, created_at)
            VALUES (?, ?, ?)
        """, (
            tg_id,
            action,
            now()
        ))
        con.commit()
        con.close()


def add_notification(tg_id, text):
    with db_lock:
        con = connect_db()
        con.execute("""
            INSERT INTO notifications(
                telegram_id,
                text,
                is_read,
                created_at
            )
            VALUES (?, ?, 0, ?)
        """, (
            tg_id,
            text,
            now()
        ))
        con.commit()
        con.close()


def change_balance(tg_id, amount, kind, description):
    with db_lock:
        con = connect_db()

        con.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE telegram_id = ?
        """, (
            amount,
            tg_id
        ))

        con.execute("""
            INSERT INTO transactions(
                telegram_id,
                amount,
                kind,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            tg_id,
            amount,
            kind,
            description,
            now()
        ))

        con.commit()
        con.close()


def is_admin(tg_id):
    if tg_id == OWNER_ID:
        return True

    user = get_user(tg_id)
    return bool(user and user["is_admin"])


def is_banned(tg_id):
    user = get_user(tg_id)
    return bool(user and user["is_banned"])


def get_setting(key, default=""):
    con = connect_db()
    row = con.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,)
    ).fetchone()
    con.close()

    if not row:
        return default

    return row["value"]


def set_setting(key, value):
    with db_lock:
        con = connect_db()
        con.execute("""
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        con.commit()
        con.close()


def statistics():
    con = connect_db()

    result = {
        "users": con.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0],

        "admins": con.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1"
        ).fetchone()[0],

        "banned": con.execute(
            "SELECT COUNT(*) FROM users WHERE is_banned = 1"
        ).fetchone()[0],

        "balance": con.execute(
            "SELECT COALESCE(SUM(balance),0) FROM users"
        ).fetchone()[0],

        "transactions": con.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0],

        "logs": con.execute(
            "SELECT COUNT(*) FROM logs"
        ).fetchone()[0],
    }

    con.close()

    return result


# ============================================================
# EXTRA FEATURES
# ============================================================

def ensure_currency(tg_id):
    with db_lock:
        con = connect_db()
        con.execute(
            "INSERT OR IGNORE INTO currencies(telegram_id, coins) VALUES (?,0)",
            (tg_id,)
        )
        con.commit()
        con.close()


def get_coins(tg_id):
    ensure_currency(tg_id)
    con = connect_db()
    row = con.execute(
        "SELECT coins FROM currencies WHERE telegram_id=?",
        (tg_id,)
    ).fetchone()
    con.close()
    return row["coins"] if row else 0


def change_coins(tg_id, amount):
    ensure_currency(tg_id)
    with db_lock:
        con = connect_db()
        con.execute(
            "UPDATE currencies SET coins=coins+? WHERE telegram_id=?",
            (amount, tg_id)
        )
        con.commit()
        con.close()


def get_vip(tg_id):
    con = connect_db()
    row = con.execute(
        "SELECT expires_at FROM vip WHERE telegram_id=?",
        (tg_id,)
    ).fetchone()
    con.close()
    return row["expires_at"] if row else None


def add_vip_days(tg_id, days):
    from datetime import timedelta
    current = get_vip(tg_id)
    now_dt = datetime.now()

    if current:
        try:
            base = datetime.strptime(current, "%Y-%m-%d %H:%M:%S")
            if base < now_dt:
                base = now_dt
        except ValueError:
            base = now_dt
    else:
        base = now_dt

    expires = base + timedelta(days=days)

    with db_lock:
        con = connect_db()
        con.execute("""
            INSERT INTO vip(telegram_id, expires_at)
            VALUES(?,?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET expires_at=excluded.expires_at
        """, (tg_id, expires.strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
        con.close()


def open_support_ticket(tg_id, text):
    with db_lock:
        con = connect_db()
        cur = con.execute("""
            INSERT INTO support_tickets(
                telegram_id, message, status, created_at
            ) VALUES(?,?,'open',?)
        """, (tg_id, text, now()))
        ticket_id = cur.lastrowid
        con.commit()
        con.close()
    return ticket_id


def get_open_tickets(limit=20):
    con = connect_db()
    rows = con.execute("""
        SELECT *
        FROM support_tickets
        WHERE status='open'
        ORDER BY id ASC
        LIMIT ?
    """, (limit,)).fetchall()
    con.close()
    return rows


def shop_products():
    con = connect_db()
    rows = con.execute("""
        SELECT *
        FROM products
        WHERE active=1
        ORDER BY id
    """).fetchall()
    con.close()
    return rows


def get_product(product_id):
    con = connect_db()
    row = con.execute(
        "SELECT * FROM products WHERE id=? AND active=1",
        (product_id,)
    ).fetchone()
    con.close()
    return row


# ============================================================
# TELEGRAM KEYBOARDS
# ============================================================

def user_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👤 Профиль"),
                KeyboardButton(text="💰 Баланс"),
            ],
            [
                KeyboardButton(text="🎁 Бонус"),
                KeyboardButton(text="📜 История"),
            ],
            [
                KeyboardButton(text="📊 Статистика"),
                KeyboardButton(text="🏆 Рейтинг"),
            ],
            [
                KeyboardButton(text="🔔 Уведомления"),
                KeyboardButton(text="📰 Новости"),
            ],
            [
                KeyboardButton(text="💸 Перевод"),
                KeyboardButton(text="➕ Пополнить"),
            ],
            [
                KeyboardButton(text="🎮 Игры"),
                KeyboardButton(text="📋 Меню"),
            ],
            [
                KeyboardButton(text="⚙️ Настройки"),
                KeyboardButton(text="🆘 Поддержка"),
            ],
            [
                KeyboardButton(text="🛒 Магазин"),
                KeyboardButton(text="⭐ VIP"),
            ],
            [
                KeyboardButton(text="📝 Задания"),
                KeyboardButton(text="👥 Рефералы"),
            ],
            [
                KeyboardButton(text="🪙 Монеты"),
                KeyboardButton(text="💬 Связь с админом"),
            ],
            [
                KeyboardButton(text="🌐 Сайт"),
                KeyboardButton(text="ℹ️ Помощь"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите кнопку..."
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👑 Админ-панель"),
                KeyboardButton(text="📊 Админ-статистика"),
            ],
            [
                KeyboardButton(text="👥 Пользователи"),
                KeyboardButton(text="🔎 Поиск"),
            ],
            [
                KeyboardButton(text="🚫 Баны"),
                KeyboardButton(text="💰 Балансы"),
            ],
            [
                KeyboardButton(text="📢 Рассылка"),
                KeyboardButton(text="💬 Поддержка"),
            ],
            [
                KeyboardButton(text="📜 Логи"),
            ],
            [
                KeyboardButton(text="🛒 Магазин"),
                KeyboardButton(text="⭐ VIP"),
            ],
            [
                KeyboardButton(text="📝 Задания"),
                KeyboardButton(text="🌐 Сайт"),
            ],
            [
                KeyboardButton(text="🧪 Тест ×50"),
                KeyboardButton(text="📰 Посты сайта"),
            ],
            [
                KeyboardButton(text="👮 Администраторы"),
                KeyboardButton(text="⚙️ Настройки"),
            ],
            [
                KeyboardButton(text="🔙 Обычное меню"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Админ-панель..."
    )


# ============================================================
# TELEGRAM HELPERS
# ============================================================

async def guard(message):
    if is_banned(message.from_user.id):
        await message.answer(
            "🚫 Вы заблокированы."
        )
        return False

    return True


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

@dp.message(Command("start"))
async def start(message: Message):
    was_new = get_user(message.from_user.id) is None
    user = register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if was_new:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("ref_"):
            try:
                referrer = int(parts[1][4:])
                if referrer != message.from_user.id and get_user(referrer):
                    with db_lock:
                        con = connect_db()
                        con.execute(
                            "INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)",
                            (referrer, message.from_user.id, now())
                        )
                        con.commit()
                        con.close()
                    change_coins(referrer, 50)
                    add_notification(referrer, "👥 Новый реферал! +50 монет.")
            except ValueError:
                pass

    if not await guard(message):
        return

    add_log(
        message.from_user.id,
        "Использована команда /start"
    )

    keyboard = (
        admin_keyboard()
        if is_admin(message.from_user.id)
        else user_keyboard()
    )

    await message.answer(
        "👋 <b>Добро пожаловать в MegaBot!</b>\n\n"
        "Здесь есть профиль, баланс, бонусы, переводы, "
        "статистика и большая панель администратора.\n\n"
        "👇 Используйте кнопки ниже.",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.message(Command("answer"))
async def answer_ticket(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer(
            "Формат: <code>/answer ID текст ответа</code>",
            parse_mode="HTML"
        )
        return

    ticket_id = int(parts[1])
    answer_text = parts[2]

    con = connect_db()
    ticket = con.execute(
        "SELECT * FROM support_tickets WHERE id=?",
        (ticket_id,)
    ).fetchone()
    con.close()

    if not ticket:
        await message.answer("❌ Тикет не найден.")
        return

    with db_lock:
        con = connect_db()
        con.execute(
            "UPDATE support_tickets SET answer=?, status='answered', answered_at=? WHERE id=?",
            (answer_text, now(), ticket_id)
        )
        con.commit()
        con.close()

    add_notification(
        ticket["telegram_id"],
        f"💬 Ответ администратора по обращению #{ticket_id}: {answer_text}"
    )

    try:
        await bot.send_message(
            ticket["telegram_id"],
            "💬 <b>Ответ администратора</b>\n\n" + html.escape(answer_text),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await message.answer(f"✅ Ответ по тикету #{ticket_id} отправлен.")


@dp.message(Command("admin"))
async def admin_command(message: Message):
    register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "👑 <b>Панель администратора</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


@dp.message(Command("menu"))
async def menu_command(message: Message):
    register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await message.answer(
        "📋 Главное меню:",
        reply_markup=user_keyboard()
    )


# ============================================================
# USER MENU
# ============================================================

@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    user = register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if not await guard(message):
        return

    username = (
        "@" + user["username"]
        if user["username"]
        else "не указан"
    )

    await message.answer(
        "👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user['telegram_id']}</code>\n"
        f"👤 Имя: {html.escape(user['first_name'])}\n"
        f"🔗 Username: {html.escape(username)}\n"
        f"💰 Баланс: <b>{user['balance']}</b>\n"
        f"📅 Регистрация: {user['created_at']}",
        parse_mode="HTML"
    )


@dp.message(F.text == "💰 Баланс")
async def balance(message: Message):
    user = register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if not await guard(message):
        return

    await message.answer(
        "💰 <b>Ваш баланс</b>\n\n"
        f"💵 Сейчас: <b>{user['balance']}</b>\n\n"
        "➕ Пополнить — информация о пополнении\n"
        "💸 Перевод — отправить средства\n"
        "📜 История — посмотреть операции",
        parse_mode="HTML"
    )


@dp.message(F.text == "🎁 Бонус")
async def bonus(message: Message):
    user = register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if not await guard(message):
        return

    today = date.today().isoformat()

    if user["bonus_date"] == today:
        await message.answer(
            "🎁 Сегодняшний бонус уже получен.\n"
            "Возвращайтесь завтра!"
        )
        return

    amount = int(get_setting("bonus", "100"))

    with db_lock:
        con = connect_db()
        con.execute("""
            UPDATE users
            SET balance = balance + ?, bonus_date = ?
            WHERE telegram_id = ?
        """, (
            amount,
            today,
            message.from_user.id
        ))

        con.execute("""
            INSERT INTO transactions(
                telegram_id, amount, kind, description, created_at
            )
            VALUES (?, ?, 'bonus', 'Ежедневный бонус', ?)
        """, (
            message.from_user.id,
            amount,
            now()
        ))

        con.commit()
        con.close()

    add_notification(
        message.from_user.id,
        f"🎁 Получен ежедневный бонус: +{amount}"
    )

    add_log(
        message.from_user.id,
        f"Получен бонус {amount}"
    )

    await message.answer(
        f"🎁 <b>Бонус получен!</b>\n\n"
        f"Начислено: <b>+{amount}</b>",
        parse_mode="HTML"
    )


@dp.message(F.text == "📜 История")
async def history(message: Message):
    register_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if not await guard(message):
        return

    con = connect_db()
    rows = con.execute("""
        SELECT amount, description, created_at
        FROM transactions
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 15
    """, (
        message.from_user.id,
    )).fetchall()
    con.close()

    if not rows:
        await message.answer("📜 История пока пуста.")
        return

    text = "📜 <b>История операций</b>\n\n"

    for row in rows:
        sign = "+" if row["amount"] > 0 else ""
        text += (
            f"{sign}{row['amount']} — "
            f"{html.escape(row['description'])}\n"
            f"🕒 {row['created_at']}\n\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


@dp.message(F.text == "📊 Статистика")
async def user_stats(message: Message):
    if not await guard(message):
        return

    s = statistics()

    await message.answer(
        "📊 <b>Статистика проекта</b>\n\n"
        f"👥 Пользователей: {s['users']}\n"
        f"👑 Администраторов: {s['admins']}\n"
        f"🚫 Заблокировано: {s['banned']}\n"
        f"💰 Общий баланс: {s['balance']}\n"
        f"📜 Операций: {s['transactions']}",
        parse_mode="HTML"
    )


@dp.message(F.text == "🏆 Рейтинг")
async def rating(message: Message):
    if not await guard(message):
        return

    con = connect_db()
    rows = con.execute("""
        SELECT first_name, balance
        FROM users
        WHERE is_banned = 0
        ORDER BY balance DESC
        LIMIT 10
    """).fetchall()
    con.close()

    text = "🏆 <b>Топ-10</b>\n\n"

    for i, row in enumerate(rows, 1):
        text += (
            f"{i}. {html.escape(row['first_name'] or 'Пользователь')} "
            f"— <b>{row['balance']}</b>\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


@dp.message(F.text == "🔔 Уведомления")
async def notifications(message: Message):
    if not await guard(message):
        return

    con = connect_db()
    rows = con.execute("""
        SELECT text, created_at
        FROM notifications
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 15
    """, (
        message.from_user.id,
    )).fetchall()

    con.execute("""
        UPDATE notifications
        SET is_read = 1
        WHERE telegram_id = ?
    """, (
        message.from_user.id,
    ))

    con.commit()
    con.close()

    if not rows:
        await message.answer("🔔 Уведомлений нет.")
        return

    text = "🔔 <b>Уведомления</b>\n\n"

    for row in rows:
        text += (
            f"• {html.escape(row['text'])}\n"
            f"🕒 {row['created_at']}\n\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


@dp.message(F.text == "📰 Новости")
async def news(message: Message):
    if not await guard(message):
        return

    await message.answer(
        "📰 <b>Новости</b>\n\n"
        "Здесь можно разместить новости проекта.\n"
        "Админ-панель уже подготовлена для дальнейшего расширения.",
        parse_mode="HTML"
    )


@dp.message(F.text == "💸 Перевод")
async def transfer_start(message: Message):
    if not await guard(message):
        return

    states[message.from_user.id] = "transfer_id"

    await message.answer(
        "💸 <b>Перевод</b>\n\n"
        "Введите Telegram ID получателя.\n"
        "Для отмены: /cancel",
        parse_mode="HTML"
    )


@dp.message(Command("cancel"))
async def cancel(message: Message):
    states.pop(message.from_user.id, None)
    await message.answer(
        "❌ Действие отменено.",
        reply_markup=user_keyboard()
    )


@dp.message(F.text == "➕ Пополнить")
async def topup(message: Message):
    if not await guard(message):
        return

    await message.answer(
        "➕ <b>Пополнение баланса</b>\n\n"
        "В этой версии баланс изменяется через админ-панель.\n\n"
        "При необходимости сюда можно подключить "
        "реальную платёжную систему.",
        parse_mode="HTML"
    )


@dp.message(F.text == "🎮 Игры")
async def games(message: Message):
    if not await guard(message):
        return

    await message.answer(
        "🎮 <b>Игры</b>\n\n"
        "Раздел готов для добавления мини-игр и рейтингов.",
        parse_mode="HTML"
    )


@dp.message(F.text == "📋 Меню")
async def menu_button(message: Message):
    await message.answer(
        "📋 Главное меню:",
        reply_markup=user_keyboard()
    )


@dp.message(F.text == "⚙️ Настройки")
async def settings(message: Message):
    if not await guard(message):
        return

    await message.answer(
        "⚙️ <b>Настройки</b>\n\n"
        "Здесь можно добавить язык, уведомления, "
        "приватность и другие параметры.",
        parse_mode="HTML"
    )


@dp.message(F.text == "🛒 Магазин")
async def shop(message: Message):
    if not await guard(message):
        return

    products = shop_products()
    if not products:
        await message.answer("🛒 Магазин пока пуст.")
        return

    text = "🛒 <b>Магазин</b>\n\n"
    for product in products:
        stock = "∞" if product["stock"] < 0 else str(product["stock"])
        text += (
            f"<b>{product['id']}. {html.escape(product['name'])}</b>\n"
            f"{html.escape(product['description'])}\n"
            f"💰 Цена: <b>{product['price']}</b>\n"
            f"📦 Остаток: {stock}\n\n"
        )
    text += "Для покупки отправьте: <code>купить ID</code>"
    states[message.from_user.id] = "shop_buy"
    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "⭐ VIP")
async def vip_info(message: Message):
    if not await guard(message):
        return
    expires = get_vip(message.from_user.id)
    if expires:
        await message.answer(
            f"⭐ <b>VIP активен</b>\n\nДо: <b>{expires}</b>",
            parse_mode="HTML"
        )
    else:
        await message.answer("⭐ VIP отсутствует. Купить его можно в 🛒 Магазине.")


@dp.message(F.text == "📝 Задания")
async def tasks_menu(message: Message):
    if not await guard(message):
        return
    con = connect_db()
    rows = con.execute("""
        SELECT t.*, tc.id AS completed
        FROM tasks t
        LEFT JOIN task_completions tc
          ON tc.task_id=t.id AND tc.telegram_id=?
        WHERE t.active=1
        ORDER BY t.id
    """, (message.from_user.id,)).fetchall()
    con.close()

    text = "📝 <b>Задания</b>\n\n"
    for row in rows:
        status = "✅ выполнено" if row["completed"] else "⏳ доступно"
        text += (
            f"<b>{row['id']}. {html.escape(row['title'])}</b>\n"
            f"{html.escape(row['description'])}\n"
            f"🎁 Награда: {row['reward']}\n"
            f"{status}\n\n"
        )
    text += "Выполнение: <code>выполнить ID</code>"
    states[message.from_user.id] = "task_action"
    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "👥 Рефералы")
async def referrals(message: Message):
    if not await guard(message):
        return
    con = connect_db()
    count = con.execute(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id=?",
        (message.from_user.id,)
    ).fetchone()[0]
    con.close()
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    await message.answer(
        "👥 <b>Реферальная система</b>\n\n"
        f"Приглашено: <b>{count}</b>\n\n"
        f"🔗 Ваша ссылка:\n<code>{link}</code>\n\n"
        "За нового пользователя начисляется 50 внутренних монет.",
        parse_mode="HTML"
    )


@dp.message(F.text == "🪙 Монеты")
async def coins_menu(message: Message):
    if not await guard(message):
        return
    await message.answer(
        f"🪙 Ваши внутренние монеты: <b>{get_coins(message.from_user.id)}</b>",
        parse_mode="HTML"
    )


@dp.message(F.text == "💬 Связь с админом")
async def contact_admin(message: Message):
    if not await guard(message):
        return
    states[message.from_user.id] = "support"
    await message.answer(
        "💬 <b>Связь с администратором</b>\n\n"
        "Напишите сообщение одним сообщением.\n"
        "Оно попадёт в очередь поддержки.\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML"
    )


@dp.message(F.text == "🆘 Поддержка")
async def support(message: Message):
    await message.answer(
        "🆘 <b>Поддержка</b>\n\n"
        "Опишите проблему сообщением.",
        parse_mode="HTML"
    )


@dp.message(F.text == "🌐 Сайт")
async def site_button(message: Message):
    await message.answer(
        "🌐 <b>Сайт проекта</b>\n\n"
        "Если бот запущен на вашем компьютере:\n"
        "http://127.0.0.1:5000",
        parse_mode="HTML"
    )


@dp.message(F.text == "ℹ️ Помощь")
async def help_button(message: Message):
    await message.answer(
        "ℹ️ <b>Помощь</b>\n\n"
        "/start — запуск\n"
        "/menu — меню\n"
        "/admin — админ-панель\n"
        "/cancel — отменить действие",
        parse_mode="HTML"
    )


# ============================================================
# ADMIN TELEGRAM
# ============================================================

@dp.message(F.text == "👑 Админ-панель")
async def admin_panel_button(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "👑 <b>Админ-панель</b>\n\n"
        "Выберите нужный раздел.",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


@dp.message(F.text == "📊 Админ-статистика")
async def admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        return

    s = statistics()

    await message.answer(
        "📊 <b>Админ-статистика</b>\n\n"
        f"👥 Пользователи: {s['users']}\n"
        f"👑 Администраторы: {s['admins']}\n"
        f"🚫 Баны: {s['banned']}\n"
        f"💰 Баланс: {s['balance']}\n"
        f"📜 Операции: {s['transactions']}\n"
        f"📝 Логи: {s['logs']}",
        parse_mode="HTML"
    )


@dp.message(F.text == "👥 Пользователи")
async def admin_users(message: Message):
    if not is_admin(message.from_user.id):
        return

    con = connect_db()
    rows = con.execute("""
        SELECT telegram_id, first_name, username, balance,
               is_admin, is_banned
        FROM users
        ORDER BY id DESC
        LIMIT 40
    """).fetchall()
    con.close()

    text = "👥 <b>Пользователи</b>\n\n"

    for row in rows:
        status = "🚫" if row["is_banned"] else "🟢"
        text += (
            f"{status} <code>{row['telegram_id']}</code> "
            f"{html.escape(row['first_name'] or '')} "
            f"💰{row['balance']}\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


@dp.message(F.text == "🔎 Поиск")
async def admin_search(message: Message):
    if not is_admin(message.from_user.id):
        return

    states[message.from_user.id] = "admin_search"

    await message.answer(
        "🔎 Введите Telegram ID или username."
    )


@dp.message(F.text == "🚫 Баны")
async def admin_bans(message: Message):
    if not is_admin(message.from_user.id):
        return

    con = connect_db()
    rows = con.execute("""
        SELECT telegram_id, first_name
        FROM users
        WHERE is_banned = 1
    """).fetchall()
    con.close()

    if not rows:
        await message.answer("🚫 Заблокированных нет.")
        return

    text = "🚫 <b>Заблокированные</b>\n\n"

    for row in rows:
        text += (
            f"<code>{row['telegram_id']}</code> "
            f"{html.escape(row['first_name'] or '')}\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


@dp.message(F.text == "💰 Балансы")
async def admin_balances(message: Message):
    if not is_admin(message.from_user.id):
        return

    states[message.from_user.id] = "admin_balance"

    await message.answer(
        "💰 Отправьте:\n\n"
        "<code>ID сумма</code>\n\n"
        "Например:\n"
        "<code>123456789 500</code>\n\n"
        "Для снятия используйте отрицательное число.",
        parse_mode="HTML"
    )


@dp.message(F.text == "🧪 Тест ×50")
async def admin_test_50(message: Message):
    if not is_admin(message.from_user.id):
        return

    test_text = "🧪 Тестовое сообщение от MegaBot."
    sent = 0
    failed = 0

    for _ in range(50):
        try:
            await bot.send_message(message.from_user.id, test_text)
            sent += 1
            await asyncio.sleep(0.15)
        except Exception:
            failed += 1

    await message.answer(
        "🧪 <b>Тест завершён</b>\n\n"
        f"✅ Отправлено тебе: {sent}\n"
        f"❌ Ошибок: {failed}",
        parse_mode="HTML"
    )


@dp.message(F.text == "🌐 Сайт")
async def admin_site_link(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "🌐 <b>Сайт проекта</b>\n\n"
        f"{html.escape(PUBLIC_URL)}",
        parse_mode="HTML"
    )


@dp.message(F.text == "📰 Посты сайта")
async def admin_site_posts_hint(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "📰 Посты сайта создаются через веб-админку:\n"
        f"{html.escape(PUBLIC_URL)}/admin/posts"
    )


@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return

    states[message.from_user.id] = "broadcast"

    await message.answer(
        "📢 Отправьте текст рассылки одним сообщением."
    )


@dp.message(F.text == "💬 Поддержка")
async def admin_support(message: Message):
    if not is_admin(message.from_user.id):
        return

    tickets = get_open_tickets()
    if not tickets:
        await message.answer("💬 Открытых обращений нет.")
        return

    text = "💬 <b>Открытые обращения</b>\n\n"
    for ticket in tickets:
        text += (
            f"#{ticket['id']} — <code>{ticket['telegram_id']}</code>\n"
            f"{html.escape(ticket['message'])}\n"
            f"<code>/answer {ticket['id']} ваш ответ</code>\n\n"
        )

    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "📜 Логи")
async def admin_logs(message: Message):
    if not is_admin(message.from_user.id):
        return

    con = connect_db()
    rows = con.execute("""
        SELECT telegram_id, action, created_at
        FROM logs
        ORDER BY id DESC
        LIMIT 40
    """).fetchall()
    con.close()

    text = "📜 <b>Логи</b>\n\n"

    for row in rows:
        text += (
            f"<code>{row['telegram_id']}</code> "
            f"{html.escape(row['action'])}\n"
            f"{row['created_at']}\n\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


@dp.message(F.text == "👮 Администраторы")
async def admin_admins(message: Message):
    if not is_admin(message.from_user.id):
        return

    con = connect_db()
    rows = con.execute("""
        SELECT telegram_id, first_name, username
        FROM users
        WHERE is_admin = 1
    """).fetchall()
    con.close()

    text = "👮 <b>Администраторы</b>\n\n"

    for row in rows:
        text += (
            f"• <code>{row['telegram_id']}</code> "
            f"{html.escape(row['first_name'] or '')}\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


@dp.message(F.text == "⚙️ Настройки")
async def admin_settings_or_user_settings(message: Message):
    # If admin keyboard is currently being used, show admin settings.
    if is_admin(message.from_user.id):
        bonus = get_setting("bonus", "100")
        maintenance = get_setting("maintenance", "0")

        await message.answer(
            "⚙️ <b>Настройки бота</b>\n\n"
            f"🎁 Бонус: <b>{bonus}</b>\n"
            f"🔧 Технический режим: "
            f"<b>{'Включён' if maintenance == '1' else 'Выключен'}</b>\n\n"
            "Настройки можно расширить.",
            parse_mode="HTML"
        )
        return

    await settings(message)


@dp.message(F.text == "🔙 Обычное меню")
async def normal_menu(message: Message):
    await message.answer(
        "🔙 Обычное меню:",
        reply_markup=user_keyboard()
    )


# ============================================================
# TELEGRAM STATE HANDLER
# ============================================================

@dp.message()
async def state_handler(message: Message):
    tg_id = message.from_user.id
    text = (message.text or "").strip()

    if text.startswith("/"):
        return

    register_user(
        tg_id,
        message.from_user.username,
        message.from_user.first_name
    )

    state = states.get(tg_id)

    # --------------------------------------------------------
    # SHOP
    # --------------------------------------------------------

    if state == "shop_buy":
        value = text.split(maxsplit=1)[1].strip() if text.lower().startswith("купить ") else text
        if not value.isdigit():
            await message.answer("❌ Укажите ID товара.")
            return
        product = get_product(int(value))
        if not product:
            states.pop(tg_id, None)
            await message.answer("❌ Товар не найден.")
            return
        if product["stock"] == 0:
            states.pop(tg_id, None)
            await message.answer("❌ Товар закончился.")
            return
        user = get_user(tg_id)
        if user["balance"] < product["price"]:
            states.pop(tg_id, None)
            await message.answer("❌ Недостаточно средств.")
            return

        change_balance(tg_id, -product["price"], "purchase", f"Покупка: {product['name']}")

        with db_lock:
            con = connect_db()
            if product["stock"] > 0:
                con.execute("UPDATE products SET stock=stock-1 WHERE id=?", (product["id"],))
            con.execute(
                "INSERT INTO purchases(telegram_id,product_id,price,created_at) VALUES(?,?,?,?)",
                (tg_id, product["id"], product["price"], now())
            )
            con.commit()
            con.close()

        if "VIP на 7" in product["name"]:
            add_vip_days(tg_id, 7)
        elif "VIP на 30" in product["name"]:
            add_vip_days(tg_id, 30)

        add_notification(tg_id, f"🛒 Куплено: {product['name']}")
        states.pop(tg_id, None)
        await message.answer(
            f"✅ Покупка успешна!\n🛒 {html.escape(product['name'])}\n💰 Списано: {product['price']}",
            parse_mode="HTML"
        )
        return

    # --------------------------------------------------------
    # TASKS
    # --------------------------------------------------------

    if state == "task_action":
        value = text.split(maxsplit=1)[1].strip() if text.lower().startswith("выполнить ") else text
        if not value.isdigit():
            await message.answer("❌ Укажите ID задания.")
            return
        task_id = int(value)
        con = connect_db()
        task = con.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
        con.close()
        if not task:
            states.pop(tg_id, None)
            await message.answer("❌ Задание не найдено.")
            return
        try:
            with db_lock:
                con = connect_db()
                con.execute(
                    "INSERT INTO task_completions(task_id,telegram_id,created_at) VALUES(?,?,?)",
                    (task_id, tg_id, now())
                )
                con.commit()
                con.close()
        except sqlite3.IntegrityError:
            await message.answer("ℹ️ Вы уже выполнили это задание.")
            return

        change_balance(tg_id, task["reward"], "task", f"Награда: {task['title']}")
        add_notification(tg_id, f"📝 Задание выполнено. +{task['reward']}")
        states.pop(tg_id, None)
        await message.answer(
            f"✅ Задание выполнено!\n🎁 Награда: <b>+{task['reward']}</b>",
            parse_mode="HTML"
        )
        return

    # --------------------------------------------------------
    # SUPPORT
    # --------------------------------------------------------

    if state == "support":
        ticket_id = open_support_ticket(tg_id, text)
        add_log(tg_id, f"Создан тикет поддержки #{ticket_id}")

        con = connect_db()
        admins = con.execute("SELECT telegram_id FROM users WHERE is_admin=1").fetchall()
        con.close()

        for admin in admins:
            try:
                await bot.send_message(
                    admin["telegram_id"],
                    "💬 <b>Новое обращение</b>\n\n"
                    f"Тикет: <code>#{ticket_id}</code>\n"
                    f"Пользователь: <code>{tg_id}</code>\n\n"
                    f"{html.escape(text)}\n\n"
                    f"Ответ: <code>/answer {ticket_id} ваш ответ</code>",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        states.pop(tg_id, None)
        await message.answer(
            f"✅ Сообщение отправлено администратору.\nНомер обращения: <b>#{ticket_id}</b>",
            parse_mode="HTML"
        )
        return

    # --------------------------------------------------------
    # TRANSFER
    # --------------------------------------------------------

    if state == "transfer_id":
        if not text.isdigit():
            await message.answer(
                "❌ Telegram ID должен состоять из цифр."
            )
            return

        recipient = int(text)

        if not get_user(recipient):
            states.pop(tg_id, None)
            await message.answer(
                "❌ Получатель не найден."
            )
            return

        states[tg_id] = f"transfer_amount:{recipient}"

        await message.answer(
            "💸 Теперь отправьте сумму."
        )
        return

    if isinstance(state, str) and state.startswith("transfer_amount:"):
        recipient = int(state.split(":")[1])

        try:
            amount = int(text)
        except ValueError:
            await message.answer(
                "❌ Сумма должна быть числом."
            )
            return

        if amount <= 0:
            await message.answer(
                "❌ Сумма должна быть больше нуля."
            )
            return

        sender = get_user(tg_id)

        if sender["balance"] < amount:
            states.pop(tg_id, None)
            await message.answer(
                "❌ Недостаточно средств."
            )
            return

        change_balance(
            tg_id,
            -amount,
            "transfer_out",
            f"Перевод пользователю {recipient}"
        )

        change_balance(
            recipient,
            amount,
            "transfer_in",
            f"Перевод от пользователя {tg_id}"
        )

        add_notification(
            recipient,
            f"💸 Вы получили {amount} от пользователя {tg_id}"
        )

        add_log(
            tg_id,
            f"Перевод {amount} пользователю {recipient}"
        )

        states.pop(tg_id, None)

        await message.answer(
            f"✅ Перевод выполнен.\n"
            f"Получатель: <code>{recipient}</code>\n"
            f"Сумма: <b>{amount}</b>",
            parse_mode="HTML"
        )
        return

    # --------------------------------------------------------
    # ADMIN SEARCH
    # --------------------------------------------------------

    if state == "admin_search":
        if not is_admin(tg_id):
            states.pop(tg_id, None)
            return

        con = connect_db()

        if text.isdigit():
            row = con.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (int(text),)
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM users WHERE username = ?",
                (text.lstrip("@"),)
            ).fetchone()

        con.close()
        states.pop(tg_id, None)

        if not row:
            await message.answer(
                "❌ Пользователь не найден."
            )
            return

        await message.answer(
            "🔎 <b>Пользователь найден</b>\n\n"
            f"ID: <code>{row['telegram_id']}</code>\n"
            f"Имя: {html.escape(row['first_name'])}\n"
            f"Username: @{html.escape(row['username']) if row['username'] else 'нет'}\n"
            f"Баланс: <b>{row['balance']}</b>\n"
            f"Админ: {'Да' if row['is_admin'] else 'Нет'}\n"
            f"Бан: {'Да' if row['is_banned'] else 'Нет'}",
            parse_mode="HTML"
        )
        return

    # --------------------------------------------------------
    # ADMIN BALANCE
    # --------------------------------------------------------

    if state == "admin_balance":
        if not is_admin(tg_id):
            states.pop(tg_id, None)
            return

        parts = text.split()

        if len(parts) != 2 or not parts[0].isdigit():
            await message.answer(
                "❌ Формат: ID сумма\n"
                "Например: 123456789 500"
            )
            return

        target = int(parts[0])

        try:
            amount = int(parts[1])
        except ValueError:
            await message.answer(
                "❌ Сумма должна быть числом."
            )
            return

        if not get_user(target):
            await message.answer(
                "❌ Пользователь не найден."
            )
            return

        change_balance(
            target,
            amount,
            "admin",
            f"Изменение администратором {tg_id}"
        )

        add_notification(
            target,
            f"💰 Баланс изменён на {amount}"
        )

        add_log(
            tg_id,
            f"Изменение баланса {target}: {amount}"
        )

        states.pop(tg_id, None)

        await message.answer(
            f"✅ Баланс пользователя <code>{target}</code> "
            f"изменён на <b>{amount}</b>.",
            parse_mode="HTML"
        )
        return

    # --------------------------------------------------------
    # BROADCAST
    # --------------------------------------------------------

    if state == "broadcast":
        if not is_admin(tg_id):
            states.pop(tg_id, None)
            return

        states.pop(tg_id, None)

        con = connect_db()
        users = con.execute("""
            SELECT telegram_id
            FROM users
            WHERE is_banned = 0
        """).fetchall()
        con.close()

        sent = 0
        failed = 0

        for row in users:
            try:
                await bot.send_message(
                    row["telegram_id"],
                    "📢 <b>Сообщение администрации</b>\n\n"
                    + html.escape(text),
                    parse_mode="HTML"
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        add_log(
            tg_id,
            f"Рассылка: {sent} успешно, {failed} ошибок"
        )

        await message.answer(
            "📢 <b>Рассылка завершена</b>\n\n"
            f"✅ Отправлено: {sent}\n"
            f"❌ Ошибок: {failed}",
            parse_mode="HTML"
        )
        return

    await message.answer(
        "🤖 Используйте кнопки меню.",
        reply_markup=(
            admin_keyboard()
            if is_admin(tg_id)
            else user_keyboard()
        )
    )


# ============================================================
# WEBSITE HTML
# ============================================================

STYLE = """
<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #0b1020;
    color: #eef2ff;
    font-family: Arial, Helvetica, sans-serif;
}

nav {
    background: #11182b;
    border-bottom: 1px solid #26304a;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    position: sticky;
    top: 0;
    z-index: 10;
}

.logo {
    font-size: 21px;
    font-weight: 800;
}

nav a {
    color: #dce4ff;
    text-decoration: none;
    margin-left: 12px;
}

.container {
    width: min(1150px, calc(100% - 30px));
    margin: 35px auto;
}

.hero {
    padding: 45px;
    border-radius: 25px;
    background: linear-gradient(135deg, #151d35, #10172a);
    border: 1px solid #293655;
    margin-bottom: 25px;
}

.hero h1 {
    font-size: 42px;
    margin: 0 0 12px;
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 18px;
}

.card {
    background: #11182b;
    border: 1px solid #27334f;
    border-radius: 18px;
    padding: 22px;
    margin-bottom: 18px;
}

.stat {
    font-size: 32px;
    font-weight: 800;
    margin-top: 8px;
}

.button {
    display: inline-block;
    padding: 11px 16px;
    border-radius: 11px;
    background: #5865f2;
    color: white;
    text-decoration: none;
    border: 0;
    cursor: pointer;
    margin: 4px;
}

.button.secondary {
    background: #252f49;
}

.button.danger {
    background: #c93d57;
}

input, textarea, select {
    width: 100%;
    background: #0b1020;
    border: 1px solid #303d5c;
    color: white;
    border-radius: 10px;
    padding: 13px;
    margin: 7px 0 14px;
}

table {
    width: 100%;
    border-collapse: collapse;
    overflow: hidden;
}

th, td {
    padding: 12px;
    border-bottom: 1px solid #29344e;
    text-align: left;
}

.badge {
    background: #25304a;
    padding: 5px 9px;
    border-radius: 9px;
}

.small {
    color: #9da9c7;
}

.error {
    color: #ff778f;
}

.success {
    color: #6ee7a2;
}

@media(max-width: 650px) {
    .hero {
        padding: 25px;
    }

    .hero h1 {
        font-size: 30px;
    }

    nav {
        flex-direction: column;
        align-items: flex-start;
    }

    table {
        font-size: 13px;
    }
}
</style>
"""


def page(title, body):
    return render_template_string(
        """
        <!doctype html>
        <html lang="ru">
        <head>
            <meta charset="utf-8">
            <meta name="viewport"
                  content="width=device-width, initial-scale=1">
            <title>{{ title }}</title>
            {{ style|safe }}
        </head>
        <body>
            <nav>
                <div class="logo">🤖 MegaBot</div>
                <div>
                    <a href="/">Главная</a>
                    <a href="/bot">🤖 Бот</a>
                    <a href="/vip">⭐ VIP</a>
                    <a href="/posts">📰 Новости</a>
                    {% if session.get("admin") %}
                    <a href="/admin">Админка</a>
                    <a href="/admin/users">Пользователи</a>
                    <a href="/admin/logs">Логи</a>
                    <a href="/logout">Выйти</a>
                    {% else %}
                    <a href="/login">Войти</a>
                    {% endif %}
                </div>
            </nav>
            <main class="container">
                {{ body|safe }}
            </main>
        </body>
        </html>
        """,
        title=title,
        body=body,
        style=STYLE
    )


# ============================================================
# WEBSITE PUBLIC HOME
# ============================================================

@app.route("/")
def website_home():
    s = statistics()

    body = f"""
    <section class="hero">
        <h1>🤖 MegaBot</h1>
        <p>
            Telegram-бот и веб-панель управления,
            работающие из одного Python-файла.
        </p>
        <a class="button" href="/bot">🤖 Посмотреть меню бота</a>
        <a class="button secondary" href="/vip">⭐ Посмотреть VIP</a>
        <a class="button secondary" href="/posts">📰 Новости</a>
        <a class="button" href="/login">🔐 Войти в админку</a>
    </section>

    <div class="grid">
        <div class="card">
            <div>👥 Пользователи</div>
            <div class="stat">{s['users']}</div>
        </div>

        <div class="card">
            <div>👑 Администраторы</div>
            <div class="stat">{s['admins']}</div>
        </div>

        <div class="card">
            <div>🚫 Баны</div>
            <div class="stat">{s['banned']}</div>
        </div>

        <div class="card">
            <div>📜 Операции</div>
            <div class="stat">{s['transactions']}</div>
        </div>
    </div>

    <div class="card">
        <h2>🌐 Что умеет сайт</h2>
        <p>
            Управление пользователями, балансами, банами,
            статистикой и логами. База данных общая с Telegram-ботом.
        </p>
    </div>
    """

    return page("MegaBot — Главная", body)


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""

    if request.method == "POST":
        value = request.form.get("telegram_id", "").strip()

        if value.isdigit() and is_admin(int(value)):
            session["admin"] = True
            session["admin_id"] = int(value)

            add_log(
                int(value),
                "Вход в веб-админку"
            )

            return redirect("/admin")

        error = "Неверный ID или пользователь не является администратором."

    body = f"""
    <div class="card">
        <h1>🔐 Вход администратора</h1>

        <p class="small">
            Используйте свой Telegram ID.
        </p>

        <form method="post">
            <label>Telegram ID</label>
            <input
                name="telegram_id"
                placeholder="123456789"
                required
            >
            <button class="button" type="submit">
                Войти
            </button>
        </form>

        <p class="error">{html.escape(error)}</p>
    </div>
    """

    return page("Вход", body)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper


# ============================================================
# WEBSITE ADMIN DASHBOARD
# ============================================================

@app.route("/admin")
@admin_required
def admin_dashboard():
    s = statistics()

    body = f"""
    <div class="hero">
        <h1>👑 Админ-панель</h1>
        <p>
            Полное управление основными функциями проекта.
        </p>
    </div>

    <div class="grid">

        <div class="card">
            <div>📰 Посты сайта</div>
            <p class="small">Публикации без изменения кода</p>
            <a class="button" href="/admin/posts">Открыть</a>
        </div>

        <div class="card">
            <div>👥 Пользователи</div>
            <div class="stat">{s['users']}</div>
            <a class="button" href="/admin/users">Открыть</a>
        </div>

        <div class="card">
            <div>👑 Администраторы</div>
            <div class="stat">{s['admins']}</div>
        </div>

        <div class="card">
            <div>🚫 Заблокировано</div>
            <div class="stat">{s['banned']}</div>
        </div>

        <div class="card">
            <div>💰 Общий баланс</div>
            <div class="stat">{s['balance']}</div>
        </div>

        <div class="card">
            <div>📜 Операции</div>
            <div class="stat">{s['transactions']}</div>
        </div>

        <div class="card">
            <div>📝 Логи</div>
            <div class="stat">{s['logs']}</div>
        </div>

    </div>

    <div class="card">
        <h2>⚡ Быстрые действия</h2>
        <a class="button" href="/admin/users">👥 Пользователи</a>
        <a class="button" href="/admin/logs">📜 Логи</a>
        <a class="button" href="/admin/settings">⚙️ Настройки</a>
        <a class="button" href="/admin/broadcast">📢 Рассылка</a>
    </div>
    """

    return page("Админ-панель", body)


# ============================================================
# WEBSITE USERS
# ============================================================

@app.route("/admin/users")
@admin_required
def website_users():
    q = request.args.get("q", "").strip()

    con = connect_db()

    if q:
        if q.isdigit():
            rows = con.execute("""
                SELECT *
                FROM users
                WHERE telegram_id = ?
                ORDER BY id DESC
            """, (int(q),)).fetchall()
        else:
            rows = con.execute("""
                SELECT *
                FROM users
                WHERE username LIKE ?
                   OR first_name LIKE ?
                ORDER BY id DESC
            """, (
                "%" + q + "%",
                "%" + q + "%"
            )).fetchall()
    else:
        rows = con.execute("""
            SELECT *
            FROM users
            ORDER BY id DESC
            LIMIT 100
        """).fetchall()

    con.close()

    rows_html = ""

    for user in rows:
        status = (
            '<span class="badge">🚫 Бан</span>'
            if user["is_banned"]
            else '<span class="badge">🟢 Активен</span>'
        )

        rows_html += f"""
        <tr>
            <td>{user['telegram_id']}</td>
            <td>{html.escape(user['first_name'] or '')}</td>
            <td>{html.escape(user['username'] or '')}</td>
            <td>{user['balance']}</td>
            <td>{status}</td>
            <td>
                <a class="button"
                   href="/admin/user/{user['telegram_id']}">
                   Открыть
                </a>
            </td>
        </tr>
        """

    body = f"""
    <div class="card">
        <h1>👥 Пользователи</h1>

        <form method="get">
            <input
                name="q"
                value="{html.escape(q)}"
                placeholder="ID, username или имя"
            >
            <button class="button" type="submit">
                🔎 Найти
            </button>
        </form>
    </div>

    <div class="card">
        <table>
            <tr>
                <th>Telegram ID</th>
                <th>Имя</th>
                <th>Username</th>
                <th>Баланс</th>
                <th>Статус</th>
                <th></th>
            </tr>
            {rows_html}
        </table>
    </div>
    """

    return page("Пользователи", body)


# ============================================================
# WEBSITE USER DETAILS
# ============================================================

@app.route("/admin/user/<int:tg_id>", methods=["GET", "POST"])
@admin_required
def website_user_details(tg_id):
    user = get_user(tg_id)

    if not user:
        return page(
            "Не найден",
            '<div class="card"><h1>❌ Пользователь не найден</h1></div>'
        )

    message = ""

    if request.method == "POST":
        action = request.form.get("action")

        if action == "balance":
            try:
                amount = int(request.form.get("amount", "0"))
            except ValueError:
                amount = 0

            change_balance(
                tg_id,
                amount,
                "web_admin",
                f"Изменение баланса через сайт"
            )

            add_notification(
                tg_id,
                f"💰 Баланс изменён на {amount}"
            )

            add_log(
                session["admin_id"],
                f"Сайт: баланс {tg_id} изменён на {amount}"
            )

            message = "Баланс изменён."

        elif action == "ban":
            with db_lock:
                con = connect_db()
                con.execute(
                    "UPDATE users SET is_banned = 1 WHERE telegram_id = ?",
                    (tg_id,)
                )
                con.commit()
                con.close()

            add_log(
                session["admin_id"],
                f"Сайт: пользователь {tg_id} заблокирован"
            )

            message = "Пользователь заблокирован."

        elif action == "unban":
            with db_lock:
                con = connect_db()
                con.execute(
                    "UPDATE users SET is_banned = 0 WHERE telegram_id = ?",
                    (tg_id,)
                )
                con.commit()
                con.close()

            add_log(
                session["admin_id"],
                f"Сайт: пользователь {tg_id} разблокирован"
            )

            message = "Пользователь разблокирован."

        elif action == "admin":
            value = request.form.get("value") == "1"

            with db_lock:
                con = connect_db()
                con.execute(
                    "UPDATE users SET is_admin = ? WHERE telegram_id = ?",
                    (1 if value else 0, tg_id)
                )
                con.commit()
                con.close()

            add_log(
                session["admin_id"],
                f"Сайт: права администратора {tg_id} = {value}"
            )

            message = "Права администратора изменены."

    user = get_user(tg_id)

    body = f"""
    <div class="card">
        <h1>👤 Пользователь</h1>

        <p>Telegram ID:
            <b>{user['telegram_id']}</b>
        </p>

        <p>Имя:
            <b>{html.escape(user['first_name'] or '')}</b>
        </p>

        <p>Username:
            <b>{html.escape(user['username'] or 'нет')}</b>
        </p>

        <p>Баланс:
            <b>{user['balance']}</b>
        </p>

        <p>Администратор:
            <b>{'Да' if user['is_admin'] else 'Нет'}</b>
        </p>

        <p>Бан:
            <b>{'Да' if user['is_banned'] else 'Нет'}</b>
        </p>

        <p class="success">{html.escape(message)}</p>
    </div>

    <div class="card">
        <h2>💰 Изменить баланс</h2>

        <form method="post">
            <input type="hidden" name="action" value="balance">
            <input
                name="amount"
                placeholder="Например: 500 или -100"
                required
            >
            <button class="button" type="submit">
                Изменить
            </button>
        </form>
    </div>

    <div class="card">
        <h2>🚫 Блокировка</h2>

        <form method="post" style="display:inline">
            <input type="hidden" name="action" value="ban">
            <button class="button danger" type="submit">
                Заблокировать
            </button>
        </form>

        <form method="post" style="display:inline">
            <input type="hidden" name="action" value="unban">
            <button class="button" type="submit">
                Разблокировать
            </button>
        </form>
    </div>

    <div class="card">
        <h2>👑 Права администратора</h2>

        <form method="post">
            <input type="hidden" name="action" value="admin">
            <input type="hidden" name="value"
                   value="{'0' if user['is_admin'] else '1'}">

            <button class="button" type="submit">
                {'Снять права' if user['is_admin'] else 'Назначить админом'}
            </button>
        </form>
    </div>
    """

    return page("Пользователь", body)


# ============================================================
# WEBSITE LOGS
# ============================================================

@app.route("/admin/logs")
@admin_required
def website_logs():
    con = connect_db()
    rows = con.execute("""
        SELECT telegram_id, action, created_at
        FROM logs
        ORDER BY id DESC
        LIMIT 200
    """).fetchall()
    con.close()

    rows_html = ""

    for row in rows:
        rows_html += f"""
        <tr>
            <td>{row['telegram_id']}</td>
            <td>{html.escape(row['action'])}</td>
            <td>{row['created_at']}</td>
        </tr>
        """

    body = f"""
    <div class="card">
        <h1>📜 Логи</h1>

        <table>
            <tr>
                <th>Telegram ID</th>
                <th>Действие</th>
                <th>Дата</th>
            </tr>
            {rows_html}
        </table>
    </div>
    """

    return page("Логи", body)


# ============================================================
# WEBSITE SETTINGS
# ============================================================

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def website_settings():
    message = ""

    if request.method == "POST":
        bonus = request.form.get("bonus", "100")
        maintenance = "1" if request.form.get("maintenance") else "0"

        try:
            int(bonus)
        except ValueError:
            bonus = "100"

        set_setting("bonus", bonus)
        set_setting("maintenance", maintenance)

        add_log(
            session["admin_id"],
            "Изменение настроек сайта"
        )

        message = "Настройки сохранены."

    bonus = get_setting("bonus", "100")
    maintenance = get_setting("maintenance", "0")

    checked = "checked" if maintenance == "1" else ""

    body = f"""
    <div class="card">
        <h1>⚙️ Настройки</h1>

        <p class="success">{html.escape(message)}</p>

        <form method="post">
            <label>🎁 Размер ежедневного бонуса</label>
            <input
                name="bonus"
                value="{html.escape(bonus)}"
            >

            <label>
                <input
                    type="checkbox"
                    name="maintenance"
                    {checked}
                    style="width:auto"
                >
                🔧 Технический режим
            </label>

            <br>

            <button class="button" type="submit">
                💾 Сохранить
            </button>
        </form>
    </div>
    """

    return page("Настройки", body)


# ============================================================
# WEBSITE SHOP
# ============================================================

@app.route("/admin/shop", methods=["GET", "POST"])
@admin_required
def website_shop():
    message = ""

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            try:
                price = int(request.form.get("price", "0"))
                stock = int(request.form.get("stock", "-1"))
            except ValueError:
                price, stock = 0, -1

            if name and price >= 0:
                with db_lock:
                    con = connect_db()
                    con.execute("""
                        INSERT INTO products(
                            name, description, price, stock, active, created_at
                        ) VALUES(?,?,?,?,1,?)
                    """, (name, description, price, stock, now()))
                    con.commit()
                    con.close()
                message = "Товар добавлен."

        elif action == "toggle":
            pid = int(request.form.get("id", "0"))
            with db_lock:
                con = connect_db()
                con.execute("UPDATE products SET active=1-active WHERE id=?", (pid,))
                con.commit()
                con.close()
            message = "Статус товара изменён."

    products = shop_products()
    rows = ""
    for product in products:
        rows += f"""
        <tr>
            <td>{product['id']}</td>
            <td>{html.escape(product['name'])}</td>
            <td>{product['price']}</td>
            <td>{'∞' if product['stock'] < 0 else product['stock']}</td>
            <td>
                <form method="post">
                    <input type="hidden" name="action" value="toggle">
                    <input type="hidden" name="id" value="{product['id']}">
                    <button class="button secondary" type="submit">Вкл./Выкл.</button>
                </form>
            </td>
        </tr>
        """

    body = f"""
    <div class="card">
        <h1>🛒 Магазин</h1>
        <p class="success">{html.escape(message)}</p>
        <form method="post">
            <input type="hidden" name="action" value="add">
            <input name="name" placeholder="Название товара" required>
            <input name="description" placeholder="Описание">
            <input name="price" type="number" min="0" placeholder="Цена" required>
            <input name="stock" type="number" placeholder="Остаток (-1 = бесконечно)" value="-1">
            <button class="button" type="submit">➕ Добавить товар</button>
        </form>
    </div>
    <div class="card">
        <table>
            <tr><th>ID</th><th>Название</th><th>Цена</th><th>Остаток</th><th></th></tr>
            {rows}
        </table>
    </div>
    """
    return page("Магазин", body)


# ============================================================
# WEBSITE SUPPORT
# ============================================================

@app.route("/admin/support")
@admin_required
def website_support():
    tickets = get_open_tickets()
    rows = ""

    for ticket in tickets:
        rows += f"""
        <div class="card">
            <h3>💬 Тикет #{ticket['id']}</h3>
            <p>Пользователь: <code>{ticket['telegram_id']}</code></p>
            <p>{html.escape(ticket['message'])}</p>
            <form method="post" action="/admin/support/answer">
                <input type="hidden" name="id" value="{ticket['id']}">
                <textarea name="answer" rows="4" placeholder="Ответ..." required></textarea>
                <button class="button" type="submit">📨 Ответить</button>
            </form>
        </div>
        """

    if not rows:
        rows = '<div class="card">Открытых обращений нет.</div>'

    return page("Поддержка", rows)


@app.route("/admin/support/answer", methods=["POST"])
@admin_required
def website_support_answer():
    ticket_id = int(request.form.get("id", "0"))
    answer_text = request.form.get("answer", "").strip()

    con = connect_db()
    ticket = con.execute(
        "SELECT * FROM support_tickets WHERE id=?",
        (ticket_id,)
    ).fetchone()
    con.close()

    if ticket and answer_text:
        with db_lock:
            con = connect_db()
            con.execute(
                "UPDATE support_tickets SET answer=?, status='answered', answered_at=? WHERE id=?",
                (answer_text, now(), ticket_id)
            )
            con.commit()
            con.close()

        add_notification(
            ticket["telegram_id"],
            f"💬 Ответ администратора по обращению #{ticket_id}: {answer_text}"
        )

        add_log(
            session["admin_id"],
            f"Веб-ответ на тикет #{ticket_id}"
        )

        async def send_answer():
            try:
                await bot.send_message(
                    ticket["telegram_id"],
                    "💬 <b>Ответ администратора</b>\n\n" + html.escape(answer_text),
                    parse_mode="HTML"
                )
            except Exception:
                pass

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(send_answer())
        finally:
            loop.close()

    return redirect("/admin/support")


# ============================================================
# WEBSITE BROADCAST
# ============================================================

@app.route("/admin/broadcast", methods=["GET", "POST"])
@admin_required
def website_broadcast():
    result = ""

    if request.method == "POST":
        text = request.form.get("text", "").strip()

        if text:
            con = connect_db()
            users = con.execute("""
                SELECT telegram_id
                FROM users
                WHERE is_banned = 0
            """).fetchall()
            con.close()

            async def send_all():
                sent = 0
                failed = 0

                for row in users:
                    try:
                        await bot.send_message(
                            row["telegram_id"],
                            "📢 <b>Сообщение администрации</b>\n\n"
                            + html.escape(text),
                            parse_mode="HTML"
                        )
                        sent += 1
                        await asyncio.sleep(0.05)
                    except Exception:
                        failed += 1

                return sent, failed

            # Flask runs outside the bot event loop.
            # Run this small task in a dedicated event loop.
            loop = asyncio.new_event_loop()
            try:
                sent, failed = loop.run_until_complete(send_all())
            finally:
                loop.close()

            add_log(
                session["admin_id"],
                f"Веб-рассылка: {sent} успешно, {failed} ошибок"
            )

            result = (
                f"Отправлено: {sent}. Ошибок: {failed}."
            )

    body = f"""
    <div class="card">
        <h1>📢 Рассылка</h1>

        <p class="small">
            Сообщение будет отправлено всем незаблокированным пользователям.
        </p>

        <p class="success">{html.escape(result)}</p>

        <form method="post">
            <textarea
                name="text"
                rows="8"
                placeholder="Текст сообщения..."
                required
            ></textarea>

            <button class="button" type="submit">
                📢 Отправить
            </button>
        </form>
    </div>
    """

    return page("Рассылка", body)


# ============================================================
# PUBLIC BOT SHOWCASE + SITE POSTS
# ============================================================

@app.route("/bot")
def website_bot_showcase():
    user_buttons = [
        "👤 Профиль", "💰 Баланс", "🎁 Бонус", "📜 История",
        "📊 Статистика", "🏆 Рейтинг", "🔔 Уведомления", "📰 Новости",
        "💸 Перевод", "➕ Пополнить", "🎮 Игры", "📋 Меню",
        "⚙️ Настройки", "🆘 Поддержка", "🛒 Магазин", "⭐ VIP",
        "📝 Задания", "👥 Рефералы", "🪙 Монеты", "💬 Связь с админом",
        "🌐 Сайт", "ℹ️ Помощь"
    ]
    admin_buttons = [
        "👑 Админ-панель", "📊 Админ-статистика", "👥 Пользователи", "🔎 Поиск",
        "🚫 Баны", "💰 Балансы", "📢 Рассылка", "💬 Поддержка", "📜 Логи",
        "🛒 Магазин", "⭐ VIP", "📝 Задания", "🌐 Сайт", "🧪 Тест ×50",
        "📰 Посты сайта", "👮 Администраторы", "⚙️ Настройки"
    ]

    def disabled_buttons(items):
        return "".join(
            f'<button class="button secondary" disabled>{html.escape(x)}</button>'
            for x in items
        )

    body = f"""
    <div class="hero">
        <h1>🤖 Меню MegaBot</h1>
        <p>Здесь можно посмотреть возможности бота. Кнопки ниже демонстрационные и не нажимаются.</p>
    </div>

    <div class="card">
        <h2>👤 Пользовательское меню</h2>
        <div>{disabled_buttons(user_buttons)}</div>
    </div>

    <div class="card">
        <h2>👑 Админское меню</h2>
        <p class="small">Показывается только как демонстрация интерфейса.</p>
        <div>{disabled_buttons(admin_buttons)}</div>
    </div>
    """
    return page("Меню бота", body)


@app.route("/vip")
def website_vip():
    products = []
    con = connect_db()
    rows = con.execute(
        "SELECT name, description, price, stock FROM products "
        "WHERE active=1 AND name LIKE 'VIP%' ORDER BY price"
    ).fetchall()
    con.close()

    cards = ""
    for row in rows:
        stock = "∞" if row["stock"] < 0 else str(row["stock"])
        cards += f"""
        <div class="card">
            <h2>⭐ {html.escape(row['name'])}</h2>
            <p>{html.escape(row['description'])}</p>
            <p><b>🪙 {row['price']}</b> монет</p>
            <p class="small">Остаток: {stock}</p>
        </div>
        """
    if not cards:
        cards = '<div class="card">VIP-предложения пока не добавлены.</div>'

    return page("VIP", f"""
    <div class="hero">
        <h1>⭐ VIP</h1>
        <p>Информация о VIP-статусах бота.</p>
    </div>
    {cards}
    """)


@app.route("/posts")
def website_posts():
    con = connect_db()
    rows = con.execute(
        "SELECT id, title, content, created_at FROM site_posts "
        "WHERE active=1 ORDER BY id DESC"
    ).fetchall()
    con.close()

    cards = ""
    for row in rows:
        cards += f"""
        <div class="card">
            <h2>📰 {html.escape(row['title'])}</h2>
            <p class="small">{html.escape(row['created_at'])}</p>
            <div>{html.escape(row['content']).replace(chr(10), '<br>')}</div>
        </div>
        """
    if not cards:
        cards = '<div class="card">Постов пока нет.</div>'

    return page("Новости", f"""
    <div class="hero">
        <h1>📰 Новости</h1>
        <p>Посты, которые администратор публикует через сайт.</p>
    </div>
    {cards}
    """)


@app.route("/admin/posts", methods=["GET", "POST"])
@admin_required
def website_posts_admin():
    message = ""

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "add":
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            if title and content:
                with db_lock:
                    con = connect_db()
                    con.execute(
                        "INSERT INTO site_posts(title, content, created_at, active) "
                        "VALUES(?,?,?,1)",
                        (title, content, now())
                    )
                    con.commit()
                    con.close()
                add_log(session["admin_id"], f"Создан пост сайта: {title}")
                message = "Пост опубликован."

        elif action == "toggle":
            post_id = int(request.form.get("id", "0"))
            with db_lock:
                con = connect_db()
                con.execute(
                    "UPDATE site_posts SET active=1-active WHERE id=?",
                    (post_id,)
                )
                con.commit()
                con.close()
            message = "Статус поста изменён."

        elif action == "delete":
            post_id = int(request.form.get("id", "0"))
            with db_lock:
                con = connect_db()
                con.execute("DELETE FROM site_posts WHERE id=?", (post_id,))
                con.commit()
                con.close()
            message = "Пост удалён."

    con = connect_db()
    rows = con.execute(
        "SELECT id, title, content, created_at, active "
        "FROM site_posts ORDER BY id DESC"
    ).fetchall()
    con.close()

    table = ""
    for row in rows:
        status = "🟢 опубликован" if row["active"] else "⚫ скрыт"
        table += f"""
        <tr>
            <td>{row['id']}</td>
            <td>{html.escape(row['title'])}</td>
            <td>{status}</td>
            <td>{html.escape(row['created_at'])}</td>
            <td>
                <form method="post" style="display:inline">
                    <input type="hidden" name="action" value="toggle">
                    <input type="hidden" name="id" value="{row['id']}">
                    <button class="button secondary" type="submit">Вкл./Выкл.</button>
                </form>
                <form method="post" style="display:inline">
                    <input type="hidden" name="action" value="delete">
                    <input type="hidden" name="id" value="{row['id']}">
                    <button class="button danger" type="submit">Удалить</button>
                </form>
            </td>
        </tr>
        """

    body = f"""
    <div class="card">
        <h1>📰 Посты сайта</h1>
        <p class="success">{html.escape(message)}</p>
        <form method="post">
            <input type="hidden" name="action" value="add">
            <input name="title" placeholder="Заголовок поста" required>
            <textarea name="content" rows="8" placeholder="Текст поста..." required></textarea>
            <button class="button" type="submit">📢 Опубликовать</button>
        </form>
    </div>

    <div class="card">
        <table>
            <tr><th>ID</th><th>Заголовок</th><th>Статус</th><th>Дата</th><th>Действия</th></tr>
            {table}
        </table>
    </div>
    """
    return page("Посты сайта", body)


# ============================================================
# API
# ============================================================

@app.route("/api/statistics")
def api_statistics():
    return jsonify(statistics())


@app.route("/api/users")
@admin_required
def api_users():
    con = connect_db()
    rows = con.execute("""
        SELECT telegram_id, username, first_name,
               balance, is_admin, is_banned
        FROM users
        ORDER BY id DESC
    """).fetchall()
    con.close()

    return jsonify([dict(row) for row in rows])


# ============================================================
# WEBSITE SERVER
# ============================================================

def run_web():
    """Run the website with automatic restart after an unexpected error."""
    while True:
        try:
            logger.info(
                "Website starting at http://127.0.0.1:%s",
                PORT
            )
            app.run(
                host=HOST,
                port=PORT,
                debug=False,
                use_reloader=False
            )
            logger.warning("Website stopped. Restarting in 5 seconds...")
        except Exception:
            logger.exception("Website crashed. Restarting in 5 seconds...")
        time.sleep(5)


# ============================================================
# BOT SERVER
# ============================================================

async def run_bot():
    """Run Telegram polling with automatic reconnect/restart."""
    delay = 5
    while True:
        try:
            logger.info("Telegram bot starting...")
            await dp.start_polling(bot)
            logger.warning("Telegram polling stopped. Restarting in %s seconds...", delay)
        except asyncio.CancelledError:
            logger.info("Telegram bot task cancelled.")
            raise
        except Exception:
            logger.exception("Telegram bot crashed. Restarting in %s seconds...", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ============================================================
# MAIN
# ============================================================

async def main():
    db_init()

    web_thread = threading.Thread(
        target=run_web,
        daemon=True
    )

    web_thread.start()

    await run_bot()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped.")
