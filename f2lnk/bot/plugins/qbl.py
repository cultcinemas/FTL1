# f2lnk/bot/plugins/qbl.py
# /qbl — qBittorrent Leech: download torrents and upload to Telegram

import os
import time
import shutil
import asyncio
import logging

from pyrogram import filters, Client
from pyrogram.types import Message

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.utils.human_readable import humanbytes
from f2lnk.utils.split_upload import upload_file_or_split

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "./qbl_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Active qBL tasks per user
_active_qbl = {}


def _get_qb_client():
    """Connect to qBittorrent WebUI."""
    import qbittorrentapi
    qb = qbittorrentapi.Client(
        host=Var.QB_HOST,
        port=Var.QB_PORT,
        username=Var.QB_USER,
        password=Var.QB_PASS,
    )
    qb.auth_log_in()
    return qb


@StreamBot.on_message(filters.command("qbl") & filters.private)
async def qbl_command(client: Client, m: Message):
    """
    /qbl <magnet_link>
    OR reply to a .torrent file with /qbl
    """
    user_id = m.from_user.id

    # Track activity for watchdog
    try:
        from f2lnk.bot.plugins.restart import touch_activity
        touch_activity()
    except Exception:
        pass

    # Check if user already has an active qbl task
    if user_id in _active_qbl:
        await m.reply_text("❌ You already have an active torrent download. Wait or /cancel.", quote=True)
        return

    magnet_link = None
    torrent_file_path = None
    torrent_url = None

    # Gather input text from command args or replied message
    input_text = ""
    if len(m.command) > 1:
        input_text = " ".join(m.command[1:]).strip()
    elif m.reply_to_message:
        # Reply to .torrent file
        if m.reply_to_message.document:
            doc = m.reply_to_message.document
            if doc.file_name and doc.file_name.lower().endswith(".torrent"):
                work_dir = os.path.join(DOWNLOAD_DIR, f"torrent_{user_id}_{int(time.time())}")
                os.makedirs(work_dir, exist_ok=True)
                torrent_file_path = await client.download_media(
                    m.reply_to_message,
                    file_name=os.path.join(work_dir, doc.file_name),
                )
            else:
                await m.reply_text("❌ Please reply to a `.torrent` file.", quote=True)
                return
        # Reply to text message containing magnet/URL
        elif m.reply_to_message.text:
            input_text = m.reply_to_message.text.strip()

    # Parse input_text (from command args or reply text)
    if input_text and not torrent_file_path:
        if input_text.startswith("magnet:"):
            magnet_link = input_text
        elif input_text.startswith(("http://", "https://")):
            torrent_url = input_text
        else:
            await m.reply_text("❌ Invalid input. Send a `magnet:` link or a torrent URL.", quote=True)
            return

    # Nothing provided
    if not magnet_link and not torrent_file_path and not torrent_url:
        await m.reply_text(
            "**Usage:**\n"
            "`/qbl <magnet_link>`\n"
            "`/qbl <torrent_URL>`\n"
            "Or reply to a `.torrent` file / magnet link with `/qbl`",
            quote=True,
        )
        return

    status_msg = await m.reply_text("⏳ Connecting to qBittorrent...", quote=True)
    _active_qbl[user_id] = True

    try:
        # If torrent_url, download the .torrent file first
        if torrent_url:
            import aiohttp
            work_dir = os.path.join(DOWNLOAD_DIR, f"torrent_{user_id}_{int(time.time())}")
            os.makedirs(work_dir, exist_ok=True)
            await status_msg.edit_text("📥 Downloading .torrent file...")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(torrent_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            await status_msg.edit_text(f"❌ Failed to download torrent: HTTP {resp.status}")
                            return
                        # Determine filename
                        fname = "download.torrent"
                        if "Content-Disposition" in resp.headers:
                            import re
                            cd = resp.headers["Content-Disposition"]
                            match = re.findall(r'filename[^;=\n]*=([\'"]?)(.+?)\1(;|$)', cd)
                            if match:
                                fname = match[0][1]
                        if not fname.lower().endswith(".torrent"):
                            fname += ".torrent"
                        torrent_file_path = os.path.join(work_dir, fname)
                        with open(torrent_file_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(1024 * 1024):
                                f.write(chunk)
            except Exception as e:
                await status_msg.edit_text(f"❌ Failed to download torrent file: `{e}`")
                return
        # Connect to qBittorrent
        try:
            qb = _get_qb_client()
        except Exception as e:
            await status_msg.edit_text(f"❌ Failed to connect to qBittorrent: `{e}`")
            return

        # Set download path
        save_path = os.path.join(DOWNLOAD_DIR, f"qbl_{user_id}_{int(time.time())}")
        os.makedirs(save_path, exist_ok=True)

        # Add torrent
        await status_msg.edit_text("📥 Adding torrent...")
        try:
            if magnet_link:
                qb.torrents_add(urls=magnet_link, save_path=save_path)
            elif torrent_file_path:
                with open(torrent_file_path, "rb") as f:
                    qb.torrents_add(torrent_files=f, save_path=save_path)
        except Exception as e:
            await status_msg.edit_text(f"❌ Failed to add torrent: `{e}`")
            return

        # Wait a moment for torrent to appear
        await asyncio.sleep(3)

        # Find the torrent we just added
        torrents = qb.torrents_info(sort="added_on", reverse=True, limit=5)
        if not torrents:
            await status_msg.edit_text("❌ Torrent not found after adding.")
            return

        torrent = torrents[0]
        torrent_hash = torrent.hash
        torrent_name = torrent.name

        await status_msg.edit_text(
            f"📥 **Downloading:**\n"
            f"📁 `{torrent_name}`\n"
            f"⏳ Starting..."
        )

        # Poll download progress
        last_update = 0
        while True:
            await asyncio.sleep(5)

            info = qb.torrents_info(torrent_hashes=torrent_hash)
            if not info:
                await status_msg.edit_text("❌ Torrent disappeared from qBittorrent.")
                return

            t = info[0]
            progress = t.progress * 100
            dl_speed = t.dlspeed
            size = t.total_size
            downloaded = t.downloaded
            eta = t.eta
            state = t.state

            # Check if done
            if progress >= 100 or state in ("uploading", "pausedUP", "stalledUP", "queuedUP"):
                break

            # Check for errors
            if state in ("error", "missingFiles"):
                await status_msg.edit_text(f"❌ Torrent error: `{state}`")
                qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
                return

            # Update status every 5 seconds
            now = time.time()
            if now - last_update > 5:
                eta_str = f"{eta // 3600}h {(eta % 3600) // 60}m" if eta > 0 and eta < 864000 else "calculating..."
                try:
                    await status_msg.edit_text(
                        f"📥 **Downloading:**\n"
                        f"📁 `{torrent_name}`\n"
                        f"📊 {progress:.1f}% — `{humanbytes(downloaded)}` / `{humanbytes(size)}`\n"
                        f"⚡ Speed: `{humanbytes(dl_speed)}/s`\n"
                        f"⏱ ETA: {eta_str}"
                    )
                except Exception:
                    pass
                last_update = now

        await status_msg.edit_text(
            f"✅ **Download complete!**\n"
            f"📁 `{torrent_name}`\n"
            f"📦 Size: `{humanbytes(t.total_size)}`\n"
            f"⬆️ Uploading to Telegram..."
        )

        # Find downloaded files using save_path + torrent name
        content_path = os.path.join(save_path, torrent_name)

        files_to_upload = []
        if os.path.isfile(content_path):
            files_to_upload.append(content_path)
        elif os.path.isdir(content_path):
            for root, dirs, files in os.walk(content_path):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    if os.path.getsize(fp) > 0:
                        files_to_upload.append(fp)

        # Fallback: use torrents_files API if nothing found
        if not files_to_upload:
            try:
                torrent_files = qb.torrents_files(torrent_hash)
                for tf in torrent_files:
                    fp = os.path.join(save_path, tf.name)
                    if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                        files_to_upload.append(fp)
            except Exception:
                pass

        # Final fallback: walk save_path
        if not files_to_upload:
            for root, dirs, files in os.walk(save_path):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    if os.path.getsize(fp) > 0 and not f.endswith(".torrent"):
                        files_to_upload.append(fp)

        if not files_to_upload:
            await status_msg.edit_text("❌ No files found after download.")
            qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
            return

        # Upload each file (auto-split if >1.95GB)
        total_files = len(files_to_upload)
        for i, fp in enumerate(files_to_upload):
            fname = os.path.basename(fp)
            fsize = os.path.getsize(fp)
            caption = (
                f"📦 **qBittorrent Leech**\n"
                f"📁 `{fname}`\n"
                f"📦 Size: `{humanbytes(fsize)}`"
            )
            if total_files > 1:
                caption += f"\n📂 File {i + 1}/{total_files}"

            try:
                await status_msg.edit_text(f"⬆️ Uploading {i + 1}/{total_files}: `{fname}`...")
            except Exception:
                pass

            try:
                await upload_file_or_split(
                    client, m.chat.id, fp,
                    caption=caption, file_name=fname,
                )
            except Exception as e:
                await m.reply_text(f"❌ Upload failed for `{fname}`: `{e}`")

        # Done
        await status_msg.edit_text(
            f"🎉 **Torrent leech complete!**\n"
            f"📁 `{torrent_name}`\n"
            f"📂 {total_files} file(s) uploaded"
        )

        # Delete torrent from qBittorrent
        try:
            qb.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
        except Exception:
            pass

    except Exception as e:
        logger.error("qBL error: %s", e, exc_info=True)
        try:
            await status_msg.edit_text(f"❌ Error: `{e}`")
        except Exception:
            pass

    finally:
        _active_qbl.pop(user_id, None)
        # Clean up save directory
        if 'save_path' in dir() and save_path and os.path.exists(save_path):
            shutil.rmtree(save_path, ignore_errors=True)
        if torrent_file_path:
            try:
                shutil.rmtree(os.path.dirname(torrent_file_path), ignore_errors=True)
            except Exception:
                pass
            
