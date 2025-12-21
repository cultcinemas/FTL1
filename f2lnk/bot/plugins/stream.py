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

msg_text = """<b>‣ ʏᴏᴜʀ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ ! 😎

‣ Fɪʟᴇ ɴᴀᴍᴇ : <i>{}</i>
{}
‣ Fɪʟᴇ ꜱɪᴢᴇ : {}

🔻 <a href="{}">𝗙𝗔𝗦𝗧 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗</a>
🔺 <a href="{}">𝗪𝗔𝗧𝗖𝗛 𝗢𝗡𝗟𝗜𝗡𝗘</a>

‣ ɢᴇᴛ <a href="https://t.me/+PA8OPL2Zglk3MDM1">ᴍᴏʀᴇ ғɪʟᴇs</a></b> 🤡"""

def save_last_file_details(message_id, file_name, file_hash):
    last_file_for_test['id'] = message_id
    last_file_for_test['name'] = file_name
    last_file_for_test['hash'] = file_hash

def is_maintenance_mode():
    return os.path.exists(MAINTENANCE_FILE)

# =========================
# PRIVATE HANDLER (UNCHANGED)
# =========================
@StreamBot.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.photo), group=4)
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
        await c.send_message(
            Var.BIN_CHANNEL,
            f"New User Joined!\n\nName: [{m.from_user.first_name}](tg://user?id={m.from_user.id})"
        )

    if Var.UPDATES_CHANNEL != "None":
        try:
            user = await c.get_chat_member(Var.UPDATES_CHANNEL, m.chat.id)
            if user.status == "kicked":
                await c.send_message(m.chat.id, "You are banned and cannot use this bot.")
                return
        except UserNotParticipant:
            await c.send_photo(
                m.chat.id,
                "https://telegra.ph/file/5eb253f28ed7ed68cb4e6.png",
                caption="<b>Please join our updates channel to use me.</b>",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Join Now 🚩", url=f"https://t.me/{Var.UPDATES_CHANNEL}")]]
                )
            )
            return

    if await db.is_banned(int(m.from_user.id)):
        return await m.reply(Var.BAN_ALERT)

    if m.from_user.id not in Var.OWNER_ID:
        await db.check_and_update_tier(m.from_user.id)
        user_data = await db.get_user_info(m.from_user.id)
        file_size = get_media_file_size(m)

        tier = user_data.get('tier', Var.DEFAULT_PLAN)
        limit_gb = Var.USER_PLANS.get(tier, Var.DAILY_LIMIT_GB)
        limit_bytes = limit_gb * 1024 * 1024 * 1024

        today = datetime.date.today().isoformat()
        if user_data.get("last_reset_date") != today:
            await db.reset_daily_usage(m.from_user.id)
            user_data['daily_data_used'] = 0

        if user_data.get("daily_data_used", 0) + file_size > limit_bytes:
            await m.reply_text("Daily limit reached.", quote=True)
            return

    log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
    file_name = get_name(log_msg)
    file_hash = get_hash(log_msg)
    file_size = get_media_file_size(m)

    save_last_file_details(log_msg.id, file_name, file_hash)
    await db.update_user_stats(m.from_user.id, file_size)

    stream = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
    download = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"

    footer = (await db.get_user_info(m.from_user.id)).get("footer", "")
    footer = f"‣ Fᴏᴏᴛᴇʀ : {footer}" if footer else ""

    await m.reply_text(
        msg_text.format(file_name, footer, humanbytes(file_size), download, stream),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("STREAM 🔺", url=stream),
              InlineKeyboardButton("DOWNLOAD 🔻", url=download)]]
        )
    )

# =========================
# CHANNEL HANDLER (FIXED)
# =========================
@StreamBot.on_message(filters.channel & (filters.document | filters.video | filters.photo) & ~filters.forwarded, group=-1)
async def channel_receive_handler(bot: Client, broadcast: Message):
    if is_maintenance_mode():
        return

    member = await bot.get_chat_member(broadcast.chat.id, bot.me.id)
    if member.status not in ("administrator", "creator"):
        return

    log_msg = await broadcast.forward(chat_id=Var.BIN_CHANNEL)
    file_name = get_name(log_msg)
    file_hash = get_hash(log_msg)
    file_size = get_media_file_size(broadcast)

    save_last_file_details(log_msg.id, file_name, file_hash)

    stream = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
    download = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"

    await bot.edit_message_text(
        broadcast.chat.id,
        broadcast.id,
        msg_text.format(file_name, "", humanbytes(file_size), download, stream),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("STREAM 🔺", url=stream),
              InlineKeyboardButton("DOWNLOAD 🔻", url=download)]]
        )
    )

# =========================
# GROUP HANDLER (FIXED)
# =========================
@StreamBot.on_message(filters.group & (filters.document | filters.video | filters.audio | filters.photo) & ~filters.forwarded, group=5)
async def group_receive_handler(bot: Client, m: Message):
    if is_maintenance_mode():
        return

    member = await bot.get_chat_member(m.chat.id, bot.me.id)
    if member.status not in ("administrator", "creator"):
        return

    log_msg = await m.forward(chat_id=Var.BIN_CHANNEL)
    file_name = get_name(log_msg)
    file_hash = get_hash(log_msg)
    file_size = get_media_file_size(m)

    save_last_file_details(log_msg.id, file_name, file_hash)

    footer = ""
    if m.from_user:
        await db.update_user_stats(m.from_user.id, file_size)
        footer = (await db.get_user_info(m.from_user.id)).get("footer", "")
        footer = f"‣ Fᴏᴏᴛᴇʀ : {footer}" if footer else ""

    stream = f"{Var.URL.rstrip('/')}/watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"
    download = f"{Var.URL.rstrip('/')}/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}"

    await m.reply_text(
        msg_text.format(file_name, footer, humanbytes(file_size), download, stream),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("STREAM 🔺", url=stream),
              InlineKeyboardButton("DOWNLOAD 🔻", url=download)]]
        )
    )
