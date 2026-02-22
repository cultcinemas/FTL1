# f2lnk/bot/plugins/commands.py

# Add this import at the top of the file
from pyromod.exceptions import ListenerTimeout
import logging

from pyrogram import filters
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from f2lnk.bot import StreamBot
from f2lnk.utils.database import Database
from f2lnk.utils.human_readable import humanbytes
from f2lnk.vars import Var

db = Database(Var.DATABASE_URL, Var.name)
from f2lnk.vars import bot_name , bisal_channel , bisal_grp

logger = logging.getLogger(__name__)

SRT_TXT = """<b>ᴊᴀɪ sʜʀᴇᴇ ᴋʀsɴᴀ {}!,
I ᴀᴍ Fɪʟᴇ ᴛᴏ Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴏʀ Bᴏᴛ ᴡɪᴛʜ Cʜᴀɴɴᴇʟ sᴜᴘᴘᴏʀᴛ.

Sᴇɴᴅ ᴍᴇ ᴀɴʏ ғɪʟᴇ ᴀɴᴅ ɢᴇᴛ ᴀ ᴅɪʀᴇᴄᴛ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ ᴀɴᴅ sᴛʀᴇᴀᴍᴀʙʟᴇ ʟɪɴᴋ.!
ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ : <a href='https://t.me/biisal_bot'>Bɪɪsᴀʟ</a></b>"""

@StreamBot.on_message(filters.command("start") & filters.private )
async def start(b, m):
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await b.send_message(
            Var.NEW_USER_LOG,
            f"**Nᴇᴡ Usᴇʀ Jᴏɪɴᴇᴅ:** \n\n__Mʏ Nᴇᴡ Fʀɪᴇɴᴅ__ [{m.from_user.first_name}](tg://user?id={m.from_user.id}) __Sᴛᴀʀᴛᴇᴅ Yᴏᴜʀ Bᴏᴛ !!__"
        )
    if Var.UPDATES_CHANNEL != "None":
        try:
            user = await b.get_chat_member(Var.UPDATES_CHANNEL, m.chat.id)
            if user.status == "kicked":
                await b.send_message(
                    chat_id=m.chat.id,
                    text="__𝓢𝓞𝓡𝓡𝓨, 𝓨𝓞𝓤 𝓐𝓡𝓔 𝓐𝓡𝓔 𝓑𝓐𝓝𝓝𝓔𝓓 𝓕𝓡𝓞𝓜 𝓤𝓢𝓘𝓝𝓖 𝓜𝓔. 𝓒ᴏɴᴛᴀᴄᴛ ᴛʜᴇ 𝓓ᴇᴠᴇʟᴏᴘᴇʀ__\n\n  **𝙃𝙚 𝙬𝙞𝙡𝙡 𝙝𝙚𝙡𝙥 𝙮𝙤𝙪**",
                    disable_web_page_preview=True
                )
                return
        except UserNotParticipant:
             await StreamBot.send_photo(
                chat_id=m.chat.id,
                photo="https://telegra.ph/file/5eb253f28ed7ed68cb4e6.png",
                caption=""""<b>Hᴇʏ ᴛʜᴇʀᴇ!\n\nPʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ ᴛᴏ ᴜsᴇ ᴍᴇ ! 😊\n\nDᴜᴇ ᴛᴏ sᴇʀᴠᴇʀ ᴏᴠᴇʀʟᴏᴀᴅ, ᴏɴʟʏ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ sᴜʙsᴄʀɪʙᴇʀs ᴄᴀɴ ᴜsᴇ ᴛʜɪs ʙᴏᴛ !</b>""",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("Jᴏɪɴ ɴᴏᴡ 🚩", url=f"https://t.me/{Var.UPDATES_CHANNEL}")
                        ]
                    ]
                ),

            )
             return
        except Exception:
            await b.send_message(
                chat_id=m.chat.id,
                text="<b>sᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ.ᴘʟᴇᴀsᴇ <a href='https://t.me/biisal_bot'>ᴄʟɪᴄᴋ ʜᴇʀᴇ ғᴏʀ sᴜᴘᴘᴏʀᴛ</a></b>",

                disable_web_page_preview=True)
            return
    await StreamBot.send_photo(
    chat_id=m.chat.id,
    photo="https://telegra.ph/file/d813fe75a3ac675ef34b7.jpg",
    caption= SRT_TXT.format(m.from_user.mention(style="md")),
    reply_markup=InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ 🤡", url=bisal_channel)],
            [
                 InlineKeyboardButton("ᴀʙᴏᴜᴛ 😎", callback_data="about"),
                 InlineKeyboardButton("ʜᴇʟᴘ 😅", callback_data="help")
            ],
            [InlineKeyboardButton("ᴏᴜʀ ɢʀᴏᴜᴘ 🚩", url=bisal_grp)],

            [
                 InlineKeyboardButton("ᴅɪsᴄʟᴀɪᴍᴇʀ 🔻", url=f"https://www.google.com"),
                 InlineKeyboardButton("ᴅᴇᴠ 😊", callback_data="aboutDev")
            ]
        ]
    )
)
@StreamBot.on_message(filters.command("help") & filters.private )
async def help_cd(b, m):
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await b.send_message(
            Var.NEW_USER_LOG,
            f"**Nᴇᴡ Usᴇʀ Jᴏɪɴᴇᴅ:** \n\n__Mʏ Nᴇᴡ Fʀɪᴇɴᴅ__ [{m.from_user.first_name}](tg://user?id={m.from_user.id}) __Sᴛᴀʀᴛᴇᴅ Yᴏᴜʀ Bᴏᴛ !!__"
        )

    # Part 1: Auto features + Commands overview
    part1 = (
        "📖 **Bot Help — Complete Guide (1/2)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "**🔹 AUTO FEATURES (No command needed):**\n\n"

        "**📁 File to Link**\n"
        "Send any file (video, audio, doc, photo) to the bot.\n"
        "Bot uploads it and gives you:\n"
        "• **Stream link** — Watch online in browser\n"
        "• **Download link** — Direct fast download\n"
        "Works in Private chat, Groups & Channels.\n"
        "Just add bot as admin in your channel!\n\n"

        "**🐦 Twitter/X Downloader**\n"
        "Paste any Twitter or X.com link.\n"
        "Bot auto-detects and downloads:\n"
        "• Videos (all qualities)\n"
        "• GIFs and Images\n"
        "• Multi-media tweets (all items)\n"
        "Bot asks for custom filename for each.\n"
        "Send /skip to keep original name.\n\n"

        "**🔗 URL Uploader**\n"
        "Send any direct download URL.\n"
        "Bot downloads the file and uploads to Telegram.\n"
        "Max file size: 1.95 GB.\n"
        "Shows progress during download & upload.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**🔹 COMMANDS:**\n\n"

        "**🎬 /vt — Video Tools Menu**\n"
        "Reply to any media file with `/vt`\n"
        "Opens interactive button menu with 10 tools:\n"
        "1️⃣ Merge Video+Video — Join multiple videos\n"
        "2️⃣ Merge Video+Audio — Add audio track\n"
        "3️⃣ Merge Video+Subtitle — Attach subtitle file\n"
        "4️⃣ Hardsub — Burn subtitles into video\n"
        "5️⃣ SubSync — Auto-sync subtitle timing\n"
        "6️⃣ Compress — Reduce file size\n"
        "7️⃣ Trim — Cut segment from video\n"
        "8️⃣ Watermark — Add text/image overlay\n"
        "9️⃣ Extract Audio — Remove video, keep audio\n"
        "🔟 Extract Video — Remove audio, keep video\n\n"

        "**📋 /mediainfo** — File Metadata\n"
        "Reply to file: `/mediainfo`\n"
        "Or by URL: `/mediainfo https://url.com/file.mp4`\n"
        "Shows codec, bitrate, resolution, audio tracks etc.\n\n"

        "**📦 /zip** — Compress to ZIP\n"
        "Send multiple files interactively, bot zips them.\n\n"
        "**📂 /unzip** — Extract Archives\n"
        "Reply to a ZIP/RAR/7z file or send URL.\n"
        "Extracts all files and uploads them.\n\n"

        "**🔗 /jl <URL>** — JL Downloader\n"
        "Download media from supported link sites.\n"
        "Supports HLS streams and direct links.\n\n"

        "**🧲 /qbl** — qBittorrent Leech\n"
        "`/qbl <magnet_link>` or reply to `.torrent` file\n"
        "Downloads torrent & uploads to Telegram.\n"
        "Auto-splits files > 2GB.\n"
    )
    await m.reply_text(part1, quote=True)

    # Part 2: /l tool details + user/admin commands
    part2 = (
        "📖 **Bot Help (2/2) — /l Batch Tools & More**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "**⚙️ /l — Batch Media Processing**\n"
        "Process multiple files with one command.\n\n"
        "**How to use:**\n"
        "1. Send your media files to bot\n"
        "2. Swipe to the FIRST file\n"
        "3. Reply with: `/l -i <count> -m <name> -<tool>`\n\n"

        "**Flags:**\n"
        "`-i 5` — Number of files to collect\n"
        "`-m output` — Output filename\n"
        "`-start 00:01:00` — Start time (trim/cut)\n"
        "`-end 00:02:30` — End time (trim/cut)\n\n"

        "**10 Tools:**\n"
        "`-vt` **Merge Videos** — Concatenate into one\n"
        "`-va` **Merge Audio** — Add audio to video\n"
        "`-aa` **Merge Audios** — Concatenate audio files\n"
        "`-vs` **Add Subtitles** — Hardcode or soft embed\n"
        "`-cv` **Compress** — 5 modes: quality/size/CRF\n"
        "`-wv` **Watermark** — Text or image, 8 animations\n"
        "`-tv` **Trim** — Keep segment between timestamps\n"
        "`-cut` **Cut** — Remove segment, stitch the rest\n"
        "`-rv` **Extract Audio** — All tracks separately\n"
        "    Formats: MP3, AAC, WAV, Keep Original\n"
        "    Multi-audio: each track = separate file\n"
        "`-ev` **Extract Video** — Strip all audio\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**📝 Examples:**\n"
        "`/l -i 2 -m merged.mp4 -vt` — Merge 2 videos\n"
        "`/l -i 2 -m output.mp4 -va` — Add audio to video\n"
        "`/l -i 1 -m trim.mp4 -tv -start 00:01:00 -end 00:03:00`\n"
        "`/l -i 1 -m cut.mp4 -cut -start 00:05:00 -end 00:07:00`\n"
        "`/l -i 3 -m compressed -cv` — Compress 3 files\n"
        "`/l -i 1 -m audio -rv` — Extract all audio tracks\n"
        "`/l -i 1 -m silent -ev` — Video without audio\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**👤 User Commands:**\n"
        "`/start` — Start the bot\n"
        "`/help` — This help guide\n"
        "`/myplan` — View your plan, tier & daily usage\n"
        "`/add_footer <text>` — Custom caption footer\n"
        "`/remove_footer` — Remove caption footer\n"
        "`/cancel <task_id>` — Cancel a running task\n"
        "`/restart` — Restart the bot (admin only)\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**⚡ Bot Features:**\n"
        "• Auto stream & download links for every file\n"
        "• Twitter/X auto-download with renaming\n"
        "• URL upload (direct links up to 1.95 GB)\n"
        "• 10 video/audio processing tools\n"
        "• Batch processing (multiple files at once)\n"
        "• Multi-audio extraction (all tracks)\n"
        "• Quality preservation with `-c copy`\n"
        "• Cancel tasks anytime mid-process\n"
        "• Daily data limits per user plan\n"
        "• Custom footer on generated links\n"
        "• Works in channels (add as admin)\n"
    )
    await m.reply_text(part2)


# --- NEW COMMAND: /myplan ---
@StreamBot.on_message(filters.command("myplan") & filters.private)
async def myplan_cmd(b, m):
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await b.send_message(
            Var.NEW_USER_LOG,
            f"**Nᴇᴡ Usᴇʀ Jᴏɪɴᴇᴅ:** \n\n__Mʏ Nᴇᴡ Fʀɪᴇɴᴅ__ [{m.from_user.first_name}](tg://user?id={m.from_user.id}) __Sᴛᴀʀᴛᴇᴅ Yᴏᴜʀ Bᴏᴛ !!__"
        )

    # Check for plan expiration
    await db.check_and_update_tier(m.from_user.id)
    user_info = await db.get_user_info(m.from_user.id)

    if not user_info:
        await m.reply_text("Could not fetch your details. Please try again.")
        return

    # Safely get each piece of info
    tier = user_info.get('tier', Var.DEFAULT_PLAN)
    daily_limit_gb = Var.USER_PLANS.get(tier, Var.DAILY_LIMIT_GB)
    expiry = user_info.get('plan_expiry_date', 'Lifetime (Default)')
    join_date = user_info.get('join_date', 'N/A')
    daily_data_used = user_info.get('daily_data_used', 0)
    total_data_used = user_info.get('total_data_used', 0)
    last_reset = user_info.get('last_reset_date', 'N/A')

    # Format the response
    text = (
        f"**👤 Your Plan Details**\n\n"
        f"**Plan:** `{tier.upper()}`\n"
        f"**Plan Expiry:** `{expiry}`\n"
        f"**Joined On:** `{join_date}`\n\n"
        f"**📊 Usage Stats**\n"
        f"**Daily Usage:** `{humanbytes(daily_data_used)}` / `{daily_limit_gb} GB`\n"
        f"**Total Usage:** `{humanbytes(total_data_used)}`\n"
        f"**Usage Resets On:** `{last_reset}`"
    )

    await m.reply_text(text, quote=True)


# --- NEW COMMANDS: Footer ---
@StreamBot.on_message(filters.command("add_footer") & filters.private)
async def add_footer_cmd(c, m):
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await c.send_message(
            Var.NEW_USER_LOG,
            f"**Nᴇᴡ Usᴇʀ Jᴏɪɴᴇᴅ:** \n\n__Mʏ Nᴇᴡ Fʀɪᴇɴᴅ__ [{m.from_user.first_name}](tg://user?id={m.from_user.id}) __Sᴛᴀʀᴛᴇᴅ Yᴏᴜʀ Bᴏᴛ !!__"
        )

    try:
        footer_text_msg = await c.ask(
            chat_id=m.chat.id,
            text="Okay, send me the text for your footer.\n\nThis will be added to all your generated file links.\n\nUse /cancel to stop.",
            timeout=60
        )

        if footer_text_msg.text and not footer_text_msg.text.startswith("/"):
            await db.set_footer(m.from_user.id, footer_text_msg.text)
            await m.reply_text("✅ Footer successfully saved.")
        else:
            await m.reply_text("Invalid input. Please send text only and do not use commands.")

    except ListenerTimeout:
        await m.reply_text("⌛️ Request timed out. Please try again.")
    except Exception as e:
        await m.reply_text(f"An error occurred: `{e}`")


@StreamBot.on_message(filters.command("remove_footer") & filters.private)
async def remove_footer_cmd(c, m):
    if not await db.is_user_exist(m.from_user.id):
        await db.add_user(m.from_user.id)
        await c.send_message(
            Var.NEW_USER_LOG,
            f"**Nᴇᴡ Usᴇʀ Jᴏɪɴᴇᴅ:** \n\n__Mʏ Nᴇᴡ Fʀɪᴇɴᴅ__ [{m.from_user.first_name}](tg://user?id={m.from_user.id}) __Sᴛᴀʀᴛᴇᴅ Yᴏᴜʀ Bᴏᴛ !!__"
        )

    await db.remove_footer(m.from_user.id)
    await m.reply_text("✅ Your custom footer has been removed.")


@StreamBot.on_message(filters.command('ban') & filters.user(Var.OWNER_ID))
async def do_ban(bot ,  message):
    userid = message.text.split(" ", 2)[1] if len(message.text.split(" ", 1)) > 1 else None
    reason = message.text.split(" ", 2)[2] if len(message.text.split(" ", 2)) > 2 else None
    if not userid:
        return await message.reply('<b>ᴘʟᴇᴀsᴇ ᴀᴅᴅ ᴀ ᴠᴀʟɪᴅ ᴜsᴇʀ/ᴄʜᴀɴɴᴇʟ ɪᴅ ᴡɪᴛʜ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ\n\nᴇx : /ban (user/channel_id) (banning reason[Optional]) \nʀᴇᴀʟ ᴇx : <code>/ban 1234567899</code>\nᴡɪᴛʜ ʀᴇᴀsᴏɴ ᴇx:<code>/ban 1234567899 seding adult links to bot</code>\nᴛᴏ ʙᴀɴ ᴀ ᴄʜᴀɴɴᴇʟ :\n<code>/ban CHANEL_ID</code>\nᴇx : <code>/ban -1001234567899</code></b>')
    text = await message.reply("<b>ʟᴇᴛ ᴍᴇ ᴄʜᴇᴄᴋ 👀</b>")
    banSts = await db.ban_user(userid)
    if banSts == True:
        await text.edit(
    text=f"<b><code>{userid}</code> ʜᴀs ʙᴇᴇɴ ʙᴀɴɴᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ\n\nSʜᴏᴜʟᴅ I sᴇɴᴅ ᴀɴ ᴀʟᴇʀᴛ ᴛᴏ ᴛʜᴇ ʙᴀɴɴᴇᴅ ᴜsᴇʀ?</b>",
    reply_markup=InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ʏᴇs ✅", callback_data=f"sendAlert_{userid}_{reason if reason else 'no reason provided'}"),
                InlineKeyboardButton("ɴᴏ ❌", callback_data=f"noAlert_{userid}"),
            ],
        ]
    ),
)
    else:
        await text.edit(f"<b>Cᴏɴᴛʀᴏʟʟ ʏᴏᴜʀ ᴀɴɢᴇʀ ʙʀᴏ...\n<code>{userid}</code> ɪs ᴀʟʀᴇᴀᴅʏ ʙᴀɴɴᴇᴅ !!</b>")
    return


@StreamBot.on_message(filters.command('unban') & filters.user(Var.OWNER_ID))
async def do_unban(bot ,  message):
    userid = message.text.split(" ", 2)[1] if len(message.text.split(" ", 1)) > 1 else None
    if not userid:
        return await message.reply('ɢɪᴠᴇ ᴍᴇ ᴀɴ ɪᴅ\nᴇx : <code>/unban 1234567899<code>')
    text = await message.reply("<b>ʟᴇᴛ ᴍᴇ ᴄʜᴇᴄᴋ 🥱</b>")
    unban_chk = await db.is_unbanned(userid)
    if  unban_chk == True:
        await text.edit(text=f'<b><code>{userid}</code> ɪs ᴜɴʙᴀɴɴᴇᴅ\nSʜᴏᴜʟᴅ I sᴇɴᴅ ᴛʜᴇ ʜᴀᴘᴘʏ ɴᴇᴡs ᴀʟᴇʀᴛ ᴛᴏ ᴛʜᴇ ᴜɴʙᴀɴɴᴇᴅ ᴜsᴇʀ?</b>',
        reply_markup=InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("ʏᴇs ✅", callback_data=f"sendUnbanAlert_{userid}"),
                InlineKeyboardButton("ɴᴏ ❌", callback_data=f"NoUnbanAlert_{userid}"),
            ],
        ]
    ),
)

    elif unban_chk==False:
        await text.edit('<b>ᴜsᴇʀ ɪs ɴᴏᴛ ʙᴀɴɴᴇᴅ ʏᴇᴛ.</b>')
    else :
        await text.edit(f"<b>ғᴀɪʟᴇᴅ ᴛᴏ ᴜɴʙᴀɴ ᴜsᴇʀ/ᴄʜᴀɴɴᴇʟ.\nʀᴇᴀsᴏɴ : {unban_chk}</b>")



@StreamBot.on_callback_query()
async def cb_handler(client, query):
    data = query.data
    if data == "close_data":
        await query.message.delete()


    if data == "start":
        await query.message.edit_caption(
        caption= SRT_TXT.format(query.from_user.mention(style="md")),
        reply_markup=InlineKeyboardMarkup(
                [
            [InlineKeyboardButton("ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ 🤡", url=bisal_channel)],
            [
                 InlineKeyboardButton("ᴀʙᴏᴜᴛ 😎", callback_data="about"),
                 InlineKeyboardButton("ʜᴇʟᴘ 😅", callback_data="help")
            ],
            [InlineKeyboardButton("ᴏᴜʀ ɢʀᴏᴜᴘ 🚩", url=bisal_grp)],

            [
                 InlineKeyboardButton("ᴅɪsᴄʟᴀɪᴍᴇʀ 🔻", url=f"https://telegra.ph/Disclaimer-11-07-37"),
                 InlineKeyboardButton("ᴅᴇᴠ 😊", callback_data="aboutDev")
            ]
        ]
            )
        )


    elif data == "about":
        await query.message.edit_caption(
            caption=f"<b>Mʏ ɴᴀᴍᴇ :<a href='https://t.me/bisal_file2link_bot'>{bot_name}</a>\nAᴅᴍɪɴ : <a href='https://t.me/biisal_bot'>Bɪɪsᴀʟ</a>\nʜᴏsᴛᴇᴅ ᴏɴ : ʜᴇʀᴏᴋᴜ\nᴅᴀᴛᴀʙᴀsᴇ : ᴍᴏɴɢᴏ ᴅʙ\nʟᴀɴɢᴜᴀɢᴇ : ᴘʏᴛʜᴏɴ 3</b>",
            reply_markup=InlineKeyboardMarkup(
                [[
                     InlineKeyboardButton("ʜᴏᴍᴇ", callback_data="start"),
                     InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")
                  ]]
            )
        )
    elif data == "help":
        await query.message.edit_caption(
        caption=f"<b>ᴡᴇ ᴅᴏɴᴛ ɴᴇᴇᴅ ᴍᴀɴʏ <a href='https://t.me/bisal_files'>ᴄᴏᴍᴍᴀɴᴅs</a> ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ 🤩.\n\nᴊᴜsᴛ sᴇɴᴅ ᴍᴇ <a href='https.t.me/bisal_files'>ᴠɪᴅᴇᴏ ғɪʟᴇs</a> ᴀɴᴅ ɪ ᴡɪʟʟ ɢɪᴠᴇ ʏᴏᴜ <a href='https://t.me/bisal_files'>ᴅɪʀᴇᴄᴛ ᴅᴏᴡɴʟᴏᴀᴅ & sᴛʀᴇᴀᴍᴀʙʟᴇ</a> ʟɪɴᴋ.\n\nᴏʀ ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ᴍᴇ ɪɴ <a href='https://t.me/bisal_files'>ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ</a>..ᴊᴜsᴛ ᴀᴅᴅ ᴍᴇ ᴀɴᴅ ᴍᴀᴋᴇ ᴍᴇ ᴀᴅᴍɪɴ ᴀɴᴅ sᴇᴇ ᴍʏ ᴍᴀɢɪᴄ 😎</b>",
            reply_markup=InlineKeyboardMarkup(
[[
                     InlineKeyboardButton("ʜᴏᴍᴇ", callback_data="start"),
                     InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")
                  ]]            )
        )
    elif data == "aboutDev":
        # please don't steal credit
        await query.message.edit_caption(
            caption=f"<b>ᴊᴀɪ sʜʀᴇᴇ ᴋʀsɴᴀ ᴅᴇᴀʀ...\nɪᴍ <a href='https://t.me/biisal_bot'>Bɪɪsᴀʟ</a>\nɪ ᴀᴍ ᴛʜᴇ ᴀᴅᴍɪɴ ᴏғ ᴛʜɪs ʙᴏᴛ..ᴀɴᴅ ɪ ᴍᴀᴅᴇ ᴛʜᴇ  ʙᴏᴛ ʙʏ ʜᴇʟᴘ ᴏғ <a href='https://github.com/adarsh-goel'>ᴀᴅᴀʀsʜ</a> ʙʀᴏ..\n\nGɪᴛʜᴜʙ : <a href='https://github.com/biisal'>Bɪɪsᴀʟ's Gɪᴛʜᴜʙ</a></b>",
            reply_markup=InlineKeyboardMarkup(
                [[
                     InlineKeyboardButton("ʜᴏᴍᴇ", callback_data="start"),
                     InlineKeyboardButton("ᴄʟᴏsᴇ ‼️", callback_data="close_data")
                  ]]            )
        )
    elif data.startswith("sendAlert"):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        if len(str(user_id)) == 10:
            reason = str(data.split("_")[2])
            try:
                await client.send_message(user_id , f'<b>ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ʙʏ ᴀᴅᴍɪɴ.\nRᴇᴀsᴏɴ : {reason}</b>')
                await query.message.edit(f"<b>Aʟᴇʀᴛ sᴇɴᴛ ᴛᴏ <code>{user_id}</code>\nRᴇᴀsᴏɴ : {reason}</b>")
            except Exception as e:
                await query.message.edit(f"<b>sʀʏ ɪ ɢᴏᴛ ᴛʜɪs ᴇʀʀᴏʀ : {e}</b>")
        else:
            await query.message.edit(f"<b>Tʜᴇ ᴘʀᴏᴄᴇss ᴡᴀs ɴᴏᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʙᴇᴄᴀᴜsᴇ ᴛʜᴇ ᴜsᴇʀ ɪᴅ ᴡᴀs ɴᴏᴛ ᴠᴀʟɪᴅ, ᴏʀ ᴘᴇʀʜᴀᴘs ɪᴛ ᴡᴀs ᴀ ᴄʜᴀɴɴᴇʟ ɪᴅ</b>")

    elif data.startswith('noAlert'):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        await query.message.edit(f"<b>Tʜᴇ ʙᴀɴ ᴏɴ <code>{user_id}</code> ᴡᴀs ᴇxᴇᴄᴜᴛᴇᴅ sɪʟᴇɴᴛʟʏ.</b>")

    elif data.startswith('sendUnbanAlert'):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        if len(str(user_id)) == 10:
            try:
                unban_text = '<b>ʜᴜʀʀᴀʏ..ʏᴏᴜ ᴀʀᴇ ᴜɴʙᴀɴɴᴇᴅ ʙʏ ᴀᴅᴍɪɴ.</b>'
                await client.send_message(user_id , unban_text)
                await query.message.edit(f"<b>Uɴʙᴀɴɴᴇᴅ Aʟᴇʀᴛ sᴇɴᴛ ᴛᴏ <code>{user_id}</code>\nᴀʟᴇʀᴛ ᴛᴇxᴛ : {unban_text}</b>")
            except Exception as e:
                await query.message.edit(f"<b>sʀʏ ɪ ɢᴏᴛ ᴛʜɪs ᴇʀʀᴏʀ : {e}</b>")
        else:
            await query.message.edit(f"<b>Tʜᴇ ᴘʀᴏᴄᴇss ᴡᴀs ɴᴏᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʙᴇᴄᴀᴜsᴇ ᴛʜᴇ ᴜsᴇʀ ɪᴅ ᴡᴀs ɴᴏᴛ ᴠᴀʟɪᴅ, ᴏʀ ᴘᴇʀʜᴀᴘs ɪᴛ ᴡᴀs ᴀ ᴄʜᴀɴɴᴇʟ ɪᴅ</b>")
    elif data.startswith('NoUnbanAlert'):
        user_id =(data.split("_")[1])
        user_id = int(user_id.replace(' ' , ''))
        await query.message.edit(f"Tʜᴇ ᴜɴʙᴀɴ ᴏɴ <code>{user_id}</code> ᴡᴀs ᴇxᴇᴄᴜᴛᴇᴅ sɪʟᴇɴᴛʟʏ.")
    
