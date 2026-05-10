import sqlite3
import os
from pymongo import MongoClient
from datetime import datetime

# === 設定區 ===
MONGO_URI = "mongodb+srv://youngtunchou:nightscout12345@cluster0.pippenm.mongodb.net/?appName=Cluster0"
SQLITE_FILE = 'cgm_data.db'

def migrate():
    if not os.path.exists(SQLITE_FILE):
        print(f"File not found: {SQLITE_FILE}")
        return

    print(f"Connecting to MongoDB...")
    try:
        client = MongoClient(MONGO_URI)
        # 這裡改為直接指定資料庫名稱，避免 get_default_database 報錯
        db = client['nightscout']
        collection = db.entries
        print(f"MongoDB connection successful")
    except Exception as e:
        print(f"MongoDB connection failed: {e}")
        return

    print(f"Reading SQLite data...")
    conn = sqlite3.connect(SQLITE_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM entries').fetchall()
    
    if not rows:
        print("No data in SQLite.")
        conn.close()
        return

    print(f"Migrating {len(rows)} records...")
    
    migrated_count = 0
    for row in rows:
        date_str = row['dateString']
        try:
            if 'T' in date_str:
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                dt = datetime.now()
            epoch_date = int(dt.timestamp() * 1000)
        except:
            epoch_date = int(datetime.now().timestamp() * 1000)

        mongo_doc = {
            "sgv": row['sgv'],
            "direction": row['direction'],
            "dateString": date_str,
            "device": row['device'] or "App",
            "type": "sgv",
            "date": epoch_date
        }
        
        # 檢查是否已存在
        if not collection.find_one({"dateString": date_str, "sgv": row['sgv']}):
            collection.insert_one(mongo_doc)
            migrated_count += 1
            if migrated_count % 100 == 0:
                print(f"Migrated {migrated_count} records...")

    conn.close()
    print(f"Migration completed! {migrated_count} records processed.")

if __name__ == '__main__':
    migrate()
