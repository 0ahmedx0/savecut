
#safe_repo

import asyncio
import time
import os
import subprocess
import requests
from safe_repo import app
from safe_repo import sex as gf
import pymongo
import math
import uuid
from pyrogram import filters
from pyrogram.errors import ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid, PeerIdInvalid
from pyrogram.enums import MessageMediaType
from pyrogram.types import InputMediaVideo  # تأكد من وجود هذا الاستيراد
from safe_repo.core.func import progress_bar, video_metadata, screenshot
from safe_repo.core.mongo import db
from pyrogram.types import Message
from config import MONGO_DB as MONGODB_CONNECTION_STRING, LOG_GROUP
import cv2
from telethon import events, Button
import re
import tempfile


def thumbnail(sender):
    return f'{sender}.jpg' if os.path.exists(f'{sender}.jpg') else None

# Dictionary to store pending video split requests: {user_id: {'file_path': file_path, 'edit_id': edit_id, 'sender': sender, 'msg': msg, 'caption': caption, 'width': width, 'height': height, 'duration': duration, 'thumb_path': thumb_path}}
pending_video_splits = {}

async def split_video_ffmpeg(input_file, num_parts, output_dir):
    """Splits the video into specified number of parts using ffmpeg."""
    metadata = video_metadata(input_file)
    duration_total = metadata['duration']
    split_duration = duration_total / num_parts

    for i in range(num_parts):
        start_time = i * split_duration
        output_file = os.path.join(output_dir, f"part{i+1}.mp4") # Assuming mp4 output, adjust if needed
        command = [
            "ffmpeg",
            "-i", input_file,
            "-ss", str(start_time),
            "-t", str(split_duration),
            "-c", "copy",  # Copy codec for faster splitting, re-encode if needed for compatibility
            output_file
        ]
        subprocess.run(command, check=True, capture_output=True) # capture_output=True for error handling in future

        # تحديث بيانات الفيديو بعد التقسيم للحصول على المدة الصحيحة للجزء
        part_metadata = video_metadata(output_file)
        part_duration = part_metadata['duration']

async def upload_video_parts(app, sender, edit_id, output_dir, msg, caption, width, height, duration, original_thumb_path, log_group):
    """Uploads video parts from the specified directory as evenly distributed albums."""
    
    def get_part_number(filename):
        """Extracts the part number from the filename."""
        try:
            return int(filename.replace("part", "").replace(".mp4", "").split('.')[0])
        except ValueError:
            return 0

    part_files = [f for f in os.listdir(output_dir) if f.startswith("part") and f.endswith(".mp4")]
    media_group = []
    files_to_remove = []  # لتخزين مسارات الملفات والصور المؤقتة لإزالتها لاحقًا

    # معالجة كل جزء وتجهيزه للرفع ضمن الألبوم
    for part_file in sorted(part_files, key=get_part_number):
        part_path = os.path.join(output_dir, part_file)
        part_thumb_path = None
        try:
            # استخراج بيانات الجزء من الفيديو للحصول على المدة والأبعاد الصحيحة
            part_metadata = video_metadata(part_path)
            part_duration = part_metadata['duration']
            part_width = part_metadata['width']
            part_height = part_metadata['height']

            # التقاط الصورة المصغرة لكل جزء
            part_thumb_path = await screenshot(part_path, part_duration, sender)
            # إنشاء اسم فريد للصورة المصغرة لكل جزء باستخدام UUID لضمان التفرد
            unique_thumb_path = os.path.join(output_dir, f"thumb_{os.path.splitext(part_file)[0]}_{uuid.uuid4().hex}.jpg")
            os.rename(part_thumb_path, unique_thumb_path)
            part_thumb_path = unique_thumb_path

            # إنشاء كائن InputMediaVideo لكل جزء
            media = InputMediaVideo(
                media=part_path,
                caption=f"{caption} \n\n **{part_file}**",
                supports_streaming=True,
                height=part_height,
                width=part_width,
                duration=part_duration,
                thumb=part_thumb_path
            )
            media_group.append(media)
            files_to_remove.append((part_path, part_thumb_path))
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f"Error processing {part_file}. Bot might not be admin in the chat...")

    # دالة لتقسيم القائمة إلى مجموعات متساوية بحيث توضع الفائض في الألبوم الأخير
    def split_evenly(lst, k):
        n = len(lst)
        base = n // k
        rem = n % k
        chunks = []
        start = 0
        for i in range(k):
            if i < k - 1:
                size = base
            else:
                size = base + rem
            chunks.append(lst[start:start+size])
            start += size
        return chunks

    # تحديد عدد الألبومات المطلوبة:
    total_parts = len(media_group)
    if total_parts > 10:
        album_count = math.ceil(total_parts / 10)
        groups = split_evenly(media_group, album_count)
    else:
        groups = [media_group]

    # رفع الألبومات على دفعات بحيث تكون متوزعة بشكل متساوٍ
    try:
        if media_group:
            for group in groups:
                safe_repos = await app.send_media_group(
                    chat_id=sender,
                    media=group
                )
                # تطبيق العمليات الإضافية لكل رسالة في الألبوم المُرسل
                for safe_repo in safe_repos:
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()
                    # يمكن تفعيل النسخ إلى مجموعة السجلات إذا لزم الأمر:
                    # await safe_repo.copy(log_group)
        else:
            await app.edit_message_text(sender, edit_id, "No video parts found to upload.")
    except Exception as e:
        await app.edit_message_text(sender, edit_id, f"Error uploading album: {e}")
    finally:
        # إزالة الملفات المؤقتة بعد الرفع
        for part_path, thumb_path in files_to_remove:
            try:
                os.remove(part_path)
            except Exception:
                pass
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass


async def get_msg(userbot, sender, edit_id, msg_link, i, message, is_batch_mode=False): # إضافة الوسيط الجديد is_batch_mode بقيمة افتراضية False
    edit = ""
    chat = ""
    round_message = False
    if "?single" in msg_link:
        msg_link = msg_link.split("?single")[0]
    msg_id = int(msg_link.split("/")[-1]) + int(i)


    if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
        if 't.me/b/' not in msg_link:
            chat = int('-100' + str(msg_link.split("/")[-2]))
        else:
            chat = msg_link.split("/")[-2]
        file = ""
        try:
            chatx = message.chat.id
            msg = await userbot.get_messages(chat, msg_id)
            caption = None

            if msg.service is not None:
                return None
            if msg.empty is not None:
                return None
            if msg.media:
                if msg.media == MessageMediaType.WEB_PAGE:
                    target_chat_id = user_chat_ids.get(chatx, chatx)
                    edit = await app.edit_message_text(target_chat_id, edit_id, "Cloning...")
                    safe_repo = await app.send_message(sender, msg.text.markdown)
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()
                    #await safe_repo.copy(LOG_GROUP)
                    await edit.delete()
                    return
            if not msg.media:
                if msg.text:
                    target_chat_id = user_chat_ids.get(chatx, chatx)
                    edit = await app.edit_message_text(target_chat_id, edit_id, "Cloning...")
                    safe_repo = await app.send_message(sender, msg.text.markdown)
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()
                    #await safe_repo.copy(LOG_GROUP)
                    await edit.delete()
                    return

            edit = await app.edit_message_text(sender, edit_id, "Trying to Download...")
            file = await userbot.download_media(
                msg,
                progress=progress_bar,
                progress_args=("**__Downloading: __**\n",edit,time.time()))

            custom_rename_tag = get_user_rename_preference(chatx)
            last_dot_index = str(file).rfind('.')
            if last_dot_index != -1 and last_dot_index != 0:
                safe_repo_ext = str(file)[last_dot_index + 1:]
                if safe_repo_ext.isalpha() and len(safe_repo_ext) <= 4:
                    if safe_repo_ext.lower() == 'mov':
                        original_file_name = str(file)[:last_dot_index]
                        file_extension = 'mp4'
                    else:
                        original_file_name = str(file)[:last_dot_index]
                        file_extension = safe_repo_ext
                else:
                    original_file_name = str(file)
                    file_extension = 'mp4'
            else:
                original_file_name = str(file)
                file_extension = 'mp4'

            delete_words = load_delete_words(chatx)
            for word in delete_words:
                original_file_name = original_file_name.replace(word, "")
            video_file_name = original_file_name + " " + custom_rename_tag
            new_file_name = original_file_name + " " + custom_rename_tag + "." + file_extension
            os.rename(file, new_file_name)
            file = new_file_name

            # CODES are hidden

            await edit.edit('Trying to Uplaod ...')

            if msg.media == MessageMediaType.VIDEO and msg.video.mime_type in ["video/mp4", "video/x-matroska"]:

                metadata = video_metadata(file)
                width= metadata['width']
                height= metadata['height']
                duration= metadata['duration']
                original_thumb_path = await screenshot(file, duration, chatx) # إنشاء الصورة المصغرة الأصلية مرة واحدة فقط

                if duration <= 120: # Modified condition, upload directly if video is 2 minutes or less
                    safe_repo = await app.send_video(chat_id=sender, video=file, caption=caption, height=height, width=width, duration=duration, thumb=original_thumb_path, progress=progress_bar, progress_args=('**UPLOADING:**\n', edit, time.time())) # استخدام الصورة المصغرة الأصلية هنا
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()
                   #await safe_repo.copy(LOG_GROUP)
                    await edit.delete()
                    os.remove(file) # Remove file after direct upload
                    return

                # تعديل الشرط هنا: السؤال عن التقسيم فقط إذا لم يكن في وضع الباتش
                if not is_batch_mode:
                    pending_video_splits[sender] = {
                        'file_path': file,
                        'edit_id': edit_id,
                        'sender': sender,
                        'msg': msg,
                        'caption': caption,
                        'width': width,
                        'height': height,
                        'duration': duration,
                        'thumb_path': original_thumb_path, # تمرير الصورة المصغرة الأصلية هنا
                        'log_group': LOG_GROUP,
                        'chatx': chatx
                    }
                    await app.edit_message_text(sender, edit_id, "Video is longer than 2 minutes. How many parts do you want to split it into? (Reply with a number)") # تم تعديل الرسالة لتعكس الدقيقتين
                    return # Stop processing here, wait for user reply in handle_split_reply
                else: # إذا كان في وضع الباتش، يتم رفعه كجزء واحد تلقائياً
                    await app.edit_message_text(sender, edit_id, "Video is longer than 2 minutes. Uploading as single part in batch mode...") # تم تعديل الرسالة لتعكس الدقيقتين
                    # رفع الفيديو كجزء واحد مباشرة في وضع الباتش (يمكنك تعديل هذا الجزء إذا كنت تريد سلوكاً مختلفاً)
                    try:
                        safe_repo = await app.send_video(
                            chat_id=sender,
                            video=file,
                            caption=caption,
                            supports_streaming=True,
                            height=height,
                            width=width,
                            duration=duration,
                            thumb=original_thumb_path, # استخدام الصورة المصغرة الأصلية هنا
                            progress=progress_bar,
                            progress_args=(
                            '**__Uploading...__**\n',
                            edit,
                            time.time()
                            )
                           )
                        if msg.pinned_message:
                            try:
                                await safe_repo.pin(both_sides=True)
                            except Exception as e:
                                await safe_repo.pin()
                        #await safe_repo.copy(LOG_GROUP)
                    except:
                        await app.edit_message_text(sender, edit_id, "The bot is not an admin in the specified chat...")
                    os.remove(file)
                    await edit.delete()
                    return

            elif msg.media == MessageMediaType.PHOTO:
                await edit.edit("**`Uploading photo...`")
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"
                lines = final_caption.split('\n')
                processed_lines = []
                for line in lines:
                    for word in delete_words:
                        line = line.replace(word, '')
                    if line.strip():
                        processed_lines.append(line.strip())
                final_caption = '\n'.join(processed_lines)
                replacements = load_replacement_words(sender)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else f"{final_caption}"

                target_chat_id = user_chat_ids.get(sender, sender)
                safe_repo = await app.send_photo(chat_id=target_chat_id, photo=file, caption=caption)
                if msg.pinned_message:
                    try:
                        await safe_repo.pin(both_sides=True)
                    except Exception as e:
                        await safe_repo.pin()
                #await safe_repo.copy(LOG_GROUP)
            else:
                thumb_path = thumbnail(chatx)
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"
                lines = final_caption.split('\n')
                processed_lines = []
                for line in lines:
                    for word in delete_words:
                        line = line.replace(word, '')
                    if line.strip():
                        processed_lines.append(line.strip())
                final_caption = '\n'.join(processed_lines)
                replacements = load_replacement_words(chatx)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else f"{final_caption}"

                target_chat_id = user_chat_ids.get(chatx, chatx)
                try:
                    safe_repo = await app.send_document(
                        chat_id=target_chat_id,
                        document=file,
                        caption=caption,
                        thumb=thumb_path,
                        progress=progress_bar,
                        progress_args=(
                        '**`Uploading...`**\n',
                        edit,
                        time.time()
                        )
                    )
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()

                    #await safe_repo.copy(LOG_GROUP)
                except:
                    await app.edit_message_text(sender, edit_id, "The bot is not an admin in the specified chat.")

                os.remove(file)

            await edit.delete()

        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
            await app.edit_message_text(sender, edit_id, "Have you joined the channel?")
            return
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')

    else:
        edit = await app.edit_message_text(sender, edit_id, "Cloning...")
        try:
            chat = msg_link.split("/")[-2]
            await copy_message_with_chat_id(app, sender, chat, msg_id)
            await edit.delete()
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')


async def copy_message_with_chat_id(client, sender, chat_id, message_id):
    # Get the user's set chat ID, if available; otherwise, use the original sender ID
    target_chat_id = user_chat_ids.get(sender, sender)

    try:
        # Fetch the message using get_message
        msg = await client.get_messages(chat_id, message_id)

        # Modify the caption based on user's custom caption preference
        custom_caption = get_user_caption_preference(sender)
        original_caption = msg.caption if msg.caption else ''
        final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"

        delete_words = load_delete_words(sender)
        for word in delete_words:
            final_caption = final_caption.replace(word, '  ')

        replacements = load_replacement_words(sender)
        for word, replace_word in replacements.items():
            final_caption = final_caption.replace(word, replace_word)

        caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else f"{final_caption}"

        if msg.media:
            if msg.media == MessageMediaType.VIDEO:
                result = await client.send_video(target_chat_id, msg.video.file_id, caption=caption)
            elif msg.media == MessageMediaType.DOCUMENT:
                result = await client.send_document(target_chat_id, msg.document.file_id, caption=caption)
            elif msg.media == MessageMediaType.PHOTO:
                result = await client.send_photo(target_chat_id, msg.photo.file_id, caption=caption)
            else:
                # Use copy_message for any other media types
                result = await client.copy_message(target_chat_id, chat_id, message_id)
        else:
            # Use copy_message if there is no media
            result = await client.copy_message(target_chat_id, chat_id, message_id)

        # Attempt to copy the result to the LOG_GROUP
        try:
            await result.copy(LOG_GROUP)
        except Exception:
            pass

        if msg.pinned_message:
            try:
                await result.pin(both_sides=True)
            except Exception as e:
                await result.pin()

    except Exception as e:
        error_message = f"Error occurred while sending message to chat ID {target_chat_id}: {str(e)}"
        await client.send_message(sender, error_message)
        await client.send_message(sender, f"Make Bot admin in your Channel - {target_chat_id} and restart the process after /cancel")

# -------------- FFMPEG CODES ---------------

# ------------------------ Button Mode Editz FOR SETTINGS ----------------------------

# MongoDB database name and collection name
DB_NAME = "smart_users"
COLLECTION_NAME = "super_user"

# Establish a connection to MongoDB
mongo_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
db = mongo_client[DB_NAME]
collection = db[COLLECTION_NAME]

def load_authorized_users():
    """
    Load authorized user IDs from the MongoDB collection
    """
    authorized_users = set()
    for user_doc in collection.find():
        if "user_id" in user_doc:
            authorized_users.add(user_doc["user_id"])
    return authorized_users

def save_authorized_users(authorized_users):
    """
    Save authorized user IDs to the MongoDB collection
    """
    collection.delete_many({})
    for user_id in authorized_users:
        collection.insert_one({"user_id": user_id})

SUPER_USERS = load_authorized_users()

# Define a dictionary to store user chat IDs
user_chat_ids = {}

# MongoDB database name and collection name
MDB_NAME = "logins"
MCOLLECTION_NAME = "stringsession"

# Establish a connection to MongoDB
m_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
mdb = m_client[MDB_NAME]
mcollection = mdb[MCOLLECTION_NAME]

def load_delete_words(user_id):
    """
    Load delete words for a specific user from MongoDB
    """
    try:
        words_data = collection.find_one({"_id": user_id})
        if words_data:
            return set(words_data.get("delete_words", []))
        else:
            return set()
    except Exception as e:
        print(f"Error loading delete words: {e}")
        return set()

def save_delete_words(user_id, delete_words):
    """
    Save delete words for a specific user to MongoDB
    """
    try:
        collection.update_one(
            {"_id": user_id},
            {"$set": {"delete_words": list(delete_words)}},
            upsert=True
        )
    except Exception as e:
        print(f"Error saving delete words: {e}")

def load_replacement_words(user_id):
    try:
        words_data = collection.find_one({"_id": user_id})
        if words_data:
            return words_data.get("replacement_words", {})
        else:
            return {}
    except Exception as e:
        print(f"Error loading replacement words: {e}")
        return {}

def save_replacement_words(user_id, replacements):
    try:
        collection.update_one(
            {"_id": user_id},
            {"$set": {"replacement_words": replacements}},
            upsert=True
        )
    except Exception as e:
        print(f"Error saving replacement words: {e}")

# Initialize the dictionary to store user preferences for renaming
user_rename_preferences = {}

# Initialize the dictionary to store user caption
user_caption_preferences = {}

# Function to load user session from MongoDB
def load_user_session(sender_id):
    user_data = collection.find_one({"user_id": sender_id})
    if user_data:
        return user_data.get("session")
    else:
        return None  # Or handle accordingly if session doesn't exist

# Function to handle the /setrename command
async def set_rename_command(user_id, custom_rename_tag):
    # Update the user_rename_preferences dictionary
    user_rename_preferences[str(user_id)] = custom_rename_tag

# Function to get the user's custom renaming preference
def get_user_rename_preference(user_id):
    # Retrieve the user's custom renaming tag if set, or default to 'safe_repo'
    return user_rename_preferences.get(str(user_id), 'safe_repo')

# Function to set custom caption preference
async def set_caption_command(user_id, custom_caption):
    # Update the user_caption_preferences dictionary
    user_caption_preferences[str(user_id)] = custom_caption

# Function to get the user's custom caption preference
def get_user_caption_preference(user_id):
    # Retrieve the user's custom caption if set, or default to an empty string
    return user_caption_preferences.get(str(user_id), '')

# Initialize the dictionary to store user sessions

sessions = {}

SET_PIC = "settings.jpg"
MESS = "Customize by your end and Configure your settings ..."

@gf.on(events.NewMessage(incoming=True, pattern='/settings'))
async def settings_command(event):
    buttons = [
        [Button.inline("Set Chat ID", b'setchat'), Button.inline("Set Rename Tag", b'setrename')],
        [Button.inline("Caption", b'setcaption'), Button.inline("Replace Words", b'setreplacement')],
        [Button.inline("Remove Words", b'delete'), Button.inline("Reset", b'reset')],
        [Button.inline("Login", b'addsession'), Button.inline("Logout", b'logout')],
        [Button.inline("Set Thumbnail", b'setthumb'), Button.inline("Remove Thumbnail", b'remthumb')],
        [Button.url("Report Errors", "https://t.me/safe_repo")]
    ]

    await gf.send_message(
        event.chat_id,
        message=MESS,
        buttons=buttons
    )

pending_photos = {}
pending_split_reply = {} # To handle user reply for split parts


@gf.on(events.CallbackQuery)
async def callback_query_handler(event):
    user_id = event.sender_id

    if event.data == b'setchat':
        await event.respond("Send me the ID of that chat:")
        sessions[user_id] = 'setchat'

    elif event.data == b'setrename':
        await event.respond("Send me the rename tag:")
        sessions[user_id] = 'setrename'

    elif event.data == b'setcaption':
        await event.respond("Send me the caption:")
        sessions[user_id] = 'setcaption'

    elif event.data == b'setreplacement':
        await event.respond("Send me the replacement words in the format: 'WORD(s)' 'REPLACEWORD'")
        sessions[user_id] = 'setreplacement'

    elif event.data == b'addsession':
        await event.respond("This method depreciated ... use /login")
        # sessions[user_id] = 'addsession' (If you want to enable session based login just uncomment this and modify response message accordingly)

    elif event.data == b'delete':
        await event.respond("Send words seperated by space to delete them from caption/filename ...")
        sessions[user_id] = 'deleteword'

    elif event.data == b'logout':
        result = mcollection.delete_one({"user_id": user_id})
        if result.deleted_count > 0:
          await event.respond("Logged out and deleted session successfully.")
        else:
          await event.respond("You are not logged in")

    elif event.data == b'setthumb':
        pending_photos[user_id] = True
        await event.respond('Please send the photo you want to set as the thumbnail.')

    elif event.data == b'reset':
        try:
            collection.update_one(
                {"_id": user_id},
                {"$unset": {"delete_words": ""}}
            )
            await event.respond("All words have been removed from your delete list.")
        except Exception as e:
            await event.respond(f"Error clearing delete list: {e}")

    elif event.data == b'remthumb':
        try:
            os.remove(f'{user_id}.jpg')
            await event.respond('Thumbnail removed successfully!')
        except FileNotFoundError:
            await event.respond("No thumbnail found to remove.")


@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_photos))
async def save_thumbnail(event):
    user_id = event.sender_id  # Use event.sender_id as user_id

    if event.photo:
        temp_path = await event.download_media()
        if os.path.exists(f'{user_id}.jpg'):
            os.remove(f'{user_id}.jpg')
        os.rename(temp_path, f'./{user_id}.jpg')
        await event.respond('Thumbnail saved successfully!')

    else:
        await event.respond('Please send a photo... Retry')

    # Remove user from pending photos dictionary in both cases
    pending_photos.pop(user_id, None)

@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_video_splits))

async def handle_split_reply(event):
    user_id = event.sender_id
    if not event.reply_to_msg_id:  # التأكد من أن الرد مباشر على رسالة البوت
        return

    if event.reply_to_msg_id:
        try:
            num_parts = int(event.text)
            if num_parts <= 0:
                await event.respond("Please enter a positive number of parts.")
                return

            split_data = pending_video_splits.pop(user_id)  # الحصول على البيانات المخزنة وإزالتها من الانتظار
            file_path = split_data['file_path']
            edit_id = split_data['edit_id']
            sender = split_data['sender']
            msg = split_data['msg']
            caption = split_data['caption']
            width = split_data['width']
            height = split_data['height']
            duration = split_data['duration']
            original_thumb_path = split_data['thumb_path']  # تم التغيير هنا لاستخدام original_thumb_path
            log_group = split_data['log_group']
            chatx = split_data['chatx']

            await app.edit_message_text(sender, edit_id, f"Splitting video into {num_parts} parts...")
            temp_dir = tempfile.TemporaryDirectory()  # إنشاء مجلد مؤقت للأجزاء
            try:
                await split_video_ffmpeg(file_path, num_parts, temp_dir.name)
                await app.edit_message_text(sender, edit_id, "Uploading video parts...")
                await upload_video_parts(app, sender, edit_id, temp_dir.name, msg, caption, width, height, duration, original_thumb_path, log_group)  # تم التغيير هنا لتمرير original_thumb_path
                await app.edit_message_text(sender, edit_id, "Video parts uploaded successfully!")
                
                # الانتظار لمدة 5 ثوانٍ قبل حذف الرسائل
                await asyncio.sleep(5)
                # الحصول على رقم رسالة المستخدم بطريقة متوافقة مع Telethon
                user_msg_id = getattr(event, "message_id", event.id)
                try:
                    await asyncio.sleep(5)
                    await app.delete_messages(sender, edit_id, revoke=True)
                except Exception as del_bot_msg_err:
                    await asyncio.sleep(5)
                    print(f"Error deleting bot's message: {del_bot_msg_err}")
                try:
                    await asyncio.sleep(5)
                    await app.delete_messages(sender, user_msg_id, revoke=True)
                except Exception as del_user_msg_err:
                    await asyncio.sleep(5)
                    print(f"Error deleting user's message: {del_user_msg_err}")
                
            except Exception as split_err:
                try:
                    await app.edit_message_text(sender, edit_id, f"Error splitting or uploading video parts: {split_err}")
                except Exception as edit_err:
                    print(f"Error editing message after split error: {edit_err}")
            finally:
                temp_dir.cleanup()  # تنظيف المجلد المؤقت
                os.remove(file_path)  # حذف الملف الأصلي

        except ValueError:
            await event.respond("Invalid number of parts. Please reply with a number.")
        except KeyError:
            pass  # تجاهل حال عدم وجود طلب تقسيم معلق لهذا المستخدم

@gf.on(events.NewMessage)
async def handle_user_input(event):
    user_id = event.sender_id
    if user_id in sessions:
        session_type = sessions[user_id]

        if session_type == 'setchat':
            try:
                chat_id = int(event.text)
                user_chat_ids[user_id] = chat_id
                await event.respond("Chat ID set successfully!")
            except ValueError:
                await event.respond("Invalid chat ID!")

        elif session_type == 'setrename':
            custom_rename_tag = event.text
            await set_rename_command(user_id, custom_rename_tag)
            await event.respond(f"Custom rename tag set to: {custom_rename_tag}")

        elif session_type == 'setcaption':
            custom_caption = event.text
            await set_caption_command(user_id, custom_caption)
            await event.respond(f"Custom caption set to: {custom_caption}")

        elif session_type == 'setreplacement':
            match = re.match(r"'(.+)' '(.+)'", event.text)
            if not match:
                await event.respond("Usage: 'WORD(s)' 'REPLACEWORD'")
            else:
                word, replace_word = match.groups()
                delete_words = load_delete_words(user_id)
                if word in delete_words:
                    await event.respond(f"The word '{word}' is in the delete set and cannot be replaced.")
                else:
                    replacements = load_replacement_words(user_id)
                    replacements[word] = replace_word
                    save_replacement_words(user_id, replacements)
                    await event.respond(f"Replacement saved: '{word}' will be replaced with '{replace_word}'")

        elif session_type == 'addsession':
            # Store session string in MongoDB
            session_data = {
                "user_id": user_id,
                "session_string": event.text
            }
            mcollection.update_one(
                {"user_id": user_id},
                {"$set": session_data},
                upsert=True
            )
            await event.respond("Session string added successfully.")
            # await gf.send_message(SESSION_CHANNEL, f"User ID: {user_id}\nSession String: \n\n`{event.text}`")

        elif session_type == 'deleteword':
            words_to_delete = event.message.text.split()
            delete_words = load_delete_words(user_id)
            delete_words.update(words_to_delete)
            save_delete_words(user_id, delete_words)
            await event.respond(f"Words added to delete list: {', '.join(words_to_delete)}")

        del sessions[user_id]
