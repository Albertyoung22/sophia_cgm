from flask import Flask, request, jsonify, render_template
import hashlib
import os
from datetime import datetime, timezone, timedelta
import requests
import database
import json

app = Flask(__name__)
database.init_db()

# 預設密碼與 Token
API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=").strip()

# 主動發送廣播訊息
def send_line_message(text):
    if not LINE_ACCESS_TOKEN: 
        print("[LINE] Skip: No Token")
        return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": text}]}
    import sys
    try: 
        response = requests.post(url, headers=headers, json=data, timeout=5)
        print(f"[LINE] Broadcast status: {response.status_code}")
        sys.stdout.flush()
    except Exception as e:
        print(f"[LINE] Error: {e}")
        sys.stdout.flush()

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
        import sys
        body = ""
        if request.method == 'POST':
            try: body = f" | Body: {request.get_data(as_text=True)[:100]}..."
            except: pass
        print(f"[Connection] {request.method} {request.path}{body}")
        sys.stdout.flush()

# ---------------------------------------------------------
# LINE Webhook (處理使用者輸入)
# ---------------------------------------------------------
@app.route("/callback", methods=['POST'])
def line_callback():
    body = request.get_json()
    print(f"[Webhook] Received: {json.dumps(body)}")
    try:
        for event in body.get('events', []):
            if event['type'] == 'message' and event['message']['type'] == 'text':
                user_msg = event['message']['text'].strip()
                reply_token = event['replyToken']
                print(f"[Message] User: {user_msg}")
                
                if user_msg == "血糖" or user_msg.lower() == "bg":
                    db = database.get_db()
                    entry = db.entries.find_one(sort=[("dateString", -1)])
                    if entry:
                        # 轉換為在地時間 (UTC+8)
                        try:
                            dt_utc = datetime.fromisoformat(entry['dateString'].replace('Z', '+00:00'))
                            local_time = dt_utc.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M')
                        except:
                            local_time = entry['dateString']
                        msg = f"【即時查詢】\n數值: {entry['sgv']}\n趨勢: {entry['direction']}\n時間: {local_time}"
                        reply_line_message(reply_token, msg)
                        print(f"✅ 已回覆數據: {entry['sgv']} at {local_time}")
                    else:
                        msg = "資料庫目前沒有任何血糖紀錄。"
                        reply_line_message(reply_token, msg)
                        print("⚠️ 資料庫是空的，已回覆提示")
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
        try: val = int(val)
        except: pass

        # 處理日期字串與時區
        now_utc = datetime.now(timezone.utc)
        if 'dateString' in entry:
            try:
                # 嘗試解析進來的時間
                dt_in = datetime.fromisoformat(entry['dateString'].replace('Z', '+00:00'))
                # 如果進來的時間比現在快超過 1 分鐘，則強制使用伺服器時間 (修正手機時間不準的問題)
                if dt_in > now_utc + timedelta(minutes=1):
                    print(f"[Time Fix] Incoming time {entry['dateString']} is in future! Adjusting to server time.")
                    date_str = now_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                else:
                    date_str = entry['dateString']
            except:
                date_str = entry.get('dateString') or now_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        else:
            date_str = now_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')

        mongo_entry = {
            "sgv": val, "direction": dir_str, "dateString": date_str, "device": "App", "type": "sgv",
            "date": int(datetime.fromisoformat(date_str.replace('Z', '+00:00')).timestamp() * 1000) if 'T' in date_str else int(datetime.now().timestamp() * 1000)
        }
        # 檢查是否已存在，避免重複寫入與重複推播
        exists = db.entries.find_one({"dateString": date_str, "sgv": val})
        if not exists:
            print(f"[Database] New entry detected: {val} mg/dL at {date_str}")
            db.entries.insert_one(mongo_entry)
            if date_str > max_date:
                max_date = date_str
                latest_entry = {"val": val, "dir": dir_str, "date": date_str}
        else:
            print(f"[Database] Duplicate ignored: {val} mg/dL at {date_str}")

    import sys
    sys.stdout.flush()

    if latest_entry:
        # 強制使用台灣時間 (UTC+8) 顯示在 LINE 上
        local_now = datetime.now(timezone(timedelta(hours=8)))
        local_time = local_now.strftime('%H:%M')

        msg = f"【目前血糖】\n數值: {latest_entry['val']}\n趨勢: {latest_entry['dir']}\n時間: {local_time}"
        send_line_message(msg)
        print(f"[Success] Broadcast triggered: {latest_entry['val']} at {local_time}")
        sys.stdout.flush()
    else:
        print("[Process] No new entries found to broadcast.")
        sys.stdout.flush()

@app.route('/')
def home():
    db = database.get_db()
    cursor = db.entries.find(sort=[("dateString", -1)]).limit(300)
    entries = []
    for doc in cursor:
        doc['_id'] = str(doc['_id'])
        # 確保 sgv 有值，否則預設為 0
        doc['bg_value'] = doc.get('sgv') or 0
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
