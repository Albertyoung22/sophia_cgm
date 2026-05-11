from flask import Flask, request, jsonify, render_template
import hashlib
import os
from datetime import datetime, timezone
import requests
import database
import json

app = Flask(__name__)
database.init_db()

# 預設密碼與 Token
API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=")

# 主動發送廣播訊息
def send_line_message(text):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": text}]}
    try: requests.post(url, headers=headers, json=data, timeout=5)
    except: pass

# 回覆特定訊息
def reply_line_message(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    requests.post(url, headers=headers, json=data, timeout=5)

@app.before_request
def log_all():
    if not request.path.startswith('/static'):
        print(f"📡 [連線請求] {request.method} {request.path}")

# ---------------------------------------------------------
# LINE Webhook (處理使用者輸入)
# ---------------------------------------------------------
@app.route("/callback", methods=['POST'])
def line_callback():
    body = request.get_json()
    print(f"📩 收到 Webhook 事件: {json.dumps(body)}")
    try:
        for event in body.get('events', []):
            if event['type'] == 'message' and event['message']['type'] == 'text':
                user_msg = event['message']['text'].strip()
                reply_token = event['replyToken']
                print(f"💬 使用者訊息: {user_msg}")
                
                if user_msg == "血糖" or user_msg.lower() == "bg":
                    db = database.get_db()
                    entry = db.entries.find_one(sort=[("dateString", -1)])
                    if entry:
                        msg = f"【即時查詢】\n數值: {entry['sgv']}\n趨勢: {entry['direction']}\n時間: {entry['dateString']}"
                        print(f"✅ 找到數據: {entry['sgv']}")
                    else:
                        msg = "資料庫目前沒有任何血糖紀錄。"
                        print("⚠️ 資料庫是空的")
                    reply_line_message(reply_token, msg)
                    print("📤 已送出回覆")
    except Exception as e:
        print(f"❌ Webhook 錯誤: {e}")
    return 'OK'

# ---------------------------------------------------------
# Nightscout 標準端點
# ---------------------------------------------------------
@app.route('/api/v1/status', methods=['GET'])
@app.route('/api/v1/status.json', methods=['GET'])
def get_status():
    now = datetime.now(timezone.utc)
    return jsonify({
        "status": "ok", "name": "nightscout", "version": "15.0.3",
        "serverTime": now.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        "serverTimeEpoch": int(now.timestamp() * 1000),
        "authorized": True, "apiEnabled": True,
        "settings": {
            "units": "mg/dL", "timeFormat": 24,
            "thresholds": {"bgHigh": 260, "bgTargetTop": 180, "bgTargetBottom": 80, "bgLow": 55},
            "enable": ["careportal", "rawbg", "iob"]
        }
    })

@app.route('/api/v1/verifyauth', methods=['GET'])
def verify_auth():
    return jsonify({
        "status": 200,
        "message": {"canRead": True, "canWrite": True, "isAdmin": True, "message": "OK", "rolefound": "FOUND", "permissions": "ROLE"}
    })

@app.route('/api/v1/entries', methods=['GET', 'POST'])
@app.route('/api/v1/entries.json', methods=['GET', 'POST'])
def entries_api():
    db = database.get_db()
    if request.method == 'GET':
        entry = db.entries.find_one(sort=[("dateString", -1)])
        if entry:
            entry['_id'] = str(entry['_id'])
            return jsonify([entry])
        return jsonify([])
    
    data = request.get_json(silent=True) or {}
    items = [data] if isinstance(data, dict) else data
    process_entries(items)
    return jsonify({"status": "success"}), 200

# 模擬 Dexcom 端點
@app.route('/ShareV1/publisher/postPublisherLatestPublisherId', methods=['POST'])
def dexcom_post():
    data = request.get_json(silent=True) or {}
    process_entries([data])
    return "OK", 200

def process_entries(items):
    if not items: return
    db = database.get_db()
    latest_entry = None
    max_date = ""

    for entry in items:
        val = entry.get('sgv') or entry.get('mbg') or entry.get('glucose') or entry.get('value') or entry.get('Value')
        if not val: continue
        dir_str = entry.get('direction') or entry.get('Direction') or 'Flat'
        date_str = entry.get('dateString') or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        try: val = int(val)
        except: pass

        mongo_entry = {
            "sgv": val, "direction": dir_str, "dateString": date_str, "device": "App", "type": "sgv",
            "date": int(datetime.fromisoformat(date_str.replace('Z', '+00:00')).timestamp() * 1000) if 'T' in date_str else int(datetime.now().timestamp() * 1000)
        }
        db.entries.insert_one(mongo_entry)
        if date_str > max_date:
            max_date = date_str
            latest_entry = {"val": val, "dir": dir_str, "date": date_str}

    if latest_entry:
        msg = f"【自動推播】\n數值: {latest_entry['val']}\n趨勢: {latest_entry['dir']}\n時間: {latest_entry['date']}"
        send_line_message(msg)
        print(f"✅ 處理完成並推播: {latest_entry['val']}")

@app.route('/')
def home():
    db = database.get_db()
    cursor = db.entries.find(sort=[("dateString", -1)]).limit(50)
    entries = []
    for doc in cursor:
        doc['_id'] = str(doc['_id'])
        doc['bg_value'] = doc.get('sgv', 0)
        doc['date_str'] = doc.get('dateString', '')
        entries.append(doc)
    return render_template('index.html', entries=entries, chart_data=list(reversed(entries)))

@app.route('/manifest.json')
def serve_manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def serve_sw():
    return app.send_static_file('sw.js')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
