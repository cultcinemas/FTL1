# f2lnk/bot/plugins/restart.py
# /restart command + auto-restart watchdog (idle & health monitoring)

import os
import sys
import time
import asyncio
import logging
import psutil

from pyrogram import filters, Client
from pyrogram.types import Message

from f2lnk.bot import StreamBot
from f2lnk.vars import Var
from f2lnk.bot.task_manager import ACTIVE_LEECH_TASKS

logger = logging.getLogger(__name__)

# ── Activity tracker ──
_last_activity_time = time.time()
_restart_lock = asyncio.Lock()

# Config
MAX_PARALLEL_TASKS = 10        # trigger restart warning above this
CPU_THRESHOLD = 90.0           # percent
RAM_THRESHOLD = 90.0           # percent
IDLE_TIMEOUT = 86400           # 24 hours in seconds
HEALTH_CHECK_INTERVAL = 300    # check every 5 minutes


def touch_activity():
    """Call this whenever any user activity happens."""
    global _last_activity_time
    _last_activity_time = time.time()


async def _graceful_restart(client: Client, reason: str, notify_chat: int = None):
    """Gracefully stop tasks and restart the bot process."""
    async with _restart_lock:
        logger.info("Restart triggered: %s", reason)

        # Notify
        restart_msg = (
            "🔄 **Bot is restarting...**\n"
            f"📋 Reason: {reason}\n"
            "Please wait a few seconds."
        )

        if notify_chat:
            try:
                await client.send_message(notify_chat, restart_msg)
            except Exception:
                pass

        # Notify owner
        try:
            await client.send_message(
                Var.OWNER_ID[0],
                f"⚠️ **Bot Restart**\n"
                f"📋 Reason: {reason}\n"
                f"🕐 Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
            )
        except Exception:
            pass

        # Cancel all active leech tasks
        for task_id, task in list(ACTIVE_LEECH_TASKS.items()):
            try:
                task.cancel_event.set()
                logger.info("Cancelled task %s for restart", task_id)
            except Exception:
                pass

        # Wait briefly for tasks to stop
        await asyncio.sleep(2)

        # Log and restart
        logger.info("Restarting bot process...")

        try:
            await client.stop()
        except Exception:
            pass

        # Restart the process
        os.execv(sys.executable, [sys.executable, "-m", "f2lnk"])


# ═══════════════════════════════════════════════════════════════
#  /restart COMMAND (admin only)
# ═══════════════════════════════════════════════════════════════

@StreamBot.on_message(filters.command("restart") & filters.private)
async def restart_command(client: Client, m: Message):
    """Restart the bot. Admin/Owner only."""
    touch_activity()

    if m.from_user.id not in Var.OWNER_ID:
        await m.reply_text("❌ **Only admins can restart the bot.**", quote=True)
        return

    await m.reply_text(
        "🔄 **Bot is restarting...**\n"
        "Please wait a few seconds.",
        quote=True,
    )

    await _graceful_restart(client, f"Manual restart by user {m.from_user.id}", m.chat.id)


# ═══════════════════════════════════════════════════════════════
#  AUTO-RESTART WATCHDOG
# ═══════════════════════════════════════════════════════════════

async def _watchdog_loop():
    """Background loop that monitors health and inactivity."""
    await asyncio.sleep(60)  # wait 1 min after startup before first check

    while True:
        try:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

            active_count = len(ACTIVE_LEECH_TASKS)

            # ── Check system health ──
            try:
                cpu = psutil.cpu_percent(interval=1)
                ram = psutil.virtual_memory().percent
            except Exception:
                cpu, ram = 0, 0

            if cpu > CPU_THRESHOLD or ram > RAM_THRESHOLD:
                if active_count == 0:
                    logger.warning(
                        "Health alert: CPU=%.1f%%, RAM=%.1f%%. No tasks running. Restarting...",
                        cpu, ram,
                    )
                    await _graceful_restart(
                        StreamBot,
                        f"High resource usage (CPU: {cpu:.0f}%, RAM: {ram:.0f}%)",
                    )
                    return
                else:
                    logger.warning(
                        "Health alert: CPU=%.1f%%, RAM=%.1f%%. %d tasks active, waiting...",
                        cpu, ram, active_count,
                    )

            # ── Check inactivity ──
            idle_seconds = time.time() - _last_activity_time
            if idle_seconds > IDLE_TIMEOUT and active_count == 0:
                hours = idle_seconds / 3600
                logger.info("Idle for %.1f hours with no tasks. Restarting...", hours)
                await _graceful_restart(
                    StreamBot,
                    f"Inactivity restart ({hours:.1f}h idle)",
                )
                return

        except Exception as e:
            logger.error("Watchdog error: %s", e)


def start_watchdog():
    """Start the watchdog background task. Call from __main__.py."""
    asyncio.get_event_loop().create_task(_watchdog_loop())
    logger.info("Restart watchdog started (idle=%ds, health=%ds interval)",
                IDLE_TIMEOUT, HEALTH_CHECK_INTERVAL)
