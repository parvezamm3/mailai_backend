# utils.py
import os
import json
import re
import base64
import pytz
from datetime import datetime
from bs4 import BeautifulSoup, NavigableString
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

from utils.common_utils import conduct_analysis
from utils.message_parsing import parse_email_body_and_attachments, extract_email_thread
from utils.transform_utils import convert_to_local_time
# Import collections from the database module
from database import users_collection, inbox_messages_collection, draft_messages_collection, inbox_conversations_collection
# Import configurations from the config module
from workers.tasks import (
    generate_attachment_summary, generate_previous_emails_summary_gmail, 
    generate_importance_analysis, generate_summary_and_replies,
    run_analysis_agent_stateful
)
import utils.gemini_utils as gemini_utils
from config import Config
import workers.tasks as tasks
# from utils.llm_agent import run_analysis_agent_stateful
# celery_app will be set dynamically from app.py
celery_app = None

def save_google_credentials(user_id, credentials, last_history_id=None):
    """Saves user's Google API credentials to MongoDB."""
    update_data = {
        'access_token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes,
        'expires_at': credentials.expiry.isoformat() if credentials.expiry else None
    }
    if last_history_id:
        update_data['last_history_id'] = last_history_id

    users_collection.update_one(
        {'user_id': user_id},
        {'$set': {'credentials': update_data}},
        upsert=True
    )
    print(f"Credentials saved/updated for user: {user_id}. History ID: {last_history_id}")

def load_google_credentials(user_id):
    """Loads user's Google API credentials from MongoDB."""
    print("Load Google Credentials")
    user_data = users_collection.find_one({'user_id': user_id})
    if user_data and 'credentials' in user_data:
        creds_data = user_data['credentials']
        creds = Credentials(
            token=creds_data.get('access_token'),
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=creds_data.get('scopes')
        )
        last_history_id = user_data.get('last_history_id')
        # Refresh token if expired
        if creds.expired and creds.refresh_token:
            print(f"Refreshing Google token for user: {user_id}")
            try:
                creds.refresh(Request())
                save_google_credentials(user_id, creds, last_history_id) # Save updated credentials
            except Exception as refresh_error:
                print(f"Error refreshing token for {user_id}: {refresh_error}")
                return None, None # Return None if refresh fails
        return creds, last_history_id
    return None, None

def setup_gmail_watch(credentials, email_address):
    """Sets up a Gmail API watch request for the given email address."""
    try:
        gmail_service = build('gmail', 'v1', credentials=credentials)
        request_body = {
            'topicName': Config.GMAIL_PUB_SUB_TOPIC,
            'labelIds': ['INBOX'] # Watch for changes in the INBOX
        }
        print(f"Attempting to set up Gmail watch for {email_address} with topic: {Config.GMAIL_PUB_SUB_TOPIC}")
        watch_response = gmail_service.users().watch(userId='me', body=request_body).execute()

        initial_history_id = watch_response.get('historyId')
        # Save the initial history ID with the user's credentials
        save_google_credentials(email_address, credentials, initial_history_id)

        print(f"Gmail watch setup successful for {email_address}: {watch_response}")
        return True
    except HttpError as error:
        print(f"Error setting up Gmail watch for {email_address}: {error}")
        if error.resp.status == 403 and 'pubsub' in str(error):
            print("Please ensure the Pub/Sub service account has 'Pub/Sub Publisher' role on your topic.")
        return False
    except Exception as e:
        print(f"An unexpected error occurred while setting up watch: {e}")
        return False

def fetch_gmail_history(credentials, email_address, start_history_id):
    """Fetches new Gmail history (messages/changes) since a given history ID."""
    try:
        gmail_service = build('gmail', 'v1', credentials=credentials)
        history_response = gmail_service.users().history().list(
            userId='me',
            startHistoryId=start_history_id,
            historyTypes=['messageAdded']
        ).execute()

        history = history_response.get('history', [])
        # print(f"Fetched {len(history)} history entries for {email_address} since {start_history_id}")

        # processed_messages = []
        for entry in history:
            messages_to_process = entry.get('messagesAdded', [])
            if messages_to_process:
                # print(f"Processing {len(messages_to_process)} messagesAdded in history entry {entry.get('id')}.")
                for msg_info in messages_to_process:
                    message_id = msg_info.get('message', {}).get('id') or msg_info.get('id')
                    if not message_id:
                        print(f"Warning: Could not extract message ID from msg_info: {msg_info}. Skipping.")
                        continue
                    try:
                        # Fetch the full message details (full format includes payload/headers/body)
                        message = gmail_service.users().messages().get(userId='me', id=message_id, format='full').execute()
                        labels = message.get('labelIds', [])
                        if 'INBOX' in labels and 'UNREAD' in labels:
                            result, thread_id, msg_doc = save_single_mail(gmail_service, message, email_address)

                            if result.upserted_id:
                                print(f"Inserted new document with _id: {result.upserted_id}")
                            else:
                                print(f"Document was updated.")
                            # print(f"  Processed and saved Gmail message: ID={message_id}, From='{sender}', Subject='{subject}'")
                            # processed_messages.append(message_doc)

                            # --- Dispatch Celery tasks asynchronously ---
                            if celery_app: # Ensure celery_app is initialized globally in app.py and passed to utils
                                # import workers.tasks as tasks
                                # print(thread_id, message_id, email_address)
                                
                                email_data = {
                                     'user_email':email_address,
                                     'conv_id':thread_id,
                                     'msg_id':message_id,
                                     'received_datetime':msg_doc.get('received_datetime', datetime.now()).strftime("%Y-%m-%dT%H:%M:%S%:z"),
                                     'sender':msg_doc.get('sender'),
                                     'subject':msg_doc.get('subject'),
                                     'body':msg_doc.get('body'),
                                     'attachments':msg_doc.get('attachments'),
                                     'email_provider':'gmail'

                                }

                                choices = ['importance_score', 'replies', 'summary_and_category']
                                thread_id = thread_id+"---"+message_id
                                run_analysis_agent_stateful.delay(thread_id, email_data, choices)
                                # if len(attachments)>0:
                                #     generate_attachment_summary.delay(thread_id, message_id, email_address, 'gmail')
                                # generate_previous_emails_summary_gmail.delay(thread_id, message_id, email_address,)
                                # generate_importance_analysis.delay(thread_id, message_id, email_address,)
                                # generate_summary_and_replies.delay(thread_id, message_id, email_address,)
                                print(f"  Dispatched Celery tasks for Gmail message {message_id}")
                            else:
                                print("  Celery app not initialized. Tasks not dispatched.")
                        # print("This message is not from inbox")
                        pass
                    except HttpError as msg_error:
                        if msg_error.resp.status == 404:
                            pass
                        else:
                            print(f"  Error fetching message {message_id}: {msg_error}")
                    except Exception as e:
                        print(f"  Unexpected error processing message {message_id}: {e}")
        
        return history_response.get('historyId', start_history_id)
    except HttpError as error:
        print(f"Error fetching Gmail history for {email_address}: {error}")
        return [], start_history_id
    except Exception as e:
        print(f"An unexpected error occurred while fetching history: {e}")
        return [], start_history_id
    

def parse_message_parts(parts, attachments, gmail_service, message_id):
    """
    Recursively parses message parts to extract body content and attachments.
    """
    pattern = r"\n+On\s+[A-Za-z]{3},\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}.*$"
    main_body = ''
    history_body = ''
    html_body = ''  
    for part in parts:
        mime_type = part.get('mimeType')
        if mime_type == 'text/plain':
            plain_text = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
            match = re.search(pattern, plain_text, flags=re.MULTILINE)

            if match:
                start_index = match.start()
                
                main_body = plain_text[:start_index]
                history_body = plain_text[start_index:]
                history_body = history_body.replace('>', '')
                history_body = os.linesep.join([s.strip() for s in history_body.splitlines() if s])
                
        if mime_type == 'text/html':
            new_content, prev_content, html_body = get_text_from_soup(part)
            if not main_body:
                main_body = new_content
                history_body = prev_content

        # Recursive step: It's a container, so parse its parts
        elif mime_type and mime_type.startswith('multipart/'):
            main_body, history_body, html_body, attachments = parse_message_parts(
                part.get('parts', []), attachments, gmail_service, message_id
            )
        
        elif part.get('filename') and part.get('filename') != '':
            if part.get('filename') in main_body or part.get('filename') not in history_body:
                # print(part)
                attachment_info = {
                    'id' : part.get('body', {}).get('attachmentId'),
                    'name': part['filename'],
                    'contentType': mime_type,
                    'size': part.get('body', {}).get('size'),
                    'isInline': 'Content-ID' in [h['name'] for h in part.get('headers', [])],
                }
                if part.get('body', {}).get('data'):
                    try:
                        attachment_info['contentBytes'] = part['body']['data']
                    except Exception as e:
                        print(f"Error decoding embedded attachment: {e}")
                elif part.get('body', {}).get('attachmentId'):
                    try:
                        attachment_content_response = gmail_service.users().messages().attachments().get(
                            userId='me',
                            messageId=message_id,
                            id=part['body']['attachmentId']
                        ).execute()
                        if attachment_content_response.get('data'):
                            attachment_info['contentBytes'] = attachment_content_response['data']
                    except HttpError as attach_error:
                        print(f"Error fetching separate attachment: {attach_error}")
                    except Exception as e:
                        print(f"Unexpected error fetching separate attachment: {e}")
                # print(attachment_info.keys())
                attachments.append(attachment_info)
        
    return main_body, history_body, html_body, attachments

def get_text_from_soup(part):
    html_content = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
    soup = BeautifulSoup(html_content, 'html.parser')
    
    for img_tag in soup.find_all('img', alt=True):
        alt_text = img_tag['alt']
        img_tag.replace_with('[image: '+alt_text+']')

    for br_tag in soup.find_all('br'):
        br_tag.replace_with('\n')
    
    first_quote_div = soup.find('div', class_='gmail_quote gmail_quote_container')
    new_content = ""
    previous_content = ""

    if first_quote_div:
        new_content = ""
        for element in first_quote_div.previous_siblings:
            if isinstance(element, NavigableString):
                new_content = str(element) + new_content
            else:
                new_content = element.get_text().strip() + new_content
        previous_content = first_quote_div.get_text().strip()

    else:
        full_text = soup.get_text(separator=' ')
        new_content = re.sub(r'\s+', ' ', full_text).strip()
        previous_content = ""
    return new_content, previous_content, html_content


def prepare_conversation_thread(email_address, thread_id, current_message_id):
    credentials, last_history_id = load_google_credentials(email_address)
    
    gmail_service = build('gmail', 'v1', credentials=credentials)
    try:
        thread = gmail_service.users().threads().get(
            userId='me',
            id=thread_id
        ).execute()
        print('Extraction completed')
        # print(thread)
        messages = thread.get('messages', [])
        # print(messages)
        # user_data = users_collection.find_one({'user_id': email_address})
        for msg in messages:
            msg_id = msg.get('id')
            if msg_id and 'TRASH' not in msg.get('labelIds', []):
                message = gmail_service.users().messages().get(userId='me', id=msg.get('id'), format='full').execute()
                # print(message)
                result, thread_id, msg_doc = save_single_mail(gmail_service, message, email_address)
                if msg_id == current_message_id:
                    conduct_analysis(email_address, thread_id, msg_doc)
            else:
                print("Skipping a malformed message object without an ID.")
        return True
    except Exception as e:
        print(f"Error occured during preparing thread for gmail {e}")
        return False
    
def save_single_mail(gmail_service, message, email_address):
    message_id = message['id']
    payload = message.get('payload', {})
    headers = payload.get('headers', [])
    thread_id = message['threadId']
    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'N/A')
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'N/A')
    def get_recipients_from_header(header_name):
        header_value = next((h['value'] for h in headers if h['name'] == header_name), '')
        # Split by comma and clean up each email address
        return [addr.strip() for addr in header_value.split(',') if addr.strip()]
    receivers_list = get_recipients_from_header('To')
    cc_list = get_recipients_from_header('Cc')
    bcc_list = get_recipients_from_header('Bcc')
    internal_date_ms = message.get('internalDate', 'N/A')
    # Convert milliseconds to seconds
    internal_date_seconds = int(internal_date_ms) / 1000

    # Create a naive datetime object from the timestamp
    naive_dt = datetime.fromtimestamp(internal_date_seconds)
    localize_dt = convert_to_local_time(naive_dt)

    main_conv = ""
    prev_conv = ""
    html_conv = ""
    attachments = []

    # Start recursive parsing
    main_conv, prev_conv, html_conv, attachments = parse_message_parts(
        payload.get('parts', []), attachments, gmail_service, message_id
    )
    
    message_doc = {
        'message_id': message_id,
        'subject': subject,
        'sender': sender,
        'receivers': receivers_list,
        'cc': cc_list,
        'bcc': bcc_list,
        'body': main_conv,
        'previous_messages':prev_conv,
        'received_datetime':localize_dt,
        'attachments': attachments,
        'type':'gmail_received_mail',
        'provider':'gmail',
        'full_message_payload': html_conv,
    }

    filter_query = {'conv_id': thread_id, 'email_address':email_address}

    update_operations = {
        '$addToSet': {'messages': message_doc},
        '$setOnInsert': {
            'conv_id': thread_id,
            'email_address': email_address
        }
    }

    result = inbox_conversations_collection.update_one(filter_query, update_operations, upsert=True)
    if result.upserted_id:
        print(f"Inserted new document with _id: {result.upserted_id}")
    else:
        print(f"Document was updated.")
    return result, thread_id, message_doc

# def conduct_analysis(email_address, thread_id, msg_doc):
#     print(f"Conducting analysis for {msg_doc.get('message_id')} subject {msg_doc.get('subject')}")
#     if celery_app:
#         email_data = {
#             'user_email':email_address,
#             'conv_id':thread_id,
#             'msg_id':msg_doc.get('message_id'),
#             'received_datetime':msg_doc.get('received_datetime', datetime.now()).strftime("%Y-%m-%dT%H:%M:%S%:z"),
#             'sender':msg_doc.get('sender'),
#             'subject':msg_doc.get('subject'),
#             'body':msg_doc.get('body'),
#             'attachments':msg_doc.get('attachments'),
#             'email_provider':'gmail'
#         }
#         choices = ['importance_score', 'replies', 'summary_and_category']
#         thread_id = thread_id+"---"+msg_doc.get('message_id', '')
#         run_analysis_agent_stateful.delay(thread_id, email_data, choices)