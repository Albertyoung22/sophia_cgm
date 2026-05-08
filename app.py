from flask import Flask, request, jsonify, render_template
import hashlib
import os
from datetime import datetime
import time
import subprocess
import requests

app = Flask(__name__)
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

# 儲存最新幾筆的血糖資料，為了在網頁上顯示
latest_cgm_entries = []
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

    # 將資料反轉，讓舊的在左邊、新的在右邊，適合畫曲線圖
    chart_data = list(reversed(latest_cgm_entries))
    
    play_audio = False
    if latest_alert:
        # 如果警報發生在 40 秒內，則在網頁上播放語音 (因為網頁每 30 秒更新)
        age = time.time() - latest_alert['time']
        if age < 40:
            play_audio = True
            
    return render_template('index.html', entries=latest_cgm_entries, chart_data=chart_data, alert=latest_alert, play_audio=play_audio)

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
    
    global latest_cgm_entries, latest_alert
    
    for entry in data:
        # 取出我們在意的欄位
        bg_value = entry.get('sgv')
        direction = entry.get('direction', '無趨勢')
        date_str = entry.get('dateString', '無時間')
        device = entry.get('device', '未知設備')
        
        print(f" ➡️ [測量時間]: {date_str} | [血糖值]: {bg_value} mg/dL | [趨勢]: {direction} | [設備]: {device}")
        
        # 存入最新的資料中
        latest_cgm_entries.insert(0, {
            'bg_value': bg_value,
            'direction': direction,
            'date_str': date_str,
            'device': device,
            'received_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        
        # --- 血糖示警功能 ---
        try:
            val = int(bg_value)
            alert_text = None
            if val > 180:
                alert_text = f"警告，當前血糖偏高，數值為 {val}。"
            elif val < 70:
                alert_text = f"警告，當前血糖偏低，數值為 {val}。"
                
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
                
                # 發送 LINE 通知
                send_line_message(alert_text)
                
        except Exception as e:
            print(f"Alert TTS error: {e}")
        
    # 只保留最近 20 筆
    latest_cgm_entries = latest_cgm_entries[:20]
        
    # 步驟 D: 回傳 HTTP 200 OK，告訴 App「我收到了」
    return jsonify({"status": "success", "message": f"成功接收 {len(data)} 筆資料"}), 200
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
