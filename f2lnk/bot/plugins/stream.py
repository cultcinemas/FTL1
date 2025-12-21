# f2lnk/bot/plugins/stream.py

import os
import asyncio
import datetime

from pyrogram import filters, Client
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from urllib.parse import quote_plus

from f2lnk.bot import StreamBot, last_file_for_test
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.vars import Var
from f2lnk.utils.file_properties import get_name, get_hash, get_media_file_size

db = Database(Var.DATABASE_URL, Var.name)
MAINTENANCE_FILE = "maintenance.txt"

msg_text ="""<b>‣ ʏᴏᴜʀ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ ! 😎

‣ Fɪʟᴇ ɴᴀᴍᴇ : <i>{}</i>
{}
‣ Fɪʟᴇ ꜱɪᴢᴇ : {}

🔻 <a href="{}">𝗙𝗔𝗦𝗧 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗</a>
🔺 <a href="{}">𝗪𝗔𝗧𝗖𝗛 𝗢𝗡𝗟𝗜𝗡𝗘</a>

‣ ɢᴇᴛ <a href="https://t.me/+PA8OPL2Zglk3MDM1">ᴍᴏʀᴇ ғɪʟᴇs</a></b> 🤡"""

def save_last_file_details(message_id, file_name, file_hash):
    """Helper function to update the last file info."""
    last_file_for_test['id'] = message_id
    last_file_for_test['name'] = file_name
    last_file_for_test['hash'] = file_hash

def is_maintenance_mode():
    """Helper function to check for maintenance mode."""
    return os.path.exists(MAINTENANCE_FILE)

@StreamBot.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.photo) , group=4)
async def private_receive_handler(c: Client, m: Message):
    if is_maintenance_mode() and m.from_user.id not in Var.OWNER_ID:
        await m.reply_text("The bot is currently under maintenance. Please try again later.", quote=True)
        return

    if m.from_user.id not in Var.OWNER_ID:
        is_bot_locked = Var.AUTH_USERS or await db.has_authorized_users()
        if is_bot_locked:
            is_in_env_auth = Var.AUTH_USERS and str(m.from_user.id) in Var.AUTH_USERS.split()
            is_in_db_auth = await db.is_user_authorized(m.from_user.id)
            if not is_in_env_auth and not is_in_db_auth:
                await m.reply_text("You are not authorized to use this bot.", True)
                return

    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await c.send_message(Var.BIN_CHANNEL, f"New User Joined! : \n\n Name : [{m.from_user.first_name}](tg://user?id={m.from_user.id}) Started Your Bot!!")
    
    if Var.UPDATES_CHANNEL != "None":
        try:
            user = await c.get_chat_member(Var.UPDATES_CHANNEL, m.chat.id)
            if user.status == "kicked":
                await c.send_message(chat_id=m.chat.id, text="You are banned and cannot use this bot.", disable_web_page_preview=True)
                return
        except UserNotParticipant:
            await c.send_photo(chat_id=m.chat.id, photo="https://telegra.ph/file/5eb253f28ed7ed68cb4e6.png", caption=""""<b>Hᴇʏ ᴛʜᴇʀᴇ!\n\nPʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴜsᴇ ᴍᴇ ! 😊</b>""", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Jᴏɪɴ ɴᴏᴡ 🚩", url=f"https://t.me/{Var.UPDATES_CHANNEL}")]]))
            return
        except Exception as e:
            print(f"Error checking updates channel: {e}. Make sure the bot is an admin in the channel.")
            
    ban_chk = await db.is_banned(int(m.from_user.id))
    if ban_chk:
        return await m.reply(Var.BAN_ALERT)
    
    # --- UPDATED --- Feature 1 & 2: Tiered Daily Usage Limit Check
    if m.from_user.id not in Var.OWNER_ID:
        # Check for expired plan and revert if necessary
        if await db.check_and_update_tier(m.from_user.id):
            await m.reply_text("Your premium plan has expired. You have been reverted to the default plan.")

        user_data = await db.get_user_info(m.from_user.id)
        file_size = get_media_file_size(m)
        
        user_tier = user_data.get('tier', Var.DEFAULT_PLAN)
        daily_limit_gb = Var.USER_PLANS.get(user_tier, Var.DAILY_LIMIT_GB)
        limit_in_bytes = daily_limit_gb * 1024 * 1024 * 1024

        today = datetime.date.today().isoformat()
        if user_data.get("last_reset_date") != today:
            await db.reset_daily_usage(m.from_user.id)
            user_data['daily_data_used'] = 0 # Reset for current check
        
        if user_data.get("daily_data_used", 0) + file_size > limit_in_bytes:
            await m.reply_text(f"You have reached your daily limit of **{daily_limit_gb} GB** for the `{user_tier.upper()}` plan.\n"
                               f"Your usage today: **{humanbytes(user_data.get('daily_data_used', 0))}**.\n"
                               "Please try again tomorrow or upgrade your plan.", quote=True)
            return

    try:
        log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
        file_name = get_name(log_msg)
        file_hash = get_hash(log_msg)
        file_size = get_media_file_size(m)
        
        save_last_file_details(log_msg.id, file_name, file_hash)
        
        await db.update_user_stats(m.from_user.id, file_size)

        stream_link = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        online_link = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        
        user_info = await db.get_user_info(m.from_user.id)
        footer = user_info.get("footer", "")
        if footer:
            footer = f"‣ Fᴏᴏᴛᴇʀ : {footer}"
        
        await m.reply_text(text=msg_text.format(file_name, footer, humanbytes(file_size), online_link, stream_link), quote=True, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream_link), InlineKeyboardButton('DOWNLOAD 🔻', url=online_link)]]))

        # --- NEW --- Feature 1: Link Logging
        log_text = (
            f"**Link Generated (Private)**\n\n"
            f"**User:** [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
            f"**User ID:** `{m.from_user.id}`\n"
            f"**File:** `{file_name}`\n"
            f"**Size:** `{humanbytes(file_size)}`\n"
            f"**Download:** [Click Here]({online_link})"
        )
        await c.send_message(Var.LOG_CHANNEL, log_text, disable_web_page_preview=True)

    except Exception as e:
        print(f"Error in private_receive_handler link generation: {e}")

# Removed ~filters.forwarded to allow forwarded files in channels
@StreamBot.on_message(filters.channel & (filters.document | filters.video | filters.photo), group=-1)
async def channel_receive_handler(bot, broadcast):
    if is_maintenance_mode():
        return
        
    is_bot_locked = Var.AUTH_USERS or await db.has_authorized_users()
    if is_bot_locked and not await db.is_user_authorized(broadcast.chat.id):
        await bot.leave_chat(broadcast.chat.id)
        return
    try:
        log_msg = await broadcast.forward(chat_id=Var.BIN_CHANNEL)
        file_name = get_name(log_msg)
        file_size = get_media_file_size(broadcast)
        file_hash = get_hash(log_msg)
        save_last_file_details(log_msg.id, file_name, file_hash)
        stream_link = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        online_link = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        
        footer = "" # No user-specific footer in channels
        
        # FIXED: Changed edit_message_text to edit_message_caption because channels send files (media), not text.
        await bot.edit_message_caption(
            chat_id=broadcast.chat.id, 
            message_id=broadcast.id, 
            caption=msg_text.format(file_name, footer, humanbytes(file_size), online_link, stream_link), 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream_link), InlineKeyboardButton('DOWNLOAD 🔻', url=online_link)]])
        )
        
        log_text = (
            f"**Link Generated (Channel)**\n\n"
            f"**Channel:** {broadcast.chat.title}\n"
            f"**Channel ID:** `{broadcast.chat.id}`\n"
            f"**File:** `{file_name}`\n"
            f"**Size:** `{humanbytes(file_size)}`\n"
            f"**Download:** [Click Here]({online_link})"
        )
        await bot.send_message(Var.LOG_CHANNEL, log_text, disable_web_page_preview=True)

    except Exception as e:
        print(f"Error in channel handler: {e}")

# Removed ~filters.forwarded to allow forwarded files in groups
@StreamBot.on_message(filters.group & (filters.document | filters.video | filters.audio | filters.photo), group=5)
async def group_receive_handler(bot: Client, m: Message):
    if is_maintenance_mode() and m.from_user and m.from_user.id not in Var.OWNER_ID:
        return

    is_bot_locked = Var.AUTH_USERS or await db.has_authorized_users()
    
    # FIXED: Logic to check authorization. If bot is locked:
    # 1. Check if group is authorized.
    # 2. OR check if the SENDER (User) is the Owner (allows you to test in non-added groups).
    if is_bot_locked:
        is_group_auth = await db.is_user_authorized(m.chat.id)
        is_owner = m.from_user and m.from_user.id in Var.OWNER_ID
        if not is_group_auth and not is_owner:
            return

    # --- UPDATED --- Feature 1 & 2: Tiered Daily Usage Limit Check
    if m.from_user and m.from_user.id not in Var.OWNER_ID:
        if not await db.is_user_exist(m.from_user.id):
            await db.add_user(m.from_user.id)
            await bot.send_message(Var.BIN_CHANNEL, f"New User Joined! : \n\n Name : [{m.from_user.first_name}](tg://user?id={m.from_user.id}) Started Your Bot!!")
        
        # Check for expired plan and revert if necessary
        await db.check_and_update_tier(m.from_user.id)

        user_data = await db.get_user_info(m.from_user.id)
        file_size_for_limit = get_media_file_size(m)
        
        user_tier = user_data.get('tier', Var.DEFAULT_PLAN)
        daily_limit_gb = Var.USER_PLANS.get(user_tier, Var.DAILY_LIMIT_GB)
        limit_in_bytes = daily_limit_gb * 1024 * 1024 * 1024

        today = datetime.date.today().isoformat()
        if user_data.get("last_reset_date") != today:
            await db.reset_daily_usage(m.from_user.id)
            user_data['daily_data_used'] = 0 # Reset for current check
        
        if user_data.get("daily_data_used", 0) + file_size_for_limit > limit_in_bytes:
            return

    try:
        log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
        file_name = get_name(log_msg)
        file_hash = get_hash(log_msg)
        file_size = get_media_file_size(m)
        
        save_last_file_details(log_msg.id, file_name, file_hash)
        
        footer = ""
        if m.from_user:
            await db.update_user_stats(m.from_user.id, file_size)
            user_info = await db.get_user_info(m.from_user.id)
            footer = user_info.get("footer", "")
            if footer:
                footer = f"‣ Fᴏᴏᴛᴇʀ : {footer}"

        stream_link = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        online_link = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
        
        await m.reply_text(text=msg_text.format(file_name, footer, humanbytes(file_size), online_link, stream_link), quote=True, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("STREAM 🔺", url=stream_link), InlineKeyboardButton('DOWNLOAD 🔻', url=online_link)]]))

        if m.from_user:
            log_text = (
                f"**Link Generated (Group)**\n\n"
                f"**Group:** {m.chat.title}\n"
                f"**Group ID:** `{m.chat.id}`\n"
                f"**User:** [{m.from_user.first_name}](tg://user?id={m.from_user.id})\n"
                f"**User ID:** `{m.from_user.id}`\n"
                f"**File:** `{file_name}`\n"
                f"**Size:** `{humanbytes(file_size)}`\n"
                f"**Download:** [Click Here]({online_link})"
            )
            await bot.send_message(Var.LOG_CHANNEL, log_text, disable_web_page_preview=True)

    except Exception as e:
        print(f"Error in group handler: {e}")
