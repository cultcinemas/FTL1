# f2lnk/bot/plugins/restart.py
# /restart command + auto-restart watchdog (idle & health monitoring)

import os
import sys
import time
import asyncio
import logging
import shutil

from pyrogram import filters, Client
from pyrogram.types import Message

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.bot.task_manager import ACTIVE_LEECH_TASKS

logger = logging.getLogger(__name__)

# ── Activity tracker ──
_last_activity_time = time.time()
_restart_in_progress = False

# Config
CPU_THRESHOLD = 90.0           # percent
RAM_THRESHOLD = 90.0           # percent
IDLE_TIMEOUT = 86400           # 24 hours in seconds
HEALTH_CHECK_INTERVAL = 300    # check every 5 minutes


def touch_activity():
    """Call this whenever any user activity happens."""
    global _last_activity_time
    _last_activity_time = time.time()


async def _do_restart(client: Client, reason: str, chat_id: int = None):
    """
    Gracefully stop everything and exit.
    start.sh restart loop will bring the bot back up.
    """
    global _restart_in_progress
    if _restart_in_progress:
        return
    _restart_in_progress = True

    logger.info("RESTART triggered: %s", reason)

    # ── 1. Send ONE restart message ──
    restart_text = (
        "🔄 **Bot is restarting...**\n"
        f"📋 Reason: {reason}\n"
        "Please wait a few seconds."
    )
    if chat_id:
        try:
            await client.send_message(chat_id, restart_text)
        except Exception:
            pass
    # Also notify owner (if different from triggering chat)
    owner_id = Var.OWNER_ID[0] if Var.OWNER_ID else None
    if owner_id and owner_id != chat_id:
        try:
            await client.send_message(owner_id, restart_text)
        except Exception:
            pass

    # ── 2. Cancel all active leech tasks ──
    for task_id, task in list(ACTIVE_LEECH_TASKS.items()):
        try:
            task.cancel_event.set()
            logger.info("Cancelled task %s for restart", task_id)
        except Exception:
            pass

    # ── 3. Wait briefly for tasks to wind down ──
    await asyncio.sleep(3)

    # ── 4. Clean temp files ──
    for d in ["./downloads", "./leech_tasks", "./vt_temp", "./zip_temp", "./mediainfo_temp"]:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d, ignore_errors=True)
                logger.info("Cleaned temp dir: %s", d)
            except Exception:
                pass

    # ── 5. Exit process — start.sh loop will restart us ──
    logger.info("Exiting process for restart...")
    await asyncio.sleep(1)

    # os._exit bypasses cleanup handlers that might hang
    os._exit(0)


# ═══════════════════════════════════════════════════════════════
#  /restart COMMAND (owner only)
# ═══════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("restart") & filters.private)
async def restart_command(client: Client, m: Message):
    """Restart the bot. Owner only."""
    touch_activity()

    if m.from_user.id not in Var.OWNER_ID:
        await m.reply_text("❌ **Only bot owner can restart.**", quote=True)
        return

    await _do_restart(client, f"Manual restart by user {m.from_user.id}", m.chat.id)


# ═══════════════════════════════════════════════════════════════
#  AUTO-RESTART WATCHDOG (background task)
# ═══════════════════════════════════════════════════════════════

async def _watchdog_loop():
    """Background loop: monitors system health and inactivity."""
    # Wait 2 minutes after startup before first check
    await asyncio.sleep(120)

    while True:
        try:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            if _restart_in_progress:
                return

            active_count = len(ACTIVE_LEECH_TASKS)

            # ── Check system health (CPU/RAM) ──
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=2)
                ram = psutil.virtual_memory().percent
            except ImportError:
                cpu, ram = 0, 0
            except Exception:
                cpu, ram = 0, 0

            if (cpu > CPU_THRESHOLD or ram > RAM_THRESHOLD) and active_count == 0:
                logger.warning(
                    "Health: CPU=%.1f%%, RAM=%.1f%%. No tasks. Auto-restarting.",
                    cpu, ram,
                )
                await _do_restart(
                    StreamBot,
                    f"High resource usage (CPU: {cpu:.0f}%, RAM: {ram:.0f}%)",
                )
                return

            if cpu > CPU_THRESHOLD or ram > RAM_THRESHOLD:
                logger.warning(
                    "Health: CPU=%.1f%%, RAM=%.1f%%. %d tasks active, waiting for them to finish.",
                    cpu, ram, active_count,
                )

            # ── Check inactivity (24h) ──
            idle_seconds = time.time() - _last_activity_time
            if idle_seconds > IDLE_TIMEOUT and active_count == 0:
                hours = idle_seconds / 3600
                logger.info("Idle for %.1f hours. Auto-restarting.", hours)
                await _do_restart(
                    StreamBot,
                    f"Inactivity restart ({hours:.1f}h idle)",
                )
                return

        except Exception as e:
            logger.error("Watchdog error: %s", e)


def start_watchdog():
    """Start the watchdog background task. Called from __main__.py."""
    loop = asyncio.get_event_loop()
    loop.create_task(_watchdog_loop())
    logger.info(
        "Watchdog started: health check every %ds, idle timeout %ds",
        HEALTH_CHECK_INTERVAL, IDLE_TIMEOUT,
    )
    
