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
    latest_cgm_entries = [dict(row) for row in entries_rows]
    
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
    test_msg = "🚨 [測試] 這是來自 CGM 儀表板的 LINE 與語音測試訊息！"
    
    # 觸發語音測試
    try:
        os.makedirs("static", exist_ok=True)
        audio_path = os.path.join("static", "alert.mp3")
        subprocess.run(['edge-tts', '--voice', 'zh-TW-HsiaoChenNeural', '--text', test_msg, '--write-media', audio_path], check=False)
        
        global latest_alert
        latest_alert = {
            "text": test_msg,
            "time": time.time(),
            "bg_value": "測試"
        }
    except Exception as e:
        print(f"Test TTS error: {e}")
        
    # 發送 LINE / Pushover / IFTTT 通知
    send_line_message(test_msg)
    send_pushover_message(test_msg)
    send_ifttt_message(test_msg)
    return jsonify({"status": "success", "message": "測試訊息已送出"})

@app.route('/api/v1/entries', methods=['POST'])
def receive_entries():
    # 步驟 A: 安全性驗證 (檢查 api-secret)
    client_secret = request.headers.get('api-secret')
    
    if not client_secret or client_secret != EXPECTED_HASH:
        print("⚠️ 阻擋了一次未授權的上傳嘗試")
        return jsonify({"error": "Unauthorized", "message": "密碼錯誤"}), 401
    
    # 步驟 B: 獲取 App 傳來的 JSON 資料 (通常是一個 List)
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "Bad Request", "message": "沒有 JSON 資料"}), 400
        
    # 步驟 C: 處理資料 (這裡我們把它印出來，您未來可以把它存進自己的資料庫或傳送 LINE 通知)
    print(f"✅ 收到 {len(data)} 筆血糖資料！")
    
    global latest_alert
    
    conn = database.get_db_connection()
    c = conn.cursor()
    
    for entry in data:
        # 取出我們在意的欄位
        bg_value = entry.get('sgv')
        direction = entry.get('direction', '無趨勢')
        date_str = entry.get('dateString', '無時間')
        device = entry.get('device', '未知設備')
        
        print(f" ➡️ [測量時間]: {date_str} | [血糖值]: {bg_value} mg/dL | [趨勢]: {direction} | [設備]: {device}")
        
        # 存入資料庫
        c.execute('INSERT INTO entries (sgv, direction, dateString, device) VALUES (?, ?, ?, ?)',
                  (bg_value, direction, date_str, device))
        
        # --- 血糖示警功能 ---
        try:
            val = int(bg_value)
            alert_text = None
            if val > 180:
                alert_text = f"警告，當前血糖偏高，數值為 {val}。"
            elif val < 70:
                alert_text = f"警告，當前血糖偏低，數值為 {val}。"
                
            # AR2 簡單預測警報 (如果過去 20 筆資料足夠，這裡只做非常粗略的兩點斜率預測)
            # 未來可擴充更精細的 AR2
            
            if alert_text:
                # 建立 static 資料夾存放語音檔
                os.makedirs("static", exist_ok=True)
                audio_path = os.path.join("static", "alert.mp3")
                # 執行 edge-tts 產生語音 (7.2.8 版)
                subprocess.run(['edge-tts', '--voice', 'zh-TW-HsiaoChenNeural', '--text', alert_text, '--write-media', audio_path], check=False)
                
                latest_alert = {
                    "text": alert_text,
                    "time": time.time(),
                    "bg_value": val
                }
                print(f"🚨 觸發警報: {alert_text}")
                
                # 發送多管道推播通知
                send_line_message(alert_text)
                send_pushover_message(alert_text)
                send_ifttt_message(alert_text)
                
        except Exception as e:
            print(f"Alert TTS error: {e}")
        
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
