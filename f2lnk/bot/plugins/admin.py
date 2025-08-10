import aiohttp
import asyncio
import datetime
import os
import random
import string
import time
from pyrogram import filters, Client
from pyrogram.types import Message
from urllib.parse import quote_plus

from f2lnk.bot import StreamBot, last_file_for_test
from f2lnk.utils.broadcast_helper import send_msg
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.vars import Var

db = Database(Var.DATABASE_URL, Var.name)
Broadcast_IDs = {}

@StreamBot.on_message(filters.command("users") & filters.private & filters.user(Var.OWNER_ID))
async def sts(c: Client, m: Message):
    total_users = await db.total_users_count()
    await m.reply_text(text=f"Total Users in DB: {total_users}", quote=True)

# --- NEW COMMAND: /userinfo ---
@StreamBot.on_message(filters.command("userinfo") & filters.private & filters.user(Var.OWNER_ID))
async def get_user_info(c: Client, m: Message):
    try:
        user_id = int(m.command[1])
    except (IndexError, ValueError):
        await m.reply_text("<b>Usage:</b> /userinfo <user_id>")
        return
        
    user_info = await db.get_user_info(user_id)
    
    if not user_info:
        await m.reply_text(f"No user found with the ID: `{user_id}`")
        return
        
    # Safely get each piece of info with a default value to prevent crashes
    join_date = user_info.get('join_date', 'N/A')
    last_active = user_info.get('last_active_date', 'N/A')
    files_processed = user_info.get('files_processed', 0)
    total_data = user_info.get('total_data_used', 0)

    # Format the response
    text = (
        f"👤 **User Info for:** `{user_id}`\n\n"
        f"Joined: `{join_date}`\n"
        f"Last Active: `{last_active}`\n"
        f"Files Processed: `{files_processed}`\n"
        f"Total Data Used: `{humanbytes(total_data)}`"
    )
    
    await m.reply_text(text)

@StreamBot.on_message(filters.command("authorize") & filters.private & filters.user(Var.OWNER_ID))
async def authorize_user(c: Client, m: Message):
    try:
        id_to_auth = int(m.command[1])
    except (IndexError, ValueError):
        await m.reply_text("<b>Usage:</b> /authorize [user_id | channel_id | group_id]")
        return
    if id_to_auth in Var.OWNER_ID:
        await m.reply_text("You can't authorize an owner.")
        return
    success = await db.add_auth_user(id_to_auth)
    if success:
        await m.reply_text(f"✅ ID `{id_to_auth}` has been successfully authorized.")
    else:
        await m.reply_text(f"ID `{id_to_auth}` is already authorized.")


@StreamBot.on_message(filters.command("unauthorize") & filters.private & filters.user(Var.OWNER_ID))
async def unauthorize_user(c: Client, m: Message):
    try:
        id_to_unauth = int(m.command[1])
    except (IndexError, ValueError):
        await m.reply_text("<b>Usage:</b> /unauthorize [user_id | channel_id | group_id]")
        return
    success = await db.remove_auth_user(id_to_unauth)
    if success:
        await m.reply_text(f"❌ ID `{id_to_unauth}` has been successfully unauthorized.")
    else:
        await m.reply_text(f"ID `{id_to_unauth}` was not found in the authorized list.")

@StreamBot.on_message(filters.command("authusers") & filters.private & filters.user(Var.OWNER_ID))
async def show_auth_users(c: Client, m: Message):
    users_cursor = db.get_all_auth_users()
    users = await users_cursor.to_list(length=None) 
    if not users:
        await m.reply_text("No users, channels, or groups have been authorized yet.")
        return
    text = "📝 **Authorized IDs (Users, Channels & Groups):**\n\n"
    for user in users:
        if 'user_id' in user:
            text += f"- `{user['user_id']}`\n"
        else:
            print(f"[WARNING] Found a malformed entry in the auth_users collection: {user}")
    await m.reply_text(text)

@StreamBot.on_message(filters.command("broadcast") & filters.private & filters.user(Var.OWNER_ID))
async def broadcast_(c: Client, m: Message):
    out = await m.reply_text(text=f"Broadcast initiated...")
    all_users = db.get_all_users()
    broadcast_msg = m.reply_to_message
    if not broadcast_msg:
        await out.edit_text("Please reply to a message to broadcast.")
        return
    start_time = time.time()
    done = 0
    failed = 0
    success = 0
    async for user in all_users:
        try:
            sts, msg = await send_msg(
                user_id=int(user['id']),
                message=broadcast_msg
            )
            if sts == 200:
                success += 1
            else:
                failed += 1
            if sts == 400:
                await db.delete_user(user['id'])
            done += 1
        except Exception:
            failed += 1
    completed_in = datetime.timedelta(seconds=int(time.time() - start_time))
    await out.edit_text(f"Broadcast completed in `{completed_in}`.\n\nTotal Users: {done}\nSuccess: {success}\nFailed: {failed}")

@StreamBot.on_message(filters.command("speedtest") & filters.private & filters.user(Var.OWNER_ID))
async def speed_test(c: Client, m: Message):
    if not last_file_for_test:
        await m.reply_text(
            "I haven't processed any files yet.\n"
            "Please send a file to a group/channel or to me privately first, then run this command again."
        )
        return
    msg = await m.reply_text("Performing speed test on the last processed file...", quote=True)
    message_id = last_file_for_test['id']
    file_name = last_file_for_test['name']
    file_hash = last_file_for_test['hash']
    download_url = f"{Var.URL.rstrip('/')}/{message_id}/{quote_plus(file_name)}?hash={file_hash}"
    start_time = time.time()
    downloaded_size = 0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status == 200:
                    total_size = int(response.headers.get('Content-Length', 0))
                    async for chunk in response.content.iter_any():
                        downloaded_size += len(chunk)
                    end_time = time.time()
                else:
                    await msg.edit(f"Error: Received status code {response.status} from the link.\nURL: {download_url}")
                    return
    except Exception as e:
        await msg.edit(f"An error occurred during the speed test: {e}")
        return
    time_taken = end_time - start_time
    if time_taken == 0:
        await msg.edit("Download was too fast to measure.")
        return
    speed_bytes_per_sec = downloaded_size / time_taken
    speed_mbps = (speed_bytes_per_sec * 8) / (1024 * 1024)
    file_size_display = humanbytes(total_size) if total_size > 0 else humanbytes(downloaded_size)
    result_text = (
        f"🚀 **Server Speed Test Complete** 🚀\n\n"
        f"**File Size:** {file_size_display}\n"
        f"**Time Taken:** {time_taken:.2f} seconds\n"
        f"**Speed:** **{speed_mbps:.2f} Mbps**"
    )
    await msg.edit(result_text)
