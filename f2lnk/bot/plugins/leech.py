# f2lnk/bot/plugins/leech.py
# /leech workflow — reply-based file tagging.
#
# How it works:
#   1. User sends multiple video files to bot (bot processes them as usual).
#   2. User swipes back to the FIRST file and REPLIES to it with:
#        /leech -i 3 -m sample.mp4 -vt
#   3. Bot reads the replied-to message (= file 1), then auto-finds
#      the next (i-1) media messages sent by the same user after it.
#   4. Tool selection → "done" → staggered downloads → merge → upload.

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


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

def _parse_leech_args(text: str):
    """
    Parse /leech flags:
      -i N       → file count (int)
      -m NAME    → output file name
      -vt        → suggested tool flag
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

    if "-vt" in text:
        suggested_tool = "vt"

    if not file_count or file_count < 2:
        raise ValueError("File count (-i) must be at least 2.")
    if not output_name:
        raise ValueError("Output name (-m) is required.")

    return file_count, output_name, suggested_tool


def _is_media_message(msg: Message) -> bool:
    """Check if a message contains downloadable media."""
    return bool(msg and (msg.video or msg.document or msg.audio))


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


async def _find_consecutive_files(
    client: Client, chat_id: int, first_msg_id: int,
    user_id: int, total_needed: int
) -> list:
    """
    Starting from first_msg_id (inclusive), scan forward through the chat
    and collect `total_needed` media messages from the same user.

    Uses get_messages with a range of IDs, filters for media from user.
    """
    collected = []

    # Request messages in batches. Bot replies are interleaved, so we
    # scan a range wider than total_needed.
    # E.g., for 10 files, bot replies double the messages → scan ~30 IDs.
    scan_range = total_needed * 4
    start_id = first_msg_id
    attempts = 0
    max_attempts = 5  # safety: don't scan forever

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
            # Skip empty/invalid messages
            if not msg or msg.empty:
                continue
            # Only pick media messages from the same user
            if msg.from_user and msg.from_user.id == user_id and _is_media_message(msg):
                # Avoid duplicates
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
      /leech -i 3 -m sample.mp4 -vt

    Bot tags that file as #1, then auto-finds the next 2 media files
    from the same user after it.
    """

    # ── Must be a reply to a file ──
    if not m.reply_to_message:
        await m.reply_text(
            "❌ **Please reply to the first file.**\n\n"
            "1️⃣ Send your video files to the bot.\n"
            "2️⃣ Swipe to the **first** file and reply to it with:\n"
            "   `/leech -i <count> -m <output_name> -vt`",
            quote=True,
        )
        return

    replied = m.reply_to_message
    if not _is_media_message(replied):
        await m.reply_text(
            "❌ The replied message is not a video/document/audio file.\n"
            "Please reply to a **media file**.",
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
            f"`/leech -i <count> -m <output_name> -vt`",
            quote=True,
        )
        return

    # ── Find consecutive files starting from the replied message ──
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
            f"Please make sure you sent at least {file_count} files "
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

    # Wait for callback
    while task.status == TaskStatus.TOOL_SELECTION_PENDING:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)

    if task.status == TaskStatus.CANCELLED:
        return

    # ── Done or Change Tool ──
    task.status = TaskStatus.WAITING_FOR_DONE
    try:
        while True:
            if task.cancel_event.is_set():
                return

            done_msg = await client.ask(
                chat_id=m.chat.id,
                text=(
                    f"🔧 **Selected tool:** "
                    f"{AVAILABLE_TOOLS.get(task.selected_tool, task.selected_tool)}\n\n"
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
        return index, path

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
    for res in results:
        if isinstance(res, Exception):
            download_errors.append(str(res))
        elif isinstance(res, tuple):
            idx, path = res
            if path:
                task.download_paths.append((idx, path))
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

    task.download_paths.sort(key=lambda x: x[0])

    await status_msg.edit_text(
        f"✅ All **{file_count}** files downloaded!\n"
        f"⏳ Starting merge..."
    )

    # ── Merge ──
    task.status = TaskStatus.MERGING

    concat_file = os.path.join(task.work_dir, "concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for _idx, path in task.download_paths:
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
        return

    if task.cancel_event.is_set():
        return

    if proc.returncode != 0:
        task.status = TaskStatus.FAILED
        err = stderr.decode("utf-8", errors="replace")[:1000]
        await status_msg.edit_text(
            f"❌ **Merge failed** for task `{task_id}`:\n```\n{err}\n```"
        )
        cleanup_task_files(task)
        remove_task(task_id)
        return

    # ── Upload ──
    task.status = TaskStatus.UPLOADING
    await status_msg.edit_text(f"⬆️ **Uploading** `{task.output_name}` ...")

    try:
        file_size = os.path.getsize(output_path)
        caption = (
            f"✅ **Merged Video**\n"
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
        remove_task(task_id)
        return

    task.status = TaskStatus.COMPLETED
    await status_msg.edit_text(
        f"🎉 **Task `{task_id}` completed!**\n"
        f"📁 `{task.output_name}` uploaded successfully."
    )
    cleanup_task_files(task)


# ═══════════════════════════════════════════════════════════════════
#  TOOL SELECTION CALLBACK
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


# ═══════════════════════════════════════════════════════════════════
#  /cancel COMMAND
# ═══════════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client: Client, m: Message):
    parts = m.text.strip().split()
    if len(parts) < 2:
        await m.reply_text(
            "❌ Please provide a task ID.\n"
            "Usage: `/cancel TASK_ID`",
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
        await m.reply_text(
            f"ℹ️ Task `{task_id}` has already completed.", quote=True
        )
        return

    if task.status in (TaskStatus.CANCELLED, TaskStatus.CANCELLING):
        await m.reply_text(
            f"ℹ️ Task `{task_id}` is already cancelled.", quote=True
        )
        return

    if task.status == TaskStatus.FAILED:
        await m.reply_text(
            f"ℹ️ Task `{task_id}` has already failed.", quote=True
        )
        return

    success = await cancel_task(task)
    if success:
        await m.reply_text(
            f"✅ Task `{task_id}` has been cancelled.", quote=True
        )
    else:
        await m.reply_text(
            f"❌ Could not cancel task `{task_id}` "
            f"(state: {task.status.value}).",
            quote=True,
        )


# ═══════════════════════════════════════════════════════════════════
#  INLINE CANCEL HELPER
# ═══════════════════════════════════════════════════════════════════

async def _handle_inline_cancel(client: Client, msg: Message, task: LeechTask):
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
