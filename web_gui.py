import http.server
import json
import os
import sys
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlparse

from crunchyroll.api import get_episode_info, get_season_episodes, get_seasons, get_series, parse_url_type
from crunchyroll.auth import load_config, save_config, login_with_credentials, auto_detect_etp_rt
from crunchyroll.downloader import download_episode, download_season, download_series
from crunchyroll.http_client import CrunchyrollHttpClient


# Global state for server
SERVER_PORT = 8000

initial_cfg = load_config()

STATE = {
    "etp_rt": initial_cfg.get("etp_rt", ""),
    "email": initial_cfg.get("username", ""),
    "remember_me": True if initial_cfg.get("etp_rt") else False,
    "config": {
        "video_quality": initial_cfg.get("video_quality", "1080p"),
        "audio_quality": initial_cfg.get("audio_quality", "192k"),
        "audio_lang": initial_cfg.get("audio_lang", "ja-JP"),
        "subs_lang": initial_cfg.get("subs_lang", "en-US"),
    },
    "current_download": {
        "status": "idle",  # idle, downloading, completed, cancelled, error
        "progress": 0.0,
        "speed": "0 MB/s",
        "current_item": "",
        "log": [],
        "cancel_requested": False,
    },
}

LOCK = threading.Lock()


def add_log(msg: str):
    with LOCK:
        STATE["current_download"]["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(STATE["current_download"]["log"]) > 100:
            STATE["current_download"]["log"].pop(0)


class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default HTTP logging
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_content, status=200):
        body = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_html(HTML_APP)
        elif path == "/api/config":
            with LOCK:
                self._send_json({
                    "etp_rt": STATE["etp_rt"],
                    "email": STATE["email"],
                    "remember_me": STATE["remember_me"],
                    "config": STATE["config"],
                })
        elif path == "/api/progress":
            with LOCK:
                self._send_json(STATE["current_download"])
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            data = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception:
            data = {}

        if path == "/api/auto-detect":
            token = auto_detect_etp_rt()
            if token:
                with LOCK:
                    STATE["etp_rt"] = token
                    STATE["remember_me"] = True
                save_config({"etp_rt": token})
                add_log("Auto-detected Crunchyroll session cookie from installed browser!")
                self._send_json({"success": True, "etp_rt": token, "message": "Cookie detected from browser!"})
            else:
                self._send_json({
                    "success": False,
                    "error": "Could not auto-detect Crunchyroll login cookie in Chrome/Edge/Firefox. Please log into crunchyroll.com in your browser first!"
                }, status=404)

        elif path == "/api/login":
            etp_rt = data.get("etp_rt", "").strip()
            email = data.get("email", "").strip()
            password = data.get("password", "").strip()
            remember = data.get("remember_me", True)

            try:
                if etp_rt:
                    add_log("Validating etp_rt session token...")
                    client = CrunchyrollHttpClient(etp_rt=etp_rt)
                elif email or password:
                    self._send_json({
                        "success": False,
                        "error": "Crunchyroll disabled direct password API logins. Please paste your 'etp_rt' cookie token into the Session Token field above!\n(Press F12 on Crunchyroll -> Application -> Cookies -> Copy 'etp_rt')"
                    }, status=400)
                    return
                else:
                    self._send_json({"success": False, "error": "Please enter your etp_rt session cookie token."}, status=400)
                    return

                with LOCK:
                    STATE["etp_rt"] = etp_rt
                    STATE["email"] = email
                    STATE["remember_me"] = remember

                if remember:
                    save_config({"etp_rt": etp_rt, "username": email})

                add_log("Logged in successfully.")
                self._send_json({"success": True, "message": "Session token saved successfully!", "etp_rt": etp_rt})

            except Exception as e:
                add_log(f"Authentication error: {e}")
                self._send_json({"success": False, "error": str(e)}, status=401)


        elif path == "/api/config":
            with LOCK:
                if "video_quality" in data:
                    STATE["config"]["video_quality"] = data["video_quality"]
                if "audio_quality" in data:
                    STATE["config"]["audio_quality"] = data["audio_quality"]
                if "audio_lang" in data:
                    STATE["config"]["audio_lang"] = data["audio_lang"]
                if "subs_lang" in data:
                    STATE["config"]["subs_lang"] = data["subs_lang"]

                save_config(STATE["config"])

            self._send_json({"success": True, "config": STATE["config"]})

        elif path == "/api/fetch-info":
            url = data.get("url", "").strip()
            if not url:
                self._send_json({"success": False, "error": "URL is required"}, status=400)
                return

            if not STATE["etp_rt"]:
                self._send_json({"success": False, "error": "Not authenticated. Please log in with credentials or token."}, status=401)
                return

            try:
                client = CrunchyrollHttpClient(STATE["etp_rt"])
                url_type, content_id = parse_url_type(url)

                if url_type == "episode":
                    info = get_episode_info(client, content_id)
                    tree = {
                        "type": "episode",
                        "id": content_id,
                        "title": info.title,
                        "series_title": info.episode_metadata.series_title,
                        "season_number": info.episode_metadata.season_number,
                        "episode_number": info.episode_metadata.episode_number,
                    }
                    self._send_json({"success": True, "data": tree})

                elif url_type in ("series", "season"):
                    audio_lang = STATE["config"].get("audio_lang", "ja-JP")
                    subs_lang = STATE["config"].get("subs_lang", "en-US")
                    series_data = get_series(client, content_id, audio_lang, subs_lang)

                    season_tree = []
                    for season in series_data.get("seasons", []):
                        episodes = get_season_episodes(client, season.id, audio_lang, subs_lang)
                        ep_list = []
                        for ep in episodes:
                            ep_list.append({
                                "id": ep.id,
                                "title": ep.title,
                                "episode_number": ep.episode_number,
                                "season_number": ep.season_number,
                                "series_title": ep.series_title,
                            })
                        season_tree.append({
                            "season_id": season.id,
                            "season_number": season.season_number,
                            "episodes": ep_list,
                        })

                    self._send_json({
                        "success": True,
                        "data": {
                            "type": "series",
                            "id": content_id,
                            "title": series_data.get("title", ""),
                            "seasons": season_tree,
                        }
                    })

                else:
                    self._send_json({"success": False, "error": "Unsupported URL format"}, status=400)

            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, status=500)

        elif path == "/api/start-download":
            if STATE["current_download"]["status"] == "downloading":
                self._send_json({"success": False, "error": "A download task is already in progress"}, status=400)
                return

            if not STATE["etp_rt"]:
                self._send_json({"success": False, "error": "Not authenticated. Please log in first."}, status=401)
                return

            items = data.get("items", [])
            video_quality = data.get("video_quality", STATE["config"]["video_quality"])
            audio_quality = data.get("audio_quality", STATE["config"]["audio_quality"])
            audio_lang = data.get("audio_lang", STATE["config"]["audio_lang"])
            subs_lang = data.get("subs_lang", STATE["config"]["subs_lang"])

            if not items:
                self._send_json({"success": False, "error": "No episodes selected for download"}, status=400)
                return

            # Start download in background thread
            t = threading.Thread(
                target=_run_download_task,
                args=(items, video_quality, audio_quality, audio_lang, subs_lang),
                daemon=True,
            )
            t.start()

            self._send_json({"success": True, "message": "Download started"})

        elif path == "/api/cancel":
            with LOCK:
                STATE["current_download"]["cancel_requested"] = True
                STATE["current_download"]["status"] = "cancelling"
            add_log("Cancellation requested by user.")
            self._send_json({"success": True, "message": "Cancellation requested"})

        else:
            self.send_error(404, "Not Found")


def _gui_progress_cb(ep_title, current_seg, total_segs, speed_str, status):
    with LOCK:
        if total_segs > 0:
            seg_pct = round((current_seg / total_segs) * 100, 1)
            STATE["current_download"]["progress"] = seg_pct
        STATE["current_download"]["speed"] = speed_str
        if ep_title:
            STATE["current_download"]["current_item"] = f"{ep_title} ({current_seg}/{total_segs})"


def _run_download_task(items, video_quality, audio_quality, audio_lang, subs_lang):
    with LOCK:
        STATE["current_download"]["status"] = "downloading"
        STATE["current_download"]["progress"] = 0.0
        STATE["current_download"]["cancel_requested"] = False
        STATE["current_download"]["log"] = []

    client = CrunchyrollHttpClient(STATE["etp_rt"])
    total = len(items)
    add_log(f"Starting batch download of {total} episode(s)...")

    for idx, item in enumerate(items):
        with LOCK:
            if STATE["current_download"]["cancel_requested"]:
                STATE["current_download"]["status"] = "cancelled"
                add_log("Download cancelled by user.")
                return

        try:
            ep_id = item.get("id") if isinstance(item, dict) else item
            info = get_episode_info(client, ep_id)
            add_log(f"[{idx+1}/{total}] Downloading {info.title} (S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d}) [{video_quality}/{audio_quality}]")

            a_langs = [p.strip() for p in audio_lang.split(",") if p.strip()] or ["ja-JP"]
            s_langs = [p.strip() for p in subs_lang.split(",") if p.strip()] or ["en-US"]

            download_episode(
                client=client,
                base_content_id=ep_id,
                info=info,
                audio_langs=a_langs,
                subs_langs=s_langs,
                video_quality=video_quality,
                audio_quality=audio_quality,
                progress_cb=_gui_progress_cb,
            )

        except Exception as e:
            add_log(f"Error downloading episode {item}: {e}")

    with LOCK:
        STATE["current_download"]["status"] = "completed"
        STATE["current_download"]["progress"] = 100.0
        STATE["current_download"]["current_item"] = "All Downloads Finished"
    add_log("Batch download completed successfully!")


HTML_APP = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crunchyroll Downloader - Modern Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0e17;
            --bg-glass: rgba(18, 26, 43, 0.65);
            --border-glass: rgba(255, 255, 255, 0.1);
            --accent-orange: #ff6b00;
            --accent-orange-hover: #ff8533;
            --accent-cyan: #00d2ff;
            --text-primary: #f0f4f8;
            --text-secondary: #8c9ba5;
            --danger: #ff4757;
            --success: #2ed573;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        body {
            background: linear-gradient(135deg, #07090e 0%, #101625 50%, #0d121f 100%);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 24px;
            display: flex;
            justify-content: center;
        }

        .container {
            width: 100%;
            max-width: 1100px;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }

        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 20px 28px;
            background: var(--bg-glass);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border-glass);
            border-radius: 16px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-icon {
            width: 36px;
            height: 36px;
            background: var(--accent-orange);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 20px;
            color: #fff;
            box-shadow: 0 0 15px rgba(255, 107, 0, 0.5);
        }

        h1 {
            font-size: 1.4rem;
            font-weight: 700;
            letter-spacing: -0.5px;
        }

        .status-badge {
            font-size: 0.85rem;
            padding: 6px 14px;
            border-radius: 20px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-glass);
            color: var(--text-secondary);
        }

        .card {
            background: var(--bg-glass);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border-glass);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }

        .card-title {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 18px;
            display: flex;
            align-items: center;
            gap: 10px;
            color: var(--text-primary);
        }

        .grid-2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }

        @media (max-width: 768px) {
            .grid-2 { grid-template-columns: 1fr; }
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 16px;
        }

        label {
            font-size: 0.85rem;
            color: var(--text-secondary);
            font-weight: 500;
        }

        input[type="text"], input[type="password"], select {
            width: 100%;
            padding: 12px 16px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border-glass);
            border-radius: 10px;
            color: var(--text-primary);
            font-size: 0.95rem;
            outline: none;
            transition: all 0.2s ease;
        }

        input:focus, select:focus {
            border-color: var(--accent-orange);
            box-shadow: 0 0 10px rgba(255, 107, 0, 0.25);
        }

        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 10px;
            cursor: pointer;
            user-select: none;
            font-size: 0.9rem;
            color: var(--text-secondary);
        }

        .checkbox-group input {
            accent-color: var(--accent-orange);
            width: 16px;
            height: 16px;
        }

        .btn {
            padding: 12px 24px;
            border-radius: 10px;
            border: none;
            font-weight: 600;
            font-size: 0.95rem;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }

        .btn-primary {
            background: var(--accent-orange);
            color: white;
            box-shadow: 0 4px 15px rgba(255, 107, 0, 0.3);
        }

        .btn-primary:hover {
            background: var(--accent-orange-hover);
            transform: translateY(-1px);
        }

        .btn-danger {
            background: var(--danger);
            color: white;
        }

        .btn-danger:hover {
            opacity: 0.9;
        }

        .url-bar {
            display: flex;
            gap: 12px;
        }

        .tree-view {
            max-height: 280px;
            overflow-y: auto;
            background: rgba(0, 0, 0, 0.25);
            border: 1px solid var(--border-glass);
            border-radius: 10px;
            padding: 14px;
            margin-top: 14px;
        }

        .tree-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px;
            border-radius: 6px;
            transition: background 0.2s;
        }

        .tree-item:hover {
            background: rgba(255, 255, 255, 0.05);
        }

        .progress-bar-container {
            width: 100%;
            height: 12px;
            background: rgba(0, 0, 0, 0.4);
            border-radius: 6px;
            overflow: hidden;
            border: 1px solid var(--border-glass);
            margin: 14px 0;
        }

        .progress-bar {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, var(--accent-orange), var(--accent-cyan));
            transition: width 0.3s ease;
        }

        .log-box {
            background: rgba(0, 0, 0, 0.5);
            border: 1px solid var(--border-glass);
            border-radius: 10px;
            padding: 12px;
            font-family: monospace;
            font-size: 0.85rem;
            height: 140px;
            overflow-y: auto;
            color: #a0aec0;
        }

        .log-entry {
            margin-bottom: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <div class="brand-icon">CR</div>
                <h1>Crunchyroll Downloader</h1>
            </div>
            <div class="status-badge" id="authStatus">Not Authenticated</div>
        </header>

        <div class="grid-2">
            <!-- Login Panel -->
            <div class="card">
                <div class="card-title">Authentication</div>
                <div class="form-group">
                    <label>Email / Username</label>
                    <input type="text" id="emailInput" placeholder="user@example.com">
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input type="password" id="passwordInput" placeholder="Account password...">
                </div>
                <div class="form-group">
                    <label>Or Session Token (etp_rt Cookie)</label>
                    <input type="password" id="etpRtInput" placeholder="Paste etp_rt token directly...">
                </div>
                <div class="form-group">
                    <label class="checkbox-group">
                        <input type="checkbox" id="rememberMeInput" checked> Remember Me
                    </label>
                </div>
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-primary" onclick="handleLogin()">Save Session</button>
                    <button class="btn btn-primary" style="background: #00d2ff; color: #000;" onclick="autoDetectCookie()">⚡ Auto-Detect from Browser</button>
                </div>
            </div>

            <!-- Quality & Settings -->
            <div class="card">
                <div class="card-title">Download Settings</div>
                <div class="grid-2">
                    <div class="form-group">
                        <label>Video Quality</label>
                        <select id="videoQuality">
                            <option value="1080p">1080p (Full HD)</option>
                            <option value="720p">720p (HD)</option>
                            <option value="480p">480p (SD)</option>
                            <option value="360p">360p (Fast)</option>
                            <option value="240p">240p</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Audio Quality</label>
                        <select id="audioQuality">
                            <option value="192k">192k (High)</option>
                            <option value="96k">96k (Standard)</option>
                        </select>
                    </div>
                </div>
                <div class="grid-2">
                    <div class="form-group">
                        <label>Audio Language</label>
                        <input type="text" id="audioLang" value="ja-JP">
                    </div>
                    <div class="form-group">
                        <label>Subtitle Language</label>
                        <input type="text" id="subsLang" value="en-US">
                    </div>
                </div>
                <button class="btn btn-primary" style="margin-top: 4px;" onclick="saveConfig()">Save Settings</button>
            </div>
        </div>

        <!-- URL & Tree View -->
        <div class="card">
            <div class="card-title">Content Selection (Series / Season / Episode)</div>
            <div class="url-bar">
                <input type="text" id="urlInput" placeholder="Paste Crunchyroll Series, Season, or Episode URL...">
                <button class="btn btn-primary" onclick="fetchInfo()">Fetch Content</button>
            </div>
            <div class="tree-view" id="treeView">
                <div style="color: var(--text-secondary); text-align: center; padding: 20px;">
                    Enter a Series, Season, or Episode URL above and click Fetch Content.
                </div>
            </div>
            <div style="margin-top: 16px; display: flex; justify-content: flex-end;">
                <button class="btn btn-primary" onclick="startDownload()">Start Batch Download</button>
            </div>
        </div>

        <!-- Dashboard / Live Progress -->
        <div class="card">
            <div class="card-title" style="justify-content: space-between;">
                <span>Live Download Dashboard</span>
                <button class="btn btn-danger" onclick="cancelDownload()">Cancel Download</button>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 0.9rem;">
                <span id="currentItemText">Status: Idle</span>
                <span id="progressText">0%</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar" id="progressBar"></div>
            </div>
            <div class="log-box" id="logBox"></div>
        </div>
    </div>

    <script>
        let fetchedTreeData = null;

        async function initPage() {
            const res = await fetch('/api/config');
            const data = await res.json();
            if (data.etp_rt) {
                document.getElementById('etpRtInput').value = data.etp_rt;
                document.getElementById('authStatus').textContent = 'Authenticated';
                document.getElementById('authStatus').style.color = '#2ed573';
            }
            if (data.email) {
                document.getElementById('emailInput').value = data.email;
            }
            if (data.config) {
                if (data.config.video_quality) document.getElementById('videoQuality').value = data.config.video_quality;
                if (data.config.audio_quality) document.getElementById('audioQuality').value = data.config.audio_quality;
                if (data.config.audio_lang) document.getElementById('audioLang').value = data.config.audio_lang;
                if (data.config.subs_lang) document.getElementById('subsLang').value = data.config.subs_lang;
            }
        }
        initPage();

        async function autoDetectCookie() {
            const res = await fetch('/api/auto-detect', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                document.getElementById('etpRtInput').value = data.etp_rt;
                document.getElementById('authStatus').textContent = 'Authenticated';
                document.getElementById('authStatus').style.color = '#2ed573';
                alert('Success! Cookie auto-detected from browser and saved!');
            } else {
                alert('Error: ' + data.error);
            }
        }

        async function handleLogin() {

            const email = document.getElementById('emailInput').value;
            const password = document.getElementById('passwordInput').value;
            const etp_rt = document.getElementById('etpRtInput').value;
            const remember_me = document.getElementById('rememberMeInput').checked;

            const res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password, etp_rt, remember_me })
            });
            const data = await res.json();
            if (data.success) {
                document.getElementById('authStatus').textContent = 'Authenticated';
                document.getElementById('authStatus').style.color = '#2ed573';
                if (data.etp_rt) document.getElementById('etpRtInput').value = data.etp_rt;
                alert('Logged in successfully!');
            } else {
                alert('Error: ' + data.error);
            }
        }

        async function saveConfig() {
            const video_quality = document.getElementById('videoQuality').value;
            const audio_quality = document.getElementById('audioQuality').value;
            const audio_lang = document.getElementById('audioLang').value;
            const subs_lang = document.getElementById('subsLang').value;

            await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ video_quality, audio_quality, audio_lang, subs_lang })
            });
            alert('Settings updated!');
        }

        async function fetchInfo() {
            const url = document.getElementById('urlInput').value;
            const treeView = document.getElementById('treeView');
            treeView.innerHTML = '<div style="color: var(--text-secondary); text-align: center; padding: 20px;">Fetching metadata...</div>';

            const res = await fetch('/api/fetch-info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
            const result = await res.json();

            if (!result.success) {
                treeView.innerHTML = `<div style="color: var(--danger); text-align: center; padding: 20px;">Error: ${result.error}</div>`;
                return;
            }

            fetchedTreeData = result.data;
            renderTree(result.data);
        }

        function renderTree(data) {
            const treeView = document.getElementById('treeView');
            treeView.innerHTML = '';

            if (data.type === 'episode') {
                treeView.innerHTML = `
                    <div class="tree-item">
                        <input type="checkbox" class="ep-check" value="${data.id}" checked>
                        <span>[S${data.season_number}E${data.episode_number}] ${data.series_title} - ${data.title}</span>
                    </div>
                `;
            } else if (data.type === 'series') {
                data.seasons.forEach(season => {
                    const seasonHeader = document.createElement('div');
                    seasonHeader.style.fontWeight = 'bold';
                    seasonHeader.style.marginTop = '12px';
                    seasonHeader.style.marginBottom = '6px';
                    seasonHeader.innerText = `Season ${season.season_number} (${season.episodes.length} Episodes)`;
                    treeView.appendChild(seasonHeader);

                    season.episodes.forEach(ep => {
                        const item = document.createElement('div');
                        item.className = 'tree-item';
                        item.innerHTML = `
                            <input type="checkbox" class="ep-check" value="${ep.id}" checked>
                            <span>E${ep.episode_number} - ${ep.title}</span>
                        `;
                        treeView.appendChild(item);
                    });
                });
            }
        }

        async function startDownload() {
            const checkboxes = document.querySelectorAll('.ep-check:checked');
            const items = Array.from(checkboxes).map(cb => ({ id: cb.value }));

            if (items.length === 0) {
                alert('Please select at least one episode to download.');
                return;
            }

            const res = await fetch('/api/start-download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    items,
                    video_quality: document.getElementById('videoQuality').value,
                    audio_quality: document.getElementById('audioQuality').value,
                    audio_lang: document.getElementById('audioLang').value,
                    subs_lang: document.getElementById('subsLang').value,
                })
            });
            const data = await res.json();
            if (!data.success) {
                alert(data.error);
            }
        }

        async function cancelDownload() {
            await fetch('/api/cancel', { method: 'POST' });
        }

        async function pollProgress() {
            try {
                const res = await fetch('/api/progress');
                const data = await res.json();

                document.getElementById('progressBar').style.width = data.progress + '%';
                document.getElementById('progressText').textContent = data.progress + '% (' + data.speed + ')';
                document.getElementById('currentItemText').textContent = data.current_item ? `Current: ${data.current_item}` : `Status: ${data.status}`;

                const logBox = document.getElementById('logBox');
                logBox.innerHTML = data.log.map(line => `<div class="log-entry">${line}</div>`).join('');
                logBox.scrollTop = logBox.scrollHeight;
            } catch (e) {}
        }

        setInterval(pollProgress, 1000);
    </script>
</body>
</html>
"""


def start_server(port=SERVER_PORT, open_browser=True):
    server_address = ("", port)
    httpd = http.server.HTTPServer(server_address, RequestHandler)
    url = f"http://localhost:{port}"
    print(f"Starting Web GUI Server on {url} ...")

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(1), webbrowser.open(url)), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Web GUI Server.")


if __name__ == "__main__":
    start_server(8000, True)
