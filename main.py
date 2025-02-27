import os
import re
import time
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded

# Configuration from environment variables
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# Initialize clients
bot_client = Client("bot", API_ID, API_HASH, bot_token=BOT_TOKEN)
user_client = None
if SESSION_STRING:
    user_client = Client("user", API_ID, API_HASH, session_string=SESSION_STRING)

# State management
user_states = {}
active_tasks = {}
progress_cache = {}
login_sessions = {}

async def start_user_client():
    global user_client
    if SESSION_STRING and user_client:
        try:
            await user_client.start()
            print("✅ User client started successfully")
        except Exception as e:
            print(f"❌ Failed to start user client: {e}")

def parse_telegram_link(link):
    private_match = re.match(r"https://t\.me/c/(\d+)/(\d+)", link)
    public_match = re.match(r"https://t\.me/([^/]+)/(\d+)", link)
    
    if private_match:
        return f"-100{private_match.group(1)}", int(private_match.group(2)), "private"
    if public_match:
        return public_match.group(1), int(public_match.group(2)), "public"
    return None, None, None

async def fetch_message(chat_id, message_id, link_type):
    try:
        client = bot_client if link_type == "public" else user_client
        return await client.get_messages(chat_id, message_id)
    except Exception as e:
        print(f"❌ Message fetch error: {e}")
        return None

async def update_progress(current, total, client, chat_id, message_id, start_time):
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

async def handle_media_transfer(message, dest_chat, link_type, user_id):
    try:
        if not message.media:
            # Send text message (using markdown parse mode)
            await bot_client.send_message(dest_chat, text=message.text, parse_mode="markdown")
            return "Text message sent"

        if link_type == "public":
            await message.copy(dest_chat)
            return "Media copied"

        progress_msg = await bot_client.send_message(dest_chat, "⏬ Downloading...")
        active_tasks[user_id] = {"cancel": False, "progress_id": progress_msg.id}
        start_time = time.time()
        temp_file = None
        try:
            temp_file = await user_client.download_media(
                message,
                progress=update_progress,
                progress_args=(bot_client, dest_chat, progress_msg.id, start_time)
            )
        except Exception as e:
            await progress_msg.edit_text(f"❌ Download failed: {str(e)}")
            return "Download failed"

        if active_tasks.get(user_id, {}).get("cancel"):
            await progress_msg.edit_text("❌ Canceled")
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)
            return "Canceled"

        await progress_msg.edit_text("⏫ Uploading...")
        thumbnail = "v3.jpg"
        caption = message.caption if message.caption else None

        try:
            common_args = {
                "progress": update_progress,
                "progress_args": (bot_client, dest_chat, progress_msg.id, start_time)
            }

            if message.video:
                await bot_client.send_video(
                    dest_chat,
                    temp_file,
                    thumb=thumbnail,
                    width=message.video.width,
                    height=message.video.height,
                    duration=message.video.duration,
                    caption=caption,
                    **common_args
                )
            elif message.video_note:
                await bot_client.send_video_note(dest_chat, temp_file, **common_args)
            elif message.voice:
                await bot_client.send_voice(dest_chat, temp_file, caption=caption, **common_args)
            elif message.sticker:
                await bot_client.send_sticker(dest_chat, temp_file)
            elif message.audio:
                await bot_client.send_audio(dest_chat, temp_file, thumb=thumbnail, caption=caption, **common_args)
            elif message.photo:
                await bot_client.send_photo(dest_chat, temp_file, caption=caption, **common_args)
            elif message.document:
                await bot_client.send_document(dest_chat, temp_file, caption=caption, **common_args)
        finally:
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)

        await bot_client.delete_messages(dest_chat, progress_msg.id)
        return "Transfer completed"

    except Exception as e:
        return f"❌ Error: {str(e)}"

@bot_client.on_message(filters.command("start"))
async def start_handler(_, message: Message):
    await message.reply_text(
        "✨ Welcome!\n"
        "- Use /batch to start transferring messages\n"
        "- Use /login to create a session\n"
        "- Use /cancel to stop current task"
    )

@bot_client.on_message(filters.command("login"))
async def login_handler(_, message: Message):
    user_id = message.from_user.id
    login_sessions[user_id] = {"stage": "phone"}
    await message.reply("Please send your phone number in international format (+1234567890):")

@bot_client.on_message(filters.command("batch"))
async def batch_handler(_, message: Message):
    if not user_client:
        await message.reply("❌ No active session! Use /login first")
        return
    
    user_id = message.from_user.id
    user_states[user_id] = {"step": "start"}
    await message.reply_text("📩 Send me the first message link")

@bot_client.on_message(filters.command("cancel"))
async def cancel_handler(_, message: Message):
    user_id = message.from_user.id
    if user_id in active_tasks:
        active_tasks[user_id]["cancel"] = True
        await message.reply_text("🛑 Cancelling current task...")
    else:
        await message.reply_text("❌ No active tasks to cancel")

@bot_client.on_message(filters.text & ~filters.command(["start", "batch", "cancel", "login"]))
async def message_handler(_, message: Message):
    user_id = message.from_user.id
    
    # Handle login process
    if user_id in login_sessions:
        login_data = login_sessions[user_id]
        
        if login_data["stage"] == "phone":
            phone_number = message.text
            temp_client = Client(f"session_{user_id}", API_ID, API_HASH)
            await temp_client.connect()
            
            try:
                sent_code = await temp_client.send_code(phone_number)
                login_sessions[user_id] = {
                    "stage": "code",
                    "temp_client": temp_client,
                    "phone_number": phone_number,
                    "phone_code_hash": sent_code.phone_code_hash
                }
                await message.reply("Enter the code you received (format: 1 2 3 4 5):")
            except Exception as e:
                await message.reply(f"❌ Error: {str(e)}")
                del login_sessions[user_id]
        
        elif login_data["stage"] == "code":
            code = message.text.replace(" ", "")
            temp_client = login_data["temp_client"]
            
            try:
                await temp_client.sign_in(
                    login_data["phone_number"],
                    login_data["phone_code_hash"],
                    code
                )
            except SessionPasswordNeeded:
                login_sessions[user_id]["stage"] = "password"
                await message.reply("Enter your 2FA password:")
                return
            except Exception as e:
                await message.reply(f"❌ Login failed: {str(e)}")
                await temp_client.disconnect()
                del login_sessions[user_id]
                return
            
            session_string = await temp_client.export_session_string()
            await temp_client.disconnect()
            await message.reply(
                f"✅ Login successful!\n"
                f"Your session string:\n`{session_string}`\n\n"
                "Update your SESSION_STRING environment variable and restart the bot!"
            )
            del login_sessions[user_id]
        
        elif login_data["stage"] == "password":
            password = message.text
            temp_client = login_data["temp_client"]
            
            try:
                await temp_client.check_password(password)
                session_string = await temp_client.export_session_string()
                await message.reply(
                    f"✅ Login successful!\n"
                    f"Your session string:\n`{session_string}`\n\n"
                    "Update your SESSION_STRING environment variable and restart the bot!"
                )
            except Exception as e:
                await message.reply(f"❌ 2FA failed: {str(e)}")
            finally:
                await temp_client.disconnect()
                del login_sessions[user_id]
        
        return
    
    # Handle normal message processing
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
        dest_chat = message.text.strip()
        progress_msg = await message.reply_text("🚀 Processing messages...")

        success_count = 0
        for i in range(user_data["message_count"]):
            current_id = user_data["start_id"] + i
            msg = await fetch_message(
                user_data["chat_id"],
                current_id,
                user_data["link_type"]
            )
            
            if not msg:
                await message.reply_text(f"⚠️ Message {current_id} not found")
                continue
                
            result = await handle_media_transfer(
                msg,
                dest_chat,
                user_data["link_type"],
                user_id
            )
            await progress_msg.edit_text(f"📨 Message {i+1}: {result}")
            if "completed" in result.lower():
                success_count += 1

        await message.reply_text(f"✅ Completed! {success_count}/{user_data['message_count']} messages transferred")
        del user_states[user_id]

async def main():
    if SESSION_STRING and user_client:
        await start_user_client()
    else:
        print("ℹ️ No session string found. Use /login to create one")
    print("✅ Bot started successfully")
    await idle()

bot_client.run(main())
