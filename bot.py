import os
import sys
import asyncio
import logging
import random
import re
from aiohttp import web, ClientSession
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice
from aiogram.filters import Command

# ================= НАСТРОЙКИ И ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
PING_URL = os.getenv("PING_URL", "https://checkushka.onrender.com/")

if not BOT_TOKEN:
    print("ОШИБКА: Переменная BOT_TOKEN не установлена!")
    sys.exit(1)

if not DATABASE_URL:
    print("ОШИБКА: Переменная DATABASE_URL не установлена!")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None

# Регулярное выражение для точного перехвата игровых ставок
GAME_BET_REGEX = r"^(?i)(футбол|баскетбол|дартс|боулинг|кубик|кости)\s+(?:ставка\s+)?(\d+)$"

# ================= ВЕБ-СЕРВЕР И АВТО-ПИНГ =================

async def handle_ping(request):
    return web.Response(text="OK", status=200)

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"HTTP-сервер для запущен на порту {port}")

async def self_ping_task():
    await asyncio.sleep(10)
    async with ClientSession() as session:
        while True:
            try:
                async with session.get(PING_URL) as resp:
                    print(f"Авто-пинг {PING_URL}: Статус {resp.status}")
            except Exception as e:
                print(f"Ошибка авто-пинга: {e}")
            await asyncio.sleep(240)

# ================= БАЗА ДАННЫХ POSTGRESQL =================

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                balance INT DEFAULT 10,
                referrer_id BIGINT,
                is_banned BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                reward INT NOT NULL,
                max_uses INT NOT NULL,
                used_count INT DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS user_promos (
                user_id BIGINT,
                code TEXT,
                PRIMARY KEY (user_id, code)
            );
            CREATE TABLE IF NOT EXISTS check_activations (
                user_id BIGINT PRIMARY KEY,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    print("База данных успешно инициализирована!")

async def get_or_create_user(user_id: int, first_name: str, username: str, referrer_id: int = None):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not user:
            await conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance, referrer_id) VALUES ($1, $2, $3, 10, $4)",
                user_id, first_name, username, referrer_id
            )
            if referrer_id and referrer_id != user_id:
                await conn.execute(
                    "UPDATE users SET balance = balance + 5 WHERE user_id = $1",
                    referrer_id
                )
                try:
                    await bot.send_message(
                        referrer_id, 
                        f"🎉 По вашей ссылке перешел {first_name}! Вам начислено 5 💎 Чекушек!"
                    )
                except Exception:
                    pass
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            return user, True
        return user, False

async def get_user(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def update_balance(user_id: int, amount: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", amount, user_id)

# ================= ОСНОВНЫЕ КОМАНДЫ =================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])

    user, is_new = await get_or_create_user(
        user_id=message.from_user.id,
        first_name=message.from_user.first_name,
        username=message.from_user.username,
        referrer_id=referrer_id
    )

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    welcome_text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Добро пожаловать в бота Чекушка! 🍾\n"
        f"Ваш баланс: {user['balance']} 💎 Чекушек.\n\n"
        f"🎮 **Как играть:**\n"
        f"Отправьте сообщение с названием игры и ставкой:\n"
        f"• `баскетбол 50`\n"
        f"• `футбол 10`\n"
        f"• `дартс 25`\n"
        f"• `боулинг 100`\n"
        f"• `кубик 15`\n\n"
        f"📜 Список команд: /help"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "📋 **Команды бота:**\n\n"
        "💰 /profile - Ваш профиль и баланс\n"
        "🔗 /ref - Реферальная ссылка\n"
        "🎁 /buy - Купить Чекушки (Telegram Stars)\n"
        "💸 `перевод [ID] [сумма]` - Передать Чекушки\n"
        "🎫 `промо [код]` - Активировать промокод\n\n"
        "🎲 **Игры:**\n"
        "Напишите: `[игра] [ставка]` (Например: `баскетбол 50`)"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Профиль не найден. Нажмите /start")
        return
    await message.answer(
        f"👤 **Ваш профиль:**\n"
        f"ID: `{user['user_id']}`\n"
        f"Имя: {user['first_name']}\n"
        f"Баланс: **{user['balance']}** 💎 Чекушек",
        parse_mode="Markdown"
    )

@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    await message.answer(
        f"🔗 **Ваша реферальная ссылка:**\n`{ref_link}`\n\n"
        f"Делитесь ссылкой и получайте **5 💎 Чекушек** за каждого приглашенного друга!",
        parse_mode="Markdown"
    )

# ================= ИГРОВОЙ ОБРАБОТЧИК (ПРИОРИТЕТНЫЙ) =================

@dp.message(F.text.regexp(GAME_BET_REGEX))
async def process_game_bet(message: Message):
    match = re.match(GAME_BET_REGEX, message.text.strip())
    if not match:
        return

    game_keyword = match.group(1).lower()
    bet = int(match.group(2))

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

    game_map = {
        "футбол": {"emoji": "⚽", "code": "football", "delay": 3.5},
        "баскетбол": {"emoji": "🏀", "code": "basketball", "delay": 3.5},
        "дартс": {"emoji": "🎯", "code": "darts", "delay": 2.5},
        "боулинг": {"emoji": "🎳", "code": "bowling", "delay": 3.5},
        "кубик": {"emoji": "🎲", "code": "dice", "delay": 2.5},
        "кости": {"emoji": "🎲", "code": "dice", "delay": 2.5},
    }

    game_info = game_map[game_keyword]
    emoji = game_info["emoji"]
    game_code = game_info["code"]

    await update_balance(message.from_user.id, -bet)

    try:
        dice_msg = await message.answer_dice(emoji=emoji)
        val = dice_msg.dice.value
    except Exception as e:
        await update_balance(message.from_user.id, bet)
        logging.error(f"Ошибка отправки dice ({emoji}): {e}")
        await message.answer("❌ Ошибка отправки игры. Ставка возвращена!")
        return

    await asyncio.sleep(game_info["delay"])

    if game_code == "bowling":
        if val == 6:
            coeff = random.uniform(2.5, 3.5) if bet < 10 else (random.uniform(2.0, 2.5) if bet < 100 else random.uniform(1.6, 2.0))
            total_payout = int(bet * coeff)
            await update_balance(message.from_user.id, total_payout)
            new_user = await get_user(message.from_user.id)
            profit = total_payout - bet
            await message.answer(
                f"🎉 **СТРАЙК! Сбиты все кегли!**\n"
                f"Выигрыш: +{total_payout} 💎 (Прибыль: +{profit} 💎)!\n"
                f"Баланс: {new_user['balance']} 💎",
                parse_mode="Markdown"
            )
        elif val in [4, 5]:
            coeff = random.uniform(1.3, 1.8) if bet < 100 else random.uniform(1.1, 1.4)
            total_payout = int(bet * coeff)
            await update_balance(message.from_user.id, total_payout)
            new_user = await get_user(message.from_user.id)
            profit = total_payout - bet
            await message.answer(
                f"🔥 **ХОРОШИЙ БРОСОК! Сбито {val} кегли(ей)!**\n"
                f"Выигрыш: +{total_payout} 💎 (Прибыль: +{profit} 💎)!\n"
                f"Баланс: {new_user['balance']} 💎",
                parse_mode="Markdown"
            )
        else:
            new_user = await get_user(message.from_user.id)
            await message.answer(
                f"🎳 **Сбито всего {val} кегли(ей)!**\n"
                f"Выигрыш: **0 💎**.\n"
                f"Баланс: {new_user['balance']} 💎",
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
            f"🎉 **ПОБЕДА!**\nВыигрыш: +{total_payout} 💎 (Прибыль: +{profit} 💎)!\nБаланс: {new_user['balance']} 💎",
            parse_mode="Markdown"
        )
    else:
        new_user = await get_user(message.from_user.id)
        await message.answer(
            f"❌ **ПРОИГРЫШ!**\nВы потеряли: {bet} 💎.\nБаланс: {new_user['balance']} 💎",
            parse_mode="Markdown"
        )

# ================= ОПЛАТА СТАРСАМИ =================

@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    prices = [LabeledPrice(label="100 Чекушек", amount=10)]
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Покупка 100 Чекушек",
        description="Пополнение баланса бота на 100 Чекушек",
        payload="buy_100_chekushek",
        currency="XTR",
        prices=prices,
        start_parameter="buy_chekushek"
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    if message.successful_payment.invoice_payload == "buy_100_chekushek":
        await update_balance(message.from_user.id, 100)
        await message.answer("🎉 Оплата прошла успешно! Вам начислено 100 💎 Чекушек.")

# ================= ПЕРЕВОДЫ И ПРОМОКОДЫ =================

@dp.message(F.text.lower().startswith("перевод "))
async def process_transfer(message: Message):
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("❌ Формат: `перевод [ID] [сумма]`", parse_mode="Markdown")
        return

    target_id = int(parts[1])
    amount = int(parts[2])

    if amount <= 0:
        await message.answer("❌ Сумма должна быть больше 0.")
        return

    if target_id == message.from_user.id:
        await message.answer("❌ Нельзя переводить самому себе.")
        return

    sender = await get_user(message.from_user.id)
    if sender["balance"] < amount:
        await message.answer("❌ Недостаточно средств.")
        return

    target = await get_user(target_id)
    if not target:
        await message.answer("❌ Пользователь не найден.")
        return

    await update_balance(message.from_user.id, -amount)
    await update_balance(target_id, amount)

    await message.answer(f"✅ Вы успешно перевели {amount} 💎 пользователю `{target_id}`.", parse_mode="Markdown")
    try:
        await bot.send_message(target_id, f"🎁 Вам переведено {amount} 💎 от `{message.from_user.id}`!", parse_mode="Markdown")
    except Exception:
        pass

@dp.message(F.text.lower().startswith("промо "))
async def process_promo(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Введите промокод!")
        return

    code = parts[1].strip()
    user_id = message.from_user.id

    async with db_pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promo_codes WHERE code = $1", code)
        if not promo:
            await message.answer("❌ Промокод не существует!")
            return

        if promo["used_count"] >= promo["max_uses"]:
            await message.answer("❌ Превышено число активаций!")
            return

        used = await conn.fetchrow("SELECT * FROM user_promos WHERE user_id = $1 AND code = $2", user_id, code)
        if used:
            await message.answer("❌ Вы уже активировали этот промокод!")
            return

        await conn.execute("INSERT INTO user_promos (user_id, code) VALUES ($1, $2)", user_id, code)
        await conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code = $1", code)
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", promo["reward"], user_id)

    await message.answer(f"🎉 Промокод активирован! Взнос: +{promo['reward']} 💎")

# ================= КОМАНДЫ АДМИНИСТРАТОРА =================

@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text.lower().startswith(("чекушка ", "выдать ", "+", "забрать ", "снять ", "-")))
async def admin_manage_balance(message: Message):
    parts = message.text.split()
    if len(parts) < 3:
        return

    cmd = parts[0].lower()
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        return

    if cmd in ["выдать", "+", "чекушка"]:
        await update_balance(target_id, amount)
        await message.answer(f"✅ Пользователю `{target_id}` выдано {amount} 💎", parse_mode="Markdown")
    elif cmd in ["забрать", "снять", "-"]:
        await update_balance(target_id, -amount)
        await message.answer(f"✅ У пользователя `{target_id}` забрано {amount} 💎", parse_mode="Markdown")

@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text.lower().startswith("создатьпромо "))
async def admin_create_promo(message: Message):
    parts = message.text.split()
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await message.answer("❌ Использование: `создатьпромо [код] [награда] [кол-во]`", parse_mode="Markdown")
        return

    code = parts[1]
    reward = int(parts[2])
    max_uses = int(parts[3])

    async with db_pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO promo_codes (code, reward, max_uses) VALUES ($1, $2, $3)",
                code, reward, max_uses
            )
            await message.answer(f"✅ Промокод `{code}` на {reward} 💎 ({max_uses} раз) создан!", parse_mode="Markdown")
        except asyncpg.UniqueViolationError:
            await message.answer("❌ Такой промокод уже существует!")

# ================= ЗАПУСК БОТА =================

async def main():
    await init_db()
    
    # Запуск фонового веб-сервера (вызывается строго 1 раз)
    await start_http_server()
    asyncio.create_task(self_ping_task())

    print("Бот успешно запущен!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен.")
