import json
import requests
import re

from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse
from flask import session
# from msal import ConfidentialClientApplication # pip install msal
from config import Config
from database import users_collection, inbox_messages_collection, inbox_conversations_collection # Assuming database.py provides this

from utils.transform_utils import decode_conversation_index, convert_utc_to_local
from utils.message_parsing import get_unique_body_outlook, get_inline_attachments_outlook


# https://fujifp.webhook.office.com/webhookb2/913cd079-55ac-4bbf-89d7-010c915152f0@4148b7ec-5c94-49f0-b57d-af6b7db7a0e9/IncomingWebhook/70468024b559404590c12691013a1c1d/99e943eb-614f-4f97-8b2f-7070b762237e/V27AfS48Mc1nlsmIqpDk2Nq8Wpf7Gs9ycjJSjU07DWdDE1

celery_app = None
msal_app = None

_app_token_cache = {}
def get_application_access_token():
    """
    Acquires and caches a new application-level access token.
    This token is for the application itself, not a specific user.
    """
    global _app_token_cache
    
    cached_token = _app_token_cache.get('access_token')
    expires_at = _app_token_cache.get('expires_at')

    # Check if the cached token is still valid
    if cached_token and expires_at and expires_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        print("Using cached application access token.")
        return cached_token

    token_url = f"https://login.microsoftonline.com/{Config.MS_GRAPH_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        'client_id': Config.MS_GRAPH_CLIENT_ID,
        'scope': ["https://graph.microsoft.com/.default"],
        'client_secret': Config.MS_GRAPH_CLIENT_SECRET,
        'grant_type': 'client_credentials'
    }
    # print(payload)

    try:
        response = requests.post(token_url, data=payload)
        # print(response.content)
        response.raise_for_status()
        token_data = response.json()
        # print(token_data)
        
        expires_in = token_data.get('expires_in', 3600)
        _app_token_cache['access_token'] = token_data.get('access_token')
        _app_token_cache['expires_at'] = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
        print("Successfully acquired and cached new application access token.")
        return _app_token_cache['access_token']
    except requests.exceptions.RequestException as e:
        print(f"Error acquiring application access token: {e}")
        return None
    
def save_outlook_credentials(user_id, token_response, expires_in):
    """Saves user's Microsoft Graph API tokens to MongoDB."""
    users_collection.update_one(
        {'user_id': user_id},
        {'$set': {
            'credentials': {
                'access_token': token_response.get('access_token'),
                'refresh_token': token_response.get('refresh_token'),
                'expires_at': datetime.now(timezone.utc) + timedelta(seconds=expires_in),
                'scope': token_response.get('scope'),
                'token_type': token_response.get('token_type'),
            },
            'account_type':'licensed'
        }},
        upsert=True
    )
    print(f"Outlook credentials saved for user: {user_id}")

def load_outlook_credentials(user_id):
    """Loads and refreshes user's Microsoft Graph API tokens from MongoDB."""
    print("Load Outlook Credentials")
    user_data = users_collection.find_one({'user_id': user_id})
    if user_data and 'credentials' in user_data:
        token_info = user_data['credentials']
        expires_at_utc = token_info.get('expires_at')

        # IMPORTANT FIX: Ensure expires_at_utc is timezone-aware if it's a datetime object
        if isinstance(expires_at_utc, datetime) and expires_at_utc.tzinfo is None:
            expires_at_utc = expires_at_utc.replace(tzinfo=timezone.utc)
        
        # Check if token is expired or close to expiration (e.g., within 5 minutes)
        if expires_at_utc and expires_at_utc < datetime.now(timezone.utc) + timedelta(minutes=5):
            print(f"Outlook token expired for user: {user_id}. Attempting refresh.")
            refresh_token = token_info.get('refresh_token')
            if refresh_token:
                result = msal_app.acquire_token_by_refresh_token(
                    refresh_token,
                    scopes=Config.MS_GRAPH_SCOPES
                )
                if "access_token" in result:
                    print(f"Outlook token refreshed for {user_id}.")
                    save_outlook_credentials(user_id, result, result.get('expires_in'))
                    return result.get('access_token')
                else:
                    print(f"Error refreshing Outlook token for {user_id}: {result.get('error_description')}")
                    return None
            else:
                print(f"No refresh token available for {user_id}. User needs to re-authenticate.")
                return None
        else:
            print(f"Outlook token for {user_id} is valid.")
            return token_info.get('access_token')
    return None

# --- Unified access token retrieval ---
def get_outlook_access_token(user_id):
    """
    Retrieves the correct access token based on the account type.
    """
    user_data = users_collection.find_one({'user_id': user_id})
    if not user_data:
        print(f"User {user_id} not found.")
        return None

    account_type = user_data.get('account_type')
    if account_type == 'licensed':
        return load_outlook_credentials(user_id)
    elif account_type == 'unlicensed':
        return get_application_access_token()
    else:
        print(f"Unknown account type for {user_id}.")
        return None

def get_outlook_message_details_graph(access_token, message_id):
    """Fetches full message details (subject, body, sender) using Microsoft Graph API."""
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    # Using 'me' for the signed-in user, and specifying fields to reduce payload
    graph_url = f"{Config.MS_GRAPH_ENDPOINT}/me//mailfolders('nbox')messages/{message_id}?$select=subject,body,sender"
    
    try:
        response = requests.get(graph_url, headers=headers)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        message_data = response.json()

        # Extracting details
        subject = message_data.get('subject', 'N/A')
        # Body content can be HTML or text. We need plain text.
        body_content = message_data.get('body', {})
        body = body_content.get('content', 'No body available.')
        if body_content.get('contentType') == 'html':
            # Basic HTML to plain text conversion (consider a more robust library for production)
            from bs4 import BeautifulSoup # pip install beautifulsoup4
            soup = BeautifulSoup(body, 'html.parser')
            body = soup.get_text()

        sender_email = message_data.get('sender', {}).get('emailAddress', {}).get('address', 'N/A')
        sender_name = message_data.get('sender', {}).get('emailAddress', {}).get('name', '')
        sender = f"{sender_name} <{sender_email}>" if sender_name else sender_email
        web_link = message_data.get('webLink')

        print(f"Fetched Outlook message details from Graph API for {message_id}")
        return {
            'subject': subject,
            'body': body,
            'sender': sender,
            'webLink': web_link
        }
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Outlook message {message_id} from Graph API: {e}")
        return None

def send_outlook_reply_graph(access_token, message_id, reply_body):
    """Sends a reply to an Outlook email using Microsoft Graph API."""
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    graph_url = f"{Config.MS_GRAPH_ENDPOINT}/me/messages/{message_id}/reply"

    # The 'reply' endpoint expects a 'Comment' and allows specifying recipient if not replying to sender
    # For a simple reply to sender, just include 'comment'
    payload = {
        "comment": reply_body,
        "message": {
            "body": {
                "contentType": "Text", # Or "Html" if you send HTML
                "content": reply_body
            }
        }
    }
    
    try:
        response = requests.post(graph_url, headers=headers, json=payload)
        response.raise_for_status() # Raise an exception for HTTP errors
        print(f"Successfully sent reply to message {message_id} via Graph API.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error sending reply to message {message_id} via Graph API: {e}")
        return False

# --- Unified subscription creation ---
def subscribe_to_outlook_mail_webhook(user_id):
    """
    Creates a Microsoft Graph API webhook subscription for new mail.
    Dynamically uses the correct access token and resource URL.
    """
    # print(user_id)
    access_token = get_outlook_access_token(user_id)
    # print(access_token)
    if not access_token:
        print(f"Failed to get access token for webhook subscription for {user_id}.")
        return False
    
    user_data = users_collection.find_one({'user_id': user_id})
    account_type = user_data.get('account_type')

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    # Determine the resource URL based on account type
    if account_type == 'licensed':
        resource_url = "/me/mailfolders('inbox')/messages"
    elif account_type == 'unlicensed':
        resource_url = f"/users/{user_id}/mailfolders('inbox')/messages"
    else:
        print(f"Cannot create subscription, unknown account type for {user_id}.")
        return False

    # Check for existing subscription logic is omitted for brevity but should be included.

    dt_aware_utc = datetime.now(timezone.utc) + timedelta(minutes=Config.MS_GRAPH_WEBHOOK_EXPIRATION_MINUTES)
    expiration_datetime = dt_aware_utc.isoformat(timespec='microseconds')
    if '+' in expiration_datetime:
        expiration_datetime = expiration_datetime.split('+')[0]
    expiration_datetime += 'Z' 

    payload = {
        "changeType": "created",
        "notificationUrl": Config.MS_GRAPH_WEBHOOK_NOTIFICATION_URL,
        "resource": resource_url,
        "expirationDateTime": expiration_datetime,
        "clientState": user_id,
    }
    
    try:
        response = requests.post(f"{Config.MS_GRAPH_ENDPOINT}/subscriptions", headers=headers, json=payload)
        response.raise_for_status()
        subscription_data = response.json()
        print(f"Outlook webhook subscription created for {user_id}: {subscription_data.get('id')}")
        return True
    except requests.exceptions.RequestException as e:
        error_detail = e.response.text if e.response else str(e)
        print(f"Error creating Outlook webhook subscription for {user_id}: {e.response.status_code if e.response else 'N/A'} - {error_detail}")
        return False


def extract_email_thread(text, delim1, delim2):
    pattern = re.compile(f"({re.escape(delim1)}|{re.escape(delim2)})")
    match = pattern.search(text)
    if match:
        split_point = match.start()
        print(split_point)
        delimiter_length = len(match.group(0))
        return [text[:split_point], text[split_point + delimiter_length:]]
    else:
        return [text]
# --- Unified webhook notification processing ---

CURRENT_MESSAGE_ID = ""
def process_outlook_webhook_notification_unified(notification_data):
    """
    Processes a single Microsoft Graph webhook notification for any account type.
    """
    resource = notification_data.get('resource')
    change_type = notification_data.get('changeType')
    user_id = notification_data.get('clientState')
    global CURRENT_MESSAGE_ID
    # print(f"Received change type: {change_type} for user: {user_id}")
    if resource and 'messages' in resource.lower() and change_type == 'created':
        print(f"Processing new message notification for user: {user_id}")
        access_token = get_outlook_access_token(user_id)
        if not access_token:
            print(f"Could not load access token for {user_id}.")
            return False

        headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
        
        # Determine the API endpoint based on the account type
        user_data = users_collection.find_one({'user_id': user_id})
        account_type = user_data.get('account_type') if user_data else None
        BASE_ENDPOINT=""
        if account_type == 'licensed':
            BASE_ENDPOINT = f"{Config.MS_GRAPH_ENDPOINT}/me"
        elif account_type == 'unlicensed':
            BASE_ENDPOINT = f"{Config.MS_GRAPH_ENDPOINT}/users/{user_id}"


        api_endpoint = f"{BASE_ENDPOINT}/mailfolders('inbox')/messages?$top=1&$orderby=receivedDateTime desc"
        print("API_ENDPOINT : ", api_endpoint)

        try:
            resp = requests.get(api_endpoint, headers=headers)
            resp.raise_for_status()
            messages = resp.json().get('value', [])
            
            if messages:
                latest_msg = messages[0]
                message_id = latest_msg.get('id')
                conv_id = latest_msg.get('conversationId')
                # print("Messaeg :", message_id)
                if message_id==CURRENT_MESSAGE_ID:
                    return True
                else:
                    CURRENT_MESSAGE_ID = message_id
                # print(inbox_conversations_collection.find_one({'conv_id': conv_id, 'messages.message_id': message_id}))
                if inbox_conversations_collection.find_one({'conv_id': conv_id, 'messages.message_id': message_id}):
                    print(f"Message with ID '{message_id}' already processed. Exiting.")
                    return True
                conv_index = latest_msg.get('conversationIndex')
                number_of_child_replies = decode_conversation_index(latest_msg.get('conversationIndex')).get("number of replies", '')
                subject = latest_msg.get('subject', 'N/A')
                # Extract sender information
                sender_info = latest_msg.get('sender', {}).get('emailAddress', {})
                sender = sender_info.get('address', 'N/A')
                
                # Extract all recipients (To, CC, BCC)
                receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in latest_msg.get('toRecipients', [])]
                
                cc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in latest_msg.get('ccRecipients', [])]

                bcc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in latest_msg.get('bccRecipients', [])]
                msg_endpoint = f"{BASE_ENDPOINT}/messages/{message_id}?$select=uniqueBody"
                single_msg_resp = requests.get(msg_endpoint, headers=headers)
                single_msg_resp.raise_for_status()
                single_msg_data = single_msg_resp.json()

                body_content = single_msg_data.get("uniqueBody", {})
                cleaned_body = get_unique_body_outlook(body_content)
                received_time = convert_utc_to_local(latest_msg.get('receivedDateTime', {}))

                # messages = extract_email_thread(body_plain, "From:", "差出人:")

                # --- NEW: Attachment Processing ---
                inline_attachments = get_inline_attachments_outlook(body_content)
                attachments_data = []
                if latest_msg.get('hasAttachments') or len(inline_attachments)>0:
                    print(f"  Message {message_id[:10]} has attachments. Fetching attachment details...")
                    attachments_url = f"{BASE_ENDPOINT}/messages/{message_id}/attachments"
                    try:
                        attachments_resp = requests.get(attachments_url, headers=headers)
                        attachments_resp.raise_for_status()
                        fetched_attachments = attachments_resp.json().get('value', [])
                        
                        for attach in fetched_attachments:
                            # Only store relevant fields and contentBytes if available
                            attachment_info = {
                                'id': attach.get('id'),
                                'name': attach.get('name'),
                                'contentType': attach.get('contentType'),
                                'size': attach.get('size'),
                                'isInline': attach.get('isInline', False),
                                'contentBytes': attach.get('contentBytes') 
                            }
                            attachments_data.append(attachment_info)
                        print(f"  Fetched {len(attachments_data)} attachment details for message {message_id}.")
                    except requests.exceptions.RequestException as attach_e:
                        print(f"  Error fetching attachments for message {message_id}: {attach_e}")
                        if attach_e.response:
                            print(f"  Attachment fetch error response: {attach_e.response.text}")
                # --- END NEW: Attachment Processing ---
                message_doc = {
                    'message_id': message_id,
                    'subject':subject,
                    'conv_index':conv_index,
                    "child_replies":number_of_child_replies,
                    'sender': sender,
                    'receivers': receivers_list,
                    'cc': cc_list,
                    'bcc': bcc_list,
                    'body': cleaned_body,
                    'full_message_payload': messages,
                    'webLink': latest_msg.get('webLink'),
                    'received_time':received_time,
                     'attachments':attachments_data,
                     'type':'outlook_received_mail'
                }
                # message_doc['type'] = 'outlook_received_mail'

                # conv_doc = {
                #     'conv_id': conv_id, 
                #     'email_address': user_id, 
                #     'messages':[message_doc]
                # }

                filter_query = {'conv_id': conv_id, 'email_address':user_id}

                update_operations = {
                    '$push': {'messages': message_doc},
                    '$setOnInsert': {
                        'conv_id': conv_id,
                        'email_address': user_id
                    }
                }

                result = inbox_conversations_collection.update_one(filter_query, update_operations, upsert=True)

                # print(f"Matched {result.matched_count} document(s).")
                # print(f"Modified {result.modified_count} document(s).")
                if result.upserted_id:
                    print(f"Inserted new document with _id: {result.upserted_id}")
                else:
                    print(f"Document was updated.")
                    
                if celery_app:
                    import workers.tasks as tasks
                    if len(attachments_data)>0:
                        tasks.generate_attachment_summary.delay(conv_id, message_id, user_id, 'outlook')
                    tasks.generate_previous_emails_summary.delay(conv_id, message_id, user_id,)
                    tasks.generate_importance_analysis.delay(conv_id, message_id, user_id)
                    tasks.generate_summary_and_replies.delay(conv_id, message_id, user_id)
                    # tasks.generate_category_task.delay(message_id, user_id)
                    print(f"  Dispatched Celery tasks for Outlook message {message_id}")
                else:
                    print("  Celery app not initialized. Tasks not dispatched.")
                CURRENT_MESSAGE_ID = ""
                return True
            else:
                print(f"Outlook webhook: No latest message found for {user_id} after notification.")
                CURRENT_MESSAGE_ID = ""
                return False
        except requests.exceptions.RequestException as e:
            print(f"Outlook webhook: Error fetching latest message for {user_id}: {e}")
            CURRENT_MESSAGE_ID = ""
            return False
    CURRENT_MESSAGE_ID = ""
    return False

# --- Unlicensed account specific authorization function ---
def authorize_unlicensed_mail(email_address):
    """
    Sets up an unlicensed mailbox for monitoring and saves its type to the database.
    This assumes an admin has already granted app permissions.
    """
    if not email_address:
        return False, "Email address is required."

    # Check if the account is already in the database
    # existing_user = users_collection.find_one({'user_id': email_address})
    # if existing_user and existing_user.get('account_type') == 'unlicensed':
    #     return True, f"Unlicensed mailbox {email_address} is already authorized."

    # Save the account as unlicensed
    users_collection.update_one(
        {'user_id': email_address},
        {'$set': {
            'account_type': 'unlicensed',
            'authorized_at': datetime.now(timezone.utc)
        }},
        upsert=True
    )
    print(f"Unlicensed mailbox {email_address} registered for monitoring.")

    # Immediately subscribe to webhooks for this account
    if subscribe_to_outlook_mail_webhook(email_address):
        return True, f"Unlicensed mailbox {email_address} authorized and webhook subscription created."
    else:
        return False, f"Unlicensed mailbox {email_address} authorized, but webhook subscription failed."
# def subscribe_to_outlook_mail_webhook(access_token, user_id):
#     """Creates a Microsoft Graph API webhook subscription for new mail."""
#     access_token = get_outlook_access_token(user_id)
#     if not access_token:
#         print(f"Failed to get access token for webhook subscription for {user_id}.")
#         return False
    
#     user_data = users_collection.find_one({'user_id': user_id})
#     account_type = user_data.get('account_type')

#     headers = {
#         'Authorization': f'Bearer {access_token}',
#         'Content-Type': 'application/json'
#     }

#     # Determine the resource URL based on account type
#     if account_type == 'licensed':
#         resource_url = '/me/messages'
#     elif account_type == 'unlicensed':
#         resource_url = f'/users/{user_id}/messages'
#     else:
#         print(f"Cannot create subscription, unknown account type for {user_id}.")
#         return False
    
#     dt_aware_utc = datetime.now(timezone.utc) + timedelta(minutes=Config.MS_GRAPH_WEBHOOK_EXPIRATION_MINUTES)
#     expiration_datetime = dt_aware_utc.isoformat(timespec='microseconds')
#     if '+' in expiration_datetime:
#         expiration_datetime = expiration_datetime.split('+')[0]
#     expiration_datetime += 'Z' 

#     payload = {
#         "changeType": "created",
#         "notificationUrl": Config.MS_GRAPH_WEBHOOK_NOTIFICATION_URL,
#         "resource": resource_url,
#         "expirationDateTime": expiration_datetime,
#         "clientState": user_id,
#     }
    
#     # Check for existing subscription for this user
#     existing_subscriptions_url = f"{Config.MS_GRAPH_ENDPOINT}/subscriptions"
#     try:
#         resp = requests.get(existing_subscriptions_url, headers=headers, json=payload)
#         print('Resp', resp)
#         resp.raise_for_status()
#         existing_subs = resp.json().get('value', [])
#         for sub in existing_subs:
#             # Check if a subscription already exists for this resource and notification URL
#             # Or if it's expired, delete and recreate
#             if sub.get('resource') == '/me/messages' and sub.get('notificationUrl') == Config.MS_GRAPH_WEBHOOK_NOTIFICATION_URL:
#                 if datetime.fromisoformat(sub.get('expirationDateTime').replace('Z', '+00:00')) > datetime.now(timezone.utc) + timedelta(hours=1):
#                     print(f"Existing, active Outlook webhook subscription found for {user_id}: {sub.get('id')}. Skipping creation.")
#                     return True # Subscription is active, no need to recreate
#                 else:
#                     print(f"Existing Outlook webhook subscription for {user_id} is near expiration or expired. Deleting...")
#                     requests.delete(f"{Config.MS_GRAPH_ENDPOINT}/subscriptions/{sub.get('id')}", headers=headers)
#                     print("Deleted expired subscription.")
#         print(existing_subs)

#     except requests.exceptions.RequestException as e:
#         print(f"Error checking existing Outlook subscriptions: {e}")
#         # Continue to try creating a new one if checking fails

#     # Create new subscription
#     # expiration_datetime =(datetime.now(timezone.utc) + timedelta(minutes=Config.MS_GRAPH_WEBHOOK_EXPIRATION_MINUTES)).isoformat(timespec='microseconds') + 'Z'
#     # expiration_datetime =(datetime.now(timezone.utc) + timedelta(minutes=60*24)).isoformat(timespec='seconds') + 'Z'
#     dt_aware_utc = datetime.now(timezone.utc) + timedelta(minutes=Config.MS_GRAPH_WEBHOOK_EXPIRATION_MINUTES)
#     expiration_datetime = dt_aware_utc.isoformat(timespec='microseconds')
#     # Remove the '+00:00' offset if present, then append 'Z'
#     if '+' in expiration_datetime:
#         expiration_datetime = expiration_datetime.split('+')[0]
#     expiration_datetime += 'Z' 

#     payload = {
#         "changeType": "created", # Listen for new messages
#         "notificationUrl": Config.MS_GRAPH_WEBHOOK_NOTIFICATION_URL,
#         "resource": "/me/messages",
#         "expirationDateTime": expiration_datetime,
#         "clientState": user_id,
	
#     }
#     print(payload)

#     try:
#         response = requests.post(f"{Config.MS_GRAPH_ENDPOINT}/subscriptions", headers=headers, json=payload)
#         print("Response ", response.json())
#         response.raise_for_status()
#         subscription_data = response.json()
#         print(f"Outlook webhook subscription created for {user_id}: {subscription_data.get('id')}")
#         return True
#     except requests.exceptions.RequestException as e:
#         error_detail = ""
#         if e.response:
#             try:
#                 error_detail = json.dumps(e.response.json(), indent=2)
#             except json.JSONDecodeError:
#                 error_detail = e.response.text
#         else:
#             error_detail = str(e) # Convert the exception object to a string

#         print(f"Error creating Outlook webhook subscription for {user_id}: {e.response.status_code if e.response else 'N/A'} - {error_detail}")
#         return False

# def process_outlook_webhook_notification(notification_data):
#     """Processes a single Microsoft Graph webhook notification."""
#     # Ensure this is a valid change notification for a new message
#     resource = notification_data.get('resource')
#     change_type = notification_data.get('changeType')
#     client_state = notification_data.get('clientState') # This is the user_id we set

#     if resource and 'messages' in resource.lower() and change_type == 'created':
#         user_id = client_state # Our user_id
        
#         print(f"Processing new message notification for user: {user_id}")
        
#         access_token = load_outlook_credentials(user_id)
#         if access_token:
#             try:
#                 headers = {
#                     'Authorization': f'Bearer {access_token}',
#                     'Content-Type': 'application/json'
#                 }
#                 latest_message_url = f"{Config.MS_GRAPH_ENDPOINT}/me/messages?$top=1&$orderby=receivedDateTime desc&$select=id,subject,sender,body,webLink"
#                 resp = requests.get(latest_message_url, headers=headers)
#                 resp.raise_for_status()
#                 messages = resp.json().get('value', [])
                
#                 if messages:
#                     latest_msg = messages[0]
#                     message_id = latest_msg.get('id')
#                     subject = latest_msg.get('subject', 'N/A')
#                     sender_email = latest_msg.get('sender', {}).get('emailAddress', {}).get('address', 'N/A')
#                     sender_name = latest_msg.get('sender', {}).get('emailAddress', {}).get('name', '')
#                     sender = f"{sender_name} <{sender_email}>" if sender_name else sender_email
                    
#                     body_content = latest_msg.get('body', {})
#                     body_plain = body_content.get('content', 'No body available.')
#                     if body_content.get('contentType') == 'html':
#                         soup = BeautifulSoup(body_plain, 'html.parser')
#                         body_plain = soup.get_text()
#                     message_doc = {
#                         'message_id': message_id,
#                         'email_address': user_id,
#                         'sender': sender,
#                         'subject': subject,
#                         'snippet': body_plain[:200] + '...' if len(body_plain) > 200 else body_plain,
#                         'full_message_payload': latest_msg, # Save full payload
#                         'webLink': latest_msg.get('webLink') # Store web link
#                     }

#                     # message_type = 'outlook_received_mail'
#                     if sender_email.lower() == user_id.lower():
#                         message_doc['type'] = 'outlook_sent_mail'
#                         print(f"  Detected sent mail: {subject}")
#                     else:
#                         message_doc['type'] = 'outlook_received_mail'
#                         print(f"  Detected received mail: {subject}")
#                         # Save to database
#                         messages_collection.update_one( # Reusing existing collection
#                             {'message_id': message_id, 'email_address': user_id},
#                             {'$set': message_doc},
#                             upsert=True
#                         )
#                         print(f"Outlook webhook: Saved new message {message_id} for {user_id}: '{subject}'")

#                         # --- Dispatch Celery tasks asynchronously ---
#                         if celery_app: # Ensure celery_app is initialized globally in app.py and passed to utils
#                             import workers.tasks as tasks
#                             tasks.generate_importance_analysis.delay(message_id, user_id)
#                             tasks.generate_summary_and_replies.delay(message_id, user_id)
#                             tasks.generate_category_task.delay(message_id, user_id)
#                             print(f"  Dispatched Celery tasks for Outlook message {message_id}")
#                         else:
#                             print("  Celery app not initialized. Tasks not dispatched.")

#                     return True
#                 else:
#                     print(f"Outlook webhook: No latest message found for {user_id} after notification.")
#                     return False
#             except requests.exceptions.RequestException as e:
#                 print(f"Outlook webhook: Error fetching latest message for {user_id} from Graph API: {e}")
#                 return False
#         else:
#             print(f"Outlook webhook: Could not load access token for {user_id}. User needs to re-authenticate for full message fetch.")
#             return False
#     return False


