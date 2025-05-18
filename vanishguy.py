import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import nest_asyncio
import os
import asyncpg
from dotenv import load_dotenv

nest_asyncio.apply()
load_dotenv()

TOKEN = os.environ.get("BOT_TOKEN")
DB_URL = os.environ.get("DATABASE_URL")  # e.g., 'postgresql://user:password@host:port/dbname'


class Database:
    def __init__(self, url):
        self.url = url
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(dsn=self.url)
        await self.create_tables()

    async def create_tables(self):
        await self.create_afk_table()
        await self.create_activity_table()

    async def create_afk_table(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS afk_users (
                    chat_id BIGINT,
                    user_id BIGINT,
                    reason TEXT,
                    since TIMESTAMPTZ,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)

    async def create_activity_table(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS last_seen (
                    chat_id BIGINT,
                    user_id BIGINT,
                    seen_at TIMESTAMPTZ,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)

    async def set_afk(self, chat_id, user_id, reason, since):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO afk_users (chat_id, user_id, reason, since)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET reason = $3, since = $4
            """, chat_id, user_id, reason, since)

    async def remove_afk(self, chat_id, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM afk_users WHERE chat_id = $1 AND user_id = $2
            """, chat_id, user_id)

    async def get_afk(self, chat_id, user_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT * FROM afk_users WHERE chat_id = $1 AND user_id = $2
            """, chat_id, user_id)

    async def update_last_seen(self, chat_id, user_id, seen_at):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO last_seen (chat_id, user_id, seen_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET seen_at = $3
            """, chat_id, user_id, seen_at)

    async def get_all_last_seen(self):
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM last_seen")


db = Database(DB_URL)


def format_afk_time(delta):
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Updates", url="https://t.me/WorkGlows"),
         InlineKeyboardButton("Support", url="https://t.me/TheCryptoElders")],
        [InlineKeyboardButton("Add Me To Your Group", url="https://t.me/vanishguybot?startgroup=true")]
    ])

    await update.message.reply_html(
        f"👋 Hello, {user.mention_html()}!\n\n"
        "I'm your friendly <b>AFK Assistant Bot</b> 🤖.\n\n"
        "Here’s what I can do for you:\n"
        "🔹 <b>/afk [reason]</b> — Let everyone know you're away.\n"
        "🔹 <b>/back</b> — Tell everyone you're back!\n\n"
        "⏰ I'll also mark you AFK if you're inactive for a while.\n\n"
        "<i>Stay active, stay awesome!</i> ✨",
        reply_markup=keyboard
    )


async def afk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    reason = " ".join(context.args) if context.args else "AFK"
    now = datetime.now(timezone.utc)
    await db.set_afk(chat_id, user.id, reason, now)
    await update.message.reply_html(f"{user.mention_html()} is now AFK: {reason}")


async def back_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    afk = await db.get_afk(chat_id, user.id)
    if afk:
        delta = datetime.now(timezone.utc) - afk["since"]
        await db.remove_afk(chat_id, user.id)
        await update.message.reply_html(
            f"Welcome back {user.mention_html()}! You were AFK for {format_afk_time(delta)}."
        )
    else:
        await update.message.reply_text("You are not AFK.")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.is_bot:
        return
    now = datetime.now(timezone.utc)
    await db.update_last_seen(chat_id, user.id, now)

    afk = await db.get_afk(chat_id, user.id)
    if afk:
        delta = now - afk["since"]
        await db.remove_afk(chat_id, user.id)
        await update.message.reply_html(
            f"Welcome back {user.mention_html()}! You were AFK for {format_afk_time(delta)}."
        )

    if update.message.reply_to_message:
        replied_user = update.message.reply_to_message.from_user
        if replied_user:
            afk = await db.get_afk(chat_id, replied_user.id)
            if afk:
                delta = now - afk["since"]
                await update.message.reply_html(
                    f"{replied_user.mention_html()} is currently AFK ({afk['reason']}) — for {format_afk_time(delta)}."
                )


async def check_inactivity():
    await asyncio.sleep(10)
    while True:
        now = datetime.now(timezone.utc)
        records = await db.get_all_last_seen()
        for record in records:
            chat_id = record["chat_id"]
            user_id = record["user_id"]
            last_time = record["seen_at"]
            inactive_time = now - last_time

            is_afk = await db.get_afk(chat_id, user_id)
            if inactive_time > timedelta(minutes=60) and not is_afk:
                await db.set_afk(chat_id, user_id, "No activity", last_time)
        await asyncio.sleep(60)


async def main():
    await db.connect()
    app = ApplicationBuilder().token(TOKEN).build()

    await app.bot.set_my_commands([
        BotCommand("start", "Start bot and see help"),
        BotCommand("afk", "Set yourself AFK"),
        BotCommand("back", "Return from AFK"),
    ])

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("afk", afk_command))
    app.add_handler(CommandHandler("back", back_command))
    app.add_handler(MessageHandler(filters.ALL, message_handler))

    asyncio.create_task(check_inactivity())
    print("Bot started...")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())