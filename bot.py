import asyncio
import base64
import io
import logging
import random
import aiosqlite
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
import os

# === НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "8950292427:AAHiJ26IAGA4cTwC4OAnJU3DxZUVE8Ld7xg")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Checkushhka_Bot")
DB_NAME = os.getenv("DB_NAME", "chekushka.db")

admin_raw = os.getenv("ADMIN_IDS", "7837011810")
ADMIN_IDS = [int(i.strip()) for i in admin_raw.split(",") if i.strip().isdigit()]

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "Seretio")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Checkushka")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === СИНХРОНИЗАЦИЯ С GITHUB ===
async def download_db_from_github():
    """Загрузка БД из GitHub при старте бота."""
    if not GITHUB_TOKEN:
        logging.warning("GITHUB_TOKEN не задан, синхронизация с GitHub отключена.")
        return

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{DB_NAME}?ref={GITHUB_BRANCH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                content = base64.b64decode(data["content"])
                with open(DB_NAME, "wb") as f:
                    f.write(content)
                logging.info("База данных успешно загружена из GitHub!")
            else:
                logging.info("Файл базы данных не найден на GitHub, создаём локально.")

async def upload_db_to_github():
    """Сохранение БД в GitHub."""
    if not GITHUB_TOKEN or not os.path.exists(DB_NAME):
        return

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{DB_NAME}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    sha = None
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{url}?ref={GITHUB_BRANCH}", headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                sha = data.get("sha")

        with open(DB_NAME, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "message": "Auto-sync database update",
            "content": content,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        async with session.put(url, headers=headers, json=payload) as resp:
            if resp.status in [200, 201]:
                logging.info("База данных успешно отправлена в GitHub!")
            else:
                logging.error(f"Ошибка при загрузке базы в GitHub: {await resp.text()}")

async def github_sync_task():
    """Авто-сохранение БД в GitHub каждые 10 минут."""
    while True:
        await asyncio.sleep(600)
        try:
            await upload_db_to_github()
        except Exception as e:
            logging.error(f"Ошибка фоновой синхронизации с GitHub: {e}")

# === ИНИЦИАЛИЗА БАЗЫ ДАННЫХ ===
async def init_db():[cite: 3]
    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        await db.execute([cite: 3]
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                balance INTEGER DEFAULT 0,
                referrer_id INTEGER,
                ref_count INTEGER DEFAULT 0,
                ref_balance INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0
            )
        """
        )[cite: 3]
        await db.commit()[cite: 3]

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БАЗЫ ===
async def get_or_create_user(user_id: int, first_name: str, username: str = None, referrer_id: int = None):[cite: 3]
    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        db.row_factory = aiosqlite.Row[cite: 3]
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:[cite: 3]
            user = await cursor.fetchone()[cite: 3]

        if not user:[cite: 3]
            valid_referrer = referrer_id if referrer_id and referrer_id != user_id else None[cite: 3]
            
            if valid_referrer:[cite: 3]
                async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (valid_referrer,)) as c:[cite: 3]
                    if not await c.fetchone():[cite: 3]
                        valid_referrer = None[cite: 3]

            await db.execute([cite: 3]
                """
                INSERT INTO users (user_id, first_name, username, referrer_id)
                VALUES (?, ?, ?, ?)
            """,
                (user_id, first_name, username, valid_referrer),
            )[cite: 3]
            await db.commit()[cite: 3]

            if valid_referrer:[cite: 3]
                await db.execute([cite: 3]
                    """
                    UPDATE users 
                    SET balance = balance + 1, ref_count = ref_count + 1, ref_balance = ref_balance + 1 
                    WHERE user_id = ?
                """,
                    (valid_referrer,),
                )[cite: 3]
                await db.commit()[cite: 3]
                
                try:[cite: 3]
                    await bot.send_message([cite: 3]
                        chat_id=valid_referrer,[cite: 3]
                        text="🎉 Друг перешёл по твоей ссылке! Ты получаешь 1 💎 Чекушку."[cite: 3]
                    )[cite: 3]
                except Exception:[cite: 3]
                    pass[cite: 3]

            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:[cite: 3]
                user = await cursor.fetchone()[cite: 3]

        return user[cite: 3]

async def get_user_by_id_or_username(identifier: str):[cite: 3]
    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        db.row_factory = aiosqlite.Row[cite: 3]
        identifier = identifier.replace("@", "").strip()[cite: 3]
        
        if identifier.isdigit():[cite: 3]
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (int(identifier),)) as cursor:[cite: 3]
                return await cursor.fetchone()[cite: 3]
        else:
            async with db.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (identifier,)) as cursor:[cite: 3]
                return await cursor.fetchone()[cite: 3]

async def get_user(user_id: int):[cite: 3]
    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        db.row_factory = aiosqlite.Row[cite: 3]
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:[cite: 3]
            return await cursor.fetchone()[cite: 3]

async def update_balance(user_id: int, amount: int):[cite: 3]
    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))[cite: 3]
        await db.commit()[cite: 3]

# === АВТО-ПИНГ ПОЛЬЗОВАТЕЛЕЙ (КАЖДЫЕ 5 МИНУТ) ===
async def auto_ping_task():
    """Фоновая рассылка сообщений раз в 5 минут."""
    await asyncio.sleep(10)
    while True:
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT user_id FROM users WHERE is_banned = 0") as cursor:
                    users = await cursor.fetchall()

            for user in users:
                try:
                    await bot.send_message(
                        chat_id=user["user_id"],
                        text="🔔 **Напоминание!** Заходи сыграть в «Чекушку»! Проверь свой баланс и играй! 🚀",
                        parse_mode="Markdown"
                    )
                    await asyncio.sleep(0.05)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Ошибка при авто-пинге: {e}")

        await asyncio.sleep(300)

# === КЛАВИАТУРЫ ===
def main_keyboard():[cite: 3]
    return InlineKeyboardMarkup([cite: 3]
        inline_keyboard=[[cite: 3]
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],[cite: 3]
            [InlineKeyboardButton(text="💎 Пополнить", callback_data="deposit")],[cite: 3]
            [InlineKeyboardButton(text="играть", callback_data="games")],[cite: 3]
        ]
    )[cite: 3]

def profile_keyboard():[cite: 3]
    return InlineKeyboardMarkup([cite: 3]
        inline_keyboard=[[cite: 3]
            [InlineKeyboardButton(text="💎 Пополнить", callback_data="deposit")],[cite: 3]
            [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],[cite: 3]
        ]
    )[cite: 3]

def deposit_keyboard():[cite: 3]
    return InlineKeyboardMarkup([cite: 3]
        inline_keyboard=[[cite: 3]
            [InlineKeyboardButton(text="💎 50 — 30 ⭐", callback_data="buy_50")],[cite: 3]
            [InlineKeyboardButton(text="💎 100 — 60 ⭐", callback_data="buy_100")],[cite: 3]
            [InlineKeyboardButton(text="💎 200 — 120 ⭐", callback_data="buy_200")],[cite: 3]
            [InlineKeyboardButton(text="💎 300 — 180 ⭐", callback_data="buy_300")],[cite: 3]
            [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],[cite: 3]
        ]
    )[cite: 3]

def games_keyboard():[cite: 3]
    return InlineKeyboardMarkup([cite: 3]
        inline_keyboard=[[cite: 3]
            [InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football")],[cite: 3]
            [InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_basketball")],[cite: 3]
            [InlineKeyboardButton(text="🎯 Дартс", callback_data="game_darts")],[cite: 3]
            [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],[cite: 3]
        ]
    )[cite: 3]

# === МЕНЮ И СТАРТ ===
MAIN_TEXT = "Привет! 👋 Добро пожаловать в «Чекушку»!\nВыберите действие ниже 👇"[cite: 3]

@dp.message(CommandStart())[cite: 3]
async def cmd_start(message: Message, command: CommandObject):[cite: 3]
    referrer_id = None[cite: 3]
    if command.args and command.args.isdigit():[cite: 3]
        referrer_id = int(command.args)[cite: 3]

    user = await get_or_create_user([cite: 3]
        user_id=message.from_user.id,[cite: 3]
        first_name=message.from_user.first_name,[cite: 3]
        username=message.from_user.username,[cite: 3]
        referrer_id=referrer_id,[cite: 3]
    )[cite: 3]

    if user['is_banned']:[cite: 3]
        await message.answer("❌ Вы заблокированы в боте.")[cite: 3]
        return[cite: 3]

    await message.answer(MAIN_TEXT, reply_markup=main_keyboard())[cite: 3]

@dp.callback_query(F.data == "main_menu")[cite: 3]
async def cb_main_menu(call: CallbackQuery):[cite: 3]
    await call.message.edit_text(MAIN_TEXT, reply_markup=main_keyboard())[cite: 3]
    await call.answer()[cite: 3]

# === ПРОФИЛЬ ===
@dp.callback_query(F.data == "profile")[cite: 3]
async def cb_profile(call: CallbackQuery):[cite: 3]
    user = await get_user(call.from_user.id)[cite: 3]
    text = ([cite: 3]
        "👤 Ваш профиль\n"[cite: 3]
        f"├ 👤 {user['first_name']}\n"[cite: 3]
        f"├ 🆔 ID: {user['user_id']}\n"[cite: 3]
        f"└ 💎 Чекушок: {user['balance']}"[cite: 3]
    )[cite: 3]
    await call.message.edit_text(text, reply_markup=profile_keyboard())[cite: 3]
    await call.answer()[cite: 3]

# === ПОПОЛНЕНИЕ И STARS ===
PACKAGES = {[cite: 3]
    "buy_50": {"amount": 50, "stars": 30, "title": "50 Чекушек"},[cite: 3]
    "buy_100": {"amount": 100, "stars": 60, "title": "100 Чекушек"},[cite: 3]
    "buy_200": {"amount": 200, "stars": 120, "title": "200 Чекушек"},[cite: 3]
    "buy_300": {"amount": 300, "stars": 180, "title": "300 Чекушек"},[cite: 3]
}[cite: 3]

@dp.callback_query(F.data == "deposit")[cite: 3]
async def cb_deposit(call: CallbackQuery):[cite: 3]
    text = ([cite: 3]
        "💎 Выберите количество Чекушек\n"[cite: 3]
        "├ 💎 50 Чекушек — 30 ⭐\n"[cite: 3]
        "├ 💎 100 Чекушек — 60 ⭐\n"[cite: 3]
        "├ 💎 200 Чекушек — 120 ⭐\n"[cite: 3]
        "└ 💎 300 Чекушек — 180 ⭐"[cite: 3]
    )[cite: 3]
    await call.message.edit_text(text, reply_markup=deposit_keyboard())[cite: 3]
    await call.answer()[cite: 3]

@dp.callback_query(F.data.in_(PACKAGES.keys()))[cite: 3]
async def cb_buy_package(call: CallbackQuery):[cite: 3]
    pkg = PACKAGES[call.data][cite: 3]
    
    await bot.send_invoice([cite: 3]
        chat_id=call.from_user.id,[cite: 3]
        title=pkg["title"],[cite: 3]
        description=f"Пополнение баланса на {pkg['amount']} Чекушек",[cite: 3]
        payload=f"purchase_{pkg['amount']}",[cite: 3]
        provider_token="",[cite: 3]
        currency="XTR",[cite: 3]
        prices=[LabeledPrice(label=pkg["title"], amount=pkg["stars"])],[cite: 3]
    )[cite: 3]
    await call.answer()[cite: 3]

@dp.pre_checkout_query()[cite: 3]
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):[cite: 3]
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)[cite: 3]

@dp.successful_payment()[cite: 3]
async def process_successful_payment(message: Message):[cite: 3]
    payload = message.successful_payment.invoice_payload[cite: 3]
    added_amount = int(payload.split("_")[1])[cite: 3]

    await update_balance(message.from_user.id, added_amount)[cite: 3]
    user = await get_user(message.from_user.id)[cite: 3]

    text = ([cite: 3]
        "✅ Пополнение успешно!\n"[cite: 3]
        f"💎 Вам начислено: {added_amount} Чекушек\n"[cite: 3]
        f"💎 Ваш баланс: {user['balance']} Чекушек"[cite: 3]
    )[cite: 3]
    await message.answer(text, reply_markup=main_keyboard())[cite: 3]

# === РЕФЕРАЛЬНАЯ СИСТЕМА ===
@dp.message(Command("ref"))[cite: 3]
async def cmd_ref(message: Message):[cite: 3]
    user = await get_or_create_user([cite: 3]
        user_id=message.from_user.id,[cite: 3]
        first_name=message.from_user.first_name,[cite: 3]
        username=message.from_user.username,[cite: 3]
    )[cite: 3]
    ref_link = f"https://t.me/{BOT_USERNAME}?start={message.from_user.id}"[cite: 3]
    
    text = ([cite: 3]
        "👥 Реферальная система\n\n"[cite: 3]
        "Приглашай друзей и получай Чекушки!\n"[cite: 3]
        "🎁 За каждого приглашённого друга ты получаешь 1 💎 Чекушку.\n\n"[cite: 3]
        "🔗 Твоя личная ссылка:\n"[cite: 3]
        f"{ref_link}\n\n"[cite: 3]
        f"👥 Приглашено: {user['ref_count']}\n"[cite: 3]
        f"💎 Получено: {user['ref_balance']}\n\n"[cite: 3]
        "Отправь свою ссылку друзьям и получай Чекушки! 🚀"[cite: 3]
    )[cite: 3]
    await message.answer(text)[cite: 3]

# === ФАЙЛ С ПОЛЬЗОВАТЕЛЯМИ (ВЫГРУЗКА И СИНХРОНИЗАЦИЯ ВРУЧНУЮ) ===
@dp.message(Command("export"))[cite: 3]
async def cmd_export(message: Message):[cite: 3]
    if message.from_user.id not in ADMIN_IDS:[cite: 3]
        return[cite: 3]

    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        db.row_factory = aiosqlite.Row[cite: 3]
        async with db.execute("SELECT * FROM users") as cursor:[cite: 3]
            users = await cursor.fetchall()[cite: 3]

    file_content = "ID | USERNAME | FIRST_NAME | BALANCE | REF_COUNT | REFERRER_ID\n"[cite: 3]
    file_content += "=" * 65 + "\n"[cite: 3]
    for u in users:[cite: 3]
        un = f"@{u['username']}" if u['username'] else "None"[cite: 3]
        file_content += f"{u['user_id']} | {un} | {u['first_name']} | {u['balance']} | {u['ref_count']} | {u['referrer_id']}\n"[cite: 3]

    input_file = BufferedInputFile(file_content.encode("utf-8"), filename="users_data.txt")[cite: 3]
    await message.answer_document(input_file, caption="📄 Полный список пользователей бота.")[cite: 3]

@dp.message(Command("sync"))
async def cmd_sync(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await upload_db_to_github()
    await message.answer("🔄 База данных вручную сохранена в GitHub!")

# === АДМИН-КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ БАЛАНСОМ ===
@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text)[cite: 3]
async def process_admin_text_commands(message: Message):[cite: 3]
    text = message.text.strip()[cite: 3]
    parts = text.split()[cite: 3]

    action = None[cite: 3]
    amount = 0[cite: 3]
    target_str = None[cite: 3]

    if len(parts) >= 3 and parts[0].lower() in ["чекушка", "выдать", "+"]:[cite: 3]
        action = "add"[cite: 3]
        if parts[1].isdigit():[cite: 3]
            amount = int(parts[1])[cite: 3]
            target_str = parts[2][cite: 3]
    elif len(parts) >= 3 and parts[0].lower() in ["забрать", "снять", "-"]:[cite: 3]
        action = "sub"[cite: 3]
        if parts[1].isdigit():[cite: 3]
            amount = int(parts[1])[cite: 3]
            target_str = parts[2][cite: 3]
    
    if message.reply_to_message and len(parts) >= 2:[cite: 3]
        if parts[0].lower() in ["чекушка", "выдать", "+"] and parts[1].isdigit():[cite: 3]
            action = "add"[cite: 3]
            amount = int(parts[1])[cite: 3]
            target_str = str(message.reply_to_message.from_user.id)[cite: 3]
        elif parts[0].lower() in ["забрать", "снять", "-"] and parts[1].isdigit():[cite: 3]
            action = "sub"[cite: 3]
            amount = int(parts[1])[cite: 3]
            target_str = str(message.reply_to_message.from_user.id)[cite: 3]

    if action and amount > 0 and target_str:[cite: 3]
        target_user = await get_user_by_id_or_username(target_str)[cite: 3]
        if not target_user:[cite: 3]
            await message.answer("❌ Пользователь не найден в базе бота.")[cite: 3]
            return[cite: 3]

        if action == "add":[cite: 3]
            await update_balance(target_user['user_id'], amount)[cite: 3]
            new_user = await get_user(target_user['user_id'])[cite: 3]
            await message.answer(f"✅ Выдали {amount} 💎 Чекушек пользователю {target_user['first_name']}.")[cite: 3]
            try:[cite: 3]
                await bot.send_message([cite: 3]
                    chat_id=target_user['user_id'],[cite: 3]
                    text=f"🎁 Вам выдано {amount} 💎 Чекушек администратором!\nВаш баланс: {new_user['balance']} 💎"[cite: 3]
                )[cite: 3]
            except Exception:[cite: 3]
                pass[cite: 3]

        elif action == "sub":[cite: 3]
            await update_balance(target_user['user_id'], -amount)[cite: 3]
            new_user = await get_user(target_user['user_id'])[cite: 3]
            await message.answer(f"⚠️ Забрали {amount} 💎 Чекушек у пользователя {target_user['first_name']}.")[cite: 3]
            try:[cite: 3]
                await bot.send_message([cite: 3]
                    chat_id=target_user['user_id'],[cite: 3]
                    text=f"🔻 У вас забрали {amount} 💎 Чекушек.\nВаш баланс: {new_user['balance']} 💎"[cite: 3]
                )[cite: 3]
            except Exception:[cite: 3]
                pass[cite: 3]
        return[cite: 3]

    await process_game_bet(message)[cite: 3]

# === ВСЕ АДМИНСКИЕ КОМАНДЫ ===
@dp.message(Command("admin"))[cite: 3]
async def cmd_admin(message: Message):[cite: 3]
    if message.from_user.id not in ADMIN_IDS:[cite: 3]
        return[cite: 3]
    text = ([cite: 3]
        "🛠 **Панель Администратора**\n\n"[cite: 3]
        "**Управление балансом:**\n"[cite: 3]
        "• `Чекушка 50 @username` — выдать 50 Чекушек\n"[cite: 3]
        "• `Забрать 20 @username` — забрать 20 Чекушек\n\n"[cite: 3]
        "**Команды админа:**\n"[cite: 3]
        "• `/export` — Скачать файл с пользователями\n"[cite: 3]
        "• `/sync` — Синхронизировать БД с GitHub вручную\n"[cite: 3]
        "• `/stats` — Общая статистика\n"[cite: 3]
        "• `/ban [ID/@username]` — Заблокировать\n"[cite: 3]
        "• `/unban [ID/@username]` — Разблокировать\n"[cite: 3]
        "• `/broadcast [текст]` — Рассылка"[cite: 3]
    )[cite: 3]
    await message.answer(text, parse_mode="Markdown")[cite: 3]

@dp.message(Command("stats"))[cite: 3]
async def cmd_stats(message: Message):[cite: 3]
    if message.from_user.id not in ADMIN_IDS:[cite: 3]
        return[cite: 3]
    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        async with db.execute("SELECT COUNT(*), SUM(balance) FROM users") as cursor:[cite: 3]
            row = await cursor.fetchone()[cite: 3]
            count = row[0] or 0[cite: 3]
            total_balance = row[1] or 0[cite: 3]

    await message.answer(f"📊 **Статистика:**\n\n👥 Пользователей: {count}\n💎 Всего Чекушек в системе: {total_balance}")[cite: 3]

@dp.message(Command("ban"))[cite: 3]
async def cmd_ban(message: Message, command: CommandObject):[cite: 3]
    if message.from_user.id not in ADMIN_IDS or not command.args:[cite: 3]
        return[cite: 3]
    user = await get_user_by_id_or_username(command.args)[cite: 3]
    if user:[cite: 3]
        async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
            await db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user['user_id'],))[cite: 3]
            await db.commit()[cite: 3]
        await message.answer(f"⛔ Пользователь {user['first_name']} заблокирован.")[cite: 3]

@dp.message(Command("unban"))[cite: 3]
async def cmd_unban(message: Message, command: CommandObject):[cite: 3]
    if message.from_user.id not in ADMIN_IDS or not command.args:[cite: 3]
        return[cite: 3]
    user = await get_user_by_id_or_username(command.args)[cite: 3]
    if user:[cite: 3]
        async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
            await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user['user_id'],))[cite: 3]
            await db.commit()[cite: 3]
        await message.answer(f"✅ Пользователь {user['first_name']} разблокирован.")[cite: 3]

@dp.message(Command("broadcast"))[cite: 3]
async def cmd_broadcast(message: Message, command: CommandObject):[cite: 3]
    if message.from_user.id not in ADMIN_IDS or not command.args:[cite: 3]
        return[cite: 3]
    
    async with aiosqlite.connect(DB_NAME) as db:[cite: 3]
        db.row_factory = aiosqlite.Row[cite: 3]
        async with db.execute("SELECT user_id FROM users") as cursor:[cite: 3]
            users = await cursor.fetchall()[cite: 3]

    success, failed = 0, 0[cite: 3]
    for u in users:[cite: 3]
        try:[cite: 3]
            await bot.send_message(chat_id=u['user_id'], text=command.args)[cite: 3]
            success += 1[cite: 3]
            await asyncio.sleep(0.05)[cite: 3]
        except Exception:[cite: 3]
            failed += 1[cite: 3]

    await message.answer(f"📢 **Рассылка завершена:**\n✅ Успешно: {success}\n❌ Не доставлено: {failed}")[cite: 3]

# === ИГРЫ (ФУТБОЛ, БАСКЕТБОЛ, ДАРТС) ===
@dp.callback_query(F.data == "games")[cite: 3]
async def cb_games(call: CallbackQuery):[cite: 3]
    text = ([cite: 3]
        "🎮 Выберите игру ниже:\n\n"[cite: 3]
        "Чтобы сделать ставку, отправьте команду с игрой и суммой ставки:\n"[cite: 3]
        "• `баскетбол 5`\n"[cite: 3]
        "• `футбол 1`\n"[cite: 3]
        "• `дартс 500`"[cite: 3]
    )[cite: 3]
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=games_keyboard())[cite: 3]
    await call.answer()[cite: 3]

@dp.callback_query(F.data.startswith("game_"))[cite: 3]
async def cb_game_info(call: CallbackQuery):[cite: 3]
    game_type = call.data.split("_")[1][cite: 3]
    names = {"football": "футбол", "basketball": "баскетбол", "darts": "дартс"}[cite: 3]
    name = names.get(game_type, "игру")[cite: 3]
    await call.message.answer(f"Чтобы сыграть, напишите в чат: `{name} [ставка]`\nНапример: `{name} 10`", parse_mode="Markdown")[cite: 3]
    await call.answer()[cite: 3]

async def process_game_bet(message: Message):[cite: 3]
    parts = message.text.strip().lower().split()[cite: 3]
    if len(parts) != 2:[cite: 3]
        return[cite: 3]

    game_name, bet_str = parts[0], parts[1][cite: 3]
    game_map = {"футбол": ("⚽", "football"), "баскетбол": ("🏀", "basketball"), "дартс": ("🎯", "darts")}[cite: 3]

    if game_name not in game_map or not bet_str.isdigit():[cite: 3]
        return[cite: 3]

    bet = int(bet_str)[cite: 3]
    if bet <= 0:[cite: 3]
        await message.answer("Ставка должна быть больше 0!")[cite: 3]
        return[cite: 3]

    user = await get_or_create_user([cite: 3]
        user_id=message.from_user.id,[cite: 3]
        first_name=message.from_user.first_name,[cite: 3]
        username=message.from_user.username[cite: 3]
    )[cite: 3]

    if user['is_banned']:[cite: 3]
        await message.answer("❌ Вы заблокированы.")[cite: 3]
        return[cite: 3]

    if user["balance"] < bet:[cite: 3]
        await message.answer(f"❌ Недостаточно Чекушек! Ваш баланс: {user['balance']} 💎")[cite: 3]
        return[cite: 3]

    await update_balance(message.from_user.id, -bet)[cite: 3]
    
    emoji, game_code = game_map[game_name][cite: 3]
    dice_msg = await message.answer_dice(emoji=emoji)[cite: 3]
    val = dice_msg.dice.value[cite: 3]
    await asyncio.sleep(2.5)[cite: 3]

    is_win = False[cite: 3]
    if game_code == "football" and val in [3, 4, 5]:[cite: 3]
        is_win = True[cite: 3]
    elif game_code == "basketball" and val in [4, 5]:[cite: 3]
        is_win = True[cite: 3]
    elif game_code == "darts" and val == 6:[cite: 3]
        is_win = True[cite: 3]

    if is_win:[cite: 3]
        if bet < 10:[cite: 3]
            win_amount = int(bet * random.uniform(1.5, 3.0))[cite: 3]
        elif bet < 100:[cite: 3]
            win_amount = int(bet * random.uniform(1.2, 1.8))[cite: 3]
        else:
            win_amount = int(bet * random.uniform(0.4, 0.6))[cite: 3]
            
        win_amount = max(1, win_amount)[cite: 3]
        await update_balance(message.from_user.id, win_amount)[cite: 3]
        new_user = await get_user(message.from_user.id)[cite: 3]
        
        await message.answer([cite: 3]
            f"🎉 **ПОБЕДА!**\n"[cite: 3]
            f"Вам начислено: +{win_amount} 💎 Чекушек!\n"[cite: 3]
            f"Ваш баланс: {new_user['balance']} 💎",[cite: 3]
            parse_mode="Markdown"[cite: 3]
        )[cite: 3]
    else:
        new_user = await get_user(message.from_user.id)[cite: 3]
        await message.answer([cite: 3]
            f"❌ **ПРОИГРЫШ!**\n"[cite: 3]
            f"Вы потеряли: {bet} 💎 Чекушек.\n"[cite: 3]
            f"Ваш баланс: {new_user['balance']} 💎",[cite: 3]
            parse_mode="Markdown"[cite: 3]
        )[cite: 3]

# === ЗАПУСК ===
async def main():
    await download_db_from_github()  # Загружаем последнюю версию БД с GitHub
    await init_db()[cite: 3]
    
    asyncio.create_task(auto_ping_task())     # Авто-пинг каждые 5 минут
    asyncio.create_task(github_sync_task())   # Авто-сохранение в GitHub каждые 10 минут
    
    print("Бот запущен с синхронизацией GitHub!")
    await dp.start_polling(bot)[cite: 3]

if __name__ == "__main__":
    asyncio.run(main())[cite: 3]
