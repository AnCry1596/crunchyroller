// crunchyroller - web dashboard logic

let pollTimer = null;
let ddVideo = null;
let ddAudioQual = null;
let ddAudio = null;
let ddSubs = null;

const VIDEO_OPTIONS = [
  { val: '1080p', label: '1080p' },
  { val: '720p', label: '720p' },
  { val: '480p', label: '480p' },
  { val: '360p', label: '360p' },
  { val: '240p', label: '240p' }
];

const AUDIO_QUAL_OPTIONS = [
  { val: '192k', label: '192k' },
  { val: '96k', label: '96k' }
];

const AUDIO_OPTIONS = [
  { val: 'all', label: 'All available' },
  { val: 'ja-JP', label: 'Japanese' },
  { val: 'en-US', label: 'English' },
  { val: 'de-DE', label: 'German' },
  { val: 'fr-FR', label: 'French' },
  { val: 'es-419', label: 'Spanish (Latin America)' },
  { val: 'es-ES', label: 'Spanish (Spain)' },
  { val: 'pt-BR', label: 'Portuguese (Brazil)' },
  { val: 'pt-PT', label: 'Portuguese (Portugal)' },
  { val: 'it-IT', label: 'Italian' },
  { val: 'ru-RU', label: 'Russian' },
  { val: 'ar-SA', label: 'Arabic' },
  { val: 'hi-IN', label: 'Hindi' },
  { val: 'ko-KR', label: 'Korean' },
  { val: 'zh-CN', label: 'Chinese' },
  { val: 'id-ID', label: 'Indonesian' }
];

const SUBS_OPTIONS = [
  { val: 'all', label: 'All available' },
  { val: 'en-US', label: 'English' },
  { val: 'es-419', label: 'Spanish (Latin America)' },
  { val: 'es-ES', label: 'Spanish (Spain)' },
  { val: 'pt-BR', label: 'Portuguese (Brazil)' },
  { val: 'pt-PT', label: 'Portuguese (Portugal)' },
  { val: 'fr-FR', label: 'French' },
  { val: 'de-DE', label: 'German' },
  { val: 'it-IT', label: 'Italian' },
  { val: 'ru-RU', label: 'Russian' },
  { val: 'ar-SA', label: 'Arabic' },
  { val: 'hi-IN', label: 'Hindi' },
  { val: 'id-ID', label: 'Indonesian' },
  { val: 'vi-VN', label: 'Vietnamese' },
  { val: 'th-TH', label: 'Thai' },
  { val: 'tr-TR', label: 'Turkish' },
  { val: 'pl-PL', label: 'Polish' }
];

class CheckboxDropdown {
  constructor(containerId, hiddenInputId, options, defaultVal, onChange, multi = true) {
    this.container = document.getElementById(containerId);
    this.hiddenInput = document.getElementById(hiddenInputId);
    this.options = options;
    this.onChange = onChange;
    this.multi = multi;
    this.value = defaultVal || (multi ? 'all' : (options[0] ? options[0].val : ''));
    this.init();
  }

  init() {
    if (!this.container) return;
    this.container.innerHTML = `
      <div class="select-btn" tabindex="0" role="button" aria-haspopup="listbox">
        <span class="select-btn-text"></span>
        <svg class="select-arrow" width="10" height="6" viewBox="0 0 10 6" fill="none">
          <path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="select-menu" role="listbox"></div>
    `;

    this.btn = this.container.querySelector('.select-btn');
    this.btnText = this.container.querySelector('.select-btn-text');
    this.menu = this.container.querySelector('.select-menu');

    this.btn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });

    this.btn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        this.toggle();
      }
    });

    document.addEventListener('click', (e) => {
      if (!this.container.contains(e.target)) {
        this.close();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.container.classList.contains('open')) {
        this.close();
      }
    });

    this.render();
  }

  toggle() {
    const wasOpen = this.container.classList.contains('open');
    document.querySelectorAll('.select-dropdown.open').forEach(el => el.classList.remove('open'));
    if (!wasOpen) {
      this.container.classList.add('open');
    }
  }

  close() {
    this.container.classList.remove('open');
  }

  getSelectedList() {
    if (!this.multi) return [this.value];
    if (!this.value || !this.value.trim()) return ['all'];
    if (this.value.trim().toLowerCase() === 'all') return ['all'];
    return this.value.split(',').map(s => s.trim()).filter(Boolean);
  }

  setValue(val, triggerChange = true) {
    if (!this.multi) {
      this.value = val || (this.options[0] ? this.options[0].val : '');
    } else {
      this.value = val || 'all';
    }
    if (this.hiddenInput) this.hiddenInput.value = this.value;
    this.render();
    if (triggerChange && this.onChange) {
      this.onChange(this.value);
    }
  }

  selectSingle(val) {
    this.setValue(val);
    this.close();
  }

  toggleItem(val) {
    if (!this.multi) {
      this.selectSingle(val);
      return;
    }

    if (val === 'all') {
      this.setValue('all');
      return;
    }

    let list = this.getSelectedList();
    const allSpecific = this.options.filter(o => o.val !== 'all').map(o => o.val.toLowerCase());

    if (list.includes('all')) {
      list = [val];
    } else {
      const idx = list.findIndex(c => c.toLowerCase() === val.toLowerCase());
      if (idx !== -1) {
        list.splice(idx, 1);
      } else {
        list.push(val);
      }
    }

    if (list.length === 0) {
      this.setValue('all');
      return;
    }

    if (list.length >= allSpecific.length && allSpecific.every(code => list.some(c => c.toLowerCase() === code))) {
      this.setValue('all');
      return;
    }

    this.setValue(list.join(','));
  }

  render() {
    if (!this.multi) {
      const found = this.options.find(o => o.val.toLowerCase() === (this.value || '').toLowerCase());
      this.btnText.textContent = found ? found.label : (this.value || 'Select');

      this.menu.innerHTML = '';
      this.options.forEach(opt => {
        const isChecked = opt.val.toLowerCase() === (this.value || '').toLowerCase();
        const row = document.createElement('div');
        row.className = 'select-opt' + (isChecked ? ' selected' : '');
        row.setAttribute('role', 'option');
        row.setAttribute('aria-selected', isChecked ? 'true' : 'false');

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'cb-custom';
        cb.checked = isChecked;
        cb.tabIndex = -1;

        const label = document.createElement('span');
        label.className = 'select-opt-text';
        label.textContent = opt.label;

        row.append(cb, label);
        row.addEventListener('click', (e) => {
          e.stopPropagation();
          this.selectSingle(opt.val);
        });

        this.menu.appendChild(row);
      });
      return;
    }

    const selected = this.getSelectedList();
    const isAll = selected.includes('all');

    if (isAll) {
      this.btnText.textContent = 'All available';
    } else {
      const labels = selected.map(code => {
        const found = this.options.find(o => o.val.toLowerCase() === code.toLowerCase());
        return found ? found.label : code;
      });
      if (labels.length === 1) {
        this.btnText.textContent = labels[0];
      } else if (labels.length === 2) {
        this.btnText.textContent = `${labels[0]}, ${labels[1]}`;
      } else {
        this.btnText.textContent = `${labels[0]}, ${labels[1]} +${labels.length - 2}`;
      }
    }

    this.menu.innerHTML = '';
    this.options.forEach(opt => {
      const isChecked = isAll ? (opt.val === 'all') : selected.some(c => c.toLowerCase() === opt.val.toLowerCase());
      const row = document.createElement('div');
      row.className = 'select-opt' + (isChecked ? ' selected' : '') + (opt.val === 'all' ? ' opt-all' : '');
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', isChecked ? 'true' : 'false');

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'cb-custom';
      cb.checked = isChecked;
      cb.tabIndex = -1;

      const label = document.createElement('span');
      label.className = 'select-opt-text';
      label.textContent = opt.label;

      row.append(cb, label);

      row.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleItem(opt.val);
      });

      this.menu.appendChild(row);
    });
  }
}

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
  try {
    const res = await fetch(endpoint, options);
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch (parseErr) {
      console.warn(`[api] Non-JSON response from ${endpoint} (HTTP ${res.status}):`, text.slice(0, 150));
      return {
        success: false,
        error: `HTTP ${res.status}: Server returned HTML instead of JSON. If you just updated files, please restart web_gui.py!`,
      };
    }
  } catch (netErr) {
    console.error(`[api] Network error calling ${endpoint}:`, netErr);
    return { success: false, error: netErr.message || 'Network error' };
  }
}

// init app state on page load
window.addEventListener('DOMContentLoaded', async () => {
  ddVideo = new CheckboxDropdown('dd-video', 'vq', VIDEO_OPTIONS, '1080p', () => saveCfg(), false);
  ddAudioQual = new CheckboxDropdown('dd-audio-qual', 'aq', AUDIO_QUAL_OPTIONS, '192k', () => saveCfg(), false);
  ddAudio = new CheckboxDropdown('dd-audio', 'al', AUDIO_OPTIONS, 'ja-JP', () => saveCfg(), true);
  ddSubs = new CheckboxDropdown('dd-subs', 'sl', SUBS_OPTIONS, 'en-US', () => saveCfg(), true);

  const state = await api('/api/state');
  applyState(state);

  ['login-email', 'login-pass'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          loginCredentials();
        }
      });
    }
  });

  initStarBanner();
});

// Prompt banner for starring the repo
function initStarBanner() {
  try {
    if (!localStorage.getItem('star_prompt_dismissed')) {
      const banner = document.getElementById('star-banner');
      if (banner) banner.style.display = 'flex';
    }
  } catch (e) {}
}

function dismissStarBanner(starred) {
  try {
    localStorage.setItem('star_prompt_dismissed', 'true');
  } catch (e) {}
  const banner = document.getElementById('star-banner');
  if (banner) {
    banner.style.opacity = '0';
    banner.style.transform = 'translateY(-6px)';
    setTimeout(() => { banner.style.display = 'none'; }, 200);
  }
}

// sync UI with backend state
function applyState(state) {
  const badge = document.getElementById('badge');
  const badgeTxt = document.getElementById('badge-txt');

  if (state.authenticated) {
    badge.classList.add('on');
    if (state.auth_type === 'android_tv') {
      badgeTxt.textContent = 'connected (login)';
    } else if (state.auth_type === 'token') {
      badgeTxt.textContent = 'connected (token)';
    } else {
      badgeTxt.textContent = 'connected';
    }
  } else {
    badge.classList.remove('on');
    badgeTxt.textContent = 'offline';
  }

  // update settings
  if (state.config) {
    if (ddVideo && state.config.video_quality) {
      ddVideo.setValue(state.config.video_quality, false);
    } else if (state.config.video_quality) {
      document.getElementById('vq').value = state.config.video_quality;
    }

    if (ddAudioQual && state.config.audio_quality) {
      ddAudioQual.setValue(state.config.audio_quality, false);
    } else if (state.config.audio_quality) {
      document.getElementById('aq').value = state.config.audio_quality;
    }

    if (ddAudio && state.config.audio_lang) {
      ddAudio.setValue(state.config.audio_lang, false);
    } else if (state.config.audio_lang) {
      document.getElementById('al').value = state.config.audio_lang;
    }

    if (ddSubs && state.config.subs_lang) {
      ddSubs.setValue(state.config.subs_lang, false);
    } else if (state.config.subs_lang) {
      document.getElementById('sl').value = state.config.subs_lang;
    }
    const forceDownload = document.getElementById('force-download');
    if (forceDownload) forceDownload.checked = Boolean(state.config.force_download);
  }

  // if a download is running, start polling progress
  if (state.download && state.download.status === 'running') startPolling();
  updateProgressPanel(state.download);
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
    document.getElementById('badge-txt').textContent = 'connected (token)';
    document.getElementById('tok').value = '';
  } else {
    toast(res.error || 'invalid token', 'err');
  }
}

// toggle collapsible Android TV login form
function toggleAndroidLoginForm() {
  const panel = document.getElementById('android-login-panel');
  const btn = document.getElementById('toggle-android-login-btn');
  if (!panel) return;
  const isHidden = panel.style.display === 'none' || !panel.style.display;
  panel.style.display = isHidden ? 'flex' : 'none';
  if (btn) btn.classList.toggle('active', isHidden);
  if (isHidden) {
    const emailInput = document.getElementById('login-email');
    if (emailInput) emailInput.focus();
  }
}

// Android TV username & password login
async function loginCredentials() {
  const username = (document.getElementById('login-email').value || '').trim();
  const password = (document.getElementById('login-pass').value || '').trim();
  if (!username || !password) {
    toast('enter email and password', 'err');
    return;
  }
  const btn = document.getElementById('btn-login-cred');
  const origText = btn ? btn.textContent : 'sign in';
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'signing in...';
  }
  toast('signing in...', 'ok');
  try {
    const res = await api('/api/login-credentials', { username, password });
    if (res.success) {
      toast('signed in successfully!', 'ok');
      document.getElementById('badge').classList.add('on');
      document.getElementById('badge-txt').textContent = 'connected (login)';
      document.getElementById('login-pass').value = '';
      setTimeout(() => {
        const panel = document.getElementById('android-login-panel');
        const toggleBtn = document.getElementById('toggle-android-login-btn');
        if (panel) panel.style.display = 'none';
        if (toggleBtn) toggleBtn.classList.remove('active');
      }, 1200);
    } else {
      toast(res.error || 'login failed', 'err');
    }
  } catch (e) {
    toast('login failed: ' + e.message, 'err');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = origText;
    }
  }
}

// save quality / language dropdowns
async function saveCfg() {
  const vqVal = ddVideo ? ddVideo.value : (document.getElementById('vq').value || '1080p');
  const aqVal = ddAudioQual ? ddAudioQual.value : (document.getElementById('aq').value || '192k');
  const audioVal = ddAudio ? ddAudio.value : (document.getElementById('al').value || 'ja-JP');
  const subsVal = ddSubs ? ddSubs.value : (document.getElementById('sl').value || 'en-US');

  await api('/api/config', {
    video_quality: vqVal,
    audio_quality: aqVal,
    audio_lang: audioVal,
    subs_lang: subsVal,
    force_download: (document.getElementById('force-download') || {}).checked || false,
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

// toggle expand/collapse all seasons
function toggleAllSeasons() {
  const blocks = document.querySelectorAll('.sn-block');
  const btn = document.getElementById('toggle-seasons-btn');
  if (!blocks.length) return;
  const anyCollapsed = [...blocks].some(b => b.classList.contains('collapsed'));
  blocks.forEach(b => b.classList.toggle('collapsed', !anyCollapsed));
  if (btn) btn.textContent = anyCollapsed ? 'collapse all' : 'expand all';
}

// render season and episode checkboxes
function renderEpisodeTree(data) {
  document.getElementById('ser-title').textContent = data.title;
  const toggleBtn = document.getElementById('toggle-seasons-btn');
  if (toggleBtn) toggleBtn.textContent = 'expand all';

  const list = document.getElementById('sn-list');
  list.innerHTML = '';

  data.seasons.forEach((season, sIdx) => {
    const block = document.createElement('div');
    block.className = 'sn-block collapsed';

    // season header
    const head = document.createElement('div');
    head.className = 'sn-head';

    const cbWrap = document.createElement('div');
    cbWrap.className = 'sn-cb-wrap';

    const seasonCb = document.createElement('input');
    seasonCb.type = 'checkbox';
    seasonCb.className = 'cb-custom sn-cb';
    seasonCb.checked = true;
    seasonCb.id = 's' + sIdx;

    cbWrap.appendChild(seasonCb);

    const titleWrap = document.createElement('div');
    titleWrap.className = 'sn-title-wrap';

    const label = document.createElement('span');
    label.className = 'sn-title';

    let snName = (season.title || '').trim();
    if (data.title && snName.toLowerCase().startsWith(data.title.toLowerCase())) {
      snName = snName.slice(data.title.length).replace(/^[\s:–—-]+/, '').trim();
    }
    if (!snName) {
      snName = season.season_number > 0 ? `Season ${season.season_number}` : 'Specials & Movies';
    } else if (season.season_number === 0 && !snName.toLowerCase().includes('special') && !snName.toLowerCase().includes('movie')) {
      snName = `Specials • ${snName}`;
    } else if (season.season_number > 0 && !snName.toLowerCase().startsWith('season') && !snName.toLowerCase().startsWith('s' + season.season_number)) {
      snName = `S${season.season_number} • ${snName}`;
    }
    label.textContent = snName;

    const count = document.createElement('span');
    count.className = 'sn-count';
    count.textContent = season.episodes.length + ' ep';

    titleWrap.append(label, count);

    const chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevron.setAttribute('class', 'sn-chevron');
    chevron.setAttribute('width', '10');
    chevron.setAttribute('height', '10');
    chevron.setAttribute('viewBox', '0 0 10 10');
    chevron.setAttribute('fill', 'none');
    chevron.innerHTML = '<path d="M3.5 1.5L7 5L3.5 8.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>';

    head.append(cbWrap, titleWrap, chevron);

    // episode rows
    const epList = document.createElement('div');
    epList.className = 'ep-list';

    cbWrap.addEventListener('click', (e) => {
      e.stopPropagation();
      const epCbs = [...epList.querySelectorAll('.epc')];
      const allChecked = epCbs.length > 0 && epCbs.every(c => c.checked);
      const shouldCheck = !allChecked;

      seasonCb.checked = shouldCheck;
      seasonCb.indeterminate = false;

      epCbs.forEach(cb => {
        cb.checked = shouldCheck;
        const row = cb.closest('.ep-row');
        if (row) row.classList.toggle('selected', shouldCheck);
      });
      updateSeasonState(block);
      updateTotalCount();
    });

    head.addEventListener('click', () => {
      block.classList.toggle('collapsed');
      const blocks = document.querySelectorAll('.sn-block');
      const tBtn = document.getElementById('toggle-seasons-btn');
      if (tBtn && blocks.length) {
        const anyCollapsed = [...blocks].some(b => b.classList.contains('collapsed'));
        tBtn.textContent = anyCollapsed ? 'expand all' : 'collapse all';
      }
    });

    season.episodes.forEach(ep => {
      const row = document.createElement('div');
      row.className = 'ep-row selected';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = true;
      cb.className = 'cb-custom epc';
      cb.dataset.id = ep.id;
      cb.dataset.title = ep.title || '';
      cb.dataset.epNum = ep.episode_number || '';
      cb.dataset.snNum = ep.season_number || '';
      cb.dataset.series = ep.series_title || '';
      cb.tabIndex = -1;

      const num = document.createElement('span');
      num.className = 'ep-num';
      num.textContent = 'E' + String(ep.episode_number).padStart(2, '0');

      const name = document.createElement('span');
      name.className = 'ep-name';
      name.textContent = ep.title;

      row.addEventListener('click', (e) => {
        if (e.target !== cb) {
          cb.checked = !cb.checked;
        }
        row.classList.toggle('selected', cb.checked);
        updateSeasonState(block);
        updateTotalCount();
      });

      row.append(cb, num, name);
      epList.appendChild(row);
    });

    block.append(head, epList);
    list.appendChild(block);
    updateSeasonState(block);
  });

  updateTotalCount();
  document.getElementById('tree').style.display = 'block';
}

function updateSeasonState(block) {
  const seasonCb = block.querySelector('.sn-cb');
  const epCbs = [...block.querySelectorAll('.epc')];
  const countEl = block.querySelector('.sn-count');

  const total = epCbs.length;
  const checked = epCbs.filter(c => c.checked).length;

  if (checked === 0) {
    seasonCb.checked = false;
    seasonCb.indeterminate = false;
  } else if (checked === total) {
    seasonCb.checked = true;
    seasonCb.indeterminate = false;
  } else {
    seasonCb.checked = false;
    seasonCb.indeterminate = true;
  }

  if (countEl) {
    countEl.textContent = checked === total ? `${total} ep` : `${checked}/${total} ep`;
  }
}

function updateTotalCount() {
  const allEps = document.querySelectorAll('.epc');
  const checkedEps = document.querySelectorAll('.epc:checked');
  const count = checkedEps.length;
  const total = allEps.length;

  const badge = document.getElementById('tree-selected-count');
  if (badge) badge.textContent = `${count} / ${total} selected`;
}

// select / deselect all episodes
function pickAll(val) {
  document.querySelectorAll('.epc').forEach(cb => {
    cb.checked = val;
    const row = cb.closest('.ep-row');
    if (row) row.classList.toggle('selected', val);
  });
  document.querySelectorAll('.sn-block').forEach(block => updateSeasonState(block));
  updateTotalCount();
}

// start batch download task
async function startDl() {
  const selected = [...document.querySelectorAll('.epc:checked')].map(c => ({
    id: c.dataset.id,
    title: c.dataset.title || '',
    episode_number: parseInt(c.dataset.epNum) || 0,
    season_number: parseInt(c.dataset.snNum) || 0,
    series_title: c.dataset.series || '',
  }));
  if (!selected.length) {
    toast('pick some episodes first', 'err');
    return;
  }

  const vqVal = ddVideo ? ddVideo.value : (document.getElementById('vq').value || '1080p');
  const aqVal = ddAudioQual ? ddAudioQual.value : (document.getElementById('aq').value || '192k');
  const audioVal = ddAudio ? ddAudio.value : (document.getElementById('al').value || 'ja-JP');
  const subsVal = ddSubs ? ddSubs.value : (document.getElementById('sl').value || 'en-US');

  const res = await api('/api/download', {
    items: selected,
    video_quality: vqVal,
    audio_quality: aqVal,
    audio_lang: audioVal,
    subs_lang: subsVal,
    force_download: (document.getElementById('force-download') || {}).checked || false,
  });

  if (!res.success) {
    toast(res.error || 'download failed to start', 'err');
    return;
  }

  toast(res.message || (selected.length + ' episode(s) added to queue'));
  document.getElementById('dl-panel').style.display = 'block';
  startPolling();
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    const state = await api('/api/state');
    if (state && state.download) {
      updateProgressPanel(state.download);
      const isRunning = state.download.status === 'running' || state.download.status === 'paused';
      const hasQueue = state.download.queue && state.download.queue.length > 0;
      if (!isRunning && !hasQueue) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }
  }, 800);
}

async function togglePause() {
  const btn = document.getElementById('dl-pause-btn');
  if (!btn) return;
  if (btn.dataset.action === 'resume') {
    const res = await api('/api/download/resume', {});
    if (res && res.success) {
      btn.textContent = 'pause';
      btn.dataset.action = 'pause';
      toast('download resumed');
      startPolling();
    } else {
      toast(res?.error || 'failed to resume', 'err');
    }
  } else {
    const res = await api('/api/download/pause', {});
    if (res && res.success) {
      btn.textContent = 'resume';
      btn.dataset.action = 'resume';
      toast('download paused');
    } else {
      toast(res?.error || 'failed to pause', 'err');
    }
  }
}

async function skipDl() {
  if (!confirm('Skip current episode and proceed to next in queue?')) return;
  const res = await api('/api/download/skip', {});
  if (res && res.success) {
    toast('Skipped active episode');
    startPolling();
  } else {
    toast(res?.error || 'failed to skip', 'err');
  }
}

async function cancelDl() {
  if (!confirm('Are you sure you want to cancel downloads and clear queue?')) return;
  const res = await api('/api/download/cancel', {});
  if (res && res.success) {
    toast('downloads cancelled', 'err');
  } else {
    toast(res?.error || 'failed to cancel', 'err');
  }
}

async function removeFromQueue(jobId) {
  const res = await api('/api/queue/remove', { id: jobId });
  if (res && res.success) {
    toast('Removed from queue');
    const state = await api('/api/state');
    if (state && state.download) {
      updateProgressPanel(state.download);
    }
  } else {
    toast(res?.error || 'failed to remove from queue', 'err');
  }
}

async function clearQueue() {
  if (!confirm('Clear all upcoming episodes from queue?')) return;
  const res = await api('/api/queue/clear', {});
  if (res && res.success) {
    toast('Queue cleared (' + (res.cleared || 0) + ' items)');
    const state = await api('/api/state');
    if (state && state.download) {
      updateProgressPanel(state.download);
    }
  } else {
    toast(res?.error || 'failed to clear queue', 'err');
  }
}

function updateProgressPanel(dl) {
  if (!dl || (dl.status === 'idle' && (!dl.queue || dl.queue.length === 0))) return;
  document.getElementById('dl-panel').style.display = 'block';

  const pill = document.getElementById('pill');
  const pauseBtn = document.getElementById('dl-pause-btn');
  const skipBtn = document.getElementById('dl-skip-btn');
  const cancelBtn = document.getElementById('dl-cancel-btn');

  if (pauseBtn && cancelBtn) {
    if (dl.status === 'running' || dl.status === 'paused') {
      pauseBtn.style.display = 'inline-block';
      cancelBtn.style.display = 'inline-block';
      if (skipBtn) skipBtn.style.display = 'inline-block';
      if (dl.status === 'paused') {
        pauseBtn.textContent = 'resume';
        pauseBtn.dataset.action = 'resume';
      } else {
        pauseBtn.textContent = 'pause';
        pauseBtn.dataset.action = 'pause';
      }
    } else {
      pauseBtn.style.display = 'none';
      cancelBtn.style.display = 'none';
      if (skipBtn) skipBtn.style.display = 'none';
    }
  }

  if (dl.status === 'running') {
    pill.className = 'pill pill-run';
    if (dl.track === 'muxing') {
      pill.innerHTML = '<span class="spin"></span>muxing mkv';
    } else if (dl.track) {
      pill.innerHTML = `<span class="spin"></span>downloading ${dl.track}`;
    } else {
      pill.innerHTML = '<span class="spin"></span>downloading';
    }
  } else if (dl.status === 'paused') {
    pill.className = 'pill pill-paused';
    pill.innerHTML = '\u23f8 paused';
  } else if (dl.status === 'completed') {
    pill.className = 'pill pill-ok';
    pill.innerHTML = '\u2713 done';
  } else if (dl.status === 'canceled') {
    pill.className = 'pill pill-err';
    pill.innerHTML = '\u2717 canceled';
  } else {
    pill.className = 'pill pill-err';
    pill.innerHTML = '\u2717 ' + dl.status;
  }

  const epIdx   = (dl.ep_idx  || 0) + 1;
  const epTotal = dl.ep_total || 1;
  document.getElementById('dl-ep-counter').textContent =
    dl.status === 'completed' ? `${epTotal} / ${epTotal}` : `${epIdx} / ${epTotal}`;

  document.getElementById('cur-ep').textContent = dl.episode || '';

  const overallPct = Math.min(100, dl.overall_pct || 0);
  document.getElementById('pbar-overall').style.width = overallPct + '%';
  document.getElementById('ppct-overall').textContent  = overallPct.toFixed(1) + '%';

  const trackPct = Math.min(100, dl.track_pct || 0);
  document.getElementById('pbar-track').style.width = trackPct + '%';

  const segsEl = document.getElementById('dl-segs');
  if (dl.segs_total > 0) {
    const trackSuffix = dl.track ? ` [${dl.track}]` : '';
    if (dl.complete_file) {
      const doneMb = (dl.segs_done / (1024 * 1024)).toFixed(1);
      const totalMb = (dl.segs_total / (1024 * 1024)).toFixed(1);
      segsEl.textContent = `${doneMb} / ${totalMb} MB${trackSuffix}`;
    } else {
      segsEl.textContent = `${dl.segs_done} / ${dl.segs_total} parts${trackSuffix}`;
    }
  } else if (dl.track) {
    segsEl.textContent = dl.track;
  } else {
    segsEl.textContent = '';
  }

  document.getElementById('dl-speed').textContent = dl.speed || '';

  const logBox = document.getElementById('log');
  if (dl.log && dl.log.length) {
    logBox.textContent = dl.log.join('\n');
    logBox.scrollTop = logBox.scrollHeight;
  }

  // Update Queue Panel
  const qPanel = document.getElementById('queue-panel');
  const qList = document.getElementById('queue-list');
  const qBadge = document.getElementById('queue-badge');
  if (qPanel && qList) {
    const queue = dl.queue || [];
    if (queue.length > 0) {
      qPanel.style.display = 'block';
      if (qBadge) qBadge.textContent = String(queue.length);
      qList.innerHTML = '';
      queue.forEach((item, index) => {
        const row = document.createElement('div');
        row.className = 'queue-item';

        const num = document.createElement('span');
        num.className = 'queue-num';
        num.textContent = `#${index + 1}`;

        const info = document.createElement('div');
        info.className = 'queue-info';

        const title = document.createElement('div');
        title.className = 'queue-title';
        title.textContent = item.label || item.title || item.ep_id;

        const meta = document.createElement('div');
        meta.className = 'queue-meta';

        if (item.video_quality) {
          const vqTag = document.createElement('span');
          vqTag.className = 'queue-tag';
          vqTag.textContent = item.video_quality;
          meta.appendChild(vqTag);
        }

        if (item.audio_langs && item.audio_langs.length) {
          const alTag = document.createElement('span');
          alTag.className = 'queue-tag';
          alTag.textContent = item.audio_langs.join(', ');
          meta.appendChild(alTag);
        }

        info.append(title, meta);

        const removeBtn = document.createElement('button');
        removeBtn.className = 'queue-remove-btn';
        removeBtn.innerHTML = '✕';
        removeBtn.title = 'Remove from queue';
        removeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          removeFromQueue(item.id || item.ep_id);
        });

        row.append(num, info, removeBtn);
        qList.appendChild(row);
      });
    } else {
      qPanel.style.display = 'none';
    }
  }
}
