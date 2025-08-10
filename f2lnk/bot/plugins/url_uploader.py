import os
import time
import aiohttp
import asyncio
import mimetypes
import datetime
from urllib.parse import quote_plus, urlparse

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.utils.file_properties import get_name, get_hash

db = Database(Var.DATABASE_URL, Var.name)
msg_text ="""<b>‣ ʏᴏᴜʀ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ ! 😎

‣ Fɪʟᴇ ɴᴀᴍᴇ : <i>{}</i>
‣ Fɪʟᴇ ꜱɪᴢᴇ : {}

🔻 <a href="{}">𝗙𝗔𝗦𝗧 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗</a>
🔺 <a href="{}">𝗪𝗔𝗧𝗖𝗛 𝗢𝗡𝗟𝗜𝗡𝗘</a>

‣ ɢᴇᴛ <a href="https://t.me/+PA8OPL2Zglk3MDM1">ᴍᴏʀᴇ ғɪʟᴇs</a></b> 🤡"""

@StreamBot.on_message(filters.command("upload") & filters.private)
async def url_upload_handler(c: Client, m: Message):
    if m.reply_to_message and m.reply_to_message.text:
        url = m.reply_to_message.text
    elif len(m.command) > 1:
        url = m.command[1]
    else:
        await m.reply_text("<b>Usage:</b> /upload <direct_file_link> or reply to a link.")
        return

    if not url.startswith(("http://", "https://")):
        await m.reply_text("Invalid URL. Please provide a valid direct download link.")
        return

    status_msg = await m.reply_text("`Checking URL and your daily limits...`", quote=True)

    # --- User limit check ---
    total_size = 0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=10, allow_redirects=True) as resp:
                if resp.status == 200:
                    total_size = int(resp.headers.get('Content-Length', 0))
                else:
                    await status_msg.edit(f"URL is invalid or inaccessible. **Status:** `{resp.status}`")
                    return
    except Exception as e:
        await status_msg.edit(f"Failed to get file details from URL: `{e}`")
        return

    if total_size == 0:
        await status_msg.edit("Could not determine file size from the URL. Cannot proceed.")
        return
        
    if m.from_user.id not in Var.OWNER_ID:
        await db.check_and_update_tier(m.from_user.id)
        user_data = await db.get_user_info(m.from_user.id)
        user_tier = user_data.get('tier', Var.DEFAULT_PLAN)
        daily_limit_gb = Var.USER_PLANS.get(user_tier, Var.DAILY_LIMIT_GB)
        limit_in_bytes = daily_limit_gb * 1024 * 1024 * 1024
        today = datetime.date.today().isoformat()
        if user_data.get("last_reset_date") != today:
            await db.reset_daily_usage(m.from_user.id)
            user_data['daily_data_used'] = 0
        
        if user_data.get("daily_data_used", 0) + total_size > limit_in_bytes:
            await status_msg.edit(f"Sorry, you don't have enough daily bandwidth to upload this file.\n\n"
                                  f"**File Size:** `{humanbytes(total_size)}`\n"
                                  f"**Available:** `{humanbytes(limit_in_bytes - user_data.get('daily_data_used', 0))}`\n"
                                  "Please try again tomorrow or upgrade your plan.")
            return

    # --- Interactive Conversation ---
    try:
        ask_filename = await c.ask(
            chat_id=m.chat.id,
            text=f"**URL:** `{url}`\n**File Size:** `{humanbytes(total_size)}`\n\nPlease send the desired filename, including extension (e.g., `My Video.mp4`).\n\nSend /skip to use original filename.",
            timeout=60
        )
        new_filename = None
        if ask_filename.text and ask_filename.text.lower() != "/skip":
            new_filename = ask_filename.text

        ask_thumb = await c.ask(
            chat_id=m.chat.id,
            text="Please send a thumbnail for this file.\n\nSend /skip for no thumbnail.",
            timeout=60
        )
        thumb_path = None
        if ask_thumb.photo:
            thumb_path = await c.download_media(ask_thumb, file_name=f"thumb_{m.from_user.id}.jpg")

    except ListenerTimeout:
        await status_msg.edit("Request timed out. Please start over.")
        return
    except Exception as e:
        await status_msg.edit(f"An error occurred: `{e}`. Please try again.")
        return
    
    # --- Downloading and Uploading ---
    await status_msg.edit("`Processing your request...`")
    file_path = f"./downloads/{time.time()}/"
    os.makedirs(file_path, exist_ok=True)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=None) as resp:
                if resp.status != 200:
                    await status_msg.edit(f"Download failed! Server returned status: `{resp.status}`")
                    if os.path.exists(file_path):
                        os.rmdir(file_path)
                    return
                
                if not new_filename:
                    parsed_url = urlparse(url)
                    new_filename = os.path.basename(parsed_url.path) or f"Untitled_{time.time()}"

                downloaded_size = 0
                last_update_time = time.time()
                
                file_save_path = os.path.join(file_path, new_filename)
                with open(file_save_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        current_time = time.time()
                        if current_time - last_update_time > 2:
                            try:
                                await status_msg.edit_text(
                                    f"**Downloading...**\n"
                                    f"`{new_filename}`\n"
                                    f"`{humanbytes(downloaded_size)}` of `{humanbytes(total_size)}`"
                                )
                            except MessageNotModified:
                                pass
                            last_update_time = current_time

        await status_msg.edit_text("Download complete. Now uploading to Telegram...")
        
        # --- Update user stats in DB ---
        await db.update_user_stats(m.from_user.id, downloaded_size)
        
        last_update_time = time.time()
        
        async def progress(current, total):
            nonlocal last_update_time
            current_time = time.time()
            if current_time - last_update_time > 2:
                try:
                    await status_msg.edit_text(
                        f"**Uploading...**\n"
                        f"`{new_filename}`\n"
                        f"`{humanbytes(current)}` of `{humanbytes(total)}`"
                    )
                except MessageNotModified:
                    pass
                last_update_time = current_time
        
        media_type = mimetypes.guess_type(file_save_path)[0]
        if media_type and media_type.startswith("video"):
             log_msg = await c.send_video(
                chat_id=Var.BIN_CHANNEL, video=file_save_path, thumb=thumb_path,
                file_name=new_filename, progress=progress
             )
        else:
             log_msg = await c.send_document(
                chat_id=Var.BIN_CHANNEL, document=file_save_path, thumb=thumb_path,
                file_name=new_filename, progress=progress
             )

        file_name = get_name(log_msg)
        file_hash = get_hash(log_msg)
        file_size = os.path.getsize(file_save_path)

        stream_link = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        online_link = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        
        # --- Send file and links to user ---
        final_caption = msg_text.format(file_name, humanbytes(file_size), online_link, stream_link)
        
        if log_msg.video:
            await c.send_video(
                chat_id=m.chat.id,
                video=log_msg.video.file_id,
                caption=final_caption,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream_link), InlineKeyboardButton('DOWNLOAD 🔻', url=online_link)]])
            )
        else:
            await c.send_document(
                chat_id=m.chat.id,
                document=log_msg.document.file_id,
                caption=final_caption,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream_link), InlineKeyboardButton('DOWNLOAD 🔻', url=online_link)]])
            )
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit(f"An error occurred: `{e}`")
    finally:
        if os.path.exists(file_path):
            import shutil
            shutil.rmtree(file_path)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
