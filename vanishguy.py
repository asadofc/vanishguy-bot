import os
import json
import time
import random
import asyncio
import nest_asyncio

import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Initialize
nest_asyncio.apply()
load_dotenv()

# Configuration
TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = os.environ.get("DATA_FILE", "data.json")

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

# Global lock for file operations
file_lock = threading.Lock()

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"leaderboard": {}, "afk": {}, "last_seen": {}}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

async def set_afk(chat_id, user_id, reason, since):
    def _sync_operation():
        with file_lock:
            data = load_data()
            data.setdefault("afk", {})
            key = f"{chat_id}:{user_id}"
            data["afk"][key] = {"reason": reason, "since": since.isoformat()}
            save_data(data)
    
    await asyncio.to_thread(_sync_operation)

async def remove_afk(chat_id, user_id):
    def _sync_operation():
        with file_lock:
            data = load_data()
            key = f"{chat_id}:{user_id}"
            if key in data.get("afk", {}):
                del data["afk"][key]
                save_data(data)
    
    await asyncio.to_thread(_sync_operation)

async def get_afk(chat_id, user_id):
    def _sync_operation():
        data = load_data()
        key = f"{chat_id}:{user_id}"
        entry = data.get("afk", {}).get(key)
        if not entry:
            return None
        entry_copy = entry.copy()
        entry_copy["since"] = datetime.fromisoformat(entry_copy["since"]).replace(tzinfo=timezone.utc)
        return entry_copy
    
    return await asyncio.to_thread(_sync_operation)

async def update_last_seen(chat_id, user_id, seen_at):
    def _sync_operation():
        with file_lock:
            data = load_data()
            data.setdefault("last_seen", {})
            key = f"{chat_id}:{user_id}"
            data["last_seen"][key] = seen_at.isoformat()
            save_data(data)
    
    await asyncio.to_thread(_sync_operation)

async def get_all_last_seen():
    def _sync_operation():
        data = load_data()
        items = []
        for key, iso in data.get("last_seen", {}).items():
            chat_id, user_id = key.split(":")
            items.append({
                "chat_id": int(chat_id),
                "user_id": int(user_id),
                "seen_at": datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
            })
        return items
    
    return await asyncio.to_thread(_sync_operation)

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

def create_delete_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️", callback_data="delete_message")]
    ])

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    try:
        await query.message.delete()
        await query.answer("🗑️ Message deleted!", show_alert=False)
    except Exception:
        await query.answer("💬 Failed to delete message!", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot_username = context.bot.username
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

async def afk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    reason = " ".join(context.args) if context.args else "AFK"
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

async def back_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    else:
        sent_msg = await update.message.reply_text(
            "You are not AFK.",
            reply_markup=create_delete_keyboard()
        )

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.is_bot:
        return
    now = datetime.now(timezone.utc)
    await update_last_seen(chat_id, user.id, now)
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

async def check_inactivity():
    await asyncio.sleep(10)
    while True:
        now = datetime.now(timezone.utc)
        records = await get_all_last_seen()
        for record in records:
            chat_id = record["chat_id"]
            user_id = record["user_id"]
            last_time = record["seen_at"]
            inactive_time = now - last_time
            afk = await get_afk(chat_id, user_id)
            if inactive_time > timedelta(minutes=60) and not afk:
                await set_afk(chat_id, user_id, "No activity", last_time)
        await asyncio.sleep(60)

async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    # Only register visible commands in the menu
    await app.bot.set_my_commands([
        BotCommand("start", "Start bot and see help"),
        BotCommand("afk", "Set yourself AFK"),
        BotCommand("back", "Return from AFK"),
    ])
    
    # Add handlers (ping command is added but not registered in menu)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("afk", afk_command))
    app.add_handler(CommandHandler("back", back_command))
    app.add_handler(CommandHandler("ping", ping_command))  # Hidden command
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="delete_message"))
    app.add_handler(MessageHandler(filters.ALL, message_handler))
    
    asyncio.create_task(check_inactivity())
    print("Bot started with JSON file storage...")
    await app.run_polling()

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"AFK bot is alive!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    print(f"Dummy server listening on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=start_dummy_server, daemon=True).start()
    asyncio.get_event_loop().run_until_complete(main())