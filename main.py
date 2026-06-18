import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ========== CONFIG ==========
TOKEN = ""  # ВСТАВЬ ТОКЕН СЮДА
ADMIN_IDS = {5932305819}
BOT_USERNAME = ""  # @username бота (без @)
REF_BONUS = 50
MAILING_RATE = 100

# ========== DB ==========
DB_PATH = "sugar.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            ref_code TEXT UNIQUE,
            referred_by INTEGER DEFAULT NULL,
            has_godka INTEGER DEFAULT 0,
            godka_until TEXT DEFAULT NULL,
            banned INTEGER DEFAULT 0,
            ban_notified INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT (datetime('now')),
            timing_minutes INTEGER DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS chats (
            chat_id TEXT PRIMARY KEY,
            chat_title TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ad_text (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            text TEXT DEFAULT '',
            media_file_id TEXT DEFAULT '',
            media_type TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS auto_reply (
            user_id INTEGER PRIMARY KEY,
            reply_text TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO config (key, value) VALUES ('ref_bonus', '50');
    """)
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ========== STATES ==========
class AdTextStates(StatesGroup):
    waiting_text = State()
    waiting_media = State()


class AutoReplyStates(StatesGroup):
    waiting_reply_text = State()


class MailingStates(StatesGroup):
    waiting_confirm = State()
    waiting_message = State()


class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_ban_user_id = State()
    waiting_unban_user_id = State()
    waiting_godka_user_id = State()
    waiting_godka_days = State()
    waiting_ungodka_user_id = State()
    waiting_ref_bonus = State()


class TimingStates(StatesGroup):
    waiting_custom_minutes = State()


# ========== KEYBOARDS ==========
def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="💬 Чаты", callback_data="chats")],
        [InlineKeyboardButton(text="📝 Текст", callback_data="ad_text")],
        [InlineKeyboardButton(text="🤖 Автоответ", callback_data="auto_reply")],
        [InlineKeyboardButton(text="⏰ Тайминг", callback_data="timing")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ]
    if user_id in ADMIN_IDS:
        kb.append([InlineKeyboardButton(text="🛡 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ]
    )


def chats_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="➕ Добавить чат", callback_data="add_chat")],
        [InlineKeyboardButton(text="📋 Добавить все чаты", callback_data="add_all_chats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="🔨 Забанить", callback_data="admin_ban")],
        [InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="⭐ Выдать годку", callback_data="admin_give_godka")],
        [InlineKeyboardButton(text="🚫 Забрать годку", callback_data="admin_take_godka")],
        [InlineKeyboardButton(text="💰 Реф бонус", callback_data="admin_ref_bonus")],
        [InlineKeyboardButton(text="ℹ️ Инфа о пользователе", callback_data="admin_user_info")],
        [InlineKeyboardButton(text="📊 Инфа о боте", callback_data="admin_bot_info")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def mailing_percent_kb() -> InlineKeyboardMarkup:
    kb = []
    for pct in [25, 40, 50, 70, 100]:
        kb.append([InlineKeyboardButton(text=f"{pct}%", callback_data=f"mailing_{pct}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def timing_kb() -> InlineKeyboardMarkup:
    kb = []
    times = [3, 5, 10, 15, 20, 25, 30, 45, 50, 55, 60]
    for i in range(0, len(times), 2):
        row = []
        for t in times[i:i+2]:
            row.append(InlineKeyboardButton(text=f"{t} мин", callback_data=f"timing_{t}"))
        kb.append(row)
    kb.append([InlineKeyboardButton(text="⏱ Своё время", callback_data="timing_custom")])
    kb.append([InlineKeyboardButton(text="🚫 Отключить", callback_data="timing_off")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ========== BOT INIT ==========
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ========== HELPERS ==========
def generate_ref_code(user_id: int) -> str:
    return f"ref{user_id}"


def get_or_create_user(user_id: int, username: str = "", referred_by: int = None) -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        conn.close()
        return dict(row)
    ref_code = generate_ref_code(user_id)
    cur.execute(
        "INSERT INTO users (user_id, username, ref_code, referred_by) VALUES (?, ?, ?, ?)",
        (user_id, username, ref_code, referred_by),
    )
    bonus = get_ref_bonus()
    if referred_by and bonus > 0:
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (bonus, referred_by))
    conn.commit()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row)


def get_ref_bonus() -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM config WHERE key = 'ref_bonus'")
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row else 50


def set_ref_bonus(value: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('ref_bonus', ?)", (str(value),))
    conn.commit()
    conn.close()


def is_banned(user_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row and row[0] == 1


def has_godka(user_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT has_godka, godka_until FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] == 0:
        return False
    if row[1]:
        until = datetime.fromisoformat(row[1])
        if datetime.now() > until:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE users SET has_godka = 0, godka_until = NULL WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            return False
    return True


async def send_ad_to_chat(bot: Bot, chat_id: str, ad_text: str, media_file_id: str, media_type: str, has_godka: bool):
    try:
        if media_file_id:
            if media_type == "photo":
                await bot.send_photo(chat_id=chat_id, photo=media_file_id, caption=ad_text)
            elif media_type == "video":
                await bot.send_video(chat_id=chat_id, video=media_file_id, caption=ad_text)
            elif media_type == "document":
                await bot.send_document(chat_id=chat_id, document=media_file_id, caption=ad_text)
            else:
                await bot.send_message(chat_id=chat_id, text=ad_text)
        else:
            await bot.send_message(chat_id=chat_id, text=ad_text)
    except Exception as e:
        logging.warning(f"Failed to send to {chat_id}: {e}")


async def auto_mailing_task():
    while True:
        await asyncio.sleep(60)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT user_id, timing_minutes FROM users WHERE timing_minutes IS NOT NULL AND banned = 0")
        users = cur.fetchall()
        conn.close()
        
        for user_row in users:
            user_id = user_row[0]
            timing = user_row[1]
            
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM ad_text WHERE id = 1")
            ad = cur.fetchone()
            cur.execute("SELECT * FROM chats")
            chats = cur.fetchall()
            conn.close()
            
            if not ad or not ad["text"] or not chats:
                continue
            
            now = datetime.now()
            if now.minute % timing == 0:
                user_has_godka = has_godka(user_id)
                for chat in chats:
                    await send_ad_to_chat(
                        bot,
                        chat["chat_id"],
                        ad["text"],
                        ad["media_file_id"],
                        ad["media_type"],
                        user_has_godka,
                    )
                    await asyncio.sleep(1 / MAILING_RATE)


# ========== START / CAPTURE ==========
@dp.message(Command("start"))
async def start_handler(message: Message, command: CommandObject):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    if is_banned(user_id):
        await message.answer("❌ Вы заблокированы.\nОбжаловать: @SUGARsupportBot")
        return
    referred_by = None
    if command.args and command.args.startswith("ref"):
        try:
            ref_uid = int(command.args.replace("ref", ""))
            if ref_uid != user_id:
                referred_by = ref_uid
        except:
            pass
    user = get_or_create_user(user_id, username, referred_by)
    await message.answer(
        f"👋 Добро пожаловать!\n\n"
        f"Твой ID: <code>{user_id}</code>\n"
        f"Баланс: {user['balance']}₽\n"
        f"Реф. ссылка: https://t.me/{BOT_USERNAME}?start={user['ref_code']}\n\n"
        "➕ Приведи друга и получи 50₽",
        reply_markup=main_keyboard(user_id),
    )


@dp.message(F.text)
async def message_handler(message: Message):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer("❌ Вы заблокированы.\nОбжаловать: @SUGARsupportBot")
        return
    get_or_create_user(user_id, message.from_user.username or "")
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT reply_text FROM auto_reply WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        await message.answer(row[0])


# ========== CALLBACKS ==========
@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.message.answer("❌ Вы заблокированы.\nОбжаловать: @SUGARsupportBot")
        return
    await callback.message.edit_text(
        "👋 Главное меню", reply_markup=main_keyboard(user_id)
    )
    await callback.answer()


@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    user = dict(row)
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user['ref_code']}"
    
    godka_status = "❌"
    if user['has_godka']:
        if user['godka_until']:
            until = datetime.fromisoformat(user['godka_until'])
            godka_status = f"✅ (до {until.strftime('%d.%m.%Y %H:%M')})"
        else:
            godka_status = "✅"
    
    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Username: @{user['username']}\n"
        f"💰 Баланс: <b>{user['balance']}₽</b>\n"
        f"⭐ Годка: {godka_status}\n"
        f"🔗 Реф. ссылка:\n<code>{ref_link}</code>\n"
        f"👥 Приведено: 0",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "chats")
async def chats_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "📊 Управление чатами для авто-рассылки", reply_markup=chats_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "add_chat")
async def add_chat_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "📌 Отправь @username или ID чата для добавления\n"
        "Пример: @mychat или -1001234567890",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message()
async def add_chat_text_handler(message: Message):
    user_id = message.from_user.id
    if is_banned(user_id):
        return
    text = message.text.strip()
    chat_id = text
    if text.startswith("@"):
        try:
            chat = await bot.get_chat(text)
            chat_id = str(chat.id)
            chat_title = chat.title or text
        except:
            await message.answer("❌ Не удалось найти чат. Убедись что бот добавлен в чат.")
            return
    else:
        chat_title = text

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO chats (chat_id, chat_title) VALUES (?, ?)",
        (chat_id, chat_title),
    )
    conn.commit()
    conn.close()
    await message.answer(f"✅ Чат {text} добавлен", reply_markup=chats_kb())


@dp.callback_query(F.data == "add_all_chats")
async def add_all_chats_handler(callback: CallbackQuery):
    try:
        updates = await bot.get_updates()
        added = 0
        for update in updates:
            if update.my_chat_member:
                chat = update.my_chat_member.chat
                if chat.type in ("group", "supergroup"):
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT OR IGNORE INTO chats (chat_id, chat_title) VALUES (?, ?)",
                        (str(chat.id), chat.title or str(chat.id)),
                    )
                    conn.commit()
                    conn.close()
                    added += 1
        await callback.message.edit_text(
            f"✅ Добавлено чатов: {added}", reply_markup=chats_kb()
        )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {e}\nУбедись, что бот админ в чатах",
            reply_markup=chats_kb(),
        )
    await callback.answer()


@dp.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "❓ <b>Помощь</b>\n\n"
        "👤 Поговорить с владельцем: @SUGARsupportBot\n"
        "⭐ Купить годку: @SUGARsupportBot\n\n"
        "<b>Годка</b> убирает подпись 'отправлено через @...' при рассылке",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


# ========== TIMING ==========
@dp.callback_query(F.data == "timing")
async def timing_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT timing_minutes FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    current = "Отключен"
    if row and row[0]:
        current = f"{row[0]} минут"
    
    await callback.message.edit_text(
        f"⏰ <b>Тайминг рассылки</b>\n\n"
        f"Текущий: {current}\n\n"
        "Выбери раз в какое время будет происходить рассылка",
        reply_markup=timing_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("timing_"))
async def timing_set_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = callback.data.split("_")[1]
    
    if data == "off":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE users SET timing_minutes = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await callback.message.edit_text(
            "✅ Тайминг отключен",
            reply_markup=back_to_main_kb(),
        )
        await callback.answer()
        return
    
    if data == "custom":
        await state.set_state(TimingStates.waiting_custom_minutes)
        await callback.message.edit_text(
            "⏱ Отправь количество минут (целое число от 1 до 1440)",
            reply_markup=back_to_main_kb(),
        )
        await callback.answer()
        return
    
    minutes = int(data)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET timing_minutes = ? WHERE user_id = ?", (minutes, user_id))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(
        f"✅ Тайминг установлен: раз в {minutes} минут",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(TimingStates.waiting_custom_minutes, F.text)
async def timing_custom_process(message: Message, state: FSMContext):
    try:
        minutes = int(message.text.strip())
        if minutes < 1 or minutes > 1440:
            raise ValueError
    except:
        await message.answer("❌ Введи целое число от 1 до 1440")
        return
    
    user_id = message.from_user.id
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET timing_minutes = ? WHERE user_id = ?", (minutes, user_id))
    conn.commit()
    conn.close()
    
    await message.answer(
        f"✅ Тайминг установлен: раз в {minutes} минут",
        reply_markup=main_keyboard(user_id),
    )
    await state.clear()


# ========== ADMIN PANEL ==========
@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🛡 <b>Админ-панель</b>", reply_markup=admin_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_mailing")
async def admin_mailing_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "📢 Выбери процент пользователей для рассылки",
        reply_markup=mailing_percent_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("mailing_"))
async def mailing_percent_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    pct = int(callback.data.split("_")[1])
    await state.update_data(mailing_pct=pct)
    await state.set_state(MailingStates.waiting_message)
    await callback.message.edit_text(
        f"📢 Отправь сообщение (текст + опционально медиа) для рассылки на {pct}% пользователей",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(MailingStates.waiting_message, F.text | F.photo | F.video | F.document)
async def mailing_message_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    pct = data["mailing_pct"]
    media_file_id = ""
    media_type = ""
    text = message.html_text or ""
    if message.photo:
        media_file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        media_file_id = message.video.file_id
        media_type = "video"
    elif message.document:
        media_file_id = message.document.file_id
        media_type = "document"
    await state.update_data(mailing_text=text, mailing_media=media_file_id, mailing_media_type=media_type)
    await state.set_state(MailingStates.waiting_confirm)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="mailing_confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")],
        ]
    )
    await message.answer(
        f"📢 <b>Предпросмотр рассылки</b>\n\n"
        f"Процент: {pct}%\n"
        f"Текст: {text[:100]}...\n"
        f"Медиа: {'✅' if media_file_id else '❌'}",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "mailing_confirm", MailingStates.waiting_confirm)
async def mailing_confirm_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    pct = data["mailing_pct"]
    text = data.get("mailing_text", "")
    media_file_id = data.get("mailing_media", "")
    media_type = data.get("mailing_media_type", "")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE banned = 0")
    all_users = [r[0] for r in cur.fetchall()]
    conn.close()

    import random
    selected = random.sample(all_users, max(1, len(all_users) * pct // 100))

    await callback.message.edit_text(f"📢 Запуск рассылки на {len(selected)} пользователей...")
    await callback.answer()

    sent = 0
    for uid in selected:
        try:
            if media_file_id:
                if media_type == "photo":
                    await bot.send_photo(chat_id=uid, photo=media_file_id, caption=text)
                elif media_type == "video":
                    await bot.send_video(chat_id=uid, video=media_file_id, caption=text)
                elif media_type == "document":
                    await bot.send_document(chat_id=uid, document=media_file_id, caption=text)
                else:
                    await bot.send_message(chat_id=uid, text=text)
            else:
                await bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(1 / MAILING_RATE)

    await callback.message.edit_text(
        f"✅ Рассылка завершена\nОтправлено: {sent}/{len(selected)}",
        reply_markup=admin_kb(),
    )
    await state.clear()


@dp.callback_query(F.data == "admin_ban")
async def admin_ban_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_ban_user_id)
    await callback.message.edit_text(
        "🔨 Отправь ID пользователя для бана",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdminStates.waiting_ban_user_id, F.text)
async def admin_ban_process(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except:
        await message.answer("❌ Неверный ID")
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (target_id,))
    conn.commit()
    conn.close()
    try:
        await bot.send_message(
            target_id,
            "❌ <b>Вы заблокированы</b>\nОбжаловать: @SUGARsupportBot",
        )
    except:
        pass
    await message.answer(f"✅ Пользователь {target_id} забанен", reply_markup=admin_kb())
    await state.clear()


@dp.callback_query(F.data == "admin_unban")
async def admin_unban_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_unban_user_id)
    await callback.message.edit_text(
        "✅ Отправь ID пользователя для разбана",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdminStates.waiting_unban_user_id, F.text)
async def admin_unban_process(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except:
        await message.answer("❌ Неверный ID")
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (target_id,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Пользователь {target_id} разбанен", reply_markup=admin_kb())
    await state.clear()


@dp.callback_query(F.data == "admin_give_godka")
async def admin_give_godka_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_godka_user_id)
    await callback.message.edit_text(
        "⭐ Отправь ID пользователя для выдачи годки",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdminStates.waiting_godka_user_id, F.text)
async def admin_give_godka_user_process(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except:
        await message.answer("❌ Неверный ID")
        return
    await state.update_data(godka_target=target_id)
    await state.set_state(AdminStates.waiting_godka_days)
    await message.answer(
        "⏰ Отправь количество дней для выдачи годки\n"
        "Или 0 для бессрочной годки",
        reply_markup=back_to_main_kb(),
    )


@dp.message(AdminStates.waiting_godka_days, F.text)
async def admin_give_godka_days_process(message: Message, state: FSMContext):
    try:
        days = int(message.text.strip())
        if days < 0:
            raise ValueError
    except:
        await message.answer("❌ Введи целое положительное число или 0")
        return
    
    data = await state.get_data()
    target_id = data["godka_target"]
    
    conn = get_db()
    cur = conn.cursor()
    if days == 0:
        cur.execute("UPDATE users SET has_godka = 1, godka_until = NULL WHERE user_id = ?", (target_id,))
        msg = f"✅ Пользователю {target_id} выдана бессрочная годка"
    else:
        until = datetime.now() + timedelta(days=days)
        cur.execute(
            "UPDATE users SET has_godka = 1, godka_until = ? WHERE user_id = ?",
            (until.isoformat(), target_id),
        )
        msg = f"✅ Пользователю {target_id} выдана годка на {days} дней (до {until.strftime('%d.%m.%Y %H:%M')})"
    conn.commit()
    conn.close()
    
    await message.answer(msg, reply_markup=admin_kb())
    await state.clear()


@dp.callback_query(F.data == "admin_take_godka")
async def admin_take_godka_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_ungodka_user_id)
    await callback.message.edit_text(
        "🚫 Отправь ID пользователя для забора годки",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdminStates.waiting_ungodka_user_id, F.text)
async def admin_take_godka_process(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except:
        await message.answer("❌ Неверный ID")
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET has_godka = 0, godka_until = NULL WHERE user_id = ?", (target_id,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ У пользователя {target_id} забрана годка", reply_markup=admin_kb())
    await state.clear()


@dp.callback_query(F.data == "admin_user_info")
async def admin_user_info_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_user_id)
    await callback.message.edit_text(
        "ℹ️ Отправь ID пользователя для получения информации",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdminStates.waiting_user_id, F.text)
async def admin_user_info_process(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except:
        await message.answer("❌ Неверный ID")
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (target_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await message.answer("❌ Пользователь не найден", reply_markup=admin_kb())
        await state.clear()
        return
    user = dict(row)
    conn.close()
    
    godka_status = "❌"
    if user['has_godka']:
        if user['godka_until']:
            until = datetime.fromisoformat(user['godka_until'])
            godka_status = f"✅ (до {until.strftime('%d.%m.%Y %H:%M')})"
        else:
            godka_status = "✅ (бессрочно)"
    
    await message.answer(
        f"ℹ️ <b>Информация о пользователе</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Username: @{user['username']}\n"
        f"💰 Баланс: {user['balance']}₽\n"
        f"⭐ Годка: {godka_status}\n"
        f"🚫 Бан: {'✅' if user['banned'] else '❌'}\n"
        f"📅 Зарегистрирован: {user['joined_at']}",
        reply_markup=admin_kb(),
    )
    await state.clear()


@dp.callback_query(F.data == "admin_ref_bonus")
async def admin_ref_bonus_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    current = get_ref_bonus()
    await state.set_state(AdminStates.waiting_ref_bonus)
    await callback.message.edit_text(
        f"💰 Текущий бонус за реферала: {current}₽\n\n"
        "Отправь новую сумму (целое число)",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdminStates.waiting_ref_bonus, F.text)
async def admin_ref_bonus_process(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    try:
        value = int(message.text.strip())
        if value < 0:
            raise ValueError
    except:
        await message.answer("❌ Введи целое положительное число")
        return
    set_ref_bonus(value)
    await message.answer(f"✅ Бонус за реферала изменён на {value}₽", reply_markup=admin_kb())
    await state.clear()


@dp.callback_query(F.data == "admin_bot_info")
async def admin_bot_info_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE banned = 1")
    banned = cur.fetchone()[0]
    active = total - banned
    conn.close()
    await callback.message.edit_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"🚫 Заблокировано: {banned}\n"
        f"✅ Активных (не удалили): {active}",
        reply_markup=admin_kb(),
    )
    await callback.answer()


# ========== AD TEXT ==========
@dp.callback_query(F.data == "ad_text")
async def ad_text_handler(callback: CallbackQuery):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ad_text WHERE id = 1")
    row = cur.fetchone()
    conn.close()
    current_text = row["text"] if row else ""
    has_media = "✅" if (row and row["media_file_id"]) else "❌"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Написать текст", callback_data="ad_text_write")],
            [InlineKeyboardButton(text="🖼 Прикрепить медиа", callback_data="ad_text_media")],
            [InlineKeyboardButton(text="💾 Сохранить", callback_data="ad_text_save")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ]
    )
    await callback.message.edit_text(
        f"📝 <b>Текст рекламы</b>\n\n"
        f"Текущий текст:\n{current_text or '(пусто)'}\n\n"
        f"Медиа: {has_media}",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data == "ad_text_write")
async def ad_text_write_handler(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdTextStates.waiting_text)
    await callback.message.edit_text(
        "✏️ Отправь новый текст рекламы",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdTextStates.waiting_text, F.text)
async def ad_text_write_process(message: Message, state: FSMContext):
    text = message.html_text or message.text
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ad_text (id, text) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET text = ?",
        (text, text),
    )
    conn.commit()
    conn.close()
    await message.answer("✅ Текст рекламы сохранён", reply_markup=back_to_main_kb())
    await state.clear()


@dp.callback_query(F.data == "ad_text_media")
async def ad_text_media_handler(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdTextStates.waiting_media)
    await callback.message.edit_text(
        "🖼 Отправь фото, видео или файл для прикрепления к рекламе",
        reply_markup=back_to_main_kb(),
    )
    await callback.answer()


@dp.message(AdTextStates.waiting_media, F.photo | F.video | F.document)
async def ad_text_media_process(message: Message, state: FSMContext):
    media_file_id = ""
    media_type = ""
    if message.photo:
        media_file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        media_file_id = message.video.file_id
        media_type = "video"
    elif message.document:
        media_file_id = message.document.file_id
        media_type = "document"
    await state.clear()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ad_text (id, text, media_file_id, media_type) VALUES (1, '', ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET media_file_id = ?, media_type = ?",
        (media_file_id, media_type, media_file_id, media_type),
    )
    conn.commit()
    conn.close()
    await message.answer("✅ Медиа прикреплено", reply_markup=back_to_main_kb())


@dp.callback_query(F.data == "ad_text_save")
async def ad_text_save_handler(callback: CallbackQuery):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ad_text WHERE id = 1")
    row = cur.fetchone()
    if not row or not row["text"]:
        await callback.answer("❌ Сначала напиши текст рекламы", show_alert=True)
        conn.close()
        return
    await callback.answer("✅ Текст рекламы сохранён", show_alert=True)
    conn.close()


# ========== AUTO REPLY ==========
@dp.callback_query(F.data == "auto_reply")
async def auto_reply_handler(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🤖 Пришли текст, который бот будет автоматически отвечать людям в личку",
        reply_markup=back_to_main_kb(),
    )
    await state.set_state(AutoReplyStates.waiting_reply_text)
    await callback.answer()


@dp.message(AutoReplyStates.waiting_reply_text, F.text)
async def auto_reply_save(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO auto_reply (user_id, reply_text) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET reply_text = ?",
        (user_id, text, text),
    )
    conn.commit()
    conn.close()
    await message.answer("✅ Автоответ сохранён", reply_markup=main_keyboard(user_id))
    await state.clear()


# ========== MAIN ==========
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    asyncio.create_task(auto_mailing_task())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
