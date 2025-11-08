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
# Extractor – finds .mp4 or file links in HTML/JS
# ---------------------------

async def extract_media_url(page_url: str) -> Optional[str]:
    """
    Async function to extract media URL from a webpage.
    Uses proper headers like the working xe.py code.
    """
    try:
        # Proper headers like your working code
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        # Get referer from URL
        parsed = urlparse(page_url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"
        headers["Referer"] = referer
        
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(page_url, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    print(f"[extract_media_url] HTTP {response.status} for {page_url}")
                    return None
                
                html = await response.text()
        
        print(f"[extract_media_url] Fetched {len(html)} bytes from {page_url}")
        
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
        
        # Pattern 4: "src": "https://...mp4"
        m = re.search(r'"src"\s*:\s*"([^"]+\.mp4[^"]*)"', html)
        if m:
            url = m.group(1)
            print(f"[extract_media_url] Found via 'src': {url}")
            return url

        # Pattern 5: Any direct .mp4 link in HTML
        m = re.search(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', html)
        if m:
            url = m.group(0)
            print(f"[extract_media_url] Found direct .mp4 link: {url}")
            return url
        
        # Pattern 6: Look for other video formats
        m = re.search(r'https?://[^\s"\'<>]+\.(mkv|avi|mov|wmv|flv|webm|m3u8)[^\s"\'<>]*', html, re.I)
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
        import traceback
        traceback.print_exc()
        return None

# ---------------------------
# Core download + upload logic (based on xe.py pattern)
# ---------------------------

async def process_jl_task(client: Client, message: Message, page_url: str):
    status_msg = None
    temp_dir = os.path.join(DOWNLOAD_ROOT, f"{message.from_user.id}_{int(time.time())}")
    thumb_path = None
    file_path = None

    try:
        # ---------- 1. Check if it's already a direct video URL ----------
        parsed_url = urlparse(page_url)
        file_ext = os.path.splitext(parsed_url.path)[1].lower()
        
        # If URL ends with video extension, treat as direct link
        if file_ext in ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m3u8']:
            print(f"[DEBUG] Direct video URL detected: {page_url}")
            status_msg = await message.reply_text("**🎥 Direct video link detected!**", quote=True)
            direct_url = page_url
        else:
            # ---------- Extract from webpage ----------
            status_msg = await message.reply_text("**🔍 Extracting video URL...**", quote=True)
            
            direct_url = await extract_media_url(page_url)
            
            if not direct_url:
                await status_msg.edit(
                    "**❌ Failed to extract video URL.**\n\n"
                    "**Possible reasons:**\n"
                    "• No direct video link found on page\n"
                    "• Site requires login/cookies\n"
                    "• Video is DRM protected\n"
                    "• JavaScript-rendered content\n\n"
                    "**💡 Tip:** Try using a direct video URL instead\n"
                    "Example: `/jl https://example.com/video.mp4`"
                )
                return

        # Make URL absolute if relative
        if direct_url.startswith("//"):
            direct_url = "https:" + direct_url
        elif direct_url.startswith("/"):
            parsed = urlparse(page_url)
            direct_url = f"{parsed.scheme}://{parsed.netloc}{direct_url}"

        # Log the full URL for debugging
        print(f"[DEBUG] Extracted URL: {direct_url}")
        
        await status_msg.edit(f"**✅ Found video!**\n`{direct_url[:70]}...`\n\n**⏳ Checking size...**")

        # ---------- 2. HEAD request – get size ----------
        total_size = 0
        
        # Use proper headers for HEAD request too
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": page_url,
            "Accept": "*/*",
        }
        
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
                async with sess.head(direct_url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        print(f"[HEAD] HTTP {resp.status} for {direct_url}")
                        # Don't fail, just continue without size
                    else:
                        total_size = int(resp.headers.get("Content-Length", 0))
        except Exception as e:
            print(f"[HEAD request error] {e}")
            # Continue anyway

        if total_size == 0:
            await status_msg.edit("**⚠️ Could not determine file size – will download anyway.**")
        else:
            # Telegram limit check
            if total_size > 1.95 * 1024**3:
                await status_msg.edit(
                    f"**❌ File too large:** `{humanbytes(total_size)}`\n"
                    "Telegram only allows **≤ 1.95 GB**."
                )
                return

        size_info = humanbytes(total_size) if total_size > 0 else "Unknown size"
        await status_msg.edit(f"**📊 File size:** `{size_info}`\n\n**⏳ Waiting for filename...**")

        # ---------- 3. Ask for filename & thumbnail ----------
        try:
            ask_name = await message.chat.ask(
                f"**🔗 URL:** `{page_url}`\n"
                f"**📦 Size:** `{size_info}`\n\n"
                "Send **filename** (with extension) or `/skip` to use original:",
                timeout=90
            )
            custom_name = ask_name.text.strip() if ask_name.text and ask_name.text.lower() != "/skip" else None

            ask_thumb = await message.chat.ask(
                "**📸 Send a thumbnail** (photo) or `/skip` for none:",
                timeout=60
            )
            if ask_thumb.photo:
                thumb_path = await ask_thumb.download(
                    file_name=os.path.join(temp_dir, f"thumb_{message.from_user.id}.jpg")
                )
        except ListenerTimeout:
            await status_msg.edit("**⌛ Timeout – aborting.**")
            return

        # ---------- 4. Download ----------
        os.makedirs(temp_dir, exist_ok=True)
        
        # Determine filename
        filename = custom_name or (os.path.basename(urlparse(direct_url).path) or "video.mp4")
        if not filename or filename == "" or "." not in filename:
            ext = os.path.splitext(urlparse(direct_url).path)[1] or ".mp4"
            filename = f"downloaded_file{ext}"
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        file_path = os.path.join(temp_dir, filename)

        await status_msg.edit("**⬇️ Starting download...**")

        downloaded = 0
        last_update = time.time()

        # Download with proper headers (like xe.py)
        download_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": page_url,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
        }

        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout, headers=download_headers) as sess:
            async with sess.get(direct_url, allow_redirects=True) as resp:
                print(f"[DEBUG] Download response status: {resp.status}")
                print(f"[DEBUG] Response headers: {dict(resp.headers)}")
                
                if resp.status != 200:
                    error_detail = f"**❌ Download failed – HTTP {resp.status}**\n\n"
                    error_detail += f"**URL tried:** `{direct_url}`\n\n"
                    error_detail += "**Possible issues:**\n"
                    error_detail += "• URL requires authentication\n"
                    error_detail += "• URL has expired\n"
                    error_detail += "• Site blocks automated downloads\n"
                    error_detail += "• Try sending a direct video URL instead"
                    await status_msg.edit(error_detail)
                    return
                
                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1 MiB chunks
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        now = time.time()
                        if now - last_update > 2:
                            try:
                                progress_text = f"**⬇️ Downloading...**\n`{filename}`\n"
                                progress_text += f"`{humanbytes(downloaded)}`"
                                if total_size > 0:
                                    percent = (downloaded / total_size) * 100
                                    progress_text += f" / `{humanbytes(total_size)}` ({percent:.1f}%)"
                                await status_msg.edit(progress_text)
                            except MessageNotModified:
                                pass
                            last_update = now
                        
                        await asyncio.sleep(0)  # Allow cancellation

        # ---------- 5. Upload to Telegram ----------
        await status_msg.edit("**📤 Download complete! Uploading to Telegram...**")
        last_update = time.time()

        async def up_progress(cur, tot):
            nonlocal last_update
            now = time.time()
            if now - last_update > 2:
                try:
                    percent = (cur / tot) * 100 if tot > 0 else 0
                    await status_msg.edit(
                        f"**📤 Uploading...**\n"
                        f"`{filename}`\n"
                        f"`{humanbytes(cur)}` / `{humanbytes(tot)}` ({percent:.1f}%)"
                    )
                except MessageNotModified:
                    pass
                last_update = now
            await asyncio.sleep(0)

        # Detect MIME type and send
        mime = mimetypes.guess_type(file_path)[0] or "video/mp4"
        file_size = os.path.getsize(file_path)
        
        if mime.startswith("video"):
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_path,
                caption=f"**📹 {filename}**\n\n**📦 Size:** `{humanbytes(file_size)}`",
                progress=up_progress
            )
        else:
            await client.send_document(
                chat_id=message.chat.id,
                document=file_path,
                thumb=thumb_path,
                caption=f"**📄 {filename}**\n\n**📦 Size:** `{humanbytes(file_size)}`",
                progress=up_progress
            )

        await status_msg.delete()

    except asyncio.CancelledError:
        if status_msg:
            await status_msg.edit("**❌ Task cancelled by user.**")
    except Exception as e:
        error_msg = str(e)
        if status_msg:
            await status_msg.edit(f"**❌ Error:** `{error_msg[:200]}`")
        print(f"[jl_downloader] Exception: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Cleanup
        if os.path.isdir(temp_dir):
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
            "**🎬 JL Downloader Usage:**\n\n"
            "`/jl <page_url>`\n\n"
            "**Example:**\n"
            "`/jl https://example.com/video123`\n\n"
            "**✅ Supported:**\n"
            "• Pages with embedded videos (.mp4, .mkv, etc.)\n"
            "• Direct video links\n"
            "• Most video hosting sites\n\n"
            "**⚠️ Max file size:** 1.95 GB (Telegram limit)"
        )
        return

    url = m.command[1].strip()
    
    if not url.startswith("http"):
        await m.reply_text("**❌ Please send a valid URL** starting with http:// or https://")
        return

    await process_jl_task(client, m, url)
