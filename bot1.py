import asyncio
import logging
import traceback
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Union
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

import aiosqlite

# ========== НАСТРОЙКА ШЛЯХУ ТА ЗМІННИХ ОТЧЕННЯ ==========
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# ========== НАСТРОЙКА ЛОГУВАННЯ ==========
class ErrorLogger:
    def __init__(self):
        self.setup_logging()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('bot_errors.log', encoding='utf-8', mode='a'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def log_error(self, context: str, error: Exception, user_id: int = None, additional_info: str = None):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_info = f" | Користувач: {user_id}" if user_id else ""
        info = f" | Інфо: {additional_info}" if additional_info else ""
        
        error_msg = f"[ПОМИЛКА] {context}: {str(error)[:200]}{user_info}{info} | Час: {timestamp}"
        
        with open('bot_errors.log', 'a', encoding='utf-8') as f:
            f.write(error_msg + '\n')
            if hasattr(error, '__traceback__'):
                traceback.print_exception(type(error), error, error.__traceback__, file=f)
        
        self.logger.error(error_msg)
        asyncio.create_task(self.log_to_db(user_id, context, str(error)[:500]))

    async def log_to_db(self, user_id: int, action_type: str, details: str):
        try:
            if user_id is None:
                user_id = 0
            async with aiosqlite.connect('kyivstar_bot.db') as db:
                await db.execute('''
                    INSERT INTO action_logs (user_id, action_type, action_details)
                    VALUES (?, ?, ?)
                ''', (user_id, f"ERROR_{action_type}", details))
                await db.commit()
        except Exception as e:
            self.logger.error(f"Помилка при запису лога в БД: {e}")

error_logger = ErrorLogger()

# ========== КОНФІГУРАЦІЯ БОТА ==========
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "your_secure_password_change_me")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
    raise ValueError("❌ BOT_TOKEN не знайдено! Встановіть змінну оточення BOT_TOKEN у файлі .env")

# ========== СТАНИ FSM ==========
class AdminStates(StatesGroup):
    login = State()
    add_card = State()
    edit_card = State()
    edit_section = State()
    broadcast = State()

# ========== ІНІЦІАЛІЗАЦІЯ ==========
router = Router()
admin_router = Router()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
dp.include_router(router)
dp.include_router(admin_router)

# ========== КЛАВІАТУРИ ==========
def get_main_reply_keyboard():
    keyboard = [
        [KeyboardButton(text="📂 Відділи"), KeyboardButton(text="💰 Тарифи")],
        [KeyboardButton(text="💪 Супер сили"), KeyboardButton(text="🌍 Роумінг")],
        [KeyboardButton(text="💬 Допомога"), KeyboardButton(text="⚙️ Налаштування")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_settings_keyboard(is_admin_user: bool = False):
    keyboard = []
    if is_admin_user:
        keyboard.append([KeyboardButton(text="👨‍💼 Адмін-панель")])
    
    keyboard.extend([
        [KeyboardButton(text="🆔 Мій ID")],
        [KeyboardButton(text="🔙 Назад")]
    ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="📂 Відділи (Додати / Редагувати)", callback_data="admin_sections_menu")],
        [InlineKeyboardButton(text="💰 Тарифи", callback_data="admin_view_cards:tariff"),
         InlineKeyboardButton(text="💪 Супер сили", callback_data="admin_view_cards:super_power")],
        [InlineKeyboardButton(text="🌍 Роумінг", callback_data="admin_view_cards:roaming")],
        [InlineKeyboardButton(text="📢 Розсилка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 Вийти з адмін-панелі", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати дію", callback_data="cancel_action")]
    ])

# ========== БАЗА ДАНИХ ==========
class DatabaseManager:
    def __init__(self):
        self.db_name = 'kyivstar_bot.db'

    async def init_db(self):
        try:
            async with aiosqlite.connect(self.db_name) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await self.create_tables(db)
                await self.create_indexes(db)
                await self.add_default_data(db)
                await db.commit()
                error_logger.logger.info("✅ База даних ініціалізована успішно")
        except Exception as e:
            error_logger.log_error("Ініціалізація БД", e)
            raise

    async def create_tables(self, db):
        tables = [
            '''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_blocked BOOLEAN DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT, content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1, display_order INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, role TEXT DEFAULT 'admin', last_login TIMESTAMP
            )''',
            '''CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY, value TEXT, description TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''',
            '''CREATE TABLE IF NOT EXISTS tariff_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, price TEXT NOT NULL, description TEXT,
                image_url TEXT, image_file_id TEXT, card_type TEXT NOT NULL CHECK(card_type IN ('tariff', 'super_power', 'roaming')),
                is_active BOOLEAN DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                display_order INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action_type TEXT NOT NULL,
                action_details TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        ]
        for table_sql in tables:
            await db.execute(table_sql)

    async def create_indexes(self, db):
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_cards_type_active ON tariff_cards(card_type, is_active, display_order)",
            "CREATE INDEX IF NOT EXISTS idx_sections_active ON sections(is_active, display_order)"
        ]
        for index in indexes:
            await db.execute(index)

    async def add_default_data(self, db):
        settings = [('welcome_message', '👋 Ласкаво просимо до бота Київстар! Оберіть розділ:', 'Привітальне повідомлення')]
        for key, value, desc in settings:
            await db.execute('INSERT OR IGNORE INTO bot_settings (key, value, description) VALUES (?, ?, ?)', (key, value, desc))
        
        test_cards = [
            ('Стандарт', '150 грн/міс', 'Базовий пакет послуг', '📱', 'tariff'),
            ('Турбо-інтернет', '75 грн/тиждень', 'Швидкість до 200 Мбіт/с', '⚡', 'super_power'),
            ('Європа Плюс', '400 грн/міс', '30 ГБ в Європі', '🇪🇺', 'roaming')
        ]
        for title, price, desc, emoji, card_type in test_cards:
            await db.execute(
                'INSERT OR IGNORE INTO tariff_cards (title, price, description, image_url, card_type) VALUES (?, ?, ?, ?, ?)',
                (title, price, desc, emoji, card_type)
            )

db_manager = DatabaseManager()

# ========== УТИЛІТИ БАЗИ ДАНИХ ==========
async def execute_query(query: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False):
    try:
        async with aiosqlite.connect(db_manager.db_name) as db:
            cursor = await db.execute(query, params)
            if fetch_one:
                result = await cursor.fetchone()
            elif fetch_all:
                result = [row async for row in cursor]
            else:
                result = cursor.lastrowid
            await db.commit()
            return result
    except Exception as e:
        error_logger.log_error("SQL запит", e, additional_info=f"Query: {query[:100]}")
        return None

async def add_user(user_id: int, username: str, first_name: str, last_name: str = None):
    await execute_query(
        'INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_activity) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)',
        (user_id, username, first_name, last_name)
    )

async def update_user_activity(user_id: int):
    await execute_query('UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?', (user_id,))

async def get_all_users():
    result = await execute_query('SELECT user_id FROM users WHERE is_blocked = 0', fetch_all=True)
    return [row[0] for row in result] if result else []

async def is_admin(user_id: int) -> bool:
    result = await execute_query('SELECT 1 FROM admins WHERE user_id = ?', (user_id,), fetch_one=True)
    return result is not None

async def add_admin(user_id: int, username: str, first_name: str, last_name: str):
    await execute_query(
        'INSERT OR REPLACE INTO admins (user_id, username, first_name, last_name, last_login) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)',
        (user_id, username, first_name, last_name)
    )

async def get_setting(key: str):
    result = await execute_query('SELECT value FROM bot_settings WHERE key = ?', (key,), fetch_one=True)
    return result[0] if result else None

async def get_tariff_cards(card_type: str):
    query = '''
        SELECT id, title, price, description, image_url, image_file_id, card_type
        FROM tariff_cards WHERE card_type = ? AND is_active = 1
        ORDER BY display_order, id
    '''
    return await execute_query(query, (card_type,), fetch_all=True)

async def get_tariff_card(card_id: int):
    query = 'SELECT id, title, price, description, image_url, image_file_id, card_type FROM tariff_cards WHERE id = ?'
    return await execute_query(query, (card_id,), fetch_one=True)

async def add_tariff_card(title: str, price: str, description: str, image_url: str, image_file_id: str, card_type: str):
    query = '''
        INSERT INTO tariff_cards (title, price, description, image_url, image_file_id, card_type)
        VALUES (?, ?, ?, ?, ?, ?)
    '''
    return await execute_query(query, (title.strip(), price.strip(), description.strip() if description else '', image_url or '', image_file_id or '', card_type))

async def update_tariff_card(card_id: int, **kwargs):
    if not kwargs:
        return True
    fields, params = [], []
    field_mapping = {
        'title': ('title', lambda x: str(x).strip()),
        'price': ('price', lambda x: str(x).strip()),
        'description': ('description', lambda x: str(x).strip() if x else ''),
        'image_url': ('image_url', lambda x: str(x) if x else ''),
        'image_file_id': ('image_file_id', lambda x: str(x) if x else ''),
        'is_active': ('is_active', lambda x: 1 if x else 0)
    }
    for key, value in kwargs.items():
        if key in field_mapping:
            db_field, processor = field_mapping[key]
            fields.append(f"{db_field} = ?")
            params.append(processor(value))
    
    if not fields:
        return False
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(card_id)
    
    query = f"UPDATE tariff_cards SET {', '.join(fields)} WHERE id = ?"
    return (await execute_query(query, tuple(params))) is not None

async def delete_tariff_card(card_id: int):
    return (await execute_query('UPDATE tariff_cards SET is_active = 0 WHERE id = ?', (card_id,))) is not None

async def get_sections():
    return await execute_query('SELECT id, name, description, content FROM sections WHERE is_active = 1 ORDER BY display_order, id', fetch_all=True)

async def get_section(section_id: int):
    return await execute_query('SELECT id, name, description, content FROM sections WHERE id = ?', (section_id,), fetch_one=True)

async def update_section(section_id: int, name: str, description: str, content: str):
    return (await execute_query(
        'UPDATE sections SET name = ?, description = ?, content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (name, description, content, section_id)
    )) is not None

async def delete_section(section_id: int):
    return (await execute_query('UPDATE sections SET is_active = 0 WHERE id = ?', (section_id,))) is not None

# ========== УТИЛІТИ ВІДПРАВКИ ==========
async def safe_send_message(chat_id: int, text: str, reply_markup=None, parse_mode: str = 'HTML', user_id: int = None):
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except Exception as e:
        error_logger.log_error("Відправка повідомлення", e, user_id, f"Текст: {text[:50]}")
        return False

async def safe_edit_message(target: Union[Message, CallbackQuery], text: str, reply_markup=None, parse_mode: str = 'HTML'):
    try:
        msg = target.message if isinstance(target, CallbackQuery) else target
        if msg.photo:
            await msg.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            await msg.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        return True
        
    except Exception as e:
        error_logger.log_error("Редагування повідомлення", e)
        try:
            await msg.delete()
            if msg.photo:
                await msg.answer_photo(
                    photo=msg.photo[-1].file_id,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            else:
                await msg.answer(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
        except Exception as fallback_e:
            error_logger.log_error("Fallback відправка замість редагування", fallback_e)
            
        return False

# ========== ВІДОБРАЖЕННЯ КАРТОК ==========
async def display_tariff_cards(target: Union[Message, CallbackQuery], card_type: str, title: str, user_id: int):
    try:
        await update_user_activity(user_id)
        cards = await get_tariff_cards(card_type)
        
        if not cards:
            text = f"📭 {title} відсутні."
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 До адмін-панелі", callback_data="back_to_admin")
            ]])
            if isinstance(target, CallbackQuery):
                await safe_edit_message(target, text, reply_markup)
            else:
                await safe_send_message(target.chat.id, text, reply_markup, user_id=user_id)
            return

        header_text = f"📋 <b>Список: {title}</b>\n(Всього: {len(cards)})"
        if isinstance(target, CallbackQuery):
            await safe_edit_message(target, header_text)
        else:
            await safe_send_message(target.chat.id, header_text, user_id=user_id)

        for card in cards:
            await display_single_card(target, card, user_id)

    except Exception as e:
        error_logger.log_error(f"Показати {title}", e, user_id)

async def display_single_card(target: Union[Message, CallbackQuery], card: tuple, user_id: int):
    try:
        if len(card) >= 7:
            card_id, title, price, description, image_url, image_file_id, card_type = card[:7]
        else:
            card_id, title, price, description, image_url, image_file_id = card[:6]
            card_type = 'tariff'

        text = f"📌 <b>{title}</b>\n\n💰 <b>Ціна:</b> {price}\n📝 <b>Опис:</b> {description or 'Немає опису'}"

        keyboard = []
        if await is_admin(user_id):
            keyboard.append([
                InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"admin_edit_card:{card_id}"),
                InlineKeyboardButton(text="🗑️ Видалити", callback_data=f"admin_delete_card:{card_id}")
            ])
        
        keyboard.append([
            InlineKeyboardButton(text="🔙 До списку", callback_data=f"admin_view_cards:{card_type}")
        ])

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        msg_target = target.message if isinstance(target, CallbackQuery) else target

        if image_file_id:
            try:
                await msg_target.answer_photo(photo=image_file_id, caption=text, reply_markup=reply_markup, parse_mode='HTML')
            except Exception as photo_error:
                error_logger.log_error("Відправка фото картки", photo_error, user_id, f"Card {card_id}")
                await msg_target.answer(text=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            if image_url:
                text = f"{image_url}\n\n{text}"
            await msg_target.answer(text=text, reply_markup=reply_markup, parse_mode='HTML')

    except Exception as e:
        error_logger.log_error("display_single_card", e, user_id)

# ========== ОСНОВНІ ХЕНДЛЕРИ ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    try:
        user = message.from_user
        await add_user(user.id, user.username, user.first_name, user.last_name)
        welcome = await get_setting('welcome_message') or '👋 Ласкаво просимо!'
        await message.answer(f"{welcome}\n\nПривіт, {user.first_name}!", reply_markup=get_main_reply_keyboard())
    except Exception as e:
        error_logger.log_error("/start", e, message.from_user.id)
        await message.answer("❌ Помилка при запуску бота.")

@router.message(F.text == "🔙 Назад")
async def back_to_main(message: Message):
    await safe_send_message(message.chat.id, "🏠 <b>Головне меню</b>", reply_markup=get_main_reply_keyboard(), user_id=message.from_user.id)

@router.message(F.text == "⚙️ Налаштування")
async def settings_menu(message: Message):
    is_admin_user = await is_admin(message.from_user.id)
    await safe_send_message(message.chat.id, "⚙️ <b>Налаштування</b>\nОберіть розділ:", reply_markup=get_settings_keyboard(is_admin_user), user_id=message.from_user.id)

@router.message(F.text == "🆔 Мій ID")
async def show_my_id(message: Message):
    user = message.from_user
    is_admin_user = await is_admin(user.id)
    text = (
        f"🆔 <b>Ваш профіль:</b>\n\n"
        f"• <b>ID:</b> <code>{user.id}</code>\n"
        f"• <b>Ім'я:</b> {user.first_name}\n"
        f"• <b>Username:</b> @{user.username or 'немає'}\n"
        f"• <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    await safe_send_message(message.chat.id, text, reply_markup=get_settings_keyboard(is_admin_user), user_id=user.id)

@router.message(F.text == "💰 Тарифи")
async def show_tariffs(message: Message):
    await display_tariff_cards(message, 'tariff', 'Тарифи', message.from_user.id)

@router.message(F.text == "💪 Супер сили")
async def show_super_powers(message: Message):
    await display_tariff_cards(message, 'super_power', 'Супер сили', message.from_user.id)

@router.message(F.text == "🌍 Роумінг")
async def show_roaming(message: Message):
    await display_tariff_cards(message, 'roaming', 'Роумінг', message.from_user.id)

@router.message(F.text == "📂 Відділи")
async def show_sections_menu(message: Message):
    sections = await get_sections()
    if not sections:
        await safe_send_message(message.chat.id, "📭 Відділи відсутні.", reply_markup=get_main_reply_keyboard(), user_id=message.from_user.id)
        return
    
    keyboard = [[InlineKeyboardButton(text=name, callback_data=f"section_{section_id}")] for section_id, name, _, _ in sections]
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    
    await safe_send_message(message.chat.id, "📂 <b>Відділи:</b>\nОберіть відділ:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), user_id=message.from_user.id)

@router.callback_query(F.data.startswith("section_"))
async def show_section(callback: CallbackQuery):
    section_id = int(callback.data.split('_')[1])
    section = await get_section(section_id)
    if not section:
        return await callback.answer("❌ Відділ не знайдено", show_alert=True)
    
    _, name, description, content = section
    keyboard = [[InlineKeyboardButton(text="🔙 Назад до відділів", callback_data="back_to_sections")]]
    await safe_edit_message(callback, f"<b>{name}</b>\n\n{description}\n\n{content}", InlineKeyboardMarkup(inline_keyboard=keyboard))

@router.callback_query(F.data == "back_to_sections")
async def back_to_sections(callback: CallbackQuery):
    sections = await get_sections()
    if not sections:
        return await safe_edit_message(callback, "📭 Відділи відсутні.")
    
    keyboard = [[InlineKeyboardButton(text=name, callback_data=f"section_{section_id}")] for section_id, name, _, _ in sections]
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    await safe_edit_message(callback, "📂 <b>Відділи:</b>\nОберіть відділ:", InlineKeyboardMarkup(inline_keyboard=keyboard))

@router.callback_query(F.data == "back_to_main")
async def back_to_main_cb(callback: CallbackQuery):
    await safe_edit_message(callback, "🏠 <b>Головне меню</b>", get_main_reply_keyboard())

@router.callback_query(F.data == "back_to_admin")
async def back_to_admin_cb(callback: CallbackQuery):
    if await is_admin(callback.from_user.id):
        await safe_edit_message(callback, "👨‍💼 <b>Адмін-панель</b>\nОберіть дію:", get_admin_keyboard())
    else:
        await safe_edit_message(callback, "🏠 <b>Головне меню</b>", get_main_reply_keyboard())

# ========== ДОПОМОГА (ВИПРАВЛЕНО) ==========
@router.message(F.text == "💬 Допомога")
async def show_help(message: Message):
    try:
        await update_user_activity(message.from_user.id)
        text = (
            "💬 <b>Допомога та підтримка:</b>\n\n"
            "📞 <b>Контакти:</b>\n"
            "• Телефон: <code>067 304 9999</code>\n"
            "• Додаток(Android): https://play.google.com/store/apps/details?id=com.kyivstar.mykyivstar \n"
            "• Додаток(iOS): https://apps.apple.com/ua/app/мій-київстар-інтернет-дзвінки/id771788824 \n\n"
            "🕐 <b>Час роботи підтримки:</b>\n"
            "Пн-Пт: 09:00-18:00\n\n"
            "<i>Якщо у вас виникли технічні проблеми з роботою бота, зверніться до адміністратора системи.</i>"
        )
        await safe_send_message(message.chat.id, text, reply_markup=get_main_reply_keyboard(), user_id=message.from_user.id)
    except Exception as e:
        error_logger.log_error("Допомога", e, message.from_user.id)

# ========== АДМІН СИСТЕМА ==========
@router.message(F.text == "👨‍💼 Адмін-панель")
async def admin_panel_menu(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_admin(user_id):
        await safe_send_message(message.chat.id, "👨‍💼 <b>Адмін-панель</b>\nОберіть дію:", reply_markup=get_admin_keyboard(), user_id=user_id)
    else:
        await safe_send_message(message.chat.id, "🔐 <b>Вхід в адмін-панель</b>\n\nВведіть пароль:", reply_markup=ReplyKeyboardRemove(), user_id=user_id)
        await state.set_state(AdminStates.login)

@router.message(AdminStates.login)
async def admin_login_check(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text.strip() == ADMIN_PASSWORD:
        user = message.from_user
        await add_admin(user.id, user.username, user.first_name, user.last_name)
        await safe_send_message(message.chat.id, "✅ Вхід виконано! Доступ надано.", reply_markup=get_main_reply_keyboard(), user_id=user_id)
        await safe_send_message(message.chat.id, "👨‍💼 <b>Адмін-панель</b>\nОберіть дію:", reply_markup=get_admin_keyboard(), user_id=user_id)
        await state.clear()
    else:
        await safe_send_message(message.chat.id, "❌ Невірний пароль!", user_id=user_id)
        await state.clear()

# ========== УНІВЕРСАЛЬНИЙ ХЕНДЛЕР СКАСУВАННЯ ==========
@router.message(Command("cancel", "скасувати", "отмена") | F.text.icontains("скасувати"))
@router.callback_query(F.data == "cancel_action")
async def cancel_action(target: Union[Message, CallbackQuery], state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        if isinstance(target, CallbackQuery):
            await target.answer("Ви не перебуваєте в активному процесі.", show_alert=True)
        return
    
    await state.clear()
    user_id = target.from_user.id
    
    if await is_admin(user_id):
        if isinstance(target, CallbackQuery):
            await safe_edit_message(target, "✅ Дію скасовано. Повертаємось до адмін-панелі.", get_admin_keyboard())
        else:
            await safe_send_message(target.chat.id, "✅ Дію скасовано. Повертаємось до адмін-панелі.", get_admin_keyboard(), user_id=user_id)
    else:
        if isinstance(target, CallbackQuery):
            await safe_edit_message(target, "✅ Дію скасовано.", get_main_reply_keyboard())
        else:
            await safe_send_message(target.chat.id, "✅ Дію скасовано.", get_main_reply_keyboard(), user_id=user_id)

# ========== АДМІН: ПЕРЕГЛЯД ТА ВИДАЛЕННЯ КАРТОК ==========
@admin_router.callback_query(F.data.startswith("admin_view_cards:"))
async def admin_view_cards(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    card_type = callback.data.split(":")[1]
    type_names = {'tariff': '💰 Тарифи', 'super_power': '💪 Супер сили', 'roaming': '🌍 Роумінг'}
    
    await callback.answer()
    await display_tariff_cards(callback, card_type, type_names.get(card_type, 'Картки'), callback.from_user.id)

@admin_router.callback_query(F.data.startswith("admin_delete_card:"))
async def admin_delete_card(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    card_id = int(callback.data.split(":")[1])
    success = await delete_tariff_card(card_id)
    
    if success:
        await callback.answer("✅ Картку деактивовано (видалено)", show_alert=True)
        await callback.message.answer("🔄 Оновлюємо список...", reply_markup=get_admin_keyboard())
    else:
        await callback.answer("❌ Не вдалося видалити картку", show_alert=True)

# ========== АДМІН: РЕДАГУВАННЯ КАРТОК (FSM) ==========
@admin_router.callback_query(F.data.startswith("admin_edit_card:"))
async def admin_edit_card_menu(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    card_id = int(callback.data.split(":")[1])
    card = await get_tariff_card(card_id)
    if not card:
        return await callback.answer("❌ Картку не знайдено", show_alert=True)
    
    await state.update_data(card_id=card_id)
    
    keyboard = [
        [InlineKeyboardButton(text="✏️ Назва", callback_data="admin_edit_field:title")],
        [InlineKeyboardButton(text="✏️ Ціна", callback_data="admin_edit_field:price")],
        [InlineKeyboardButton(text="✏️ Опис", callback_data="admin_edit_field:description")],
        [InlineKeyboardButton(text="✏️ Зображення", callback_data="admin_edit_field:image")],
        [InlineKeyboardButton(text="🔙 Назад до списку", callback_data=f"admin_view_cards:{card[6]}")]
    ]
    
    await safe_edit_message(callback, f"⚙️ <b>Редагування:</b> {card[1]}\n\nОберіть поле для зміни:", InlineKeyboardMarkup(inline_keyboard=keyboard))

@admin_router.callback_query(F.data.startswith("admin_edit_field:"))
async def admin_edit_field_prompt(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    field = callback.data.split(":")[1]
    data = await state.get_data()
    if not data.get('card_id'):
        return await callback.answer("❌ Помилка сесії. Почніть спочатку.", show_alert=True)
    
    await state.update_data(edit_field=field)
    
    prompts = {
        'title': "Введіть нову <b>назву</b>:",
        'price': "Введіть нову <b>ціну</b>:",
        'description': "Введіть новий <b>опис</b> (або 'пропустити' для очищення):",
        'image': "Надішліть нове <b>фото</b> або текст (емодзі/посилання), або 'пропустити' для видалення:"
    }
    
    await callback.message.answer(prompts.get(field, "Введіть нове значення:"), reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.edit_card)
    await callback.answer()

@admin_router.message(AdminStates.edit_card)
async def admin_save_card_edit(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await state.clear()
        return

    data = await state.get_data()
    card_id = data.get('card_id')
    field = data.get('edit_field')
    
    if not card_id or not field:
        await message.answer("❌ Помилка сесії. Почніть редагування спочатку.")
        await state.clear()
        return

    update_kwargs = {}
    if field == 'image':
        if message.photo:
            update_kwargs['image_file_id'] = message.photo[-1].file_id
            update_kwargs['image_url'] = '🖼️'
        elif message.text and message.text.strip().lower() in ['пропустити', 'skip', 'видалити', '-']:
            update_kwargs['image_file_id'] = ''
            update_kwargs['image_url'] = ''
        else:
            update_kwargs['image_url'] = message.text.strip()
            update_kwargs['image_file_id'] = ''
    else:
        value = message.text.strip()
        if field == 'description' and value.lower() in ['пропустити', 'skip', 'видалити', '-']:
            value = ''
        if field in ['title', 'price'] and not value:
            await message.answer("❌ Це поле не може бути порожнім. Спробуйте ще раз:", reply_markup=get_cancel_keyboard())
            return
        update_kwargs[field] = value

    result = await update_tariff_card(card_id, **update_kwargs)
    if result:
        await message.answer("✅ Поле успішно оновлено!", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Помилка при оновленні бази даних.", reply_markup=get_admin_keyboard())
    
    await state.clear()

# ========== АДМІН: ДОДАВАННЯ КАРТОК (FSM - ВИПРАВЛЕНО) ==========
@admin_router.callback_query(F.data.startswith("admin_add_card:"))
async def admin_add_card_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    card_type = callback.data.split(":")[1]
    type_names = {'tariff': 'тарифу', 'super_power': 'супер сили', 'roaming': 'роумінгу'}
    
    await state.update_data(card_type=card_type, step=1)
    await callback.message.answer(
        f"➕ <b>Додавання: {type_names.get(card_type, 'картки')}</b>\n\n"
        f"Введіть <b>назву</b> (або напишіть /cancel для скасування):",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.add_card)
    await callback.answer()

@admin_router.message(AdminStates.add_card)
async def admin_add_card_process(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await state.clear()
        return

    data = await state.get_data()
    step = data.get('step', 1)
    card_type = data.get('card_type', 'tariff')

    if step == 1:  # Назва
        if not message.text or not message.text.strip():
            await message.answer("❌ Назва не може бути порожньою. Спробуйте ще раз:", reply_markup=get_cancel_keyboard())
            return
        await state.update_data(title=message.text.strip(), step=2)
        await message.answer("💰 Введіть <b>ціну</b> (наприклад, '150 грн/міс'):", reply_markup=get_cancel_keyboard())
        
    elif step == 2:  # Ціна
        if not message.text or not message.text.strip():
            await message.answer("❌ Ціна не може бути порожньою. Спробуйте ще раз:", reply_markup=get_cancel_keyboard())
            return
        await state.update_data(price=message.text.strip(), step=3)
        await message.answer("📝 Введіть <b>опис</b> (або напишіть 'пропустити'):", reply_markup=get_cancel_keyboard())
        
    elif step == 3:  # Опис
        description = message.text.strip() if message.text else ''
        if description.lower() in ['пропустити', 'skip', '-']:
            description = ''
        await state.update_data(description=description, step=4)
        await message.answer("🖼️ Надішліть <b>фото</b> або напишіть емодзі/посилання (або 'пропустити'):", reply_markup=get_cancel_keyboard())
        
    elif step == 4:  # Зображення та збереження
        image_url, image_file_id = '', ''
        if message.photo:
            image_file_id = message.photo[-1].file_id
            image_url = '🖼️'
        elif message.text and message.text.strip().lower() not in ['пропустити', 'skip', '-']:
            image_url = message.text.strip()

        card_id = await add_tariff_card(data['title'], data['price'], data['description'], image_url, image_file_id, card_type)
        type_display = {'tariff': 'Тариф', 'super_power': 'Супер силу', 'roaming': 'Роумінг'}.get(card_type, 'Картку')

        if card_id:
            await message.answer(f"✅ {type_display} '<b>{data['title']}</b>' успішно додано! (ID: {card_id})", reply_markup=get_admin_keyboard())
        else:
            await message.answer("❌ Помилка при додаванні до бази даних.", reply_markup=get_admin_keyboard())

        await state.clear()

# ========== АДМІН: ВІДДІЛИ ==========
@admin_router.callback_query(F.data == "admin_sections_menu")
async def admin_sections_menu(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    keyboard = [
        [InlineKeyboardButton(text="➕ Додати відділ", callback_data="admin_add_section")],
        [InlineKeyboardButton(text="✏️ Редагувати відділи", callback_data="admin_edit_sections")],
        [InlineKeyboardButton(text="🗑️ Видалити відділ", callback_data="admin_delete_section")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
    ]
    await safe_edit_message(callback, "📂 <b>Управління відділами</b>\nОберіть дію:", InlineKeyboardMarkup(inline_keyboard=keyboard))

@admin_router.callback_query(F.data == "admin_edit_sections")
async def admin_edit_sections_list(callback: CallbackQuery):
    sections = await get_sections()
    if not sections:
        return await callback.message.answer("📭 Поки немає жодного відділу.", reply_markup=get_admin_keyboard())

    kb = [[InlineKeyboardButton(text=f"✏️ {name}", callback_data=f"edit_section_{section_id}")] for section_id, name, _, _ in sections]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_sections_menu")])
    await safe_edit_message(callback, "✏️ <b>Редагування відділів</b>\n\nОберіть відділ:", InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data.startswith("edit_section_"))
async def edit_section_start(callback: CallbackQuery, state: FSMContext):
    section_id = int(callback.data.split("_")[-1])
    section = await get_section(section_id)
    if not section:
        return await callback.answer("Відділ не знайдено", show_alert=True)
    
    await state.update_data(section_id=section_id, step='name')
    await callback.message.answer(f"Редагуємо відділ: <b>{section[1]}</b>\n\nВведіть нову назву (або /cancel для скасування):", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.edit_section)
    await callback.answer()

@admin_router.message(AdminStates.edit_section)
async def process_edit_section(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await state.clear()
        return

    data = await state.get_data()
    section_id = data.get('section_id')
    step = data.get('step')

    if step == 'name':
        await state.update_data(name=message.text.strip(), step='description')
        await message.answer("Введіть новий <b>опис</b> (або 'пропустити'):", reply_markup=get_cancel_keyboard())
    elif step == 'description':
        desc = message.text.strip() if message.text.strip().lower() not in ['пропустити', 'skip'] else ''
        await state.update_data(description=desc, step='content')
        await message.answer("Введіть новий <b>контент</b> (або 'пропустити'):", reply_markup=get_cancel_keyboard())
    elif step == 'content':
        content = message.text.strip() if message.text.strip().lower() not in ['пропустити', 'skip'] else ''
        success = await update_section(section_id, data['name'], data['description'], content)
        
        if success:
            await message.answer("✅ Відділ успішно оновлено!", reply_markup=get_admin_keyboard())
        else:
            await message.answer("❌ Помилка оновлення.", reply_markup=get_admin_keyboard())
        await state.clear()

@admin_router.callback_query(F.data == "admin_delete_section")
async def admin_delete_section_list(callback: CallbackQuery):
    sections = await get_sections()
    if not sections:
        return await callback.message.answer("📭 Поки немає жодного відділу.", reply_markup=get_admin_keyboard())

    kb = [[InlineKeyboardButton(text=f"🗑️ {name}", callback_data=f"delete_section_confirm_{section_id}")] for section_id, name, _, _ in sections]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_sections_menu")])
    await safe_edit_message(callback, "🗑️ <b>Видалення відділів</b>\n\nОберіть відділ для видалення:", InlineKeyboardMarkup(inline_keyboard=kb))

@admin_router.callback_query(F.data.startswith("delete_section_confirm_"))
async def delete_section_confirm(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    section_id = int(callback.data.split("_")[-1])
    success = await delete_section(section_id)
    
    if success:
        await callback.answer("✅ Відділ успішно видалено.", show_alert=True)
        await callback.message.answer("Повертаємось в адмін-панель...", reply_markup=get_admin_keyboard())
    else:
        await callback.answer("❌ Не вдалося видалити відділ.", show_alert=True)

# ========== АДМІН: РОЗСИЛКА (100% РЕАЛІЗОВАНО) ==========
@admin_router.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    await state.clear()
    await callback.message.answer(
        "📢 <b>Налаштування розсилки</b>\n\n"
        "Надішліть повідомлення, яке потрібно розіслати всім користувачам.\n"
        "Це може бути просто текст, або фото з підписом.\n\n"
        "Для скасування натисніть кнопку нижче або напишіть /cancel",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.broadcast)
    await callback.answer()

@admin_router.message(AdminStates.broadcast)
async def broadcast_receive(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await state.clear()
        return

    # Зберігаємо дані розсилки
    data = {}
    if message.text:
        data['text'] = message.text
    if message.caption:
        data['text'] = message.caption
    if message.photo:
        data['photo_id'] = message.photo[-1].file_id
    
    if not data.get('text') and not data.get('photo_id'):
        await message.answer("❌ Повідомлення не може бути порожнім. Надішліть текст або фото.", reply_markup=get_cancel_keyboard())
        return

    await state.update_data(**data)
    
    # Клавіатура підтвердження
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Підтвердити та надіслати", callback_data="broadcast_confirm")],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_action")]
    ])
    
    await message.answer("👀 <b>Попередній перегляд розсилки:</b>\n\nНатисніть 'Підтвердити', щоб надіслати це повідомлення всім активним користувачам.", reply_markup=kb)
    
    # Показуємо, як це виглядатиме
    await message.answer("👇 <b>Так це виглядатиме для користувачів:</b>")
    if data.get('photo_id'):
        await message.answer_photo(photo=data['photo_id'], caption=data.get('text', ''))
    else:
        await message.answer(data.get('text', 'Порожнє повідомлення'))

@admin_router.callback_query(F.data == "broadcast_confirm")
async def broadcast_execute(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return await callback.answer("❌ Немає доступу", show_alert=True)
    
    await callback.answer("⏳ Розсилка розпочата... Це може зайняти деякий час.")
    data = await state.get_data()
    users = await get_all_users()
    
    if not users:
        await state.clear()
        return await callback.message.answer("❌ Немає активних користувачів для розсилки.", reply_markup=get_admin_keyboard())
    
    success_count = 0
    fail_count = 0
    
    # Повідомлення про прогрес
    progress_msg = await callback.message.answer(f"📢 Розсилка... 0/{len(users)}")
    
    for i, user_id in enumerate(users):
        try:
            if data.get('photo_id'):
                await bot.send_photo(user_id, photo=data['photo_id'], caption=data.get('text', ''))
            else:
                await bot.send_message(user_id, text=data.get('text', ''))
            success_count += 1
        except Exception:
            # Ігноруємо помилки (наприклад, користувач заблокував бота), щоб не зупиняти розсилку
            fail_count += 1
        
        # Оновлюємо прогрес кожні 10 користувачів, щоб не отримати flood wait
        if i % 10 == 0:
            try:
                await progress_msg.edit_text(
                    f"📢 Розсилка...\n"
                    f"Оброблено: {i+1}/{len(users)}\n"
                    f"✅ Успішно: {success_count}\n"
                    f"❌ Помилок (блок/видалення): {fail_count}"
                )
            except Exception:
                pass # Ігноруємо помилки редагування повідомлення під час flood control
    
    await state.clear()
    
    # Фінальний звіт
    final_report = (
        f"✅ <b>Розсилку завершено!</b>\n\n"
        f"👥 Всього отримувачів: {len(users)}\n"
        f"✅ Успішно доставлено: {success_count}\n"
        f"❌ Не доставлено (користувач заблокував бота або видалив акаунт): {fail_count}"
    )
    
    try:
        await progress_msg.edit_text(final_report, reply_markup=get_admin_keyboard())
    except Exception:
        await callback.message.answer(final_report, reply_markup=get_admin_keyboard())

# ========== ЗАПУСК БОТА ==========
async def main():
    try:
        await db_manager.init_db()
        error_logger.logger.info("🚀 Бот запускається...")
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        error_logger.log_error("Критична помилка запуску", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())