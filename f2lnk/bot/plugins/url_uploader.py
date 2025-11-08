# f2lnk/bot/plugins/url_uploader.py

import os
import time
import aiohttp
import asyncio
import mimetypes
import datetime
from urllib.parse import quote_plus, urlparse
from asyncio import CancelledError

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot, ACTIVE_UPLOADS, ACTIVE_TWITTER_TASKS
from f2lnk.vars import Var
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.utils.file_properties import get_name, get_hash

db = Database(Var.DATABASE_URL, Var.name)

# Set the maximum file size to 1.95 GB to avoid Telegram API errors
MAX_FILE_SIZE = 1.95 * 1024 * 1024 * 1024


async def _start_upload_process(c: Client, m: Message):
    """
    This function contains the entire logic for the upload process.
    It's run as a separate asyncio task to make it cancellable.
    """
    status_msg = None
    file_path = f"./downloads/{time.time()}/"
    thumb_path = None
    
    try:
        if m.reply_to_message and m.reply_to_message.text:
            url = m.reply_to_message.text.strip()
        elif len(m.command) > 1:
            url = m.command[1].strip()
        else:
            # This case is handled by the launcher, but as a safeguard:
            await m.reply_text("<b>Usage:</b> /upload <direct_file_link> or reply to a link.")
            return

        if not url.startswith(("http://", "https://")):
            await m.reply_text("Invalid URL. Please provide a valid direct download link.")
            return

        status_msg = await m.reply_text("`Checking URL...`", quote=True)

        # --- Get file size and check URL ---
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
            
        # --- NEW: Size Limit Check ---
        if total_size > MAX_FILE_SIZE:
            await status_msg.edit(
                f"File size ({humanbytes(total_size)}) is larger than the allowed limit of **1.95 GB**.\n\n"
                "Please upload a file smaller than 1.95 GB to avoid issues with Telegram's limits."
            )
            return

        # --- User daily limit check ---
        if m.from_user.id not in Var.OWNER_ID:
            await status_msg.edit("`Checking your daily limits...`")
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
            default_filename = os.path.basename(urlparse(url).path) or f"Untitled_{int(time.time())}"
            ask_filename = await c.ask(
                chat_id=m.chat.id,
                text=f"**URL:** `{url}`\n**File Size:** `{humanbytes(total_size)}`\n\nPlease send the desired filename, including extension (e.g., `My Video.mp4`).\n\nSend /skip to use default:\n`{default_filename}`",
                timeout=60
            )
            new_filename = None
            if ask_filename.text and ask_filename.text.lower() != "/skip":
                new_filename = ask_filename.text
            else:
                new_filename = default_filename


            ask_thumb = await c.ask(
                chat_id=m.chat.id,
                text="Please send a thumbnail for this file.\n\nSend /skip for no thumbnail.",
                timeout=60
            )
            if ask_thumb.photo:
                os.makedirs(file_path, exist_ok=True)
                thumb_path = await c.download_media(ask_thumb, file_name=os.path.join(file_path, f"thumb_{m.from_user.id}.jpg"))


        except ListenerTimeout:
            await status_msg.edit("Request timed out. Please start over.")
            return
        
        # --- Downloading and Uploading ---
        await status_msg.edit("`Starting download... You can use /cancel to stop this process.`")
        os.makedirs(file_path, exist_ok=True)
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=None) as resp:
                if resp.status != 200:
                    await status_msg.edit(f"Download failed! Server returned status: `{resp.status}`")
                    return
                
                downloaded_size = 0
                last_update_time = time.time()
                
                file_save_path = os.path.join(file_path, new_filename)
                with open(file_save_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        await asyncio.sleep(0) 

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

        await status_msg.edit_text("`Download complete. Now uploading to Telegram...`")
        
        await db.update_user_stats(m.from_user.id, downloaded_size)
        
        last_update_time = time.time()
        
        async def progress(current, total):
            nonlocal last_update_time
            await asyncio.sleep(0)
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
        
        # --- Upload to BIN_CHANNEL ---
        log_msg = None
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
        
        if not log_msg:
            await status_msg.edit("Failed to upload file to bin channel.")
            return

        file_name = get_name(log_msg)
        file_hash = get_hash(log_msg)

        stream_link = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        online_link = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        
        # --- Fetch footer and create custom caption ---
        user_info = await db.get_user_info(m.from_user.id)
        footer = user_info.get("footer", "")
        
        final_caption = f"**{file_name}**"
        if footer:
            final_caption += f"\n\n{footer}"
        
        # --- Send file to user from BIN_CHANNEL ---
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream_link), InlineKeyboardButton('DOWNLOAD 🔻', url=online_link)]])
        
        if log_msg.video:
            await m.reply_video(video=log_msg.video.file_id, caption=final_caption, reply_markup=reply_markup, quote=True)
        else:
            await m.reply_document(document=log_msg.document.file_id, caption=final_caption, reply_markup=reply_markup, quote=True)
        
        if status_msg:
            await status_msg.delete()

    except CancelledError:
        if status_msg:
            await status_msg.edit("Process was successfully cancelled.")
        print(f"Upload process for user {m.from_user.id} cancelled.")

    except Exception as e:
        if status_msg:
            await status_msg.edit(f"An error occurred: `{e}`")
        print(f"URL Uploader Error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Cleanup resources
        if os.path.exists(file_path):
            import shutil
            shutil.rmtree(file_path, ignore_errors=True)

@StreamBot.on_message(filters.command("upload") & filters.private)
async def url_upload_handler(c: Client, m: Message):
    if m.from_user.id not in Var.OWNER_ID and m.from_user.id in ACTIVE_UPLOADS and ACTIVE_UPLOADS.get(m.from_user.id):
        await m.reply_text("You already have an active upload process. Please wait for it to complete or use /cancel.", quote=True)
        return
        
    if not (m.reply_to_message and m.reply_to_message.text) and not (len(m.command) > 1):
        await m.reply_text("<b>Usage:</b> /upload <direct_file_link> or reply to a link.")
        return

    if m.from_user.id not in ACTIVE_UPLOADS:
        ACTIVE_UPLOADS[m.from_user.id] = []

    task = asyncio.create_task(_start_upload_process(c, m))
    ACTIVE_UPLOADS[m.from_user.id].append(task)

    try:
        await task
    except CancelledError:
        pass
    finally:
        if m.from_user.id in ACTIVE_UPLOADS:
            try:
                ACTIVE_UPLOADS[m.from_user.id].remove(task)
                if not ACTIVE_UPLOADS[m.from_user.id]:
                    ACTIVE_UPLOADS.pop(m.from_user.id, None)
            except ValueError:
                pass


@StreamBot.on_message(filters.command("cancel") & filters.private)
async def universal_cancel_handler(c: Client, m: Message):
    user_id = m.from_user.id
    cancelled_uploads = 0
    cancelled_twitter = 0

    if tasks := ACTIVE_UPLOADS.pop(user_id, None):
        cancelled_uploads = len(tasks)
        for task in tasks:
            if not task.done() and not task.cancelled():
                task.cancel()

    if tasks := ACTIVE_TWITTER_TASKS.pop(user_id, None):
        cancelled_twitter = len(tasks)
        for task in tasks:
            if not task.done() and not task.cancelled():
                task.cancel()

    if cancelled_uploads > 0 or cancelled_twitter > 0:
        await m.reply_text(f"✅ Successfully cancelled {cancelled_uploads} upload task(s) and {cancelled_twitter} Twitter task(s).", quote=True)
    else:
        await m.reply_text("You have no active processes to cancel.", quote=True)
