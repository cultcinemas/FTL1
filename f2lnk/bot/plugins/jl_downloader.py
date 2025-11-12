import os
import re
import time
import shutil
import aiohttp
import asyncio
import mimetypes
import subprocess
from urllib.parse import urlparse, unquote, urljoin
from typing import Optional
from bs4 import BeautifulSoup

from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified
from pyromod.exceptions import ListenerTimeout

# Assuming these are imported correctly from your project environment
from f2lnk.bot import StreamBot
from f2lnk.utils.database import Database
from f2lnk.vars import Var

# Mock objects (If you are running outside your FTL environment, keep these. Otherwise, use your actual imports.)
class Database:
    def __init__(self, *args): pass
    async def get_user_info(self, user_id): return {"footer": "Downloaded via StreamBot"}

class Var:
    DATABASE_URL = "sqlite://test.db"
    name = "jl_downloader_bot"
    
db = Database(Var.DATABASE_URL, Var.name)
DOWNLOAD_ROOT = "downloads"
os.makedirs(DOWNLOAD_ROOT, exist_ok=True)

# ---------------------------
# Helpers
# ---------------------------

def humanbytes(size: int) -> str:
    if not size: return "0 B"
    power = 1024
    n = 0
    Dic_powerN = {0: "B", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {Dic_powerN[n]}"

# ---------------------------
# Intelligent URL Extractor
# ---------------------------

async def extract_media_url(page_url: str, headers: dict) -> Optional[str]:
    """
    Async function to extract media URL from a webpage by parsing HTML and falling back to regex.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(page_url, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    print(f"[Extractor] HTTP {response.status} for {page_url}")
                    return None
                html = await response.text()

        soup = BeautifulSoup(html, "html.parser")
        video_tags = soup.find_all("video")
        for tag in video_tags:
            source_tag = tag.find("source")
            if source_tag and source_tag.get("src"):
                return source_tag["src"]
            if tag.get("src"):
                return tag["src"]

        patterns = [
            # FIX: Corrected SyntaxError: EOL while scanning string literal
            r'source\s*:\s*["\']', 
            r'"file"\s*:\s*"([^"]+.(?:mp4|m3u8|mkv)[^"])"',
            r'"src"\s:\s*"([^"]+.(?:mp4|m3u8|mkv)[^"])"',
            r'https?://[^\s"\'<>]+.(?:mp4|mkv|webm|m3u8)[^\s"\'<>]'
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                return match.group(match.lastindex or 0)

        return None
    except Exception as e:
        print(f"[Extractor] An error occurred: {e}")
        return None

# --- Robust HLS Downloader with Headers (FIXED FOR SMOOTH PLAYBACK) ---

async def download_hls_stream(stream_url: str, file_path: str, status_msg: Message, headers: dict):
    """
    Downloads HLS stream using ffmpeg, passing browser headers and RE-ENCODING for smooth playback.
    
    FIX: Re-encoding with libx264/aac instead of '-c copy' to fix timestamp and lagging-frame issues.
    """
    await status_msg.edit("⬇️ Downloading and Processing HLS stream...\n(This uses FFmpeg and may take some time as it's re-encoding for smooth playback.)")
    
    header_str = "".join([f"{key}: {value}\r\n" for key, value in headers.items()])
    
    process = await asyncio.create_subprocess_exec(
        'ffmpeg', 
        '-y', 
        '-headers', header_str, 
        '-i', stream_url, 
        '-c:v', 'libx264',    # Video codec: H.264 (Universally compatible)
        '-c:a', 'aac',       # Audio codec: AAC (Standard for MP4)
        '-b:a', '128k',      # Audio bitrate (Good default)
        '-crf', '23',        # Quality setting (Good balance)
        '-movflags', '+faststart', # Optimize for web streaming
        file_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    
    _, stderr = await process.communicate()
    
    if process.returncode != 0:
        error_message = stderr.decode().strip()
        raise Exception(f"FFmpeg failed: {error_message.splitlines()[-1]}")
    
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        raise Exception("FFmpeg finished, but the output file is missing or empty.")

# ---------------------------
# Core download + upload logic
# ---------------------------

async def process_jl_task(client: Client, message: Message, page_url: str):
    status_msg = None
    temp_dir = os.path.join(DOWNLOAD_ROOT, f"{message.from_user.id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    thumb_path = None
    file_path = None

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": page_url
    }

    try:
        parsed_page_url = urlparse(page_url)
        is_direct_link = os.path.splitext(parsed_page_url.path)[1].lower() in ['.mp4', '.mkv', '.m3u8']

        if is_direct_link:
            status_msg = await message.reply_text("🎥 Direct media link detected!", quote=True)
            direct_url = page_url
        else:
            status_msg = await message.reply_text("🔍 Scraping website for video...", quote=True)
            direct_url = await extract_media_url(page_url, browser_headers)
            if not direct_url:
                await status_msg.edit("❌ Failed to find a downloadable video.")
                return

        direct_url = urljoin(page_url, direct_url)
        await status_msg.edit(f"✅ Video found!\n{direct_url[:70]}...\n\n⏳ Checking details...")

        total_size, is_hls = 0, '.m3u8' in direct_url

        if not is_hls:
            try:
                # FIX: Pass headers to head() request to avoid HTTP 400/403 errors during size check.
                async with aiohttp.ClientSession() as s, s.head(direct_url, headers=browser_headers, timeout=20) as r:
                    if r.status == 200: total_size = int(r.headers.get("Content-Length", 0))
            except Exception: pass

        if total_size > 1.95 * 1024**3:
            await status_msg.edit(f"❌ File too large: {humanbytes(total_size)}")
            return

        try:
            default_filename = unquote(os.path.basename(urlparse(direct_url).path)) or "video.mp4"
            if is_hls: default_filename = os.path.splitext(default_filename)[0] + ".mp4"
            
            ask_name = await message.chat.ask(f"📦 Size: {humanbytes(total_size) if total_size else 'Unknown'}\n\nSend filename or /skip to use default:\n{default_filename}", timeout=90)
            custom_name = ask_name.text.strip() if ask_name.text and ask_name.text.lower() != "/skip" else None

            ask_thumb = await message.chat.ask("📸 Send a thumbnail or /skip for none.", timeout=60)
            if ask_thumb.photo:
                thumb_path = await ask_thumb.download(os.path.join(temp_dir, "thumb.jpg"))
        except ListenerTimeout:
            await status_msg.edit("⌛ Timeout – aborting.")
            return

        filename = custom_name or default_filename
        file_path = os.path.join(temp_dir, filename)

        if is_hls:
            # Calls the fixed HLS download function
            await download_hls_stream(direct_url, file_path, status_msg, browser_headers)
        else:
            await status_msg.edit("⬇️ Starting download...")
            
            last_update = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.get(direct_url, headers=browser_headers, timeout=None) as resp:
                    if resp.status != 200:
                        await status_msg.edit(f"❌ Download failed – HTTP {resp.status}")
                        return

                    downloaded = 0
                    with open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024): # 1MB Chunks
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last_update > 3:
                                try:
                                    progress = f"⬇️ Downloading...\n{humanbytes(downloaded)}"
                                    if total_size: progress += f" / {humanbytes(total_size)}"
                                    await status_msg.edit(progress)
                                except MessageNotModified: pass
                                last_update = now
        
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            raise Exception("Download completed, but the final file is missing or empty.")

        await status_msg.edit("📤 Download complete! Uploading...")
        
        # FIX: Initialize last_update in the outer scope before the progress function
        last_update = time.time()
        
        async def up_progress(cur, tot):
            nonlocal last_update
            now = time.time()
            if now - last_update > 3:
                try: 
                    await status_msg.edit(f"📤 Uploading... {humanbytes(cur)} / {humanbytes(tot)}")
                except MessageNotModified: 
                    pass
                except Exception:
                    pass
                last_update = now

        user_info = await db.get_user_info(message.from_user.id)
        footer = user_info.get("footer", "")
        caption = f"{filename}" + (f"\n\n{footer}" if footer else "")
        
        # FIX: Send video with proper error handling
        try:
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_path,
                caption=caption,
                progress=up_progress,
                supports_streaming=True
            )
            await status_msg.delete()
        except Exception as upload_error:
            await status_msg.edit(f"❌ Upload failed: {str(upload_error)}")
            raise

    except Exception as e:
        if status_msg: await status_msg.edit(f"❌ An error occurred: {e}")
        import traceback; traceback.print_exc()
        
    finally:
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

# ---------------------------
# Command Handler
# ---------------------------

@StreamBot.on_message(filters.command(["jl_downloader", "jl"]) & filters.private)
async def jl_handler(client: Client, m: Message):
    if len(m.command) < 2 and not m.reply_to_message:
        await m.reply_text("🎬 Website Video Downloader\n\nUsage: /jl <website_url>")
        return

    url = m.command[1].strip() if len(m.command) > 1 else m.reply_to_message.text.strip()
    if not url.startswith("http"):
        await m.reply_text("❌ Please send a valid URL.")
        return

    await process_jl_task(client, m, url)
    
