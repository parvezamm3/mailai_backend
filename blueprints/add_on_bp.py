import json
import requests
import time
import asyncio
from bs4 import BeautifulSoup
from flask import Blueprint, request, jsonify, send_file
from database import preferences_collection, users_collection, inbox_messages_collection, draft_messages_collection, inbox_conversations_collection
# from database_async import get_async_db
from utils.outlook_utils import (
    load_outlook_credentials, send_outlook_reply_graph, 
    get_application_access_token, get_outlook_access_token,
    prepare_conversation_thread as prepare_conversation_thread_outlook, process_outlook_mail,
    get_base_endpoint, get_url_headers
)
from utils.common_utils import conduct_analysis
from utils.transform_utils import decode_conversation_index, convert_utc_str_to_local_datetime
from utils.message_parsing import get_unique_body_outlook, get_inline_attachments_outlook
from utils.gmail_utils import (
    load_google_credentials, 
    prepare_conversation_thread as prepare_conversation_thread_gmail)
from utils.gemini_utils import call_gemini_api_structured_output
from workers.tasks import (
    generate_attachment_summary, generate_previous_emails_summary, generate_importance_analysis, 
    generate_summary_and_replies)
from config import Config
from pprint import pprint


add_on_bp = Blueprint('add_on_bp', __name__)


@add_on_bp.route('/dashboard_data', methods=['POST'])
def get_dashboard_data():
    """
    Provides initial analysis and user preferences based on email content.
    Now supports both Gmail and Outlook message details.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    # db = get_async_db()
    # users_collection_async = db[Config.MONGO_USERS_COLLECTION]
    # inbox_conversations_collection_async = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
    # preferences_collection_async = db[Config.MONGO_PREFERENCES_COLLECTION]
    data = request.get_json()
    user_id = data.get('user_id')
    owner_email = data.get('ownerEmail')
    sender = data.get('sender')
    message_id = data.get('message_id').replace('/', '-').replace('+', '_') # For Outlook messages
    conv_id = data.get('conv_id').replace('/', '-').replace('+', '_')
    provider = data.get('provider', '')
    print(message_id[:20], provider)
    if not user_id:
        print("No User id")
        return jsonify({"status": "error", "message": "ユーザーIDが必要です。"}), 401
    if sender == owner_email:
        return jsonify({"status": "error", "message": "ユーザーはメールの送信者です。分析は処理されません。"}), 400
    
    user_data = users_collection.find_one({'user_id': user_id})
    if not user_data:
        print("No User Dtata")
        return jsonify({"status": "error", "message": "このメールアドレス {user_id} を持つユーザーは存在しません。まず承認してください。"}), 401
    print("Preliminary check succeded")
    # --- 1. Get or Create User Preferences from MongoDB ---
    user_pref_doc = preferences_collection.find_one({'user_id': user_id})
    if not user_pref_doc:
        # print(f"Creating new preferences for user: {user_id} in MongoDB")
        default_prefs = {
            'user_id': user_id,
            'enable_importance': True,
            'enable_generation': True
        }
        preferences_collection.insert_one(default_prefs)
        user_pref_doc = default_prefs # Use the newly created defaults
    
    preferences = {
        'enable_importance': user_pref_doc.get('enable_importance', False),
        'enable_generation': user_pref_doc.get('enable_generation', False)
    }
    # 3. Determine if the conversation or message needs to be prepared
    message_doc_exists = inbox_conversations_collection.count_documents(
        {'conv_id': conv_id, 'messages.message_id': message_id}, limit=1
    )
    print(f"Message Exists {message_doc_exists}")
    # If the message isn't in the DB, initiate the appropriate analysis task.
    # This logic is now outside the loop to prevent re-triggering.
    if not message_doc_exists:
        # First, try to prepare the full conversation thread
        if provider=="outlook":
            print(f"Provider {provider}")
            prepare_conversation_thread_outlook(owner_email, conv_id, message_id)
        if provider == "gmail":
            print(f"Provider {provider}")
            prepare_conversation_thread_gmail(owner_email, conv_id, message_id)

        
        # After attempting to get the thread, check if the specific message now exists.
        # If not, fall back to processing the individual message.
        # message_doc_exists_after_thread = inbox_conversations_collection.count_documents(
        #     {'conv_id': conv_id, 'messages.message_id': message_id}, limit=1
        # )
        # if not message_doc_exists_after_thread:
        #     conv_id, message_id = process_outlook_mail(message_id, owner_email)



    # 4. Asynchronous Polling for Analysis Result
    max_retries = 25
    # print('Refresshidfjfvfjdjkdfjk')
    generating_analysis = False
    for _ in range(max_retries):
        current_message_doc = inbox_conversations_collection.find_one(
            {'conv_id': conv_id, 'messages.message_id': message_id},
            {'_id': 0, 'messages.$': 1}
        )
        # print(current_message_doc)
        analysis_data = {}
        
        if current_message_doc:
            current_message = current_message_doc['messages'][0]
            analysis_data = current_message.get('analysis', {})
            if not analysis_data and not generating_analysis :
                # if provider == "gmail":
                #     msg_doc = {
                #         'message_id':current_message.get('message_id'),
                #         'received_datetime':current_message.get('received_datetime'),
                #         'sender':current_message.get('sender'),
                #         'subject':current_message.get('subject'),
                #         'body':current_message.get('body'),
                #         'attachments':current_message.get('attachments'),
                #         'provider':current_message.get('provider')
                #     }
                #     conduct_analysis_gmail(user_id, conv_id, msg_doc)
                # if provider == "outlook":
                msg_doc = {
                    'message_id':current_message.get('message_id'),
                    'received_datetime':current_message.get('received_datetime'),
                    'sender':current_message.get('sender'),
                    'subject':current_message.get('subject'),
                    'body':current_message.get('body'),
                    'attachments':current_message.get('attachments'),
                    'provider':current_message.get('provider')
                }
                conduct_analysis(user_id, conv_id, msg_doc)
                generating_analysis = True
            else:
                print("Else clause")
                if analysis_data.get('completed'):
                    generating_analysis = False
                    return jsonify({
                        "status": "success",
                        "is_spam": analysis_data.get('is_spam', False),
                        "is_malicious": analysis_data.get('is_malicious', False),
                        "analysis_result": f"重要度スコア: {analysis_data.get('importance_score', 'N/A')} \n 説明: {analysis_data.get('importance_description', 'Loading...')}",
                        "preferences": preferences,
                        'summary': analysis_data.get('summary', ''),
                        'category': analysis_data.get('category', ''),
                        'replies': analysis_data.get('replies', [])
                    })
                
            # else:
            #     if provider == "gmail" and generation_required==True:
            #         print(type(current_message.get('received_datetime')))
            #         msg_doc = {
            #             'message_id':current_message.get('message_id'),
            #             'received_datetime':current_message.get('received_datetime'),
            #             'sender':current_message.get('sender'),
            #             'subject':current_message.get('subject'),
            #             'body':current_message.get('body'),
            #             'attachments':current_message.get('attachments'),
            #         }
            #         conduct_analysis_gmail(user_id, conv_id, msg_doc)
        
        # Asynchronously wait before polling again
        time.sleep(1)

    # If the loop finishes without finding completed analysis
    return jsonify({"status": "error", "message": "問題が発生したか、処理に時間がかかっています。結果を表示するには画面をリフレッシュしてください。"}), 400
    

    # is_spam = False
    # is_malicious = False
    # analysis_result = "Analysis loading..."
    # current_message_doc = await inbox_conversations_collection_async.find_one(
    #     {'conv_id': conv_id, 'messages.message_id': message_id},
    #     {'_id': 0, 'messages.$': 1}
    # )
    # # print(current_message_doc)
    # current_message = {}
    # analysis_data = {}
    # if current_message_doc:
    #     current_message = current_message_doc['messages'][0]
    #     analysis_data = current_message.get('analysis', {})
    # else:
    #     await prepare_conversation_thread(owner_email, conv_id, message_id)
    #     current_message_doc = await inbox_conversations_collection_async.find_one(
    #         {'conv_id': conv_id, 'messages.message_id': message_id},
    #         {'_id': 0, 'messages.$': 1}
    #     )
    #     if current_message_doc:
    #         current_message = current_message_doc['messages'][0]
    #         analysis_data = current_message.get('analysis', {})
    #     else:
    #         conv_id, message_id = await process_outlook_mail(message_id, owner_email)
    #         current_message_doc = await inbox_conversations_collection_async.find_one(
    #             {'conv_id': conv_id, 'messages.message_id': message_id},
    #             {'_id': 0, 'messages.$': 1}
    #         )
    #         if current_message_doc:
    #             current_message = current_message_doc['messages'][0]
    #             analysis_data = current_message.get('analysis', {})

    # if analysis_data:
    #     is_spam = analysis_data.get('is_spam', False)
    #     is_malicious = analysis_data.get('is_malicious', False)
    #     importance_score = analysis_data.get('importance_score', '')
    #     importance_description = analysis_data.get('importance_description', "")
    #     category = analysis_data.get('category', '')
    #     summary = analysis_data.get('summary', '')
    #     replies = analysis_data.get('replies', [])
    #     # print(replies, category)
        
    #     analysis_result = f"重要度スコア: {importance_score or 'N/A'} \n 説明: {importance_description or 'Loading...'}"

    #     return jsonify({
    #         "status": "success",
    #         "is_spam":is_spam,
    #         "is_malicious":is_malicious,
    #         "analysis_result": analysis_result,
    #         "preferences": preferences,
    #         'summary':summary,
    #         'category':category,
    #         'replies':replies
    #     })
    # return jsonify({"status": "error", "message": "問題が発生したか、処理に時間がかかっています。結果を表示するには画面をリフレッシュしてください。"}), 400

    # # print("Message Loaded")
    # generating_analsysis = False
    
    # count = 0
    # # print("Category :", analysis_data.get('category'))
    # # print(analysis_data and not analysis_data.get('category'))
    # while not analysis_data or not analysis_data.get('completed'):
    #     if not generating_analsysis:
    #         prepare_conversation_thread(owner_email, conv_id, message_id)
    #         generating_analsysis = True      
        
    #     current_message_doc = inbox_conversations_collection.find_one(
    #         {'conv_id': conv_id, 'messages.message_id': message_id},
    #         {'_id': 0, 'messages.$': 1}
    #     )
    #     if current_message_doc:
    #         current_message = current_message_doc['messages'][0]
    #         analysis_data = current_message.get('analysis', {})
    #     else:
    #         conv_id, message_id = process_outlook_mail(message_id, owner_email)
    #     if sender==owner_email:
    #         return jsonify({"status": "error", "message": "ユーザーはメールの送信者です。分析は処理されません。"}), 400
    #     count+=1
    #     # print(analysis_data)
    #     # print(count)
    #     if count>25:
    #         break
    #     time.sleep(1)
        
    # if analysis_data:
    #     is_spam = analysis_data.get('is_spam', False)
    #     is_malicious = analysis_data.get('is_malicious', False)
    #     importance_score = analysis_data.get('importance_score', '')
    #     importance_description = analysis_data.get('importance_description', "")
    #     category = analysis_data.get('category', '')
    #     summary = analysis_data.get('summary', '')
    #     replies = analysis_data.get('replies', [])
    #     # print(replies, category)
        
    #     analysis_result = f"重要度スコア: {importance_score or 'N/A'} \n 説明: {importance_description or 'Loading...'}"

    #     return jsonify({
    #         "status": "success",
    #         "is_spam":is_spam,
    #         "is_malicious":is_malicious,
    #         "analysis_result": analysis_result,
    #         "preferences": preferences,
    #         'summary':summary,
    #         'category':category,
    #         'replies':replies
    #     })
    # return jsonify({"status": "error", "message": "問題が発生したか、処理に時間がかかっています。結果を表示するには画面をリフレッシュしてください。"}), 400


# @add_on_bp.route('/generate_analysis_outlook', methods=['POST'])
# def generate_analysis_outlook():
#     if not request.is_json:
#         return jsonify({"status": "error", "message": "Request must be JSON"}), 400

#     data = request.get_json()
#     owner_email = data.get('ownerEmail')
#     message_id = data.get('message_id').replace('/', '-').replace('+', '_') # For Outlook messages
#     result = process_outlook_mail(message_id, owner_email)
#     analysis_data = result.get('messages')[0]
#     is_spam = analysis_data.get('is_spam', False)
#     is_malicious = analysis_data.get('is_malicious', False)
#     importance_score = analysis_data.get('importance_score', '')
#     importance_description = analysis_data.get('importance_description', "")
#     analysis_result = f"重要度スコア: {importance_score or 'N/A'} \n 説明: {importance_description or 'Loading...'}"
#     return jsonify({
#         "status": "success",
#         "is_spam":is_spam,
#         "is_malicious":is_malicious,
#         "analysis_result": analysis_result
#     })

@add_on_bp.route('/save_preferences', methods=['POST'])
def save_preferences():
    """Saves user preferences to MongoDB."""
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    # print(data)
    user_id = data.get('user_id')
    enable_importance = data.get('enable_importance')
    enable_generation = data.get('enable_generation')

    if not user_id:
        return jsonify({"status": "error", "message": "User ID is required"}), 400

    update_data = {
        'enable_importance': enable_importance,
        'enable_generation': enable_generation
    }

    preferences_collection.update_one(
        {'user_id': user_id},
        {'$set': update_data},
        upsert=True
    )
    print(f"Preferences saved for user {user_id}: {update_data}")

    return jsonify({
        "status": "success",
        "message": "Preferences updated",
        "preferences": update_data
    })


@add_on_bp.route('/suggest_reply', methods=['POST'])
def suggest_reply():
    """
    Receives email content and returns 2-3 suggested replies.
    Now supports both Gmail and Outlook contexts.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    user_id = data.get('user_id')
    message_id = data.get('message_id').replace('/', '-').replace('+', '_') # For Outlook messages
    conv_id = data.get('conv_id').replace('/', '-').replace('+', '_') # From Outlook context


    if not user_id:
        return jsonify({"status": "error", "message": "User ID is required"}), 400

    # print(f"Message id : {message_id} User Id:{user_id}")
    # message_doc = inbox_messages_collection.find_one({'message_id': message_id, 'email_address': user_id})
    # print(message_doc)
    current_message_doc = inbox_conversations_collection.find_one(
        {'conv_id': conv_id, 'messages.message_id': message_id},
        {'_id': 0, 'messages.$': 1}
    )
    # print(current_message_doc)
    current_message = {}
    if current_message_doc:
        current_message = current_message_doc['messages'][0]
    category = ''
    suggested_replies = []
    if current_message and 'analysis' in current_message:
        replies_from_db = current_message.get('analysis').get('replies', [])
        if replies_from_db:
            suggested_replies = replies_from_db
            category = current_message.get('analysis').get('category')
            # print(f"Fetched replies from DB for {message_id}.")
        else:
            # print(f"Replies not yet generated for {message_id}. Attempting fallback dummy replies.")
            # Fallback if replies aren't in DB yet (tasks still running)
            suggested_replies = [
                f"データベースに生成された返信候補がありません",
            ]
    else:
        # print(f"Message {message_id} not found in DB or analysis missing. Generating dummy replies.")
        # Fallback if message not found or no analysis field
        suggested_replies = [
            f"このメールの分析データはデータベースに存在しません"
        ]
    
    return jsonify({
        "status": "success",
        "suggested_replies": suggested_replies,
        "category":category
    })

@add_on_bp.route('/send_outlook_reply', methods=['POST'])
def send_outlook_reply():
    """
    Receives selected reply and sends it via Microsoft Graph API.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Request must be JSON"}), 400

    data = request.get_json()
    user_id = data.get('user_id')
    original_message_id = data.get('original_message_id')
    reply_body = data.get('reply_body')

    if not all([user_id, original_message_id, reply_body]):
        return jsonify({"status": "error", "message": "Missing required fields for sending reply"}), 400

    outlook_access_token = load_outlook_credentials(user_id)
    if not outlook_access_token:
        return jsonify({"status": "error", "message": "Outlook user not authorized or token expired."}), 401

    if send_outlook_reply_graph(outlook_access_token, original_message_id, reply_body):
        return jsonify({"status": "success", "message": "Reply sent via Outlook Graph API."})
    else:
        return jsonify({"status": "error", "message": "Failed to send reply via Outlook Graph API. Check backend logs."}), 500


@add_on_bp.route('/emails', methods=['GET'])
def get_emails():
    conversations = []

    # Fetch all conversations
    for conv_doc in inbox_conversations_collection.find({}, {'_id': 0}):
        conv_id = conv_doc.get('conv_id')
        subject = conv_doc.get('messages')[0].get('subject')
        email_address = conv_doc.get('email_address', '')
        messages_out = []

        for msg in conv_doc.get('messages', []):
            attachments_out = []
            for attach in msg.get('attachments', []):
                attachments_out.append({
                    'id': attach.get('id'),
                    'name': attach.get('name'),
                    'contentType': attach.get('contentType'),
                    'size': attach.get('size'),
                    'isInline': attach.get('isInline', False),
                    'summary':attach.get('attachment_summary', '')
                    # ⚠ Don’t send contentBytes here unless explicitly requested
                })

            messages_out.append({
                'message_id': msg.get('message_id'),
                'conv_id': conv_doc.get('conv_id'),
                'email_address':conv_doc.get('email_address', ''),
                'subject': msg.get('subject'),
                'conv_index': msg.get('conv_index'),
                'child_replies': msg.get('child_replies'),
                'sender': msg.get('sender'),
                'receivers': msg.get('receivers'),
                'cc': msg.get('cc'),
                'bcc': msg.get('bcc'),
                'body': msg.get('body'),
                'webLink': msg.get('webLink'),
                'received_time': msg.get('received_time'),
                'attachments': attachments_out,
                'type': msg.get('type')
            })

        # Sort messages by received_time if present
        messages_out.sort(key=lambda m: m.get('received_time') or '', reverse=False)

        conversations.append({
            'conv_id': conv_id,
            'email_address': email_address,
            'subject': subject,
            'messages': messages_out
        })
    # print(conversations)

    return jsonify({
        "status": "success",
        "conversations": conversations
    })
# @add_on_bp.route('/emails', methods=['GET'])
# def get_emails():
#     """
#     Fetches a list of saved emails from the database.
#     """
#     emails = []
#     count = 0
#     for doc in inbox_messages_collection.find({}, {'_id': 0, 'message_id': 1, 'subject': 1, 'sender': 1, 'email_address': 1, 'type': 1, 'analysis': 1, 'body':1, "receivers":1, "email_address":1, "history":1}):
#         count+=1
#         emails.append(doc)
    
#     return jsonify({"status": "success", "emails": emails})

@add_on_bp.route('/trigger_analysis/<string:conv_id>/<string:message_id>/<string:user_id>/<string:analysis_type>', methods=['POST'])
def trigger_analysis(conv_id, message_id, user_id, analysis_type):
    """
    Triggers a specific analysis task for a given email.
    """
    # print(conv_id[:10], message_id[:10], user_id)
    # import workers.tasks as tasks
    if analysis_type == 'importance':
        generate_importance_analysis.delay(conv_id, message_id, user_id)
        message = f"Importance analysis triggered for message {message_id}."
    elif analysis_type == 'summary_replies':
        generate_summary_and_replies.delay(conv_id, message_id, user_id)
        message = f"Summary and replies generation triggered for message {message_id}."
    # elif analysis_type == 'categorization':
    #     tasks.generate_category_task(message_id, user_id)
    #     message = f"Categorization triggered for message {message_id}."
    else:
        return jsonify({"status": "error", "message": "Invalid analysis type."}), 400
    
    return jsonify({"status": "success", "message": message})

@add_on_bp.route('/email_analysis/<string:conv_id>/<string:message_id>/<string:user_id>', methods=['GET'])
def get_email_analysis(conv_id, message_id, user_id):
    """
    Fetches the latest analysis results for a specific email.
    """
    print("Get Email Analysis")

    current_message_doc = inbox_conversations_collection.find_one(
        {'conv_id': conv_id, "email_address":user_id, 'messages.message_id': message_id},
        {'_id': 0, 'messages.$': 1}
    )
    current_message = current_message_doc['messages'][0]
    
    if current_message and 'analysis' in current_message:
        print("Pooling Analysis")
        # print(current_message['analysis'])
        return jsonify({"status": "success", "analysis": current_message['analysis']})
    else:
        return jsonify({"status": "not_found", "message": "Analysis not found or not yet completed."}), 200
        # return jsonify({"status": "not_found", "message": "Analysis not found or not yet completed."}), 404


@add_on_bp.route('/sync_all_mail_history', methods=['POST'])
def sync_all_mail():
    try:
        data = request.get_json()
        email_address = data.get('email_address')
        operator = data.get('operator')
        mailType = data.get('mailType')
        # print(email_address, operator, mailType)
        if operator=="Outlook":
            user_data = users_collection.find_one({'user_id': email_address})
            account_type = user_data.get('account_type')
            access_token = ""
            # api_endpoint = ''
            mail_folders_endpoint = ''
            
            if account_type=="licensed":
                access_token = get_outlook_access_token(email_address, account_type, user_data)
                # api_endpoint = f"{Config.MS_GRAPH_ENDPOINT}/me/mailfolders('inbox')/messages?$orderby=receivedDateTime desc"
                mail_folders_endpoint = f"{Config.MS_GRAPH_ENDPOINT}/me/mailFolders"
            elif account_type=="unlicensed":
                access_token = get_application_access_token()
                # api_endpoint = f"{Config.MS_GRAPH_ENDPOINT}/users/{email_address}/mailfolders('inbox')/messages?$orderby=receivedDateTime desc"
                mail_folders_endpoint = f"{Config.MS_GRAPH_ENDPOINT}/users/{email_address}/mailFolders('inbox')/childFolders"
            else:
                return jsonify({"error": "Unknown account type"}), 400
        
            headers = {
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/json'
                    }

            all_messages = []
            
            next_folders_link = mail_folders_endpoint
            while next_folders_link:
                folders_resp = requests.get(next_folders_link, headers=headers)
                folders_resp.raise_for_status()
                folders = folders_resp.json().get('value', [])
            
                for folder in folders:
                    folder_id = folder.get('id')
                    folder_display_name = folder.get('displayName')
                    if folder_display_name=="緊急度高":
                        messages_endpoint = f"{mail_folders_endpoint}/'{folder_id}'/messages?$select=conversationId,id&$orderby=receivedDateTime desc"
                        next_link = messages_endpoint
                        
                        while next_link:
                            resp = requests.get(next_link, headers=headers)
                            resp.raise_for_status()
                            response_data = resp.json()
                            
                            conversations = response_data.get('value', [])
                            # print(len(conversations))
                            
                            for conv in conversations[1:3]:
                                conversation_id = conv.get('conversationId')
                                message_id=conv.get('id')
                                # subject = conv.get('subject', 'N/A')
                                # print("------------Conversation-------------------")
                                # print(conversation_id, subject)
                                # inbox_conversations_collection.update_one
                                prepare_conversation_thread_outlook(email_address, conversation_id, message_id)
                            all_messages.extend(conversations)
                            
                            # Check for the next page link
                            next_link = response_data.get('@odata.nextLink')

                        # print(f"Total messages fetched from all folders: {len(all_messages)}")
                next_folders_link = folders_resp.json().get('@odata.nextLink')


        return jsonify({"status": "success", "message": f"Full mail sync initiated for {email_address}"}), 200
    except requests.exceptions.RequestException as e:
        print(f"Error syncing mail history: {e}")
        return jsonify({"error": f"API request error: {e}"}), 500
    except Exception as e:
        print(f"Error during full mail sync request: {e}")
        return jsonify({"status": "error", "message": f"Internal server error: {e}"}), 500

# def prepare_conversation_thread(email_address, headers, conv, conversation_id):
#     conversation_endpoint = f"{Config.MS_GRAPH_ENDPOINT}/users/{email_address}/messages?$filter=conversationId eq '{conversation_id}'"
#     conv_resp = requests.get(conversation_endpoint, headers=headers)
#     conv_resp.raise_for_status()
#     conv_response_data = conv_resp.json()
                                
#     conv_messages = conv_response_data.get('value', [])
#     messages = []
#                                 # count = 0
#     for msg in conv_messages:
#                                     # print(msg)
#                                     # print('-----------------Message---------------')
#         message_id = msg.get('id')
#         msg_subject = msg.get('subject')
#                                     # print(message_id, msg_subject)
#         msg_endpoint = f"{Config.MS_GRAPH_ENDPOINT}/users/{email_address}/messages/{message_id}?$select=uniqueBody"
#         single_msg_resp = requests.get(msg_endpoint, headers=headers)
#         single_msg_resp.raise_for_status()
#         single_msg_data = single_msg_resp.json()
#                                     # print(single_msg_data.get("uniqueBody", {}))

#         conv_index = msg.get('conversationIndex')
#         number_of_child_replies = decode_conversation_index(msg.get('conversationIndex')).get("number of replies", '')
#         sender_info = msg.get('sender', {}).get('emailAddress', {})
#         sender = sender_info.get('address', 'N/A')
#         receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('toRecipients', [])]
#         receivers_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('toRecipients', [])]
#         cc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('ccRecipients', [])]
#         bcc_list = [r.get('emailAddress', {}).get('address', 'N/A') for r in msg.get('bccRecipients', [])]
#                                     # body_content = msg.get('body')
#         body_content = single_msg_data.get("uniqueBody", {})
#         cleaned_body = get_unique_body_outlook(body_content)
#         inline_attachments = get_inline_attachments_outlook(body_content)
#         attachments_data = []
#                                     # print(msg.get('hasAttachments'))
#         if msg.get('hasAttachments') or len(inline_attachments)>0:
#                                         # print(f"Message {message_id[:10]} has attachments. Fetching attachment details...")
#             attachments_url = f"{Config.MS_GRAPH_ENDPOINT}/users/{email_address}/messages/{message_id}/attachments"
#             try:
#                 attachments_resp = requests.get(attachments_url, headers=headers)
#                 attachments_resp.raise_for_status()
#                 fetched_attachments = attachments_resp.json().get('value', [])
                                            
#                 for attach in fetched_attachments:
#                                                 # Only store relevant fields and contentBytes if available
#                     attachment_info = {
#                                                     'id': attach.get('id'),
#                                                     'name': attach.get('name'),
#                                                     'contentType': attach.get('contentType'),
#                                                     'size': attach.get('size'),
#                                                     'isInline': attach.get('isInline', False),
#                                                     'contentBytes': attach.get('contentBytes') 
#                                                 }
#                     attachments_data.append(attachment_info)
#                                             # print(f"  Fetched {len(attachments_data)} attachment details for message {message_id}.")
#             except requests.exceptions.RequestException as attach_e:
#                 print(f"  Error fetching attachments for message {message_id}: {attach_e}")
#                 if attach_e.response:
#                     print(f"  Attachment fetch error response: {attach_e.response.text}")
#         received_time = convert_utc_to_local(msg.get('receivedDateTime', {}))
                                    
#         message_doc = {
#                                         'message_id': message_id,
#                                         'subject':msg_subject,
#                                         'conv_index':conv_index,
#                                         "child_replies":number_of_child_replies,
#                                         'sender': sender,
#                                         'receivers': receivers_list,
#                                         'cc': cc_list,
#                                         'bcc': bcc_list,
#                                         'body': cleaned_body,
#                                         'full_message_payload': conv,
#                                         'webLink': conv.get('webLink'),
#                                         'received_time':received_time,
#                                         'attachments':attachments_data,
#                                         'type':'outlook_received_mail'
#                                     }
#         messages.append(message_doc)
#     conv_doc = {
#                                     'conv_id': conversation_id, 
#                                     'email_address': email_address, 
#                                     'messages':messages
#                                 }
#     inbox_conversations_collection.update_one(
#                                     {'conv_id': conversation_id},
#                                     {'$set': conv_doc},
#                                     upsert=True
#                                 )
#     for message in messages:
#         if 'ffp.co.jp' not in message.get('sender'):
#             attachments = message.get('attachments')
#             if len(attachments)>0:
#                 generate_attachment_summary.delay(conversation_id, message.get('message_id'), email_address, 'outlook')
#             generate_previous_emails_summary.delay(conversation_id, message.get('message_id'), email_address)

@add_on_bp.route('/validate_outgoing_gmail', methods=['POST'])
def validate_outgoing_gamil():
    """
    Receives draft data from the Apps Script, saves it as a new document
    in the MongoDB 'drafts' collection, and returns the inserted ID.
    """
    try:
        # Get the JSON data from the request body
        data = request.json
        if not data:
            return jsonify({"status": "error", 'error': 'No data provided'}), 400
        
        conv_id = data.get('conv_id').replace('/', '-').replace('+', '_')
        
        sender = data.get('sender')
        subject = data.get('subject')
        body = data.get('body').split('From:')[0]

        receipients = data.get('recipients')
        cc = data.get('cc')
        bcc = data.get('bcc')
        print(sender, subject, body, receipients, cc, bcc)
        attachments = data.get('attachments')
        attachment_names = [attachment['name'] for attachment in attachments]
        email_address = data.get('email_address')
        previous_emails_summary = ''
        previous_email_sender = ''
        previous_email_cc = []
        previous_email_bcc = []
        previous_email_receipients = []
        # print(email_data['sender'])
        if conv_id:
            latest_message = get_latest_message_with_aggregation(conv_id, email_address)
            latest_message_id = latest_message.get('message_id')
            if latest_message_id:
                previous_emails_summary = latest_message.get('previous_messages_summary', '')
                previous_email_sender = latest_message.get('sender', '')
                previous_email_receipients = latest_message.get('receivers', [])
                previous_email_cc = latest_message.get('cc', [])
                previous_email_bcc = latest_message.get('bcc', [])

        # prompt = (
        #     f"Analyze the following email and return a JSON object based on the schema. "
        #     f"The analysis should cover four key areas: sensitive data, missing attachments, grammatical/spelling issues, and general business etiquette.\n\n"
        #     f"Provide a True/False result for each condition. If an issue is found (e.g., True), provide a clear and concise description of the error in Japanese within 200 characters. If no issue is found (e.g., False), provide a brief explanatory comment in Japanese.\n\n"
        #     f"**Instructions:**\n"
        #     f"- **Sensitive Data:** Check the email body and subject for any Personally Identifiable Information (PII) or other sensitive content. **Exclude the email signature from this check, as it is considered personal information that is always present and acceptable.**\n"
        #     f"- **Missing Attachments:** Based on the body content (e.g., phrases like 'see attached file'), determine if an attachment is mentioned but not present in the provided list. Assume the provided 'attachments' list contains the names of all files.\n"
        #     f"- **Grammar & Spelling:** Identify all grammatical and spelling errors. Be sure to check recipient names against previous email data for consistency.\n"
        #     f"- **Japanese Business Etiquette:** Evaluate if the email follows standard Japanese business etiquette, including appropriate use of honorifics (e.g., `様` and `さん`), formal language (`keigo`), and a respectful tone. \n\n"
        #     f"**Email Data:**\n"
        #     f"Subject: {subject}\n"
        #     f"Body:\n{body}\n"
        #     f"Attachment Names: {str(attachment_names)}\n"
        #     f"Sender: {sender}\n"
        #     f"Recipients: {', '.join(receipients) if isinstance(receipients, list) else receipients}\n"
        #     f"CC: {', '.join(cc) if isinstance(cc, list) else cc}\n"
        #     f"BCC: {', '.join(bcc) if isinstance(bcc, list) else bcc}\n\n"
        #     f"**Previous Email Data (if available):**\n"
        #     f"Previous Conversation Summary: {previous_emails_summary}\n"
        #     f"Previous Email Sender: {previous_email_sender}\n"
        #     f"Previous Email Recipients: {', '.join(previous_email_receipients)}\n"
        #     f"Previous Email CC: {', '.join(previous_email_cc)}\n"
        #     f"Previous Email BCC: {', '.join(previous_email_bcc)}\n\n"
        #     f"**Output Format (JSON)**:\n"
        #     f"{json.dumps(GEMINI_RESPONSE_SCHEMA, indent=2)}\n"
        # )
        # response = call_gemini_api_structured_output(prompt, GEMINI_RESPONSE_SCHEMA)
        # Extract the necessary fields. These will become fields in our MongoDB document.
        # message_id = data.get('message_id')
        # sender = data.get('sender')
        # receiver = data.get('receiver')
        # subject = data.get('subject')
        # contents = data.get('contents')
        # drafts_at = data.get('drafts_at')
        
        # # Validate that all required fields are present
        # if not all([receiver, subject, contents]):
        #     return jsonify({'error': 'Missing required fields: recipient, subject, or body'}), 400

        # # Create a document to insert into MongoDB
        # insert_result = draft_messages_collection.insert_one(
        #     {'message_id': message_id, 'sender': sender,
        #                     'receiver': receiver,
        #                     'subject': subject,
        #                     'contents': contents,
        #                     'drafts_at': drafts_at,},
        # )
        return jsonify({"status": "success", "message": "Email is ready to send.",}), 201
        
        # Return a success message with the ID of the new document
        # response = {
        #     'attachments': {'comment': '本文中で添付ファイルについて言及されておらず、添付ファイルもありません。', 'has_missing_attachments': False}, 
        #     'best_practices': {'comment': '宛名、挨拶、結びの言葉がなく、ビジネスメールとしての形式や敬語が使用されていません。非常に簡潔すぎる内容です。', 'is_not_followed': True}, 
        #     'grammatical_errors': {'comment': '文法的な誤りはありません。', 'has_errors': True}, 
        #     'sensitive_data': {'comment': '個人情報や機密情報は含まれていません。', 'has_sensitive_data': True}, 
        #     'spelling_mistakes': {'comment': 'スペルミスはありません。', 'has_mistakes': False}}
        # return jsonify({"status": "success", "message": "Email is ready to send.", "data":json.dumps(response)}), 200

    except Exception as e:
        print(f"Error processing outgoing email: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred."}), 500


def get_latest_message_with_aggregation(conversation_id, email_address):
    conv_id = conversation_id.replace('/', '-').replace('+', '_')
    pipeline = [
        # Match the document with the specific conversation ID
        {'$match': {'conv_id': conv_id, 'email_address':email_address}},
        # Unwind the messages array to treat each message as a separate document
        {'$unwind': '$messages'},
        # Sort the messages by received_time in descending order
        {'$sort': {'messages.received_time': -1}},
        # Group back and take the first (latest) message
        {'$limit': 1},
        # You can add a $project stage to shape the output if needed
        {'$project': {'_id': 0, 'latest_message': '$messages'}}
    ]
    # print(conv_id, email_address)
    result = list(inbox_conversations_collection.aggregate(pipeline))
    # print(result)
    if result:
        return result[0]['latest_message']
    
    return {}


GEMINI_RESPONSE_SCHEMA = {
                        "type": "OBJECT",
                        "properties": {
                            "sensitive_data": {
                            "type": "OBJECT",
                            "properties": {
                                "has_sensitive_data": { "type": "boolean" },
                                "comment": { "type": "string" }
                            },
                            "required": ["has_sensitive_data", "comment"]
                            },
                            "attachments": {
                            "type": "OBJECT",
                            "properties": {
                                "has_missing_attachments": { "type": "boolean" },
                                "comment": { "type": "string" }
                            },
                            "required": ["has_missing_attachments", "comment"]
                            },
                            "grammatical_errors": {
                            "type": "OBJECT",
                            "properties": {
                                "has_errors": { "type": "boolean" },
                                "comment": { "type": "string" }
                            },
                            "required": ["has_errors", "comment"]
                            },
                            "best_practices": {
                            "type": "OBJECT",
                            "properties": {
                                "is_not_followed": { "type": "boolean" },
                                "comment": { "type": "string" }
                            },
                            "required": ["is_not_followed", "comment"]
                            },
                            "spelling_mistakes": {
                            "type": "OBJECT",
                            "properties": {
                                "has_mistakes": { "type": "boolean" },
                                "comment": { "type": "string" }
                            },
                            "required": ["has_mistakes", "comment"]
                            }
                        },
                        "required": ["sensitive_data", "attachments", "grammatical_errors", "best_practices", "spelling_mistakes"]
                        }

@add_on_bp.route('/validate_outgoing', methods=['POST'])
def validate_outgoing():
    # print("********************Validating Outgoing Mail*********************")
    try:
        email_data = request.json
        if not email_data:
            return jsonify({"status": "error", "message": "No email data provided"}), 400
        # message_id = email_data.get('message_id').replace('/', '-').replace('+', '_')
        conv_id = email_data.get('conv_id').replace('/', '-').replace('+', '_')
        
        sender = email_data.get('sender')
        subject = email_data.get('subject')
        body = email_data.get('body').split('From:')[0]

        receipients = email_data.get('recipients')
        cc = email_data.get('cc')
        bcc = email_data.get('bcc')
        
        attachments = email_data.get('attachments')
        # print(attachments)
        attachment_names = [attachment['name'] for attachment in attachments]
        # print(attachment_names)
        email_address = email_data.get('email_address')
        previous_emails_summary = ''
        previous_email_sender = ''
        previous_email_cc = []
        previous_email_bcc = []
        previous_email_receipients = []
        # print(email_data['sender'])
        if conv_id:
            latest_message = get_latest_message_with_aggregation(conv_id, email_address)
            latest_message_id = latest_message.get('message_id')
            if latest_message_id:
                previous_emails_summary = latest_message.get('previous_messages_summary', '')
                previous_email_sender = latest_message.get('sender', '')
                previous_email_receipients = latest_message.get('receivers', [])
                previous_email_cc = latest_message.get('cc', [])
                previous_email_bcc = latest_message.get('bcc', [])
        prompt = (
            f"Analyze the following email and return a JSON object based on the schema. "
            f"The analysis should cover four key areas: sensitive data, missing attachments, grammatical/spelling issues, and general business etiquette.\n\n"
            f"Provide a True/False result for each condition. If an issue is found (e.g., True), provide a clear and concise description of the error in Japanese within 200 characters. If no issue is found (e.g., False), provide a brief explanatory comment in Japanese.\n\n"
            f"**Instructions:**\n"
            f"- **Sensitive Data:** Check the email body and subject for any Personally Identifiable Information (PII) or other sensitive content. **Exclude the email signature from this check, as it is considered personal information that is always present and acceptable.**\n"
            f"- **Missing Attachments:** Based on the body content (e.g., phrases like 'see attached file'), determine if an attachment is mentioned but not present in the provided list. Assume the provided 'attachments' list contains the names of all files.\n"
            f"- **Grammar & Spelling:** Identify all grammatical and spelling errors. Be sure to check recipient names against previous email data for consistency.\n"
            f"- **Japanese Business Etiquette:** Evaluate if the email follows standard Japanese business etiquette, including appropriate use of honorifics (e.g., `様` and `さん`), formal language (`keigo`), and a respectful tone. \n\n"
            f"**Email Data:**\n"
            f"Subject: {subject}\n"
            f"Body:\n{body}\n"
            f"Attachment Names: {str(attachment_names)}\n"
            f"Sender: {sender}\n"
            f"Recipients: {', '.join(receipients) if isinstance(receipients, list) else receipients}\n"
            f"CC: {', '.join(cc) if isinstance(cc, list) else cc}\n"
            f"BCC: {', '.join(bcc) if isinstance(bcc, list) else bcc}\n\n"
            f"**Previous Email Data (if available):**\n"
            f"Previous Conversation Summary: {previous_emails_summary}\n"
            f"Previous Email Sender: {previous_email_sender}\n"
            f"Previous Email Recipients: {', '.join(previous_email_receipients)}\n"
            f"Previous Email CC: {', '.join(previous_email_cc)}\n"
            f"Previous Email BCC: {', '.join(previous_email_bcc)}\n\n"
            f"**Output Format (JSON)**:\n"
            f"{json.dumps(GEMINI_RESPONSE_SCHEMA, indent=2)}\n"
        )
        response = call_gemini_api_structured_output(prompt, GEMINI_RESPONSE_SCHEMA)
        # print("Body :", body)
        # print(response)
        # response = {}

        # data = {
        #     'attachments': {'comment': '本文中で添付ファイルについて言及されておらず、添付ファイルもありません。', 'has_missing_attachments': False}, 
        #     'best_practices': {'comment': '宛名、挨拶、結びの言葉がなく、ビジネスメールとしての形式や敬語が使用されていません。非常に簡潔すぎる内容です。', 'is_not_followed': True}, 
        #     'grammatical_errors': {'comment': '文法的な誤りはありません。', 'has_errors': True}, 
        #     'sensitive_data': {'comment': '個人情報や機密情報は含まれていません。', 'has_sensitive_data': True}, 
        #     'spelling_mistakes': {'comment': 'スペルミスはありません。', 'has_mistakes': False}}
        return jsonify({"status": "success", "message": "Email is ready to send.", "data":json.dumps(response)}), 200

    except Exception as e:
        print(f"Error processing outgoing email: {e}")
        return jsonify({"status": "error", "message": "An internal server error occurred."}), 500

import xlsxwriter
import io
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pytz
@add_on_bp.route('/download_excel', methods=['GET'])
def download_excel():
    # Create an in-memory buffer to hold the Excel data
    output = io.BytesIO()
    workbook  = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet()
    worksheet.set_column("A:Z", 30)
    # Define cell formats if needed
    header_format = workbook.add_format({
        'align': 'center',
        'valign': 'vcenter',
        'bold':True,
        "text_wrap":True
    })
    cell_format_center = workbook.add_format({
        'align': 'center',
        'valign': 'vcenter',
        "text_wrap":True
    })
    cell_format_left = workbook.add_format({
        'align': 'center',
        'valign': 'vcenter',
        "text_wrap":True
    })

    # Set up the headers with merged cells
    worksheet.merge_range('A1:A2', 'スレッドの件名', header_format)
    worksheet.merge_range('B1:B2', '受信時刻', header_format)
    worksheet.merge_range('C1:C2', 'メールの件名', header_format)
    worksheet.merge_range('D1:D2', '送信者', header_format)
    worksheet.merge_range('E1:F2', '本文', header_format)
    worksheet.merge_range('G1:H1', '添付ファイル', header_format)
    worksheet.write('G2', 'ファイル名', header_format)
    worksheet.write('H2', '概要', header_format)
    worksheet.merge_range('I1:I2', '過去のメール概要', header_format)
    worksheet.merge_range('J1:N1', '分析', header_format)
    worksheet.write('J2', '重要度スコアと理由', header_format)
    worksheet.write('K2', '概要', header_format)
    worksheet.merge_range('L2:M2', '返信', header_format)
    worksheet.write('N2', 'カテゴリ', header_format)


    pipeline = [
            # Stage 1: Deconstruct the messages array.
            # This creates a new document for each element in the messages array.
            {"$unwind": "$messages"},

            # Stage 2: Sort the deconstructed documents.
            # We sort by conv_id to keep messages from the same conversation together,
            # and then by the nested received_time to order them chronologically.
            {"$sort": {"conv_id": 1, "messages.received_time": 1}},

            # Stage 3: Reconstruct the original document structure.
            # We group by the original document's _id to put the messages back together.
            {"$group": {
                "_id": "$_id",
                "conv_id": {"$first": "$conv_id"},
                "subject": {"$first": "$subject"},
                "messages": {"$push": "$messages"}
            }},

            # Stage 4: (Optional) Project the final document to the desired format if needed.
            # This can be used to re-arrange or hide fields.
            # {"$project": {
            #     "_id": 0,
            #     "conv_id": 1,
            #     "email_address": 1,
            #     "subject": 1,
            #     "messages": 1
            # }}
        ]

    # all_conversations = list(inbox_conversations_collection.find({}))
    all_conversations = list(inbox_conversations_collection.aggregate(pipeline))
    
    start_row=3
    end_row = 3
    for doc in all_conversations:
        messages = doc.get('messages')
        msg_start = start_row
        msg_end = end_row
        for message in messages:
            attachments = message.get('attachments', [])
            for attachment in attachments:
                worksheet.write('G'+str(msg_end), attachment.get('name'), cell_format_center)
                worksheet.write('H'+str(msg_end), attachment.get('attachment_summary'), cell_format_left)
                msg_end+=1
            analysis = message.get('analysis', {})
            without_tz_str =  message.get('received_time').strftime("%Y-%m-%d %H:%M:%S")
            without_tz = datetime.strptime(without_tz_str, "%Y-%m-%d %H:%M:%S")
            original_tz = pytz.timezone('Etc/GMT+9')
            aware_time = original_tz.localize(without_tz)
            converted_time = aware_time.astimezone(pytz.utc)
            received_time = converted_time.strftime("%Y/%m/%d, %H:%M")
            # dt_object = datetime.fromisoformat(received_time)

            # offset = message.get('received_time').tzinfo.utcoffset(message.get('received_time'))
            # print(message.get('received_time'), received_time)
            # received_time = message.get('received_time').strftime("%Y/%m/%d, %H:%M")
            if msg_end!=msg_start:
                if analysis:
                    imp_score = analysis.get('importance_score', '')
                    imp_desc = analysis.get('importance_description', '')
                    score_and_reason = f"{imp_score} : \n {imp_desc}"
                    worksheet.merge_range('J'+str(msg_start)+':J'+str(msg_end), score_and_reason, cell_format_left)
                    worksheet.merge_range('K'+str(msg_start)+':K'+str(msg_end), analysis.get('summary'), cell_format_left)
                    replies = analysis.get('replies', [])
                    reply_txt = ''
                    if replies:
                        reply_txt = f"簡潔 : {replies[0]}\n確認 : {replies[1]}\n丁寧 : {replies[2]}"
                    worksheet.merge_range('L'+str(msg_start)+':M'+str(msg_end), reply_txt, cell_format_left)
                    worksheet.merge_range('N'+str(msg_start)+':N'+str(msg_end), analysis.get('category'), cell_format_center)
                    worksheet.merge_range('I'+str(msg_start)+':I'+str(msg_end), message.get('previous_messages_summary', ''), cell_format_left)
                worksheet.merge_range('B'+str(msg_start)+':B'+str(msg_end), received_time, cell_format_left)
                worksheet.merge_range('C'+str(msg_start)+':C'+str(msg_end), message.get('subject'), cell_format_left)
                worksheet.merge_range('D'+str(msg_start)+':D'+str(msg_end), message.get('sender'), cell_format_left)
                worksheet.merge_range('E'+str(msg_start)+':F'+str(msg_end), message.get('body'), cell_format_left)
                
            else:
                if analysis:
                    imp_score = analysis.get('importance_score', '')
                    imp_desc = analysis.get('importance_description', '')
                    score_and_reason = f"{imp_score} : \n {imp_desc}"
                    worksheet.write('J'+str(msg_start), score_and_reason, cell_format_left)
                    worksheet.write('K'+str(msg_start), analysis.get('summary'), cell_format_left)
                    replies = analysis.get('replies', [])
                    reply_txt = ''
                    if replies:
                        reply_txt = f"簡潔 : {replies[0]}\n確認 : {replies[1]}\n丁寧 : {replies[2]}"
                    worksheet.merge_range('L'+str(msg_start)+':M'+str(msg_end), reply_txt, cell_format_left)
                    worksheet.write('N'+str(msg_start), analysis.get('category'), cell_format_center)
                    worksheet.write('I'+str(msg_start), message.get('previous_messages_summary', ''), cell_format_left)
                
                worksheet.write('B'+str(msg_start), received_time, cell_format_left)
                worksheet.write('C'+str(msg_start), message.get('subject'), cell_format_left)
                worksheet.write('D'+str(msg_start), message.get('sender'), cell_format_left)
                worksheet.merge_range('E'+str(msg_start)+':F'+str(msg_end), message.get('body'), cell_format_left)
                
            msg_end+=1
            msg_start=msg_end
            end_row = msg_end
        
        worksheet.merge_range('A'+str(start_row)+':A'+str(end_row), doc.get('subject'), cell_format_center)
        end_row+=1
        start_row = end_row
        
    workbook.close()
    output.seek(0)
    
    # Use Flask's send_file to return the buffer as an attachment
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='data.xlsx'
    )