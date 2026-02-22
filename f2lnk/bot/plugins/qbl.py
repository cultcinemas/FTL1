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

    # Case 1: magnet link in command
    if len(m.command) > 1:
        magnet_link = " ".join(m.command[1:]).strip()
        if not magnet_link.startswith("magnet:"):
            await m.reply_text("❌ Invalid magnet link. Must start with `magnet:`", quote=True)
            return

    # Case 2: reply to .torrent file
    elif m.reply_to_message and m.reply_to_message.document:
        doc = m.reply_to_message.document
        if doc.file_name and doc.file_name.endswith(".torrent"):
            work_dir = os.path.join(DOWNLOAD_DIR, f"torrent_{user_id}_{int(time.time())}")
            os.makedirs(work_dir, exist_ok=True)
            torrent_file_path = await client.download_media(
                m.reply_to_message,
                file_name=os.path.join(work_dir, doc.file_name),
            )
        else:
            await m.reply_text("❌ Please reply to a `.torrent` file.", quote=True)
            return
    else:
        await m.reply_text(
            "**Usage:**\n"
            "`/qbl <magnet_link>`\n"
            "Or reply to a `.torrent` file with `/qbl`",
            quote=True,
        )
        return

    status_msg = await m.reply_text("⏳ Connecting to qBittorrent...", quote=True)
    _active_qbl[user_id] = True

    try:
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

        # Find downloaded files
        content_path = t.content_path
        if not content_path or not os.path.exists(content_path):
            # Fallback: search save_path
            content_path = save_path

        files_to_upload = []
        if os.path.isfile(content_path):
            files_to_upload.append(content_path)
        elif os.path.isdir(content_path):
            for root, dirs, files in os.walk(content_path):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    if os.path.getsize(fp) > 0:
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
