# database.py
from pymongo import AsyncMongoClient
from config import Config # Import the CONFIG dictionary from config.py

client = AsyncMongoClient(Config.MONGO_URI)
db = client[Config.MONGO_DB_NAME]
users_collection_async = db[Config.MONGO_USERS_COLLECTION]
inbox_conversations_collection_async = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
preferences_collection_async = db[Config.MONGO_PREFERENCES_COLLECTION]


# def get_db_client():
#     """
#     Returns a Motor client. It reuses the client if it already exists in the application context.
#     """
#     global _async_client
#     if _async_client is None:
#         _async_client = AsyncMongoClient(Config.MONGO_URI)
#     return _async_client

# def get_async_db():
#     """
#     Get the database from the global context, ensuring it's on the correct event loop.
#     """
#     if 'async_db' not in g:
#         client = get_db_client()
#         # You can access the database here and store it in the global context
#         g.async_db = client[Config.MONGO_DB_NAME] 
#     return g.async_db
# def init_db():
#     """Initializes the MongoDB connection and global collection objects."""
#     global client, db, users_collection, inbox_messages_collection, draft_messages_collection, sent_messages_collection, preferences_collection, inbox_conversations_collection
#     try:
#         client = MongoClient(Config.MONGO_URI)
#         db = client[Config.MONGO_DB_NAME]
#         users_collection = db[Config.MONGO_USERS_COLLECTION]
#         inbox_messages_collection = db[Config.MONGO_INBOX_MESSAGES_COLLECTION]
#         inbox_conversations_collection = db[Config.MONGO_INBOX_CONVERSATIONS_COLLECTION]
#         draft_messages_collection = db[Config.MONGO_DRAFT_MESSAGES_COLLECTION]
#         sent_messages_collection = db[Config.MONGO_SENT_MESSAGES_COLLECTION]
#         preferences_collection = db[Config.MONGO_PREFERENCES_COLLECTION]
#         inbox_conversations_collection.create_index([("conv_id", ASCENDING)], unique=True)
#         # print(preferences_collection)
#         print("Connected to MongoDB successfully!")
#     except Exception as e:
#         print(f"Error connecting to MongoDB: {e}")
#         # Re-raise the exception to propagate the error up to app.py
#         raise
