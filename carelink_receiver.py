# -*- coding: utf-8 -*-
"""
美敦力 Medtronic CareLink 台灣地區 (TW) 血糖數據接收程式
支援 Auth0 OAuth 2.0 雲端認證、自動 reCAPTCHA 勾選、Chrome Performance Log 擷取授權碼與 Groq AI 輔助。
"""

import sys
import os
import re
import time
import json
import logging
import urllib.parse
from datetime import datetime
import requests
from openai import OpenAI

# 解決 Windows 終端機 CP950 編碼無法輸出 Emoji 的問題
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# 設定日誌格式
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE_PATH = os.path.join(BASE_DIR, '.env')
TOKEN_FILE_PATH = os.path.join(BASE_DIR, '.carelink_tokens.json')


def load_env():
    """讀取專案目錄下的 .env 設定"""
    env_vars = {}
    if os.path.exists(ENV_FILE_PATH):
        try:
            with open(ENV_FILE_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        env_vars[key.strip()] = value.strip()
        except Exception:
            pass
    return env_vars


class TaiwanCareLinkReceiver:
    # 台灣/國際區 CareLink 服務端點與 Auth0 參數 (相容於 xDrip+ 架構)
    DISCOVERY_URL = "https://clcloud.minimed.eu/connect/carepartner/v13/discover/android/3.6"
    DEFAULT_SSO_CONFIG_URL = "https://carelink.minimed.eu/configs/v1/carepartner_auth0_ous_sso_config_v1.json"
    
    # Auth0 預設 OAuth2 參數
    CLIENT_ID = "PeAhkbhQWlQRxJiQxWfcFBiGus1lxfe9"
    AUTH_HOST = "carelink-login.minimed.eu"
    SCOPE = "profile openid offline_access"
    REDIRECT_URI = "com.medtronic.carepartner:/sso"
    AUDIENCE = "carepartner.patient.ous"
    
    CLOUD_URL = "https://clcloud.minimed.eu/connect/carepartner/v13"

    # 趨勢符號對照表
    TREND_MAP = {
        "NONE": "➡️ 平穩",
        "FLAT": "➡️ 平穩",
        "TRIPLE_UP": "⬆️⬆️⬆️ 急升",
        "DOUBLE_UP": "⬆️⬆️ 快速上升",
        "SINGLE_UP": "⬆️ 上升",
        "FORTY_FIVE_UP": "↗️ 緩升",
        "FORTY_FIVE_DOWN": "↘️ 緩降",
        "SINGLE_DOWN": "⬇️ 下降",
        "DOUBLE_DOWN": "⬇️⬇️ 快速下降",
        "TRIPLE_DOWN": "⬇️⬇️⬇️ 急降",
    }

    def __init__(self, username=None, password=None, country_code="tw"):
        env = load_env()
        self.username = username or env.get("CARELINK_USERNAME") or os.environ.get("CARELINK_USERNAME") or "Sophiafa"
        self.password = password or env.get("CARELINK_PASSWORD") or os.environ.get("CARELINK_PASSWORD") or "20151120"
        self.country = (country_code or env.get("CARELINK_COUNTRY") or os.environ.get("CARELINK_COUNTRY") or "tw").lower()
        self.groq_api_key = env.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        
        self.mongo_uri = env.get("MONGO_URI") or env.get("MONGO_CONNECTION")
        self.tokens = self._load_tokens()
        
        self.groq_client = OpenAI(
            api_key=self.groq_api_key,
            base_url="https://api.groq.com/openai/v1"
        ) if self.groq_api_key else None
        
        self.history_file = os.path.join(BASE_DIR, '.carelink_history.json')
        self.history = self._load_history()
        self.last_ai_advice = ""

    def _load_history(self):
        """載入歷史血糖記錄"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"無法讀取歷史檔案: {e}")
        return []

    def _save_history(self):
        """儲存歷史血糖記錄"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"儲存歷史檔案失敗: {e}")

    def add_to_history(self, cgm_data):
        """將最新資料加入歷史記錄，避免重複並限制最大長度 (例如 144 筆，相當於12小時)"""
        if not cgm_data or not cgm_data.get("time"):
            return
        
        # 檢查是否已存在相同時間的紀錄
        if self.history and self.history[-1].get("time") == cgm_data["time"]:
            return
            
        # 建立簡化版的歷史紀錄
        history_entry = {
            "glucose": cgm_data["glucose"],
            "trend": cgm_data["trend"],
            "time": cgm_data["time"],
            "iob": cgm_data["iob"]
        }
        self.history.append(history_entry)
        
        # 限制最大長度為 144 筆 (約12小時的 5分鐘 間隔資料)
        if len(self.history) > 144:
            self.history = self.history[-144:]
            
        self._save_history()

    def analyze_with_groq(self, glucose, trend, iob):
        """使用 Groq AI 對當前血糖數據進行分析並提供叮嚀"""
        if not self.groq_client:
            return "⚠️ 未設定 GROQ_API_KEY，無法使用 AI 分析助理。"

        prompt = f"""
你是一位專業的內分泌科醫生與糖尿病照護 AI 助理。請針對使用者的當前血糖數據進行簡短分析並給予溫馨的日常叮嚀。
當前血糖數值: {glucose} mg/dL
血糖趨勢: {trend}
活性胰島素 (IOB): {iob} U
目前時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

要求：
1. 必須以繁體中文 (zh-TW) 回覆。
2. 內容要簡潔、實用、語氣溫和，控制在 2-3 句話以內。
3. 如果血糖偏低 (<70 mg/dL) 或偏高 (>250 mg/dL)，請明確給予警示與具體應對建議。
"""
        try:
            response = self.groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.7
            )
            ai_advice = response.choices[0].message.content.strip()
            return ai_advice
        except Exception as e:
            logging.error(f"Groq AI 分析異常: {e}")
            return "⚠️ AI 助理分析時發生異常，請稍後重試。"

    def _get_mongo_client(self):
        if not self.mongo_uri:
            return None
        try:
            from pymongo import MongoClient
            client = MongoClient(self.mongo_uri)
            try:
                db = client.get_default_database()
            except Exception:
                db = None
            if db is None:
                db = client["nightscout"]
            return db
        except Exception as e:
            logging.error(f"MongoDB連線錯誤: {e}")
            return None

    def _load_tokens(self):
        """讀取儲存的 Token 憑證（優先從 MongoDB 讀取，其次本機 JSON 檔，最後環境變數）"""
        # 1. 嘗試從 MongoDB 載入
        db = self._get_mongo_client()
        if db is not None:
            try:
                doc = db.carelink_tokens.find_one({"key": "carelink_credentials"})
                if doc and doc.get("tokens") and doc["tokens"].get("refresh_token"):
                    logging.info("🔑 成功從 MongoDB 載入 CareLink 憑證。")
                    return doc["tokens"]
            except Exception as e:
                logging.warning(f"從 MongoDB 載入憑證失敗: {e}")

        # 2. 嘗試從本機檔案載入
        tokens = {}
        if os.path.exists(TOKEN_FILE_PATH):
            try:
                with open(TOKEN_FILE_PATH, 'r', encoding='utf-8') as f:
                    tokens = json.load(f)
            except Exception as e:
                logging.warning(f"無法讀取 Token 檔案: {e}")
        
        # 3. 嘗試從環境變數載入以利初始設定 (Render)
        if not tokens.get("refresh_token"):
            env_refresh_token = os.environ.get("CARELINK_REFRESH_TOKEN")
            if env_refresh_token:
                logging.info("🔑 從環境變數 CARELINK_REFRESH_TOKEN 載入 Refresh Token...")
                tokens = {
                    "access_token": "",
                    "refresh_token": env_refresh_token,
                    "expires_at": 0
                }
        return tokens

    def _save_tokens(self, tokens):
        """儲存 Token 憑證（同步寫入 MongoDB 與本機 JSON 檔）"""
        self.tokens = tokens
        
        # 1. 嘗試同步到 MongoDB
        db = self._get_mongo_client()
        if db is not None:
            try:
                db.carelink_tokens.replace_one(
                    {"key": "carelink_credentials"},
                    {"key": "carelink_credentials", "tokens": tokens, "updated_at": time.time()},
                    upsert=True
                )
                logging.info("💾 CareLink 憑證已同步儲存至 MongoDB。")
            except Exception as e:
                logging.error(f"儲存憑證至 MongoDB 失敗: {e}")

        # 2. 儲存至本地檔案
        try:
            with open(TOKEN_FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(tokens, f, ensure_ascii=False, indent=2)
            logging.info("💾 CareLink 憑證已儲存至本機檔案。")
        except Exception as e:
            logging.error(f"儲存憑證至本機檔案失敗: {e}")

    def is_authenticated(self):
        """檢查目前的 Access Token 是否存在且未過期"""
        if not self.tokens or "access_token" not in self.tokens:
            return False
        expires_at = self.tokens.get("expires_at", 0)
        # 預留 60 秒緩衝期
        return time.time() < (expires_at - 60)

    def refresh_access_token(self):
        """使用 refresh_token 刷新 access_token"""
        refresh_token = self.tokens.get("refresh_token")
        if not refresh_token:
            logging.warning("⚠️ 找不到 refresh_token，需要重新進行 Auth0 登入認證。")
            return False

        logging.info("🔄 嘗試使用 Refresh Token 刷新存取憑證...")
        token_url = f"https://{self.AUTH_HOST}/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.CLIENT_ID,
            "refresh_token": refresh_token
        }

        try:
            res = self.session.post(token_url, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json()
                new_tokens = {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", refresh_token),
                    "expires_in": data.get("expires_in", 86400),
                    "expires_at": time.time() + data.get("expires_in", 86400)
                }
                self._save_tokens(new_tokens)
                logging.info("✅ 憑證 Token 刷新成功！")
                return True
            else:
                logging.error(f"❌ 憑證刷新失敗 (HTTP {res.status_code}): {res.text}")
                return False
        except Exception as e:
            logging.error(f"❌ 憑證刷新發生例外: {e}")
            return False

    def _extract_code_from_string(self, text):
        """從 URL 或文字字串中解析 code= 參數"""
        if not text:
            return None
        match = re.search(r'code=([a-zA-Z0-9_\-\.]+)', text)
        if match:
            code = match.group(1)
            if len(code) > 10:
                return code
        return None

    def _try_click_recaptcha(self, driver):
        """嘗試切換至 reCAPTCHA iframe 並自動勾選『我不是機器人』"""
        try:
            from selenium.webdriver.common.by import By
            iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha'], iframe[title*='recaptcha'], iframe[title*='機器人']")
            for iframe in iframes:
                try:
                    driver.switch_to.frame(iframe)
                    checkboxes = driver.find_elements(By.CSS_SELECTOR, ".recaptcha-checkbox-border, #recaptcha-anchor, div.recaptcha-checkbox-checkmark")
                    if checkboxes:
                        checkboxes[0].click()
                        logging.info("🤖 已自動觸發 reCAPTCHA『我不是機器人』勾選框！")
                        driver.switch_to.default_content()
                        return True
                    driver.switch_to.default_content()
                except Exception:
                    driver.switch_to.default_content()
        except Exception:
            pass
        return False

    def login_with_selenium(self):
        """使用 Selenium Chrome 模擬開啟 Auth0 登入網頁並取得授權碼 (authorization code)"""
        logging.info("🌐 正在啟動 Selenium Chrome 完成 CareLink Auth0 網頁登入...")

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
        except ImportError:
            logging.error("❌ 缺少 selenium 或 webdriver-manager 模組，請執行 pip install selenium webdriver-manager")
            return False

        auth_url = (
            f"https://{self.AUTH_HOST}/authorize?"
            f"client_id={self.CLIENT_ID}&"
            f"response_type=code&"
            f"scope={urllib.parse.quote(self.SCOPE)}&"
            f"redirect_uri={urllib.parse.quote(self.REDIRECT_URI)}&"
            f"audience={urllib.parse.quote(self.AUDIENCE)}"
        )

        options = webdriver.ChromeOptions()
        # 開啟 Performance 網路日誌以捕捉未註冊 Schema (com.medtronic.carepartner:/sso) 的 302 重導向 URL
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--log-level=3')

        driver = None
        auth_code = None

        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            driver.get(auth_url)

            logging.info("⏳ 載入 CareLink Auth0 登入頁面中...")

            # 等待輸入框出現並嘗試自動輸入帳號密碼
            try:
                username_elem = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='username'], input[type='email'], input#username"))
                )
                username_elem.clear()
                username_elem.send_keys(self.username)

                password_elem = driver.find_element(By.CSS_SELECTOR, "input[name='password'], input[type='password'], input#password")
                password_elem.clear()
                password_elem.send_keys(self.password)

                # 嘗試自動點擊 reCAPTCHA 勾選框
                time.sleep(1)
                self._try_click_recaptcha(driver)
                time.sleep(1)

                login_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], button[name='action'], button#btn-login, input[type='submit']")
                login_btn.click()
                logging.info("🔑 已發送帳密登入資訊，等待轉址驗證 (如彈出圖片圖片驗證挑戰，請在瀏覽器中協助點選)...")
            except Exception as ex:
                logging.info(f"💡 請在開啟的 Chrome 視窗中完成登入/驗證操作: {ex}")

            # 監控瀏覽器網址與網路日誌 (包含捕捉桌面版 Chrome 無法直接開啟的自訂 Scheme 重導向)
            max_wait_seconds = 120
            start_time = time.time()
            recaptcha_clicked = False

            while time.time() - start_time < max_wait_seconds:
                # 嘗試動態自動勾選 reCAPTCHA
                if not recaptcha_clicked:
                    recaptcha_clicked = self._try_click_recaptcha(driver)
                    if recaptcha_clicked:
                        try:
                            # 勾選後自動補按登入按鈕
                            time.sleep(1)
                            login_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], button[name='action'], button#btn-login, input[type='submit']")
                            login_btn.click()
                        except Exception:
                            pass

                # 1. 檢查 current_url
                try:
                    current_url = driver.current_url
                    auth_code = self._extract_code_from_string(current_url)
                    if auth_code:
                        logging.info("🎉 從 current_url 成功擷取 Authorization Code！")
                        break
                except Exception:
                    pass

                # 2. 檢查 page_source
                try:
                    source = driver.page_source
                    if "code=" in source:
                        auth_code = self._extract_code_from_string(source)
                        if auth_code:
                            logging.info("🎉 從頁面內容成功擷取 Authorization Code！")
                            break
                except Exception:
                    pass

                # 3. 檢查 Chrome Performance Logs (針對 302 重導向 com.medtronic.carepartner:/sso?code=...)
                try:
                    logs = driver.get_log("performance")
                    for entry in logs:
                        message_str = entry.get("message", "")
                        if "code=" in message_str and ("carepartner" in message_str or "sso" in message_str or "redirect" in message_str):
                            auth_code = self._extract_code_from_string(message_str)
                            if auth_code:
                                logging.info("🎉 從 Chrome Performance 網路日誌成功擷取 Authorization Code！")
                                break
                    if auth_code:
                        break
                except Exception:
                    pass

                time.sleep(1)

        except Exception as e:
            logging.error(f"❌ Selenium 登入過程中發生異常: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        if not auth_code:
            logging.error("❌ 未能在指定時間內取得 Auth0 授權碼。")
            return False

        # 使用 Authorization Code 換取 Access Token & Refresh Token
        return self._exchange_code_for_tokens(auth_code)

    def _exchange_code_for_tokens(self, auth_code):
        """使用 OAuth2 Authorization Code 換取 Token"""
        token_url = f"https://{self.AUTH_HOST}/oauth/token"
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.CLIENT_ID,
            "code": auth_code,
            "redirect_uri": self.REDIRECT_URI
        }

        try:
            res = self.session.post(token_url, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json()
                tokens = {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token"),
                    "expires_in": data.get("expires_in", 86400),
                    "expires_at": time.time() + data.get("expires_in", 86400)
                }
                self._save_tokens(tokens)
                logging.info("✅ 成功完成 Auth0 SSO 認證並取得 Token！")
                return True
            else:
                logging.error(f"❌ Code 換取 Token 失敗 (HTTP {res.status_code}): {res.text}")
                return False
        except Exception as e:
            logging.error(f"❌ 換取 Token 時發生例外: {e}")
            return False

    def ensure_authenticated(self):
        """確保登入狀態，必要時自動刷新或重新登入"""
        if self.is_authenticated():
            return True

        # 嘗試刷新
        if self.refresh_access_token():
            return True

        # 若刷新失敗，執行 Selenium 登入
        return self.login_with_selenium()

    def fetch_latest_cgm(self):
        """抓取美敦力 CareLink 最新 CGM 血糖資料"""
        if not self.ensure_authenticated():
            logging.error("❌ 認證失敗，無法發送 CGM 數據請求。")
            return None

        # 1. 取得關聯的患者列表以獲得 patientId 與裝置類型
        patients_url = "https://carelink.minimed.eu/api/carepartner/v2/links/patients"
        headers = {
            "Authorization": f"Bearer {self.tokens.get('access_token')}",
            "Accept": "application/json, text/plain, */*"
        }

        try:
            # 檢查 / 刷新 Token 是否可用
            res = self.session.get(patients_url, headers=headers, timeout=15)
            
            # 若 Token 過期 (401/403)，強制刷新後重試
            if res.status_code in [401, 403]:
                logging.warning("⚠️ Access Token 無效或已過期，嘗試重新認證...")
                if self.refresh_access_token() or self.login_with_selenium():
                    headers["Authorization"] = f"Bearer {self.tokens.get('access_token')}"
                    res = self.session.get(patients_url, headers=headers, timeout=15)
                else:
                    return None

            if res.status_code == 200:
                patients = res.json()
                if not isinstance(patients, list) or len(patients) == 0:
                    logging.error("❌ 關聯的患者列表為空。")
                    return None
                
                patient = patients[0]
                patient_id = patient.get("username")
                device_family = patient.get("lastDeviceFamily", "").upper()
                
                # 判斷是否為 BLE / SIMPLERA 裝置，決定使用哪種 API 端點
                is_ble = "BLE" in device_family or "SIMPLERA" in device_family or "INSTINCT" in device_family
                
                res_data = None
                if is_ble:
                    # 使用新的 display/message POST 端點
                    data_url = f"{self.CLOUD_URL}/display/message"
                    post_headers = {
                        "Authorization": f"Bearer {self.tokens.get('access_token')}",
                        "Accept": "application/json, text/plain, */*",
                        "Content-Type": "application/json; charset=utf-8",
                        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Mobile Safari/537.36",
                        "Sec-Ch-Ua": '"Google Chrome";v="117", "Not;A=Brand";v="8", "Chromium";v="117"'
                    }
                    payload = {
                        "username": self.username,
                        "role": "carepartner",
                        "patientId": patient_id,
                        "appVersion": "3.6.0"
                    }
                    logging.info(f"📡 檢測到患者使用 BLE 裝置 ({device_family})，使用 display/message POST 端點...")
                    res_data = self.session.post(data_url, headers=post_headers, json=payload, timeout=15)
                else:
                    # 檢測到為 GUARDIAN 裝置，使用 M2M GET 端點
                    data_url = f"https://clcloud.minimed.eu/patient/m2m/connect/data/gc/patients/{patient_id}"
                    logging.info(f"📡 檢測到患者使用 GUARDIAN 裝置 ({device_family})，使用 M2M GET 端點...")
                    res_data = self.session.get(data_url, headers=headers, timeout=15)

                if res_data and res_data.status_code == 200:
                    try:
                        json_data = res_data.json()
                        return self._parse_cgm_json(json_data)
                    except ValueError as je:
                        content_type = res_data.headers.get('Content-Type', '')
                        preview = res_data.text[:200].replace('\n', ' ')
                        logging.error(f"❌ 響應內容並非有效的 JSON 格式。Content-Type: {content_type}, 前200字元: {preview}")
                        raise je
                elif res_data and res_data.status_code == 204:
                    logging.warning("⚠️ 伺服器返回 204 No Content，暫無新數據。")
                    return None
                else:
                    status = res_data.status_code if res_data else "Unknown"
                    text = res_data.text if res_data else ""
                    logging.error(f"❌ 讀取血糖數據失敗 (HTTP {status}): {text}")
                    return None
            else:
                logging.error(f"❌ 無法讀取患者列表 (HTTP {res.status_code}): {res.text}")
                return None

        except Exception as e:
            logging.error(f"❌ 擷取血糖數據異常: {e}")
            
            # 若為 JSON 格式錯誤或會話失效，可能需要清除憑證重試
            if "expecting value" in str(e).lower() or "json" in str(e).lower():
                logging.warning("⚠️ 會話可能在伺服器端失效，強制清除憑證以利下一次重啟重新認證...")
                self.tokens = {}
                if os.path.exists(TOKEN_FILE_PATH):
                    try:
                        os.remove(TOKEN_FILE_PATH)
                    except Exception as e_del:
                        logging.warning(f"清除舊憑證檔失敗: {e_del}")
            return None

    def _parse_cgm_json(self, data):
        """解析 JSON 數據內容"""
        try:
            # 如果是新版 display/message 回應，資料包裝在 patientData 欄位內
            if "patientData" in data:
                data = data["patientData"]

            sgl = data.get("lastSG", {}) or data.get("sgl", {})
            glucose = sgl.get("sg") or sgl.get("value")
            
            # 趨勢與時間
            trend_raw = sgl.get("trend") or data.get("lastSGTrend") or data.get("trend", "NONE")
            rec_time = sgl.get("datetime") or sgl.get("timestamp")

            # 活性胰島素 IOB
            iob = data.get("activeInsulin", {}).get("amount", 0.0) or data.get("pumpStatus", {}).get("activeInsulin", {}).get("amount", 0.0)

            trend_str = self.TREND_MAP.get(trend_raw, trend_raw)

            return {
                "glucose": glucose,
                "trend": trend_str,
                "time": rec_time,
                "iob": iob,
                "raw": data
            }
        except Exception as e:
            logging.error(f"JSON 解析失敗: {e}")
            return None

    def run_receiver_loop(self, poll_interval=300):
        """啟動定時接收迴圈 (預設 5 分鐘接收一次)"""
        print("\n" + "=" * 50)
        print(" 🇹🇼 台灣美敦力 CareLink 血糖數據即時接收服務 啟動 (Auth0 SSO)")
        print(f" 👤 使用者帳號: {self.username}")
        print("=" * 50 + "\n")

        if not self.ensure_authenticated():
            print("❌ 初始登入驗證失敗，程式停止。")
            return

        try:
            while True:
                cgm = self.fetch_latest_cgm()
                now_str = time.strftime('%Y-%m-%d %H:%M:%S')

                if cgm and cgm.get("glucose"):
                    # 記錄至歷史中
                    self.add_to_history(cgm)
                    
                    # 檢查是否為新讀值以執行 AI 分析
                    last_time = self.history[-2].get("time") if len(self.history) >= 2 else None
                    if not self.last_ai_advice or cgm["time"] != last_time:
                        logging.info("🧠 偵測到新血糖數據，啟動 Groq AI 照護建議分析...")
                        self.last_ai_advice = self.analyze_with_groq(cgm['glucose'], cgm['trend'], cgm['iob'])
                    
                    print(f"[{now_str}] 🩸 最新血糖報告：")
                    print(f"  ├─ 血糖數值:  {cgm['glucose']} mg/dL")
                    print(f"  ├─ 血糖趨勢:  {cgm['trend']}")
                    print(f"  ├─ 活性胰島素: {cgm['iob']} U")
                    print(f"  ├─ 資料時間:  {cgm['time']}")
                    print(f"  └─ AI 叮嚀:   {self.last_ai_advice}")
                    print("-" * 50)
                else:
                    print(f"[{now_str}] ⚠️ 暫無新血糖紀錄或等待 CareLink 數據更新...")

                time.sleep(poll_interval)

        except KeyboardInterrupt:
            print("\n已終止數據接收服務。")


if __name__ == "__main__":
    app = TaiwanCareLinkReceiver()
    app.run_receiver_loop(poll_interval=300)
