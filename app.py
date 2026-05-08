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
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=")

def send_line_message(text):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messages": [{"type": "text", "text": text}]}
    try:
        requests.post(url, headers=headers, json=data)
        print("✅ LINE通知已發送")
    except Exception as e: print(f"LINE error: {e}")

def send_pushover_message(text):
    uk = os.environ.get("PUSHOVER_USER_KEY")
    tk = os.environ.get("PUSHOVER_API_TOKEN")
    if uk and tk:
        try: requests.post("https://api.pushover.net/1/messages.json", data={"token": tk, "user": uk, "message": text})
        except: pass

def send_ifttt_message(text):
    url = os.environ.get("IFTTT_WEBHOOK_URL")
    if url:
        try: requests.post(url, json={"value1": text})
        except: pass

latest_alert = None

@app.route('/sw.js')
def sw(): return app.send_static_file('sw.js')

@app.route('/manifest.json')
def manifest(): return app.send_static_file('manifest.json')

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST': return "OK", 200
    conn = database.get_db_connection()
    entries_rows = conn.execute('SELECT * FROM entries ORDER BY dateString DESC LIMIT 50').fetchall()
    latest_cgm_entries = []
    for row in entries_rows:
        d = dict(row)
        d['bg_value'] = d['sgv']
        d['date_str'] = d['dateString']
        latest_cgm_entries.append(d)
    conn.close()
    chart_data = list(reversed(latest_cgm_entries))
    view_mode = request.args.get('view', 'main')
    return render_template('index.html', entries=latest_cgm_entries, chart_data=chart_data, alert=latest_alert, view_mode=view_mode)

@app.route('/api/v1/status', methods=['GET'])
def get_status():
    return jsonify({"status": "ok", "name": "Nightscout", "version": "14.2.2"})

@app.route('/api/v1/verifyauth', methods=['GET'])
def verify_auth():
    return jsonify({"status": "ok", "authorized": True})

@app.route('/api/v1/entries', methods=['POST'])
def receive_entries():
    client_secret = request.headers.get('api-secret')
    if not client_secret or (client_secret != EXPECTED_HASH and client_secret != API_SECRET):
        print(f"⚠️ 授權失敗！收到: {client_secret}")
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.get_json()
    if not data: return jsonify({"error": "No Data"}), 400
    entries_list = [data] if isinstance(data, dict) else data
    
    print(f"✅ 收到 {len(entries_list)} 筆資料")
    global latest_alert
    conn = database.get_db_connection()
    c = conn.cursor()
    
    for entry in entries_list:
        bg_value = entry.get('sgv') or entry.get('mbg') or entry.get('glucose') or entry.get('value')
        direction = entry.get('direction') or entry.get('trend_direction') or 'Flat'
        date_str = entry.get('dateString') or entry.get('date_string') or entry.get('display_time') or datetime.now().isoformat()
        device = entry.get('device') or 'App'
        
        c.execute('INSERT INTO entries (sgv, direction, dateString, device) VALUES (?, ?, ?, ?)', (bg_value, direction, date_str, device))
        
        try:
            val = int(bg_value)
            arrows = {'DoubleUp': '⇈', 'SingleUp': '↑', 'FortyFiveUp': '↗', 'Flat': '→', 'FortyFiveDown': '↘', 'SingleDown': '↓', 'DoubleDown': '⇊'}
            arrow = arrows.get(direction, direction)
            line_text = f"【血糖報告】\n數值: {val} {arrow}\n時間: {date_str.split('T')[1][:5] if 'T' in date_str else date_str}"
            
            if val > 180 or val < 70:
                alert_text = f"{'⚠️ 偏高' if val > 180 else '🚨 偏低'}! ({val})"
                os.makedirs("static", exist_ok=True)
                subprocess.run(['edge-tts', '--voice', 'zh-TW-HsiaoChenNeural', '--text', alert_text, '--write-media', 'static/alert.mp3'], check=False)
                latest_alert = {"text": alert_text, "time": time.time(), "bg_value": val}
                line_text = f"{alert_text}\n{line_text}"
                send_pushover_message(alert_text); send_ifttt_message(alert_text)
            
            send_line_message(line_text)
        except: pass
        
    conn.commit()
    conn.close()
    return jsonify({"status": "success"}), 200

@app.route('/api/v1/test_line', methods=['POST'])
def test_line():
    send_line_message("測試連通性成功")
    return jsonify({"status": "success"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
