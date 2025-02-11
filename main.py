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
import requests
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
PAYMENT_URL = os.getenv('PAYMENT_URL')
ADMIN_URL = os.getenv('ADMIN_URL')
CODES_URL = os.getenv('CODES_URL')
DELETED_CODES_URL = os.getenv('DELETED_CODES_URL')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID'))

subscription_data = {}
user_data = {}
SUBSCRIPTION_FILE = "subscription_data.json"
CODE_FILE = "codes.json"

# Global variable to store the subscription code fetched from the API.
subscription_code = None

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

# ------------------ User Command: Request Link ------------------ #
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter the 8-digit subscription code provided by the admin:")
    context.user_data["awaiting_code"] = True

# ------------------ Handler: Process Code Input ------------------ #
async def handle_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global subscription_code  # Access the global variable
    user_id = update.message.from_user.id

    # Process subscription code input for link generation
    if context.user_data.get("awaiting_code"):
        if not subscription_code:
            response = requests.get(url=CODES_URL)
            try:
                response.raise_for_status()
                data = response.json()
                subscription_code = data['sheet1'][0]['codes']
            except requests.exceptions.HTTPError as err:
                print("HTTP Error:", err)
        try:
            # Convert the input code to integer if needed.
            code_input = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("The code must be numeric. Please try again.")
            return

        if subscription_code == code_input:
            expiry_dt = datetime.now() + timedelta(days=30)   # Create an expiry date 30 days from now
            requests.delete(url=DELETED_CODES_URL)            # delete used code from Google sheet
            subscription_code = None
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

    # Process the user's full name after code verification
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
            # Notify admin that this user has successfully generated the channel invite link.
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"<b>🔰SUBSCRIPTION PURCHASED🔰</b>\n\n"
                     f"Name: <a href='tg://user?id={user_id}'>{subscription_data[user_id]['name']}</a>\n"
                     f"User ID: {user_id}\n"
                     f"Expiry: {day} at {time_str}",
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
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"<b>🔰SUBSCRIPTION EXPIRED🔰</b>\n\n"
                         f"📌 <a href='tg://user?id={chat_id}'>{subscription_data[chat_id]['name']}</a> "
                         f"removed from Shared Instructor Bot channel.",
                    parse_mode="HTML"
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="<b>🔰SUBSCRIPTION EXPIRED🔰</b>\n\nPlz, type /start command to make the payment",
                    parse_mode="HTML"
                )
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

# ------------------ Total Codes Command ------------------ #
async def total_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    sent_message = await update.message.reply_text("Counting...")
    try:
        response = requests.get(url=CODES_URL)
        context.job_queue.run_once(delete_message, 0, data=(sent_message.chat.id, sent_message.message_id))
        try:
            response.raise_for_status()
            data = response.json()
            # Count the total number of subscription codes remaining.
            count = len(data['sheet1'])
            await update.message.reply_text(f"Total number of remaining subscription codes: {count}")
            print(f"Total number of remaining subscription codes: {count}")
        except requests.exceptions.HTTPError as err:
            print("HTTP Error:", err)
    except BadRequest as e:
        logger.error(f"BadRequest Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__} - {e}")

# ------------------ Start Command ------------------ #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global subscription_code  # Declare as global so we can assign to it
    try:
        response = requests.get(url=CODES_URL)
        try:
            response.raise_for_status()
            data = response.json()
            # Assign the fetched code to our global variable
            subscription_code = data['sheet1'][0]['codes']
            # print("Subscription code from API:", subscription_code)
        except requests.exceptions.HTTPError as err:
            print("HTTP Error:", err)
        user_id = update.message.from_user.id
        chat_member = await context.bot.get_chat_member(PRIVATE_CHANNEL_ID, user_id)
        is_premium = chat_member.status in ["member", "administrator", "creator"]
        button_text = "Access Turnitin Account" if is_premium else "🚀Make Payment🚀"
        button = InlineKeyboardButton(
            button_text,
            web_app=WebAppInfo(url=ACCOUNT_URL) if is_premium else None,
            url=PAYMENT_URL if not is_premium else None,
        )
        keyboard = [[button]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        sent_message = await update.message.reply_text(
            f"*🔰You are already a premium member!🔰*" if is_premium else
            f"*🔰You are not a premium member!🔰*"
            f"\n\nTo use this bot, you must first purchase a subscription. Please click on the button below to make the payment."
            f"\n\n Your User ID: `{user_id}`\n"
            f"(Use this User ID on Razorpay Payment Gateway)",
            reply_markup=reply_markup,
            parse_mode="Markdown"
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
/show_users - Show list of all premium users
/total_codes - Count total number of remaining subscription codes
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
    application.add_handler(CommandHandler("show_users", show_users))
    application.add_handler(CommandHandler("total_codes", total_codes))
    application.add_handler(CommandHandler("admin_commands", admin_commands))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code_input))
    scheduler = BackgroundScheduler(timezone="UTC")
    # scheduler.add_job(lambda: asyncio.run(check_expired_subscriptions(application)), "interval", hours=1)
    scheduler.add_job(lambda: asyncio.run(check_expired_subscriptions(application)), "interval", minutes=1)
    scheduler.start()
    application.run_polling()

if __name__ == "__main__":
    main()
