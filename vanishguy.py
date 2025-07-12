import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import nest_asyncio
import os
import json
import threading
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

nest_asyncio.apply()
load_dotenv()

TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = os.environ.get("DATA_FILE", "data.json")
MONGODB_URL = os.environ.get("MONGODB_URL")

# MongoDB Data Manager (using same interface as original JSON version)
class DataManager:
    def __init__(self, file_name=DATA_FILE):
        self.file_name = file_name
        self.lock = threading.Lock()
        
        # Try MongoDB first
        if MONGODB_URL:
            try:
                self.client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
                # Test connection
                self.client.admin.command('ping')
                self.db = self.client.afk_bot
                self.afk_collection = self.db.afk_data
                self.last_seen_collection = self.db.last_seen_data
                self.leaderboard_collection = self.db.leaderboard_data
                print("MongoDB connected successfully")
                self.use_mongodb = True
            except (ConnectionFailure, ServerSelectionTimeoutError) as e:
                print(f"MongoDB connection failed: {e}")
                print("Falling back to JSON storage")
                self.use_mongodb = False
        else:
            print("MongoDB URL not provided, using JSON storage")
            self.use_mongodb = False
        
        # Fallback to JSON if MongoDB not available
        if not self.use_mongodb:
            self.data = self._load()

    def _load(self):
        if not os.path.exists(self.file_name):
            return {"leaderboard": {}, "afk": {}, "last_seen": {}}
        with open(self.file_name, "r") as f:
            return json.load(f)

    def _save(self):
        with open(self.file_name, "w") as f:
            json.dump(self.data, f, indent=4)

    async def set_afk(self, chat_id, user_id, reason, since):
        def _sync_mongodb():
            with self.lock:
                key = f"{chat_id}:{user_id}"
                doc = {
                    "_id": key,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "reason": reason,
                    "since": since.isoformat()
                }
                self.afk_collection.replace_one({"_id": key}, doc, upsert=True)
        
        def _sync_json():
            with self.lock:
                self.data.setdefault("afk", {})
                key = f"{chat_id}:{user_id}"
                self.data["afk"][key] = {"reason": reason, "since": since.isoformat()}
                self._save()
        
        if self.use_mongodb:
            await asyncio.to_thread(_sync_mongodb)
        else:
            await asyncio.to_thread(_sync_json)

    async def remove_afk(self, chat_id, user_id):
        def _sync_mongodb():
            with self.lock:
                key = f"{chat_id}:{user_id}"
                self.afk_collection.delete_one({"_id": key})
        
        def _sync_json():
            with self.lock:
                key = f"{chat_id}:{user_id}"
                if key in self.data.get("afk", {}):
                    del self.data["afk"][key]
                    self._save()
        
        if self.use_mongodb:
            await asyncio.to_thread(_sync_mongodb)
        else:
            await asyncio.to_thread(_sync_json)

    async def get_afk(self, chat_id, user_id):
        def _sync_mongodb():
            key = f"{chat_id}:{user_id}"
            doc = self.afk_collection.find_one({"_id": key})
            if not doc:
                return None
            return {
                "reason": doc["reason"],
                "since": datetime.fromisoformat(doc["since"]).replace(tzinfo=timezone.utc)
            }
        
        def _sync_json():
            key = f"{chat_id}:{user_id}"
            entry = self.data.get("afk", {}).get(key)
            if not entry:
                return None
            entry_copy = entry.copy()
            entry_copy["since"] = datetime.fromisoformat(entry_copy["since"]).replace(tzinfo=timezone.utc)
            return entry_copy
        
        if self.use_mongodb:
            return await asyncio.to_thread(_sync_mongodb)
        else:
            return await asyncio.to_thread(_sync_json)

    async def update_last_seen(self, chat_id, user_id, seen_at):
        def _sync_mongodb():
            with self.lock:
                key = f"{chat_id}:{user_id}"
                doc = {
                    "_id": key,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "seen_at": seen_at.isoformat()
                }
                self.last_seen_collection.replace_one({"_id": key}, doc, upsert=True)
        
        def _sync_json():
            with self.lock:
                self.data.setdefault("last_seen", {})
                key = f"{chat_id}:{user_id}"
                self.data["last_seen"][key] = seen_at.isoformat()
                self._save()
        
        if self.use_mongodb:
            await asyncio.to_thread(_sync_mongodb)
        else:
            await asyncio.to_thread(_sync_json)

    async def get_all_last_seen(self):
        def _sync_mongodb():
            items = []
            for doc in self.last_seen_collection.find():
                items.append({
                    "chat_id": doc["chat_id"],
                    "user_id": doc["user_id"],
                    "seen_at": datetime.fromisoformat(doc["seen_at"]).replace(tzinfo=timezone.utc)
                })
            return items
        
        def _sync_json():
            items = []
            for key, iso in self.data.get("last_seen", {}).items():
                chat_id, user_id = key.split(":")
                items.append({
                    "chat_id": int(chat_id),
                    "user_id": int(user_id),
                    "seen_at": datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
                })
            return items
        
        if self.use_mongodb:
            return await asyncio.to_thread(_sync_mongodb)
        else:
            return await asyncio.to_thread(_sync_json)

data = DataManager()

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

async def delete_message_after(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Updates", url="https://t.me/WorkGlows"),
            InlineKeyboardButton("Support", url="https://t.me/SoulMeetsHQ")
        ],
        [
            InlineKeyboardButton("Add Me To Your Group", url="https://t.me/vanishguybot?startgroup=true")
        ]
    ])
    await update.message.reply_html(
        f"👋 Hello, {user.mention_html()}!\n\n"
        "I'm your friendly <b>AFK Assistant Bot</b> 🤖.\n\n"
        "Here's what I can do for you:\n"
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
    await data.set_afk(chat_id, user.id, reason, now)
    sent_msg = await update.message.reply_html(f"{user.mention_html()} is now AFK: {reason}")
    asyncio.create_task(delete_message_after(sent_msg, 30))

async def back_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    afk = await data.get_afk(chat_id, user.id)
    if afk:
        delta = datetime.now(timezone.utc) - afk["since"]
        await data.remove_afk(chat_id, user.id)
        sent_msg = await update.message.reply_html(
            f"Welcome back {user.mention_html()}! You were AFK for {format_afk_time(delta)}."
        )
        asyncio.create_task(delete_message_after(sent_msg, 30))
    else:
        sent_msg = await update.message.reply_text("You are not AFK.")
        asyncio.create_task(delete_message_after(sent_msg, 30))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.is_bot:
        return
    now = datetime.now(timezone.utc)
    await data.update_last_seen(chat_id, user.id, now)
    afk = await data.get_afk(chat_id, user.id)
    if afk:
        delta = now - afk["since"]
        await data.remove_afk(chat_id, user.id)
        sent_msg = await update.message.reply_html(
            f"Welcome back {user.mention_html()}! You were AFK for {format_afk_time(delta)}."
        )
        asyncio.create_task(delete_message_after(sent_msg, 30))
    if update.message.reply_to_message:
        replied_user = update.message.reply_to_message.from_user
        if replied_user:
            afk = await data.get_afk(chat_id, replied_user.id)
            if afk:
                delta = now - afk["since"]
                sent_msg = await update.message.reply_html(
                    f"{replied_user.mention_html()} is currently AFK ({afk['reason']}) — for {format_afk_time(delta)}."
                )
                asyncio.create_task(delete_message_after(sent_msg, 30))

async def check_inactivity():
    await asyncio.sleep(10)
    while True:
        now = datetime.now(timezone.utc)
        records = await data.get_all_last_seen()
        for record in records:
            chat_id = record["chat_id"]
            user_id = record["user_id"]
            last_time = record["seen_at"]
            inactive_time = now - last_time
            afk = await data.get_afk(chat_id, user_id)
            if inactive_time > timedelta(minutes=60) and not afk:
                await data.set_afk(chat_id, user_id, "No activity", last_time)
        await asyncio.sleep(60)

async def main():
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