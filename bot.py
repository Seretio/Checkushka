import asyncio
import base64
import logging
import os
import random
import aiohttp
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandObject, CommandStart
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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8950292427:AAHiJ26IAGA4cTwC4OAnJU3DxZUVE8Ld7xg")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Checkushhka_Bot")
DB_NAME = os.getenv("DB_NAME", "chekushka.db")

admin_raw = os.getenv("ADMIN_IDS", "7837011810")
ADMIN_IDS = [int(i.strip()) for i in admin_raw.split(",") if i.strip().isdigit()]

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "Seretio")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Checkushka")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

github_sync_lock = asyncio.Lock()
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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

# === СИНХРОНИЗАЦИЯ С GITHUB ===
async def download_db_from_github():
    if not GITHUB_TOKEN:
        logging.warning("GITHUB_TOKEN не задан, синхронизация отключена.")
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
                logging.info("Файл БД не найден на GitHub, создаём локально.")

async def upload_db_to_github():
    if not GITHUB_TOKEN:
        return False

    if not os.path.exists(DB_NAME):
        return False

    async with github_sync_lock:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{DB_NAME}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            async with aiohttp.ClientSession() as session:
                sha = None
                async with session.get(f"{url}?ref={GITHUB_BRANCH}", headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sha = data.get("sha")

                with open(DB_NAME, "rb") as f:
                    content = base64.b64encode(f.read()).decode("utf-8")

                payload = {
                    "message": "Auto-save database",
                    "content": content,
                    "branch": GITHUB_BRANCH,
                }
                if sha:
                    payload["sha"] = sha

                async with session.put(url, headers=headers, json=payload) as resp:
                    return resp.status in (200, 201)
        except Exception as e:
            logging.exception(f"Ошибка GitHub-сохранения: {e}")
            return False

async def github_sync_task():
    while True:
        await asyncio.sleep(600)
        try:
            await upload_db_to_github()
        except Exception:
            pass

# === ИНИЦИАЛИЗА БД ===
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
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
        )
        await db.commit()

async def get_or_create_user(user_id: int, first_name: str, username: str = None, referrer_id: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if not user:
            valid_referrer = referrer_id if referrer_id and referrer_id != user_id else None
            if valid_referrer:
                async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (valid_referrer,)) as c:
                    if not await c.fetchone():
                        valid_referrer = None

            await db.execute(
                "INSERT INTO users (user_id, first_name, username, referrer_id) VALUES (?, ?, ?, ?)",
                (user_id, first_name, username, valid_referrer),
            )
            await db.commit()

            if valid_referrer:
                await db.execute(
                    "UPDATE users SET balance = balance + 1, ref_count = ref_count + 1, ref_balance = ref_balance + 1 WHERE user_id = ?",
                    (valid_referrer,),
                )
                await db.commit()

            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
                user = await cursor.fetchone()
            await upload_db_to_github()

        return user

async def get_user_by_id_or_username(identifier: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        identifier = identifier.replace("@", "").strip()
        if identifier.isdigit():
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (int(identifier),)) as cursor:
                return await cursor.fetchone()
        else:
            async with db.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (identifier,)) as cursor:
                return await cursor.fetchone()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def update_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
    await upload_db_to_github()

# === КЛАВИАТУРЫ ===
def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎮 Играть")]],
        resize_keyboard=True
    )

def profile_inline_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Пополнить", callback_data="deposit")]])

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
            [InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football")],
            [InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_basketball")],
            [InlineKeyboardButton(text="🎯 Дартс", callback_data="game_darts")],
        ]
    )

# === СТАРТ И ОСНОВНОЕ МЕНЮ ===
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    referrer_id = int(command.args) if command.args and command.args.isdigit() else None
    user = await get_or_create_user(
        user_id=message.from_user.id,
        first_name=message.from_user.first_name,
        username=message.from_user.username,
        referrer_id=referrer_id,
    )

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    await message.answer("Привет! 👋 Добро пожаловать в «Чекушку»!\nВыберите действие ниже 👇", reply_markup=main_reply_keyboard())

# === ОБРАБОТКА КОМАНД В ЧАТАХ И ГРУППАХ ===
@dp.message(F.text.lower().in_(["чекушка", "профиль", "👤 профиль"]))
async def msg_profile(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        user = await get_or_create_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    text = (
        "👤 **Ваш профиль**\n"
        f"├ 👤 {user['first_name']}\n"
        f"├ 🆔 ID: `{user['user_id']}`\n"
        f"└ 💎 Чекушок: {user['balance']}"
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
        "• `баскетбол 5`\n"
        "• `футбол 1`\n"
        "• `дартс 500`"
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

# === АДМИН-ОБРАБОТЧИК ===
@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text)
async def process_admin_text_commands(message: Message):
    text = message.text.strip()
    parts = text.split()

    action, amount, target_str = None, 0, None

    if len(parts) >= 3 and parts[0].lower() in ["чекушка", "выдать", "+"]:
        if parts[1].isdigit():
            action, amount, target_str = "add", int(parts[1]), parts[2]
    elif len(parts) >= 3 and parts[0].lower() in ["забрать", "снять", "-"]:
        if parts[1].isdigit():
            action, amount, target_str = "sub", int(parts[1]), parts[2]
    
    if message.reply_to_message and len(parts) >= 2:
        if parts[0].lower() in ["чекушка", "выдать", "+"] and parts[1].isdigit():
            action, amount, target_str = "add", int(parts[1]), str(message.reply_to_message.from_user.id)
        elif parts[0].lower() in ["забрать", "снять", "-"] and parts[1].isdigit():
            action, amount, target_str = "sub", int(parts[1]), str(message.reply_to_message.from_user.id)

    if not action or amount <= 0 or not target_str:
        await process_game_bet(message)
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

# === ИГРОВОЙ ОБРАБОТЧИК ===
@dp.callback_query(F.data.startswith("game_"))
async def cb_game_info(call: CallbackQuery):
    game_type = call.data.split("_")[1]
    names = {"football": "футбол", "basketball": "баскетбол", "darts": "дартс"}
    await call.message.answer(f"Чтобы сыграть, напишите в чат: `{names.get(game_type, 'игру')} [ставка]`\nНапример: `{names.get(game_type, 'игру')} 10`", parse_mode="Markdown")
    await call.answer()

@dp.message(F.text)
async def process_game_bet(message: Message):
    raw_text = message.text.replace(f"@{BOT_USERNAME}", "").strip()
    parts = raw_text.split()
    
    if len(parts) != 2:
        return

    game_name, bet_str = parts[0].lower(), parts[1]
    game_map = {"футбол": ("⚽", "football"), "баскетбол": ("🏀", "basketball"), "дартс": ("🎯", "darts")}

    if game_name not in game_map or not bet_str.isdigit():
        return

    bet = int(bet_str)
    if bet <= 0:
        await message.answer("Ставка должна быть больше 0!")
        return

    user = await get_or_create_user(
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
    
    emoji, game_code = game_map[game_name]
    dice_msg = await message.answer_dice(emoji=emoji)
    val = dice_msg.dice.value
    await asyncio.sleep(2.5)

    is_win = False
    if game_code == "football" and val in [3, 4, 5]:
        is_win = True
    elif game_code == "basketball" and val in [4, 5]:
        is_win = True
    elif game_code == "darts" and val == 6:
        is_win = True

    if is_win:
        if bet < 10:
            coeff = random.uniform(1.8, 2.5)
        elif bet < 100:
            coeff = random.uniform(1.4, 1.8)
        else:
            coeff = random.uniform(1.2, 1.5)

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
    await download_db_from_github()
    await init_db()
    await upload_db_to_github()
    await start_http_server()
    asyncio.create_task(github_sync_task())

    print("Бот запущен!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())
