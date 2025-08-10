import datetime
import motor.motor_asyncio
from f2lnk.vars import Var

class Database:
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.users
        self.bannedList = self.db.bannedList
        self.auth_users = self.db.auth_users

    def new_user(self, id):
        today = datetime.date.today().isoformat()
        return dict(
            id=id,
            join_date=today,
            last_active_date=today,
            files_processed=0,
            total_data_used=0,
            daily_data_used=0,
            last_reset_date=today,
            # --- NEW --- Feature 1: User Tiers
            tier=Var.DEFAULT_PLAN,
            plan_expiry_date=None
        )

    async def add_user(self, id):
        user = self.new_user(id)
        await self.col.insert_one(user)

    async def is_user_exist(self, id):
        user = await self.col.find_one({'id': int(id)})
        return True if user else False
        
    async def get_user_info(self, id):
        """Retrieves a user's statistics from the database."""
        return await self.col.find_one({'id': int(id)})

    async def update_user_stats(self, id, file_size):
        """Increments file count and data usage for a user."""
        today = datetime.date.today().isoformat()
        await self.col.update_one(
            {'id': int(id)},
            {
                '$inc': {
                    'files_processed': 1,
                    'total_data_used': file_size,
                    'daily_data_used': file_size
                },
                '$set': {'last_active_date': today}
            }
        )
    
    async def reset_daily_usage(self, id):
        """Resets the daily usage for a user."""
        today = datetime.date.today().isoformat()
        await self.col.update_one(
            {'id': int(id)},
            {
                '$set': {'daily_data_used': 0, 'last_reset_date': today}
            }
        )

    # --- NEW --- Feature 1: User Tiers
    async def set_user_tier(self, id, tier, expiry_date):
        """Sets a user's tier and expiry date."""
        await self.col.update_one(
            {'id': int(id)},
            {
                '$set': {
                    'tier': tier,
                    'plan_expiry_date': expiry_date.isoformat() if expiry_date else None
                }
            }
        )

    # --- NEW --- Feature 1: User Tiers
    async def check_and_update_tier(self, id):
        """Checks if a user's plan has expired and reverts them to default."""
        user = await self.get_user_info(id)
        if not user or not user.get('plan_expiry_date'):
            return
        
        expiry_date = datetime.datetime.fromisoformat(user['plan_expiry_date']).date()
        
        if datetime.date.today() > expiry_date:
            await self.col.update_one(
                {'id': int(id)},
                {
                    '$set': {
                        'tier': Var.DEFAULT_PLAN,
                        'plan_expiry_date': None
                    }
                }
            )
            return True # Indicates the plan has expired
        return False # Plan is still active

    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    def get_all_users(self):
        return self.col.find({})

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})

    async def ban_user(self , user_id):
        user = await self.bannedList.find_one({'banId' : int(user_id)})
        if user:
            return False
        else:
            await self.bannedList.insert_one({'banId' : int(user_id)})
            return True

    async def is_banned(self , user_id):
        user = await self.bannedList.find_one({'banId' : int(user_id)})
        return True if user else False

    async def is_unbanned(self , user_id):
        try :
            if await self.bannedList.find_one({'banId' : int(user_id)}):
                await self.bannedList.delete_one({'banId' : int(user_id)})
                return True
            else:
                return False
        except Exception as e:
            e = f'Failed to unban. Reason: {e}'
            print(e)
            return e

    async def add_auth_user(self, user_id):
        is_authorized = await self.auth_users.find_one({'user_id': int(user_id)})
        if is_authorized:
            return False
        await self.auth_users.insert_one({'user_id': int(user_id)})
        return True

    async def remove_auth_user(self, user_id):
        is_authorized = await self.auth_users.find_one({'user_id': int(user_id)})
        if not is_authorized:
            return False
        await self.auth_users.delete_one({'user_id': int(user_id)})
        return True

    async def is_user_authorized(self, user_id):
        is_authorized = await self.auth_users.find_one({'user_id': int(user_id)})
        return True if is_authorized else False

    def get_all_auth_users(self):
        return self.auth_users.find({})

    async def has_authorized_users(self):
        count = await self.auth_users.count_documents({})
        return count > 0
