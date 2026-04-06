// ── Telegram WebApp ────────────────────────────────────────────────────────────
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor('#0d1114'); tg.setBackgroundColor('#0d1114'); }

// Telegram BackButton
const TgBack = tg?.BackButton;
if (TgBack) TgBack.onClick(() => goBack());

// Set API base — same origin when served by FastAPI.
// For local dev without a server, uncomment: const API = 'http://localhost:8000';
const API = '';

const CHAT_ID = tg?.initDataUnsafe?.user?.id || null;

// ── View stack ─────────────────────────────────────────────────────────────────
// Stack always starts at the root of current tab
const viewStack = [];
let activeTab = 'home'; // 'home' | 'history'

function stackRoot(viewId) {
  viewStack.length = 0;
  viewStack.push(viewId);
  syncBackBtn();
}

function stackPush(viewId) {
  if (viewStack[viewStack.length - 1] !== viewId) viewStack.push(viewId);
  syncBackBtn();
}

function stackPop() {
  if (viewStack.length > 1) viewStack.pop();
  syncBackBtn();
  return viewStack[viewStack.length - 1];
}

function syncBackBtn() {
  if (!TgBack) return;
  viewStack.length > 1 ? TgBack.show() : TgBack.hide();
}

// ── Show a view ────────────────────────────────────────────────────────────────
function showView(id, push = true) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(`view-${id}`).classList.add('active');
  document.getElementById('dl-bar').style.display = id === 'formats' ? 'block' : 'none';
  document.getElementById('main').scrollTop = 0;
  if (push) stackPush(id);
  syncBackBtn();
}

// ── Back ───────────────────────────────────────────────────────────────────────
function goBack() {
  if (viewStack.length <= 1) return;
  const prev = stackPop();
  showView(prev, false);
  // If going back to home, cancel any poll
  if (prev === 'home') {
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  }
}

function goHome() {
  stackRoot('home');
  showView('home', false);
  setActiveNavTab('home');
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

// ── Bottom nav ─────────────────────────────────────────────────────────────────
function setActiveNavTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.getElementById(`nav-${tab}`).classList.add('active');
}

function navTo(tab) {
  setActiveNavTab(tab);
  if (tab === 'home') {
    stackRoot('home');
    showView('home', false);
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  } else if (tab === 'history') {
    stackRoot('history');
    showView('history', false);
    renderHistory();
  }
}

// ── State ──────────────────────────────────────────────────────────────────────
let mode = 'single';
let selectedFmt = null;
let currentUrl = '';
let currentTitle = '';
let currentThumb = '';
let pollInterval = null;

// ── History ────────────────────────────────────────────────────────────────────
const HIST_KEY = `mf_hist_${CHAT_ID || 'anon'}`;

function loadHist() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); }
  catch { return []; }
}

function saveHist(arr) {
  try { localStorage.setItem(HIST_KEY, JSON.stringify(arr.slice(0, 50))); }
  catch(e) { console.warn('localStorage unavailable:', e); }
}

function addHist(entry) {
  const h = loadHist().filter(x => !(x.url === entry.url && x.fmt === entry.fmt));
  h.unshift(entry);
  saveHist(h);
  refreshBadge();
}

function refreshBadge() {
  const n = loadHist().length;
  const b = document.getElementById('hist-badge');
  if (n > 0) { b.textContent = n > 99 ? '99+' : n; b.classList.add('show'); }
  else { b.classList.remove('show'); }
}

function clearHistory() {
  if (!confirm('Clear all download history?')) return;
  localStorage.removeItem(HIST_KEY);
  refreshBadge();
  renderHistory();
  showToast('History cleared');
}

function renderHistory() {
  const hist = loadHist();
  const container = document.getElementById('hist-container');
  const clearBtn = document.getElementById('clear-btn');

  if (!hist.length) {
    clearBtn.style.display = 'none';
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">📭</div>
        <div class="empty-title">No downloads yet</div>
        <div class="empty-sub">Your history appears here after each download</div>
      </div>`;
    return;
  }

  clearBtn.style.display = 'block';
  const list = document.createElement('div');
  list.className = 'history-list';

  hist.forEach(e => {
    const item = document.createElement('div');
    item.className = `history-item ${e.type || 'video'}`;

    let dateStr = '';
    try {
      const d = new Date(e.at);
      dateStr = d.toLocaleDateString(undefined, {month:'short',day:'numeric'}) + ' ' +
                d.toLocaleTimeString(undefined, {hour:'2-digit',minute:'2-digit'});
    } catch {}

    // Encode entry data for redownload button (avoid nested quotes)
    const safeEntry = btoa(JSON.stringify(e));

    item.innerHTML = `
      ${e.thumb
        ? `<img class="hist-thumb" src="${escHtml(e.thumb)}" alt="" onerror="this.outerHTML='<div class=hist-thumb-ph>${e.type==='audio'?'🎵':'🎬'}</div>'">`
        : `<div class="hist-thumb-ph">${e.type === 'audio' ? '🎵' : '🎬'}</div>`}
      <div class="hist-info">
        <div class="hist-title">${escHtml(e.title)}</div>
        <div class="hist-meta">
          <span class="hist-badge ${e.type || 'video'}">${(e.type||'VIDEO').toUpperCase()}</span>
          <span>${escHtml(e.fmt || '')}</span>
          ${dateStr ? `<span>${dateStr}</span>` : ''}
        </div>
      </div>
      <button class="hist-redl" title="Re-download" onclick="redownload('${safeEntry}')">↺</button>`;

    list.appendChild(item);
  });

  container.innerHTML = '';
  container.appendChild(list);
}

function redownload(b64) {
  try {
    const e = JSON.parse(atob(b64));
    document.getElementById('url-input').value = e.url;
    currentUrl = e.url;
    navTo('home');
    showToast('URL loaded — tap Fetch to pick format');
  } catch(err) { showToast('Could not load URL'); }
}

// ── Sites strip ────────────────────────────────────────────────────────────────
const SITES = ['YouTube','Instagram','TikTok','Twitter/X','Reddit','Facebook','Vimeo',
  'Twitch','SoundCloud','Bilibili','Dailymotion','Pinterest','LinkedIn','Snapchat',
  'Tumblr','Odysee','PeerTube','Rumble','BandCamp','Mixcloud','NicoNico','VK','OK.ru','Weibo'];
const si = document.getElementById('sites-inner');
[...SITES,...SITES].forEach(s => { const d=document.createElement('div'); d.className='site-chip'; d.textContent=s; si.appendChild(d); });

// ── Input helpers ──────────────────────────────────────────────────────────────
async function pasteUrl() {
  try { document.getElementById('url-input').value = await navigator.clipboard.readText(); showToast('Pasted ✓'); }
  catch { showToast('Tap input to paste manually'); }
}
function clearUrl() { document.getElementById('url-input').value = ''; document.getElementById('url-input').focus(); }
function setMode(m, btn) {
  mode = m;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('fetch-btn').textContent = m === 'playlist' ? 'Fetch Playlist' : 'Fetch Media Info';
}

// ── Fetch ──────────────────────────────────────────────────────────────────────
async function fetchMedia() {
  const url = document.getElementById('url-input').value.trim();
  if (!url) { showToast('Enter a URL first'); return; }
  const btn = document.getElementById('fetch-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  currentUrl = url;
  try {
    if (mode === 'playlist') await fetchPlaylist(url);
    else await fetchSingle(url);
  } catch(e) {
    showToast('❌ ' + (e.message || 'Failed to fetch'));
  } finally {
    btn.disabled = false;
    btn.textContent = mode === 'playlist' ? 'Fetch Playlist' : 'Fetch Media Info';
  }
}

async function fetchSingle(url) {
  const res = await fetch(`${API}/api/info`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url}),
  });
  if (!res.ok) { const e = await res.json().catch(()=>{}); throw new Error(e?.detail||'Failed'); }
  const info = await res.json();
  currentTitle = info.title; currentThumb = info.thumbnail || '';
  renderFormats(info);
  showView('formats');
}

async function fetchPlaylist(url) {
  const res = await fetch(`${API}/api/playlist`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url}),
  });
  if (!res.ok) { const e = await res.json().catch(()=>{}); throw new Error(e?.detail||'Failed'); }
  const data = await res.json();
  renderPlaylist(data);
  showView('playlist');
}

// ── Render formats ─────────────────────────────────────────────────────────────
function renderFormats(info) {
  selectedFmt = null;
  document.getElementById('dl-btn').disabled = true;
  document.getElementById('dl-btn').textContent = 'Select a format to download';

  const thumb = info.thumbnail
    ? `<div class="thumb-wrap"><img src="${info.thumbnail}" alt="" onerror="this.parentElement.style.display='none'"><div class="thumb-overlay"></div>${info.duration_str?`<div class="thumb-badge">${info.duration_str}</div>`:''}</div>` : '';

  document.getElementById('media-card').innerHTML = `${thumb}
    <div class="media-meta">
      <div class="media-title">${escHtml(info.title)}</div>
      <div class="meta-row">
        ${info.uploader?`<span>👤 ${escHtml(info.uploader)}</span>`:''}
        ${info.view_count?`<span>👁 ${(info.view_count/1000).toFixed(0)}K</span>`:''}
        ${info.extractor?`<span>🌐 ${escHtml(info.extractor)}</span>`:''}
      </div>
    </div>`;

  const pg = document.getElementById('presets-grid');
  pg.innerHTML = '';
  [
    {label:'⚡ Best Quality', sub:'Auto best video+audio', dl_format:'bestvideo+bestaudio/best', media_type:'video', icon:'⚡', badge:'best'},
    {label:'🎵 Audio Only (MP3)', sub:'Best audio → MP3 192kbps', dl_format:'bestaudio/best', media_type:'audio', icon:'🎵', badge:'audio'},
  ].forEach(p => pg.appendChild(mkFmtBtn(p)));

  const vg = document.getElementById('video-grid');
  vg.innerHTML = '';
  if (!info.formats.video.length) vg.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:2px 0">No separate video streams</div>';
  else info.formats.video.forEach(f => vg.appendChild(mkFmtBtn({
    label:`${f.label} + Best Audio`, sub:`${f.ext.toUpperCase()} · ${f.filesize_str}`,
    dl_format:f.dl_format, media_type:'video', icon:'🎬', badge:'video',
  })));

  const ag = document.getElementById('audio-grid');
  ag.innerHTML = '';
  if (!info.formats.audio.length) ag.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:2px 0">No separate audio streams</div>';
  else info.formats.audio.forEach(f => ag.appendChild(mkFmtBtn({
    label:f.label, sub:f.filesize_str, dl_format:f.dl_format, media_type:'audio', icon:'🎵', badge:'audio',
  })));
}

function mkFmtBtn({label, sub, dl_format, media_type, icon, badge}) {
  const btn = document.createElement('button');
  btn.className = 'format-btn';
  btn.innerHTML = `<div class="fmt-icon">${icon}</div>
    <div class="fmt-info"><div class="fmt-label">${escHtml(label)}</div>${sub?`<div class="fmt-sub">${escHtml(sub)}</div>`:''}</div>
    <span class="fmt-badge ${badge}">${badge.toUpperCase()}</span>
    <div class="check-mark">✓</div>`;
  btn.addEventListener('click', () => {
    document.querySelectorAll('.format-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    selectedFmt = {dl_format, media_type, label};
    document.getElementById('dl-btn').disabled = false;
    document.getElementById('dl-btn').textContent = `Download · ${label}`;
  });
  return btn;
}

// ── Render playlist ────────────────────────────────────────────────────────────
function renderPlaylist(data) {
  document.getElementById('pl-title').textContent = data.title;
  document.getElementById('pl-count').textContent = `${data.count} videos`;
  window._pl = data;
  const list = document.getElementById('entry-list');
  list.innerHTML = '';
  data.entries.forEach(e => {
    const item = document.createElement('div');
    item.className = 'entry-item';
    const ds = e.duration ? `${Math.floor(e.duration/60)}:${String(Math.floor(e.duration%60)).padStart(2,'0')}` : '';
    item.innerHTML = `<span class="entry-num">${e.index+1}</span>
      ${e.thumbnail?`<img class="entry-thumb" src="${e.thumbnail}" alt="" onerror="this.remove()">` : ''}
      <span class="entry-title">${escHtml(e.title)}</span>
      ${ds?`<span class="entry-dur">${ds}</span>`:''}`;
    item.addEventListener('click', () => loadEntryFormats(e));
    list.appendChild(item);
  });
}

async function loadEntryFormats(e) {
  document.getElementById('entry-list').innerHTML = '<div class="loading-row"><div class="spinner"></div> Loading formats…</div>';
  try {
    const res = await fetch(`${API}/api/info`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url:e.url})});
    if (!res.ok) throw new Error('Failed');
    const info = await res.json();
    currentUrl = e.url; currentTitle = info.title; currentThumb = info.thumbnail || '';
    renderFormats(info);
    showView('formats');
  } catch {
    showToast('❌ Failed to fetch entry formats');
    renderPlaylist(window._pl);
  }
}

async function downloadAll(mediaType) {
  if (!CHAT_ID) { showToast('⚠ Could not detect your Telegram ID'); return; }
  const data = window._pl; if (!data) return;
  showToast(`Starting ${data.count} downloads…`, 3000);
  for (const e of data.entries) {
    if (!e.url) continue;
    await triggerDownload(e.url, mediaType==='audio'?'bestaudio/best':'bestvideo+bestaudio/best', mediaType, e.title, e.thumbnail||'', 'Best');
    await new Promise(r => setTimeout(r, 1500));
  }
}

// ── Download ───────────────────────────────────────────────────────────────────
async function startDownload() {
  if (!selectedFmt) return;
  if (!CHAT_ID) { showToast('⚠ Open via your Telegram bot to download'); return; }
  await triggerDownload(currentUrl, selectedFmt.dl_format, selectedFmt.media_type, currentTitle, currentThumb, selectedFmt.label);
}

async function triggerDownload(url, fmt, type, title, thumb, fmtLabel) {
  showView('progress');
  document.getElementById('prog-title').textContent = title || url;
  document.getElementById('done-section').style.display = 'none';
  document.getElementById('error-section').style.display = 'none';
  document.getElementById('prog-size').textContent = '';
  const fill = document.getElementById('prog-fill');
  fill.style.width = '0%'; fill.classList.add('shimmer');
  updateProgUI({status:'queued', progress:0, speed:'—', eta:'—'});

  try {
    const res = await fetch(`${API}/api/download`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, format_id:fmt, media_type:type, chat_id:CHAT_ID, title:title||''}),
    });
    if (!res.ok) { const e = await res.json().catch(()=>{}); throw new Error(e?.detail||'Failed'); }
    const {job_id} = await res.json();
    pollJob(job_id, {url, title, thumb, type, fmtLabel});
  } catch(e) {
    fill.classList.remove('shimmer');
    document.getElementById('error-msg').textContent = e.message;
    document.getElementById('error-section').style.display = 'flex';
  }
}

function pollJob(jobId, meta) {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/job/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();
      updateProgUI(job);
      if (job.status === 'done') {
        clearInterval(pollInterval); pollInterval = null;
        document.getElementById('prog-fill').classList.remove('shimmer');
        document.getElementById('done-section').style.display = 'flex';
        addHist({url:meta.url, title:meta.title||meta.url, thumb:meta.thumb||'', type:meta.type, fmt:meta.fmtLabel||'', at:new Date().toISOString()});
      } else if (job.status === 'error') {
        clearInterval(pollInterval); pollInterval = null;
        document.getElementById('prog-fill').classList.remove('shimmer');
        document.getElementById('error-msg').textContent = job.error || 'Unknown error';
        document.getElementById('error-section').style.display = 'flex';
      }
    } catch {}
  }, 1000);
}

function updateProgUI(job) {
  const pct = job.progress || 0;
  document.getElementById('prog-fill').style.width = pct + '%';
  document.getElementById('stat-pct').textContent = pct + '%';
  document.getElementById('stat-speed').textContent = job.speed || '—';
  document.getElementById('stat-eta').textContent = job.eta || '—';
  if (job.downloaded && job.total) document.getElementById('prog-size').textContent = `${job.downloaded} / ${job.total}`;
  document.getElementById('status-dot').className = 'status-dot ' + (job.status || 'queued');
  const labels = {queued:'Queued…',downloading:'Downloading…',uploading:'Uploading to Telegram…',done:'Done!',error:'Error'};
  document.getElementById('status-label').textContent = labels[job.status] || job.status;
}

// ── Toast ──────────────────────────────────────────────────────────────────────
function showToast(msg, ms=2200) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), ms);
}

// ── escHtml ────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}

// ── Init ───────────────────────────────────────────────────────────────────────
refreshBadge();
stackRoot('home');
syncBackBtn();
