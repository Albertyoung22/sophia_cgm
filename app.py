from flask import Flask, request, jsonify, render_template
import hashlib
import os
from datetime import datetime
import time
import subprocess
import requests
import database

app = Flask(__name__)
database.init_db()

API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")
EXPECTED_HASH = hashlib.sha1(API_SECRET.encode('utf-8')).hexdigest()

def get_client_secret():
    return request.headers.get('api-secret') or request.headers.get('API-SECRET') or request.args.get('token')

def is_authorized():
    secret = get_client_secret()
    if not secret: return False
    return secret == EXPECTED_HASH or secret == API_SECRET

def send_line_message(text):
    token = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=")
    if not token: return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": text}]}
    try: requests.post(url, headers=headers, json=data)
    except: pass

@app.route('/sw.js')
def sw(): return app.send_static_file('sw.js')

@app.route('/manifest.json')
def manifest(): return app.send_static_file('manifest.json')

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST': return "OK", 200
    conn = database.get_db_connection()
    rows = conn.execute('SELECT * FROM entries ORDER BY dateString DESC LIMIT 50').fetchall()
    entries = []
    for r in rows:
        d = dict(r)
        d['bg_value'] = d['sgv']
        d['date_str'] = d['dateString']
        entries.append(d)
    conn.close()
    return render_template('index.html', entries=entries, chart_data=list(reversed(entries)), view_mode=request.args.get('view', 'main'))

@app.route('/api/v1/status', methods=['GET'])
def get_status():
    return jsonify({
        "status": "ok",
        "name": "Nightscout",
        "version": "14.2.2",
        "authorized": is_authorized(),
        "settings": {"units": "mg/dL", "timeFormat": 24}
    })

@app.route('/api/v1/verifyauth', methods=['GET'])
def verify_auth():
    auth_ok = is_authorized()
    return jsonify({"status": "ok", "authorized": auth_ok, "api_secret_hash": EXPECTED_HASH})

@app.route('/api/v1/profile', methods=['GET'])
def get_profile():
    # 回傳一個標準的模擬 Profile
    return jsonify([{"startDate": "2020-01-01T00:00:00.000Z", "defaultProfile": "Default", "store": {"Default": {"timezone": "Asia/Taipei", "units": "mg/dL", "targets_high": [{"time": "00:00", "value": 180}], "targets_low": [{"time": "00:00", "value": 70}]}}}])

@app.route('/api/v1/entries', methods=['POST'])
@app.route('/api/v1/entries.json', methods=['POST'])
def receive_entries():
    if not is_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data: return jsonify({"error": "No Data"}), 400
    items = [data] if isinstance(data, dict) else data
    conn = database.get_db_connection()
    c = conn.cursor()
    for entry in items:
        val = entry.get('sgv') or entry.get('mbg') or entry.get('glucose') or entry.get('value')
        if not val: continue
        dir_str = entry.get('direction') or 'Flat'
        date_str = entry.get('dateString') or entry.get('date_string') or datetime.now().isoformat()
        c.execute('INSERT INTO entries (sgv, direction, dateString, device) VALUES (?, ?, ?, ?)', (val, dir_str, date_str, 'App'))
        try:
            arrows = {'DoubleUp': '⇈', 'SingleUp': '↑', 'FortyFiveUp': '↗', 'Flat': '→', 'FortyFiveDown': '↘', 'SingleDown': '↓', 'DoubleDown': '⇊'}
            msg = f"【血糖紀錄】\n數值: {val} {arrows.get(dir_str, dir_str)}\n時間: {date_str.split('T')[1][:5] if 'T' in date_str else date_str}"
            send_line_message(msg)
        except: pass
    conn.commit()
    conn.close()
    return jsonify({"status": "success"}), 200

@app.route('/api/v1/treatments', methods=['GET', 'POST'])
def treatments(): return jsonify([])

@app.route('/api/v1/devicestatus', methods=['GET', 'POST'])
def devicestatus(): return jsonify([])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
