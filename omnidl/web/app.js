'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const TOKEN = window.OMNIDL_TOKEN;

// ------------------------------------------------------------------ icons (lucide-style, MIT)
const ICONS = {
  download: '<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  clipboard: '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  play: '<path d="M7 4.5v15a.5.5 0 0 0 .77.42l11.5-7.5a.5.5 0 0 0 0-.84L7.77 4.08A.5.5 0 0 0 7 4.5Z"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M8 16H3v5"/>',
  retry: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  terminal: '<path d="m5 17 5-5-5-5"/><path d="M12 19h7"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  listx: '<path d="M11 12H3M16 6H3M16 18H3M19 10l-4 4M15 10l4 4"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/><path d="m16 16-1.9-1.9"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4"/>',
  film: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18M17 3v18M3 7.5h4M3 12h18M3 16.5h4M17 7.5h4M17 16.5h4"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="m5.7 5.7 12.6 12.6"/>',
};
const icon = name => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;
function hydrateIcons(root = document) {
  $$('i[data-icon]', root).forEach(el => { el.outerHTML = icon(el.dataset.icon); });
}
hydrateIcons();

const STATUS = { queued: '等待中', running: '進行中', paused: '已暫停', finished: '完成', error: '失敗', cancelled: '已取消' };
const STAGE = {
  analyzing: '解析中', sniffing: '網頁嗅探中', downloading: '下載中', repairing: '修復偽裝片段',
  merging: 'FFmpeg 合併中', fixing: '修正容器', converting: '轉檔中', embedding: '嵌入資訊',
  moving: '整理檔案', processing: '後處理', done: '完成',
};
const VIA = { native: 'yt-dlp', impersonate: '指紋偽裝', sniff: '網頁嗅探' };
const STATUS_ICON = { queued: 'clock', running: 'download', paused: 'pause', finished: 'check', error: 'alert', cancelled: 'ban' };

const state = { jobs: new Map(), settings: {}, analysis: null, selectedFormat: null, inputRev: 0 };

// ------------------------------------------------------------------ utils
async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || (opts.body ? 'POST' : 'GET'),
    headers: { 'X-OmniDL-Token': TOKEN, 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function size(n) {
  if (n == null) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
}
function dur(s) {
  if (s == null) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(x).padStart(2, '0')}`;
}
function host(u) { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return u; } }
function toast(msg, type = '') {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = icon(type === 'error' ? 'alert' : 'check') + `<span>${esc(msg)}</span>`;
  box.append(el);
  while (box.children.length > 3) box.firstChild.remove();
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, type === 'error' ? 5500 : 2600);
}
function busy(btn, on, label) {
  if (on) { btn.dataset.html = btn.innerHTML; btn.innerHTML = icon('refresh') + (label || ''); btn.classList.add('spin'); btn.disabled = true; }
  else { btn.innerHTML = btn.dataset.html || btn.innerHTML; btn.classList.remove('spin'); btn.disabled = false; }
}
const urlInput = $('#url-input');
function urlsFromInput() { return urlInput.value.split(/\s+/).map(s => s.trim()).filter(s => /^https?:\/\//i.test(s)); }
/** 送出後只在使用者沒有再編輯輸入框時才清空，避免吃掉新輸入的網址 */
function clearInputIfUntouched(rev) { if (state.inputRev === rev) { urlInput.value = ''; autoGrow(); } }
function autoGrow() {
  urlInput.style.height = 'auto';
  urlInput.style.height = `${Math.min(urlInput.scrollHeight, 160)}px`;
  const n = urlsFromInput().length;
  $('#url-hint').textContent = n > 1 ? `${n} 個網址` : '';
}

// ------------------------------------------------------------------ tabs / theme
$$('.seg-btn').forEach(t => t.addEventListener('click', () => {
  $$('.seg-btn').forEach(x => x.classList.toggle('active', x === t));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === `tab-${t.dataset.tab}`));
  if (t.dataset.tab === 'history') loadHistory();
  if (t.dataset.tab === 'doctor') loadDoctor();
  if (t.dataset.tab === 'settings') fillSettings();
}));
(function theme() {
  const btn = $('#btn-theme');
  const current = () => document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const paint = () => { btn.innerHTML = icon(current() === 'dark' ? 'sun' : 'moon'); };
  try { const saved = localStorage.getItem('omnidl-theme'); if (saved) document.documentElement.dataset.theme = saved; } catch { /* ignore */ }
  paint();
  btn.addEventListener('click', () => {
    const next = current() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('omnidl-theme', next); } catch { /* ignore */ }
    paint();
  });
})();

// ------------------------------------------------------------------ composer
urlInput.addEventListener('input', () => { state.inputRev++; autoGrow(); });
urlInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('#btn-add').click(); }
});
$('#btn-paste').addEventListener('click', async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    const cur = urlInput.value.trim();
    urlInput.value = cur ? `${cur}\n${text}` : text;
    state.inputRev++; autoGrow(); urlInput.focus();
  } catch { toast('無法讀取剪貼簿，請直接 Ctrl+V 貼上', 'error'); }
});
function quickOptions() {
  const q = $('#quick-quality').value;
  return q !== state.settings.quality ? { quality: q } : {};
}
$('#btn-add').addEventListener('click', async () => {
  const urls = urlsFromInput();
  if (!urls.length) { urlInput.focus(); return toast('請輸入 http:// 或 https:// 開頭的網址', 'error'); }
  const rev = state.inputRev;
  try {
    await api('/api/jobs', { body: { urls, options: quickOptions() } });
    clearInputIfUntouched(rev);
    toast(urls.length > 1 ? `已加入 ${urls.length} 個工作` : '已加入佇列');
  } catch (e) { toast(e.message, 'error'); }
});

// ------------------------------------------------------------------ analysis
$('#btn-analyze').addEventListener('click', async () => {
  const urls = urlsFromInput();
  if (!urls.length) { urlInput.focus(); return toast('請輸入有效的網址', 'error'); }
  const btn = $('#btn-analyze');
  busy(btn, true, '解析中');
  try {
    const info = await api('/api/analyze', { body: { url: urls[0] } });
    info.source_url = urls[0];
    info.rev = state.inputRev;
    showAnalysis(info);
  } catch (e) { toast(`解析失敗：${e.message}`, 'error'); }
  finally { busy(btn, false); }
});
$('#an-close').addEventListener('click', () => { $('#analysis').hidden = true; state.analysis = null; });

function showAnalysis(info) {
  state.analysis = info; state.selectedFormat = null;
  $('#analysis').hidden = false;
  $('#sniff-box').hidden = true;
  $('#an-title').textContent = info.title || info.webpage_url;
  const wrap = $('#an-thumb-wrap');
  wrap.innerHTML = icon(info.type === 'playlist' ? 'listx' : 'film');
  if (info.thumbnail) {
    const img = new Image();
    img.onload = () => { wrap.innerHTML = ''; wrap.append(img); };
    img.src = info.thumbnail;
  }
  const sub = [`<span class="tag accent">${esc(VIA[info.via] || info.via)}</span>`];
  if (info.extractor) sub.push(`<span>${esc(info.extractor)}</span>`);
  if (info.uploader) sub.push(`<span class="sep"></span><span>${esc(info.uploader)}</span>`);
  if (info.duration) sub.push(`<span class="sep"></span><span>${dur(info.duration)}</span>`);
  sub.push(`<span class="sep"></span><span>${info.type === 'playlist' ? `${info.count} 部影片` : `${info.formats.length} 種格式`}</span>`);
  $('#an-sub').innerHTML = sub.join('');

  const body = $('#an-body');
  if (info.type === 'playlist') {
    body.innerHTML = `<div class="entries-head"><input type="checkbox" class="cb" id="ent-all" checked><label for="ent-all">全選</label></div>
      <div class="entries">${info.entries.map(e => `<label><input type="checkbox" class="cb ent" value="${e.index}" checked>
      <span class="n">${e.index}</span><span class="t">${esc(e.title || e.url)}</span><span class="muted xs">${dur(e.duration)}</span></label>`).join('')}</div>`;
    const sync = () => { $('#an-pick').textContent = `已選 ${$$('.ent').filter(c => c.checked).length} / ${info.entries.length}`; };
    $('#ent-all').addEventListener('change', ev => { $$('.ent').forEach(c => { c.checked = ev.target.checked; }); sync(); });
    $$('.ent').forEach(c => c.addEventListener('change', sync));
    sync();
    return;
  }
  const KIND = { av: '影音', video: '視訊', audio: '音訊' };
  const rows = info.formats.map((f, i) => `<tr data-i="${i}">
      <td><span class="radio"></span></td>
      <td class="strong">${esc(f.kind === 'audio' ? '音訊' : (f.height ? `${f.height}p` : f.resolution || ''))}${f.fps && f.fps > 30 ? ` <span class="muted">${Math.round(f.fps)}fps</span>` : ''}</td>
      <td>${KIND[f.kind]}</td><td>${esc(f.ext)}</td>
      <td>${esc([(f.vcodec || '').split('.')[0], (f.acodec || '').split('.')[0]].filter(Boolean).join(' / '))}</td>
      <td class="r">${f.tbr ? Math.round(f.tbr) + 'k' : ''}</td><td class="r">${size(f.filesize)}</td>
      <td class="muted">${esc(f.format_id)}</td></tr>`).join('');
  body.innerHTML = `<div class="ftable"><table><thead><tr><th></th><th>畫質</th><th>類型</th><th>格式</th><th>編碼</th>
    <th class="r">位元率</th><th class="r">大小</th><th>ID</th></tr></thead>
    <tbody><tr data-i="-1" class="sel"><td><span class="radio"></span></td><td class="strong" colspan="7">自動選擇
    <span class="muted">— 依畫質設定挑最佳影音並合併</span></td></tr>${rows}</tbody></table></div>`;
  $('#an-pick').textContent = '';
  $$('.ftable tbody tr', body).forEach(tr => tr.addEventListener('click', () => {
    $$('.ftable tr.sel', body).forEach(x => x.classList.remove('sel'));
    tr.classList.add('sel');
    const i = Number(tr.dataset.i);
    state.selectedFormat = i < 0 ? null : info.formats[i];
    const f = state.selectedFormat;
    $('#an-pick').textContent = f ? (f.kind === 'video' ? `${f.format_id} + 最佳音訊` : f.format_id) : '';
  }));
}

$('#an-download').addEventListener('click', async () => {
  const info = state.analysis;
  if (!info) return;
  const options = quickOptions();
  if (info.type === 'playlist') {
    const picked = $$('.ent').filter(c => c.checked).map(c => c.value);
    if (!picked.length) return toast('請至少選擇一部影片', 'error');
    if (picked.length !== info.entries.length) options.playlist_items = picked.join(',');
  } else if (state.selectedFormat) {
    const f = state.selectedFormat;
    options.format_id = f.kind === 'video' ? `${f.format_id}+ba/${f.format_id}` : f.format_id;
    if (f.kind === 'audio') options.quality = 'audio';
  }
  if (info.via === 'sniff' && info.sniff_url) options.sniff_url = info.sniff_url;
  try {
    await api('/api/jobs', { body: { url: info.source_url, options, title: info.title } });
    toast('已加入佇列');
    $('#analysis').hidden = true;
    clearInputIfUntouched(info.rev);
  } catch (e) { toast(e.message, 'error'); }
});

$('#an-sniff').addEventListener('click', async () => {
  const info = state.analysis;
  if (!info) return;
  const btn = $('#an-sniff');
  busy(btn, true, '嗅探中');
  try {
    const res = await api('/api/sniff', { body: { url: info.source_url } });
    const box = $('#sniff-box');
    box.hidden = false;
    if (!res.candidates.length) { box.innerHTML = '<div class="cand"><span class="muted">網頁原始碼中找不到串流網址</span></div>'; return; }
    box.innerHTML = res.candidates.map((c, i) => `<div class="cand">
      <span class="tag ${c.is_master ? 'accent' : ''}">${esc(c.ext)}</span><code title="${esc(c.url)}">${esc(c.url)}</code>
      <span class="note">${esc(c.note || '')}</span><button class="tbtn" data-c="${i}">${icon('download')}使用</button></div>`).join('');
    $$('button[data-c]', box).forEach(b => b.addEventListener('click', async () => {
      const c = res.candidates[Number(b.dataset.c)];
      try {
        await api('/api/jobs', { body: { url: info.source_url, title: res.title || info.title, options: { ...quickOptions(), sniff_url: c.url } } });
        toast('已加入佇列');
      } catch (e) { toast(e.message, 'error'); }
    }));
  } catch (e) { toast(`嗅探失敗：${e.message}`, 'error'); }
  finally { busy(btn, false); }
});

// ------------------------------------------------------------------ queue
function upsertJob(j) {
  state.jobs.set(j.id, j);
  renderJob(j);
  updateQueueMeta();
}
function removeJob(id) {
  state.jobs.delete(id);
  const el = document.getElementById(`job-${id}`);
  if (el) el.remove();
  updateQueueMeta();
}
function updateQueueMeta() {
  const all = [...state.jobs.values()];
  const active = all.filter(j => j.status === 'running').length;
  const pending = all.filter(j => ['running', 'queued'].includes(j.status)).length;
  $('#queue-count').textContent = all.length ? `${active} 進行中 · 共 ${all.length}` : '';
  $('#queue-empty').hidden = all.length > 0;
  const badge = $('#tab-count');
  badge.hidden = !pending; badge.textContent = pending;
  document.title = active ? `(${active}) OmniDL` : 'OmniDL';
}
function jobButtons(j) {
  const b = [];
  if (j.status === 'running' || j.status === 'queued') b.push(['pause', 'pause', '暫停']);
  if (j.status === 'paused') b.push(['resume', 'play', '繼續（續傳）']);
  if (j.status === 'error' || j.status === 'cancelled') b.push(['retry', 'retry', '重試']);
  if (['running', 'queued', 'paused', 'error'].includes(j.status)) b.push(['cancel', 'x', '取消並刪除暫存檔']);
  b.push(['log', 'terminal', '詳細紀錄']);
  if (j.status !== 'running') b.push(['remove', 'trash', '從清單移除']);
  return b.map(([a, ic, tip]) => `<button class="ibtn sm" data-a="${a}" data-tip="${tip}">${icon(ic)}</button>`).join('');
}
function renderJob(j) {
  let el = document.getElementById(`job-${j.id}`);
  if (!el) {
    el = document.createElement('div');
    el.id = `job-${j.id}`;
    el.innerHTML = `<div class="ico"></div><div class="body"><div class="l1"><span class="title"></span><span class="pct"></span></div>
      <div class="bar"><div></div></div><div class="meta"></div><div class="err" hidden></div><div class="files"></div></div>
      <div class="actions"></div>`;
    el.addEventListener('click', e => {
      const btn = e.target.closest('button[data-a]');
      if (btn) return jobAction(j.id, btn.dataset.a);
      const f = e.target.closest('[data-file]');
      if (f) openPath(f.dataset.file);
    });
    $('#queue').prepend(el);
  }
  el.className = `row job ${j.status}`;
  const ico = $('.ico', el);
  const icName = STATUS_ICON[j.status] || 'clock';
  if (ico.dataset.ic !== icName) { ico.innerHTML = icon(icName); ico.dataset.ic = icName; }
  const title = $('.title', el);
  title.textContent = j.title || j.url;
  title.title = j.url;
  const actions = $('.actions', el);
  const html = jobButtons(j);
  if (actions.dataset.html !== html) { actions.innerHTML = html; actions.dataset.html = html; }

  const p = j.progress || {};
  const running = j.status === 'running';
  const pct = j.status === 'finished' ? 100 : p.percent;
  const indeterminate = running && (pct == null || ['analyzing', 'sniffing'].includes(j.stage));
  $('.bar', el).classList.toggle('indeterminate', indeterminate);
  $('.bar > div', el).style.width = `${pct || 0}%`;
  $('.pct', el).textContent = running && pct != null && !indeterminate ? `${pct.toFixed(1)}%` : '';

  const m = [];
  if (running) m.push(`<span class="stage">${esc(STAGE[j.stage] || '準備中')}</span>`);
  else m.push(`<span class="st">${STATUS[j.status] || j.status}</span>`);
  if (running && p.total) m.push(`<span>${size(p.downloaded)} / ${size(p.total)}</span>`);
  if (running && p.speed) m.push(`<span>${size(p.speed)}/s</span>`);
  if (running && p.eta) m.push(`<span>剩 ${dur(p.eta)}</span>`);
  if (running && p.fragment_count) m.push(`<span>片段 ${p.fragment_index || 0}/${p.fragment_count}</span>`);
  if (running && p.playlist_index && p.n_entries) m.push(`<span>第 ${p.playlist_index}/${p.n_entries} 部</span>`);
  const q = j.options && j.options.quality;
  if (q) m.push(`<span>${q === 'audio' ? '僅音訊' : q === 'best' ? '最高畫質' : `≤${q}p`}</span>`);
  if (j.via && j.via !== 'native') m.push(`<span>${VIA[j.via]}</span>`);
  m.push(`<span>${esc(host(j.url))}</span>`);
  $('.meta', el).innerHTML = m.join('');
  const err = $('.err', el);
  err.hidden = !j.error; err.textContent = j.error || '';
  const files = j.files || [];
  const fkey = files.join('|');
  const fbox = $('.files', el);
  if (fbox.dataset.k !== fkey) {
    fbox.dataset.k = fkey;
    fbox.innerHTML = files.map(f => `<a class="file-link" data-file="${esc(f)}" title="在資料夾中顯示">${icon(/\.(mp3|m4a|opus|flac|wav|aac)$/i.test(f) ? 'music' : 'file')}<span>${esc(f.split(/[\\/]/).pop())}</span></a>`).join('');
  }
}
async function jobAction(id, action) {
  if (action === 'log') return showLog(id);
  try {
    await api(`/api/jobs/${id}/${action}`, { body: {} });
    if (action === 'remove') removeJob(id);
  } catch (e) { toast(e.message, 'error'); }
}
$$('[data-bulk]').forEach(b => b.addEventListener('click', async () => {
  try {
    await api(`/api/bulk/${b.dataset.bulk}`, { body: {} });
    if (b.dataset.bulk === 'clear_done') refreshState();
  } catch (e) { toast(e.message, 'error'); }
}));
async function showLog(id) {
  try {
    const j = await api(`/api/jobs/${id}/log`);
    $('#log-title').textContent = j.title || j.url;
    $('#log-body').innerHTML = (j.log || []).map(l => `<span class="${l.level}">${esc(l.msg)}</span>`).join('\n') || '（沒有紀錄）';
    $('#log-dialog').showModal();
  } catch (e) { toast(e.message, 'error'); }
}
$$('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
$('#log-dialog').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.close(); });
async function openPath(path) {
  try { await api('/api/open', { body: path ? { path } : {} }); } catch (e) { toast(e.message, 'error'); }
}
$('#btn-open-folder').addEventListener('click', () => openPath(null));
$('#btn-quit').addEventListener('click', async () => {
  if (!confirm('確定要結束 OmniDL？進行中的下載會暫停，下次開啟可續傳。')) return;
  try { await api('/api/shutdown', { body: {} }); } catch { /* ignore */ }
  document.body.innerHTML = `<div class="empty" style="padding-top:28vh">${icon('power')}<div>OmniDL 已結束</div><div class="muted xs">可以關閉此分頁</div></div>`;
});

// ------------------------------------------------------------------ history
async function loadHistory() {
  state.history = await api('/api/history').catch(() => []);
  renderHistory();
}
function renderHistory() {
  const q = ($('#history-search').value || '').toLowerCase();
  const items = (state.history || []).filter(h => !q || `${h.title} ${h.url}`.toLowerCase().includes(q));
  const fmt = new Intl.DateTimeFormat(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  $('#history').innerHTML = items.length ? items.map((h, i) => `<div class="row hist">
      <div class="body"><div class="title" title="${esc(h.url)}">${esc(h.title || h.url)}</div>
      <div class="sub">${fmt.format(new Date((h.finished || 0) * 1000))} · ${esc(host(h.url))}${h.via && h.via !== 'native' ? ` · ${esc(VIA[h.via])}` : ''}</div></div>
      <div class="actions">
      ${(h.files || []).length ? `<button class="ibtn sm" data-h-open="${i}" data-tip="顯示檔案">${icon('folder')}</button>` : ''}
      <button class="ibtn sm" data-h-again="${i}" data-tip="再下載一次">${icon('retry')}</button></div></div>`).join('')
    : `<div class="list-empty">${q ? '沒有符合的紀錄' : '還沒有下載紀錄'}</div>`;
  $$('[data-h-open]').forEach(b => b.addEventListener('click', () => openPath(items[Number(b.dataset.hOpen)].files[0])));
  $$('[data-h-again]').forEach(b => b.addEventListener('click', async () => {
    try { await api('/api/jobs', { body: { urls: [items[Number(b.dataset.hAgain)].url] } }); toast('已加入佇列'); }
    catch (e) { toast(e.message, 'error'); }
  }));
}
$('#history-search').addEventListener('input', renderHistory);
$('#btn-clear-history').addEventListener('click', async () => {
  if (!confirm('確定清除所有歷史紀錄？（不會刪除已下載的檔案）')) return;
  await api('/api/history', { method: 'DELETE' });
  loadHistory();
});

// ------------------------------------------------------------------ settings
function fillSettings() {
  const form = $('#settings-form');
  for (const [k, v] of Object.entries(state.settings)) {
    const el = form.elements[k];
    if (!el || el === document.activeElement) continue;
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v ?? '';
  }
}
let saveTimer = null;
$('#settings-form').addEventListener('input', () => {
  clearTimeout(saveTimer);
  $('#settings-status').textContent = '';
  saveTimer = setTimeout(saveSettings, 450);
});
$('#settings-form').addEventListener('submit', e => e.preventDefault());
async function saveSettings() {
  const data = {};
  for (const el of $('#settings-form').elements) {
    if (!el.name) continue;
    data[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value;
  }
  try {
    state.settings = await api('/api/settings', { method: 'PUT', body: data });
    $('#settings-status').textContent = '· 已儲存';
    $('#quick-quality').value = state.settings.quality;
    fillSettings();
  } catch (e) { $('#settings-status').textContent = `· 儲存失敗：${e.message}`; }
}

// ------------------------------------------------------------------ doctor
const ROLES = {
  yt_dlp: ['yt-dlp', '主控端：解析網址、找出 Master Playlist、選最高畫質'],
  ffmpeg: ['FFmpeg', '合併片段、無損封裝成 MP4（必要）'],
  ffprobe: ['FFprobe', '偵測影音格式（必要）'],
  aria2c: ['aria2c', '多連線極速下載（選用）'],
  js: ['Deno / Node', 'YouTube 解出完整畫質所需（建議）'],
  curl_cffi: ['curl_cffi', '瀏覽器指紋偽裝，突破 Cloudflare（選用）'],
};
async function loadDoctor() {
  const d = await api('/api/doctor').catch(e => { toast(e.message, 'error'); return null; });
  if (!d) return;
  const row = (key, ok, ver, path, optional) => `<div class="row tool">
    <span class="dot ${ok ? 'ok' : optional ? 'opt' : ''}"></span><span class="name">${ROLES[key][0]}</span>
    <div class="body"><div class="role">${ROLES[key][1]}</div>${path ? `<div class="path" title="${esc(path)}">${esc(path)}</div>` : ''}</div>
    <span class="ver">${ok ? esc(ver || '已安裝') : '未安裝'}</span></div>`;
  $('#doctor').innerHTML = [
    row('yt_dlp', true, d.yt_dlp.version, null),
    row('ffmpeg', !!d.ffmpeg.path, d.ffmpeg.version, d.ffmpeg.path),
    row('ffprobe', !!d.ffprobe.path, d.ffprobe.version, d.ffprobe.path),
    row('aria2c', !!d.aria2c.path, d.aria2c.version, d.aria2c.path, true),
    row('js', !!d.js_runtime.name, d.js_runtime.name ? `${d.js_runtime.name} ${d.js_runtime.version || ''}` : '', d.js_runtime.path, true),
    row('curl_cffi', d.curl_cffi, '可用', null, true),
  ].join('');
  $('#btn-install-tools').hidden = !d.can_auto_install;
  const missing = !d.ffmpeg.path || !d.aria2c.path || !d.js_runtime.name;
  $('#tool-message').textContent = d.task.message || (missing ? d.install_hint : '所有工具都已就緒。');
  $('#doctor-dot').hidden = !!d.ffmpeg.path;
}
$('#btn-refresh-doctor').addEventListener('click', loadDoctor);
$('#btn-install-tools').addEventListener('click', () => api('/api/tools/install', { body: {} }).catch(e => toast(e.message, 'error')));
$('#btn-update-ytdlp').addEventListener('click', () => api('/api/tools/update-ytdlp', { body: {} }).catch(e => toast(e.message, 'error')));

// ------------------------------------------------------------------ live events
async function refreshState() {
  const s = await api('/api/state');
  state.settings = s.settings;
  $('#quick-quality').value = s.settings.quality;
  const ids = new Set(s.jobs.map(j => j.id));
  [...state.jobs.keys()].filter(id => !ids.has(id)).forEach(removeJob);
  s.jobs.forEach(upsertJob);
  updateQueueMeta();
}
function connect() {
  const es = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  es.onmessage = ev => {
    const { type, data } = JSON.parse(ev.data);
    if (type === 'job') {
      const prev = state.jobs.get(data.id);
      upsertJob(data);
      if (prev && prev.status !== data.status) {
        if (data.status === 'finished') toast(`下載完成：${data.title || data.url}`);
        if (data.status === 'error') toast(`下載失敗：${data.title || data.url}`, 'error');
      }
    } else if (type === 'removed') removeJob(data);
    else if (type === 'history' && $('#tab-history').classList.contains('active')) loadHistory();
    else if (type === 'tool') {
      $('#tool-message').textContent = data.message;
      $('#btn-install-tools').disabled = $('#btn-update-ytdlp').disabled = data.busy;
      if (!data.busy) { loadDoctor(); toast(data.message, /失敗/.test(data.message) ? 'error' : ''); }
    }
  };
  es.onopen = () => refreshState().catch(() => {});
}

refreshState().then(() => api('/api/doctor')).then(d => {
  if (!d || d.ffmpeg.path) return;
  $('#doctor-dot').hidden = false;
  if (d.can_auto_install && !d.task.busy) {
    // Windows 首次啟動：自動下載 FFmpeg / aria2c / Deno
    toast('首次啟動：正在自動下載 FFmpeg 等工具，完成前請稍候…');
    api('/api/tools/install', { body: {} }).catch(e => toast(e.message, 'error'));
  } else {
    toast('找不到 FFmpeg，影片可能無法合併，請到「環境」頁查看安裝方式', 'error');
  }
}).catch(e => toast(e.message, 'error'));
connect();
autoGrow();
