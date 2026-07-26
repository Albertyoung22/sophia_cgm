import os
import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

USERNAME = "Sophiafa"
PASSWORD = "[user provided password]"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "logindata.json")

print("==================================================")
print("SophiaCarelink Automated Token Generator")
print("==================================================")
print("Starting automated login sequence...")

options = Options()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

try:
    driver = webdriver.Chrome(options=options)
    url = "https://carelink.minimed.eu/patient/sso/login"
    driver.get(url)
    print("Opened CareLink login page in headless mode.")

    # Wait for inputs to render
    user_input = None
    pass_input = None
    for attempt in range(15):
        time.sleep(1)
        inputs = driver.find_elements(By.TAG_NAME, "input")
        for inp in inputs:
            inp_type = str(inp.get_attribute("type")).lower()
            inp_name = str(inp.get_attribute("name")).lower()
            inp_id = str(inp.get_attribute("id")).lower()
            if inp_type in ["text", "email"] or "user" in inp_name or "user" in inp_id:
                user_input = inp
            elif inp_type == "password" or "pass" in inp_name or "pass" in inp_id:
                pass_input = inp
        if user_input and pass_input:
            print(f"Found input fields on attempt {attempt+1}!")
            break

    if not (user_input and pass_input):
        print("Error: Could not locate username and password input fields.")
        driver.quit()
        exit(1)

    # Use JS React/Angular Native Setter to trigger input & change events
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
    driver.execute_script(js_set_val, user_input, pass_input, USERNAME, PASSWORD)
    print(f"Entered credentials for account '{USERNAME}' with event dispatching.")
    time.sleep(1)

    # Find submit button
    buttons = driver.find_elements(By.TAG_NAME, "button")
    submit_btn = None
    for btn in buttons:
        btn_type = str(btn.get_attribute("type")).lower()
        btn_text = str(btn.text).lower()
        if btn_type == "submit" or "log" in btn_text or "sign" in btn_text or "登入" in btn_text:
            submit_btn = btn
            break

    if not submit_btn:
        try:
            submit_btn = driver.find_element(By.XPATH, "//input[@type='submit']")
        except:
            pass

    if submit_btn:
        print("Clicking submit button...")
        driver.execute_script("arguments[0].click();", submit_btn)
    else:
        print("Warning: Submit button not found, attempting form submission...")
        driver.execute_script("document.forms[0].submit();")

    # Poll for cookies for up to 20 seconds
    token_saved = False
    for second in range(20):
        time.sleep(1)
        cookies = driver.get_cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        curr_url = driver.current_url

        if "auth_tmp_token" in cookie_dict or ("patient" in curr_url and "login" not in curr_url and len(cookie_dict) > 2):
            auth_token = cookie_dict.get("auth_tmp_token") or "web_session_active"
            token_data = {
                "access_token": auth_token,
                "refresh_token": "web_session_active",
                "scope": "profile openid roles country",
                "client_id": "4fb211b8-f130-4398-b51e-28900bf68527",
                "client_secret": "",
                "mag-identifier": "web-session",
                "cookies": cookie_dict
            }
            with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                json.dump(token_data, f, indent=4)
            print("\n🎉🎉 SUCCESS! CareLink Session Token successfully generated & saved to logindata.json! 🎉🎉")
            token_saved = True
            break

    driver.quit()

    if not token_saved:
        print("Failed to capture token after form submission. Current URL:", curr_url)

except Exception as e:
    print(f"[Auto Token Error] {e}")
