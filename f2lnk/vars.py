import os
from os import getenv, environ
from dotenv import load_dotenv

load_dotenv()
bot_name = "Bɪɪsᴀʟ Fɪʟᴇ2Lɪɴᴋ Bᴏᴛ"
bisal_channel = "https://telegram.me/bisal_files"
bisal_grp = "https://t.me/+PA8OPL2Zglk3MDM1"

class Var(object):
    AUTH_USERS = '7386512270:AAF_szBwnRs5neTpBAWQAOz91cKigxYh9HE' # Add id of users you want to authorize to use the bot. Separated by spaces
    AUTH_USERS = AUTH_USERS.split()
    MULTI_CLIENT = False
    API_ID = int(getenv('API_ID', '28737888'))
    API_HASH = str(getenv('API_HASH', 'aa9fc525a5e5a837256c1f0b445af447'))
    BOT_TOKEN = str(getenv('BOT_TOKEN' , '6975980093:AAF95hTPJZ5asfYzFqyzso3u4NUsQ_8v9j8'))
    name = str(getenv('name', 'bisal_file2link_bot'))
    SLEEP_THRESHOLD = int(getenv('SLEEP_THRESHOLD', '60'))
    WORKERS = int(getenv('WORKERS', '4'))
    BIN_CHANNEL = int(getenv('BIN_CHANNEL', '-1002101797163'))
    NEW_USER_LOG = int(getenv('NEW_USER_LOG', '-1002101797163'))
    PORT = int(getenv('PORT', '8080'))
    BIND_ADRESS = str(getenv('WEB_SERVER_BIND_ADDRESS', '0.0.0.0'))
    PING_INTERVAL = int(environ.get("PING_INTERVAL", "1200"))  # 20 minutes
    OWNER_ID = [int(x) for x in os.environ.get("OWNER_ID", "6101337858").split()]
    URL = getenv('URL', 'http://185.55.240.55:8080')
    if not URL.endswith('/'):
        URL = f'{URL}/'
    PORT = getenv('PORT', 8080)
    OWNER_USERNAME = str(getenv('OWNER_USERNAME', 'Tyson3290'))
    DATABASE_URL = str(getenv('DATABASE_URL', 'mongodb+srv://NewMoviesRoBot:NewMoviesRoBot3290@cluster0.73vkexn.mongodb.net/?retryWrites=true&w=majority'))
    UPDATES_CHANNEL = str(getenv('UPDATES_CHANNEL', '@CultCinemas'))
    BANNED_CHANNELS = list(set(int(x) for x in str(getenv("BANNED_CHANNELS", "")).split()))
    BAN_CHNL = list(set(int(x) for x in str(getenv("BAN_CHNL", "")).split()))
    BAN_ALERT = str(getenv('BAN_ALERT' , '<b>ʏᴏᴜʀ ᴀʀᴇ ʙᴀɴɴᴇᴅ ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ.Pʟᴇᴀsᴇ ᴄᴏɴᴛᴀᴄᴛ @biisal_bot ᴛᴏ ʀᴇsᴏʟᴠᴇ ᴛʜᴇ ɪssᴜᴇ!!</b>'))
