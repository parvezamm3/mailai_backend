# blueprints/webhook_bp.py
import json
import base64
from flask import Blueprint, request, jsonify
from config import Config
from utils.gmail_utils import load_google_credentials, fetch_gmail_history

webhook_bp = Blueprint('gmail_webhook_bp', __name__)

@webhook_bp.route('/gmail-webhook', methods=['POST'])
def gmail_webhook():
    """
    Receives push notifications from Google Cloud Pub/Sub, indicating Gmail changes.
    Fetches history and saves new messages to MongoDB.
    """
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            print("Invalid Pub/Sub message format.")
            return jsonify({"status": "error", "message": "Invalid Pub/Sub message format"}), 400

        pubsub_message = data['message']
        encoded_data = pubsub_message['data']
        decoded_data = base64.b64decode(encoded_data).decode('utf-8')
        gmail_notification = json.loads(decoded_data)
        # print(f"Decoded Gmail notification: {json.dumps(gmail_notification, indent=2)}")

        email_address = gmail_notification.get('emailAddress')
        if not email_address:
            print(f"Missing emailAddress in Gmail notification: {gmail_notification}")
            return jsonify({"status": "error", "message": "Missing emailAddress"}), 400
        
        credentials, last_stored_history_id = load_google_credentials(email_address) # Use helper
        if not credentials:
            print(f"No credentials found for {email_address}. Cannot fetch history.")
            return jsonify({"status": "error", "message": "User not authorized or token expired"}), 401
        
        # Use the historyId from the notification if no last_stored_history_id
        start_fetch_history_id = last_stored_history_id or gmail_notification.get('historyId')
        if not start_fetch_history_id:
            print(f"Cannot determine start_history_id for fetching history for {email_address}.")
            return jsonify({"status": "error", "message": "Cannot determine start_history_id"}), 400
        
        new_messages, new_latest_history_id = fetch_gmail_history(credentials, email_address, start_fetch_history_id) # Use helper

        # Always update the user's last_history_id with the latest one received from the API
        if new_latest_history_id:
            from database import users_collection
            users_collection.update_one( # Use imported collection
                {'user_id': email_address},
                {'$set': {'last_history_id': new_latest_history_id}}
            )
            print(f"Updated last_history_id for {email_address} to {new_latest_history_id}")
        else:
            print(f"Warning: No new historyId returned by fetch_gmail_history for {email_address}. last_history_id not updated.")

        return jsonify({"status": "success", "message": "Notification processed"}), 200

    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({"status": "error", "message": f"Internal server error: {e}"}), 200


# @webhook_bp.route('/sync_all_mail', methods=['POST'])
# def sync_all_mail():
#     """
#     Triggers a one-time sync of all previous INBOX and SENT messages for a user.
#     """
#     try:
#         data = request.get_json()
#         email_address = data.get('email_address')
#         if not email_address:
#             return jsonify({"status": "error", "message": "Missing email_address"}), 400

#         credentials, _ = load_google_credentials(email_address)
#         if not credentials:
#             return jsonify({"status": "error", "message": "User not authorized"}), 401

#         # This will be a long-running process, so you might want to run it as a background task.
#         sync_all_mail_history(credentials, email_address)

#         return jsonify({"status": "success", "message": "Full sync initiated"}), 200
    
#     except Exception as e:
#         print(f"Error initiating full mail sync: {e}")
#         return jsonify({"status": "error", "message": f"Internal server error: {e}"}), 200