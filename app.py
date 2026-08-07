# -*- coding: utf-8 -*-
import sys
import threading
import time
import os
import json
import urllib.parse
import asyncio
from datetime import datetime, timezone, timedelta
import requests
from flask import Flask, jsonify, render_template, request, send_file

# 解決 Windows 終端機 CP950 編碼無法輸出 Emoji 的問題
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

from carelink_receiver import TaiwanCareLinkReceiver

app = Flask(__name__, template_folder='templates')
receiver = TaiwanCareLinkReceiver()

# 儲存全域最新的 CGM 資料狀態
latest_data = {
    "glucose": receiver.history[-1].get("glucose") if receiver.history else None,
    "trend": receiver.history[-1].get("trend", "➡️ 平穩") if receiver.history else "➡️ 平穩",
    "time": receiver.history[-1].get("time", "尚未更新") if receiver.history else "尚未更新",
    "iob": receiver.history[-1].get("iob", 0.0) if receiver.history else 0.0,
    "ai_advice": receiver.last_ai_advice or "等待接收數據...",
    "is_loading": False,
    "error": None
}

# LINE 通知相關設定
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "VcvnrEjM8eo/5c93V8zgGAdEe/nJChrM0ndXWIVrLwQH0qk1YDnG9FwS9rLX/UJXOAFd9iG+TuihqOLssHCJpL4vhBE3Xoan1Yq01ahcH/Qn2OsrshF8tM4yKrzGPsHpruXRC7D7Nn680dKl4STfTQdB04t89/1O/w1cDnyilFU=").strip()
BASE_URL = os.environ.get("BASE_URL", "https://sophia-cgm.onrender.com").rstrip('/')
API_SECRET = os.environ.get("API_SECRET", "tigerlion2007")

last_push_info = {
    "time": datetime.min.replace(tzinfo=timezone.utc),
    "state": "normal"
}

def send_line_message(text, image_url=None):
    if not LINE_ACCESS_TOKEN:
        print("[LINE] 跳過: 未設定 LINE_ACCESS_TOKEN")
        return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN.strip()}",
        "Content-Type": "application/json"
    }
    messages = [{"type": "text", "text": text}]
    if image_url:
        messages.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url
        })
    data = {
        "messages": messages
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"[LINE Broadcast] 推播狀態碼: {response.status_code}")
        if response.status_code != 200:
            print(f"❌ [LINE Broadcast 錯誤詳細內容]: {response.text}")
            if image_url and response.status_code == 400:
                print("🔄 圖片存取失敗，降級為僅廣播純文字訊息...")
                data["messages"] = [{"type": "text", "text": text}]
                res_retry = requests.post(url, headers=headers, json=data, timeout=10)
                print(f"[LINE Broadcast 降級狀態碼]: {res_retry.status_code}")
    except Exception as e:
        print(f"[LINE Broadcast 傳送例外]: {e}")

def get_daily_stats(hours=24):
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)
        
        valid_entries = []
        for entry in receiver.history:
            entry_time_str = entry.get("time")
            if not entry_time_str:
                continue
            try:
                dt = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
                if dt >= cutoff:
                    valid_entries.append(entry)
            except Exception:
                continue
                
        if not valid_entries:
            return None
            
        vals = [entry.get("glucose") for entry in valid_entries if entry.get("glucose") is not None]
        if not vals:
            return None
            
        avg = sum(vals) / len(vals)
        in_range = len([v for v in vals if 70 <= v <= 180])
        tir = (in_range / len(vals)) * 100
        
        high = len([v for v in vals if v > 180])
        low = len([v for v in vals if v < 70])
        
        gmi = 3.31 + (0.02392 * avg)
        
        return {
            "avg": round(avg),
            "tir": round(tir),
            "high": round((high / len(vals)) * 100),
            "low": round((low / len(vals)) * 100),
            "gmi": round(gmi, 1),
            "count": len(vals),
            "entries": valid_entries
        }
    except Exception as e:
        print(f"[Stats Error] {e}")
        return None

def generate_line_chart():
    try:
        data = receiver.history[-144:]
        if not data:
            return False
            
        times = []
        vals = []
        for d in data:
            if d.get("glucose") is not None and d.get("time"):
                try:
                    dt = datetime.fromisoformat(d["time"].replace('Z', '+00:00'))
                    times.append(dt)
                    vals.append(d["glucose"])
                except Exception:
                    continue
                    
        if not times:
            return False
            
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import numpy as np
        from scipy.interpolate import make_interp_spline
        
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
                    plt.plot([datetime.fromtimestamp(ts, tz=timezone.utc) for ts in x_new], 
                             y_smooth, color=LINE_COLOR, linewidth=2, alpha=0.7, zorder=3)
                    plt.fill_between([datetime.fromtimestamp(ts, tz=timezone.utc) for ts in x_new], 
                                    y_smooth, 40, color=LINE_COLOR, alpha=0.05, zorder=2)
            except Exception as e:
                print(f"[Smooth Plot Error] {e}")
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
            
        tz_tw = timezone(timedelta(hours=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=tz_tw))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        
        plt.grid(color=GRID_COLOR, linestyle='-', linewidth=0.5, alpha=0.8)
        
        last_update = latest_time.astimezone(tz_tw).strftime('%m/%d %H:%M')
        plt.title(f"血糖趨勢圖 ({last_update})", color=TEXT_COLOR, fontsize=12, pad=15, fontweight='bold')
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        
        os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)
        output_path = os.path.join(app.root_path, 'static', 'line_chart.png')
        plt.savefig(output_path, facecolor='black')
        plt.close()
        return True
    except Exception as e:
        print(f"[Chart Error] {e}")
        return False

def generate_summary_chart(hours=24):
    try:
        stats = get_daily_stats(hours)
        if not stats or not stats.get("entries"):
            return False
            
        valid_entries = stats["entries"]
        times = []
        vals = []
        for d in valid_entries:
            if d.get("glucose") is not None and d.get("time"):
                try:
                    dt = datetime.fromisoformat(d["time"].replace('Z', '+00:00'))
                    times.append(dt)
                    vals.append(d["glucose"])
                except Exception:
                    continue
                    
        if not times:
            return False
            
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        
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
        for spine in ax.spines.values():
            spine.set_color('#333333')
            
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=timezone(timedelta(hours=8))))
        plt.grid(color='#222222', linestyle='--', linewidth=0.5)
        
        plt.title(f"過去 {hours} 小時趨勢圖", color='white', pad=20, fontsize=12)
        plt.tight_layout()
        
        os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)
        output_path = os.path.join(app.root_path, 'static', 'summary_chart.png')
        plt.savefig(output_path, facecolor='black')
        plt.close()
        return True
    except Exception as e:
        print(f"[Summary Chart Error] {e}")
        return False

def check_and_send_line_alert(cgm, ai_advice):
    global last_push_info
    if not LINE_ACCESS_TOKEN:
        return
        
    glucose = cgm.get("glucose")
    if not glucose:
        return
        
    # 判定當前血糖狀態
    if glucose < 70:
        current_state = "low"
    elif glucose > 180:
        current_state = "high"
    else:
        current_state = "normal"
        
    # 判斷是否需要推播
    should_push = False
    reason = ""
    
    now = datetime.now(timezone.utc)
    minutes_since_last = (now - last_push_info["time"]).total_seconds() / 60.0
    last_state = last_push_info["state"]
    is_urgent = (current_state in ["low", "high"])
    
    # 1. 狀態發生改變時，必定推播
    if current_state != last_state:
        should_push = True
        reason = f"狀態由 {last_state} 轉變為 {current_state}"
    # 2. 狀態未改變但持續處於異常 (low/high)，且距離上次推播超過 60 分鐘，再次提醒
    elif is_urgent and minutes_since_last >= 60:
        should_push = True
        reason = f"持續處於 {current_state} 異常狀態超過 60 分鐘"
        
    if should_push:
        local_now = datetime.now(timezone(timedelta(hours=8)))
        local_time = local_now.strftime('%H:%M')
        
        # 狀態標題
        if current_state == "low":
            title = "🚨 警告：血糖偏低"
        elif current_state == "high":
            title = "🚨 警告：血糖偏高"
        else:
            title = "📊 血糖恢復正常"
            
        chart_url = None
        if generate_line_chart():
            now_ts = int(now.timestamp())
            chart_url = f"{BASE_URL}/static/line_chart.png?t={now_ts}"

        msg = (
            f"【{title}】\n"
            f"🩸 血糖數值: {glucose} mg/dL\n"
            f"📈 趨勢: {cgm.get('trend', '➡️ 平穩')}\n"
            f"⏰ 時間: {local_time}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🤖 AI 照護建議:\n"
            f"{ai_advice}"
        )
        
        send_line_message(msg, chart_url)
        last_push_info = {"time": now, "state": current_state}
        print(f"✅ LINE 通知已發送 ({reason}) | 血糖: {glucose}")

def update_latest_data(cgm):
    global latest_data
    if cgm:
        latest_data["glucose"] = cgm.get("glucose")
        latest_data["trend"] = cgm.get("trend")
        latest_data["time"] = cgm.get("time")
        latest_data["iob"] = cgm.get("iob")
        latest_data["ai_advice"] = receiver.last_ai_advice or "目前無 AI 照護建議。"
        latest_data["error"] = None
    else:
        latest_data["error"] = "無法從 CareLink 取得血糖數據，請確認設定或重試。"

def background_cgm_fetcher():
    """背景執行緒：定期 (每 5 分鐘) 抓取 CareLink 數據"""
    print("🚀 背景 CareLink 血糖接收服務已啟動...")
    
    # 如果歷史紀錄中有數據，先在背景為最後一筆資料生成 Groq AI 分析，避免網頁剛載入時顯示「等待接收數據...」
    if receiver.history and not receiver.last_ai_advice:
        try:
            print("🧠 偵測到歷史紀錄，正在為最新歷史數據生成初始 Groq AI 分析...")
            last_cgm = receiver.history[-1]
            receiver.last_ai_advice = receiver.analyze_with_groq(
                last_cgm.get("glucose"), 
                last_cgm.get("trend"), 
                last_cgm.get("iob")
            )
            latest_data["ai_advice"] = receiver.last_ai_advice
            print(f"✅ 初始 AI 分析生成成功: {receiver.last_ai_advice}")
        except Exception as e:
            print(f"❌ 背景生成初始 AI 分析失敗: {e}")
    
    # 第一次執行前，若無本機 tokens，先嘗試執行登入
    if not receiver.tokens:
        print("🔑 找不到本機憑證 Token，嘗試透過 Selenium 進行初始認證...")
        receiver.ensure_authenticated()

    while True:
        try:
            latest_data["is_loading"] = True
            # 確保認證有效，必要時刷新或要求登入
            if receiver.ensure_authenticated():
                cgm = receiver.fetch_latest_cgm()
                if cgm:
                    receiver.add_to_history(cgm)
                    update_latest_data(cgm)
                    
                    # 偵測是否為新讀值，是的話才叫 Groq AI 進行分析
                    last_time = receiver.history[-2].get("time") if len(receiver.history) >= 2 else None
                    if not receiver.last_ai_advice or cgm["time"] != last_time:
                        print("🧠 偵測到全新血糖數據，發送 Groq AI 分析請求...")
                        receiver.last_ai_advice = receiver.analyze_with_groq(cgm['glucose'], cgm['trend'], cgm['iob'])
                        update_latest_data(cgm)
                        check_and_send_line_alert(cgm, receiver.last_ai_advice)
                else:
                    latest_data["error"] = "未能成功取得最新血糖數據。"
            else:
                latest_data["error"] = "認證失效且無法自動登入，請重試。"
        except Exception as e:
            print(f"❌ 背景抓取過程中發生錯誤: {e}")
            latest_data["error"] = f"背景錯誤: {str(e)}"
        finally:
            latest_data["is_loading"] = False
        
        time.sleep(300)

# 啟動背景執行緒
fetcher_thread = threading.Thread(target=background_cgm_fetcher, daemon=True)
fetcher_thread.start()

@app.route('/')
def index():
    """渲染主儀表板畫面"""
    return render_template('index.html')

@app.route('/api/cgm')
def get_cgm():
    """取得當前血糖資訊與歷史數據的 API"""
    return jsonify({
        "glucose": latest_data["glucose"],
        "trend": latest_data["trend"],
        "time": latest_data["time"],
        "iob": latest_data["iob"],
        "ai_advice": latest_data["ai_advice"],
        "is_loading": latest_data["is_loading"],
        "error": latest_data["error"],
        "history": receiver.history
    })

@app.route('/api/force_refresh', methods=['POST'])
def force_refresh():
    """強制手動重整數據的 API"""
    global latest_data
    if latest_data["is_loading"]:
        return jsonify({"status": "error", "message": "系統正在抓取數據中，請稍後..."}), 400
    
    def run_manual_refresh():
        try:
            latest_data["is_loading"] = True
            receiver.ensure_authenticated()
            cgm = receiver.fetch_latest_cgm()
            if cgm:
                receiver.add_to_history(cgm)
                # 手動強制重新產生 AI 分析
                print("🧠 手動強制觸發 Groq AI 分析...")
                receiver.last_ai_advice = receiver.analyze_with_groq(cgm['glucose'], cgm['trend'], cgm['iob'])
                update_latest_data(cgm)
                check_and_send_line_alert(cgm, receiver.last_ai_advice)
            else:
                latest_data["error"] = "手動抓取血糖數據失敗。"
        except Exception as e:
            latest_data["error"] = f"手動重整錯誤: {str(e)}"
        finally:
            latest_data["is_loading"] = False

    threading.Thread(target=run_manual_refresh).start()
    return jsonify({"status": "success", "message": "手動更新已觸發，請於幾秒後重新整理儀表板。"})

# ---------------------------------------------------------
# LINE Webhook (互動功能，使用者傳送訊息如 "血糖" / "報表")
# ---------------------------------------------------------
def reply_line_message(reply_token, text, image_url=None):
    if not LINE_ACCESS_TOKEN:
        print("[LINE] 跳過: 未設定 LINE_ACCESS_TOKEN")
        return
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN.strip()}",
        "Content-Type": "application/json"
    }
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
        print(f"[LINE Reply] 回覆狀態碼: {response.status_code}")
        if response.status_code != 200:
            print(f"❌ [LINE Reply 錯誤詳細內容]: {response.text}")
            # 若包含圖片被拒絕 (HTTP 400)，降級為僅發送純文字訊息
            if image_url and response.status_code == 400:
                print("🔄 圖片網址驗證失敗，降級為僅發送純文字訊息...")
                data["messages"] = [{"type": "text", "text": text}]
                res_retry = requests.post(url, headers=headers, json=data, timeout=10)
                print(f"[LINE Reply 降級狀態碼]: {res_retry.status_code}")
    except Exception as e:
        print(f"[LINE Reply 傳送例外]: {e}")

@app.route("/callback", methods=['POST'])
def line_callback():
    body = request.get_json() or {}
    print(f"[Webhook] Received: {json.dumps(body)}")
    try:
        for event in body.get('events', []):
            if event.get('type') == 'message' and event.get('message', {}).get('type') == 'text':
                user_msg = event['message']['text'].strip()
                reply_token = event.get('replyToken')
                print(f"[Message] User: {user_msg}")
                
                # 1. 檢查是否為手動輸入血糖數值 (例如 "血糖 125", "125", "125 mg/dL", "血糖: 125")
                bg_match = re.search(r'^(?:血糖|bg|cgm)?\s*[:：=]?\s*(\d{2,3})\s*(?:mg/dl)?$', user_msg, re.IGNORECASE)
                
                if bg_match and user_msg not in ["血糖", "bg", "cgm"]:
                    bg_val = int(bg_match.group(1))
                    if 40 <= bg_val <= 400:
                        now_dt = datetime.now(timezone(timedelta(hours=8)))
                        now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')
                        cgm_entry = {
                            "glucose": bg_val,
                            "trend": "➡️ 手動紀錄",
                            "time": now_str,
                            "iob": 0.0
                        }
                        receiver.add_to_history(cgm_entry)
                        update_latest_data(cgm_entry)
                        
                        # 發送 Groq AI 分析
                        ai_advice = receiver.analyze_with_groq(bg_val, "➡️ 手動紀錄", 0.0)
                        receiver.last_ai_advice = ai_advice
                        
                        chart_url = None
                        if generate_line_chart():
                            now_ts = int(time.time())
                            chart_url = f"{BASE_URL}/static/line_chart.png?t={now_ts}"
                            
                        msg = (
                            f"【🩸 手動血糖紀錄成功】\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"🩸 數值: {bg_val} mg/dL\n"
                            f"⏰ 時間: {now_str[11:16]}\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"🤖 AI 照護建議:\n"
                            f"{ai_advice}"
                        )
                        reply_line_message(reply_token, msg, chart_url)
                        print(f"✅ 已成功紀錄並回覆手動血糖: {bg_val} mg/dL")
                        continue
                
                # 2. 查詢當前血糖
                if "血糖" in user_msg or user_msg.lower() in ["bg", "cgm", "blood glucose"]:
                    if receiver.history:
                        entry = receiver.history[-1]
                        try:
                            dt_utc = datetime.fromisoformat(entry['time'].replace('Z', '+00:00'))
                            local_time = dt_utc.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M')
                        except:
                            local_time = entry['time']
                            
                        chart_url = None
                        if generate_line_chart():
                            now_ts = int(time.time())
                            chart_url = f"{BASE_URL}/static/line_chart.png?t={now_ts}"
                            
                        trend_emoji = entry.get("trend", "➡️ 平穩")
                        msg = (
                            f"【即時查詢】\n"
                            f"🩸 血糖數值: {entry['glucose']} mg/dL\n"
                            f"📈 趨勢: {trend_emoji}\n"
                            f"⏰ 數據時間: {local_time}\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"🤖 AI 照護建議:\n"
                            f"{receiver.last_ai_advice or '目前尚無建議。'}"
                        )
                        reply_line_message(reply_token, msg, chart_url)
                        print(f"✅ 已回覆即時血糖數據與趨勢圖！")
                    else:
                        reply_line_message(reply_token, "目前歷史紀錄中沒有任何血糖數據。")
                        
                elif any(k in user_msg for k in ["報表", "報告", "report", "統計"]):
                    stats = get_daily_stats(24)
                    if stats:
                        chart_url = None
                        if generate_summary_chart(24):
                            now_ts = int(time.time())
                            chart_url = f"{BASE_URL}/static/summary_chart.png?t={now_ts}"
                            
                        msg = (
                            f"📊 【過去 24 小時報表】\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"🔹 平均血糖: {stats['avg']} mg/dL\n"
                            f"🔹 TIR (範圍內): {stats['tir']}%\n"
                            f"🔹 預估 A1C (GMI): {stats['gmi']}%\n"
                            f"🔹 偏高比例: {stats['high']}%\n"
                            f"🔹 偏低比例: {stats['low']}%\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"過去 24 小時共記錄 {stats['count']} 次數據。"
                        )
                        reply_line_message(reply_token, msg, chart_url)
                        print(f"✅ 已回覆 24 小時結算報告與摘要圖！")
                    else:
                        reply_line_message(reply_token, "過去 24 小時沒有足夠的血糖數據。")
    except Exception as e:
        print(f"[Webhook Error] {e}")
    return "OK"

@app.route('/api/v1/daily_report', methods=['GET'])
def trigger_daily_report():
    token = request.args.get('token')
    if token != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
        
    stats = get_daily_stats(24)
    if stats:
        chart_url = None
        if generate_summary_chart(24):
            now_ts = int(time.time())
            chart_url = f"{BASE_URL}/static/summary_chart.png?t={now_ts}"
            
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
        send_line_message(msg)
        if chart_url:
            url = "https://api.line.me/v2/bot/message/broadcast"
            headers = {
                "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }
            data = {
                "messages": [
                    {
                        "type": "image",
                        "originalContentUrl": chart_url,
                        "previewImageUrl": chart_url
                    }
                ]
            }
            try:
                requests.post(url, headers=headers, json=data, timeout=10)
            except Exception as e:
                print(f"[LINE Chart Send Error] {e}")
        return jsonify({"status": "success", "stats": stats})
    return jsonify({"status": "no_data"}), 200

@app.route('/api/v1/tts')
def get_tts():
    import edge_tts
    import asyncio
    text = request.args.get('text', '血糖正常')
    voice = "zh-TW-HsiaoChenNeural"
    
    os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)
    output_path = os.path.join(app.root_path, 'static', 'voice.mp3')
    
    async def amain():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        
    try:
        asyncio.run(amain())
        return send_file(output_path, mimetype="audio/mpeg")
    except Exception as e:
        print(f"[TTS Error] {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # 關閉 Flask debug 模式以防啟動雙執行緒
    app.run(host='127.0.0.1', port=5000, debug=False)
