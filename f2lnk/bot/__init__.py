from pyrogram import Client
import pyromod.listen
from ..vars import Var
from os import getcwd

StreamBot = Client(
    name='Web Streamer',
    api_id=Var.API_ID,
    api_hash=Var.API_HASH,
    bot_token=Var.BOT_TOKEN,
    sleep_threshold=Var.SLEEP_THRESHOLD,
    workers=Var.WORKERS
)

multi_clients = {}
work_loads = {}

# --- NEW: Central location for task tracking ---
# Dictionaries to track active user tasks. The value is a list of tasks
# to support multiple parallel tasks for admins.
ACTIVE_UPLOADS = {}
ACTIVE_TWITTER_TASKS = {}

# --- NEW: Central location for the speedtest variable ---
# This makes the system more robust.
last_file_for_test = {}
