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
from pyrogram.errors import FloodWait
from io import BytesIO

from f2lnk.bot import StreamBot, last_file_for_test
from f2lnk.utils.broadcast_helper import send_msg
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.vars import Var
from f2lnk.utils.file_properties import get_name, get_hash, get_media_from_message

db = Database(Var.DATABASE_URL, Var.name)
Broadcast_IDs = {}
MAINTENANCE_FILE = "maintenance.txt"

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
    # --- NEW --- Feature 2: Daily Usage Limit
    daily_data = user_info.get('daily_data_used', 0)
    last_reset = user_info.get('last_reset_date', 'N/A')
    # --- NEW --- Feature 1: User Tiers
    tier = user_info.get('tier', Var.DEFAULT_PLAN)
    expiry = user_info.get('plan_expiry_date', 'N/A')


    # Format the response
    text = (
        f"👤 **User Info for:** `{user_id}`\n\n"
        f"Joined: `{join_date}`\n"
        f"Last Active: `{last_active}`\n"
        f"Files Processed: `{files_processed}`\n"
        f"Total Data Used: `{humanbytes(total_data)}`\n"
        f"Today's Usage: `{humanbytes(daily_data)}` (Resets on: `{last_reset}`)\n"
        f"Current Plan: `{tier.upper()}`\n"
        f"Plan Expiry: `{expiry}`"
    )
    
    await m.reply_text(text)

# --- NEW COMMAND --- Feature 1: User Tiers
@StreamBot.on_message(filters.command("set_tier") & filters.private & filters.user(Var.OWNER_ID))
async def set_user_tier(c: Client, m: Message):
    try:
        user_id = int(m.command[1])
        tier_name = m.command[2].lower()
        days = int(m.command[3])

        if tier_name not in Var.USER_PLANS and tier_name != Var.DEFAULT_PLAN:
            await m.reply_text(f"<b>Invalid tier name.</b>\n\nAvailable plans: `{', '.join(Var.USER_PLANS.keys())}` or use `{Var.DEFAULT_PLAN}` to reset.")
            return
            
    except (IndexError, ValueError):
        await m.reply_text("<b>Usage:</b> /set_tier <user_id> <tier_name> <days>\n\n<b>Example:</b> `/set_tier 123456 plan1 30`")
        return

    if not await db.is_user_exist(user_id):
        await m.reply_text(f"User with ID `{user_id}` is not found in the database.")
        return

    expiry_date = datetime.date.today() + datetime.timedelta(days=days)
    await db.set_user_tier(user_id, tier_name, expiry_date)
    
    limit = Var.USER_PLANS.get(tier_name, Var.DAILY_LIMIT_GB)

    # Notify Admin
    await m.reply_text(f"✅ Successfully set plan for user `{user_id}`.\n\n**Plan:** `{tier_name.upper()}`\n**Expires on:** `{expiry_date.isoformat()}`\n**Daily Limit:** `{limit} GB`")

    # Notify User
    try:
        notification_text = (
            f"🎉 **Your Plan Has Been Updated!** 🎉\n\n"
            f"An admin has set your account to the **{tier_name.upper()}** plan.\n\n"
            f"**Benefits:**\n"
            f"- Daily Bandwidth Limit: **{limit} GB**\n"
            f"- Your plan is valid until: **{expiry_date.isoformat()}**\n\n"
            f"Enjoy the enhanced features!"
        )
        await c.send_message(chat_id=user_id, text=notification_text)
    except Exception as e:
        await m.reply_text(f"Couldn't send notification to user `{user_id}`. **Error:** `{e}`")


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

# --- RENAMED COMMAND ---
@StreamBot.on_message(filters.command("Authorizes") & filters.private & filters.user(Var.OWNER_ID))
async def show_auth_users(c: Client, m: Message):
    users_cursor = db.get_all_auth_users()
    users = await users_cursor.to_list(length=None) 
    if not users:
        await m.reply_text("No users, channels, or groups have been authorized yet.")
        return

    msg = await m.reply_text("Fetching details, please wait...")
    
    lines = []
    for user in users:
        if 'user_id' in user:
            user_id = user['user_id']
            try:
                # Get chat info which works for users, groups, and channels
                chat_info = await c.get_chat(user_id)
                # 'title' for channels/groups, 'first_name' for users
                name = chat_info.title or chat_info.first_name
                lines.append(f"**{name}** - `{user_id}`")
            except Exception:
                # If bot can't get info (e.g., not in chat, user deleted), fallback
                lines.append(f"**Unknown/Inaccessible** - `{user_id}`")
        else:
            print(f"[WARNING] Found a malformed entry in the auth_users collection: {user}")

    if lines:
        text = "📝 **Authorized IDs (Users, Channels & Groups):**\n\n" + "\n".join(lines)
    else:
        text = "No valid authorized IDs found in the database."
        
    await msg.edit_text(text)


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

# --- NEW COMMAND --- Feature 3: Maintenance Mode
@StreamBot.on_message(filters.command("maintenance") & filters.private & filters.user(Var.OWNER_ID))
async def maintenance_mode(c: Client, m: Message):
    try:
        status = m.command[1].lower()
    except IndexError:
        await m.reply_text("<b>Usage:</b> /maintenance [on | off]")
        return

    if status == "on":
        if os.path.exists(MAINTENANCE_FILE):
            await m.reply_text("✅ Maintenance mode is already **enabled**.")
            return
        with open(MAINTENANCE_FILE, "w") as f:
            f.write("enabled")
        await m.reply_text("✅ Maintenance mode has been **enabled**.\n\nAll user requests will be paused.")
    elif status == "off":
        if not os.path.exists(MAINTENANCE_FILE):
            await m.reply_text("✅ Maintenance mode is already **disabled**.")
            return
        os.remove(MAINTENANCE_FILE)
        await m.reply_text("✅ Maintenance mode has been **disabled**.\n\nBot is now fully operational.")
    else:
        await m.reply_text("<b>Invalid status.</b> Use `on` or `off`.")


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

# --- NEW AND IMPROVED /batch COMMAND ---
@StreamBot.on_message(filters.command("batch") & filters.private & filters.user(Var.OWNER_ID))
async def batch_link_generator(c: Client, m: Message):
    try:
        # Command usage: /batch <channel_id> <start_msg_id> <end_msg_id>
        channel_id = m.command[1]
        start_id = int(m.command[2])
        end_id = int(m.command[3])
        
        if not (channel_id.startswith("-") or channel_id.startswith("@")):
             channel_id = int(channel_id)
        
        if start_id > end_id:
            await m.reply_text("Start message ID must be less than or equal to the end message ID.")
            return
    except (IndexError, ValueError):
        await m.reply_text(
            "<b>Usage:</b> /batch <channel_id_or_username> <start_msg_id> <end_msg_id>\n\n"
            "Scrapes files from a specified channel and generates links. "
            "Make sure the bot is an admin in the target channel."
        )
        return

    status_msg = await m.reply_text(f"Batch processing initiated for channel `{channel_id}` from message ID `{start_id}` to `{end_id}`.\n\nPlease wait...")
    
    generated_links = []
    total_messages = (end_id - start_id) + 1
    processed_count = 0
    success_count = 0
    error_count = 0

    try:
        await c.get_chat(channel_id)
    except Exception as e:
        await status_msg.edit_text(f"An error occurred while accessing the channel `{channel_id}`.\n\n**Error:** `{e}`\n\nPlease ensure the bot is an admin in the channel and the ID/username is correct.")
        return

    # Loop through the message IDs
    for msg_id in range(start_id, end_id + 1):
        processed_count += 1
        
        # Update status periodically
        if processed_count % 20 == 0 or processed_count == total_messages:
            try:
                await status_msg.edit_text(f"**Progress for `{channel_id}`:**\n\n- Processed: `{processed_count}/{total_messages}`\n- Generated: `{success_count}` links\n- Errors: `{error_count}`")
            except FloodWait as f:
                await asyncio.sleep(f.x)

        try:
            # Get the message from the target channel
            message = await c.get_messages(channel_id, msg_id)

            if get_media_from_message(message):
                # Forward the file to BIN_CHANNEL to make it streamable
                log_msg = await message.forward(Var.BIN_CHANNEL)
                
                file_name = get_name(log_msg)
                file_hash = get_hash(log_msg)

                if not file_name:
                    file_name = f"File_{log_msg.id}"
                
                # Generate the link using the new message ID from BIN_CHANNEL
                stream_link = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
                generated_links.append(stream_link)
                success_count += 1
                await asyncio.sleep(1) # Small delay to avoid hitting rate limits

        except FloodWait as e:
            print(f"Sleeping for {e.x}s due to FloodWait.")
            await asyncio.sleep(e.x)
            processed_count -=1 # Decrement to re-process this message
            continue

        except Exception as e:
            error_count += 1
            print(f"Error processing message {msg_id} from {channel_id}: {e}")
            continue

    # Final status update
    await status_msg.edit_text(f"**Batch Processing Complete for `{channel_id}`!**\n\n- Total Messages Scanned: `{processed_count}`\n- Links Generated: `{success_count}`\n- Errors Encountered: `{error_count}`")

    # Send the generated links
    if generated_links:
        # Join links with a double newline for a visible gap
        links_text = "\n\n".join(generated_links) 
        
        if len(links_text) > 4096:
            with BytesIO(links_text.encode('utf-8')) as file:
                file.name = f"batch_links_{channel_id}_{start_id}_to_{end_id}.txt"
                await m.reply_document(
                    document=file,
                    caption="All generated links are in the file above."
                )
        else:
            await m.reply_text(links_text)
    else:
        await m.reply_text("No files were found in the specified message range in that channel.")
