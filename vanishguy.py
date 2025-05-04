import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update, BotCommand
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import nest_asyncio
import os

nest_asyncio.apply()

TOKEN = os.environ.get("BOT_TOKEN")

afk_users = {}
last_seen = {}
afk_last_announcement = {}

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
    await update.message.reply_html(
        f"👋 Hello, {user.mention_html()}!\n\n"
        "I'm your friendly <b>AFK Assistant Bot</b> 🤖.\n\n"
        "Here’s what I can do for you:\n"
        "🔹 <b>/afk [reason]</b> — Let everyone know you're away.\n"
        "🔹 <b>/back</b> — Tell everyone you're back!\n\n"
        "⏰ I'll also mark you AFK if you're inactive for a while.\n\n"
        "<i>Stay active, stay awesome!</i> ✨"
    )

async def afk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    reason = " ".join(context.args) if context.args else "AFK"
    afk_users.setdefault(chat_id, {})[user.id] = {"since": datetime.now(timezone.utc), "reason": reason}
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await update.message.reply_html(f"{user.mention_html()} is now AFK: {reason}")

async def back_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    if user.id in afk_users.get(chat_id, {}):
        afk_info = afk_users[chat_id].pop(user.id)
        afk_time = datetime.now(timezone.utc) - afk_info["since"]
        formatted_time = format_afk_time(afk_time)
        await update.message.reply_html(
            f"Welcome back {user.mention_html()}! You were AFK for {formatted_time}."
        )
    else:
        await update.message.reply_text("You are not AFK.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat = update.effective_chat
    chat_id = chat.id
    user = update.effective_user
    if user.is_bot:
        return
    now = datetime.now(timezone.utc)
    last_seen.setdefault(chat_id, {})[user.id] = now
    if user.id in afk_users.get(chat_id, {}):
        afk_info = afk_users[chat_id].pop(user.id)
        afk_time = now - afk_info['since']
        formatted_time = format_afk_time(afk_time)
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await update.message.reply_html(
            f"Welcome back {user.mention_html()}! You were AFK for {formatted_time}."
        )
    if update.message.reply_to_message:
        replied_user = update.message.reply_to_message.from_user
        if replied_user and replied_user.id in afk_users.get(chat_id, {}):
            last_announcement = afk_last_announcement.get(chat_id, {}).get(replied_user.id)
            if not last_announcement or (now - last_announcement) >= timedelta(minutes=30):
                afk_info = afk_users[chat_id][replied_user.id]
                afk_time = now - afk_info['since']
                formatted_time = format_afk_time(afk_time)
                afk_last_announcement.setdefault(chat_id, {})[replied_user.id] = now
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await update.message.reply_html(
                    f"{replied_user.mention_html()} is currently AFK ({afk_info['reason']}) — for {formatted_time}."
                )

async def check_inactivity(app):
    await asyncio.sleep(10)
    while True:
        now = datetime.now(timezone.utc)
        for chat_id, users in list(last_seen.items()):
            for user_id, last_time in list(users.items()):
                if user_id not in afk_users.get(chat_id, {}):
                    inactive_time = now - last_time
                    if inactive_time > timedelta(minutes=5):
                        afk_users.setdefault(chat_id, {})[user_id] = {"since": last_time, "reason": "No activity"}
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
    asyncio.create_task(check_inactivity(app))
    print("Bot started...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
