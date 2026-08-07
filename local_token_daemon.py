# -*- coding: utf-8 -*-
"""
美敦力 CareLink 本機憑證常駐維護服務 (Local Auth Daemon)
功能：
1. 本機電腦背景常駐執行，維護 CareLink Auth0 登入狀態。
2. 當憑證即將過期或失效時，自動透過刷新或本機 Chrome (Selenium) 重新認證。
3. 將最新取得的憑證同步發送至 MongoDB 雲端資料庫，供 Render 上運行的服務取用。
"""

import sys
import os
import time
import logging
from datetime import datetime

# 解決 Windows 終端機 CP950 編碼無法輸出 Emoji 的問題
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

from carelink_receiver import TaiwanCareLinkReceiver


def run_daemon(check_interval_seconds=1800):
    """
    啟動本機憑證維護迴圈
    :param check_interval_seconds: 檢查間隔時間（預設 1800 秒 = 30 分鐘）
    """
    print("\n" + "=" * 60)
    print(" 🇹🇼 美敦力 CareLink 本機憑證常駐維護服務 (Local Auth Daemon)")
    print(" 💡 本腳本將負責本機登入維護，並自動將憑證同步至 MongoDB 雲端資料庫")
    print("=" * 60 + "\n")

    receiver = TaiwanCareLinkReceiver()

    # 檢查 MongoDB 連線狀態
    db = receiver._get_mongo_client()
    if db is not None:
        logging.info("✅ 已成功連接至 MongoDB 雲端資料庫，將為 Render 提供即時憑證同步。")
    else:
        logging.warning("⚠️ 未偵測到有效的 MONGO_URI 設定，憑證將僅儲存於本機檔案。如需供 Render 使用，請於 .env 設定 MONGO_URI。")

    logging.info(f"🔄 常駐服務已啟動，每 {check_interval_seconds // 60} 分鐘檢查一次 CareLink 憑證有效性...\n")

    try:
        while True:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logging.info(f"🔍 [{now_str}] 正在檢查 CareLink 憑證狀態...")

            try:
                # ensure_authenticated 會在過期時優先刷新，刷新失敗時啟動 Selenium 登入
                # 登入/刷新成功後會自動調用 _save_tokens 同步至 MongoDB
                success = receiver.ensure_authenticated()

                if success:
                    expires_at = receiver.tokens.get("expires_at", 0)
                    if expires_at > 0:
                        remaining_min = int((expires_at - time.time()) / 60)
                        logging.info(f"🎉 憑證狀態正常！Access Token 尚有約 {remaining_min} 分鐘有效期 (已同步至 MongoDB)。")
                    else:
                        logging.info("🎉 憑證狀態正常 (已同步至 MongoDB)。")
                else:
                    logging.error("❌ 憑證驗證與登入失敗，將於下一次迴圈重新嘗試。")

            except Exception as ex:
                logging.error(f"❌ 檢查憑證時發生例外狀況: {ex}")

            # 休眠等待下一次檢查
            time.sleep(check_interval_seconds)

    except KeyboardInterrupt:
        print("\n👋 已終止本機 CareLink 憑證維護服務。")


if __name__ == "__main__":
    # 可傳入自訂檢查間隔時間（單位：秒）
    run_daemon(check_interval_seconds=1800)
