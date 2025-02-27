import os
import re
import time
import random
import string
from pyrogram import Client, filters, types
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import API_ID, API_HASH, BOT_TOKEN

# Initialize clients
userbot = Client("userbot", api_id=API_ID, api_hash=API_HASH)
bot = Client("bot", bot_token=BOT_TOKEN)

# Global variables
progress_cache = {}

def generate_random_name(length=7):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def parse_link(link: str):
    pattern = r"https?://t\.me/(?:c/|(?:\w+/)?)(\d+|[^\s/]+)/(\d+)"
    match = re.match(pattern, link)
    if not match:
        return None, None, None
    
    chat_part, msg_id = match.groups()
    try:
        chat_id = int(chat_part) if chat_part.isdigit() else chat_part
        msg_id = int(msg_id)
    except ValueError:
        return None, None, None
    
    link_type = "channel" if chat_part.isdigit() else "group" if chat_part.startswith("-") else "private"
    return chat_id, msg_id, link_type

@bot.on_message(filters.command("start"))
async def start(_, message):
    await message.reply_text("Welcome to the bot! Use /batch to start processing.")

@bot.on_message(filters.command("batch"))
async def batch(_, message):
    await message.reply_text("Send the start link.")

@bot.on_message(filters.command("cancel"))
async def cancel(_, message):
    await message.reply_text("Cancelling...")

@bot.on_message(filters.text & ~filters.command(["start", "batch", "cancel"]))
async def handle_text(_, message):
    if message.text.startswith("http"):
        chat_id, msg_id, link_type = parse_link(message.text)
        if not chat_id or not msg_id:
            await message.reply_text("Invalid link. Please check the format.")
            return
        await message.reply_text("How many messages?")

@bot.on_message(filters.command("login"))
async def generate_session(_, message):
    user_id = message.chat.id
    number = await _.ask(user_id, 'Please enter your phone number along with the country code.\nExample: +19876543210', filters=filters.text)
    phone_number = number.text

    try:
        await message.reply("📲 Sending OTP...")
        client = Client(f"session_{user_id}", api_id=API_ID, api_hash=API_HASH)
        await client.connect()
    except Exception as e:
        await message.reply(f"❌ Failed to send OTP: {e}")
        return

    try:
        code = await client.send_code(phone_number)
    except Exception as e:
        await message.reply(f"❌ Error: {e}")
        return

    while True:
        try:
            otp_code = await _.ask(user_id, "Please enter the OTP you received:", filters=filters.text, timeout=600)
            phone_code = otp_code.text.replace(" ", "")
            if phone_code.lower() == 'resend':
                await client.send_code(phone_number)
                continue
            break
        except TimeoutError:
            await message.reply('⏰ Time limit exceeded. Please restart the session.')
            return

    try:
        await client.sign_in(phone_number, code.phone_code_hash, phone_code)
    except Exception as e:
        await message.reply(f"❌ Error: {e}")
        return

    string_session = await client.export_session_string()
    await client.disconnect()
    await message.reply(f"✅ Login successful!\n\nYour session string:\n`{string_session}`")

@bot.on_message(filters.command("logout"))
async def clear_session(_, message):
    user_id = message.chat.id
    session_file = f"session_{user_id}.session"
    memory_file = f"session_{user_id}.session-journal"

    if os.path.exists(session_file):
        os.remove(session_file)
    if os.path.exists(memory_file):
        os.remove(memory_file)

    await message.reply("✅ Logged out successfully!")

if __name__ == "__main__":
    bot.start()
    userbot.start()
    print("Bot started successfully!")
    bot.idle()
