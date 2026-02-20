# f2lnk/bot/plugins/leech.py
# /leech workflow — interactive confirmation, staggered parallel downloads,
# multi-file merge, and task-based cancellation.

import os
import re
import asyncio
import shutil
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
      -m NAME    → output file name (str)
      -vt        → suggested tool flag
    Returns (file_count, output_name, suggested_tool) or raises ValueError.
    """
    file_count = None
    output_name = None
    suggested_tool = DEFAULT_TOOL

    # -i <number>
    m = re.search(r"-i\s+(\d+)", text)
    if m:
        file_count = int(m.group(1))

    # -m <name>  (captures next non-flag token, may include extension)
    m = re.search(r"-m\s+(\S+)", text)
    if m:
        output_name = m.group(1)

    # -vt flag (anywhere in text)
    if "-vt" in text:
        suggested_tool = "vt"

    if not file_count or file_count < 2:
        raise ValueError("File count (-i) must be at least 2.")
    if not output_name:
        raise ValueError("Output name (-m) is required.")

    return file_count, output_name, suggested_tool


async def _run_cmd(cmd: list, timeout: int = 600):
    """Run a subprocess and return (process, returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return proc, -1, b"", b"Process timed out."
    return proc, proc.returncode, stdout, stderr


def _tool_selection_keyboard(current: str = None):
    """Build inline keyboard for tool selection."""
    buttons = []
    for key, label in AVAILABLE_TOOLS.items():
        prefix = "✅ " if key == current else ""
        buttons.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"leech_tool_{key}")]
        )
    buttons.append([InlineKeyboardButton("❌ Cancel Task", callback_data="leech_tool_cancel")])
    return InlineKeyboardMarkup(buttons)


# ═══════════════════════════════════════════════════════════════════
#  /leech COMMAND
# ═══════════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("leech") & filters.private)
async def leech_command(client: Client, m: Message):
    """
    Entry point:  /leech -i 10 -m sample.mp4 -vt
    """
    # ── Step 2: Parse command ──
    try:
        file_count, output_name, suggested_tool = _parse_leech_args(m.text)
    except ValueError as e:
        await m.reply_text(
            f"❌ **Invalid command.**\n`{e}`\n\n"
            f"**Usage:** `/leech -i <count> -m <output_name> -vt`\n"
            f"Example: `/leech -i 10 -m sample.mp4 -vt`",
            quote=True,
        )
        return

    # ── Step 3: Generate task ID ──
    task_id = generate_task_id()
    task = LeechTask(
        task_id=task_id,
        user_id=m.from_user.id,
        chat_id=m.chat.id,
        file_count=file_count,
        output_name=output_name,
        suggested_tool=suggested_tool,
    )
    register_task(task)

    await m.reply_text(
        f"🆔 **Task ID:** `{task_id}`\n"
        f"Use `/cancel {task_id}` to stop this task anytime.\n\n"
        f"📂 **Files expected:** {file_count}\n"
        f"📝 **Output name:** `{task.output_name}`",
        quote=True,
    )

    # ── Step 1 (collect files): Ask user to send files one by one ──
    task.status = TaskStatus.COLLECTING_FILES
    collected = 0
    try:
        while collected < file_count:
            if task.cancel_event.is_set():
                return  # cancelled during collection

            prompt = (
                f"📤 Send file **{collected + 1}** of **{file_count}**\n"
                f"(or send /cancel {task_id} to abort)"
            )
            file_msg = await client.ask(
                chat_id=m.chat.id,
                text=prompt,
                timeout=300,  # 5 minutes per file
            )

            # Check if user sent a cancel command inline
            if file_msg.text and file_msg.text.strip().startswith("/cancel"):
                await _handle_inline_cancel(client, file_msg, task)
                return

            # Validate it's a media file
            if not (file_msg.video or file_msg.document or file_msg.audio):
                await client.send_message(
                    m.chat.id,
                    "⚠️ Please send a valid **video/document/audio** file. Try again.",
                )
                continue  # don't increment, ask again

            task.file_messages.append(file_msg)
            collected += 1

    except ListenerTimeout:
        task.status = TaskStatus.FAILED
        cleanup_task_files(task)
        remove_task(task_id)
        await client.send_message(m.chat.id, f"⌛ Task `{task_id}` timed out during file collection.")
        return

    if task.cancel_event.is_set():
        return

    # ── Step 4: Tool selection ──
    task.status = TaskStatus.TOOL_SELECTION_PENDING
    tool_msg = await client.send_message(
        m.chat.id,
        f"✅ All **{file_count}** files received!\n\n"
        f"**Select Tool:**",
        reply_markup=_tool_selection_keyboard(suggested_tool),
    )

    # Wait for tool selection via callback (handled by leech_tool_callback below).
    # We use a simple polling loop checking the task state.
    while task.status == TaskStatus.TOOL_SELECTION_PENDING:
        if task.cancel_event.is_set():
            return
        await asyncio.sleep(0.5)

    if task.status == TaskStatus.CANCELLED:
        return

    # ── Step 5: Done or Change Tool confirmation ──
    task.status = TaskStatus.WAITING_FOR_DONE
    try:
        while True:
            if task.cancel_event.is_set():
                return

            done_msg = await client.ask(
                chat_id=m.chat.id,
                text=(
                    f"🔧 **Selected tool:** {AVAILABLE_TOOLS.get(task.selected_tool, task.selected_tool)}\n\n"
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
            # If they click a button, the callback handler will update selected_tool
            # and we loop again to ask for "done"

    except ListenerTimeout:
        task.status = TaskStatus.FAILED
        cleanup_task_files(task)
        remove_task(task_id)
        await client.send_message(m.chat.id, f"⌛ Task `{task_id}` timed out. Cleaned up.")
        return

    if task.cancel_event.is_set():
        return

    # ── Step 6: Staggered parallel downloads ──
    task.status = TaskStatus.DOWNLOADING
    status_msg = await client.send_message(
        m.chat.id,
        f"⬇️ **Downloading {file_count} files** (staggered, 5s apart)...\n"
        f"Task: `{task_id}`",
    )

    async def _download_one(index: int, file_msg: Message):
        """Download a single file from Telegram to the task directory."""
        path = await client.download_media(
            file_msg,
            file_name=os.path.join(task.work_dir, ""),
        )
        return index, path

    # Start downloads with 5-second stagger
    for i, fmsg in enumerate(task.file_messages):
        if task.cancel_event.is_set():
            break
        dl_task = asyncio.create_task(_download_one(i, fmsg))
        task.download_tasks.append(dl_task)
        logger.info("Task %s: started download %d/%d", task_id, i + 1, file_count)

        # Update progress
        try:
            await status_msg.edit_text(
                f"⬇️ **Downloading files** — Task: `{task_id}`\n"
                f"📥 Started: {i + 1}/{file_count}\n"
                f"✅ Completed: {task.downloads_completed}/{file_count}"
            )
        except Exception:
            pass  # message edit rate limit

        # Stagger: wait 5 seconds before starting next (except for last one)
        if i < file_count - 1:
            for _ in range(STAGGER_DELAY):
                if task.cancel_event.is_set():
                    break
                await asyncio.sleep(1)

    if task.cancel_event.is_set():
        return

    # ── Step 8: Wait for all downloads to complete ──
    try:
        results = await asyncio.gather(*task.download_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        return

    if task.cancel_event.is_set():
        return

    # Collect download paths, check for errors
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

    # Sort by original index to maintain order
    task.download_paths.sort(key=lambda x: x[0])

    await status_msg.edit_text(
        f"✅ All **{file_count}** files downloaded!\n"
        f"⏳ Starting merge..."
    )

    # ── Step 9: Merge process ──
    task.status = TaskStatus.MERGING

    # Create concat file for ffmpeg
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

    # ── Step 10: Upload final output ──
    task.status = TaskStatus.UPLOADING
    await status_msg.edit_text(
        f"⬆️ **Uploading** `{task.output_name}` ..."
    )

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

    # ── Completion ──
    task.status = TaskStatus.COMPLETED
    await status_msg.edit_text(
        f"🎉 **Task `{task_id}` completed!**\n"
        f"📁 `{task.output_name}` uploaded successfully."
    )
    cleanup_task_files(task)
    # Keep task in registry briefly so /cancel reports "already completed"
    # A background cleaner could remove it later if desired.


# ═══════════════════════════════════════════════════════════════════
#  TOOL SELECTION CALLBACK
# ═══════════════════════════════════════════════════════════════════

@StreamBot.on_callback_query(filters.regex(r"^leech_tool_"), group=11)
async def leech_tool_callback(client: Client, cb: CallbackQuery):
    data = cb.data  # e.g. "leech_tool_vt" or "leech_tool_cancel"

    # Find the task for this user that is in TOOL_SELECTION_PENDING or WAITING_FOR_DONE
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

    # If we were in TOOL_SELECTION_PENDING, advance to WAITING_FOR_DONE
    if task.status == TaskStatus.TOOL_SELECTION_PENDING:
        task.status = TaskStatus.WAITING_FOR_DONE

    # Update the keyboard to show current selection
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
    """
    /cancel TASK_ID
    """
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

    # Verify ownership (only task owner or bot owner can cancel)
    if task.user_id != m.from_user.id and m.from_user.id not in Var.OWNER_ID:
        await m.reply_text("❌ You don't have permission to cancel this task.", quote=True)
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
        await m.reply_text(f"❌ Could not cancel task `{task_id}` (state: {task.status.value}).", quote=True)


# ═══════════════════════════════════════════════════════════════════
#  INLINE CANCEL HELPER
# ═══════════════════════════════════════════════════════════════════

async def _handle_inline_cancel(client: Client, msg: Message, task: LeechTask):
    """Handle /cancel sent as a text message during interactive flow."""
    parts = msg.text.strip().split()
    if len(parts) >= 2:
        target_id = parts[1].lower()
        if target_id != task.task_id:
            # Cancelling a different task
            other = get_task(target_id)
            if other:
                await cancel_task(other)
                remove_task(target_id)
                await client.send_message(msg.chat.id, f"✅ Task `{target_id}` has been cancelled.")
            else:
                await client.send_message(msg.chat.id, f"❌ Task `{target_id}` not found.")
            return

    # Cancel current task
    await cancel_task(task)
    remove_task(task.task_id)
    await client.send_message(msg.chat.id, f"✅ Task `{task.task_id}` has been cancelled.")
