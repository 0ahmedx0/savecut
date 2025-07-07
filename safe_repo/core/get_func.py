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

def format_duration(seconds: int) -> str:
    """يحول الثواني إلى تنسيق MM:SS удобочитаемый."""
    if seconds is None:
        return "00:00"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


# Dictionary to store pending video split requests: {user_id: {'file_path': file_path, ...}}
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
    files_to_remove = []

    for part_file in sorted(part_files, key=get_part_number):
        part_path = os.path.join(output_dir, part_file)
        part_thumb_path = None
        try:
            part_metadata = video_metadata(part_path)
            part_duration = part_metadata['duration']
            part_width = part_metadata['width']
            part_height = part_metadata['height']

            part_thumb_path = await screenshot(part_path, part_duration, sender)
            unique_thumb_path = os.path.join(output_dir, f"thumb_{os.path.splitext(part_file)[0]}_{uuid.uuid4().hex}.jpg")
            os.rename(part_thumb_path, unique_thumb_path)
            part_thumb_path = unique_thumb_path

            # تعديل الكابشن ليشمل مدة الجزء بالتنسيق الجديد
            part_caption = f"{caption}\n\n**{part_file} | المدة: {format_duration(part_duration)}**"

            media = InputMediaVideo(
                media=part_path,
                caption=part_caption,
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

    def split_evenly(lst, k):
        n = len(lst)
        base = n // k
        rem = n % k
        chunks = []
        start = 0
        for i in range(k):
            size = base + (1 if i < rem else 0)
            chunks.append(lst[start:start+size])
            start += size
        return chunks if chunks else [[]]

    total_parts = len(media_group)
    album_count = math.ceil(total_parts / 10)
    groups = split_evenly(media_group, album_count) if total_parts > 0 else []

    try:
        if media_group:
            for group in groups:
                if not group: continue
                safe_repos = await app.send_media_group(
                    chat_id=sender,
                    media=group
                )
                for safe_repo in safe_repos:
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception:
                            await safe_repo.pin()
        else:
            await app.edit_message_text(sender, edit_id, "No video parts found to upload.")
    except Exception as e:
        await app.edit_message_text(sender, edit_id, f"Error uploading album: {e}")
    finally:
        for part_path, thumb_path in files_to_remove:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except OSError:
                    pass

async def get_msg(userbot, sender, edit_id, msg_link, i, message, is_batch_mode=False):
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

            if msg.service is not None or msg.empty is not None:
                return None

            if msg.media == MessageMediaType.WEB_PAGE:
                target_chat_id = user_chat_ids.get(chatx, chatx)
                edit = await app.edit_message_text(target_chat_id, edit_id, "Cloning...")
                safe_repo = await app.send_message(sender, msg.text.markdown)
                if msg.pinned_message:
                    try:
                        await safe_repo.pin(both_sides=True)
                    except Exception:
                        await safe_repo.pin()
                await edit.delete()
                return
            if not msg.media and msg.text:
                target_chat_id = user_chat_ids.get(chatx, chatx)
                edit = await app.edit_message_text(target_chat_id, edit_id, "Cloning...")
                safe_repo = await app.send_message(sender, msg.text.markdown)
                if msg.pinned_message:
                    try:
                        await safe_repo.pin(both_sides=True)
                    except Exception:
                        await safe_repo.pin()
                await edit.delete()
                return

            edit = await app.edit_message_text(sender, edit_id, "Trying to Download...")
            file = await userbot.download_media(
                msg,
                progress=progress_bar,
                progress_args=("**__Downloading: __**\n",edit,time.time()))

            custom_rename_tag = get_user_rename_preference(chatx)
            file_path_no_ext, file_ext = os.path.splitext(file)
            safe_repo_ext = file_ext.lower().replace('.', '')

            if safe_repo_ext == 'mov':
                file_extension = 'mp4'
            else:
                file_extension = safe_repo_ext if safe_repo_ext.isalpha() and len(safe_repo_ext) <= 4 else 'mp4'
            
            original_file_name = os.path.basename(file_path_no_ext)
            
            delete_words = load_delete_words(chatx)
            for word in delete_words:
                original_file_name = original_file_name.replace(word, "")
            
            new_file_name_base = original_file_name.strip() + " " + custom_rename_tag
            new_file_name = new_file_name_base + "." + file_extension
            os.rename(file, new_file_name)
            file = new_file_name

            await edit.edit('Trying to Upload ...')

            if msg.media == MessageMediaType.VIDEO and msg.video.mime_type in ["video/mp4", "video/x-matroska"]:
                metadata = video_metadata(file)
                width = metadata['width']
                height = metadata['height']
                duration = metadata['duration']
                original_thumb_path = await screenshot(file, duration, chatx)

                if duration <= 120:
                    safe_repo = await app.send_video(chat_id=sender, video=file, caption=caption, height=height, width=width, duration=duration, thumb=original_thumb_path, progress=progress_bar, progress_args=('**UPLOADING:**\n', edit, time.time()))
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception:
                            await safe_repo.pin()
                    await edit.delete()
                    if os.path.exists(file): os.remove(file)
                    if original_thumb_path and os.path.exists(original_thumb_path): os.remove(original_thumb_path)
                    return
                
                if not is_batch_mode:
                    pending_video_splits[sender] = {
                        'file_path': file, 'edit_id': edit_id, 'sender': sender, 'msg': msg, 'caption': caption,
                        'width': width, 'height': height, 'duration': duration, 'thumb_path': original_thumb_path,
                        'log_group': LOG_GROUP, 'chatx': chatx
                    }
                    # إضافة أزرار 20 و 30 جزء
                    buttons = [
                        [Button.inline("4 أجزاء", b'split_4'), Button.inline("5 أجزاء", b'split_5')],
                        [Button.inline("6 أجزاء", b'split_6'), Button.inline("7 أجزاء", b'split_7')],
                        [Button.inline("8 أجزاء", b'split_8'), Button.inline("9 أجزاء", b'split_9')],
                        [Button.inline("10 أجزاء", b'split_10')],
                        [Button.inline("20 جزءاً", b'split_20'), Button.inline("30 جزءاً", b'split_30')],
                        [Button.inline("أكثر من 10 📝", b'split_more')]
                    ]
                    # تعديل الرسالة لعرض المدة بالتنسيق الجديد
                    await gf.send_message(
                        sender,
                        f"💡 الفيديو أطول من دقيقتين (مدته: {format_duration(duration)})، اختر عدد الأجزاء للتقسيم:",
                        buttons=buttons
                    )
                    return
                else:
                    await app.edit_message_text(sender, edit_id, "Video is longer than 2 minutes. Uploading as single part in batch mode...")
                    try:
                        safe_repo = await app.send_video(
                            chat_id=sender, video=file, caption=caption, supports_streaming=True, height=height,
                            width=width, duration=duration, thumb=original_thumb_path, progress=progress_bar,
                            progress_args=('**__Uploading...__**\n', edit, time.time())
                        )
                        if msg.pinned_message:
                            try:
                                await safe_repo.pin(both_sides=True)
                            except Exception:
                                await safe_repo.pin()
                    except Exception:
                        await app.edit_message_text(sender, edit_id, "The bot is not an admin in the specified chat...")
                    finally:
                        if os.path.exists(file): os.remove(file)
                        if original_thumb_path and os.path.exists(original_thumb_path): os.remove(original_thumb_path)
                        await edit.delete()
                    return

            # ... [بقية الكود الخاص بالصور والملفات الأخرى يبقى كما هو] ...
            # باقي الكود بدون تغيير كبير في هذا الجزء
            elif msg.media == MessageMediaType.PHOTO:
                await edit.edit("**`Uploading photo...`")
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"
                lines = final_caption.split('\n')
                processed_lines = [line.strip() for line in lines if any(line.strip().replace(word, '') for word in delete_words) or not delete_words]
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
            else:
                thumb_path = thumbnail(chatx)
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                final_caption = f"{original_caption}" if custom_caption else f"{original_caption}"
                lines = final_caption.split('\n')
                processed_lines = [line.strip() for line in lines if any(line.strip().replace(word, '') for word in delete_words) or not delete_words]
                final_caption = '\n'.join(processed_lines)
                replacements = load_replacement_words(chatx)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)
                caption = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else f"{final_caption}"

                target_chat_id = user_chat_ids.get(chatx, chatx)
                try:
                    safe_repo = await app.send_document(
                        chat_id=target_chat_id, document=file, caption=caption, thumb=thumb_path,
                        progress=progress_bar, progress_args=('**`Uploading...`**\n', edit, time.time())
                    )
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()
                except:
                    await app.edit_message_text(sender, edit_id, "The bot is not an admin in the specified chat.")

                os.remove(file)

            await edit.delete()

        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
            await app.edit_message_text(sender, edit_id, "Have you joined the channel?")
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

# --- إصلاح الخلل المنطقي وهيكلة الدالة ---
async def copy_message_with_chat_id(client, sender, chat_id, message_id):
    target_chat_id = user_chat_ids.get(sender, sender)

    try:
        msg = await client.get_messages(chat_id, message_id)

        custom_caption_suffix = get_user_caption_preference(sender)
        
        # تحديد النص الأصلي سواء كان كابشن أو نص رسالة
        text_to_process = msg.caption if msg.caption is not None else msg.text
        if text_to_process is None:
            text_to_process = ""

        # معالجة النص
        delete_words = load_delete_words(sender)
        for word in delete_words:
            text_to_process = text_to_process.replace(word, ' ')

        replacements = load_replacement_words(sender)
        for word, replace_word in replacements.items():
            text_to_process = text_to_process.replace(word, replace_word)

        # إضافة اللاحقة المخصصة
        final_text_or_caption = f"{text_to_process}\n\n__**{custom_caption_suffix}**__" if custom_caption_suffix else text_to_process

        result = None
        if msg.media:
            if msg.media == MessageMediaType.VIDEO:
                result = await client.send_video(target_chat_id, msg.video.file_id, caption=final_text_or_caption)
            elif msg.media == MessageMediaType.DOCUMENT:
                result = await client.send_document(target_chat_id, msg.document.file_id, caption=final_text_or_caption)
            elif msg.media == MessageMediaType.PHOTO:
                result = await client.send_photo(target_chat_id, msg.photo.file_id, caption=final_text_or_caption)
            else:
                # لبقية أنواع الميديا، يتم النسخ (الكابشن الأصلي سيبقى)
                result = await client.copy_message(target_chat_id, chat_id, message_id)
        else:
            # للرسائل النصية، يتم إرسال النص المعالج
            result = await client.send_message(target_chat_id, final_text_or_caption)

        if result:
            try:
                await result.copy(LOG_GROUP)
            except Exception:
                pass
            if msg.pinned_message:
                try:
                    await result.pin(both_sides=True)
                except Exception:
                    await result.pin()

    except Exception as e:
        error_message = f"Error occurred while sending message to chat ID {target_chat_id}: {str(e)}"
        await client.send_message(sender, error_message)
        await client.send_message(sender, f"Make Bot admin in your Channel - {target_chat_id} and restart the process after /cancel")


# -------------- FFMPEG CODES ---------------
# ... [بقية الكود حتى الوصول إلى معالج الكويري] ...
# ------------------------ Button Mode Editz FOR SETTINGS ----------------------------

# MongoDB database name and collection name
DB_NAME = "smart_users"
COLLECTION_NAME = "super_user"

# Establish a connection to MongoDB
mongo_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
db = mongo_client[DB_NAME]
collection = db[COLLECTION_NAME]

def load_authorized_users():
    authorized_users = set()
    for user_doc in collection.find():
        if "user_id" in user_doc:
            authorized_users.add(user_doc["user_id"])
    return authorized_users

def save_authorized_users(authorized_users):
    collection.delete_many({})
    for user_id in authorized_users:
        collection.insert_one({"user_id": user_id})

SUPER_USERS = load_authorized_users()

user_chat_ids = {}

MDB_NAME = "logins"
MCOLLECTION_NAME = "stringsession"

m_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
mdb = m_client[MDB_NAME]
mcollection = mdb[MCOLLECTION_NAME]

def load_delete_words(user_id):
    try:
        words_data = collection.find_one({"_id": user_id})
        return set(words_data.get("delete_words", [])) if words_data else set()
    except Exception as e:
        print(f"Error loading delete words: {e}")
        return set()

def save_delete_words(user_id, delete_words):
    try:
        collection.update_one(
            {"_id": user_id}, {"$set": {"delete_words": list(delete_words)}}, upsert=True
        )
    except Exception as e:
        print(f"Error saving delete words: {e}")

def load_replacement_words(user_id):
    try:
        words_data = collection.find_one({"_id": user_id})
        return words_data.get("replacement_words", {}) if words_data else {}
    except Exception as e:
        print(f"Error loading replacement words: {e}")
        return {}

def save_replacement_words(user_id, replacements):
    try:
        collection.update_one(
            {"_id": user_id}, {"$set": {"replacement_words": replacements}}, upsert=True
        )
    except Exception as e:
        print(f"Error saving replacement words: {e}")

user_rename_preferences = {}
user_caption_preferences = {}

def load_user_session(sender_id):
    user_data = collection.find_one({"user_id": sender_id})
    return user_data.get("session") if user_data else None

async def set_rename_command(user_id, custom_rename_tag):
    user_rename_preferences[str(user_id)] = custom_rename_tag

def get_user_rename_preference(user_id):
    return user_rename_preferences.get(str(user_id), 'safe_repo')

async def set_caption_command(user_id, custom_caption):
    user_caption_preferences[str(user_id)] = custom_caption

def get_user_caption_preference(user_id):
    return user_caption_preferences.get(str(user_id), '')

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
    await gf.send_message(event.chat_id, message=MESS, buttons=buttons)

pending_photos = {}

@gf.on(events.CallbackQuery)
async def callback_query_handler(event):
    user_id = event.sender_id

    if event.data == b'setchat':
        await event.respond("Send me the ID of that chat:")
        sessions[user_id] = 'setchat'

    elif event.data == b'setrename':
        await event.respond("Send me the rename tag:")
        sessions[user_id] = 'setrename'
    
    # ... [بقية معالجات الأزرار بدون تغيير] ...
    elif event.data == b'setcaption':
        await event.respond("Send me the caption:")
        sessions[user_id] = 'setcaption'
    elif event.data == b'setreplacement':
        await event.respond("Send me the replacement words in the format: 'WORD(s)' 'REPLACEWORD'")
        sessions[user_id] = 'setreplacement'
    elif event.data == b'addsession':
        await event.respond("This method depreciated ... use /login")
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
            collection.update_one({"_id": user_id}, {"$unset": {"delete_words": ""}})
            await event.respond("All words have been removed from your delete list.")
        except Exception as e:
            await event.respond(f"Error clearing delete list: {e}")
    elif event.data == b'remthumb':
        try:
            os.remove(f'{user_id}.jpg')
            await event.respond('Thumbnail removed successfully!')
        except FileNotFoundError:
            await event.respond("No thumbnail found to remove.")
    # --- تعديل معالج أزرار التقسيم ---
    elif event.data.startswith(b'split_'):
        value = event.data.decode().split('_')[1]
        await event.delete()  # حذف رسالة الأزرار فوراً

        if value == 'more':
            if user_id in pending_video_splits:
                prompt_msg = await event.respond("📝 اكتب العدد المطلوب (أكبر من 10) كرد على هذه الرسالة.")
                # تخزين رقم رسالة الطلب للتحقق منها لاحقًا
                pending_video_splits[user_id]['prompt_msg_id'] = prompt_msg.id
            return

        # التعامل مع أرقام الأجزاء مباشرة من الأزرار
        try:
            num_parts = int(value)
            if user_id in pending_video_splits:
                split_data = pending_video_splits.pop(user_id)
                file_path = split_data.get('file_path')
                original_thumb_path = split_data.get('thumb_path')
                
                temp_dir = tempfile.TemporaryDirectory()
                try:
                    await app.edit_message_text(split_data['sender'], split_data['edit_id'], f"Splitting video into {num_parts} parts...")
                    await split_video_ffmpeg(file_path, num_parts, temp_dir.name)
                    await app.edit_message_text(split_data['sender'], split_data['edit_id'], "Uploading video parts...")
                    await upload_video_parts(app, **{k: v for k, v in split_data.items() if k not in ['file_path', 'thumb_path']}, output_dir=temp_dir.name, original_thumb_path=original_thumb_path)
                    await app.delete_messages(split_data['sender'], split_data['edit_id'])
                except Exception as split_err:
                    await app.edit_message_text(split_data['sender'], split_data['edit_id'], f"Error during split/upload: {split_err}")
                finally:
                    temp_dir.cleanup()
                    if file_path and os.path.exists(file_path): os.remove(file_path)
                    if original_thumb_path and os.path.exists(original_thumb_path): os.remove(original_thumb_path)
        except (ValueError, KeyError):
             await event.respond("An error occurred. Please try the process again.")


@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_photos))
async def save_thumbnail(event):
    user_id = event.sender_id

    if event.photo:
        temp_path = await event.download_media()
        thumb_path = f'./{user_id}.jpg'
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
        os.rename(temp_path, thumb_path)
        await event.respond('Thumbnail saved successfully!')
    else:
        await event.respond('Please send a photo... Retry')
    
    pending_photos.pop(user_id, None)

# --- تحسين معالج الردود لتقسيم الفيديو ---
@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_video_splits and e.is_reply))
async def handle_split_reply(event):
    user_id = event.sender_id
    split_data_prelim = pending_video_splits.get(user_id, {})
    prompt_id = split_data_prelim.get('prompt_msg_id')

    # التأكد من أن الرد هو على رسالة الطلب الصحيحة
    if not prompt_id or event.reply_to_msg_id != prompt_id:
        return

    # التحقق من صحة المدخلات
    try:
        num_parts = int(event.text)
        if num_parts <= 10:
            await event.reply("الرجاء إدخال رقم أكبر من 10.")
            return # إبقاء المستخدم في حالة الانتظار للمحاولة مرة أخرى
    except ValueError:
        await event.reply("إدخال غير صالح. الرجاء الرد برقم صحيح.")
        return

    # تم التحقق من المدخلات، ابدأ المعالجة
    split_data = pending_video_splits.pop(user_id)
    file_path = split_data.get('file_path')
    original_thumb_path = split_data.get('thumb_path')
    sender = split_data.get('sender')
    edit_id = split_data.get('edit_id')

    temp_dir = tempfile.TemporaryDirectory()
    try:
        await app.edit_message_text(sender, edit_id, f"Splitting video into {num_parts} parts...")
        await split_video_ffmpeg(file_path, num_parts, temp_dir.name)
        
        await app.edit_message_text(sender, edit_id, "Uploading video parts...")
        await upload_video_parts(app, **{k: v for k, v in split_data.items() if k not in ['file_path', 'thumb_path', 'prompt_msg_id']}, output_dir=temp_dir.name, original_thumb_path=original_thumb_path)
        
        await app.edit_message_text(sender, edit_id, "Video parts uploaded successfully!")
        await asyncio.sleep(5)
        
        # حذف الرسائل المتعلقة بالعملية
        messages_to_delete = [edit_id, event.id, prompt_id]
        await app.delete_messages(sender, messages_to_delete, revoke=True)

    except Exception as e:
        await app.edit_message_text(sender, edit_id, f"Error splitting or uploading: {e}")
    finally:
        # تنظيف الموارد في كل الحالات
        temp_dir.cleanup()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        if original_thumb_path and os.path.exists(original_thumb_path):
            os.remove(original_thumb_path)


@gf.on(events.NewMessage)
async def handle_user_input(event):
    user_id = event.sender_id
    if user_id in sessions:
        session_type = sessions.pop(user_id) # pop it to avoid re-triggering

        if session_type == 'setchat':
            try:
                user_chat_ids[user_id] = int(event.text)
                await event.respond("Chat ID set successfully!")
            except ValueError:
                await event.respond("Invalid chat ID!")
        
        # ... [بقية معالجات الإعدادات كما هي] ...
        elif session_type == 'setrename':
            await set_rename_command(user_id, event.text)
            await event.respond(f"Custom rename tag set to: {event.text}")
        elif session_type == 'setcaption':
            await set_caption_command(user_id, event.text)
            await event.respond(f"Custom caption set to: {event.text}")
        elif session_type == 'setreplacement':
            match = re.match(r"'(.+)' '(.+)'", event.text)
            if not match:
                await event.respond("Usage: 'WORD(s)' 'REPLACEWORD'")
            else:
                word, replace_word = match.groups()
                replacements = load_replacement_words(user_id)
                replacements[word] = replace_word
                save_replacement_words(user_id, replacements)
                await event.respond(f"Replacement saved: '{word}' will be replaced with '{replace_word}'")
        elif session_type == 'addsession':
            session_data = {"user_id": user_id, "session_string": event.text}
            mcollection.update_one({"user_id": user_id}, {"$set": session_data}, upsert=True)
            await event.respond("Session string added successfully.")
        elif session_type == 'deleteword':
            words_to_delete = event.message.text.split()
            delete_words = load_delete_words(user_id)
            delete_words.update(words_to_delete)
            save_delete_words(user_id, delete_words)
            await event.respond(f"Words added to delete list: {', '.join(words_to_delete)}")            
            del sessions[user_id] # Exit delete word session
