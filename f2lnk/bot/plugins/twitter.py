# f2lnk/bot/plugins/twitter.py

import re
import aiohttp
import asyncio
import os
import html
import datetime
import shutil
from urllib.parse import quote_plus, urlparse
from asyncio import CancelledError

from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot, ACTIVE_TWITTER_TASKS
from f2lnk.vars import Var
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.utils.file_properties import get_name, get_hash
from f2lnk.bot.plugins.stream import is_maintenance_mode

db = Database(Var.DATABASE_URL, Var.name)
MAX_FILE_SIZE = 1.95 * 1024 * 1024 * 1024
TWITTER_URL_REGEX = r"(?:https?:\/\/)?(?:www\.)?(?:twitter|x)\.com\/\w+\/(?:status|web)\/(\d+)"

class APIException(Exception):
    pass

async def get_tweet_media(tweet_id: str):
    """Fetches all media items (videos and images) from a tweet."""
    api_url = f'https://api.vxtwitter.com/Twitter/status/{tweet_id}'
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url) as resp:
            if resp.status != 200:
                try:
                    text = await resp.text()
                    if match := re.search(r'<meta content="(.*?)" property="og:description" />', text):
                        raise APIException(f'API Error: {html.unescape(match.group(1))}')
                except Exception: pass
                raise APIException(f'Failed to fetch tweet data (Status: {resp.status})')
            try:
                data = await resp.json()
                return data.get('media_extended', [])
            except aiohttp.ContentTypeError:
                raise APIException('API returned an invalid response.')

async def _run_twitter_process(c: Client, m: Message):
    """The core, cancellable logic for handling a Twitter URL."""
    status_msg = None
    download_dir = f"downloads/{m.from_user.id}_{int(datetime.datetime.now().timestamp())}/"
    
    try:
        match = re.search(TWITTER_URL_REGEX, m.text)
        if not match: return
        tweet_id = match.group(1)
        
        status_msg = await m.reply_text(f"🐦 Found Twitter link. Fetching media for tweet **{tweet_id}**...", quote=True)
        
        media_items = await get_tweet_media(tweet_id)
        if not media_items:
            await status_msg.edit("This tweet contains no downloadable media.")
            return

        for item_index, item in enumerate(media_items):
            media_url, media_type, thumb_url = item.get('url'), item.get('type'), item.get('thumbnail_url')
            if not media_url: continue

            original_filename = os.path.basename(urlparse(media_url).path)
            new_filename = original_filename
            
            try:
                ask_filename = await c.ask(chat_id=m.chat.id, text=f"**File {item_index + 1}/{len(media_items)}:** `{original_filename}`\n\nSend a new filename including extension, or send /skip to use the default.", timeout=180)
                if ask_filename.text and ask_filename.text.lower() != "/skip":
                    new_filename = ask_filename.text
            except ListenerTimeout:
                await m.reply_text("⌛ Timeout. Using original filename.", quote=True)

            await status_msg.edit(f"Checking details for **{new_filename}**...")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(media_url, timeout=15) as resp:
                        file_size = int(resp.headers.get('Content-Length', 0))
            except Exception as e:
                await m.reply_text(f"⚠️ Failed to get size for **{new_filename}**: `{e}`. Skipping.", quote=True)
                continue
            
            if file_size > MAX_FILE_SIZE:
                await m.reply_text(f"Skipping **{new_filename}**. File size (**{humanbytes(file_size)}**) is over the 1.95 GB limit.", quote=True)
                continue

            if m.from_user.id not in Var.OWNER_ID:
                # Bandwidth checking logic can be placed here
                pass
            
            download_path, thumb_path = None, None
            os.makedirs(download_dir, exist_ok=True)

            # Download thumbnail for both videos and images (if available)
            if thumb_url:
                thumb_path = os.path.join(download_dir, "thumb.jpg")
                async with aiohttp.ClientSession() as session, session.get(thumb_url) as resp:
                    with open(thumb_path, 'wb') as f:
                        async for chunk in resp.content.iter_any(): f.write(chunk)

            download_path = os.path.join(download_dir, new_filename)
            await status_msg.edit(f"Downloading **{new_filename}** (**{humanbytes(file_size)}**)...")
            async with aiohttp.ClientSession() as session, session.get(media_url) as resp:
                with open(download_path, 'wb') as f:
                    async for chunk in resp.content.iter_any():
                        f.write(chunk)
                        await asyncio.sleep(0) # Allow cancellation to interrupt here

            await status_msg.edit(f"⬆️ Uploading **{new_filename}**...")
            
            # --- MODIFIED: Upload images as documents to preserve quality ---
            if media_type in ['video', 'gif']:
                log_msg = await c.send_video(Var.BIN_CHANNEL, download_path, thumb=thumb_path, file_name=new_filename)
            else: # Send images and other types as files
                log_msg = await c.send_document(Var.BIN_CHANNEL, download_path, thumb=thumb_path, file_name=new_filename)

            dump_log_text = (f"**File:** **{new_filename}**\n"
                             f"**User:** [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
                             f"**User ID:** `{m.from_user.id}`\n"
                             f"**Source Tweet:** {m.text}")
            await log_msg.reply_text(dump_log_text, quote=True, disable_web_page_preview=True)

            file_name, file_hash = get_name(log_msg), get_hash(log_msg)
            stream = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
            download = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream), InlineKeyboardButton('DOWNLOAD 🔻', url=download)]])
            
            # --- NEW: Fetch footer and create custom caption ---
            user_info = await db.get_user_info(m.from_user.id)
            footer = user_info.get("footer", "")
            
            caption = f"**{file_name}**"
            if footer:
                caption += f"\n\n{footer}"
            
            # --- MODIFIED: Reply with correct media type and new caption ---
            if log_msg.video:
                await m.reply_video(log_msg.video.file_id, caption=caption, reply_markup=markup, quote=True)
            else:
                await m.reply_document(log_msg.document.file_id, caption=caption, reply_markup=markup, quote=True)
            
            await db.update_user_stats(m.from_user.id, file_size)
            await c.send_message(Var.LOG_CHANNEL, f"**Link Generated (Twitter)**\n\n**User:** **{m.from_user.first_name}** (`{m.from_user.id}`)\n**File:** **{file_name}**\n**Download:** [Click Here]({download})", disable_web_page_preview=True)

    except CancelledError:
        if status_msg:
            await status_msg.edit("✅ Process successfully cancelled.")
    except Exception as e:
        if status_msg:
            await status_msg.edit(f"An error occurred: `{e}`")
    finally:
        if os.path.exists(download_dir):
            shutil.rmtree(download_dir)
        if status_msg:
            await asyncio.sleep(3)
            await status_msg.delete()

@StreamBot.on_message(filters.regex(TWITTER_URL_REGEX) & filters.private, group=3)
async def twitter_task_handler(c: Client, m: Message):
    """Starts and manages the Twitter download task."""
    user_id = m.from_user.id
    
    # For regular users, check if they have any active tasks. Admins can bypass this.
    if user_id not in Var.OWNER_ID and user_id in ACTIVE_TWITTER_TASKS and ACTIVE_TWITTER_TASKS.get(user_id):
        await m.reply_text("You already have an active process running. Please wait for it to complete or use /cancel.", quote=True)
        return

    # Initial checks (auth, ban, maintenance) can be placed here
    if await db.is_banned(user_id):
        await m.reply_text(Var.BAN_ALERT, quote=True)
        return

    # Initialize the list of tasks for the user if it doesn't exist
    if user_id not in ACTIVE_TWITTER_TASKS:
        ACTIVE_TWITTER_TASKS[user_id] = []

    task = asyncio.create_task(_run_twitter_process(c, m))
    ACTIVE_TWITTER_TASKS[user_id].append(task)
    try:
        await task
    except CancelledError:
        pass # The task itself handles the cancellation message
    finally:
        # Ensure the user's task is always removed from the active list when it's done
        if user_id in ACTIVE_TWITTER_TASKS:
            try:
                ACTIVE_TWITTER_TASKS[user_id].remove(task)
                if not ACTIVE_TWITTER_TASKS[user_id]:
                    ACTIVE_TWITTER_TASKS.pop(user_id, None)
            except ValueError:
                # Task might have been removed by the cancel command already
                pass
