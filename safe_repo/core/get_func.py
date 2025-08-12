# safe_repo

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
from pyrogram.types import InputMediaVideo
from safe_repo.core.func import progress_bar, video_metadata, screenshot
from safe_repo.core.mongo import db
from pyrogram.types import Message
from config import MONGO_DB as MONGODB_CONNECTION_STRING, LOG_GROUP
import cv2
from telethon import events, Button
import re
import tempfile


def thumbnail(sender):
    """Returns the user-specific thumbnail path if it exists."""
    return f'{sender}.jpg' if os.path.exists(f'{sender}.jpg') else None

def format_duration(seconds: int) -> str:
    """Converts seconds into a human-readable MM:SS format."""
    if seconds is None:
        return "00:00"
    minutes, rem_seconds = divmod(int(seconds), 60)
    return f"{minutes:02d}:{rem_seconds:02d}"

# Dictionary to store pending video split requests
pending_video_splits = {}


async def split_video_ffmpeg(input_file, num_parts, output_dir):
    """Splits the video into a specified number of parts using ffmpeg."""
    metadata = video_metadata(input_file)
    duration_total = metadata.get('duration', 0)
    if duration_total <= 0:
        raise ValueError("Video duration is invalid or could not be determined.")
    split_duration = duration_total / num_parts

    for i in range(num_parts):
        start_time = i * split_duration
        output_file = os.path.join(output_dir, f"part{i+1}.mp4")
        command = [
            "ffmpeg", "-y", "-i", input_file, "-ss", str(start_time),
            "-t", str(split_duration), "-c", "copy", output_file
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)


async def upload_video_parts(app, sender, edit_id, output_dir, msg, caption, log_group, total_parts, **kwargs): # Added total_parts
    """Uploads video parts from a directory."""
    def get_part_number(filename):
        try:
            return int(re.search(r'part(\d+)', filename).group(1))
        except (AttributeError, ValueError):
            return float('inf')

    part_files = sorted(
        [f for f in os.listdir(output_dir) if f.startswith("part") and f.endswith(".mp4")],
        key=get_part_number
    )

    uploaded_count = 0
    files_to_remove = []

    await app.edit_message_text(sender, edit_id, f"📤 جارٍ رفع الأجزاء: {uploaded_count}/{total_parts}")
    
    for part_file in part_files:
        part_path = os.path.join(output_dir, part_file)
        try:
            part_metadata = video_metadata(part_path)
            part_duration = part_metadata['duration']
            part_width = part_metadata['width']
            part_height = part_metadata['height']

            part_thumb_path = await screenshot(part_path, 0, sender)
            unique_thumb_path = os.path.join(output_dir, f"thumb_{uuid.uuid4().hex}.jpg")
            os.rename(part_thumb_path, unique_thumb_path)
            
            part_caption = f"{caption if caption else ''}\n\n**{os.path.basename(part_file)} | المدة: {format_duration(part_duration)}**"
            
            # --- MODIFICATION START ---
            uploaded_count += 1
            progress_msg_text = f"📤 جارٍ رفع الجزء {uploaded_count}/{total_parts}"
            
            safe_repo = await app.send_video(
                chat_id=sender, 
                video=part_path, 
                caption=part_caption, 
                supports_streaming=True,
                height=part_height, 
                width=part_width, 
                duration=part_duration, 
                thumb=unique_thumb_path,
                progress=progress_bar, # Use the progress_bar for each part
                progress_args=(progress_msg_text, edit_id, time.time(), os.path.basename(part_path))
            )
            
            # Since progress_bar updates the original edit_id message, we need to ensure it exists
            # And then update it to reflect the current part count.
            # No direct update to edit_id here, progress_bar handles it.
            # After each part, we update the main progress message
            await app.edit_message_text(sender, edit_id, f"📤 تم رفع {uploaded_count}/{total_parts} أجزاء. جارٍ معالجة التالي...")

            if msg.pinned_message:
                try:
                    await safe_repo.pin(both_sides=True)
                except Exception:
                    await safe_repo.pin()
            # Optional: Uncomment to copy to log group
            # await safe_repo.copy(log_group) 
            
            # Add files to remove after successful upload of this part
            files_to_remove.append((part_path, unique_thumb_path))

            # Small delay to avoid overwhelming Telegram
            await asyncio.sleep(0.5)
            # --- MODIFICATION END ---
            
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f"Error processing {part_file}: {e}")
            # If an error occurs, still try to clean up the current part
            files_to_remove.append((part_path, unique_thumb_path))


    if uploaded_count == len(part_files):
        await app.edit_message_text(sender, edit_id, f"✅ تم رفع جميع الأجزاء بنجاح ({uploaded_count}/{total_parts}).")
    else:
        await app.edit_message_text(sender, edit_id, f"⚠️ تم رفع {uploaded_count} من {total_parts} أجزاء. حدثت أخطاء في الأجزاء المتبقية.")
        
    # Ensure cleanup of all processed files
    for part_path, thumb_path in files_to_remove:
        if os.path.exists(part_path):
            os.remove(part_path)
        if os.path.exists(thumb_path):
            os.remove(thumb_path)


async def get_msg(userbot, sender, edit_id, msg_link, i, message, is_batch_mode=False):
    if "?single" in msg_link:
        msg_link = msg_link.split("?single")[0]
    msg_id = int(msg_link.split("/")[-1]) + int(i)

    if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
        chat = int('-100' + msg_link.split("/")[-2]) if 't.me/c/' in msg_link else msg_link.split("/")[-2]
        
        file_path = None
        original_thumb_path = None
        try:
            chatx = message.chat.id
            msg = await userbot.get_messages(chat, msg_id)
            caption = msg.caption or ""

            if msg.service or msg.empty:
                return

            if not msg.media or msg.media == MessageMediaType.WEB_PAGE:
                edit = await app.edit_message_text(sender, edit_id, "Cloning...")
                safe_repo = await app.send_message(sender, msg.text.markdown)
                if msg.pinned_message: await safe_repo.pin()
                await edit.delete()
                return

            edit = await app.edit_message_text(sender, edit_id, "📥 جارٍ التحضير للتحميل...")
            
            file_name = "file" 
            if getattr(msg, 'document', None):
                file_name = msg.document.file_name
            elif getattr(msg, 'video', None):
                file_name = msg.video.file_name
            elif getattr(msg, 'audio', None):
                file_name = msg.audio.file_name
            elif getattr(msg, 'photo', None):
                file_name = f"photo_{msg.photo.file_unique_id}.jpg"

            file_path = await userbot.download_media(
                msg,
                progress=progress_bar,
                progress_args=("📥 جارٍ التحميل", edit, time.time(), file_name)
            )

            base, ext = os.path.splitext(file_path)
            custom_rename_tag = get_user_rename_preference(chatx)
            new_filename = os.path.basename(base)
            delete_words = load_delete_words(chatx)
            for word in delete_words:
                new_filename = new_filename.replace(word, "").strip()
            
            new_filepath = f"{new_filename} {custom_rename_tag}{ext}"
            os.rename(file_path, new_filepath)
            file_path = new_filepath
            
            upload_filename = os.path.basename(file_path)

            await edit.edit('📤 جارٍ التحضير للرفع...')

            if msg.media == MessageMediaType.VIDEO and msg.video.mime_type in ["video/mp4", "video/x-matroska"]:
                metadata = video_metadata(file_path)
                width, height, duration = metadata['width'], metadata['height'], metadata['duration']
                original_thumb_path = await screenshot(file_path, duration / 2, chatx)

                if duration <= 120:
                    safe_repo = await app.send_video(
                        chat_id=sender, video=file_path, caption=caption, height=height, width=width,
                        duration=duration, thumb=original_thumb_path,
                        progress=progress_bar, progress_args=('📤 جارٍ الرفع', edit, time.time(), upload_filename)
                    )
                    if msg.pinned_message: await safe_repo.pin()
                    await edit.delete()
                    return 

                if not is_batch_mode:
                    pending_video_splits[sender] = {
                        'file_path': file_path, 'edit_id': edit_id, 'sender': sender, 'msg': msg, 'caption': caption,
                        'log_group': LOG_GROUP, 'thumb_path': original_thumb_path, 'total_duration': duration # Added for splitting logic
                    }
                    buttons = [
                        [Button.inline("4 أجزاء", b'split_4'), Button.inline("5 أجزاء", b'split_5')],
                        [Button.inline("6 أجزاء", b'split_6'), Button.inline("7 أجزاء", b'split_7')],
                        [Button.inline("8 أجزاء", b'split_8'), Button.inline("9 أجزاء", b'split_9')],
                        [Button.inline("10 أجزاء", b'split_10'), Button.inline("20 جزءاً", b'split_20')],
                        [Button.inline("30 جزءاً", b'split_30')],
                        [Button.inline("تحديد عدد مخصص 📝", b'split_more')]
                    ]
                    await gf.send_message(
                        sender,
                        f"💡 الفيديو أطول من دقيقتين (مدته: {format_duration(duration)}). اختر عدد الأجزاء للتقسيم:",
                        buttons=buttons
                    )
                    file_path = None
                    original_thumb_path = None
                    return 

                else:
                    await app.edit_message_text(sender, edit_id, "Video > 2 mins. Uploading as single file in batch mode.")
                    safe_repo = await app.send_video(
                        chat_id=sender, video=file_path, caption=caption, supports_streaming=True, height=height,
                        width=width, duration=duration, thumb=original_thumb_path
                    )
                    if msg.pinned_message: await safe_repo.pin()
                    await edit.delete()
                    return

            else:
                delete_words = load_delete_words(sender)
                custom_caption_suffix = get_user_caption_preference(sender)
                processed_caption = caption
                for word in delete_words:
                    processed_caption = processed_caption.replace(word, '')
                replacements = load_replacement_words(chatx)
                for word, replace_word in replacements.items():
                    processed_caption = processed_caption.replace(word, replace_word)
                if custom_caption_suffix:
                    processed_caption = f"{processed_caption}\n\n__**{custom_caption_suffix}**__"

                target_chat_id = user_chat_ids.get(chatx, chatx)
                if msg.media == MessageMediaType.PHOTO:
                    await edit.edit("📤 جارٍ رفع الصورة...")
                    safe_repo = await app.send_photo(chat_id=target_chat_id, photo=file_path, caption=processed_caption)
                else:
                    thumb_path = thumbnail(chatx)
                    await edit.edit("📤 جارٍ رفع الملف...")
                    safe_repo = await app.send_document(
                        chat_id=target_chat_id, document=file_path, caption=processed_caption,
                        thumb=thumb_path, progress=progress_bar, progress_args=('📤 جارٍ الرفع', edit, time.time(), upload_filename)
                    )

                if msg.pinned_message: await safe_repo.pin()
                await edit.delete()

        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid) as e:
            await app.edit_message_text(sender, edit_id, f"Channel Error: {e.__class__.__name__}. Have you joined?")
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {e}')
        finally:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            if original_thumb_path and os.path.exists(original_thumb_path):
                os.remove(original_thumb_path)
    else:
        edit = await app.edit_message_text(sender, edit_id, "Cloning from public channel...")
        try:
            chat = msg_link.split("/")[-2]
            await copy_message_with_chat_id(app, sender, chat, msg_id)
            await edit.delete()
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {e}')


async def copy_message_with_chat_id(client, sender, chat_id, message_id):
    target_chat_id = user_chat_ids.get(sender, sender)
    try:
        msg = await client.get_messages(chat_id, message_id)
        
        text_or_caption = msg.caption or msg.text or ""

        custom_suffix = get_user_caption_preference(sender)
        delete_words = load_delete_words(sender)
        replacements = load_replacement_words(sender)

        for word in delete_words:
            text_or_caption = text_or_caption.replace(word, ' ')
        for word, replace_word in replacements.items():
            text_or_caption = text_or_caption.replace(word, replace_word)

        final_caption = f"{text_or_caption.strip()}\n\n__**{custom_suffix}**__" if custom_suffix else text_or_caption.strip()

        result = None
        if msg.media:
            if msg.media == MessageMediaType.VIDEO:
                result = await client.send_video(target_chat_id, msg.video.file_id, caption=final_caption)
            elif msg.media == MessageMediaType.DOCUMENT:
                result = await client.send_document(target_chat_id, msg.document.file_id, caption=final_caption)
            elif msg.media == MessageMediaType.PHOTO:
                result = await client.send_photo(target_chat_id, msg.photo.file_id, caption=final_caption)
            else:
                result = await client.copy_message(target_chat_id, chat_id, message_id)
        else:
            result = await client.send_message(target_chat_id, final_caption)

        if result:
            if msg.pinned_message:
                try: await result.pin(both_sides=True)
                except: await result.pin()
            try: await result.copy(LOG_GROUP)
            except: pass

    except Exception as e:
        error_message = f"Error sending to chat {target_chat_id}: {e}"
        await client.send_message(sender, error_message)
        await client.send_message(sender, f"Make sure the bot is an admin in your channel ({target_chat_id}) and restart with /cancel.")


# --- Database and Settings Functions ---
DB_NAME = "smart_users"
COLLECTION_NAME = "super_user"
mongo_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
db = mongo_client[DB_NAME]
collection = db[COLLECTION_NAME]
user_chat_ids = {}
MDB_NAME = "logins"
MCOLLECTION_NAME = "stringsession"
m_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
mdb = m_client[MDB_NAME]
mcollection = mdb[MCOLLECTION_NAME]

def load_delete_words(user_id):
    words_data = collection.find_one({"_id": user_id})
    return set(words_data.get("delete_words", [])) if words_data else set()

def save_delete_words(user_id, delete_words):
    collection.update_one({"_id": user_id}, {"$set": {"delete_words": list(delete_words)}}, upsert=True)

def load_replacement_words(user_id):
    words_data = collection.find_one({"_id": user_id})
    return words_data.get("replacement_words", {}) if words_data else {}

def save_replacement_words(user_id, replacements):
    collection.update_one({"_id": user_id}, {"$set": {"replacement_words": replacements}}, upsert=True)

user_rename_preferences = {}
user_caption_preferences = {}
sessions = {}
pending_photos = {}

def get_user_rename_preference(user_id): return user_rename_preferences.get(str(user_id), 'safe_repo')
def get_user_caption_preference(user_id): return user_caption_preferences.get(str(user_id), '')
async def set_rename_command(user_id, tag): user_rename_preferences[str(user_id)] = tag
async def set_caption_command(user_id, caption): user_caption_preferences[str(user_id)] = caption

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
    await gf.send_message(event.chat_id, "Customize your settings:", buttons=buttons)

@gf.on(events.CallbackQuery)
async def callback_query_handler(event):
    user_id = event.sender_id
    data = event.data

    if data.startswith(b'split_'):
        if user_id not in pending_video_splits:
            await event.answer("This request has expired. Please send the video again.", alert=True)
            return

        value = data.decode().split('_')[1]
        await event.delete()

        split_data = pending_video_splits[user_id] 

        if value == 'more':
            prompt_msg = await event.respond("📝 Please reply to this message with the number of parts you want (must be > 10).")
            split_data['prompt_msg_id'] = prompt_msg.id
            return

        split_data = pending_video_splits.pop(user_id) 
        file_path = split_data.get('file_path')
        original_thumb_path = split_data.get('thumb_path')
        temp_dir = tempfile.TemporaryDirectory()
        try:
            num_parts = int(value)
            await app.edit_message_text(user_id, split_data['edit_id'], f"Splitting video into {num_parts} parts...")
            await split_video_ffmpeg(file_path, num_parts, temp_dir.name)
            # Pass total_parts to upload_video_parts
            await upload_video_parts(app, output_dir=temp_dir.name, total_parts=num_parts, **split_data) 
            await app.delete_messages(user_id, split_data['edit_id'])
        except Exception as e:
            await app.edit_message_text(user_id, split_data['edit_id'], f"Error: {e}")
        finally:
            temp_dir.cleanup()
            if file_path and os.path.exists(file_path): os.remove(file_path)
            if original_thumb_path and os.path.exists(original_thumb_path): os.remove(original_thumb_path)
        return

    prompts = {
        b'setchat': ("Send me the ID of that chat:", 'setchat'),
        b'setrename': ("Send me the rename tag:", 'setrename'),
        b'setcaption': ("Send me the caption:", 'setcaption'),
        b'setreplacement': ("Format: 'WORD_TO_REPLACE' 'NEW_WORD'", 'setreplacement'),
        b'delete': ("Send words separated by a space to delete:", 'deleteword'),
    }
    if data in prompts:
        await event.respond(prompts[data][0])
        sessions[user_id] = prompts[data][1]
    elif data == b'addsession': await event.respond("This method is deprecated. Use /login.")
    elif data == b'logout':
        if mcollection.delete_one({"user_id": user_id}).deleted_count > 0: await event.respond("Logged out.")
        else: await event.respond("You are not logged in.")
    elif data == b'setthumb':
        pending_photos[user_id] = True
        await event.respond('Send the photo for the thumbnail.')
    elif data == b'remthumb':
        if os.path.exists(f'{user_id}.jpg'):
            os.remove(f'{user_id}.jpg')
            await event.respond('Thumbnail removed!')
        else: await event.respond("No thumbnail found.")
    elif data == b'reset':
        collection.update_one({"_id": user_id}, {"$set": {"delete_words": [], "replacement_words": {}}})
        await event.respond("All custom words/replacements have been reset.")


@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_video_splits and e.is_reply))
async def handle_split_reply(event):
    user_id = event.sender_id
    split_info = pending_video_splits.get(user_id, {})
    if not split_info or event.reply_to_msg_id != split_info.get('prompt_msg_id'):
        return

    try:
        num_parts = int(event.text)
        if num_parts <= 10: # This check is here, but the inline buttons handle <=10. Still good to have.
            await event.reply("The number must be greater than 10. Please try again.")
            return
    except ValueError:
        await event.reply("Invalid input. Please reply with a number.")
        return

    split_data = pending_video_splits.pop(user_id)
    file_path = split_data.get('file_path')
    original_thumb_path = split_data.get('thumb_path')
    temp_dir = tempfile.TemporaryDirectory()
    try:
        await event.client.delete_messages(event.chat_id, [event.id, split_data['prompt_msg_id']])
        await app.edit_message_text(user_id, split_data['edit_id'], f"Splitting video into {num_parts} parts...")
        await split_video_ffmpeg(file_path, num_parts, temp_dir.name)
        # Pass total_parts to upload_video_parts
        await upload_video_parts(app, output_dir=temp_dir.name, total_parts=num_parts, **split_data)
        await app.delete_messages(user_id, split_data['edit_id'])
    except Exception as e:
        await app.edit_message_text(user_id, split_data['edit_id'], f"An error occurred: {e}")
    finally:
        temp_dir.cleanup()
        if file_path and os.path.exists(file_path): os.remove(file_path)
        if original_thumb_path and os.path.exists(original_thumb_path): os.remove(original_thumb_path)


@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_photos))
async def save_thumbnail(event):
    user_id = event.sender_id
    if event.photo:
        thumb_path = f'./{user_id}.jpg'
        if os.path.exists(thumb_path): os.remove(thumb_path)
        await event.download_media(file=thumb_path)
        await event.respond('Thumbnail saved successfully!')
    else: await event.respond('Please send a photo.')
    pending_photos.pop(user_id, None)


@gf.on(events.NewMessage)
async def handle_user_input(event):
    user_id = event.sender_id
    if user_id in sessions:
        session_type = sessions.pop(user_id)
        text = event.text
        if session_type == 'setchat':
            try:
                user_chat_ids[user_id] = int(text)
                await event.respond("Chat ID set successfully!")
            except ValueError: await event.respond("Invalid chat ID!")
        elif session_type == 'setrename':
            await set_rename_command(user_id, text)
            await event.respond(f"Custom rename tag set to: {text}")
        elif session_type == 'setcaption':
            await set_caption_command(user_id, text)
            await event.respond(f"Custom caption set to: {text}")
        elif session_type == 'setreplacement':
            match = re.match(r"'(.+)' '(.+)'", text)
            if match:
                word, replace_word = match.groups()
                replacements = load_replacement_words(user_id)
                replacements[word] = replace_word
                save_replacement_words(user_id, replacements)
                await event.respond(f"Saved: '{word}' -> '{replace_word}'")
            else: await event.respond("Invalid format. Use: 'word to replace' 'new word'")
        elif session_type == 'deleteword':
            words = text.split()
            current_words = load_delete_words(user_id)
            current_words.update(words)
            save_delete_words(user_id, current_words)
            await event.respond(f"Words added to delete list.")
