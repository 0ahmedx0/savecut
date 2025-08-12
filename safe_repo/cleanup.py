import os
import asyncio
from pyrogram import filters
from pyrogram.types import Message

from safe_repo import app
from config import OWNER_ID

# استيراد دوال قاعدة البيانات اللازمة
from safe_repo.core.mongo import db

# استيراد المتغيرات الموجودة في الذاكرة لتنظيفها
# قد تحتاج إلى تعديل مسارات الاستيراد إذا كانت الملفات في مكان آخر
try:
    from safe_repo.main import users_loop
except ImportError:
    users_loop = {} # إذا لم يتم العثور عليه، افترض أنه قاموس فارغ

try:
    from safe_repo.core.get_func import (
        pending_video_splits,
        user_chat_ids,
        sessions as telethon_sessions, # إعادة تسمية لتجنب التعارض
        pending_photos,
        user_rename_preferences,
        user_caption_preferences
    )
except ImportError:
    # قيم افتراضية إذا فشل الاستيراد
    pending_video_splits = {}
    user_chat_ids = {}
    telethon_sessions = {}
    pending_photos = {}
    user_rename_preferences = {}
    user_caption_preferences = {}


@app.on_message(filters.command("cleanup") & filters.user(OWNER_ID))
async def cleanup_user_data(_, message: Message):
    """
    يقوم هذا الأمر بتنظيف بيانات المستخدم المؤقتة والملفات والإعدادات
    دون حذف جلسة تسجيل الدخول الخاصة بهم.
    الاستخدام:
    /cleanup - لتنظيف بياناتك الخاصة.
    /cleanup <user_id> - لتنظيف بيانات مستخدم معين.
    """
    user_to_clean_id = 0
    
    try:
        if len(message.command) > 1:
            user_to_clean_id = int(message.command[1])
        else:
            user_to_clean_id = message.from_user.id
    except (ValueError, IndexError):
        return await message.reply_text("❌ **خطأ:** يرجى تقديم ID مستخدم صحيح أو استخدام `/cleanup` لتنظيف بياناتك.")

    msg = await message.reply_text(f"🧹 **جارٍ تنظيف البيانات للمستخدم:** `{user_to_clean_id}`...")

    cleanup_log = []

    # 1. تنظيف الملفات من على الخادم
    try:
        thumb_path = f"{user_to_clean_id}.jpg"
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
            cleanup_log.append("🗑️ تم حذف الصورة المصغرة.")
    except Exception as e:
        cleanup_log.append(f"⚠️ خطأ في حذف الصورة المصغرة: {e}")

    # 2. تنظيف البيانات من قاعدة البيانات (باستثناء الجلسة)
    try:
        await db.remove_thumbnail(user_to_clean_id)
        cleanup_log.append("✔️ تمت إعادة تعيين الصورة المصغرة من قاعدة البيانات.")
        
        await db.remove_caption(user_to_clean_id)
        cleanup_log.append("✔️ تمت إعادة تعيين الكابشن المخصص من قاعدة البيانات.")

        await db.remove_replace(user_to_clean_id)
        cleanup_log.append("✔️ تم حذف كلمات الاستبدال من قاعدة البيانات.")

        await db.all_words_remove(user_to_clean_id)
        cleanup_log.append("✔️ تم حذف الكلمات المراد إزالتها من قاعدة البيانات.")
        
        await db.remove_channel(user_to_clean_id)
        cleanup_log.append("✔️ تم حذف القناة المستهدفة من قاعدة البيانات.")

    except Exception as e:
        cleanup_log.append(f"⚠️ خطأ أثناء تنظيف قاعدة البيانات: {e}")

    # 3. تنظيف البيانات من ذاكرة البوت (In-Memory)
    try:
        users_loop.pop(user_to_clean_id, None)
        pending_video_splits.pop(user_to_clean_id, None)
        user_chat_ids.pop(user_to_clean_id, None)
        telethon_sessions.pop(user_to_clean_id, None)
        pending_photos.pop(user_to_clean_id, None)
        user_rename_preferences.pop(str(user_to_clean_id), None)
        user_caption_preferences.pop(str(user_to_clean_id), None)
        cleanup_log.append("🧠 تم تنظيف البيانات المؤقتة من ذاكرة البوت.")
    except Exception as e:
        cleanup_log.append(f"⚠️ خطأ أثناء تنظيف الذاكرة: {e}")
    
    # رسالة التأكيد النهائية
    final_report = f"✅ **اكتمل التنظيف للمستخدم:** `{user_to_clean_id}`\n\n"
    final_report += "\n".join(cleanup_log)
    
    await msg.edit_text(final_report)
