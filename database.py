# database.py
from pymongo import MongoClient, ASCENDING
from config import Config # Import the CONFIG dictionary from config.py

client = None
db = None
users_collection = None
inbox_messages_collection = None
inbox_conversations_collection = None
draft_messages_collection = None
preferences_collection = None
sent_messages_collection = None

def init_db():
    """Initializes the MongoDB connection and global collection objects."""
    global client, db, users_collection, inbox_messages_collection, draft_messages_collection, sent_messages_collection, preferences_collection, inbox_conversations_collection
    try:
        client = MongoClient(Config.MONGO_URI)
        db = client[Config.MONGO_DB_NAME]
        users_collection = db[Config.MONGO_USERS_COLLECTION]
        inbox_messages_collection = db[Config.MONGO_INBOX_MESSAGES_COLLECTION]
        inbox_conversations_collection = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
        draft_messages_collection = db[Config.MONGO_DRAFT_MESSAGES_COLLECTION]
        sent_messages_collection = db[Config.MONGO_SENT_MESSAGES_COLLECTION]
        preferences_collection = db[Config.MONGO_PREFERENCES_COLLECTION]
        inbox_conversations_collection.create_index([("conv_id", ASCENDING)], unique=True)
        # print(preferences_collection)
        print("Connected to MongoDB successfully!")
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        # Re-raise the exception to propagate the error up to app.py
        raise
