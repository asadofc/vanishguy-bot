import asyncio
import os
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pymongo
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGODB_URL = os.environ.get("MONGODB_URL")

# MongoDB-only Data Manager
class DataManager:
    def __init__(self):
        if not MONGODB_URL:
            raise ValueError("MONGODB_URL is required for this bot")
        
        try:
            # Configure MongoDB with SSL settings
            self.client = MongoClient(
                MONGODB_URL, 
                serverSelectionTimeoutMS=10000,
                tlsAllowInvalidCertificates=True,
                retryWrites=True
            )
            # Test connection
            self.client.admin.command('ping')
            self.db = self.client.afk_bot
            self.afk_collection = self.db.afk_data
            self.last_seen_collection = self.db.last_seen_data
            print("MongoDB connected successfully")
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            print(f"MongoDB connection failed: {e}")
            print("Please check your MongoDB connection string and network connectivity")
            raise

    async def set_afk(self, chat_id, user_id, reason, since):
        def _sync_operation():
            key = f"{chat_id}:{user_id}"
            doc = {
                "_id": key,
                "chat_id": chat_id,
                "user_id": user_id,
                "reason": reason,
                "since": since.isoformat()
            }
            self.afk_collection.replace_one({"_id": key}, doc, upsert=True)
        
        await asyncio.to_thread(_sync_operation)

    async def remove_afk(self, chat_id, user_id):
        def _sync_operation():
            key = f"{chat_id}:{user_id}"
            self.afk_collection.delete_one({"_id": key})
        
        await asyncio.to_thread(_sync_operation)

    async def get_afk(self, chat_id, user_id):
        def _sync_operation():
            key = f"{chat_id}:{user_id}"
            doc = self.afk_collection.find_one({"_id": key})
            if not doc:
                return None
            return {
                "reason": doc["reason"],
                "since": datetime.fromisoformat(doc["since"]).replace(tzinfo=timezone.utc)
            }
        
        return await asyncio.to_thread(_sync_operation)

    async def update_last_seen(self, chat_id, user_id, seen_at):
        def _sync_operation():
            key = f"{chat_id}:{user_id}"
            doc = {
                "_id": key,
                "chat_id": chat_id,
                "user_id": user_id,
                "seen_at": seen_at.isoformat()
            }
            self.last_seen_collection.replace_one({"_id": key}, doc, upsert=True)
        
        await asyncio.to_thread(_sync_operation)

    async def get_all_last_seen(self):
        def _sync_operation():
            items = []
            for doc in self.last_seen_collection.find():
                items.append({
                    "chat_id": doc["chat_id"],
                    "user_id": doc["user_id"],
                    "seen_at": datetime.fromisoformat(doc["seen_at"]).replace(tzinfo=timezone.utc)
                })
            return items
        
        return await asyncio.to_thread(_sync_operation)

# Initialize data manager
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

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user = message.from_user
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Updates", url="https://t.me/WorkGlows"),
            InlineKeyboardButton(text="Support", url="https://t.me/TheCryptoElders")
        ],
        [
            InlineKeyboardButton(text="Add Me To Your Group", url="https://t.me/vanishguybot?startgroup=true")
        ]
    ])
    
    await message.reply(
        f"👋 Hello, <a href='tg://user?id={user.id}'>{user.first_name}</a>!\n\n"
        "I'm your friendly <b>AFK Assistant Bot</b> 🤖.\n\n"
        "Here's what I can do for you:\n"
        "🔹 <b>/afk [reason]</b> — Let everyone know you're away.\n"
        "🔹 <b>/back</b> — Tell everyone you're back!\n\n"
        "⏰ I'll also mark you AFK if you're inactive for a while.\n\n"
        "<i>Stay active, stay awesome!</i> ✨",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.message(Command("afk"))
async def afk_command(message: types.Message):
    user = message.from_user
    chat_id = message.chat.id
    
    # Extract reason from command args
    command_args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    reason = " ".join(command_args) if command_args else "AFK"
    
    now = datetime.now(timezone.utc)
    await data.set_afk(chat_id, user.id, reason, now)
    
    sent_msg = await message.reply(
        f"<a href='tg://user?id={user.id}'>{user.first_name}</a> is now AFK: {reason}",
        parse_mode="HTML"
    )
    asyncio.create_task(delete_message_after(sent_msg, 30))

@dp.message(Command("back"))
async def back_command(message: types.Message):
    user = message.from_user
    chat_id = message.chat.id
    afk = await data.get_afk(chat_id, user.id)
    
    if afk:
        delta = datetime.now(timezone.utc) - afk["since"]
        await data.remove_afk(chat_id, user.id)
        sent_msg = await message.reply(
            f"Welcome back <a href='tg://user?id={user.id}'>{user.first_name}</a>! You were AFK for {format_afk_time(delta)}.",
            parse_mode="HTML"
        )
        asyncio.create_task(delete_message_after(sent_msg, 30))
    else:
        sent_msg = await message.reply("You are not AFK.")
        asyncio.create_task(delete_message_after(sent_msg, 30))

@dp.message()
async def message_handler(message: types.Message):
    if not message.from_user or message.from_user.is_bot:
        return
    
    chat_id = message.chat.id
    user = message.from_user
    now = datetime.now(timezone.utc)
    
    await data.update_last_seen(chat_id, user.id, now)
    
    afk = await data.get_afk(chat_id, user.id)
    if afk:
        delta = now - afk["since"]
        await data.remove_afk(chat_id, user.id)
        sent_msg = await message.reply(
            f"Welcome back <a href='tg://user?id={user.id}'>{user.first_name}</a>! You were AFK for {format_afk_time(delta)}.",
            parse_mode="HTML"
        )
        asyncio.create_task(delete_message_after(sent_msg, 30))
    
    # Check if replying to someone who is AFK
    if message.reply_to_message and message.reply_to_message.from_user:
        replied_user = message.reply_to_message.from_user
        afk = await data.get_afk(chat_id, replied_user.id)
        if afk:
            delta = now - afk["since"]
            sent_msg = await message.reply(
                f"<a href='tg://user?id={replied_user.id}'>{replied_user.first_name}</a> is currently AFK ({afk['reason']}) — for {format_afk_time(delta)}.",
                parse_mode="HTML"
            )
            asyncio.create_task(delete_message_after(sent_msg, 30))

async def check_inactivity():
    await asyncio.sleep(10)
    while True:
        try:
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
        except Exception as e:
            print(f"Error in inactivity check: {e}")
        await asyncio.sleep(60)

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

async def main():
    try:
        # Set bot commands
        await bot.set_my_commands([
            BotCommand(command="start", description="Start bot and see help"),
            BotCommand(command="afk", description="Set yourself AFK"),
            BotCommand(command="back", description="Return from AFK"),
        ])
        
        # Start inactivity checker
        asyncio.create_task(check_inactivity())
        
        print("Bot started...")
        await dp.start_polling(bot)
    except Exception as e:
        print(f"Error starting bot: {e}")
        raise

if __name__ == "__main__":
    # Start dummy server in a separate thread
    dummy_thread = threading.Thread(target=start_dummy_server, daemon=True)
    dummy_thread.start()
    
    # Run the bot
    asyncio.run(main())