from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackContext,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from telegram.error import BadRequest
import os
from datetime import datetime, timedelta
import json
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import logging
import random
from keep_alive import keep_alive
keep_alive()

# from dotenv import load_dotenv
# load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot settings from environment variables
TOKEN = os.getenv('TOKEN')
PRIVATE_CHANNEL_ID = int(os.getenv('PRIVATE_CHANNEL_ID'))
ACCOUNT_URL = os.getenv('ACCOUNT_URL')
MSG_DELETE_TIME = int(os.getenv('MSG_DELETE_TIME'))
ADMIN_URL = os.getenv('ADMIN_URL')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID'))

subscription_data = {}
user_data = {}
SUBSCRIPTION_FILE = "subscription_data.json"
CODE_FILE = "codes.json"

# ------------------ Subscription Data Helpers ------------------ #
def save_subscription_data():
    serializable_data = {
        chat_id: {
            "name": details.get("name", "Unknown"),
            "expiry": details["expiry"].strftime("%Y-%m-%d %H:%M"),
        }
        for chat_id, details in subscription_data.items()
    }
    with open(SUBSCRIPTION_FILE, "w") as file:
        json.dump(serializable_data, file, indent=4)

def load_subscription_data():
    if os.path.exists(SUBSCRIPTION_FILE):
        try:
            with open(SUBSCRIPTION_FILE, "r") as file:
                data = json.load(file)
                return {
                    chat_id: {
                        "name": details.get("name", "Unknown"),
                        "expiry": datetime.strptime(details["expiry"], "%Y-%m-%d %H:%M"),
                    }
                    for chat_id, details in data.items()
                }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Error loading subscription data: {e}. Resetting to an empty dictionary.")
    return {}

# ------------------ Code Helpers ------------------ #
def load_codes():
    """
    Load subscription codes from the JSON file.
    Each code should be stored as a dictionary:
       {"code": "123456", "expiry": "YYYY-MM-DD HH:MM:SS"}
    """
    if os.path.exists(CODE_FILE):
        try:
            with open(CODE_FILE, "r") as file:
                codes = json.load(file)
                return codes if isinstance(codes, list) else []
        except Exception as e:
            logger.error(f"Error loading codes: {e}")
    return []

def save_codes(codes):
    with open(CODE_FILE, "w") as file:
        json.dump(codes, file, indent=4)

# ------------------ Admin Command: Generate Code ------------------ #
async def generate_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    # Build an inline keyboard with duration options
    keyboard = [
        [InlineKeyboardButton("1 Day", callback_data="gen_code_1_day")],
        [InlineKeyboardButton("1 Week", callback_data="gen_code_1_week")],
        [InlineKeyboardButton("1 Month", callback_data="gen_code_1_month")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "For which duration do you want to generate the code?", reply_markup=reply_markup
    )

# ------------------ Callback Query Handler for Code Generation ------------------ #
async def code_duration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # e.g., "gen_code_1_day", "gen_code_1_week", or "gen_code_1_month"
    if data == "gen_code_1_day":
        duration = timedelta(days=1)
    elif data == "gen_code_1_week":
        duration = timedelta(weeks=1)
    elif data == "gen_code_1_month":
        duration = timedelta(days=30)
    else:
        duration = timedelta(days=1)
    # Generate a random 8-digit code
    code = str(random.randint(10000000, 99999999))
    expiry = datetime.now() + duration
    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S")
    codes = load_codes()
    codes.append({"code": code, "expiry": expiry_str})
    save_codes(codes)
    await query.edit_message_text(
        f"Generated Subscription Code: `{code}`",
        # f"\nValid until: {expiry_str}",
        parse_mode="Markdown"
    )

# ------------------ New Command: Show Active Codes ------------------ #
async def show_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    codes = load_codes()
    now = datetime.now()
    active_codes = []
    for c in codes:
        if isinstance(c, dict):
            expiry_dt = datetime.strptime(c["expiry"], "%Y-%m-%d %H:%M:%S")
            if expiry_dt >= now:
                active_codes.append(c)
    if active_codes:
        message = "Active Subscription Codes:\n"
        for code_entry in active_codes:
            message += f"`{code_entry['code']}` - Expires: {code_entry['expiry']}\n"
        await update.message.reply_text(message, parse_mode="Markdown")
    else:
        await update.message.reply_text("No active subscription codes found.")

# ------------------ User Command: Request Link ------------------ #
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter the 8-digit subscription code provided by the admin:")
    context.user_data["awaiting_code"] = True

# ------------------ Handler: Process Code Input ------------------ #
async def handle_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if context.user_data.get("awaiting_code"):
        code_input = update.message.text.strip()
        codes = load_codes()
        matched_code = None
        expiry_dt = None
        for c in codes:
            # Ensure that c is a dictionary before accessing its keys
            if isinstance(c, dict) and c.get("code") == code_input:
                expiry_str = c.get("expiry")
                expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                if expiry_dt >= datetime.now():
                    matched_code = c
                    break
        if matched_code:
            codes.remove(matched_code)
            save_codes(codes)
            # Store the verified code's expiry so we can use it for the invite link
            context.user_data["verified"] = True
            context.user_data["code_expiry"] = expiry_dt
            context.user_data["awaiting_code"] = False
            await update.message.reply_text(
                "<b>🔰SUBSCRIPTION CODE VERIFIED!🔰</b>\n\nPlease enter your full name:",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("Invalid or expired code. Please try again.")
        return

    if context.user_data.get("verified"):
        user_name = update.message.text.strip()
        expiry_dt = context.user_data.get("code_expiry", datetime.now() + timedelta(days=30))
        subscription_data[user_id] = {"name": user_name, "expiry": expiry_dt}
        save_subscription_data()
        logger.info(f"User {user_id} ({user_name}) plan expires on {expiry_dt}.")
        day = expiry_dt.strftime("%Y-%m-%d")
        time_str = expiry_dt.strftime("%H:%M")
        try:
            # Create a chat invite link with the expiry date from the code (as a Unix timestamp)
            invite_link = await context.bot.create_chat_invite_link(
                PRIVATE_CHANNEL_ID,
                member_limit=1,
                expire_date=int(expiry_dt.timestamp())
            )
            await update.message.reply_text(
                f"🚀 Here is your premium member invite link:\n\n{invite_link.invite_link}\n"
                f"<b>(Valid for one-time use)</b>\n\n"
                f"✅ After joining this channel, type /start to access the instructor account.\n\n"
                f"<b>🌐 Your plan will expire on {day} at {time_str}.</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            await update.message.reply_text("Error generating invite link. Please try again later.")
            logger.error(f"Error creating invite link: {e}")
        context.user_data.pop("verified", None)
        context.user_data.pop("code_expiry", None)

# ------------------ Periodic Task: Check Expired Subscriptions ------------------ #
async def check_expired_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    expired_users = []
    for chat_id, details in list(subscription_data.items()):
        expiry_value = details["expiry"]
        if isinstance(expiry_value, str):
            expiry_date = datetime.strptime(expiry_value, "%Y-%m-%d %H:%M:%S")
        else:
            expiry_date = expiry_value
        if expiry_date < now:
            try:
                await context.bot.ban_chat_member(PRIVATE_CHANNEL_ID, chat_id, until_date=now)
                await context.bot.unban_chat_member(PRIVATE_CHANNEL_ID, chat_id)
                logger.info(f"Removed expired user {chat_id} from the private channel.")
            except Exception as e:
                logger.error(f"Failed to remove/unban user {chat_id}: {e}")
            expired_users.append(chat_id)
    for chat_id in expired_users:
        del subscription_data[chat_id]
    save_subscription_data()

# ------------------ Admin Command: Show Users ------------------ #
async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    if not subscription_data:
        await update.message.reply_text("No active users found.")
        return
    user_list = "\n".join([
        f"👤 <a href='tg://user?id={chat_id}'>{details['name']}</a> (Expiry: {details['expiry'].strftime('%Y-%m-%d %H:%M')})"
        for chat_id, details in subscription_data.items()
    ])
    await update.message.reply_text(
        f"📜 <b>Active Users:</b>\n\n{user_list}",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

# ------------------ New Admin Command: Delete Code ------------------ #
async def delete_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /delete_code<space><code>")
        return

    code_to_delete = args[0].strip()
    codes = load_codes()
    found = False
    for c in codes:
        if isinstance(c, dict) and c.get("code") == code_to_delete:
            codes.remove(c)
            found = True
            break
    if found:
        save_codes(codes)
        await update.message.reply_text(f"Code `{code_to_delete}` deleted successfully.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Code `{code_to_delete}` not found.", parse_mode="Markdown")

# ------------------ User Command: Request Link ------------------ #
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter the 8-digit subscription code provided by the admin:")
    context.user_data["awaiting_code"] = True

# ------------------ Start Command ------------------ #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        chat_member = await context.bot.get_chat_member(PRIVATE_CHANNEL_ID, user_id)
        is_premium = chat_member.status in ["member", "administrator", "creator"]
        button_text = "Access Turnitin Account" if is_premium else "🚀Contact Admin🚀"
        button = InlineKeyboardButton(
            button_text,
            web_app=WebAppInfo(url=ACCOUNT_URL) if is_premium else None,
            url=ADMIN_URL if not is_premium else None,
        )
        keyboard = [[button]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        sent_message = await update.message.reply_text(
            "<b>🔰You are already a premium member!🔰</b>" if is_premium else
            "<b>🔰You are not a premium member!🔰</b>\n\nTo use this bot, you must first purchase a subscription. Please click on the button below to contact Admin and make the payment.",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        context.job_queue.run_once(delete_message, MSG_DELETE_TIME,
                                   data=(sent_message.chat.id, sent_message.message_id))
    except BadRequest as e:
        logger.error(f"BadRequest Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__} - {e}")

# ------------------ Delete Message Function ------------------ #
async def delete_message(context: CallbackContext):
    chat_id, message_id = context.job.data
    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)

# ------------------ New Admin Command: Admin Commands ------------------ #
async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    await update.message.reply_text(
        """
Commands available:
/generate_code - Generate a 8-digit code with a duration option
/show_users - Show list of all premium users
/show_codes - Show all active subscription codes
/delete_code - Delete a specific subscription code. 
Usage: /delete_code<space><code>
"""
    )


# ------------------ Help Command ------------------ #
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
Commands available:
/start - Start the bot and check your membership
/generate_link - Generate your invite link using a 8-digit code
/admin_commands - Show all commands that only admin can use
/help - Show this help message
"""
    )

# ------------------ On Shutdown ------------------ #
async def on_shutdown(application):
    save_subscription_data()
    logger.info("Subscription data saved on shutdown.")

# ------------------ Main Function ------------------ #
def main():
    global subscription_data
    subscription_data = load_subscription_data()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("generate_link", link_command))
    application.add_handler(CommandHandler("generate_code", generate_code))
    application.add_handler(CommandHandler("show_users", show_users))
    application.add_handler(CommandHandler("show_codes", show_codes))
    application.add_handler(CommandHandler("delete_code", delete_code))
    application.add_handler(CommandHandler("admin_commands", admin_commands))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code_input))
    # Register the callback query handler for code generation options
    application.add_handler(CallbackQueryHandler(code_duration_handler, pattern="^gen_code_"))
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(lambda: asyncio.run(check_expired_subscriptions(application)), "interval", hours=1)
    scheduler.start()
    application.run_polling()

if __name__ == "__main__":
    main()
