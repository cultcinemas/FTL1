# f2lnk/bot/plugins/video_tools.py

import os
import asyncio
import shutil
import time

from pyrogram import filters, Client
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.utils.human_readable import humanbytes

# ─────────────────────── helpers ───────────────────────

TEMP_DIR = "./vt_temp"


def _user_dir(user_id: int) -> str:
    """Return a unique temp directory for the user's current job."""
    d = os.path.join(TEMP_DIR, f"{user_id}_{int(time.time())}")
    os.makedirs(d, exist_ok=True)
    return d


async def _run_cmd(cmd: list, timeout: int = 600) -> tuple:
    """Run a subprocess command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, b"", b"Process timed out."
    return proc.returncode, stdout, stderr


async def _download_replied_media(client: Client, msg: Message, dest_dir: str, label: str = "file") -> str:
    """Download the media from a replied message and return the local path."""
    if not msg or not (msg.video or msg.document or msg.audio or msg.photo):
        return None
    path = await client.download_media(msg, file_name=os.path.join(dest_dir, ""))
    return path


async def _upload_result(client: Client, chat_id: int, file_path: str, reply_to: int, caption: str = ""):
    """Upload the result file back to the user."""
    file_size = os.path.getsize(file_path)
    cap = caption or os.path.basename(file_path)
    cap += f"\n\n📦 **Size:** `{humanbytes(file_size)}`"

    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".ts"):
        await client.send_video(
            chat_id, file_path, caption=cap, reply_to_message_id=reply_to,
            file_name=os.path.basename(file_path)
        )
    elif ext in (".mp3", ".aac", ".ogg", ".flac", ".wav", ".m4a", ".opus"):
        await client.send_audio(
            chat_id, file_path, caption=cap, reply_to_message_id=reply_to,
            file_name=os.path.basename(file_path)
        )
    else:
        await client.send_document(
            chat_id, file_path, caption=cap, reply_to_message_id=reply_to,
            file_name=os.path.basename(file_path)
        )


# ─────────────────────── /vt command ───────────────────────

VT_MENU_TEXT = (
    "<b>🎬 Video Tools</b>\n\n"
    "Select a tool from the menu below:"
)

VT_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("1️⃣ Merge Video+Video", callback_data="vt_merge_vv"),
        InlineKeyboardButton("2️⃣ Merge Video+Audio", callback_data="vt_merge_va"),
    ],
    [
        InlineKeyboardButton("3️⃣ Merge Video+Subtitle", callback_data="vt_merge_vs"),
        InlineKeyboardButton("4️⃣ Hardsub", callback_data="vt_hardsub"),
    ],
    [
        InlineKeyboardButton("5️⃣ SubSync", callback_data="vt_subsync"),
        InlineKeyboardButton("6️⃣ Compress", callback_data="vt_compress"),
    ],
    [
        InlineKeyboardButton("7️⃣ Trim Video", callback_data="vt_trim"),
        InlineKeyboardButton("8️⃣ Watermark", callback_data="vt_watermark"),
    ],
    [
        InlineKeyboardButton("9️⃣ Remove Video Stream", callback_data="vt_remove_vs"),
        InlineKeyboardButton("🔟 Extract Video Stream", callback_data="vt_extract_vs"),
    ],
    [InlineKeyboardButton("❌ Close", callback_data="vt_close")],
])


@StreamBot.on_message(filters.command("vt") & filters.private)
async def vt_menu(client: Client, m: Message):
    await m.reply_text(VT_MENU_TEXT, reply_markup=VT_BUTTONS, quote=True)


# ─────────────────────── callback router ───────────────────────

@StreamBot.on_callback_query(filters.regex(r"^vt_"), group=10)
async def vt_callback(client: Client, cb: CallbackQuery):
    data = cb.data

    if data == "vt_close":
        await cb.message.delete()
        return

    await cb.answer()

    handlers = {
        "vt_merge_vv": _merge_video_video,
        "vt_merge_va": _merge_video_audio,
        "vt_merge_vs": _merge_video_subtitle,
        "vt_hardsub": _hardsub,
        "vt_subsync": _subsync,
        "vt_compress": _compress,
        "vt_trim": _trim,
        "vt_watermark": _watermark,
        "vt_remove_vs": _remove_video_stream,
        "vt_extract_vs": _extract_video_stream,
    }

    handler = handlers.get(data)
    if handler:
        await handler(client, cb)


# ═══════════════════════ TOOL IMPLEMENTATIONS ═══════════════════════


# ──── 1. Merge Video + Video ────
async def _merge_video_video(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Merge Video + Video**\n\nSend the **first video** file:")
    try:
        vid1_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **first** video file:", timeout=120)
        vid1_path = await _download_replied_media(client, vid1_msg, work_dir, "video1")
        if not vid1_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        vid2_msg = await client.ask(chat_id=chat_id, text="⬆️ Now send the **second** video file:", timeout=120)
        vid2_path = await _download_replied_media(client, vid2_msg, work_dir, "video2")
        if not vid2_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        await status.edit_text("⏳ Merging videos... Please wait.")

        # Create concat file
        concat_file = os.path.join(work_dir, "concat.txt")
        with open(concat_file, "w") as f:
            f.write(f"file '{os.path.abspath(vid1_path)}'\n")
            f.write(f"file '{os.path.abspath(vid2_path)}'\n")

        output_path = os.path.join(work_dir, "merged_output.mkv")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file, "-c", "copy", output_path
        ]
        rc, _, stderr = await _run_cmd(cmd)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading merged video...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Merged Video**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 2. Merge Video + Audio ────
async def _merge_video_audio(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Merge Video + Audio**\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        aud_msg = await client.ask(chat_id=chat_id, text="⬆️ Now send the **audio** file:", timeout=120)
        aud_path = await _download_replied_media(client, aud_msg, work_dir, "audio")
        if not aud_path:
            await status.edit_text("❌ No valid audio received. Cancelled.")
            return

        await status.edit_text("⏳ Merging video + audio... Please wait.")

        output_path = os.path.join(work_dir, "merged_va.mkv")
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_path,
            "-i", aud_path,
            "-c:v", "copy", "-c:a", "copy",
            "-map", "0:v:0", "-map", "1:a:0",
            output_path
        ]
        rc, _, stderr = await _run_cmd(cmd)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading result...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Merged Video + Audio**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 3. Merge Video + Subtitle ────
async def _merge_video_subtitle(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Merge Video + Subtitle (soft sub)**\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        sub_msg = await client.ask(chat_id=chat_id, text="⬆️ Now send the **subtitle** file (.srt / .ass / .ssa / .vtt):", timeout=120)
        sub_path = await _download_replied_media(client, sub_msg, work_dir, "subtitle")
        if not sub_path:
            await status.edit_text("❌ No valid subtitle received. Cancelled.")
            return

        await status.edit_text("⏳ Muxing subtitle into video... Please wait.")

        output_path = os.path.join(work_dir, "softsub_output.mkv")
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_path,
            "-i", sub_path,
            "-c:v", "copy", "-c:a", "copy", "-c:s", "srt",
            "-map", "0:v", "-map", "0:a?", "-map", "1:s",
            output_path
        ]
        rc, _, stderr = await _run_cmd(cmd)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading result...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Video + Subtitle (Soft Sub)**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 4. Hardsub ────
async def _hardsub(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Hardsub (Burn Subtitles)**\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        sub_msg = await client.ask(chat_id=chat_id, text="⬆️ Now send the **subtitle** file (.srt / .ass):", timeout=120)
        sub_path = await _download_replied_media(client, sub_msg, work_dir, "subtitle")
        if not sub_path:
            await status.edit_text("❌ No valid subtitle received. Cancelled.")
            return

        await status.edit_text("⏳ Hardsubbing video... This may take a while.")

        # Escape special chars in path for ffmpeg filter
        safe_sub_path = sub_path.replace("\\", "/").replace(":", "\\:")
        output_path = os.path.join(work_dir, "hardsub_output.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_path,
            "-vf", f"subtitles={safe_sub_path}",
            "-c:a", "copy",
            output_path
        ]
        rc, _, stderr = await _run_cmd(cmd, timeout=1800)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading hardsubbed video...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Hardsubbed Video**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 5. SubSync ────
async def _subsync(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**SubSync (Auto Subtitle Synchronization)**\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** (reference) file:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        sub_msg = await client.ask(chat_id=chat_id, text="⬆️ Now send the **out-of-sync subtitle** file (.srt):", timeout=120)
        sub_path = await _download_replied_media(client, sub_msg, work_dir, "subtitle")
        if not sub_path:
            await status.edit_text("❌ No valid subtitle received. Cancelled.")
            return

        await status.edit_text("⏳ Synchronizing subtitles... This may take a while.")

        output_path = os.path.join(work_dir, "synced_subtitle.srt")
        cmd = [
            "ffsubsync",
            vid_path,
            "-i", sub_path,
            "-o", output_path,
        ]
        rc, _, stderr = await _run_cmd(cmd, timeout=600)
        if rc != 0:
            await status.edit_text(f"❌ ffsubsync error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading synced subtitle...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Synced Subtitle**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 6. Compress Video ────
async def _compress(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Compress Video**\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file to compress:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        crf_msg = await client.ask(
            chat_id=chat_id,
            text=(
                "Choose compression level (CRF value):\n\n"
                "• `18` — High quality, larger file\n"
                "• `23` — Medium quality (recommended)\n"
                "• `28` — Lower quality, smaller file\n"
                "• `32` — Heavy compression\n\n"
                "Send a number between **18** and **51**, or send /skip for default (28):"
            ),
            timeout=60,
        )

        crf = "28"
        if crf_msg.text and crf_msg.text.strip() != "/skip":
            try:
                crf_val = int(crf_msg.text.strip())
                if 0 <= crf_val <= 51:
                    crf = str(crf_val)
            except ValueError:
                pass

        await status.edit_text(f"⏳ Compressing video with CRF={crf}... This may take a while.")

        output_path = os.path.join(work_dir, "compressed_output.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_path,
            "-c:v", "libx264", "-crf", crf, "-preset", "faster",
            "-c:a", "aac", "-b:a", "128k",
            output_path
        ]
        rc, _, stderr = await _run_cmd(cmd, timeout=3600)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        original_size = os.path.getsize(vid_path)
        compressed_size = os.path.getsize(output_path)

        await status.edit_text("⬆️ Uploading compressed video...")
        caption = (
            f"✅ **Compressed Video** (CRF={crf})\n"
            f"📉 `{humanbytes(original_size)}` → `{humanbytes(compressed_size)}`"
        )
        await _upload_result(client, chat_id, output_path, cb.message.id, caption)
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 7. Trim Video ────
async def _trim(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Trim Video**\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file to trim:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        start_msg = await client.ask(
            chat_id=chat_id,
            text="⏱️ Send the **start time** (format: `HH:MM:SS` or `MM:SS` or seconds).\n\nExample: `00:01:30` or `90`",
            timeout=60,
        )
        start_time = start_msg.text.strip() if start_msg.text else "00:00:00"

        end_msg = await client.ask(
            chat_id=chat_id,
            text="⏱️ Send the **end time** (same format).\n\nExample: `00:05:00` or `300`",
            timeout=60,
        )
        end_time = end_msg.text.strip() if end_msg.text else ""
        if not end_time:
            await status.edit_text("❌ End time is required. Cancelled.")
            return

        await status.edit_text(f"⏳ Trimming video from `{start_time}` to `{end_time}`...")

        output_path = os.path.join(work_dir, "trimmed_output.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_path,
            "-ss", start_time,
            "-to", end_time,
            "-c", "copy",
            output_path
        ]
        rc, _, stderr = await _run_cmd(cmd)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading trimmed video...")
        await _upload_result(
            client, chat_id, output_path, cb.message.id,
            f"✅ **Trimmed Video** (`{start_time}` → `{end_time}`)"
        )
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 8. Watermark Video ────
async def _watermark(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Watermark Video**\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        wm_type_msg = await client.ask(
            chat_id=chat_id,
            text=(
                "Choose watermark type:\n\n"
                "• Send `text` — to add a text watermark\n"
                "• Send an **image** — to add an image watermark"
            ),
            timeout=60,
        )

        output_path = os.path.join(work_dir, "watermarked_output.mp4")

        if wm_type_msg.photo or wm_type_msg.document:
            # Image watermark
            wm_path = await _download_replied_media(client, wm_type_msg, work_dir, "watermark")
            if not wm_path:
                await status.edit_text("❌ No valid image received. Cancelled.")
                return

            pos_msg = await client.ask(
                chat_id=chat_id,
                text=(
                    "Choose position for the watermark:\n\n"
                    "• `tl` — Top Left\n"
                    "• `tr` — Top Right\n"
                    "• `bl` — Bottom Left\n"
                    "• `br` — Bottom Right (default)\n"
                    "• `center` — Center"
                ),
                timeout=30,
            )
            pos = (pos_msg.text.strip().lower() if pos_msg.text else "br")

            # Map position to ffmpeg overlay coordinates
            pos_map = {
                "tl": "10:10",
                "tr": "main_w-overlay_w-10:10",
                "bl": "10:main_h-overlay_h-10",
                "br": "main_w-overlay_w-10:main_h-overlay_h-10",
                "center": "(main_w-overlay_w)/2:(main_h-overlay_h)/2",
            }
            overlay = pos_map.get(pos, pos_map["br"])

            await status.edit_text("⏳ Adding image watermark...")
            cmd = [
                "ffmpeg", "-y",
                "-i", vid_path,
                "-i", wm_path,
                "-filter_complex", f"[1:v]scale=iw/5:-1[wm];[0:v][wm]overlay={overlay}",
                "-c:a", "copy",
                output_path
            ]
        else:
            # Text watermark
            wm_text = wm_type_msg.text.strip() if wm_type_msg.text else "Watermark"

            pos_msg = await client.ask(
                chat_id=chat_id,
                text=(
                    "Choose position:\n\n"
                    "• `tl` — Top Left\n"
                    "• `tr` — Top Right\n"
                    "• `bl` — Bottom Left\n"
                    "• `br` — Bottom Right (default)\n"
                    "• `center` — Center"
                ),
                timeout=30,
            )
            pos = (pos_msg.text.strip().lower() if pos_msg.text else "br")

            pos_map = {
                "tl": "x=10:y=10",
                "tr": "x=w-tw-10:y=10",
                "bl": "x=10:y=h-th-10",
                "br": "x=w-tw-10:y=h-th-10",
                "center": "x=(w-tw)/2:y=(h-th)/2",
            }
            dt = pos_map.get(pos, pos_map["br"])

            safe_text = wm_text.replace("'", "\\'").replace(":", "\\:")
            await status.edit_text("⏳ Adding text watermark...")
            cmd = [
                "ffmpeg", "-y",
                "-i", vid_path,
                "-vf", f"drawtext=text='{safe_text}':fontsize=24:fontcolor=white@0.7:{dt}",
                "-c:a", "copy",
                output_path
            ]

        rc, _, stderr = await _run_cmd(cmd, timeout=1800)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading watermarked video...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Watermarked Video**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 9. Remove Video Stream (extract audio only) ────
async def _remove_video_stream(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Remove Video Stream**\nThis keeps only the audio track.\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        await status.edit_text("⏳ Removing video stream...")

        output_path = os.path.join(work_dir, "audio_only.m4a")
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_path,
            "-vn", "-c:a", "copy",
            output_path
        ]
        rc, _, stderr = await _run_cmd(cmd)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading audio-only file...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Audio Only (Video Stream Removed)**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ──── 10. Extract Video Stream (remove audio) ────
async def _extract_video_stream(client: Client, cb: CallbackQuery):
    chat_id = cb.message.chat.id
    work_dir = _user_dir(cb.from_user.id)
    status = await cb.message.edit_text("**Extract Video Stream**\nThis removes all audio tracks.\n\nSend the **video** file:")
    try:
        vid_msg = await client.ask(chat_id=chat_id, text="⬆️ Send the **video** file:", timeout=120)
        vid_path = await _download_replied_media(client, vid_msg, work_dir, "video")
        if not vid_path:
            await status.edit_text("❌ No valid video received. Cancelled.")
            return

        await status.edit_text("⏳ Extracting video stream (removing audio)...")

        output_path = os.path.join(work_dir, "video_only.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_path,
            "-an", "-c:v", "copy",
            output_path
        ]
        rc, _, stderr = await _run_cmd(cmd)
        if rc != 0:
            await status.edit_text(f"❌ FFmpeg error:\n`{stderr.decode('utf-8', errors='replace')[:1000]}`")
            return

        await status.edit_text("⬆️ Uploading video-only file...")
        await _upload_result(client, chat_id, output_path, cb.message.id, "✅ **Video Only (Audio Removed)**")
        await status.delete()

    except ListenerTimeout:
        await status.edit_text("⌛ Timed out. Please try again with /vt")
    except Exception as e:
        await status.edit_text(f"❌ Error: `{e}`")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
