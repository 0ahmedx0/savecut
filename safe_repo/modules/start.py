from pyrogram import filters
from safe_repo import app
from safe_repo.core import script
from safe_repo.core.func import subscribe
from config import OWNER_ID
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# ------------------- Start-Buttons ------------------- #

buttons = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("Join Channel", url="https://t.me/safe_repo")],
        [InlineKeyboardButton("Buy Premium", url="https://t.me/safe_repo_bot")]
    ]
)

@app.on_message(filters.command("start"))
async def start(_, message):
    # التحقق مما إذا كانت الرسالة من مستخدم (محادثة خاصة) أم من قناة
    if message.from_user:
        user_mention = message.from_user.mention
        join = await subscribe(_, message)
        if join == 1:
            return
    else:
        # في حال كانت الرسالة من قناة (لا يوجد from_user)
        user_mention = message.chat.title

    await message.reply_text(
        text=script.START_TXT.format(user_mention),
        reply_markup=buttons
    )

