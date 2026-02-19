# f2lnk/bot/plugins/zip_cmd.py

import os
import asyncio
import zipfile
import shutil
import time
import aiohttp

from pyrogram import filters, Client
from pyrogram.types import Message
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.utils.human_readable import humanbytes

TEMP_DIR = "./zip_temp"


def _work_dir(user_id: int) -> str:
    d = os.path.join(TEMP_DIR, f"{user_id}_{int(time.time())}")
    os.makedirs(d, exist_ok=True)
    return d


# ═══════════════════════ /zip COMMAND ═══════════════════════

@StreamBot.on_message(filters.command("zip") & filters.private)
async def zip_cmd(client: Client, m: Message):
    """
    /zip — Collect multiple files interactively, then compress them into a .zip archive.
    """
    work_dir = _work_dir(m.from_user.id)
    input_dir = os.path.join(work_dir, "input")
    os.makedirs(input_dir, exist_ok=True)
    status = await m.reply_text(
        "<b>📦 Zip Creator</b>\n\n"
        "Send me files one by one. When you're done, send <code>/done</code> to create the zip.\n\n"
        "Send <code>/cancel</code> to abort.",
        quote=True,
    )
    collected_files = []

    try:
        while True:
            file_msg = await client.ask(
                chat_id=m.chat.id,
                text=f"📎 **Files collected: {len(collected_files)}**\n\nSend another file, or send /done to create the zip:",
                timeout=120,
            )

            # Check for /done
            if file_msg.text and file_msg.text.strip().lower() == "/done":
                break

            # Check for /cancel
            if file_msg.text and file_msg.text.strip().lower() == "/cancel":
                await status.edit_text("❌ Zip creation cancelled.")
                return

            # Download the file if it has media
            if file_msg.video or file_msg.document or file_msg.audio or file_msg.photo:
                dl_path = await client.download_media(
                    file_msg, file_name=os.path.join(input_dir, "")
                )
                if dl_path:
                    collected_files.append(dl_path)
                    await file_msg.reply_text(
                        f"✅ Added: `{os.path.basename(dl_path)}`\n"
                        f"Total files: **{len(collected_files)}**",
                        quote=True,
                    )
            else:
                await file_msg.reply_text("⚠️ That's not a file. Send a file or /done to finish.", quote=True)

        if not collected_files:
            await status.edit_text("❌ No files collected. Cancelled.")
            return

        # Ask for zip filename
        name_msg = await client.ask(
            chat_id=m.chat.id,
            text="📝 Send a name for the zip file (without .zip extension), or /skip for default:",
            timeout=60,
        )
        zip_name = "archive"
        if name_msg.text and name_msg.text.strip().lower() != "/skip":
            # Sanitize filename
            zip_name = "".join(c for c in name_msg.text.strip() if c.isalnum() or c in " _-").strip()
            if not zip_name:
                zip_name = "archive"

        await status.edit_text(f"⏳ Creating **{zip_name}.zip** with {len(collected_files)} files...")

        zip_path = os.path.join(work_dir, f"{zip_name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in collected_files:
                zf.write(fpath, arcname=os.path.basename(fpath))

        zip_size = os.path.getsize(zip_path)
        if zip_size > 1.95 * 1024 * 1024 * 1024:
            await status.edit_text(f"❌ The zip file is too large ({humanbytes(zip_size)}) to upload to Telegram (limit: 1.95 GB).")
            return

        await status.edit_text("⬆️ Uploading zip file...")
        await client.send_document(
            m.chat.id,
            zip_path,
            caption=f"📦 **{zip_name}.zip**\n\n📁 Files: **{len(collected_files)}**\n📦 Size: **{humanbytes(zip_size)}**",
            reply_to_message_id=m.id,
            file_name=f"{zip_name}.zip",
        )
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out waiting for files. Please start again with /zip")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ═══════════════════════ /unzip COMMAND ═══════════════════════

async def _download_url_file(url: str, dest_dir: str) -> str:
    """Download a file from URL to the dest_dir."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=600)) as resp:
            if resp.status != 200:
                return None
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                fname = cd.split("filename=")[-1].strip('" ')
            else:
                fname = os.path.basename(url.split("?")[0]) or f"archive_{int(time.time())}"

            fpath = os.path.join(dest_dir, fname)
            with open(fpath, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
            return fpath


@StreamBot.on_message(filters.command("unzip") & filters.private)
async def unzip_cmd(client: Client, m: Message):
    """
    /unzip — Reply to a zip/rar/7z file or provide a URL to extract and upload contents.
    """
    work_dir = _work_dir(m.from_user.id)
    archive_dir = os.path.join(work_dir, "archive")
    extract_dir = os.path.join(work_dir, "extracted")
    os.makedirs(archive_dir, exist_ok=True)
    os.makedirs(extract_dir, exist_ok=True)

    status = await m.reply_text("⏳ Processing...", quote=True)
    archive_path = None

    try:
        # Case 1: Reply to a file
        if m.reply_to_message and (m.reply_to_message.document or m.reply_to_message.video or m.reply_to_message.audio):
            await status.edit_text("⬇️ Downloading archive...")
            archive_path = await client.download_media(
                m.reply_to_message,
                file_name=os.path.join(archive_dir, ""),
            )

        # Case 2: URL in command args
        elif len(m.command) > 1:
            url = m.command[1]
            if not url.startswith(("http://", "https://")):
                await status.edit_text("❌ Invalid URL.")
                return
            await status.edit_text("⬇️ Downloading archive from URL...")
            archive_path = await _download_url_file(url, archive_dir)

        # Case 3: Reply to a text message containing a URL
        elif m.reply_to_message and m.reply_to_message.text:
            url = m.reply_to_message.text.strip()
            if not url.startswith(("http://", "https://")):
                await status.edit_text("❌ Replied message is not a valid URL.")
                return
            await status.edit_text("⬇️ Downloading archive from URL...")
            archive_path = await _download_url_file(url, archive_dir)

        else:
            await status.edit_text(
                "<b>Usage:</b>\n\n"
                "• <code>/unzip</code> — Reply to a zip/rar/7z file\n"
                "• <code>/unzip &lt;url&gt;</code> — Provide a direct URL to an archive"
            )
            return

        if not archive_path or not os.path.exists(archive_path):
            await status.edit_text("❌ Failed to download the archive.")
            return

        await status.edit_text("📂 Extracting archive...")

        ext = os.path.splitext(archive_path)[1].lower()

        if ext == ".zip":
            # Use Python's zipfile
            try:
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extractall(extract_dir)
            except zipfile.BadZipFile:
                await status.edit_text("❌ The file is not a valid ZIP archive.")
                return

        elif ext in (".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"):
            # Use 7z CLI for rar/7z/tar and other formats
            cmd = ["7z", "x", archive_path, f"-o{extract_dir}", "-y"]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0:
                await status.edit_text(f"❌ Extraction failed:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
                return
        else:
            # Try 7z as a fallback for unknown extensions
            cmd = ["7z", "x", archive_path, f"-o{extract_dir}", "-y"]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0:
                await status.edit_text("❌ Unsupported or invalid archive format.")
                return

        # Gather all extracted files
        extracted_files = []
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                extracted_files.append(os.path.join(root, f))

        if not extracted_files:
            await status.edit_text("❌ Archive is empty or extraction failed.")
            return

        await status.edit_text(f"⬆️ Uploading **{len(extracted_files)}** extracted files...")

        uploaded = 0
        skipped = 0
        for fpath in extracted_files:
            fsize = os.path.getsize(fpath)
            fname = os.path.basename(fpath)

            if fsize > 1.95 * 1024 * 1024 * 1024:
                skipped += 1
                await m.reply_text(f"⚠️ Skipped **{fname}** — too large ({humanbytes(fsize)})", quote=True)
                continue

            if fsize == 0:
                skipped += 1
                continue

            try:
                await client.send_document(
                    m.chat.id,
                    fpath,
                    caption=f"📄 **{fname}**\n📦 Size: **{humanbytes(fsize)}**",
                    reply_to_message_id=m.id,
                    file_name=fname,
                )
                uploaded += 1
                # Small delay to avoid flood
                await asyncio.sleep(1)
            except Exception as e:
                skipped += 1
                await m.reply_text(f"⚠️ Failed to upload **{fname}**: `{e}`", quote=True)

        await status.edit_text(
            f"✅ **Extraction Complete!**\n\n"
            f"📁 Total files: **{len(extracted_files)}**\n"
            f"⬆️ Uploaded: **{uploaded}**\n"
            f"⚠️ Skipped: **{skipped}**"
        )

    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")

    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
