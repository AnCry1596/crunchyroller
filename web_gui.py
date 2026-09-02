import http.server
import json
import os
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse

# Ensure pywebview uses PyQt6 on Linux when available
os.environ.setdefault("QT_API", "pyqt6")

class SafeStream:
    def __init__(self, target):
        self._target = target

    def write(self, s):
        if self._target is None:
            return
        try:
            self._target.write(s)
        except (AttributeError, UnicodeEncodeError):
            try:
                enc = getattr(self._target, "encoding", "utf-8") or "utf-8"
                safe_s = s.encode(enc, errors="replace").decode(enc, errors="replace")
                self._target.write(safe_s)
            except Exception:
                pass

    def flush(self):
        if self._target is not None and hasattr(self._target, "flush"):
            try:
                self._target.flush()
            except Exception:
                pass

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    if hasattr(sys.stderr, "reconfigure"):
        try: sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

sys.stdout = SafeStream(sys.stdout)
sys.stderr = SafeStream(sys.stderr)

from crunchyroll.api import get_episode_info, get_season_episodes, get_series, parse_url_type
from crunchyroll.auth import load_config, save_config, auto_detect_etp_rt, open_webview_login
from crunchyroll.downloader import download_episode
from crunchyroll.http_client import CrunchyrollHttpClient

# root folder for static web assets
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# global download & app state
initial_cfg = load_config()
STATE = {
    "etp_rt": initial_cfg.get("etp_rt", ""),
    "config": {
        "video_quality": initial_cfg.get("video_quality", "1080p"),
        "audio_quality": initial_cfg.get("audio_quality", "192k"),
        "audio_lang":    initial_cfg.get("audio_lang", "ja-JP"),
        "subs_lang":     initial_cfg.get("subs_lang", "en-US"),
    },
    "download": {
        "status":      "idle",
        "episode":     "",
        "track":       "",
        "ep_idx":      0,
        "ep_total":    0,
        "segs_done":   0,
        "segs_total":  0,
        "speed":       "",
        "overall_pct": 0.0,
        "track_pct":   0.0,
        "log":         [],
    },
}
LOCK = threading.Lock()


def _log(msg):
    with LOCK:
        STATE["download"]["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(STATE["download"]["log"]) > 200:
            STATE["download"]["log"].pop(0)


def _run_download(items, vq, aq, al, sl):
    ep_total = len(items)
    with LOCK:
        STATE["download"].update(
            status="running", episode="", speed="", track="",
            segs_done=0, segs_total=0, ep_idx=0, ep_total=ep_total,
            overall_pct=0.0, track_pct=0.0, log=[],
        )

    client = CrunchyrollHttpClient(STATE["etp_rt"])
    _log(f"starting {ep_total} episode(s)...")

    a_langs = [x.strip() for x in al.split(",") if x.strip()] or ["ja-JP"]
    s_langs = [x.strip() for x in sl.split(",") if x.strip()] or ["en-US"]

    for idx, item in enumerate(items):
        ep_id = item.get("id") if isinstance(item, dict) else item
        try:
            info = get_episode_info(client, ep_id)
            label = f"S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d} \u2014 {info.title}"
            with LOCK:
                STATE["download"]["ep_idx"]      = idx
                STATE["download"]["episode"]     = label
                STATE["download"]["track"]       = "starting"
                STATE["download"]["segs_done"]   = 0
                STATE["download"]["segs_total"]  = 0
                STATE["download"]["track_pct"]   = 0.0
                STATE["download"]["overall_pct"] = round((idx / ep_total) * 100, 1)
            _log(f"[{idx+1}/{ep_total}] {label} [{vq}/{aq}]")

            def _cb(title, cur, tot, speed, status, _idx=idx):
                ep_base = (_idx / ep_total) * 100
                ep_slice = (1 / ep_total) * 100

                track_type = str(status).lower() if status else "video"
                frac = (cur / tot) if tot > 0 else 0.0

                if "audio" in track_type:
                    # Audio represents the first 15% of the episode
                    within_ep = frac * 0.15
                    display_track = "audio"
                elif "mux" in track_type:
                    within_ep = 0.98
                    display_track = "muxing"
                elif "done" in track_type:
                    within_ep = 1.0
                    display_track = "done"
                else:
                    # Video represents 15% - 95% of the episode
                    within_ep = 0.15 + (frac * 0.80)
                    display_track = "video"

                overall = round(ep_base + (within_ep * ep_slice), 1)
                cap = round(((_idx + 1) / ep_total) * 100 - 0.1, 1)

                with LOCK:
                    STATE["download"]["segs_done"]   = cur
                    STATE["download"]["segs_total"]  = tot
                    STATE["download"]["speed"]        = speed or ""
                    STATE["download"]["track"]        = display_track
                    STATE["download"]["track_pct"]   = round(frac * 100, 1) if "mux" not in track_type else 100.0
                    STATE["download"]["overall_pct"] = min(overall, cap)

            download_episode(
                client=client, base_content_id=ep_id, info=info,
                audio_langs=a_langs, subs_langs=s_langs,
                video_quality=vq, audio_quality=aq, progress_cb=_cb,
            )
            with LOCK:
                STATE["download"]["overall_pct"] = round(((idx + 1) / ep_total) * 100, 1)
                STATE["download"]["track_pct"]   = 100.0
                STATE["download"]["track"]       = "done"
                STATE["download"]["speed"]       = ""
            _log(f"done: {label}")

        except Exception as e:
            _log(f"error on {ep_id}: {e}")
            with LOCK:
                STATE["download"].update(status="error", episode=f"failed: {ep_id}")
            return

    with LOCK:
        STATE["download"].update(
            status="completed", overall_pct=100.0, track_pct=100.0,
            episode="all done", track="", speed=""
        )
    _log(f"finished {ep_total} episode(s)")




# http request handler
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for h, v in [("Access-Control-Allow-Origin","*"),("Access-Control-Allow-Methods","GET,POST,OPTIONS"),("Access-Control-Allow-Headers","Content-Type")]:
            self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        # REST API endpoints
        if path == "/api/state":
            with LOCK:
                self._json({"authenticated": bool(STATE["etp_rt"]), "config": STATE["config"], "download": STATE["download"]})
            return

        # serve static files from web/ directory
        if path == "/":
            rel_path = "index.html"
        else:
            rel_path = path.lstrip("/")

        full_path = os.path.normpath(os.path.join(WEB_DIR, rel_path))

        # prevent directory traversal
        if not full_path.startswith(WEB_DIR) or not os.path.exists(full_path) or os.path.isdir(full_path):
            self.send_error(404, "File not found")
            return

        # content types
        ext = os.path.splitext(full_path)[1].lower()
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        ctype = content_types.get(ext, "application/octet-stream")

        try:
            with open(full_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_error(500, "Internal server error")

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        if path == "/api/auto-detect":
            tok = auto_detect_etp_rt()
            if tok:
                with LOCK: STATE["etp_rt"] = tok
                save_config({"etp_rt": tok})
                self._json({"success": True})
            else:
                self._json({"success": False, "error": "couldn't find a session cookie. log into crunchyroll.com first."}, 404)

        elif path == "/api/webview-login":
            tok = open_webview_login()
            if tok:
                with LOCK: STATE["etp_rt"] = tok
                save_config({"etp_rt": tok})
                self._json({"success": True, "etp_rt": tok})
            else:
                self._json({"success": False, "error": "In-app login window closed or session token not detected."}, 400)

        elif path == "/api/login":
            tok = data.get("etp_rt", "").strip()
            if not tok:
                self._json({"success": False, "error": "paste your etp_rt token"}, 400); return
            try:
                CrunchyrollHttpClient(etp_rt=tok)
                with LOCK: STATE["etp_rt"] = tok
                save_config({"etp_rt": tok})
                self._json({"success": True})
            except Exception as e:
                self._json({"success": False, "error": str(e)}, 401)

        elif path == "/api/config":
            with LOCK:
                for k in ("video_quality","audio_quality","audio_lang","subs_lang"):
                    if k in data: STATE["config"][k] = data[k]
            save_config(STATE["config"])
            self._json({"success": True})

        elif path == "/api/fetch":
            url = data.get("url","").strip()
            if not url: self._json({"success":False,"error":"url required"},400); return
            if not STATE["etp_rt"]: self._json({"success":False,"error":"not logged in"},401); return
            try:
                client = CrunchyrollHttpClient(STATE["etp_rt"])
                kind, cid = parse_url_type(url)
                al, sl = STATE["config"]["audio_lang"], STATE["config"]["subs_lang"]
                api_audio = al if al.strip().lower() not in {"all", "*"} else "ja-JP"
                api_subs = sl if sl.strip().lower() not in {"all", "*"} else "en-US"
                if kind == "episode":
                    info = get_episode_info(client, cid)
                    seasons = [{"season_number": info.episode_metadata.season_number,
                        "episodes": [{"id":cid,"title":info.title,"episode_number":info.episode_metadata.episode_number,
                                      "season_number":info.episode_metadata.season_number,"series_title":info.episode_metadata.series_title}]}]
                    title = info.episode_metadata.series_title
                else:
                    s = get_series(client, cid, api_audio, api_subs)
                    title = s.get("title","")
                    seasons = []
                    for sn in s.get("seasons",[]):
                        eps = get_season_episodes(client, sn.id, api_audio, api_subs)
                        seasons.append({"season_number":sn.season_number,
                            "episodes":[{"id":e.id,"title":e.title,"episode_number":e.episode_number,
                                         "season_number":e.season_number,"series_title":e.series_title} for e in eps]})
                self._json({"success":True,"title":title,"seasons":seasons})
            except Exception as e:
                self._json({"success":False,"error":str(e)},500)

        elif path == "/api/download":
            if STATE["download"]["status"] == "running":
                self._json({"success":False,"error":"already downloading"},400); return
            if not STATE["etp_rt"]:
                self._json({"success":False,"error":"not logged in"},401); return
            items = data.get("items",[])
            if not items:
                self._json({"success":False,"error":"select some episodes"},400); return
            c = STATE["config"]
            threading.Thread(target=_run_download, daemon=True, args=(
                items,
                data.get("video_quality", c["video_quality"]),
                data.get("audio_quality", c["audio_quality"]),
                data.get("audio_lang", c["audio_lang"]),
                data.get("subs_lang", c["subs_lang"]),
            )).start()
            self._json({"success": True})
        else:
            self.send_error(404)


def start_gui(port=8000, use_browser=False):
    """launch crunchyroller inside a native desktop pywebview window (or default browser)"""
    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}"
    print(f"crunchyroller running on {url}")

    if use_browser:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nstopped.")
    else:
        try:
            import webview
            webview.create_window(
                "crunchyroller",
                url=url,
                width=860,
                height=760,
                min_size=(640, 520),
                background_color="#000000",
            )
            gui_backend = "qt" if sys.platform != "win32" else None
            webview.start(gui=gui_backend)
        except Exception as e:
            err = str(e).lower()
            # WebView2 not installed — show a dialog so the user knows what to do
            if "webview2" in err or "edge" in err or "clsid" in err or "cocreateinstance" in err or True:
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        "The app needs Microsoft Edge WebView2 to run as a native window.\n\n"
                        "Download it from:\nhttps://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                        "Opening in your browser for now as a fallback.",
                        "WebView2 Required",
                        0x40  # MB_ICONINFORMATION
                    )
                except Exception:
                    pass
            print(f"native window failed ({e}), opening in browser...")
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nstopped.")


def start_server(port=8000, open_browser=False):
    start_gui(port=port, use_browser=open_browser)

