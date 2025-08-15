import json
import time
import requests
import base64
import asyncio
import threading
from bs4 import BeautifulSoup
 # Assuming this is your central message collection
from database import inbox_messages_collection, inbox_conversations_collection
from utils.gemini_utils import call_gemini_api, call_gemini_api_structured
# from celery import Celery, shared_task
from app import celery_app
from utils.attachment_processing import extract_text_from_attachment
# import app

# celery_app will be set dynamically from app.py
# celery_app = None
# Task 1: Generate Importance Score and Description

GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "replies": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "enum": ["Concise", "Confirm", "Polite"]},
                    "text": {"type": "STRING"}
                },
                "required": ["type", "text"]
            }
        },
        "category": {
            "type": "STRING",
            "enum": ["エラー", "修理", "問い合わせ", "報告", "キャンペーン", "プロモーション", "スパム", "有害", "返信不要"]
        }
    },
    "required": ["summary", "replies", "category"]
}


def get_previous_messages(conv_id, email_address, current_message_id, current_message_time):
    """
    Finds and returns all messages in a conversation received before a specific message.
    """
    
    # 1. Define the query to find the specific conversation
    query = {
        'conv_id': conv_id,
        'email_address': email_address
    }
    
    # 2. Define the projection to filter the messages array
    projection = {
        '_id': 0,
        'messages': {
            '$filter': {
                'input': '$messages',
                'as': 'msg',
                'cond': {
                    # Filter for messages with a received_time less than the reference time
                    '$lt': ['$$msg.received_time', current_message_time]
                }
            }
        }
    }
    
    # 3. Find the document and apply the filter
    messages_result = inbox_conversations_collection.aggregate([
        {'$match': query},
        {'$project': projection}
    ])
    
    # 4. Extract the messages and sort them by received_time
    # The $filter operation preserves the original order, but it's good practice to sort explicitly.
    try:
        doc = next(messages_result)
        # print(doc)
        # Sort the filtered messages list in ascending order of received_time
        sorted_messages = sorted(doc.get('messages', []), key=lambda x: x.get('received_time'))
        return sorted_messages
    except StopIteration:
        return []
    

class LoopThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.loop = asyncio.new_event_loop()
    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

_loop_thread = LoopThread()
_loop_thread.start()
time.sleep(0.1)  # Give it time to start

def run_async(coro, timeout=None):
    future = asyncio.run_coroutine_threadsafe(coro, _loop_thread.loop)
    return future.result(timeout)


async def _extract_text_from_attachments(data, filename, message_type):
    """
    Helper function to extract plain text content from attachments within the
    full_message_payload (either Gmail or Outlook format).
    This is a manual step, assuming attachment data is embedded in the payload.
    For large attachments, a separate API call or FastMCP would be needed.
    """
    attachment_texts = []
    try:
        if 'gmail' in message_type:
            decoded_bytes = base64.urlsafe_b64decode(data)
        elif 'outlook' in message_type:
            decoded_bytes = base64.b64decode(data)
        else:
            return attachment_texts

        # Await the coroutine instead of calling asyncio.run()
        text = await extract_text_from_attachment(decoded_bytes, filename)
        if text:
            attachment_texts.append(f"--- Attachment: {filename} ---\n{text}\n--- End Attachment ---")
    except Exception as e:
        print(f"Error processing attachment {filename}: {e}")
    
    return attachment_texts

# @celery_app.task(name='tasks.generate_attatchment_summary')
async def _generate_attachment_summary_async(conv_id, msg_id, user_id, provider_type):
    # print('Generating summary')
    message_result = inbox_conversations_collection.find_one(
        {
            'conv_id': conv_id,
            'email_address':user_id,
            'messages.message_id': msg_id
        },
        {
            # Projection to include only the messages that match the criteria
            '_id': 0,  # Exclude the _id field
            'messages': {
                '$elemMatch': {
                    'message_id': msg_id
                }
            }
        }
    )
    if not message_result or not message_result.get('messages'):
        # print('Message not found.')
        return 'Message Not Found'
    attachments = message_result['messages'][0].get('attachments', [])
    for attachment in attachments:
        # time.sleep(2)
        attachment_size = attachment.get('size')
        if attachment_size<1200000:
            # print('Generating Attachment summary')
            extracted_text =await _extract_text_from_attachments(attachment.get('contentBytes'), attachment.get('name'), provider_type)
            attachment_summary = ""
            if extracted_text:
                prompt_attachment_summary = f'Summarize the content of the attatchments: {extracted_text} within 200 characters in Japanese. Only include Japanese, no Romaji.'
                try:
                    attachment_summary = await call_gemini_api(prompt_attachment_summary,  model="gemini-2.0-flash")
                    inbox_conversations_collection.update_one(
                        {
                            'conv_id': conv_id, 'email_address': user_id, 'messages.message_id':msg_id
                        },
                        {
                            '$set': {
                            'messages.$[message].attachments.$[attachment].attachment_summary': attachment_summary,
                            }
                        },
                        array_filters=[
                            {"message.message_id": msg_id},
                            {"attachment.id": attachment.get('id')}
                        ]
                    )
                except Exception as e:
                    print("Gemini error occured", e)
            else:
                print('Extraction is not completed')
        else:
            print('File is Too Large!!!')
            # return 'Extraction Failed'
    return "Done"


@celery_app.task(name='tasks.generate_attachment_summary')
def generate_attachment_summary(conv_id, msg_id, user_id, provider_type):
    # run the actual async implementation in a fresh event loop
    try:
        result = run_async(_generate_attachment_summary_async(conv_id, msg_id, user_id, provider_type))
        return result
    except Exception as e:
        # Optionally log error, or update DB with failure
        return f'Error: {str(e)}'


async def _generate_previous_emails_summary_async(conv_id, message_id, user_id):
    current_message_doc = inbox_conversations_collection.find_one(
        {'conv_id': conv_id, "email_address":user_id, 'messages.message_id': message_id},
        {'_id': 0, 'messages.$': 1}
    )
    current_message = current_message_doc['messages'][0]
    previous_message_texts = ''
    pm_count = 1
    if current_message_doc and 'messages' in current_message_doc:
        current_time = current_message['received_time']
        previous_messages = get_previous_messages(conv_id, user_id, message_id, current_time)
        
        for pm in previous_messages:
            if isinstance(pm, dict):
                previous_message_texts+=f'Messages {pm_count}: \n{pm.get('body', '')}\n\n'
            pm_count+=1

        # print("Previous Messages :")
        # print(previous_message_texts)

    prompt_summary = f'Summarize the key points and unresolved issues from this previous email of this thread: {previous_message_texts} within 200 characters in Japanese. Only include Japanese, no Romaji.'

    try:
        # time.sleep(2)
        summary = ""
        if previous_message_texts:
            summary = await call_gemini_api(prompt_summary)
            # print(summary)
        inbox_conversations_collection.update_one(
            {
                'conv_id': conv_id, 'email_address': user_id, 'messages.message_id':message_id
            },
            {
                '$set': {
                'messages.$[message].previous_messages_summary': summary,
                }
            },
            array_filters=[
                {"message.message_id": message_id},
            ]
        )
    except Exception as e:
        print("Gemini error occured", e)
        return False

@celery_app.task(name='tasks.generate_previous_emails_summary')
def generate_previous_emails_summary(conv_id, message_id, user_id):
    try:
        result = run_async(_generate_previous_emails_summary_async(conv_id, message_id, user_id))
        return result
    except Exception as e:
        # Optionally log error, or update DB with failure
        return f'Error: {str(e)}'
    

async def _generate_importance_analysis_async(conv_id, message_id, user_id):
    """Celery task to generate importance score and description using Gemini API."""
    print(f"Running Importance Analysis Task Async.")

    current_message_doc = inbox_conversations_collection.find_one(
        {'conv_id': conv_id, 'messages.message_id': message_id},
        {'_id': 0, 'messages.$': 1}
    )
    current_message = current_message_doc['messages'][0]
    # print(message_doc)
    subject = current_message.get('subject', '')
    body = current_message.get('body')
    attachments = current_message.get('attachments', {})
    attachment_summary = f''
    if len(attachments)>0:
        for attachment in attachments:
            attachment_summary+=f"Summary of Attachment Name:{attachment.get('name')} Summary:{attachment.get('attachment_summary', '')}"
    else:
        attachment_summary+="No Attachments"

    previous_emails_summary = current_message.get("previous_messages_summary")
    if not previous_emails_summary:
        previous_emails_summary = "No previous emails"

    prompt_template = '''"""  Analyze the following email (Subject + Latest Message +Attachment Summary + Summary from the previous emails of the conversation thred) and 
        assign it an urgency “importance score” from 0 to 100, 
        based on these procurement/EDI criteria. Provide a one‑sentence description *within 100 characters*. Use the format below:

        Desired output example:
        Score: 45  
        Description: 中（標準） 個人アカウント操作の質問に関する軽微な問い合わせ

        You must Strictly follow the output format.
        There are some keywords and score are mentioned in the JSON rules for every severity category. 
        If any keyword or it's synonymous text from any section exists in the mail score it corresponds to it's category and mention the keyword in the description. 

        JSON rules:
        """
        {{
        "緊急度分類（購買・EDI特化）": {{
            "🔴 重大（クリティカル） - 即座に対応（15分以内）": {{
            "スコア":80-100,
            "システム影響": "購買・調達業務の完全停止、サプライチェーン断絶",
            "状況": {{
                "購買管理システム": [
                "発注システム全停止",
                "仕入先マスタ全件アクセス不可",
                "承認ワークフロー完全停止",
                "在庫切れ商品の緊急発注不可",
                "月末締め処理の完全停止"
                ],
                "EDIシステム": [
                "EDI通信の完全断絶（全取引先）",
                "受発注データの送受信停止",
                "大手取引先との自動連携停止",
                "出荷指示データ送信不可",
                "請求・支払データ交換停止"
                ]
            }},
            "業務への影響": [
                "生産ライン停止リスク",
                "店舗・倉庫への商品供給停止",
                "主要取引先との取引停止",
                "決済・支払処理の全面停止"
            ],
            "キーワード": "「EDI停止」「発注できない（全社）」「取引先と繋がらない」「生産停止」「在庫切れ緊急」"
            }},
            "🟡 高（高優先度） - 優先対応（1時間以内）": {{ 
            "スコア":60-79,
            "システム影響":"重要機能の部分停止、主要取引先への影響",
            "状況":{{
                "購買管理システム":[
                "特定カテゴリの発注機能停止",
                "承認者不在による承認遅延",
                "発注書印刷・送付機能不具合",
                "仕入先別発注データ抽出不可",
                "予算管理機能の異常"
                ],
                "EDIシステム":[
                    "特定取引先とのEDI通信障害", 
                    "データ変換エラー（一部取引先）",
                    "自動発注の部分的停止",
                    "在庫連携データの送信遅延",
                    "受注確認データの未受信"
                ]
                }},
                "業務シナリオ":[
                    "主要仕入先との定期発注に支障", 
                    "特定商品カテゴリの調達停止",
                    "大口取引先からの受注処理遅延",
                    "月次・週次の定期発注に影響"
                ],
                "キーワード":"「A社とのEDI不通」「○○カテゴリ発注不可」「定期発注エラー」「受注データ未着」" 
            }},
            "🟢 中（標準） - 通常対応（4時間以内）": {{
                "スコア":30-59,
                "システム影響":"個人・部分的な業務への影響",
                "状況":{{
                    "購買管理システム":[
                        "個人の発注権限設定問題", 
                        "特定商品の単価・仕入先情報更新",
                        "発注書印刷・送付機能不具合",
                        "帳票レイアウトの軽微な問題",
                        "ユーザー操作に関する質問"
                    ],
                    "EDIシステム":[
                        "小規模取引先との通信問題", 
                        "データフォーマット軽微修正",
                        "送信履歴・ログ確認方法",
                        "EDI設定変更の相談"
                    ]
                }},
                "キーワード":"「個人アカウント」「操作方法」「履歴確認」「軽微な修正」"
            }},
            "🟦 低（一般） - 計画対応（1営業日以内）": {{
            "スコア":0-29,
            "システム影響":"業務継続に直接影響なし",
            "内容":[
                "システム改善要望", 
                "新規取引先EDI接続準備",
                "マスタデータ整備計画",
                "操作研修・マニュアル整備",
                "将来的なシステム更改相談"
            ]
            }}
        }}
        }}

        Subject: {subject}
        Latest Message:
        {body}

        Attatchment Summary: {attachment_summary}
        Previous Conversation Summary:{summary}

    """'''
    prompt = prompt_template.format(subject=subject, body=body, attachment_summary=attachment_summary, summary=previous_emails_summary)
    # print(prompt)

    gemini_response = None
    try:
        gemini_response = await call_gemini_api(prompt, model="gemini-2.0-flash")
        # print('Gemini Response')
        # print(gemini_response)
    except Exception as e:
        print("Gemini error occured", e)
        return False

    importance_score = None
    importance_description = "Could not generate importance description."

    if gemini_response:
        try:
            lines = gemini_response.split('\n', 1)
            score_line = lines[0]
            importance_score=0
            if score_line.startswith('Score:'):
                importance_score = int(score_line.replace('Score:', '').strip())
            if len(lines) > 1 and lines[1].startswith('Description:'):
                importance_description = lines[1].replace('Description:', '').strip()
            else:
                importance_description = gemini_response

            words = importance_description.split()
            if len(words) > 100:
                importance_description = ' '.join(words[:100]) + '...'

            if importance_score > 80 and "helpdesk@ffp.co.jp" in current_message.get('receivers', ''):
                # Prepare the JSON payload for a Teams message
                # This uses a simple text format
                received_time = current_message.get('received_time', '')
                # teams_payload = {
                #     "@type": "MessageCard",
                #     "@context": "http://schema.org/extensions",
                #     "summary": "MailAIからのアラート",  # This is the message preview text
                #     "title": "MailAIからのアラート", # This is the title of the card
                #     "text": f"**件名:** {subject}\n\n**受信日時:** {received_time}\n\n**本文:** {body}",
                #     "themeColor": "b20000" # A hex code for the color of the card border (red for high importance)
                # }

                teams_payload = {
                    "type": "message",
                    "attachments": [
                        {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard",
                            "version": "1.2",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "text": "Critical Mail Alert",
                                    "wrap": True,
                                    "style": "heading",
                                    "color":"attention"
                                },
                                {
                                    "type": "FactSet",
                                    "facts": [
                                        {
                                        "title": "件名",
                                        "value": f"{subject}"
                                        },
                                        {
                                        "title": "受信日時",
                                        "value": f"{received_time}"
                                        },
                                        {
                                        "title": "本文",
                                        "value": f"{body}"
                                        }
                                    ]
                                },
                            ],
                        }
                    }
                    ]
                }

                # Set up the headers for the request
                headers = {
                    "Content-Type": "application/json"
                }

                # Send the POST request to the Teams webhook
                try:
                    teams_webhook_url = "https://prod-07.japaneast.logic.azure.com:443/workflows/7846e0ca56c44bd7a1b2aeb34ac6a4da/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=-TVc0SuSMCleLgFr2QrR2us-Jbe81poMuU3QhWHbnFo"
                    response = requests.post(teams_webhook_url, data=json.dumps(teams_payload), headers=headers)
                    response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
                    print("Message successfully sent to Teams.")
                except requests.exceptions.HTTPError as err:
                    print(f"HTTP Error: {err}")
                except Exception as e:
                    print(f"An error occurred: {e}")

        except Exception as e:
            print(f"Error parsing Gemini importance response for {message_id}: {e}")
            importance_description = f"Failed to parse importance response: {gemini_response}"
            return False

    inbox_conversations_collection.update_one(
        {
            'conv_id': conv_id, 'email_address': user_id, 'messages.message_id':message_id
        },
        {
            '$set': {
            'messages.$[message].analysis.importance_score': importance_score,
            'messages.$[message].analysis.importance_description': importance_description,
            }
        },
        array_filters=[
            {"message.message_id": message_id},
        ])
    # inbox_messages_collection.update_one(
    #     {'_id': message_doc['_id']},
    #     {'$set': {
    #         'analysis.importance_score': importance_score,
    #         'analysis.importance_description': importance_description
    #     }}
    # )
    print(f"Importance analysis for {message_id[:10]} completed: Score={importance_score}, Description='{importance_description[:50]}...'")
    return True

@celery_app.task(name='tasks.generate_importance_analysis')
def generate_importance_analysis(conv_id, message_id, user_id):
    """Celery task to generate importance score and description using Gemini API."""
    print(f"Running Importance Analysis Task.")
    try:
        result = run_async(_generate_importance_analysis_async(conv_id, message_id, user_id))
        return result
    except Exception as e:
        return f'Error: {str(e)}'
    

async def _generate_summary_and_replies_async(conv_id, message_id, user_id):
    """Celery task to generate email summary, three replies, and categorization using Gemini API."""
    print(f"Running summary, replies, and categorization task for message {message_id} (User: {user_id})")
    # message_doc = inbox_messages_collection.find_one({'message_id': message_id, 'email_address': user_id})
    current_message_doc = inbox_conversations_collection.find_one(
        {'conv_id': conv_id, 'messages.message_id': message_id},
        {'_id': 0, 'messages.$': 1}
    )
    current_message = current_message_doc['messages'][0]
    # if not message_doc:
    #     print(f"Message {message_id} not found for summary/replies/categorization analysis.")
    #     return

    subject = current_message.get('subject', '')
    body = current_message.get('body')
    
    sender = current_message.get('sender', '')
    attachment_summary = current_message.get('attachment_summary', '')
    previous_email_summary = current_message.get('previous_messages_summary', '') # Assuming history_summary is stored in message_doc

    prompt = (
        f"Analyze the following email thread. "
        f"Here is the original email, summary of the attached file and a summary of the previous conversation thread."
        f"Based on the content, perform the following tasks and provide the output in JSON format.\n\n"
        f"1. **Summarize the email**: Provide a concise summary (2-3 sentences) of the latest email and its context within the conversation history."
        f"2. **Suggest Replies**: If a reply is needed, suggest three reply options in Business Japanese."
        f"   These replies should be **from the recipient of this email (the user of this system) to the sender of this email** (`{sender}`). "
        f"   - One 'Concise' reply."
        f"   - One 'Confirm' reply (for confirmation of receipt or understanding)."
        f"   - One 'Polite' reply (using the most polite form of Japanese)."
        f"   If no reply is needed (e.g., sender contains 'no-reply' or content is purely informational with no action required), the 'replies' array should be empty and the 'summary' should state '返信不要' (No reply needed)."
        f"3. **Categorize the email**: Assign the email to one of the following categories in Japanese: "
        f"'エラー' (Error), '修理' (Repair), '問い合わせ' (Inquiry), '報告' (Report), 'キャンペーン' (Campaign),'お知らせ' (Notice), 'プロモーション' (Promotion), 'スパム' (Spam), '有害' (Harmful), '返信不要' (No reply needed)."
        f"\n\n"
        f"**Output Format (JSON)**:\n"
        f"{json.dumps(GEMINI_RESPONSE_SCHEMA, indent=2)}\n\n" # Embed the schema for clarity to the model
        f"Sender: {sender}\n\nSubject: {subject}\n\nBody:\n{body}\n\nAttachment Summary{attachment_summary}\n\nPrevious Conversation Summary:\n{previous_email_summary}"
    )

    # print(prompt)

    gemini_response_json = await call_gemini_api_structured(prompt, GEMINI_RESPONSE_SCHEMA)
    # print(gemini_response_json)
    summary = "Could not generate summary."
    replies = []
    category = "Unknown"

    if gemini_response_json:
        try:
            summary = gemini_response_json.get('summary', summary)
            # Replies are an array of objects, extract just the 'text'
            replies = [r.get('text') for r in gemini_response_json.get('replies', []) if r.get('text')]
            category = gemini_response_json.get('category', category)

        except Exception as e:
            print(f"Error parsing structured Gemini response for {message_id}: {e}")
            summary = f"Failed to parse structured response: {gemini_response_json}"
            replies = ["Error parsing replies.", "Please check backend logs."]
            category = "Parsing Error"

    inbox_conversations_collection.update_one(
        {
            'conv_id': conv_id, 'email_address': user_id, 'messages.message_id':message_id
        },
        {
            '$set': {
            'messages.$[message].analysis.summary': summary,
            'messages.$[message].analysis.replies': replies,
            'messages.$[message].analysis.category': category,
            }
        },
        array_filters=[
            {"message.message_id": message_id},
        ])
    
    # inbox_messages_collection.update_one(
    #     {'_id': current_message['_id']},
    #     {'$set': {
    #         'analysis.summary': summary,
    #         'analysis.suggested_replies': replies,
    #         'analysis.category': category # Update category as well
    #     }}
    # )
    print(f"Summary, replies, and categorization for {message_id} completed: Summary='{summary[:50]}...', Replies count={len(replies)}, Category='{category}'")
    

@celery_app.task(name='tasks.generate_summary_and_replies')
def generate_summary_and_replies(conv_id, message_id, user_id):
    """Celery task to generate email summary, three replies, and categorization using Gemini API."""
    print(f"Running summary, replies, and categorization task for message {message_id} (User: {user_id})")
    try:
        result = run_async(_generate_summary_and_replies_async(conv_id, message_id, user_id))
        return result
    except Exception as e:
        return f'Error: {str(e)}'
    
# Task 3: Categorize Message (Spam/Promotional/Primary etc.)


# @celery_app.task(name='tasks.categorize_message')
# def generate_category_task(message_id, user_id): # Renamed function to avoid conflict with task name
#     """Celery task to categorize message (spam, promotional, etc.) using Gemini API."""
#     # from database import messages_collection
#     # print(messages_collection)
#     # print(f"Running categorization task for message {message_id} (User: {user_id})")
#     message_doc = inbox_messages_collection.find_one({'message_id': message_id, 'email_address': user_id})
#     if not message_doc:
#         print(f"Message {message_id} not found for categorization.")
#         return

#     subject = message_doc.get('subject', '')
#     body = message_doc.get('body')
#     # if message_doc.get('type') == 'gmail_message_added':
#     #     body = message_doc.get('full_message_payload', {}).get('body', {}).get('content', '')
#     #     if message_doc.get('full_message_payload', {}).get('body', {}).get('contentType') == 'html':
#     #         try:
#     #             soup = BeautifulSoup(body, 'html.parser')
#     #             body = soup.get_text()
#     #         except Exception as parse_error:
#     #             print(f"Error parsing HTML body for categorization (Gmail): {parse_error}")
#     # elif message_doc.get('type') == 'outlook_message_added':
#     #     body = message_doc.get('full_message_payload', {}).get('body', {}).get('content', '')
#     #     if message_doc.get('full_message_payload', {}).get('body', {}).get('contentType') == 'html':
#     #         try:
#     #             soup = BeautifulSoup(body, 'html.parser')
#     #             body = soup.get_text()
#     #         except Exception as parse_error:
#     #             print(f"Error parsing HTML body for categorization (Outlook): {parse_error}")
    
#     sender = message_doc.get('sender', '')

#     prompt = (
#         f"Categorize the email into one of these categories: "
#         f"'エラー', '修理', '問い合わせ', '報告', 'キャンペーン', 'プロモーション', 'スパム', '有害'. "
#         f"Consider sender, subject, and content.\n\n"
#         f"Sender: {sender}\nSubject: {subject}\n\nBody:\n{body}\n\nCategory:"
#     )

#     gemini_response = call_gemini_api(prompt)
#     category = "Unknown"

#     if gemini_response:
#         cleaned_response = gemini_response.strip().lower()
#         if "エラー" in cleaned_response:
#             category = "エラー"
#         elif "修理" in cleaned_response:
#             category = "修理"
#         elif "問い合わせ" in cleaned_response:
#             category = "問い合わせ"
#         elif "報告" in cleaned_response:
#             category = "報告"
#         elif "キャンペーン" in cleaned_response:
#             category = "キャンペーン"
#         elif "プロモーション" in cleaned_response:
#             category = "プロモーション"
#         elif "スパム" in cleaned_response:
#             category = "スパム"
#         elif "有害" in cleaned_response:
#             category = "有害"
#         else:
#             category = "Other"

#     inbox_messages_collection.update_one(
#         {'_id': message_doc['_id']},
#         {'$set': {
#             'analysis.category': category
#         }}
#     )
#     print(f"Message {message_id} categorized as: {category}")
#     return True

