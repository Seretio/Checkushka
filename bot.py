import asyncio
import base64
import io
import logging
import random
import aiosqlite
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
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

# Защита от одновременных записей БД в GitHub.
github_sync_lock = asyncio.Lock()

PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === МИНИМАЛЬНЫЙ HTTP-СЕРВЕР ДЛЯ RENDER ===
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
    """Мгновенно сохраняет актуальную SQLite-БД в GitHub."""
    if not GITHUB_TOKEN:
        logging.warning("GITHUB_TOKEN не задан, синхронизация отключена.")
        return False

    if not os.path.exists(DB_NAME):
        logging.warning(f"Файл БД не найден: {DB_NAME}")
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

                async with session.get(
                    f"{url}?ref={GITHUB_BRANCH}",
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sha = data.get("sha")
                    elif resp.status != 404:
                        logging.error(
                            f"GitHub: ошибка получения SHA: "
                            f"{resp.status} {await resp.text()}"
                        )
                        return False

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
                    if resp.status in (200, 201):
                        logging.info("✅ База сразу сохранена в GitHub.")
                        return True

                    logging.error(
                        f"❌ Ошибка сохранения БД: "
                        f"{resp.status} {await resp.text()}"
                    )
                    return False

        except Exception as e:
            logging.exception(f"❌ Ошибка GitHub-сохранения: {e}")
            return False


async def github_sync_task():
    """Резервная синхронизация БД каждые 10 минут."""
    while True:
        await asyncio.sleep(600)
        try:
            await upload_db_to_github()
        except Exception as e:
            logging.error(f"Ошибка фоновой синхронизации с GitHub: {e}")


# === ИНИЦИАЛИЗА БАЗЫ ДАННЫХ ===
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

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БАЗЫ ===
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
                """
                INSERT INTO users (user_id, first_name, username, referrer_id)
                VALUES (?, ?, ?, ?)
            """,
                (user_id, first_name, username, valid_referrer),
            )
            await db.commit()

            if valid_referrer:
                await db.execute(
                    """
                    UPDATE users 
                    SET balance = balance + 1, ref_count = ref_count + 1, ref_balance = ref_balance + 1 
                    WHERE user_id = ?
                """,
                    (valid_referrer,),
                )
                await db.commit()
                
                try:
                    await bot.send_message(
                        chat_id=valid_referrer,
                        text="🎉 Друг перешёл по твоей ссылке! Ты получаешь 1 💎 Чекушку."
                    )
                except Exception:
                    pass

            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
                user = await cursor.fetchone()

            # Создание пользователя/реферала уже commit'нуто — сохраняем сразу.
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
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

    # Сохраняем баланс сразу после изменения.
    await upload_db_to_github()


# === КЛАВИАТУРЫ ===
def main_reply_keyboard():
    """Нижняя обычная клавиатура."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎮 Играть")]
        ],
        resize_keyboard=True
    )

def profile_inline_keyboard():
    """Инлайн-кнопка пополнения под сообщением профиля."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Пополнить", callback_data="deposit")]
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
            [InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football")],
            [InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_basketball")],
            [InlineKeyboardButton(text="🎯 Дартс", callback_data="game_darts")],
        ]
    )

# === МЕНЮ И СТАРТ ===
MAIN_TEXT = "Привет! 👋 Добро пожаловать в «Чекушку»!\nВыберите действие ниже 👇"

@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    referrer_id = None
    if command.args and command.args.isdigit():
        referrer_id = int(command.args)

    user = await get_or_create_user(
        user_id=message.from_user.id,
        first_name=message.from_user.first_name,
        username=message.from_user.username,
        referrer_id=referrer_id,
    )

    if user['is_banned']:
        await message.answer("❌ Вы заблокированы в боте.")
        return

    await message.answer(MAIN_TEXT, reply_markup=main_reply_keyboard())

# === ПРОФИЛЬ (ВЫЗОВ ПО НИЖНЕЙ КНОПКЕ) ===
@dp.message(F.text == "👤 Профиль")
async def msg_profile(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        user = await get_or_create_user(
            user_id=message.from_user.id,
            first_name=message.from_user.first_name,
            username=message.from_user.username
        )

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

# === ИГРАТЬ (ВЫЗОВ ПО НИЖНЕЙ КНОПКЕ) ===
@dp.message(F.text == "🎮 Играть")
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

# === ПОПОЛНЕНИЕ И STARS ===
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

    text = (
        "✅ Пополнение успешно!\n"
        f"💎 Вам начислено: {added_amount} Чекушек\n"
        f"💎 Ваш баланс: {user['balance']} Чекушек"
    )
    await message.answer(text, reply_markup=main_reply_keyboard())

# === РЕФЕРАЛЬНАЯ СИСТЕМА ===
@dp.message(Command("ref"))
async def cmd_ref(message: Message):
    user = await get_or_create_user(
        user_id=message.from_user.id,
        first_name=message.from_user.first_name,
        username=message.from_user.username,
    )
    ref_link = f"https://t.me/{BOT_USERNAME}?start={message.from_user.id}"
    
    text = (
        "👥 Реферальная система\n\n"
        "Приглашай друзей и получай Чекушки!\n"
        "🎁 За каждого приглашённого друга ты получаешь 1 💎 Чекушку.\n\n"
        "🔗 Твоя личная ссылка:\n"
        f"{ref_link}\n\n"
        f"👥 Приглашено: {user['ref_count']}\n"
        f"💎 Получено: {user['ref_balance']}\n\n"
        "Отправь свою ссылку друзьям и получай Чекушки! 🚀"
    )
    await message.answer(text)

# === ФАЙЛ С ПОЛЬЗОВАТЕЛЯМИ ===
@dp.message(Command("export"))
async def cmd_export(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            users = await cursor.fetchall()

    file_content = "ID | USERNAME | FIRST_NAME | BALANCE | REF_COUNT | REFERRER_ID\n"
    file_content += "=" * 65 + "\n"
    for u in users:
        un = f"@{u['username']}" if u['username'] else "None"
        file_content += f"{u['user_id']} | {un} | {u['first_name']} | {u['balance']} | {u['ref_count']} | {u['referrer_id']}\n"

    input_file = BufferedInputFile(file_content.encode("utf-8"), filename="users_data.txt")
    await message.answer_document(input_file, caption="📄 Полный список пользователей бота.")

@dp.message(Command("sync"))
async def cmd_sync(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await upload_db_to_github()
    await message.answer("✅ Актуальная база данных сохранена в GitHub!")

# === АДМИН-КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ БАЛАНСОМ ===
@dp.message(F.from_user.id.in_(ADMIN_IDS) & F.text)
async def process_admin_text_commands(message: Message):
    text = message.text.strip()
    parts = text.split()

    action = None
    amount = 0
    target_str = None

    if len(parts) >= 3 and parts[0].lower() in ["чекушка", "выдать", "+"]:
        action = "add"
        if parts[1].isdigit():
            amount = int(parts[1])
            target_str = parts[2]
    elif len(parts) >= 3 and parts[0].lower() in ["забрать", "снять", "-"]:
        action = "sub"
        if parts[1].isdigit():
            amount = int(parts[1])
            target_str = parts[2]
    
    if message.reply_to_message and len(parts) >= 2:
        if parts[0].lower() in ["чекушка", "выдать", "+"] and parts[1].isdigit():
            action = "add"
            amount = int(parts[1])
            target_str = str(message.reply_to_message.from_user.id)
        elif parts[0].lower() in ["забрать", "снять", "-"] and parts[1].isdigit():
            action = "sub"
            amount = int(parts[1])
            target_str = str(message.reply_to_message.from_user.id)

    if action and amount > 0 and target_str:
        target_user = await get_user_by_id_or_username(target_str)
        if not target_user:
            await message.answer("❌ Пользователь не найден в базе бота.")
            return

        if action == "add":
            await update_balance(target_user['user_id'], amount)
            new_user = await get_user(target_user['user_id'])
            await message.answer(f"✅ Выдали {amount} 💎 Чекушек пользователю {target_user['first_name']}.")
            try:
                await bot.send_message(
                    chat_id=target_user['user_id'],
                    text=f"🎁 Вам выдано {amount} 💎 Чекушек администратором!\nВаш баланс: {new_user['balance']} 💎"
                )
            except Exception:
                pass

        elif action == "sub":
            await update_balance(target_user['user_id'], -amount)
            new_user = await get_user(target_user['user_id'])
            await message.answer(f"⚠️ Забрали {amount} 💎 Чекушек у пользователя {target_user['first_name']}.")
            try:
                await bot.send_message(
                    chat_id=target_user['user_id'],
                    text=f"🔻 У вас забрали {amount} 💎 Чекушек.\nВаш баланс: {new_user['balance']} 💎"
                )
            except Exception:
                pass
        return

    await process_game_bet(message)

# === ВСЕ АДМИНСКИЕ КОМАНДЫ ===
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = (
        "🛠 **Панель Администратора**\n\n"
        "**Управление балансом:**\n"
        "• `Чекушка 50 @username` — выдать 50 Чекушек\n"
        "• `Забрать 20 @username` — забрать 20 Чекушек\n\n"
        "**Команды админа:**\n"
        "• `/export` — Скачать файл с пользователями\n"
        "• `/sync` — Синхронизировать БД с GitHub вручную\n"
        "• `/stats` — Общая статистика\n"
        "• `/ban [ID/@username]` — Заблокировать\n"
        "• `/unban [ID/@username]` — Разблокировать\n"
        "• `/broadcast [текст]` — Рассылка"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*), SUM(balance) FROM users") as cursor:
            row = await cursor.fetchone()
            count = row[0] or 0
            total_balance = row[1] or 0

    await message.answer(f"📊 **Статистика:**\n\n👥 Пользователей: {count}\n💎 Всего Чекушек в системе: {total_balance}")

@dp.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS or not command.args:
        return
    user = await get_user_by_id_or_username(command.args)
    if user:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user['user_id'],))
            await db.commit()
        await upload_db_to_github()
        await message.answer(f"⛔ Пользователь {user['first_name']} заблокирован.")

@dp.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS or not command.args:
        return
    user = await get_user_by_id_or_username(command.args)
    if user:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user['user_id'],))
            await db.commit()
        await upload_db_to_github()
        await message.answer(f"✅ Пользователь {user['first_name']} разблокирован.")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS or not command.args:
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()

    success, failed = 0, 0
    for u in users:
        try:
            await bot.send_message(chat_id=u['user_id'], text=command.args)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(f"📢 **Рассылка завершена:**\n✅ Успешно: {success}\n❌ Не доставлено: {failed}")

# === ИГРЫ (ФУТБОЛ, БАСКЕТБОЛ, ДАРТС) ===
@dp.callback_query(F.data.startswith("game_"))
async def cb_game_info(call: CallbackQuery):
    game_type = call.data.split("_")[1]
    names = {"football": "футбол", "basketball": "баскетбол", "darts": "дартс"}
    name = names.get(game_type, "игру")
    await call.message.answer(f"Чтобы сыграть, напишите в чат: `{name} [ставка]`\nНапример: `{name} 10`", parse_mode="Markdown")
    await call.answer()

async def process_game_bet(message: Message):
    parts = message.text.strip().lower().split()
    if len(parts) != 2:
        return

    game_name, bet_str = parts[0], parts[1]
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
            win_amount = int(bet * random.uniform(1.5, 3.0))
        elif bet < 100:
            win_amount = int(bet * random.uniform(1.2, 1.8))
        else:
            win_amount = int(bet * random.uniform(0.4, 0.6))
            
        win_amount = max(1, win_amount)
        await update_balance(message.from_user.id, win_amount)
        new_user = await get_user(message.from_user.id)
        
        await message.answer(
            f"🎉 **ПОБЕДА!**\n"
            f"Вам начислено: +{win_amount} 💎 Чекушек!\n"
            f"Ваш баланс: {new_user['balance']} 💎",
            parse_mode="Markdown"
        )
    else:
        new_user = await get_user(message.from_user.id)
        await message.answer(
            f"❌ **ПРОИГРЫШ!**\n"
            f"Вы потеряли: {bet} 💎 Чекушек.\n"
            f"Ваш баланс: {new_user['balance']} 💎",
            parse_mode="Markdown"
        )

# === ЗАПУСК ===
async def main():
    # Восстанавливаем последнюю сохранённую БД.
    await download_db_from_github()
    await init_db()

    # Если БД только что создана — сразу сохраняем её в GitHub.
    await upload_db_to_github()

    await start_http_server()
    # Дополнительная страховка раз в 10 минут.
    asyncio.create_task(github_sync_task())

    print("Бот запущен. Изменения БД сохраняются в GitHub сразу после записи.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
