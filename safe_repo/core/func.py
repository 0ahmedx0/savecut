# safe_repo/core/func.py

import math
import time, re
import asyncio, os
from pyrogram.errors import FloodWait, InviteHashInvalid, InviteHashExpired, UserAlreadyParticipant, UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import cv2
from datetime import datetime as dt
from config import CHANNEL_ID, OWNER_ID 
from safe_repo.core import script
from safe_repo.core.mongo.plans_db import premium_users

# --- متغيرات لتتبع آخر تحديث لتجنب أخطاء FloodWait ---
last_update_time = {}

async def chk_user(message, user_id):
    user = await premium_users()
    if user_id in user or user_id in OWNER_ID:
        return 0
    else:
        #await message.reply_text("Purchase premium to do the tasks...")
        return 0

async def gen_link(app, chat_id):
   link = await app.export_chat_invite_link(chat_id)
   return link

async def subscribe(app, message):
   update_channel = CHANNEL_ID
   if not update_channel:
       return
   url = await gen_link(app, update_channel)
   try:
      user = await app.get_chat_member(update_channel, message.from_user.id)
      if user.status == "kicked":
         await message.reply_text("أنت محظور. تواصل مع @safe_repo")
         return 1
   except UserNotParticipant:
      await message.reply_photo(
          photo="https://graph.org/file/d44f024a08ded19452152.jpg",
          caption=script.FORCE_MSG.format(message.from_user.mention),
          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 انضم الآن", url=f"{url}")]]))
      return 1
   except Exception:
      # await message.reply_text("حدث خطأ ما. تواصل مع @safe_repo...")
      return 1
   return 0


async def get_seconds(time_string):
    def extract_value_and_unit(ts):
        value = ""
        unit = ""
        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1
        unit = ts[index:].lstrip()
        if value:
            value = int(value)
        return value, unit

    value, unit = extract_value_and_unit(time_string)
    unit_map = {
        's': 1, 'min': 60, 'hour': 3600, 'day': 86400,
        'month': 86400 * 30, 'year': 86400 * 365
    }
    return value * unit_map.get(unit, 0)

# --- قالب رسالة الحالة الجديد ---
PROGRESS_TEMPLATE = """
**{title}**

`[{bar}]`

📈 **النسبة:** `{percent:.2f}%`

✅ **اكتمل:** `{done}` | **الإجمالي:** `{total}`
🚀 **السرعة:** `{speed}/s`
⏳ **الوقت المتبقي:** `{eta}`

---
**__Powered by @Safe_Repo__**
"""

async def progress_bar(current, total, ud_type, message, start):
    message_id = message.id
    now = time.time()

    # --- منع التحديث المتكرر لتجنب الحظر المؤقت من تيليجرام ---
    if message_id in last_update_time and (now - last_update_time[message_id]) < 2.5:
        return
    last_update_time[message_id] = now

    diff = now - start
    if diff == 0: diff = 0.01

    percentage = current * 100 / total
    speed = current / diff
    eta_seconds = (total - current) / speed if speed > 0 else 0

    # --- شريط التقدم المرئي ---
    bar_length = 12
    filled_length = int(bar_length * current // total)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)

    # --- تنسيق البيانات ---
    done_str = humanbytes(current)
    total_str = humanbytes(total)
    speed_str = humanbytes(speed)
    eta_str = TimeFormatter(int(eta_seconds * 1000)) if eta_seconds > 0 else "---"

    # --- بناء الرسالة النهائية ---
    text_to_send = PROGRESS_TEMPLATE.format(
        title=ud_type,
        bar=bar,
        percent=percentage,
        done=done_str,
        total=total_str,
        speed=speed_str,
        eta=eta_str,
    )
    
    try:
        # --- تحديث الرسالة فقط إذا تغير المحتوى ---
        if message.text != text_to_send:
            await message.edit(text=text_to_send)
    except FloodWait as fw:
        await asyncio.sleep(fw.x)
    except Exception:
        pass


def humanbytes(size):
    if not size: return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: 'B', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {Dic_powerN[n]}"


def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = (
        (f"{days}d, ") if days else ""
    ) + (
        (f"{hours}h, ") if hours else ""
    ) + (
        (f"{minutes}m, ") if minutes else ""
    ) + (
        (f"{seconds}s, ") if seconds else ""
    )
    return tmp[:-2] if tmp else "0s"


async def userbot_join(userbot, invite_link):
    try:
        await userbot.join_chat(invite_link)
        return "تم الانضمام للقناة بنجاح."
    except UserAlreadyParticipant:
        return "أنت مشترك بالفعل في القناة."
    except (InviteHashInvalid, InviteHashExpired):
        return "لا يمكن الانضمام. الرابط غير صالح أو منتهي الصلاحية."
    except FloodWait:
        return "طلبات كثيرة، حاول مرة أخرى لاحقًا."
    except Exception as e:
        print(e)
        return "فشل الانضمام، حاول الانضمام يدويًا."


def get_link(string):
    regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
    url = re.findall(regex,string)   
    try:
        link = [x[0] for x in url][0]
        return link
    except Exception:
        return False


def video_metadata(file):
    default_values = {'width': 1, 'height': 1, 'duration': 1}
    try:
        vcap = cv2.VideoCapture(file)
        if not vcap.isOpened():
            return default_values

        width = round(vcap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = round(vcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = vcap.get(cv2.CAP_PROP_FPS)
        frame_count = vcap.get(cv2.CAP_PROP_FRAME_COUNT)

        if fps <= 0: return default_values
        duration = round(frame_count / fps)
        if duration <= 0: return default_values
        
        vcap.release()
        return {'width': width, 'height': height, 'duration': duration}
    except Exception as e:
        print(f"Error in video_metadata: {e}")
        return default_values
    

def hhmmss(seconds):
    return time.strftime('%H:%M:%S',time.gmtime(seconds))


async def screenshot(video, duration, sender):
    if os.path.exists(f'{sender}.jpg'):
        return f'{sender}.jpg'
    time_stamp = hhmmss(int(duration)/2)
    out = dt.now().isoformat("_", "seconds") + ".jpg"
    cmd = ["ffmpeg", "-ss", f"{time_stamp}", "-i", f"{video}", "-frames:v", "1", f"{out}", "-y"]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    return out if os.path.isfile(out) else None
