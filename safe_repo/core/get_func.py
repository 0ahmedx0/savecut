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
    """Formats duration from seconds to a readable MM:SS format."""
    if seconds is None:
        return "00:00"
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"

# Dictionary to store pending video split requests
pending_video_splits = {}


async def split_video_ffmpeg(input_file, num_parts, output_dir):
    """Splits the video into a specified number of parts using ffmpeg."""
    metadata = video_metadata(input_file)
    duration_total = metadata.get('duration', 0)
    if duration_total <= 0:
        raise ValueError("Video duration is zero or invalid, cannot split.")
    split_duration = duration_total / num_parts

    for i in range(num_parts):
        start_time = i * split_duration
        output_file = os.path.join(output_dir, f"part{i+1}.mp4")
        command = [
            "ffmpeg",
            "-i", input_file,
            "-ss", str(start_time),
            "-t", str(split_duration),
            "-c", "copy",
            "-y",  # Overwrite output file if it exists
            output_file
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error_message = stderr.decode().strip()
            raise subprocess.CalledProcessError(process.returncode, command, output=stdout, stderr=error_message)


async def upload_video_parts(app, sender, edit_id, output_dir, msg, caption, width, height, duration, thumb_path, **kwargs):
    """Generates unique thumbnails for each part and uploads them in albums."""
    
    def get_part_number(filename):
        """Extracts the part number from a filename like 'part12.mp4'."""
        try:
            return int(re.search(r'part(\d+)', filename).group(1))
        except (AttributeError, ValueError):
            return 0

    part_files = sorted(
        [f for f in os.listdir(output_dir) if f.startswith("part") and f.endswith(".mp4")],
        key=get_part_number
    )

    media_group = []
    files_to_remove = []

    for part_file in part_files:
        part_path = os.path.join(output_dir, part_file)
        unique_thumb_path = None
        try:
            part_metadata = video_metadata(part_path)
            part_duration = int(part_metadata.get('duration', 0))
            part_width = part_metadata.get('width', width)
            part_height = part_metadata.get('height', height)
            formatted_part_duration = format_duration(part_duration)

            # Generate a unique thumbnail for each part
            unique_thumb_path = await screenshot(part_path, part_duration / 2, sender)

            # Create an InputMediaVideo object with unique data for each part
            media_group.append(InputMediaVideo(
                media=part_path,
                caption=f"{caption or ''} \n\n🎬 **{part_file}** | ⏳ **{formatted_part_duration}**",
                supports_streaming=True,
                height=part_height,
                width=part_width,
                duration=part_duration,
                thumb=unique_thumb_path
            ))
            files_to_remove.append((part_path, unique_thumb_path))
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f"Error processing {part_file}: {e}")
            if unique_thumb_path and os.path.exists(unique_thumb_path):
                os.remove(unique_thumb_path)
    
    # Function to chunk the list into groups of 10 for album upload
    def chunk_list(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    groups = list(chunk_list(media_group, 10))
    
    try:
        if media_group:
            for i, group in enumerate(groups):
                await app.edit_message_text(sender, edit_id, f"Uploading Album {i+1}/{len(groups)}...")
                safe_repos = await app.send_media_group(chat_id=sender, media=group)
                if msg.pinned_message:
                    for safe_repo in safe_repos:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except:
                            await safe_repo.pin()
        else:
            await app.edit_message_text(sender, edit_id, "No video parts found to upload.")
    except Exception as e:
        await app.edit_message_text(sender, edit_id, f"Error uploading album: {e}")
    finally:
        # Cleanup all temporary part files and their unique thumbnails
        for path, thumb in files_to_remove:
            if os.path.exists(path):
                os.remove(path)
            if thumb and os.path.exists(thumb):
                os.remove(thumb)
        # Cleanup original thumbnail
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)

async def get_msg(userbot, sender, edit_id, msg_link, i, message, is_batch_mode=False):
    """Main function to download, process, and upload/clone messages."""
    if "?single" in msg_link:
        msg_link = msg_link.split("?single")[0]
    
    msg_id = int(msg_link.split("/")[-1]) + i

    if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
        if 't.me/b/' in msg_link:
            chat = msg_link.split("/")[-2]
        else:
            chat = int('-100' + str(msg_link.split("/")[-2]))
        
        try:
            chatx = message.chat.id
            msg = await userbot.get_messages(chat, msg_id)

            if msg.service or msg.empty:
                return
            
            # Simplified media handling
            if not msg.media and msg.text:
                edit = await app.edit_message_text(sender, edit_id, "Cloning text message...")
                safe_repo = await app.send_message(sender, msg.text.markdown)
                if msg.pinned_message:
                    try:
                        await safe_repo.pin(both_sides=True)
                    except:
                        await safe_repo.pin()
                await edit.delete()
                return

            edit = await app.edit_message_text(sender, edit_id, "Trying to Download...")
            file = await userbot.download_media(
                msg,
                progress=progress_bar,
                progress_args=("**Downloading:**\n", edit, time.time())
            )
            
            # --- Caption and Filename Processing ---
            custom_rename_tag = get_user_rename_preference(chatx)
            file_name, file_ext = os.path.splitext(file)
            delete_words = load_delete_words(chatx)
            for word in delete_words:
                file_name = file_name.replace(word, "")
            new_file_name = file_name.strip() + " " + custom_rename_tag.strip() + file_ext
            os.rename(file, new_file_name)
            file = new_file_name
            
            original_caption = msg.caption or ''
            final_caption = original_caption
            replacements = load_replacement_words(chatx)
            for word, replace_word in replacements.items():
                final_caption = final_caption.replace(word, replace_word)
            custom_caption = get_user_caption_preference(chatx)
            if custom_caption:
                final_caption += f"\n\n__**{custom_caption}**__"
            
            await edit.edit('Trying to Upload...')

            if msg.media == MessageMediaType.VIDEO:
                metadata = video_metadata(file)
                width = metadata.get('width', 0)
                height = metadata.get('height', 0)
                duration = int(metadata.get('duration', 0))
                thumb_path = await screenshot(file, duration / 2, chatx)

                if not is_batch_mode and duration > 120:
                    pending_video_splits[sender] = {
                        'file_path': file, 'edit_id': edit_id, 'sender': sender, 'msg': msg, 'caption': final_caption,
                        'width': width, 'height': height, 'duration': duration, 'thumb_path': thumb_path
                    }
                    buttons = [
                        [Button.inline("4", b'split_4'), Button.inline("5", b'split_5'), Button.inline("6", b'split_6')],
                        [Button.inline("7", b'split_7'), Button.inline("8", b'split_8'), Button.inline("9", b'split_9')],
                        [Button.inline("10", b'split_10'), Button.inline("20", b'split_20'), Button.inline("30", b'split_30')],
                        [Button.inline("Custom 📝", b'split_more')]
                    ]
                    await gf.send_message(
                        sender,
                        f"💡 **Video is longer than 2 minutes ({format_duration(duration)})**.\nChoose number of parts to split into:",
                        buttons=buttons
                    )
                    await edit.delete()
                    return

                # Default action: Upload as single video (in batch mode, or if duration <= 120)
                await app.send_video(
                    chat_id=sender, video=file, caption=final_caption, height=height, width=width,
                    duration=duration, thumb=thumb_path, progress=progress_bar,
                    progress_args=('**Uploading:**\n', edit, time.time())
                )

            elif msg.media == MessageMediaType.PHOTO:
                await app.send_photo(chat_id=sender, photo=file, caption=final_caption)
                
            else: # Document or other types
                user_thumb = thumbnail(chatx)
                await app.send_document(
                    chat_id=sender, document=file, caption=final_caption, thumb=user_thumb,
                    progress=progress_bar, progress_args=('**Uploading:**\n', edit, time.time())
                )

            await edit.delete()
            if os.path.exists(file): os.remove(file)
            if 'thumb_path' in locals() and thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)

        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
            await app.edit_message_text(sender, edit_id, "Bot is not in the source channel or channel is private. Join it and retry.")
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\n**Error:** {e}')
    
    else: # Public channel clone
        edit = await app.edit_message_text(sender, edit_id, "Cloning...")
        try:
            chat = msg_link.split("/")[-2]
            await copy_message_with_chat_id(app, sender, chat, msg_id)
            await edit.delete()
        except Exception as e:
            await app.edit_message_text(sender, edit_id, f'Failed to clone: `{msg_link}`\n\n**Error:** {e}')


async def copy_message_with_chat_id(client, sender, chat_id, message_id):
    """Clones a message from a public chat, applying user's custom settings."""
    target_chat_id = user_chat_ids.get(sender, sender)
    try:
        msg = await client.get_messages(chat_id, message_id)

        # Process caption with user settings
        original_caption = msg.caption or ''
        delete_words = load_delete_words(sender)
        replacements = load_replacement_words(sender)
        processed_caption = original_caption
        for word in delete_words:
            processed_caption = processed_caption.replace(word, '')
        for word, replace_word in replacements.items():
            processed_caption = processed_caption.replace(word, replace_word)
        custom_caption = get_user_caption_preference(sender)
        final_caption = f"{processed_caption.strip()}\n\n__**{custom_caption}**__" if custom_caption else processed_caption.strip()
        
        result = None
        # Simplified media handling using file_id to avoid re-download
        if msg.media == MessageMediaType.VIDEO:
            result = await client.send_video(target_chat_id, msg.video.file_id, caption=final_caption)
        elif msg.media == MessageMediaType.DOCUMENT:
            result = await client.send_document(target_chat_id, msg.document.file_id, caption=final_caption)
        elif msg.media == MessageMediaType.PHOTO:
            result = await client.send_photo(target_chat_id, msg.photo.file_id, caption=final_caption)
        else:
            # copy_message for text or other media types where caption modification is not needed/possible
            result = await client.copy_message(target_chat_id, chat_id, message_id)

        if result and msg.pinned_message:
            try:
                await result.pin(both_sides=True)
            except:
                await result.pin()

    except Exception as e:
        await client.send_message(sender, f"Cloning Error: {e}\n\nMake sure Bot is admin in `{target_chat_id}`.")


# ------------------------ Settings and User Data Management ----------------------------

DB_NAME = "smart_users"
COLLECTION_NAME = "super_user"
mongo_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
db = mongo_client[DB_NAME]
collection = db[COLLECTION_NAME]

MDB_NAME = "logins"
MCOLLECTION_NAME = "stringsession"
m_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
mdb = m_client[MDB_NAME]
mcollection = mdb[MCOLLECTION_NAME]

user_chat_ids = {}
user_rename_preferences = {}
user_caption_preferences = {}
sessions = {}
pending_photos = {}


def load_delete_words(user_id):
    user_data = collection.find_one({"_id": user_id})
    return set(user_data.get("delete_words", [])) if user_data else set()

def save_delete_words(user_id, delete_words):
    collection.update_one({"_id": user_id}, {"$set": {"delete_words": list(delete_words)}}, upsert=True)

def load_replacement_words(user_id):
    user_data = collection.find_one({"_id": user_id})
    return user_data.get("replacement_words", {}) if user_data else {}

def save_replacement_words(user_id, replacements):
    collection.update_one({"_id": user_id}, {"$set": {"replacement_words": replacements}}, upsert=True)

async def set_rename_command(user_id, tag):
    user_rename_preferences[str(user_id)] = tag

def get_user_rename_preference(user_id):
    return user_rename_preferences.get(str(user_id), 'safe_repo')

async def set_caption_command(user_id, caption):
    user_caption_preferences[str(user_id)] = caption

def get_user_caption_preference(user_id):
    return user_caption_preferences.get(str(user_id), '')


# ------------------------ Telethon Event Handlers ----------------------------

@gf.on(events.NewMessage(incoming=True, pattern='/settings'))
async def settings_command(event):
    buttons = [
        [Button.inline("Set Chat ID", b'setchat'), Button.inline("Set Rename Tag", b'setrename')],
        [Button.inline("Custom Caption", b'setcaption'), Button.inline("Replace Words", b'setreplacement')],
        [Button.inline("Remove Words", b'delete'), Button.inline("Reset Words", b'reset')],
        [Button.inline("Login", b'addsession'), Button.inline("Logout", b'logout')],
        [Button.inline("Set Thumbnail", b'setthumb'), Button.inline("Remove Thumbnail", b'remthumb')],
        [Button.url("Help & Support", "https://t.me/safe_repo")]
    ]
    await gf.send_message(event.chat_id, "⚙️ Configure your settings:", buttons=buttons)


async def process_video_split(num_parts, split_data):
    """Centralized function to handle the ffmpeg splitting and uploading process."""
    sender = split_data['sender']
    # Create a new message for progress updates
    progress_msg = await app.send_message(sender, f"▶️ Splitting video into {num_parts} parts... Please wait.")
    edit_id = progress_msg.id
    
    temp_dir = tempfile.TemporaryDirectory()
    try:
        await split_video_ffmpeg(split_data['file_path'], num_parts, temp_dir.name)
        await upload_video_parts(app=app, sender=sender, edit_id=edit_id, output_dir=temp_dir.name, **split_data)
        await app.edit_message_text(sender, edit_id, "✅ Video parts uploaded successfully!")
        await asyncio.sleep(5)
        await app.delete_messages(sender, edit_id)
    except Exception as e:
        await app.edit_message_text(sender, edit_id, f"❌ **Error:** {e}")
    finally:
        temp_dir.cleanup()
        if os.path.exists(split_data['file_path']):
            os.remove(split_data['file_path'])

@gf.on(events.CallbackQuery)
async def callback_query_handler(event):
    user_id = event.sender_id
    data = event.data

    if data.startswith(b'split_'):
        split_request = pending_video_splits.pop(user_id, None)
        if not split_request:
            return await event.answer("This request has expired. Please send the link again.", alert=True)
        
        await event.delete()
        value = data.decode().split('_')[1]

        if value == 'more':
            prompt_msg = await event.respond("📝 Reply to this message with the desired number of parts.")
            split_request['prompt_msg_id'] = prompt_msg.id
            pending_video_splits[user_id] = split_request
            return
        
        try:
            num_parts = int(value)
            await process_video_split(num_parts, split_request)
        except (ValueError, Exception) as e:
            await app.send_message(split_request['sender'], f"Failed to start split process. Error: {e}")
            if os.path.exists(split_request.get('file_path')): os.remove(split_request['file_path'])
            if os.path.exists(split_request.get('thumb_path')): os.remove(split_request['thumb_path'])

    elif data == b'setchat':
        sessions[user_id] = 'setchat'
        await event.respond("Send the destination Chat ID (must be a number):")
    elif data == b'setrename':
        sessions[user_id] = 'setrename'
        await event.respond("Send the custom rename tag:")
    elif data == b'setcaption':
        sessions[user_id] = 'setcaption'
        await event.respond("Send the custom caption to append:")
    elif data == b'setreplacement':
        sessions[user_id] = 'setreplacement'
        await event.respond("Send words to replace in the format:\n`'word to find' 'word to replace with'`")
    elif data == b'delete':
        sessions[user_id] = 'deleteword'
        await event.respond("Send words to remove (separated by space):")
    elif data == b'reset':
        save_delete_words(user_id, [])
        save_replacement_words(user_id, {})
        await event.respond("✅ Your custom word lists have been cleared.")
    elif data == b'addsession':
        sessions[user_id] = 'addsession'
        await event.respond("Please send your Pyrogram V2 Session String:")
    elif data == b'logout':
        if mcollection.delete_one({"user_id": user_id}).deleted_count > 0:
            await event.respond("✅ You have been successfully logged out.")
        else:
            await event.respond("ℹ️ You were not logged in.")
    elif data == b'setthumb':
        pending_photos[user_id] = True
        await event.respond("Please send a photo to set as the default thumbnail for documents.")
    elif data == b'remthumb':
        if os.path.exists(f'{user_id}.jpg'):
            os.remove(f'{user_id}.jpg')
            await event.respond("✅ Thumbnail removed successfully.")
        else:
            await event.respond("ℹ️ No default thumbnail found to remove.")

@gf.on(events.NewMessage(func=lambda e: e.is_reply and e.sender_id in pending_video_splits))
async def handle_split_reply(event):
    user_id = event.sender_id
    split_request = pending_video_splits.get(user_id)
    if not split_request or event.reply_to_msg_id != split_request.get('prompt_msg_id'):
        return

    # Valid reply received, remove from pending
    split_request = pending_video_splits.pop(user_id)
    try:
        num_parts = int(event.text)
        if num_parts <= 1:
            raise ValueError("Number of parts must be greater than 1.")
        
        await process_video_split(num_parts, split_request)
        # Cleanup prompt messages
        await event.client.delete_messages(event.chat_id, [event.id, split_request['prompt_msg_id']])
    except (ValueError, Exception) as e:
        await event.respond(f"Error: {e}. Please try again.")
        # Cleanup files if process fails
        if os.path.exists(split_request['file_path']): os.remove(split_request['file_path'])
        if os.path.exists(split_request['thumb_path']): os.remove(split_request['thumb_path'])


@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_photos))
async def save_thumbnail(event):
    user_id = event.sender_id
    if event.photo and user_id in pending_photos:
        thumb_path = f'./{user_id}.jpg'
        if os.path.exists(thumb_path): os.remove(thumb_path)
        await event.download_media(file=thumb_path)
        await event.respond('✅ Thumbnail saved successfully!')
    else:
        await event.respond('⚠️ Please send a valid photo.')
    pending_photos.pop(user_id, None)

@gf.on(events.NewMessage(func=lambda e: e.sender_id in sessions))
async def handle_user_input(event):
    user_id = event.sender_id
    session_type = sessions.pop(user_id, None)
    if not session_type: return

    text = event.text.strip()
    
    if session_type == 'setchat':
        try:
            user_chat_ids[user_id] = int(text)
            await event.respond("✅ Chat ID set successfully!")
        except ValueError:
            await event.respond("⚠️ Invalid Chat ID. It must be a number.")
    
    elif session_type == 'setrename':
        await set_rename_command(user_id, text)
        await event.respond(f"✅ Custom rename tag set to: `{text}`")

    elif session_type == 'setcaption':
        await set_caption_command(user_id, text)
        await event.respond(f"✅ Custom caption set to: `{text}`")
    
    elif session_type == 'setreplacement':
        match = re.match(r"'(.+?)'\s+'(.+?)'", text)
        if not match:
            await event.respond("⚠️ Invalid format. Use: `'word to find' 'replacement'`")
        else:
            find, replace = match.groups()
            replacements = load_replacement_words(user_id)
            replacements[find] = replace
            save_replacement_words(user_id, replacements)
            await event.respond(f"✅ Replacement saved.")
            
    elif session_type == 'deleteword':
        words = text.split()
        current_words = load_delete_words(user_id)
        current_words.update(words)
        save_delete_words(user_id, current_words)
        await event.respond(f"✅ Words added to delete list.")

    elif session_type == 'addsession':
        mcollection.update_one({"user_id": user_id}, {"$set": {"session_string": text}}, upsert=True)
        await event.respond("✅ Session string saved. Please /restart the bot to apply changes.")
