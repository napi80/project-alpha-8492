import sys
import time
import json
import re
import html
import requests
import nest_asyncio
import datetime
import hashlib
import os
import pytz 
from seleniumbase import Driver
from pyvirtualdisplay import Display
from colorama import Fore, Back, Style, init

# Init
nest_asyncio.apply()
init(autoreset=True)

# =================================================================
# Config
# =================================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TG_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TG_CHAT_ID")
MY_EMAIL = os.environ.get("MY_EMAIL")
MY_PASSWORD = os.environ.get("MY_PASSWORD")
TARGET_URL = os.environ.get("FJ_URL")

if not TELEGRAM_BOT_TOKEN or not MY_EMAIL or not TARGET_URL:
    print(f"{Fore.RED}Err: Config Missing.{Style.RESET_ALL}")
    sys.exit(1)

BLACKLIST_WORDS = [] 

# =================================================================
# State
# =================================================================
SEEN_SIGNATURES = set()
# FIX: Use timezone-aware UTC time to match incoming data format and avoid comparison errors
START_TIME = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)

# =================================================================
# Helper
# =================================================================
def sys_log(msg, color=Fore.WHITE):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.CYAN}[{ts}]{color} {msg}{Style.RESET_ALL}")

def parse_iso_date(date_str):
    if not date_str: return None
    try:
        date_str = str(date_str).replace('Z', '+00:00')
        if '+' not in date_str and 'Z' not in date_str:
             date_str += '+00:00'
        if "." in date_str: 
            date_str = date_str.split(".")[0] + "+00:00"
        return datetime.datetime.fromisoformat(date_str)
    except: return None

def convert_to_tehran(utc_dt):
    if not utc_dt: return "N/A"
    try:
        tehran_tz = pytz.timezone('Asia/Tehran')
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=datetime.timezone.utc)
        tehran_dt = utc_dt.astimezone(tehran_tz)
        return tehran_dt.strftime("%H:%M:%S")
    except:
        return utc_dt.strftime("%H:%M:%S")

def sanitize_text(text):
    if not text: return ""
    text = html.unescape(str(text))
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return "\n".join(lines)

def generate_signature(title, date_str):
    raw = f"{title}_{date_str if date_str else 'ND'}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()

def dispatch_payload(data):
    if not TELEGRAM_BOT_TOKEN: return
    
    raw_title = data.get('Title', data.get('FJTitle', 'No Title'))
    title = sanitize_text(raw_title)
    
    # DEBUG: Log that we are attempting to process a title
    # Using LIGHTBLACK_EX (Dimmed color) to not clutter critical logs
    sys_log(f"Trace: Processing '{title[:30]}...'", Fore.LIGHTBLACK_EX)
    
    publish_date = data.get('DatePublished') or data.get('PublishedDate') or data.get('PublishDate') or data.get('Date')
    
    for word in BLACKLIST_WORDS:
        if word.lower() in title.lower(): 
            sys_log(f"Skip: Blacklisted word ({word})", Fore.LIGHTBLACK_EX)
            return

    sig = generate_signature(title, publish_date)
    if sig in SEEN_SIGNATURES: 
        sys_log(f"Skip: Duplicate Signature", Fore.LIGHTBLACK_EX)
        return
    
    SEEN_SIGNATURES.add(sig)

    news_time_str = "N/A"
    if publish_date:
        dt = parse_iso_date(publish_date)
        if dt:
            # TIME FILTER CHECK
            if dt < START_TIME: 
                sys_log(f"Skip: Too Old (Time: {convert_to_tehran(dt)})", Fore.LIGHTBLACK_EX)
                return
            news_time_str = convert_to_tehran(dt)
        else:
            sys_log("Warn: Date Parse Failed", Fore.YELLOW)
    else:
         sys_log("Warn: No Date Field", Fore.YELLOW)

    news_id = data.get('NewsID', data.get('Id', '-'))
    tags = data.get('Tags', [])
    tags_str = ", ".join([str(t.get('Name')) for t in tags]) if tags else "-"
    breaking = data.get('Breaking', False)
    level = data.get('Level', '-')
    
    r_link = data.get('RURL', '')
    e_link = data.get('EURL', '')
    if not r_link: r_link = "-"
    if not e_link: e_link = "-"
    
    labels = data.get('Labels', [])
    labels_str = ", ".join([str(l) for l in labels]) if labels else "-"
    img_link = data.get('Img', '-')
    if not img_link: img_link = "-"
    
    description = sanitize_text(data.get('Description', ''))
    actual = data.get('Actual')
    forecast = data.get('Forecast')
    previous = data.get('Previous')

    icon = "🚨 " if breaking else ""
    msg = f"{icon}<b>{title}</b>\n\n"
    if description: msg += f"{description}\n\n"
    
    msg += "<b>INFO:</b>\n"
    msg += f"NewsID: {news_id}\n"
    msg += f"Tags: {tags_str}\n"
    msg += f"Breaking: {breaking}\n"
    msg += f"Level: {level}\n"
    msg += f"RURL: {r_link}\n"
    msg += f"EURL: {e_link}\n"
    msg += f"Labels: {labels_str}\n"
    msg += f"Img: {img_link}\n"
    msg += f"DatePublished: {news_time_str}\n"

    if actual or forecast:
        msg += "\n<b>DATA:</b>\n"
        msg += f"Act: {actual} | Fcst: {forecast} | Prev: {previous}\n"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
    
    try:
        requests.post(url, json=payload, timeout=5)
        sys_log(f"Packet: SENT ({len(title)} chars)", Fore.MAGENTA)
    except Exception as e:
        sys_log(f"Net: Err {e}", Fore.RED)

# =================================================================
# Spy Script
# =================================================================
JS_PAYLOAD = """
window.ws_spy_active = true;
window.ws_captured_logs = [];
const nativeWebSocket = window.WebSocket;
window.WebSocket = function(...args) {
  const socket = new nativeWebSocket(...args);
  socket.addEventListener('message', function(event) {
    if(window.ws_captured_logs) {
        window.ws_captured_logs.push(event.data);
    }
  });
  return socket;
};
"""

# =================================================================
# Main Loop (With Retry & Debug)
# =================================================================
def perform_login(driver):
    try:
        driver.get(TARGET_URL)
        time.sleep(7)

        sys_log(f"Debug: Page Title -> {driver.title}", Fore.CYAN)

        try:
            btns = driver.find_elements("xpath", "//a[contains(text(), 'Sign In')]")
            if btns: btns[0].click()
            else:
                btns = driver.find_elements("xpath", "//div[contains(@class, 'login')]")
                if btns: btns[0].click()
        except: 
            sys_log("Login btn skipped/not found", Fore.YELLOW)

        time.sleep(3)

        driver.find_element("css selector", "#ctl00_SignInSignUp_loginForm1_inputEmail").clear()
        driver.find_element("css selector", "#ctl00_SignInSignUp_loginForm1_inputEmail").send_keys(MY_EMAIL)
        
        driver.find_element("css selector", "#ctl00_SignInSignUp_loginForm1_inputPassword").clear()
        driver.find_element("css selector", "#ctl00_SignInSignUp_loginForm1_inputPassword").send_keys(MY_PASSWORD)
        
        driver.find_element("css selector", "#ctl00_SignInSignUp_loginForm1_btnLogin").click()
        sys_log("Auth: Credentials Sent... Waiting...", Fore.GREEN)
        
        time.sleep(20)
        
        # --- DEEP DEBUG: Check Login State ---
        # sys_log(f"Debug: Post-Login URL -> {driver.current_url}", Fore.CYAN)
        
        cookies = driver.get_cookies()
        
        if any('.ASPXAUTH' in c['name'] for c in cookies):
            sys_log("Debug: Auth Token (.ASPXAUTH) DETECTED! ✅", Fore.GREEN)
            return True
        
        sys_log("Debug: Auth Token MISSING. Checking page for errors...", Fore.RED)
        try:
            body_text = driver.find_element("tag name", "body").text
            if "Invalid login" in body_text or "failed" in body_text:
                sys_log("Debug: Detected Login Error Message on page.", Fore.RED)
        except: pass
        
        return False

    except Exception as e:
        sys_log(f"Auth Err: {e}", Fore.RED)
        return False

def run_service():
    sys_log(f"Core: Online (Debug Mode)", Fore.CYAN)
    display = Display(visible=0, size=(1920, 1080))
    display.start()
    driver = Driver(uc=True, headless=False)

    try:
        logged_in = False
        for attempt in range(1, 4):
            sys_log(f"Auth: Attempt {attempt}/3...", Fore.YELLOW)
            if perform_login(driver):
                sys_log("Status: Verified ✅", Fore.GREEN)
                logged_in = True
                break
            else:
                sys_log("Status: Failed ❌ (Retrying...)", Fore.RED)
                time.sleep(5)
        
        if not logged_in:
            sys_log("CRITICAL: Login failed 3 times. Exiting.", Fore.RED)
            driver.quit()
            display.stop()
            sys.exit(1)
        
        sys_log("Link: Established", Fore.GREEN)
        
        last_msg_time = time.time()
        
        while True:
            try: act = driver.execute_script("return window.ws_spy_active;")
            except: act = False

            if not act:
                sys_log("Spy: Injecting Payload...", Fore.YELLOW)
                driver.execute_script(JS_PAYLOAD)
                try: driver.execute_script("if($.connection && $.connection.hub){$.connection.hub.stop();setTimeout(()=>$.connection.hub.start(),1000);}")
                except: pass
                time.sleep(5)

            try:
                logs = driver.execute_script("""
                    if (typeof window.ws_captured_logs === 'undefined') return [];
                    return window.ws_captured_logs.splice(0, window.ws_captured_logs.length);
                """)
                if logs:
                    last_msg_time = time.time()
                    for raw_json in logs:
                        # Filter out empty or keep-alive packets to reduce noise
                        if raw_json == "{}" or raw_json == '{"S":1,"M":[]}': continue
                        
                        try:
                            data_obj = json.loads(raw_json)
                            if 'M' in data_obj:
                                for item in data_obj['M']:
                                    if 'A' in item and len(item['A']) > 0:
                                        payload_str = item['A'][0]
                                        try:
                                            # Debug log for raw payload presence
                                            # sys_log("Debug: Payload caught", Fore.LIGHTBLACK_EX)
                                            if isinstance(payload_str, str) and (payload_str.startswith('[') or payload_str.startswith('{')):
                                                inner_list = json.loads(payload_str)
                                                if isinstance(inner_list, list):
                                                    for news_item in inner_list: 
                                                        dispatch_payload(news_item)
                                                else:
                                                    # Single object case
                                                    dispatch_payload(inner_list)
                                        except Exception as e_inner: 
                                            sys_log(f"Parse Err (Inner): {e_inner}", Fore.RED)
                        except Exception as e_outer:
                            sys_log(f"Parse Err (Outer): {e_outer}", Fore.RED)
            except Exception as e_script:
                 sys_log(f"Spy script execution error: {e_script}", Fore.RED)
            
            if time.time() - last_msg_time > 1800:
                sys_log("Heartbeat Lost (30m). Restarting...", Fore.RED)
                break 

            time.sleep(1)

    except KeyboardInterrupt: pass
    finally:
        try: driver.quit()
        except: pass
        try: display.stop()
        except: pass

if __name__ == "__main__":
    run_service()
