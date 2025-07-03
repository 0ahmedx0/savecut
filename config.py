# safe_repo
# Note if you are trying to deploy on vps then directly fill values in ("")

from os import getenv
import os

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = list(map(int, os.getenv("OWNER_ID", "").split()))
MONGO_DB = os.getenv("MONGO_DB")
LOG_GROUP = os.getenv("LOG_GROUP")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
