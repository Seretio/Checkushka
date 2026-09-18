import asyncio
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import aiohttp
import asyncpg
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)

# =====================================================================
# CONFIGURATION & CONSTANTS
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8950292427:AAHiJ26IAGA4cTwC4OAnJU3DxZUVE8Ld7xg")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Checkushhka_Bot")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 8080))

admin_raw = os.getenv("ADMIN_IDS", "7837011810")
ADMIN_IDS = [int(i.strip()) for i in admin_raw.split(",") if i.strip().isdigit()]
if not ADMIN_IDS:
    ADMIN_IDS = [7837011810]

PACKAGES = {
    "buy_50": {"amount": 50, "stars": 30, "title": "50 Чекушек"},
    "buy_100": {"amount": 100, "stars": 60, "title": "100 Чекушек"},
    "buy_200": {"amount": 200, "stars": 120, "title": "200 Чекушек"},
    "buy_300": {"amount": 300, "stars": 180, "title": "300 Чекушек"},
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None

# =====================================================================
# DATABASE MANAGEMENT
# =====================================================================
async def init_db():
    global db_pool
    dsn = DATABASE_URL
    if dsn and dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)

    db_pool = await asyncpg.create_pool(dsn=dsn)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                balance INT DEFAULT 100,
                bottles INT DEFAULT 0,
                referrer_id BIGINT,
                ref_count INT DEFAULT 0,
                ref_balance INT DEFAULT 0,
                is_banned INT DEFAULT 0,
                last_bonus_claim TIMESTAMP WITH TIME ZONE
            );
            CREATE TABLE IF NOT EXISTS checks (
                check_id TEXT PRIMARY KEY,
                creator_id BIGINT,
                amount INT,
                is_activated INT DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                reward INT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS promo_activations (
                code TEXT REFERENCES promo_codes(code) ON DELETE CASCADE,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                activated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (code, user_id)
            );
        """)
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS bottles INT DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus_claim TIMESTAMP WITH TIME ZONE;
        """)
    logging.info("База данных PostgreSQL успешно инициализирована с нуля.")

async def get_or_create_user(user_id: int, first_name: str, username: str = None, referrer_id: int = None):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        is_new = False

        if not user:
            is_new = True
            valid_ref = referrer_id if referrer_id and referrer_id != user_id else None
            if valid_ref:
                exists = await conn.fetchval("SELECT user_id FROM users WHERE user_id = $1", valid_ref)
                if not exists:
                    valid_ref = None

            await conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance, bottles, referrer_id) VALUES ($1, $2, $3, 100, 0, $4)",
                user_id, first_name, username, valid_ref
            )

            if valid_ref:
                await conn.execute(
                    "UPDATE users SET balance = balance + 1, ref_count = ref_count + 1, ref_balance = ref_balance + 1 WHERE user_id = $1",
                    valid_ref
                )
                try:
                    await bot.send_message(
                        chat_id=valid_ref,
                        text=f"👤 Пользователь **{first_name}** перешел по твоей реферальной ссылке! Начислена 1 💎 Чекушка.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
    return user, is_new

async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def get_user_by_id_or_username(identifier: str):
    async with db_pool.acquire() as conn:
        clean_id = identifier.replace("@", "").strip()
        if clean_id.isdigit():
            return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", int(clean_id))
        return await conn.fetchrow("SELECT * FROM users WHERE LOWER(username) = LOWER($1)", clean_id)

async def update_balance(user_id: int, amount: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", amount, user_id)

async def update_bottles(user_id: int, amount: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET bottles = bottles + $1 WHERE user_id = $2", amount, user_id)

async def update_bonus_claim_time(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET last_bonus_claim = CURRENT_TIMESTAMP WHERE user_id = $1", user_id)

async def create_check_db(creator_id: int, amount: int) -> str:
    check_id = str(uuid.uuid4())[:8]
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO checks (check_id, creator_id, amount) VALUES ($1, $2, $3)", check_id, creator_id, amount)
    return check_id

async def get_check_db(check_id: str):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM checks WHERE check_id = $1", check_id)

async def activate_check_db(check_id: str):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE checks SET is_activated = 1 WHERE check_id = $1", check_id)

async def create_promo_code_db(code: str, reward: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO promo_codes (code, reward) VALUES ($1, $2) ON CONFLICT (code) DO UPDATE SET reward = EXCLUDED.reward",
            code.upper(), reward
        )

async def claim_promo_code_db(code: str, user_id: int):
    code_clean = code.upper()
    async with db_pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promo_codes WHERE code = $1", code_clean)
        if not promo:
            return "NOT_FOUND", 0
        already = await conn.fetchval("SELECT 1 FROM promo_activations WHERE code = $1 AND user_id = $2", code_clean, user_id)
        if already:
            return "ALREADY_USED", 0
        await conn.execute("INSERT INTO promo_activations (code, user_id) VALUES ($1, $2)", code_clean, user_id)
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", promo["reward"], user_id)
        return "SUCCESS", promo["reward"]

# =====================================================================
# KEYBOARDS
# =====================================================================
def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎮 Играть")],
            [KeyboardButton(text="🛒 Магазин"), KeyboardButton(text="🔗 Рефералка")],
            [KeyboardButton(text="🎁 Бонус")]
        ],
        resize_keyboard=True
    )

def profile_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Пополнить", callback_data="deposit")]])

def shop_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🍾 Купить бутылку", callback_data="buy_bottle")]])

def deposit_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 50 — 30 ⭐", callback_data="buy_50")],
            [InlineKeyboardButton(text="💎 100 — 60 ⭐", callback_data="buy_100")],
            [InlineKeyboardButton(text="💎 200 — 120 ⭐", callback_data="buy_200")],
            [InlineKeyboardButton(text="💎 300 — 180 ⭐", callback_data="buy_300")],
        ]
    )

def games_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football"), InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_basketball")],
            [InlineKeyboardButton(text="🎯 Дартс", callback_data="game_darts"), InlineKeyboardButton(text="🎳 Боулинг", callback_data="game_bowling")]
        ]
    )

def bonus_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Получить бонус", callback_data="claim_daily_bonus")]])

# =====================================================================
# BOT HANDLERS & LOGIC
# =====================================================================
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    args = command.args.strip() if command.args else None

    if args and args.startswith("check_"):
        check_id = args.replace("check_", "")
        check = await get_check_db(check_id)
        if not check:
            await message.answer("❌ Чек не найден или недействителен.")
            return
        if check["is_activated"]:
            await message.answer("⚠️ Этот чек уже был кем-то активирован.")
            return

        receiver, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
        if receiver["is_banned"]:
            await message.answer("❌ Вы заблокированы в боте.")
            return

        await activate_check_db(check_id)
        await update_balance(receiver["user_id"], check["amount"])
        await message.answer(f"🎉 Вы активировали чек на **{check['amount']}** 💎 Чекушек!", parse_mode="Markdown")

        try:
            await bot.send_message(
                chat_id=check["creator_id"],
                text=f"🔔 Пользователь {receiver['first_name']} (@{receiver['username'] or 'без юзернейма'}) активировал ваш чек на **{check['amount']}** 💎 Чекушек!",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        return

    ref_id = int(args) if args and args.isdigit() else None
    user, is_new = await get_or_create_user(
        user_id=message.from_user.id,
        first_name=message.from_user.first_name,
        username=message.from_user.username,
        referrer_id=ref_id
    )

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    text = "Привет! 👋 Добро пожаловать в «Чекушку»!\n"
    text += "Вам начислено 100 💎 Чекушек на старт!\nВыберите действие ниже 👇" if is_new else "Выберите действие ниже 👇"
    await message.answer(text, reply_markup=main_reply_keyboard())

@dp.message(F.text.lower().in_(["бонус", "🎁 бонус"]))
async def msg_bonus(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    text = (
        "🎁 **Ежедневный бонус**\n\n"
        "Вы можете получать от **10 до 60** 💎 Чекушек каждые 24 часа!\n\n"
        "📌 **Условие:** Укажите в своем описании (био) Telegram фразу:\n"
        "`Самая первая Чекушка @Checkushhka_Bot`"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=bonus_inline_keyboard())

@dp.callback_query(F.data == "claim_daily_bonus")
async def cb_claim_bonus(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    if not user:
        user, _ = await get_or_create_user(call.from_user.id, call.from_user.first_name, call.from_user.username)

    if user['is_banned']:
        await call.answer("❌ Вы заблокированы.", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    if user["last_bonus_claim"]:
        last_claim = user["last_bonus_claim"]
        if last_claim.tzinfo is None:
            last_claim = last_claim.replace(tzinfo=timezone.utc)
        next_claim = last_claim + timedelta(hours=24)
        if now < next_claim:
            diff = next_claim - now
            h, rem = divmod(int(diff.total_seconds()), 3600)
            m, _ = divmod(rem, 60)
            await call.answer(f"⏳ Бонус уже получен! Доступен через {h} ч. {m} мин.", show_alert=True)
            return

    try:
        chat = await bot.get_chat(call.from_user.id)
        bio = chat.bio or ""
    except Exception:
        bio = ""

    req_phrase = "Самая первая Чекушка @Checkushhka_Bot"
    if req_phrase.lower() not in bio.lower():
        await call.message.answer(f"❌ У тебя не установлена нужная фраза в био!\nДобавь: `{req_phrase}`", parse_mode="Markdown")
        await call.answer()
        return

    reward = random.randint(10, 60)
    await update_balance(call.from_user.id, reward)
    await update_bonus_claim_time(call.from_user.id)
    await call.message.answer(f"🎉 Вы получили бонус **+{reward}** 💎 Чекушек!\nВозвращайтесь через 24 часа!", parse_mode="Markdown")
    await call.answer()

@dp.message(F.text.lower().startswith(("промо ", "промокод ")))
async def process_promo_code(message: Message):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Введите промокод! Пример: `промо СТАРТ`", parse_mode="Markdown")
        return

    user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
    if user["is_banned"]:
        await message.answer("❌ Вы заблокированы.")
        return

    status, reward = await claim_promo_code_db(parts[1].strip(), message.from_user.id)
    if status == "NOT_FOUND":
        await message.answer("❌ Такого промокода не существует или он недействителен.")
    elif status == "ALREADY_USED":
        await message.answer("⚠️ Вы уже активировали этот промокод!")
    elif status == "SUCCESS":
        await message.answer(f"🎉 **Промокод успешно активирован!**\nВам начислено **+{reward}** 💎 Чекушек!", parse_mode="Markdown")

@dp.message(Command("shop", "магазин"))
@dp.message(F.text.lower().in_(["магазин", "🛒 магазин"]))
async def msg_shop(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    text = "🛒 **МАГАЗИН**\n\n🍾 **Бутылка** — 5 Чекушек\nОдноразовый предмет."
    await message.answer(text, parse_mode="Markdown", reply_markup=shop_inline_keyboard())

@dp.callback_query(F.data == "buy_bottle")
async def cb_buy_bottle(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    if not user:
        user, _ = await get_or_create_user(call.from_user.id, call.from_user.first_name, call.from_user.username)

    if user['is_banned']:
        await call.answer("❌ Вы заблокированы.", show_alert=True)
        return

    if user["balance"] < 5:
        await call.answer("❌ Недостаточно Чекушек! Бутылка стоит 5 Чекушек.", show_alert=True)
        return

    await update_balance(call.from_user.id, -5)
    await update_bottles(call.from_user.id, 1)
    await call.message.answer("✅ Ты купил 🍾 бутылку за 5 Чекушек!")
    await call.answer()

@dp.message(F.text.lower().in_(["рефералка", "🔗 рефералка", "рефералы"]))
async def msg_referral(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    ref_link = f"https://t.me/{BOT_USERNAME}?start={user['user_id']}"
    text = (
        "🔗 **Реферальная программа**\n\n"
        "Приглашайте друзей и получайте **1 💎 Чекушку** за каждого зашедшего пользователя!\n\n"
        f"👥 Приглашено рефералов: **{user['ref_count']}**\n"
        f"💰 Заработано с рефералов: **{user['ref_balance']}** 💎\n\n"
        f"Ваша реферальная ссылка:\n`{ref_link}`"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text.lower().startswith(("дать ", "перевести ", "перевод ")))
async def process_transfer(message: Message):
    parts = message.text.strip().split()
    sender, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
    
    if sender["is_banned"]:
        await message.answer("❌ Вы заблокированы.")
        return

    amount, target_str = 0, None
    if message.reply_to_message and len(parts) >= 2 and parts[1].isdigit():
        amount = int(parts[1])
        target_str = str(message.reply_to_message.from_user.id)
    elif len(parts) >= 3 and parts[1].isdigit():
        amount = int(parts[1])
        target_str = parts[2]

    if amount <= 0 or not target_str:
        await message.answer("❌ Использование: `дать [сумма] [@username или ID]` или ответом на сообщение: `дать [сумма]`", parse_mode="Markdown")
        return

    if sender["balance"] < amount:
        await message.answer(f"❌ Недостаточно Чекушек! Ваш баланс: {sender['balance']} 💎")
        return

    target_user = await get_user_by_id_or_username(target_str)
    if not target_user:
        await message.answer("❌ Пользователь не найден в базе бота.")
        return

    if target_user["user_id"] == sender["user_id"]:
        await message.answer("❌ Нельзя переводить Чекушки самому себе!")
        return

    await update_balance(sender["user_id"], -amount)
    await update_balance(target_user["user_id"], amount)
    await message.answer(f"✅ Вы успешно перевели {amount} 💎 Чекушек пользователю {target_user['first_name']}!")

@dp.message(F.text.lower().startswith(("чек ", "создать чек ")))
async def process_create_check(message: Message):
    parts = message.text.strip().split()
    sender, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if sender["is_banned"]:
        await message.answer("❌ Вы заблокированы.")
        return

    amount = next((int(p) for p in parts if p.isdigit()), 0)
    if amount <= 0:
        await message.answer("❌ Использование: `чек [сумма]` (например: `чек 50`)", parse_mode="Markdown")
        return

    if sender["balance"] < amount:
        await message.answer(f"❌ Недостаточно Чекушек для создания чека! Ваш баланс: {sender['balance']} 💎")
        return

    await update_balance(sender["user_id"], -amount)
    check_id = await create_check_db(sender["user_id"], amount)
    check_link = f"https://t.me/{BOT_USERNAME}?start=check_{check_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Забрать чек", url=check_link)]])

    await message.answer(
        f"💳 **Создан чек на {amount} 💎 Чекушек!**\n\nАктивировать чек может любой пользователь по кнопке ниже 👇",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.message(F.text.lower().in_(["чекушка", "профиль", "👤 профиль"]))
async def msg_profile(message: Message):
    if message.reply_to_message and message.text.lower().strip() == "чекушка":
        attacker, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
        if attacker['is_banned']:
            await message.answer("❌ Вы заблокированы в боте.")
            return

        if attacker.get("bottles", 0) <= 0:
            await message.answer("❌ У вас нет 🍾 бутылки! Купите ее в магазине `/shop` за 5 Чекушек.")
            return

        victim = message.reply_to_message.from_user
        if victim.id == attacker["user_id"]:
            await message.answer("❌ Нельзя ударить самого себя!")
            return

        await update_bottles(attacker["user_id"], -1)
        await message.answer(f"🍾 {attacker['first_name']} ударил {victim.first_name} бутылкой!")
        return

    user = await get_user(message.from_user.id)
    if not user:
        user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    text = (
        "👤 **Ваш профиль**\n"
        f"├ 👤 {user['first_name']}\n"
        f"├ 🆔 ID: `{user['user_id']}`\n"
        f"├ 💎 Чекушок: {user['balance']}\n"
        f"└ 🍾 Бутылок: {user.get('bottles', 0)}"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=profile_inline_keyboard())

@dp.message(F.text.lower().in_(["играть", "🎮 играть"]))
async def msg_games(message: Message):
    user = await get_user(message.from_user.id)
    if user and user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    text = (
        "🎮 Выберите игру ниже:\n\n"
        "Чтобы сделать ставку, отправьте команду с игрой и суммой ставки:\n"
        "• `баскетбол 5` или `баскетбол ставка 5`\n"
        "• `футбол 1` или `футбол ставка 1`\n"
        "• `дартс 500` или `дартс ставка 500`\n"
        "• `боулинг 10` или `боулинг ставка 10`\n"
        "• `кубик 10` или `кости 10`"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=games_keyboard())

@dp.message(F.text.lower() == "пополнить")
async def msg_deposit(message: Message):
    text = (
        "💎 Выберите количество Чекушек\n"
        "├ 💎 50 Чекушек — 30 ⭐\n"
        "├ 💎 100 Чекушек — 60 ⭐\n"
        "├ 💎 200 Чекушек — 120 ⭐\n"
        "└ 💎 300 Чекушек — 180 ⭐"
    )
    await message.answer(text, reply_markup=deposit_keyboard())

@dp.callback_query(F.data == "deposit")
async def cb_deposit(call: CallbackQuery):
    text = (
        "💎 Выберите количество Чекушек\n"
        "├ 💎 50 Чекушек — 30 ⭐\n"
        "├ 💎 100 Чекушек — 60 ⭐\n"
        "├ 💎 200 Чекушек — 120 ⭐\n"
        "└ 💎 300 Чекушек — 180 ⭐"
    )
    await call.message.edit_text(text, reply_markup=deposit_keyboard())
    await call.answer()

@dp.callback_query(F.data.in_(PACKAGES.keys()))
async def cb_buy_package(call: CallbackQuery):
    pkg = PACKAGES[call.data]
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=pkg["title"],
        description=f"Пополнение баланса на {pkg['amount']} Чекушек",
        payload=f"purchase_{pkg['amount']}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=pkg["title"], amount=pkg["stars"])],
    )
    await call.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    added = int(message.successful_payment.invoice_payload.split("_")[1])
    await update_balance(message.from_user.id, added)
    user = await get_user(message.from_user.id)
    await message.answer(f"✅ Пополнение успешно!\n💎 Баланс: {user['balance']} Чекушек", reply_markup=main_reply_keyboard())

# =====================================================================
# ADMIN PANEL LOGIC
# =====================================================================
@dp.message(F.from_user.id.in_(ADMIN_IDS) & Command("addpromo"))
async def cmd_add_promo(message: Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("❌ Использование: `/addpromo КОД СУММА`\nПример: `/addpromo Чекушка 500`", parse_mode="Markdown")
        return
    code, reward = args[0].strip(), int(args[1])
    await create_promo_code_db(code, reward)
    await message.answer(f"🎟️ **Промокод создан!**\nКод: `{code.upper()}`\nНаграда: **{reward}** 💎 Чекушек", parse_mode="Markdown")

@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text.lower().startswith(("промокод ", "создатьпромо ", "создать промо ")))
async def process_admin_create_promo_text(message: Message):
    parts = message.text.strip().split()
    if len(parts) >= 3 and parts[-1].isdigit():
        code, reward = parts[1].strip(), int(parts[-1])
        await create_promo_code_db(code, reward)
        await message.answer(f"🎟️ **Промокод создан!**\nКод: `{code.upper()}`\nНаграда: **{reward}** 💎 Чекушек", parse_mode="Markdown")

@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text.lower().startswith(("чекушка ", "выдать ", "+", "забрать ", "снять ", "-")))
async def process_admin_text_commands(message: Message):
    parts = message.text.strip().split()
    action = "add" if parts[0].lower() in ["чекушка", "выдать", "+"] else "sub"
    amount, target_str = 0, None

    if message.reply_to_message:
        target_str = str(message.reply_to_message.from_user.id)
        amount = next((int(p) for p in parts[1:] if p.isdigit()), 0)
    else:
        for part in parts[1:]:
            if part.isdigit():
                amount = int(part)
            else:
                target_str = part
        if not target_str:
            target_str = str(message.from_user.id)

    if amount <= 0:
        return

    target_user = await get_user_by_id_or_username(target_str)
    if not target_user:
        await message.answer("❌ Пользователь не найден в базе бота.")
        return

    if action == "add":
        await update_balance(target_user['user_id'], amount)
        await message.answer(f"✅ Выдали {amount} 💎 Чекушек пользователю {target_user['first_name']}.")
    else:
        await update_balance(target_user['user_id'], -amount)
        await message.answer(f"⚠️ Забрали {amount} 💎 Чекушек у пользователя {target_user['first_name']}.")

# =====================================================================
# GAME ENGINE & BETS
# =====================================================================
@dp.callback_query(F.data.startswith("game_"))
async def cb_game_info(call: CallbackQuery):
    g_type = call.data.split("_")[1]
    names = {"football": "футбол", "basketball": "баскетбол", "darts": "дартс", "bowling": "боулинг", "dice": "кубик"}
    await call.message.answer(f"Чтобы сыграть, напишите в чат: `{names.get(g_type, 'игру')} [ставка]`\nНапример: `{names.get(g_type, 'игру')} 10`", parse_mode="Markdown")
    await call.answer()

@dp.message(F.text)
async def process_game_bet(message: Message):
    if not message.text:
        return

    raw_text = message.text.replace(f"@{BOT_USERNAME}", "").strip().lower()
    parts = raw_text.split()

    game_map = {
        "футбол": ("⚽", "football"), 
        "баскетбол": ("🏀", "basketball"), 
        "дартс": ("🎯", "darts"),
        "боулинг": ("🎳", "bowling"),
        "кубик": ("🎲", "dice"),
        "кости": ("🎲", "dice")
    }

    found_game = next((k for k in game_map.keys() if k in parts), None)
    if not found_game:
        return

    bet = next((int(p) for p in parts if p.isdigit()), None)
    if bet is None:
        await message.answer(f"❌ Укажите сумму ставки цифрами!\nПример: `{found_game} 10` или `{found_game} ставка 10`", parse_mode="Markdown")
        return

    if bet <= 0:
        await message.answer("❌ Ставка должна быть больше 0!")
        return

    user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)
    if user['is_banned']:
        await message.answer("❌ Вы заблокированы.")
        return

    if user["balance"] < bet:
        await message.answer(f"❌ Недостаточно Чекушек! Ваш баланс: {user['balance']} 💎")
        return

    await update_balance(message.from_user.id, -bet)
    emoji, game_code = game_map[found_game]
    
    try:
        dice_msg = await message.answer_dice(emoji=emoji)
        val = dice_msg.dice.value
    except Exception as e:
        await update_balance(message.from_user.id, bet)
        logging.error(f"Ошибка отправки dice: {e}")
        await message.answer("❌ Ошибка отправки анимации. Ставка возвращена!")
        return

    await asyncio.sleep(2.5)

    if game_code == "bowling":
        if val == 6:
            coeff = random.uniform(2.5, 3.5) if bet < 10 else (random.uniform(2.0, 2.5) if bet < 100 else random.uniform(1.6, 2.0))
            payout = int(bet * coeff)
            await update_balance(message.from_user.id, payout)
            new_user = await get_user(message.from_user.id)
            await message.answer(f"🎉 **СТРАЙК! Сбиты абсолютно все кегли!**\nВыигрыш: +{payout} 💎 Чекушек!\nВаш баланс: {new_user['balance']} 💎", parse_mode="Markdown")
        elif val == 5:
            coeff = random.uniform(1.8, 2.2) if bet < 10 else (random.uniform(1.5, 1.8) if bet < 100 else random.uniform(1.3, 1.5))
            payout = int(bet * coeff)
            await update_balance(message.from_user.id, payout)
            new_user = await get_user(message.from_user.id)
            await message.answer(f"🔥 **ПОЧТИ СТРАЙК!**\nВыигрыш: +{payout} 💎 Чекушек!\nВаш баланс: {new_user['balance']} 💎", parse_mode="Markdown")
        elif val == 4:
            coeff = random.uniform(1.4, 1.6) if bet < 10 else (random.uniform(1.2, 1.4) if bet < 100 else random.uniform(1.1, 1.25))
            payout = int(bet * coeff)
            await update_balance(message.from_user.id, payout)
            new_user = await get_user(message.from_user.id)
            await message.answer(f"👍 **ХОРОШИЙ БРОСОК!**\nВыигрыш: +{payout} 💎 Чекушек!\nВаш баланс: {new_user['balance']} 💎", parse_mode="Markdown")
        else:
            new_user = await get_user(message.from_user.id)
            await message.answer(f"🎳 **Сбито кеглей: {val}**\nВыигрыш: **0 💎**\nВаш баланс: {new_user['balance']} 💎", parse_mode="Markdown")
        return

    is_win = (
        (game_code == "football" and val in [3, 4, 5]) or
        (game_code == "basketball" and val in [4, 5]) or
        (game_code == "darts" and val == 6) or
        (game_code == "dice" and val in [4, 5, 6])
    )

    if is_win:
        coeff = random.uniform(1.8, 2.5) if bet < 10 else (random.uniform(1.4, 1.8) if bet < 100 else random.uniform(1.2, 1.5))
        payout = int(bet * coeff)
        await update_balance(message.from_user.id, payout)
        new_user = await get_user(message.from_user.id)
        await message.answer(f"🎉 **ПОБЕДА!**\nВыигрыш: +{payout} 💎 Чекушек!\nВаш баланс: {new_user['balance']} 💎", parse_mode="Markdown")
    else:
        new_user = await get_user(message.from_user.id)
        await message.answer(f"❌ **ПРОИГРЫШ!**\nПотеряно: {bet} 💎 Чекушек.\nВаш баланс: {new_user['balance']} 💎", parse_mode="Markdown")

# =====================================================================
# SERVER & SELF PING tasks
# =====================================================================
async def handle_ping(request):
    return web.Response(text="Bot running cleanly!")

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"HTTP сервер активен на порту {PORT}")

async def self_ping_task():
    url = "https://checkushka-yk10.onrender.com/"
    await asyncio.sleep(10)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url) as resp:
                    logging.info(f"Self-ping ok ({url}): status {resp.status}")
            except Exception as e:
                logging.error(f"Self-ping error: {e}")
            await asyncio.sleep(600)

# =====================================================================
# MAIN ENTRY POINT
# =====================================================================
async def main():
    await init_db()
    await start_http_server()
    asyncio.create_task(self_ping_task())

    print("Чистый бот полностью переписан и запущен!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())
