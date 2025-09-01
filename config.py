import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask application settings
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'your-default-flask-secret-key')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', "gemini_api_key")

    # MongoDB Configuration
    MONGO_URI = os.getenv('MONGO_URI')
    MONGO_DB_NAME = os.getenv('MONGO_DB_NAME')
    MONGO_USERS_COLLECTION = os.getenv('MONGO_USERS_COLLECTION', 'users')
    MONGO_INBOX_MESSAGES_COLLECTION = os.getenv('MONGO_INBOX_MESSAGES_COLLECTION', 'inbox_messages_collection')
    MONGO_INBOX_CONVERSATIONS_COLLECTION = os.getenv('MONGO_INBOX_CONVERSATIONS_COLLECTION', 'inbox_conversations_collection')
    MONGO_DRAFT_MESSAGES_COLLECTION = os.getenv('MONGO_DRAFT_MESSAGES_COLLECTION', 'draft_messages_collection')
    MONGO_SENT_MESSAGES_COLLECTION = os.getenv('MONGO_SENT_MESSAGES_COLLECTION', 'sent_messages_collection')
    MONGO_PREFERENCES_COLLECTION = os.getenv('MONGO_PREFERENCES_COLLECTION', 'user_preferences')

    # Google OAuth 2.0 Configuration (for Gmail Add-on & Pub/Sub)
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
    GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')
    GMAIL_PUB_SUB_TOPIC = os.getenv('GMAIL_PUB_SUB_TOPIC')
    GCP_PROJECT_ID = os.getenv('GCP_PROJECT_ID')
    GOOGLE_SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/pubsub',
        'https://www.googleapis.com/auth/userinfo.email',
        'https://www.googleapis.com/auth/gmail.compose',
        'openid' # Crucial for 'Scope has changed' error
    ]

    # Microsoft Graph API Configuration (for Outlook Add-in & Webhooks)
    MS_GRAPH_CLIENT_ID = os.getenv('MS_GRAPH_CLIENT_ID')
    MS_GRAPH_CLIENT_SECRET = os.getenv('MS_GRAPH_CLIENT_SECRET')
    MS_GRAPH_REDIRECT_URI = os.getenv('MS_GRAPH_REDIRECT_URI')
    MS_GRAPH_TENANT_ID = os.getenv('MS_GRAPH_TENANT_ID')
    MS_GRAPH_SCOPES = os.getenv('MS_GRAPH_SCOPES', 'Mail.Read Mail.ReadBasic.All Mail.Send Mail.ReadWrite').split()
    MS_GRAPH_AUTHORITY = "https://login.microsoftonline.com/common" # Common endpoint for multi-tenant apps
    MS_GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"
    MS_GRAPH_WEBHOOK_NOTIFICATION_URL = os.getenv('MS_GRAPH_WEBHOOK_NOTIFICATION_URL') # URL where Graph sends notifications
    MS_GRAPH_WEBHOOK_EXPIRATION_MINUTES = 10070 # Max value for subscription (42300 minutes = 29 days)
    MS_GRAPH_CLIENT_STATE = "jdhfg78e5t34ktjr09erjte"
    CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    # CELERY_ACCEPT_CONTENT = ['json']
    # CELERY_TASK_SERIALIZER = 'json'
    # CELERY_RESULT_SERIALIZER = 'json'
    # CELERY_TIMEZONE = 'UTC'
    CELERY_INCLUDE = ['workers.tasks']
    # CELERY_WORKER_POOL = os.getenv('CELERY_WORKER_POOL', 'fork')

    # Validate essential environment variables
    REQUIRED_VARS = [
        'SECRET_KEY', 'GEMINI_API_KEY', 'MONGO_URI', 'MONGO_DB_NAME',
        'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI', 'GMAIL_PUB_SUB_TOPIC', 'GCP_PROJECT_ID',
        'MS_GRAPH_CLIENT_ID', 'MS_GRAPH_CLIENT_SECRET', 'MS_GRAPH_REDIRECT_URI', 'MS_GRAPH_WEBHOOK_NOTIFICATION_URL'
    ]

    for var in REQUIRED_VARS:
        if not os.getenv(var):
            print(f"Error: Environment variable '{var}' is not set in .env")
            # In production, you might want to raise an exception or exit.
            # For development, just print a warning.
