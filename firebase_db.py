import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

DB_FILE_NAME = "testing_database"  # Define the firebase database file
# DB_FILE_NAME = "Instructor_subscriptions"  # Define the firebase database file

# Load Firebase credentials
cred = credentials.Certificate("firebase-adminsdk.json")  # Use your downloaded key
firebase_admin.initialize_app(cred)
db = firestore.client()

def save_subscription(user_id, name, expiry, email="Unknown", mobile="Unknown"):
    """Save user subscription to Firestore with email & mobile"""
    doc_ref = db.collection(DB_FILE_NAME).document(str(user_id))
    doc_ref.set({
        "name": name,
        "expiry": expiry.strftime("%Y-%m-%d %H:%M"),
        "email": email,  # Default: "Unknown"
        "mobile": mobile  # Default: "Unknown"
    })


def load_subscriptions():
    """Load all subscriptions from Firestore, safely handling errors"""
    try:
        users_ref = db.collection(DB_FILE_NAME).stream()
        return {
            user.id: {
                "name": user.to_dict().get("name", "Unknown"),
                "expiry": datetime.strptime(user.to_dict()["expiry"], "%Y-%m-%d %H:%M"),
                "email": user.to_dict().get("email", "Unknown"),
                "mobile": user.to_dict().get("mobile", "Unknown")
            }
            for user in users_ref
        }
    except Exception as e:
        print(f"Firestore Error: {e}")
        return {}  # Return empty dict instead of crashing


def remove_expired_subscriptions():
    """Remove expired subscriptions from Firestore"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    users_ref = db.collection(DB_FILE_NAME).stream()

    for user in users_ref:
        data = user.to_dict()
        if data["expiry"] < now:
            db.collection(DB_FILE_NAME).document(user.id).delete()

    # remove_expired_subscriptions()
