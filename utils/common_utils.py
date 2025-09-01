from datetime import datetime
from workers.tasks import (
    run_analysis_agent_stateful
)

def conduct_analysis(email_address, thread_id, msg_doc):
    print(f"Conducting analysis for {msg_doc.get('message_id')} subject {msg_doc.get('subject')}")
    email_data = {
        'user_email':email_address,
        'conv_id':thread_id,
        'msg_id':msg_doc.get('message_id'),
        'received_datetime':msg_doc.get('received_datetime', datetime.now()).strftime("%Y-%m-%dT%H:%M:%S%:z"),
        'sender':msg_doc.get('sender'),
        'subject':msg_doc.get('subject'),
        'body':msg_doc.get('body'),
        'attachments':msg_doc.get('attachments'),
        'email_provider':msg_doc.get('provider')
    }
    choices = ['importance_score', 'replies', 'summary_and_category']
    thread_id = thread_id+"---"+msg_doc.get('message_id', '')
    run_analysis_agent_stateful.delay(thread_id, email_data, choices)