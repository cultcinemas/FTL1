# f2lnk/bot/plugins/leech.py
# /leech command — reply-based file tagging with 6 tools.
#
# Supported tools:
#   -vt  Video + Video Merge
#   -va  Video + Audio Merge
#   -aa  Audio + Audio Merge
#   -vs  Video + Subtitle Merge
#   -cv  Compress Video
#   -wv  Watermark Video

import os
import re
import asyncio
import logging

from pyrogram import filters, Client
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
)
from pyromod.exceptions import ListenerTimeout

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.utils.human_readable import humanbytes
from f2lnk.bot.task_manager import (
    LeechTask,
    TaskStatus,
    ACTIVE_LEECH_TASKS,
    AVAILABLE_TOOLS,
    DEFAULT_TOOL,
    generate_task_id,
    get_task,
    register_task,
    remove_task,
    cancel_task,
    cleanup_task_files,
)
from f2lnk.bot.leech_tools import (
    process_video_video,
    process_video_audio,
    process_audio_audio,
    process_video_subtitle,
    process_compress_video,
    process_watermark_video,
    process_trim_video,
    process_cut_video,
    process_remove_video,
    process_extract_video,
    upload_batch_results,
)

logger = logging.getLogger(__name__)

STAGGER_DELAY = 5


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

# Map of flag → tool key
FLAG_TO_TOOL = {
    "-vt": "vt",
    "-va": "va",
    "-aa": "aa",
    "-vs": "vs",
    "-cv": "cv",
    "-wv": "wv",
    "-tv": "tv",
    "-cut": "cut",
    "-rv": "rv",
    "-ev": "ev",
}


def _parse_leech_args(text: str):
    """Parse /leech flags. Returns (file_count, output_name, suggested_tool, start_time, end_time)."""
    file_count = None
    output_name = None
    suggested_tool = DEFAULT_TOOL
    start_time = None
    end_time = None

    m = re.search(r"-i\s+(\d+)", text)
    if m:
        file_count = int(m.group(1))

    m = re.search(r"-m\s+(.+?)(?:\s+-\w|$)", text)
    if m:
        output_name = m.group(1).strip()

    # Parse timestamps for trim/cut
    m = re.search(r"-start\s+([\d:]+)", text)
    if m:
        start_time = m.group(1).strip()
    m = re.search(r"-end\s+([\d:]+)", text)
    if m:
        end_time = m.group(1).strip()

    # Check for tool flags (longest first to avoid -cut matching -cv)
    for flag, tool_key in sorted(FLAG_TO_TOOL.items(), key=lambda x: -len(x[0])):
        if flag in text:
            suggested_tool = tool_key
            break

    if not file_count or file_count < 1:
        raise ValueError("File count (-i) must be at least 1.")
    if not output_name:
        raise ValueError("Output name (-m) is required.")

    return file_count, output_name, suggested_tool, start_time, end_time


def _is_media_message(msg: Message) -> bool:
    return bool(msg and (msg.video or msg.document or msg.audio))


# ───── Keyboard builders ─────

def _tool_selection_keyboard(current: str = None):
    buttons = []
    for key, label in AVAILABLE_TOOLS.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_tool_{key}")]
        )
    buttons.append(
        [InlineKeyboardButton("❌ Cancel Task", callback_data="leech_tool_cancel")]
    )
    return InlineKeyboardMarkup(buttons)


def _audio_mode_keyboard(current: int = None):
    modes = {
        1: "🔇 Remove original audio + Add uploaded audios",
        2: "🔊 Keep original audio + Add uploaded audios",
    }
    buttons = []
    for key, label in modes.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_amode_{key}")]
        )
    return InlineKeyboardMarkup(buttons)


def _subtitle_mode_keyboard(current: int = None):
    modes = {
        1: "🔥 Hardcode (burn-in) subtitles",
        2: "📝 Soft subtitles (selectable streams)",
    }
    buttons = []
    for key, label in modes.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_smode_{key}")]
        )
    return InlineKeyboardMarkup(buttons)


def _compress_mode_keyboard(current: int = None):
    modes = {
        1: "🟢 High Quality (CRF 18)",
        2: "🟡 Balanced (CRF 23)",
        3: "🔴 High Compression (CRF 28)",
        4: "🎯 Target File Size",
        5: "⚙️ Custom CRF",
    }
    buttons = []
    for key, label in modes.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_cmode_{key}")]
        )
    return InlineKeyboardMarkup(buttons)


def _audio_format_keyboard(current: str = None):
    formats = {
        "mp3": "🎵 MP3",
        "aac": "🎵 AAC",
        "wav": "🎵 WAV",
        "copy": "📋 Keep Original Codec",
    }
    buttons = []
    for key, label in formats.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_afmt_{key}")]
        )
    return InlineKeyboardMarkup(buttons)


def _watermark_mode_keyboard(current: int = None):
    modes = {
        1: "📌 Static",
        2: "🌅 Fade In",
        3: "🌅🌇 Fade In + Out",
        4: "➡️ Moving",
        5: "⚡ Bouncing",
        6: "🌊 Floating Random",
        7: "📜 Scrolling L→R",
        8: "💫 Pulsing Opacity",
    }
    buttons = []
    for key, label in modes.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_wmode_{key}")]
        )
    return InlineKeyboardMarkup(buttons)


def _position_keyboard(current: str = None):
    positions = {
        "tl": "↖️ Top Left",
        "tr": "↗️ Top Right",
        "bl": "↙️ Bottom Left",
        "br": "↘️ Bottom Right",
        "center": "🎯 Center",
    }
    buttons = []
    for key, label in positions.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_wpos_{key}")]
        )
    return InlineKeyboardMarkup(buttons)


def _sub_choice_keyboard(subtitle_names: list):
    """Build keyboard to pick which subtitle to hardcode."""
    buttons = []
    for i, name in enumerate(subtitle_names):
        short = os.path.basename(name)[:40]
        buttons.append(
            [InlineKeyboardButton(f"{i + 1}. {short}", callback_data=f"leech_subpick_{i}")]
        )
    return InlineKeyboardMarkup(buttons)


# ───── File scanner ─────

async def _find_consecutive_files(client, chat_id, first_msg_id, user_id, total_needed):
    collected = []
    scan_range = total_needed * 4
    start_id = first_msg_id
    attempts = 0
    while len(collected) < total_needed and attempts < 5:
        ids_to_check = list(range(start_id, start_id + scan_range))
        try:
            messages = await client.get_messages(chat_id, message_ids=ids_to_check)
        except Exception as e:
            logger.warning("Error fetching messages: %s", e)
            break
        if not isinstance(messages, list):
            messages = [messages]
        for msg in messages:
            if len(collected) >= total_needed:
                break
            if not msg or msg.empty:
                continue
            if msg.from_user and msg.from_user.id == user_id and _is_media_message(msg):
                if not any(m.id == msg.id for m in collected):
                    collected.append(msg)
        start_id += scan_range
        attempts += 1
    return collected


# ═══════════════════════════════════════════════════════════════
#  /leech COMMAND — MAIN FLOW
# ═══════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("leech") & filters.private)
async def leech_command(client: Client, m: Message):

    # ── Reply required ──
    if not m.reply_to_message:
        await m.reply_text(
            "❌ **Please reply to the first file.**\n\n"
            "Send your files, then swipe to the **first** file and reply with:\n"
            "`/leech -i <count> -m <name> -vt`\n\n"
            "**Available tools:**\n"
            "`-vt` Video+Video  `-va` Video+Audio\n"
            "`-aa` Audio+Audio  `-vs` Video+Subtitle\n"
            "`-cv` Compress     `-wv` Watermark\n"
            "`-tv` Trim         `-cut` Cut\n"
            "`-rv` Extract Audio `-ev` Extract Video",
            quote=True,
        )
        return

    replied = m.reply_to_message
    if not _is_media_message(replied):
        await m.reply_text("❌ The replied message is not a media file.", quote=True)
        return

    # ── Parse command ──
    try:
        file_count, output_name, suggested_tool, start_time, end_time = _parse_leech_args(m.text)
    except ValueError as e:
        await m.reply_text(
            f"❌ **Invalid command.**\n`{e}`\n\n"
            f"**Usage:** `/leech -i <count> -m <name> -vt`",
            quote=True,
        )
        return

    # ── Find files ──
    wait_msg = await m.reply_text(
        f"🔍 Finding **{file_count}** files...", quote=True,
    )
    file_messages = await _find_consecutive_files(
        client, m.chat.id, replied.id, m.from_user.id, file_count
    )
    if len(file_messages) < file_count:
        await wait_msg.edit_text(
            f"❌ Found only **{len(file_messages)}** file(s) but need **{file_count}**.\n"
            f"Send more files and try again."
        )
        return

    # ── Create task ──
    task_id = generate_task_id()
    task = LeechTask(
        task_id=task_id,
        user_id=m.from_user.id,
        chat_id=m.chat.id,
        file_count=file_count,
        output_name=output_name,
        suggested_tool=suggested_tool,
    )
    task.file_messages = file_messages
    if start_time:
        task.start_time = start_time
    if end_time:
        task.end_time = end_time
    register_task(task)

    await wait_msg.edit_text(
        f"🆔 **Task ID:** `{task_id}`\n"
        f"Use `/cancel {task_id}` to stop anytime.\n\n"
        f"📂 **Files:** {file_count}\n"
        f"📝 **Output:** `{task.output_name}`"
    )

    # ── Tool selection ──
    task.status = TaskStatus.TOOL_SELECTION_PENDING
    await client.send_message(
        m.chat.id, "**Select Tool:**",
        reply_markup=_tool_selection_keyboard(suggested_tool),
    )
    while task.status == TaskStatus.TOOL_SELECTION_PENDING:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)
    if task.status == TaskStatus.CANCELLED:
        return

    # ── Tool-specific configuration ──
    if task.selected_tool == "va":
        await _config_audio_mode(client, m, task)
        if task.cancel_event.is_set():
            return

    elif task.selected_tool == "vs":
        await _config_subtitle_mode(client, m, task)
        if task.cancel_event.is_set():
            return

    elif task.selected_tool == "cv":
        await _config_compress_mode(client, m, task)
        if task.cancel_event.is_set():
            return

    elif task.selected_tool == "wv":
        await _config_watermark(client, m, task)
        if task.cancel_event.is_set():
            return

    elif task.selected_tool in ("tv", "cut"):
        await _config_trim_cut(client, m, task)
        if task.cancel_event.is_set():
            return

    elif task.selected_tool == "rv":
        await _config_audio_format(client, m, task)
        if task.cancel_event.is_set():
            return

    # ── Done confirmation ──
    task.status = TaskStatus.WAITING_FOR_DONE
    try:
        while True:
            if task.cancel_event.is_set():
                return

            summary = _build_task_summary(task)
            done_msg = await client.ask(
                chat_id=m.chat.id,
                text=f"{summary}\n\nType **\"done\"** to start processing\nor select another tool:",
                timeout=120,
                reply_markup=_tool_selection_keyboard(task.selected_tool),
            )
            if done_msg.text and done_msg.text.strip().lower() == "done":
                break
            elif done_msg.text and done_msg.text.strip().startswith("/cancel"):
                await _handle_inline_cancel(client, done_msg, task)
                return
    except ListenerTimeout:
        task.status = TaskStatus.FAILED
        cleanup_task_files(task)
        remove_task(task_id)
        await client.send_message(m.chat.id, f"⌛ Task `{task_id}` timed out.")
        return

    if task.cancel_event.is_set():
        return

    # ── Staggered parallel downloads ──
    task.status = TaskStatus.DOWNLOADING
    status_msg = await client.send_message(
        m.chat.id,
        f"⬇️ **Downloading {file_count} files** ({STAGGER_DELAY}s apart)...\n"
        f"Task: `{task_id}`",
    )

    async def _download_one(index, file_msg):
        orig_name = ""
        if file_msg.video and file_msg.video.file_name:
            orig_name = file_msg.video.file_name
        elif file_msg.audio and file_msg.audio.file_name:
            orig_name = file_msg.audio.file_name
        elif file_msg.document and file_msg.document.file_name:
            orig_name = file_msg.document.file_name
        else:
            orig_name = f"file_{index}"
        unique_name = f"{index:03d}_{orig_name}"
        save_path = os.path.join(task.work_dir, unique_name)
        path = await client.download_media(file_msg, file_name=save_path)
        return index, path, file_msg

    for i, fmsg in enumerate(task.file_messages):
        if task.cancel_event.is_set():
            break
        dl_task = asyncio.create_task(_download_one(i, fmsg))
        task.download_tasks.append(dl_task)
        try:
            await status_msg.edit_text(
                f"⬇️ **Downloading** — Task: `{task_id}`\n"
                f"📥 Started: {i + 1}/{file_count}"
            )
        except Exception:
            pass
        if i < file_count - 1:
            for _ in range(STAGGER_DELAY):
                if task.cancel_event.is_set():
                    break
                await asyncio.sleep(1)

    if task.cancel_event.is_set():
        return

    # ── Wait for downloads ──
    try:
        results = await asyncio.gather(*task.download_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        return

    if task.cancel_event.is_set():
        return

    download_errors = []
    downloaded_files = []
    for res in results:
        if isinstance(res, Exception):
            download_errors.append(str(res))
        elif isinstance(res, tuple):
            idx, path, orig_msg = res
            if path:
                downloaded_files.append((idx, path, orig_msg))
            else:
                download_errors.append(f"File {idx + 1} returned None")

    if download_errors:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **Download errors:**\n```\n{chr(10).join(download_errors[:5])}\n```"
        )
        cleanup_task_files(task)
        remove_task(task_id)
        return

    downloaded_files.sort(key=lambda x: x[0])
    await status_msg.edit_text(f"✅ All **{file_count}** files downloaded!\n⏳ Processing...")

    # ── Route to tool processor ──
    task.status = TaskStatus.PROCESSING
    tool = (task.selected_tool or "").strip()
    logger.info("Task %s: routing to tool '%s' (repr=%r)", task_id, tool, tool)

    # Dict dispatch — more robust than elif chain
    TOOL_DISPATCH = {
        "vt":  lambda: process_video_video(task, downloaded_files, status_msg),
        "va":  lambda: process_video_audio(client, task, downloaded_files, status_msg),
        "aa":  lambda: process_audio_audio(task, downloaded_files, status_msg),
        "vs":  lambda: process_video_subtitle(client, task, downloaded_files, status_msg),
        "cv":  lambda: process_compress_video(client, task, downloaded_files, status_msg),
        "wv":  lambda: process_watermark_video(client, task, downloaded_files, status_msg),
        "tv":  lambda: process_trim_video(task, downloaded_files, status_msg),
        "cut": lambda: process_cut_video(task, downloaded_files, status_msg),
        "rv":  lambda: process_remove_video(task, downloaded_files, status_msg),
        "ev":  lambda: process_extract_video(task, downloaded_files, status_msg),
    }

    handler = TOOL_DISPATCH.get(tool)
    if handler is None:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ Unknown tool: `{tool}` (type={type(tool).__name__}, repr={repr(tool)})"
        )
        logger.error("Task %s: unknown tool '%s', repr=%r", task_id, tool, tool)
        cleanup_task_files(task)
        remove_task(task_id)
        return

    ok = await handler()

    if not ok or task.cancel_event.is_set():
        return

    # ── Upload ──
    if tool in ("cv", "wv", "rv", "ev", "cut") and file_count > 1:
        # Batch tools produce multiple files
        upload_ok = await upload_batch_results(client, task, status_msg)
    else:
        # Single output file
        output_path = os.path.join(task.work_dir, task.output_name)
        task.status = TaskStatus.UPLOADING
        await status_msg.edit_text(f"⬆️ **Uploading** `{task.output_name}`...")

        try:
            file_size = os.path.getsize(output_path)
            caption = (
                f"✅ **Processed**\n"
                f"📁 `{task.output_name}`\n"
                f"📦 Size: `{humanbytes(file_size)}`\n"
                f"🆔 Task: `{task_id}`"
            )
            ext = os.path.splitext(output_path)[1].lower()
            if ext in (".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".ts"):
                await client.send_video(
                    task.chat_id, output_path, caption=caption,
                    file_name=task.output_name,
                )
            elif ext in (".mp3", ".aac", ".ogg", ".flac", ".wav", ".m4a", ".opus"):
                await client.send_audio(
                    task.chat_id, output_path, caption=caption,
                    file_name=task.output_name,
                )
            else:
                await client.send_document(
                    task.chat_id, output_path, caption=caption,
                    file_name=task.output_name,
                )
            upload_ok = True
        except Exception as e:
            task.status = TaskStatus.FAILED
            await status_msg.edit_text(f"❌ Upload failed: `{e}`")
            cleanup_task_files(task)
            remove_task(task_id)
            return

    if upload_ok:
        task.status = TaskStatus.COMPLETED
        await status_msg.edit_text(
            f"🎉 **Task `{task_id}` completed!**\n📁 Uploaded successfully."
        )
    cleanup_task_files(task)


# ═══════════════════════════════════════════════════════════════
#  TOOL-SPECIFIC CONFIGURATION FLOWS
# ═══════════════════════════════════════════════════════════════

async def _config_audio_mode(client, m, task):
    """VA tool: ask for audio mode (remove/keep original)."""
    await client.send_message(
        m.chat.id, "🎵 **Select Audio Mode:**",
        reply_markup=_audio_mode_keyboard(),
    )
    while task.audio_mode is None:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)


async def _config_subtitle_mode(client, m, task):
    """VS tool: ask for subtitle mode (hardcode/soft)."""
    await client.send_message(
        m.chat.id, "📝 **Select Subtitle Mode:**",
        reply_markup=_subtitle_mode_keyboard(),
    )
    while task.subtitle_mode is None:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)


async def _config_compress_mode(client, m, task):
    """CV tool: ask for compression mode, then target size or custom CRF if needed."""
    await client.send_message(
        m.chat.id, "📦 **Select Compression Mode:**",
        reply_markup=_compress_mode_keyboard(),
    )
    while task.compress_mode is None:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)

    if task.compress_mode == 4:
        # Ask for target file size
        try:
            size_msg = await client.ask(
                chat_id=m.chat.id,
                text="🎯 **Enter target size per file** (e.g. `100MB` or `50MB`):",
                timeout=60,
            )
            size_text = size_msg.text.strip().upper().replace(" ", "")
            # Parse MB value
            mb_match = re.search(r"(\d+(?:\.\d+)?)", size_text)
            if mb_match:
                task.target_size_mb = float(mb_match.group(1))
            else:
                task.target_size_mb = 100
            await client.send_message(
                m.chat.id,
                f"🎯 Target size set to: **{task.target_size_mb} MB** per file"
            )
        except ListenerTimeout:
            task.target_size_mb = 100
            await client.send_message(m.chat.id, "⏳ Defaulting to 100MB target.")

    elif task.compress_mode == 5:
        # Ask for custom CRF
        try:
            crf_msg = await client.ask(
                chat_id=m.chat.id,
                text="⚙️ **Enter CRF value** (0-51, lower = better quality, default 23):",
                timeout=60,
            )
            crf_val = int(crf_msg.text.strip())
            task.custom_crf = max(0, min(51, crf_val))
            await client.send_message(
                m.chat.id, f"⚙️ CRF set to: **{task.custom_crf}**"
            )
        except (ListenerTimeout, ValueError):
            task.custom_crf = 23
            await client.send_message(m.chat.id, "⏳ Defaulting to CRF 23.")


async def _config_watermark(client, m, task):
    """WV tool: ask for watermark content, mode, and position."""
    # Step 1: Ask for watermark content
    try:
        wm_msg = await client.ask(
            chat_id=m.chat.id,
            text=(
                "🖼️ **Send watermark content:**\n\n"
                "• Send a **text message** for text watermark\n"
                "• Send an **image** for image watermark"
            ),
            timeout=120,
        )
    except ListenerTimeout:
        task.status = TaskStatus.FAILED
        await client.send_message(m.chat.id, "⌛ Watermark input timed out.")
        cleanup_task_files(task)
        remove_task(task.task_id)
        return

    if wm_msg.photo:
        task.watermark_type = "image"
        img_path = await client.download_media(
            wm_msg, file_name=os.path.join(task.work_dir, "watermark.png")
        )
        task.watermark_image_path = img_path
        await client.send_message(m.chat.id, "✅ Image watermark received!")
    elif wm_msg.text:
        task.watermark_type = "text"
        task.watermark_text = wm_msg.text.strip()
        await client.send_message(
            m.chat.id, f"✅ Text watermark: **{task.watermark_text}**"
        )
    else:
        task.watermark_type = "text"
        task.watermark_text = "Watermark"

    # Step 2: Watermark animation mode
    await client.send_message(
        m.chat.id, "🎬 **Select Watermark Mode:**",
        reply_markup=_watermark_mode_keyboard(),
    )
    while task.watermark_mode is None:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)

    # Step 3: Position
    await client.send_message(
        m.chat.id, "📍 **Select Position:**",
        reply_markup=_position_keyboard(),
    )
    while task.watermark_position is None:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)


async def _config_trim_cut(client, m, task):
    """TV/CUT tool: confirm or ask for timestamps."""
    if not task.start_time or not task.end_time:
        try:
            ts_msg = await client.ask(
                chat_id=m.chat.id,
                text=(
                    "⏱️ **Enter start and end timestamps:**\n"
                    "Format: `HH:MM:SS` or `MM:SS`\n\n"
                    "Example: `00:01:00 00:02:30`"
                ),
                timeout=60,
            )
            parts = ts_msg.text.strip().split()
            if len(parts) >= 2:
                task.start_time = parts[0]
                task.end_time = parts[1]
            else:
                await client.send_message(
                    m.chat.id, "❌ Need both start and end. Use: `00:01:00 00:02:30`"
                )
                return
        except ListenerTimeout:
            task.status = TaskStatus.FAILED
            await client.send_message(m.chat.id, "⌛ Timed out.")
            cleanup_task_files(task)
            remove_task(task.task_id)
            return

    action = "KEEP" if task.selected_tool == "tv" else "REMOVE"
    await client.send_message(
        m.chat.id,
        f"⏱️ **{action} segment:**\n"
        f"Start: `{task.start_time}`\n"
        f"End: `{task.end_time}`"
    )


async def _config_audio_format(client, m, task):
    """RV tool: ask for output audio format."""
    await client.send_message(
        m.chat.id, "🎵 **Choose output audio format:**",
        reply_markup=_audio_format_keyboard("mp3"),
    )
    while task.audio_format is None:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)


def _build_task_summary(task: LeechTask) -> str:
    """Build a summary string for the done-confirmation prompt."""
    tool_label = AVAILABLE_TOOLS.get(task.selected_tool, task.selected_tool)
    lines = [f"🔧 **Tool:** {tool_label}"]

    if task.selected_tool == "va" and task.audio_mode:
        lines.append(
            "🔇 Remove original audio" if task.audio_mode == 1
            else "🔊 Keep original audio"
        )
    elif task.selected_tool == "vs" and task.subtitle_mode:
        lines.append(
            "🔥 Hardcode subtitles" if task.subtitle_mode == 1
            else "📝 Soft subtitles"
        )
    elif task.selected_tool == "cv" and task.compress_mode:
        mode_labels = {
            1: "🟢 High Quality", 2: "🟡 Balanced", 3: "🔴 High Compression",
            4: f"🎯 Target: {task.target_size_mb}MB",
            5: f"⚙️ Custom CRF: {task.custom_crf}",
        }
        lines.append(mode_labels.get(task.compress_mode, ""))
    elif task.selected_tool == "wv":
        if task.watermark_type == "text":
            lines.append(f"📝 Text: \"{task.watermark_text}\"")
        else:
            lines.append("🖼️ Image watermark")
        mode_labels = {
            1: "Static", 2: "Fade In", 3: "Fade In+Out",
            4: "Moving", 5: "Bouncing", 6: "Floating",
            7: "Scrolling", 8: "Pulsing",
        }
        lines.append(f"🎬 Mode: {mode_labels.get(task.watermark_mode, '')}")
        lines.append(f"📍 Position: {task.watermark_position or 'br'}")
    elif task.selected_tool in ("tv", "cut"):
        if task.start_time:
            lines.append(f"⏱️ Start: {task.start_time}")
        if task.end_time:
            lines.append(f"⏱️ End: {task.end_time}")
        if task.selected_tool == "cut":
            lines.append("🗑️ Segment between timestamps will be REMOVED")
        else:
            lines.append("✂️ Segment between timestamps will be KEPT")
    elif task.selected_tool == "rv" and task.audio_format:
        lines.append(f"🎵 Output format: {task.audio_format}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  CALLBACKS
# ═══════════════════════════════════════════════════════════════

@StreamBot.on_callback_query(filters.regex(r"^leech_tool_"), group=11)
async def leech_tool_callback(client: Client, cb: CallbackQuery):
    data = cb.data
    user_id = cb.from_user.id
    task = None
    for t in ACTIVE_LEECH_TASKS.values():
        if t.user_id == user_id and t.status in (
            TaskStatus.TOOL_SELECTION_PENDING, TaskStatus.WAITING_FOR_DONE,
        ):
            task = t
            break
    if not task:
        await cb.answer("No active task found.", show_alert=True)
        return

    if data == "leech_tool_cancel":
        await cb.answer("Cancelling...")
        await cancel_task(task)
        remove_task(task.task_id)
        await cb.message.edit_text(f"🚫 Task `{task.task_id}` cancelled.")
        return

    tool_key = data.replace("leech_tool_", "")
    if tool_key not in AVAILABLE_TOOLS:
        await cb.answer("Unknown tool.", show_alert=True)
        return

    task.selected_tool = tool_key
    await cb.answer(f"Selected: {AVAILABLE_TOOLS[tool_key]}")
    if task.status == TaskStatus.TOOL_SELECTION_PENDING:
        task.status = TaskStatus.WAITING_FOR_DONE
    try:
        await cb.message.edit_reply_markup(reply_markup=_tool_selection_keyboard(tool_key))
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_amode_"), group=12)
async def audio_mode_callback(client: Client, cb: CallbackQuery):
    task = _find_task(cb.from_user.id, tool="va", field="audio_mode")
    if not task:
        await cb.answer("No active task.", show_alert=True)
        return
    mode = int(cb.data.replace("leech_amode_", ""))
    if mode not in (1, 2):
        await cb.answer("Invalid.", show_alert=True)
        return
    task.audio_mode = mode
    label = "🔇 Remove original + add uploaded" if mode == 1 else "🔊 Keep original + add uploaded"
    await cb.answer(f"Mode {mode}")
    try:
        await cb.message.edit_text(f"🎵 **Audio Mode:** {label}")
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_smode_"), group=12)
async def subtitle_mode_callback(client: Client, cb: CallbackQuery):
    task = _find_task(cb.from_user.id, tool="vs", field="subtitle_mode")
    if not task:
        await cb.answer("No active task.", show_alert=True)
        return
    mode = int(cb.data.replace("leech_smode_", ""))
    if mode not in (1, 2):
        await cb.answer("Invalid.", show_alert=True)
        return
    task.subtitle_mode = mode
    label = "🔥 Hardcode" if mode == 1 else "📝 Soft subtitles"
    await cb.answer(f"Mode: {label}")
    try:
        await cb.message.edit_text(f"📝 **Subtitle Mode:** {label}")
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_cmode_"), group=12)
async def compress_mode_callback(client: Client, cb: CallbackQuery):
    task = _find_task(cb.from_user.id, tool="cv", field="compress_mode")
    if not task:
        await cb.answer("No active task.", show_alert=True)
        return
    mode = int(cb.data.replace("leech_cmode_", ""))
    if mode not in range(1, 6):
        await cb.answer("Invalid.", show_alert=True)
        return
    task.compress_mode = mode
    labels = {
        1: "🟢 High Quality", 2: "🟡 Balanced", 3: "🔴 High Compression",
        4: "🎯 Target File Size", 5: "⚙️ Custom CRF",
    }
    await cb.answer(labels.get(mode, ""))
    try:
        await cb.message.edit_text(f"📦 **Compression:** {labels.get(mode, '')}")
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_wmode_"), group=12)
async def watermark_mode_callback(client: Client, cb: CallbackQuery):
    task = _find_task(cb.from_user.id, tool="wv", field="watermark_mode")
    if not task:
        await cb.answer("No active task.", show_alert=True)
        return
    mode = int(cb.data.replace("leech_wmode_", ""))
    if mode not in range(1, 9):
        await cb.answer("Invalid.", show_alert=True)
        return
    task.watermark_mode = mode
    labels = {
        1: "Static", 2: "Fade In", 3: "Fade In+Out", 4: "Moving",
        5: "Bouncing", 6: "Floating", 7: "Scrolling", 8: "Pulsing",
    }
    await cb.answer(f"Mode: {labels.get(mode, '')}")
    try:
        await cb.message.edit_text(f"🎬 **Watermark Mode:** {labels.get(mode, '')}")
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_wpos_"), group=12)
async def watermark_pos_callback(client: Client, cb: CallbackQuery):
    task = _find_task(cb.from_user.id, tool="wv", field="watermark_position")
    if not task:
        await cb.answer("No active task.", show_alert=True)
        return
    pos = cb.data.replace("leech_wpos_", "")
    if pos not in ("tl", "tr", "bl", "br", "center"):
        await cb.answer("Invalid.", show_alert=True)
        return
    task.watermark_position = pos
    await cb.answer(f"Position: {pos}")
    try:
        await cb.message.edit_text(f"📍 **Position:** {pos.upper()}")
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_afmt_"), group=12)
async def audio_format_callback(client: Client, cb: CallbackQuery):
    """Handle audio format selection for RV tool."""
    task = _find_task(cb.from_user.id, tool="rv", field="audio_format")
    if not task:
        await cb.answer("No active task.", show_alert=True)
        return
    fmt = cb.data.replace("leech_afmt_", "")
    if fmt not in ("mp3", "aac", "wav", "copy"):
        await cb.answer("Invalid.", show_alert=True)
        return
    task.audio_format = fmt
    await cb.answer(f"Format: {fmt}")
    try:
        await cb.message.edit_text(f"🎵 **Audio Format:** {fmt.upper()}")
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_subpick_"), group=12)
async def subtitle_pick_callback(client: Client, cb: CallbackQuery):
    """Handle which subtitle to hardcode."""
    task = None
    for t in ACTIVE_LEECH_TASKS.values():
        if t.user_id == cb.from_user.id and t.selected_tool == "vs" and t.subtitle_mode == 1:
            if t.hardcode_sub_index is None:
                task = t
                break
    if not task:
        await cb.answer("No active task.", show_alert=True)
        return
    idx = int(cb.data.replace("leech_subpick_", ""))
    task.hardcode_sub_index = idx
    await cb.answer(f"Subtitle {idx + 1} selected")
    try:
        await cb.message.edit_text(f"📝 **Burning subtitle #{idx + 1}**")
    except Exception:
        pass


def _find_task(user_id: int, tool: str, field: str):
    """Find active task for a user matching tool with a None field."""
    for t in ACTIVE_LEECH_TASKS.values():
        if t.user_id == user_id and t.selected_tool == tool:
            if getattr(t, field, "SET") is None:
                return t
    return None


# ═══════════════════════════════════════════════════════════════
#  /cancel COMMAND
# ═══════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client: Client, m: Message):
    parts = m.text.strip().split()
    if len(parts) < 2:
        await m.reply_text("❌ Usage: `/cancel TASK_ID`", quote=True)
        return

    task_id = parts[1].lower()
    task = get_task(task_id)
    if not task:
        await m.reply_text(f"❌ Task `{task_id}` not found.", quote=True)
        return
    if task.user_id != m.from_user.id and m.from_user.id not in Var.OWNER_ID:
        await m.reply_text("❌ Not your task.", quote=True)
        return
    if task.status == TaskStatus.COMPLETED:
        await m.reply_text(f"ℹ️ Task `{task_id}` already completed.", quote=True)
        return
    if task.status in (TaskStatus.CANCELLED, TaskStatus.CANCELLING):
        await m.reply_text(f"ℹ️ Task `{task_id}` already cancelled.", quote=True)
        return

    success = await cancel_task(task)
    await m.reply_text(
        f"✅ Task `{task_id}` cancelled." if success
        else f"❌ Could not cancel `{task_id}` ({task.status.value}).",
        quote=True,
    )


async def _handle_inline_cancel(client, msg, task):
    parts = msg.text.strip().split()
    if len(parts) >= 2:
        target_id = parts[1].lower()
        if target_id != task.task_id:
            other = get_task(target_id)
            if other:
                await cancel_task(other)
                remove_task(target_id)
                await client.send_message(msg.chat.id, f"✅ Task `{target_id}` cancelled.")
            else:
                await client.send_message(msg.chat.id, f"❌ Task `{target_id}` not found.")
            return
    await cancel_task(task)
    remove_task(task.task_id)
    await client.send_message(msg.chat.id, f"✅ Task `{task.task_id}` cancelled.")
