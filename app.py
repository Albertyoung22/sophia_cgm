from flask import Flask, request, jsonify, render_template
import hashlib
import os
from datetime import datetime, timezone
import time
import subprocess
import requests
import database

app = Flask(__name__)
database.init_db()

def bootstrap_db():
    conn = database.get_db_connection()
    count = conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
    if count == 0:
        conn.execute("INSERT INTO entries (sgv, direction, dateString, device) VALUES (100, 'Flat', ?, 'System')", (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
    conn.close()

bootstrap_db()

API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")
EXPECTED_HASH = hashlib.sha1(API_SECRET.encode('utf-8')).hexdigest()

def get_client_secret():
    # 支援所有可能的大小寫組合
    return (request.headers.get('api-secret') or 
            request.headers.get('Api-Secret') or 
            request.headers.get('API-SECRET') or 
            request.headers.get('x-nightscout-token') or
            request.args.get('token') or 
            request.args.get('api_secret'))

def is_authorized():
    secret = get_client_secret()
    if not secret: return False
    # 確認 App 傳來的是明文或是雜湊值
    return secret.lower() == EXPECTED_HASH.lower() or secret == API_SECRET

@app.after_request
def add_nightscout_headers(response):
    response.headers['X-Nightscout-API-Version'] = '14.2.2'
    return response

@app.before_request
def log_request_info():
    if not request.path.startswith('/static'):
        print(f"📡 [連線請求] {request.method} {request.path}")
        headers = {k: v for k, v in request.headers.items() if k.lower() in ['api-secret', 'authorization', 'token', 'user-agent']}
        print(f"🔑 [標頭內容] {headers}")

@app.route('/sw.js')
def sw(): return app.send_static_file('sw.js')

@app.route('/manifest.json')
def manifest(): return app.send_static_file('manifest.json')

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST': return "OK", 200
    conn = database.get_db_connection()
    rows = conn.execute('SELECT * FROM entries ORDER BY dateString DESC LIMIT 50').fetchall()
    entries = [dict(r) for r in rows]
    for d in entries:
        d['bg_value'] = d['sgv']
        d['date_str'] = d['dateString']
    conn.close()
    return render_template('index.html', entries=entries, chart_data=list(reversed(entries)), view_mode=request.args.get('view', 'main'))

@app.route('/api/v1/status', methods=['GET'])
@app.route('/api/v1/status.json', methods=['GET'])
def get_status():
    now = datetime.now(timezone.utc)
    return jsonify({
        "status": "ok",
        "name": "Nightscout",
        "version": "14.2.2",
        "serverTime": now.isoformat().replace('+00:00', 'Z'),
        "serverTimeEpoch": int(now.timestamp() * 1000),
        "apiEnabled": True,
        "authorized": is_authorized(),
        "settings": {"units": "mg/dL", "timeFormat": 24, "nightMode": True, "editMode": True}
    })

@app.route('/api/v1/verifyauth', methods=['GET'])
def verify_auth():
    auth_ok = is_authorized()
    # 完全模擬正版 Nightscout 的回傳結構 (精簡版)
    return jsonify({
        "canRead": True,
        "canWrite": True,
        "isAdmin": True,
        "message": "OK" if auth_ok else "UNAUTHORIZED",
        "rolefound": "FOUND" if auth_ok else "NOTFOUND",
        "permissions": "ROLE" if auth_ok else "DEFAULT"
    })

@app.route('/api/v1/experiments/test', methods=['GET'])
def experiments_test(): return jsonify({"status": "ok", "authorized": is_authorized()})

@app.route('/api/v1/profile', methods=['GET'])
@app.route('/api/v1/profile.json', methods=['GET'])
def get_profile():
    return jsonify([{"startDate": "2020-01-01T00:00:00.000Z", "defaultProfile": "Default", "store": {"Default": {"timezone": "Asia/Taipei", "units": "mg/dL"}}}])

@app.route('/api/v1/entries', methods=['GET', 'POST'])
@app.route('/api/v1/entries/', methods=['GET', 'POST'])
@app.route('/api/v1/entries.json', methods=['GET', 'POST'])
def entries_api():
    if request.method == 'GET':
        conn = database.get_db_connection()
        rows = conn.execute('SELECT sgv, direction, dateString FROM entries ORDER BY dateString DESC LIMIT 10').fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    
    if not is_authorized(): return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data: return jsonify({"error": "No Data"}), 400
    items = [data] if isinstance(data, dict) else data
    conn = database.get_db_connection()
    c = conn.cursor()
    for entry in items:
        val = entry.get('sgv') or entry.get('mbg') or entry.get('glucose') or entry.get('value')
        if not val: continue
        dir_str = entry.get('direction') or 'Flat'
        date_str = entry.get('dateString') or entry.get('date_string') or datetime.now(timezone.utc).isoformat()
        c.execute('INSERT INTO entries (sgv, direction, dateString, device) VALUES (?, ?, ?, ?)', (val, dir_str, date_str, 'App'))
        try:
            arrows = {'DoubleUp': '⇈', 'SingleUp': '↑', 'FortyFiveUp': '↗', 'Flat': '→', 'FortyFiveDown': '↘', 'SingleDown': '↓', 'DoubleDown': '⇊'}
            msg = f"【血糖紀錄】\n數值: {val} {arrows.get(dir_str, dir_str)}\n時間: {date_str}"
            send_line_message(msg)
        except: pass
    conn.commit()
    conn.close()
    print(f"✅ 成功接收 {len(items)} 筆資料")
    return jsonify({"status": "success"}), 200

@app.route('/api/v1/treatments', methods=['GET', 'POST'])
@app.route('/api/v1/devicestatus', methods=['GET', 'POST'])
def empty_api(): return jsonify([])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
