# f2lnk/bot/task_manager.py
# Central task state management for the /leech workflow.

import os
import time
import string
import random
import shutil
import asyncio
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────── Task States ────────────────────

class TaskStatus(str, Enum):
    IDLE                   = "IDLE"
    COMMAND_RECEIVED       = "COMMAND_RECEIVED"
    COLLECTING_FILES       = "COLLECTING_FILES"
    TOOL_SELECTION_PENDING = "TOOL_SELECTION_PENDING"
    WAITING_FOR_DONE       = "WAITING_FOR_DONE"
    DOWNLOADING            = "DOWNLOADING"
    MERGING                = "MERGING"
    UPLOADING              = "UPLOADING"
    COMPLETED              = "COMPLETED"
    CANCELLING             = "CANCELLING"
    CANCELLED              = "CANCELLED"
    FAILED                 = "FAILED"

# States that allow cancellation
CANCELLABLE_STATES = {
    TaskStatus.COMMAND_RECEIVED,
    TaskStatus.COLLECTING_FILES,
    TaskStatus.TOOL_SELECTION_PENDING,
    TaskStatus.WAITING_FOR_DONE,
    TaskStatus.DOWNLOADING,
    TaskStatus.MERGING,
    TaskStatus.UPLOADING,
}

# ──────────────────── Available Tools ────────────────────

AVAILABLE_TOOLS = {
    "vt": "Video + Video Merge (-vt)",
}
DEFAULT_TOOL = "vt"

# ──────────────────── Task Object ────────────────────

TASKS_ROOT = "./tasks"

@dataclass
class LeechTask:
    task_id: str
    user_id: int
    chat_id: int
    file_count: int                              # -i value
    output_name: str                             # -m value (with .mp4 guaranteed)
    suggested_tool: str = DEFAULT_TOOL           # from command flags
    selected_tool: Optional[str] = None          # confirmed by user
    status: TaskStatus = TaskStatus.COMMAND_RECEIVED

    # File tracking
    file_messages: list = field(default_factory=list)      # stored Telegram message objects
    download_paths: list = field(default_factory=list)     # local paths after download

    # Worker tracking
    download_tasks: list = field(default_factory=list)     # asyncio.Task objects
    merge_process: Optional[asyncio.subprocess.Process] = None

    # Internal
    work_dir: str = ""
    created_at: float = field(default_factory=time.time)
    cancel_event: Optional[asyncio.Event] = None  # set when cancel requested

    def __post_init__(self):
        # Ensure output name ends with .mp4
        if not os.path.splitext(self.output_name)[1]:
            self.output_name += ".mp4"
        # Create isolated task directory
        self.work_dir = os.path.join(TASKS_ROOT, self.task_id)
        os.makedirs(self.work_dir, exist_ok=True)
        # Cancel event
        self.cancel_event = asyncio.Event()

    @property
    def is_cancellable(self) -> bool:
        return self.status in CANCELLABLE_STATES

    @property
    def downloads_started(self) -> int:
        return len(self.download_tasks)

    @property
    def downloads_completed(self) -> int:
        return sum(1 for t in self.download_tasks if t.done() and not t.cancelled())

# ──────────────────── Global Registry ────────────────────

# key = task_id (str), value = LeechTask
ACTIVE_LEECH_TASKS: Dict[str, LeechTask] = {}


def generate_task_id(length: int = 6) -> str:
    """Generate a unique alphanumeric task ID."""
    chars = string.ascii_lowercase + string.digits
    while True:
        tid = "".join(random.choices(chars, k=length))
        if tid not in ACTIVE_LEECH_TASKS:
            return tid


def get_task(task_id: str) -> Optional[LeechTask]:
    """Look up a task by its ID (case-insensitive)."""
    return ACTIVE_LEECH_TASKS.get(task_id.lower())


def register_task(task: LeechTask) -> None:
    """Add a task to the global registry."""
    ACTIVE_LEECH_TASKS[task.task_id] = task
    logger.info("Registered task %s for user %s", task.task_id, task.user_id)


def remove_task(task_id: str) -> None:
    """Remove a task from the registry (does NOT clean files)."""
    ACTIVE_LEECH_TASKS.pop(task_id.lower(), None)


async def cancel_task(task: LeechTask) -> bool:
    """
    Cancel a running task.  Returns True on success.
    Handles: stopping downloads, killing merge, cleaning files.
    """
    if not task.is_cancellable:
        return False

    task.status = TaskStatus.CANCELLING
    task.cancel_event.set()

    # 1. Cancel all download asyncio tasks
    for t in task.download_tasks:
        if not t.done():
            t.cancel()
    # Give cancelled tasks a moment to finalize
    if task.download_tasks:
        await asyncio.gather(*task.download_tasks, return_exceptions=True)

    # 2. Kill merge subprocess
    if task.merge_process and task.merge_process.returncode is None:
        try:
            task.merge_process.kill()
            await task.merge_process.wait()
        except Exception as e:
            logger.warning("Error killing merge process for task %s: %s", task.task_id, e)

    # 3. Clean up task folder
    cleanup_task_files(task)

    task.status = TaskStatus.CANCELLED
    logger.info("Task %s cancelled successfully.", task.task_id)
    return True


def cleanup_task_files(task: LeechTask) -> None:
    """Delete the task's working directory."""
    if task.work_dir and os.path.exists(task.work_dir):
        try:
            shutil.rmtree(task.work_dir, ignore_errors=True)
            logger.info("Cleaned up files for task %s", task.task_id)
        except Exception as e:
            logger.warning("Cleanup error for task %s: %s", task.task_id, e)
