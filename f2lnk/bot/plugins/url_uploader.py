import os
import time
import aiohttp
import asyncio
import mimetypes
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

@StreamBot.on_message(filters.command("upload") & filters.private & filters.user(Var.OWNER_ID))
async def url_upload_handler(c: Client, m: Message):
    if m.reply_to_message and m.reply_to_message.text:
        url = m.reply_to_message.text
    elif len(m.command) > 1:
        url = m.command[1]
    else:
        await m.reply_text("<b>Usage:</b> /upload <direct_file_link> or reply to a link.")
        return

    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        await m.reply_text("Invalid URL. Please provide a valid direct download link.")
        return

    # --- Interactive Conversation ---
    try:
        # Ask for new filename
        ask_filename = await c.ask(
            chat_id=m.chat.id,
            text=f"**URL:** `{url}`\n\nPlease send me the desired filename for this file, including the extension (e.g., `My Video.mp4`).\n\nSend /skip to use the original filename.",
            timeout=60
        )
        new_filename = None
        if ask_filename.text and ask_filename.text.lower() != "/skip":
            new_filename = ask_filename.text

        # Ask for thumbnail
        ask_thumb = await c.ask(
            chat_id=m.chat.id,
            text="Please send me a thumbnail for this file.\n\nSend /skip to use a default thumbnail.",
            timeout=60
        )
        thumb_path = None
        if ask_thumb.photo:
            thumb_path = await c.download_media(ask_thumb, file_name=f"thumb_{m.from_user.id}.jpg")

    except ListenerTimeout:
        await m.reply_text("Request timed out. Please start over.")
        return
    except Exception as e:
        await m.reply_text(f"An error occurred: `{e}`. Please try again.")
        return
    
    # --- Downloading and Uploading ---
    status_msg = await m.reply_text("Processing your request, please wait...")
    file_path = f"./downloads/{time.time()}/"
    os.makedirs(file_path, exist_ok=True)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=None) as resp:
                if resp.status != 200:
                    await status_msg.edit(f"Download failed! Server returned status code: `{resp.status}`")
                    if os.path.exists(file_path):
                        os.rmdir(file_path)
                    return
                
                # Get filename from URL if not provided
                if not new_filename:
                    parsed_url = urlparse(url)
                    new_filename = os.path.basename(parsed_url.path) or f"Untitled_{time.time()}"

                total_size = int(resp.headers.get('Content-Length', 0))
                downloaded_size = 0
                last_update_time = time.time()
                
                # Download file
                file_save_path = os.path.join(file_path, new_filename)
                with open(file_save_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        # Update progress
                        current_time = time.time()
                        if current_time - last_update_time > 2:
                            progress_text = (
                                f"**Downloading...**\n"
                                f"**File:** `{new_filename}`\n"
                                f"Progress: {humanbytes(downloaded_size)} / {humanbytes(total_size)}\n"
                                f"Percentage: {downloaded_size / total_size * 100:.2f}%"
                            )
                            try:
                                await status_msg.edit_text(progress_text)
                            except MessageNotModified:
                                pass
                            last_update_time = current_time

        await status_msg.edit_text("Download complete. Now uploading to Telegram...")
        
        last_update_time = time.time()
        
        # Progress callback for upload
        async def progress(current, total):
            nonlocal last_update_time
            current_time = time.time()
            if current_time - last_update_time > 2:
                progress_text = (
                    f"**Uploading...**\n"
                    f"**File:** `{new_filename}`\n"
                    f"Progress: {humanbytes(current)} / {humanbytes(total)}\n"
                    f"Percentage: {current / total * 100:.2f}%"
                )
                try:
                    await status_msg.edit_text(progress_text)
                except MessageNotModified:
                    pass
                last_update_time = current_time
        
        # Upload to BIN_CHANNEL
        media_type = mimetypes.guess_type(file_save_path)[0]
        if media_type and media_type.startswith("video"):
             log_msg = await c.send_video(
                chat_id=Var.BIN_CHANNEL,
                video=file_save_path,
                thumb=thumb_path,
                file_name=new_filename,
                progress=progress
             )
        else:
             log_msg = await c.send_document(
                chat_id=Var.BIN_CHANNEL,
                document=file_save_path,
                thumb=thumb_path,
                file_name=new_filename,
                progress=progress
             )

        # Generate link
        file_name = get_name(log_msg)
        file_hash = get_hash(log_msg)
        file_size = os.path.getsize(file_save_path)

        stream_link = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        online_link = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        
        await status_msg.edit(
            text=msg_text.format(file_name, humanbytes(file_size), online_link, stream_link),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream_link), InlineKeyboardButton('DOWNLOAD 🔻', url=online_link)]])
        )

    except Exception as e:
        await status_msg.edit(f"An error occurred during the process: `{e}`")
    finally:
        # Cleanup
        if os.path.exists(file_path):
            import shutil
            shutil.rmtree(file_path)
