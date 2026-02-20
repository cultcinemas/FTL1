import os
from os import getenv, environ
from dotenv import load_dotenv

load_dotenv()
bot_name = "Bɪɪsᴀʟ Fɪʟᴇ2Lɪɴᴋ Bᴏᴛ"
bisal_channel = "https://telegram.me/bisal_files"
bisal_grp = "https://t.me/+PA8OPL2Zglk3MDM1"

class Var(object):
    AUTH_USERS = '' # Add id of users you want to authorize to use the bot. Separated by spaces
    MULTI_CLIENT = True
    API_ID = int(getenv('API_ID', '28737888'))
    API_HASH = str(getenv('API_HASH', 'aa9fc525a5e5a837256c1f0b445af447'))
    BOT_TOKEN = str(getenv('BOT_TOKEN' , '7001727517:AAHirrJg2tVsbQZLuAF_fitd0dv6ItM5vmY'))
    name = str(getenv('name', 'bisal_file2link_bot'))
    SLEEP_THRESHOLD = int(getenv('SLEEP_THRESHOLD', '60'))
    WORKERS = int(getenv('WORKERS', '8'))
    BIN_CHANNEL = int(getenv('BIN_CHANNEL', '-1002627625468'))
    NEW_USER_LOG = int(getenv('NEW_USER_LOG', '-1002627625468'))
    # --- NEW --- Feature 1: Log Channel
    LOG_CHANNEL = int(getenv('LOG_CHANNEL', '-1002627625468')) # Add your Log Channel ID
    PORT = int(getenv('PORT', '8080'))
    BIND_ADRESS = str(getenv('WEB_SERVER_BIND_ADDRESS', '0.0.0.0'))
    PING_INTERVAL = int(environ.get("PING_INTERVAL", "1200"))  # 20 minutes
    OWNER_ID = [int(x) for x in os.environ.get("OWNER_ID", "1392184089 5602172369 5452354891 6726242663").split()]
    URL = getenv('URL', 'http://185.55.240.55:3030/')
    if not URL.endswith('/'):
        URL = f'{URL}/'
    PORT = getenv('PORT', 8080)
    OWNER_USERNAME = str(getenv('OWNER_USERNAME', 'Tyson3290'))
    DATABASE_URL = str(getenv('DATABASE_URL', "mongodb+srv://pinkybitlu:pinky7268@cluster0.dizew5m.mongodb.net/?retryWrites=true&w=majority"))
    UPDATES_CHANNEL = str(getenv('UPDATES_CHANNEL', '@CultCinemas'))
    BANNED_CHANNELS = list(set(int(x) for x in str(getenv("BANNED_CHANNELS", "")).split()))
    BAN_CHNL = list(set(int(x) for x in str(getenv("BAN_CHNL", "")).split()))
    BAN_ALERT = str(getenv('BAN_ALERT' , '<b>ʏᴏᴜʀ ᴀʀᴇ ʙᴀɴɴᴇᴅ ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ.Pʟᴇᴀsᴇ ᴄᴏɴᴛᴀᴄᴛ @biisal_bot ᴛᴏ ʀᴇsᴏʟᴠᴇ ᴛʜᴇ ɪssᴜᴇ!!</b>'))
    # --- NEW --- Feature 2: Daily Usage Limit in GB
    DAILY_LIMIT_GB = int(getenv('DAILY_LIMIT_GB', '10'))
    # --- NEW --- Feature 1: User Tiers
    USER_PLANS = {
        "plan1": 20,
        "plan2": 50,
        "plan3": 100
    }
    DEFAULT_PLAN = "default"
    
    MULTI_TOKENS = getenv('MULTI_TOKENS', '7656364400:AAEGoVoUCXvjoQcAO2GyzwpcTVlXkWoE6Z8 8048965809:AAEQZOja-aNNHiINBihVsehvIIo1DEnDELM 7576471028:AAH91c5MjKLIqiWB4DN79v0VoBYRMI29oqw 8006698778:AAELDwINICfcuRJPGJQiUR-1Qf6uZrCqH2A 7937350226:AAHbnksthkjIcwcskz5IKfVBu12lgVXLuvg 7670657219:AAEC48szzPAFmUEljo3PXaEzuU52LHyEjQo 7681049237:AAEHBMC6x_yfkVxD5TTKmk3Hp7NXWtVqO7w 8105409082:AAEZeoSHga_OTI6BYCt5SAyVzqv5VLdqYYo 7728245130:AAEZQ0-jv0HMyNg6zMCiRqY_b-ltAtBXsE4')
