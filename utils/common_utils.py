from datetime import datetime
from workers.tasks import (
    run_analysis_agent_stateful
)

def conduct_analysis(email_address, thread_id, msg_doc):
    print(f"Conducting analysis for {msg_doc.get('message_id')}")
    email_data = {
        'user_email':email_address,
        'conv_id':thread_id,
        'msg_id':msg_doc.get('message_id'),
        'email_provider':msg_doc.get('provider')
    }
    choices = ['importance_score', 'replies', 'summary_and_category']
    thread_id = thread_id+"---"+msg_doc.get('message_id', '')
    run_analysis_agent_stateful.delay(thread_id, email_data, choices)