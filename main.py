import os as os_module, re as regex
from pyrogram import Client as TelegramClient, filters as Filters
from pyrogram.types import Message as TelegramMessage
import time
from config import API_ID, API_HASH, BOT_TOKEN, SESSION_STRING

bot_client = TelegramClient("bot", API_ID, API_HASH, bot_token=BOT_TOKEN)
user_client = TelegramClient("user", API_ID, API_HASH, session_string=SESSION_STRING)

user_states = {}
active_tasks = {}
progress_cache = {}

try:
    user_client.start()
    print("User client started successfully")
except Exception as e:
    print(f"Failed to start user client: {e}")
    exit(1)

def parse_telegram_link(link):
    """Extract chat information from Telegram message link"""
    private_chat_match = regex.match(r"https://t\.me/c/(\d+)/(\d+)", link)
    public_chat_match = regex.match(r"https://t\.me/([^/]+)/(\d+)", link)
    
    if private_chat_match:
        return f"-100{private_chat_match.group(1)}", int(private_chat_match.group(2)), "private"
    if public_chat_match:
        return public_chat_match.group(1), int(public_chat_match.group(2)), "public"
    return None, None, None
        
async def fetch_message(client, chat_id, message_id, link_type):
    """Retrieve message from specified chat"""
    try:
        return await (bot_client if link_type == "public" else user_client).get_messages(chat_id, message_id)
    except Exception as e:
        print(f"Message fetch error: {e}")
        return None

async def update_progress(current, total, client, chat_id, message_id, start_time):
    """Update progress bar for active transfers"""
    global progress_cache
    progress_percent = (current / total) * 100
    progress_step = int(progress_percent // 10) * 10

    if message_id not in progress_cache or progress_cache[message_id] != progress_step or progress_percent >= 100:
        progress_cache[message_id] = progress_step
        progress_bar = "🟢" * (int(progress_percent // 10)) + "🔴" * (10 - int(progress_percent // 10))
        
        elapsed_time = time.time() - start_time
        transfer_speed = (current / elapsed_time) / (1024 ** 2) if elapsed_time > 0 else 0
        eta_seconds = (total - current) / (transfer_speed * 1024 ** 2) if transfer_speed > 0 else 0
        eta_formatted = time.strftime("%M:%S", time.gmtime(eta_seconds))
        
        status_message = (
            "__**Pyro Handler...**__\n\n"
            f"{progress_bar}\n\n"
            f"📊 **Completed**: {progress_percent:.2f}%\n"
            f"🚀 **Speed**: {transfer_speed:.2f} MB/s\n"
            f"⏳ **ETA**: {eta_formatted}\n\n"
            "**Powered by Team SPY**"
        )
        
        await client.edit_message_text(chat_id, message_id, status_message)
        if progress_percent >= 100:
            progress_cache.pop(message_id, None)

async def handle_media_transfer(bot, user, message, dest_chat, link_type, user_id):
    """Handle media file transfer between chats"""
    try:
        if not message.media:
            await bot.send_message(dest_chat, text=message.text.markdown)
            return "Text message sent"

        if link_type == "public":
            await message.copy(dest_chat)
            return "Media copied"

        # Private chat handling
        progress_msg = await bot.send_message(dest_chat, "⏬ Downloading...")
        active_tasks[user_id] = {"cancel": False, "progress_id": progress_msg.id}
        
        start_time = time.time()
        temp_file = await user.download_media(message, 
            progress=update_progress,
            progress_args=(bot, dest_chat, progress_msg.id, start_time)
        
        if active_tasks.get(user_id, {}).get("cancel"):
            await bot.edit_message_text(dest_chat, progress_msg.id, "❌ Canceled")
            if os_module.exists(temp_file):
                os_module.remove(temp_file)
            return "Canceled"

        if not temp_file:
            await bot.edit_message_text(dest_chat, progress_msg.id, "❌ Failed")
            return "Failed"

        await bot.edit_message_text(dest_chat, progress_msg.id, "⏫ Uploading...")
        thumbnail = "v3.jpg"
        
        try:
            media_args = {
                "caption": message.caption.markdown if message.caption else None,
                "progress": update_progress,
                "progress_args": (bot, dest_chat, progress_msg.id, start_time)
            }
            
            if message.video:
                await bot.send_video(dest_chat, temp_file, thumb=thumbnail,
                    width=message.video.width, height=message.video.height,
                    duration=message.video.duration, **media_args)
            elif message.video_note:
                await bot.send_video_note(dest_chat, temp_file, **media_args)
            elif message.voice:
                await bot.send_voice(dest_chat, temp_file, **media_args)
            elif message.sticker:
                await bot.send_sticker(dest_chat, message.sticker.file_id)
            elif message.audio:
                await bot.send_audio(dest_chat, temp_file, thumb=thumbnail, **media_args)
            elif message.photo:
                await bot.send_photo(dest_chat, temp_file, **media_args)
            elif message.document:
                await bot.send_document(dest_chat, temp_file, **media_args)
        finally:
            if os_module.exists(temp_file):
                os_module.remove(temp_file)

        await bot.delete_messages(dest_chat, progress_msg.id)
        return "Transfer completed"

    except Exception as e:
        return f"Error: {str(e)}"

@bot_client.on_message(Filters.command("start"))
async def start_command(client, message: TelegramMessage):
    await message.reply_text("✨ Welcome! Use /batch to start transferring messages")

@bot_client.on_message(Filters.command("batch"))
async def batch_command(client, message: TelegramMessage):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "start"}
    await message.reply_text("📩 Send me the first message link")

@bot_client.on_message(Filters.command("cancel"))
async def cancel_command(client, message: TelegramMessage):
    user_id = message.from_user.id
    if user_id in active_tasks:
        active_tasks[user_id]["cancel"] = True
        await message.reply_text("🛑 Cancelling current task...")
    else:
        await message.reply_text("❌ No active tasks to cancel")

@bot_client.on_message(Filters.text & ~Filters.command(["start", "batch", "cancel"]))
async def handle_user_input(client, message: TelegramMessage):
    user_id = message.from_user.id
    if user_id not in user_states:
        return

    state = user_states[user_id]
    current_step = state.get("step")

    if current_step == "start":
        chat_id, start_id, link_type = parse_telegram_link(message.text)
        if not chat_id or not start_id:
            await message.reply_text("❌ Invalid link format")
            del user_states[user_id]
            return

        user_states[user_id].update({
            "step": "count",
            "chat_id": chat_id,
            "start_id": start_id,
            "link_type": link_type
        })
        await message.reply_text("🔢 How many messages to transfer?")

    elif current_step == "count":
        if not message.text.isdigit():
            await message.reply_text("❌ Please enter a valid number")
            return

        user_states[user_id].update({
            "step": "destination",
            "message_count": int(message.text)
        })
        await message.reply_text("📤 Enter destination chat ID")

    elif current_step == "destination":
        user_data = user_states[user_id]
        chat_id = message.text.strip()
        progress_msg = await message.reply_text("🚀 Processing messages...")

        success_count = 0
        for i in range(user_data["message_count"]):
            current_id = user_data["start_id"] + i
            msg = await fetch_message(client, user_data["chat_id"], current_id, user_data["link_type"])
            
            if not msg:
                await message.reply_text(f"⚠️ Message {current_id} not found")
                continue
                
            result = await handle_media_transfer(bot_client, user_client, msg, 
                chat_id, user_data["link_type"], user_id)
            await progress_msg.edit(f"📨 Message {i+1}: {result}")
            if "completed" in result.lower():
                success_count += 1

        await message.reply_text(f"✅ Completed! {success_count}/{user_data['message_count']} messages transferred")
        del user_states[user_id]

print("✅ Bot started successfully")
bot_client.run()
