import os
import json
import time
import random
import asyncio
import logging
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional, List, Any

# Lazy imports for better startup performance
asyncpg = None
nest_asyncio = None
load_dotenv = None
BotCommand = None
InlineKeyboardButton = None
InlineKeyboardMarkup = None
Update = None
Message = None
ChatAction = None
ApplicationBuilder = None
CallbackQueryHandler = None
CommandHandler = None
ContextTypes = None
MessageHandler = None
filters = None

def _lazy_imports():
    """Lazy load heavy imports to improve startup time"""
    global asyncpg, nest_asyncio, load_dotenv
    global BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, Message, ChatAction
    global ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
    
    if asyncpg is None:
        import asyncpg
        import nest_asyncio
        from dotenv import load_dotenv
        from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, Message
        from telegram.constants import ChatAction
        from telegram.ext import (
            ApplicationBuilder,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

# Color codes for logging
class Colors:
    BLUE = '\033[94m'      # INFO/WARNING
    GREEN = '\033[92m'     # DEBUG
    YELLOW = '\033[93m'    # INFO
    RED = '\033[91m'       # ERROR
    RESET = '\033[0m'      # Reset color
    BOLD = '\033[1m'       # Bold text

class ColoredFormatter(logging.Formatter):
    """Custom formatter to add colors to entire log messages"""

    COLORS = {
        'DEBUG': Colors.GREEN,
        'INFO': Colors.YELLOW,
        'WARNING': Colors.BLUE,
        'ERROR': Colors.RED,
    }

    def format(self, record):
        # Get the original formatted message
        original_format = super().format(record)

        # Get color based on log level
        color = self.COLORS.get(record.levelname, Colors.RESET)

        # Apply color to the entire message
        colored_format = f"{color}{original_format}{Colors.RESET}"

        return colored_format

# Configure optimized logging with colors
def setup_colored_logging():
    """Setup optimized colored logging configuration"""
    logger = logging.getLogger(__name__)
    
    # Use environment variable to control log level for performance
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create console handler with optimized settings
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)

    # Create colored formatter with optimized format
    formatter = ColoredFormatter(
        fmt='%(asctime)s - [%(levelname)s] - %(message)s',  # Simplified format
        datefmt='%H:%M:%S'  # Shorter date format
    )
    console_handler.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(console_handler)

    return logger

# Initialize colored logger
logger = setup_colored_logging()

def extract_user_info(msg: Message) -> Dict[str, any]:
    """Extract user and chat information from message"""
    logger.debug("🔍 Extracting user information from message")
    u = msg.from_user
    c = msg.chat
    info = {
        "user_id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "chat_id": c.id,
        "chat_type": c.type,
        "chat_title": c.title or c.first_name or "",
        "chat_username": f"@{c.username}" if c.username else "No Username",
        "chat_link": f"https://t.me/{c.username}" if c.username else "No Link",
    }
    logger.info(
        f"📑 User info extracted: {info['full_name']} (@{info['username']}) "
        f"[ID: {info['user_id']}] in {info['chat_title']} [{info['chat_id']}] {info['chat_link']}"
    )
    return info

def log_with_user_info(level: str, message: str, user_info: Dict[str, any]) -> None:
    """Log message with user information"""
    user_detail = (
        f"👤 {user_info['full_name']} (@{user_info['username']}) "
        f"[ID: {user_info['user_id']}] | "
        f"💬 {user_info['chat_title']} [{user_info['chat_id']}] "
        f"({user_info['chat_type']}) {user_info['chat_link']}"
    )
    full_message = f"{message} | {user_detail}"

    if level.upper() == "INFO":
        logger.info(full_message)
    elif level.upper() == "DEBUG":
        logger.debug(full_message)
    elif level.upper() == "WARNING":
        logger.warning(full_message)
    elif level.upper() == "ERROR":
        logger.error(full_message)
    else:
        logger.info(full_message)

# Initialize
_lazy_imports()
nest_asyncio.apply()
load_dotenv()

logger.info("🚀 Starting AFK Bot initialization...")

# Configuration
TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATA_FILE = os.environ.get("DATA_FILE", "data.json")

if not TOKEN:
    logger.error("❌ BOT_TOKEN not found in environment variables!")
    exit(1)

if not DATABASE_URL:
    logger.error("❌ DATABASE_URL not found in environment variables!")
    exit(1)

logger.info(f"🗄️ Using PostgreSQL database")
logger.info(f"📁 Backup data file: {DATA_FILE}")

# Global database pool and prepared statements
db_pool = None
prepared_statements = {}

# Simple in-memory cache for frequently accessed data
_cache = {
    'afk_status': {},  # Cache AFK statuses
    'last_seen': {},   # Cache last seen timestamps
    'cache_time': {}   # Track cache timestamps
}
CACHE_TTL = 300  # 5 minutes cache TTL

# Performance metrics
_performance_metrics = {
    'db_queries': 0,
    'cache_hits': 0,
    'cache_misses': 0,
    'messages_processed': 0,
    'afk_operations': 0,
    'start_time': None
}

def _increment_metric(metric_name: str):
    """Increment a performance metric"""
    if metric_name in _performance_metrics:
        _performance_metrics[metric_name] += 1

def _get_performance_stats() -> Dict[str, Any]:
    """Get current performance statistics"""
    uptime = 0
    if _performance_metrics['start_time']:
        uptime = (datetime.now(timezone.utc) - _performance_metrics['start_time']).total_seconds()
    
    cache_hit_rate = 0
    total_cache_requests = _performance_metrics['cache_hits'] + _performance_metrics['cache_misses']
    if total_cache_requests > 0:
        cache_hit_rate = (_performance_metrics['cache_hits'] / total_cache_requests) * 100
    
    return {
        'uptime_seconds': uptime,
        'db_queries': _performance_metrics['db_queries'],
        'cache_hits': _performance_metrics['cache_hits'],
        'cache_misses': _performance_metrics['cache_misses'],
        'cache_hit_rate': round(cache_hit_rate, 2),
        'messages_processed': _performance_metrics['messages_processed'],
        'afk_operations': _performance_metrics['afk_operations']
    }

# Optimized message dictionaries using tuples for memory efficiency
START_MESSAGE = (
    "👋 Hello, {user}!",
    "I'm your friendly <b>AFK Assistant Bot</b> 🤖.",
    "",
    "Here's what I can do for you:",
    "🔹 <b>/afk [reason]</b> — Let everyone know you're away.",
    "🔹 <b>/back</b> — Tell everyone you're back!",
    "",
    "⏰ I'll also mark you AFK if you're inactive for a while.",
    "",
    "<i>Stay active, stay awesome!</i> ✨"
)

AFK_MESSAGES = (
    "{user} is now AFK: {reason}",
    "{user} has gone AFK: {reason}",
    "{user} is away: {reason}",
    "{user} stepped away: {reason}",
    "{user} is taking a break: {reason}",
    "{user} is currently unavailable: {reason}",
    "{user} has left the chat temporarily: {reason}",
    "{user} is offline: {reason}"
)

BACK_MESSAGES = (
    "Welcome back {user}! You were AFK for {duration}.",
    "{user} is back! Was away for {duration}.",
    "{user} has returned after being AFK for {duration}.",
    "Hey {user}! You're back after {duration} of being away.",
    "{user} is online again! Was AFK for {duration}.",
    "Good to see you back {user}! You were away for {duration}.",
    "{user} has rejoined us after {duration} of AFK time.",
    "Welcome back {user}! Your AFK lasted {duration}."
)

AFK_STATUS_MESSAGES = (
    "{user} is currently away: {reason}. Been offline for {duration}",
    "{user} stepped away: {reason}. Inactive for {duration}",
    "{user} is AFK: {reason}. Last seen {duration} ago",
    "{user} left temporarily: {reason}. Away for {duration}",
    "{user} is taking time off: {reason}. Been gone for {duration}",
    "{user} went offline: {reason}. Unavailable for {duration}",
    "{user} is unreachable: {reason}. Missing for {duration}",
    "{user} stepped out: {reason}. Been away for {duration}"
)

# Global lock for file operations (backup)
file_lock = threading.Lock()

def _get_cache_key(chat_id: int, user_id: int) -> str:
    """Generate cache key for user in chat"""
    return f"{chat_id}:{user_id}"

def _is_cache_valid(key: str) -> bool:
    """Check if cache entry is still valid"""
    if key not in _cache['cache_time']:
        return False
    return (datetime.now(timezone.utc) - _cache['cache_time'][key]).total_seconds() < CACHE_TTL

def _cache_afk_status(chat_id: int, user_id: int, status: Optional[Dict[str, Any]]):
    """Cache AFK status"""
    key = _get_cache_key(chat_id, user_id)
    _cache['afk_status'][key] = status
    _cache['cache_time'][key] = datetime.now(timezone.utc)

def _get_cached_afk_status(chat_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Get cached AFK status if valid"""
    key = _get_cache_key(chat_id, user_id)
    if _is_cache_valid(key) and key in _cache['afk_status']:
        return _cache['afk_status'][key]
    return None

def _invalidate_afk_cache(chat_id: int, user_id: int):
    """Invalidate AFK cache for user"""
    key = _get_cache_key(chat_id, user_id)
    _cache['afk_status'].pop(key, None)
    _cache['cache_time'].pop(key, None)

async def init_database():
    """Initialize database connection and create tables"""
    global db_pool
    
    logger.info("🔗 Initializing database connection...")
    
    try:
        # Create optimized connection pool
        db_pool = await asyncpg.create_pool(
            DATABASE_URL, 
            min_size=2,  # Increased minimum for better performance
            max_size=20,  # Increased maximum for better concurrency
            max_queries=50000,  # Maximum queries per connection
            max_inactive_connection_lifetime=300.0,  # 5 minutes
            command_timeout=60,  # 60 seconds timeout
            server_settings={
                'jit': 'off',  # Disable JIT for better performance in this use case
                'application_name': 'afk_bot'
            }
        )
        logger.info("✅ Database connection pool created successfully")
        
        # Create tables if they don't exist
        async with db_pool.acquire() as conn:
            # AFK status table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS afk_status (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    reason TEXT NOT NULL,
                    since TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(chat_id, user_id)
                )
            ''')
            
            # Last seen table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS last_seen (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    seen_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(chat_id, user_id)
                )
            ''')
            
            # Create optimized indexes for better performance
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_afk_status_chat_user 
                ON afk_status (chat_id, user_id)
            ''')
            
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_afk_status_since 
                ON afk_status (since)
            ''')
            
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_last_seen_chat_user 
                ON last_seen (chat_id, user_id)
            ''')
            
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_last_seen_seen_at 
                ON last_seen (seen_at DESC)
            ''')
            
            # Create composite index for common queries
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_last_seen_chat_seen_at 
                ON last_seen (chat_id, seen_at DESC)
            ''')
            
        logger.info("✅ Database tables created/verified successfully")
        
        # Prepare frequently used statements for better performance
        await _prepare_statements()
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise

async def _prepare_statements():
    """Prepare frequently used SQL statements for better performance"""
    global prepared_statements
    
    try:
        async with db_pool.acquire() as conn:
            # Prepare AFK status queries
            prepared_statements['set_afk'] = await conn.prepare('''
                INSERT INTO afk_status (chat_id, user_id, reason, since)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET reason = $3, since = $4, created_at = NOW()
            ''')
            
            prepared_statements['remove_afk'] = await conn.prepare('''
                DELETE FROM afk_status 
                WHERE chat_id = $1 AND user_id = $2
            ''')
            
            prepared_statements['get_afk'] = await conn.prepare('''
                SELECT reason, since FROM afk_status 
                WHERE chat_id = $1 AND user_id = $2
            ''')
            
            prepared_statements['update_last_seen'] = await conn.prepare('''
                INSERT INTO last_seen (chat_id, user_id, seen_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET seen_at = $3, updated_at = NOW()
            ''')
            
            prepared_statements['get_all_last_seen'] = await conn.prepare('''
                SELECT chat_id, user_id, seen_at 
                FROM last_seen
                ORDER BY seen_at DESC
            ''')
            
        logger.info("✅ Prepared statements created successfully")
        
    except Exception as e:
        logger.error(f"❌ Error preparing statements: {e}")
        # Continue without prepared statements

async def close_database():
    """Close database connection pool"""
    global db_pool
    
    if db_pool:
        await db_pool.close()
        logger.info("🔌 Database connection pool closed")

# Backup functions (keeping JSON as fallback)
def load_data():
    """Load data from JSON file with error handling (backup only)"""
    logger.debug(f"📂 Loading backup data from {DATA_FILE}")
    try:
        if not os.path.exists(DATA_FILE):
            logger.warning(f"⚠️ Backup data file {DATA_FILE} not found, creating new one")
            default_data = {"leaderboard": {}, "afk": {}, "last_seen": {}}
            save_data(default_data)
            return default_data
        
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            logger.debug(f"✅ Successfully loaded backup data")
            return data
    except Exception as e:
        logger.error(f"❌ Error loading backup data: {e}")
        return {"leaderboard": {}, "afk": {}, "last_seen": {}}

def save_data(data):
    """Save data to JSON file with error handling (backup only)"""
    logger.debug(f"💾 Saving backup data to {DATA_FILE}")
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
        logger.debug("✅ Backup data saved successfully")
    except Exception as e:
        logger.error(f"❌ Error saving backup data to {DATA_FILE}: {e}")

async def set_afk(chat_id: int, user_id: int, reason: str, since: datetime):
    """Set user as AFK with database storage and caching"""
    logger.debug(f"⏰ Setting AFK for user {user_id} in chat {chat_id} with reason: {reason}")
    
    try:
        _increment_metric('db_queries')
        _increment_metric('afk_operations')
        async with db_pool.acquire() as conn:
            if 'set_afk' in prepared_statements:
                await prepared_statements['set_afk'].fetch(chat_id, user_id, reason, since)
            else:
                await conn.execute('''
                    INSERT INTO afk_status (chat_id, user_id, reason, since)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (chat_id, user_id)
                    DO UPDATE SET reason = $3, since = $4, created_at = NOW()
                ''', chat_id, user_id, reason, since)
            
        # Cache the AFK status
        afk_status = {"reason": reason, "since": since}
        _cache_afk_status(chat_id, user_id, afk_status)
        
        logger.info(f"✅ User {user_id} set as AFK in chat {chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Error setting AFK for user {user_id}: {e}")
        # Try backup method
        try:
            with file_lock:
                data = load_data()
                data.setdefault("afk", {})
                key = f"{chat_id}:{user_id}"
                data["afk"][key] = {"reason": reason, "since": since.isoformat()}
                save_data(data)
                logger.warning(f"⚠️ Used backup storage for AFK user {user_id}")
        except Exception as backup_error:
            logger.error(f"❌ Backup storage also failed: {backup_error}")

async def remove_afk(chat_id: int, user_id: int):
    """Remove user from AFK status with database storage and cache invalidation"""
    logger.debug(f"🔄 Removing AFK status for user {user_id} in chat {chat_id}")
    
    try:
        _increment_metric('db_queries')
        _increment_metric('afk_operations')
        async with db_pool.acquire() as conn:
            if 'remove_afk' in prepared_statements:
                await prepared_statements['remove_afk'].fetch(chat_id, user_id)
            else:
                result = await conn.execute('''
                    DELETE FROM afk_status 
                    WHERE chat_id = $1 AND user_id = $2
                ''', chat_id, user_id)
            
        # Invalidate cache
        _invalidate_afk_cache(chat_id, user_id)
        
        logger.info(f"✅ AFK status removed for user {user_id} in chat {chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Error removing AFK for user {user_id}: {e}")
        # Try backup method
        try:
            with file_lock:
                data = load_data()
                key = f"{chat_id}:{user_id}"
                if key in data.get("afk", {}):
                    del data["afk"][key]
                    save_data(data)
                    logger.warning(f"⚠️ Used backup storage to remove AFK user {user_id}")
        except Exception as backup_error:
            logger.error(f"❌ Backup storage also failed: {backup_error}")

async def get_afk(chat_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Get AFK status for user with database storage and caching"""
    logger.debug(f"🔍 Checking AFK status for user {user_id} in chat {chat_id}")
    
    # Check cache first
    cached_status = _get_cached_afk_status(chat_id, user_id)
    if cached_status is not None:
        _increment_metric('cache_hits')
        logger.debug(f"✅ Found cached AFK status for user {user_id}")
        return cached_status
    else:
        _increment_metric('cache_misses')
    
    try:
        _increment_metric('db_queries')
        async with db_pool.acquire() as conn:
            if 'get_afk' in prepared_statements:
                row = await prepared_statements['get_afk'].fetchrow(chat_id, user_id)
            else:
                row = await conn.fetchrow('''
                    SELECT reason, since FROM afk_status 
                    WHERE chat_id = $1 AND user_id = $2
                ''', chat_id, user_id)
            
        if not row:
            logger.debug(f"ℹ️ User {user_id} is not AFK in chat {chat_id}")
            _cache_afk_status(chat_id, user_id, None)
            return None
            
        result = {
            "reason": row["reason"],
            "since": row["since"].replace(tzinfo=timezone.utc)
        }
        
        # Cache the result
        _cache_afk_status(chat_id, user_id, result)
        
        logger.debug(f"✅ Found AFK status for user {user_id}: {result['reason']}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error getting AFK status for user {user_id}: {e}")
        # Try backup method
        try:
            data = load_data()
            key = f"{chat_id}:{user_id}"
            entry = data.get("afk", {}).get(key)
            if not entry:
                _cache_afk_status(chat_id, user_id, None)
                return None
            entry_copy = entry.copy()
            entry_copy["since"] = datetime.fromisoformat(entry_copy["since"]).replace(tzinfo=timezone.utc)
            logger.warning(f"⚠️ Used backup storage for AFK check user {user_id}")
            _cache_afk_status(chat_id, user_id, entry_copy)
            return entry_copy
        except Exception as backup_error:
            logger.error(f"❌ Backup storage also failed: {backup_error}")
            return None

async def update_last_seen(chat_id: int, user_id: int, seen_at: datetime):
    """Update last seen timestamp for user with database storage"""
    logger.debug(f"👁️ Updating last seen for user {user_id} in chat {chat_id}")
    
    try:
        _increment_metric('db_queries')
        async with db_pool.acquire() as conn:
            if 'update_last_seen' in prepared_statements:
                await prepared_statements['update_last_seen'].fetch(chat_id, user_id, seen_at)
            else:
                await conn.execute('''
                    INSERT INTO last_seen (chat_id, user_id, seen_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (chat_id, user_id)
                    DO UPDATE SET seen_at = $3, updated_at = NOW()
                ''', chat_id, user_id, seen_at)
            
    except Exception as e:
        logger.error(f"❌ Error updating last seen for user {user_id}: {e}")
        # Try backup method
        try:
            with file_lock:
                data = load_data()
                data.setdefault("last_seen", {})
                key = f"{chat_id}:{user_id}"
                data["last_seen"][key] = seen_at.isoformat()
                save_data(data)
                logger.warning(f"⚠️ Used backup storage for last seen user {user_id}")
        except Exception as backup_error:
            logger.error(f"❌ Backup storage also failed: {backup_error}")

async def get_all_last_seen() -> List[Dict[str, Any]]:
    """Get all last seen records with database storage"""
    logger.debug("📊 Retrieving all last seen records")
    
    try:
        _increment_metric('db_queries')
        async with db_pool.acquire() as conn:
            if 'get_all_last_seen' in prepared_statements:
                rows = await prepared_statements['get_all_last_seen'].fetch()
            else:
                rows = await conn.fetch('''
                    SELECT chat_id, user_id, seen_at 
                    FROM last_seen
                    ORDER BY seen_at DESC
                ''')
            
        items = []
        for row in rows:
            items.append({
                "chat_id": row["chat_id"],
                "user_id": row["user_id"],
                "seen_at": row["seen_at"].replace(tzinfo=timezone.utc)
            })
            
        logger.debug(f"✅ Retrieved {len(items)} last seen records from database")
        return items
        
    except Exception as e:
        logger.error(f"❌ Error getting last seen records: {e}")
        # Try backup method
        try:
            data = load_data()
            items = []
            for key, iso in data.get("last_seen", {}).items():
                chat_id, user_id = key.split(":")
                items.append({
                    "chat_id": int(chat_id),
                    "user_id": int(user_id),
                    "seen_at": datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
                })
            logger.warning(f"⚠️ Used backup storage for last seen records: {len(items)} records")
            return items
        except Exception as backup_error:
            logger.error(f"❌ Backup storage also failed: {backup_error}")
            return []

def format_afk_time(delta: timedelta) -> str:
    """Format time delta into human readable string"""
    seconds = int(delta.total_seconds())
    years, remainder = divmod(seconds, 31536000)
    months, remainder = divmod(remainder, 2592000)
    days, remainder = divmod(remainder, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if years > 0:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months > 0:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if days > 0:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds > 0 or not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " ".join(parts)

def create_delete_keyboard():
    """Create inline keyboard with delete button"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️", callback_data="delete_message")]
    ])

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle delete button callback"""
    query = update.callback_query
    user_info = extract_user_info(query.message)
    
    log_with_user_info("INFO", "🗑️ Delete button pressed", user_info)

    try:
        await query.message.delete()
        await query.answer("🗑️ Message deleted!", show_alert=False)
        log_with_user_info("INFO", "✅ Message successfully deleted", user_info)
    except Exception as e:
        logger.error(f"❌ Failed to delete message: {e}")
        await query.answer("💬 Failed to delete message!", show_alert=True)
        log_with_user_info("ERROR", f"❌ Failed to delete message: {e}", user_info)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_info = extract_user_info(update.message)
    log_with_user_info("INFO", "🚀 /start command received", user_info)
    
    try:
        user = update.effective_user
        bot_username = context.bot.username
        
        logger.debug(f"🤖 Bot username: {bot_username}")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Updates", url="https://t.me/WorkGlows"),
                InlineKeyboardButton("Support", url="https://t.me/SoulMeetsHQ")
            ],
            [
                InlineKeyboardButton("Add Me To Your Group", url=f"https://t.me/{bot_username}?startgroup=true")
            ]
        ])

        message_text = "\n".join(START_MESSAGE).format(user=user.mention_html())

        await update.message.reply_html(message_text, reply_markup=keyboard)
        log_with_user_info("INFO", "✅ Start message sent successfully", user_info)
    except Exception as e:
        logger.error(f"❌ Error in start command: {e}")
        log_with_user_info("ERROR", f"❌ Error in start command: {e}", user_info)

async def afk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /afk command"""
    user_info = extract_user_info(update.message)
    reason = " ".join(context.args) if context.args else "AFK"
    
    log_with_user_info("INFO", f"😴 /afk command received with reason: {reason}", user_info)
    
    try:
        user = update.effective_user
        chat_id = update.effective_chat.id
        now = datetime.now(timezone.utc)
        
        await set_afk(chat_id, user.id, reason, now)

        message = random.choice(AFK_MESSAGES).format(
            user=user.mention_html(),
            reason=reason
        )

        sent_msg = await update.message.reply_html(
            message,
            reply_markup=create_delete_keyboard()
        )
        
        log_with_user_info("INFO", f"✅ AFK status set successfully", user_info)
    except Exception as e:
        logger.error(f"❌ Error in afk command: {e}")
        log_with_user_info("ERROR", f"❌ Error in afk command: {e}", user_info)

async def back_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /back command"""
    user_info = extract_user_info(update.message)
    log_with_user_info("INFO", "🔙 /back command received", user_info)
    
    try:
        user = update.effective_user
        chat_id = update.effective_chat.id
        afk = await get_afk(chat_id, user.id)
        
        if afk:
            delta = datetime.now(timezone.utc) - afk["since"]
            await remove_afk(chat_id, user.id)

            message = random.choice(BACK_MESSAGES).format(
                user=user.mention_html(),
                duration=format_afk_time(delta)
            )

            sent_msg = await update.message.reply_html(
                message,
                reply_markup=create_delete_keyboard()
            )
            log_with_user_info("INFO", f"✅ User returned from AFK after {format_afk_time(delta)}", user_info)
        else:
            sent_msg = await update.message.reply_text(
                "You are not AFK.",
                reply_markup=create_delete_keyboard()
            )
            log_with_user_info("INFO", "ℹ️ User tried /back but was not AFK", user_info)
    except Exception as e:
        logger.error(f"❌ Error in back command: {e}")
        log_with_user_info("ERROR", f"❌ Error in back command: {e}", user_info)

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command"""
    user_info = extract_user_info(update.message)
    log_with_user_info("INFO", "🏓 /ping command received", user_info)
    
    try:
        start_time = time.time()

        # Send initial "Pinging..." message
        sent_msg = await update.message.reply_text("🛰️ Pinging...")

        # Calculate ping time
        end_time = time.time()
        ping_ms = round((end_time - start_time) * 1000, 2)

        # Create the pong message with group links (no preview)
        pong_text = f'🏓 <a href="https://t.me/SoulMeetsHQ">Pong!</a> {ping_ms}ms'

        # Edit the message to show pong result
        await sent_msg.edit_text(
            pong_text,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
        log_with_user_info("INFO", f"✅ Ping response sent: {ping_ms}ms", user_info)
    except Exception as e:
        logger.error(f"❌ Error in ping command: {e}")
        log_with_user_info("ERROR", f"❌ Error in ping command: {e}", user_info)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command for performance monitoring"""
    user_info = extract_user_info(update.message)
    log_with_user_info("INFO", "📊 /stats command received", user_info)
    
    try:
        stats = _get_performance_stats()
        
        stats_text = f"""📊 <b>Bot Performance Statistics</b>

⏱️ <b>Uptime:</b> {int(stats['uptime_seconds'])} seconds
🗄️ <b>Database Queries:</b> {stats['db_queries']}
💾 <b>Cache Hits:</b> {stats['cache_hits']}
❌ <b>Cache Misses:</b> {stats['cache_misses']}
📈 <b>Cache Hit Rate:</b> {stats['cache_hit_rate']}%
💬 <b>Messages Processed:</b> {stats['messages_processed']}
😴 <b>AFK Operations:</b> {stats['afk_operations']}

<i>Performance optimized for speed and efficiency!</i> 🚀"""
        
        await update.message.reply_html(
            stats_text,
            reply_markup=create_delete_keyboard()
        )
        log_with_user_info("INFO", "✅ Stats message sent successfully", user_info)
    except Exception as e:
        logger.error(f"❌ Error in stats command: {e}")
        log_with_user_info("ERROR", f"❌ Error in stats command: {e}", user_info)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Optimized handler for all incoming messages"""
    if not update.message:
        return
    
    try:
        chat_id = update.effective_chat.id
        user = update.effective_user
        
        if user.is_bot:
            logger.debug(f"🤖 Ignoring message from bot: {user.username}")
            return
            
        user_info = extract_user_info(update.message)
        _increment_metric('messages_processed')
        logger.debug(f"💬 Processing message from user")
        
        now = datetime.now(timezone.utc)
        
        # Use asyncio.gather for concurrent operations
        tasks = []
        
        # Always update last seen
        tasks.append(update_last_seen(chat_id, user.id, now))
        
        # Check if user was AFK
        afk_task = get_afk(chat_id, user.id)
        tasks.append(afk_task)
        
        # Check if replying to AFK user
        reply_afk_task = None
        if update.message.reply_to_message:
            replied_user = update.message.reply_to_message.from_user
            if replied_user and not replied_user.is_bot:
                reply_afk_task = get_afk(chat_id, replied_user.id)
                tasks.append(reply_afk_task)
        
        # Execute all database operations concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        user_afk = results[1] if len(results) > 1 else None
        reply_afk = results[2] if len(results) > 2 and reply_afk_task else None
        
        # Handle user returning from AFK
        if user_afk and not isinstance(user_afk, Exception):
            delta = now - user_afk["since"]
            await remove_afk(chat_id, user.id)

            message = random.choice(BACK_MESSAGES).format(
                user=user.mention_html(),
                duration=format_afk_time(delta)
            )

            await update.message.reply_html(
                message,
                reply_markup=create_delete_keyboard()
            )
            log_with_user_info("INFO", f"🔄 Auto-returned user from AFK after {format_afk_time(delta)}", user_info)
        
        # Handle reply to AFK user
        if reply_afk and not isinstance(reply_afk, Exception) and update.message.reply_to_message:
            replied_user = update.message.reply_to_message.from_user
            delta = now - reply_afk["since"]

            message = random.choice(AFK_STATUS_MESSAGES).format(
                user=replied_user.mention_html(),
                reason=reply_afk['reason'],
                duration=format_afk_time(delta)
            )

            await update.message.reply_html(
                message,
                reply_markup=create_delete_keyboard()
            )
            log_with_user_info("INFO", f"ℹ️ Notified about AFK user: {replied_user.full_name}", user_info)
            
    except Exception as e:
        logger.error(f"❌ Error in message handler: {e}")

async def check_inactivity():
    """Optimized background task to check for inactive users and set them AFK"""
    logger.info("⏰ Starting optimized inactivity checker task")
    await asyncio.sleep(10)  # Initial delay
    
    check_count = 0
    batch_size = 50  # Process users in batches for better performance
    inactivity_threshold = timedelta(minutes=60)
    
    while True:
        try:
            check_count += 1
            logger.debug(f"🔍 Running inactivity check #{check_count}")
            
            now = datetime.now(timezone.utc)
            records = await get_all_last_seen()
            
            inactive_users = 0
            # Process records in batches to avoid memory issues
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                
                # Use asyncio.gather for concurrent processing
                tasks = []
                for record in batch:
                    chat_id = record["chat_id"]
                    user_id = record["user_id"]
                    last_time = record["seen_at"]
                    inactive_time = now - last_time
                    
                    if inactive_time > inactivity_threshold:
                        tasks.append(_check_and_set_afk(chat_id, user_id, last_time, inactive_time))
                
                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    inactive_users += sum(1 for result in results if result is True)
            
            if check_count % 10 == 0:  # Log summary every 10 checks
                logger.info(f"📊 Inactivity check #{check_count}: {len(records)} users monitored, {inactive_users} set as AFK")
                
        except Exception as e:
            logger.error(f"❌ Error in inactivity checker: {e}")
        
        await asyncio.sleep(60)  # Check every minute

async def _check_and_set_afk(chat_id: int, user_id: int, last_time: datetime, inactive_time: timedelta) -> bool:
    """Helper function to check and set AFK status for a single user"""
    try:
        afk = await get_afk(chat_id, user_id)
        if not afk:
            await set_afk(chat_id, user_id, "No activity", last_time)
            logger.info(f"😴 Auto-set user {user_id} as AFK due to {format_afk_time(inactive_time)} of inactivity")
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Error checking AFK for user {user_id}: {e}")
        return False

async def main():
    """Main bot function"""
    logger.info("🤖 Starting main bot function")
    
    # Initialize performance metrics
    _performance_metrics['start_time'] = datetime.now(timezone.utc)
    
    try:
        # Initialize database first
        await init_database()
        
        app = ApplicationBuilder().token(TOKEN).build()
        logger.info("✅ Bot application built successfully")
        
        # Only register visible commands in the menu
        commands = [
            BotCommand("start", "Start bot and see help"),
            BotCommand("afk", "Set yourself AFK"),
            BotCommand("back", "Return from AFK"),
            BotCommand("stats", "View bot performance statistics"),
        ]
        
        await app.bot.set_my_commands(commands)
        logger.info(f"📋 Bot commands registered: {[cmd.command for cmd in commands]}")

        # Add handlers (ping command is added but not registered in menu)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("afk", afk_command))
        app.add_handler(CommandHandler("back", back_command))
        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("ping", ping_command))  # Hidden command
        app.add_handler(CallbackQueryHandler(delete_callback, pattern="delete_message"))
        app.add_handler(MessageHandler(filters.ALL, message_handler))
        
        logger.info("✅ All handlers registered successfully")

        # Start background task
        asyncio.create_task(check_inactivity())
        logger.info("⏰ Inactivity checker task started")
        
        logger.info("🚀 Bot started with PostgreSQL database storage...")
        
        try:
            await app.run_polling()
        finally:
            # Clean up database connection when bot stops
            await close_database()
            
    except Exception as e:
        logger.error(f"❌ Critical error in main function: {e}")
        await close_database()
        raise

class DummyHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for health checks"""
    
    def do_GET(self):
        logger.debug(f"🌐 Health check request from {self.client_address[0]}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"AFK bot is alive!")

    def do_HEAD(self):
        logger.debug(f"🌐 HEAD request from {self.client_address[0]}")
        self.send_response(200)
        self.end_headers()
    
    def log_message(self, format, *args):
        """Override to prevent default HTTP logging"""
        pass

def start_dummy_server():
    """Start HTTP server for health checks"""
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), DummyHandler)
        logger.info(f"🌐 HTTP health check server started on port {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Error starting HTTP server: {e}")

if __name__ == "__main__":
    logger.info("🎬 Application starting...")
    
    try:
        # Start HTTP server in background
        threading.Thread(target=start_dummy_server, daemon=True).start()
        
        # Start main bot
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("⏹️ Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        raise