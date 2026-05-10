from pymongo import MongoClient
import json

MONGO_URI = "mongodb+srv://youngtunchou:nightscout12345@cluster0.pippenm.mongodb.net/?appName=Cluster0"

def check_latest():
    client = MongoClient(MONGO_URI)
    db = client['nightscout']
    # 抓取最後 3 筆資料
    latest = db.entries.find(sort=[("dateString", -1)]).limit(3)
    
    print("--- 雲端資料庫最新的 3 筆紀錄 ---")
    for doc in latest:
        print(f"時間: {doc.get('dateString')}, 數值: {doc.get('sgv')}, 趨勢: {doc.get('direction')}")

if __name__ == "__main__":
    check_latest()
