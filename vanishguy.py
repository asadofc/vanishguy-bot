import os
import json
import time
import random
import asyncio
import asyncpg
import nest_asyncio
import logging
import aiofiles
import aiohttp
from datetime import datetime, timezone, timedelta
from aiohttp import web
from typing import Dict, Optional, List, Any, Set
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
from concurrent.futures import ThreadPoolExecutor
from functools import partial

# IMPORTANT: Apply nest_asyncio BEFORE setting uvloop policy
nest_asyncio.apply()

try:
    import uvloop
    # Use uvloop for better performance (only if available)
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    # Fall back to default event loop if uvloop is not available
    pass

# Color codes for logging
class Colors:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support"""
    COLORS = {
        'DEBUG': Colors.GREEN,
        'INFO': Colors.YELLOW,
        'WARNING': Colors.BLUE,
        'ERROR': Colors.RED,
    }

    def format(self, record):
        original_format = super().format(record)
        color = self.COLORS.get(record.levelname, Colors.RESET)
        return f"{color}{original_format}{Colors.RESET}"

# Async logging handler
class AsyncLogHandler(logging.Handler):
    """Asynchronous logging handler using queue"""
    def __init__(self):
        super().__init__()
        self.queue = asyncio.Queue(maxsize=10000)
        self.task = None

    async def start(self):
        """Start the async log processor"""
        self.task = asyncio.create_task(self._process_logs())

    async def stop(self):
        """Stop the async log processor"""
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _process_logs(self):
        """Process logs from queue asynchronously"""
        while True:
            try:
                record = await self.queue.get()
                if record:
                    self._emit(record)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def emit(self, record):
        """Queue log record for async processing"""
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull:
            # Drop log if queue is full (non-blocking)
            pass

    def _emit(self, record):
        """Actually emit the log record"""
        try:
            msg = self.format(record)
            print(msg)
        except Exception:
            self.handleError(record)

# Configure async logging
async_handler = AsyncLogHandler()

def setup_colored_logging():
    """Setup colored async logging"""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    formatter = ColoredFormatter(
        fmt='%(asctime)s - %(name)s - [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    async_handler.setFormatter(formatter)
    logger.addHandler(async_handler)
    
    return logger

logger = setup_colored_logging()

# Initialize
load_dotenv()

# Configuration
TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATA_FILE = os.environ.get("DATA_FILE", "data.json")

if not TOKEN:
    logger.error("❌ BOT_TOKEN not found!")
    exit(1)

if not DATABASE_URL:
    logger.error("❌ DATABASE_URL not found!")
    exit(1)

# Connection pools and caches
db_pool = None
redis_pool = None  # Optional Redis for caching
executor = ThreadPoolExecutor(max_workers=4)

# In-memory caches with TTL
class AsyncCache:
    """Async TTL cache implementation"""
    def __init__(self, ttl_seconds=300):
        self.cache = {}
        self.ttl = ttl_seconds
        self.lock = asyncio.Lock()

    async def get(self, key):
        async with self.lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    return value
                else:
                    del self.cache[key]
            return None

    async def set(self, key, value):
        async with self.lock:
            self.cache[key] = (value, time.time())

    async def delete(self, key):
        async with self.lock:
            if key in self.cache:
                del self.cache[key]

    async def clear_expired(self):
        """Clear expired cache entries"""
        async with self.lock:
            current_time = time.time()
            expired_keys = [
                key for key, (_, timestamp) in self.cache.items()
                if current_time - timestamp >= self.ttl
            ]
            for key in expired_keys:
                del self.cache[key]

# Initialize caches
afk_cache = AsyncCache(ttl_seconds=60)
last_seen_cache = AsyncCache(ttl_seconds=30)
user_info_cache = AsyncCache(ttl_seconds=300)

# Message rate limiter
class AsyncRateLimiter:
    """Async rate limiter for message handling"""
    def __init__(self, rate=10, per=1.0):
        self.rate = rate
        self.per = per
        self.allowance = rate
        self.last_check = time.monotonic()
        self.lock = asyncio.Lock()

    async def is_allowed(self):
        async with self.lock:
            current = time.monotonic()
            time_passed = current - self.last_check
            self.last_check = current
            self.allowance += time_passed * (self.rate / self.per)
            
            if self.allowance > self.rate:
                self.allowance = self.rate
            
            if self.allowance < 1.0:
                return False
            
            self.allowance -= 1.0
            return True

# Rate limiters per user
user_rate_limiters = {}

# Message queues
message_queue = asyncio.Queue(maxsize=1000)
deletion_queue = asyncio.Queue(maxsize=500)

# Messages
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
    "{user} stepped away: {reason}"
]

BACK_MESSAGES = [
    "Welcome back {user}! You were AFK for {duration}.",
    "{user} is back! Was away for {duration}.",
    "{user} has returned after being AFK for {duration}."
]

AFK_STATUS_MESSAGES = [
    "{user} is currently away: {reason}. Been offline for {duration}",
    "{user} stepped away: {reason}. Inactive for {duration}",
    "{user} is AFK: {reason}. Last seen {duration} ago"
]

# Utility functions
def extract_user_info(msg: Message) -> Dict[str, any]:
    """Extract user info (synchronous, fast)"""
    u = msg.from_user
    c = msg.chat
    return {
        "user_id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "chat_id": c.id,
        "chat_type": c.type,
        "chat_title": c.title or c.first_name or "",
        "chat_username": f"@{c.username}" if c.username else "No Username",
        "chat_link": f"https://t.me/{c.username}" if c.username else "No Link",
    }

async def init_database():
    """Initialize database with connection pooling"""
    global db_pool
    
    logger.info("🔗 Initializing database...")
    
    try:
        # Create connection pool with optimized settings
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=5,
            max_size=20,
            max_queries=50000,
            max_inactive_connection_lifetime=300,
            command_timeout=10,
            statement_cache_size=0  # Disable for better memory usage
        )
        
        logger.info("✅ Database pool created")
        
        # Create tables with proper indexes
        async with db_pool.acquire() as conn:
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
            
            # Create optimized indexes (handle errors gracefully)
            indexes = [
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_afk_chat_user ON afk_status (chat_id, user_id)',
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_last_seen_chat_user ON last_seen (chat_id, user_id)',
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_last_seen_at ON last_seen (seen_at DESC)'
            ]
            
            for index_sql in indexes:
                try:
                    await conn.execute(index_sql)
                except Exception as idx_error:
                    logger.warning(f"⚠️ Index creation warning: {idx_error}")
            
        logger.info("✅ Database initialized")
        
    except Exception as e:
        logger.error(f"❌ Database init failed: {e}")
        raise

async def close_database():
    """Close database pool"""
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("🔌 Database closed")

# Async file operations
async def load_data():
    """Load data asynchronously"""
    try:
        if not os.path.exists(DATA_FILE):
            default_data = {"leaderboard": {}, "afk": {}, "last_seen": {}}
            await save_data(default_data)
            return default_data
        
        async with aiofiles.open(DATA_FILE, 'r') as f:
            content = await f.read()
            return json.loads(content)
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}")
        return {"leaderboard": {}, "afk": {}, "last_seen": {}}

async def save_data(data):
    """Save data asynchronously"""
    try:
        async with aiofiles.open(DATA_FILE, 'w') as f:
            await f.write(json.dumps(data, indent=4))
    except Exception as e:
        logger.error(f"❌ Error saving data: {e}")

# Database operations with caching
async def set_afk(chat_id: int, user_id: int, reason: str, since: datetime):
    """Set AFK status with caching"""
    cache_key = f"{chat_id}:{user_id}"
    
    # Update cache immediately
    await afk_cache.set(cache_key, {"reason": reason, "since": since})
    
    # Database update (non-blocking)
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO afk_status (chat_id, user_id, reason, since)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET reason = $3, since = $4
            ''', chat_id, user_id, reason, since)
    except Exception as e:
        logger.error(f"❌ DB error setting AFK: {e}")

async def remove_afk(chat_id: int, user_id: int):
    """Remove AFK status with cache invalidation"""
    cache_key = f"{chat_id}:{user_id}"
    
    # Clear cache immediately
    await afk_cache.delete(cache_key)
    
    # Database update (non-blocking)
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                DELETE FROM afk_status 
                WHERE chat_id = $1 AND user_id = $2
            ''', chat_id, user_id)
    except Exception as e:
        logger.error(f"❌ DB error removing AFK: {e}")

async def get_afk(chat_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Get AFK status with caching"""
    cache_key = f"{chat_id}:{user_id}"
    
    # Check cache first
    cached = await afk_cache.get(cache_key)
    if cached:
        return cached
    
    # Database lookup
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT reason, since FROM afk_status 
                WHERE chat_id = $1 AND user_id = $2
            ''', chat_id, user_id)
        
        if row:
            result = {
                "reason": row["reason"],
                "since": row["since"].replace(tzinfo=timezone.utc)
            }
            # Update cache
            await afk_cache.set(cache_key, result)
            return result
            
    except Exception as e:
        logger.error(f"❌ DB error getting AFK: {e}")
    
    return None

async def batch_update_last_seen(updates: List[tuple]):
    """Batch update last seen timestamps"""
    if not updates:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.executemany('''
                INSERT INTO last_seen (chat_id, user_id, seen_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET seen_at = $3, updated_at = NOW()
            ''', updates)
    except Exception as e:
        logger.error(f"❌ Batch update error: {e}")

async def update_last_seen(chat_id: int, user_id: int, seen_at: datetime):
    """Queue last seen update for batching"""
    cache_key = f"{chat_id}:{user_id}"
    await last_seen_cache.set(cache_key, seen_at)
    
    # Queue for batch processing
    try:
        await message_queue.put(('last_seen', (chat_id, user_id, seen_at)))
    except asyncio.QueueFull:
        # Drop update if queue is full
        pass

async def get_all_last_seen() -> List[Dict[str, Any]]:
    """Get all last seen records efficiently"""
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT chat_id, user_id, seen_at 
                FROM last_seen
                WHERE seen_at > NOW() - INTERVAL '2 hours'
                ORDER BY seen_at DESC
                LIMIT 1000
            ''')
        
        return [
            {
                "chat_id": row["chat_id"],
                "user_id": row["user_id"],
                "seen_at": row["seen_at"].replace(tzinfo=timezone.utc)
            }
            for row in rows
        ]
    except Exception as e:
        logger.error(f"❌ Error getting last seen: {e}")
        return []

def format_afk_time(delta: timedelta) -> str:
    """Format time delta efficiently"""
    seconds = int(delta.total_seconds())
    
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"

def create_delete_keyboard():
    """Create inline keyboard"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️", callback_data="delete_message")]
    ])

# Message processors
async def process_message_queue():
    """Process message queue in batches"""
    last_seen_batch = []
    
    while True:
        try:
            # Collect messages for batch processing
            deadline = asyncio.create_task(asyncio.sleep(0.5))
            
            while len(last_seen_batch) < 50:  # Max batch size
                try:
                    msg_task = asyncio.create_task(message_queue.get())
                    done, pending = await asyncio.wait(
                        {msg_task, deadline},
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    if msg_task in done:
                        msg_type, data = msg_task.result()
                        if msg_type == 'last_seen':
                            last_seen_batch.append(data)
                    else:
                        msg_task.cancel()
                        break
                        
                except asyncio.TimeoutError:
                    break
            
            # Process batches
            if last_seen_batch:
                await batch_update_last_seen(last_seen_batch)
                last_seen_batch.clear()
                
        except Exception as e:
            logger.error(f"❌ Queue processor error: {e}")
        
        await asyncio.sleep(0.1)

async def process_deletion_queue():
    """Process message deletions asynchronously"""
    while True:
        try:
            message, delay = await deletion_queue.get()
            asyncio.create_task(delete_message_after_delay(message, delay))
        except Exception as e:
            logger.error(f"❌ Deletion queue error: {e}")
        await asyncio.sleep(0.1)

async def delete_message_after_delay(message: Message, delay: int):
    """Delete message after delay"""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

# Command handlers
async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle delete callback"""
    query = update.callback_query
    try:
        await query.message.delete()
        await query.answer("🗑️ Deleted!", show_alert=False)
    except Exception:
        await query.answer("💬 Failed!", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    bot_username = context.bot.username
    
    # Send typing action (non-blocking)
    asyncio.create_task(
        context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING
        )
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Updates", url="https://t.me/WorkGlows"),
            InlineKeyboardButton("Support", url="https://t.me/SoulMeetsHQ")
        ],
        [
            InlineKeyboardButton(
                "Add Me To Your Group",
                url=f"https://t.me/{bot_username}?startgroup=true"
            )
        ]
    ])
    
    message_text = "\n".join(START_MESSAGE).format(user=user.mention_html())
    await update.message.reply_html(message_text, reply_markup=keyboard)

async def afk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /afk command"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    reason = " ".join(context.args) if context.args else "AFK"
    now = datetime.now(timezone.utc)
    
    # Set AFK (non-blocking)
    asyncio.create_task(set_afk(chat_id, user.id, reason, now))
    
    message = random.choice(AFK_MESSAGES).format(
        user=user.mention_html(),
        reason=reason
    )
    
    sent_msg = await update.message.reply_html(
        message,
        reply_markup=create_delete_keyboard()
    )
    
    # Queue for deletion
    try:
        await deletion_queue.put((sent_msg, 60))
    except asyncio.QueueFull:
        pass

async def back_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /back command"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    afk = await get_afk(chat_id, user.id)
    
    if afk:
        delta = datetime.now(timezone.utc) - afk["since"]
        
        # Remove AFK (non-blocking)
        asyncio.create_task(remove_afk(chat_id, user.id))
        
        message = random.choice(BACK_MESSAGES).format(
            user=user.mention_html(),
            duration=format_afk_time(delta)
        )
    else:
        message = "You are not AFK."
    
    sent_msg = await update.message.reply_html(
        message,
        reply_markup=create_delete_keyboard()
    )
    
    try:
        await deletion_queue.put((sent_msg, 60))
    except asyncio.QueueFull:
        pass

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command"""
    start_time = time.perf_counter()
    
    sent_msg = await update.message.reply_text("🛰️ Pinging...")
    
    ping_ms = round((time.perf_counter() - start_time) * 1000, 2)
    
    await sent_msg.edit_text(
        f'🏓 <a href="https://t.me/SoulMeetsHQ">Pong!</a> {ping_ms}ms',
        parse_mode='HTML',
        disable_web_page_preview=True
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages asynchronously"""
    if not update.message or update.effective_user.is_bot:
        return
    
    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id
    
    # Rate limiting
    if user_id not in user_rate_limiters:
        user_rate_limiters[user_id] = AsyncRateLimiter(rate=30, per=60)
    
    if not await user_rate_limiters[user_id].is_allowed():
        return  # Drop message if rate limited
    
    now = datetime.now(timezone.utc)
    
    # Update last seen (non-blocking)
    asyncio.create_task(update_last_seen(chat_id, user_id, now))
    
    # Check AFK status
    afk = await get_afk(chat_id, user_id)
    if afk:
        delta = now - afk["since"]
        
        # Remove AFK (non-blocking)
        asyncio.create_task(remove_afk(chat_id, user_id))
        
        message = random.choice(BACK_MESSAGES).format(
            user=user.mention_html(),
            duration=format_afk_time(delta)
        )
        
        sent_msg = await update.message.reply_html(
            message,
            reply_markup=create_delete_keyboard()
        )
        
        try:
            await deletion_queue.put((sent_msg, 60))
        except asyncio.QueueFull:
            pass
    
    # Check replies to AFK users
    if update.message.reply_to_message:
        replied_user = update.message.reply_to_message.from_user
        if replied_user:
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
                
                try:
                    await deletion_queue.put((sent_msg, 60))
                except asyncio.QueueFull:
                    pass

async def check_inactivity():
    """Check for inactive users efficiently"""
    await asyncio.sleep(10)
    
    while True:
        try:
            now = datetime.now(timezone.utc)
            records = await get_all_last_seen()
            
            # Process in parallel
            tasks = []
            for record in records:
                chat_id = record["chat_id"]
                user_id = record["user_id"]
                last_time = record["seen_at"]
                inactive_time = now - last_time
                
                if inactive_time > timedelta(minutes=60):
                    # Check if already AFK
                    afk = await get_afk(chat_id, user_id)
                    if not afk:
                        tasks.append(
                            set_afk(chat_id, user_id, "No activity", last_time)
                        )
            
            # Execute all AFK updates in parallel
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                
        except Exception as e:
            logger.error(f"❌ Inactivity checker error: {e}")
        
        await asyncio.sleep(60)

async def cache_cleaner():
    """Periodically clean expired cache entries"""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            await afk_cache.clear_expired()
            await last_seen_cache.clear_expired()
            await user_info_cache.clear_expired()
        except Exception as e:
            logger.error(f"❌ Cache cleaner error: {e}")

# Web server for health checks
async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="AFK bot is alive!", status=200)

async def start_web_server():
    """Start async web server"""
    try:
        app = web.Application()
        app.router.add_get('/', health_check)
        app.router.add_get('/health', health_check)
        
        port = int(os.environ.get("PORT", 10000))
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        logger.info(f"🌐 Web server started on port {port}")
    except Exception as e:
        logger.error(f"❌ Web server error: {e}")

async def main():
    """Main bot function with full async"""
    logger.info("🤖 Starting bot...")
    
    # Start async logger
    await async_handler.start()
    
    # Initialize tasks list
    tasks = []
    
    try:
        # Initialize database
        await init_database()
        
        # Build application
        app = ApplicationBuilder().token(TOKEN).build()
        
        # Set commands
        commands = [
            BotCommand("start", "Start bot and see help"),
            BotCommand("afk", "Set yourself AFK"),
            BotCommand("back", "Return from AFK"),
            BotCommand("ping", "Check bot response time"),
        ]
        
        await app.bot.set_my_commands(commands)
        
        # Add handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("afk", afk_command))
        app.add_handler(CommandHandler("back", back_command))
        app.add_handler(CommandHandler("ping", ping_command))
        app.add_handler(CallbackQueryHandler(delete_callback, pattern="delete_message"))
        app.add_handler(MessageHandler(filters.ALL, message_handler))
        
        # Start background tasks
        tasks = [
            asyncio.create_task(process_message_queue()),
            asyncio.create_task(process_deletion_queue()),
            asyncio.create_task(check_inactivity()),
            asyncio.create_task(cache_cleaner()),
            asyncio.create_task(start_web_server()),
        ]
        
        logger.info("🚀 Bot started successfully!")
        
        # Run bot
        await app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Critical error: {e}")
        raise
    finally:
        # Cleanup
        logger.info("🧹 Cleaning up...")
        
        for task in tasks:
            if not task.done():
                task.cancel()
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        await close_database()
        await async_handler.stop()
        executor.shutdown(wait=False)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️ Bot stopped")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        raise