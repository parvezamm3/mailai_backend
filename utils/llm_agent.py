import os
from datetime import datetime
import json
import sqlite3
import base64
import asyncio
from typing import TypedDict, Optional, List, Literal
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
import logging

# Assuming these are available and async-compatible.
# Note: You'll need to change `database.py` to use an async client like `motor`.
from config import Config
from app import celery_app
from database_async import users_collection_async, inbox_conversations_collection_async 
# from database_async import inbox_conversations_collection_async
from utils.gemini_utils import call_gemini_api 
from utils.transform_utils import convert_to_local_time
from utils.attachment_processing import extract_text_from_attachment

logger = logging.getLogger(__name__)
CONDITION_RULES = {
    "severity_rules": {
        "🔴 重大（クリティカル） - 即座に対応（15分以内）": {
            "スコア": "80-100",
            "システム影響": "購買・調達業務の完全停止、サプライチェーン断絶",
            "状況": {
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
            },
            "業務への影響": [
                "生産ライン停止リスク",
                "店舗・倉庫への商品供給停止",
                "主要取引先との取引停止",
                "決済・支払処理の全面停止"
            ],
            "キーワード": "「EDI停止」「発注できない（全社）」「取引先と繋がらない」「生産停止」「在庫切れ緊急」"
        },
        "🟡 高（高優先度） - 優先対応（1時間以内）": {
            "スコア": "60-79",
            "システム影響": "重要機能の部分停止、主要取引先への影響",
            "状況": {
                "購買管理システム": [
                    "特定カテゴリの発注機能停止",
                    "承認者不在による承認遅延",
                    "発注書印刷・送付機能不具合",
                    "仕入先別発注データ抽出不可",
                    "予算管理機能の異常"
                ],
                "EDIシステム": [
                    "特定取引先とのEDI通信障害",
                    "データ変換エラー（一部取引先）",
                    "自動発注の部分的停止",
                    "在庫連携データの送信遅延",
                    "受注確認データの未受信"
                ]
            },
            "業務シナリオ": [
                "主要仕入先との定期発注に支障",
                "特定商品カテゴリの調達停止",
                "大口取引先からの受注処理遅延",
                "月次・週次の定期発注に影響"
            ],
            "キーワード": "「A社とのEDI不通」「○○カテゴリ発注不可」「定期発注エラー」「受注データ未着」"
        },
        "🟢 中（標準） - 通常対応（4時間以内）": {
            "スコア": "30-59",
            "システム影響": "個人・部分的な業務への影響",
            "状況": {
                "購買管理システム": [
                    "個人の発注権限設定問題",
                    "特定商品の単価・仕入先情報更新",
                    "発注書印刷・送付機能不具合",
                    "帳票レイアウトの軽微な問題",
                    "ユーザー操作に関する質問"
                ],
                "EDIシステム": [
                    "小規模取引先との通信問題",
                    "データフォーマット軽微修正",
                    "送信履歴・ログ確認方法",
                    "EDI設定変更の相談"
                ]
            },
            "キーワード": "「個人アカウント」「操作方法」「履歴確認」「軽微な修正」"
        },
        "🟦 低（一般） - 計画対応（1営業日以内）": {
            "スコア": "0-29",
            "システム影響": "業務継続に直接影響なし",
            "内容": [
                "システム改善要望",
                "新規取引先EDI接続準備",
                "マスタデータ整備計画",
                "操作研修・マニュアル整備",
                "将来的なシステム更改相談"
            ]
        }
    }
}


if "GOOGLE_API_KEY" not in os.environ:
    os.environ["GOOGLE_API_KEY"] = Config.GEMINI_API_KEY

gemini_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")

async def _extract_text_from_attachments(data, filename, email_provider):
    """
    Helper function to extract plain text content from attachments within the
    full_message_payload (either Gmail or Outlook format).
    This is a manual step, assuming attachment data is embedded in the payload.
    For large attachments, a separate API call or FastMCP would be needed.
    """
    attachment_texts = []
    try:
        if 'gmail' in email_provider:
            decoded_bytes = base64.urlsafe_b64decode(data)
        elif 'outlook' in email_provider:
            decoded_bytes = base64.b64decode(data)
        else:
            return attachment_texts

        text = await extract_text_from_attachment(decoded_bytes, filename)
        if text:
            attachment_texts.append(f"--- Attachment: {filename} ---\n{text}\n--- End Attachment ---")
    except Exception as e:
        print(f"Error processing attachment {filename}: {e}")
    
    return attachment_texts

# =========================================================================
# Pydantic Models for Structured Output
# =========================================================================
class SpamCheckResult(BaseModel):
    """The result of the spam and malicious content check."""
    is_spam: bool = Field(..., description="True if the email is spam.")
    is_malicious: bool = Field(..., description="True if the email contains malicious content.")

class ImportanceScoreResult(BaseModel):
    """The importance score and description for the email."""
    score: int = Field(..., description="An importance score from 0-100.")
    description: str = Field(..., description="A short Japanese description of the score reason.")

class ReplyOption(BaseModel):
    """A single suggested reply for the email."""
    type: Literal["Concise", "Confirm", "Polite"]
    text: str = Field(..., description="The Japanese text of the reply.")

class RepliesResult(BaseModel):
    """A list of suggested replies for the email."""
    replies: List[ReplyOption] = Field(..., description="A list of suggested replies.")

class SummarizationAndCategoryResult(BaseModel):
    """The summary and category for the email."""
    summary: str = Field(..., description="A concise summary of the email.")
    category: Literal["エラー", "修理", "問い合わせ", "報告", "キャンペーン", "プロモーション", "スパム", "有害", "返信不要"]


class AgentState(TypedDict):
    """
    Represents the state of a single email analysis session.
    The state persists across multiple requests for the same email.
    """
    email_provider:str
    conv_id:str
    msg_id:str
    user_email:str
    email_body: str
    sender: str
    subject: str
    received_datetime: str
    attachments: Optional[List[dict]]
    attachment_summaries: Optional[str]
    previous_conversation_summary: Optional[str]
    user_choices: List[str] # List of tasks to perform if not spam
    
    # Analysis results are updated by the nodes
    importance_score_result: Optional[dict]
    replies_result: Optional[dict]
    summarization_and_category_result: Optional[dict]
    spam_check_result: Optional[dict]

# =========================================================================
# LangGraph Nodes
# =========================================================================
async def generate_attachment_summary(state: AgentState):
    """
    Generates a summary for each attachment in parallel and saves it to the state.
    """
    print("Starting parallel attachment summary generation...")
    attachments = state.get('attachments', [])
    if not attachments:
        print("No attachments found. Skipping summary generation.")
        return {"attachment_summaries": "No Attachment"}
    
    async def _summarize_single_attachment_async(conv_id, msg_id, user_id, attachment):
        """Helper function to process a single attachment asynchronously."""
        # db = get_async_db()
        # inbox_conversations_collection_async = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
        attachment_id = attachment.get('id')
        attachment_size = attachment.get('size')
        if attachment_size < 1200000:
            extracted_text = await _extract_text_from_attachments(
                attachment.get('contentBytes'), attachment.get('name'), state["email_provider"]
            )
            attachment_summary = ""
            if extracted_text:
                prompt = (
                    f'Summarize the content of the attachments: {extracted_text} '
                    f'within 200 characters in Japanese. Only include Japanese, no Romaji.'
                )
                try:
                    # Corrected: await the async call_gemini_api function
                    attachment_summary = await call_gemini_api(prompt, model="gemini-2.0-flash")
                    if attachment_summary:
                        # Corrected: Use await with the async database client (`motor`)
                        await inbox_conversations_collection_async.update_one(
                            {
                                'conv_id': conv_id, 'email_address': user_id, 'messages.message_id': msg_id
                            },
                            {
                                '$set': {
                                'messages.$[message].attachments.$[attachment].attachment_summary': attachment_summary,
                                }
                            },
                            array_filters=[
                                {"message.message_id": msg_id},
                                {"attachment.id": attachment_id}
                            ]
                        )
                        print(f"DB Update: Saved summary for attachment '{attachment_id}' in thread '{conv_id}'")
                        return {"name": attachment.get('name'), "summary": attachment_summary}
                except Exception as e:
                    print(f"Gemini error occurred for attachment {attachment_id}: {e}")
            else:
                print(f"Text extraction failed for attachment {attachment_id}")
        else:
            print(f"File {attachment_id} is too large (>1.2MB). Skipping.")
        return None

    # Get the unique identifiers for the thread from the state
    user_id = state['user_email']
    conv_id = state['conv_id']
    msg_id = state['msg_id']

    # Create a list of tasks to run in parallel
    tasks = [
        _summarize_single_attachment_async(conv_id, msg_id, user_id, attachment)
        for attachment in attachments
    ]
    # Use asyncio.gather to run all tasks concurrently
    summaries = await asyncio.gather(*tasks)
    
      # Filter out any failed tasks and combine the results
    filtered_summaries_list = [s for s in summaries if s is not None]
    attachment_summaries_text = "\n".join([f"File Name: {s['name']}\t\t Summary: {s['summary']}" for s in filtered_summaries_list])

    print(f"Parallel summary generation complete. {len(filtered_summaries_list)} summaries generated.")
    return {"attachment_summaries": attachment_summaries_text}


async def generate_previous_conversation_summary(state:AgentState):
    print("Generation of previous summary started")
    # db = get_async_db()
    # inbox_conversations_collection_async = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
    try:
        current_received_time = datetime.fromisoformat(state["received_datetime"])
    except ValueError:
        current_received_time = state["received_datetime"]
    try:
        pipeline = [
            { "$match": {
                "conv_id": state['conv_id'],
                "email_address": state["user_email"]
            }},
            { "$project": {
                "_id": 0,
                "previous_messages": {
                    "$filter": {
                        "input": "$messages",
                        "as": "msg",
                        "cond": {
                            "$lt": ["$$msg.received_datetime", current_received_time]
                        }
                    }
                }
            }},
            { "$project": {
                "previous_messages": {
                "$sortArray": {
                    "input": "$previous_messages",
                    "sortBy": { "received_datetime": 1 }
                }
                }
            }}
        ]
        # Corrected: Use await with the async database client (`motor`)
        cursor = await inbox_conversations_collection_async.aggregate(pipeline)
        result = await cursor.to_list(length=None)
        previous_messages = result[0].get("previous_messages", []) if result else []
        print(f"Previous_messages length : {len(previous_messages)}")
    except Exception as e:
        print(f"DB aggregation error: {e}")
        previous_messages = []
    
    summary = ''
    if previous_messages:
        if len(previous_messages) == 1:
            summary = previous_messages[0].get("analysis", {}).get('summary','')
        else:
            previous_messages_summaries = ""
            for message in previous_messages:
                # assuming message.received_datetime is a datetime object
                received_time_str = convert_to_local_time(message['received_datetime']).strftime("%Y-%m-%d %H:%M:%S")
                logger.info("Received datetime %s", received_time_str)
                previous_messages_summaries += f"Received Time : {received_time_str}\t\tMail Summary: {message.get("analysis", {}).get('summary','')}"
            logger.info("previous message summaries : %s", previous_messages_summaries)
            if previous_messages_summaries:
                prompt_summary = f'Summarize the key points and unresolved issues from the summaries of the previous email of this thread: {previous_messages_summaries} within 200 characters in Japanese. Only include Japanese, no Romaji.'

                try:
                    # Corrected: await the async call_gemini_api function
                    summary = await call_gemini_api(prompt_summary)
                    logger.info('Generated summary %s', summary)
                except Exception as e:
                    print("Gemini error occured", e)
    
    # Corrected: Use await with the async database client (`motor`)
    await inbox_conversations_collection_async.update_one(
        {
            'conv_id': state['conv_id'], 'email_address': state['user_email'], 'messages.message_id':state['msg_id']
        },
        {
            '$set': {
            'messages.$[message].previous_messages_summary': summary,
            }
        },
        array_filters=[
            {"message.message_id": state['msg_id']},
        ]
    )
    print("Previous generation summary completed")
    return {"previous_conversation_summary": summary}

# Corrected: This function is already async, no changes needed here.
async def initial_processing_and_parallel_nodes(state: AgentState):
    """
    A single node that wraps the parallel execution of the two asynchronous
    summary generation tasks. This allows LangGraph to treat them as one atomic
    step and then proceed.
    """
    print("Starting initial processing (parallel tasks)...")
    
    attachment_result, previous_summary_result = await asyncio.gather(
        generate_attachment_summary(state),
        generate_previous_conversation_summary(state)
    )
    
    combined_state_update = {}
    combined_state_update.update(attachment_result)
    combined_state_update.update(previous_summary_result)
    
    print("All initial processing tasks completed.")
    return combined_state_update
    
# Corrected: Change this to an async function and use ainvoke
async def check_spam_and_malicious(state: AgentState):
    """Checks if the email is spam or malicious."""
    print("Running spam check...")

    prompt = (
        f"Check the mail is spam or has malicious content"
        f"Your response must be a single JSON object."
        "The JSON should have two keys: 'is_spam' and 'is_malicious', with boolean values.\n"
        f'Sender: {state['sender']}\n'
        f'Subject: {state['subject']}\n'
        f'Body:\n{state['email_body']}\n\n'
    )
    if state.get("attachment_summaries") and state["attachment_summaries"] != "No Attachment":
        prompt+=f'Summary Of The Attachments :\n{state["attachment_summaries"]}\n\n'
    if state.get('previous_conversation_summary'):
        prompt += f"Previous Conversation Summary:\n{state['previous_conversation_summary']}"

    try:
        # gemini_llm_2 = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
        llm_with_structured_output = gemini_llm.with_structured_output(SpamCheckResult)
        response = await asyncio.to_thread(llm_with_structured_output.invoke, [HumanMessage(prompt)])
        print(response)
        return {"spam_check_result": {'is_spam':response.is_spam, 'is_malicious':response.is_malicious}}
    except Exception as e:
        print(f"Error invoking Gemini with structured output for spam check: {e}")
        return {"spam_check_result": {'is_spam':False, 'is_malicious':False}}

# Corrected: These functions already use ainvoke correctly.
async def get_importance_score(state: AgentState):
    """Assigns an importance score to the email."""
    print("Running importance score analysis...")
    prompt = (
        f"Assign an importance score from 0-100 based on these rules: {CONDITION_RULES} in the following email."
        f'Then, Provide a one-sentence summary *within 100 characters* describing the reason behind the scoring in Japanese.'
        f'If any keyword or its synonymous text from the conditions exists in the mail, score it corresponding to its category and mention the keyword in the description.'
        f"Your response must be a single JSON object. The JSON should have two keys: 'score' (number) and 'description' (string).\n\n"
        f'Body:\n{state['email_body']}\n\n'
    )
    if state.get("attachment_summaries"):
        prompt+=f'Attachment Summaries:\n{state["attachment_summaries"]}\n\n'
    if state.get('previous_conversation_summary'):
        prompt += f"Previous conversation summary: {state['previous_conversation_summary']}"
    try:
        llm_with_structured_output = gemini_llm.with_structured_output(ImportanceScoreResult)
        response = await asyncio.to_thread(llm_with_structured_output.invoke, [HumanMessage(prompt)])
        # print(response)

        return {"importance_score_result": {'score':response.score, 'description':response.description}}
    except Exception as e:
        print(f"Error invoking Gemini with structured output for importance score: {e}")
        return {"importance_score_result": {'score':0, 'description':"JSON parsing error"}}

async def suggest_replies(state: AgentState):
    """Suggests three business Japanese replies for the email."""
    print("Running reply suggestions...")
    prompt = (
        f"Analyze the following email content and determine if reply needed or not."
        f"If a reply is needed, generate three reply options in Business Japanese: 'Concise', 'Confirm', and 'Polite'.\n"
        f"Your response must be a single JSON object. The JSON must have a single key 'replies' which is an array of objects.\n"
        f"Each reply object should have 'type' (enum: 'Concise', 'Confirm', 'Polite') and 'text' (string).\n"
        f"You must format the reply text to be highly readable. Insert newline characters (`\n`) for clarity.\n"
        f"If no reply is needed, the 'replies' array should be empty.\n\n"
        f'Sender: {state['sender']}\n'
        f'Body:\n{state['email_body']}\n\n'
    )
    if state.get("attachment_summaries"):
        prompt+=f'Attachment Summaries:\n{state["attachment_summaries"]}\n\n'
    if state.get('previous_conversation_summary'):
        prompt += f"Previous conversation summary: {state['previous_conversation_summary']}"
        
    try:
        llm_with_structured_output = gemini_llm.with_structured_output(RepliesResult)
        response = await asyncio.to_thread(llm_with_structured_output.invoke, [HumanMessage(prompt)])
        # print(response)
        replies = {}
        for reply in response.replies:
            replies[reply.type] = reply.text
        return {"replies_result": replies}
    except Exception as e:
        print(f"Error invoking Gemini with structured output for replies: {e}")
        return {"replies_result": {}}

async def summarize_and_categorize_email(state: AgentState):
    """Categorizes the email into a predefined category."""
    print("Running email categorization...")
    prompt = (
        f"Provide a concise summary (2-3 sentences) of the email and its context within the conversation history in Japanese."
        f"Categorize the email into one of the following categories in Japanese: "
        f"'問い合わせ', '報告', etc.\n\n{state['email_body']}"
        f"'エラー' (Error), '修理' (Repair), '問い合わせ' (Inquiry), '報告' (Report), 'キャンペーン' (Campaign),'お知らせ' (Notice), 'プロモーション' (Promotion), 'スパム' (Spam), '有害' (Harmful), '返信不要' (No reply needed)."
        f"Your response must be a single JSON object. The JSON should have two keys: 'summary' (string) and 'category' (string).\n\n"
        f'Sender: {state['sender']}\n'
        f'Body:\n{state['email_body']}\n\n'
    )
    if state.get("attachment_summaries"):
        prompt+=f'Attachment Summaries:\n{state["attachment_summaries"]}\n\n'
    if state.get('previous_conversation_summary'):
        prompt += f"Previous conversation summary: {state['previous_conversation_summary']}"
    try:
        llm_with_structured_output = gemini_llm.with_structured_output(SummarizationAndCategoryResult)
        response = await asyncio.to_thread(llm_with_structured_output.invoke, [HumanMessage(prompt)])
        # print(response)
        return {"summarization_and_category_result": {'category':response.category, 'summary':response.summary}}
    except Exception as e:
        print(f"Error invoking Gemini with structured output for summary/category: {e}")
        return {"summarization_and_category_result": {'summary':"JSON parsing error", 'category':"返信不要"}}


async def run_all_chosen_analyses(state: AgentState):
    """
    A single node that wraps the parallel execution of the selected analysis nodes.
    This replaces the conditional edges directly on the user_choices_router.
    """
    print("Running chosen analyses in parallel...")
    choices = state['user_choices']
    tasks = []
    
    if 'importance_score' in choices:
        tasks.append(get_importance_score(state))
    if 'replies' in choices:
        tasks.append(suggest_replies(state))
    if 'summary_and_category' in choices:
        tasks.append(summarize_and_categorize_email(state))
    try:
        results = await asyncio.gather(*tasks)
        combined_results = {}
        for res in results:
            combined_results.update(res)
        return combined_results
    except Exception as e:
        logger.info("Exception: %s", e)
        return {}
    
def spam_router(state: AgentState) -> str:
    """
    Determines the next step based on the spam check result.
    If spam, the graph ends. Otherwise, it proceeds to other analyses.
    """
    print("Spam router")
    if state.get("spam_check_result"):
        if state['spam_check_result'].get('is_spam') or state['spam_check_result'].get('is_malicious'):
            print("Spam detected. Ending analysis.")
            return "end"
        else:
            print("No spam detected. Proceeding to other analyses.")
            return "run_all_chosen_analyses"
    print("Spam check result not found in state. Proceeding with analysis as a precaution.")
    return "run_all_chosen_analyses"


# Build the LangGraph with a SqliteSaver checkpointer
workflow = StateGraph(AgentState)

# Define the graph structure with the new node
workflow.add_node("initial_processing", initial_processing_and_parallel_nodes)
workflow.add_node("spam_check", check_spam_and_malicious)
workflow.add_node("importance_score", get_importance_score)
workflow.add_node("suggest_replies", suggest_replies)
workflow.add_node("summarize_and_categorize", summarize_and_categorize_email)
workflow.add_node("run_all_chosen_analyses", run_all_chosen_analyses)

# Set the entry point to the new initial processing node
workflow.set_entry_point("initial_processing")

# Add an edge from the initial processing node to the spam check node
workflow.add_edge("initial_processing", "spam_check")

# Add a conditional edge from the spam check node
workflow.add_conditional_edges("spam_check", spam_router, {"end": END, "run_all_chosen_analyses": "run_all_chosen_analyses"})

# From the user_choices_router, conditionally route to the other analysis nodes
workflow.add_edge("run_all_chosen_analyses", END)

# Compile the graph with the SqliteSaver checkpointer
agent = workflow.compile()


async def run_analysis_agent_stateful_async(thread_id: str, email_data: dict, choices: Optional[List[str]] = None):
    """
    Runs the LangGraph agent in a stateful manner.
    The `thread_id` is used to load and save the state.
    """
    logger.info("Async processing started for thread_id=%s", thread_id)
    # db = get_async_db()
    # inbox_conversations_collection_async = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
    async with aiosqlite.connect(":memory:") as conn:
        sqlite_saver = AsyncSqliteSaver(conn=conn)
        config = {"configurable": {"thread_id": thread_id}, "checkpointer": sqlite_saver}
        
        initial_state = {
            'email_provider':email_data['email_provider'],
            'email_body': email_data['body'],
            'sender': email_data['sender'],
            'subject': email_data['subject'],
            'conv_id': email_data.get('conv_id'),
            'user_email': email_data.get('user_email'),
            'msg_id': email_data.get('msg_id'),
            'received_datetime': email_data.get('received_datetime'),
            'attachments': email_data.get('attachments', []),
            'previous_conversation_summary': None,
            'user_choices': choices if choices is not None else [],
            'attachment_summaries': None,
            'importance_score_result': None,
            'replies_result': None,
            'summarization_and_category_result': None,
            'spam_check_result': None,
        }
        
        # Corrected: await the ainvoke call on the agent
        final_state = await agent.ainvoke(initial_state, config=config)

        # logger.info("importance_score_result: %s", final_state.get("importance_score_result"))
        # logger.info("replies_result: %s", final_state.get("replies_result"))
        # logger.info("summarization_and_category_result: %s", final_state.get("summarization_and_category_result"))

    analyzing_results = {}

    if final_state.get("spam_check_result"):
        analyzing_results["is_spam"] = final_state["spam_check_result"].get('is_spam')
        analyzing_results["is_malicious"] = final_state["spam_check_result"].get('is_malicious')
    if final_state.get("importance_score_result"):
        analyzing_results["importance_score"] = final_state["importance_score_result"].get('score')
        analyzing_results["importance_description"] = final_state["importance_score_result"].get('description')
    if final_state.get("summarization_and_category_result"):
        analyzing_results["summary"] = final_state["summarization_and_category_result"].get('summary')
        analyzing_results["category"] = final_state["summarization_and_category_result"].get('category')
    if final_state.get("replies_result"):
        analyzing_results["replies"] = final_state["replies_result"]
    analyzing_results["completed"] = True
    if analyzing_results:
        try:
            # Corrected: Use await with the async database client (`motor`)
            await inbox_conversations_collection_async.update_one(
                {
                    'conv_id': final_state['conv_id'],
                    'email_address': final_state['user_email'],
                    'messages.message_id': final_state['msg_id']
                },
                {
                    '$set': {
                        'messages.$[message].analysis': analyzing_results
                    }
                },
                array_filters=[
                    {"message.message_id": final_state['msg_id']}
                ]
            )
            print(f"DB Update: Saved analyzing_results for message '{final_state['msg_id']}'")
        except Exception as e:
            print(f"Error updating database with analyzing_results: {e}")
    
    print(f"\n--- Analysis complete for thread ID: {thread_id} ---")
    return final_state