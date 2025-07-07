--- START OF FILE get_func (1).py ---


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
    # Calculate actual duration per part including remainder on the last part
    split_duration = duration_total / num_parts

    for i in range(num_parts):
        start_time = i * split_duration
        # For the last part, split until the end of the video
        if i == num_parts - 1:
             command = [
                "ffmpeg",
                "-i", input_file,
                "-ss", str(start_time),
                "-c", "copy",  # Copy codec for faster splitting, re-encode if needed for compatibility
                os.path.join(output_dir, f"part{i+1}.mp4") # Assuming mp4 output, adjust if needed
            ]
        else:
            command = [
                "ffmpeg",
                "-i", input_file,
                "-ss", str(start_time),
                "-t", str(split_duration),
                "-c", "copy",  # Copy codec for faster splitting, re-encode if needed for compatibility
                os.path.join(output_dir, f"part{i+1}.mp4") # Assuming mp4 output, adjust if needed
            ]
        
        # Use asyncio.create_subprocess_exec for non-blocking execution if needed in larger bots
        # For now, keep it simple with subprocess.run, but acknowledge it's blocking
        process = subprocess.run(command, capture_output=True, text=True)
        if process.returncode != 0:
            print(f"Error splitting part {i+1}: {process.stderr}")
            # Consider raising an exception or handling the error appropriately
            pass # Allow it to try next parts

        # Note: video_metadata after -c copy split might show slightly off duration
        # Getting accurate duration per part often requires re-encoding or a more complex approach.
        # The current method is faster but duration display for *parts* might be estimations based on SS/T
        # For *original* duration display in prompt, we already have it.

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
        
        # Skip file if it doesn't exist or is empty after splitting
        if not os.path.exists(part_path) or os.path.getsize(part_path) == 0:
             print(f"Skipping {part_file}: file not found or empty.")
             continue

        part_thumb_path = None
        try:
            # استخراج بيانات الجزء من الفيديو للحصول على المدة والأبعاد الصحيحة
            # Using video_metadata on the actual split file provides more accurate per-part metadata
            part_metadata = video_metadata(part_path)
            part_duration = part_metadata.get('duration', 0) # Use .get for safety
            part_width = part_metadata.get('width', width)
            part_height = part_metadata.get('height', height)

            # التقاط الصورة المصغرة لكل جزء (اختياري، يمكن استخدام صورة مصغرة واحدة)
            # باستخدام original_thumb_path يوفر وقتًا وتكلفة
            # If you want unique thumbnails for each part, uncomment the line below:
            # part_thumb_path = await screenshot(part_path, part_duration, sender) 
            part_thumb_path = original_thumb_path # استخدم الصورة المصغرة الأصلية لجميع الأجزاء

            # إذا تم إنشاء صورة مصغرة لكل جزء (بعد تفعيل السطر السابق):
            # If part_thumb_path was generated uniquely:
            # if part_thumb_path and os.path.exists(part_thumb_path):
            #     # إنشاء اسم فريد للصورة المصغرة لكل جزء باستخدام UUID لضمان التفرد
            #     unique_thumb_path = os.path.join(output_dir, f"thumb_{os.path.splitext(part_file)[0]}_{uuid.uuid4().hex}.jpg")
            #     os.rename(part_thumb_path, unique_thumb_path)
            #     part_thumb_path = unique_thumb_path
            #     files_to_remove.append((part_path, part_thumb_path))
            # else:
            #     files_to_remove.append((part_path, None))

            files_to_remove.append((part_path, None)) # Add only the part path to remove if using single thumb

            # إنشاء كائن InputMediaVideo لكل جزء
            media = InputMediaVideo(
                media=part_path,
                caption=f"{caption} \n\n **{part_file}**\n__Duration: {round(part_duration)} seconds__", # Add duration for part
                supports_streaming=True,
                height=part_height,
                width=part_width,
                duration=part_duration,
                thumb=original_thumb_path # استخدم الصورة المصغرة الأصلية
            )
            media_group.append(media)
            
        except Exception as e:
            print(f"Error processing {part_file}: {e}")
            # await app.edit_message_text(sender, edit_id, f"Error processing {part_file}. Details: {e}")
            pass # Continue processing other parts


    # دالة لتقسيم القائمة إلى مجموعات متساوية بحيث توضع الفائض في الألبوم الأخير
    def split_evenly(lst, k):
        n = len(lst)
        if k <= 0: # Avoid division by zero or negative groups
             return [lst]
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

    # تحديد عدد الألبومات المطلوبة (كل ألبوم لا يزيد عن 10 رسائل)
    total_parts = len(media_group)
    if total_parts == 0:
         await app.edit_message_text(sender, edit_id, "No video parts were successfully processed to upload.")
         return # Exit if no valid parts were added
         
    album_size_limit = 10 # Telegram album limit
    album_count = math.ceil(total_parts / album_size_limit)
    groups = split_evenly(media_group, album_count)

    # رفع الألبومات على دفعات بحيث تكون متوزعة بشكل متساوٍ
    try:
        if media_group:
            for group in groups:
                 if not group: continue # Skip empty groups

                # Handle potential errors during send_media_group
                 try:
                     safe_repos = await app.send_media_group(
                         chat_id=sender,
                         media=group,
                         # You might need to add a caption to the first item in the group 
                         # for it to be displayed on the album message in some clients.
                         # For example: group[0].caption = f"{caption} (Album Part X)"
                     )
                     # تطبيق العمليات الإضافية لكل رسالة في الألبوم المُرسل (مثل التثبيت)
                     for safe_repo in safe_repos:
                         if msg and msg.pinned_message: # Check if msg object is valid
                            try:
                                await safe_repo.pin(both_sides=True)
                            except Exception as e:
                                print(f"Error pinning message: {e}")
                                await safe_repo.pin() # Try pinning on one side
                         # يمكن تفعيل النسخ إلى مجموعة السجلات إذا لزم الأمر:
                         # if log_group: await safe_repo.copy(log_group)
                 except Exception as group_upload_error:
                    print(f"Error uploading media group: {group_upload_error}")
                    # Provide feedback to the user about the failed group upload
                    await app.send_message(sender, f"Error uploading a group of video parts: {group_upload_error}")

        else:
            await app.edit_message_text(sender, edit_id, "No valid video parts found to upload.")
    except Exception as e:
        print(f"Overall error during album upload process: {e}")
        await app.edit_message_text(sender, edit_id, f"Overall error during album upload process: {e}")
    finally:
        # إزالة الملفات المؤقتة بعد الرفع
        for part_path, thumb_path in files_to_remove:
            try:
                if os.path.exists(part_path):
                     os.remove(part_path)
            except Exception as file_remove_err:
                 print(f"Error removing part file {part_path}: {file_remove_err}")

            # if thumb_path and os.path.exists(thumb_path):
            #     try:
            #         os.remove(thumb_path)
            #     except Exception as thumb_remove_err:
            #          print(f"Error removing thumb file {thumb_path}: {thumb_remove_err}")


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
            
            # إضافة نقطة قبل الامتداد إذا لم تكن موجودة (إصلاح بسيط)
            if not new_file_name.endswith(f'.{file_extension}'):
                new_file_name = f"{original_file_name} {custom_rename_tag}.{file_extension}"

            os.rename(file, new_file_name)
            file = new_file_name

            # CODES are hidden

            await edit.edit('Trying to Uplaod ...')

            # تأكد من أن الملف الذي تم تنزيله هو الفيديو المقصود قبل معالجة الميتا داتا والتقسيم
            if msg.media == MessageMediaType.VIDEO and os.path.exists(file):

                metadata = video_metadata(file)
                width= metadata.get('width', 0) # Use .get with default values
                height= metadata.get('height', 0)
                duration= metadata.get('duration', 0)
                original_thumb_path = await screenshot(file, duration, chatx) # إنشاء الصورة المصغرة الأصلية مرة واحدة فقط

                # Convert duration to int for comparison
                duration_int = int(duration)

                if duration_int <= 120: # Modified condition, upload directly if video is 2 minutes or less
                    safe_repo = await app.send_video(
                        chat_id=sender,
                        video=file,
                        caption=caption, # Use the original caption if needed, or modify
                        height=height,
                        width=width,
                        duration=duration_int, # Use int duration here
                        thumb=original_thumb_path, # استخدام الصورة المصغرة الأصلية هنا
                        progress=progress_bar,
                        progress_args=('**UPLOADING:**\n', edit, time.time())
                    ) 
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()
                   #await safe_repo.copy(LOG_GROUP)
                    await edit.delete()
                    os.remove(file) # Remove file after direct upload
                    if original_thumb_path and os.path.exists(original_thumb_path):
                        try:
                            os.remove(original_thumb_path)
                        except:
                             pass
                    return

                # تعديل الشرط هنا: السؤال عن التقسيم فقط إذا لم يكن في وضع الباتش
                if not is_batch_mode:
                    # إضافة المدة إلى بيانات التقسيم المعلقة
                    pending_video_splits[sender] = {
                        'file_path': file,
                        'edit_id': edit_id,
                        'sender': sender,
                        'msg': msg,
                        'caption': caption,
                        'width': width,
                        'height': height,
                        'duration': duration, # حفظ المدة الأصلية
                        'thumb_path': original_thumb_path, # تمرير الصورة المصغرة الأصلية هنا
                        'log_group': LOG_GROUP,
                        'chatx': chatx
                    }
                    # --- START OF MODIFICATION: إضافة أزرار التقسيم للمدة ---
                    buttons = [
                        [Button.inline("4 أجزاء", b'split_4'), Button.inline("5 أجزاء", b'split_5')],
                        [Button.inline("6 أجزاء", b'split_6'), Button.inline("7 أجزاء", b'split_7')],
                        [Button.inline("8 أجزاء", b'split_8'), Button.inline("9 أجزاء", b'split_9')],
                        [Button.inline("10 أجزاء", b'split_10')],
                        # إضافة أزرار 20 و 30 جزء
                        [Button.inline("20 أجزاء", b'split_20'), Button.inline("30 أجزاء", b'split_30')],
                        [Button.inline("أكثر من 10 📝", b'split_more')]
                    ]
                    # تعديل نص الرسالة لعرض المدة الفعلية للفيديو
                    await gf.send_message(
                        sender,
                        f"💡 الفيديو أطول من دقيقتين ({round(duration)} ثانية)، اختر عدد الأجزاء للتقسيم:",
                        buttons=buttons
                    )
                    # --- END OF MODIFICATION: إضافة أزرار التقسيم للمدة ---
                    return  # لا تكمل أي شيء بعد هذا
                else: # إذا كان في وضع الباتش، يتم رفعه كجزء واحد تلقائياً
                    await app.edit_message_text(sender, edit_id, f"Video is longer than 2 minutes ({round(duration)} seconds). Uploading as single part in batch mode...") # تم تعديل الرسالة لتعكس الدقيقتين والمدة
                    # رفع الفيديو كجزء واحد مباشرة في وضع الباتش (يمكنك تعديل هذا الجزء إذا كنت تريد سلوكاً مختلفاً)
                    try:
                        safe_repo = await app.send_video(
                            chat_id=sender,
                            video=file,
                            caption=caption, # Use the original caption
                            supports_streaming=True,
                            height=height,
                            width=width,
                            duration=duration_int, # Use int duration here
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
                    except Exception as upload_err:
                         print(f"Error uploading video in batch mode: {upload_err}")
                         await app.edit_message_text(sender, edit_id, f"Error uploading video in batch mode. Bot might not be admin or other issue. Details: {upload_err}")
                    
                    # Cleanup
                    if os.path.exists(file): os.remove(file)
                    if original_thumb_path and os.path.exists(original_thumb_path):
                        try:
                            os.remove(original_thumb_path)
                        except:
                            pass
                    await edit.delete()
                    return

            # Handle Photo upload (Existing logic - no changes needed based on request)
            elif msg.media == MessageMediaType.PHOTO and os.path.exists(file):
                await edit.edit("**`Uploading photo...`")
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''
                
                # Build the final caption based on preferences
                final_caption = original_caption
                
                # Apply word deletions
                for word in delete_words:
                    final_caption = final_caption.replace(word, '')
                    
                # Apply replacements after deletions
                replacements = load_replacement_words(sender)
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)

                # Add custom caption if set
                caption_text_to_send = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else final_caption
                
                target_chat_id = user_chat_ids.get(sender, sender)
                try:
                    safe_repo = await app.send_photo(chat_id=target_chat_id, photo=file, caption=caption_text_to_send) # Use modified caption
                    if msg.pinned_message:
                        try:
                            await safe_repo.pin(both_sides=True)
                        except Exception as e:
                            await safe_repo.pin()
                    #await safe_repo.copy(LOG_GROUP)
                except Exception as photo_upload_err:
                     print(f"Error uploading photo: {photo_upload_err}")
                     await app.edit_message_text(sender, edit_id, f"Error uploading photo. Bot might not be admin or other issue. Details: {photo_upload_err}")

                # Cleanup
                if os.path.exists(file): os.remove(file)
                await edit.delete()


            # Handle Document/Other media upload (Existing logic - no changes needed based on request)
            else:
                thumb_path = thumbnail(chatx)
                delete_words = load_delete_words(sender)
                custom_caption = get_user_caption_preference(sender)
                original_caption = msg.caption if msg.caption else ''

                 # Build the final caption based on preferences
                final_caption = original_caption
                
                # Apply word deletions
                for word in delete_words:
                    final_caption = final_caption.replace(word, '')
                    
                # Apply replacements after deletions
                replacements = load_replacement_words(chatx) # Check if using sender or chatx for document captions
                for word, replace_word in replacements.items():
                    final_caption = final_caption.replace(word, replace_word)

                # Add custom caption if set
                caption_text_to_send = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else final_caption

                target_chat_id = user_chat_ids.get(chatx, chatx) # Check if using sender or chatx
                
                if not os.path.exists(file):
                     await app.edit_message_text(sender, edit_id, "Downloaded file not found.")
                     return # Exit if file doesn't exist after download

                try:
                    safe_repo = await app.send_document(
                        chat_id=target_chat_id,
                        document=file,
                        caption=caption_text_to_send, # Use modified caption
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
                except Exception as doc_upload_err:
                    print(f"Error uploading document: {doc_upload_err}")
                    await app.edit_message_text(sender, edit_id, f"Error uploading document. Bot might not be admin or other issue. Details: {doc_upload_err}")

                # Cleanup
                if os.path.exists(file): os.remove(file)
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        os.remove(thumb_path)
                    except:
                        pass

            await edit.delete()

        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
            await app.edit_message_text(sender, edit_id, "Have you joined the channel?")
            # Ensure cleanup of downloaded file and thumb on error
            if 'file' in locals() and os.path.exists(file): os.remove(file)
            if 'original_thumb_path' in locals() and original_thumb_path and os.path.exists(original_thumb_path):
                try: os.remove(original_thumb_path)
                except: pass

            return
        except Exception as e:
            print(f"An unexpected error occurred: {str(e)}") # Log unexpected errors
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')
             # Ensure cleanup of downloaded file and thumb on error
            if 'file' in locals() and os.path.exists(file): os.remove(file)
            if 'original_thumb_path' in locals() and original_thumb_path and os.path.exists(original_thumb_path):
                try: os.remove(original_thumb_path)
                except: pass


    # Handle public channel/group links using copy_message (Existing logic - no changes needed based on request)
    else:
        edit = await app.edit_message_text(sender, edit_id, "Cloning...")
        try:
            chat = msg_link.split("/")[-2]
            await copy_message_with_chat_id(app, sender, chat, msg_id)
            await edit.delete()
        except Exception as e:
            print(f"Error cloning public message: {str(e)}") # Log unexpected errors
            await app.edit_message_text(sender, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')


async def copy_message_with_chat_id(client, sender, chat_id, message_id):
    # Get the user's set chat ID, if available; otherwise, use the original sender ID
    target_chat_id = user_chat_ids.get(sender, sender)

    try:
        # Fetch the message using get_message
        msg = await client.get_messages(chat_id, message_id)

        # Handle deleted or inaccessible message
        if not msg or msg.empty or msg.service:
             await client.send_message(sender, f"Could not fetch message {message_id} from {chat_id}.")
             return

        # Modify the caption based on user's custom caption preference
        custom_caption = get_user_caption_preference(sender)
        original_caption = msg.caption if msg.caption else ''
        
        final_caption = original_caption
        
        delete_words = load_delete_words(sender)
        for word in delete_words:
            final_caption = final_caption.replace(word, '') # Replace with empty string or '  ' ?

        replacements = load_replacement_words(sender) # Check if using sender or chat_id for copy message captions
        for word, replace_word in replacements.items():
            final_caption = final_caption.replace(word, replace_word)

        caption_text_to_send = f"{final_caption}\n\n__**{custom_caption}**__" if custom_caption else final_caption

        if msg.media:
            # Use send_ methods for potentially modified captions/thumbs
            # Note: file_id might expire or have restrictions
            try:
                if msg.media == MessageMediaType.VIDEO and msg.video:
                    # You might need to regenerate thumb or use original if you want caption/thumb modification
                    # For simplicity with copy, often it's just copy_message
                    # If modification is required, download and re-upload is safer.
                    # Sticking to copy_message for public links as requested (simpler, might lose custom caption/thumb)
                    result = await client.copy_message(target_chat_id, chat_id, message_id, caption=caption_text_to_send if caption_text_to_send != original_caption else None) # Only set caption if modified? copy_message caption is limited
                elif msg.media == MessageMediaType.DOCUMENT and msg.document:
                     result = await client.copy_message(target_chat_id, chat_id, message_id, caption=caption_text_to_send if caption_text_to_send != original_caption else None)
                elif msg.media == MessageMediaType.PHOTO and msg.photo:
                     result = await client.copy_message(target_chat_id, chat_id, message_id, caption=caption_text_to_send if caption_text_to_send != original_caption else None)
                else:
                    # Use copy_message for any other media types
                    result = await client.copy_message(target_chat_id, chat_id, message_id)
            except Exception as send_media_err:
                 print(f"Attempting copy_message instead after failed send_media attempt: {send_media_err}")
                 result = await client.copy_message(target_chat_id, chat_id, message_id)

        else:
            # Use copy_message if there is no media
            result = await client.copy_message(target_chat_id, chat_id, message_id, caption=caption_text_to_send) # Apply custom caption to text message


        # Attempt to copy the result to the LOG_GROUP
        try:
            if LOG_GROUP: await result.copy(LOG_GROUP)
        except Exception as log_copy_err:
            print(f"Error copying to LOG_GROUP: {log_copy_err}")
            pass # Ignore logging error


        # Pin the message if the original was pinned
        if msg.pinned_message and result: # Check if result is valid
            try:
                await result.pin(both_sides=True)
            except Exception as pin_err:
                print(f"Error pinning copied message: {pin_err}")
                await result.pin() # Try pinning on one side

    except Exception as e:
        error_message = f"Error occurred while processing or sending message to chat ID {target_chat_id}: {str(e)}"
        print(error_message) # Log the error
        await client.send_message(sender, error_message)
        # Add a more specific message if it seems related to bot permissions
        if "not a member of the chat" in str(e).lower() or "chat not found" in str(e).lower() or "CHANNEL_PRIVATE" in str(e):
             await client.send_message(sender, f"Make Bot admin in your Channel/Group ({target_chat_id}) and restart the process after /cancel if needed.")
        elif "MESSAGE_ID_INVALID" in str(e):
             await client.send_message(sender, f"Message ID {message_id} in chat {chat_id} is invalid or deleted.")


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
    try:
        for user_doc in collection.find():
            if "user_id" in user_doc:
                authorized_users.add(user_doc["user_id"])
    except Exception as e:
        print(f"Error loading authorized users: {e}")
    return authorized_users

def save_authorized_users(authorized_users):
    """
    Save authorized user IDs to the MongoDB collection
    """
    try:
        collection.delete_many({})
        for user_id in authorized_users:
            collection.insert_one({"user_id": user_id})
    except Exception as e:
         print(f"Error saving authorized users: {e}")


SUPER_USERS = load_authorized_users()

# Define a dictionary to store user chat IDs
# Should probably load these from DB too if they are persistent
user_chat_ids = {}

# MongoDB database name and collection name for sessions
MDB_NAME = "logins" # Using a different DB name for sessions as in the original code
MCOLLECTION_NAME = "stringsession"

# Establish a connection to MongoDB for sessions
m_client = pymongo.MongoClient(MONGODB_CONNECTION_STRING)
mdb = m_client[MDB_NAME]
mcollection = mdb[MCOLLECTION_NAME]


# Functions for loading/saving user preferences from the main collection (smart_users/super_user)
# Note: The current implementation uses _id as user_id for delete/replacement/caption/rename preferences.
# Consistency is important: either use user_id field for everything or _id for user preferences.
# The code below uses _id for preferences, which is consistent with how save_delete_words etc. are written.
# The SUPER_USERS uses the 'user_id' field.
# It might be better to standardize on using the 'user_id' field for everything in the 'super_user' collection.
# For now, preserving the original structure.

def load_delete_words(user_id):
    """
    Load delete words for a specific user from MongoDB
    """
    try:
        # Assuming preferences are stored with _id = user_id
        words_data = collection.find_one({"_id": user_id}) 
        if words_data:
            return set(words_data.get("delete_words", []))
        else:
            return set()
    except Exception as e:
        print(f"Error loading delete words for user {user_id}: {e}")
        return set()

def save_delete_words(user_id, delete_words):
    """
    Save delete words for a specific user to MongoDB
    """
    try:
        # Store preferences with _id = user_id
        collection.update_one(
            {"_id": user_id},
            {"$set": {"delete_words": list(delete_words)}},
            upsert=True
        )
    except Exception as e:
        print(f"Error saving delete words for user {user_id}: {e}")

def load_replacement_words(user_id):
    try:
        # Assuming preferences are stored with _id = user_id
        words_data = collection.find_one({"_id": user_id})
        if words_data:
            return words_data.get("replacement_words", {})
        else:
            return {}
    except Exception as e:
        print(f"Error loading replacement words for user {user_id}: {e}")
        return {}

def save_replacement_words(user_id, replacements):
    try:
        # Store preferences with _id = user_id
        collection.update_one(
            {"_id": user_id},
            {"$set": {"replacement_words": replacements}},
            upsert=True
        )
    except Exception as e:
        print(f"Error saving replacement words for user {user_id}: {e}")

# Functions for loading/saving rename/caption preferences from DB would be similar
# For now, user_rename_preferences and user_caption_preferences are in-memory.
# To make them persistent, load/save them to the 'super_user' collection with _id=user_id.

# Initialize the dictionary to store user preferences for renaming (in-memory)
user_rename_preferences = {}
# Load existing rename preferences from DB if stored (assuming they are in the 'super_user' collection)
# You would need to add loading logic here, similar to load_delete_words
# Example (conceptual):
# def load_rename_preference(user_id):
#    user_data = collection.find_one({"_id": user_id})
#    return user_data.get("custom_rename_tag") if user_data else 'safe_repo'
# def save_rename_preference(user_id, tag):
#    collection.update_one({"_id": user_id}, {"$set": {"custom_rename_tag": tag}}, upsert=True)

# Initialize the dictionary to store user caption preferences (in-memory)
user_caption_preferences = {}
# Load existing caption preferences from DB if stored (similar to rename)
# Example (conceptual):
# def load_caption_preference(user_id):
#    user_data = collection.find_one({"_id": user_id})
#    return user_data.get("custom_caption") if user_data else ''
# def save_caption_preference(user_id, caption):
#    collection.update_one({"_id": user_id}, {"$set": {"custom_caption": caption}}, upsert=True)


# Function to get the user's custom renaming preference (currently reads from memory)
def get_user_rename_preference(user_id):
    # Retrieve the user's custom renaming tag if set in memory, or default to 'safe_repo'
    # For persistence, load from DB here or load all into memory on startup
    return user_rename_preferences.get(str(user_id), 'safe_repo')

# Function to handle the /setrename command (currently saves to memory)
async def set_rename_command(user_id, custom_rename_tag):
    # Update the user_rename_preferences dictionary (in-memory)
    user_rename_preferences[str(user_id)] = custom_rename_tag
    # To make persistent, call save_rename_preference(user_id, custom_rename_tag)

# Function to get the user's custom caption preference (currently reads from memory)
def get_user_caption_preference(user_id):
    # Retrieve the user's custom caption if set in memory, or default to an empty string
    # For persistence, load from DB here or load all into memory on startup
    return user_caption_preferences.get(str(user_id), '')

# Function to set custom caption preference (currently saves to memory)
async def set_caption_command(user_id, custom_caption):
    # Update the user_caption_preferences dictionary (in-memory)
    user_caption_preferences[str(user_id)] = custom_caption
    # To make persistent, call save_caption_preference(user_id, custom_caption)


# Function to load user session string from MongoDB (using the 'logins' collection)
def load_user_session_string(user_id):
    """Loads session string for a user from the logins collection."""
    try:
        user_data = mcollection.find_one({"user_id": user_id})
        if user_data:
            return user_data.get("session_string")
        else:
            return None
    except Exception as e:
        print(f"Error loading session string for user {user_id}: {e}")
        return None

# Dictionary to store temporary state for settings interactions (in-memory)
sessions = {}

SET_PIC = "settings.jpg" # Unused variable in the current code
MESS = "Customize by your end and Configure your settings ..."

@gf.on(events.NewMessage(incoming=True, pattern='/settings'))
async def settings_command(event):
    # Ensure only authorized users can access settings if needed, otherwise remove this check
    # if event.sender_id not in SUPER_USERS:
    #     await event.reply("You are not authorized to use this command.")
    #     return

    buttons = [
        # Consider adding load/save options for current in-memory settings (rename, caption, chat_id)
        [Button.inline("Set Chat ID", b'setchat'), Button.inline("Set Rename Tag", b'setrename')],
        [Button.inline("Caption", b'setcaption'), Button.inline("Replace Words", b'setreplacement')],
        [Button.inline("Remove Words", b'delete'), Button.inline("Reset Delete/Replace", b'reset_filters')], # Renamed reset for clarity
        [Button.inline("Login Userbot", b'login'), Button.inline("Logout Userbot", b'logout')], # Renamed addsession to login
        [Button.inline("Set Thumbnail", b'setthumb'), Button.inline("Remove Thumbnail", b'remthumb')],
        [Button.url("Report Errors", "https://t.me/safe_repo")],
         # [Button.inline("Show Settings", b'show_settings')] # Optional: Add a button to show current settings
    ]

    # Add image if SET_PIC exists
    if os.path.exists(SET_PIC):
         await gf.send_file(
             event.chat_id,
             file=SET_PIC,
             caption=MESS,
             buttons=buttons
         )
    else:
         await gf.send_message(
             event.chat_id,
             message=MESS,
             buttons=buttons
         )


# Dictionary to store user ID who is expected to send a photo for thumbnail
pending_photos = {} 
# Dictionary to store user ID who is expected to reply with a number for splitting > 10
pending_split_reply = {} # To handle user reply for split parts


@gf.on(events.CallbackQuery)
async def callback_query_handler(event):
    user_id = event.sender_id

    # Check if the user is authorized to use settings callback queries if needed
    # if user_id not in SUPER_USERS:
    #     await event.answer("You are not authorized.")
    #     return

    query_data = event.data

    if query_data == b'setchat':
        await event.answer() # Answer the callback query
        await event.respond("Send me the ID of that chat where messages will be forwarded:")
        sessions[user_id] = 'setchat'

    elif query_data == b'setrename':
        await event.answer()
        await event.respond("Send me the rename tag you want to append to filenames:")
        sessions[user_id] = 'setrename'

    elif query_data == b'setcaption':
        await event.answer()
        await event.respond("Send me the custom caption text you want to append (or reply 'clear' to remove):")
        sessions[user_id] = 'setcaption'

    elif query_data == b'setreplacement':
        await event.answer()
        await event.respond("Send me the replacement words in the format: 'WORD(s)' 'REPLACEWORD'. You can send multiple pairs separated by a newline.\nExample:\n'BadWord' 'GoodWord'\n'OldPhrase' 'New Phrase'")
        sessions[user_id] = 'setreplacement'

    elif query_data == b'login': # Renamed from addsession
        await event.answer()
        await event.respond("Please send your Pyrogram session string now.")
        sessions[user_id] = 'addsession_string' # Use a specific state for receiving the string

    elif query_data == b'delete':
        await event.answer()
        await event.respond("Send words separated by space or newline to add them to the delete list for captions/filenames:")
        sessions[user_id] = 'deleteword'

    elif query_data == b'logout':
        await event.answer("Attempting to log out userbot...")
        try:
            # Delete session from DB
            result = mcollection.delete_one({"user_id": user_id})
            if result.deleted_count > 0:
                # Remove session from in-memory storage as well
                if user_id in sessions: # Sessions dictionary is for callback state, not userbot sessions
                    # Remove userbot session from wherever it's stored/managed if necessary
                    pass # Implement logic to remove userbot client if it was running

                await event.respond("Logged out and deleted userbot session successfully.")
            else:
                await event.respond("No userbot session found for your account.")
        except Exception as e:
             await event.respond(f"Error logging out: {e}")


    elif query_data == b'setthumb':
        await event.answer()
        pending_photos[user_id] = True # Mark user as pending to send a photo
        await event.respond('Please send the photo you want to set as the thumbnail.')

    elif query_data == b'reset_filters': # Renamed from reset
        await event.answer("Attempting to reset delete/replace filters...")
        try:
            # Clear delete words and replacement words for the user in DB
            collection.update_one(
                {"_id": user_id},
                {"$unset": {"delete_words": "", "replacement_words": ""}}
            )
            await event.respond("All delete words and replacement words have been removed for your account.")
        except Exception as e:
            await event.respond(f"Error resetting filters: {e}")

    elif query_data == b'remthumb':
        await event.answer("Attempting to remove thumbnail...")
        try:
            # Remove the thumbnail file for the user
            if os.path.exists(f'./{user_id}.jpg'):
                os.remove(f'./{user_id}.jpg')
                await event.respond('Thumbnail removed successfully!')
            else:
                 await event.respond("No thumbnail found to remove.")
        except Exception as e:
             await event.respond(f"Error removing thumbnail: {e}")


    # ------- [قسم معالجة أزرار التقسيم] -------
    elif query_data.startswith(b'split_'):
        await event.answer() # أجب على الكولباك كويري فوراً
        value = query_data.decode().split('_')[1]

        # إزالة رسالة الأزرار بغض النظر عن الخيار المختار لتنظيف الدردشة
        try:
            await event.delete() 
        except Exception as del_err:
            print(f"Error deleting split options message: {del_err}")
            pass # لا تتوقف إذا فشل الحذف

        if value == 'more':
            await event.respond("📝 اكتب العدد المطلوب (أكبر من 10) كرد على هذه الرسالة.")
            pending_split_reply[user_id] = True # ضع المستخدم في وضع انتظار الرد لعدد الأجزاء
            return # لا تكمل أي شيء آخر، انتظر الرد

        # إذا لم يكن الخيار "أكثر من 10"، عالج التقسيم مباشرة
        try:
            num_parts = int(value)
            if num_parts <= 0:
                await event.respond("Please enter a positive number of parts.")
                # Optional: Put user back in pending state if they clicked an invalid button (shouldn't happen with hardcoded buttons)
                # pending_split_reply[user_id] = True
                return
            
            # تأكد من وجود بيانات التقسيم لهذا المستخدم
            if user_id in pending_video_splits:
                # --- START OF MODIFICATION: استرجاع بيانات التقسيم والمدة ---
                split_data = pending_video_splits.pop(user_id)  # الحصول على البيانات المخزنة وإزالتها من الانتظار
                file_path = split_data['file_path']
                edit_id = split_data['edit_id']
                sender = split_data['sender'] # غالبا هو user_id
                msg = split_data['msg']
                caption = split_data['caption']
                width = split_data['width']
                height = split_data['height']
                duration = split_data['duration'] # استرجاع المدة الأصلية
                original_thumb_path = split_data['thumb_path']  
                log_group = split_data['log_group']
                chatx = split_data['chatx']
                # --- END OF MODIFICATION: استرجاع بيانات التقسيم والمدة ---


                await app.edit_message_text(sender, edit_id, f"Splitting video into {num_parts} parts...")
                temp_dir = tempfile.TemporaryDirectory()  # إنشاء مجلد مؤقت للأجزاء
                try:
                    await split_video_ffmpeg(file_path, num_parts, temp_dir.name)
                    await app.edit_message_text(sender, edit_id, "Uploading video parts...")
                    # --- START OF MODIFICATION: تمرير original_thumb_path ---
                    await upload_video_parts(app, sender, edit_id, temp_dir.name, msg, caption, width, height, duration, original_thumb_path, log_group) # تمرير original_thumb_path
                    # --- END OF MODIFICATION: تمرير original_thumb_path ---
                    
                    await app.edit_message_text(sender, edit_id, "Video parts uploaded successfully!")
                    
                    # الانتظار قليلا ثم حذف رسالة البوت الأصلية التي تم تعديلها
                    # لا تحذف رسالة المستخدم هنا، سيتم حذفها في handle_split_reply إذا كان ردا.
                    # إذا لم يكن ردا، هذه الدالة (callback_query_handler) هي التي تعمل
                    # وربما تحتاج منطقا منفصلا لحذف الرسالة التي أرسلها البوت والتي تحمل الأزرار
                    # وقد قمنا بإضافته بالفعل فوق باستخدام event.delete().
                    
                    await asyncio.sleep(5)
                    try:
                       # محاولة حذف رسالة "Splitting/Uploading..." التي تم تعديلها
                       # باستخدام edit_id
                       await app.delete_messages(sender, edit_id) 
                    except Exception as del_bot_msg_err:
                        print(f"Error deleting bot's progress message: {del_bot_msg_err}")


                except Exception as split_err:
                    print(f"Error splitting or uploading video parts via button: {split_err}") # Log the error
                    try:
                        await app.edit_message_text(sender, edit_id, f"Error splitting or uploading video parts: {split_err}")
                    except Exception as edit_err:
                        print(f"Error editing message after split error: {edit_err}")

                finally:
                    # التأكد من إزالة الملف الأصلي والمجلد المؤقت والصورة المصغرة
                    if os.path.exists(file_path):
                        try: os.remove(file_path)
                        except: pass
                    if original_thumb_path and os.path.exists(original_thumb_path):
                         try: os.remove(original_thumb_path)
                         except: pass

                    try:
                        temp_dir.cleanup()  # تنظيف المجلد المؤقت
                    except Exception as tmp_cleanup_err:
                         print(f"Error cleaning up temp dir: {tmp_cleanup_err}")

            else:
                 # هذا يعني أن المستخدم ضغط على زر التقسيم ولكن لم يتم العثور على بيانات فيديو معلقة له
                 await event.respond("No pending video task found for splitting. Please try sending the video link/file again.")


        except ValueError:
            # هذا الخطأ يجب ألا يحدث مع الأزرار المحددة مسبقًا (4, 5, ..., 30)
            await event.respond("Invalid value received from button.") # يمكن إزالة هذه الرسالة
        except KeyError:
            # هذا الخطأ يشير إلى أن split_data['key'] غير موجودة
            print("KeyError: Missing data in pending_video_splits entry.")
            await event.respond("An internal error occurred (missing video data). Please try again.")


# Handler for when user replies with a number for splitting > 10
@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_split_reply and e.reply_to_msg_id is not None))
async def handle_split_reply(event):
    user_id = event.sender_id

    # Retrieve the replied message to check if it was the bot's split prompt
    # Using try-except in case the replied message is gone
    try:
        replied_message = await event.get_reply_message()
        # Check if the replied message is likely the bot's prompt for splitting
        # You might need a more robust check (e.g., store the bot's prompt message ID)
        if replied_message and "اكتب العدد المطلوب" in replied_message.text:
            # This is the expected reply
            try:
                num_parts = int(event.text.strip()) # استخدم strip() لإزالة المسافات البيضاء الزائدة

                if num_parts <= 0:
                    await event.reply("Please enter a positive number of parts.")
                    # Keep user in pending state to reply again
                    return
                
                # Check if the user actually has a pending video split task
                if user_id not in pending_video_splits:
                    await event.reply("No pending video task found for splitting. Please try sending the video link/file again.")
                    pending_split_reply.pop(user_id, None) # Remove user from pending state
                    return

                # --- START OF MODIFICATION: استرجاع بيانات التقسيم والمدة ---
                split_data = pending_video_splits.pop(user_id)  # Get stored data and remove from pending_video_splits
                pending_split_reply.pop(user_id, None) # Remove user from pending_split_reply state

                file_path = split_data['file_path']
                edit_id = split_data['edit_id']
                sender = split_data['sender'] # This is likely the user_id
                msg = split_data['msg']
                caption = split_data['caption']
                width = split_data['width']
                height = split_data['height']
                duration = split_data['duration'] # Get original duration
                original_thumb_path = split_data['thumb_path'] 
                log_group = split_data['log_group']
                chatx = split_data['chatx']
                # --- END OF MODIFICATION: استرجاع بيانات التقسيم والمدة ---

                # Use the same edit message ID for progress updates
                progress_msg_id = edit_id # Use the original edit_id

                # Check if the original video file still exists before proceeding
                if not os.path.exists(file_path):
                    await app.send_message(sender, "The video file was not found. It might have been deleted or an error occurred.")
                    # Clean up thumbnail if it exists
                    if original_thumb_path and os.path.exists(original_thumb_path):
                        try: os.remove(original_thumb_path)
                        except: pass
                    try: # Delete the user's reply and bot's prompt
                        await app.delete_messages(sender, [event.id, replied_message.id])
                    except Exception as del_err:
                         print(f"Error deleting messages after file not found: {del_err}")
                    return

                await app.edit_message_text(sender, progress_msg_id, f"Splitting video into {num_parts} parts...")
                
                temp_dir = tempfile.TemporaryDirectory()  # Create temporary directory for parts
                try:
                    await split_video_ffmpeg(file_path, num_parts, temp_dir.name)
                    await app.edit_message_text(sender, progress_msg_id, "Uploading video parts...")
                    # --- START OF MODIFICATION: تمرير original_thumb_path ---
                    await upload_video_parts(app, sender, progress_msg_id, temp_dir.name, msg, caption, width, height, duration, original_thumb_path, log_group)  # Pass original_thumb_path
                    # --- END OF MODIFICATION: تمرير original_thumb_path ---
                    
                    await app.edit_message_text(sender, progress_msg_id, "Video parts uploaded successfully!")
                    
                    # Clean up original messages (user reply and bot prompt)
                    await asyncio.sleep(5) # Wait a bit before deleting
                    try:
                       await app.delete_messages(sender, [event.id, replied_message.id, progress_msg_id]) 
                    except Exception as del_err:
                         print(f"Error deleting messages after successful split/upload: {del_err}")

                except Exception as split_err:
                    print(f"Error splitting or uploading video parts via reply: {split_err}") # Log the error
                    try:
                         # Update the progress message with the error
                         await app.edit_message_text(sender, progress_msg_id, f"Error splitting or uploading video parts: {split_err}")
                         # Optionally, delete original messages and leave the error message
                         await asyncio.sleep(5)
                         try:
                            await app.delete_messages(sender, [event.id, replied_message.id])
                         except Exception as del_err:
                             print(f"Error deleting messages after split error: {del_err}")

                    except Exception as edit_err:
                         print(f"Error editing message after split error: {edit_err}")
                         # If editing fails, maybe send a new error message
                         await app.send_message(sender, f"Error splitting or uploading video parts: {split_err}")
                         # Try to delete original messages
                         await asyncio.sleep(5)
                         try:
                            await app.delete_messages(sender, [event.id, replied_message.id])
                         except Exception as del_err:
                            print(f"Error deleting messages after edit error: {del_err}")

                finally:
                    # Ensure cleanup of original file, temporary directory, and thumbnail
                    if os.path.exists(file_path):
                         try: os.remove(file_path)
                         except: pass
                    if original_thumb_path and os.path.exists(original_thumb_path):
                         try: os.remove(original_thumb_path)
                         except: pass

                    try:
                        temp_dir.cleanup()  # Clean up the temporary directory
                    except Exception as tmp_cleanup_err:
                        print(f"Error cleaning up temp dir: {tmp_cleanup_err}")


            # If somehow pending_video_splits is empty but user is in pending_split_reply state
            else:
                await event.reply("No pending video task found for splitting. Please send the video link/file again.")
                pending_split_reply.pop(user_id, None) # Remove user from pending state
                # Also try to delete the user's reply and the bot's prompt
                try:
                    await app.delete_messages(sender, [event.id, replied_message.id])
                except Exception as del_err:
                     print(f"Error deleting messages after no pending task: {del_err}")

        except ValueError:
            # User replied with non-numeric text
            await event.reply("Invalid input. Please reply with a positive number for the parts.")
            # Keep user in pending state to reply again (do not pop from pending_split_reply)
            pass # Do nothing, wait for another reply

        except KeyError:
            # Should not happen if user_id is in pending_split_reply but not pending_video_splits
            # This case is handled above.
            pass # Ignore
        except Exception as general_error:
            print(f"An unexpected error occurred in handle_split_reply: {general_error}")
            await event.reply(f"An unexpected error occurred: {general_error}")
            pending_split_reply.pop(user_id, None) # Exit pending state on unexpected error


    else:
        # If the user is in pending_split_reply state but the reply was not to the correct message
        # Or if the user is in pending_split_reply state but sent a different message type
        if user_id in pending_split_reply:
            await event.reply("Please reply to the splitting prompt message with the desired number of parts (or send /cancel).")
            # Do NOT pop from pending_split_reply, keep waiting for a valid reply

@gf.on(events.NewMessage(func=lambda e: e.sender_id in pending_photos and e.reply_to_msg_id is None)) # Only process if expecting photo and not a reply
async def save_thumbnail(event):
    user_id = event.sender_id

    # Check if user is marked for sending a photo
    if user_id in pending_photos:
        if event.photo:
            await event.reply("Processing thumbnail...")
            temp_path = await event.download_media()
            thumb_file_path = f'./{user_id}.jpg'
            
            # Delete old thumbnail if it exists
            if os.path.exists(thumb_file_path):
                try:
                    os.remove(thumb_file_path)
                    print(f"Removed old thumbnail for user {user_id}")
                except Exception as rm_err:
                     print(f"Error removing old thumbnail for user {user_id}: {rm_err}")

            # Rename downloaded file to user's thumbnail path
            try:
                os.rename(temp_path, thumb_file_path)
                await event.respond('Thumbnail saved successfully!')
                print(f"New thumbnail saved for user {user_id} at {thumb_file_path}")
            except Exception as rename_err:
                 print(f"Error renaming downloaded file to thumbnail path for user {user_id}: {rename_err}")
                 await event.respond(f"Error saving thumbnail: {rename_err}")
                 # Clean up downloaded file if renaming failed
                 if os.path.exists(temp_path):
                      try: os.remove(temp_path)
                      except: pass

        else:
            # If the user sends something other than a photo while in pending_photos state
            await event.respond('Please send a photo file to set the thumbnail. Use /settings and then "Set Thumbnail" again if needed.')

        # Remove user from pending photos dictionary regardless of success/failure in this handler run
        pending_photos.pop(user_id, None)


# Handler for general user input (not a reply to split or setting a thumbnail)
@gf.on(events.NewMessage)
async def handle_user_input(event):
    user_id = event.sender_id

    # Check if user is currently in a setting input session
    if user_id in sessions:
        session_type = sessions[user_id]
        user_input = event.text # Get the user's message text

        if session_type == 'setchat':
            try:
                chat_id = int(user_input)
                # Store the chat ID. For persistence, this should ideally be saved to DB.
                user_chat_ids[user_id] = chat_id 
                await event.respond(f"Forward chat ID set successfully to `{chat_id}`! Note: This setting is currently in-memory and resets on bot restart.")
            except ValueError:
                await event.respond("Invalid chat ID! Please send a valid integer chat ID.")
                # Keep user in session to try again
                return # Do not pop from sessions
            finally:
                # Only pop from sessions on successful input
                del sessions[user_id]

        elif session_type == 'setrename':
            custom_rename_tag = user_input.strip()
            # Set rename tag. For persistence, save to DB.
            user_rename_preferences[str(user_id)] = custom_rename_tag
            await event.respond(f"Custom rename tag set to: `{custom_rename_tag}`. Note: This setting is currently in-memory and resets on bot restart.")
            del sessions[user_id]


        elif session_type == 'setcaption':
            custom_caption = user_input.strip()
            # Set caption. For persistence, save to DB.
            if custom_caption.lower() == 'clear':
                 user_caption_preferences.pop(str(user_id), None) # Remove from memory
                 # To make persistent, also remove from DB
                 await event.respond("Custom caption cleared.")
            else:
                user_caption_preferences[str(user_id)] = custom_caption
                await event.respond(f"Custom caption set to: `{custom_caption}`. Note: This setting is currently in-memory and resets on bot restart.")
                # To make persistent, save to DB
            del sessions[user_id]


        elif session_type == 'setreplacement':
            lines = user_input.strip().split('\n')
            success_count = 0
            failed_count = 0
            # Load existing replacements (if not already loaded)
            replacements = load_replacement_words(user_id)
            delete_words = load_delete_words(user_id) # Get delete words to prevent replacement conflicts

            for line in lines:
                match = re.match(r"'(.+)' '(.+)'", line)
                if match:
                    word_to_replace, replace_with = match.groups()
                    # Prevent replacing words that are in the delete list
                    if word_to_replace in delete_words:
                         await event.respond(f"Warning: The word '{word_to_replace}' is in your delete list and cannot be replaced. Please remove it from the delete list first.")
                         failed_count += 1
                    else:
                        replacements[word_to_replace] = replace_with
                        success_count += 1
                else:
                    failed_count += 1
                    await event.respond(f"Warning: Invalid format for line: `{line}`. Use 'WORD(s)' 'REPLACEWORD'")
                    # Keep user in session to send valid format? Or handle all and exit?
                    # For now, handle all lines and exit the session.

            if success_count > 0:
                save_replacement_words(user_id, replacements) # Save updated replacements to DB
                await event.respond(f"Successfully added/updated {success_count} replacement pair(s).")
            if failed_count > 0:
                 await event.respond(f"Failed to add/update {failed_count} replacement pair(s) due to incorrect format or conflict with delete list.")

            del sessions[user_id] # Exit replacement setting session


        elif session_type == 'addsession_string': # State for receiving session string
            session_string = user_input.strip()
            if not session_string:
                 await event.respond("Session string cannot be empty.")
                 # Keep user in session to try again?
                 return

            # Store session string in MongoDB ('logins' collection)
            session_data = {
                "user_id": user_id,
                "session_string": session_string
            }
            try:
                mcollection.update_one(
                    {"user_id": user_id},
                    {"$set": session_data},
                    upsert=True
                )
                await event.respond("Pyrogram session string saved successfully.")
                # Optional: Log the session addition to an admin group/channel
                # if LOG_GROUP:
                #    await app.send_message(LOG_GROUP, f"New session added for User ID: {user_id}\nSession String (use with caution):\n\n`{session_string}`")
                
                # Need to implement logic to START the userbot client using this session
                await event.respond("Session saved. Please wait for the userbot to connect (may require bot restart).")

            except Exception as e:
                 await event.respond(f"Error saving session string: {e}")
                 print(f"Error saving session string for user {user_id}: {e}") # Log error


            del sessions[user_id] # Exit session string input state


        elif session_type == 'deleteword':
            words_to_delete = user_input.split() # Split by space and newline by default behavior of .split()
            words_to_delete = [w.strip() for w in words_to_delete if w.strip()] # Clean and remove empty strings

            if not words_to_delete:
                await event.respond("No valid words provided to delete.")
                # Keep user in session?
                return

            delete_words = load_delete_words(user_id)
            # Check for conflicts with replacement words before adding to delete list
            replacements = load_replacement_words(user_id)
            conflict_words = [word for word in words_to_delete if word in replacements]

            if conflict_words:
                await event.respond(f"Cannot add words to delete list that are also in replacement words. Please remove them from replacements first: {', '.join(conflict_words)}")
                # Keep user in session?
                return

            delete_words.update(words_to_delete)
            save_delete_words(user_id, delete_words) # Save updated delete words to DB
            await event.respond(f"Words added to delete list: {', '.join(words_to_delete)}")

            del sessions[user_id] # Exit delete word session


        # Add other setting session handlers here...

    # If the message is not part of a pending setting session,
    # and not a reply for splitting,
    # and not a photo for thumbnail (handled by specific handler),
    # it must be a command or link/file to process.
    # The rest of your code (like /clone, processing links/files) should come AFTER this session check.
    # The current structure places the main message processing *outside* this function.
    # Ensure the handlers are ordered correctly.

# Ensure other handlers like the one processing links and files run *after*
# the session handler if the user is not in a session state.
# Telethon event handlers are processed in the order they are defined unless restricted.
