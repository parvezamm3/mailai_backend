import json
import requests
import re

from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse
from flask import session
# from msal import ConfidentialClientApplication # pip install msal
from config import Config
from database import users_collection, inbox_conversations_collection
# from database_async import get_async_db

from utils.transform_utils import decode_conversation_index, convert_utc_str_to_local_datetime
from utils.message_parsing import get_unique_body_outlook, get_inline_attachments_outlook
from workers.tasks import (
    generate_attachment_summary, generate_previous_emails_summary, 
    generate_importance_analysis, generate_summary_and_replies, 
    run_analysis_agent_stateful
)

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

    try:
        response = requests.post(token_url, data=payload)
        response.raise_for_status()
        token_data = response.json()
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

def load_outlook_credentials(user_id, user_data):
    """Loads and refreshes user's Microsoft Graph API tokens from MongoDB."""
    print("Load Outlook Credentials")
    # user_data = users_collection.find_one({'user_id': user_id})
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
def get_outlook_access_token(user_id, account_type, user_data):
    """
    Retrieves the correct access token based on the account type.
    """
    if account_type == 'licensed':
        return load_outlook_credentials(user_id, user_data)
    elif account_type == 'unlicensed':
        return get_application_access_token()
    else:
        print(f"Unknown account type for {user_id}.")
        return None
    
def get_base_endpoint(user_id, account_type):
    BASE_ENDPOINT=""
    if account_type == 'licensed':
        BASE_ENDPOINT = f"{Config.MS_GRAPH_ENDPOINT}/me"
    elif account_type == 'unlicensed':
        BASE_ENDPOINT = f"{Config.MS_GRAPH_ENDPOINT}/users/{user_id}"
    return BASE_ENDPOINT

def get_url_headers(user_id, account_type, user_data):
    access_token = get_outlook_access_token(user_id, account_type, user_data)
    if not access_token:
        print(f"Could not load access token for {user_id}.")
        return None

    headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
    return headers

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
             # pip install beautifulsoup4
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
    # db = get_async_db()
    # users_collection_async = db[Config.MONGO_USERS_COLLECTION]
    user_data = users_collection.find_one({'user_id': user_id})
    # print("user Data", user_data)
    account_type = user_data.get('account_type')
    headers = get_url_headers(user_id, account_type, user_data)
    # print(headers)
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
        # print(split_point)
        delimiter_length = len(match.group(0))
        return [text[:split_point], text[split_point + delimiter_length:]]
    else:
        return [text]
# --- Unified webhook notification processing ---

def process_outlook_mail(message_id, owner_mail):
    # db = get_async_db()
    # users_collection_async = db[Config.MONGO_USERS_COLLECTION]
    user_data = users_collection.find_one({'user_id': owner_mail})
    # print(user_data)
    if not user_data:
        print(f'No user with email {owner_mail} exist is the database.')
    account_type = user_data.get('account_type')
    BASE_ENDPOINT = get_base_endpoint(owner_mail, account_type)
    headers = get_url_headers(owner_mail, account_type, user_data)
    msg_endpoint = f"{BASE_ENDPOINT}/messages/{message_id}"
    msg_resp = requests.get(msg_endpoint, headers=headers)
    msg_resp.raise_for_status()
    msg_data = msg_resp.json()

    conv_id = msg_data.get('conversationId')
    new_conv_id, new_msg_id = process_single_mail(BASE_ENDPOINT, owner_mail, conv_id, headers, msg_data, msg_data.get('id'))
    return new_conv_id, new_msg_id

CURRENT_MESSAGE_ID = ""
def process_outlook_webhook_notification_unified(notification_data):
    """
    Processes a single Microsoft Graph webhook notification for any account type.
    """
    # db = get_async_db()
    # users_collection_async = db[Config.MONGO_USERS_COLLECTION]
    resource = notification_data.get('resource')
    change_type = notification_data.get('changeType')
    user_id = notification_data.get('clientState')
    # if user_id=="yokoyama_yu@ffp.co.jp":
    #     return True
    global CURRENT_MESSAGE_ID
    # print(f"Received change type: {change_type} for user: {user_id}")
    if resource and 'messages' in resource.lower() and change_type == 'created':
        print(f"Processing new message notification for user: {user_id}")
        # access_token = get_outlook_access_token(user_id)
        # if not access_token:
        #     print(f"Could not load access token for {user_id}.")
        #     return False

        # headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
        user_data = users_collection.find_one({'user_id': user_id})
        account_type = user_data.get('account_type')
        headers = get_url_headers(user_id, account_type, user_data)
        
        # Determine the API endpoint based on the account type
        BASE_ENDPOINT = get_base_endpoint(user_id, account_type)


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
                if message_id==CURRENT_MESSAGE_ID:
                    return True
                else:
                    CURRENT_MESSAGE_ID = message_id

                
                process_single_mail(BASE_ENDPOINT, user_id, conv_id, headers, latest_msg, message_id)
                # conv_index = latest_msg.get('conversationIndex')
                # number_of_child_replies = decode_conversation_index(latest_msg.get('conversationIndex')).get("number of replies", '')
                # subject = latest_msg.get('subject', 'N/A')
                # # Extract sender information
                # sender_info = latest_msg.get('sender', {}).get('emailAddress', {})
                # sender = sender_info.get('address', 'N/A')
                
                # # Extract all recipients (To, CC, BCC)
                # receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in latest_msg.get('toRecipients', [])]
                
                # cc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in latest_msg.get('ccRecipients', [])]

                # bcc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in latest_msg.get('bccRecipients', [])]
                # msg_endpoint = f"{BASE_ENDPOINT}/messages/{message_id}?$select=uniqueBody"
                # single_msg_resp = requests.get(msg_endpoint, headers=headers)
                # single_msg_resp.raise_for_status()
                # single_msg_data = single_msg_resp.json()

                # body_content = single_msg_data.get("uniqueBody", {})
                # cleaned_body = get_unique_body_outlook(body_content)
                # received_time = convert_utc_to_local(latest_msg.get('receivedDateTime', {}))

                # # messages = extract_email_thread(body_plain, "From:", "差出人:")

                # # --- NEW: Attachment Processing ---
                # inline_attachments = get_inline_attachments_outlook(body_content)
                # attachments_data = []
                # if latest_msg.get('hasAttachments') or len(inline_attachments)>0:
                #     print(f"  Message {message_id[:10]} has attachments. Fetching attachment details...")
                #     attachments_url = f"{BASE_ENDPOINT}/messages/{message_id}/attachments"
                #     try:
                #         attachments_resp = requests.get(attachments_url, headers=headers)
                #         attachments_resp.raise_for_status()
                #         fetched_attachments = attachments_resp.json().get('value', [])
                        
                #         for attach in fetched_attachments:
                #             # Only store relevant fields and contentBytes if available
                #             attachment_info = {
                #                 'id': attach.get('id'),
                #                 'name': attach.get('name'),
                #                 'contentType': attach.get('contentType'),
                #                 'size': attach.get('size'),
                #                 'isInline': attach.get('isInline', False),
                #                 'contentBytes': attach.get('contentBytes') 
                #             }
                #             attachments_data.append(attachment_info)
                #         print(f"  Fetched {len(attachments_data)} attachment details for message {message_id}.")
                #     except requests.exceptions.RequestException as attach_e:
                #         print(f"  Error fetching attachments for message {message_id}: {attach_e}")
                #         if attach_e.response:
                #             print(f"  Attachment fetch error response: {attach_e.response.text}")
                # # --- END NEW: Attachment Processing ---
                # message_doc = {
                #     'message_id': message_id,
                #     'subject':subject,
                #     'conv_index':conv_index,
                #     "child_replies":number_of_child_replies,
                #     'sender': sender,
                #     'receivers': receivers_list,
                #     'cc': cc_list,
                #     'bcc': bcc_list,
                #     'body': cleaned_body,
                #     # 'full_message_payload': messages,
                #     # 'webLink': latest_msg.get('webLink'),
                #     'received_time':received_time,
                #      'attachments':attachments_data,
                #      'type':'outlook_received_mail'
                # }

                # filter_query = {'conv_id': conv_id, 'email_address':user_id}

                # update_operations = {
                #     '$push': {'messages': message_doc},
                #     '$setOnInsert': {
                #         'conv_id': conv_id,
                #         'email_address': user_id
                #     }
                # }

                # result = inbox_conversations_collection.update_one(filter_query, update_operations, upsert=True)

                # if result.upserted_id:
                #     print(f"Inserted new document with _id: {result.upserted_id}")
                # else:
                #     print(f"Document was updated.")
                    
                # if celery_app:
                #     import workers.tasks as tasks
                #     if len(attachments_data)>0:
                #         tasks.generate_attachment_summary.delay(conv_id, message_id, user_id, 'outlook')
                #     tasks.generate_previous_emails_summary.delay(conv_id, message_id, user_id,)
                #     tasks.generate_importance_analysis.delay(conv_id, message_id, user_id)
                #     tasks.generate_summary_and_replies.delay(conv_id, message_id, user_id)
                #     # tasks.generate_category_task.delay(message_id, user_id)
                #     print(f"  Dispatched Celery tasks for Outlook message {message_id}")
                # else:
                #     print("  Celery app not initialized. Tasks not dispatched.")
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
    # db = get_async_db()
    # users_collection = db[Config.MONGO_USERS_COLLECTION]
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


def prepare_conversation_thread(email_address, conversation_id, current_message_id):
    user_data = users_collection.find_one({'user_id': email_address})
    if not user_data:
        print(f'No user with email {email_address} exist is the database.')
    account_type = user_data.get('account_type')
    BASE_ENDPOINT = get_base_endpoint(email_address, account_type)
    headers = get_url_headers(email_address, account_type, user_data)
    conversation_endpoint = f"{BASE_ENDPOINT}/messages?$filter=conversationId eq '{conversation_id}'"
    conv_resp = requests.get(conversation_endpoint, headers=headers)
    conv_resp.raise_for_status()
    conv_response_data = conv_resp.json()
                                
    conv_messages = conv_response_data.get('value', [])
    messages = []
    for msg in conv_messages:
        message_id = msg.get('id')
        msg_endpoint = f"{BASE_ENDPOINT}/messages/{message_id}?$select=uniqueBody"
        single_msg_resp = requests.get(msg_endpoint, headers=headers)
        single_msg_resp.raise_for_status()
        single_msg_data = single_msg_resp.json()

        conv_index = msg.get('conversationIndex')
        number_of_child_replies = decode_conversation_index(msg.get('conversationIndex')).get("number of replies", '')
        subject = msg.get('subject')
        sender_info = msg.get('sender', {}).get('emailAddress', {})
        sender = sender_info.get('address', 'N/A')
        receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('toRecipients', [])]
        receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('toRecipients', [])]
        cc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('ccRecipients', [])]
        bcc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('bccRecipients', [])]
        body_content = single_msg_data.get("uniqueBody", {})
        cleaned_body = get_unique_body_outlook(body_content)
        inline_attachments = get_inline_attachments_outlook(body_content)
        attachments_data = []
        if msg.get('hasAttachments') or len(inline_attachments)>0:
            attachments_url = f"{BASE_ENDPOINT}/messages/{message_id}/attachments"
            try:
                attachments_resp = requests.get(attachments_url, headers=headers)
                attachments_resp.raise_for_status()
                fetched_attachments = attachments_resp.json().get('value', [])
                                                
                for attach in fetched_attachments:
                    attachment_info = {
                            'id': attach.get('id'),
                            'name': attach.get('name'),
                            'contentType': attach.get('contentType'),
                            'size': attach.get('size'),
                            'isInline': attach.get('isInline', False),
                            'contentBytes': attach.get('contentBytes') 
                        }
                    attachments_data.append(attachment_info)
            except requests.exceptions.RequestException as attach_e:
                print(f"  Error fetching attachments for message {message_id}: {attach_e}")
                if attach_e.response:
                    print(f"  Attachment fetch error response: {attach_e.response.text}")
            received_time = convert_utc_str_to_local_datetime(msg.get('receivedDateTime'))
            # print(received_time)                               
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
                    'full_body':msg.get('body'),
                    'received_datetime':received_time,
                    'attachments':attachments_data,
                    'type':'outlook_received_mail'
                }
            filter_query = {'conv_id': conversation_id, 'email_address':email_address}

            update_operations = {
                    '$push': {'messages': message_doc},
                    '$setOnInsert': {
                        'conv_id': conversation_id,
                        'email_address': email_address
                    }
                }

            result = inbox_conversations_collection.update_one(filter_query, update_operations, upsert=True)
            messages.append(message_id)
            if current_message_id==message_id :
                email_data = {
                    'user_email':email_address,
                    'conv_id':conversation_id,
                    'msg_id':message_id,
                    'received_datetime':received_time.strftime("%Y-%m-%dT%H:%M:%S%:z"),
                    'sender':sender,
                    'subject':subject,
                    'body':cleaned_body,
                    'attachments':attachments_data,
                    'email_provider':'gmail'

                }

                choices = ['importance_score', 'replies', 'summary_and_category']
                thread_id = conversation_id+"---"+message_id
                run_analysis_agent_stateful.delay(thread_id, email_data, choices)
    if current_message_id not in messages:
        process_outlook_mail(current_message_id, email_address)
    return True


def process_single_mail(BASE_ENDPOINT, email_address, conversation_id, headers, message, current_message_id):
    # db = get_async_db()
    # inbox_conversations_collection_async = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
    message_id = message.get('id')
    if inbox_conversations_collection.find_one({'conv_id': conversation_id, 'messages.message_id': message_id}):
        # print(f"Message with ID '{message_id}' already processed. Exiting.")
        return conversation_id, message_id
    msg_endpoint = f"{BASE_ENDPOINT}/messages/{message_id}?$select=uniqueBody"
    single_msg_resp = requests.get(msg_endpoint, headers=headers)
    single_msg_resp.raise_for_status()
    single_msg_data = single_msg_resp.json()

    conv_index = message.get('conversationIndex')
    number_of_child_replies = decode_conversation_index(message.get('conversationIndex')).get("number of replies", '')
    subject = message.get('subject')
    sender_info = message.get('sender', {}).get('emailAddress', {})
    sender = sender_info.get('address', 'N/A')
    receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in message.get('toRecipients', [])]
    receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in message.get('toRecipients', [])]
    cc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in message.get('ccRecipients', [])]
    bcc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in message.get('bccRecipients', [])]
    body_content = single_msg_data.get("uniqueBody", {})
    cleaned_body = get_unique_body_outlook(body_content)
    inline_attachments = get_inline_attachments_outlook(body_content)
    attachments_data = []
    if message.get('hasAttachments') or len(inline_attachments)>0:
        attachments_url = f"{BASE_ENDPOINT}/messages/{message_id}/attachments"
        try:
            attachments_resp = requests.get(attachments_url, headers=headers)
            attachments_resp.raise_for_status()
            fetched_attachments = attachments_resp.json().get('value', [])
                                            
            for attach in fetched_attachments:
                attachment_info = {
                        'id': attach.get('id'),
                        'name': attach.get('name'),
                        'contentType': attach.get('contentType'),
                        'size': attach.get('size'),
                        'isInline': attach.get('isInline', False),
                        'contentBytes': attach.get('contentBytes') 
                    }
                attachments_data.append(attachment_info)
        except requests.exceptions.RequestException as attach_e:
            print(f"  Error fetching attachments for message {message_id}: {attach_e}")
            if attach_e.response:
                print(f"  Attachment fetch error response: {attach_e.response.text}")
    received_time = convert_utc_str_to_local_datetime(message.get('receivedDateTime'))
    print(received_time)                               
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
            'full_body':message.get('body'),
            'received_datetime':received_time,
            'attachments':attachments_data,
            'type':'outlook_received_mail'
        }
    filter_query = {'conv_id': conversation_id, 'email_address':email_address}

    update_operations = {
            '$push': {'messages': message_doc},
            '$setOnInsert': {
                'conv_id': conversation_id,
                'email_address': email_address
            }
        }

    result = inbox_conversations_collection.update_one(filter_query, update_operations, upsert=True)

    if celery_app:
        if message_id==current_message_id or email_address!=sender:
            email_data = {
                'user_email':email_address,
                'conv_id':conversation_id,
                'msg_id':message_id,
                'received_datetime':received_time.strftime("%Y-%m-%dT%H:%M:%S%:z"),
                'sender':sender,
                'subject':subject,
                'body':cleaned_body,
                'attachments':attachments_data,
                'email_provider':'gmail'

            }

            choices = ['importance_score', 'replies', 'summary_and_category']
            thread_id = conversation_id+"---"+message_id
            run_analysis_agent_stateful.delay(thread_id, email_data, choices)
            # if len(attachments_data)>0:
            #     generate_attachment_summary.delay(conversation_id, message_id, email_address, 'outlook')
            # generate_previous_emails_summary.delay(conversation_id, message_id, email_address)
            # generate_importance_analysis.delay(conversation_id, message_id, email_address)
            # generate_summary_and_replies.delay(conversation_id, message_id, email_address)

    if result.upserted_id:
        print(f"Inserted new document with _id: {result.upserted_id}")
    else:
        print("Document updated")
    return conversation_id, message_id
