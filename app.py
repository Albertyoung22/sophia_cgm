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
# ==========================================
# 1. 系統設定
# 從 Render 的環境變數讀取 API_SECRET，若無則預設為 'tigerlion2007'
# ==========================================
API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")

# 事先計算好正確的 SHA1 雜湊值，用來與 App 傳來的比對
EXPECTED_HASH = hashlib.sha1(API_SECRET.encode('utf-8')).hexdigest()

# ==========================================
# LINE Bot 設定
# 預設使用您提供的 Channel Access Token
# ==========================================
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=")

def send_line_message(text):
    if not LINE_ACCESS_TOKEN:
        return
    
    # 使用 broadcast 可以傳送給所有加入此 Bot 的好友，不需要知道 User ID
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messages": [{"type": "text", "text": text}]
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code != 200:
            print(f"⚠️ LINE 傳送失敗: {response.text}")
        else:
            print("✅ 成功發送 LINE 通知")
    except Exception as e:
        print(f"⚠️ LINE 發生錯誤: {e}")

PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = os.environ.get("PUSHOVER_API_TOKEN", "")
IFTTT_WEBHOOK_URL = os.environ.get("IFTTT_WEBHOOK_URL", "")

def send_pushover_message(text):
    if not PUSHOVER_USER_KEY or not PUSHOVER_API_TOKEN:
        return
    try:
        requests.post("https://api.pushover.net/1/messages.json", data={
            "token": PUSHOVER_API_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "message": text
        })
    except Exception as e:
        print(f"Pushover error: {e}")

def send_ifttt_message(text):
    if not IFTTT_WEBHOOK_URL:
        return
    try:
        requests.post(IFTTT_WEBHOOK_URL, json={"value1": text})
    except Exception as e:
        print(f"IFTTT error: {e}")

def calculate_iob_cob(treatments):
    now = datetime.now()
    total_iob = 0.0
    total_cob = 0.0
    # DIA (Duration of Insulin Action) = 3 hours = 180 mins
    # Carbs action = 3 hours = 180 mins (simplification)
    for t in treatments:
        try:
            # Handle ISO format strings potentially lacking timezone info or using 'Z'
            time_str = t['created_at'].replace('Z', '')
            created = datetime.fromisoformat(time_str)
            age_mins = (now - created).total_seconds() / 60.0
            if age_mins < 0:
                age_mins = 0
            
            # Simple linear decay for IOB (DIA 3 hours)
            insulin = float(t.get('insulin') or 0)
            if insulin > 0 and age_mins < 180:
                total_iob += insulin * (1.0 - (age_mins / 180.0))
                
            # Simple linear decay for COB (3 hours)
            carbs = float(t.get('carbs') or 0)
            if carbs > 0 and age_mins < 180:
                total_cob += carbs * (1.0 - (age_mins / 180.0))
        except Exception as e:
            print(f"Calc error: {e}")
            pass
    return round(total_iob, 2), round(total_cob, 2)

# 儲存最新的警報資訊
latest_alert = None

@app.route('/sw.js')
def sw():
    return app.send_static_file('sw.js')

@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')

@app.route('/', methods=['GET', 'POST'])
def home():
    # 如果是 LINE 跑來驗證 Webhook (使用 POST)，直接回傳 200 OK 讓他通過
    if request.method == 'POST':
        return "OK", 200

    conn = database.get_db_connection()
    # 抓取最新的血糖資料
    entries_rows = conn.execute('SELECT * FROM entries ORDER BY dateString DESC LIMIT 20').fetchall()
    latest_cgm_entries = []
    for row in entries_rows:
        d = dict(row)
        d['bg_value'] = d['sgv']
        d['date_str'] = d['dateString']
        latest_cgm_entries.append(d)
    
    # 抓取最近的治療紀錄 (供畫面顯示 IOB/COB 或標籤使用)
    treatment_rows = conn.execute('SELECT * FROM treatments ORDER BY created_at DESC LIMIT 50').fetchall()
    recent_treatments = [dict(row) for row in treatment_rows]
    
    # 抓取最新的設備狀態
    device_status_row = conn.execute('SELECT * FROM devicestatus ORDER BY created_at DESC LIMIT 1').fetchone()
    device_status = dict(device_status_row) if device_status_row else None
    
    conn.close()

    # 計算 IOB / COB
    calculated_iob, calculated_cob = calculate_iob_cob(recent_treatments)
    if device_status:
        device_status['display_iob'] = device_status.get('iob') if device_status.get('iob') is not None else calculated_iob
        device_status['display_cob'] = device_status.get('cob') if device_status.get('cob') is not None else calculated_cob
    else:
        device_status = {'display_iob': calculated_iob, 'display_cob': calculated_cob, 'uploaderBattery': None, 'pumpBattery': None}

    # 將資料反轉，讓舊的在左邊、新的在右邊，適合畫曲線圖
    chart_data = list(reversed(latest_cgm_entries))
    
    play_audio = False
    if latest_alert:
        # 如果警報發生在 40 秒內，則在網頁上播放語音 (因為網頁每 30 秒更新)
        age = time.time() - latest_alert['time']
        if age < 40:
            play_audio = True
            
    # 支援多種檢視模式，可透過 ?view=xxx 來切換
    view_mode = request.args.get('view', 'main')
            
    return render_template('index.html', entries=latest_cgm_entries, chart_data=chart_data, 
                           alert=latest_alert, play_audio=play_audio, 
                           treatments=recent_treatments, devicestatus=device_status, view_mode=view_mode)

# ==========================================
# 2. 建立接收資料的 API 端點
# App 會把資料 POST 到 /api/v1/entries
# 許多上傳器會先檢查 /api/v1/status，所以我們給他一個假的回應讓他通過檢查
# ==========================================
@app.route('/api/v1/status', methods=['GET'])
def get_status():
    return jsonify({
        "status": "ok",
        "name": "Nightscout",
        "version": "14.2.2",
        "settings": {
            "units": "mg/dL",
            "timeFormat": 24,
            "nightMode": False,
            "editMode": True,
            "showRawbg": "never",
            "customTitle": "CGM"
        }
    })

@app.route('/api/v1/test_line', methods=['POST'])
def test_line():
    import random
    from datetime import datetime, timedelta
    
    conn = database.get_db_connection()
    c = conn.cursor()
    
    now = datetime.now()
    
    # 決定模擬情境：高血糖急速上升或低血糖急速下降
    is_high = random.choice([True, False])
    
    if is_high:
        base_bg = random.randint(110, 130)
        trend = random.randint(6, 10)  # 上升趨勢
        direction = "DoubleUp"
    else:
        base_bg = random.randint(120, 150)
        trend = random.randint(-10, -6) # 下降趨勢
        direction = "DoubleDown"
        
    final_bg = base_bg
    for i in range(11, -1, -1):
        dt = now - timedelta(minutes=i*5)
        bg = base_bg + (11 - i) * trend + random.randint(-2, 2)
        if bg < 40: bg = 40
        if bg > 400: bg = 400
        
        date_str = dt.isoformat()
        c.execute('INSERT INTO entries (sgv, direction, dateString, device) VALUES (?, ?, ?, ?)',
                  (bg, direction, date_str, "Simulator"))
        
        if i == 0:
            final_bg = bg
            
    conn.commit()
    conn.close()
    
    # 根據最後一筆資料決定警報內容
    val = final_bg
    alert_text = None
    if val > 180:
        alert_text = f"警告，模擬資料測試，當前血糖偏高，數值為 {val}。"
    elif val < 70:
        alert_text = f"警告，模擬資料測試，當前血糖偏低，數值為 {val}。"
    else:
        alert_text = f"模擬資料測試完成，當前血糖 {val}。"
        
    # 產生語音警報
    try:
        os.makedirs("static", exist_ok=True)
        audio_path = os.path.join("static", "alert.mp3")
        subprocess.run(['edge-tts', '--voice', 'zh-TW-HsiaoChenNeural', '--text', alert_text, '--write-media', audio_path], check=False)
        
        global latest_alert
        latest_alert = {
            "text": alert_text,
            "time": time.time(),
            "bg_value": val
        }
    except Exception as e:
        print(f"Test TTS error: {e}")
        
    # 發送通知
    send_line_message(alert_text)
    send_pushover_message(alert_text)
    send_ifttt_message(alert_text)
    return jsonify({"status": "success", "message": "模擬資料已寫入"})

@app.route('/api/v1/clear_simulation', methods=['POST'])
def clear_simulation():
    conn = database.get_db_connection()
    conn.execute("DELETE FROM entries WHERE device='Simulator'")
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/v1/entries', methods=['POST'])
def receive_entries():
    # 步驟 A: 安全性驗證 (檢查 api-secret)
    client_secret = request.headers.get('api-secret')
    
    # 同時支援 SHA1 雜湊值與明碼驗證，增加 App 相容性
    if not client_secret or (client_secret != EXPECTED_HASH and client_secret != API_SECRET):
        print(f"⚠️ 授權失敗！收到: {client_secret}")
        return jsonify({"error": "Unauthorized", "message": "密碼或授權錯誤"}), 401
    
    # 步驟 B: 獲取 App 傳來的 JSON 資料
    data = request.get_json()
    if not data:
        return jsonify({"error": "Bad Request", "message": "沒有 JSON 資料"}), 400
        
    # 強大相容性：不論 App 傳送的是單一物件 {} 還是物件陣列 [{}, {}]，都統一處理
    if isinstance(data, dict):
        entries_list = [data]
    else:
        entries_list = data

    print(f"✅ 收到 {len(entries_list)} 筆血糖資料！來源: {request.remote_addr}")
    
    global latest_alert
    conn = database.get_db_connection()
    c = conn.cursor()
    
    for entry in entries_list:
        # 取出欄位 (支援多種 App 常見命名)
        bg_value = entry.get('sgv') or entry.get('mbg') or entry.get('glucose') or entry.get('value')
        direction = entry.get('direction') or entry.get('trend_direction') or 'Flat'
        
        # 時間戳記相容性
        date_str = entry.get('dateString') or entry.get('date_string') or entry.get('display_time')
        if not date_str:
            date_str = datetime.now().isoformat()
        
        device = entry.get('device') or entry.get('uploader') or '未知設備'
        
        print(f" ➡️ [測量時間]: {date_str} | [血糖值]: {bg_value} | [趨勢]: {direction}")
        
        # 存入資料庫
        c.execute('INSERT INTO entries (sgv, direction, dateString, device) VALUES (?, ?, ?, ?)',
                  (bg_value, direction, date_str, device))
        
        # --- 血糖紀錄與示警功能 ---
        try:
            val = int(bg_value)
            
            # 定義趨勢箭頭
            arrows = {
                'DoubleUp': '⇈', 'SingleUp': '↑', 'FortyFiveUp': '↗',
                'Flat': '→', 'FortyFiveDown': '↘', 'SingleDown': '↓', 'DoubleDown': '⇊'
            }
            arrow = arrows.get(direction, direction)
            
            # 格式化顯示內容
            line_text = f"【血糖報告】\n數值: {val} {arrow}\n趨勢: {direction}\n時間: {date_str.split('T')[1][:5] if 'T' in date_str else date_str}"
            
            alert_text = None
            if val > 180:
                alert_text = f"⚠️ 警告：血糖偏高！({val})"
            elif val < 70:
                alert_text = f"🚨 警告：血糖偏低！({val})"
                
            if alert_text:
                os.makedirs("static", exist_ok=True)
                audio_path = os.path.join("static", "alert.mp3")
                subprocess.run(['edge-tts', '--voice', 'zh-TW-HsiaoChenNeural', '--text', alert_text, '--write-media', audio_path], check=False)
                
                latest_alert = { "text": alert_text, "time": time.time(), "bg_value": val }
                line_text = f"{alert_text}\n{line_text}"
                
                send_pushover_message(alert_text)
                send_ifttt_message(alert_text)
            
            # 每一次血糖變化都發送到 LINE
            send_line_message(line_text)
                
        except Exception as e:
            print(f"Alert processing error: {e}")
        
    conn.commit()
    conn.close()
        
    # 步驟 D: 回傳 HTTP 200 OK，告訴 App「我收到了」
    return jsonify({"status": "success", "message": f"成功接收 {len(data)} 筆資料"}), 200

@app.route('/api/v1/treatments', methods=['GET', 'POST', 'PUT', 'DELETE'])
def api_treatments():
    conn = database.get_db_connection()
    if request.method == 'POST':
        client_secret = request.headers.get('api-secret')
        if client_secret and client_secret != EXPECTED_HASH:
            return jsonify({"error": "Unauthorized"}), 401
            
        data = request.get_json()
        if not data:
            return jsonify({"error": "Bad Request"}), 400
            
        if not isinstance(data, list):
            data = [data]
            
        c = conn.cursor()
        for t in data:
            c.execute('''INSERT INTO treatments (eventType, carbs, insulin, notes, created_at, enteredBy)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (t.get('eventType', 'Note'), float(t.get('carbs', 0) or 0), float(t.get('insulin', 0) or 0), 
                       t.get('notes', ''), t.get('created_at', datetime.now().isoformat()), t.get('enteredBy', 'CGM-Receiver')))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"}), 200
    elif request.method == 'GET':
        rows = conn.execute('SELECT * FROM treatments ORDER BY created_at DESC LIMIT 50').fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    else:
        return jsonify({"status": "ok"}), 200

@app.route('/api/v1/devicestatus', methods=['GET', 'POST'])
def api_devicestatus():
    conn = database.get_db_connection()
    if request.method == 'POST':
        client_secret = request.headers.get('api-secret')
        if client_secret and client_secret != EXPECTED_HASH:
            return jsonify({"error": "Unauthorized"}), 401
            
        data = request.get_json()
        if not data:
            return jsonify({"error": "Bad Request"}), 400
            
        if not isinstance(data, list):
            data = [data]
            
        c = conn.cursor()
        for d in data:
            uploader_bat = None
            if 'uploader' in d and 'battery' in d['uploader']:
                uploader_bat = d['uploader']['battery']
                
            pump_bat = None
            iob = None
            cob = None
            if 'pump' in d:
                pump_bat = d['pump'].get('battery', {}).get('percent')
                iob = d['pump'].get('iob', {}).get('iob')
                cob = d['pump'].get('cob')
                
            c.execute('''INSERT INTO devicestatus (device, uploaderBattery, pumpBattery, iob, cob, created_at)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (d.get('device', 'unknown'), uploader_bat, pump_bat, iob, cob, d.get('created_at', datetime.now().isoformat())))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"}), 200
    else: # GET
        rows = conn.execute('SELECT * FROM devicestatus ORDER BY created_at DESC LIMIT 10').fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

if __name__ == '__main__':
    # Render 預設提供 PORT 環境變數，若無則使用 10000
    port = int(os.environ.get("PORT", 10000))
    # 必須綁定 0.0.0.0 才能讓外部網路連線
    try:
        from waitress import serve
        print(f"🚀 Starting server with Waitress on port {port}...")
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        print(f"⚠️ Waitress not found. Starting development server with Flask on port {port}...")
        app.run(host='0.0.0.0', port=port, debug=True)
