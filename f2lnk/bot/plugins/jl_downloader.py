# ===============================================================
# Generic HTML/JS Downloader Plugin
# Command: /jl_downloader or /jl
# Works with pages containing direct .mp4 or downloadable URLs.
# Integrates with StreamBot (FTL project).
# Developer: ASN GADGETS
# ===============================================================

import os
import re
import time
import shutil
import aiohttp
import asyncio
import mimetypes
import subprocess
from urllib.parse import urlparse, unquote
from typing import Optional
from bs4 import BeautifulSoup

from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified
from pyromod.exceptions import ListenerTimeout

# Import your bot instance and database
from f2lnk.bot import StreamBot
from f2lnk.utils.database import Database
from f2lnk.vars import Var

db = Database(Var.DATABASE_URL, Var.name)
DOWNLOAD_ROOT = "downloads"
os.makedirs(DOWNLOAD_ROOT, exist_ok=True)

# ---------------------------
# Helpers
# ---------------------------

def humanbytes(size: int) -> str:
    if not size:
        return "0 B"
    power = 1024
    n = 0
    Dic_powerN = {0: "B", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {Dic_powerN[n]}"

# ---------------------------
# Extractor – finds media links in HTML/JS
# ---------------------------

async def extract_media_url(page_url: str) -> Optional[str]:
    """
    Async function to extract media URL from a webpage by parsing HTML and using regex.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(page_url, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    print(f"[extract_media_url] HTTP {response.status} for {page_url}")
                    return None
                
                html = await response.text()

        # Method 1: Use BeautifulSoup to parse the HTML for <video> tags
        soup = BeautifulSoup(html, "html.parser")
        video_tag = soup.find("video")
        if video_tag:
            # Prefer a <source> tag within the <video> tag
            source_tag = video_tag.find("source")
            if source_tag and source_tag.get("src"):
                url = source_tag["src"]
                print(f"[extract_media_url] Found via BeautifulSoup <source src>: {url}")
                return url
            # Fallback to the <video> tag's own src attribute
            if video_tag.get("src"):
                url = video_tag["src"]
                print(f"[extract_media_url] Found via BeautifulSoup <video src>: {url}")
                return url

        # Method 2: Fallback to regex on the entire page content (good for URLs in JavaScript)
        patterns = [
            r'source\s*:\s*["\']([^"\']+\.(?:mp4|mkv|m3u8)[^"\']*)["\']',
            r'"file"\s*:\s*"([^"]+\.(?:mp4|mkv|m3u8)[^"]*)"',
            r'"src"\s*:\s*"([^"]+\.(?:mp4|mkv|m3u8)[^"]*)"',
            r'https?://[^\s"\'<>]+\.(?:mp4|mkv|webm|m3u8)[^\s"\'<>]*'
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                url = match.group(1)
                print(f"[extract_media_url] Found via Regex: {url}")
                return url

        print(f"[extract_media_url] No media URL found using BeautifulSoup or Regex on {page_url}")
        return None
        
    except Exception as e:
        print(f"[extract_media_url] An error occurred during extraction: {e}")
        return None

# --- FFmpeg Downloader for HLS streams ---
async def download_hls_stream(stream_url: str, file_path: str, status_msg: Message):
    """Downloads HLS stream using ffmpeg."""
    await status_msg.edit("**⬇️ Downloading HLS stream with FFmpeg...** (This may take a while and progress is not shown)")
    process = await asyncio.create_subprocess_exec(
        'ffmpeg', '-y', '-i', stream_url, '-c', 'copy', file_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error_message = stderr.decode().strip()
        print(f"FFmpeg error: {error_message}")
        raise Exception(f"FFmpeg failed. Error: {error_message.splitlines()[-1]}")
    
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        raise Exception("FFmpeg finished but the output file is missing or empty.")

    return file_path

# ---------------------------
# Core download + upload logic
# ---------------------------
async def process_jl_task(client: Client, message: Message, page_url: str):
    status_msg = None
    temp_dir = os.path.join(DOWNLOAD_ROOT, f"{message.from_user.id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    thumb_path = None
    file_path = None

    try:
        # ---------- 1. Extract URL ----------
        parsed_page_url = urlparse(page_url)
        file_ext = os.path.splitext(parsed_page_url.path)[1].lower()
        
        is_direct_link = file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m3u8']

        if is_direct_link:
            status_msg = await message.reply_text("**🎥 Direct media link detected!**", quote=True)
            direct_url = page_url
        else:
            status_msg = await message.reply_text("**🔍 Scraping website for video URL...**", quote=True)
            direct_url = await extract_media_url(page_url)
            if not direct_url:
                await status_msg.edit(
                    "**❌ Failed to extract video URL.**\n\n"
                    "This can happen if:\n"
                    "• No downloadable video was found on the page.\n"
                    "• The site uses advanced protection (DRM).\n"
                    "• The video requires a login.\n\n"
                    "**💡 Tip:** Try finding a more direct link if possible."
                )
                return

        # Make URL absolute if it's relative
        if direct_url.startswith("//"):
            direct_url = "https:" + direct_url
        elif direct_url.startswith("/"):
            direct_url = f"{parsed_page_url.scheme}://{parsed_page_url.netloc}{direct_url}"

        print(f"[DEBUG] Final URL to process: {direct_url}")
        await status_msg.edit(f"**✅ Video found!**\n`{direct_url[:70]}...`\n\n**⏳ Checking details...**")

        # ---------- 2. Get details and ask for filename ----------
        total_size = 0
        is_hls = '.m3u8' in direct_url

        if not is_hls:
            try:
                headers = {"User-Agent": "Mozilla/5.0", "Referer": page_url}
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
                    async with sess.head(direct_url, allow_redirects=True) as resp:
                        if resp.status == 200:
                            total_size = int(resp.headers.get("Content-Length", 0))
            except Exception as e:
                print(f"[HEAD request error] {e}")

        size_info = humanbytes(total_size) if total_size > 0 else "Unknown (HLS Stream)"
        
        if total_size > 1.95 * 1024**3:
            await status_msg.edit(f"**❌ File too large:** `{humanbytes(total_size)}`\nTelegram only allows **≤ 1.95 GB**.")
            return

        # ---------- 3. Ask for filename & thumbnail ----------
        try:
            default_filename = unquote(os.path.basename(urlparse(direct_url).path)) or "video.mp4"
            if is_hls:
                default_filename = os.path.splitext(default_filename)[0] + ".mp4"

            ask_name = await message.chat.ask(
                f"**🔗 URL:** `{page_url}`\n**📦 Size:** `{size_info}`\n\nSend **filename** (with extension) or /skip to use default:\n`{default_filename}`",
                timeout=90
            )
            custom_name = ask_name.text.strip() if ask_name.text and ask_name.text.lower() != "/skip" else None

            ask_thumb = await message.chat.ask("**📸 Send a thumbnail** (photo) or /skip for none:", timeout=60)
            if ask_thumb.photo:
                thumb_path = await ask_thumb.download(file_name=os.path.join(temp_dir, f"thumb_{message.from_user.id}.jpg"))
        except ListenerTimeout:
            await status_msg.edit("**⌛ Timeout – aborting.**")
            return

        # ---------- 4. Download ----------
        filename = custom_name or default_filename
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        file_path = os.path.join(temp_dir, filename)

        if is_hls:
            file_path = await download_hls_stream(direct_url, file_path, status_msg)
        else:
            await status_msg.edit("**⬇️ Starting download...**")
            downloaded = 0
            last_update = time.time()
            headers = {"User-Agent": "Mozilla/5.0", "Referer": page_url}
            timeout = aiohttp.ClientTimeout(total=None)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
                async with sess.get(direct_url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        await status_msg.edit(f"**❌ Download failed – HTTP {resp.status}**\nThis can happen if the link is temporary, protected, or invalid.")
                        return
                    
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last_update > 2:
                                try:
                                    progress_text = f"**⬇️ Downloading...**\n`{filename}`\n`{humanbytes(downloaded)}`"
                                    if total_size > 0:
                                        percent = (downloaded / total_size) * 100
                                        progress_text += f" / `{humanbytes(total_size)}` ({percent:.1f}%)"
                                    await status_msg.edit(progress_text)
                                except MessageNotModified:
                                    pass
                                last_update = now

        # ---------- 5. Upload to Telegram ----------
        await status_msg.edit("**📤 Download complete! Uploading to Telegram...**")
        
        async def up_progress(cur, tot):
            nonlocal last_update
            now = time.time()
            if now - last_update > 2:
                try:
                    percent = (cur / tot) * 100 if tot > 0 else 0
                    await status_msg.edit(f"**📤 Uploading...**\n`{filename}`\n`{humanbytes(cur)}` / `{humanbytes(tot)}` ({percent:.1f}%)")
                except MessageNotModified:
                    pass
                last_update = now

        user_info = await db.get_user_info(message.from_user.id)
        footer = user_info.get("footer", "")
        file_size_display = humanbytes(os.path.getsize(file_path))
        
        caption = f"**{filename}**\n\n**📦 Size:** `{file_size_display}`"
        if footer:
            caption += f"\n\n{footer}"
        
        mime = mimetypes.guess_type(file_path)[0] or "video/mp4"
        
        if mime.startswith("video"):
            await client.send_video(chat_id=message.chat.id, video=file_path, thumb=thumb_path, caption=caption, progress=up_progress)
        else:
            await client.send_document(chat_id=message.chat.id, document=file_path, thumb=thumb_path, caption=caption, progress=up_progress)

        await status_msg.delete()

    except asyncio.CancelledError:
        if status_msg: await status_msg.edit("**❌ Task cancelled by user.**")
    except Exception as e:
        error_msg = str(e)
        if status_msg: await status_msg.edit(f"**❌ An error occurred:** `{error_msg[:300]}`")
        print(f"[jl_downloader] Exception: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

# ---------------------------
# Command Handler
# ---------------------------
@StreamBot.on_message(filters.command(["jl_downloader", "jl"]) & filters.private)
async def jl_handler(client: Client, m: Message):
    if len(m.command) < 2 and not m.reply_to_message:
        await m.reply_text(
            "**🎬 JL Website Downloader**\n\n"
            "This command scrapes a website URL to find and download embedded videos.\n\n"
            "**Usage:**\n`/jl <website_url>`\n\n"
            "**Example:**\n`/jl https://example.com/page-with-a-video`\n\n"
            "**⚠️ Max file size:** 1.95 GB (Telegram limit)"
        )
        return

    url = m.command[1].strip() if len(m.command) > 1 else m.reply_to_message.text.strip()
    if not url.startswith(("http://", "https://")):
        await m.reply_text("**❌ Please send a valid URL** starting with http:// or https://")
        return

    await process_jl_task(client, m, url)
