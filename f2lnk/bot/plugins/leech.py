# f2lnk/bot/plugins/leech.py
# /leech workflow — reply-based file tagging.
#
# Supported tools:
#   vt — Video + Video Merge (concat N videos)
#   va — Video + Audio Merge (1 video + N audios as separate streams)

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

logger = logging.getLogger(__name__)

STAGGER_DELAY = 5  # seconds between each download start

# File extensions for auto-detection
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".ts", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".aac", ".ogg", ".flac", ".wav", ".m4a", ".opus", ".wma", ".ac3", ".eac3", ".dts"}


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

def _parse_leech_args(text: str):
    """
    Parse /leech flags:
      -i N       → file count (int)
      -m NAME    → output file name
      -vt / -va  → suggested tool flag
    """
    file_count = None
    output_name = None
    suggested_tool = DEFAULT_TOOL

    m = re.search(r"-i\s+(\d+)", text)
    if m:
        file_count = int(m.group(1))

    m = re.search(r"-m\s+(.+?)(?:\s+-\w|$)", text)
    if m:
        output_name = m.group(1).strip()

    if "-va" in text:
        suggested_tool = "va"
    elif "-vt" in text:
        suggested_tool = "vt"

    if not file_count or file_count < 2:
        raise ValueError("File count (-i) must be at least 2.")
    if not output_name:
        raise ValueError("Output name (-m) is required.")

    return file_count, output_name, suggested_tool


def _is_media_message(msg: Message) -> bool:
    """Check if a message contains downloadable media."""
    return bool(msg and (msg.video or msg.document or msg.audio))


def _detect_file_type(msg: Message) -> str:
    """
    Detect whether a message is 'video' or 'audio'.
    Returns 'video', 'audio', or 'unknown'.
    """
    # Pyrogram typed attributes
    if msg.video:
        return "video"
    if msg.audio:
        return "audio"

    # For documents, check mime_type and file name
    if msg.document:
        mime = (msg.document.mime_type or "").lower()
        fname = (msg.document.file_name or "").lower()
        ext = os.path.splitext(fname)[1] if fname else ""

        if mime.startswith("video/") or ext in VIDEO_EXTENSIONS:
            return "video"
        if mime.startswith("audio/") or ext in AUDIO_EXTENSIONS:
            return "audio"

    return "unknown"


def _tool_selection_keyboard(current: str = None):
    """Build inline keyboard for tool selection."""
    buttons = []
    for key, label in AVAILABLE_TOOLS.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(
                f"{prefix}{label}", callback_data=f"leech_tool_{key}"
            )]
        )
    buttons.append(
        [InlineKeyboardButton("❌ Cancel Task", callback_data="leech_tool_cancel")]
    )
    return InlineKeyboardMarkup(buttons)


def _audio_mode_keyboard(current: int = None):
    """Build inline keyboard for audio mode selection (VA tool)."""
    modes = {
        1: "🔇 Remove original audio + Add uploaded audios",
        2: "🔊 Keep original audio + Add uploaded audios",
    }
    buttons = []
    for key, label in modes.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(
                f"{prefix}{label}", callback_data=f"leech_amode_{key}"
            )]
        )
    return InlineKeyboardMarkup(buttons)


async def _find_consecutive_files(
    client: Client, chat_id: int, first_msg_id: int,
    user_id: int, total_needed: int
) -> list:
    """
    Starting from first_msg_id (inclusive), scan forward through the chat
    and collect `total_needed` media messages from the same user.
    """
    collected = []
    scan_range = total_needed * 4
    start_id = first_msg_id
    attempts = 0
    max_attempts = 5

    while len(collected) < total_needed and attempts < max_attempts:
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


# ═══════════════════════════════════════════════════════════════════
#  /leech COMMAND
# ═══════════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("leech") & filters.private)
async def leech_command(client: Client, m: Message):
    """
    User replies to the FIRST file with:
      /leech -i 6 -m sample.mp4 -vt
    Bot tags that file + auto-finds the next (i-1) files.
    """

    # ── Must be a reply to a file ──
    if not m.reply_to_message:
        await m.reply_text(
            "❌ **Please reply to the first file.**\n\n"
            "1️⃣ Send your files to the bot.\n"
            "2️⃣ Swipe to the **first** file and reply with:\n"
            "   `/leech -i <count> -m <output_name> -vt`\n"
            "   `/leech -i <count> -m <output_name> -va`",
            quote=True,
        )
        return

    replied = m.reply_to_message
    if not _is_media_message(replied):
        await m.reply_text(
            "❌ The replied message is not a media file.\n"
            "Please reply to a **video/document/audio** file.",
            quote=True,
        )
        return

    # ── Parse command ──
    try:
        file_count, output_name, suggested_tool = _parse_leech_args(m.text)
    except ValueError as e:
        await m.reply_text(
            f"❌ **Invalid command.**\n`{e}`\n\n"
            f"**Usage:** Reply to first file with:\n"
            f"`/leech -i <count> -m <name> -vt` (video merge)\n"
            f"`/leech -i <count> -m <name> -va` (video+audio)",
            quote=True,
        )
        return

    # ── Find consecutive files ──
    wait_msg = await m.reply_text(
        f"🔍 Finding **{file_count}** files starting from the tagged message...",
        quote=True,
    )

    file_messages = await _find_consecutive_files(
        client, m.chat.id, replied.id, m.from_user.id, file_count
    )

    if len(file_messages) < file_count:
        await wait_msg.edit_text(
            f"❌ **Found only {len(file_messages)} media file(s)** "
            f"but you requested **-i {file_count}**.\n\n"
            f"Make sure you sent at least {file_count} files "
            f"after the tagged message, then try again."
        )
        return

    # ── Generate task ID ──
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
    register_task(task)

    await wait_msg.edit_text(
        f"🆔 **Task ID:** `{task_id}`\n"
        f"Use `/cancel {task_id}` to stop this task anytime.\n\n"
        f"📂 **Files tagged:** {file_count}\n"
        f"📝 **Output name:** `{task.output_name}`"
    )

    # ── Tool selection ──
    task.status = TaskStatus.TOOL_SELECTION_PENDING
    await client.send_message(
        m.chat.id,
        "**Select Tool:**",
        reply_markup=_tool_selection_keyboard(suggested_tool),
    )

    while task.status == TaskStatus.TOOL_SELECTION_PENDING:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)

    if task.status == TaskStatus.CANCELLED:
        return

    # ── Audio mode selection (VA tool only) ──
    if task.selected_tool == "va":
        await client.send_message(
            m.chat.id,
            "🎵 **Select Audio Mode:**",
            reply_markup=_audio_mode_keyboard(),
        )

        # Wait for audio mode callback
        while task.audio_mode is None:
            if task.cancel_event.is_set():
                return
            await asyncio.sleep(0.5)

    if task.cancel_event.is_set():
        return

    # ── Done or Change Tool ──
    task.status = TaskStatus.WAITING_FOR_DONE
    try:
        while True:
            if task.cancel_event.is_set():
                return

            tool_label = AVAILABLE_TOOLS.get(task.selected_tool, task.selected_tool)
            mode_label = ""
            if task.selected_tool == "va" and task.audio_mode:
                mode_label = (
                    "\n🔇 Mode: Remove original audio + add uploaded"
                    if task.audio_mode == 1
                    else "\n🔊 Mode: Keep original audio + add uploaded"
                )

            done_msg = await client.ask(
                chat_id=m.chat.id,
                text=(
                    f"🔧 **Selected tool:** {tool_label}{mode_label}\n\n"
                    f'Type **"done"** to start processing\n'
                    f"or select another tool below:"
                ),
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
        await client.send_message(
            m.chat.id, f"⌛ Task `{task_id}` timed out. Cleaned up."
        )
        return

    if task.cancel_event.is_set():
        return

    # ── Staggered parallel downloads ──
    task.status = TaskStatus.DOWNLOADING
    status_msg = await client.send_message(
        m.chat.id,
        f"⬇️ **Downloading {file_count} files** "
        f"(staggered, {STAGGER_DELAY}s apart)...\n"
        f"Task: `{task_id}`",
    )

    async def _download_one(index: int, file_msg: Message):
        path = await client.download_media(
            file_msg,
            file_name=os.path.join(task.work_dir, ""),
        )
        return index, path, file_msg

    for i, fmsg in enumerate(task.file_messages):
        if task.cancel_event.is_set():
            break

        dl_task = asyncio.create_task(_download_one(i, fmsg))
        task.download_tasks.append(dl_task)
        logger.info("Task %s: started download %d/%d", task_id, i + 1, file_count)

        try:
            await status_msg.edit_text(
                f"⬇️ **Downloading files** — Task: `{task_id}`\n"
                f"📥 Started: {i + 1}/{file_count}\n"
                f"✅ Completed: {task.downloads_completed}/{file_count}"
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

    # ── Wait for all downloads ──
    try:
        results = await asyncio.gather(*task.download_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        return

    if task.cancel_event.is_set():
        return

    download_errors = []
    downloaded_files = []  # list of (index, path, original_msg)
    for res in results:
        if isinstance(res, Exception):
            download_errors.append(str(res))
        elif isinstance(res, tuple):
            idx, path, orig_msg = res
            if path:
                downloaded_files.append((idx, path, orig_msg))
            else:
                download_errors.append(f"File {idx + 1} download returned None")

    if download_errors:
        task.status = TaskStatus.FAILED
        error_text = "\n".join(download_errors[:5])
        await status_msg.edit_text(
            f"❌ **Download errors for task `{task_id}`:**\n```\n{error_text}\n```"
        )
        cleanup_task_files(task)
        remove_task(task_id)
        return

    downloaded_files.sort(key=lambda x: x[0])

    await status_msg.edit_text(
        f"✅ All **{file_count}** files downloaded!\n"
        f"⏳ Starting processing..."
    )

    # ── Route to the correct tool processor ──
    task.status = TaskStatus.MERGING

    if task.selected_tool == "vt":
        success = await _process_video_video(task, downloaded_files, status_msg)
    elif task.selected_tool == "va":
        success = await _process_video_audio(client, task, downloaded_files, status_msg)
    else:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(f"❌ Unknown tool: `{task.selected_tool}`")
        cleanup_task_files(task)
        remove_task(task_id)
        return

    if not success or task.cancel_event.is_set():
        return

    # ── Upload ──
    output_path = os.path.join(task.work_dir, task.output_name)
    task.status = TaskStatus.UPLOADING
    await status_msg.edit_text(f"⬆️ **Uploading** `{task.output_name}` ...")

    try:
        file_size = os.path.getsize(output_path)
        caption = (
            f"✅ **Processed Video**\n"
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
        else:
            await client.send_document(
                task.chat_id, output_path, caption=caption,
                file_name=task.output_name,
            )
    except Exception as e:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(f"❌ Upload failed: `{e}`")
        cleanup_task_files(task)
        remove_task(task.task_id)
        return

    task.status = TaskStatus.COMPLETED
    await status_msg.edit_text(
        f"🎉 **Task `{task.task_id}` completed!**\n"
        f"📁 `{task.output_name}` uploaded successfully."
    )
    cleanup_task_files(task)


# ═══════════════════════════════════════════════════════════════════
#  TOOL: VIDEO + VIDEO MERGE (concat)
# ═══════════════════════════════════════════════════════════════════

async def _process_video_video(task, downloaded_files, status_msg) -> bool:
    """Concatenate multiple videos using ffmpeg concat demuxer."""
    concat_file = os.path.join(task.work_dir, "concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for _idx, path, _msg in downloaded_files:
            abs_path = os.path.abspath(path).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")

    output_path = os.path.join(task.work_dir, task.output_name)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file, "-c", "copy", output_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task.merge_process = proc
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        return False

    if task.cancel_event.is_set():
        return False

    if proc.returncode != 0:
        task.status = TaskStatus.FAILED
        err = stderr.decode("utf-8", errors="replace")[:1000]
        await status_msg.edit_text(
            f"❌ **Merge failed** for task `{task.task_id}`:\n```\n{err}\n```"
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    return True


# ═══════════════════════════════════════════════════════════════════
#  TOOL: VIDEO + AUDIO MERGE (multi-audio streams)
# ═══════════════════════════════════════════════════════════════════

async def _process_video_audio(client, task, downloaded_files, status_msg) -> bool:
    """
    Mux 1 video + N audio files into a single output with separate
    selectable audio streams.
    Mode 1: Remove original audio, add uploaded audios only.
    Mode 2: Keep original audio, add uploaded audios.
    """
    # ── Auto-detect video vs audio ──
    video_files = []
    audio_files = []

    for idx, path, msg in downloaded_files:
        ftype = _detect_file_type(msg)
        if ftype == "video":
            video_files.append((idx, path))
        elif ftype == "audio":
            audio_files.append((idx, path))
        else:
            # Fallback: guess from downloaded file extension
            ext = os.path.splitext(path)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                video_files.append((idx, path))
            elif ext in AUDIO_EXTENSIONS:
                audio_files.append((idx, path))
            else:
                audio_files.append((idx, path))  # default to audio

    # ── Validate ──
    if len(video_files) == 0:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **No video file found** in task `{task.task_id}`.\n"
            f"For Video+Audio merge, exactly 1 video file is required."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    if len(video_files) > 1:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **Multiple video files found** ({len(video_files)}) "
            f"in task `{task.task_id}`.\n"
            f"For Video+Audio merge, exactly 1 video file is required.\n"
            f"Use **Video+Video Merge (-vt)** to merge multiple videos."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    if len(audio_files) == 0:
        task.status = TaskStatus.FAILED
        await status_msg.edit_text(
            f"❌ **No audio files found** in task `{task.task_id}`.\n"
            f"At least 1 audio file is required."
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    video_path = video_files[0][1]
    audio_mode = task.audio_mode or 1

    await status_msg.edit_text(
        f"⚙️ **Processing** — Task: `{task.task_id}`\n"
        f"🎬 1 video + {len(audio_files)} audio(s)\n"
        f"{'🔇 Removing' if audio_mode == 1 else '🔊 Keeping'} original audio..."
    )

    # ── Build ffmpeg command ──
    # Inputs: -i video -i audio1 -i audio2 ...
    # Maps:   -map 0:v  (video stream from input 0)
    #         -map 0:a  (original audio — only in mode 2)
    #         -map 1:a  (audio from input 1)
    #         -map 2:a  (audio from input 2) ...

    output_path = os.path.join(task.work_dir, task.output_name)

    cmd = ["ffmpeg", "-y"]

    # Add all inputs
    cmd.extend(["-i", video_path])
    for _idx, apath in audio_files:
        cmd.extend(["-i", apath])

    # Map video stream
    cmd.extend(["-map", "0:v"])

    # Map original audio (mode 2 only)
    if audio_mode == 2:
        cmd.extend(["-map", "0:a?"])  # '?' = don't fail if no audio in source

    # Map each uploaded audio as a separate stream
    for i in range(len(audio_files)):
        input_index = i + 1  # input 0 is the video
        cmd.extend(["-map", f"{input_index}:a"])

    # Copy all streams without re-encoding (original quality preserved).
    # Force MKV container for multi-audio — MP4 has a known bug where
    # multiple audio streams from separate inputs share the same data,
    # causing all tracks to play the last audio only.
    cmd.extend(["-c", "copy"])

    # Force .mkv extension for proper multi-audio support
    output_base = os.path.splitext(task.output_name)[0]
    task.output_name = output_base + ".mkv"
    output_path = os.path.join(task.work_dir, task.output_name)

    logger.info("Task %s: ffmpeg cmd = %s", task.task_id, " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task.merge_process = proc
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        return False

    if task.cancel_event.is_set():
        return False

    if proc.returncode != 0:
        task.status = TaskStatus.FAILED
        err = stderr.decode("utf-8", errors="replace")[:1000]
        await status_msg.edit_text(
            f"❌ **Processing failed** for task `{task.task_id}`:\n```\n{err}\n```"
        )
        cleanup_task_files(task)
        remove_task(task.task_id)
        return False

    return True


# ═══════════════════════════════════════════════════════════════════
#  CALLBACKS
# ═══════════════════════════════════════════════════════════════════

@StreamBot.on_callback_query(filters.regex(r"^leech_tool_"), group=11)
async def leech_tool_callback(client: Client, cb: CallbackQuery):
    data = cb.data
    user_id = cb.from_user.id
    task = None
    for t in ACTIVE_LEECH_TASKS.values():
        if t.user_id == user_id and t.status in (
            TaskStatus.TOOL_SELECTION_PENDING,
            TaskStatus.WAITING_FOR_DONE,
        ):
            task = t
            break

    if not task:
        await cb.answer("No active task found.", show_alert=True)
        return

    if data == "leech_tool_cancel":
        await cb.answer("Cancelling task...")
        await cancel_task(task)
        remove_task(task.task_id)
        await cb.message.edit_text(f"🚫 Task `{task.task_id}` has been cancelled.")
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
        await cb.message.edit_reply_markup(
            reply_markup=_tool_selection_keyboard(tool_key)
        )
    except Exception:
        pass


@StreamBot.on_callback_query(filters.regex(r"^leech_amode_"), group=12)
async def leech_audio_mode_callback(client: Client, cb: CallbackQuery):
    """Handle audio mode selection for VA tool."""
    data = cb.data  # e.g. "leech_amode_1" or "leech_amode_2"
    user_id = cb.from_user.id

    task = None
    for t in ACTIVE_LEECH_TASKS.values():
        if t.user_id == user_id and t.selected_tool == "va" and t.audio_mode is None:
            task = t
            break

    if not task:
        await cb.answer("No active task found.", show_alert=True)
        return

    try:
        mode = int(data.replace("leech_amode_", ""))
    except ValueError:
        await cb.answer("Invalid mode.", show_alert=True)
        return

    if mode not in (1, 2):
        await cb.answer("Invalid mode.", show_alert=True)
        return

    task.audio_mode = mode
    mode_label = (
        "🔇 Remove original + add uploaded audios"
        if mode == 1
        else "🔊 Keep original + add uploaded audios"
    )
    await cb.answer(f"Mode {mode} selected!")
    try:
        await cb.message.edit_text(f"🎵 **Audio Mode:** {mode_label}")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  /cancel COMMAND
# ═══════════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client: Client, m: Message):
    parts = m.text.strip().split()
    if len(parts) < 2:
        await m.reply_text(
            "❌ Please provide a task ID.\nUsage: `/cancel TASK_ID`",
            quote=True,
        )
        return

    task_id = parts[1].lower()
    task = get_task(task_id)

    if not task:
        await m.reply_text(f"❌ Task `{task_id}` not found.", quote=True)
        return

    if task.user_id != m.from_user.id and m.from_user.id not in Var.OWNER_ID:
        await m.reply_text(
            "❌ You don't have permission to cancel this task.", quote=True
        )
        return

    if task.status == TaskStatus.COMPLETED:
        await m.reply_text(f"ℹ️ Task `{task_id}` has already completed.", quote=True)
        return
    if task.status in (TaskStatus.CANCELLED, TaskStatus.CANCELLING):
        await m.reply_text(f"ℹ️ Task `{task_id}` is already cancelled.", quote=True)
        return
    if task.status == TaskStatus.FAILED:
        await m.reply_text(f"ℹ️ Task `{task_id}` has already failed.", quote=True)
        return

    success = await cancel_task(task)
    if success:
        await m.reply_text(f"✅ Task `{task_id}` has been cancelled.", quote=True)
    else:
        await m.reply_text(
            f"❌ Could not cancel task `{task_id}` (state: {task.status.value}).",
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
                await client.send_message(
                    msg.chat.id, f"✅ Task `{target_id}` has been cancelled."
                )
            else:
                await client.send_message(
                    msg.chat.id, f"❌ Task `{target_id}` not found."
                )
            return

    await cancel_task(task)
    remove_task(task.task_id)
    await client.send_message(
        msg.chat.id, f"✅ Task `{task.task_id}` has been cancelled."
    )
