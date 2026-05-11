import os
import requests
import json
import database

def send_line_message(text):
    token = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=")
    if not token: 
        print("LINE Token missing")
        return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": text}]}
    try:
        response = requests.post(url, headers=headers, json=data, timeout=5)
        print(f"LINE Status: {response.status_code}")
        print(f"LINE Response: {response.text}")
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    # Get latest data from MongoDB
    db = database.get_db()
    entry = db.entries.find_one(sort=[("dateString", -1)])
    
    if entry:
        msg = f"【手動測試】\n數值: {entry['sgv']}\n趨勢: {entry['direction']}\n時間: {entry['dateString']}"
        print(f"Sending message: {msg}")
        send_line_message(msg)
    else:
        print("No entries found in database.")
