import os
import json
import time
import random
import asyncio
import asyncpg
import nest_asyncio
import logging
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional, List, Any
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

# Configure logging with colors
def setup_colored_logging():
    """Setup colored logging configuration"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)

    # Create colored formatter with enhanced format
    formatter = ColoredFormatter(
        fmt='%(asctime)s - %(name)s - [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
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

# Global database pool
db_pool = None

# Message dictionaries
START_MESSAGE = [
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
]

AFK_MESSAGES = [
    "{user} is now AFK: {reason}",
    "{user} has gone AFK: {reason}",
    "{user} is away: {reason}",
    "{user} stepped away: {reason}",
    "{user} is taking a break: {reason}",
    "{user} is currently unavailable: {reason}",
    "{user} has left the chat temporarily: {reason}",
    "{user} is offline: {reason}"
]

BACK_MESSAGES = [
    "Welcome back {user}! You were AFK for {duration}.",
    "{user} is back! Was away for {duration}.",
    "{user} has returned after being AFK for {duration}.",
    "Hey {user}! You're back after {duration} of being away.",
    "{user} is online again! Was AFK for {duration}.",
    "Good to see you back {user}! You were away for {duration}.",
    "{user} has rejoined us after {duration} of AFK time.",
    "Welcome back {user}! Your AFK lasted {duration}."
]

AFK_STATUS_MESSAGES = [
    "{user} is currently away: {reason}. Been offline for {duration}",
    "{user} stepped away: {reason}. Inactive for {duration}",
    "{user} is AFK: {reason}. Last seen {duration} ago",
    "{user} left temporarily: {reason}. Away for {duration}",
    "{user} is taking time off: {reason}. Been gone for {duration}",
    "{user} went offline: {reason}. Unavailable for {duration}",
    "{user} is unreachable: {reason}. Missing for {duration}",
    "{user} stepped out: {reason}. Been away for {duration}"
]

# Global lock for file operations (backup)
file_lock = threading.Lock()

async def init_database():
    """Initialize database connection and create tables"""
    global db_pool
    
    logger.info("🔗 Initializing database connection...")
    
    try:
        # Create connection pool
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
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
            
            # Create indexes for better performance
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_afk_status_chat_user 
                ON afk_status (chat_id, user_id)
            ''')
            
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_last_seen_chat_user 
                ON last_seen (chat_id, user_id)
            ''')
            
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_last_seen_seen_at 
                ON last_seen (seen_at)
            ''')
            
        logger.info("✅ Database tables created/verified successfully")
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise

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
    """Set user as AFK with database storage"""
    logger.debug(f"⏰ Setting AFK for user {user_id} in chat {chat_id} with reason: {reason}")
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO afk_status (chat_id, user_id, reason, since)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET reason = $3, since = $4, created_at = NOW()
            ''', chat_id, user_id, reason, since)
            
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
    """Remove user from AFK status with database storage"""
    logger.debug(f"🔄 Removing AFK status for user {user_id} in chat {chat_id}")
    
    try:
        async with db_pool.acquire() as conn:
            result = await conn.execute('''
                DELETE FROM afk_status 
                WHERE chat_id = $1 AND user_id = $2
            ''', chat_id, user_id)
            
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
    """Get AFK status for user with database storage"""
    logger.debug(f"🔍 Checking AFK status for user {user_id} in chat {chat_id}")
    
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT reason, since FROM afk_status 
                WHERE chat_id = $1 AND user_id = $2
            ''', chat_id, user_id)
            
        if not row:
            logger.debug(f"ℹ️ User {user_id} is not AFK in chat {chat_id}")
            return None
            
        result = {
            "reason": row["reason"],
            "since": row["since"].replace(tzinfo=timezone.utc)
        }
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
                return None
            entry_copy = entry.copy()
            entry_copy["since"] = datetime.fromisoformat(entry_copy["since"]).replace(tzinfo=timezone.utc)
            logger.warning(f"⚠️ Used backup storage for AFK check user {user_id}")
            return entry_copy
        except Exception as backup_error:
            logger.error(f"❌ Backup storage also failed: {backup_error}")
            return None

async def update_last_seen(chat_id: int, user_id: int, seen_at: datetime):
    """Update last seen timestamp for user with database storage"""
    logger.debug(f"👁️ Updating last seen for user {user_id} in chat {chat_id}")
    
    try:
        async with db_pool.acquire() as conn:
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
        async with db_pool.acquire() as conn:
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


async def delete_message_after_delay(message: Message, delay: int):
    """Delete a message after a specified delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
        logger.info(f"🗑️ Automatically deleted message {message.message_id} after {delay} seconds.")
    except Exception as e:
        logger.warning(f"⚠️ Could not auto-delete message {message.message_id}: {e}")

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
        asyncio.create_task(delete_message_after_delay(sent_msg, 60))
        
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
            asyncio.create_task(delete_message_after_delay(sent_msg, 60))
            log_with_user_info("INFO", f"✅ User returned from AFK after {format_afk_time(delta)}", user_info)
        else:
            sent_msg = await update.message.reply_text(
                "You are not AFK.",
                reply_markup=create_delete_keyboard()
            )
            asyncio.create_task(delete_message_after_delay(sent_msg, 60))
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

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all incoming messages"""
    if not update.message:
        return
    
    try:
        chat_id = update.effective_chat.id
        user = update.effective_user
        
        if user.is_bot:
            logger.debug(f"🤖 Ignoring message from bot: {user.username}")
            return
            
        user_info = extract_user_info(update.message)
        logger.debug(f"💬 Processing message from user")
        
        now = datetime.now(timezone.utc)
        await update_last_seen(chat_id, user.id, now)
        
        # Check if user was AFK and auto-return them
        afk = await get_afk(chat_id, user.id)
        if afk:
            delta = now - afk["since"]
            await remove_afk(chat_id, user.id)

            message = random.choice(BACK_MESSAGES).format(
                user=user.mention_html(),
                duration=format_afk_time(delta)
            )

            sent_msg = await update.message.reply_html(
                message,
                reply_markup=create_delete_keyboard()
            )
            asyncio.create_task(delete_message_after_delay(sent_msg, 60))
            log_with_user_info("INFO", f"🔄 Auto-returned user from AFK after {format_afk_time(delta)}", user_info)
        
        # Check if user replied to someone who is AFK
        if update.message.reply_to_message:
            replied_user = update.message.reply_to_message.from_user
            if replied_user:
                logger.debug(f"📤 Message is a reply to user {replied_user.id}")
                afk = await get_afk(chat_id, replied_user.id)
                if afk:
                    delta = now - afk["since"]

                    message = random.choice(AFK_STATUS_MESSAGES).format(
                        user=replied_user.mention_html(),
                        reason=afk['reason'],
                        duration=format_afk_time(delta)
                    )

                    sent_msg = await update.message.reply_html(
                        message,
                        reply_markup=create_delete_keyboard()
                    )
                    asyncio.create_task(delete_message_after_delay(sent_msg, 60))
                    log_with_user_info("INFO", f"ℹ️ Notified about AFK user: {replied_user.full_name}", user_info)
    except Exception as e:
        logger.error(f"❌ Error in message handler: {e}")

async def check_inactivity():
    """Background task to check for inactive users and set them AFK"""
    logger.info("⏰ Starting inactivity checker task")
    await asyncio.sleep(10)  # Initial delay
    
    check_count = 0
    while True:
        try:
            check_count += 1
            logger.debug(f"🔍 Running inactivity check #{check_count}")
            
            now = datetime.now(timezone.utc)
            records = await get_all_last_seen()
            
            inactive_users = 0
            for record in records:
                chat_id = record["chat_id"]
                user_id = record["user_id"]
                last_time = record["seen_at"]
                inactive_time = now - last_time
                
                afk = await get_afk(chat_id, user_id)
                if inactive_time > timedelta(minutes=60) and not afk:
                    await set_afk(chat_id, user_id, "No activity", last_time)
                    inactive_users += 1
                    logger.info(f"😴 Auto-set user {user_id} as AFK due to {format_afk_time(inactive_time)} of inactivity")
            
            if check_count % 10 == 0:  # Log summary every 10 checks
                logger.info(f"📊 Inactivity check #{check_count}: {len(records)} users monitored, {inactive_users} set as AFK")
                
        except Exception as e:
            logger.error(f"❌ Error in inactivity checker: {e}")
        
        await asyncio.sleep(60)  # Check every minute

async def main():
    """Main bot function"""
    logger.info("🤖 Starting main bot function")
    
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
        ]
        
        await app.bot.set_my_commands(commands)
        logger.info(f"📋 Bot commands registered: {[cmd.command for cmd in commands]}")

        # Add handlers (ping command is added but not registered in menu)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("afk", afk_command))
        app.add_handler(CommandHandler("back", back_command))
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
