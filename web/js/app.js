// crunchyroller - web dashboard logic

let pollTimer = null;

// quick toast popup
function toast(msg, type = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show ' + type;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.className = '', 2800);
}

// fetch wrapper for backend API calls
async function api(endpoint, payload = null) {
  const options = payload != null
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }
    : { method: 'GET' };
  const res = await fetch(endpoint, options);
  return res.json();
}

// init app state on page load
window.addEventListener('DOMContentLoaded', async () => {
  const state = await api('/api/state');
  applyState(state);
});

// sync UI with backend state
function applyState(state) {
  const badge = document.getElementById('badge');
  const badgeTxt = document.getElementById('badge-txt');

  if (state.authenticated) {
    badge.classList.add('on');
    badgeTxt.textContent = 'connected';
  } else {
    badge.classList.remove('on');
    badgeTxt.textContent = 'offline';
  }

  // update settings selects
  const fieldMap = { vq: 'video_quality', aq: 'audio_quality', al: 'audio_lang', sl: 'subs_lang' };
  Object.entries(fieldMap).forEach(([id, key]) => {
    const el = document.getElementById(id);
    if (el && state.config[key]) el.value = state.config[key];
  });

  // if a download is running, start polling progress
  if (state.download.status === 'running') startPolling();
  updateProgressPanel(state.download);
}

// scan browser for session cookie
async function detect() {
  toast('scanning browsers…');
  const res = await api('/api/auto-detect', {});
  if (res.success) {
    toast('found session cookie!', 'ok');
    document.getElementById('badge').classList.add('on');
    document.getElementById('badge-txt').textContent = 'connected';
  } else {
    toast(res.error || 'no cookie found', 'err');
  }
}

// launch pywebview window to log in
async function webviewLogin() {
  toast('opening in-app browser…');
  const res = await api('/api/webview-login', {});
  if (res.success) {
    toast('logged in!', 'ok');
    document.getElementById('badge').classList.add('on');
    document.getElementById('badge-txt').textContent = 'connected';
  } else {
    toast(res.error || 'login closed', 'err');
  }
}

// manual token save
async function saveToken() {
  const val = document.getElementById('tok').value.trim();
  if (!val) {
    toast('paste your token first', 'err');
    return;
  }
  const res = await api('/api/login', { etp_rt: val });
  if (res.success) {
    toast('token saved!', 'ok');
    document.getElementById('badge').classList.add('on');
    document.getElementById('badge-txt').textContent = 'connected';
    document.getElementById('tok').value = '';
  } else {
    toast(res.error || 'invalid token', 'err');
  }
}

// save quality / language dropdowns
async function saveCfg() {
  await api('/api/config', {
    video_quality: document.getElementById('vq').value,
    audio_quality: document.getElementById('aq').value,
    audio_lang: document.getElementById('al').value,
    subs_lang: document.getElementById('sl').value,
  });
}

// fetch URL metadata & show episode tree
async function fetchUrl() {
  const url = document.getElementById('url').value.trim();
  if (!url) {
    toast('paste a crunchyroll url', 'err');
    return;
  }

  const btn = document.getElementById('fetch-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>';

  const res = await api('/api/fetch', { url });
  btn.disabled = false;
  btn.textContent = 'fetch';

  if (!res.success) {
    toast(res.error || 'fetch failed', 'err');
    return;
  }

  renderEpisodeTree(res);
  toast(res.title, 'ok');
}

// render season and episode checkboxes
function renderEpisodeTree(data) {
  document.getElementById('ser-title').textContent = data.title;
  const list = document.getElementById('sn-list');
  list.innerHTML = '';

  data.seasons.forEach((season, sIdx) => {
    const block = document.createElement('div');
    block.className = 'sn-block';

    // season header
    const head = document.createElement('div');
    head.className = 'sn-head';

    const seasonCb = document.createElement('input');
    seasonCb.type = 'checkbox';
    seasonCb.checked = true;
    seasonCb.id = 's' + sIdx;
    seasonCb.addEventListener('change', e => {
      block.querySelectorAll('.epc').forEach(cb => cb.checked = e.target.checked);
    });

    const label = document.createElement('label');
    label.htmlFor = 's' + sIdx;
    label.textContent = 'season ' + season.season_number;
    label.style.cssText = 'cursor:pointer;color:var(--white);font-weight:600;margin:0';

    const count = document.createElement('span');
    count.className = 'sn-count';
    count.textContent = season.episodes.length + ' ep';

    head.append(seasonCb, label, count);

    // episode rows
    const epList = document.createElement('div');
    epList.className = 'ep-list';

    season.episodes.forEach(ep => {
      const row = document.createElement('div');
      row.className = 'ep-row';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.className = 'epc';
      cb.dataset.id = ep.id;

      const num = document.createElement('span');
      num.className = 'ep-num';
      num.textContent = 'E' + String(ep.episode_number).padStart(2, '0');

      const name = document.createElement('span');
      name.className = 'ep-name';
      name.textContent = ep.title;

      row.addEventListener('click', e => {
        if (e.target !== cb) cb.checked = !cb.checked;
      });

      row.append(cb, num, name);
      epList.appendChild(row);
    });

    block.append(head, epList);
    list.appendChild(block);
  });

  document.getElementById('tree').style.display = 'block';
}

// select / deselect all episodes
function pickAll(val) {
  document.querySelectorAll('.epc, [id^="s"]').forEach(cb => cb.checked = val);
}

// start batch download task
async function startDl() {
  const selected = [...document.querySelectorAll('.epc:checked')].map(c => ({ id: c.dataset.id }));
  if (!selected.length) {
    toast('pick some episodes first', 'err');
    return;
  }

  const res = await api('/api/download', {
    items: selected,
    video_quality: document.getElementById('vq').value,
    audio_quality: document.getElementById('aq').value,
    audio_lang: document.getElementById('al').value,
    subs_lang: document.getElementById('sl').value,
  });

  if (!res.success) {
    toast(res.error || 'download failed to start', 'err');
    return;
  }

  toast(selected.length + ' episode(s) starting…');
  document.getElementById('dl-panel').style.display = 'block';
  startPolling();
}

// poll progress every 1.2s while download is active
function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    const state = await api('/api/state');
    updateProgressPanel(state.download);
    if (state.download.status !== 'running') {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }, 1200);
}

// update progress bar and console logs
function updateProgressPanel(dl) {
  if (!dl || dl.status === 'idle') return;
  document.getElementById('dl-panel').style.display = 'block';

  const pct = Math.min(100, dl.progress || 0);
  document.getElementById('pbar').style.width = pct + '%';
  document.getElementById('ppct').textContent = pct.toFixed(1) + '%';
  document.getElementById('cur-ep').textContent = dl.episode || '';

  const pill = document.getElementById('pill');
  if (dl.status === 'running') {
    pill.className = 'pill pill-run';
    pill.innerHTML = '<span class="spin"></span>downloading';
  } else if (dl.status === 'completed') {
    pill.className = 'pill pill-ok';
    pill.innerHTML = '✓ done';
  } else {
    pill.className = 'pill pill-err';
    pill.innerHTML = '✗ ' + dl.status;
  }

  const logBox = document.getElementById('log');
  if (dl.log && dl.log.length) {
    logBox.textContent = dl.log.join('\n');
    logBox.scrollTop = logBox.scrollHeight;
  }
}
