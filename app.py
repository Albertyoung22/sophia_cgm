import os
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory, make_response, send_file
import database
from datetime import datetime, timedelta, timezone
import sys
import json
import edge_tts
import asyncio
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib
import numpy as np
from scipy.interpolate import make_interp_spline
matplotlib.use('Agg') # 讓 matplotlib 在背景運行而不開啟視窗

app = Flask(__name__)
database.init_db()

# 預設密碼與 Token
API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=").strip()

# 全域變數，紀錄上次推播狀態
last_push_info = {"time": datetime.min.replace(tzinfo=timezone.utc), "val": 0, "type": "normal"}
def get_direction_emoji(direction):
    mapping = {
        "DoubleUp": "⇈",
        "SingleUp": "↑",
        "FortyFiveUp": "↗",
        "Flat": "→",
        "FortyFiveDown": "↘",
        "SingleDown": "↓",
        "DoubleDown": "⇊",
        "RateOutOfRange": "!!",
        "NOT COMPUTABLE": "?",
        "NONE": "-"
    }
    return mapping.get(direction, direction)

def send_line_message(text, image_url=None):
    if not LINE_ACCESS_TOKEN: 
        print("[LINE] Skip: No Token")
        return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    
    messages = [{"type": "text", "text": text}]
    if image_url:
        messages.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url
        })
        
    data = {"messages": messages}
    import sys
    try: 
        response = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"[LINE] Broadcast status: {response.status_code}")
        sys.stdout.flush()
    except Exception as e:
        print(f"[LINE] Error: {e}")
        sys.stdout.flush()

def generate_line_chart():
    try:
        db = database.get_db()
        # 取得最近 144 筆資料 (約 12 小時)
        cursor = db.entries.find(sort=[("dateString", -1)]).limit(144)
        data = list(cursor)
        if not data: return None
        
        # 轉為時間順序
        data.reverse()
        
        times = [datetime.fromisoformat(d['dateString'].replace('Z', '+00:00')) for d in data]
        vals = [d.get('sgv', 0) for d in data]
        
        if not times: return None

        # --- 樣式設定 (現代感、深色主題) ---
        BG_COLOR = '#121212'      # 深背景
        GRID_COLOR = '#2A2A2A'    # 格線
        TEXT_COLOR = '#E0E0E0'    # 文字
        NORMAL_COLOR = '#00E676'  # 綠色 (正常)
        HIGH_COLOR = '#FF9100'    # 橘色 (偏高)
        LOW_COLOR = '#FF5252'     # 紅色 (偏低)
        LINE_COLOR = '#FFFFFF'    # 主線條白色
        
        plt.figure(figsize=(10, 5), facecolor=BG_COLOR, dpi=120)
        ax = plt.gca()
        ax.set_facecolor(BG_COLOR)
        
        # --- 範圍帶狀背景 ---
        plt.axhspan(70, 180, color=NORMAL_COLOR, alpha=0.03) # 正常範圍淡淡綠色
        plt.axhline(y=180, color=HIGH_COLOR, linestyle='--', linewidth=1, alpha=0.3)
        plt.axhline(y=70, color=LOW_COLOR, linestyle='--', linewidth=1, alpha=0.3)
        
        # --- 繪製曲線 ---
        # 1. 繪製平滑曲線 (如果有足夠資料)
        if len(times) > 10:
            try:
                # 將時間轉為秒數進行插值
                x = np.array([t.timestamp() for t in times])
                y = np.array(vals)
                
                # 移除重複的 timestamp 避免插值失敗
                x, unique_idx = np.unique(x, return_index=True)
                y = y[unique_idx]
                
                if len(x) > 3:
                    x_new = np.linspace(x.min(), x.max(), 300)
                    spl = make_interp_spline(x, y, k=3)
                    y_smooth = spl(x_new)
                    
                    # 繪製平滑線
                    plt.plot([datetime.fromtimestamp(ts, tz=timezone.utc) for ts in x_new], 
                             y_smooth, color=LINE_COLOR, linewidth=2, alpha=0.7, zorder=3)
                    
                    # 繪製漸層填滿 (曲線下方)
                    plt.fill_between([datetime.fromtimestamp(ts, tz=timezone.utc) for ts in x_new], 
                                    y_smooth, 40, color=LINE_COLOR, alpha=0.05, zorder=2)
            except Exception as e:
                print(f"[Smooth Plot Error] {e}")
                plt.plot(times, vals, color=LINE_COLOR, linewidth=2, alpha=0.6, zorder=3)
        else:
            plt.plot(times, vals, color=LINE_COLOR, linewidth=2, alpha=0.6, zorder=3)
        
        # 2. 繪製資料點 (顏色依數值而定)
        colors = []
        for v in vals:
            if v >= 180: colors.append(HIGH_COLOR)
            elif v <= 70: colors.append(LOW_COLOR)
            else: colors.append(NORMAL_COLOR)
        
        plt.scatter(times, vals, c=colors, s=25, edgecolors=BG_COLOR, linewidth=0.5, zorder=4)
        
        # 3. 強調最新數值
        latest_time = times[-1]
        latest_val = vals[-1]
        latest_color = colors[-1]
        
        plt.scatter(latest_time, latest_val, color=latest_color, s=120, edgecolors='white', linewidth=2, zorder=5)
        
        # 在圖上標註最新數值
        plt.annotate(f"{latest_val}", 
                     (latest_time, latest_val),
                     textcoords="offset points", 
                     xytext=(0, 15), 
                     ha='center', 
                     fontsize=14, 
                     fontweight='bold', 
                     color='white',
                     bbox=dict(boxstyle='round,pad=0.3', fc=latest_color, alpha=0.9, ec='white', lw=1))

        # --- 格式化座標軸 ---
        plt.ylim(40, 300 if max(vals) < 280 else max(vals) + 20)
        ax.tick_params(colors=TEXT_COLOR, labelsize=10)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COLOR)
        
        # 時間格式 (台灣時間)
        tz_tw = timezone(timedelta(hours=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=tz_tw))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        
        plt.grid(color=GRID_COLOR, linestyle='-', linewidth=0.5, alpha=0.8)
        
        # 圖表標題與資訊
        last_update = latest_time.astimezone(tz_tw).strftime('%m/%d %H:%M')
        plt.title(f"血糖趨勢圖 ({last_update})", color=TEXT_COLOR, fontsize=12, pad=15, fontweight='bold')
        
        # 隱藏右方與上方的邊框
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        
        output_path = os.path.join("static", "line_chart.png")
        plt.savefig(output_path, facecolor='black')
        plt.close()
        return True
    except Exception as e:
        print(f"[Chart Error] {e}")
        import traceback
        traceback.print_exc()
        return False

def get_daily_stats(hours=24):
    try:
        db = database.get_db()
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=hours)
        
        # 查詢過去 N 小時的資料
        cursor = db.entries.find({
            "dateString": {"$gte": start_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')}
        }).sort("dateString", 1)
        
        data = list(cursor)
        if not data: return None
        
        vals = [d.get('sgv', 0) for d in data]
        if not vals: return None
        
        avg = sum(vals) / len(vals)
        in_range = len([v for v in vals if 70 <= v <= 180])
        tir = (in_range / len(vals)) * 100
        
        high = len([v for v in vals if v > 180])
        low = len([v for v in vals if v < 70])
        
        # GMI 計算公式: 3.31 + 0.02392 * [平均血糖 mg/dL]
        gmi = 3.31 + (0.02392 * avg)
        
        return {
            "avg": round(avg),
            "tir": round(tir),
            "high": round((high / len(vals)) * 100),
            "low": round((low / len(vals)) * 100),
            "gmi": round(gmi, 1),
            "count": len(vals)
        }
    except Exception as e:
        print(f"[Stats Error] {e}")
        return None

def generate_summary_chart(hours=24):
    try:
        db = database.get_db()
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=hours)
        
        cursor = db.entries.find({
            "dateString": {"$gte": start_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')}
        }).sort("dateString", 1)
        
        data = list(cursor)
        if not data: return None
        
        times = [datetime.fromisoformat(d['dateString'].replace('Z', '+00:00')) for d in data]
        vals = [d.get('sgv', 0) for d in data]
        
        plt.figure(figsize=(10, 5), facecolor='black')
        ax = plt.gca()
        ax.set_facecolor('black')
        
        # 繪製背景範圍
        plt.axhspan(70, 180, color='#32D74B', alpha=0.1, label='Target Range')
        
        # 繪製點與線
        plt.plot(times, vals, color='#555555', linewidth=1.5, alpha=0.6)
        
        colors = []
        for v in vals:
            if v >= 180: colors.append('#FF9F0A')
            elif v <= 70: colors.append('#FF453A')
            else: colors.append('#00BFFF')
            
        plt.scatter(times, vals, c=colors, s=15, zorder=3)
        
        plt.ylim(40, 350)
        ax.tick_params(colors='gray', labelsize=9)
        for spine in ax.spines.values(): spine.set_color('#333333')
        
        # 時間格式化 (台灣時間)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=timezone(timedelta(hours=8))))
        plt.grid(color='#222222', linestyle='--', linewidth=0.5)
        
        plt.title(f"過去 {hours} 小時趨勢圖", color='white', pad=20, fontsize=12)
        plt.tight_layout()
        
        output_path = os.path.join("static", "summary_chart.png")
        plt.savefig(output_path, facecolor='black')
        plt.close()
        return True
    except Exception as e:
        print(f"[Summary Chart Error] {e}")
        return False

@app.route('/api/v1/daily_report', methods=['GET'])
def trigger_daily_report():
    # 安全性檢查 (可選)
    token = request.args.get('token')
    if token != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    
    stats = get_daily_stats(24)
    if stats:
        chart_url = None
        if generate_summary_chart(24):
            now_ts = int(datetime.now().timestamp())
            chart_url = f"https://sophia-cgm.onrender.com/static/summary_chart.png?t={now_ts}"
        
        msg = (
            f"📊 【每日血糖自動結算】\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔹 平均血糖: {stats['avg']} mg/dL\n"
            f"🔹 TIR (範圍內): {stats['tir']}%\n"
            f"🔹 預估 A1C (GMI): {stats['gmi']}%\n"
            f"🔹 偏高比例: {stats['high']}%\n"
            f"🔹 偏低比例: {stats['low']}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"過去 24 小時共記錄 {stats['count']} 次數據。"
        )
        send_line_message(msg, chart_url)
        return jsonify({"status": "success", "stats": stats})
    return jsonify({"status": "no_data"}), 200

# 回覆特定訊息
def reply_line_message(reply_token, text, image_url=None):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"}
    
    messages = [{"type": "text", "text": text}]
    if image_url:
        messages.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url
        })
        
    data = {
        "replyToken": reply_token,
        "messages": messages
    }
    try:
        requests.post(url, headers=headers, json=data, timeout=10)
    except Exception as e:
        print(f"[LINE Reply Error] {e}")

@app.before_request
def log_all():
    # 忽略常見的靜態檔案請求，減少日誌噪音
    if request.path.startswith('/static') or request.path in ['/favicon.ico', '/manifest.json', '/sw.js']:
        return
    
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
                        
                        # 手動查詢也附上趨勢圖 (Reply 訊息是免費的)
                        chart_url = None
                        if generate_line_chart():
                            now_ts = int(datetime.now().timestamp())
                            chart_url = f"https://sophia-cgm.onrender.com/static/line_chart.png?t={now_ts}"
                            
                        dir_emoji = get_direction_emoji(entry['direction'])
                        msg = f"【即時查詢】\n🩸 數值: {entry['sgv']}\n📈 趨勢: {dir_emoji} ({entry['direction']})\n⏰ 時間: {local_time}"
                        reply_line_message(reply_token, msg, chart_url)
                        print(f"✅ 已回覆數據與圖表: {entry['sgv']} at {local_time}")
                    else:
                        msg = "資料庫目前沒有任何血糖紀錄。"
                        reply_line_message(reply_token, msg)
                        print("⚠️ 資料庫是空的，已回覆提示")

                elif user_msg in ["報表", "報告", "report"]:
                    stats = get_daily_stats(24)
                    if stats:
                        chart_url = None
                        if generate_summary_chart(24):
                            now_ts = int(datetime.now().timestamp())
                            # 請確保此網址與您的實際網址一致
                            chart_url = f"https://sophia-cgm.onrender.com/static/summary_chart.png?t={now_ts}"
                        
                        msg = (
                            f"📊 【過去 24 小時報表】\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"🔹 平均血糖: {stats['avg']} mg/dL\n"
                            f"🔹 TIR (範圍內): {stats['tir']}%\n"
                            f"🔹 預估 A1C (GMI): {stats['gmi']}%\n"
                            f"🔹 偏高比例: {stats['high']}%\n"
                            f"🔹 偏低比例: {stats['low']}%\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"共分析 {stats['count']} 筆數據"
                        )
                        reply_line_message(reply_token, msg, chart_url)
                        print(f"✅ 已回覆 24H 報表: Avg {stats['avg']}")
                    else:
                        reply_line_message(reply_token, "暫時無法產生報表，請確認是否有過去 24 小時的資料。")
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

@app.route('/api/v1/treatments', methods=['GET', 'POST'])
def treatments_api():
    db = database.get_db()
    if request.method == 'GET':
        # 回傳最近的治療紀錄 (或回傳空陣列以消去 404 錯誤)
        cursor = db.treatments.find(sort=[("created_at", -1)]).limit(50)
        treatments = []
        for t in cursor:
            t['_id'] = str(t['_id'])
            treatments.append(t)
        return jsonify(treatments)
    
    # 處理 POST (接收新的治療紀錄)
    data = request.get_json(silent=True) or {}
    if data:
        db.treatments.insert_one(data)
    return jsonify({"status": "success"}), 200

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
        global last_push_info
        now = datetime.now(timezone.utc)
        val = latest_entry['val']
        
        # 判斷是否需要推播
        is_urgent = val > 180 or val < 80
        minutes_since_last = (now - last_push_info["time"]).total_seconds() / 60
        
        should_push = False
        reason = ""
        
        if is_urgent:
            # 緊急狀況：每 30 分鐘推播一次
            if minutes_since_last >= 30 or last_push_info["type"] == "normal":
                should_push = True
                reason = "Urgent Alert"
        else:
            # 正常狀況：每 120 分鐘推播一次
            if minutes_since_last >= 120:
                should_push = True
                reason = "Regular Update"

        if should_push:
            # 強制使用台灣時間 (UTC+8) 顯示在 LINE 上
            local_now = datetime.now(timezone(timedelta(hours=8)))
            local_time = local_now.strftime('%H:%M')
            
            # 如果是緊急狀態，生成圖表並發送圖片
            chart_url = None
            if is_urgent:
                if generate_line_chart():
                    # 請確保此網址與您的 Render 網址一致
                    chart_url = f"https://sophia-cgm.onrender.com/static/line_chart.png?t={int(now.timestamp())}"

            dir_emoji = get_direction_emoji(latest_entry['dir'])
            msg = f"【{'🚨 警告' if is_urgent else '📊 目前血糖'}】\n🩸 數值: {val}\n📈 趨勢: {dir_emoji} ({latest_entry['dir']})\n⏰ 時間: {local_time}"
            send_line_message(msg, chart_url)
            
            last_push_info = {"time": now, "val": val, "type": "urgent" if is_urgent else "normal"}
            print(f"[Success] {reason} broadcast: {val} at {local_time}")
        else:
            print(f"[Skip] Push throttled. Last push was {int(minutes_since_last)} mins ago. (Val: {val})")
    else:
        print("[Process] No new entries found to broadcast.")
    sys.stdout.flush()

@app.route('/api/v1/tts')
def get_tts():
    text = request.args.get('text', '血糖正常')
    voice = "zh-TW-HsiaoChenNeural"
    output_path = os.path.join("static", "voice.mp3")
    
    async def amain():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
    
    try:
        # 在同步 Flask 中運行非同步任務
        asyncio.run(amain())
        return send_file(output_path, mimetype="audio/mpeg")
    except Exception as e:
        print(f"[TTS Error] {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/v2/chart_data')
def get_chart_data():
    db = database.get_db()
    cursor = db.entries.find(sort=[("dateString", -1)]).limit(300)
    entries = []
    for doc in cursor:
        doc['_id'] = str(doc['_id'])
        entries.append(doc)
    return jsonify(list(reversed(entries)))

@app.route('/')
def home():
    db = database.get_db()
    cursor = db.entries.find(sort=[("dateString", -1)]).limit(300)
    entries = []
    for doc in cursor:
        doc['_id'] = str(doc['_id'])
        doc['bg_value'] = doc.get('sgv') or 0
        doc['date_str'] = doc.get('dateString', '')
        entries.append(doc)
    return render_template('index.html', entries=entries, chart_data=list(reversed(entries)))

@app.route('/manifest.json')
def serve_manifest():
    response = make_response(send_from_directory('static', 'manifest.json'))
    response.headers['Content-Type'] = 'application/manifest+json'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

@app.route('/sw.js')
def serve_sw():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@app.route('/favicon.ico')
@app.route('/static/icon.svg')
def serve_icon():
    response = make_response(send_from_directory('static', 'icon.svg'))
    response.headers['Content-Type'] = 'image/svg+xml'
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
