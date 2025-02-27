import os
import re
import time
import logging
import asyncio
from typing import Dict, Tuple, Optional, Union
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait
from config import API_ID, API_HASH, BOT_TOKEN, SESSION_STRING

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize clients
bot_client = Client("bot_client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_client = Client("user_client", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# State management
user_states: Dict[int, dict] = {}
active_tasks: Dict[int, dict] = {}
progress_cache: Dict[int, int] = {}

# Constants
SUPPORTED_MEDIA = (
    "video", "video_note", "voice", "sticker", 
    "audio", "photo", "document"
)
THUMBNAIL = "v3.jpg"  # Replace with actual thumbnail path
MAX_MESSAGES = 100  # Maximum messages per batch

def extract_chat_info(link: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """Extract chat information from Telegram URL."""
    patterns = [
        (r"https://t\.me/c/(\d+)/(\d+)", "private"),
        (r"https://t\.me/([^/]+)/(\d+)", "public")
    ]
    
    for pattern, link_type in patterns:
        match = re.match(pattern, link)
        if match:
            group = match.groups()
            return (f"-100{group[0]}" if link_type == "private" else group[0], 
                    int(group[1]), 
                    link_type)
    return None, None, None

async def fetch_message(
    chat_id: str, 
    message_id: int, 
    link_type: str
) -> Optional[Message]:
    """Fetch message using appropriate client."""
    try:
        client = user_client if link_type == "private" else bot_client
        return await client.get_messages(chat_id, message_id)
    except FloodWait as e:
        logger.warning(f"Rate limit hit, waiting {e.value} seconds")
        await asyncio.sleep(e.value)
        return await fetch_message(chat_id, message_id, link_type)
    except Exception as e:
        logger.error(f"Error fetching message {message_id}: {e}")
        return None

async def update_progress(
    client: Client,
    chat_id: int,
    message_id: int,
    current: int,
    total: int,
    start_time: float
) -> None:
    """Update progress message with current status."""
    progress = (current / total) * 100
    step = int(progress // 5) * 5  # Update every 5% for more responsive UI
    
    # Only update if progress step changed or completion
    if progress_cache.get(message_id, 0) <= step or progress >= 100:
        progress_cache[message_id] = step
        
        # Format progress elements
        bar = "●" * (int(progress // 10)) + "○" * (10 - int(progress // 10))
        elapsed = time.time() - start_time
        speed = (current / elapsed) / (1024 * 1024) if elapsed > 0 else 0
        eta = (total - current) / (speed * 1024 * 1024) if speed > 0 else 0
        
        # Format time
        eta_str = time.strftime("%H:%M:%S", time.gmtime(eta)) if eta > 0 else "00:00:00"
        
        try:
            await client.edit_message_text(
                chat_id,
                message_id,
                f"**Pyro Handler**\n\n{bar}\n"
                f"📊 **Progress**: {progress:.2f}%\n"
                f"🚀 **Speed**: {speed:.2f} MB/s\n"
                f"⏳ **ETA**: {eta_str}\n\n"
                "**Powered by Team SPY**"
            )
            
            if progress >= 100:
                progress_cache.pop(message_id, None)
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.error(f"Error updating progress: {e}")

async def get_file_urls(client: Client, file_id: str, file_name: str) -> dict:
    """Generate direct download and stream URLs for a file."""
    try:
        file_path = await client.get_file(file_id)
        if not file_path:
            logger.error(f"Failed to get file path for {file_id}")
            return {}
            
        # Get the Bot API file path
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path.file_path}"
        
        # Create a friendly URL for streaming (may not work for all media types)
        stream_url = file_url
        
        return {
            "download_url": file_url,
            "stream_url": stream_url,
            "file_name": file_name
        }
    except Exception as e:
        logger.error(f"Error getting file URL: {e}")
        return {}

async def handle_media(
    client: Client,
    user_id: int,
    message: Message,
    dest_chat: str,
    link_type: str
) -> Union[str, Message]:
    """Handle media message forwarding with progress tracking."""
    try:
        if not message.media:
            return "No media found"
            
        start_time = time.time()
        status_message = await client.send_message(dest_chat, "🚀 Starting download...")
        
        # Register active task
        active_tasks[user_id] = {
            "cancel": False,
            "progress_message": status_message.id
        }
        
        # Download file
        temp_file = await user_client.download_media(
            message,
            progress=update_progress,
            progress_args=(client, dest_chat, status_message.id, start_time)
        )
        
        # Handle cancellation
        if active_tasks.get(user_id, {}).get("cancel"):
            await client.edit_message_text(
                dest_chat,
                status_message.id,
                "❌ Transfer cancelled"
            )
            if os.path.exists(temp_file):
                os.remove(temp_file)
            active_tasks.pop(user_id, None)
            return "Cancelled"
            
        if not temp_file:
            await client.edit_message_text(
                dest_chat,
                status_message.id,
                "⚠️ Failed to download media"
            )
            active_tasks.pop(user_id, None)
            return "Download failed"
            
        # Upload file
        await client.edit_message_text(
            dest_chat,
            status_message.id,
            "📤 Starting upload..."
        )
        
        media_args = {
            "caption": message.caption.markdown if message.caption else None,
            "progress": update_progress,
            "progress_args": (client, dest_chat, status_message.id, start_time)
        }
        
        sent_message = None
        file_name = ""
        
        try:
            for media_type in SUPPORTED_MEDIA:
                if getattr(message, media_type, None):
                    if media_type == "video":
                        media_args.update({
                            "width": message.video.width,
                            "height": message.video.height,
                            "duration": message.video.duration,
                            "thumb": THUMBNAIL
                        })
                        file_name = message.video.file_name or f"video_{int(time.time())}.mp4"
                    elif media_type == "audio":
                        file_name = message.audio.file_name or f"audio_{int(time.time())}.mp3"
                    elif media_type == "document":
                        file_name = message.document.file_name or f"document_{int(time.time())}"
                    
                    sent_message = await getattr(client, f"send_{media_type}")(
                        dest_chat,
                        temp_file,
                        **media_args
                    )
                    break
            else:  # If no media type matched
                file_name = os.path.basename(temp_file)
                sent_message = await client.send_document(
                    dest_chat,
                    temp_file,
                    **media_args
                )
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                
        await client.delete_messages(dest_chat, status_message.id)
        active_tasks.pop(user_id, None)
        
        # Add download and stream buttons if applicable
        if sent_message and hasattr(sent_message, 'document') or hasattr(sent_message, 'video') or hasattr(sent_message, 'audio'):
            # Get the file_id based on the type of media
            file_id = None
            if hasattr(sent_message, 'document'):
                file_id = sent_message.document.file_id
            elif hasattr(sent_message, 'video'):
                file_id = sent_message.video.file_id
            elif hasattr(sent_message, 'audio'):
                file_id = sent_message.audio.file_id
                
            if file_id:
                await client.edit_message_reply_markup(
                    chat_id=dest_chat,
                    message_id=sent_message.id,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬇️ Download", callback_data=f"dl_{file_id}_{file_name}")],
                        [InlineKeyboardButton("📊 File Info", callback_data=f"fileinfo_{sent_message.id}")]
                    ])
                )
            
            return sent_message
        
        return "✅ Transfer completed"
        
    except Exception as e:
        logger.error(f"Media handling error: {e}")
        return f"⚠️ Error: {str(e)}"

@bot_client.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """Handle start command."""
    await message.reply(
        "✨ **Welcome to Pyro Handler Bot!**\n\n"
        "**Commands:**\n"
        "• /batch - Start forwarding messages\n"
        "• /cancel - Stop current operation\n"
        "• /help - Show detailed help\n"
        "• /status - Check bot status\n\n"
        "**Powered by Team SPY**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Help", callback_data="help"),
             InlineKeyboardButton("📊 Status", callback_data="status")]
        ])
    )

@bot_client.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    """Show detailed help information."""
    await message.reply(
        "📚 **Pyro Handler Bot Help**\n\n"
        "**How to use:**\n\n"
        "1️⃣ Use /batch to start forwarding messages\n"
        "2️⃣ Send the source message link\n"
        "3️⃣ Specify how many messages to forward\n"
        "4️⃣ Provide the destination chat ID\n\n"
        "**Other commands:**\n"
        "• /cancel - Stop current operation\n"
        "• /status - Check bot status\n\n"
        "**Features:**\n"
        "• Media downloads with progress tracking\n"
        "• Batch message forwarding\n"
        "• Direct file downloads\n"
        "• File information\n\n"
        "**Powered by Team SPY**"
    )

@bot_client.on_message(filters.command("status"))
async def status_command(client: Client, message: Message):
    """Show bot status information."""
    # Get active task count
    active_count = len(active_tasks)
    
    await message.reply(
        f"📊 **Bot Status**\n\n"
        f"• Active tasks: {active_count}\n"
        f"• Bot API Status: Online\n\n"
        f"**Last update:** {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )

@bot_client.on_message(filters.command("batch"))
async def batch_command(client: Client, message: Message):
    """Initiate batch forwarding process."""
    user_id = message.from_user.id
    
    # Cancel any existing task
    if user_id in active_tasks:
        active_tasks[user_id]["cancel"] = True
        await message.reply("⚠️ Cancelling previous task before starting new one...")
    
    user_states[user_id] = {"step": "start"}
    await message.reply(
        "🔗 **Batch Forwarding**\n\n"
        "Please send the first message link:"
    )

@bot_client.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    """Cancel current operation."""
    user_id = message.from_user.id
    if user_id in active_tasks:
        active_tasks[user_id]["cancel"] = True
        await message.reply("🛑 Cancelling current task...")
    else:
        await message.reply("⚠️ No active task to cancel")

@bot_client.on_callback_query(filters.regex(r"^dl_"))
async def download_callback(client: Client, callback_query: CallbackQuery):
    """Handle download button."""
    # Extract file_id and name from callback data
    parts = callback_query.data.split('_', 2)
    if len(parts) < 3:
        await callback_query.answer("Invalid file information", show_alert=True)
        return
        
    file_id = parts[1]
    file_name = parts[2]
    
    try:
        # Get direct file URLs
        file_urls = await get_file_urls(client, file_id, file_name)
        
        if not file_urls:
            await callback_query.answer("Could not generate download links", show_alert=True)
            return
            
        # Send download options
        await callback_query.message.reply(
            f"📥 **Download Options for:** `{file_name}`\n\n"
            f"Choose how you want to access this file:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬇️ Direct Download", url=file_urls["download_url"])],
                [InlineKeyboardButton("🔗 Copy Download Link", callback_data=f"copy_{file_urls['download_url']}")],
            ])
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error processing download: {e}")
        await callback_query.answer("Error generating download link", show_alert=True)

@bot_client.on_callback_query(filters.regex(r"^copy_"))
async def copy_link_callback(client: Client, callback_query: CallbackQuery):
    """Handle copy link button."""
    link = callback_query.data[5:]
    await callback_query.answer("Link copied to clipboard:", text=link, show_alert=True)

@bot_client.on_callback_query(filters.regex(r"^fileinfo_"))
async def file_info_callback(client: Client, callback_query: CallbackQuery):
    """Handle file info button clicks."""
    message_id = int(callback_query.data.split("_")[1])
    
    try:
        # Get the message
        message = await client.get_messages(
            callback_query.message.chat.id, 
            message_id
        )
        
        if not message or not message.media:
            await callback_query.answer("File information not available", show_alert=True)
            return
            
        # Extract file information
        file_info = {}
        
        for media_type in SUPPORTED_MEDIA:
            media_obj = getattr(message, media_type, None)
            if media_obj:
                file_info["type"] = media_type
                file_info["size"] = media_obj.file_size
                file_info["name"] = getattr(media_obj, "file_name", None) or f"{media_type}_{int(time.time())}"
                if media_type == "video":
                    file_info["width"] = media_obj.width
                    file_info["height"] = media_obj.height
                    file_info["duration"] = media_obj.duration
                elif media_type == "audio":
                    file_info["duration"] = media_obj.duration
                    file_info["performer"] = media_obj.performer
                    file_info["title"] = media_obj.title
                break
                
        if not file_info:
            await callback_query.answer("File information not available", show_alert=True)
            return
            
        # Format file size
        size_str = "Unknown"
        if file_info.get("size"):
            size_bytes = file_info["size"]
            if size_bytes < 1024:
                size_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes/1024:.2f} KB"
            elif size_bytes < 1024 * 1024 * 1024:
                size_str = f"{size_bytes/(1024*1024):.2f} MB"
            else:
                size_str = f"{size_bytes/(1024*1024*1024):.2f} GB"
                
        # Build info text
        info_text = f"📄 **File Information**\n\n"
        info_text += f"• **Name:** `{file_info.get('name', 'Unknown')}`\n"
        info_text += f"• **Type:** `{file_info.get('type', 'Unknown')}`\n"
        info_text += f"• **Size:** `{size_str}`\n"
        
        if "duration" in file_info:
            mins, secs = divmod(file_info["duration"], 60)
            hours, mins = divmod(mins, 60)
            duration_str = f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
            info_text += f"• **Duration:** `{duration_str}`\n"
            
        if "width" in file_info and "height" in file_info:
            info_text += f"• **Resolution:** `{file_info['width']}x{file_info['height']}`\n"
            
        await callback_query.answer()
        await callback_query.message.reply(info_text)
        
    except Exception as e:
        logger.error(f"Error processing file info: {e}")
        await callback_query.answer("Error retrieving file information", show_alert=True)

@bot_client.on_callback_query(filters.regex(r"^help$"))
async def help_callback(client: Client, callback_query: CallbackQuery):
    """Handle help button callback."""
    await help_command(client, callback_query.message)
    await callback_query.answer()

@bot_client.on_callback_query(filters.regex(r"^status$"))
async def status_callback(client: Client, callback_query: CallbackQuery):
    """Handle status button callback."""
    await status_command(client, callback_query.message)
    await callback_query.answer()

@bot_client.on_message(filters.text & ~filters.command(["start", "batch", "cancel", "help", "status"]))
async def handle_text(client: Client, message: Message):
    """Handle user input through state machine."""
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    if not state:
        return
        
    text = message.text.strip()
    
    try:
        if state["step"] == "start":
            chat_id, start_id, link_type = extract_chat_info(text)
            if not chat_id or not start_id:
                await message.reply("❌ Invalid link format!\n"
                                  "Please use proper Telegram message link")
                user_states.pop(user_id, None)
                return
                
            user_states[user_id] = {
                "step": "count",
                "chat_id": chat_id,
                "start_id": start_id,
                "link_type": link_type
            }
            await message.reply(
                f"🔢 How many messages to forward? (Max {MAX_MESSAGES})",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("5", callback_data="count_5"),
                        InlineKeyboardButton("10", callback_data="count_10"),
                        InlineKeyboardButton("20", callback_data="count_20")
                    ],
                    [
                        InlineKeyboardButton("50", callback_data="count_50"),
                        InlineKeyboardButton("100", callback_data="count_100")
                    ]
                ])
            )
            
        elif state["step"] == "count":
            if not text.isdigit() or not (0 < int(text) <= MAX_MESSAGES):
                await message.reply(f"⚠️ Please enter a valid number between 1-{MAX_MESSAGES}")
                return
                
            user_states[user_id].update({
                "step": "destination",
                "count": min(int(text), MAX_MESSAGES)
            })
            await message.reply("📩 Please send destination chat ID:")
            
        elif state["step"] == "destination":
            dest_chat = text.strip()
    
