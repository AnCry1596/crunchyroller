import http.server
import json
import os
import threading
import time
import webbrowser
from urllib.parse import urlparse

from crunchyroll.api import get_episode_info, get_season_episodes, get_series, parse_url_type
from crunchyroll.auth import load_config, save_config, auto_detect_etp_rt
from crunchyroll.downloader import download_episode
from crunchyroll.http_client import CrunchyrollHttpClient

# -- state --
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
        # keep it from growing forever
        if len(STATE["download"]["log"]) > 200:
            STATE["download"]["log"].pop(0)


# -- download thread --
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


# -- http handler --
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
        if path in ("/", "/index.html"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            with LOCK:
                self._json({"authenticated": bool(STATE["etp_rt"]), "config": STATE["config"], "download": STATE["download"]})
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try: data = json.loads(raw)
        except: data = {}

        if path == "/api/auto-detect":
            tok = auto_detect_etp_rt()
            if tok:
                with LOCK: STATE["etp_rt"] = tok
                save_config({"etp_rt": tok})
                self._json({"success": True})
            else:
                self._json({"success": False, "error": "couldn't find a session cookie. log into crunchyroll.com first."}, 404)

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


# -- the whole frontend in one string --
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crunchyroller</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0c0d10;
  --card:#141518;
  --border:#1f2028;
  --accent:#8b7cf7;
  --accent-dim:rgba(139,124,247,.12);
  --accent-glow:rgba(139,124,247,.25);
  --text:#d4d4d8;
  --text2:#71717a;
  --white:#f4f4f5;
  --green:#4ade80;
  --red:#f87171;
  --radius:12px;
}
body{
  font-family:'Inter',system-ui,sans-serif;
  background:var(--bg);color:var(--text);
  min-height:100vh;display:flex;flex-direction:column;align-items:center;
}

/* header */
header{
  width:100%;padding:16px 32px;display:flex;align-items:center;gap:12px;
  border-bottom:1px solid var(--border);background:rgba(20,21,24,.8);
  backdrop-filter:blur(16px);position:sticky;top:0;z-index:50;
}
.logo{
  width:32px;height:32px;border-radius:8px;
  background:linear-gradient(135deg,#7c5cbf,#a78bfa);
  display:flex;align-items:center;justify-content:center;
  font-size:16px;
}
header h1{font-size:1.15rem;font-weight:700;color:var(--white);letter-spacing:-.5px}
header h1 b{color:var(--accent);font-weight:700}
.badge{
  margin-left:auto;display:flex;align-items:center;gap:6px;
  padding:5px 12px;border-radius:99px;font-size:.75rem;font-weight:500;
  border:1px solid var(--border);color:var(--text2);
}
.badge .dot{width:7px;height:7px;border-radius:50%;background:var(--text2)}
.badge.on .dot{background:var(--green);box-shadow:0 0 6px var(--green)}
.badge.on{color:var(--green);border-color:rgba(74,222,128,.2)}

/* main */
main{width:100%;max-width:880px;padding:28px 20px 60px;display:flex;flex-direction:column;gap:20px}

/* card */
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:22px}
.card-label{
  font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;
  color:var(--text2);margin-bottom:14px;
}

/* inputs */
input,select{
  width:100%;background:rgba(0,0,0,.3);border:1px solid var(--border);
  border-radius:8px;color:var(--text);font-family:inherit;font-size:.88rem;
  padding:10px 12px;outline:none;transition:border .2s;
}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-dim)}
input::placeholder{color:var(--text2)}
select option{background:#1a1b1e}
label{display:block;font-size:.78rem;color:var(--text2);margin-bottom:5px;font-weight:500}
.field{margin-bottom:12px}.field:last-child{margin-bottom:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:560px){.grid2{grid-template-columns:1fr}}

/* buttons */
button{
  cursor:pointer;border:none;outline:none;font-family:inherit;
  font-weight:600;font-size:.88rem;border-radius:8px;padding:10px 18px;
  transition:all .15s;
}
button:active{transform:scale(.97)}
.btn{background:var(--accent);color:#fff}
.btn:hover{box-shadow:0 0 16px var(--accent-glow)}
.btn-o{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-o:hover{border-color:var(--accent);color:var(--accent)}
.btn-s{padding:7px 12px;font-size:.8rem;border-radius:7px}
.btn-w{width:100%}

/* layout helpers */
.row{display:flex;gap:8px}
.row input{flex:1}
.gap{gap:8px;display:flex}

/* episode tree */
#tree{display:none;margin-top:16px}
.ser-title{font-size:1rem;font-weight:700;color:var(--white);margin-bottom:12px}
.sn-block{margin-bottom:10px}
.sn-head{
  display:flex;align-items:center;gap:8px;padding:8px 12px;
  background:rgba(255,255,255,.02);border:1px solid var(--border);
  border-radius:8px;cursor:pointer;user-select:none;font-size:.84rem;font-weight:600;
  transition:background .15s;
}
.sn-head:hover{background:rgba(255,255,255,.05)}
.sn-head input[type=checkbox]{accent-color:var(--accent);width:14px;height:14px}
.sn-count{margin-left:auto;font-size:.72rem;color:var(--text2);font-weight:400}
.ep-list{padding:4px 0 0 24px;display:flex;flex-direction:column;gap:2px}
.ep-row{
  display:flex;align-items:center;gap:8px;padding:6px 10px;
  border-radius:7px;cursor:pointer;transition:background .12s;font-size:.82rem;
}
.ep-row:hover{background:rgba(255,255,255,.03)}
.ep-row input[type=checkbox]{accent-color:var(--accent);width:13px;height:13px;flex-shrink:0}
.ep-num{color:var(--accent);font-weight:600;font-size:.75rem;min-width:28px}
.ep-name{color:var(--text)}

/* progress */
#dl-panel{display:none}
.prog-bar-wrap{background:rgba(0,0,0,.3);border-radius:99px;height:6px;overflow:hidden;margin:10px 0}
.prog-bar{height:100%;background:linear-gradient(to right,#7c5cbf,#a78bfa);border-radius:99px;transition:width .4s;width:0%}
.prog-meta{display:flex;justify-content:space-between;font-size:.75rem;color:var(--text2)}
.pill{
  display:inline-flex;align-items:center;gap:5px;padding:3px 10px;
  border-radius:99px;font-size:.73rem;font-weight:600;
}
.pill-run{background:var(--accent-dim);color:var(--accent);border:1px solid rgba(139,124,247,.25)}
.pill-ok{background:rgba(74,222,128,.1);color:var(--green);border:1px solid rgba(74,222,128,.2)}
.pill-err{background:rgba(248,113,113,.1);color:var(--red);border:1px solid rgba(248,113,113,.2)}

/* log */
#log{
  background:rgba(0,0,0,.35);border:1px solid var(--border);border-radius:8px;
  padding:12px;font-family:'Courier New',monospace;font-size:.73rem;
  color:var(--text2);height:140px;overflow-y:auto;white-space:pre-wrap;
  line-height:1.55;margin-top:12px;
}
#log::-webkit-scrollbar{width:3px}
#log::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* toast */
#toast{
  position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(16px);
  background:rgba(20,21,24,.92);border:1px solid var(--border);backdrop-filter:blur(12px);
  padding:10px 20px;border-radius:99px;font-size:.82rem;font-weight:500;
  opacity:0;pointer-events:none;transition:all .25s;z-index:200;
}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#toast.err{color:var(--red);border-color:rgba(248,113,113,.3)}
#toast.ok{color:var(--green);border-color:rgba(74,222,128,.3)}

/* spinner */
.spin{display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.15);border-top-color:var(--accent);border-radius:50%;animation:sp .6s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}

hr.div{border:none;border-top:1px solid var(--border);margin:14px 0}
</style>
</head>
<body>

<header>
  <div class="logo">🌀</div>
  <h1>crunchy<b>roller</b></h1>
  <div class="badge" id="badge"><div class="dot"></div><span id="badge-txt">offline</span></div>
</header>

<main>

  <!-- auth -->
  <div class="card" id="auth-card">
    <div class="card-label">session</div>
    <div class="field">
      <label>etp_rt cookie</label>
      <div class="row">
        <input type="password" id="tok" placeholder="paste your token here…">
        <button class="btn-o btn-s" onclick="saveToken()">save</button>
      </div>
    </div>
    <button class="btn btn-w" onclick="detect()" style="margin-top:10px">⚡ auto-detect from browser</button>
    <p style="font-size:.72rem;color:var(--text2);margin-top:10px;line-height:1.45">
      log into <strong style="color:var(--white)">crunchyroll.com</strong> in your browser, then click auto-detect.
      or grab it manually: F12 → Application → Cookies → <code>etp_rt</code>
    </p>
  </div>

  <!-- settings -->
  <div class="card">
    <div class="card-label">settings</div>
    <div class="grid2">
      <div class="field">
        <label>video quality</label>
        <select id="vq" onchange="saveCfg()">
          <option value="1080p">1080p</option>
          <option value="720p">720p</option>
          <option value="480p">480p</option>
          <option value="360p">360p</option>
          <option value="240p">240p</option>
        </select>
      </div>
      <div class="field">
        <label>audio quality</label>
        <select id="aq" onchange="saveCfg()">
          <option value="192k">192k</option>
          <option value="96k">96k</option>
        </select>
      </div>
      <div class="field">
        <label>audio language</label>
        <select id="al" onchange="saveCfg()">
          <option value="ja-JP">Japanese</option>
          <option value="en-US">English</option>
          <option value="de-DE">German</option>
          <option value="fr-FR">French</option>
          <option value="es-419">Spanish (Latin)</option>
          <option value="pt-BR">Portuguese (BR)</option>
          <option value="ko-KR">Korean</option>
          <option value="zh-CN">Chinese (CN)</option>
        </select>
      </div>
      <div class="field">
        <label>subtitles</label>
        <select id="sl" onchange="saveCfg()">
          <option value="en-US">English</option>
          <option value="de-DE">German</option>
          <option value="fr-FR">French</option>
          <option value="es-419">Spanish (Latin)</option>
          <option value="pt-BR">Portuguese (BR)</option>
          <option value="ru-RU">Russian</option>
          <option value="ar-SA">Arabic</option>
        </select>
      </div>
    </div>
  </div>

  <!-- content -->
  <div class="card">
    <div class="card-label">content</div>
    <div class="row">
      <input type="text" id="url" placeholder="crunchyroll.com/series/… or /watch/…">
      <button class="btn" id="fetch-btn" onclick="fetchUrl()">fetch</button>
    </div>
    <div id="tree">
      <hr class="div">
      <div class="ser-title" id="ser-title"></div>
      <div id="sn-list"></div>
      <hr class="div">
      <div class="row" style="justify-content:flex-end">
        <button class="btn-o btn-s" onclick="pickAll(true)">all</button>
        <button class="btn-o btn-s" onclick="pickAll(false)">none</button>
        <button class="btn" onclick="startDl()">download selected</button>
      </div>
    </div>
  </div>

  <!-- progress -->
  <div class="card" id="dl-panel">
    <div class="card-label">progress</div>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span id="pill" class="pill pill-run"><span class="spin"></span>downloading</span>
      <span id="cur-ep" style="font-size:.8rem;color:var(--text2)"></span>
    </div>
    <div class="prog-bar-wrap"><div class="prog-bar" id="pbar"></div></div>
    <div class="prog-meta"><span id="ppct">0%</span><span></span></div>
    <div id="log"></div>
  </div>

</main>
<div id="toast"></div>

<script>
let _poll=null;

function toast(m,t='ok'){const e=document.getElementById('toast');e.textContent=m;e.className='show '+t;clearTimeout(e._t);e._t=setTimeout(()=>e.className='',2800)}
async function api(p,b=null){const o=b!=null?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{};return(await fetch(p,o)).json()}

window.addEventListener('DOMContentLoaded',async()=>{const s=await api('/api/state');apply(s)});

function apply(s){
  const b=document.getElementById('badge'),t=document.getElementById('badge-txt');
  if(s.authenticated){b.classList.add('on');t.textContent='connected'}else{b.classList.remove('on');t.textContent='offline'}
  const m={vq:'video_quality',aq:'audio_quality',al:'audio_lang',sl:'subs_lang'};
  Object.entries(m).forEach(([id,k])=>{const e=document.getElementById(id);if(e&&s.config[k])e.value=s.config[k]});
  if(s.download.status==='running')poll();
  updDl(s.download);
}

async function detect(){toast('scanning…');const r=await api('/api/auto-detect',{});r.success?(toast('found it!'),document.getElementById('badge').classList.add('on'),document.getElementById('badge-txt').textContent='connected'):toast(r.error||'nope','err')}
async function saveToken(){const v=document.getElementById('tok').value.trim();if(!v){toast('paste your token','err');return}const r=await api('/api/login',{etp_rt:v});r.success?(toast('saved!'),document.getElementById('badge').classList.add('on'),document.getElementById('badge-txt').textContent='connected',document.getElementById('tok').value=''):toast(r.error||'bad token','err')}
async function saveCfg(){await api('/api/config',{video_quality:document.getElementById('vq').value,audio_quality:document.getElementById('aq').value,audio_lang:document.getElementById('al').value,subs_lang:document.getElementById('sl').value})}

async function fetchUrl(){
  const u=document.getElementById('url').value.trim();if(!u){toast('paste a url','err');return}
  const btn=document.getElementById('fetch-btn');btn.disabled=true;btn.innerHTML='<span class="spin"></span>';
  const r=await api('/api/fetch',{url:u});btn.disabled=false;btn.textContent='fetch';
  if(!r.success){toast(r.error||'failed','err');return}
  renderTree(r);toast(r.title,'ok');
}

function renderTree(d){
  document.getElementById('ser-title').textContent=d.title;
  const list=document.getElementById('sn-list');list.innerHTML='';
  d.seasons.forEach((sn,si)=>{
    const bl=document.createElement('div');bl.className='sn-block';
    const hd=document.createElement('div');hd.className='sn-head';
    const sc=document.createElement('input');sc.type='checkbox';sc.checked=true;sc.id='s'+si;
    sc.addEventListener('change',e=>{bl.querySelectorAll('.epc').forEach(c=>c.checked=e.target.checked)});
    const lb=document.createElement('label');lb.htmlFor='s'+si;lb.textContent='season '+sn.season_number;lb.style.cssText='cursor:pointer;color:var(--white);font-weight:600;margin:0';
    const ct=document.createElement('span');ct.className='sn-count';ct.textContent=sn.episodes.length+' ep';
    hd.append(sc,lb,ct);
    const el=document.createElement('div');el.className='ep-list';
    sn.episodes.forEach(ep=>{
      const row=document.createElement('div');row.className='ep-row';
      const cb=document.createElement('input');cb.type='checkbox';cb.checked=true;cb.className='epc';cb.dataset.id=ep.id;
      const num=document.createElement('span');num.className='ep-num';num.textContent='E'+String(ep.episode_number).padStart(2,'0');
      const nm=document.createElement('span');nm.className='ep-name';nm.textContent=ep.title;
      row.addEventListener('click',e=>{if(e.target!==cb)cb.checked=!cb.checked});
      row.append(cb,num,nm);el.appendChild(row);
    });
    bl.append(hd,el);list.appendChild(bl);
  });
  document.getElementById('tree').style.display='block';
}

function pickAll(v){document.querySelectorAll('.epc,[id^="s"]').forEach(c=>c.checked=v)}

async function startDl(){
  const sel=[...document.querySelectorAll('.epc:checked')].map(c=>({id:c.dataset.id}));
  if(!sel.length){toast('pick some episodes','err');return}
  const r=await api('/api/download',{items:sel,video_quality:document.getElementById('vq').value,audio_quality:document.getElementById('aq').value,audio_lang:document.getElementById('al').value,subs_lang:document.getElementById('sl').value});
  if(!r.success){toast(r.error||'nope','err');return}
  toast(sel.length+' episode(s) starting…');document.getElementById('dl-panel').style.display='block';poll();
}

function poll(){if(_poll)return;_poll=setInterval(async()=>{const s=await api('/api/state');updDl(s.download);if(s.download.status!=='running'){clearInterval(_poll);_poll=null}},1200)}

function updDl(dl){
  if(!dl||dl.status==='idle')return;
  document.getElementById('dl-panel').style.display='block';
  const p=Math.min(100,dl.progress||0);
  document.getElementById('pbar').style.width=p+'%';
  document.getElementById('ppct').textContent=p.toFixed(1)+'%';
  document.getElementById('cur-ep').textContent=dl.episode||'';
  const pill=document.getElementById('pill');
  if(dl.status==='running'){pill.className='pill pill-run';pill.innerHTML='<span class="spin"></span>downloading'}
  else if(dl.status==='completed'){pill.className='pill pill-ok';pill.innerHTML='✓ done'}
  else{pill.className='pill pill-err';pill.innerHTML='✗ '+dl.status}
  const log=document.getElementById('log');
  if(dl.log&&dl.log.length){log.textContent=dl.log.join('\n');log.scrollTop=log.scrollHeight}
}
</script>
</body>
</html>"""


def start_server(port=8000, open_browser=True):
    srv = http.server.HTTPServer(("", port), Handler)
    print(f"crunchyroller → http://localhost:{port}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
