import os
import json
import time
import requests
from datetime import datetime, timezone

CARELINK_USERNAME = os.environ.get("CARELINK_USERNAME", "Sophiafa")
CARELINK_PASSWORD = os.environ.get("CARELINK_PASSWORD", "[user provided password]")
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
                            self.session.cookies.set(k, v, domain=HOST)

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

    def get_recent_data(self):
        if not self.load_token():
            self.last_status = "Token Missing (Please run login script)"
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
                print("[CareLink Warning] Token expired (401/403). Attempting auto-refresh...")
                if self.auto_refresh_token():
                    return self.get_recent_data()
                else:
                    self.last_status = f"[CareLink Error] Token Expired ({resp.status_code})."
                    return None
            else:
                self.last_status = f"API Error ({resp.status_code})"
                return None
        except Exception as e:
            self.last_status = f"Network Exception: {e}"
            print(f"[CareLink Exception] {e}")

        return None

    def auto_refresh_token(self):
        try:
            ref_url = f"https://{HOST}/patient/sso/login?country=TW&lang=zh"
            resp = self.session.get(ref_url, timeout=15, allow_redirects=True)
            cookies = self.session.cookies.get_dict()
            if "auth_tmp_token" in cookies:
                new_token = cookies["auth_tmp_token"]
                if "cookies" not in self.token_data:
                    self.token_data["cookies"] = {}
                self.token_data["cookies"]["auth_tmp_token"] = new_token
                self.token_data["access_token"] = new_token
                self.headers["Authorization"] = f"Bearer {new_token}"
                with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.token_data, f, indent=4)
                print("[CareLink Auto-Refresh] Successfully refreshed token!")
                return True
        except Exception as e:
            print(f"[CareLink Auto-Refresh Exception] {e}")
        return False
