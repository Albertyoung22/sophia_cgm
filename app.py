import os
import time
import threading
from flask import Flask, jsonify, render_template, request
import database
from carelink_client import CareLinkClient

app = Flask(__name__)
database.init_db()

client = CareLinkClient()

@app.route('/')
def index():
    return render_template('index.html')

@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/v1/entries', methods=['GET'])
def get_entries():
    # 如果有 count 參數，或者 Accept 要求 JSON 且不是來自儀表板，則回傳 Nightscout 相容格式
    if 'count' in request.args or (request.headers.get('Accept') == 'application/json' and not request.args.get('dashboard')):
        count = request.args.get('count', default=10, type=int)
        ns_entries = database.get_nightscout_entries(count)
        return jsonify(ns_entries)
        
    latest = database.get_latest_entry()
    history = database.get_recent_entries(288) # 24 小時歷史 (5分鐘一筆 = 約 288 筆)
    stats = database.get_daily_stats(24)
    return jsonify({
        "status": "success",
        "latest": latest,
        "history": history,
        "stats": stats
    })

@app.route('/api/v1/entries.json', methods=['GET'])
@app.route('/api/v1/entries/sgv.json', methods=['GET'])
def get_entries_json():
    count = request.args.get('count', default=10, type=int)
    ns_entries = database.get_nightscout_entries(count)
    return jsonify(ns_entries)

@app.route('/api/v1/status.json', methods=['GET'])
def get_status_json():
    return jsonify({
        "status": "ok",
        "name": "SophiaCarelink",
        "version": "1.0.0",
        "settings": {
            "units": "mg/dl",
            "timeFormat": 24,
            "customTitle": "SophiaCarelink"
        }
    })

@app.route('/api/v1/sync', methods=['POST', 'GET'])
def trigger_sync():
    data = client.get_recent_data()
    if data:
        saved = database.save_entry(
            sgv=data['sgv'],
            direction=data['direction'],
            date_string=data['dateString'],
            timestamp=data['date'],
            device=data['device']
        )
        return jsonify({"status": "success", "data": data, "saved": saved})
    return jsonify({
        "status": "warning",
        "message": client.last_status or "CareLink 伺服器尚未回應或 Token 需更新"
    })

@app.route('/api/v1/status', methods=['GET'])
def get_status():
    return jsonify({
        "account": client.username,
        "country": client.country,
        "last_status": client.last_status,
        "last_glucose": client.last_glucose,
        "last_fetch_time": client.last_fetch_time.isoformat() if client.last_fetch_time else None,
        "has_token": bool(client.token_data)
    })

def start_background_loop():
    def loop():
        print("[SophiaCarelink Thread] 背景定時抓取任務已啟動 (每 5 分鐘自動執行)...")
        while True:
            try:
                data = client.get_recent_data()
                if data:
                    database.save_entry(
                        sgv=data['sgv'],
                        direction=data['direction'],
                        date_string=data['dateString'],
                        timestamp=data['date'],
                        device=data['device']
                    )
            except Exception as e:
                print(f"[Background Loop Exception] {e}")
            time.sleep(300)

    t = threading.Thread(target=loop, daemon=True)
    t.start()

# 啟動背景定時抓取任務 (無論是直接執行或經由 Gunicorn 啟動)
start_background_loop()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"SophiaCarelink Python Service Starting: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
