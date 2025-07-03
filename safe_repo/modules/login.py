from pyrogram import filters, Client
from safe_repo import app
from pyromod import listen
import os
from safe_repo.core.mongo import db
from safe_repo.core.func import subscribe
from config import API_ID as api_id, API_HASH as api_hash
from pyrogram.errors import (
    ApiIdInvalid,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    FloodWait
)


async def delete_session_files(user_id):
    session_file = f"session_{user_id}.session"
    memory_file = f"session_{user_id}.session-journal"

    if os.path.exists(session_file):
        os.remove(session_file)
    
    if os.path.exists(memory_file):
        os.remove(memory_file)

    # حذف الجلسة من قاعدة البيانات
    await db.delete_session(user_id)


@app.on_message(filters.command("logout"))
async def clear_db(client, message):
    user_id = message.chat.id
    await delete_session_files(user_id)
    await message.reply("✅ تم حذف بيانات الجلسة الخاصة بك من الذاكرة والقرص.")


@app.on_message(filters.command("login"))
async def generate_session(_, message):
    user_id = message.chat.id
    
    # التحقق من الجلسة المحفوظة في قاعدة البيانات
    session_string = await db.get_session(user_id)
    if session_string:
        try:
            client = Client(session_string=session_string)
            await client.start()
            me = await client.get_me()
            await client.stop()
            await message.reply(f"✅ لديك جلسة صالحة بالفعل.\nالمستخدم: {me.first_name} (ID: {me.id})")
            return
        except Exception as e:
            await message.reply(f"⚠️ الجلسة المحفوظة غير صالحة أو انتهت صلاحيتها:\n{e}")
            # إذا كانت الجلسة غير صالحة، احذفها من قاعدة البيانات
            await db.delete_session(user_id)

    # إذا لم تكن هناك جلسة محفوظة أو كانت غير صالحة، متابعة عملية تسجيل الدخول
    joined = await subscribe(_, message)
    if joined == 1:
        return

    number = await _.ask(user_id, '📱 أدخل رقم هاتفك مع رمز الدولة:\nمثال: +19876543210', filters=filters.text)   
    phone_number = number.text
    try:
        await message.reply("📲 جارٍ إرسال رمز التحقق (OTP)...")
        client = Client(f"session_{user_id}", api_id, api_hash)
        await client.connect()
    except Exception as e:
        await message.reply(f"❌ فشل إرسال رمز التحقق: {e}. حاول مرة أخرى لاحقًا.")
        return

    try:
        code = await client.send_code(phone_number)
    except ApiIdInvalid:
        await message.reply('❌ API ID أو API HASH غير صحيحين. أعد المحاولة.')
        return
    except PhoneNumberInvalid:
        await message.reply('❌ رقم الهاتف غير صحيح. أعد المحاولة.')
        return

    try:
        otp_code = await _.ask(user_id, "📥 أدخل رمز OTP الذي وصلك (بدون مسافات):", filters=filters.text, timeout=600)
    except TimeoutError:
        await message.reply('⏰ انتهت المهلة. أعد المحاولة.')
        return

    phone_code = otp_code.text.replace(" ", "")
    try:
        await client.sign_in(phone_number, code.phone_code_hash, phone_code)
    except PhoneCodeInvalid:
        await message.reply('❌ رمز OTP غير صحيح. أعد المحاولة.')
        return
    except PhoneCodeExpired:
        await message.reply('❌ رمز OTP منتهي. أعد المحاولة.')
        return
    except SessionPasswordNeeded:
        try:
            two_step_msg = await _.ask(user_id, '🔒 حسابك يحتوي على تحقق بخطوتين. أدخل كلمة المرور:', filters=filters.text, timeout=300)
            password = two_step_msg.text
            await client.check_password(password=password)
        except PasswordHashInvalid:
            await message.reply('❌ كلمة المرور غير صحيحة. أعد المحاولة.')
            return

    string_session = await client.export_session_string()
    await db.set_session(user_id, string_session)
    await client.disconnect()
    await otp_code.reply("✅ تسجيل الدخول ناجح!")

from pyrogram import filters, Client
from safe_repo import app
from safe_repo.core.mongo import db
from pyrogram import Client

@app.on_message(filters.command("add_session"))
async def add_session(_, message):
    user_id = message.chat.id

    await message.reply("📩 أرسل لي Session String الخاص بك لاستخدامه في تسجيل الدخول:")

    try:
        # استقبال النص من المستخدم
        session_msg = await _.listen(user_id, timeout=600)
        session_string = session_msg.text.strip()

        # استخدام اسم قصير للملف
        client = Client("short_session", session_string=session_string)
        await client.start()

        me = await client.get_me()
        await message.reply(f"✅ تم تسجيل الدخول بنجاح!\nالمستخدم: {me.first_name} (ID: {me.id})")

        # حفظ الجلسة في قاعدة البيانات
        await db.set_session(user_id, session_string)
        await client.stop()

    except Exception as e:
        await message.reply(f"❌ حدث خطأ أثناء تسجيل الدخول باستخدام Session String:\n{e}")


@app.on_message(filters.command("check_session"))
async def check_session(_, message):
    user_id = message.chat.id
    
    # استرداد نص الجلسة من قاعدة البيانات
    session_string = await db.get_session(user_id)
    if not session_string:
        await message.reply("⚠️ لا توجد جلسة محفوظة.")
        return

    try:
        # التحقق من صحة الجلسة
        client = Client(session_string)
        await client.start()
        me = await client.get_me()
        await message.reply(f"✅ الجلسة صالحة.\nالمستخدم: {me.first_name} (ID: {me.id})")
        await client.stop()
    except Exception as e:
        await message.reply(f"❌ الجلسة غير صالحة أو انتهت صلاحيتها:\n{e}")
