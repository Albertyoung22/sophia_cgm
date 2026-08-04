# -*- coding: utf-8 -*-
import sys
import threading
import time
import os
from flask import Flask, jsonify, render_template

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
    "glucose": None,
    "trend": "➡️ 平穩",
    "time": "尚未更新",
    "iob": 0.0,
    "ai_advice": "等待接收數據...",
    "is_loading": False,
    "error": None
}

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
                    
                    # 偵測是否為新讀值，是的話才叫 Groq AI 進行分析
                    last_time = receiver.history[-2].get("time") if len(receiver.history) >= 2 else None
                    if not receiver.last_ai_advice or cgm["time"] != last_time:
                        print("🧠 偵測到全新血糖數據，發送 Groq AI 分析請求...")
                        receiver.last_ai_advice = receiver.analyze_with_groq(cgm['glucose'], cgm['trend'], cgm['iob'])
                    
                    update_latest_data(cgm)
                else:
                    latest_data["error"] = "未能成功取得最新血糖數據。"
            else:
                latest_data["error"] = "認證失效且無法自動登入，請重試。"
        except Exception as e:
            print(f"❌ 背景抓取過程中發生錯誤: {e}")
            latest_data["error"] = f"背景錯誤: {str(e)}"
        finally:
            latest_data["is_loading"] = False
        
        # 每 300 秒 (5 分鐘) 輪詢一次
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
            else:
                latest_data["error"] = "手動抓取血糖數據失敗。"
        except Exception as e:
            latest_data["error"] = f"手動重整錯誤: {str(e)}"
        finally:
            latest_data["is_loading"] = False

    threading.Thread(target=run_manual_refresh).start()
    return jsonify({"status": "success", "message": "手動更新已觸發，請於幾秒後重新整理儀表板。"})

if __name__ == '__main__':
    # 關閉 Flask debug 模式以防啟動雙執行緒
    app.run(host='127.0.0.1', port=5000, debug=False)
