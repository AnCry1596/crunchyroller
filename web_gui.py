import http.server
import json
import os
import threading
import time
import webbrowser
from urllib.parse import urlparse

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
        "status":   "idle",
        "progress": 0.0,
        "episode":  "",
        "log":      [],
    },
}
LOCK = threading.Lock()


def _log(msg):
    with LOCK:
        STATE["download"]["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(STATE["download"]["log"]) > 200:
            STATE["download"]["log"].pop(0)


# background download runner
def _run_download(items, vq, aq, al, sl):
    with LOCK:
        STATE["download"].update(status="running", progress=0.0, log=[])

    client = CrunchyrollHttpClient(STATE["etp_rt"])
    total = len(items)
    _log(f"starting {total} episode(s)...")

    a_langs = [x.strip() for x in al.split(",") if x.strip()] or ["ja-JP"]
    s_langs = [x.strip() for x in sl.split(",") if x.strip()] or ["en-US"]

    for idx, item in enumerate(items):
        ep_id = item.get("id") if isinstance(item, dict) else item
        try:
            info = get_episode_info(client, ep_id)
            label = f"S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d} — {info.title}"
            with LOCK:
                STATE["download"]["episode"]  = label
                STATE["download"]["progress"] = round((idx / total) * 100, 1)
            _log(f"[{idx+1}/{total}] {label} [{vq}/{aq}]")

            def _cb(title, cur, tot, speed, status):
                with LOCK:
                    base  = (idx / total) * 100
                    extra = ((cur / tot) / total) * 100 if tot > 0 else 0
                    STATE["download"]["progress"] = round(base + extra, 1)
                    STATE["download"]["episode"]  = f"{title} ({cur}/{tot})"

            download_episode(
                client=client, base_content_id=ep_id, info=info,
                audio_langs=a_langs, subs_langs=s_langs,
                video_quality=vq, audio_quality=aq, progress_cb=_cb,
            )
            _log(f"done: {label}")
        except Exception as e:
            _log(f"error on {ep_id}: {e}")

    with LOCK:
        STATE["download"].update(status="completed", progress=100.0, episode="all done")
    _log(f"finished {total} episode(s)")


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
                if kind == "episode":
                    info = get_episode_info(client, cid)
                    seasons = [{"season_number": info.episode_metadata.season_number,
                        "episodes": [{"id":cid,"title":info.title,"episode_number":info.episode_metadata.episode_number,
                                      "season_number":info.episode_metadata.season_number,"series_title":info.episode_metadata.series_title}]}]
                    title = info.episode_metadata.series_title
                else:
                    s = get_series(client, cid, al, sl)
                    title = s.get("title","")
                    seasons = []
                    for sn in s.get("seasons",[]):
                        eps = get_season_episodes(client, sn.id, al, sl)
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


def start_server(port=8000, open_browser=True):
    srv = http.server.HTTPServer(("", port), Handler)
    print(f"crunchyroller → http://localhost:{port}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
