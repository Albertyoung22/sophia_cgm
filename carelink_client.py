import os
import json
import time
import requests
from datetime import datetime, timezone

CARELINK_USERNAME = os.environ.get("CARELINK_USERNAME", "Sophiafa")
CARELINK_PASSWORD = os.environ.get("CARELINK_PASSWORD", "20151120")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "logindata.json")
HOST = "carelink.minimed.eu"

class CareLinkClient:
    def __init__(self, username=CARELINK_USERNAME, password=CARELINK_PASSWORD):
        self.username = username
        self.password = password
        self.country = "TW"
        self.token_data = None
        self.last_fetch_time = None
        self.last_glucose = None
        self.last_trend = "Flat"
        self.last_status = "Initialized"
        self.session = requests.Session()
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def load_token(self):
        env_token = os.environ.get("CARELINK_TOKEN_JSON")
        if env_token:
            try:
                self.token_data = json.loads(env_token)
                with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.token_data, f, indent=4)
            except Exception as e:
                print(f"[CareLink] Failed to load token from CARELINK_TOKEN_JSON env: {e}")

        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                    self.token_data = json.load(f)
                    
                    if "cookies" in self.token_data:
                        for k, v in self.token_data["cookies"].items():
                            self.session.cookies.set(k, v, domain=".minimed.eu")
                            self.session.cookies.set(k, v, domain="carelink.minimed.eu")
                            self.session.cookies.set(k, v, domain="carelink-login.minimed.eu")

                    token_val = self.token_data.get("access_token")
                    if token_val and token_val != "web_session_active":
                        self.headers["Authorization"] = f"Bearer {token_val}"

                    return True
            except Exception as e:
                print(f"[CareLink] Failed to load token file: {e}")
        return False

    def parse_trend(self, trend_raw):
        mapping = {
            "NONE": "Flat",
            "FLAT": "Flat",
            "UP_SLOW": "FortyFiveUp",
            "UP": "SingleUp",
            "UP_FAST": "DoubleUp",
            "DOWN_SLOW": "FortyFiveDown",
            "DOWN": "SingleDown",
            "DOWN_FAST": "DoubleDown"
        }
        return mapping.get(str(trend_raw).upper(), "Flat")

    def login_with_chrome_window(self, username=None, password=None):
        uname = username or self.username or CARELINK_USERNAME
        pwd = password or self.password or CARELINK_PASSWORD

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from webdriver_manager.chrome import ChromeDriverManager

            opts = Options()
            opts.add_argument('--start-maximized')

            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=opts)
            driver.set_page_load_timeout(60)

            login_url = f"https://{HOST}/patient/sso/login?country=TW&lang=zh"
            print(f"[CareLink Window Login] Opening {login_url}...")
            driver.get(login_url)

            time.sleep(3)

            # 嘗試自動填入帳號與密碼
            try:
                user_input = None
                pass_input = None
                inputs = driver.find_elements(By.TAG_NAME, 'input')
                for inp in inputs:
                    inp_type = str(inp.get_attribute('type')).lower()
                    inp_name = str(inp.get_attribute('name')).lower()
                    inp_id = str(inp.get_attribute('id')).lower()
                    if inp_type in ['text', 'email'] or 'user' in inp_name or 'user' in inp_id or inp_id == 'username':
                        user_input = inp
                    elif inp_type == 'password' or 'pass' in inp_name or 'pass' in inp_id or inp_id == 'password':
                        pass_input = inp

                if user_input and pass_input:
                    js_set_val = """
                    function setVal(input, val) {
                        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        nativeSetter.call(input, val);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new Event('blur', { bubbles: true }));
                    }
                    setVal(arguments[0], arguments[2]);
                    setVal(arguments[1], arguments[3]);
                    """
                    driver.execute_script(js_set_val, user_input, pass_input, uname, pwd)
                    print("[CareLink Window Login] 已自動帶入帳號與密碼！")
            except Exception as fill_err:
                print(f"[CareLink Window Login Auto-Fill Warning] {fill_err}")

            print("[CareLink Window Login] 瀏覽器視窗已開啟，請通過人機驗證並點擊登入...")
            token_saved = False
            token_data = None

            # 輪詢最多 3 分鐘 (180 秒) 擷取 Cookie
            for sec in range(180):
                time.sleep(1)
                try:
                    cookies = driver.get_cookies()
                    cookie_dict = {c['name']: c['value'] for c in cookies}
                    curr_url = driver.current_url

                    if 'auth_tmp_token' in cookie_dict or ('patient' in curr_url and 'login' not in curr_url and len(cookie_dict) > 2):
                        auth_token = cookie_dict.get('auth_tmp_token') or 'web_session_active'
                        token_data = {
                            'access_token': auth_token,
                            'refresh_token': 'web_session_active',
                            'scope': 'profile openid roles country',
                            'client_id': '4fb211b8-f130-4398-b51e-28900bf68527',
                            'client_secret': '',
                            'mag-identifier': 'web-session',
                            'cookies': cookie_dict
                        }
                        token_saved = True
                        break
                except Exception:
                    break

            try:
                driver.quit()
            except Exception:
                pass

            if token_saved and token_data:
                with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                    json.dump(token_data, f, indent=4)
                self.token_data = token_data
                self.load_token()
                self.last_status = "連線成功"
                return True, "完成登入驗證，已擷取並寫入最新 Session Token！"
            return False, "逾時或未擷取到 Session Token"
        except Exception as e:
            return False, f"開啟 Chrome 視窗登入異常: {e}"

    def login_with_selenium(self, username=None, password=None):
        uname = username or self.username or CARELINK_USERNAME
        pwd = password or self.password or CARELINK_PASSWORD

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from webdriver_manager.chrome import ChromeDriverManager

            opts = Options()
            opts.add_argument('--headless=new')
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')
            opts.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=opts)
            driver.set_page_load_timeout(40)

            login_url = f"https://{HOST}/patient/sso/login?country=TW&lang=zh"
            print(f"[CareLink Auto-Login] Opening {login_url}...")
            driver.get(login_url)

            user_input = None
            pass_input = None
            for attempt in range(20):
                time.sleep(1)
                inputs = driver.find_elements(By.TAG_NAME, 'input')
                for inp in inputs:
                    inp_type = str(inp.get_attribute('type')).lower()
                    inp_name = str(inp.get_attribute('name')).lower()
                    inp_id = str(inp.get_attribute('id')).lower()
                    if inp_type in ['text', 'email'] or 'user' in inp_name or 'user' in inp_id or inp_id == 'username':
                        user_input = inp
                    elif inp_type == 'password' or 'pass' in inp_name or 'pass' in inp_id or inp_id == 'password':
                        pass_input = inp
                if user_input and pass_input:
                    break

            if not (user_input and pass_input):
                driver.quit()
                return False, "無法在 CareLink 登入頁面上定位輸入框 (需要人機驗證請使用視窗模式)"

            js_set_val = """
            function setVal(input, val) {
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(input, val);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.dispatchEvent(new Event('blur', { bubbles: true }));
            }
            setVal(arguments[0], arguments[2]);
            setVal(arguments[1], arguments[3]);
            """
            driver.execute_script(js_set_val, user_input, pass_input, uname, pwd)
            time.sleep(1)

            buttons = driver.find_elements(By.TAG_NAME, 'button')
            submit_btn = None
            for btn in buttons:
                btn_type = str(btn.get_attribute('type')).lower()
                btn_text = str(btn.text).lower()
                if btn_type == 'submit' or 'log' in btn_text or 'sign' in btn_text or '登入' in btn_text:
                    submit_btn = btn
                    break

            if submit_btn:
                driver.execute_script('arguments[0].click();', submit_btn)
            else:
                driver.execute_script('document.forms[0].submit();')

            token_saved = False
            token_data = None
            for sec in range(30):
                time.sleep(1)
                cookies = driver.get_cookies()
                cookie_dict = {c['name']: c['value'] for c in cookies}
                curr_url = driver.current_url
                if 'auth_tmp_token' in cookie_dict or ('patient' in curr_url and 'login' not in curr_url and len(cookie_dict) > 2):
                    auth_token = cookie_dict.get('auth_tmp_token') or 'web_session_active'
                    token_data = {
                        'access_token': auth_token,
                        'refresh_token': 'web_session_active',
                        'scope': 'profile openid roles country',
                        'client_id': '4fb211b8-f130-4398-b51e-28900bf68527',
                        'client_secret': '',
                        'mag-identifier': 'web-session',
                        'cookies': cookie_dict
                    }
                    token_saved = True
                    break

            driver.quit()

            if token_saved and token_data:
                with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                    json.dump(token_data, f, indent=4)
                self.token_data = token_data
                self.load_token()
                self.last_status = "連線成功"
                return True, "自動登入成功，已寫入最新 Session Token！"
            return False, f"已嘗試登入，因人機驗證 (reCAPTCHA) 請選擇 [🖥️ 開啟 Chrome 視窗登入] (URL: {curr_url})"
        except Exception as e:
            return False, f"Selenium 自動登入發生例外: {e}"

    def set_manual_token(self, token_str):
        if not token_str:
            return False, "Token 不能為空"
        
        token_str = token_str.strip()
        if token_str.startswith("{"):
            try:
                data = json.loads(token_str)
                if "cookies" in data and "auth_tmp_token" in data["cookies"]:
                    token_str = data["cookies"]["auth_tmp_token"]
                elif "access_token" in data:
                    token_str = data["access_token"]
            except Exception:
                pass

        token_data = {
            "access_token": token_str,
            "refresh_token": "web_session_active",
            "scope": "profile openid roles country",
            "client_id": "4fb211b8-f130-4398-b51e-28900bf68527",
            "client_secret": "",
            "mag-identifier": "web-session",
            "cookies": {
                "auth_tmp_token": token_str
            }
        }
        with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(token_data, f, indent=4)
        
        self.token_data = token_data
        self.load_token()
        self.last_status = "連線成功 (手動更新 Token)"
        return True, "手動更新 Token 成功！"

    def auto_refresh_token(self):
        print("[CareLink Auto-Refresh] Token 已過期，嘗試自動進行免人工登入...")
        success, msg = self.login_with_selenium()
        if success:
            print(f"[CareLink Auto-Refresh Success] {msg}")
            return True
        else:
            print(f"[CareLink Auto-Refresh Warning] {msg}")
            return False

    def get_recent_data(self):
        if not self.load_token():
            self.last_status = "Token Missing"
            return None

        # 1. 自動獲取關聯患者 ID
        if not hasattr(self, "patient_id") or not self.patient_id:
            try:
                p_url = f"https://{HOST}/patient/m2m/links/patients"
                p_resp = self.session.get(p_url, headers=self.headers, timeout=10)
                if p_resp.status_code == 200 and p_resp.json():
                    self.patient_id = p_resp.json()[0].get("username")
                    print(f"[CareLink] 成功獲取 Patient ID: {self.patient_id}")
            except Exception as e:
                print(f"[CareLink] 獲取 Patient ID 失敗: {e}")

        # 2. 決定 API 端點
        if hasattr(self, "patient_id") and self.patient_id:
            url = f"https://clcloud.minimed.eu/patient/m2m/connect/data/gc/patients/{self.patient_id}"
        else:
            url = f"https://{HOST}/patient/connect/data"

        try:
            resp = self.session.get(url, headers=self.headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self.last_status = "連線成功"

                # 提取最新血糖
                sgs = data.get("sgs") or []
                if sgs:
                    latest = sgs[-1]  # 最新一筆
                    sgv = int(latest.get("sg", 0))
                    raw_dt = latest.get("datetime")

                    trend_str = data.get("lastSG", {}).get("trend") if isinstance(data.get("lastSG"), dict) else "FLAT"
                    direction = self.parse_trend(trend_str or "FLAT")

                    if raw_dt:
                        try:
                            from datetime import timedelta
                            dt_clean = raw_dt.replace("Z", "+00:00")
                            dt_obj = datetime.fromisoformat(dt_clean)
                            if dt_obj.tzinfo is None:
                                dt_obj = dt_obj.replace(tzinfo=timezone(timedelta(hours=8)))
                        except Exception:
                            dt_obj = datetime.now(timezone(timedelta(hours=8)))
                    else:
                        dt_obj = datetime.now(timezone(timedelta(hours=8)))

                    iso_str = dt_obj.isoformat()
                    ts_ms = int(dt_obj.timestamp() * 1000)

                    self.last_glucose = sgv
                    self.last_fetch_time = dt_obj

                    device_name = "Medtronic MiniMed"
                    if isinstance(data.get("cgmInfo"), dict):
                        device_name = data.get("cgmInfo", {}).get("modelNumber", "Medtronic MiniMed")

                    return {
                        "sgv": sgv,
                        "direction": direction,
                        "dateString": iso_str,
                        "date": ts_ms,
                        "device": device_name
                    }
                else:
                    self.last_status = "無連續血糖數據"
                    return None
            elif resp.status_code in (401, 403):
                print("[CareLink Warning] Token 到期 (401/403)，自動啟動連線續約...")
                if self.auto_refresh_token():
                    return self.get_recent_data()
                else:
                    self.last_status = f"Token 已過期 ({resp.status_code})，請進行登入驗證"
                    return None
            else:
                self.last_status = f"API 伺服器錯誤 ({resp.status_code})"
                return None
        except Exception as e:
            self.last_status = f"網路連線異常: {e}"
            print(f"[CareLink Exception] {e}")

        return None
