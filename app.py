import os
import time
import json
import asyncio
import threading
import warnings
from datetime import datetime, timezone, timedelta
import requests
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory, make_response

import database
from carelink_client import CareLinkClient

# Suppress Matplotlib CJK Glyph warnings on Linux environments like Render
warnings.filterwarnings("ignore", category=UserWarning)

# Matplotlib Setup for Headless Chart Generation
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'SimHei', 'DejaVu Sans', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from scipy.interpolate import make_interp_spline

app = Flask(__name__)
database.init_db()

client = CareLinkClient()

# LINE & API Secrets
API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=").strip()

# Last Push Notification State Tracker
last_push_info = {"time": datetime.min.replace(tzinfo=timezone.utc), "val": 0, "type": "normal"}

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

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
    return mapping.get(direction, direction or "-")

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
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"[LINE] Broadcast status: {response.status_code}")
    except Exception as e:
        print(f"[LINE Broadcast Error] {e}")

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
        response = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"[LINE Reply] Status: {response.status_code}")
    except Exception as e:
        print(f"[LINE Reply Error] {e}")

def generate_line_chart():
    try:
        entries = database.get_nightscout_entries(limit=144)
        if not entries:
            return False
        
        entries.reverse()
        
        times = []
        vals = []
        tz_tw = timezone(timedelta(hours=8))
        for e in entries:
            try:
                dt = datetime.fromisoformat(e['dateString'].replace('Z', '+00:00'))
                times.append(dt.astimezone(tz_tw))
                vals.append(e.get('sgv', 0))
            except Exception:
                pass

        if not times or not vals:
            return False

        BG_COLOR = '#121212'
        GRID_COLOR = '#2A2A2A'
        TEXT_COLOR = '#E0E0E0'
        NORMAL_COLOR = '#00E676'
        HIGH_COLOR = '#FF9100'
        LOW_COLOR = '#FF5252'
        LINE_COLOR = '#FFFFFF'
        
        plt.figure(figsize=(10, 5), facecolor=BG_COLOR, dpi=120)
        ax = plt.gca()
        ax.set_facecolor(BG_COLOR)
        
        plt.axhspan(70, 180, color=NORMAL_COLOR, alpha=0.03)
        plt.axhline(y=180, color=HIGH_COLOR, linestyle='--', linewidth=1, alpha=0.3)
        plt.axhline(y=70, color=LOW_COLOR, linestyle='--', linewidth=1, alpha=0.3)
        
        if len(times) > 10:
            try:
                x = np.array([t.timestamp() for t in times])
                y = np.array(vals)
                x, unique_idx = np.unique(x, return_index=True)
                y = y[unique_idx]
                
                if len(x) > 3:
                    x_new = np.linspace(x.min(), x.max(), 300)
                    spl = make_interp_spline(x, y, k=3)
                    y_smooth = spl(x_new)
                    
                    plt.plot([datetime.fromtimestamp(ts, tz=tz_tw) for ts in x_new], 
                             y_smooth, color=LINE_COLOR, linewidth=2, alpha=0.7, zorder=3)
                    plt.fill_between([datetime.fromtimestamp(ts, tz=tz_tw) for ts in x_new], 
                                    y_smooth, 40, color=LINE_COLOR, alpha=0.05, zorder=2)
            except Exception as e:
                print(f"[Smooth Chart Warning] {e}")
                plt.plot(times, vals, color=LINE_COLOR, linewidth=2, alpha=0.6, zorder=3)
        else:
            plt.plot(times, vals, color=LINE_COLOR, linewidth=2, alpha=0.6, zorder=3)
        
        colors = []
        for v in vals:
            if v >= 180: colors.append(HIGH_COLOR)
            elif v <= 70: colors.append(LOW_COLOR)
            else: colors.append(NORMAL_COLOR)
        
        plt.scatter(times, vals, c=colors, s=25, edgecolors=BG_COLOR, linewidth=0.5, zorder=4)
        
        latest_time = times[-1]
        latest_val = vals[-1]
        latest_color = colors[-1]
        
        plt.scatter(latest_time, latest_val, color=latest_color, s=120, edgecolors='white', linewidth=2, zorder=5)
        
        plt.annotate(f"{latest_val}", 
                     (latest_time, latest_val),
                     textcoords="offset points", 
                     xytext=(0, 15), 
                     ha='center', 
                     fontsize=14, 
                     fontweight='bold', 
                     color='white',
                     bbox=dict(boxstyle='round,pad=0.3', fc=latest_color, alpha=0.9, ec='white', lw=1))

        plt.ylim(40, 300 if max(vals) < 280 else max(vals) + 20)
        ax.tick_params(colors=TEXT_COLOR, labelsize=10)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COLOR)
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=tz_tw))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        
        plt.grid(color=GRID_COLOR, linestyle='-', linewidth=0.5, alpha=0.8)
        
        last_update = latest_time.strftime('%m/%d %H:%M')
        plt.title(f"Glucose Trend ({last_update})", color=TEXT_COLOR, fontsize=12, pad=15, fontweight='bold')
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        
        output_path = os.path.join(STATIC_DIR, "line_chart.png")
        plt.savefig(output_path, facecolor='black')
        plt.close()
        return True
    except Exception as e:
        print(f"[Generate Line Chart Error] {e}")
        return False

def generate_summary_chart(hours=24):
    try:
        entries = database.get_nightscout_entries(limit=288)
        if not entries:
            return False
        
        entries.reverse()
        tz_tw = timezone(timedelta(hours=8))
        times = []
        vals = []
        for e in entries:
            try:
                dt = datetime.fromisoformat(e['dateString'].replace('Z', '+00:00'))
                times.append(dt.astimezone(tz_tw))
                vals.append(e.get('sgv', 0))
            except Exception:
                pass

        if not times or not vals:
            return False
        
        plt.figure(figsize=(10, 5), facecolor='black')
        ax = plt.gca()
        ax.set_facecolor('black')
        
        plt.axhspan(70, 180, color='#32D74B', alpha=0.1, label='Target Range')
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
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=tz_tw))
        plt.grid(color='#222222', linestyle='--', linewidth=0.5)
        
        plt.title(f"Past {hours} Hours Trend", color='white', pad=20, fontsize=12)
        plt.tight_layout()
        
        output_path = os.path.join(STATIC_DIR, "summary_chart.png")
        plt.savefig(output_path, facecolor='black')
        plt.close()
        return True
    except Exception as e:
        print(f"[Generate Summary Chart Error] {e}")
        return False

@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/v1/entries', methods=['GET'])
@app.route('/api/v1/entries.json', methods=['GET', 'POST'])
def get_entries():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        items = [data] if isinstance(data, dict) else data
        for entry in items:
            val = entry.get('sgv') or entry.get('mbg') or entry.get('glucose')
            if val:
                database.save_entry(
                    sgv=int(val),
                    direction=entry.get('direction', 'Flat'),
                    date_string=entry.get('dateString', datetime.now(timezone(timedelta(hours=8))).isoformat()),
                    timestamp=entry.get('date', int(time.time() * 1000)),
                    device=entry.get('device', 'App')
                )
        return jsonify({"status": "success"}), 200

    if 'count' in request.args or (request.headers.get('Accept') == 'application/json' and not request.args.get('dashboard')):
        count = request.args.get('count', default=10, type=int)
        ns_entries = database.get_nightscout_entries(count)
        return jsonify(ns_entries)
        
    latest = database.get_latest_entry()
    history = database.get_recent_entries(288)
    stats = database.get_daily_stats(24)
    return jsonify({
        "status": "success",
        "latest": latest,
        "history": history,
        "stats": stats
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
        generate_line_chart()
        return jsonify({"status": "success", "data": data, "saved": saved})
    return jsonify({
        "status": "warning",
        "message": client.last_status or "CareLink 伺服器尚未回應或 Token 需更新"
    })

@app.route('/api/v1/status', methods=['GET'])
@app.route('/api/v1/status.json', methods=['GET'])
def get_status():
    now = datetime.now(timezone.utc)
    return jsonify({
        "status": "ok",
        "name": "SophiaCarelink",
        "version": "1.0.0",
        "account": client.username,
        "country": client.country,
        "last_status": client.last_status,
        "last_glucose": client.last_glucose,
        "last_fetch_time": client.last_fetch_time.isoformat() if client.last_fetch_time else None,
        "has_token": bool(client.token_data),
        "serverTime": now.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        "serverTimeEpoch": int(now.timestamp() * 1000),
        "authorized": True,
        "apiEnabled": True,
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
        "message": {"canRead": True, "canWrite": True, "isAdmin": True, "message": "OK", "rolefound": "FOUND", "permissions": "ROLE"}
    })

@app.route('/api/v1/daily_report', methods=['GET'])
def trigger_daily_report():
    token = request.args.get('token')
    if token != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    
    stats = database.get_daily_stats(24)
    if stats:
        chart_url = None
        if generate_summary_chart(24):
            now_ts = int(time.time())
            host_url = request.host_url.rstrip('/')
            chart_url = f"{host_url}/static/summary_chart.png?t={now_ts}"
        
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

# LINE Webhook (處理廣播與即時查詢，採異步非阻塞處理避免 Gunicorn Timeout)
@app.route("/callback", methods=['POST'])
def line_callback():
    body = request.get_json(silent=True) or {}
    events = body.get('events', [])
    host_url = request.host_url.rstrip('/')
    
    def process_line_events_async(event_list, base_host):
        for event in event_list:
            if event.get('type') == 'message' and event.get('message', {}).get('type') == 'text':
                user_msg = event['message']['text'].strip()
                reply_token = event['replyToken']
                
                if user_msg == "血糖" or user_msg.lower() == "bg":
                    latest = database.get_latest_entry()
                    if latest:
                        try:
                            dt_in = datetime.fromisoformat(latest['dateString'].replace('Z', '+00:00'))
                            local_time = dt_in.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M')
                        except Exception:
                            local_time = latest['dateString']
                        
                        chart_url = None
                        if generate_line_chart():
                            now_ts = int(time.time())
                            chart_url = f"{base_host}/static/line_chart.png?t={now_ts}"
                            
                        dir_emoji = get_direction_emoji(latest.get('direction'))
                        msg = f"【即時血糖查詢】\n🩸 數值: {latest['sgv']} mg/dL\n📈 趨勢: {dir_emoji} ({latest.get('direction', 'Flat')})\n⏰ 時間: {local_time}"
                        reply_line_message(reply_token, msg, chart_url)
                    else:
                        reply_line_message(reply_token, "資料庫目前沒有任何血糖紀錄。")

                elif user_msg in ["報表", "報告", "report"]:
                    stats = database.get_daily_stats(24)
                    if stats:
                        chart_url = None
                        if generate_summary_chart(24):
                            now_ts = int(time.time())
                            chart_url = f"{base_host}/static/summary_chart.png?t={now_ts}"
                        
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
                    else:
                        reply_line_message(reply_token, "暫時無法產生報表，請確認是否有過去 24 小時的資料。")

    if events:
        threading.Thread(target=process_line_events_async, args=(events, host_url), daemon=True).start()

    return 'OK', 200

@app.route('/api/v1/tts')
def get_tts():
    text = request.args.get('text', '血糖正常')
    voice = "zh-TW-HsiaoChenNeural"
    output_path = os.path.join(STATIC_DIR, "voice.mp3")
    
    async def amain():
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
    
    try:
        asyncio.run(amain())
        return send_file(output_path, mimetype="audio/mpeg")
    except Exception as e:
        print(f"[TTS Error] {e}")
        return jsonify({"error": str(e)}), 500

def check_and_push_alerts(data):
    global last_push_info
    if not data:
        return
    
    val = data.get('sgv')
    if not val:
        return

    now = datetime.now(timezone.utc)
    if val < 80:
        current_state = "low"
    elif val > 180:
        current_state = "high"
    else:
        current_state = "normal"
        
    last_state = last_push_info.get("type", "normal")
    minutes_since_last = (now - last_push_info["time"]).total_seconds() / 60
    is_urgent = current_state in ["low", "high"]
    
    should_push = False
    reason = ""
    
    if current_state != last_state:
        should_push = True
        reason = f"State transition from {last_state} to {current_state}"
    elif is_urgent and minutes_since_last >= 60:
        should_push = True
        reason = f"Persistent {current_state} state alert"

    if should_push:
        local_now = datetime.now(timezone(timedelta(hours=8)))
        local_time = local_now.strftime('%H:%M')
        
        chart_url = None
        if is_urgent:
            if generate_line_chart():
                now_ts = int(now.timestamp())
                chart_url = f"https://sophia-cgm.onrender.com/static/line_chart.png?t={now_ts}"

        dir_emoji = get_direction_emoji(data.get('direction'))
        msg = f"【{'🚨 警告' if is_urgent else '📊 目前血糖'}】\n🩸 數值: {val} mg/dL\n📈 趨勢: {dir_emoji} ({data.get('direction', 'Flat')})\n⏰ 時間: {local_time}"
        send_line_message(msg, chart_url)
        
        last_push_info = {"time": now, "val": val, "type": current_state}
        print(f"[LINE Alert] {reason} broadcast: {val} at {local_time}")

def start_background_loop():
    def loop():
        print("[SophiaCarelink Thread] 美敦力 CareLink 自動背景同步與 LINE 警報任務已啟動 (每 5 分鐘自動執行)...")
        while True:
            try:
                data = client.get_recent_data()
                if data:
                    saved = database.save_entry(
                        sgv=data['sgv'],
                        direction=data['direction'],
                        date_string=data['dateString'],
                        timestamp=data['date'],
                        device=data['device']
                    )
                    generate_line_chart()
                    if saved:
                        check_and_push_alerts(data)
            except Exception as e:
                print(f"[Background Loop Exception] {e}")
            time.sleep(300)

    t = threading.Thread(target=loop, daemon=True)
    t.start()

# 啟動背景任務
start_background_loop()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"SophiaCarelink Python Service Starting: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
