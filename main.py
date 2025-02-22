from firebase_db import save_subscription, load_subscriptions, remove_expired_subscriptions
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackContext,
    CallbackQueryHandler,
    filters, ConversationHandler, MessageHandler,
)
from telegram.error import BadRequest
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import logging
import requests
import json
import random
import string
import time
import os

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
PAYMENT_CAPTURED_DETAILS_URL= os.getenv("PAYMENT_CAPTURED_DETAILS_URL")
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID'))
# PRICE = int(os.getenv("PRICE"))

subscription_data = {}
user_data = {}
codes_data = {}
CODES_FILE = "codes.json"
price = 1500

# Global variable to store the subscription code fetched from the API.
subscription_code = None
# Define conversation states
ENTER_CODE = 1
WAITING_FOR_CODE, WAITING_FOR_PAYMENT, WAITING_FOR_USER = range(3)

def load_codes():
    try:
        with open(CODES_FILE, "r") as f:
            codes_data = json.load(f)
            if not isinstance(codes_data, dict):  # Ensure it is a dictionary
                codes_data = {}
            return codes_data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}  # Return an empty dictionary if file not found or corrupted


def save_codes():
    with open(CODES_FILE, "w") as f:
        json.dump(codes_data, f, indent=4)


def generate_code(validity_days=1):
    codes_data = load_codes()  # Load existing codes from file
    # Generate a random alphanumeric code
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    # Set expiry date
    # expiry_dt = datetime.now() + timedelta(days=validity_days)
    expiry_dt = datetime.now() + timedelta(days=validity_days)
    # Store the code with its expiry date
    codes_data[code] = expiry_dt.strftime("%Y-%m-%d %H:%M")
    # Save the updated codes back to the file
    with open(CODES_FILE, "w") as f:
        json.dump(codes_data, f, indent=4)
    return code


def remove_expired_codes():
    codes_data = load_codes()
    now = datetime.datetime.now()

    updated_codes = {
        code: expiry for code, expiry in codes_data.items()
        if datetime.datetime.strptime(expiry, "%Y-%m-%d %H:%M") > now
    }

    if len(updated_codes) != len(codes_data):
        save_codes()

# ------------------ fetching Payment details made by user------------------ #
def fetch_payment_details(chat_id,payment_amount):
    response = requests.get(url=PAYMENT_CAPTURED_DETAILS_URL)
    try:
        response.raise_for_status()
        data = response.json()
        for entry in data:
            if entry['user_Id'] == chat_id:
                if entry['amount'] == str(payment_amount):
                    return entry
        # print("No payment details found! ")
    except requests.exceptions.HTTPError as err:
        print("HTTP Error:", err)

async def generate_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    keyboard = [
        [InlineKeyboardButton("1 Day", callback_data="generate_1")],
        [InlineKeyboardButton("1 Week", callback_data="generate_7")],
        [InlineKeyboardButton("1 Month", callback_data="generate_30")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Please choose a period for this subscription code:", reply_markup=reply_markup)

async def redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Start the upload process """
    # Explicitly reset the conversation state
    context.user_data.clear()
    await update.message.reply_text("Please enter you subscription code:")
    return WAITING_FOR_CODE

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    """ Receive payment amount and prompt for user ID """
    code = update.message.text
    global codes_data
    codes_data = load_codes()
    # print(codes_data)
    # print(f'code:{code}, data type: {type(code)}')
    # print(code in codes_data)
    if code in codes_data:
        print("Success")
        # Default email & mobile as "Unknown"
        user_name = update.message.from_user.full_name
        email = "Unknown"  # Placeholder if not provided
        mobile = "Unknown"  # Placeholder if not provided

        expiry_dt = datetime.strptime(codes_data[code], "%Y-%m-%d %H:%M")
        # Ensure expiry_dt is in the future
        expiry_timestamp = int(expiry_dt.timestamp())
        if expiry_timestamp <= int(time.time()):
            logger.error("Generated expiry date is invalid (in the past).")
            await update.message.reply_text("Error: The generated expiry date is invalid.")
            return
        day = expiry_dt.strftime("%Y-%m-%d")
        time_str = expiry_dt.strftime("%H:%M")
        # subscription_data[user_id] = {
        #     "name": update.message.from_user.full_name,
        #     "expiry": expiry_dt
        # }
        # save_subscription_data()
        save_subscription(user_id, user_name, expiry_dt, email, mobile)
        # Refresh subscriptions from Firestore
        global subscription_data
        subscription_data = load_subscriptions()  # Refresh from Firebase

        del codes_data[code]
        logger.error(f"Code {code} removed from codes_data.")
        save_codes()
        try:
            # Create a chat invite link with the expiry date from the code (as a Unix timestamp)
            invite_link = await context.bot.create_chat_invite_link(
                PRIVATE_CHANNEL_ID,
                member_limit=1,
                expire_date=expiry_timestamp
            )
        except Exception as e:
            await update.message.reply_text("Error generating invite link. Please try again later.")
            logger.error(f"Error creating invite link: {e}")

        await update.message.reply_text(
            f"<b>🔰CODE REDEEM SUCCESSFULLY🔰</b>\n\n"
            f"🚀 Here is your premium member invite link:\n{invite_link.invite_link}\n"
            f"<b>(Valid for one-time use)</b>\n\n"
            f"✅ After joining this channel, type /start to access the instructor account.\n\n"
            f"<b>🌐 Your plan will expire on {day} at {time_str}.</b>",
            parse_mode="HTML"
        )
        # Notify admin that this user has successfully generated the channel invite link.
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"<b>🔰SUBSCRIPTION ACTIVATED🔰</b>\n\n"
                 f"✅ Subscription code redeem successfully\n\n"
                 f"<b>User ID:</b> {user_id}\n"
                 f"<b>Expiry:</b> {day} at {time_str}",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Invalid or expired code. Try again.")

    return ConversationHandler.END

# ------------------ Handler: Process Code Input ------------------ #
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global subscription_code  # Access the global variable
    query = update.callback_query
    await query.answer()  # Acknowledge the button press
    if query.data.startswith("generate_"):
        days = int(query.data.split("_")[1])
        print(f"days: {days}")
        code = generate_code(days)
        await query.message.reply_text(f"Generated Code: `{code}` \n(Valid for {days} days)", parse_mode="Markdown")

    if query.data.startswith("verify_"):
        user_id = query.data.replace("verify_", "")
        print(f"user id: {user_id}, data type: {type(user_id)}")
        sent_message = await query.edit_message_text(f"♻️ Payment verifying. Please wait...")
        try:
            payment_details = fetch_payment_details(user_id,price)
            print(payment_details)
            paid_amount = int(payment_details['amount'])

            if paid_amount==price:
                context.job_queue.run_once(delete_message, 0, data=(sent_message.chat.id, sent_message.message_id))
                # Get details from payment
                user_name = payment_details['name']
                user_email = payment_details.get('email', "Unknown")  # Get email, default to "Unknown"
                user_mobile = payment_details.get('mobile', "Unknown")  # Get mobile, default to "Unknown"

                # Set expiry date (e.g., 30 days from now)
                expiry_dt = datetime.now() + timedelta(days=30)

                # Save to Firestore with real email & mobile
                save_subscription(user_id, user_name, expiry_dt, user_email, user_mobile)

                # user_name = payment_details['name']
                # user_email = payment_details['email']
                # user_mobile = payment_details['mobile']
                # subscription_data[user_id] = {
                #     "name": user_name,
                #     "email": user_email,
                #     "mobile": user_mobile,
                #     "expiry": expiry_dt
                # }
                # save_subscription_data()

                DELETED_CODES_URL = f"{PAYMENT_CAPTURED_DETAILS_URL}/amount/{paid_amount}"
                requests.delete(url=DELETED_CODES_URL)
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
                    await query.message.reply_text(
                        f"<b>🔰PAYMENT VERIFIED🔰</b>\n\n"
                        f"🙏Thank you for making the payment.\n\n"
                        f"🚀 Here is your premium member invite link:\n{invite_link.invite_link}\n"
                        f"<b>(Valid for one-time use)</b>\n\n"
                        f"✅ After joining this channel, type /start to access the instructor account.\n\n"
                        f"<b>🌐 Your plan will expire on {day} at {time_str}.</b>",
                        parse_mode="HTML"
                    )
                    # Notify admin that this user has successfully generated the channel invite link.
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"<b>🔰SUBSCRIPTION PURCHASED🔰</b>\n\n"
                             f"<b>Name:</b> <a href='tg://user?id={user_id}'>{user_name}</a>\n"
                             f"<b>Email:</b> {user_email}\n"
                             f"<b>Mobile No:</b> {user_mobile}\n"
                             f"<b>User ID:</b> {user_id}\n"
                             f"<b>Expiry:</b> {day} at {time_str}",
                        parse_mode="HTML"
                    )
                    context.job_queue.run_once(delete_message, 0, data=(sent_message.chat.id, sent_message.message_id))
                except Exception as e:
                    await query.message.reply_text("Error generating invite link. Please try again later.")
                    logger.error(f"Error creating invite link: {e}")
            else:
                await query.message.reply_text("❌ There is an error in verifying your payment. Please contact Admin @coding_services.")
        except Exception as e:
            context.job_queue.run_once(delete_message, 0, data=(sent_message.chat.id, sent_message.message_id))
            await query.message.reply_text("First make the payment, then click on Verify Payment button.")
            logger.error(f"Error verifying payment: {e}")
            return None


# ------------------ Periodic Task: Check Expired Subscriptions ------------------ #
async def check_expired_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    remove_expired_subscriptions()  # Remove expired entries from Firestore
    subscription_data = load_subscriptions()  # Refresh from Firebase
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

# ------------------ Admin Command: Show Users ------------------ #
async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not authorized to use this command.")
        return
    subscription_data = load_subscriptions()  # Refresh from Firebase
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

# ------------------ Start Command ------------------ #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global subscription_code  # Declare as global so we can assign to it
    try:
        user_id = update.message.from_user.id
        chat_member = await context.bot.get_chat_member(PRIVATE_CHANNEL_ID, user_id)
        is_premium = chat_member.status in ["member", "administrator", "creator"]
        url_button_text = "🚀Access Turnitin Account🚀" if is_premium else f"🚀Make Payment of Rs {price}/-🚀"
        verify_payment_button_text = "✅Verify Payment" if not is_premium else None
        url_button = InlineKeyboardButton(
            url_button_text,
            web_app=WebAppInfo(url=ACCOUNT_URL) if is_premium else None,
            url=PAYMENT_URL if not is_premium else None,
        )
        keyboard = [[url_button]]
        if verify_payment_button_text:  # Only add if it has a valid value
            download_button = InlineKeyboardButton(verify_payment_button_text, callback_data=f"verify_{user_id}")
            keyboard.append([download_button])
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
                                   data=(sent_message.chat.id, sent_message.message_id)) if is_premium else None
    except BadRequest as e:
        logger.error(f"BadRequest Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__} - {e}")


# ------------------ Admin Command: Update Price ------------------ #
async def update_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global price
    user_id = update.message.from_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Please provide a new price. Usage: `/update_price 2000`")
        return

    try:
        new_price = int(context.args[0])
        if new_price <= 0:
            raise ValueError("Price must be a positive number.")

        price = new_price
        await update.message.reply_text(f"✅ New price has been set to Rs {price}/-")
        logger.info(f"Admin set a new price: Rs {price}/-")
    except ValueError:
        await update.message.reply_text("❌ Invalid price! Please enter a valid positive number.")


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
/update_price - Set new price for instructor bot 
/generate_code - Generate a subscription redeem code
"""
    )


# ------------------ Help Command ------------------ #
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        """
Commands available:
/start - Start the bot and check your membership
/redeem_code - Redeem subscription code provided by Admin
/admin_commands - Show all commands that only admin can use
/help - Show this help message
"""
    )

# ------------------ Main Function ------------------ #
def main():
    global subscription_data, codes_data
    # subscription_data = load_subscription_data()
    subscription_data = load_subscriptions()  # Load from Firebase
    codes_data = load_codes()
    application = Application.builder().token(TOKEN).build()
    # Upload file conversation handler
    conv_handler_upload = ConversationHandler(
        entry_points=[CommandHandler("redeem_code", redeem_code)],
        states={
            WAITING_FOR_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_code)],
        },
        fallbacks=[
            CommandHandler("redeem_code", redeem_code),
        ],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("show_users", show_users))
    application.add_handler(CommandHandler("generate_code", generate_code_command))
    application.add_handler(CommandHandler("admin_commands", admin_commands))
    application.add_handler(CommandHandler("update_price", update_price))  # New command handler
    application.add_handler(CommandHandler("help", help_command))
    # application.add_handler(CommandHandler("redeem_code", redeem_code))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(conv_handler_upload)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(lambda: asyncio.run(check_expired_subscriptions(application)), "interval", hours=1)
    # scheduler.add_job(lambda: asyncio.run(check_expired_subscriptions(application)), "interval", minutes=1)
    scheduler.start()
    application.run_polling()

if __name__ == "__main__":
    main()
