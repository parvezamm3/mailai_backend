# utils.py
import json
import base64
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

from utils.message_parsing import parse_email_body_and_attachments, extract_email_thread

# Import collections from the database module
from database import users_collection, inbox_messages_collection, draft_messages_collection, inbox_conversations_collection
# Import configurations from the config module

import utils.gemini_utils as gemini_utils
from config import Config
import workers.tasks as tasks
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
        last_history_id = creds_data.get('last_history_id')
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
            historyTypes=['messageAdded', 'messageDeleted', 'labelAdded', 'labelRemoved']
        ).execute()

        history = history_response.get('history', [])
        # print(f"Fetched {len(history)} history entries for {email_address} since {start_history_id}")

        processed_messages = []
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

                        headers = message['payload']['headers']
                        thread_id = message['threadId']
                        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'N/A')
                        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'N/A')
                        # Extract recipients (To, Cc, Bcc)
                        def get_recipients_from_header(header_name):
                            header_value = next((h['value'] for h in headers if h['name'] == header_name), '')
                            # Split by comma and clean up each email address
                            return [addr.strip() for addr in header_value.split(',') if addr.strip()]
                        receivers_list = get_recipients_from_header('To')
                        cc_list = get_recipients_from_header('Cc')
                        bcc_list = get_recipients_from_header('Bcc')
                        # --- Parse Body and Attachments using the new utility ---
                        # --- Parse Body and Attachments ---
                        plain_body_content = "No body available."
                        attachments_data = []

                        def process_parts(parts):
                            nonlocal plain_body_content # Allow modifying outer scope variable
                            extracted_attachments = []
                            for part in parts:
                                # Prioritize text/plain for main body
                                if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                                    try:
                                        plain_body_content = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                                    except Exception as e:
                                        print(f"Error decoding Gmail text/plain body part: {e}")
                                # Handle HTML body, convert to plain text
                                elif part.get('mimeType') == 'text/html' and part.get('body', {}).get('data'):
                                    try:
                                        html_content = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                                        soup = BeautifulSoup(html_content, 'html.parser')
                                        # Only update plain_body_content if it's currently empty or less descriptive
                                        if plain_body_content == "No body available." or len(soup.get_text()) > len(plain_body_content):
                                            plain_body_content = soup.get_text()
                                    except Exception as e:
                                        print(f"Error decoding Gmail text/html body part: {e}")

                                # Handle attachments
                                if part.get('filename') and part.get('filename') != '': # Ensure it's a file, not just a part
                                    attachment_info = {
                                        'id': part.get('partId'),
                                        'name': part['filename'],
                                        'contentType': part.get('mimeType'),
                                        'size': part.get('body', {}).get('size'),
                                        'isInline': 'Content-ID' in [h['name'] for h in part.get('headers', [])],
                                    }
                                    
                                    # Check if content is directly in body.data
                                    if part.get('body', {}).get('data'):
                                        try:
                                            # decoded_bytes = base64.urlsafe_b64decode(part['body']['data'])
                                            attachment_info['contentBytes'] = part['body']['data']
                                            # print(f"  Attachment '{part['filename']}' content embedded.")
                                        except Exception as e:
                                            print(f"Error decoding embedded attachment {part['filename']}: {e}")
                                    # If not in body.data, check for attachmentId and fetch separately
                                    elif part.get('body', {}).get('attachmentId'):
                                        try:
                                            # print(f" Fetching content for attachment '{part['filename']}' via separate API call...")
                                            attachment_content_response = gmail_service.users().messages().attachments().get(
                                                userId='me',
                                                messageId=message_id,
                                                id=part['body']['attachmentId']
                                            ).execute()
                                            if attachment_content_response.get('data'):
                                                # decoded_bytes = base64.urlsafe_b64decode(attachment_content_response['data'])
                                                # print(f"Debug: File size is {len(decoded_bytes)} bytes.")
                                                attachment_info['contentBytes'] = attachment_content_response['data']
                                                # print(f"  Successfully fetched content for attachment '{part['filename']}'.")
                                            else:
                                                print(f"  No data found in separate attachment fetch for '{part['filename']}'.")
                                        except HttpError as attach_error:
                                            print(f"  Error fetching separate attachment {part['filename']}: {attach_error}")
                                        except Exception as e:
                                            print(f"  Unexpected error fetching separate attachment {part['filename']}: {e}")
                                    else:
                                        print(f"  Attachment '{part['filename']}' has no embedded data or attachmentId. Skipping content.")

                                    extracted_attachments.append(attachment_info)

                                # Recursively process nested parts (e.g., multipart/mixed)
                                if part.get('parts'):
                                    extracted_attachments.extend(process_parts(part['parts']))
                            return extracted_attachments

                        if message['payload'].get('parts'):
                            attachments_data = process_parts(message['payload']['parts'])
                        elif message['payload'].get('body', {}).get('data'): # Simple case, no parts
                            # This handles cases where the entire message is a single text/plain or text/html part
                            plain_body_content_temp, _ = parse_email_body_and_attachments(
                                message, 'gmail_message_added'
                            )
                            plain_body_content = plain_body_content_temp
                        # Separate current message from history
                        current_message_body, previous_history_body = extract_email_thread(plain_body_content)
                        # print(current_message_body, previous_history_body)
                        # snippet = message.get('snippet', 'No snippet available.')
                        message_doc = {
                            'message_id': message_id,
                            'history_id': entry['id'],
                            'subject': subject,
                            'sender': sender,
                            'receivers': receivers_list,
                            'cc': cc_list,
                            'bcc': bcc_list,
                            'body': current_message_body,
                            # 'history':previous_history_body,
                            'received_time': message.get('internalDate', 'N/A'),
                            'attachments': attachments_data,
                            'type':'gmail_received_mail',
                            'full_message_payload': message,
                        }
                        # message_doc = {
                        #     'message_id': message_id,
                        #     'history_id': entry['id'],
                        #     'email_address': email_address,
                        #     'sender': sender,
                        #     'subject': subject,
                        #     'snippet': snippet,
                        #     'received_at': message.get('internalDate', 'N/A'),
                        #     'full_message_payload': message # Save full payload for detailed analysis if needed
                        # }
                        
                        labels = message.get('labelIds', [])
                        # print(labels)
                        if 'INBOX' in labels and 'UNREAD' in labels:
                            # Extract sender, subject, and snippet
                            # message_doc['type'] = 'gmail_received_mail'
                            # Save to database
                            # inbox_messages_collection.update_one(
                            #     {'message_id': message_id, 'email_address': email_address},
                            #     {'$set': message_doc},
                            #     upsert=True
                            # )
                            filter_query = {'conv_id': thread_id, 'email_address':email_address}

                            update_operations = {
                                '$push': {'messages': message_doc},
                                '$setOnInsert': {
                                    'conv_id': thread_id,
                                    'email_address': email_address
                                }
                            }

                            result = inbox_conversations_collection.update_one(filter_query, update_operations, upsert=True)

                            # print(f"Matched {result.matched_count} document(s).")
                            # print(f"Modified {result.modified_count} document(s).")
                            if result.upserted_id:
                                print(f"Inserted new document with _id: {result.upserted_id}")
                            else:
                                print(f"Document was updated.")
                            print(f"  Processed and saved Gmail message: ID={message_id}, From='{sender}', Subject='{subject}'")
                            processed_messages.append(message_doc)

                            # --- Dispatch Celery tasks asynchronously ---
                            # if celery_app: # Ensure celery_app is initialized globally in app.py and passed to utils
                            #     import workers.tasks as tasks
                            #     print(message_id, email_address)
                            #     if len(attachments_data)>0:
                            #         tasks.generate_attachment_summary.delay(thread_id, message_id, email_address, 'gmail')
                            #     tasks.generate_previous_emails_summary.delay(thread_id, message_id, email_address,)
                            #     tasks.generate_importance_analysis.delay(thread_id, message_id, email_address,)
                            #     tasks.generate_summary_and_replies.delay(thread_id, message_id, email_address,)
                            #     print(f"  Dispatched Celery tasks for Gmail message {message_id}")
                            # else:
                            #     print("  Celery app not initialized. Tasks not dispatched.")
                       
                    except HttpError as msg_error:
                        if msg_error.resp.status == 404:
                            pass
                            # print(f"  Warning: Message {message_id} not found (404). It may have been deleted or moved. Skipping.")
                        else:
                            print(f"  Error fetching message {message_id}: {msg_error}")
                    except Exception as e:
                        print(f"  Unexpected error processing message {message_id}: {e}")
        
        return processed_messages, history_response.get('historyId', start_history_id)
    except HttpError as error:
        print(f"Error fetching Gmail history for {email_address}: {error}")
        return [], start_history_id
    except Exception as e:
        print(f"An unexpected error occurred while fetching history: {e}")
        return [], start_history_id



    # """
    # Fetches ALL messages from INBOX and SENT labels and saves them to the database.
    # This is for a full historical sync.
    # """
    # try:
    #     print(f"Starting full mail sync for {email_address}...")
    #     gmail_service = build('gmail', 'v1', credentials=credentials)
    #     page_token = None
    #     while True:
    #         response = gmail_service.users().messages().list(
    #             userId='me',
    #             labelIds=['INBOX', 'SENT'],
    #             pageToken=page_token
    #         ).execute()

    #         messages = response.get('messages', [])
    #         if not messages:
    #             print("No more messages to sync.")
    #             break

    #         for msg in messages:
    #             try:
    #                 message = gmail_service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
                    
    #                 headers = message['payload']['headers']
    #                 subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'N/A')
    #                 snippet = message.get('snippet', 'No snippet available.')
                    
    #                 label_ids = message.get('labelIds', [])
    #                 sender = next((h['value'] for h in headers if h['name'] == 'From'), 'N/A')
    #                 if label_ids=="INBOX":
    #                     message_type = 'gmail_received_mail'

    #                     message_doc = {
    #                         'message_id': message['id'],
    #                         'email_address': email_address,
    #                         'sender': sender,
    #                         'subject': subject,
    #                         'snippet': snippet,
    #                         'received_at': message.get('internalDate', 'N/A'),
    #                         'full_message_payload': message,
    #                         'type': message_type
    #                     }

    #                     inbox_messages_collection.update_one(
    #                         {'message_id': message['id'], 'email_address': email_address},
    #                         {'$set': message_doc},
    #                         upsert=True
    #                     )

    #                 if label_ids=="SENT":
    #                     message_type = 'gmail_received_mail'
    #                     receiver = next((h['value'] for h in headers if h['name'] == 'To'), 'N/A')

    #                     message_doc = {
    #                         'message_id': message['id'],
    #                         'sender': email_address,
    #                         'receiver':receiver,
    #                         'subject': subject,
    #                         'contents': snippet,
    #                         'drafts_at': message.get('internalDate', 'N/A'),
    #                     }

    #                     draft_messages_collection.update_one(
    #                         {'message_id': message['id']},
    #                         {'$set': message_doc},
    #                         upsert=True
    #                     )
    #                 # print(f"  Synced message: ID={message['id']}, From='{sender}', Subject='{subject}'")

    #             except HttpError as msg_error:
    #                 if msg_error.resp.status == 404:
    #                     print(f"  Warning: Message {msg['id']} not found (404). Skipping.")
    #                 else:
    #                     print(f"  Error fetching message {msg['id']}: {msg_error}")
    #             except Exception as e:
    #                 print(f"  Unexpected error processing message {msg['id']}: {e}")

    #         page_token = response.get('nextPageToken')
    #         if not page_token:
    #             break
        
    #     print(f"Full mail sync for {email_address} complete.")
    
    # except HttpError as error:
    #     print(f"Error fetching messages during full sync: {error}")
    # except Exception as e:
    #     print(f"An unexpected error occurred during full sync: {e}")