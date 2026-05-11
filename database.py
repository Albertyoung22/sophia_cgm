import os
from pymongo import MongoClient
from datetime import datetime

# 從環境變數讀取 MongoDB 連接字串，預設為本地端 (測試用)
MONGO_URI = os.environ.get("MONGO_CONNECTION") or os.environ.get("MONGO_URI") or "mongodb+srv://youngtunchou:nightscout12345@cluster0.pippenm.mongodb.net/?appName=Cluster0"
DB_NAME = "nightscout"

client = None
db = None

def get_db():
    global client, db
    if db is None:
        client = MongoClient(MONGO_URI)
        # 從 URI 中提取資料庫名稱，如果沒有則用預設值
        try:
            db = client.get_default_database()
        except:
            db = None
            
        if db is None:
            db = client[DB_NAME]
    return db

def init_db():
    # MongoDB 不需要像 SQL 那樣預先 Create Table，寫入時會自動建立
    # 但我們可以預先建立索引 (Index) 來加速查詢
    db = get_db()
    db.entries.create_index([("dateString", -1)])
    db.treatments.create_index([("created_at", -1)])
    db.devicestatus.create_index([("created_at", -1)])
    print("MongoDB indexes initialized.")

def get_entries_collection():
    return get_db().entries

def get_treatments_collection():
    return get_db().treatments

def get_devicestatus_collection():
    return get_db().devicestatus

# 為了保持與舊程式碼的部分相容性 (雖然邏輯會變)
def get_db_connection():
    return get_db()

if __name__ == '__main__':
    init_db()
