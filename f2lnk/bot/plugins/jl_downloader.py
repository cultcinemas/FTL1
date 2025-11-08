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
from urllib.parse import urlparse
from typing import Optional

from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified
from pyromod.exceptions import ListenerTimeout

# Import your bot instance
from f2lnk.bot import StreamBot

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
# Extractor – finds .mp4 or file links in HTML/JS (ASYNC VERSION)
# ---------------------------

async def extract_media_url(page_url: str) -> Optional[str]:
    """
    Async function to extract media URL from a webpage.
    This prevents blocking the event loop.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(page_url, allow_redirects=True) as response:
                if response.status != 200:
                    print(f"[extract_media_url] HTTP {response.status} for {page_url}")
                    return None
                
                html = await response.text()
        
        # Pattern 1: <video src="...">
        m = re.search(r'<video[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']', html, re.I)
        if m:
            url = m.group(1)
            print(f"[extract_media_url] Found via <video src>: {url}")
            return url

        # Pattern 2: "file": "https://...mp4"
        m = re.search(r'"file"\s*:\s*"([^"]+\.mp4[^"]*)"', html)
        if m:
            url = m.group(1)
            print(f"[extract_media_url] Found via 'file': {url}")
            return url

        # Pattern 3: source: "https://...mp4"
        m = re.search(r'source\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']', html)
        if m:
            url = m.group(1)
            print(f"[extract_media_url] Found via 'source': {url}")
            return url

        # Pattern 4: Any direct .mp4 link in HTML
        m = re.search(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', html)
        if m:
            url = m.group(0)
            print(f"[extract_media_url] Found direct .mp4 link: {url}")
            return url
        
        # Pattern 5: Look for other video formats
        m = re.search(r'https?://[^\s"\'<>]+\.(mkv|avi|mov|wmv|flv|webm)[^\s"\'<>]*', html, re.I)
        if m:
            url = m.group(0)
            print(f"[extract_media_url] Found video file: {url}")
            return url

        print(f"[extract_media_url] No media URL found in page")
        return None
        
    except asyncio.TimeoutError:
        print(f"[extract_media_url] Timeout while fetching {page_url}")
        return None
    except Exception as e:
        print(f"[extract_media_url] Error: {e}")
        return None

# ---------------------------
# Core download + upload logic
# ---------------------------

async def process_jl_task(client: Client, message: Message, page_url: str):
    status = await message.reply_text("🔍 Extracting link...", quote=True)
    temp_dir = os.path.join(DOWNLOAD_ROOT, f"{message.from_user.id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    thumb_path = None
    file_path = None

    try:
        # Extract media URL (now async)
        direct_url = await extract_media_url(page_url)
        
        if not direct_url:
            await status.edit("❌ No direct video/file link found on that page.\n\nMake sure the URL contains a playable video.")
            return

        # Make URL absolute if it's relative
        if direct_url.startswith("//"):
            direct_url = "https:" + direct_url
        elif direct_url.startswith("/"):
            parsed = urlparse(page_url)
            direct_url = f"{parsed.scheme}://{parsed.netloc}{direct_url}"

        await status.edit(f"✅ Found link:\n`{direct_url[:80]}...`\n\n⏳ Checking file size...")

        # HEAD request for size
        total_size = 0
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.head(direct_url, allow_redirects=True) as r:
                    total_size = int(r.headers.get("Content-Length", 0))
        except Exception as e:
            print(f"[HEAD request error] {e}")
            # Continue anyway, we'll handle unknown size during download

        if total_size > 1.95 * 1024**3:
            await status.edit(f"⚠️ File too large ({humanbytes(total_size)}). Telegram max ≈ 2 GB.")
            return

        size_info = humanbytes(total_size) if total_size > 0 else "Unknown size"
        await status.edit(f"📊 File size: {size_info}\n\n⏳ Waiting for filename...")

        # Ask filename and thumbnail
        try:
            ask_name = await message.chat.ask(
                "✏️ Send custom filename (with extension) or type `/skip`:",
                timeout=60,
            )
            fname = ask_name.text.strip() if ask_name.text and ask_name.text.lower() != "/skip" else None

            ask_thumb = await message.chat.ask(
                "📸 Send a thumbnail image or type `/skip`:",
                timeout=60,
            )
            if ask_thumb.photo:
                thumb_path = await ask_thumb.download(
                    file_name=os.path.join(temp_dir, f"thumb_{message.from_user.id}.jpg")
                )
        except ListenerTimeout:
            await status.edit("⌛ Timeout. Cancelled.")
            return

        # Determine filename
        filename = fname or os.path.basename(urlparse(direct_url).path)
        if not filename or filename == "" or "." not in filename:
            # Extract extension from URL or default to mp4
            ext = os.path.splitext(urlparse(direct_url).path)[1] or ".mp4"
            filename = f"downloaded_file{ext}"
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        file_path = os.path.join(temp_dir, filename)

        await status.edit(f"⬇️ Downloading **{filename}**...")

        # Download file
        downloaded = 0
        last_update = time.time()
        
        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(direct_url, allow_redirects=True) as r:
                if r.status != 200:
                    await status.edit(f"❌ Failed to download: HTTP {r.status}")
                    return
                
                with open(file_path, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024 * 1024):  # 1MB chunks
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Update progress every 2 seconds
                        if time.time() - last_update > 2:
                            try:
                                progress_text = f"⬇️ Downloaded: {humanbytes(downloaded)}"
                                if total_size > 0:
                                    percent = (downloaded / total_size) * 100
                                    progress_text += f" / {humanbytes(total_size)} ({percent:.1f}%)"
                                await status.edit(progress_text)
                            except MessageNotModified:
                                pass
                            last_update = time.time()

        await status.edit("📤 Uploading to Telegram...")

        # Progress callback for upload
        async def progress(cur, tot):
            if tot and tot > 0:
                percent = cur / tot * 100
                try:
                    await status.edit(f"📤 Uploading... {percent:.1f}%\n{humanbytes(cur)} / {humanbytes(tot)}")
                except MessageNotModified:
                    pass

        # Determine MIME type and send
        mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        
        if mime.startswith("video"):
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_path,
                caption=f"**{filename}**\n\n📦 Size: {humanbytes(os.path.getsize(file_path))}",
                progress=progress,
            )
        else:
            await client.send_document(
                chat_id=message.chat.id,
                document=file_path,
                thumb=thumb_path,
                caption=f"**{filename}**\n\n📦 Size: {humanbytes(os.path.getsize(file_path))}",
                progress=progress,
            )

        await status.delete()

    except Exception as e:
        error_msg = str(e)
        await status.edit(f"❌ Error: `{error_msg[:200]}`")
        print(f"[jl_downloader] Exception: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Cleanup
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        if thumb_path and os.path.exists(thumb_path) and not thumb_path.startswith(temp_dir):
            try:
                os.remove(thumb_path)
            except:
                pass

# ---------------------------
# Command Handler
# ---------------------------

@StreamBot.on_message(filters.command(["jl_downloader", "jl"]) & filters.private)
async def jl_handler(client: Client, m: Message):
    """Handler for /jl_downloader and /jl commands"""
    
    if len(m.command) == 1:
        await m.reply_text(
            "**JL Downloader Usage:**\n\n"
            "`/jl <page_url>`\n\n"
            "**Example:**\n"
            "`/jl https://example.com/video123`\n\n"
            "**Supported:** Pages with embedded .mp4, .mkv, .avi, .mov, etc."
        )
        return

    url = m.command[1].strip()
    
    if not url.startswith("http"):
        await m.reply_text("❌ Please send a valid URL starting with http:// or https://")
        return

    await process_jl_task(client, m, url)
