import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = 1143455526219432018
NOTIFY_CHANNEL_ID = 1486052246209953812
REGISTER_CHANNEL_ID = 1486416388510978251
HISTORY_SHOWMATCH_CHANNEL_ID = 1486418088760180917
START_SHOWMATCH_CHANNEL_ID = 1486419689746862110
SHOWMATCH_ROLE_ID = 1481915254140305481  # Role tự động gán cho người chơi showmatch

# Global time stage variables (in minutes).
# Stage 1: warn / start check-in  (default 12 hours = 720 min)
# Stage 2: final call or cancel   (default 11 hours = 660 min)
# Stage 3: auto split teams       (default  6 hours = 360 min)
# Stage 0: match start            (always 0, not configurable)
# Admins can update these at runtime via /set_time_stages.
TIME_STAGE_1 = 12 * 60  # 720 minutes
TIME_STAGE_2 = 11 * 60  # 660 minutes
TIME_STAGE_3 =  6 * 60  # 360 minutes
