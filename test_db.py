import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv() # Load your .env file

MONGO_URI = os.getenv('MONGO_URI')
MONGO_DB_NAME = os.getenv('MONGO_DB_NAME')

if not MONGO_URI or not MONGO_DB_NAME:
    print("MONGO_URI or MONGO_DB_NAME not set in .env")
    exit(1)

try:
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB_NAME]
    # Try to list collections to confirm connection
    print(f"Successfully connected to MongoDB! Collections: {db.list_collection_names()}")
    client.close()
except Exception as e:
    print(f"Connection failed: {e}")