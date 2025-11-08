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
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from typing import Optional

from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified
from pyromod.exceptions import ListenerTimeout

# Import your bot instance - adjust the import path as needed
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

def extract_media_url(page_url: str) -> Optional[str]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = requests.get(page_url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None

        html = r.text

        # <video src="...">
        m = re.search(r'<video[^>]+src=["\']([^"\']+\.mp4)[^"\']*["\']', html, re.I)
        if m:
            return m.group(1)

        # "file": "https://...mp4"
        m = re.search(r'"file"\s*:\s*"([^"]+\.mp4[^"]*)"', html)
        if m:
            return m.group(1)

        # source: "https://...mp4"
        m = re.search(r'source\s*:\s*"([^"]+\.mp4[^"]*)"', html)
        if m:
            return m.group(1)

        # Any direct .mp4 link
        m = re.search(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', html)
        if m:
            return m.group(0)

        return None
    except Exception as e:
        print(f"[extract_media_url] {e}")
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
        direct_url = extract_media_url(page_url)
        if not direct_url:
            await status.edit("❌ No direct video/file link found on that page.")
            return

        await status.edit(f"✅ Found link:\n`{direct_url[:90]}...`\n\n⏳ Checking file size...")

        # HEAD request for size
        total_size = 0
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.head(direct_url, allow_redirects=True) as r:
                total_size = int(r.headers.get("Content-Length", 0))

        if total_size > 1.95 * 1024**3:
            await status.edit(f"⚠️ File too large ({humanbytes(total_size)}). Telegram max ≈ 2 GB.")
            return

        # Ask filename and thumbnail
        try:
            ask_name = await message.chat.ask(
                "✏️ Send custom filename (with extension) or `/skip`.",
                timeout=60,
            )
            fname = ask_name.text.strip() if ask_name.text and ask_name.text.lower() != "/skip" else None

            ask_thumb = await message.chat.ask(
                "📸 Send a thumbnail image or `/skip`.",
                timeout=60,
            )
            if ask_thumb.photo:
                thumb_path = await ask_thumb.download(
                    file_name=os.path.join(temp_dir, f"thumb_{message.from_user.id}.jpg")
                )
        except ListenerTimeout:
            await status.edit("⌛ Timeout. Cancelled.")
            return

        filename = fname or os.path.basename(urlparse(direct_url).path)
        if not filename or filename == "":
            filename = "downloaded_file.mp4"
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        file_path = os.path.join(temp_dir, filename)

        await status.edit(f"⬇️ Downloading **{filename}** ...")

        downloaded = 0
        last_update = time.time()
        async with aiohttp.ClientSession() as sess:
            async with sess.get(direct_url) as r:
                with open(file_path, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if time.time() - last_update > 2:
                            try:
                                await status.edit(
                                    f"⬇️ {humanbytes(downloaded)} / {humanbytes(total_size or downloaded)}"
                                )
                            except MessageNotModified:
                                pass
                            last_update = time.time()

        # Upload to Telegram
        await status.edit("📤 Uploading to Telegram...")

        async def progress(cur, tot):
            if tot and tot > 0:
                percent = cur / tot * 100
                try:
                    await status.edit(f"📤 Uploading... {percent:.1f}%")
                except MessageNotModified:
                    pass

        mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        if mime.startswith("video"):
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_path,
                caption=f"**{filename}**",
                progress=progress,
            )
        else:
            await client.send_document(
                chat_id=message.chat.id,
                document=file_path,
                caption=f"**{filename}**",
                progress=progress,
            )

        await status.delete()

    except Exception as e:
        await status.edit(f"❌ Error: `{e}`")
        print(f"[jl_downloader] {e}")
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        if thumb_path and os.path.exists(thumb_path) and not thumb_path.startswith(temp_dir):
            os.remove(thumb_path)

# ---------------------------
# Command
# ---------------------------

@StreamBot.on_message(filters.command(["jl_downloader", "jl"]))
async def jl_handler(client: Client, m: Message):
    if len(m.command) == 1:
        await m.reply_text(
            "Usage:\n`/jl_downloader <page_url>`\n\nExample:\n`/jl https://example.com/video123`"
        )
        return

    url = m.command[1].strip()
    if not url.startswith("http"):
        await m.reply_text("❌ Please send a valid URL.")
        return

    await process_jl_task(client, m, url)
