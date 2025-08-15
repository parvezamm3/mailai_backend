import json
from flask import Blueprint, request, jsonify
from utils.outlook_utils import process_outlook_webhook_notification_unified
from config import Config # Import Config for MS_GRAPH_WEBHOOK_NOTIFICATION_URL

outlook_webhook_bp = Blueprint('outlook_webhook_bp', __name__)

@outlook_webhook_bp.route('/outlook-webhook', methods=['POST'])
def outlook_webhook():
    """
    Receives push notifications from Microsoft Graph API.
    Handles validation and processes new mail notifications.
    """
    try:
        # Microsoft Graph webhook validation (initial setup)
        validation_token = request.args.get('validationToken')
        print(f"Validataion token  {validation_token}")
        if validation_token:
            print(f"Received Outlook webhook validation request. Token: {validation_token}")
            # Respond with the validation token to confirm the endpoint
            return validation_token, 200, {'Content-Type': 'text/plain'}

        # Process actual change notification
        notification_data = request.get_json()
        if not notification_data or 'value' not in notification_data:
            print("Invalid Outlook webhook notification format.")
            return jsonify({"status": "error", "message": "Invalid notification format"}), 400

        # Each 'value' in the payload is a notification
        for notification in notification_data['value']:
            # print(f"Processing Outlook notification: {json.dumps(notification, indent=2)}")
            # You should verify the clientState and potentially message integrity here
            # For simplicity, we'll just pass the notification to a helper for processing
            if not process_outlook_webhook_notification_unified(notification):
                print(f"Failed to process Outlook notification: {notification}")

        # Always return 202 Accepted to Graph API to acknowledge receipt,
        # even if processing fails internally. Handle errors via logging.
        return jsonify({"status": "accepted"}), 202

    except Exception as e:
        print(f"Error processing Outlook webhook: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500
