# -*- coding: utf-8 -*-
import sys
import os
import urllib.parse
from carelink_receiver import TaiwanCareLinkReceiver

# 解決 Windows 終端機 CP950 編碼無法輸出 Emoji 的問題
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

def main():
    print("=========================================================")
    print("      美敦力 CareLink Personal 手動瀏覽器登入小助手")
    print("=========================================================")
    print("由於雲端或自動化瀏覽器會被 Google 圖形驗證 (reCAPTCHA) 阻擋，")
    print("本程式將產生的登入網址提供給您，讓您在「平常使用的瀏覽器」中登入。")
    print("瀏覽器具有您的 Google 登入狀態與信任度，圖形驗證將非常容易通過！")
    print("---------------------------------------------------------")

    receiver = TaiwanCareLinkReceiver()
    
    # 產生登入授權網址
    auth_url = (
        f"https://{receiver.AUTH_HOST}/authorize?"
        f"client_id={receiver.CLIENT_ID}&"
        f"response_type=code&"
        f"scope={urllib.parse.quote(receiver.SCOPE)}&"
        f"redirect_uri={urllib.parse.quote(receiver.REDIRECT_URI)}&"
        f"audience={urllib.parse.quote(receiver.AUDIENCE)}"
    )

    print("\n👉 第一步：請複製以下網址，在您平常使用的瀏覽器（如 Chrome/Edge/Safari）中開啟並登入：\n")
    print(auth_url)
    print("\n---------------------------------------------------------")
    print("💡 登入完成後，瀏覽器頁面會顯示「無法連線/找不到網頁」（因為重導向至 com.medtronic.carepartner 協定）。")
    print("這是【正常現象】，請不用擔心！")
    print("👉 第二步：此時請複製「瀏覽器網址列」的整串網址（包含 code=...），並貼在下方：")
    print("---------------------------------------------------------\n")

    try:
        redirected_url = input("請貼上重導向後的完整網址: ").strip()
        if not redirected_url:
            print("❌ 輸入內容為空，登入失敗。")
            return

        # 解析網址中的 code 參數
        parsed_url = urllib.parse.urlparse(redirected_url)
        params = urllib.parse.parse_qs(parsed_url.query)
        
        # 若使用者直接貼上整串 query 或只貼了 code 也行
        if not params and "code=" in redirected_url:
            # 嘗試重新包裝以利解析
            if not redirected_url.startswith("http"):
                redirected_url = "http://localhost/?" + redirected_url
            parsed_url = urllib.parse.urlparse(redirected_url)
            params = urllib.parse.parse_qs(parsed_url.query)

        auth_code = params.get("code", [None])[0]
        
        if not auth_code:
            # 最後一搏：若是使用者直接貼了 code
            if len(redirected_url) > 20 and "=" not in redirected_url and "/" not in redirected_url:
                auth_code = redirected_url
            else:
                print("❌ 無法從您貼上的網址中解析出 'code' 參數，請確認是否複製完整。")
                return

        print(f"\n🔑 成功擷取到授權碼 (Code): {auth_code[:10]}...")
        print("🔄 正在向美敦力伺服器交換 Token...")
        
        success = receiver._exchange_code_for_tokens(auth_code)
        if success:
            print("\n🎉 登入認證成功！憑證已成功儲存至本機檔案 .carelink_tokens.json。")
            print("您現在可以：")
            print("1. 打開 .carelink_tokens.json 複製其中的 refresh_token，將其設定至 Render 的環境變數 CARELINK_REFRESH_TOKEN 中。")
            print("2. 重新啟動本地 app.py 進行本機測試。")
        else:
            print("\n❌ 交換 Token 失敗，請確認授權碼是否過期（必須在登入完成後 1-2 分鐘內貼上）。")

    except KeyboardInterrupt:
        print("\n操作已取消。")
    except Exception as e:
        print(f"\n❌ 發生異常錯誤: {e}")

if __name__ == "__main__":
    main()
