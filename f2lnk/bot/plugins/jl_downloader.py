# f2lnk/bot/plugins/jl_downloader.py
# /jl — Universal Media Downloader powered by yt-dlp
# Supports 1000+ sites: YouTube, Instagram, Facebook, TikTok, Vimeo, etc.

import os
import re
import time
import shutil
import asyncio
import logging

from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import MessageNotModified
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.utils.split_upload import upload_file_or_split

logger = logging.getLogger(__name__)
db = Database(Var.DATABASE_URL, Var.name)

DOWNLOAD_ROOT = "./jl_downloads"
os.makedirs(DOWNLOAD_ROOT, exist_ok=True)


async def _ytdlp_info(url: str) -> dict:
    """Get media info from yt-dlp without downloading."""
    cmd = [
        "yt-dlp", "--no-download", "--print-json",
        "--no-warnings", "--no-playlist",
        "-f", "best",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise Exception(f"yt-dlp info failed: {err.splitlines()[-1] if err else 'Unknown error'}")

    import json
    return json.loads(stdout.decode("utf-8", errors="replace"))


async def _ytdlp_download(url: str, output_path: str, status_msg: Message) -> str:
    """Download media using yt-dlp with progress updates."""
    output_template = os.path.join(output_path, "%(title).100s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-warnings", "--no-playlist",
        "-f", "best[filesize<1.95G]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--newline",  # for progress parsing
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    last_update = time.time()
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()

        # Parse progress: [download]  45.2% of 120.50MiB at 5.21MiB/s ETA 00:13
        if "[download]" in text and "%" in text:
            now = time.time()
            if now - last_update > 3:
                try:
                    await status_msg.edit_text(f"⬇️ **Downloading...**\n`{text}`")
                except (MessageNotModified, Exception):
                    pass
                last_update = now

        # Merge progress
        if "[Merger]" in text or "[ffmpeg]" in text:
            now = time.time()
            if now - last_update > 3:
                try:
                    await status_msg.edit_text("🔄 **Merging audio & video...**")
                except (MessageNotModified, Exception):
                    pass
                last_update = now

    await proc.wait()
    if proc.returncode != 0:
        stderr = await proc.stderr.read()
        err = stderr.decode("utf-8", errors="replace").strip()
        raise Exception(f"Download failed: {err.splitlines()[-1] if err else 'Unknown error'}")

    # Find downloaded file(s)
    files = []
    for f in os.listdir(output_path):
        fp = os.path.join(output_path, f)
        if os.path.isfile(fp) and os.path.getsize(fp) > 0:
            files.append(fp)
    return files


async def process_jl_task(client: Client, message: Message, page_url: str):
    """Core download + upload logic."""
    status_msg = None
    temp_dir = os.path.join(DOWNLOAD_ROOT, f"{message.from_user.id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        status_msg = await message.reply_text("🔍 **Checking URL...**", quote=True)

        # Try yt-dlp info first
        try:
            info = await _ytdlp_info(page_url)
            title = info.get("title", "video")
            ext = info.get("ext", "mp4")
            duration = info.get("duration", 0)
            filesize = info.get("filesize") or info.get("filesize_approx") or 0
            uploader = info.get("uploader", "Unknown")
            site = info.get("extractor", "Unknown")

            dur_str = ""
            if duration:
                m, s = divmod(int(duration), 60)
                h, m = divmod(m, 60)
                dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

            info_text = (
                f"✅ **Media found!**\n\n"
                f"📁 **Title:** `{title[:80]}`\n"
                f"🌐 **Site:** {site}\n"
                f"👤 **Uploader:** {uploader}\n"
            )
            if dur_str:
                info_text += f"⏱ **Duration:** {dur_str}\n"
            if filesize:
                info_text += f"📦 **Size:** ~{humanbytes(filesize)}\n"

            await status_msg.edit_text(info_text)

        except Exception as e:
            logger.warning("yt-dlp info failed for %s: %s", page_url, e)
            title = "download"
            ext = "mp4"
            await status_msg.edit_text(
                "⚠️ Could not get media info, but will try downloading anyway..."
            )

        # Ask for custom filename
        try:
            default_name = re.sub(r'[\\/*?:"<>|]', '', title)[:80] + f".{ext}"
            ask_name = await message.chat.ask(
                f"📝 Send filename or **/skip** for default:\n`{default_name}`",
                timeout=60,
            )
            if ask_name.text and ask_name.text.strip().lower() != "/skip":
                custom_name = ask_name.text.strip()
            else:
                custom_name = None
        except ListenerTimeout:
            custom_name = None

        filename = custom_name or default_name

        # Download
        await status_msg.edit_text("⬇️ **Starting download...**")
        downloaded_files = await _ytdlp_download(page_url, temp_dir, status_msg)

        if not downloaded_files:
            await status_msg.edit_text("❌ Download finished but no files found.")
            return

        # Rename if custom name
        if custom_name and len(downloaded_files) == 1:
            old_path = downloaded_files[0]
            old_ext = os.path.splitext(old_path)[1]
            if not os.path.splitext(custom_name)[1]:
                custom_name += old_ext
            new_path = os.path.join(temp_dir, custom_name)
            os.rename(old_path, new_path)
            downloaded_files = [new_path]
            filename = custom_name

        # Upload all files
        total = len(downloaded_files)
        for i, fp in enumerate(downloaded_files):
            fname = os.path.basename(fp)
            fsize = os.path.getsize(fp)

            await status_msg.edit_text(
                f"⬆️ **Uploading** {i + 1}/{total}: `{fname}`\n"
                f"📦 Size: `{humanbytes(fsize)}`"
            )

            # Get footer
            try:
                user_info = await db.get_user_info(message.from_user.id)
                footer = user_info.get("footer", "")
            except Exception:
                footer = ""

            caption = f"**{fname}**"
            if footer:
                caption += f"\n\n{footer}"

            try:
                await upload_file_or_split(
                    client, message.chat.id, fp,
                    caption=caption, file_name=fname,
                    reply_to=message.id,
                )
            except Exception as e:
                await message.reply_text(f"❌ Upload failed for `{fname}`: `{e}`")

        await status_msg.delete()
        logger.info("JL task complete for user %s: %s", message.from_user.id, page_url)

    except Exception as e:
        logger.error("JL error: %s", e, exc_info=True)
        if status_msg:
            try:
                await status_msg.edit_text(f"❌ Error: `{e}`")
            except Exception:
                pass

    finally:
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


@StreamBot.on_message(filters.command(["jl_downloader", "jl"]) & filters.private)
async def jl_handler(client: Client, m: Message):
    # Track activity for watchdog
    try:
        from f2lnk.bot.plugins.restart import touch_activity
        touch_activity()
    except Exception:
        pass

    if len(m.command) < 2 and not m.reply_to_message:
        await m.reply_text(
            "🎬 **Universal Media Downloader**\n\n"
            "**Usage:** `/jl <URL>`\n\n"
            "**Supported sites (1000+):**\n"
            "YouTube, Instagram, Facebook, TikTok,\n"
            "Twitter/X, Vimeo, Dailymotion, Reddit,\n"
            "SoundCloud, Twitch, Bilibili, and many more!\n\n"
            "Powered by yt-dlp 🚀",
        )
        return

    url = m.command[1].strip() if len(m.command) > 1 else m.reply_to_message.text.strip()
    if not url.startswith("http"):
        await m.reply_text("❌ Please send a valid URL.")
        return

    await process_jl_task(client, m, url)
            
