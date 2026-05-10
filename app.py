from flask import Flask, request, jsonify, render_template
import hashlib
import os
from datetime import datetime, timezone
import requests
import database
import json

app = Flask(__name__)
database.init_db()

# 預設密碼
API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")
EXPECTED_HASH = hashlib.sha1(API_SECRET.encode('utf-8')).hexdigest()

def send_line_message(text):
    token = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=")
    if not token: return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": text}]}
    try: requests.post(url, headers=headers, json=data, timeout=5)
    except: pass

@app.before_request
def log_all():
    if not request.path.startswith('/static'):
        print(f"📡 [連線請求] {request.method} {request.path}")
        if request.method == 'POST':
            print(f"📦 [POST 內容] {request.get_data(as_text=True)}")

# ---------------------------------------------------------
# 1. 寬容的 Nightscout 端點 (原本的模式)
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
            "units": "mg/dL", 
            "timeFormat": 24,
            "thresholds": {"bgHigh": 260, "bgTargetTop": 180, "bgTargetBottom": 80, "bgLow": 55},
            "enable": ["careportal", "rawbg", "iob"]
        }
    })

@app.route('/api/v1/verifyauth', methods=['GET'])
def verify_auth():
    return jsonify({
        "status": 200,
        "message": {
            "canRead": True, "canWrite": True, "isAdmin": True,
            "message": "OK", "rolefound": "FOUND", "permissions": "ROLE"
        }
    })

@app.route('/api/v1/entries', methods=['GET', 'POST'])
@app.route('/api/v1/entries.json', methods=['GET', 'POST'])
@app.route('/api/v1/entries/', methods=['GET', 'POST'])
def entries_api():
    if request.method == 'GET':
        conn = database.get_db_connection()
        rows = conn.execute('SELECT sgv, direction, dateString FROM entries ORDER BY dateString DESC LIMIT 1').fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    
    # 接收資料邏輯
    data = request.get_json(silent=True) or {}
    items = [data] if isinstance(data, dict) else data
    process_entries(items)
    return jsonify({"status": "success"}), 200

# ---------------------------------------------------------
# 2. 模擬 Dexcom Share 端點 (歐態 App 常用的備選方案)
# ---------------------------------------------------------
@app.route('/ShareV1/login/loginPublisherLatestPublisherId', methods=['POST'])
def dexcom_login():
    return jsonify("00000000-0000-0000-0000-000000000000") # 模擬 Session ID

@app.route('/ShareV1/publisher/latestPublisherId', methods=['POST'])
def dexcom_latest():
    return jsonify("00000000-0000-0000-0000-000000000000")

@app.route('/ShareV1/publisher/postPublisherLatestPublisherId', methods=['POST'])
def dexcom_post():
    data = request.get_json(silent=True) or {}
    process_entries([data])
    return "OK", 200

# ---------------------------------------------------------
# 核心處理邏輯
# ---------------------------------------------------------
def process_entries(items):
    if not items: return
    conn = database.get_db_connection()
    c = conn.cursor()
    for entry in items:
        # 廣泛搜尋可能包含血糖值的欄位
        val = entry.get('sgv') or entry.get('mbg') or entry.get('glucose') or entry.get('value') or entry.get('Value')
        if not val: continue
        dir_str = entry.get('direction') or entry.get('Direction') or 'Flat'
        date_str = entry.get('dateString') or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        c.execute('INSERT INTO entries (sgv, direction, dateString, device) VALUES (?, ?, ?, ?)', (val, dir_str, date_str, 'App'))
        try:
            msg = f"【血糖紀錄】\n數值: {val}\n趨勢: {dir_str}\n時間: {date_str}"
            send_line_message(msg)
        except: pass
    conn.commit()
    conn.close()
    print(f"✅ 成功處理資料")

# ---------------------------------------------------------
# UI 網頁
# ---------------------------------------------------------
@app.route('/')
def home():
    conn = database.get_db_connection()
    rows = conn.execute('SELECT * FROM entries ORDER BY dateString DESC LIMIT 50').fetchall()
    entries = [dict(r) for r in rows]
    for d in entries:
        d['bg_value'] = d['sgv']
        d['date_str'] = d['dateString']
    conn.close()
    return render_template('index.html', entries=entries, chart_data=list(reversed(entries)))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
