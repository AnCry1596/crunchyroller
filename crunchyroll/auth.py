import json
import os
import uuid
from typing import Dict, Any, Optional, Tuple
import requests

CONFIG_FILE = "config.json"

# just need one device id per session
_DEVICE_ID = str(uuid.uuid4())


def get_access_token(etp_rt: str) -> str:
    """swap our session cookie for a bearer token"""
    url = "https://www.crunchyroll.com/auth/v1/token"
    headers = {
        "Authorization": "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    }
    cookies = {
        "device_id": _DEVICE_ID,
        "etp_rt": etp_rt,
    }
    data = {
        "grant_type": "etp_rt_cookie",
        "device_id": _DEVICE_ID,
        "device_type": "Firefox on Linux",
    }

    response = requests.post(url, headers=headers, cookies=cookies, data=data)
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to get access token (status {response.status_code}): {response.text}"
        )

    json_resp = response.json()
    return json_resp.get("access_token", "")


def auto_detect_etp_rt() -> Optional[str]:
    """try to grab the etp_rt cookie from whatever browser is installed"""
    import glob
    import sqlite3
    import shutil
    import tempfile
    import base64
    import ctypes
    from ctypes import wintypes
    from Crypto.Cipher import AES

    # firefox
    ff_paths = [
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Mozilla", "Firefox", "Profiles", "*", "cookies.sqlite"),
        os.path.join(os.path.expanduser("~"), ".mozilla", "firefox", "*", "cookies.sqlite"),
    ]
    for pattern in ff_paths:
        for db in glob.glob(pattern):
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False).name
                shutil.copy2(db, tmp)
                conn = sqlite3.connect(tmp)
                c = conn.cursor()
                c.execute("SELECT value FROM moz_cookies WHERE host LIKE '%crunchyroll%' AND name='etp_rt'")
                row = c.fetchone()
                conn.close()
                os.remove(tmp)
                if row and row[0]:
                    return row[0]
            except Exception:
                pass

    # chromium browsers
    if os.name == "nt":
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        def unprotect_data(data):
            in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
            out_blob = DATA_BLOB()
            if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
                res = ctypes.string_at(out_blob.pbData, out_blob.cbData)
                ctypes.windll.kernel32.LocalFree(out_blob.pbData)
                return res
            return None

        def get_chrome_key(local_state_path):
            with open(local_state_path, "r", encoding="utf-8") as f:
                local_state = json.load(f)
            encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])[5:]
            return unprotect_data(encrypted_key)

        def decrypt_val(val, key):
            try:
                iv = val[3:15]
                payload = val[15:]
                cipher = AES.new(key, AES.MODE_GCM, iv)
                return cipher.decrypt(payload)[:-16].decode("utf-8")
            except Exception:
                return ""

        browsers = {
            "Brave": os.path.join(os.path.expanduser("~"), "AppData", "Local", "BraveSoftware", "Brave-Browser", "User Data"),
            "Chrome": os.path.join(os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data"),
            "Edge": os.path.join(os.path.expanduser("~"), "AppData", "Local", "Microsoft", "Edge", "User Data"),
            "Opera": os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Opera Software", "Opera Stable"),
        }

        def copy_db_safely(src, dst):
            try:
                shutil.copy2(src, dst)
                return True
            except Exception:
                pass
            # try raw win32 read if file is locked
            try:
                GENERIC_READ = 0x80000000
                FILE_SHARE_READ = 0x00000001
                FILE_SHARE_WRITE = 0x00000002
                FILE_SHARE_DELETE = 0x00000004
                OPEN_EXISTING = 3
                handle = ctypes.windll.kernel32.CreateFileW(
                    ctypes.c_wchar_p(src), GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    None, OPEN_EXISTING, 0, None
                )
                if handle != -1 and handle != 0xFFFFFFFF:
                    size = ctypes.windll.kernel32.GetFileSize(handle, None)
                    buf = ctypes.create_string_buffer(size)
                    bread = wintypes.DWORD()
                    ctypes.windll.kernel32.ReadFile(handle, buf, size, ctypes.byref(bread), None)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    with open(dst, "wb") as f:
                        f.write(buf.raw[:bread.value])
                    return True
            except Exception:
                pass
            return False

        for name, bpath in browsers.items():
            lpath = os.path.join(bpath, "Local State")
            if not os.path.exists(lpath):
                continue

            cookie_candidates = glob.glob(os.path.join(bpath, "Default", "Network", "Cookies"))
            cookie_candidates.extend(glob.glob(os.path.join(bpath, "Profile *", "Network", "Cookies")))
            cookie_candidates.extend(glob.glob(os.path.join(bpath, "Cookies")))

            try:
                key = get_chrome_key(lpath)
                for cpath in cookie_candidates:
                    if os.path.exists(cpath):
                        tmp = tempfile.NamedTemporaryFile(delete=False).name
                        if copy_db_safely(cpath, tmp):
                            try:
                                conn = sqlite3.connect(tmp)
                                c = conn.cursor()
                                c.execute("SELECT encrypted_value FROM cookies WHERE host_key LIKE '%crunchyroll%' AND name='etp_rt'")
                                row = c.fetchone()
                                conn.close()
                                os.remove(tmp)
                                if row:
                                    dec = decrypt_val(row[0], key)
                                    if dec:
                                        return dec
                            except Exception:
                                if os.path.exists(tmp):
                                    try:
                                        os.remove(tmp)
                                    except Exception:
                                        pass
            except Exception:
                pass

    return None




def login_with_credentials(username: str, password: str, device_id_val: Optional[str] = None) -> Tuple[str, str]:
    """legacy password login is dead. just try to grab the cookie."""
    token = auto_detect_etp_rt()
    if token:
        return "", token

    raise RuntimeError(
        "Crunchyroll no longer supports direct password API login. "
        "Please use your 'etp_rt' session token instead!\n"
        "How to get etp_rt:\n"
        "1. Log in to crunchyroll.com in your browser.\n"
        "2. Press F12 (Developer Tools) -> Application -> Cookies -> crunchyroll.com.\n"
        "3. Copy the value of 'etp_rt' and paste it into the Session Token field!"
    )





def load_config(config_path: str = CONFIG_FILE) -> Dict[str, Any]:
    """load config if it exists"""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(config_dict: Dict[str, Any], config_path: str = CONFIG_FILE) -> None:
    """save settings"""
    existing = load_config(config_path)
    existing.update(config_dict)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=4)


def open_webview_login() -> Optional[str]:
    """
    Opens an in-app browser window navigating to crunchyroll.com/login.
    Monitors cookies for etp_rt upon successful user login.
    """
    try:
        import webview
    except ImportError:
        return None

    captured = {"etp_rt": None}

    def _check_cookies(window):
        import time
        while not captured["etp_rt"]:
            time.sleep(1)
            try:
                cookies = window.get_cookies()
                if cookies:
                    for c in cookies:
                        # handles both dicts and pywebview cookie objects
                        name = getattr(c, "name", "") if hasattr(c, "name") else (c.get("name", "") if isinstance(c, dict) else "")
                        val = getattr(c, "value", "") if hasattr(c, "value") else (c.get("value", "") if isinstance(c, dict) else "")
                        if name == "etp_rt" and val:
                            captured["etp_rt"] = val
                            window.destroy()
                            break
            except Exception:
                pass

    import threading
    window = webview.create_window(
        "Crunchyroll In-App Login",
        "https://www.crunchyroll.com/login",
        width=960,
        height=720,
    )
    t = threading.Thread(target=_check_cookies, args=(window,), daemon=True)
    t.start()
    webview.start()

    return captured["etp_rt"]

