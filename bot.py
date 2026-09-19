import asyncio

# Защита от повторной обработки одного и того же сообщения
processed_action_messages = set()

from datetime import datetime, timezone
import logging
import os
import random
import uuid
import aiohttp
import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F, BaseMiddleware
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

# === НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "8950292427:AAGEWlJWD0eCCdlkffBUXoUMz2b3-fQbZcw")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Checkushhka_Bot")
DATABASE_URL = os.getenv("DATABASE_URL")

admin_raw = os.getenv("ADMIN_IDS", "7837011810")
ADMIN_IDS = [int(i.strip()) for i in admin_raw.split(",") if i.strip().isdigit()]
if not ADMIN_IDS:
    ADMIN_IDS = [7837011810]

PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None

# === ЗАЩИТА ОТ ДУБЛИРОВАНИЯ UPDATE ===
class DedupMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        update = data.get("event_update")
        update_id = getattr(update, "update_id", None)

        if update_id is not None and db_pool is not None:
            async with db_pool.acquire() as conn:
                saved_id = await conn.fetchval(
                    """
                    INSERT INTO processed_updates (update_id)
                    VALUES ($1)
                    ON CONFLICT (update_id) DO NOTHING
                    RETURNING update_id
                    """,
                    update_id
                )

            # Этот update уже обработал другой экземпляр бота.
            if saved_id is None:
                return

        return await handler(event, data)


# === МИНИМАЛЬНЫЙ HTTP-СЕРВЕР ===
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"HTTP-сервер запущен на порту {PORT}")

# === АВТОПИНГ СЕРВИСА ===
async def self_ping_task():
    url = "https://checkushka-yk10.onrender.com/"
    await asyncio.sleep(10)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url) as resp:
                    logging.info(f"Автопинг выполнен ({url}): статус {resp.status}")
            except Exception as e:
                logging.error(f"Ошибка автопинга ({url}): {e}")
            await asyncio.sleep(600)

# === ИНИЦИАЛИЗА БД (POSTGRESQL) ===
async def init_db():
    global db_pool
    url = DATABASE_URL
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    db_pool = await asyncpg.create_pool(dsn=url)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                balance INT DEFAULT 100,
                bottles INT DEFAULT 0,
                bricks INT DEFAULT 0,
                referrer_id BIGINT,
                ref_count INT DEFAULT 0,
                ref_balance INT DEFAULT 0,
                is_banned INT DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS checks (
                check_id TEXT PRIMARY KEY,
                creator_id BIGINT,
                amount INT,
                is_activated INT DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS processed_updates (
                update_id BIGINT PRIMARY KEY,
                processed_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS bottles INT DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS bricks INT DEFAULT 0;
        """)
    logging.info("База данных PostgreSQL успешно инициализирована.")

async def get_or_create_user(user_id: int, first_name: str, username: str = None, referrer_id: int = None):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        is_new = False

        if not user:
            is_new = True
            valid_referrer = referrer_id if referrer_id and referrer_id != user_id else None
            if valid_referrer:
                ref_exists = await conn.fetchval("SELECT user_id FROM users WHERE user_id = $1", valid_referrer)
                if not ref_exists:
                    valid_referrer = None

            await conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance, bottles, referrer_id) VALUES ($1, $2, $3, 100, 0, $4)",
                user_id, first_name, username, valid_referrer
            )

            if valid_referrer:
                await conn.execute(
                    "UPDATE users SET balance = balance + 1, ref_count = ref_count + 1, ref_balance = ref_balance + 1 WHERE user_id = $1",
                    valid_referrer
                )
                try:
                    await bot.send_message(
                        chat_id=valid_referrer,
                        text=f"👤 Пользователь **{first_name}** перешел по твоей реферальной ссылке! Начислена 1 💎 Чекушка.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

    return user, is_new

async def get_user_by_id_or_username(identifier: str):
    async with db_pool.acquire() as conn:
        identifier = identifier.replace("@", "").strip()
        if identifier.isdigit():
            return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", int(identifier))
        else:
            return await conn.fetchrow("SELECT * FROM users WHERE LOWER(username) = LOWER($1)", identifier)

async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def update_balance(user_id: int, amount: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", amount, user_id)

async def update_bottles(user_id: int, amount: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET bottles = bottles + $1 WHERE user_id = $2", amount, user_id)

async def update_bricks(user_id: int, amount: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET bricks = bricks + $1 WHERE user_id = $2", amount, user_id)

async def create_check_db(creator_id: int, amount: int) -> str:
    check_id = str(uuid.uuid4())[:8]
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO checks (check_id, creator_id, amount) VALUES ($1, $2, $3)",
            check_id, creator_id, amount
        )
    return check_id

async def get_check_db(check_id: str):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM checks WHERE check_id = $1", check_id)

async def activate_check_db(check_id: str):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE checks SET is_activated = 1 WHERE check_id = $1", check_id)

# === КЛАВИАТУРЫ ===
def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎮 Играть")],
            [KeyboardButton(text="🛒 Магазин"), KeyboardButton(text="🔗 Рефералка")]
        ],
        resize_keyboard=True
    )

def profile_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Пополнить", callback_data="deposit")]])

def shop_inline_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍾 Купить бутылку — 5 💎", callback_data="buy_bottle")],
            [InlineKeyboardButton(text="🧱 Купить кирпич — 10 💎", callback_data="buy_brick")]
        ]
    )

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

# === СТАРТ И ОБРАБОТКА ЧЕКОВ ===
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

        receiver, _ = await get_or_create_user(
            user_id=message.from_user.id,
            first_name=message.from_user.first_name,
            username=message.from_user.username
        )

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

    referrer_id = int(args) if args and args.isdigit() else None
    user, is_new = await get_or_create_user(
        user_id=message.from_user.id,
        first_name=message.from_user.first_name,
        username=message.from_user.username,
        referrer_id=referrer_id,
    )

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    if is_new:
        start_text = (
            "Привет! 👋 Добро пожаловать в «Чекушку»!\n"
            "Вам начислено 100 💎 Чекушек на старт!\n"
            "Выберите действие ниже 👇"
        )
    else:
        start_text = (
            "Привет! 👋 Добро пожаловать в «Чекушку»!\n"
            "Выберите действие ниже 👇"
        )

    await message.answer(start_text, reply_markup=main_reply_keyboard())

# === МАГАЗИН ===
@dp.message(Command("shop", "магазин"))
@dp.message(F.text.lower().in_(["магазин", "🛒 магазин"]))
async def msg_shop(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    text = (
        "🛒 **МАГАЗИН**\n\n"
        "🍾 **Бутылка** — 5 Чекушек\n"
        "🧱 **Кирпич** — 10 Чекушек\n\n"
        "Предметы одноразовые. Чтобы использовать предмет, ответьте на сообщение игрока и напишите его название."
    )
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

@dp.callback_query(F.data == "buy_brick")
async def cb_buy_brick(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    if not user:
        user, _ = await get_or_create_user(call.from_user.id, call.from_user.first_name, call.from_user.username)

    if user['is_banned']:
        await call.answer("❌ Вы заблокированы.", show_alert=True)
        return

    if user["balance"] < 10:
        await call.answer("❌ Недостаточно Чекушек! Кирпич стоит 10 Чекушек.", show_alert=True)
        return

    await update_balance(call.from_user.id, -10)
    await update_bricks(call.from_user.id, 1)

    await call.message.answer("✅ Ты купил 🧱 кирпич за 10 Чекушек!")
    await call.answer()

# === РЕФЕРАЛЬНАЯ СИСТЕМА ===
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
        f"Приглашайте друзей и получайте **1 💎 Чекушку** за каждого зашедшего пользователя!\n\n"
        f"👥 Приглашено рефералов: **{user['ref_count']}**\n"
        f"💰 Заработано с рефералов: **{user['ref_balance']}** 💎\n\n"
        f"Ваша реферальная ссылка:\n`{ref_link}`"
    )
    await message.answer(text, parse_mode="Markdown")

# === ОБРАБОТКА ПЕРЕВОДОВ И СОЗДАНИЯ ЧЕКОВ ===
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

    await message.answer(
        f"✅ Вы успешно перевели {amount} 💎 Чекушек пользователю {target_user['first_name']}!"
    )

@dp.message(F.text.lower().startswith(("чек ", "создать чек ")))
async def process_create_check(message: Message):
    parts = message.text.strip().split()
    sender, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if sender["is_banned"]:
        await message.answer("❌ Вы заблокированы.")
        return

    amount = 0
    for part in parts:
        if part.isdigit():
            amount = int(part)
            break

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

# === ОБРАБОТКА КОМАНД В ЧАТАХ И ГРУППАХ ===
@dp.message(F.text.lower().in_(["чекушка", "кирпич", "профиль", "👤 профиль"]))
async def msg_profile(message: Message):
    action = message.text.lower().strip()

    if message.reply_to_message and action in ["бутылка", "кирпич"]:
        # Telegram/диспетчер не должен повторно обрабатывать одно сообщение.
        if message.message_id in processed_action_messages:
            return
        processed_action_messages.add(message.message_id)

        attacker, _ = await get_or_create_user(
            user_id=message.from_user.id,
            first_name=message.from_user.first_name,
            username=message.from_user.username
        )

        if attacker['is_banned']:
            await message.answer("❌ Вы заблокированы в боте.")
            return

        victim_user = message.reply_to_message.from_user
        if victim_user.id == attacker["user_id"]:
            await message.answer("❌ Нельзя использовать предмет на себе!")
            return

        if action == "чекушка":
            if attacker.get("bottles", 0) <= 0:
                await message.answer("❌ У вас нет 🍾 бутылки! Купите ее в магазине за 5 Чекушек.")
                return

            await update_bottles(attacker["user_id"], -1)
            await message.answer(
                f"🍾 {attacker['first_name']} ударил {victim_user.first_name} бутылкой!"
            )
        else:
            if attacker.get("bricks", 0) <= 0:
                await message.answer("❌ У вас нет 🧱 кирпича! Купите его в магазине за 10 Чекушек.")
                return

            await update_bricks(attacker["user_id"], -1)
            await message.answer(
                f"🧱 {attacker['first_name']} кинул кирпич в {victim_user.first_name}!"
            )
        return

    user = await get_user(message.from_user.id)
    if not user:
        user, _ = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    bottles_cnt = user.get("bottles", 0)
    bricks_cnt = user.get("bricks", 0)
    text = (
        "👤 **Ваш профиль**\n"
        f"├ 👤 {user['first_name']}\n"
        f"├ 🆔 ID: `{user['user_id']}`\n"
        f"├ 💎 Чекушок: {user['balance']}\n"
        f"├ 🍾 Бутылок: {bottles_cnt}\n"
        f"└ 🧱 Кирпичей: {bricks_cnt}"
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

# === ПОПОЛНЕНИЕ СТАРЗ ===
PACKAGES = {
    "buy_50": {"amount": 50, "stars": 30, "title": "50 Чекушек"},
    "buy_100": {"amount": 100, "stars": 60, "title": "100 Чекушек"},
    "buy_200": {"amount": 200, "stars": 120, "title": "200 Чекушек"},
    "buy_300": {"amount": 300, "stars": 180, "title": "300 Чекушек"},
}

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
    payload = message.successful_payment.invoice_payload
    added_amount = int(payload.split("_")[1])
    await update_balance(message.from_user.id, added_amount)
    user = await get_user(message.from_user.id)
    await message.answer(f"✅ Пополнение успешно!\n💎 Баланс: {user['balance']} Чекушек", reply_markup=main_reply_keyboard())

# === АДМИН-ОБРАБОТЧИК (ЕДИНСТВЕННАЯ АДМИН-ФУНКЦИЯ: ВЫДАЧА И СНЯТИЕ БАЛАНСА) ===
@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text.lower().startswith(("чекушка ", "выдать ", "+", "забрать ", "снять ", "-")))
async def process_admin_text_commands(message: Message):
    text = message.text.strip()
    parts = text.split()

    first_word = parts[0].lower()
    action = "add" if first_word in ["чекушка", "выдать", "+"] else "sub"
    amount = 0
    target_str = None

    if message.reply_to_message:
        target_str = str(message.reply_to_message.from_user.id)
        for part in parts[1:]:
            if part.isdigit():
                amount = int(part)
                break
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
    elif action == "sub":
        await update_balance(target_user['user_id'], -amount)
        await message.answer(f"⚠️ Забрали {amount} 💎 Чекушек у пользователя {target_user['first_name']}.")

# === ИГРОКОВЫЕ КНОПКИ И СТАВКИ ===
@dp.callback_query(F.data.startswith("game_"))
async def cb_game_info(call: CallbackQuery):
    game_type = call.data.split("_")[1]
    names = {"football": "футбол", "basketball": "баскетбол", "darts": "дартс", "bowling": "боулинг", "dice": "кубик"}
    await call.message.answer(f"Чтобы сыграть, напишите в чат: `{names.get(game_type, 'игру')} [ставка]`\nНапример: `{names.get(game_type, 'игру')} 10`", parse_mode="Markdown")
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

    found_game = None
    for game_keyword in game_map.keys():
        if game_keyword in parts:
            found_game = game_keyword
            break

    if not found_game:
        return

    bet = None
    for part in parts:
        if part.isdigit():
            bet = int(part)
            break

    if bet is None:
        await message.answer(
            f"❌ Укажите сумму ставки цифрами!\nПример: `{found_game} 10` или `{found_game} ставка 10`",
            parse_mode="Markdown"
        )
        return

    if bet <= 0:
        await message.answer("❌ Ставка должна быть больше 0!")
        return

    user, _ = await get_or_create_user(
        user_id=message.from_user.id,
        first_name=message.from_user.first_name,
        username=message.from_user.username
    )

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
        await message.answer("❌ Ошибка отправки анимации. Убедитесь, что у бота есть права отправлять эмодзи/стикеры. Ставка возвращена!")
        return

    await asyncio.sleep(2.5)

    if game_code == "bowling":
        if val == 6:
            coeff = random.uniform(2.5, 3.5) if bet < 10 else (random.uniform(2.0, 2.5) if bet < 100 else random.uniform(1.6, 2.0))
            total_payout = int(bet * coeff)
            await update_balance(message.from_user.id, total_payout)
            new_user = await get_user(message.from_user.id)
            profit = total_payout - bet
            
            await message.answer(
                f"🎉 **СТРАЙК! Сбиты абсолютно все кегли!**\n"
                f"Выигрыш: +{total_payout} 💎 Чекушек (Прибыль: +{profit} 💎)!\n"
                f"Ваш баланс: {new_user['balance']} 💎",
                parse_mode="Markdown"
            )

        elif val == 5:
            coeff = random.uniform(1.8, 2.2) if bet < 10 else (random.uniform(1.5, 1.8) if bet < 100 else random.uniform(1.3, 1.5))
            total_payout = int(bet * coeff)
            await update_balance(message.from_user.id, total_payout)
            new_user = await get_user(message.from_user.id)
            profit = total_payout - bet

            await message.answer(
                f"🔥 **ПОЧТИ СТРАЙК! Сбиты все кегли, кроме одной!**\n"
                f"Выигрыш: +{total_payout} 💎 Чекушек (Прибыль: +{profit} 💎)!\n"
                f"Ваш баланс: {new_user['balance']} 💎",
                parse_mode="Markdown"
            )

        elif val == 4:
            coeff = random.uniform(1.4, 1.6) if bet < 10 else (random.uniform(1.2, 1.4) if bet < 100 else random.uniform(1.1, 1.25))
            total_payout = int(bet * coeff)
            await update_balance(message.from_user.id, total_payout)
            new_user = await get_user(message.from_user.id)
            profit = total_payout - bet

            await message.answer(
                f"👍 **ХОРОШИЙ БРОСОК! Сбиты почти все кегли (осталось 2)!**\n"
                f"Выигрыш: +{total_payout} 💎 Чекушек (Прибыль: +{profit} 💎)!\n"
                f"Ваш баланс: {new_user['balance']} 💎",
                parse_mode="Markdown"
            )

        else:
            new_user = await get_user(message.from_user.id)
            await message.answer(
                f"🎳 **Сбито всего {val} кегли(ей)!**\n"
                f"Выигрыш: **0 💎 Чекушек**.\n"
                f"Ваш баланс: {new_user['balance']} 💎",
                parse_mode="Markdown"
            )
        return

    is_win = False
    if game_code == "football" and val in [3, 4, 5]:
        is_win = True
    elif game_code == "basketball" and val in [4, 5]:
        is_win = True
    elif game_code == "darts" and val == 6:
        is_win = True
    elif game_code == "dice" and val in [4, 5, 6]:
        is_win = True

    if is_win:
        coeff = random.uniform(1.8, 2.5) if bet < 10 else (random.uniform(1.4, 1.8) if bet < 100 else random.uniform(1.2, 1.5))
        total_payout = int(bet * coeff)
        await update_balance(message.from_user.id, total_payout)
        new_user = await get_user(message.from_user.id)
        
        profit = total_payout - bet
        await message.answer(
            f"🎉 **ПОБЕДА!**\nВыигрыш: +{total_payout} 💎 Чекушек (Прибыль: +{profit} 💎)!\nВаш баланс: {new_user['balance']} 💎",
            parse_mode="Markdown"
        )
    else:
        new_user = await get_user(message.from_user.id)
        await message.answer(
            f"❌ **ПРОИГРЫШ!**\nВы потеряли: {bet} 💎 Чекушек.\nВаш баланс: {new_user['balance']} 💎",
            parse_mode="Markdown"
        )

# === ЗАПУСК ===
async def main():
    await init_db()
    await start_http_server()
    asyncio.create_task(self_ping_task())

    # Один и тот же Telegram update будет обработан только один раз,
    # даже если случайно запущены две копии бота.
    dp.update.outer_middleware(DedupMiddleware())

    print("Бот запущен на PostgreSQL!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())
