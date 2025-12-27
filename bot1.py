import asyncio
import logging
import json
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, 
    InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, PhotoSize, FSInputFile
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
import aiosqlite

# ========== НАСТРОЙКА ЛОГГИРОВАНИЯ ==========
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
        user_info = f" | Пользователь: {user_id}" if user_id else ""
        info = f" | Инфо: {additional_info}" if additional_info else ""
        
        error_msg = f"[ОШИБКА] {context}: {str(error)[:200]}{user_info}{info} | Время: {timestamp}"
        
        with open('bot_errors.log', 'a', encoding='utf-8') as f:
            f.write(error_msg + '\n')
            if hasattr(error, '__traceback__'):
                traceback.print_exception(type(error), error, error.__traceback__, file=f)
        
        self.logger.error(error_msg)
        asyncio.create_task(self.log_to_db(user_id, context, str(error)[:500]))
    
    async def log_to_db(self, user_id: int, action_type: str, details: str):
        try:
            async with aiosqlite.connect('kyivstar_bot.db') as db:
                await db.execute('''
                    INSERT INTO action_logs (user_id, action_type, action_details)
                    VALUES (?, ?, ?)
                ''', (user_id, f"ERROR_{action_type}", details))
                await db.commit()
        except Exception as e:
            self.logger.error(f"Ошибка при записи лога в БД: {e}")

error_logger = ErrorLogger()

# ========== КОНФИГУРАЦИЯ БОТА ==========
ADMIN_PASSWORD = "your_secure_password"
BOT_TOKEN = "your_bot_token_here"

# ========== СОСТОЯНИЯ FSM ==========
class AdminStates(StatesGroup):
    login = State()
    create_section_name = State()
    create_section_description = State()
    create_section_content = State()
    broadcast = State()
    add_card = State()
    edit_card_field = State()
    edit_section = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
router = Router()
admin_router = Router()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
dp.include_router(router)
dp.include_router(admin_router)

temp_card_data: Dict[int, Dict] = {}
temp_edit_data: Dict[int, Dict] = {}

# ========== КЛАВИАТУРЫ ==========
def get_main_reply_keyboard():
    keyboard = [
        [KeyboardButton(text="📋 Отделы"), KeyboardButton(text="💰 Тарифы")],
        [KeyboardButton(text="💪 Супер силы"), KeyboardButton(text="🌍 Роуминг")],
        [KeyboardButton(text="💬 Помощь"), KeyboardButton(text="⚙️ Настройки")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_settings_keyboard(is_admin_user: bool = False):
    keyboard = []
    
    if is_admin_user:
        keyboard.append([KeyboardButton(text="👨‍💼 Админ-панель")])
    
    keyboard.extend([
        [KeyboardButton(text="🆔 Мой ID")],
        [KeyboardButton(text="📊 Популярные тарифы")],
        [KeyboardButton(text="🔙 Назад")]
    ])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="➕ Добавить отдел", callback_data="admin_add_section")],
        [InlineKeyboardButton(text="✏️ Редактировать отдел", callback_data="admin_edit_sections")],
        [InlineKeyboardButton(text="🗑️ Удалить отдел", callback_data="admin_delete_section")],
        [InlineKeyboardButton(text="💰 Добавить тариф", callback_data="admin_add_card:tariff")],
        [InlineKeyboardButton(text="💪 Добавить супер силу", callback_data="admin_add_card:super_power")],
        [InlineKeyboardButton(text="🌍 Добавить роуминг", callback_data="admin_add_card:roaming")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📋 Логи ошибок", callback_data="admin_error_logs")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ========== БАЗА ДАННЫХ ==========
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
                error_logger.logger.info("✅ База данных инициализирована успешно")
        except Exception as e:
            error_logger.log_error("Инициализация БД", e)
            raise
    
    async def create_tables(self, db):
        tables = [
            '''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notifications BOOLEAN DEFAULT 1,
                language TEXT DEFAULT 'uk',
                is_blocked BOOLEAN DEFAULT 0,
                block_reason TEXT
            )''',
            '''CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                display_order INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                role TEXT DEFAULT 'admin',
                permissions TEXT DEFAULT 'all',
                last_login TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )''',
            '''CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                attempts INTEGER DEFAULT 0,
                last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                banned_until TIMESTAMP NULL,
                ip_hash TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )''',
            '''CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by INTEGER,
                FOREIGN KEY (updated_by) REFERENCES admins(user_id)
            )''',
            '''CREATE TABLE IF NOT EXISTS tariff_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                description TEXT,
                image_url TEXT,
                image_file_id TEXT,
                card_type TEXT NOT NULL CHECK(card_type IN ('tariff', 'super_power', 'roaming')),
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                display_order INTEGER DEFAULT 0,
                views_count INTEGER DEFAULT 0,
                last_viewed TIMESTAMP,
                popularity_score FLOAT DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action_type TEXT NOT NULL,
                action_details TEXT,
                ip_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )''',
            '''CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE DEFAULT CURRENT_DATE,
                total_users INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                new_users INTEGER DEFAULT 0,
                total_cards INTEGER DEFAULT 0,
                card_views INTEGER DEFAULT 0,
                admin_actions INTEGER DEFAULT 0
            )'''
        ]
        
        for table_sql in tables:
            await db.execute(table_sql)
    
    async def create_indexes(self, db):
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_users_activity ON users(last_activity)",
            "CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(is_blocked)",
            "CREATE INDEX IF NOT EXISTS idx_sections_active ON sections(is_active, display_order)",
            "CREATE INDEX IF NOT EXISTS idx_sections_name ON sections(name)",
            "CREATE INDEX IF NOT EXISTS idx_cards_type_active ON tariff_cards(card_type, is_active, display_order)",
            "CREATE INDEX IF NOT EXISTS idx_cards_popularity ON tariff_cards(popularity_score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_cards_views ON tariff_cards(views_count DESC)",
            "CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(user_id, banned_until)",
            "CREATE INDEX IF NOT EXISTS idx_action_logs_date ON action_logs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_action_logs_user ON action_logs(user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_statistics_date ON statistics(date)"
        ]
        
        for index in indexes:
            await db.execute(index)
    
    async def add_default_data(self, db):
        current_date = datetime.now().strftime('%d.%m.%Y')
        
        settings = [           
            ('maintenance_mode', '0', 'Режим обслуживания'),
            ('broadcast_enabled', '1', 'Разрешить рассылку'),
            ('max_login_attempts', '3', 'Максимум попыток входа'),
            ('ban_duration', '24', 'Длительность бана (часы)'),
            ('welcome_message', 
             '👋 Добро пожаловать в бот Киевстар! Выберите раздел:',
             'Приветственное сообщение')
        ]
        
        for key, value, desc in settings:
            await db.execute(
                'INSERT OR IGNORE INTO bot_settings (key, value, description) VALUES (?, ?, ?)',
                (key, value, desc)
            )
        
        test_cards = [
            ('Стандарт', '150 грн/мес', 'Базовый пакет услуг', '📱', 'tariff'),
            ('Премиум+', '300 грн/мес', 'Все включено', '🚀', 'tariff'),
            ('Турбо-интернет', '75 грн/неделя', 'Скорость до 200 Мбит/с', '⚡', 'super_power'),
            ('Европа Плюс', '400 грн/мес', '30 ГБ в Европе', '🇪🇺', 'roaming'),
            ('Супер-ночь', '50 грн/мес', 'Безлимитный интернет с 00:00 до 07:00', '🌙', 'super_power')
        ]
        
        for title, price, desc, emoji, card_type in test_cards:
            await db.execute(
                '''INSERT OR IGNORE INTO tariff_cards 
                (title, price, description, image_url, card_type) 
                VALUES (?, ?, ?, ?, ?)''',
                (title, price, desc, emoji, card_type)
            )
        
        await db.execute(
            '''INSERT OR IGNORE INTO statistics (date, total_users, active_users) 
            VALUES (CURRENT_DATE, 0, 0)'''
        )

db_manager = DatabaseManager()

# ========== УТИЛИТЫ БАЗЫ ДАННЫХ ==========
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
        error_logger.log_error("SQL запрос", e, additional_info=f"Query: {query[:100]}")
        return None

async def add_user(user_id: int, username: str, first_name: str, last_name: str = None):
    query = '''
    INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_activity)
    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    '''
    await execute_query(query, (user_id, username, first_name, last_name))
    await log_action(user_id, 'user_register', f'Регистрация: {first_name}')

async def update_user_activity(user_id: int):
    query = 'UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?'
    await execute_query(query, (user_id,))

async def get_all_users():
    query = 'SELECT user_id FROM users WHERE is_blocked = 0'
    result = await execute_query(query, fetch_all=True)
    return [row[0] for row in result] if result else []

async def is_admin(user_id: int) -> bool:
    query = 'SELECT 1 FROM admins WHERE user_id = ?'
    result = await execute_query(query, (user_id,), fetch_one=True)
    return result is not None

async def add_admin(user_id: int, username: str, first_name: str, last_name: str):
    query = '''
    INSERT OR REPLACE INTO admins (user_id, username, first_name, last_name, last_login)
    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    '''
    await execute_query(query, (user_id, username, first_name, last_name))
    await log_action(user_id, 'admin_added', f'Добавлен админ: {username}')

async def get_setting(key: str):
    query = 'SELECT value FROM bot_settings WHERE key = ?'
    result = await execute_query(query, (key,), fetch_one=True)
    return result[0] if result else None

async def get_tariff_cards(card_type: str):
    query = '''
    SELECT id, title, price, description, image_url, image_file_id 
    FROM tariff_cards 
    WHERE card_type = ? AND is_active = 1
    ORDER BY display_order, popularity_score DESC, id
    '''
    return await execute_query(query, (card_type,), fetch_all=True)

async def get_tariff_card(card_id: int):
    query = '''
    SELECT id, title, price, description, image_url, image_file_id, card_type 
    FROM tariff_cards WHERE id = ?
    '''
    card = await execute_query(query, (card_id,), fetch_one=True)
    
    if card:
        await execute_query(
            'UPDATE tariff_cards SET views_count = views_count + 1, last_viewed = CURRENT_TIMESTAMP WHERE id = ?',
            (card_id,)
        )
    
    return card

async def add_tariff_card(title: str, price: str, description: str, image_url: str, image_file_id: str, card_type: str):
    query = '''
    INSERT INTO tariff_cards (title, price, description, image_url, image_file_id, card_type)
    VALUES (?, ?, ?, ?, ?, ?)
    '''
    card_id = await execute_query(
        query, 
        (title.strip(), price.strip(), description.strip() if description else '',
         image_url if image_url else '', image_file_id if image_file_id else '', card_type)
    )
    
    if card_id:
        await log_action(None, 'card_created', f'{card_type}: {title}')
    
    return card_id

async def update_tariff_card(card_id: int, **kwargs):
    if not kwargs:
        return True
    
    fields = []
    params = []
    
    field_mapping = {
        'title': ('title', lambda x: x.strip()),
        'price': ('price', lambda x: x.strip()),
        'description': ('description', lambda x: x.strip() if x else ''),
        'image_url': ('image_url', lambda x: x if x else ''),
        'image_file_id': ('image_file_id', lambda x: x if x else ''),
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
    
    result = await execute_query(query, tuple(params))
    await log_action(None, 'card_updated', f'Карточка {card_id}')
    return result is not None

async def delete_tariff_card(card_id: int):
    query = 'UPDATE tariff_cards SET is_active = 0 WHERE id = ?'
    result = await execute_query(query, (card_id,))
    
    if result is not None:
        await log_action(None, 'card_deleted', f'Карточка {card_id}')
        return True
    return False

async def get_popular_cards(limit: int = 5):
    query = '''
    SELECT id, title, price, card_type, views_count
    FROM tariff_cards 
    WHERE is_active = 1
    ORDER BY popularity_score DESC, views_count DESC
    LIMIT ?
    '''
    return await execute_query(query, (limit,), fetch_all=True)

async def get_sections():
    query = '''
    SELECT id, name, description, content 
    FROM sections 
    WHERE is_active = 1 
    ORDER BY display_order, id
    '''
    return await execute_query(query, fetch_all=True)

async def get_section(section_id: int):
    query = 'SELECT id, name, description, content FROM sections WHERE id = ?'
    return await execute_query(query, (section_id,), fetch_one=True)

async def add_section(name: str, description: str, content: str):
    query = 'INSERT INTO sections (name, description, content) VALUES (?, ?, ?)'
    section_id = await execute_query(query, (name, description, content))
    
    if section_id:
        await log_action(None, 'section_created', f'Отдел: {name}')
    
    return section_id

async def update_section(section_id: int, name: str, description: str, content: str):
    query = '''
    UPDATE sections 
    SET name = ?, description = ?, content = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
    '''
    result = await execute_query(query, (name, description, content, section_id))
    
    if result is not None:
        await log_action(None, 'section_updated', f'Отдел {section_id}')
        return True
    return False

async def delete_section(section_id: int):
    query = 'UPDATE sections SET is_active = 0 WHERE id = ?'
    result = await execute_query(query, (section_id,))
    
    if result is not None:
        await log_action(None, 'section_deleted', f'Отдел {section_id}')
        return True
    return False

async def log_action(user_id: int, action_type: str, details: str = None):
    query = '''
    INSERT INTO action_logs (user_id, action_type, action_details)
    VALUES (?, ?, ?)
    '''
    await execute_query(query, (user_id, action_type, details))

# ========== УТИЛИТЫ ОТПРАВКИ ==========
async def safe_send_message(chat_id: int, text: str, reply_markup=None, parse_mode: str = 'HTML', user_id: int = None):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        return True
    except Exception as e:
        error_logger.log_error("Отправка сообщения", e, user_id, f"Текст: {text[:50]}")
        return False

async def safe_edit_message(message_or_callback, text: str, reply_markup=None, parse_mode: str = 'HTML'):
    try:
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            await message_or_callback.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        return True
    except Exception as e:
        error_logger.log_error("Редактирование сообщения", e)
        return False

# ========== ОСНОВНЫЕ ХЕНДЛЕРЫ ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    try:
        user = message.from_user
        await add_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome = await get_setting('welcome_message') or '👋 Добро пожаловать!'
        await message.answer(
            f"{welcome}\n\nПривет, {user.first_name}!",
            reply_markup=get_main_reply_keyboard()
        )
    except Exception as e:
        error_logger.log_error("/start", e, message.from_user.id)
        await message.answer("❌ Ошибка при запуске бота.")

@router.message(F.text == "🔙 Назад")
async def back_to_main(message: Message):
    try:
        await safe_send_message(
            message.chat.id,
            "🏠 <b>Главное меню</b>",
            reply_markup=get_main_reply_keyboard(),
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error("Назад в главное", e, message.from_user.id)

@router.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message):
    try:
        await update_user_activity(message.from_user.id)
        is_admin_user = await is_admin(message.from_user.id)
        
        await safe_send_message(
            message.chat.id,
            "⚙️ <b>Настройки</b>\nВыберите раздел:",
            reply_markup=get_settings_keyboard(is_admin_user),
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error("Меню настроек", e, message.from_user.id)

@router.message(F.text == "🆔 Мой ID")
async def show_my_id(message: Message):
    try:
        user = message.from_user
        await update_user_activity(user.id)
        is_admin_user = await is_admin(user.id)
        
        text = (
            f"🆔 <b>Ваш профиль:</b>\n\n"
            f"• <b>ID:</b> <code>{user.id}</code>\n"
            f"• <b>Имя:</b> {user.first_name}\n"
            f"• <b>Username:</b> @{user.username if user.username else 'нет'}\n"
            f"• <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await safe_send_message(
            message.chat.id,
            text,
            reply_markup=get_settings_keyboard(is_admin_user),
            user_id=user.id
        )
    except Exception as e:
        error_logger.log_error("Показать ID", e, message.from_user.id)

@router.message(F.text == "📊 Популярные тарифы")
async def show_popular_tariffs(message: Message):
    try:
        await update_user_activity(message.from_user.id)
        popular = await get_popular_cards(5)
        
        if not popular:
            await safe_send_message(
                message.chat.id,
                "Популярные тарифы пока отсутствуют.",
                reply_markup=get_settings_keyboard(),
                user_id=message.from_user.id
            )
            return
        
        text = "🔥 <b>Самые популярные тарифы:</b>\n\n"
        for i, (card_id, title, price, card_type, views) in enumerate(popular, 1):
            emoji = "💰" if card_type == 'tariff' else "💪" if card_type == 'super_power' else "🌍"
            text += f"{i}. {emoji} <b>{title}</b>\n"
            text += f"   💰 {price}\n"
            text += f"   👁️ {views} просмотров\n\n"
        
        await safe_send_message(
            message.chat.id,
            text,
            reply_markup=get_settings_keyboard(),
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error("Популярные тарифы", e, message.from_user.id)

@router.message(F.text == "📋 Отделы")
async def show_sections_menu(message: Message):
    try:
        await update_user_activity(message.from_user.id)
        sections = await get_sections()
        
        if not sections:
            await safe_send_message(
                message.chat.id,
                "Отделы отсутствуют.",
                reply_markup=get_main_reply_keyboard(),
                user_id=message.from_user.id
            )
            return
        
        keyboard = []
        for section_id, name, description, _ in sections:
            keyboard.append([InlineKeyboardButton(text=name, callback_data=f"section_{section_id}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await safe_send_message(
            message.chat.id,
            "📂 <b>Отделы:</b>\nВыберите отдел:",
            reply_markup=reply_markup,
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error("Показать отделы", e, message.from_user.id)

@router.callback_query(F.data.startswith("section_"))
async def show_section(callback: CallbackQuery):
    try:
        await update_user_activity(callback.from_user.id)
        section_id = int(callback.data.split('_')[1])
        section = await get_section(section_id)
        
        if not section:
            await callback.answer("❌ Отдел не найден")
            return
        
        _, name, description, content = section
        
        await execute_query(
            'UPDATE sections SET views = views + 1 WHERE id = ?',
            (section_id,)
        )
        
        keyboard = [
            [InlineKeyboardButton(text="🔙 Назад к отделам", callback_data="back_to_sections")],
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await safe_edit_message(
            callback,
            f"<b>{name}</b>\n\n{description}\n\n{content}",
            reply_markup
        )
    except Exception as e:
        error_logger.log_error("Показать отдел", e, callback.from_user.id)
        await callback.answer("❌ Ошибка")

@router.callback_query(F.data == "back_to_sections")
async def back_to_sections(callback: CallbackQuery):
    try:
        await update_user_activity(callback.from_user.id)
        sections = await get_sections()
        
        if not sections:
            await safe_edit_message(callback, "Отделы отсутствуют.")
            return
        
        keyboard = []
        for section_id, name, description, _ in sections:
            keyboard.append([InlineKeyboardButton(text=name, callback_data=f"section_{section_id}")])
        
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await safe_edit_message(
            callback,
            "📂 <b>Отделы:</b>\nВыберите отдел:",
            reply_markup
        )
    except Exception as e:
        error_logger.log_error("Назад к отделам", e, callback.from_user.id)

# ========== ТАРИФЫ И КАРТОЧКИ ==========
async def display_tariff_cards(message: Message, card_type: str, title: str):
    try:
        await update_user_activity(message.from_user.id)
        cards = await get_tariff_cards(card_type)
        
        if not cards:
            await safe_send_message(
                message.chat.id,
                f"{title} отсутствуют.",
                reply_markup=get_main_reply_keyboard(),
                user_id=message.from_user.id
            )
            return
        
        for card in cards:
            await display_single_card(message, card)
        
        keyboard = [[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await safe_send_message(
            message.chat.id,
            "Выберите действие:",
            reply_markup=reply_markup,
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error(f"Показать {title}", e, message.from_user.id)

async def display_single_card(message: Message, card: tuple, show_admin_buttons: bool = True):
    try:
        card_id, title, price, description, image_url, image_file_id = card
        
        text = (
            f"<b>{title}</b>\n\n"
            f"💰 <b>Цена:</b> {price}\n"
            f"📝 <b>Описание:</b> {description or 'Без описания'}"
        )
        
        keyboard = []
        if show_admin_buttons and await is_admin(message.from_user.id):
            keyboard.append([
                InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_card_{card_id}"),
                InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_card_{card_id}")
            ])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
        
        if image_file_id:
            try:
                await message.answer_photo(
                    photo=image_file_id,
                    caption=text,
                    reply_markup=reply_markup
                )
                return
            except Exception:
                pass
        
        if image_url:
            text = f"{image_url} {text}"
        
        await safe_send_message(
            message.chat.id,
            text,
            reply_markup=reply_markup,
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error("Отобразить карточку", e, message.from_user.id)

@router.message(F.text == "💰 Тарифы")
async def show_tariffs(message: Message):
    await display_tariff_cards(message, 'tariff', 'Тарифы')

@router.message(F.text == "💪 Супер силы")
async def show_super_powers(message: Message):
    await display_tariff_cards(message, 'super_power', 'Супер силы')

@router.message(F.text == "🌍 Роуминг")
async def show_roaming(message: Message):
    await display_tariff_cards(message, 'roaming', 'Роуминг')

@router.message(F.text == "💬 Помощь")
async def show_help(message: Message):
    try:
        await update_user_activity(message.from_user.id)
        
        text = (
            "💬 <b>Помощь и поддержка:</b>\n\n"
            "📞 <b>Контакты:</b>\n"
            "• Телефон: <code>0 800 300 460</code>\n"
            "• Сайт: kyivstar.ua\n"
            "• Email: help@kyivstar.ua\n\n"
            "🕐 <b>Время работы:</b>\n"
            "Пн-Пт: 9:00-20:00\n"
            "Сб-Вс: 10:00-18:00\n\n"
            "<i>Вы также можете обратиться к администратору через админ-панель.</i>"
        )
        
        await safe_send_message(
            message.chat.id,
            text,
            reply_markup=get_main_reply_keyboard(),
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error("Помощь", e, message.from_user.id)

# ========== АДМИН СИСТЕМА ==========
@router.message(F.text == "👨‍💼 Админ-панель")
async def admin_panel_menu(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        await update_user_activity(user_id)
        
        if await is_admin(user_id):
            await show_admin_panel_internal(message)
            return
        
        await safe_send_message(
            message.chat.id,
            "🔐 <b>Вход в админ-панель</b>\n\nВведите пароль:",
            reply_markup=ReplyKeyboardRemove(),
            user_id=user_id
        )
        await state.set_state(AdminStates.login)
    except Exception as e:
        error_logger.log_error("Вход в админку", e, message.from_user.id)

@router.message(AdminStates.login)
async def admin_login_check(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        password = message.text.strip()
        
        if password == ADMIN_PASSWORD:
            user = message.from_user
            await add_admin(user.id, user.username, user.first_name, user.last_name)
            
            await safe_send_message(
                message.chat.id,
                "✅ Вход выполнен! Доступ предоставлен.",
                reply_markup=get_main_reply_keyboard(),
                user_id=user_id
            )
            await show_admin_panel_internal(message)
            await state.clear()
        else:
            await safe_send_message(
                message.chat.id,
                "❌ Неверный пароль!",
                user_id=user_id
            )
    except Exception as e:
        error_logger.log_error("Проверка пароля", e, message.from_user.id)
        await state.clear()

async def show_admin_panel_internal(message: Message):
    try:
        await safe_send_message(
            message.chat.id,
            "👨‍💼 <b>Админ-панель</b>\nВыберите действие:",
            reply_markup=get_admin_keyboard(),
            user_id=message.from_user.id
        )
    except Exception as e:
        error_logger.log_error("Показать админ-панель", e, message.from_user.id)

# ========== АДМИН: РЕДАКТИРОВАНИЕ КАРТОЧЕК ==========
@admin_router.callback_query(F.data.startswith("edit_card_"))
async def admin_edit_card_start(callback: CallbackQuery):
    try:
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа")
            return
        
        card_id = int(callback.data.split('_')[-1])
        card = await get_tariff_card(card_id)
        
        if not card:
            await callback.answer("❌ Карточка не найдена")
            return
        
        card_id, title, price, description, image_url, image_file_id, card_type = card
        
        keyboard = [
            [InlineKeyboardButton(text="✏️ Название", callback_data=f"edit_title_{card_id}")],
            [InlineKeyboardButton(text="✏️ Цена", callback_data=f"edit_price_{card_id}")],
            [InlineKeyboardButton(text="✏️ Описание", callback_data=f"edit_desc_{card_id}")],
            [InlineKeyboardButton(text="✏️ Изображение", callback_data=f"edit_img_{card_id}")],
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        image_info = "🖼️ Фото" if image_file_id else f"{image_url or 'Нет'}"
        
        await safe_edit_message(
            callback,
            f"<b>Редактирование:</b> {title}\n\n"
            f"💰 Цена: {price}\n"
            f"📝 Описание: {description or 'Нет'}\n"
            f"📸 Изображение: {image_info}\n\n"
            "Выберите поле:",
            reply_markup
        )
    except Exception as e:
        error_logger.log_error("Начать редактирование карточки", e, callback.from_user.id)

@admin_router.callback_query(F.data.startswith("edit_title_"))
async def edit_card_title(callback: CallbackQuery, state: FSMContext):
    try:
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа")
            return
        
        card_id = int(callback.data.split('_')[-1])
        
        temp_edit_data[callback.from_user.id] = {
            'card_id': card_id,
            'field': 'title'
        }
        
        await callback.message.answer("Введите новое название:")
        await state.set_state(AdminStates.edit_card_field)
    except Exception as e:
        error_logger.log_error("Редактировать название", e, callback.from_user.id)

@admin_router.callback_query(F.data.startswith("edit_price_"))
async def edit_card_price(callback: CallbackQuery, state: FSMContext):
    try:
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа")
            return
        
        card_id = int(callback.data.split('_')[-1])
        
        temp_edit_data[callback.from_user.id] = {
            'card_id': card_id,
            'field': 'price'
        }
        
        await callback.message.answer("Введите новую цену:")
        await state.set_state(AdminStates.edit_card_field)
    except Exception as e:
        error_logger.log_error("Редактировать цену", e, callback.from_user.id)

@admin_router.callback_query(F.data.startswith("edit_desc_"))
async def edit_card_description(callback: CallbackQuery, state: FSMContext):
    try:
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа")
            return
        
        card_id = int(callback.data.split('_')[-1])
        
        temp_edit_data[callback.from_user.id] = {
            'card_id': card_id,
            'field': 'description'
        }
        
        await callback.message.answer("Введите новое описание (или 'удалить' для очистки):")
        await state.set_state(AdminStates.edit_card_field)
    except Exception as e:
        error_logger.log_error("Редактировать описание", e, callback.from_user.id)

@admin_router.callback_query(F.data.startswith("edit_img_"))
async def edit_card_image(callback: CallbackQuery, state: FSMContext):
    try:
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа")
            return
        
        card_id = int(callback.data.split('_')[-1])
        
        temp_edit_data[callback.from_user.id] = {
            'card_id': card_id,
            'field': 'image'
        }
        
        await callback.message.answer("Отправьте новое фото или эмодзи (или 'удалить'):")
        await state.set_state(AdminStates.edit_card_field)
    except Exception as e:
        error_logger.log_error("Редактировать изображение", e, callback.from_user.id)

@admin_router.message(AdminStates.edit_card_field)
async def save_card_edit(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        if not await is_admin(user_id):
            await message.answer("❌ Нет доступа")
            await state.clear()
            return
        
        if user_id not in temp_edit_data:
            await message.answer("❌ Сессия утеряна")
            await state.clear()
            return
        
        data = temp_edit_data[user_id]
        card_id = data['card_id']
        field = data['field']
        
        success = False
        
        if field == 'image':
            if message.photo:
                photo = message.photo[-1]
                success = await update_tariff_card(
                    card_id,
                    image_file_id=photo.file_id,
                    image_url="🖼️"
                )
            elif message.text and message.text.lower() in ['удалить', 'пропустить']:
                success = await update_tariff_card(
                    card_id,
                    image_url='',
                    image_file_id=None
                )
            else:
                success = await update_tariff_card(
                    card_id,
                    image_url=message.text,
                    image_file_id=None
                )
        else:
            value = message.text.strip()
            
            if field == 'description' and value.lower() in ['удалить', 'пропустить']:
                value = ''
            
            if field in ['title', 'price'] and not value:
                await message.answer("❌ Поле не может быть пустым")
                return
            
            update_data = {field: value}
            success = await update_tariff_card(card_id, **update_data)
        
        if success:
            card = await get_tariff_card(card_id)
            if card:
                _, title, price, description, image_url, image_file_id, card_type = card
                
                keyboard = [
                    [InlineKeyboardButton(text="✏️ Название", callback_data=f"edit_title_{card_id}")],
                    [InlineKeyboardButton(text="✏️ Цена", callback_data=f"edit_price_{card_id}")],
                    [InlineKeyboardButton(text="✏️ Описание", callback_data=f"edit_desc_{card_id}")],
                    [InlineKeyboardButton(text="✏️ Изображение", callback_data=f"edit_img_{card_id}")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_cards_{card_type}")]
                ]
                reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
                
                image_info = "🖼️ Фото" if image_file_id else f"{image_url or 'Нет'}"
                
                await message.answer(
                    f"✅ Обновлено!\n\n"
                    f"<b>Редактирование:</b> {title}\n"
                    f"💰 Цена: {price}\n"
                    f"📝 Описание: {description or 'Нет'}\n"
                    f"📸 Изображение: {image_info}",
                    reply_markup=reply_markup
                )
            else:
                await message.answer("✅ Обновлено!")
        else:
            await message.answer("❌ Ошибка обновления")
        
        if user_id in temp_edit_data:
            del temp_edit_data[user_id]
        await state.clear()
        
    except Exception as e:
        error_logger.log_error("Сохранить редактирование", e, message.from_user.id)
        await state.clear()

@admin_router.callback_query(F.data.startswith("delete_card_"))
async def admin_delete_card(callback: CallbackQuery):
    try:
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа")
            return
        
        card_id = int(callback.data.split('_')[-1])
        success = await delete_tariff_card(card_id)
        
        if success:
            await safe_edit_message(callback, "✅ Карточка удалена")
        else:
            await safe_edit_message(callback, "❌ Ошибка удаления")
    except Exception as e:
        error_logger.log_error("Удалить карточку", e, callback.from_user.id)

@admin_router.callback_query(F.data.startswith("back_cards_"))
async def back_to_cards_admin(callback: CallbackQuery):
    try:
        card_type = callback.data.split('_')[-1]
        type_names = {
            'tariff': 'Тарифы',
            'super_power': 'Супер силы',
            'roaming': 'Роуминг'
        }
        title = type_names.get(card_type, 'Карточки')
        await display_tariff_cards(callback.message, card_type, title)
    except Exception as e:
        error_logger.log_error("Назад к карточкам (админ)", e, callback.from_user.id)

# ========== АДМИН: ДОБАВЛЕНИЕ КАРТОЧЕК ==========
@admin_router.callback_query(F.data.startswith("admin_add_card:"))
async def admin_add_card_start(callback: CallbackQuery, state: FSMContext):
    try:
        if not await is_admin(callback.from_user.id):
            await callback.answer("❌ Нет доступа")
            return
        
        card_type = callback.data.split(':')[1]
        type_names = {
            'tariff': 'тариф',
            'super_power': 'супер силу',
            'roaming': 'роуминг'
        }
        
        temp_card_data[callback.from_user.id] = {
            'card_type': card_type,
            'step': 0
        }
        
        await callback.message.answer(f"Введите название {type_names.get(card_type, 'карточки')}:")
        await state.set_state(AdminStates.add_card)
    except Exception as e:
        error_logger.log_error("Начать добавление карточки", e, callback.from_user.id)

@admin_router.message(AdminStates.add_card)
async def admin_add_card_process(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        if not await is_admin(user_id):
            await message.answer("❌ Нет доступа")
            await state.clear()
            return
        
        if user_id not in temp_card_data:
            await message.answer("❌ Сессия утеряна")
            await state.clear()
            return
        
        data = temp_card_data[user_id]
        step = data.get('step', 0)
        
        if step == 0:
            if not message.text.strip():
                await message.answer("❌ Название не может быть пустым")
                return
            
            data['title'] = message.text.strip()
            data['step'] = 1
            await message.answer("Введите цену:")
        
        elif step == 1:
            if not message.text.strip():
                await message.answer("❌ Цена не может быть пустой")
                return
            
            data['price'] = message.text.strip()
            data['step'] = 2
            await message.answer("Введите описание (или 'пропустить'):")
        
        elif step == 2:
            description = message.text.strip()
            if description.lower() == 'пропустить':
                description = ''
            
            data['description'] = description
            data['step'] = 3
            await message.answer("Отправьте фото или эмодзи (или 'пропустить'):")
        
        elif step == 3:
            card_type = data.get('card_type', 'tariff')
            image_url = ""
            image_file_id = ""
            
            if message.photo:
                photo = message.photo[-1]
                image_file_id = photo.file_id
                image_url = "🖼️"
            elif message.text and message.text.strip().lower() != 'пропустить':
                image_url = message.text.strip()
            
            card_id = await add_tariff_card(
                data['title'],
                data['price'],
                data['description'],
                image_url,
                image_file_id,
                card_type
            )
            
            type_display = {
                'tariff': 'Тариф',
                'super_power': 'Супер силу',
                'roaming': 'Роуминг'
            }.get(card_type, 'Карточку')
            
            if card_id:
                await message.answer(
                    f"✅ {type_display} '{data['title']}' добавлен!\nID: {card_id}",
                    reply_markup=get_main_reply_keyboard()
                )
            else:
                await message.answer(
                    "❌ Ошибка добавления",
                    reply_markup=get_main_reply_keyboard()
                )
            
            if user_id in temp_card_data:
                del temp_card_data[user_id]
            await state.clear()
            
    except Exception as e:
        error_logger.log_error("Добавить карточку", e, message.from_user.id)
        await state.clear()

# ========== ЗАПУСК БОТА ==========
async def main():
    try:
        await db_manager.init_db()
        error_logger.logger.info("🚀 Бот запускается...")
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        error_logger.log_error("Критическая ошибка запуска", e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
