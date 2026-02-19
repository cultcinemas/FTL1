# f2lnk/bot/plugins/mediainfo_cmd.py

import os
import asyncio
import aiohttp
import time
import shutil

from pyrogram import filters, Client
from pyrogram.types import Message

from f2lnk.bot import StreamBot


TEMP_DIR = "./mediainfo_temp"


def _work_dir(user_id: int) -> str:
    d = os.path.join(TEMP_DIR, f"{user_id}_{int(time.time())}")
    os.makedirs(d, exist_ok=True)
    return d


async def _run_mediainfo(file_path: str) -> str:
    """Run the mediainfo CLI on a file and return the output text."""
    proc = await asyncio.create_subprocess_exec(
        "mediainfo", file_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        return f"Error: {stderr.decode('utf-8', errors='replace')}"
    return stdout.decode("utf-8", errors="replace")


async def _download_url_partial(url: str, dest_dir: str, max_bytes: int = 50 * 1024 * 1024) -> str:
    """Download up to max_bytes from URL (enough for mediainfo header analysis)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                return None
            # Try to get filename from headers or URL
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                fname = cd.split("filename=")[-1].strip('" ')
            else:
                fname = os.path.basename(url.split("?")[0]) or f"file_{int(time.time())}"
            
            fpath = os.path.join(dest_dir, fname)
            downloaded = 0
            with open(fpath, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded >= max_bytes:
                        break
            return fpath


@StreamBot.on_message(filters.command("mediainfo") & filters.private)
async def mediainfo_cmd(client: Client, m: Message):
    """
    /mediainfo — Reply to a file or send a URL to get its media metadata.
    """
    work_dir = _work_dir(m.from_user.id)
    status = await m.reply_text("⏳ Processing...", quote=True)
    file_path = None

    try:
        # Case 1: Reply to a file
        if m.reply_to_message and (
            m.reply_to_message.video
            or m.reply_to_message.document
            or m.reply_to_message.audio
            or m.reply_to_message.photo
        ):
            await status.edit_text("⬇️ Downloading file for analysis...")
            file_path = await client.download_media(
                m.reply_to_message,
                file_name=os.path.join(work_dir, ""),
            )

        # Case 2: URL in command args
        elif len(m.command) > 1:
            url = m.command[1]
            if not url.startswith(("http://", "https://")):
                await status.edit_text("❌ Invalid URL. Please provide a valid HTTP/HTTPS link.")
                return
            await status.edit_text("⬇️ Downloading from URL for analysis...")
            file_path = await _download_url_partial(url, work_dir)

        # Case 3: Reply to a text message with a URL
        elif m.reply_to_message and m.reply_to_message.text:
            url = m.reply_to_message.text.strip()
            if not url.startswith(("http://", "https://")):
                await status.edit_text("❌ Replied message is not a valid URL. Please reply to a file or provide a URL.")
                return
            await status.edit_text("⬇️ Downloading from URL for analysis...")
            file_path = await _download_url_partial(url, work_dir)

        else:
            await status.edit_text(
                "<b>Usage:</b>\n\n"
                "• <code>/mediainfo</code> — Reply to a file\n"
                "• <code>/mediainfo &lt;url&gt;</code> — Provide a direct URL"
            )
            return

        if not file_path or not os.path.exists(file_path):
            await status.edit_text("❌ Failed to download the file.")
            return

        await status.edit_text("🔍 Running mediainfo...")
        info_text = await _run_mediainfo(file_path)

        if not info_text.strip():
            await status.edit_text("❌ mediainfo returned empty output for this file.")
            return

        # If output is short, send as message; otherwise as a file
        if len(info_text) <= 4000:
            await status.edit_text(f"<b>📋 MediaInfo</b>\n\n<pre>{info_text}</pre>")
        else:
            txt_path = os.path.join(work_dir, "mediainfo_output.txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(info_text)
            await client.send_document(
                m.chat.id,
                txt_path,
                caption="📋 **MediaInfo Output**",
                reply_to_message_id=m.id,
                file_name="mediainfo_output.txt",
            )
            await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")

    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
