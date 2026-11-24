// dataeditor.js -- DISC > Data Editor.
//
// The RDR Data Editor app inside the Studio, laid out as the app: its pages (Working disc, Manual .dat/.zip
// extracting/building, Settings), the saved Mods bar, the folder tree (Master + a folder for each .dat) and the
// file list with the editor under it, and the Workbench below that holds files freely.
//
// You keep changed game files in its own mods folder; Build rebuilds the archives they belong in -- laid out as the
// game's own -- and injects them into the working disc. It keeps apart from the Studio's own edits: each archive is
// saved as it was before its first injection (Studio edits included) and Restore defaults, here and only here, puts
// that back. Server side: rdrstudio/dataeditor.py and dataeditor_more.py.

import { onPanel, el } from './transfer.js';

let A = null;                 // the app's bridge (toast, saveFile, ...)
let data = null;              // the last de_overview
let folder = 'master';        // the folder shown
let selected = new Set();     // file keys selected in the list
let lastClicked = null;
let viewing = null;           // {where, key, name} open in the editor
let dirty = false;
let pollTimer = null;
let wired = false;
let page = 'main';
let wb = { items: [], open: new Set(['']), sel: new Set(), last: null };
let lastJobLog = [];
const $ = (s) => document.querySelector(s);
const DRAG = 'application/x-rdr-dataeditor';

async function get(route) {
  const r = await fetch('/api/' + route);
  const j = await r.json();
  if (j && j.error) throw new Error(j.error);
  if (j && j.ok === false) throw new Error(j.error_text || 'refused');
  return j;
}

async function post(route, body) {
  const r = await fetch('/api/' + route, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                           body: JSON.stringify(body || {}) });
  const j = await r.json();
  if (j && j.error) throw new Error(j.error);
  if (j && j.ok === false) throw new Error(j.error_text || 'refused');
  return j;
}

function toast(msg, kind) { (A && A.toast) ? A.toast(msg, kind) : console.log(msg); }
function status(msg, done) {
  const s = $('#deStatus');
  s.textContent = msg || ''; s.title = msg || '';
  s.style.color = done ? 'var(--grn)' : ''; s.style.fontWeight = done ? '600' : '';
}

const kb = (n) => (n >= 1 << 20 ? (n / (1 << 20)).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');
const when = (t) => { if (!t) return ''; const d = new Date(t * 1000); return d.toLocaleDateString() + ' ' + d.toTimeString().slice(0, 5); };
const base = (p) => p.split('/').pop();

// ------------------------------------------------------------------ the dialog (one at a time)

function dialog(title, build, buttons, wide) {
  const back = el('div');
  back.style.cssText = 'position:fixed;inset:0;z-index:130;background:rgba(0,0,0,.55);display:flex;'
    + 'align-items:center;justify-content:center';
  const box = el('div');
  box.style.cssText = `width:min(${wide || 620}px,96vw);max-height:92vh;overflow:auto;background:var(--bg2);`
    + 'border:1px solid var(--line);border-radius:6px;box-shadow:0 18px 60px rgba(0,0,0,.6);padding:14px 16px;'
    + 'display:flex;flex-direction:column';
  const h = el('div', null, title);
  h.style.cssText = 'font:var(--t-head);color:var(--title);margin-bottom:10px';
  const body = el('div');
  body.style.cssText = 'flex:1;min-height:0;display:flex;flex-direction:column';
  box.append(h, body);
  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:14px;align-items:center';
  const close = () => { back.remove(); document.removeEventListener('keydown', esc); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', esc);
  for (const b of buttons) {
    if (b.spacer) { const s = el('span', 'muted', b.text || ''); s.style.flex = '1'; bar.append(s); body._note = s; continue; }
    const btn = el('button', b.cls || '', b.label);
    if (b.title) btn.title = b.title;
    btn.onclick = async () => { if (b.run) { const keep = await b.run(body, btn); if (keep) return; } close(); };
    bar.append(btn);
  }
  box.append(bar);
  back.append(box);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  document.body.append(back);
  build(body, close);
  return { body, close, box };
}

// The same as dialog(), drawn INTO a page area instead of over the page (TEXT > HUD text shows the editor beside
// its list). Close empties the area back to its note.
function panel(host, title, build, buttons, emptyNote) {
  host.innerHTML = '';
  host.style.display = 'flex';
  host.style.flexDirection = 'column';
  const head = el('div', 'bar');
  head.append(el('b', null, title));
  const body = el('div');
  body.style.cssText = 'flex:1;min-height:0;display:flex;flex-direction:column;padding:8px 12px';
  const bar = el('div', 'bar');
  bar.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;align-items:center;border-top:1px solid var(--line)';
  const close = () => { host.innerHTML = ''; if (emptyNote) host.append(emptyNote()); };
  for (const b of buttons) {
    if (b.spacer) { const sp = el('span', 'muted', b.text || ''); sp.style.flex = '1'; bar.append(sp); body._note = sp; continue; }
    const btn = el('button', b.cls || '', b.label);
    if (b.title) btn.title = b.title;
    btn.onclick = async () => { if (b.run) { const keep = await b.run(body, btn); if (keep) return; } close(); };
    bar.append(btn);
  }
  host.append(head, body, bar);
  build(body, close);
  return { body, close, box: host };
}

function ask(title, label, value) {
  return new Promise((res) => {
    let inp;
    dialog(title, (b) => {
      b.append(el('div', 'muted', label));
      inp = el('input'); inp.value = value || ''; inp.style.cssText = 'width:100%;margin-top:6px';
      b.append(inp);
      setTimeout(() => { inp.focus(); inp.select(); }, 0);
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { const v = inp.value.trim(); document.querySelector('.de-ask-ok')?.click(); res(v); } });
    }, [{ label: 'Cancel', run: () => { res(null); } }, { label: 'OK', cls: 'primary de-ask-ok', run: () => { res(inp.value.trim()); } }]);
  });
}

function menu(x, y, items) {
  document.querySelectorAll('.de-menu').forEach((m) => m.remove());
  const m = el('div', 'de-menu');
  for (const it of items) {
    if (it === '-') { m.append(el('div', 'de-menu-sep')); continue; }
    const r = el('div', 'de-menu-item' + (it.disabled ? ' off' : ''), it.label);
    if (!it.disabled) r.onclick = () => { m.remove(); it.run(); };
    m.append(r);
  }
  document.body.append(m);
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.min(x, innerWidth - w - 6) + 'px';
  m.style.top = Math.min(y, innerHeight - h - 6) + 'px';
  setTimeout(() => document.addEventListener('mousedown', function off(e) {
    if (!m.contains(e.target)) { m.remove(); document.removeEventListener('mousedown', off); }
  }), 0);
}

// ------------------------------------------------------------------ jobs

// A build that could not run is a pop-up, not a passing note: "nothing happened" must say why.
function problem(title, msg) {
  dialog(title, (b) => {
    const p = el('p', null, msg);
    p.style.cssText = 'font-size:13px;line-height:1.5;margin:0';
    b.append(p);
  }, [{ label: 'OK', cls: 'primary' }], 520);
}

async function startJob(route, body, label) {
  try {
    await post(route, body);
  } catch (e) { problem(label + ': not started', e.message); return false; }
  lastJobLog = [label + '…'];
  // Play waits for the build (the header's disc poll keeps it so until the job ends)
  const pb = document.querySelector('#btnPlay');
  if (pb) { pb.disabled = true; pb.style.opacity = '.45'; pb.title = label + ' in progress: Play is available when it finishes.'; }
  setBusy(true);
  status(label + '…');
  return new Promise((res) => { jobDone = res; poll(); });
}
let jobDone = null;

async function poll() {
  clearTimeout(pollTimer);
  let j;
  try { j = await get('de_job'); } catch { pollTimer = setTimeout(poll, 2000); return; }
  lastJobLog = j.log || [];
  const logBox = document.querySelector('#deLogText');
  if (logBox) { logBox.textContent = lastJobLog.join('\n'); logBox.scrollTop = 1e9; }
  const cb = document.querySelector('#deCancel');
  if (j.state === 'running') {
    status('Building: ' + j.label + '…  ' + (lastJobLog[lastJobLog.length - 1] || ''));
    if (cb) {
      cb.style.display = '';
      cb.disabled = !j.cancellable || !!j.cancel;
      cb.textContent = j.cancel ? 'Cancelling…' : 'Cancel';
      cb.title = !j.cancellable ? 'It is writing the working disc now: it finishes first (stopping half way would leave the disc broken)'
        : 'Stop at the next safe point: nothing is written to the working disc';
    }
    pollTimer = setTimeout(poll, 800);
    return;
  }
  if (cb) cb.style.display = 'none';
  setBusy(false);
  if (j.state === 'done') {
    status('Done!  ' + j.label + (j.seconds != null ? ' in ' + j.seconds + ' s' : '') + '.  ' + (lastJobLog[lastJobLog.length - 1] || ''), true);
  } else {
    status(j.state === 'refused' ? (j.label + ': refused (see the Log)') : j.state === 'error' ? (j.label + ': stopped (see the Log)')
      : j.state === 'cancelled' ? (j.label + ': cancelled, nothing written to the disc') : '');
  }
  if (j.state === 'cancelled') toast(j.label + ' cancelled: the working disc is as it was', 'good');
  if (j.state === 'done' && /Build|Restore/.test(j.label || '')) toast(j.label + ' finished', 'good');
  // after every job: a New .dat (a new level in the mods folder) has to show in the level editor's list at once --
  // it waited for the next Build before
  if (j.state === 'done' && A && A.refreshLists) A.refreshLists();
  if (j.state === 'error' || j.state === 'refused') {
    const why = lastJobLog.find((l) => /PCSX2|Stopped|refused|wait|not enough|cannot|could not/i.test(l)) || 'See the Log for what happened.';
    problem(j.label + (j.state === 'refused' ? ': refused' : ': stopped'), why.replace(/^Stopped: \w+: /, ''));
  }
  await refresh();
  if (/Export/.test(j.label || '')) loadWorkbench();
  if (jobDone) { const d = jobDone; jobDone = null; d(j.state === 'done'); }
}

function setBusy(on) {
  for (const b of document.querySelectorAll('#tab-dataeditor button[data-write]')) b.disabled = on;
  $('#tab-dataeditor').classList.toggle('de-busy', on);
  document.body.classList.toggle('building', on);   // look around, change nothing (xboxmode.js)
}

function logDialog() {
  dialog('Log', (b) => {
    const pre = el('pre');
    pre.id = 'deLogText';
    pre.style.cssText = 'margin:0;height:56vh;overflow:auto;padding:8px 10px;font:var(--t-mono);background:var(--well);'
      + 'border:1px solid var(--line);white-space:pre-wrap';
    pre.textContent = lastJobLog.length ? lastJobLog.join('\n') : 'Nothing yet: a Build, Restore, Export or New .dat writes its steps here.';
    b.append(pre);
    setTimeout(() => { pre.scrollTop = 1e9; }, 0);
  }, [{ label: 'Close' }], 900);
}

// ------------------------------------------------------------------ the working disc page

async function refresh() {
  try { data = await get('de_overview'); } catch (e) {
    $('#deTable tbody').innerHTML = '';
    $('#deEmpty').style.display = '';
    $('#deEmpty').textContent = 'Could not read the Data Editor: ' + e.message;
    return;
  }
  renderFolders();
  renderFiles();
  renderDisc();
  if (data.job && data.job.state === 'running' && !pollTimer) { setBusy(true); poll(); }
}

function renderDisc() {
  const host = $('#deDisc');
  host.innerHTML = '';
  const inj = Object.keys(data.injected || {});
  const changed = inj.filter((p) => (data.health || {})[p] === 'changed');
  host.append(el('div', null, inj.length ? `◆ ${inj.length} archive(s) of yours on the working disc` : 'Nothing of yours on the working disc yet'));
  if (changed.length > 1) {
    const all = el('button', 'primary', `Keep the Studio's versions of all ${changed.length}`);
    all.title = "For each archive below: the disc's copy (with the Studio's later edits) becomes what your next build starts from";
    all.dataset.write = '1';
    all.style.cssText = 'margin-top:6px;width:100%';
    all.onclick = async () => {
      try { for (const p of changed) await post('de_adopt', { archive: p }); toast("The Studio's versions kept: builds include these archives again", 'good'); refresh(); }
      catch (e) { toast(e.message, 'bad'); }
    };
    host.append(all);
  }
  for (const p of changed) {
    const row = el('div');
    row.style.cssText = 'margin-top:6px;color:var(--warn)';
    row.textContent = '⚠ ' + p + ' was changed in the Studio after your build: builds leave it alone. ';
    const keep = el('button', 'ghost', 'Keep the Studio\'s version');
    keep.title = 'The disc\'s copy (with the Studio\'s later edits) becomes what your next build starts from';
    keep.dataset.write = '1';
    keep.onclick = async () => { try { await post('de_adopt', { archive: p }); toast(p + ': the Studio\'s version kept', 'good'); refresh(); } catch (e) { toast(e.message, 'bad'); } };
    row.append(keep);
    host.append(row);
  }
}

function folderKind(k) { const f = (data.folders || []).find((x) => x.key === k); return f ? f.kind : null; }

function renderFolders() {
  const host = $('#deFolders');
  const q = $('#deFolderFilter').value.trim().toLowerCase();
  host.innerHTML = '';
  const counts = {};
  for (const f of data.files) counts[f.folder] = (counts[f.folder] || 0) + 1;
  for (const f of data.folders) {
    if (q && !f.label.toLowerCase().includes(q) && f.key !== folder) continue;
    const row = el('div', 'de-folder' + (f.key === folder ? ' on' : ''));
    const dot = el('span', 'de-dot ' + (data.dots[f.key] || ''));
    dot.title = data.dots[f.key] === 'pending' ? 'Changes waiting for the next Build' : data.dots[f.key] === 'built' ? 'Built into the working disc' : '';
    const name = el('span', null, (f.kind === 'master' ? 'Master' : f.label) + (counts[f.key] ? '  (' + counts[f.key] + ')' : ''));
    row.title = f.kind === 'master'
      ? 'Master only changes the level .dat files (lvls_*), front.dat and swat.dat: its files replace a file of their name there, else are added. banks.dat, pupshow.dat and streams.dat are left alone.'
      : f.kind === 'new' ? 'A new archive: built from this folder and added to the disc' : 'Files here go into ' + f.key;
    row.append(dot, name);
    if (f.injected) {
      const m = el('span', 'de-inj', (data.health || {})[f.key] === 'changed' ? '⚠' : '◆');
      m.title = (data.health || {})[f.key] === 'changed' ? 'Changed in the Studio after your build' : 'Your build of it is on the working disc';
      row.append(m);
    }
    row.onclick = () => { folder = f.key; selected.clear(); renderFolders(); renderFiles(); };
    row.oncontextmenu = (e) => {
      e.preventDefault();
      folder = f.key; selected.clear(); renderFolders(); renderFiles();
      const isDat = f.kind !== 'master';
      menu(e.clientX, e.clientY, [
        { label: 'Export this .dat in full to the Workbench', disabled: !isDat || f.kind === 'new', run: exportDat },
        { label: 'Export this .dat folder (.zip, remembers lvls_sng / lvls_m2 / lvls_m4)', disabled: !isDat, run: exportZip },
        { label: 'Edit its HUD text…', disabled: !isDat, run: () => hudDialog(f.key) },
        { label: 'Add from game…', disabled: !isDat || f.kind === 'new', run: fromGameDialog },
        '-',
        { label: 'Delete all files new to the archive', disabled: f.kind !== 'archive', run: deleteNewFiles },
        { label: 'Open its folder', run: () => openFolder('mods', folderRel(f.key)) },
      ]);
    };
    dropTarget(row, (e) => dropOnFolder(e, f.key));
    host.append(row);
  }
}

function folderRel(k) { return k === 'master' ? 'Master' : k; }

function statusText(f) {
  if (f.status === 'not a game file') return 'not a game file';
  if (f.folder === 'master' && !f.game_file) return f.status === 'built' ? 'Built (new to the game)' : 'New to the game';
  if (f.new_to_archive) return f.status === 'built' ? 'Built (new to the archive)' : 'New to the archive';
  return { built: 'Built', changed: 'Changed', new: 'New' }[f.status] || f.status;
}

// "All files shown": the selected .dat's own files too (status Base), from the unedited disc's list
let showAll = false;
try { showAll = localStorage.getItem('rdr.deShowAll') === '1'; } catch { /* no storage */ }
const baseNames = new Map();          // archive -> [names] (or 'loading')
function wireShowAll() {
  const cb = $('#deShowAll');
  if (!cb || cb._wired) return;
  cb._wired = true;
  cb.checked = showAll;
  cb.onchange = () => {
    showAll = cb.checked;
    try { localStorage.setItem('rdr.deShowAll', showAll ? '1' : '0'); } catch { /* no storage */ }
    renderFiles();
  };
}
function baseRows(tb, rows) {
  if (!showAll || folder === 'master' || folderKind(folder) !== 'archive') return 0;
  const got = baseNames.get(folder);
  if (!got) {
    baseNames.set(folder, 'loading');
    get('de_archive_names?archive=' + encodeURIComponent(folder))
      .then((r) => { baseNames.set(folder, r.names || []); renderFiles(); })
      .catch(() => baseNames.delete(folder));
    return 0;
  }
  if (got === 'loading') return 0;
  const mine = new Set(rows.map((f) => f.name.toLowerCase()));
  let n = 0;
  for (const name of got) {
    const base = name.split('/').pop();
    if (mine.has(base.toLowerCase())) continue;
    const tr = el('tr', 'muted');
    tr.title = 'As the game has it. Double-click to copy it into the mods folder to edit';
    tr.append(el('td', null, name), el('td', 'de-st base', 'Base'), el('td'), el('td'), el('td', 'muted', folder), el('td'));
    tr.ondblclick = async () => {
      try {
        await post('de_from_game', { archive: folder, names: [name], to: null });
        toast(base + ' copied into the mods folder (' + folder + ')', 'good');
        await refresh();
      } catch (e) { toast(e.message, 'bad'); }
    };
    tb.append(tr);
    n++;
  }
  return n;
}

function renderFiles() {
  wireShowAll();
  const tb = $('#deTable tbody');
  tb.innerHTML = '';
  const isMaster = folder === 'master';
  const rows = data.files.filter((f) => f.folder === folder);
  const removed = (data.removed || []).filter((k) => k.startsWith(folder === 'master' ? 'master/' : folder + '/'));
  const keys = rows.map((f) => f.key);
  for (const f of rows) {
    const tr = el('tr');
    tr.className = selected.has(f.key) ? 'sel' : '';
    tr.draggable = true;
    const stt = statusText(f);
    const st = el('td', 'de-st ' + f.status.replace(/ /g, '-'), stt);
    const mode = el('td');
    if (isMaster && f.mode) {
      const b = el('button', 'ghost de-mode', f.mode === 'add' ? 'Add to all' : 'Replace only');
      b.title = !f.game_file ? 'New to the game: it can only be added (into every level .dat, front.dat and swat.dat)'
        : f.mode === 'add' ? 'Goes into every level .dat, front.dat and swat.dat (click: only the .dat files that have it)'
          : 'Goes only into the .dat files that have a file of its name (click: into every level .dat, front.dat and swat.dat)';
      b.dataset.write = '1';
      b.disabled = !f.game_file;
      b.onclick = async (e) => { e.stopPropagation(); await setModes([f.name], f.mode === 'add' ? 'replace' : 'add'); };
      mode.append(b);
    }
    const rel = f.key.split('/').slice(folder === 'master' ? 1 : folder.split('/').length).join('/');
    tr.append(el('td', null, rel), st, el('td', 'num', kb(f.size)), el('td', 'muted', when(f.mtime)),
              el('td', 'muted', f.into.length > 3 ? f.into.length + ' archives' : f.into.join(', ')), mode);
    tr.onclick = (e) => {
      if (e.ctrlKey || e.metaKey) { selected.has(f.key) ? selected.delete(f.key) : selected.add(f.key); }
      else if (e.shiftKey && lastClicked) {
        const a = keys.indexOf(lastClicked), b = keys.indexOf(f.key);
        for (const k of keys.slice(Math.min(a, b), Math.max(a, b) + 1)) selected.add(k);
      } else { selected = new Set([f.key]); openFile({ where: 'mods', key: f.key, name: f.name, size: f.size, archive: folder }); }
      lastClicked = f.key;
      renderFiles();
    };
    tr.oncontextmenu = (e) => {
      e.preventDefault();
      if (!selected.has(f.key)) { selected = new Set([f.key]); renderFiles(); }
      menu(e.clientX, e.clientY, [
        { label: 'Delete…', run: deleteSelected },
        { label: 'Open its folder', run: () => openFolder('mods', f.key) },
        { label: 'Copy to the Workbench', run: () => transfer('mods', [...selected], 'workbench', wbFolder(), false) },
        '-',
        { label: 'Master: Replace only (the .dat files that have it)', disabled: !isMaster, run: () => setSelectedMaster('replace') },
        { label: 'Master: Add to all (every level .dat, front.dat and swat.dat)', disabled: !isMaster, run: () => setSelectedMaster('add') },
        '-',
        { label: 'Delete all files new to the archive', disabled: folderKind(folder) !== 'archive', run: deleteNewFiles },
      ]);
    };
    tr.ondragstart = (e) => {
      if (!selected.has(f.key)) { selected = new Set([f.key]); }
      e.dataTransfer.setData(DRAG, JSON.stringify({ where: 'mods', paths: [...selected] }));
      e.dataTransfer.setData('DownloadURL', 'application/octet-stream:' + f.name + ':' + location.origin
        + '/api/de_x_download?where=mods&key=' + encodeURIComponent(f.key));
      e.dataTransfer.effectAllowed = 'copyMove';
    };
    tb.append(tr);
  }
  for (const k of removed) {
    const tr = el('tr', 'muted');
    tr.append(el('td', null, k), el('td', 'de-st removed', 'Removed'), el('td'), el('td'), el('td', null, 'the game\'s own comes back at the next Build'), el('td'));
    tb.append(tr);
  }
  const nBase = baseRows(tb, rows);
  $('#deEmpty').style.display = rows.length || removed.length || nBase ? 'none' : '';
  $('#deEmpty').textContent = folder === 'master'
    ? 'Drop files here for every level .dat, front.dat and swat.dat (each replaces the files of its name, or is added).'
    : 'Drop changed game files here: they go into ' + folder + ' at the next Build. Add from game copies its own files out to edit.';
  // counts
  const master = data.files.filter((f) => f.folder === 'master' && f.game_file);
  const nAdd = master.filter((f) => f.mode === 'add').length;
  $('#deMasterCounts').textContent = master.length ? `(${master.length - nAdd} Replace only, ${nAdd} Add to all)` : '(Master is empty)';
  const waiting = data.files.filter((f) => f.status === 'new' || f.status === 'changed').length + (data.removed || []).length;
  $('#deSummary').textContent = `${data.files.length} mod file(s)` + (waiting ? `, ${waiting} waiting for a Build` : ', all built')
    + `  ·  ${folder === 'master' ? 'Master' : folder}`;
}

async function setModes(names, mode) {
  try { await post('de_mode', { names, mode }); await refresh(); } catch (e) { toast(e.message, 'bad'); }
}
function setSelectedMaster(mode) {
  const names = data.files.filter((f) => selected.has(f.key) && f.folder === 'master' && f.game_file).map((f) => f.name);
  if (!names.length) return toast('Select Master files with game names first', 'bad');
  setModes(names, mode);
}

// ------------------------------------------------------------------ the editor under the list

async function openFile(f) {
  if (dirty && viewing && !confirm('Discard the unsaved changes to ' + viewing.name + '?')) return;
  dirty = false;
  viewing = f;
  const v = $('#deViewer');
  v.innerHTML = '';
  const head = el('div', 'bar');
  head.append(el('b', null, f.name));
  head.append(el('span', 'muted', '  ' + (f.where === 'workbench' ? 'Workbench' : (f.archive === 'master' ? 'Master' : f.archive || '')) + (f.size != null ? ' · ' + kb(f.size) : '')));
  const sp = el('span'); sp.style.flex = '1'; head.append(sp);
  v.append(head);
  if (/\.strtbl$/i.test(f.name)) {
    const b = el('button', 'primary', 'Edit HUD text…');
    b.title = 'Open this text table in the HUD text editor';
    b.onclick = () => hudEditor(f.where, f.key, f.where === 'mods' ? f.archive : null);
    head.append(b);
  }
  let j;
  try { j = await get('de_file?key=' + encodeURIComponent(f.key) + (f.where === 'workbench' ? '&in=workbench' : '')); } catch (e) { v.append(el('div', 'empty', e.message)); return; }
  if (j.text != null) {
    const ta = el('textarea');
    const orig = j.text.replace(/\r\n/g, '\n');
    ta.value = orig;
    ta.spellcheck = false;
    ta.style.cssText = 'flex:1;width:100%;min-height:60px;resize:none;background:var(--well);color:var(--ink);'
      + 'border:1px solid var(--line);font:var(--t-mono);padding:6px';
    const save = el('button', 'primary', 'Save');
    const revert = el('button', 'ghost', 'Revert');
    if (f.where === 'mods') save.dataset.write = '1';
    save.disabled = revert.disabled = true;
    ta.oninput = () => { dirty = true; save.disabled = revert.disabled = false; };
    revert.onclick = () => { ta.value = orig; dirty = false; save.disabled = revert.disabled = true; };
    const doSave = async () => {
      try {
        await post('de_save', { key: f.key, text: ta.value, in: f.where === 'workbench' ? 'workbench' : undefined });
        dirty = false; save.disabled = revert.disabled = true;
        toast('Saved ' + f.name + (f.where === 'mods' ? ': it goes in at the next Build' : ''), 'good');
        if (f.where === 'mods') refresh(); else loadWorkbench();
      } catch (e) { toast(e.message, 'bad'); }
    };
    save.onclick = doSave;
    save.title = 'Ctrl+S';
    ta.addEventListener('keydown', (e) => { if (e.ctrlKey && e.key === 's') { e.preventDefault(); doSave(); } });
    head.append(revert, save);
    v.append(ta);
  } else {
    const pre = el('pre', 'muted');
    pre.style.cssText = 'margin:0;padding:6px;font:var(--t-mono);overflow:auto;flex:1';
    const hex = j.hex || '';
    let out = '';
    for (let o = 0; o < hex.length / 2; o += 16) {
      const row = hex.slice(o * 2, o * 2 + 32);
      let asc = '';
      for (let i = 0; i < row.length; i += 2) { const c = parseInt(row.slice(i, i + 2), 16); asc += c >= 32 && c < 127 ? String.fromCharCode(c) : '.'; }
      out += o.toString(16).padStart(8, '0') + '  ' + row.replace(/(..)/g, '$1 ').padEnd(48) + ' ' + asc + '\n';
    }
    pre.textContent = 'binary file, ' + j.size.toLocaleString() + ' bytes (read only; the first 4 KB):\n\n' + out;
    v.append(pre);
  }
}

function clearViewer() { viewing = null; dirty = false; $('#deViewer').innerHTML = ''; $('#deViewer').append(el('div', 'empty', 'Select a file to see it here')); }

// ------------------------------------------------------------------ files in and out

async function readAsB64(file) {
  const buf = new Uint8Array(await file.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(s);
}

// Files and folders dropped from Explorer: [{file, path}] with each folder's layout kept.
async function droppedEntries(dt) {
  const out = [];
  const walk = (entry, prefix) => new Promise((res) => {
    if (entry.isFile) { entry.file((f) => { out.push({ file: f, path: prefix + f.name }); res(); }, () => res()); return; }
    const rd = entry.createReader();
    const all = [];
    const more = () => rd.readEntries(async (ents) => {
      if (!ents.length) { for (const e of all) await walk(e, prefix + entry.name + '/'); res(); return; }
      all.push(...ents); more();
    }, () => res());
    more();
  });
  const items = [...(dt.items || [])].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (items.length) { for (const e of items) await walk(e, ''); }
  else for (const f of dt.files || []) out.push({ file: f, path: f.name });
  return out;
}

function dropTarget(node, fn) {
  node.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); node.classList.add('drop'); });
  node.addEventListener('dragleave', () => node.classList.remove('drop'));
  node.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); node.classList.remove('drop'); fn(e); });
}

async function dropOnFolder(e, into) {
  const own = e.dataTransfer.getData(DRAG);
  if (own) {
    const d = JSON.parse(own);
    if (d.where === 'mods') return transfer('mods', d.paths, 'mods', folderRel(into), true);  // within the mods folder: moved
    return transfer('workbench', d.paths, 'mods', folderRel(into), false);                     // from the Workbench: copied
  }
  const ents = await droppedEntries(e.dataTransfer);
  dropFiles(ents.map((x) => x.file), into);
}

// Where an imported .dat goes: a name ending in 2 / 4 (colseum2.dat, mymap_m4.dat) is asked about -- the Showdown
// maps' folders lvls_m2 / lvls_m4 -- else (or when declined) the folder it was dropped on. null: cancelled.
function datInto(name, into) {
  const m = name.match(/(?:^|[_\-.])m?([24])\.dat$/i) || name.match(/([24])\.dat$/i);
  if (!m) return into;
  const g = 'lvls_m' + m[1];
  if (into && String(into).toLowerCase().startsWith(g)) return into;
  const yes = confirm(`${name} ends in ${m[1]}: is it a Showdown map's ${m[1]}-player half?\n\nOK: put it in ${g} (where the game reads it)\nCancel: put it in ${into && String(into).startsWith('lvls_') ? into : 'lvls_sng'}`);
  return yes ? g : (into && String(into).startsWith('lvls_') ? into : 'lvls_sng');
}

function askReplace(name, place) {
  return new Promise((res) => {
    let done = false;
    const fin = (v) => { if (!done) { done = true; res(v); } };
    const title = name + ' is in the mods folder already';
    dialog(title, (b) => {
      b.append(el('p', null, `The mods folder has ${place.archive} already (${place.files} file(s)).`),
        el('p', 'muted', 'Replace: its old files go to the Recycle Bin and the new .dat’s are put in. '
          + 'Merge: the old files stay and the new .dat’s files go over them (files only the old one has are kept).'));
    }, [{ label: 'Cancel', run: () => { fin(null); } },
        { label: 'Merge', run: () => { fin('merge'); } },
        { label: 'Replace', cls: 'primary', run: () => { fin('replace'); } }]);
    // Escape closes it with no button: cancelled
    const obs = new MutationObserver(() => {
      if (done) { obs.disconnect(); return; }
      if (![...document.querySelectorAll('div')].some((x) => x.textContent === title)) { obs.disconnect(); fin(null); }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  });
}

async function importDats(list, into) {
  const out = [];
  for (const f of list) {
    const where = datInto(f.name, into);
    if (where === null) continue;
    let replace = false;
    try {
      const place = await post('de_dat_place', { name: f.name, into: where });
      if (place.exists) {
        const how = await askReplace(f.name, place);
        if (!how) continue;
        replace = how === 'replace';
      }
    } catch (e) { /* an older server: imported as before */ }
    status('Importing ' + f.name + '…');
    const r = await post('de_import', { files: [{ name: f.name, data: await readAsB64(f) }], into: where, replace });
    for (const n of r.results) { toast(n.note, 'good'); out.push(n); if ((n.converted || []).length) showNotes(n.converted); }
  }
  if (out.length) folder = out[out.length - 1].archive;
  return out;
}

async function dropFiles(list, into) {
  const files = [...list];
  if (!files.length) return;
  if ($('#tab-dataeditor').classList.contains('de-busy')) return toast('A build is running: wait for it to finish before adding files.', 'bad');
  into = into || folder;
  const zips = files.filter((f) => /\.zip$/i.test(f.name));
  if (zips.length) await importZips(zips);
  const dats = files.filter((f) => /\.dat$/i.test(f.name));
  const rest = files.filter((f) => !/\.dat$/i.test(f.name) && !/\.zip$/i.test(f.name));
  try {
    if (dats.length) await importDats(dats, into);
    const ps2 = rest.length ? await convertXbox(rest) : [];
    if (ps2 === null) { status(''); return; }
    const fitted = ps2.length ? await fitTextures(into, ps2) : [];
    if (fitted.length) {
      const payload = [];
      for (const f of fitted) payload.push({ name: f.name, data: await readAsB64(f) });
      const r = await post('de_add', { folder: into, files: payload });
      showNotes(r.notes);
      toast(r.added.length + ' file(s) added to ' + (into === 'master' ? 'Master' : into), 'good');
      if (into === 'swat.dat' && fitted.some((f) => /\.(cr|rsc|xrsc)$/i.test(f.name))) { status(''); refresh(); offerShowdown(fitted.map((f) => f.name)); return; }
    }
  } catch (e) { toast(e.message, 'bad'); }
  status('');
  refresh();
}

async function transfer(from, paths, toWhere, to, move) {
  if (!paths.length) return;
  if (from === 'workbench' && toWhere === 'mods' && paths.some((p) => /\.dat$/i.test(p))) {
    const keep = [], files = [];
    for (const p of paths) {
      if (!/\.dat$/i.test(p)) { keep.push(p); continue; }
      try {
        const res = await fetch('/api/de_x_download?where=workbench&key=' + encodeURIComponent(p));
        if (!res.ok) throw new Error('a folder');
        files.push(new File([await res.blob()], p.split('/').pop()));
      } catch { keep.push(p); }                               // a .dat folder (its files): copied as it is
    }
    if (files.length) {
      try { await importDats(files, to === 'Master' ? '' : to); } catch (e) { toast(e.message, 'bad'); }
      status('');
    }
    paths = keep;
    if (!paths.length) { selected.clear(); refresh(); return; }
  }
  if (from === 'workbench' && toWhere === 'mods' && paths.some((p) => /\.tex$/i.test(p))) {
    // a texture over one of the game's of another size goes through the crop window first
    const keep = [];
    for (const p of paths) {
      if (!/\.tex$/i.test(p)) { keep.push(p); continue; }
      let f;
      try {
        const blob = await (await fetch('/api/de_x_download?where=workbench&key=' + encodeURIComponent(p))).blob();
        f = new File([blob], p.split('/').pop());
      } catch { keep.push(p); continue; }
      const got = await fitTextures(to, [f]);
      if (!got.length) continue;                              // cancelled
      if (got[0] === f) { keep.push(p); continue; }           // the same size, or kept as it is
      try {
        await post('de_add', { folder: to === 'Master' ? 'master' : to, files: [{ name: f.name, data: await readAsB64(got[0]) }] });
        toast(f.name + ' fitted and put in ' + to, 'good');
      } catch (e) { toast(e.message, 'bad'); }
    }
    paths = keep;
    if (!paths.length) { selected.clear(); refresh(); return; }
  }
  try {
    const r = await post('de_x_transfer', { from, paths, to_where: toWhere, to, move });
    toast(`${r.done.length} item(s) ${move ? 'moved' : 'copied'} to ${toWhere === 'workbench' ? 'the Workbench' : (to === 'Master' ? 'Master' : to)}`, 'good');
    if (toWhere === 'mods') { try { showNotes((await post('de_x_transfer_notes', {})).notes); } catch { /* older server */ } }
    if (toWhere === 'mods' && to === 'swat.dat') setTimeout(() => offerShowdown(paths), 100);
  } catch (e) { toast(e.message, 'bad'); }
  if (from === 'mods' || toWhere === 'mods') { selected.clear(); refresh(); }
  if (from === 'workbench' || toWhere === 'workbench') loadWorkbench();
}

// The game's streams (streams.dat), searched by name or by what is said (the Audio tab's search), heard, one picked.
// -> its name (no .stm) or null.
function pickStream(start) {
  return new Promise((res) => {
    let done = false;
    const fin = (v) => { if (!done) { done = true; res(v); } };
    const title = 'Pick a stream';
    const d = dialog(title, (b) => {
      const q = el('input'); q.placeholder = 'search: a name (red_, mp_line ...) or words said'; q.style.width = '100%';
      q.value = start || '';
      const player = el('audio'); player.controls = true; player.style.cssText = 'width:100%;margin:6px 0';
      const info = el('div', 'muted', ''); info.style.fontSize = '11px';
      const list = el('div'); list.style.cssText = 'height:46vh;overflow:auto;border:1px solid var(--line);margin-top:6px';
      b.append(q, player, info, list);
      let timer = null;
      const run = async () => {
        const text = q.value.trim();
        list.innerHTML = '';
        if (!text) { list.append(el('div', 'pad muted', 'Type to search the game\'s streams.')); return; }
        list.append(el('div', 'pad muted', 'Searching…'));
        let r;
        try { r = await (await fetch('/api/streams_search?q=' + encodeURIComponent(text))).json(); }
        catch (e) { list.innerHTML = ''; list.append(el('div', 'pad muted', 'Search failed: ' + e.message)); return; }
        list.innerHTML = '';
        const hits = (r && r.hits) || [];
        info.textContent = (r && r.count != null ? r.count : hits.length) + ' found' + (r && r.truncated ? ' (the first 400 shown)' : '');
        if (!hits.length) { list.append(el('div', 'pad muted', 'Nothing found.')); return; }
        for (const st of hits) {
          const name = String(st.name || '').replace(/\.stm$/i, '');
          const row = el('div'); row.style.cssText = 'display:flex;gap:6px;align-items:center;padding:3px 6px;border-bottom:1px solid var(--line)';
          const play = el('button', null, '▶'); play.style.cssText = 'padding:0 7px;font-size:11px';
          play.onclick = () => {
            info.textContent = 'decoding ' + name + '…';
            player.src = '/api/streams_wav?name=' + encodeURIComponent(st.name);
            player.oncanplay = () => { info.textContent = name; };
            player.onerror = () => { info.textContent = 'could not decode ' + name; };
            player.play().catch(() => {});
          };
          const txt = el('div'); txt.style.cssText = 'flex:1;min-width:0';
          const words = st.caption || st.phrase;
          if (words) txt.append(el('div', null, '“' + words + '”'));
          const fn = el('div', 'muted', name); fn.style.cssText = 'font:var(--t-mono);font-size:11px';
          txt.append(fn);
          const use = el('button', 'primary', 'Use');
          use.onclick = () => { fin(name); player.pause(); d.close(); };
          row.append(play, txt, use);
          list.append(row);
        }
      };
      q.oninput = () => { clearTimeout(timer); timer = setTimeout(run, 300); };
      setTimeout(() => { q.focus(); run(); }, 0);
    }, [{ label: 'Cancel', run: () => fin(null) }], 760);
    const obs = new MutationObserver(() => {
      if (done) { obs.disconnect(); return; }
      if (![...document.querySelectorAll('div')].some((x) => x.textContent === title)) { obs.disconnect(); fin(null); }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  });
}

// The full portrait's frame (128 x 256, upright, as it stands on the select screen): the picture dragged into place
// and sized with the mouse wheel. -> an upright 128 x 256 PNG (base64), or null.
function placePhoto(src, title) {
  return new Promise((res) => {
    let done = false;
    const fin = (v) => { if (!done) { done = true; res(v); } };
    const im = new Image();
    im.onload = () => {
      const t = title || 'Place the full portrait in its frame';
      const W = 128, H = 256, Z = 2;                         // shown twice its size
      const cv = el('canvas'); cv.width = W * Z; cv.height = H * Z;
      cv.style.cssText = 'display:block;margin:0 auto;border:1px solid var(--line);cursor:grab;'
        + 'background:repeating-conic-gradient(#3a3a3a 0 25%, #2c2c2c 0 50%) 0 0/16px 16px';
      let sc = Math.min(W / im.width, H / im.height);       // the whole picture in the frame
      let ox = (W - im.width * sc) / 2, oy = H - im.height * sc;
      const draw = () => {
        const g = cv.getContext('2d');
        g.clearRect(0, 0, cv.width, cv.height);
        g.drawImage(im, ox * Z, oy * Z, im.width * sc * Z, im.height * sc * Z);
        g.strokeStyle = 'rgba(255,211,107,.8)'; g.setLineDash([4, 3]); g.strokeRect(.5, .5, cv.width - 1, cv.height - 1);
      };
      let drag = null;
      cv.onmousedown = (e) => {
        drag = { x: e.clientX, y: e.clientY, ox, oy }; cv.style.cursor = 'grabbing';
        const move = (ev) => { ox = drag.ox + (ev.clientX - drag.x) / Z; oy = drag.oy + (ev.clientY - drag.y) / Z; draw(); };
        const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); cv.style.cursor = 'grab'; };
        document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
      };
      cv.onwheel = (e) => {
        e.preventDefault();
        const r = cv.getBoundingClientRect();
        const px = (e.clientX - r.left) / Z, py = (e.clientY - r.top) / Z;
        const k = e.deltaY < 0 ? 1.06 : 1 / 1.06;
        ox = px - (px - ox) * k; oy = py - (py - oy) * k; sc *= k; draw();
      };
      dialog(t, (b) => {
        b.append(el('p', 'muted', 'Drag the picture to place it in the frame; the mouse wheel sizes it. The dashed box is the '
          + 'photo as it stands on the select screen (leave room above the head for the back rows, as the game\'s photos do).'), cv);
        draw();
      }, [{ label: 'Cancel', run: () => fin(null) }, { label: 'Use this', cls: 'primary', run: () => {
        const out = document.createElement('canvas'); out.width = W; out.height = H;
        out.getContext('2d').drawImage(im, ox, oy, im.width * sc, im.height * sc);
        fin(out.toDataURL('image/png').split(',')[1]);
      } }], 360);
      const obs = new MutationObserver(() => {
        if (done) { obs.disconnect(); return; }
        if (![...document.querySelectorAll('div')].some((x) => x.textContent === t)) { obs.disconnect(); fin(null); }
      });
      obs.observe(document.body, { childList: true, subtree: true });
    };
    im.onerror = () => fin(null);
    im.src = src;
  });
}

// Recycle Bin or gone for good: every delete the user starts asks which. -> 'recycle' | 'permanent' | null (cancel)
function askDelete(title, names, note) {
  return new Promise((res) => {
    let done = false;
    const fin = (v) => { if (!done) { done = true; res(v); } };
    dialog(title, (b) => {
      const l = el('div', null, names.slice(0, 14).join('\n') + (names.length > 14 ? '\n…' : ''));
      l.style.cssText = 'white-space:pre;font:var(--t-mono);color:var(--dim-ink)';
      b.append(l);
      if (note) b.append(el('p', 'muted', note));
      b.append(el('p', 'muted', 'Recycle Bin: you can get them back from there. Delete permanently: they are gone at once.'));
    }, [{ label: 'Cancel', run: () => fin(null) },
        { label: 'Delete permanently', cls: 'danger', run: () => fin('permanent') },
        { label: 'Recycle Bin', cls: 'primary', run: () => fin('recycle') }]);
    // Escape closes it with no button: cancelled
    const obs = new MutationObserver(() => {
      if (done) { obs.disconnect(); return; }
      if (![...document.querySelectorAll('div')].some((x) => x.textContent === title)) { obs.disconnect(); fin(null); }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  });
}

async function deleteSelected() {
  const keys = [...selected];
  if (!keys.length) { toast('Select the files to delete first (Ctrl+click picks several)', 'bad'); return; }
  const how = await askDelete('Delete ' + keys.length + ' file(s)?', keys,
    'The game\'s own files come back at the next Build.');
  if (!how) return;
  try {
    await post('de_x_remove', { where: 'mods', paths: keys, permanent: how === 'permanent' });
    selected.clear();
    if (viewing && keys.includes(viewing.key)) clearViewer();
    toast(keys.length + (how === 'permanent' ? ' file(s) deleted permanently' : ' file(s) to the Recycle Bin'), 'good');
    refresh();
  } catch (e) { toast(e.message, 'bad'); }
}

async function deleteNewFiles() {
  if (folderKind(folder) !== 'archive') return toast('Select one of the game\'s .dat folders first', 'bad');
  const fresh = data.files.filter((f) => f.folder === folder && f.new_to_archive);
  if (!fresh.length) return toast(folder + ' has no files new to the archive', 'good');
  const how = await askDelete(`Delete the ${fresh.length} file(s) new to ${folder}?`, fresh.map((f) => f.name),
    'The archive has no file of these names.');
  if (!how) return;
  try {
    const r = await post('de_x_delete_new', { folder, permanent: how === 'permanent' });
    toast(r.deleted.length + (how === 'permanent' ? ' file(s) deleted permanently' : ' file(s) to the Recycle Bin'), 'good');
    refresh();
  } catch (e) { toast(e.message, 'bad'); }
}

async function openFolder(where, rel) {
  try { await post('de_x_open', { where, path: rel || '' }); } catch (e) { toast(e.message, 'bad'); }
}

// ------------------------------------------------------------------ dialogs of the working disc page

async function restoreDialog() {
  // the record as it is now, not the page's last copy (a build, import or reset since left the list empty)
  try { const fresh = await get('de_overview'); if (fresh && fresh.injected) data = fresh; } catch (e) { /* the last copy */ }
  const inj = Object.keys((data && data.injected) || {});
  const checks = [];
  let recyc;
  dialog('Restore defaults', (b) => {
    b.append(el('p', 'muted', 'The checked .dat files go back to how they were before the Data Editor first built them '
      + '(the Studio\'s own edits made before that are kept). New archives come off the disc. This is the only place '
      + 'these changes are undone. Master files that go into them come back at the next Build.'));
    if (!inj.length) b.append(el('div', null, 'Nothing the Data Editor built is on the working disc.'));
    const list = el('div');
    list.style.cssText = 'max-height:44vh;overflow:auto;border:1px solid var(--line);padding:4px 8px;margin:4px 0';
    for (const p of inj) {
      const lab = el('label');
      lab.style.cssText = 'display:block;margin:3px 0';
      const c = el('input'); c.type = 'checkbox'; c.checked = false; c.value = p;
      checks.push(c);
      const h = (data.health || {})[p];
      lab.append(c, el('span', null, ' ' + p + (data.injected[p].new ? '  (new archive)' : '')
        + (h === 'changed' ? '  — changed in the Studio since: those later Studio edits go too' : '')));
      list.append(lab);
    }
    if (inj.length) b.append(list);
    const rl = el('label');
    recyc = el('input'); recyc.type = 'checkbox';
    rl.append(recyc, el('span', null, ' Also move their files to the Recycle Bin (their .dat folders emptied)'));
    b.append(rl);
  }, [
    { label: 'Check all', run: () => { checks.forEach((c) => { c.checked = true; }); return true; } },
    { spacer: true },
    { label: 'Restore entire ISO', cls: 'danger', title: 'Every archive the Data Editor built goes back; the disc returns to its size before it', run: () => {
      if (!inj.length) return true;
      doRestore(['all'], inj, recyc.checked);
    } },
    { label: 'Cancel' },
    { label: 'Restore', cls: 'primary', run: () => {
      const pick = checks.filter((c) => c.checked).map((c) => c.value);
      if (!pick.length) { toast('Check the .dat files to restore', 'bad'); return true; }
      doRestore(pick, pick, recyc.checked);
    } }], 680);
}

async function doRestore(arg, archives, recycleFiles) {
  const ok = await startJob('de_restore', { archives: arg }, 'Restore');
  if (ok && recycleFiles) {
    try { await post('de_x_remove', { where: 'mods', paths: archives }); } catch (e) { toast(e.message, 'bad'); }
    refresh();
  }
}

async function newDatDialog() {
  let lv;
  try { lv = await get('de_levels'); } catch (e) { toast(e.message, 'bad'); return; }
  dialog('New .dat', (b) => {
    const grid = el('div');
    grid.style.cssText = 'display:grid;grid-template-columns:120px 1fr;gap:8px;align-items:center';
    const kind = el('select');
    kind.innerHTML = '<option value="showdown">Showdown map (2 and 4 players: lvls_m2 + lvls_m4, menu entry, unlock, thumbnail)</option><option value="sng">Single player level (lvls_sng)</option>';
    const name = el('input'); name.placeholder = 'up to 7 letters (8 for a single player level)';
    const tpl = el('select');
    const fill = () => {
      tpl.innerHTML = '';
      if (kind.value === 'showdown') {
        for (const m of lv.showdown) tpl.append(new Option(m + (m === 'ranch' ? ' (default)' : ''), m));
        for (const m of lv.story) tpl.append(new Option(m + ' (story level: its place on Ranch\'s duels)', m));
        tpl.value = 'ranch';
      } else {
        for (const m of lv.story) tpl.append(new Option(m + (m === 'saloon' ? ' (default, the unused one)' : ''), m));
        tpl.value = 'saloon';
      }
    };
    kind.onchange = fill; fill();
    const title = el('input'); title.placeholder = 'The menu\'s name (Showdown)';
    const sub = el('input'); sub.value = 'custom map';
    // a blank template (no mesh, no collision, its spawns to start) or the whole map. "Leave out its geometry, zones
    // and collision" was the blank template meant another way: taken out (2026-11-18, the user's)
    const noGeo = el('select');
    for (const [v, t] of [['blank', 'Blank template: no mesh, no collision, only its spawns to start'],
                          ['whole', 'The whole map (to edit that map)']]) noGeo.append(new Option(t, v));
    noGeo.value = 'blank';
    const noGeoBox = { get checked() { return noGeo.value !== 'whole'; } };
    noGeo.title = 'Blank template: the map it starts from without its mesh, zones and collision -- its spawns, lights, '
      + 'sounds and shared files kept -- to build on in the level editor (and its Xbox bundle made beside it). The whole '
      + 'map: copied as it is, to edit that map.';
    for (const [l, c] of [['Kind', kind], ['Name', name], ['Start from', tpl], ['Title', title], ['Subtitle', sub],
                          ['', noGeo]]) {
      grid.append(el('span', 'muted', l), c);
    }
    b.append(grid);
    b.append(el('p', 'muted', 'Makes the archives in the mods folder from the game\'s own files (a Showdown map also '
      + 'gets its menu row, unlock and thumbnail; a story level gets 4x4 spawns and High Noon duel spots). Build puts '
      + 'them on the working disc.'));
    b._get = () => ({ kind: kind.value, name: name.value.trim(), template: tpl.value, title: title.value.trim(), subtitle: sub.value.trim(),
                      no_geometry: noGeoBox.checked, blank: noGeo.value === 'blank' });
  }, [{ label: 'Cancel' }, { label: 'Make', cls: 'primary', run: (b) => {
    const v = b._get();
    if (!/^[a-z0-9_]{1,8}$/i.test(v.name)) { toast('Give a name: letters, digits and _ only', 'bad'); return true; }
    startJob('de_newdat', v, 'New .dat');
  } }]);
}

// Delete .dat (2026-11-18): the selected .dat folder out of the mods folder. A level made by New .dat goes whole --
// both halves of a Showdown map, its menu row, thumbnail and unlock -- and comes off the working disc if it was built.
async function deleteDatDialog() {
  if (!folder || folderKind(folder) === 'master' || !/\.dat$/i.test(folder)) {
    toast('Select a .dat folder first (the list on the left)', 'bad'); return;
  }
  let plan;
  try { plan = await post('de_delete_dat', { archive: folder, dry: true }); } catch (e) { toast(e.message, 'bad'); return; }
  if (!plan.removed.length && !plan.cleaned.length) { toast(folder + ' has nothing in the mods folder to delete', 'bad'); return; }
  const what = plan.removed.map((f) => f + '  (the whole folder)').concat(plan.cleaned);
  const note = (plan.new_level ? 'A new level: everything New .dat made for it goes. ' : 'A game archive: its mod files go; ')
    + (plan.on_disc.length ? plan.on_disc.join(', ') + ' is on the working disc too: it is taken off it (Restore).'
                          : plan.new_level ? 'It is not on the working disc.' : 'the files of the game come back at the next Build.');
  const how = await askDelete('Delete ' + folder + '?', what, note);
  if (!how) return;
  try {
    const r = await post('de_delete_dat', { archive: folder, permanent: how === 'permanent' });
    toast('Deleted ' + (r.removed.join(', ') || folder) + (how === 'permanent' ? ' permanently' : ' (Recycle Bin)'), 'good');
    if (r.on_disc.length) await startJob('de_restore', { archives: r.on_disc }, 'Restore');
    folder = 'master';
    refresh();
    if (window.RDR && window.RDR.refreshLists) window.RDR.refreshLists();
  } catch (e) { toast(e.message, 'bad'); }
}

async function fromGameDialog() {
  if (folderKind(folder) !== 'archive') { toast('Select one of the game\'s .dat folders first', 'bad'); return; }
  let names;
  try { names = (await get('de_archive_names?archive=' + encodeURIComponent(folder))).names; } catch (e) { toast(e.message, 'bad'); return; }
  dialog('Add from game: ' + folder, (b) => {
    b.append(el('p', 'muted', 'Copies the chosen files of ' + folder + ' (as the working disc has them, with the Studio\'s edits) out to change or look at. ' + names.length + ' files.'));
    const to = el('select');
    to.append(new Option('Its .dat folder (the next Build puts them on the disc)', 'dat'),
              new Option('The Workbench (to work on; never built)', 'workbench'));
    to.style.width = '100%';
    const toRow = el('div'); toRow.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:6px';
    toRow.append(el('span', 'muted', 'Copy into'), to);
    b.append(toRow);
    b._to = () => to.value;
    const f = el('input'); f.placeholder = 'Type to filter'; f.style.width = '100%';
    const list = el('select'); list.multiple = true; list.size = 16; list.style.cssText = 'width:100%;margin-top:6px';
    const fill = () => { list.innerHTML = ''; const q = f.value.toLowerCase(); for (const n of names) if (!q || n.toLowerCase().includes(q)) list.append(new Option(n, n)); };
    f.oninput = fill; fill();
    b.append(f, list);
    setTimeout(() => f.focus(), 0);
    b._get = () => [...list.selectedOptions].map((o) => o.value);
  }, [{ spacer: true, text: 'Ctrl/Shift+click picks several' }, { label: 'Cancel' }, { label: 'Copy in', cls: 'primary', run: async (b) => {
    const pick = b._get();
    if (!pick.length) return true;
    const dest = b._to();
    try {
      const r = await post('de_from_game', { archive: folder, names: pick, to: dest === 'workbench' ? 'workbench' : null });
      toast(r.copied.length + ' file(s) copied into ' + (dest === 'workbench' ? 'the Workbench (From ' + folder.replace('/', '_') + ')' : folder), 'good');
      refresh();
      if (dest === 'workbench') loadWorkbench();
    }
    catch (e) { toast(e.message, 'bad'); }
  } }]);
}

function importMenu(e) {
  const r = e.target.getBoundingClientRect();
  menu(r.left, r.bottom + 2, [
    { label: 'A .dat file…', run: () => $('#deImportInput').click() },
    { label: 'A folder of a .dat\'s files…', run: () => $('#deImportDirInput').click() },
    { label: 'A .dat folder exported as a .zip…', run: () => $('#deImportZipInput').click() },
  ]);
}

async function importDatFolder(files) {
  // a folder picked: <name>.dat\... becomes that .dat folder (or the selected one when the name is not a .dat)
  const list = [...files];
  if (!list.length) return;
  const top = (list[0].webkitRelativePath || '').split('/')[0];
  if (/\.dat$/i.test(top)) {
    // where it goes is the server's call: its rdr_folder.txt (every export writes one), else the game's own folder
    // for that name, else lvls_m2 / lvls_m4 for a Showdown map's <map>2 / <map>4, else lvls_sng
    const mark = list.find((f) => (f.webkitRelativePath || '').toLowerCase() === top.toLowerCase() + '/rdr_folder.txt');
    const payload = [];
    for (const f of list) payload.push({ name: (f.webkitRelativePath || f.name).split('/').slice(1).join('/') || f.name, data: await readAsB64(f) });
    try {
      const r = await post('de_x_import_dir', { name: top, marker: mark ? await mark.text() : null, files: payload });
      showNotes(r.notes);
      toast(`${r.added.length} file(s) into ${r.folder} (${r.why})`, 'good');
      folder = r.folder;
    } catch (e) { toast(e.message, 'bad'); }
    refresh();
    return;
  }
  let into = /\.dat$/i.test(top) ? top.toLowerCase() : folder;
  if (/\.dat$/i.test(top)) {
    const hit = data.folders.find((f) => base(f.key) === into);
    into = hit ? hit.key : (folder.startsWith('lvls_') ? folder.split('/')[0] + '/' + into : 'lvls_sng/' + into);
  }
  if (into === 'master') return toast('Select the .dat folder to import into (or name the folder like tomb.dat)', 'bad');
  const payload = [];
  for (const f of list) payload.push({ name: (f.webkitRelativePath || f.name).split('/').slice(1).join('/') || f.name, data: await readAsB64(f) });
  try {
    const r = await post('de_x_upload', { where: 'mods', folder: into, files: payload });
    toast(`${r.added.length} file(s) into ${into}`, 'good');
    folder = into;
  } catch (e) { toast(e.message, 'bad'); }
  refresh();
}

async function importZips(list) {
  for (const f of list) {
    try {
      const r = await post('de_x_import_zip', { name: f.name, data: await readAsB64(f) });
      showNotes(r.notes);
      toast(`${f.name}: ${r.files} file(s) into ${r.folder} (${r.why})`, 'good');
      folder = r.folder;
    } catch (e) { toast(f.name + ': ' + e.message, 'bad'); }
  }
  refresh();
}

async function exportZip() {
  const k = folderKind(folder);
  if (k !== 'archive' && k !== 'new') return toast('Select one of your .dat folders first', 'bad');
  try {
    const r = await post('de_x_export_zip', { archive: folder });
    toast(`${r.files} file(s) saved to ${r.path} (it goes back into ${r.home === '.' ? 'the top folder' : r.home} on import)`, 'good');
  } catch (e) { toast(e.message, 'bad'); }
}

function exportDat() {
  const k = folderKind(folder);
  if (k !== 'archive') return toast('Select one of the game\'s .dat folders first', 'bad');
  startJob('de_x_export_dat', { archive: folder }, 'Export ' + folder + ' to the Workbench');
}

// ------------------------------------------------------------------ Edit HUD text

async function hudDialog(only) {
  const arc = only || (folder !== 'master' ? folder : null);
  let tables;
  try { tables = (await get('de_x_strtbls' + (arc ? '?archive=' + encodeURIComponent(arc) : ''))).tables; } catch (e) { toast(e.message, 'bad'); return; }
  if (!tables.length) return toast((arc || 'The game') + ' has no text tables (.strtbl). swat.dat has the HUD text (swat.strtbl).', 'bad');
  dialog('Edit HUD text', (b) => {
    b.append(el('p', 'muted', 'Pick a text table (swat.strtbl is the HUD text): it is copied to your Workbench (your edited copy if its .dat folder has one) and opened to edit.'));
    const f = el('input'); f.placeholder = 'Type to filter'; f.style.width = '100%';
    const list = el('select'); list.size = 14; list.style.cssText = 'width:100%;margin-top:6px';
    const fill = () => {
      list.innerHTML = ''; const q = f.value.toLowerCase();
      for (const t of tables) {
        const label = `${t.name}   (${t.archive})${t.edited ? '  — in its .dat folder' : ''}`;
        if (!q || label.toLowerCase().includes(q)) list.append(new Option(label, t.archive + '|' + t.name));
      }
      const sw = [...list.options].find((o) => o.value === 'swat.dat|swat.strtbl');
      (sw || list.options[0] || {}).selected = true;
    };
    f.oninput = fill; fill();
    list.ondblclick = () => b.parentNode.querySelector('.de-hud-ok')?.click();
    b.append(f, list);
    b._get = () => list.value;
  }, [{ label: 'Cancel' }, { label: 'Edit', cls: 'primary de-hud-ok', run: async (b) => {
    const v = b._get();
    if (!v) return true;
    const [archive, name] = v.split('|');
    try {
      const r = await post('de_x_hud_open', { archive, name });
      loadWorkbench();
      hudEditor(r.where, r.key, r.archive);
    } catch (e) { toast(e.message, 'bad'); }
  } }]);
}

async function hudEditor(where, key, archive, host) {
  let t;
  try { t = await get('de_x_strtbl?where=' + where + '&key=' + encodeURIComponent(key)); } catch (e) { toast(e.message, 'bad'); return; }
  const blocks = t.blocks.map((rows) => rows.map((r) => ({ ...r })));
  let lang = 0;
  let cur = null;                 // the selected row index in the language shown
  let picked = new Set();
  let hudDirty = false;
  const inMods = where === 'mods';
  const title = 'HUD text: ' + base(key) + (inMods ? ` (in ${archive}'s folder)` : ' (Workbench' + (archive ? ', from ' + archive : '') + ')');
  const show = host ? (t, build, btns) => panel(host, t, build, btns, hudEmptyNote) : (t, build, btns) => dialog(t, build, btns, 1100);
  const d = show(title, (b) => {
    if (!host) b.style.minHeight = '70vh';
    const top = el('div', 'bar');
    top.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:0 0 6px';
    const langSel = el('select');
    t.langs.forEach((l, i) => langSel.append(new Option(l, i)));
    const find = el('input'); find.placeholder = 'Find (ID or text)'; find.style.width = '220px';
    const addB = el('button', '', 'New entry…'); addB.title = 'A whole new entry: a new ID the game\'s files can ask for, in every language';
    const renB = el('button', '', 'Rename ID…'); renB.title = 'Changes the selected entry\'s ID (in every language). The game finds a line by its ID: rename only lines you added, or ones whose ID you change in the game\'s files too';
    const delB = el('button', 'ghost', 'Delete entry'); delB.title = 'Deletes the selected entries (their IDs) from every language';
    top.append(el('span', 'muted', 'Language:'), langSel, el('span', 'muted', 'Find:'), find, addB, renB, delB);
    const wrap = el('div', 'scroll');
    wrap.style.cssText = 'flex:1;min-height:200px;border:1px solid var(--line)';
    const table = el('table', 'de-hud');
    table.innerHTML = '<thead><tr><th style="width:24%">ID</th><th>Text</th><th style="width:13%">Font</th><th style="width:9%">Size</th></tr></thead><tbody></tbody>';
    wrap.append(table);
    // the selected entry
    const edit = el('div');
    edit.style.cssText = 'border:1px solid var(--line);border-top:none;padding:8px;display:flex;flex-direction:column;gap:6px';
    const eh = el('div');
    eh.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    const lineLabel = el('b', null, 'Select an entry to edit it');
    const addLine = el('button', '', 'Add line'); addLine.title = 'A new line under the line the cursor is on (Enter in the text does the same)';
    const remLine = el('button', '', 'Remove line'); remLine.title = 'Takes out the line the cursor is on (the lines below move up)';
    eh.append(lineLabel, addLine, remLine, el('span', 'muted', 'Each line shows on its own row in the game (stacked, as "Twiggy" over Phelps)'));
    const txt = el('textarea');
    txt.style.cssText = 'width:100%;height:84px;resize:vertical;background:var(--well);color:var(--ink);border:1px solid var(--line);font:var(--t-mono);padding:6px';
    const props = el('div');
    props.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    const font = el('input'); font.style.width = '160px';
    const sx = el('input'); sx.type = 'number'; sx.step = '0.05'; sx.style.width = '80px';
    const sy = el('input'); sy.type = 'number'; sy.step = '0.05'; sy.style.width = '80px';
    const allL = el('input'); allL.type = 'checkbox'; allL.checked = true;
    const allLab = el('label'); allLab.append(allL, el('span', null, ' Change it in every language'));
    props.append(el('span', 'muted', 'Font:'), font, el('span', 'muted', 'Size X:'), sx, el('span', 'muted', 'Y:'), sy, allLab);
    edit.append(eh, txt, props);
    b.append(top, wrap, edit);

    const rows = () => blocks[lang];
    const targets = (id) => (allL.checked ? blocks : [blocks[lang]]).map((bl) => bl.find((r) => r.id === id)).filter(Boolean);
    const draw = () => {
      const tb = table.tBodies[0];
      tb.innerHTML = '';
      const q = find.value.trim().toLowerCase();
      rows().forEach((r, i) => {
        if (q && !r.id.toLowerCase().includes(q) && !r.text.toLowerCase().includes(q)) return;
        const tr = el('tr');
        tr.className = (i === cur ? 'sel' : '') + (picked.has(r.id) && i !== cur ? ' sel2' : '');
        const tx = el('td', null, r.text);
        tx.style.whiteSpace = 'pre-wrap';
        tr.append(el('td', 'mono', r.id), tx, el('td', 'muted', r.font), el('td', 'muted', (+r.sx).toFixed(2) + (r.sx !== r.sy ? ' × ' + (+r.sy).toFixed(2) : '')));
        tr.onclick = (e) => {
          if (e.ctrlKey) { picked.has(r.id) ? picked.delete(r.id) : picked.add(r.id); }
          else picked = new Set([r.id]);
          cur = i; show(); draw();
        };
        tb.append(tr);
      });
      b._note.textContent = `${rows().length} entries` + (hudDirty ? ' · not saved' : '');
    };
    const show = () => {
      const r = cur != null ? rows()[cur] : null;
      for (const c of [txt, font, sx, sy, addLine, remLine]) c.disabled = !r;
      if (!r) { lineLabel.textContent = 'Select an entry to edit it'; txt.value = ''; return; }
      lineLabel.textContent = r.id;
      txt.value = r.text; font.value = r.font; sx.value = r.sx; sy.value = r.sy;
    };
    const changed = () => { hudDirty = true; };
    txt.oninput = () => { const r = rows()[cur]; if (!r) return; for (const x of targets(r.id)) x.text = txt.value; changed(); draw(); };
    font.oninput = () => { const r = rows()[cur]; if (!r) return; for (const x of targets(r.id)) x.font = font.value; changed(); draw(); };
    sx.oninput = () => { const r = rows()[cur]; if (!r) return; for (const x of targets(r.id)) x.sx = +sx.value; changed(); draw(); };
    sy.oninput = () => { const r = rows()[cur]; if (!r) return; for (const x of targets(r.id)) x.sy = +sy.value; changed(); draw(); };
    const lineAt = () => txt.value.slice(0, txt.selectionStart).split('\n').length - 1;
    addLine.onclick = () => {
      const ls = txt.value.split('\n'); const at = lineAt();
      ls.splice(at + 1, 0, '');
      txt.value = ls.join('\n'); txt.oninput();
      const pos = ls.slice(0, at + 2).join('\n').length;
      txt.focus(); txt.setSelectionRange(pos, pos);
    };
    remLine.onclick = () => {
      const ls = txt.value.split('\n');
      if (ls.length < 2) return toast('It has one line: there is no second line to remove', 'bad');
      ls.splice(lineAt(), 1);
      txt.value = ls.join('\n'); txt.oninput();
    };
    langSel.onchange = () => { lang = +langSel.value; const id = cur != null ? blocks[0][cur]?.id : null; cur = null;
      if (id) { const i = rows().findIndex((r) => r.id === id); cur = i >= 0 ? i : null; } show(); draw(); };
    find.oninput = draw;
    addB.onclick = async () => {
      const id = await ask('New entry', 'ID: letters, digits and _ (the name the game\'s files ask for). The line goes into every language.', '');
      if (!id) return;
      if (!/^[A-Za-z0-9_]+$/.test(id)) return toast('An ID is letters, digits and _', 'bad');
      if (blocks.some((bl) => bl.some((r) => r.id.toLowerCase() === id.toLowerCase()))) return toast(id + ' is already there', 'bad');
      const like = rows()[cur] || rows()[0] || { font: 'damaged', sx: 0.8, sy: 0.8 };
      for (const bl of blocks) bl.push({ id, font: like.font, text: '', sx: like.sx, sy: like.sy });
      cur = rows().length - 1; picked = new Set([id]); changed(); show(); draw();
      txt.focus();
    };
    renB.onclick = async () => {
      const r = rows()[cur];
      if (!r) return toast('Select the entry to rename', 'bad');
      const id = await ask('Rename ID', 'The new ID for ' + r.id + ' (in every language). The game finds a line by its ID.', r.id);
      if (!id || id === r.id) return;
      if (!/^[A-Za-z0-9_]+$/.test(id)) return toast('An ID is letters, digits and _', 'bad');
      if (blocks.some((bl) => bl.some((x) => x.id.toLowerCase() === id.toLowerCase() && x !== r))) return toast(id + ' is already there', 'bad');
      const old = r.id;
      for (const bl of blocks) for (const x of bl) if (x.id === old) x.id = id;
      picked = new Set([id]); changed(); show(); draw();
    };
    delB.onclick = () => {
      const ids = picked.size ? [...picked] : (rows()[cur] ? [rows()[cur].id] : []);
      if (!ids.length) return toast('Select the entries to delete', 'bad');
      if (!confirm(`Delete ${ids.length} entr${ids.length === 1 ? 'y' : 'ies'} (${ids.slice(0, 5).join(', ')}${ids.length > 5 ? ' …' : ''}) from every language?`)) return;
      for (let i = 0; i < blocks.length; i++) blocks[i] = blocks[i].filter((r) => !ids.includes(r.id));
      cur = null; picked = new Set(); changed(); show(); draw();
    };
    b._save = async (putIn) => {
      try {
        const r = await post('de_x_strtbl_save', { where, key, blocks, put_in: putIn ? archive : null });
        hudDirty = false; draw();
        toast(r.note, 'good');
        if (inMods || putIn) refresh();
        loadWorkbench();
        if (viewing && viewing.key === key) openFile(viewing);
      } catch (e) { toast(e.message, 'bad'); }
    };
    b._dirty = () => hudDirty;
    show(); draw();
  }, [
    { spacer: true },
    { label: 'Save', cls: inMods ? 'primary' : '', title: inMods ? `Saves it in ${archive}'s .dat folder: it goes in at the next Build` : 'Saves the Workbench copy', run: (b) => { b._save(false); return true; } },
    ...(archive && !inMods ? [{ label: 'Save and put in ' + archive, cls: 'primary', title: `Saves, and copies it into ${archive}'s .dat folder so the next Build puts it in the game`, run: (b) => { b._save(true); return true; } }] : []),
    { label: 'Close', run: (b) => (b._dirty() && !confirm('Close without saving your changes?')) },
  ]);
  return d;
}

// ------------------------------------------------------------------ Convert images

// The picture kinds with a page to place the picture on: where it sits, per kind and file ({s: zoom on top of
// "cover", dx, dy in page pixels}), and how the game draws each (the same recipes as ps2build.py's).
const bookPlace = {};
const PICTURE_PAGES = {
  book_red: { w: 256, h: 256, title: 'Fit the picture in the frame' },
  mp_portrait: { w: 128, h: 128, title: 'Fit the character on the team square' },
  level_sp: { w: 128, h: 128, title: 'Fit the picture on the journal page' },
  level_mp: { w: 256, h: 128, title: 'Fit the picture in the map picture' },
  // shown upright, as the game shows it; stored on its side (ps2build.mp_photo)
  mp_photo: { w: 128, h: 256, title: 'Fit the character in the photo (shown upright, as in the game)' },
};

// ------------------------------------------------------------------ the crop window
//
// Drag the box to move it, drag a corner to resize it; `aspect` (width / height) locks its shape, as a texture of a
// set size needs. Resolves {x, y, w, h} in the picture's pixels (rows top first), 'keep' (opts.keepLabel), or null.
function cropDialog(src, opts = {}) {
  return new Promise((resolve) => {
    let result;
    const done = (v) => { result = v; };
    const d = dialog(opts.title || 'Crop the picture', (b) => {
      if (opts.text) b.append(el('p', 'muted', opts.text));
      const cv = el('canvas');
      cv.style.cssText = 'display:block;margin:0 auto;background:#3a3a3a;cursor:move;max-width:100%';
      const info = el('div', 'muted');
      info.style.cssText = 'text-align:center;margin-top:6px;font:var(--t-mono)';
      const row = el('div');
      row.style.cssText = 'display:flex;gap:8px;justify-content:center;margin-top:6px';
      const whole = el('button', 'ghost', 'Whole picture');
      const fit = el('button', 'ghost', 'Largest fit');
      row.append(fit, whole);
      b.append(cv, info, row);
      const im = new Image();
      im.onload = () => {
        const W = im.width, H = im.height;
        const k = Math.min(620 / W, 420 / H, 4);
        cv.width = Math.max(1, Math.round(W * k)); cv.height = Math.max(1, Math.round(H * k));
        const A = opts.aspect || 0;
        const largest = () => {
          if (!A) return { x: 0, y: 0, w: W, h: H };
          let w = W, h = W / A;
          if (h > H) { h = H; w = H * A; }
          return { x: (W - w) / 2, y: (H - h) / 2, w, h };
        };
        let box = opts.initial ? { ...opts.initial } : largest();
        const clamp = () => {
          box.w = Math.max(4, Math.min(box.w, W)); box.h = Math.max(4, Math.min(box.h, H));
          if (A) { if (box.w / box.h > A) box.w = box.h * A; else box.h = box.w / A; }
          box.x = Math.max(0, Math.min(box.x, W - box.w)); box.y = Math.max(0, Math.min(box.y, H - box.h));
        };
        const draw = () => {
          const g = cv.getContext('2d');
          g.clearRect(0, 0, cv.width, cv.height);
          g.drawImage(im, 0, 0, cv.width, cv.height);
          g.fillStyle = 'rgba(0,0,0,.55)';
          const bx = box.x * k, by = box.y * k, bw = box.w * k, bh = box.h * k;
          g.fillRect(0, 0, cv.width, by); g.fillRect(0, by + bh, cv.width, cv.height - by - bh);
          g.fillRect(0, by, bx, bh); g.fillRect(bx + bw, by, cv.width - bx - bw, bh);
          g.strokeStyle = '#ffd36b'; g.lineWidth = 2; g.strokeRect(bx, by, bw, bh);
          g.fillStyle = '#ffd36b';
          for (const [cx, cy] of [[bx, by], [bx + bw, by], [bx, by + bh], [bx + bw, by + bh]]) g.fillRect(cx - 5, cy - 5, 10, 10);
          info.textContent = `${Math.round(box.w)} x ${Math.round(box.h)} of ${W} x ${H}`
            + (opts.target ? `  ->  ${opts.target}` : '');
        };
        let drag = null;
        cv.onmousedown = (e) => {
          const r = cv.getBoundingClientRect(), sx = cv.width / r.width;
          const mx = (e.clientX - r.left) * sx / k, my = (e.clientY - r.top) * sx / k;
          const near = (x, y) => Math.abs(mx - x) * k < 10 && Math.abs(my - y) * k < 10;
          const c = [[box.x, box.y, 1, 1], [box.x + box.w, box.y, 0, 1], [box.x, box.y + box.h, 1, 0], [box.x + box.w, box.y + box.h, 0, 0]]
            .find(([x, y]) => near(x, y));
          // a corner: resized from the opposite one; inside: moved
          drag = c ? { kind: 'size', ax: c[2] ? box.x + box.w : box.x, ay: c[3] ? box.y + box.h : box.y }
            : { kind: 'move', ox: mx - box.x, oy: my - box.y };
          const move = (ev) => {
            const nx = (ev.clientX - r.left) * sx / k, ny = (ev.clientY - r.top) * sx / k;
            if (drag.kind === 'move') { box.x = nx - drag.ox; box.y = ny - drag.oy; }
            else {
              let w = Math.abs(nx - drag.ax), h = Math.abs(ny - drag.ay);
              if (A) { if (w / h > A) h = w / A; else w = h * A; }
              box = { x: nx < drag.ax ? drag.ax - w : drag.ax, y: ny < drag.ay ? drag.ay - h : drag.ay, w, h };
            }
            clamp(); draw();
          };
          const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
          document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
        };
        cv.onwheel = (e) => {
          e.preventDefault();
          const f = e.deltaY < 0 ? 1 / 1.06 : 1.06, cx = box.x + box.w / 2, cy = box.y + box.h / 2;
          box.w *= f; box.h *= f; box.x = cx - box.w / 2; box.y = cy - box.h / 2; clamp(); draw();
        };
        whole.onclick = () => { box = { x: 0, y: 0, w: W, h: H }; if (A) box = largest(); clamp(); draw(); };
        fit.onclick = () => { box = largest(); clamp(); draw(); };
        whole.style.display = A ? 'none' : '';
        clamp(); draw();
        b._box = () => ({ x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.w), h: Math.round(box.h) });
      };
      im.onerror = () => { info.textContent = 'The picture would not open.'; };
      im.src = src;
    }, [{ label: 'Cancel' }].concat(opts.keepLabel ? [{ label: opts.keepLabel, run: () => { done('keep'); } }] : [])
      .concat([{ label: opts.okLabel || 'Use this crop', cls: 'primary', run: (b) => { if (b._box) done(b._box()); } }]), 700);
    const watch = setInterval(() => {
      if (!d.box.isConnected) { clearInterval(watch); resolve(result === undefined ? null : result); }
    }, 200);
  });
}

// A .tex going over one of the game's that has another size, format or mip count: the crop window, then the
// picture made the game's size and format (de_x_tex_fit), so the texture is what the game reads. -> the files to add
// (a fitted copy, the original if kept as it is, none if cancelled).
async function fitTextures(into, list) {
  const out = [];
  for (const f of list) {
    if (!/\.tex$/i.test(f.name)) { out.push(f); continue; }
    let t = null;
    try { t = (await post('de_x_tex_target', { folder: into === 'Master' ? 'master' : into, name: f.name })).target; } catch { t = null; }
    const head = new DataView(await f.slice(0, 8).arrayBuffer());
    const w = head.getUint16(0, true), h = head.getUint16(2, true), fm = head.getUint16(4, true), lv = head.getUint16(6, true);
    if (!t || (t.w === w && t.h === h && t.format === fm && t.levels === lv)) { out.push(f); continue; }
    const b64 = await readAsB64(f);
    let png;
    try { png = (await post('de_x_tex_to_png', { data: b64 })).png; } catch (e) { toast(f.name + ': ' + e.message, 'bad'); out.push(f); continue; }
    const pick = await cropDialog('data:image/png;base64,' + png, {
      title: 'Fit ' + f.name + ' to the game\'s texture', aspect: t.w / t.h, keepLabel: 'Keep it as it is',
      okLabel: 'Crop and convert', target: `${t.w} x ${t.h}, ${t.levels} level${t.levels === 1 ? '' : 's'}`,
      text: `${f.name} is ${w} x ${h} (${lv} level${lv === 1 ? '' : 's'}); the game's (${t.archive}) is ${t.w} x ${t.h} `
        + `(${t.levels} level${t.levels === 1 ? '' : 's'}). The game reads it by that layout, so pick the part to keep: it is `
        + 'made that size and format. Drag the box, drag a corner, or use the mouse wheel.' });
    if (pick === 'keep') { out.push(f); continue; }
    if (!pick) continue;
    try {
      const r = await post('de_x_tex_fit', { data: b64, crop: pick, target: t });
      const bytes = Uint8Array.from(atob(r.data), (c) => c.charCodeAt(0));
      out.push(new File([bytes], f.name));
      toast(`${f.name}: ${r.w} x ${r.h}, ${r.levels} level(s)` + (r.note ? ' (' + r.note + ')' : ''), 'good');
    } catch (e) { toast(f.name + ': not converted: ' + e.message, 'bad'); }
  }
  return out;
}

// ------------------------------------------------------------------ Xbox files, imported as the PS2's
//
// .xtex / .xmod / .xrsc + .xrnt are the Xbox game's own formats for .tex / .mod / .rsc + .rnt: imported anywhere,
// they are converted (the Data Editor's converter, de_x_xbox_convert). Textures (and a bundle's) get the image
// converter's transparency choices first, since the Xbox draws some see-through textures without an alpha. An Xbox
// level's files are not converted here: refused, with the way to do it (Blender's level export).
const XBOX_RE = /\.(xtex|xmod|xrsc|xrnt|xznes|xshadows|xlsh|xfilm|xhud|xmmb|xrain|xflares|xalpha)$/i;
const ALPHA_CHOICES = [['auto', 'Auto (as the game\'s own texture of that name)'], ['keep', 'The image\'s own'],
  ['dark', 'Dark background clear'], ['key', 'One colour clear'], ['mask', 'Brightness is the shape, white'],
  ['ink_black', 'Ink transparency, black ink'], ['ink_white', 'Ink transparency, white ink']];

function showNotes(notes) {
  for (const n of notes || []) {
    if (n.startsWith('LEVEL ')) { toast(n.slice(6), 'bad'); alert(n.slice(6)); } else toast(n, 'good');
  }
}

// -> the transparency picked for each Xbox texture / bundle ({name: {alpha, key, tolerance}}), or null (cancelled)
function xboxDialog(files) {
  return new Promise((resolve) => {
    let result = null;
    const pics = files.filter((f) => /\.(xtex|xrsc)$/i.test(f.name) && !/^lvl_/i.test(f.name));
    const d = dialog('Xbox files to PS2', (b) => {
      b.append(el('p', 'muted', 'These are the Xbox game\'s files; the PS2 game reads its own formats, so they are converted: '
        + 'textures (.xtex -> .tex, in the format the PS2 game\'s texture of that name has), models (.xmod -> .mod) and '
        + 'character bundles (.xrsc + .xrnt -> .rsc + .rnt). Pick how each texture\'s transparency is made (the Xbox draws '
        + 'some see-through textures with no alpha that the PS2\'s keep in their alpha).'));
      const rows = el('div');
      rows.style.cssText = 'display:grid;grid-template-columns:72px 1fr 1fr;gap:8px;align-items:center';
      const opts = {};
      for (const f of pics) {
        const th = el('canvas'); th.width = 64; th.height = 64; th.style.cssText = 'background:#5a544a;border:1px solid var(--line)';
        const nm = el('div', null, f.name + (/\.xrsc$/i.test(f.name) ? ' (its textures)' : ''));
        nm.style.cssText = 'font:var(--t-mono);word-break:break-all';
        const sel = el('select');
        for (const [v, l] of ALPHA_CHOICES) sel.append(new Option(l, v));
        const key = el('input'); key.type = 'color'; key.value = '#000000';
        const tol = el('input'); tol.type = 'number'; tol.value = 16; tol.min = 0; tol.max = 255; tol.style.width = '56px';
        const keyRow = el('span'); keyRow.style.cssText = 'display:none;gap:4px;align-items:center;margin-left:6px';
        keyRow.append(key, el('span', 'muted', 'tolerance'), tol);
        sel.onchange = () => { keyRow.style.display = sel.value === 'key' ? 'inline-flex' : 'none'; };
        const cell = el('div'); cell.append(sel, keyRow);
        rows.append(th, nm, cell);
        opts[f.name] = () => ({ alpha: sel.value, key: key.value.slice(1), tolerance: +tol.value });
        if (/\.xtex$/i.test(f.name)) {
          readAsB64(f).then((b64) => post('de_x_tex_to_png', { data: b64 })).then((r) => {
            const im = new Image();
            im.onload = () => { const k = Math.min(64 / im.width, 64 / im.height); th.getContext('2d').drawImage(im, (64 - im.width * k) / 2, (64 - im.height * k) / 2, im.width * k, im.height * k); };
            im.src = 'data:image/png;base64,' + r.png;
          }).catch(() => {});
        }
      }
      if (!pics.length) rows.append(el('span'), el('span', 'muted', 'No textures to choose for: models are converted as they are.'), el('span'));
      b.append(rows);
      b._get = () => Object.fromEntries(Object.entries(opts).map(([k, fn]) => [k, fn()]));
    }, [{ label: 'Cancel' }, { label: 'Convert and add', cls: 'primary', run: (b) => { result = b._get(); } }], 760);
    const w = setInterval(() => { if (!d.box.isConnected) { clearInterval(w); resolve(result); } }, 200);
  });
}

// Xbox files among `list` converted (after the transparency window); an Xbox level's refused with its note.
// -> the list as the PS2 game reads it, or null when cancelled.
async function convertXbox(list) {
  // nothing converted on the way in (2026-11-17): Xbox and PS2 files are kept exactly as dropped, each for its own
  // game -- the PS2 build makes a PS2 copy of an Xbox file only where the mod has no PS2 file of its own
  if (list) return list;
  if (!list.some((f) => XBOX_RE.test(f.name))) return list;
  const xb = list.filter((f) => XBOX_RE.test(f.name));
  const rest = list.filter((f) => !XBOX_RE.test(f.name));
  const alpha = await xboxDialog(xb);
  if (!alpha) return null;
  const payload = [];
  for (const f of xb.concat(rest.filter((f) => /\.skel$/i.test(f.name)))) payload.push({ name: f.name, data: await readAsB64(f) });
  let r;
  try { r = await post('de_x_xbox_convert', { files: payload, alpha, resize: false }); } catch (e) { toast('Not converted: ' + e.message, 'bad'); return null; }
  showNotes(r.notes);
  const conv = (r.files || []).filter((f) => !/\.skel$/i.test(f.name))
    .map((f) => new File([Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0))], f.name.split('/').pop()));
  return rest.concat(conv);
}

function convertDialog() {
  let files = [];
  let previewFile = null;
  dialog('Convert images to textures', (b) => {
    const grid = el('div');
    grid.style.cssText = 'display:grid;grid-template-columns:130px 1fr;gap:8px 10px;align-items:center';
    const pick = el('input'); pick.type = 'file'; pick.accept = '.png'; pick.multiple = true;
    const picked = el('div', 'muted', 'No images chosen (or drop .png files here)');
    pick.onchange = () => { files = [...pick.files]; picked.textContent = files.map((f) => f.name).join(', ') || 'No images chosen'; fillPreviewList(); };
    const pickWrap = el('div'); pickWrap.append(pick, picked);
    const toWb = el('input'); toWb.type = 'checkbox'; toWb.checked = true;
    const toLab = el('label'); toLab.append(toWb, el('span', null, ' Save in the Workbench (Workbench\\Textures)'));
    const toNote = el('div', 'muted');
    const upd = () => { toNote.textContent = toWb.checked ? '' : 'Into the selected folder: ' + (folder === 'master' ? 'Master' : folder); };
    toWb.onchange = upd; upd();
    const toBox = el('div'); toBox.append(toLab, toNote);
    const alpha = el('select');
    alpha.innerHTML = '<option value="keep">The image\'s own (none for most PNGs)</option>'
      + '<option value="dark">Dark background clear (menu portraits, ui_mphoto_*)</option>'
      + '<option value="key">One colour clear</option>'
      + '<option value="mask">Brightness is the shape, white (dust, smoke, shadows)</option>'
      + '<option value="ink">Ink transparency, no template: greyscale, dark parts solid ink, light parts clear (its own size)</option>'
      + '<option value="level_sp">level.tex image: single player journal page (img0_*.tex, 128x128, black ink)</option>'
      + '<option value="level_mp">level.tex image: multiplayer level select (levelselect_*.tex, 256x128, white ink)</option>'
      + '<option value="mp_portrait">mp portrait: multiplayer character portrait (mp_&lt;code&gt;.tex, your mp template)</option>'
      + '<option value="mp_photo">mp photo: Showdown full-body photo (ui_mphoto_&lt;code&gt;.tex, 256x128, in colour)</option>'
      + '<option value="book_red">book_red picture: your picture in the book_red frame and paper (your GIMP template)</option>'
      + '<option value="book">Weapon book page: greyscale, dark ink solid and white see-through (book_w*)</option>';
    const keyRow = el('div');
    keyRow.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    const key = el('input'); key.type = 'color'; key.value = '#000000';
    const tol = el('input'); tol.type = 'number'; tol.value = 16; tol.min = 0; tol.max = 255; tol.style.width = '64px';
    const soft = el('input'); soft.type = 'number'; soft.value = 48; soft.min = 0; soft.max = 255; soft.style.width = '64px';
    keyRow.append(el('span', 'muted', 'Colour'), key, el('span', 'muted', 'Tolerance:'), tol, el('span', 'muted', 'Softness:'), soft);
    const inkCol = el('select');
    inkCol.append(new Option('Black ink', 'black'), new Option('White ink', 'white'));
    const inkRow = el('div'); inkRow.style.cssText = 'display:flex;gap:6px;align-items:center';
    inkRow.append(el('span', 'muted', 'Ink colour'), inkCol);
    const alphaWanted = () => (alpha.value === 'ink' ? (inkCol.value === 'white' ? 'ink_white' : 'ink_black') : alpha.value);
    const fmt = el('select');
    fmt.innerHTML = '<option value="auto">Auto (256 colours; with alpha 256 colours + alpha; book: 16 + alpha)</option>'
      + '<option value="p8">256 colours</option><option value="pa8">256 colours + alpha</option>'
      + '<option value="p4">16 colours</option><option value="pa4">16 colours + alpha</option><option value="rgba">RGBA (full colour)</option>';
    const mips = el('input'); mips.type = 'checkbox'; mips.checked = true;
    const res = el('input'); res.type = 'checkbox'; res.checked = true;
    const crop = el('input'); crop.type = 'checkbox'; crop.checked = true;
    const cropLab = el('label'); cropLab.append(crop, el('span', null, ' Crop to the frame\'s oval  '));
    cropLab.title = 'book_red picture: the parts of your picture outside the frame\'s oval are cut away';
    const opt = el('div'); const l1 = el('label'); l1.append(mips, el('span', null, ' Mipmaps  '));
    const l2 = el('label'); l2.append(res, el('span', null, ' Resize to power of two (64, 128, 256 ... as the game\'s)'));
    opt.append(cropLab, l1, l2);
    const out = el('pre', 'muted');
    out.style.cssText = 'margin:8px 0 0;max-height:150px;overflow:auto;font:var(--t-mono);white-space:pre-wrap';

    // ---- book_red picture: place the picture in the frame by hand (drag to move, wheel or slider to zoom)
    const prevBox = el('div');
    prevBox.style.cssText = 'display:none;gap:12px;margin-top:10px;align-items:flex-start';
    const cv = el('canvas'); cv.width = cv.height = 256;
    cv.style.cssText = 'border:1px solid var(--line);background:#c4b28c;cursor:grab;image-rendering:auto';
    const pageOf = () => PICTURE_PAGES[alpha.value] || PICTURE_PAGES.book_red;
    const sizeCanvas = () => {
      const pg = pageOf();
      cv.width = pg.w; cv.height = pg.h;
      const scale = pg.w >= 256 ? 1.25 : 2.5;
      cv.style.width = (pg.w * scale) + 'px'; cv.style.height = (pg.h * scale) + 'px';
      sideTitle.textContent = pg.title;
    };
    const sideTitle = el('b', null, '');
    // Save as: the texture's name, with the prefix its kind has in the game (book_red is always book_red.tex)
    const PREFIX = { mp_portrait: 'mp_', level_sp: 'img0_', level_mp: 'levelselect_', mp_photo: 'ui_mphoto_' };
    const outNames = {};
    const nameRow = el('div');
    nameRow.style.cssText = 'display:flex;align-items:center;gap:4px';
    const preLab = el('span', 'mono', '');
    const nameIn = el('input'); nameIn.style.cssText = 'flex:1;min-width:80px';
    const extLab = el('span', 'mono', '.tex');
    nameRow.append(preLab, nameIn, extLab);
    const stemOf = (f) => f.name.replace(/\.[^.]*$/, '').toLowerCase().replace(/[^a-z0-9_]+/g, '_');
    function syncName() {
      const mode = alpha.value;
      if (mode === 'book_red') { preLab.textContent = ''; nameIn.value = 'book_red'; nameIn.placeholder = ''; nameIn.disabled = true; return; }
      nameIn.disabled = !previewFile;
      const pre = PREFIX[mode] || '';
      preLab.textContent = pre;
      if (!previewFile) { nameIn.value = ''; nameIn.placeholder = "the image's own name"; return; }
      // empty: the .png's own name (shown greyed as the placeholder)
      let ph = stemOf(previewFile);
      if (pre && ph.startsWith(pre)) ph = ph.slice(pre.length);
      nameIn.placeholder = ph;
      let v = outNames[mode + '|' + previewFile.name] || '';
      if (pre && v.startsWith(pre)) v = v.slice(pre.length);
      nameIn.value = v;
    }
    nameIn.oninput = () => { if (previewFile && alpha.value !== 'book_red') outNames[alpha.value + '|' + previewFile.name] = nameIn.value.trim(); };
    const side = el('div');
    side.style.cssText = 'display:flex;flex-direction:column;gap:8px;font-size:12px;min-width:200px';
    const which = el('select');
    const zoom = el('input'); zoom.type = 'range'; zoom.min = '0.3'; zoom.max = '3'; zoom.step = '0.01'; zoom.value = '1';
    const zLab = el('span', 'muted', 'Zoom 100%');
    const reset = el('button', 'ghost', 'Reset (fill the page)');
    const cropBtn = el('button', 'ghost', 'Crop…');
    const cropNote = el('span', 'muted', '');
    const nameBox = el('div');
    nameBox.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    nameBox.append(which, nameRow, cropBtn, cropNote);
    nameRow.style.flex = '1';
    side.append(sideTitle, el('span', 'muted', 'Drag the picture to move it; the mouse wheel or the slider zooms.'),
                zLab, zoom, reset,
                el('span', 'muted', 'The preview shows the texture as the game draws it: dark is ink. book_red: with "Crop to the frame\'s oval" on, what is outside the oval is cut away.'));
    prevBox.append(cv, side);
    const layers = {};
    const loadImg = (src) => new Promise((res) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = src; });
    Promise.all([loadImg('/api/de_x_asset?name=book_red_shadow.png'), loadImg('/api/de_x_asset?name=book_red_background.png'),
                 loadImg('/api/de_x_asset?name=mp_square.png')])
      .then(([sh, bg, sq]) => { layers.shadow = sh; layers.page = bg; layers.square = sq; draw(); });
    const pictures = {};
    const crops = {};                                          // {file name: {x, y, w, h}} from the crop window
    async function rawPicture(f) {
      if (!pictures[f.name]) pictures[f.name] = await loadImg(URL.createObjectURL(f));
      return pictures[f.name];
    }
    // the picture as the converter gets it: cropped when a crop was picked
    async function picture(f) {
      const im = await rawPicture(f);
      const c = crops[f.name];
      if (!im || !c) return im;
      const cc = document.createElement('canvas'); cc.width = c.w; cc.height = c.h;
      cc.getContext('2d').drawImage(im, c.x, c.y, c.w, c.h, 0, 0, c.w, c.h);
      return cc;
    }
    const showCrop = () => {
      const c = previewFile && crops[previewFile.name];
      cropNote.textContent = c ? `cropped to ${c.w} x ${c.h}` : '';
      cropBtn.disabled = !previewFile;
    };
    cropBtn.onclick = async () => {
      if (!previewFile) return;
      const pg = PICTURE_PAGES[alpha.value];
      const pick = await cropDialog(URL.createObjectURL(previewFile), {
        title: 'Crop ' + previewFile.name, aspect: pg ? pg.w / pg.h : 0, initial: crops[previewFile.name],
        keepLabel: crops[previewFile.name] ? 'No crop' : null,
        text: pg ? `Locked to the ${pg.w} x ${pg.h} page's shape; the placement below still applies.` : 'Any shape; the part kept is what gets converted.' });
      if (pick === 'keep') delete crops[previewFile.name];
      else if (pick) crops[previewFile.name] = pick;
      showCrop(); draw();
    };
    function fillPreviewList() {
      which.innerHTML = '';
      for (const f of files) which.append(new Option(f.name, f.name));
      which.style.display = files.length > 1 ? '' : 'none';
      previewFile = files[0] || null;
      syncZoom();
      showCrop();
      draw();
    }
    which.onchange = () => { previewFile = files.find((f) => f.name === which.value) || null; syncZoom(); showCrop(); draw(); };
    const placeKey = (f) => alpha.value + '|' + f.name;
    const placeOf = (f) => (bookPlace[placeKey(f)] = bookPlace[placeKey(f)] || { s: 1, dx: 0, dy: 0 });
    function syncZoom() {
      if (previewFile) { zoom.value = placeOf(previewFile).s; zLab.textContent = 'Zoom ' + Math.round(placeOf(previewFile).s * 100) + '%'; }
      syncName();
    }
    zoom.oninput = () => { if (!previewFile) return; placeOf(previewFile).s = +zoom.value; syncZoom(); draw(); };
    reset.onclick = () => { if (!previewFile) return; bookPlace[placeKey(previewFile)] = { s: 1, dx: 0, dy: 0 }; syncZoom(); draw(); };
    // the picture, placed on a page of this size, greyscaled
    async function placedGrey(W, H, colour) {
      const pc = document.createElement('canvas'); pc.width = W; pc.height = H;
      if (!previewFile) return pc;
      const im = await picture(previewFile);
      if (!im) return pc;
      const p = placeOf(previewFile);
      const k = Math.max(W / im.width, H / im.height) * p.s;
      const nw = im.width * k, nh = im.height * k;
      const g2 = pc.getContext('2d');
      g2.filter = colour ? 'none' : 'grayscale(1)';
      g2.drawImage(im, (W - nw) / 2 + p.dx, (H - nh) / 2 + p.dy, nw, nh);
      g2.filter = 'none';
      return pc;
    }
    // ink of a given alpha on a paper colour (dark) or light ink on a dark ground (white ink)
    function inked(g, W, H, alphaAt, white) {
      const out = g.createImageData(W, H), o = out.data;
      for (let i = 0, j = 0; i < o.length; i += 4, j++) {
        const a = alphaAt(j);
        if (white) { o[i] = 40 + 200 * a; o[i + 1] = 36 + 196 * a; o[i + 2] = 30 + 180 * a; }
        else { o[i] = 196 * (1 - a) + 30 * a; o[i + 1] = 178 * (1 - a) + 26 * a; o[i + 2] = 140 * (1 - a) + 22 * a; }
        o[i + 3] = 255;
      }
      g.putImageData(out, 0, 0);
    }
    async function drawOther(mode) {
      const { w: W, h: H } = PICTURE_PAGES[mode];
      const g = cv.getContext('2d');
      if (mode === 'mp_photo') {
        // the photo in its own colours and transparency, over a mid ground
        const pcc = await placedGrey(W, H, true);
        g.fillStyle = '#5a544a'; g.fillRect(0, 0, W, H);
        g.drawImage(pcc, 0, 0);
        return;
      }
      const pc = await placedGrey(W, H);
      const pd = pc.getContext('2d').getImageData(0, 0, W, H).data;
      if (mode === 'mp_portrait') {
        const sc = document.createElement('canvas'); sc.width = W; sc.height = H;
        const sg = sc.getContext('2d');
        if (layers.square) sg.drawImage(layers.square, 0, 0);   // the picture is drawn even without the square
        const sd = sg.getImageData(0, 0, W, H).data;
        // the square (white) with the character's shape erased, the character on top as black (alpha = darkness);
        // drawn as the game shows a portrait: black ink and the white square over a mid ground
        const out = g.createImageData(W, H), o = out.data;
        for (let i = 0; i < o.length; i += 4) {
          const cut = pd[i + 3] / 255, ink = (1 - pd[i] / 255) * cut;
          const sqa = (sd[i + 3] / 255) * (1 - cut) * (1 - ink);
          const base = [90, 84, 74];
          for (let c = 0; c < 3; c++) o[i + c] = base[c] * (1 - ink - sqa) + 255 * sqa;
          o[i + 3] = 255;
        }
        g.putImageData(out, 0, 0);
        return;
      }
      inked(g, W, H, (j) => (1 - pd[j * 4] / 255) * (pd[j * 4 + 3] / 255), false);  // drawn as ink, as book_red (also white + alpha) is
    }
    // the same recipe as the converter (rdrstudio/dataeditor_lib/ps2build.py book_red_page), drawn as the game shows it
    async function draw() {
      if (!PICTURE_PAGES[alpha.value]) return;
      sizeCanvas();
      if (alpha.value !== 'book_red') return drawOther(alpha.value);
      const g = cv.getContext('2d');
      g.clearRect(0, 0, 256, 256);
      // the template's layers missing: the picture is still drawn (as ink), only the frame is not
      if (!layers.shadow || !layers.page) return drawOther('book_red');
      const work = document.createElement('canvas'); work.width = work.height = 256;
      const w = work.getContext('2d');
      w.drawImage(layers.shadow, 0, 0);
      if (previewFile) {
        const im = await picture(previewFile);
        if (im) {
          const p = placeOf(previewFile);
          const k = Math.max(256 / im.width, 256 / im.height) * p.s;
          const nw = im.width * k, nh = im.height * k;
          const pc = document.createElement('canvas'); pc.width = pc.height = 256;
          const pg = pc.getContext('2d');
          pg.filter = 'grayscale(1) invert(1)';
          pg.drawImage(im, (256 - nw) / 2 + p.dx, (256 - nh) / 2 + p.dy, nw, nh);
          pg.filter = 'none';
          if (crop.checked) {                                   // only inside the oval (where the shadow is)
            pg.globalCompositeOperation = 'destination-in';
            const m = document.createElement('canvas'); m.width = m.height = 256;
            const mg = m.getContext('2d'); mg.drawImage(layers.shadow, 0, 0);
            const md = mg.getImageData(0, 0, 256, 256);
            for (let i = 3; i < md.data.length; i += 4) md.data[i] = md.data[i] > 0 ? 255 : 0;
            mg.putImageData(md, 0, 0);
            pg.drawImage(m, 0, 0);
            pg.globalCompositeOperation = 'source-over';
          }
          w.globalAlpha = 191 / 255;
          w.drawImage(pc, 0, 0);
          w.globalAlpha = 1;
        }
      }
      w.drawImage(layers.page, 0, 0);
      // colour erase black -> alpha = brightness (topped at 214); the game draws that alpha as ink on the paper
      const d = w.getImageData(0, 0, 256, 256), px = d.data;
      const out = g.createImageData(256, 256), o = out.data;
      for (let i = 0; i < px.length; i += 4) {
        const a = (Math.max(px[i], px[i + 1], px[i + 2]) / 255) * (px[i + 3] / 255) * (214 / 255);
        o[i] = 196 * (1 - a) + 60 * a; o[i + 1] = 178 * (1 - a) + 50 * a; o[i + 2] = 140 * (1 - a) + 40 * a; o[i + 3] = 255;
      }
      g.putImageData(out, 0, 0);
    }
    let drag = null;
    cv.onmousedown = (e) => { if (!previewFile) return; drag = { x: e.clientX, y: e.clientY, p: { ...placeOf(previewFile) } }; cv.style.cursor = 'grabbing'; };
    window.addEventListener('mousemove', (e) => {
      if (!drag || !previewFile) return;
      const k = cv.width / cv.getBoundingClientRect().width;
      Object.assign(placeOf(previewFile), { dx: drag.p.dx + (e.clientX - drag.x) * k, dy: drag.p.dy + (e.clientY - drag.y) * k });
      draw();
    });
    window.addEventListener('mouseup', () => { drag = null; cv.style.cursor = 'grab'; });
    cv.onwheel = (e) => {
      if (!previewFile) return;
      e.preventDefault();
      const p = placeOf(previewFile);
      p.s = Math.min(3, Math.max(0.3, p.s * (e.deltaY < 0 ? 1.06 : 1 / 1.06)));
      syncZoom(); draw();
    };
    grid.append(el('span', 'muted', 'Images (.png)'), pickWrap, el('span', 'muted', 'Save as'), nameBox, el('span', 'muted', 'Output'), toBox, el('span', 'muted', 'Transparency'), alpha,
                el('span', 'muted', 'Clear colour'), keyRow, el('span', 'muted', ''), inkRow, el('span', 'muted', 'Format'), fmt, el('span', 'muted', 'Options'), opt);
    b.append(grid, prevBox, out);
    const showKey = () => {
      keyRow.style.opacity = /^(key|book)$/.test(alpha.value) ? '1' : '.45';
      inkRow.style.display = alpha.value === 'ink' ? 'flex' : 'none';
      cropLab.style.display = alpha.value === 'book_red' ? '' : 'none';
      prevBox.style.display = PICTURE_PAGES[alpha.value] ? 'flex' : 'none';
      // the shipped textures of these kinds have no mipmaps and an exact size: set for them, not asked
      const preset = ['mp_portrait', 'level_sp', 'level_mp', 'mp_photo'].includes(alpha.value);
      l1.style.display = l2.style.display = preset ? 'none' : '';
      syncZoom();
      draw();
    };
    crop.onchange = () => draw();
    alpha.onchange = showKey; showKey();
    dropTarget(b, (e) => { files = [...e.dataTransfer.files].filter((f) => /\.png$/i.test(f.name)); picked.textContent = files.map((f) => f.name).join(', ') || 'No .png images in that drop'; fillPreviewList(); });
    b._run = async (btn) => {
      if (!files.length) return toast('Choose the .png images first', 'bad');
      if (!toWb.checked && folder === 'master') return toast('Textures go in a .dat folder (select one) or the Workbench', 'bad');
      btn.disabled = true; btn.textContent = 'Converting…';
      try {
        const payload = [];
        for (const f of files) payload.push({ name: f.name, data: await readAsB64(f) });
        const r = await post('de_x_convert_images', { files: payload, opts: {
          to: toWb.checked ? 'workbench' : folder, alpha: alphaWanted(), key: key.value.slice(1), tolerance: +tol.value,
          crops: Object.fromEntries(files.filter((f) => crops[f.name]).map((f) => [f.name, crops[f.name]])),
          soft: +soft.value, format: fmt.value, mips: mips.checked, resize: res.checked, crop: crop.checked,
          place: Object.fromEntries(files.filter((f) => bookPlace[alpha.value + '|' + f.name]).map((f) => [f.name, bookPlace[alpha.value + '|' + f.name]])),
          names: Object.fromEntries(files.filter((f) => outNames[alpha.value + '|' + f.name]).map((f) => [f.name, outNames[alpha.value + '|' + f.name]])) } });
        out.textContent = r.lines.join('\n');
        toast('Converted into ' + (r.where === 'workbench' ? 'the Workbench (' + r.folder + ')' : r.folder), 'good');
        if (r.where === 'workbench') { wb.open.add(r.folder); loadWorkbench(); } else refresh();
      } catch (e) { out.textContent = e.message; toast(e.message, 'bad'); }
      btn.disabled = false; btn.textContent = 'Convert';
    };
  }, [{ label: 'Close' }, { label: 'Convert', cls: 'primary', run: (b, btn) => { b._run(btn); return true; } }], 820);
}

// ------------------------------------------------------------------ the Workbench

async function loadWorkbench() {
  try { const r = await get('de_x_tree?where=workbench'); wb.items = r.items; wb.root = r.root; } catch (e) { wb.items = []; }
  renderWorkbench();
}

function wbFolder() {
  // the folder the Workbench's actions go into: the selected folder, or the selected file's folder
  const s = wb.last && wb.sel.has(wb.last) ? wb.last : null;
  if (!s) return '';
  const it = wb.items.find((i) => i.path === s);
  return it && it.dir ? s : s.split('/').slice(0, -1).join('/');
}

function renderWorkbench() {
  const host = $('#deWbTree');
  host.innerHTML = '';
  const kids = {};
  for (const it of wb.items) {
    const parent = it.path.split('/').slice(0, -1).join('/');
    (kids[parent] = kids[parent] || []).push(it);
  }
  const order = [];
  const walk = (p, depth) => {
    const list = (kids[p] || []).slice().sort((a, b) => (b.dir - a.dir) || a.path.toLowerCase().localeCompare(b.path.toLowerCase()));
    for (const it of list) {
      order.push(it.path);
      const row = el('div', 'de-wbrow' + (wb.sel.has(it.path) ? ' on' : ''));
      row.style.paddingLeft = (8 + depth * 16) + 'px';
      row.draggable = true;
      const tw = el('span', 'de-tw', it.dir ? (wb.open.has(it.path) ? '▾' : '▸') : '');
      const ic = el('span', 'de-ic', it.dir ? '📁' : '📄');
      const nm = el('span', null, base(it.path));
      nm.style.flex = '1';
      row.append(tw, ic, nm);
      if (!it.dir) { const sz = el('span', 'muted', kb(it.size)); row.append(sz); }
      row.title = it.path;
      tw.onclick = (e) => { e.stopPropagation(); toggle(it.path); };
      row.ondblclick = () => { if (it.dir) toggle(it.path); };
      row.onclick = (e) => {
        if (e.ctrlKey) { wb.sel.has(it.path) ? wb.sel.delete(it.path) : wb.sel.add(it.path); }
        else if (e.shiftKey && wb.last) {
          const a = wb.order.indexOf(wb.last), z = wb.order.indexOf(it.path);
          for (const k of wb.order.slice(Math.min(a, z), Math.max(a, z) + 1)) wb.sel.add(k);
        } else {
          wb.sel = new Set([it.path]);
          if (!it.dir) openFile({ where: 'workbench', key: it.path, name: base(it.path), size: it.size });
        }
        wb.last = it.path;
        renderWorkbench();
        host.focus();
      };
      row.oncontextmenu = (e) => {
        e.preventDefault();
        if (!wb.sel.has(it.path)) { wb.sel = new Set([it.path]); wb.last = it.path; renderWorkbench(); }
        wbMenu(e.clientX, e.clientY);
      };
      row.ondragstart = (e) => {
        if (!wb.sel.has(it.path)) { wb.sel = new Set([it.path]); }
        e.dataTransfer.setData(DRAG, JSON.stringify({ where: 'workbench', paths: [...wb.sel] }));
        if (!it.dir) e.dataTransfer.setData('DownloadURL', 'application/octet-stream:' + base(it.path) + ':' + location.origin
          + '/api/de_x_download?where=workbench&key=' + encodeURIComponent(it.path));
        e.dataTransfer.effectAllowed = 'copyMove';
      };
      if (it.dir) dropTarget(row, (e) => dropOnWorkbench(e, it.path));
      host.append(row);
      if (it.dir && wb.open.has(it.path)) walk(it.path, depth + 1);
    }
  };
  walk('', 0);
  wb.order = order;
  if (!wb.items.length) host.append(el('div', 'empty', 'The Workbench holds files freely: drop files and folders here, or copy files from the list. Nothing here is built.'));
  const nf = wb.items.filter((i) => !i.dir).length;
  $('#deWbInfo').textContent = `${nf} file(s)` + (wb.sel.size ? `, ${wb.sel.size} selected` : '') + '  ·  This table will hold files freely';
}

function toggle(p) { wb.open.has(p) ? wb.open.delete(p) : wb.open.add(p); renderWorkbench(); }

function wbMenu(x, y) {
  menu(x, y, [
    { label: 'Add New Folder', run: wbNewFolder },
    { label: 'Rename', disabled: wb.sel.size !== 1, run: wbRename },
    { label: 'Remove…', disabled: !wb.sel.size, run: wbRemove },
    '-',
    { label: 'Copy to the selected .dat folder (' + (folder === 'master' ? 'Master' : folder) + ')', disabled: !wb.sel.size, run: wbToDat },
    { label: 'Open in Explorer', run: () => openFolder('workbench', wb.sel.size === 1 ? [...wb.sel][0] : '') },
  ]);
}

async function dropOnWorkbench(e, into) {
  const own = e.dataTransfer.getData(DRAG);
  if (own) {
    const d = JSON.parse(own);
    return transfer(d.where, d.paths, 'workbench', into, d.where === 'workbench');  // within: moved; from the list: copied
  }
  const ents = await droppedEntries(e.dataTransfer);
  if (!ents.length) return;
  status('Copying ' + ents.length + ' file(s) to the Workbench…');
  try {
    for (let i = 0; i < ents.length; i += 40) {
      const payload = [];
      for (const x of ents.slice(i, i + 40)) payload.push({ name: x.path, data: await readAsB64(x.file) });
      await post('de_x_upload', { where: 'workbench', folder: into, files: payload });
    }
    if (into) wb.open.add(into);
    toast(ents.length + ' file(s) copied to the Workbench', 'good');
  } catch (err) { toast(err.message, 'bad'); }
  status('');
  loadWorkbench();
}

async function wbNewFolder() {
  const n = await ask('Add New Folder', 'A new folder in ' + (wbFolder() || 'the Workbench') + ':', 'New folder');
  if (!n) return;
  try { const r = await post('de_x_new_folder', { where: 'workbench', parent: wbFolder(), name: n }); wb.open.add(wbFolder()); wb.sel = new Set([r.path]); wb.last = r.path; loadWorkbench(); }
  catch (e) { toast(e.message, 'bad'); }
}

async function wbRename() {
  if (wb.sel.size !== 1) return toast('Select one item to rename', 'bad');
  const p = [...wb.sel][0];
  const n = await ask('Rename', 'A new name for ' + base(p) + ':', base(p));
  if (!n || n === base(p)) return;
  try { const r = await post('de_x_rename', { where: 'workbench', path: p, name: n }); wb.sel = new Set([r.path]); wb.last = r.path; loadWorkbench(); }
  catch (e) { toast(e.message, 'bad'); }
}

async function wbRemove() {
  const ps = [...wb.sel];
  if (!ps.length) return toast('Select what to remove', 'bad');
  const how = await askDelete(`Remove ${ps.length} item(s) from the Workbench?`, ps);
  if (!how) return;
  try { await post('de_x_remove', { where: 'workbench', paths: ps, permanent: how === 'permanent' }); wb.sel.clear(); if (viewing && viewing.where === 'workbench' && ps.includes(viewing.key)) clearViewer(); loadWorkbench(); }
  catch (e) { toast(e.message, 'bad'); }
}

function wbToDat() {
  const ps = [...wb.sel];
  if (!ps.length) return toast('Select Workbench files first', 'bad');
  transfer('workbench', ps, 'mods', folderRel(folder), false);
}

// ------------------------------------------------------------------ saved Mods

async function loadSaved() {
  let r;
  try { r = await get('de_x_saved'); } catch { return; }
  const box = $('#deModBox');
  box.innerHTML = '';
  box.append(new Option(r.current ? r.current : '(not saved)', r.current || ''));
  for (const m of r.mods) if (m.name !== r.current) box.append(new Option(m.name, m.name));
  box.value = r.current || '';
  box._mods = r.mods;
  box._current = r.current;
}

async function saveMod(as) {
  const box = $('#deModBox');
  let name = box._current;
  if (as || !name) {
    name = await ask(as ? 'Save Mod As' : 'Save Mod', 'A name for this Mod (the mods folder and the Workbench are kept under it):', name || 'My RDR Mod');
    if (!name) return;
    if (box._mods.some((m) => m.name === name) && name !== box._current && !confirm(name + ' exists: replace it?')) return;
  }
  try { await post('de_x_mod_save', { name }); toast('Mod saved: ' + name, 'good'); loadSaved(); } catch (e) { toast(e.message, 'bad'); }
}

async function loadModDialog() {
  const box = $('#deModBox');
  await loadSaved();
  if (!box._mods.length) return toast('No saved Mods yet: Save keeps the mods folder and the Workbench by name', 'bad');
  let saveFirst;
  dialog('Load a saved Mod', (b) => {
    const list = el('select'); list.size = 12; list.style.width = '100%';
    // newest first, with the levels each holds (2026-11-18)
    for (const m of box._mods) {
      const lv = (m.levels || []).length ? '  levels: ' + m.levels.slice(0, 6).join(', ') + (m.levels.length > 6 ? ' +' + (m.levels.length - 6) : '') : '';
      list.append(new Option(`${m.name}   (${m.when}, ${m.files} mod file(s)${lv})`, m.name));
    }
    list.selectedIndex = 0;
    list.ondblclick = () => b.parentNode.querySelector('.de-load-ok')?.click();
    const l = el('label'); saveFirst = el('input'); saveFirst.type = 'checkbox'; saveFirst.checked = !!box._current;
    l.append(saveFirst, el('span', null, ' Save the current files first' + (box._current ? ' (as ' + box._current + ')' : '')));
    b.append(list, el('p', 'muted', 'The Mod\'s files take the place of the mods folder\'s and the Workbench\'s (those go to the Recycle Bin). Build puts them on the working disc.'), l);
    b._get = () => list.value;
  }, [{ label: 'Cancel' }, { label: 'Load', cls: 'primary de-load-ok', run: async (b) => {
    const n = b._get();
    if (!n) return true;
    try {
      if (saveFirst.checked) {
        const cur = box._current || await ask('Save the current files', 'A name for the current files:', 'My RDR Mod');
        if (cur) await post('de_x_mod_save', { name: cur });
      }
      await post('de_x_mod_load', { name: n });
      toast('Loaded ' + n + ': Build puts it on the working disc', 'good');
      selected.clear(); clearViewer(); wb.sel.clear();
      refresh(); loadWorkbench(); loadSaved();
    } catch (e) { toast(e.message, 'bad'); }
  } }]);
}

// New Mod (2026-11-18, the user's): a fresh start under a name of your own -- the current files saved first (as
// chosen, New .dat levels included), both games put back to the base game (the Xbox game files from their backup,
// the PS2 working disc from the stock disc), the mods folder emptied, and an empty Saved Mod of that name made the
// current one. The page reloads (as Reset to stock does) and the Studio's own files are built back in.
async function newModDialog() {
  const box = $('#deModBox');
  await loadSaved();
  let xs = null;
  try { xs = await post('de_x_xbox_state', {}); } catch { /* no Xbox side */ }
  const hasX = !!(xs && xs.backup_exists);
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + ' ' + pad(now.getHours()) + pad(now.getMinutes());
  let nameIn, saveBox, saveName;
  dialog('New Mod', (b) => {
    const grid = el('div');
    grid.style.cssText = 'display:grid;grid-template-columns:150px 1fr;gap:8px;align-items:center';
    nameIn = el('input'); nameIn.placeholder = 'My new mod'; nameIn.maxLength = 80;
    grid.append(el('span', 'muted', 'New Mod name'), nameIn);
    b.append(grid);
    const l = el('label');
    l.style.cssText = 'display:flex;gap:6px;align-items:center;margin:10px 0 4px;cursor:pointer';
    saveBox = el('input'); saveBox.type = 'checkbox'; saveBox.checked = true;
    l.append(saveBox, el('span', null, ' Save the current files first (New .dat levels included), as:'));
    saveName = el('input'); saveName.style.cssText = 'width:100%'; saveName.maxLength = 80;
    saveName.value = box._current || ('Before new mod ' + stamp);
    saveBox.onchange = () => { saveName.disabled = !saveBox.checked; };
    b.append(l, saveName);
    b.append(el('p', 'muted', 'Then ' + (hasX ? 'both games go back to the base game: the PS2 working disc copied from the stock disc and the Xbox game files from their backup'
      : 'the PS2 working disc goes back to the base game (copied from the stock disc; no Xbox backup is set, so the Xbox side is left as it is)')
      + ', the mods folder and the Studio\'s edits are emptied, and the new Mod starts empty as the current Mod. Close PCSX2 and the Xbox game first.'));
  }, [{ label: 'Cancel' }, { label: 'Make the new Mod', cls: 'danger', run: async () => {
    const bad = /[<>:"\/\\|?*]/;
    const name = nameIn.value.trim();
    if (!name || bad.test(name)) { toast('Give the new Mod a name (no < > : " / \\ | ? *)', 'bad'); return true; }
    if ((box._mods || []).some((m) => m.name.toLowerCase() === name.toLowerCase())) {
      toast('A saved Mod is named ' + name + ' already: pick another name', 'bad'); return true;
    }
    const keep = saveBox.checked ? saveName.value.trim() : '';
    if (saveBox.checked && (!keep || bad.test(keep))) { toast('Give the save of the current files a name', 'bad'); return true; }
    if (keep && keep !== box._current && (box._mods || []).some((m) => m.name.toLowerCase() === keep.toLowerCase())
        && !confirm(keep + ' exists: replace it with the current files?')) return true;
    try {
      if (keep) { await post('de_x_mod_save', { name: keep }); toast('Current files saved as ' + keep, 'good'); }
      if (hasX) {
        const okx = await startJob('de_x_xbox_reset', { clear_mods: true, save: false }, 'Reset to stock (Xbox)');
        if (!okx) { toast('The Xbox reset stopped (see the Log): nothing else was changed', 'bad'); return; }
      }
      toast('Putting the PS2 working disc back to the base game (copying 2.2 GB)…');
      await post('restore', { clear_mods: true, save: false });
      await post('de_x_mod_save', { name });
      try { localStorage.setItem('rdr.buildAfterReset', hasX ? 'both' : 'ps2'); } catch { /* no rebuild */ }
      alert('New Mod "' + name + '" started from the base game' + (hasX ? ' (PS2 and Xbox).' : ' (PS2).')
        + (keep ? '\n\nYour previous files are the Saved Mod "' + keep + '".' : ''));
      location.reload();
    } catch (e) { toast('New Mod stopped: ' + e.message, 'bad'); }
  } }]);
}

async function deleteMod() {
  const box = $('#deModBox');
  const n = box.value || box._current;
  if (!n) return toast('Pick a saved Mod in the Mod box first', 'bad');
  const how = await askDelete('Delete the saved Mod ' + n + '?', [n], 'Your mods folder and Workbench stay as they are.');
  if (!how) return;
  try {
    await post('de_x_mod_delete', { name: n, permanent: how === 'permanent' });
    toast(n + (how === 'permanent' ? ' deleted permanently' : ' to the Recycle Bin'), 'good');
    loadSaved();
  } catch (e) { toast(e.message, 'bad'); }
}

async function exportMod() {
  const n = ($('#deModBox')._current || 'My RDR Mod') + '.rdrmod';
  if (A && A.saveFile) { if (await A.saveFile('/api/de_x_export_mod', n)) toast('Exported ' + n, 'good'); }
  else location.href = '/api/de_x_export_mod';
}

async function importMod(file) {
  if (!file) return;
  try {
    status('Importing ' + file.name + '…');
    const r = await post('de_x_mod_import', { name: file.name, data: await readAsB64(file) });
    toast(r.note, 'good');
  } catch (e) { toast(e.message, 'bad'); }
  status('');
  refresh(); loadWorkbench();
}

// ------------------------------------------------------------------ the manual page

function manualView() {
  const host = $('#dePageManual');
  if (host.dataset.built) return;
  host.dataset.built = '1';
  const shell = () => (window.pywebview && window.pywebview.api) || null;
  const field = (label, ph, pick) => {
    const row = el('div');
    row.style.cssText = 'display:grid;grid-template-columns:150px 1fr auto;gap:8px;align-items:center;margin:6px 0';
    const inp = el('input'); inp.placeholder = ph;
    row.append(el('span', 'muted', label), inp);
    const b = el('button', 'ghost', 'Browse…');
    b.disabled = !(shell() && shell()[pick]);
    b.title = b.disabled ? 'Type the path (the desktop app offers a picker here)' : '';
    b.onclick = async () => { const p = await shell()[pick](); if (p) inp.value = p; };
    row.append(b);
    return [row, inp];
  };
  const card = (title, note) => {
    const c = el('div', 'de-card');
    c.append(el('div', 'de-card-title', title));
    if (note) c.append(el('div', 'muted', note));
    host.append(c);
    return c;
  };
  let c = card('Extract', 'A .dat (or a prototype .zip) into a folder named after it inside the one chosen.');
  const [r1, arc] = field('Archive (.dat / .zip)', 'the archive file', 'pick_open_file');
  const [r2, to] = field('Extract to', 'a folder', 'pick_folder');
  const go1 = el('button', 'primary', 'Extract'); go1.dataset.write = '1';
  go1.onclick = () => { if (!arc.value || !to.value) return toast('Give the archive and the folder', 'bad'); startJob('de_manual_extract', { archive: arc.value.trim(), to: to.value.trim() }, 'Extract an archive'); };
  c.append(r1, r2, go1);
  c = card('Extract every archive', 'All 66 archives of the unedited disc into one folder, laid out as on the disc (swat.dat\\, front.dat\\, lvls_sng\\tomb.dat\\ …).');
  const [r3, toAll] = field('Extract to', 'a folder', 'pick_folder');
  const go2 = el('button', 'primary', 'Extract every archive'); go2.dataset.write = '1';
  go2.onclick = () => { if (!toAll.value) return toast('Give the folder', 'bad'); startJob('de_manual_extract_all', { to: toAll.value.trim() }, 'Extract every archive'); };
  c.append(r3, go2);
  c = card('Build', 'A folder of files as a .dat (retail: laid out as the game\'s, 32 byte packing that never crosses a 2048 byte sector, the engine\'s name order, names and files compressed) or a .zip (the prototype build: plain names).');
  const [r4, src] = field('Folder to build', 'a folder', 'pick_folder');
  const [r5, out] = field('Save as', 'e.g. C:\\…\\mymap2.dat or prototype.zip', 'pick_save_file');
  const kind = el('div');
  kind.style.cssText = 'display:flex;gap:14px;margin:4px 0 8px 158px';
  const rd = el('label'); const rdi = el('input'); rdi.type = 'radio'; rdi.name = 'deBuildKind'; rdi.checked = true; rd.append(rdi, el('span', null, ' .dat (retail)'));
  const rz = el('label'); const rzi = el('input'); rzi.type = 'radio'; rzi.name = 'deBuildKind'; rz.append(rzi, el('span', null, ' .zip (prototype build)'));
  const fixExt = () => { if (out.value) out.value = out.value.replace(/\.(dat|zip)$/i, '') + (rzi.checked ? '.zip' : '.dat'); };
  rdi.onchange = rzi.onchange = fixExt;
  kind.append(rd, rz);
  const go3 = el('button', 'primary', 'Build'); go3.dataset.write = '1';
  go3.onclick = () => { if (!src.value || !out.value) return toast('Give the folder and where to save', 'bad'); fixExt(); startJob('de_manual_build', { folder: src.value.trim(), output: out.value.trim() }, 'Build an archive'); };
  c.append(r4, r5, kind, go3);
  if (!shell()) host.append(el('p', 'muted', 'These work only in the Studio on this computer (the desktop app gives native pickers; in a browser type the paths).'));
}

// ------------------------------------------------------------------ the settings page

async function settingsView() {
  const host = $('#dePageSettings');
  host.innerHTML = '';
  let s;
  try { s = await get('de_x_settings'); } catch (e) { host.append(el('div', 'empty', e.message)); return; }
  const card = (title, note) => {
    const c = el('div', 'de-card');
    c.append(el('div', 'de-card-title', title));
    if (note) c.append(el('div', 'muted', note));
    host.append(c);
    return c;
  };
  const row = (c, label, value, ok, actions) => {
    const r = el('div', 'de-setrow');
    const mark = el('span', ok ? 'de-ok' : 'de-bad', ok ? '✓' : '✗');
    const v = el('span', 'mono', value || 'not set');
    v.style.cssText = 'word-break:break-all;flex:1';
    r.append(mark, el('span', 'muted de-setlabel', label), v);
    for (const a of actions || []) { const b = el('button', 'ghost', a[0]); b.title = a[2] || ''; b.onclick = a[1]; r.append(b); }
    c.append(r);
  };
  const appSetup = ['App setup…', () => { const b = document.querySelector('#btnAppSetup'); if (b) b.click(); else toast('App setup is in the desktop app', 'bad'); }, 'Change it in App setup (the Studio restarts)'];
  let c = card('Game', 'The Data Editor builds into the Studio\'s working disc and reads the unedited disc for the game\'s own files. PS2 (USA).');
  row(c, 'Clean ISO (unedited)', s.vanilla, !!s.vanilla, [appSetup]);
  row(c, 'Working disc (test ISO)', s.working, !!s.working, [['Open folder', () => openFolder('disc', ''), 'Show the working disc\'s folder'], appSetup]);
  row(c, 'PCSX2', s.pcsx2, !!s.pcsx2, [appSetup]);
  c = card('Data Editor folders', 'Beside the working disc unless App setup > Folders > Data Editor says otherwise.');
  row(c, 'Data Editor folder', s.home, true, [['Open', () => openFolder('home', '')], appSetup]);
  row(c, 'Mods folder (RDRMods)', s.mods, true, [['Open', () => openFolder('mods', '')]]);
  row(c, 'Workbench', s.workbench, true, [['Open', () => openFolder('workbench', '')]]);
  row(c, 'Saved Mods', s.saved, true, [['Open', () => openFolder('saved', '')]]);
  row(c, 'Cache (extracted archives)', s.cache, true, []);
  c = card('Building', 'Archives are laid out as the game\'s own: every file 32 bytes after the last but never across a 2048 byte sector, '
    + 'in the original\'s load order, each file compressed or stored as the original, names compressed. That is what keeps '
    + 'loading as fast as the unedited game, so there are no compression switches here: the original\'s settings are always used.');
  c = card('Xbox game (for Xbox mods)', 'Folders of the UNEDITED Xbox game: its .dat files, or its archives extracted to folders. '
    + 'An imported file that is one of the Xbox game\u2019s own unedited files is then recognised: the PS2 game\u2019s own copy is used, '
    + 'and only what the mod changed is converted. Without it, every Xbox file is converted.');
  const xr = s.xbox_reference || [];
  if (!xr.length) c.append(el('div', 'muted', 'No folder set.'));
  xr.forEach((f, i) => row(c, 'Folder ' + (i + 1), f, true, [['Remove', async () => {
    try { await post('de_x_xbox_ref', { folders: xr.filter((_x, j) => j !== i) }); } catch (e) { return toast(e.message, 'bad'); }
    settingsView(); }]]));
  const addX = el('button', 'ghost', 'Add a folder…');
  addX.onclick = async () => {
    const f = prompt('A folder of the unedited Xbox game (its .dat files, or an extracted archive):');
    if (!f) return;
    toast('Reading the Xbox game\u2019s files (once)…');
    try { const r = await post('de_x_xbox_ref', { folders: xr.concat([f]) }); toast(r.files + ' Xbox file names known.', 'good'); }
    catch (e) { return toast(e.message, 'bad'); }
    settingsView();
  };
  c.append(addX);
  try {
    const X = await post('de_x_xbox_state', {});
    c = card('Xbox working disc', 'The Xbox side\'s game files: the recomp\'s assets folder (its game, as it plays). The Studio '
      + 'reads the Xbox game\'s levels the PS2 disc has not (Coliseum) from here, and with Game: Xbox, Build writes the mod for '
      + 'it into the recomp\'s mods folder.');
    row(c, 'Game files', X.assets || '(none)', !!X.assets_ok, [['Change…', async () => {
      const f = prompt('The recomp\'s game files (its assets folder):', X.assets || '');
      if (f === null) return;
      try { await post('de_x_xbox_set', { assets: f }); toast('Xbox working disc set.', 'good'); } catch (e) { return toast(e.message, 'bad'); }
      settingsView();
    }]]);
    row(c, 'Recomp', X.exe || '(none)', !!X.exe_ok, [['Change…', async () => {
      const f = prompt('The recomp\'s RDR.exe (its folder or the file):', X.exe || '');
      if (f === null) return;
      try { await post('de_x_xbox_set', { exe: f }); toast('Recomp set.', 'good'); } catch (e) { return toast(e.message, 'bad'); }
      settingsView();
    }]]);
  } catch (e) { /* an older server: no Xbox side */ }
  c = card('Waiting for a rebuild', 'Studio edits that needed more room than their old space. They go in at the next Rebuild and play (the Play button), all at once.');
  if (!s.pending.length) c.append(el('div', 'muted', 'Nothing waiting.'));
  for (const p of s.pending) c.append(el('div', 'mono', `${p.archive}: ${p.files.join(', ')}`));
}

// ------------------------------------------------------------------ pages and wiring

function setPage(v) {
  page = v;
  $('#dePgMain').classList.toggle('on', v === 'main');
  $('#dePgManual').classList.toggle('on', v === 'manual');
  $('#dePgSettings').classList.toggle('on', v === 'settings');
  $('#dePageMain').style.display = v === 'main' ? 'flex' : 'none';
  $('#dePageManual').style.display = v === 'manual' ? 'block' : 'none';
  $('#dePageSettings').style.display = v === 'settings' ? 'block' : 'none';
  if (v === 'manual') manualView();
  if (v === 'settings') settingsView();
}

function splitter(bar, target, sign, key) {
  try { const h = localStorage.getItem(key); if (h) target.style.height = h; } catch { /* per-viewer only */ }
  bar.addEventListener('mousedown', (e) => {
    e.preventDefault();
    const y0 = e.clientY, h0 = target.getBoundingClientRect().height;
    const move = (ev) => { target.style.height = Math.max(80, h0 + sign * (y0 - ev.clientY)) + 'px'; };
    const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
      try { localStorage.setItem(key, target.style.height); } catch { /* fine */ } };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
}

function wire() {
  if (wired) return;
  wired = true;
  $('#dePgMain').onclick = () => setPage('main');
  $('#dePgManual').onclick = () => setPage('manual');
  $('#dePgSettings').onclick = () => setPage('settings');
  // the PS2 / Xbox switch (xboxmode.js) picks where Build puts the mod
  $('#deBuild').onclick = () => (window.rdrPlatform && window.rdrPlatform() === 'xbox'
    ? startJob('de_x_xbox_build', {}, 'Build into the Xbox game')
    : startJob('de_build', {}, 'Build into the working disc'));
  $('#deRestore').onclick = restoreDialog;
  $('#deNewDat').onclick = newDatDialog;
  if ($('#deDeleteDat')) $('#deDeleteDat').onclick = deleteDatDialog;
  $('#deFromGame').onclick = fromGameDialog;
  $('#deDelete').onclick = deleteSelected;
  $('#deDelNew').onclick = deleteNewFiles;
  $('#deOpenFolder').onclick = () => openFolder('mods', folderRel(folder));
  $('#deConvert').onclick = convertDialog;
  $('#deCancel').onclick = async () => {
    const b = $('#deCancel'); b.disabled = true; b.textContent = 'Cancelling…';
    try { const r = await post('de_cancel', {}); if (!r.cancel && r.why) toast('Not now: ' + r.why, 'bad'); } catch (e) { toast(e.message, 'bad'); }
  };
  $('#deHud').onclick = () => hudDialog(null);
  $('#deExportDat').onclick = exportDat;
  $('#deAllReplace').onclick = () => setModes(data.files.filter((f) => f.folder === 'master' && f.game_file).map((f) => f.name), 'replace');
  $('#deAllAdd').onclick = () => setModes(data.files.filter((f) => f.folder === 'master' && f.game_file).map((f) => f.name), 'add');
  $('#deAddBtn').onclick = () => $('#deAddInput').click();
  $('#deAddInput').onchange = (e) => { dropFiles(e.target.files, folder); e.target.value = ''; };
  $('#deImportBtn').onclick = importMenu;
  $('#deImportInput').onchange = (e) => { dropFiles(e.target.files, folder); e.target.value = ''; };
  $('#deImportDirInput').onchange = (e) => { importDatFolder(e.target.files); e.target.value = ''; };
  $('#deImportZipInput').onchange = (e) => { importZips([...e.target.files]); e.target.value = ''; };
  $('#deExportZip').onclick = exportZip;
  $('#deFolderFilter').oninput = renderFolders;
  $('#deLogBtn').onclick = logDialog;
  // saved Mods
  $('#deModSave').onclick = () => saveMod(false);
  $('#deModSaveAs').onclick = () => saveMod(true);
  $('#deModLoad').onclick = loadModDialog;
  if ($('#deModNew')) $('#deModNew').onclick = newModDialog;
  $('#deModDelete').onclick = deleteMod;
  $('#deModExport').onclick = exportMod;
  $('#deModImport').onclick = () => $('#deModImportInput').click();
  $('#deModImportInput').onchange = (e) => { importMod(e.target.files[0]); e.target.value = ''; };
  // the Workbench
  $('#deWbFolder').onclick = wbNewFolder;
  $('#deWbAdd').onclick = () => $('#deWbAddInput').click();
  $('#deWbAddInput').onchange = async (e) => {
    const fs = [...e.target.files]; e.target.value = '';
    const payload = []; for (const f of fs) payload.push({ name: f.name, data: await readAsB64(f) });
    try { await post('de_x_upload', { where: 'workbench', folder: wbFolder(), files: payload }); } catch (err) { toast(err.message, 'bad'); }
    loadWorkbench();
  };
  $('#deWbRename').onclick = wbRename;
  $('#deWbRemove').onclick = wbRemove;
  $('#deWbToDat').onclick = wbToDat;
  $('#deWbOpen').onclick = () => openFolder('workbench', wb.sel.size === 1 ? [...wb.sel][0] : '');
  const wbt = $('#deWbTree');
  dropTarget(wbt, (e) => dropOnWorkbench(e, ''));
  dropTarget($('#deWbBar'), (e) => dropOnWorkbench(e, wbFolder()));
  wbt.oncontextmenu = (e) => { if (e.target === wbt) { e.preventDefault(); wb.sel.clear(); renderWorkbench(); wbMenu(e.clientX, e.clientY); } };
  wbt.addEventListener('keydown', (e) => {
    if (e.key === 'Delete') { e.preventDefault(); wbRemove(); }
    else if (e.key === 'F2') { e.preventDefault(); wbRename(); }
    else if (e.ctrlKey && e.key.toLowerCase() === 'a') { e.preventDefault(); wb.sel = new Set(wb.order); renderWorkbench(); }
  });
  wbt.addEventListener('paste', async (e) => {
    const files = [...(e.clipboardData.files || [])];
    if (files.length) {
      e.preventDefault();
      const payload = []; for (const f of files) payload.push({ name: f.name, data: await readAsB64(f) });
      try { await post('de_x_upload', { where: 'workbench', folder: wbFolder(), files: payload }); toast(files.length + ' file(s) pasted', 'good'); } catch (err) { toast(err.message, 'bad'); }
      return loadWorkbench();
    }
    const t = e.clipboardData.getData('text');
    if (t) { e.preventDefault(); try { const r = await post('de_x_paste_text', { folder: wbFolder(), text: t }); wb.sel = new Set([r.path]); wb.last = r.path; toast('Pasted as ' + r.path, 'good'); } catch (err) { toast(err.message, 'bad'); } loadWorkbench(); }
  });
  // the list
  const zone = $('#deFilesZone');
  dropTarget(zone, (e) => dropOnFolder(e, folder));
  document.addEventListener('keydown', (e) => {
    if (!$('#tab-dataeditor').classList.contains('on') || page !== 'main') return;
    const tag = document.activeElement.tagName;
    if (tag === 'TEXTAREA' || tag === 'INPUT' || tag === 'SELECT' || document.activeElement === wbt) return;
    if (document.querySelector('.de-menu') || document.querySelector('body > div[style*="z-index:130"]')) return;
    if (e.key === 'Delete') deleteSelected();
    else if (e.ctrlKey && e.key.toLowerCase() === 'a') { e.preventDefault(); selected = new Set(data.files.filter((f) => f.folder === folder).map((f) => f.key)); renderFiles(); }
  });
  splitter($('#deSplitEditor'), $('#deViewer'), 1, 'deViewerH');
  splitter($('#deSplitWb'), $('#deWb'), 1, 'deWbH');
  clearViewer();
}

// ------------------------------------------------------------------ BALANCE > Creatures > Add Character

async function addCharacterDialog(opts = {}) {
  let list = [];
  try { list = (await get('creatures')).creatures.map((c) => c.name); } catch (e) { toast(e.message, 'bad'); return; }
  const current = (document.querySelector('#crList .row.sel') || {}).dataset?.cr || 'srd.cr';
  let files = [];
  dialog('Add Character: a new character slot', (b) => {
    // where the model comes from: an existing character's, or a .rsc of your own
    const how = el('div');
    how.style.cssText = 'display:flex;flex-direction:column;gap:6px;margin-bottom:10px';
    const mk = (v, label, on) => {
      const l = el('label'); const r = el('input'); r.type = 'radio'; r.name = 'acHow'; r.value = v; r.checked = on;
      l.append(r, el('span', null, ' ' + label)); how.append(l); return r;
    };
    const rCopy = mk('copy', 'Copy from an existing character (its model too)', true);
    const rOwn = mk('rsc', 'Use an .rsc from my files (a model exported by the RDR Blender add-on)', false);
    b.append(how);
    const grid = el('div');
    grid.style.cssText = 'display:grid;grid-template-columns:150px 1fr;gap:8px;align-items:center';
    const src = el('select');
    for (const n of list) src.append(new Option(n.replace(/\.cr$/, ''), n));
    src.value = list.includes(current) ? current : list[0];
    const srcLabel = el('span', 'muted', 'Copy from');
    const code = el('input'); code.placeholder = 'e.g. gbw (letters, digits, _)';
    const KINDS = [['cr', 'tuning (.cr)'], ['skel', 'skeleton'], ['dam', 'damage'], ['hot', 'hit spots'], ['msd', 'motion'],
                   ['rep', 'rep'], ['sphs', 'spheres'], ['swt', 'collision'], ['ars', 'ars'], ['tail', 'tail'], ['acc', 'accessories']];
    const pick = el('input'); pick.type = 'file'; pick.multiple = true;
    pick.accept = '.rsc,.rnt,.tex,.xrsc,.xrnt,.xtex,.xmod,' + KINDS.map(([k]) => '.' + k).join(',');
    const picked = el('div', 'muted', 'Pick the .rsc and its .rnt; also the .skel it was made on, its .tex textures, and any character files of your own (Ctrl+click)');
    const pickWrap = el('div'); pickWrap.append(pick, picked);
    const fileLabel = el('span', 'muted', 'Your files');
    // the rest of the character: copied from the character picked, kind by kind; a file of yours replaces the copy
    const copyLabel = el('span', 'muted', 'Copy these');
    const copyBox = el('div');
    copyBox.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px';
    const copyChecks = {};
    for (const [k, label] of KINDS) {
      const l = el('label'); const c = el('input'); c.type = 'checkbox'; c.checked = true;
      copyChecks[k] = { c, span: el('span', null, ' ' + label) };
      l.title = '.' + k;
      l.append(c, copyChecks[k].span); copyBox.append(l);
    }
    const mark = () => {
      const mine = new Set(files.map((f) => (f.name.split('.').pop() || '').toLowerCase()));
      for (const [k, label] of KINDS) {
        const yours = mine.has(k);
        copyChecks[k].c.disabled = yours;
        copyChecks[k].span.textContent = ' ' + label + (yours ? ' (yours)' : '');
      }
    };
    pick.onchange = () => { files = [...pick.files]; picked.textContent = files.map((f) => f.name).join(', ') || 'Pick the .rsc and .rnt (and your other files)'; mark(); };
    const mpBox = el('input'); mpBox.type = 'checkbox'; mpBox.checked = !!opts.mp;
    const mpLab = el('label'); mpLab.append(mpBox, el('span', null, ' Then make it a Showdown character (its roster entry, name and portraits, named as the game names them)'));
    grid.append(srcLabel, src, fileLabel, pickWrap, copyLabel, copyBox, el('span', 'muted', 'New code'), code, el('span', 'muted', 'Showdown'), mpLab);
    b.append(grid);
    const note = el('p', 'muted');
    b.append(note);
    const upd = () => {
      const own = rOwn.checked;
      fileLabel.style.display = pickWrap.style.display = copyLabel.style.display = copyBox.style.display = own ? '' : 'none';
      srcLabel.textContent = own ? 'Copy the rest from' : 'Copy from';
      note.textContent = own
        ? 'Your bundle becomes the new character\'s model (its mesh names are renamed <code>_lod_01 …). The rest of the '
          + 'character is copied from the character picked above, the kinds ticked; a file of yours of a kind (a .cr, '
          + '.skel, .dam …) is used instead of the copy. The model and the skeleton must have the same bone count. '
          + 'SWAT.DAT is rebuilt to make room (under a minute). All of its files, the .tex too, are copied to the Workbench. Not undoable with Ctrl+Z; Reset to stock removes it.'
        : 'Copies a character\'s files under a new code into SWAT.DAT: its .cr (pointing at the new code), model bundle '
          + '(<code>_lods.rsc, its meshes renamed <code>_lod_01 …), skeleton, damage, hit spots, motion, collision and '
          + 'tuning. The animation set stays the source\'s; face animation is off (the face morphs belong to the source\'s '
          + 'bundle). SWAT.DAT is rebuilt to make room (a few minutes). Not undoable with Ctrl+Z; Reset to stock removes it.';
    };
    rCopy.onchange = rOwn.onchange = upd; upd();
    b._get = () => ({ own: rOwn.checked, source: src.value, code: code.value.trim().toLowerCase(), mp: mpBox.checked,
                      copy: KINDS.map(([k]) => k).filter((k) => copyChecks[k].c.checked && !copyChecks[k].c.disabled) });
  }, [{ label: 'Cancel' }, { label: 'Add Character', cls: 'primary', run: async (b, btn) => {
    const v = b._get();
    if (!/^[a-z][a-z0-9_]{1,11}$/.test(v.code)) { toast('The new code: 2 to 12 letters, digits or _, starting with a letter', 'bad'); return true; }
    const body = { source: v.source, code: v.code };
    if (v.own) body.copy = v.copy;
    if (v.own) {
      // the PS2's .rsc + .rnt, or the Xbox's .xrsc + .xrnt (converted on the server)
      const rsc = files.find((f) => /\.x?rsc$/i.test(f.name));
      const rnt = files.find((f) => /\.x?rnt$/i.test(f.name));
      if (!rsc || !rnt) { toast('Pick your model\'s .rsc and its .rnt (both; the Xbox .xrsc + .xrnt work too)', 'bad'); return true; }
      if (/^lvl_/i.test(rsc.name)) { toast('That is a level bundle, not a character', 'bad'); return true; }
      body.rsc_b64 = await readAsB64(rsc);
      body.rnt_b64 = await readAsB64(rnt);
      body.rsc_name = rsc.name;
      const sk = files.find((f) => /\.skel$/i.test(f.name));
      if (sk) body.skel_b64 = await readAsB64(sk);
      // the rest: character files of yours (used instead of the copies) and its .tex textures (kept in the Workbench)
      body.extra = [];
      for (const f of files) if (f !== rsc && f !== rnt && f !== sk) body.extra.push({ name: f.name, data: await readAsB64(f) });
    }
    btn.disabled = true; btn.textContent = 'Rebuilding SWAT.DAT…';
    try {
      const r = await post('creature_add', body);
      showNotes(r.notes);
      toast('New character ' + v.code + ': ' + r.added.length + ' files added to SWAT.DAT' + (v.own ? ' (your model)' : '') + '; all its files are in the Workbench (' + v.code + ')', 'good');
      if (A && A.refreshLists) await A.refreshLists();
      if (A && A.refreshCreatures) await A.refreshCreatures(r.name);
      if (v.mp) setTimeout(() => showdownDialog(v.code), 50);
    } catch (e) { toast('Not added: ' + e.message, 'bad'); btn.disabled = false; btn.textContent = 'Add Character'; return true; }
  } }], 660);
}

// ------------------------------------------------------------------ Showdown: make a character playable in versus

// A character dropped into swat.dat that is not a Showdown character yet: offered at once, as a fresh entry with
// a place on the select screen already picked (front row), so one click makes it playable.
async function offerShowdown(names) {
  let r;
  try { r = await post('de_x_mp_candidates', { names }); } catch { return; }
  const codes = r.codes || [];
  if ((r.xbox || []).length)
    toast('These are Xbox files, which the PS2 game does not read: ' + r.xbox.join(', ')
      + '. Export again from Blender with Platform: PS2 (.rsc / .rnt / .mod / .tex).', 'warn');
  // a model with no character: made one first (the rest copied from a character on the same skeleton)
  for (const b of r.bundles || []) {
    const made = await characterFromBundle(b);
    if (made && !codes.includes(b.code)) codes.push(b.code);
  }
  for (const c of codes) {
    toast(c + ' is a new character: make it a Showdown character (one click, or place it first).', 'good');
    await showdownDialog(c, { auto: true });
  }
}

function characterFromBundle(b) {
  return new Promise((resolve) => {
    let done = false;
    const d = dialog('Make ' + b.code + ' a character', (box) => {
      box.append(el('p', null, b.code + '_lods.rsc is a model with no character files yet (no ' + b.code + '.cr). To play it, '
        + 'the rest of a character (its tuning, skeleton, damage, hit spots, motion ...) is copied from a character on a '
        + 'skeleton of the same size (' + (b.bones || '?') + ' bones), as Add Character does with your own .rsc; your model '
        + 'stays the look. Then it is made a Showdown character.'));
      const sel = el('select');
      for (const s of b.sources) sel.append(new Option(s, s + '.cr'));
      if (!b.sources.length) sel.append(new Option('(no character has ' + b.bones + ' bones: use Add Character)', ''));
      const row = el('div'); row.style.cssText = 'display:flex;gap:8px;align-items:center';
      row.append(el('span', 'muted', 'Copy the rest from'), sel);
      box.append(row, el('p', 'muted', 'SWAT.DAT is rebuilt to add it (a few minutes). All its files are copied to the Workbench.'));
      box._src = () => sel.value;
    }, [{ label: 'Not now' }, { label: 'Make it a character', cls: 'primary', run: async (box, btn) => {
      const src = box._src();
      if (!src) { toast('No character has a skeleton of that size: use Add Character', 'bad'); return true; }
      btn.disabled = true; btn.textContent = 'Rebuilding SWAT.DAT…';
      try {
        const r = await post('de_x_mp_make_character', { code: b.code, source: src });
        toast(b.code + ' is a character now (' + r.added.length + ' files, the rest from ' + src.replace(/\.cr$/, '') + ')', 'good');
        done = true;
        if (A && A.refreshCreatures) A.refreshCreatures();
      } catch (e) { toast('Not made: ' + e.message, 'bad'); btn.disabled = false; btn.textContent = 'Make it a character'; return true; }
    } }], 620);
    const w = setInterval(() => { if (!d.box.isConnected) { clearInterval(w); resolve(done); } }, 300);
  });
}

async function showdownDialog(code, opts = {}) {
  const cr = code || selectedCreature();
  if (!cr) return toast('Select the character first', 'bad');
  let info;
  try { info = await post('de_x_mp_info', { code: cr }); } catch (e) { return toast(e.message, 'bad'); }
  let portrait = null;
  let closedResolve;
  const closed = new Promise((res) => { closedResolve = res; });
  const dlg = dialog('Make ' + info.code + ' a Showdown character', (b) => {
    b.append(el('p', 'muted', 'The game\'s naming is filled in for you: roster code ' + info.mcode + ' (m + its code), its own '
      + info.mcode + '.cr, its name and portraits by ' + info.code + ' (mp_' + info.code + '.tex, ui_mphoto_' + info.code
      + '.tex), and its place on the select screen (hotspot_' + info.code + '). Everything goes into the Data Editor\'s '
      + 'mods folder; Build puts it on the disc.'));
    const grid = el('div');
    grid.style.cssText = 'display:grid;grid-template-columns:170px 1fr;gap:8px;align-items:center';
    const modeBox = el('div'); modeBox.style.cssText = 'display:flex;gap:14px;flex-wrap:wrap';
    const mk = (v, label, on) => {
      const l = el('label'); const r = el('input'); r.type = 'radio'; r.name = 'mpMode'; r.value = v; r.checked = on;
      l.append(r, el('span', null, ' ' + label)); modeBox.append(l); return r;
    };
    const rNew = mk('new', 'A fresh entry (all 48 stay)', opts.auto || !info.in_roster);
    const rRep = mk('replace', 'In place of a roster entry', !opts.auto && info.in_roster);
    // the name the menu shows, on one line or two stacked (the game's own: "Pig" over Josh -- a line break in it)
    const nameLines = String(info.name || '').split(/\r?\n/);
    const name = el('input'); name.value = nameLines[0] || ''; name.placeholder = 'the name the menu shows';
    const name2 = el('input'); name2.value = nameLines.slice(1).join(' '); name2.placeholder = 'second line (optional): stacked under the first';
    name.style.width = name2.style.width = '100%';
    const nameBox = el('div'); nameBox.style.cssText = 'display:flex;flex-direction:column;gap:4px';
    nameBox.append(name, name2);
    const nameValue = () => [name.value.trim(), name2.value.trim()].filter(Boolean).join('\n');
    const slot = el('select');
    for (const r of info.roster) slot.append(new Option(r.code + (r.name ? ' — ' + r.name : ''), r.code));
    slot.value = info.slot;
    const slotLab = el('span', 'muted', 'Takes the place of');
    const bounty = el('input'); bounty.type = 'number'; bounty.value = 500; bounty.min = 0; bounty.style.width = '110px';
    const bountyLab = el('span', 'muted', 'Bounty');
    const tpl = el('select');
    // every Showdown character, alphabetical; one on another skeleton is marked (its moves may not match)
    for (const t of info.templates) {
      tpl.append(new Option((t.name ? t.name.replace(/\s+/g, ' ') + ' — ' : '') + t.code
        + (t.fits === false ? '  (another skeleton: ' + (t.skeleton || '?') + ')' : ''), t.code));
    }
    if (info.template) tpl.value = info.template;
    tpl.append(new Option('The slot\'s own (may not fit this skeleton)', ''));
    tpl.value = info.template || '';
    const unl = el('input'); unl.type = 'checkbox'; unl.checked = true;
    const unlLab = el('label'); unlLab.append(unl, el('span', null, ' Unlocked from the start'));
    const pPick = el('input'); pPick.type = 'file'; pPick.accept = '.png,.tex,.xtex,.jpg';
    // the portrait: cropped square first, so the face fills the 128 x 128 portrait as the game's do
    pPick.onchange = async () => {
      const f = pPick.files[0];
      portrait = null;
      if (!f) return;
      let src = URL.createObjectURL(f);
      if (/\.x?tex$/i.test(f.name)) {
        try { src = 'data:image/png;base64,' + (await post('de_x_tex_to_png', { data: await readAsB64(f) })).png; } catch (e) { return toast(e.message, 'bad'); }
      }
      const pick = await cropDialog(src, { title: 'Fit the portrait in its texture (mp_' + info.code + '.tex)', aspect: 1,
        target: '128 x 128 portrait', keepLabel: 'Use it whole', okLabel: 'Use this crop',
        text: 'Pick the part to keep: it becomes the 128 x 128 portrait (as the game\'s, the face filling most of it). '
          + 'Drag the box, drag a corner, or use the mouse wheel.' });
      if (pick === null) { pPick.value = ''; return; }
      const im = await loadImg(src);
      if (!im) return;
      const c = document.createElement('canvas');
      const r = pick === 'keep' ? { x: 0, y: 0, w: im.width, h: im.height } : pick;
      c.width = r.w; c.height = r.h;
      c.getContext('2d').drawImage(im, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
      portrait = { name: info.code + '_portrait.png', data: c.toDataURL('image/png').split(',')[1] };
      toast('Portrait ready (' + r.w + ' x ' + r.h + ' -> 128 x 128)', 'good');
    };
    const fPick = el('input'); fPick.type = 'file'; fPick.accept = '.png,.jpg,.tex,.xtex';
    // transparency, as Convert images makes it (the portrait's own recipe first: the team square and ink)
    const alphaRow = (withRecipe) => {
      const sel = el('select');
      if (withRecipe) sel.append(new Option('mp portrait recipe (team square, ink), as the game\'s', 'recipe'));
      for (const [v, l] of ALPHA_CHOICES) if (v !== 'auto') sel.append(new Option(l, v));
      const key = el('input'); key.type = 'color'; key.value = '#000000';
      const tol = el('input'); tol.type = 'number'; tol.value = 16; tol.min = 0; tol.max = 255; tol.style.width = '56px';
      const keyBox = el('span'); keyBox.style.cssText = 'display:none;gap:4px;align-items:center;margin-left:6px';
      keyBox.append(key, el('span', 'muted', 'tolerance'), tol);
      // the game's look: the colour ramp and contrast of the shipped photos (portraits), learnt from the disc
      const tone = el('input'); tone.type = 'checkbox';
      const toneLab = el('label', 'muted'); toneLab.style.cssText = 'display:inline-flex;gap:4px;align-items:center;margin-left:6px';
      toneLab.title = 'Recolours the picture with the colour gradient of the game\'s own ' + (withRecipe ? 'portraits' : 'select screen photos')
        + ' (each brightness its sepia colour, the same contrast), so it sits with the others. Its transparency is kept.';
      toneLab.append(tone, document.createTextNode('Colour like the other ' + (withRecipe ? 'portraits' : 'photos')));
      const row = el('div'); row.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
      row.append(sel, keyBox);
      if (!withRecipe) row.append(toneLab);                    // the photo only: no recolouring of the portrait
      return { row, sel, key, tol, keyBox, tone, opt: () => ({ alpha: sel.value, key: key.value.slice(1), tolerance: +tol.value,
        tone: tone.checked ? (withRecipe ? 'portrait' : 'photo') : '' }) };
    };
    const pA = alphaRow(true), fA = alphaRow(false);
    const fAlpha = fA.sel, fKey = fA.keyBox, fKeyC = fA.key, fKeyT = fA.tol, fAlphaOpt = fA.opt;
    fA.tone.onchange = () => photoSource(false);
    pA.sel.onchange = () => { pA.keyBox.style.display = pA.sel.value === 'key' ? 'inline-flex' : 'none'; };
    grid.append(el('span', 'muted', 'Roster'), modeBox, el('span', 'muted', 'Name shown (one or two lines)'), nameBox, slotLab, slot, bountyLab, bounty,
                el('span', 'muted', 'Versus tuning from'), tpl, el('span', 'muted', ''), unlLab,
                el('span', 'muted', 'Mugshot (mp_, 128x128)'), pPick, el('span', 'muted', 'Mugshot transparency'), pA.row,
                el('span', 'muted', 'Full portrait (ui_mphoto_, upright)'), fPick, el('span', 'muted', 'Full portrait transparency'), fA.row);
    // the voice line it says on the select screen (snd_98.csv: a stream, numbered by its place in the list)
    const vMode = el('select');
    vMode.append(new Option('Keep what it has (none for a new one)', 'keep'), new Option('None', 'none'),
      new Option('Same as the versus tuning character\'s', 'template'), new Option('A stream of the game\'s', 'stream'),
      new Option('Your own (.wav 16-bit PCM, or .stm)', 'file'));
    const vStream = el('input'); vStream.placeholder = 'stream name, e.g. red_NEWmp_line'; vStream.style.width = '220px';
    const vList = el('datalist'); vList.id = 'mpVoiceList' + Date.now(); vStream.setAttribute('list', vList.id);
    const vFile = el('input'); vFile.type = 'file'; vFile.accept = '.wav,.stm';
    // the game's streams, searched and heard as in Audio > Voices & music, one picked
    const vBrowse = el('button', null, 'Browse streams…');
    vBrowse.title = 'Search the game\'s streams (by name or by what is said), listen, and pick one';
    vBrowse.onclick = async () => {
      const got = await pickStream(vStream.value.trim());
      if (got) vStream.value = got;
    };
    const vRow = el('div'); vRow.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    vRow.append(vMode, vStream, vBrowse, vList, vFile);
    const vShow = () => {
      vStream.style.display = vBrowse.style.display = vMode.value === 'stream' ? '' : 'none';
      vFile.style.display = vMode.value === 'file' ? '' : 'none';
    };
    vMode.onchange = vShow; vShow();
    post('de_x_voice_lines', {}).then((r) => {
      for (const v of r.lines || []) vList.append(new Option(v.name, v.stream));
      const mine = (r.lines || []).find((v) => v.code === info.code);
      if (mine) vMode.options[0].text = 'Keep its own (' + mine.stream + ')';
    }).catch(() => {});
    grid.append(el('span', 'muted', 'Voice line (select screen)'), vRow);
    // its in-match sounds (the .swt's CreatureSoundSet: voice, injuries, deaths ...) and footsteps (StepType)
    const sMode = el('select');
    sMode.append(new Option('Keep what it has', 'keep'), new Option('Same as…', 'same'),
      new Option('A new sound bank copied from… (its own, to change in the Sound tab)', 'new'));
    const sSrc = el('select'); sSrc.style.maxWidth = '220px';
    const sStep = el('select');
    sStep.append(new Option('Footsteps: keep', 'keep'), new Option('Footsteps: like that character', 'source'),
      new Option('Footsteps: type 1 (most characters)', '1'), new Option('Footsteps: type 3', '3'));
    const sRow = el('div'); sRow.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    const sNote = el('span', 'muted', '');
    sRow.append(sMode, sSrc, sStep, sNote);
    const sShow = () => { sSrc.style.display = sMode.value === 'keep' && sStep.value !== 'source' ? 'none' : ''; };
    sMode.onchange = sStep.onchange = sShow; sShow();
    post('de_x_sound_sets', {}).then((r) => {
      for (const x of r.sets || []) sSrc.append(new Option(`${x.name} (${x.code}) · set ${x.set}, steps ${x.step}`, x.code));
      const mine = (r.sets || []).find((x) => x.code === info.code);
      if (mine) sNote.textContent = `now: sound set ${mine.set}, footsteps ${mine.step}`;
    }).catch(() => {});
    grid.append(el('span', 'muted', 'In-match sounds'), sRow);
    b._sounds = () => ({ mode: sMode.value, source: sSrc.value, step: sStep.value });
    b.append(grid);
    b._voice = async () => {
      const m = vMode.value;
      if (m === 'stream') return { mode: m, stream: vStream.value.trim() };
      if (m === 'file') {
        const f = vFile.files[0];
        if (!f) throw new Error('pick the voice line\'s .wav or .stm');
        return { mode: m, file: { name: f.name, data: await readAsB64(f) } };
      }
      return { mode: m };
    };

    // ---- the select screen: drag the photo into place, click the face to put the pointer
    const head = el('div', 'muted', 'The select screen, drawn from the game\'s own files. Drag ' + info.code + '\'s photo left / right '
      + 'to place it; drag up / down or use the mouse wheel to size the figure in its frame; click the face to put the pointer '
      + '(the gold cross). Gold dots: everyone else\'s pointers.');
    head.style.cssText = 'margin:10px 0 4px;font-size:12px';
    const cv = el('canvas'); cv.width = 1024; cv.height = 256;
    cv.style.cssText = 'width:100%;border:1px solid var(--line);background:#222;cursor:grab;display:block';
    const ctl = el('div'); ctl.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:6px;font-size:12px';
    // The layer: the game draws the photos in the order of the select screen's list (later over earlier, back row
    // first), so the photo's place in that order decides who it covers and who covers it.
    const layer = el('input'); layer.type = 'range'; layer.min = 0; layer.max = 0; layer.step = 1; layer.style.width = '220px';
    const toBack = el('button', 'btn', 'Behind all'), toFront = el('button', 'btn', 'In front of all');
    const layLab = el('span', 'muted', '');
    const pos = el('span', 'mono', '');
    ctl.append(el('span', 'muted', 'Layer'), toBack, layer, toFront, layLab, pos);
    // Which lineup: lineup 2 is the second page of the select screen (L1 / R1 pan between them in the game; the
    // build adds that code mod by itself while a character is on lineup 2). Its background can be changed once it
    // has a character; lineup 1's any time.
    const lineRow = el('div'); lineRow.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px;font-size:12px';
    const lineSel = el('select'); lineSel.append(new Option('Lineup 1 (the game\'s)', '0'), new Option('Lineup 2 (second page: L1 / R1)', '1'));
    const bgPick = el('input'); bgPick.type = 'file'; bgPick.accept = '.png,.jpg,.jpeg,.tex'; bgPick.style.display = 'none';
    const bgBtn = el('button', 'btn', 'Change background…'), bgReset = el('button', 'btn', 'Reset background');
    const bgNote = el('span', 'muted', '');
    lineRow.append(el('span', 'muted', 'Lineup'), lineSel, bgBtn, bgReset, bgPick, bgNote);
    b.append(head, lineRow, cv, ctl);
    const st = { bg: null, stack: [], spots: [], fig: null, x: 448, fs: 0.9, fy: null, sx: 64, sy: 40, placed: false, layer: null,
                 lineup: null, info: null };
    function bgShow() {
      const ln = +lineSel.value, i = st.info || { counts: [48, 0] };
      const can = ln === 0 || i.counts[1] > 0;              // lineup 2's background: once it has a character
      bgBtn.style.display = can ? '' : 'none';
      bgReset.style.display = can && (ln === 0 ? i.bg1_changed : i.own_bg2) ? '' : 'none';
      bgNote.textContent = ln === 1 ? (i.counts[1] ? `${i.counts[1]} on lineup 2` + (i.own_bg2 ? ', its own background' : ', lineup 1\'s background')
        : 'Lineup 2 is empty: its background can be changed once a character is on it (it uses lineup 1\'s).') : '';
    }
    lineSel.onchange = () => { st.lineup = +lineSel.value; st.layer = null; loadLineup(); };
    bgBtn.onclick = () => bgPick.click();
    bgPick.onchange = async () => {
      const f = bgPick.files[0]; bgPick.value = '';
      if (!f) return;
      const data = await readAsB64(f);
      let src;
      try { src = 'data:image/png;base64,' + (await post('de_x_alpha_png', { data, alpha: { alpha: 'keep' } })).png; }
      catch (e) { return toast(f.name + ': ' + e.message, 'bad'); }
      const pick = await cropDialog(src, { title: 'Lineup ' + (+lineSel.value + 1) + ' background', aspect: 4,
        target: '1024 x 256 (two 512 x 256 halves, 24-bit)', keepLabel: 'Fit it whole', okLabel: 'Use this crop',
        text: 'Pick the part to use as the lineup\'s background: it fills the 1024 x 256 picture the characters stand in front of.' });
      if (pick === null) return;
      try {
        await post('de_x_lineup_bg', { lineup: +lineSel.value, data, crop: pick === 'keep' ? null : pick });
        toast('Lineup ' + (+lineSel.value + 1) + '\'s background is in the mods folder. Build to put it on the disc.', 'good');
      } catch (e) { return toast(e.message, 'bad'); }
      loadLineup();
    };
    bgReset.onclick = async () => {
      try { await post('de_x_lineup_bg', { lineup: +lineSel.value, reset: true }); } catch (e) { return toast(e.message, 'bad'); }
      toast(+lineSel.value ? 'Lineup 2 uses lineup 1\'s background again' : 'Lineup 1 has the game\'s background again', 'good');
      loadLineup();
    };
    const frame = document.createElement('canvas'); frame.width = 128; frame.height = 256;
    const loadImg = (src) => new Promise((res) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = src; });
    function drawFrame() {
      const g = frame.getContext('2d'); g.clearRect(0, 0, 128, 256);
      if (!st.fig) return;
      const h = 256 * st.fs, w = h * st.fig.width / st.fig.height;
      if (st.fy === null) st.fy = 256 - h;
      g.drawImage(st.fig, (128 - w) / 2, st.fy, w, h);
    }
    function draw() {
      const g = cv.getContext('2d');
      g.clearRect(0, 0, 1024, 256);
      if (st.bg) g.drawImage(st.bg, 0, 0);
      drawFrame();
      const n = st.stack.length, k = st.layer === null ? n : Math.max(0, Math.min(n, st.layer));
      for (let i = 0; i <= n; i++) {
        if (i === k) g.drawImage(frame, st.x, 0);             // its photo, at its layer
        if (i < n && st.stack[i].img) g.drawImage(st.stack[i].img, st.stack[i].x, 0);
      }
      g.fillStyle = 'rgba(255,211,107,.85)';
      for (const p of st.spots) { g.beginPath(); g.arc(p.x + p.sx, p.sy, 2.5, 0, 7); g.fill(); }
      layer.max = n; layer.value = k;
      const up = (c) => (c || '').toUpperCase();
      layLab.textContent = n ? (k === 0 ? 'behind everyone' : k === n ? 'in front of everyone'
        : `in front of ${up(st.stack[k - 1].code)}, behind ${up(st.stack[k].code)} (${k} of ${n} behind it)`) : '';
      g.strokeStyle = 'rgba(255,255,255,.6)'; g.setLineDash([4, 3]); g.strokeRect(st.x + .5, .5, 127, 255); g.setLineDash([]);
      const px = st.x + st.sx, py = st.sy;
      g.strokeStyle = '#ffd36b'; g.lineWidth = 2;
      g.beginPath(); g.moveTo(px - 8, py); g.lineTo(px + 8, py); g.moveTo(px, py - 8); g.lineTo(px, py + 8); g.stroke();
      pos.textContent = `photo at x ${Math.round(st.x)} · pointer ${Math.round(st.x + st.sx)}, ${Math.round(st.sy)}`
        + (st.placed ? '' : ' (click the face to place it)');
    }
    async function loadLineup() {
      const ex = [info.code];
      if (rRep.checked) ex.push(slot.value);
      let L;
      try {
        L = await post('de_x_mp_lineup', { exclude: ex, lineup: st.lineup || 0 });
        if (st.lineup === null) {
          // first look: the lineup it is on now (replacing: the slot's), and that one drawn
          const on = L.on || {};
          const want = on[info.code] !== undefined ? on[info.code] : (rRep.checked && on[slot.value.slice(1)] !== undefined ? on[slot.value.slice(1)] : 0);
          st.lineup = want ? 1 : 0;
          lineSel.value = String(st.lineup);
          if (st.lineup) L = await post('de_x_mp_lineup', { exclude: ex, lineup: 1 });
        }
      } catch (e) { head.textContent = 'The select screen could not be drawn: ' + e.message; return; }
      st.info = L.info || st.info;
      bgShow();
      st.bg = await loadImg('data:image/png;base64,' + L.png);
      st.stack = await Promise.all((L.stack || []).map(async (s) => ({ ...s, img: await loadImg('data:image/png;base64,' + s.png) })));
      st.spots = L.hotspots;
      // its layer: where it is drawn now, else (replacing) where the slot is, else in front of everyone
      const lay = L.layer || {};
      st.layer = lay[info.code] !== undefined ? lay[info.code]
        : (rRep.checked && lay[slot.value.slice(1)] !== undefined) ? lay[slot.value.slice(1)] : st.stack.length;
      // its own photo and pointer if it has them; replacing: start where the slot's were
      const mineCode = info.code, slotBase = slot.value.slice(1);
      if (!fPick.files[0] && L.photos[mineCode]) { st.fig = await loadImg('data:image/png;base64,' + L.photos[mineCode]); st.fs = 1; st.fy = 0; }
      // start from its own pointer if it has one, else (replacing) the slot's
      const start = (L.own || {})[mineCode] || (rRep.checked ? (L.own || {})[slotBase] : null);
      if (start && !st.placed) { st.x = start.x; st.sx = start.sx; st.sy = start.sy; st.placed = true; }
      if (!st.placed && rNew.checked) {
        // a first place, as the front row stands: the right end, the figure ~175 px tall with its head ~100 px down,
        // the pointer on the head -- ready to use, and draggable
        st.x = 880; st.sx = 64; st.sy = 114; st.placed = true;
        if (st.fig) { st.fs = 175 / 256; st.fy = 100; }
      }
      if (rRep.checked && !st.fig && L.photos[slotBase]) {
        st.fig = await loadImg('data:image/png;base64,' + L.photos[slotBase]); st.fs = 1; st.fy = 0;
      }
      draw();
    }
    let photoFile = null, photoCrop = null;
    async function photoSource(askCrop) {
      const f = photoFile;
      if (!f) return;
      let src;
      try {
        src = 'data:image/png;base64,' + (await post('de_x_alpha_png', { data: await readAsB64(f), alpha: fAlphaOpt() })).png;
      } catch (e) { return toast(f.name + ': ' + e.message, 'bad'); }
      let im = await loadImg(src);
      if (!im) return toast(f.name + ' would not open', 'bad');
      // a stored (sideways) ui_mphoto texture is turned upright first
      if (im.width > im.height && /\.x?tex$/i.test(f.name)) {
        const c = document.createElement('canvas'); c.width = im.height; c.height = im.width;
        const g = c.getContext('2d'); g.translate(c.width, 0); g.rotate(Math.PI / 2); g.drawImage(im, 0, 0);
        src = c.toDataURL('image/png');
        im = await loadImg(src);
      }
      if (askCrop) {
        // the crop window, locked to the photo's frame (128 x 256): what is kept fills the frame exactly
        const pick = await cropDialog(src, { title: 'Fit the photo in its texture (ui_mphoto_' + info.code + '.tex)', aspect: 0.5,
          target: '128 x 256 photo (stored on its side, 256 x 128)', keepLabel: 'Fit it whole', okLabel: 'Use this crop',
          text: 'Pick the part to keep: it fills the 128 x 256 photo frame, as it stands on the select screen (the dashed '
            + 'box). Leave room above the head for the back rows to show, as the game\'s photos do. "Fit it whole" keeps '
            + 'the picture as it is and lets you size it in the frame.' });
        if (pick === null) { fPick.value = ''; photoFile = null; return; }
        photoCrop = pick;
      }
      if (photoCrop === 'keep' || !photoCrop) {
        st.fig = im; if (askCrop) { st.fs = 0.9; st.fy = null; }
      } else {
        const c = document.createElement('canvas'); c.width = photoCrop.w; c.height = photoCrop.h;
        c.getContext('2d').drawImage(im, photoCrop.x, photoCrop.y, photoCrop.w, photoCrop.h, 0, 0, photoCrop.w, photoCrop.h);
        st.fig = c; if (askCrop) { st.fs = 1; st.fy = 0; }
      }
      draw();
    }
    fPick.onchange = () => { photoFile = fPick.files[0] || null; photoCrop = null; photoSource(true); };
    fAlpha.onchange = () => { fKey.style.display = fAlpha.value === 'key' ? 'inline-flex' : 'none'; photoSource(false); };
    fKeyC.onchange = fKeyT.onchange = () => photoSource(false);
    let drag = null;
    const at = (e) => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * 1024 / r.width, (e.clientY - r.top) * 256 / r.height]; };
    cv.onmousedown = (e) => {
      const [mx, my] = at(e);
      drag = { mx, my, x: st.x, fy: st.fy === null ? 0 : st.fy, moved: false };
      const move = (ev) => {
        const [nx, ny] = at(ev);
        if (Math.abs(nx - drag.mx) + Math.abs(ny - drag.my) > 3) drag.moved = true;
        if (!drag.moved) return;
        st.x = Math.max(-64, Math.min(1024 - 64, drag.x + nx - drag.mx));
        if (st.fig) st.fy = drag.fy + ny - drag.my;
        draw();
      };
      const up = (ev) => {
        document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
        if (!drag.moved) {
          // a click: the pointer goes here (kept inside its photo)
          const [cx, cy] = at(ev);
          st.sx = Math.max(0, Math.min(127, cx - st.x)); st.sy = Math.max(0, Math.min(255, cy)); st.placed = true;
        }
        drag = null; draw();
      };
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    };
    cv.onwheel = (e) => {
      e.preventDefault(); if (!st.fig) return;
      const k = e.deltaY < 0 ? 1.04 : 1 / 1.04, bottom = (st.fy === null ? 0 : st.fy) + 256 * st.fs;
      st.fs = Math.max(0.2, Math.min(1.6, st.fs * k)); st.fy = bottom - 256 * st.fs; draw();
    };
    layer.oninput = () => { st.layer = +layer.value; draw(); };
    toBack.onclick = () => { st.layer = 0; draw(); };
    toFront.onclick = () => { st.layer = st.stack.length; draw(); };
    const upd = () => {
      const rep = rRep.checked;
      slotLab.style.display = slot.style.display = rep ? '' : 'none';
      bountyLab.style.display = bounty.style.display = rep ? 'none' : '';
      loadLineup();
    };
    rNew.onchange = rRep.onchange = upd; slot.onchange = () => { st.placed = false; loadLineup(); };
    upd();
    b._pAlpha = pA.opt;
    b._get = () => {
      drawFrame();
      return { code: info.code, name: nameValue(), slot: slot.value, template: tpl.value || null, unlocked: unl.checked,
               mode: rNew.checked ? 'new' : 'replace', bounty: bounty.value,
               layer: st.layer === null ? st.stack.length : st.layer, on_lineup: st.lineup || 0,
               lineup: (st.placed || rNew.checked) ? { x: Math.round(st.x), sx: Math.round(st.sx), sy: Math.round(st.sy) } : null,
               photo_upright: st.fig ? frame.toDataURL('image/png').split(',')[1] : null, placed: st.placed };
    };
  }, [{ label: opts.auto ? 'Not now' : 'Cancel' },
      // a Showdown character already: its stats, pictures, looks and place in the Showdown editor
      ...(info.in_roster ? [{ label: 'Edit…', run: () => { showdownEditor(info.code); } }] : []),
      { label: 'Add to Showdown', cls: 'primary', run: async (b, btn) => {
    const v = b._get();
    if (v.mode === 'new' && !v.placed) { toast('Click ' + info.code + '\'s face on the select screen to put the pointer', 'bad'); return true; }
    let voice;
    try { voice = await b._voice(); } catch (e) { toast(e.message, 'bad'); return true; }
    btn.disabled = true; btn.textContent = 'Writing…';
    try {
      const r = await post('de_x_mp_add', { ...v, voice, portrait: portrait ? { ...portrait, alpha: b._pAlpha() } : null });
      toast(`${r.mcode} "${r.name}" is in the Showdown roster ${v.mode === 'new' ? 'as a new entry' : 'in place of ' + r.slot}, with its place on the select screen; ${r.wrote.length} files in the mods folder. Build to put it on the disc.`, 'good');
      for (const n of r.notes) toast(n, 'warn');
      const snd = b._sounds();
      if (snd.mode !== 'keep' || snd.step !== 'keep') {
        try {
          const s = await post('de_x_mp_sounds', { code: info.code, ...snd });
          toast(`In-match sounds: sound set ${s.set}${snd.mode === 'new' ? ' (its own bank: change its sounds in the Sound tab)' : ''}, footsteps type ${s.step}.`, 'good');
        } catch (e) { toast('Its sounds were not changed: ' + e.message, 'bad'); }
      }
      refresh();
    } catch (e) { toast('Not added: ' + e.message, 'bad'); btn.disabled = false; btn.textContent = 'Add to Showdown'; return true; }
  } }], 1100);
  const watch = setInterval(() => { if (!dlg.box.isConnected) { clearInterval(watch); closedResolve(); } }, 300);
  return closed;
}

// Creatures > Replace All Characters: every humanoid character made a copy of one character -- every file of it (its
// .cr, model, skeleton, hit spots, damage ...), the way Add Character copies one (dataeditor.replace_all_characters).
// Written to the mods folder: the next Build puts it in the PS2 game, Build (Xbox) in the Xbox game.
async function replaceAllCharactersDialog() {
  let hum;
  try { hum = await post('de_x_humanoids', {}); } catch (e) { return toast(e.message, 'bad'); }
  const codes = (hum.characters || []).slice().sort();
  if (!codes.length) return toast('No humanoid characters found', 'bad');
  const sel0 = (selectedCreature() || '').toLowerCase().replace(/\.cr$/, '');
  dialog('Replace All Characters', (b, close) => {
    const p = el('p', 'muted', 'Every humanoid character becomes a copy of the character you pick. Each one plays its model, '
      + 'face and skeleton (its .cr points at them even with .cr unticked), wears its skins and Showdown pictures, and '
      + 'takes the other files you tick. Animals and characters rigged differently are left as they are. It goes in the '
      + 'mods folder (the files of an earlier Replace All are taken out first): Build puts it in the game.');
    const row = el('div'); row.style.cssText = 'display:flex;gap:8px;align-items:center;margin:8px 0';
    const pick = el('select');
    for (const c of codes) pick.append(new Option(c, c));
    pick.value = codes.includes(sel0) ? sel0 : (codes.includes('pgj') ? 'pgj' : codes[0]);
    row.append(el('span', null, 'Copy'), pick, el('span', null, 'over every humanoid character'));
    // which of its files: every kind ticked to start with; an unticked kind stays each character's own
    const kindsBox = el('div');
    kindsBox.style.cssText = 'display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 14px;font-size:12px;margin:4px 0 8px';
    const boxes = [];
    for (const k of hum.kinds || []) {
      const lab = el('label'); lab.style.cssText = 'display:flex;gap:6px;align-items:center;cursor:pointer';
      const cb = el('input'); cb.type = 'checkbox'; cb.checked = true; cb.dataset.key = k.key;
      lab.append(cb, el('span', null, k.label));
      kindsBox.append(lab);
      boxes.push(cb);
    }
    const kinds = () => boxes.filter((c) => c.checked).map((c) => c.dataset.key);
    const out = el('div', 'muted'); out.style.cssText = 'min-height:40px;font-size:12px;white-space:pre-wrap';
    const go = el('button', 'primary', 'Replace all');
    const cancel = el('button', 'btn', 'Cancel');
    const acts = el('div'); acts.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:8px';
    acts.append(cancel, go);
    b.append(p, row, el('div', 'muted', 'Files to copy from it:'), kindsBox, out, acts);
    const check = async () => {
      out.textContent = 'Checking…';
      go.disabled = true;
      try {
        if (!kinds().length) { out.textContent = 'Tick at least one kind of file to copy.'; return; }
        const d = await post('de_x_replace_all', { source: pick.value, dry: true, kinds: kinds() });
        if (d.ok === false) { out.textContent = d.error_text; return; }
        out.textContent = `${d.characters} characters become copies of ${d.source} (${d.files} files; it has: ${d.kinds.join(', ')}): `
          + d.codes.slice(0, 30).join(', ') + (d.codes.length > 30 ? ', …' : '')
          + '\nYour own character files in the mods folder (swat.dat) stay yours.';
        go.disabled = false;
      } catch (e) { out.textContent = e.message; }
    };
    pick.onchange = check;
    for (const c of boxes) c.onchange = check;
    cancel.onclick = () => close && close();
    go.onclick = async () => {
      go.disabled = true;
      out.textContent = 'Writing the files…';
      try {
        const r = await post('de_x_replace_all', { source: pick.value, kinds: kinds() });
        if (r.ok === false) { out.textContent = r.error_text; go.disabled = false; return; }
        toast(`${r.characters} characters are copies of ${r.source} in the mods folder` + (r.removed ? ` (${r.removed} files of an earlier Replace All taken out)` : '') + '. Build to put it in the game.', 'good');
        if (close) close();
        if (window.rdrRefreshDE) window.rdrRefreshDE();
      } catch (e) { out.textContent = e.message; go.disabled = false; }
    };
    check();
  }, []);
}

// A brand-new character made for Showdown: Add Character (its model, character files and .tex of your own, or copied),
// then this dialog for its roster entry, portraits and place on the select screen.
function newShowdownCharacter() { addCharacterDialog({ mp: true }); }

// The Showdown editor: the character select as it is. Click a character's photo to pick them; drag to move the photo,
// click inside it to put the pointer on the face; layer, lineup (lineup 2: the second page, L1 / R1 in the game) and
// either lineup's background; and the character's stats -- health, dodges, weapons, deadeye, special moves, bot and
// every other value of its m<code>.swt. Save writes into the mods folder; Build puts it on the disc.
async function showdownEditor(startCode) {
  let info;
  try { info = await post('de_x_lineup_info', {}); } catch (e) { return toast('The select screen could not be read: ' + e.message, 'bad'); }
  const loadImg = (src) => new Promise((res) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = src; });
  dialog('Showdown editor — the character select', (b) => {
    const top = el('div'); top.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px';
    const viewSel = el('select'); viewSel.append(new Option('Lineup 1', '0'), new Option('Lineup 2 (second page: L1 / R1)', '1'));
    const bgPick = el('input'); bgPick.type = 'file'; bgPick.accept = '.png,.jpg,.jpeg,.tex'; bgPick.style.display = 'none';
    const bgBtn = el('button', 'btn', 'Change background…'), bgReset = el('button', 'btn', 'Reset background');
    const who = el('b', null, 'Click a character\'s photo');
    top.append(el('span', 'muted', 'Showing'), viewSel, bgBtn, bgReset, bgPick, el('span', 'muted', '·'), who);
    const cv = el('canvas'); cv.width = 1024; cv.height = 256;
    cv.style.cssText = 'width:100%;border:1px solid var(--line);background:#222;cursor:pointer;display:block;margin-top:6px';
    const ctl = el('div'); ctl.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:6px;font-size:12px';
    const layer = el('input'); layer.type = 'range'; layer.min = 0; layer.max = 0; layer.step = 1; layer.style.width = '200px';
    const toBack = el('button', 'btn', 'Behind all'), toFront = el('button', 'btn', 'In front of all');
    const onSel = el('select'); onSel.append(new Option('on lineup 1', '0'), new Option('on lineup 2 (L1 / R1)', '1'));
    const layLab = el('span', 'muted', ''), pos = el('span', 'mono', '');
    ctl.append(el('span', 'muted', 'Layer'), toBack, layer, toFront, layLab, onSel, pos);
    const stats = el('div'); stats.style.cssText = 'margin-top:10px;border-top:1px solid var(--line);padding-top:8px;font-size:12px';
    b.append(top, cv, ctl, stats);
    const st = { view: 0, code: null, lineup: 0, bg: null, stack: [], spots: [], fig: null, x: 0, sx: 64, sy: 100, layer: 0,
                 dirty: false, all: [] };
    ctl.style.visibility = 'hidden';
    function bgShow() {
      const ln = st.view;
      const can = ln === 0 || info.counts[1] > 0;
      bgBtn.style.display = can ? '' : 'none';
      bgReset.style.display = can && (ln === 0 ? info.bg1_changed : info.own_bg2) ? '' : 'none';
    }
    const nameOf = (c) => ((info.roster || []).find((r) => r.code === c) || { name: (c || '').toUpperCase() }).name;
    function draw() {
      const g = cv.getContext('2d');
      g.clearRect(0, 0, 1024, 256);
      if (st.bg) g.drawImage(st.bg, 0, 0);
      const n = st.stack.length, k = Math.max(0, Math.min(n, st.layer));
      for (let i = 0; i <= n; i++) {
        if (st.code && i === k && st.fig && st.lineup === st.view) g.drawImage(st.fig, st.x, 0, 128, 256);
        if (i < n && st.stack[i].img) g.drawImage(st.stack[i].img, st.stack[i].x, 0);
      }
      g.fillStyle = 'rgba(255,211,107,.85)';
      for (const p of st.spots) { g.beginPath(); g.arc(p.x + p.sx, p.sy, 2.5, 0, 7); g.fill(); }
      if (st.code && st.lineup === st.view) {
        g.strokeStyle = 'rgba(255,255,255,.7)'; g.setLineDash([4, 3]); g.strokeRect(st.x + .5, .5, 127, 255); g.setLineDash([]);
        const px = st.x + st.sx, py = st.sy;
        g.strokeStyle = '#ffd36b'; g.lineWidth = 2;
        g.beginPath(); g.moveTo(px - 8, py); g.lineTo(px + 8, py); g.moveTo(px, py - 8); g.lineTo(px, py + 8); g.stroke();
      }
      layer.max = n; layer.value = k;
      const up = (c) => (c || '').toUpperCase();
      layLab.textContent = n ? (k === 0 ? 'behind everyone' : k === n ? 'in front of everyone'
        : `in front of ${up(st.stack[k - 1].code)}, behind ${up(st.stack[k].code)}`) : '';
      pos.textContent = st.code ? `photo x ${Math.round(st.x)} · pointer ${Math.round(st.x + st.sx)}, ${Math.round(st.sy)}`
        + (st.dirty ? ' · place not saved' : '') : '';
    }
    async function load(keepPlace) {
      let L;
      try { L = await post('de_x_mp_lineup', { exclude: st.code ? [st.code] : [], lineup: st.view }); }
      catch (e) { who.textContent = 'The select screen could not be drawn: ' + e.message; return; }
      info = L.info || info;
      bgShow();
      st.bg = await loadImg('data:image/png;base64,' + L.png);
      st.stack = await Promise.all((L.stack || []).map(async (s) => ({ ...s, img: await loadImg('data:image/png;base64,' + s.png) })));
      st.spots = L.hotspots;
      if (st.code) {
        st.fig = L.photos[st.code] ? await loadImg('data:image/png;base64,' + L.photos[st.code]) : null;
        if (!keepPlace) {
          const own = (L.own || {})[st.code];
          if (own) { st.x = own.x; st.sx = own.sx; st.sy = own.sy; }
          st.layer = (L.layer || {})[st.code] !== undefined && (L.on || {})[st.code] === st.view ? L.layer[st.code] : st.stack.length;
          st.dirty = false;
        } else if (st.layer > st.stack.length) st.layer = st.stack.length;
      }
      // what can be clicked: everyone drawn (the picked one included), topmost first, with their pixels to test
      restack();
    }
    function restack() {
      st.all = st.stack.map((s) => ({ code: s.code, x: s.x, img: s.img }));
      if (st.code && st.fig && st.lineup === st.view) st.all.splice(Math.min(st.layer, st.all.length), 0, { code: st.code, x: st.x, img: st.fig, mine: true });
      draw();
    }
    const probe = document.createElement('canvas'); probe.width = 128; probe.height = 256;
    function hit(px, py) {                                     // the topmost photo with a visible pixel there
      for (let i = st.all.length - 1; i >= 0; i--) {
        const s = st.all[i], lx = Math.floor(px - (s.mine ? st.x : s.x)), ly = Math.floor(py);
        if (lx < 0 || lx >= 128 || ly < 0 || ly >= 256 || !s.img) continue;
        const g = probe.getContext('2d'); g.clearRect(0, 0, 128, 256); g.drawImage(s.img, 0, 0, 128, 256);
        if (g.getImageData(lx, ly, 1, 1).data[3] > 40) return s.code;
      }
      return null;
    }
    if (startCode) setTimeout(() => pick(startCode), 0);      // opened from Add to Showdown's Edit
    async function pick(code) {
      if (st.dirty && st.code && code !== st.code && !confirm('Leave ' + nameOf(st.code) + '\'s place unsaved?')) return;
      st.code = code;
      const r = (info.roster || []).find((x) => x.code === code);
      st.lineup = r ? r.lineup : st.view; onSel.value = String(st.lineup);
      who.textContent = nameOf(code) + ' (' + code + ')';
      ctl.style.visibility = '';
      await load(false);
      loadStats();
    }
    // ---- stats
    let S = null;
    const fields = {};
    async function loadStats() {
      stats.innerHTML = '';
      if (!st.code) return;
      try { S = await post('de_x_creature_stats', { code: st.code }); }
      catch (e) { stats.append(el('span', 'muted', 'Its stats could not be read: ' + e.message)); return; }
      for (const k of Object.keys(fields)) delete fields[k];
      const grid = el('div'); grid.style.cssText = 'display:grid;grid-template-columns:150px 1fr 150px 1fr;gap:6px 10px;align-items:center';
      const sel = (opts, val) => { const s = el('select'); for (const o of opts) s.append(new Option(o, o)); if (val && !opts.includes(val)) s.append(new Option(val, val)); s.value = val || ''; return s; };
      const num = (v) => { const i = el('input'); i.type = 'number'; i.step = 'any'; i.value = v; i.style.width = '110px'; return i; };
      const chk = (v) => { const c = el('input'); c.type = 'checkbox'; c.checked = String(v) === '1'; return c; };
      fields.health = num(S.health);
      fields.w1 = sel(['', ...S.choices.weapons], S.weapons[0] || '');
      fields.w2 = sel(['', ...S.choices.weapons], S.weapons[1] || '');
      fields.brain = sel(S.choices.brains, S.brain);
      const sw = S.swt;
      const swt = {};
      for (const k of ['MRKDodge', 'PGJDodge', 'BDGDodge']) if (sw[k] !== undefined) swt[k] = chk(sw[k]);
      for (const k of ['NormalSpecialMove', 'MeleeSpecialMove']) if (sw[k] !== undefined) swt[k] = sel(S.choices.moves, sw[k]);
      for (const k of ['DodgeTime', 'PunchDamage', 'KickDamage', 'DiveDamage', 'ThrowDamage']) if (sw[k] !== undefined) swt[k] = num(sw[k]);
      fields.swt = swt;
      const lab = (t, tip) => { const s = el('span', 'muted', t); if (tip) s.title = tip; return s; };
      grid.append(lab('Health', 'm' + st.code + '.cr MaxHitPoints'), fields.health, lab('Bot brain', 'AIBrainTable'), fields.brain,
                  lab('Weapon 1', 'CharWeaponTable (and its .ars)'), fields.w1, lab('Weapon 2 (optional)'), fields.w2,
                  lab('Special move (deadeye)', '.swt NormalSpecialMove: every spmove the game has'), swt.NormalSpecialMove || el('span'),
                  lab('Melee special', '.swt MeleeSpecialMove: every spmove the game has'), swt.MeleeSpecialMove || el('span'),
                  lab('Dodge time', '.swt DodgeTime'), swt.DodgeTime || el('span'));
      const dodges = el('div'); dodges.style.cssText = 'display:flex;gap:14px;align-items:center';
      for (const k of ['MRKDodge', 'PGJDodge', 'BDGDodge']) if (swt[k]) { const l = el('label'); l.style.cssText = 'display:inline-flex;gap:4px;align-items:center'; l.append(swt[k], document.createTextNode(k)); dodges.append(l); }
      grid.append(lab('Dodges', 'Mr Kelly\'s, Pig Josh\'s and Dan\'s dodge styles (.swt)'), dodges);
      for (const k of ['PunchDamage', 'KickDamage', 'DiveDamage', 'ThrowDamage']) if (swt[k]) grid.append(lab(k), swt[k]);
      stats.append(el('b', null, 'Stats'), grid);
      // every other value of the .swt
      const more = el('details'); more.style.marginTop = '8px';
      more.append(el('summary', 'muted', `All ${Object.keys(sw).length} values of m${st.code}.swt`));
      const g2 = el('div'); g2.style.cssText = 'display:grid;grid-template-columns:repeat(3, 190px 110px);gap:4px 8px;max-height:220px;overflow:auto;margin-top:6px';
      for (const [k, v] of Object.entries(sw)) {
        if (swt[k]) continue;
        const i = el('input'); i.value = v; i.style.width = '100px'; swt[k] = i;
        g2.append(lab(k), i);
      }
      more.append(g2);
      stats.append(more);
      stats.append(picturesBox(st.code));
      const looks = el('div'); looks.style.cssText = 'margin-top:12px;border-top:1px solid var(--line);padding-top:8px';
      stats.append(looks);
      loadLooks(looks);
    }
    // ---- its pictures: the select screen photo (ui_mphoto_) and the portrait (mp_), which the end screen's mugshot
    // is made from at the next Build
    function picturesBox(code) {
      const box = el('div'); box.style.cssText = 'margin-top:12px;border-top:1px solid var(--line);padding-top:8px';
      box.append(el('b', null, 'Pictures'));
      const row = (label, kind, tip, withTone) => {
        const r = el('div'); r.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px;font-size:12px';
        const pickIn = el('input'); pickIn.type = 'file'; pickIn.accept = '.png,.jpg,.jpeg,.tex,.xtex'; pickIn.style.display = 'none';
        const btn = el('button', 'btn', 'Change ' + label + '…'); btn.title = tip;
        const alpha = el('select');
        if (kind === 'portrait') alpha.append(new Option('mp portrait recipe (team square, ink), as the game\'s', 'recipe'));
        for (const [v, l] of ALPHA_CHOICES) if (v !== 'auto') alpha.append(new Option(l, v));
        const tone = el('input'); tone.type = 'checkbox';
        const toneLab = el('label', 'muted'); toneLab.style.cssText = 'display:inline-flex;gap:4px;align-items:center';
        toneLab.append(tone, document.createTextNode('Colour like the other photos'));
        btn.onclick = () => pickIn.click();
        const savePlaced = async (png) => {
          await post('de_x_mp_picture', { code, kind: 'photo', file: { name: code + '_photo.png', data: png, upright: true } });
          toast('Full portrait changed. Build to put it in the game.', 'good');
          load(true);
        };
        let adjust = null;
        if (kind === 'photo') {
          adjust = el('button', 'btn', 'Adjust in its frame…');
          adjust.title = 'Drag the full portrait it has now into place in its frame, and size it';
          adjust.onclick = async () => {
            if (!st.fig) return toast('It has no full portrait to adjust yet', 'bad');
            const png = await placePhoto(st.fig.src, 'Adjust ' + code + '\'s full portrait');
            if (png) { try { await savePlaced(png); } catch (e) { toast(e.message, 'bad'); } }
          };
        }
        pickIn.onchange = async () => {
          const f = pickIn.files[0]; pickIn.value = '';
          if (!f) return;
          if (kind === 'photo') {
            // its transparency (and colouring) first, upright, then placed in the frame by hand
            try {
              let src = 'data:image/png;base64,' + (await post('de_x_alpha_png', { data: await readAsB64(f),
                alpha: { alpha: alpha.value, tone: withTone && tone.checked ? 'photo' : '' } })).png;
              if (/\.x?tex$/i.test(f.name)) {
                const im = await new Promise((r) => { const i = new Image(); i.onload = () => r(i); i.onerror = () => r(null); i.src = src; });
                if (im && im.width > im.height) {                // a stored (sideways) ui_mphoto: upright first
                  const c = document.createElement('canvas'); c.width = im.height; c.height = im.width;
                  const g = c.getContext('2d'); g.translate(c.width, 0); g.rotate(Math.PI / 2); g.drawImage(im, 0, 0);
                  src = c.toDataURL('image/png');
                }
              }
              const png = await placePhoto(src, 'Place ' + code + '\'s full portrait in its frame');
              if (png) await savePlaced(png);
            } catch (e) { toast(e.message, 'bad'); }
            return;
          }
          try {
            await post('de_x_mp_picture', { code, kind, file: { name: f.name, data: await readAsB64(f),
              alpha: { alpha: alpha.value, tone: withTone && tone.checked ? 'photo' : '' } } });
            toast(label[0].toUpperCase() + label.slice(1) + ' changed. Build to put it in the game'
              + (kind === 'portrait' ? ' (the end screen\'s mugshot follows it).' : '.'), 'good');
            if (kind === 'photo') load(true);
          } catch (e) { toast(e.message, 'bad'); }
        };
        r.append(el('span', 'muted', label[0].toUpperCase() + label.slice(1)), btn);
        if (adjust) r.append(adjust);
        r.append(pickIn, el('span', 'muted', 'transparency'), alpha);
        if (withTone) r.append(toneLab);
        return r;
      };
      box.append(row('full portrait', 'photo', 'ui_mphoto_' + code + '.tex: the full-body photo on the select screen (upright, 128 x 256)', true),
                 row('mugshot', 'portrait', 'mp_' + code + '.tex: the 128 x 128 portrait; the end-of-match screen\'s mugshot is made from it at the next Build', false));
      return box;
    }
    // ---- alternate looks for players 2-4 (<code>02.tex .. <code>04.tex: the game draws one in place of the model's first texture)
    async function loadLooks(box) {
      box.innerHTML = '';
      const code = st.code;
      let A;
      try { A = await post('de_x_alt_looks', { code }); }
      catch (e) { box.append(el('b', null, 'Alternate looks'), el('div', 'muted', 'Not available: ' + e.message)); return; }
      if (code !== st.code) return;
      const head = el('div'); head.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap';
      head.append(el('b', null, 'Alternate looks (players 2–4)'));
      const tip = el('div', 'muted', 'In a match, players 2, 3 and 4 (bots too) who pick this character can wear their own version of its texture ('
        + code + '02 / 03 / 04.tex). The game draws the WHOLE model with that one texture, so this is for a model with a single texture, like the game’s own. '
        + 'With none, they look like player 1.');
      tip.style.cssText = 'font-size:11px;margin:4px 0 6px';
      box.append(head, tip);
      // texture sizes on the disc (the build shrinks; the mods folder keeps full size, so unticking restores them)
      try {
        const O = (await post('de_x_sd_options', { code })).options;
        const opt = el('div'); opt.style.cssText = 'display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin:2px 0 8px';
        const mk = (key, text, tipText) => {
          const l = el('label'); l.style.cssText = 'display:inline-flex;gap:5px;align-items:center'; l.title = tipText;
          const c = el('input'); c.type = 'checkbox'; c.checked = !!O[key];
          c.onchange = async () => {
            try { await post('de_x_sd_options', { code, set: true, [key]: c.checked }); toast('Saved. Build to put it on the disc.', 'good'); }
            catch (e) { c.checked = !c.checked; toast(e.message, 'bad'); }
          };
          l.append(c, document.createTextNode(text));
          return l;
        };
        opt.append(
          mk('own_small', 'Shrink its own textures to 128 x 128 on the disc', 'Saves video memory (the PS2 has 4 MB): safer with several custom characters in a match. The mods folder keeps them full size; untick to put full-size textures back at the next build.'),
          mk('alt_small', 'Shrink players 2\u20134 alternates to 128 x 128', 'Each changed texture is kept in video memory for the match; full size for three players can run past it (the screen fills with noise). Untick for full-size alternates at the next build.'));
        box.append(opt);
      } catch (e) { /* older back end: no options */ }
      const T = A.textures || [];
      if (!T.length) { box.append(el('span', 'muted', 'Its model has no textures.')); return; }
      if (A.multi) {
        // several textures: players 2-4 each get their own version of every one (<code>0N_<k>.tex, the alttex code mod)
        tip.textContent = 'In a match, players 2, 3 and 4 (bots too) who pick this character can wear their own version of each of its textures ('
          + code + '02_0.tex …). Change any of them; the rest stay as player 1’s. Only changed ones are loaded (the PS2 has 4 MB of video memory). The build adds the code mod that loads them.';
        const imgs = await Promise.all(T.map((t) => loadImg('data:image/png;base64,' + t.png)));
        const grid = el('div');
        grid.style.cssText = `display:grid;grid-template-columns:70px repeat(${T.length}, 104px);gap:6px;align-items:start;overflow-x:auto`;
        const cellCanvas = (img, filter) => {
          const c = el('canvas'); c.width = 96; c.height = 96; c.style.cssText = 'width:96px;height:96px;border:1px solid var(--line);background:#333';
          const g = c.getContext('2d'); g.filter = filter || 'none'; if (img) g.drawImage(img, 0, 0, 96, 96); return c;
        };
        const send = async (n, body, what) => {
          try { await post('de_x_alt_looks_set', { code, pics: { [n]: body } }); toast(what + ' is in the mods folder. Build to put it on the disc.', 'good'); }
          catch (e) { toast(e.message, 'bad'); }
          loadLooks(box);
        };
        const filtered = (img, flt) => {
          const full = document.createElement('canvas'); full.width = img.naturalWidth; full.height = img.naturalHeight;
          const g = full.getContext('2d'); g.filter = flt; g.drawImage(img, 0, 0); return full.toDataURL('image/png').split(',')[1];
        };
        const sliders = (n, onLive, onUse, useLabel) => {
          const box2 = el('div'); box2.style.cssText = 'display:none;flex-direction:column;gap:2px;font-size:11px';
          const rng = (min, max, v) => { const r = el('input'); r.type = 'range'; r.min = min; r.max = max; r.value = v; r.style.width = '96px'; return r; };
          const hue = rng(-180, 180, n === 2 ? 120 : n === 3 ? -120 : 60), sat = rng(0, 200, 100), lig = rng(30, 170, 100);
          const use = el('button', 'btn', useLabel);
          box2.append(el('span', 'muted', 'Hue'), hue, el('span', 'muted', 'Saturation'), sat, el('span', 'muted', 'Brightness'), lig, use);
          const flt = () => `hue-rotate(${hue.value}deg) saturate(${sat.value / 100}) brightness(${lig.value / 100})`;
          hue.oninput = sat.oninput = lig.oninput = () => onLive(flt());
          use.onclick = () => onUse(flt());
          return box2;
        };
        // header row: the model's own (player 1), each savable as a PNG to paint on
        grid.append(el('b', null, 'Player 1'));
        T.forEach((t, k) => {
          const c = el('div'); c.style.cssText = 'display:flex;flex-direction:column;gap:3px;font-size:11px';
          const a = el('a', null, t.name.replace(/\.png$/i, '')); a.href = 'data:image/png;base64,' + t.png;
          a.download = code + '_' + t.name.replace(/\.png$/i, '') + '.png'; a.title = 'Save it as a PNG to paint on';
          c.append(cellCanvas(imgs[k]), a);
          grid.append(c);
        });
        for (const n of [2, 3, 4]) {
          const row = A.alts[n] || {};
          const lab = el('div'); lab.style.cssText = 'display:flex;flex-direction:column;gap:4px';
          const any = Object.values(row).some((v) => v && v.own);
          const allT = el('button', 'btn', 'Recolour all…'), reset = el('button', 'btn', 'Reset');
          reset.style.display = any ? '' : 'none';
          lab.append(el('b', null, 'Player ' + n), allT, reset);
          grid.append(lab);
          const canv = [];
          T.forEach((t, k) => {
            const cur = row[k];
            const c = el('div'); c.style.cssText = 'display:flex;flex-direction:column;gap:3px;font-size:11px';
            let shown = cur && cur.own ? null : imgs[k];
            const cv = cellCanvas(shown, cur && cur.own ? '' : 'grayscale(1) opacity(.45)');
            if (cur && cur.own) loadImg('data:image/png;base64,' + cur.png).then((im) => { const g = cv.getContext('2d'); g.clearRect(0, 0, 96, 96); g.drawImage(im, 0, 0, 96, 96); });
            canv.push(cv);
            const pickF = el('input'); pickF.type = 'file'; pickF.accept = '.png,.jpg,.jpeg,.tex'; pickF.style.display = 'none';
            const bP = el('button', 'btn', 'Picture…'), bT = el('button', 'btn', 'Recolour…'), bR = el('button', 'btn', 'Reset');
            for (const b2 of [bP, bT, bR]) b2.style.cssText = 'font-size:11px;padding:1px 4px';
            bR.style.display = cur && cur.own ? '' : 'none';
            const paint = (flt) => { const g = cv.getContext('2d'); g.clearRect(0, 0, 96, 96); g.filter = flt; g.drawImage(imgs[k], 0, 0, 96, 96); g.filter = 'none'; };
            const sl = sliders(n, paint, (flt) => send(n, { [k]: filtered(imgs[k], flt) }, `Player ${n}’s ${t.name}`), 'Use');
            bT.onclick = () => { sl.style.display = sl.style.display === 'none' ? 'flex' : 'none'; };
            bP.onclick = () => pickF.click();
            pickF.onchange = async () => { const f = pickF.files[0]; pickF.value = ''; if (f) send(n, { [k]: await readAsB64(f) }, `Player ${n}’s ${t.name}`); };
            bR.onclick = () => send(n, { [k]: '' }, `Player ${n}’s ${t.name} (the model’s own)`);
            const btns = el('div'); btns.style.cssText = 'display:flex;gap:2px;flex-wrap:wrap'; btns.append(bP, bT, bR, pickF);
            c.append(cv, btns, sl);
            grid.append(c);
          });
          const slAll = sliders(n, (flt) => canv.forEach((cv, k) => { const g = cv.getContext('2d'); g.clearRect(0, 0, 96, 96); g.filter = flt; g.drawImage(imgs[k], 0, 0, 96, 96); g.filter = 'none'; }),
            (flt) => { const body = {}; T.forEach((t, k) => { body[k] = filtered(imgs[k], flt); }); send(n, body, `Player ${n}’s look`); }, 'Use on all');
          lab.append(slAll);
          allT.onclick = () => { slAll.style.display = slAll.style.display === 'none' ? 'flex' : 'none'; };
          reset.onclick = () => { if (confirm(`Put every one of player ${n}’s textures back to the model’s own?`)) send(n, '', `Player ${n}’s look (the model’s own)`); };
        }
        box.append(grid);
        return;
      }
      const row = el('div'); row.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px';
      const texSel = el('select');
      for (const t of T) texSel.append(new Option(`${t.name} (${t.w} x ${t.h})`, t.name));
      texSel.value = T[0].name;
      texSel.disabled = T.length < 2;
      const dl = el('a', 'btn', 'Save it as a PNG to paint on');
      dl.href = 'data:image/png;base64,' + T[0].png; dl.download = code + '_' + T[0].name.replace(/\.png$/i, '') + '.png';
      row.append(el('span', 'muted', 'Texture that changes'), texSel, dl);
      box.append(row);
      texSel.onchange = async () => {
        const name = texSel.value;
        if (!confirm(`Make ${name} the texture players 2–4 change? It is moved into its model's first slot (the model looks the same).`)) { texSel.value = T[0].name; return; }
        try { const r = await post('de_x_alt_looks_set', { code, texture: name, pics: {} }); toast(r.note || 'Done', 'good'); }
        catch (e) { toast(e.message, 'bad'); }
        loadLooks(box);
      };
      const base = await loadImg('data:image/png;base64,' + T[0].png);
      const cards = el('div'); cards.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap';
      const card = (title, src, sub) => {
        const c = el('div'); c.style.cssText = 'display:flex;flex-direction:column;gap:4px;align-items:flex-start;width:150px';
        const cv2 = el('canvas'); cv2.width = 128; cv2.height = 128; cv2.style.cssText = 'width:128px;height:128px;border:1px solid var(--line);background:#333';
        c.append(el('b', null, title), cv2);
        if (sub) c.append(el('span', 'muted', sub));
        return [c, cv2];
      };
      const paint = (cv2, img, filter) => { const g = cv2.getContext('2d'); g.clearRect(0, 0, 128, 128); g.filter = filter || 'none'; if (img) g.drawImage(img, 0, 0, 128, 128); g.filter = 'none'; };
      const [c1, cv1] = card('Player 1', null, "the model's own");
      paint(cv1, base);
      cards.append(c1);
      for (const n of [2, 3, 4]) {
        const cur = A.alts[n];
        const [c, cvn] = card('Player ' + n, null, cur ? (cur.own ? 'yours' : "the game's") : 'none: same as player 1');
        const img = cur ? await loadImg('data:image/png;base64,' + cur.png) : null;
        paint(cvn, img || base, img ? '' : 'grayscale(1) opacity(.45)');
        const pickF = el('input'); pickF.type = 'file'; pickF.accept = '.png,.jpg,.jpeg,.tex'; pickF.style.display = 'none';
        const bPic = el('button', 'btn', 'Picture…'), bTint = el('button', 'btn', 'Recolour…'), bDel = el('button', 'btn', 'Remove');
        bDel.style.display = cur && cur.own ? '' : 'none';
        const btns = el('div'); btns.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap'; btns.append(bPic, bTint, bDel, pickF);
        const tint = el('div'); tint.style.cssText = 'display:none;flex-direction:column;gap:2px;font-size:11px;width:150px';
        const rng = (min, max, v) => { const r = el('input'); r.type = 'range'; r.min = min; r.max = max; r.value = v; r.style.width = '140px'; return r; };
        const hue = rng(-180, 180, n === 2 ? 120 : n === 3 ? -120 : 60), sat = rng(0, 200, 100), lig = rng(30, 170, 100);
        const use = el('button', 'btn', 'Use this colour');
        tint.append(el('span', 'muted', 'Hue'), hue, el('span', 'muted', 'Saturation'), sat, el('span', 'muted', 'Brightness'), lig, use);
        const flt = () => `hue-rotate(${hue.value}deg) saturate(${sat.value / 100}) brightness(${lig.value / 100})`;
        const live = () => paint(cvn, base, flt());
        hue.oninput = sat.oninput = lig.oninput = live;
        bTint.onclick = () => { const on = tint.style.display === 'none'; tint.style.display = on ? 'flex' : 'none'; if (on) live(); else paint(cvn, img || base, img ? '' : 'grayscale(1) opacity(.45)'); };
        const send = async (pic) => {
          try { await post('de_x_alt_looks_set', { code, pics: { [n]: pic } }); toast('Player ' + n + '’s look is in the mods folder. Build to put it on the disc.', 'good'); }
          catch (e) { toast(e.message, 'bad'); }
          loadLooks(box);
        };
        use.onclick = () => {
          const full = document.createElement('canvas'); full.width = base.naturalWidth; full.height = base.naturalHeight;
          const g = full.getContext('2d'); g.filter = flt(); g.drawImage(base, 0, 0);
          send(full.toDataURL('image/png').split(',')[1]);
        };
        bPic.onclick = () => pickF.click();
        pickF.onchange = async () => { const f = pickF.files[0]; pickF.value = ''; if (f) send(await readAsB64(f)); };
        bDel.onclick = () => { if (confirm('Remove your player ' + n + ' look?')) send(''); };
        c.append(btns, tint);
        cards.append(c);
      }
      box.append(cards);
    }
    async function saveStats() {
      if (!S) return [];
      const swt = {};
      for (const [k, f] of Object.entries(fields.swt)) {
        const v = f.type === 'checkbox' ? (f.checked ? '1' : '0') : f.value.trim();
        if (String(S.swt[k]) !== v && !(f.type === 'number' && parseFloat(S.swt[k]) === parseFloat(v))) swt[k] = v;
      }
      const ws = [fields.w1.value, fields.w2.value].filter(Boolean);
      const body = { code: st.code, swt };
      if (parseFloat(fields.health.value) !== S.health) body.health = parseFloat(fields.health.value);
      if (ws.join() !== S.weapons.filter(Boolean).join()) body.weapons = ws;
      if (fields.brain.value !== S.brain) body.brain = fields.brain.value;
      if (!Object.keys(swt).length && body.health === undefined && !body.weapons && !body.brain) return [];
      const r = await post('de_x_creature_stats_set', body);
      await loadStats();
      return r.wrote;
    }
    viewSel.onchange = () => { st.view = +viewSel.value; load(true); };
    onSel.onchange = () => { st.lineup = +onSel.value; st.dirty = true; st.view = st.lineup; viewSel.value = String(st.view); st.layer = 1e9; load(true); };
    layer.oninput = () => { st.layer = +layer.value; st.dirty = true; restack(); };
    toBack.onclick = () => { st.layer = 0; st.dirty = true; restack(); };
    toFront.onclick = () => { st.layer = st.stack.length; st.dirty = true; restack(); };
    bgBtn.onclick = () => bgPick.click();
    bgPick.onchange = async () => {
      const f = bgPick.files[0]; bgPick.value = '';
      if (!f) return;
      const data = await readAsB64(f);
      let src;
      try { src = 'data:image/png;base64,' + (await post('de_x_alpha_png', { data, alpha: { alpha: 'keep' } })).png; }
      catch (e) { return toast(f.name + ': ' + e.message, 'bad'); }
      const pk = await cropDialog(src, { title: 'Lineup ' + (st.view + 1) + ' background', aspect: 4,
        target: '1024 x 256 (two 512 x 256 halves, 24-bit)', keepLabel: 'Fit it whole', okLabel: 'Use this crop',
        text: 'Pick the part to use as the lineup\'s background: it fills the 1024 x 256 picture the characters stand in front of.' });
      if (pk === null) return;
      try { await post('de_x_lineup_bg', { lineup: st.view, data, crop: pk === 'keep' ? null : pk }); } catch (e) { return toast(e.message, 'bad'); }
      toast('Lineup ' + (st.view + 1) + '\'s background is in the mods folder. Build to put it on the disc.', 'good');
      load(true);
    };
    bgReset.onclick = async () => {
      try { await post('de_x_lineup_bg', { lineup: st.view, reset: true }); } catch (e) { return toast(e.message, 'bad'); }
      load(true);
    };
    let drag = null;
    const at = (e) => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * 1024 / r.width, (e.clientY - r.top) * 256 / r.height]; };
    cv.onmousedown = (e) => {
      const [mx, my] = at(e);
      const inMine = st.code && st.lineup === st.view && mx >= st.x && mx < st.x + 128;
      drag = { mx, my, x: st.x, moved: false, inMine };
      const move = (ev) => {
        if (!drag.inMine) return;                              // only the picked character's photo moves
        const [nx, ny] = at(ev);
        if (Math.abs(nx - drag.mx) + Math.abs(ny - drag.my) > 3) drag.moved = true;
        if (!drag.moved) return;
        st.x = Math.max(-64, Math.min(1024 - 64, drag.x + nx - drag.mx)); st.dirty = true; draw();
      };
      const up = (ev) => {
        document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
        const [cx, cy] = at(ev);
        if (drag.moved) { const s = st.all.find((a) => a.mine); if (s) s.x = st.x; drag = null; draw(); return; }
        const h = hit(cx, cy);
        if (h && h !== st.code) { drag = null; pick(h); return; }   // a click on someone else's photo picks them
        if (drag.inMine) {                                       // a click in the picked one's frame: the pointer
          st.sx = Math.max(0, Math.min(127, cx - st.x)); st.sy = Math.max(0, Math.min(255, cy)); st.dirty = true;
        }
        drag = null; draw();
      };
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    };
    b._save = async () => {
      if (!st.code) return toast('Click a character\'s photo first', 'bad');
      const wrote = [];
      if (st.dirty) {
        const r = await post('de_x_mp_place', { code: st.code, spot: { x: Math.round(st.x), sx: Math.round(st.sx), sy: Math.round(st.sy) },
                                                 layer: Math.min(st.layer, st.stack.length), on_lineup: st.lineup });
        wrote.push(...r.wrote);
        st.dirty = false;
        try { info = await post('de_x_lineup_info', {}); } catch { /* keep the old one */ }
      }
      wrote.push(...await saveStats());
      bgShow(); draw();
      toast(wrote.length ? `${nameOf(st.code)}: ${wrote.length} files in the mods folder. Build to put them on the disc.` : 'Nothing changed.', wrote.length ? 'good' : 'warn');
    };
    load(false);
  }, [{ label: 'Close' }, { label: 'Save', cls: 'primary', run: async (b, btn) => {
    btn.disabled = true;
    try { await b._save(); } catch (e) { toast(e.message, 'bad'); }
    btn.disabled = false;
    return true;
  } }], 1150);
}

// ------------------------------------------------------------------ BALANCE > Creatures > Delete Character

const selectedCreature = () => ((document.querySelector('#crList .row.sel') || {}).dataset || {}).cr || '';

let delCheck = 0;
async function updateDeleteButton() {
  const b = document.querySelector('#crDelChar');
  if (!b) return;
  const cr = selectedCreature();
  const me = ++delCheck;
  b.disabled = true;
  if (!cr) { b.title = 'Select a character you added to delete it'; return; }
  try {
    const p = await post('creature_delete', { code: cr, dry: true });
    if (me !== delCheck) return;
    b.disabled = p.base_game;
    b.title = p.base_game ? `${p.code} is one of the game's own characters: only characters you added can be deleted`
      : `Delete ${p.code} from the working disc (${p.archives.length} archive(s))`;
  } catch (e) { b.title = e.message; }
}

async function deleteCharacterDialog() {
  const cr = selectedCreature();
  if (!cr) return toast('Select the character to delete first', 'bad');
  let p;
  try { p = await post('creature_delete', { code: cr, dry: true }); } catch (e) { return toast(e.message, 'bad'); }
  if (p.base_game) return toast(p.code + ' is one of the game\'s own characters: only characters you added can be deleted', 'bad');
  dialog('Delete character ' + p.code + '?', (b) => {
    b.append(el('p', null, `${p.files.length} file(s) of ${p.code} come out of ${p.archives.length} archive(s) on the working disc`
      + (p.archives.length > 3 ? '' : ': ' + p.archives.join(', ')) + '.'));
    const l = el('div', null, p.files.join('  '));
    l.style.cssText = 'font:var(--t-mono);color:var(--dim-ink);margin-bottom:8px';
    b.append(l);
    if (p.mods.length) b.append(el('p', 'muted', `${p.mods.length} file(s) of it in the Data Editor's mods folder are moved to the Workbench (Deleted character ${p.code}), so a later Build does not bring it back.`));
    b.append(el('p', 'muted', 'Every archive holding it is rebuilt without it (swat.dat takes a few minutes). Not undoable with Ctrl+Z. Enemies or levels that still use it should be pointed at another character first.'));
  }, [{ label: 'Cancel' }, { label: 'Delete', cls: 'danger', run: async (b, btn) => {
    btn.disabled = true; btn.textContent = 'Deleting…';
    try { await post('creature_delete', { code: p.code }); } catch (e) { toast(e.message, 'bad'); return; }
    toast('Deleting ' + p.code + '…', 'good');
    let j;
    do { await new Promise((r) => setTimeout(r, 1500)); try { j = await get('de_job'); } catch { j = { state: 'running' }; } } while (j.state === 'running');
    if (j.state !== 'done') return toast('Not deleted: ' + ((j.log || []).find((x) => /^Stopped/.test(x)) || j.state), 'bad');
    toast(p.code + ' deleted from the working disc', 'good');
    if (A && A.refreshLists) await A.refreshLists();
    if (A && A.refreshCreatures) await A.refreshCreatures();
  } }]);
}

// ------------------------------------------------------------------ TEXT > HUD text

let hudTables = null;
function hudEmptyNote() {
  const n = el('div', 'pad muted');
  n.style.cssText = 'max-width:720px;line-height:1.6;padding:14px 18px';
  n.innerHTML = 'Pick a text table on the left: <b>swat.strtbl</b> is the HUD text. It opens here: every line by its ID, '
    + 'with its font and size; lines added or removed (stacked in the game as "Twiggy" over Phelps), new entries and '
    + 'renamed IDs. <b>Save and put in &lt;dat&gt;</b> puts it in the folder of that .dat; the next Build puts it on the disc.';
  return n;
}
async function hudTextPage() {
  const list = document.querySelector('#hudList');
  if (!list) return;
  const pane = document.querySelector('#hudPane');
  if (pane && !pane.childElementCount) pane.append(hudEmptyNote());
  if (!hudTables) {
    list.innerHTML = '';
    list.append(el('div', 'pad muted', 'Reading the text tables…'));
    try { hudTables = (await get('de_x_strtbls')).tables; } catch (e) { list.innerHTML = ''; list.append(el('div', 'pad muted', e.message)); return; }
  }
  const f = document.querySelector('#hudFilter');
  const draw = () => {
    const q = (f.value || '').toLowerCase();
    list.innerHTML = '';
    const rows = hudTables.filter((t) => !q || (t.name + ' ' + t.archive).toLowerCase().includes(q));
    // swat.strtbl (the HUD text) first
    rows.sort((a, b) => (b.archive === 'swat.dat' && b.name === 'swat.strtbl') - (a.archive === 'swat.dat' && a.name === 'swat.strtbl'));
    for (const t of rows) {
      const r = el('div', 'de-wbrow');
      r.style.padding = '4px 12px';
      const n = el('span', null, t.name); n.style.flex = '1';
      r.append(n, el('span', 'muted', t.archive + (t.edited ? ' · in its .dat folder' : '')));
      r.title = 'Edit ' + t.name + ' (' + t.archive + ')';
      r.onclick = async () => {
        const pane = document.querySelector('#hudPane');
        if (pane && pane._dirty && pane._dirty() && !confirm('Leave the table you are editing without saving?')) return;
        for (const x of list.querySelectorAll('.de-wbrow')) x.classList.remove('on');
        r.classList.add('on');
        try {
          const o = await post('de_x_hud_open', { archive: t.archive, name: t.name });
          const d = await hudEditor(o.where, o.key, o.archive, pane);
          if (pane && d && d.body) pane._dirty = d.body._dirty;
        } catch (e) { toast(e.message, 'bad'); }
      };
      list.append(r);
    }
    const tally = document.querySelector('#hudTally');
    if (tally) tally.textContent = rows.length;
  };
  f.oninput = draw;
  draw();
}

// ------------------------------------------------------------------ TEXT > Flash screens (.pck)
// The game's Flash screens (menus, journal pages, shops, popups, credits) and their text, written back in place
// (rdrstudio/pck.py): a line keeps about the letters it had and only the letters its font has.

let pckScreens = null;
// what happens to letters a font lacks: added on save when "Fill missing letters on save" is ticked, else refused
function pckAfNote() {
  let on = false;
  try { on = localStorage.getItem('rdr.pck.autofill') === '1'; } catch (e) { /* off */ }
  return on ? ' — added to the font when you save (from the game\'s other screens, or the TrueType font picked above).'
    : ' — tick "Fill missing letters on save" above to add them, else the save is refused.';
}
function pckEmptyNote() {
  const n = el('div', 'pad muted');
  n.style.cssText = 'max-width:760px;line-height:1.6;padding:14px 18px';
  n.innerHTML = 'Pick a Flash screen on the left: the menus, the journal pages, the shops, the popups and the credits '
    + 'are <b>.pck</b> files. It opens here as the game draws it (the <b>Pages view</b>): every object in a tree, move, scale and '
    + 'rotate things, edit text where it is, replace or import pictures, fonts and sounds. <b>Text view</b> lists its text line by line.<br><br>'
    + 'A text that fits its room is written back <b>in place</b>; a longer one moves to the end of the file (the file '
    + 'grows) &mdash; there is no length limit. A letter its font lacks is added to the font when you save: from the '
    + 'same typeface in the game\'s other screens, else from the TrueType font picked at the top. '
    + 'Centred lines stay centred.<br><br>'
    + '<b>Save and put in &lt;dat&gt;</b> puts the screen in the folder of that '
    + '.dat; the next Build puts it on the disc.';
  return n;
}

// the width of a line in twips, from the font's advances (1024ths of the size)
function pckWidth(font, size, text) {
  const f = font || { chars: '', adv: {} };
  const vals = Object.values(f.adv || {});
  const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 512;
  let w = 0;
  for (const c of text) {
    if (f.chars.includes(c)) w += (f.adv[c] != null ? f.adv[c] : avg) * size / 1024;
    else if (c === ' ') w += size * 0.27;
  }
  return w;
}
const pckGlyphs = (font, text) => [...text].filter((c) => font && font.chars.includes(c)).length;
const pckMissing = (font, text, extra) => [...new Set([...text].filter((c) => !(font && font.chars.includes(c)) && !(extra || ' ').includes(c)))];
const pckColor = (hex) => '#' + (hex || '000000ff').slice(0, 6);

async function pckEditor(where, key, archive, host) {
  let t;
  try { t = await get('de_x_pck?where=' + where + '&key=' + encodeURIComponent(key)); } catch (e) { toast(e.message, 'bad'); return; }
  const inMods = where === 'mods';
  const edits = {};
  const title = 'Flash screen: ' + base(key) + (inMods ? ` (in ${archive}'s folder)` : ' (Workbench' + (archive ? ', from ' + archive : '') + ')');
  const d = panel(host, title, (b) => {
    const c = t.counts || {};
    const top = el('div', 'bar');
    top.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:0 0 6px';
    const find = el('input'); find.placeholder = 'Find text'; find.style.width = '220px';
    const onlyCh = el('input'); onlyCh.type = 'checkbox';
    const onlyLab = el('label'); onlyLab.append(onlyCh, el('span', null, ' Only changed'));
    const pagesB = el('button', 'primary', 'Pages view');
    pagesB.title = 'The whole screen drawn as the game draws it, every object in a tree: move things, export and replace pictures';
    pagesB.onclick = () => {
      if (Object.keys(edits).length && !confirm('Leave your text changes without saving?')) return;
      pckPagesOpen(where, key, archive, host);
    };
    top.append(pagesB);
    // letters a font lacks: the same typeface from the game's other screens, then this TrueType font (shared with
    // the Pages view)
    const ttfSel = el('select'); ttfSel.style.maxWidth = '230px';
    ttfSel.title = 'Letters a line needs that its font lacks are added when you save: from the same typeface in the game\'s other screens, and what none has from this TrueType font';
    ttfSel.append(new Option('Missing letters: other screens only', ''));
    ttfSel.onchange = () => { try { localStorage.setItem('rdr.pck.ttf', ttfSel.value); } catch (e) { /* this session only */ } };
    get('de_x_pck_ttfs').then((r) => {
      for (const f of r.fonts) ttfSel.append(new Option('Missing letters from: ' + f.name, f.id));
      try { ttfSel.value = localStorage.getItem('rdr.pck.ttf') || ''; } catch (e) { /* none */ }
    }).catch(() => {});
    const afB = el('input'); afB.type = 'checkbox';
    try { afB.checked = localStorage.getItem('rdr.pck.autofill') === '1'; } catch (e) { /* off */ }
    const afL = el('label'); afL.append(afB, el('span', null, ' Fill missing letters on save'));
    afL.title = 'When ticked, a letter a line needs that its font lacks is added to the font when you save. Unticked, such a save is refused.';
    afB.onchange = () => { try { localStorage.setItem('rdr.pck.autofill', afB.checked ? '1' : ''); } catch (e) { /* this session */ } };
    top.append(afL, ttfSel);
    top.append(el('span', 'muted', `${Math.round(t.width)}×${Math.round(t.height)} · ${c.text || 0} texts · ${c['edit text'] || 0} edit texts (${t.texts.filter((x) => x.kind === 'edit').length} with text of their own) · ${c.bitmap || 0} pictures · ${c.font || 0} fonts`),
               el('span', 'muted', '·'), find, onlyLab);
    const wrap = el('div', 'scroll');
    wrap.style.cssText = 'flex:1;min-height:200px;display:flex;flex-direction:column;gap:8px;padding-right:4px';
    b.append(top, wrap);
    const cards = [];
    const changed = () => { b._note && (b._note.textContent = Object.keys(edits).length ? Object.keys(edits).length + ' text(s) changed, not saved' : ''); };

    for (const x of t.texts) {
      const card = el('div');
      card.style.cssText = 'border:1px solid var(--line);padding:8px;display:flex;gap:10px;align-items:flex-start';
      const left = el('div'); left.style.cssText = 'flex:1;min-width:0;display:flex;flex-direction:column;gap:4px';
      const head = el('div'); head.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap';
      const room = el('span', 'muted');
      const warn = el('div'); warn.style.cssText = 'color:var(--bad, #e06c5a);font-size:12px';
      const font = x.kind === 'edit' ? t.fonts[String(x.font)] : null;
      card._changed = () => !!edits[x.id];
      card._text = () => (x.kind === 'edit' ? x.text : x.runs.map((r) => r.text).join(' ')).toLowerCase();
      if (x.error) {
        head.append(el('b', null, 'Text ' + x.id), el('span', 'muted', x.error));
        left.append(head); card.append(left); wrap.append(card); cards.push(card); continue;
      }
      if (x.kind === 'edit') {
        head.append(el('b', null, 'Edit text ' + x.id), el('span', 'muted', `font ${x.font} · ${(x.size / 20).toFixed(0)} px · ${x.align}`), room);
        const multi = x.text.includes('\n');
        const inp = el(multi ? 'textarea' : 'input');
        inp.value = x.text;
        inp.style.cssText = 'width:100%' + (multi ? ';height:60px;resize:vertical;background:var(--well);color:var(--ink);border:1px solid var(--line);padding:4px' : '');
        const sync = () => {
          const v = inp.value;
          const miss = pckMissing(font, v, ' \n\t');
          const n = v.replace(/\r?\n/g, '\r').length;
          room.textContent = n > x.max ? `${n} letters: longer than its room (${x.max}), it moves to the end of the file` : `${n} / ${x.max} letters fit in place`;
          room.style.color = n > x.max ? 'var(--warn, #d9a33a)' : '';
          warn.textContent = miss.length ? 'Not in its font: ' + miss.join(' ') + pckAfNote() : '';
          warn.style.color = 'var(--warn, #d9a33a)';
          if (v === x.text) delete edits[x.id]; else edits[x.id] = { kind: 'edit', text: v };
          card.style.borderColor = edits[x.id] ? 'var(--accent)' : '';
          changed();
        };
        inp.oninput = sync;
        left.append(head, inp, warn);
        card.append(left);
        sync();
      } else {
        const f0 = x.runs.length ? t.fonts[String(x.runs[0].font)] : null;
        const alignSel = el('select');
        for (const [v, l] of [['left', 'Lines keep their left edge'], ['center', 'Lines stay centred'], ['right', 'Lines keep their right edge']]) alignSel.append(new Option(l, v));
        alignSel.value = x.align || 'left';
        head.append(el('b', null, 'Text ' + x.id), el('span', 'muted', x.runs.length ? `font ${x.runs[0].font} · ${(x.runs[0].size / 20).toFixed(0)} px` : ''), alignSel, room);
        left.append(head);
        const inputs = x.runs.map((r) => {
          const inp = el('input'); inp.value = r.text; inp.style.width = '100%';
          inp.title = 'Line at ' + (r.x / 20).toFixed(0) + ', ' + (r.y / 20).toFixed(0) + ' px (font ' + r.font + ')';
          left.append(inp);
          return inp;
        });
        const fontsUsed = [...new Set(x.runs.map((r) => r.font))];
        const charsNote = el('div', 'muted');
        charsNote.style.cssText = 'font-size:12px;word-break:break-all';
        const fc = fontsUsed.map((fid) => t.fonts[String(fid)]).filter(Boolean);
        if (fc.length && fc.every((f) => !f.table) && fc[0].chars.length < 100) charsNote.textContent = 'Letters its font has: ' + fc.map((f) => f.chars.replace(/ /g, '')).join(' / ');
        left.append(warn, charsNote);
        const cv = el('canvas');
        cv.style.cssText = 'border:1px solid var(--line);background:#c4b28c;flex:none';
        const draw = () => {
          const lines = x.runs.map((r, i) => {
            const v = inputs[i].value;
            const f = t.fonts[String(r.font)];
            const nw = v === r.text ? r.w : pckWidth(f, r.size, v);
            const dx = alignSel.value === 'center' ? (r.w - nw) / 2 : alignSel.value === 'right' ? r.w - nw : 0;
            return { r, v, nw, nx: r.x + dx };
          });
          const minX = Math.min(...lines.map((l) => Math.min(l.r.x, l.nx)));
          const maxX = Math.max(...lines.map((l) => Math.max(l.r.x + l.r.w, l.nx + l.nw)));
          const minY = Math.min(...x.runs.map((r) => r.y - r.size));
          const maxY = Math.max(...x.runs.map((r) => r.y + r.size * 0.3));
          const W = Math.max(1, maxX - minX), H = Math.max(1, maxY - minY);
          const k = Math.min(300 / W, 160 / H, 1 / 10);
          cv.width = Math.ceil(W * k) + 12; cv.height = Math.ceil(H * k) + 12;
          const g = cv.getContext('2d');
          g.clearRect(0, 0, cv.width, cv.height);
          for (const l of lines) {
            const px = (l.nx - minX) * k + 6, py = (l.r.y - minY) * k + 6;
            g.strokeStyle = 'rgba(0,0,0,.25)'; g.setLineDash([3, 3]);
            g.strokeRect((l.r.x - minX) * k + 6, py - l.r.size * k * 0.8, l.r.w * k, l.r.size * k);
            g.setLineDash([]);
            g.fillStyle = pckColor(l.r.color);
            g.font = Math.max(6, l.r.size * k * 0.82) + 'px Georgia, serif';
            g.textBaseline = 'alphabetic';
            g.fillText(l.v, px, py, Math.max(1, l.nw * k));
          }
          return lines;
        };
        const sync = () => {
          const lines = draw();
          let used = x.fixed, miss = [];
          for (const l of lines) {
            const f = t.fonts[String(l.r.font)];
            const n = l.v === l.r.text ? l.r.glyphs : pckGlyphs(f, l.v);
            used += n ? 4 + 4 * n : 0;
            miss = miss.concat(pckMissing(f, l.v));
          }
          miss = [...new Set(miss)];
          const left_ = Math.floor((x.room - used) / 4);
          room.textContent = left_ >= 0 ? `room for ${left_} more letter${left_ === 1 ? '' : 's'} in place` : `${-left_} letter${left_ === -1 ? '' : 's'} past its room: it moves to the end of the file (it grows)`;
          room.style.color = left_ < 0 ? 'var(--warn, #d9a33a)' : '';
          const wide = lines.filter((l) => l.v !== l.r.text && l.nw > l.r.w * 1.15 + 200);
          warn.style.color = 'var(--warn, #d9a33a)';
          warn.textContent = (miss.length ? 'Not in its font: ' + miss.map((m) => (m === ' ' ? 'space' : m)).join(' ') + pckAfNote() + ' ' : '')
            + (wide.length ? `${wide.length} line(s) are wider than they were: check they still fit the page. ` : '');
          const vals = inputs.map((i) => i.value);
          const same = vals.every((v, i) => v === x.runs[i].text) && alignSel.value === (x.align || 'left');
          if (same) delete edits[x.id]; else edits[x.id] = { kind: 'text', lines: vals, align: alignSel.value };
          card.style.borderColor = edits[x.id] ? 'var(--accent)' : '';
          changed();
        };
        inputs.forEach((i) => { i.oninput = sync; });
        alignSel.onchange = sync;
        card.append(left, cv);
        sync();
      }
      wrap.append(card);
      cards.push(card);
    }
    if (!t.texts.length) wrap.append(el('div', 'pad muted', 'This screen has no text of its own (the game fills it in, or it is all pictures).'));
    const filt = () => {
      const q = (find.value || '').toLowerCase();
      for (const card of cards) {
        const ch = card._changed && card._changed();
        card.style.display = (!q || card._text().includes(q)) && (!onlyCh.checked || ch) ? '' : 'none';
      }
    };
    find.oninput = filt; onlyCh.onchange = filt;
    b._save = async (putIn) => {
      const changes = Object.entries(edits).map(([id, v]) => ({ id: +id, ...v }));
      // nothing changed still saves: it repairs pictures an earlier Studio wrote with the wrong upload fields
      try {
        let ttf = null;
        try { ttf = localStorage.getItem('rdr.pck.ttf') || null; } catch (e) { /* none */ }
        let autofill = false;
        try { autofill = localStorage.getItem('rdr.pck.autofill') === '1'; } catch (e) { /* off */ }
        const r = await post('de_x_pck_save', { where, key, changes, put_in: putIn ? archive : null, ttf, autofill });
        toast(r.note, 'good');
        pckScreens = null;
        if (inMods || putIn) refresh();
        loadWorkbench();
        // reopen: the lines as they were laid (centring, letter spacing) and the room left
        const o = putIn ? await post('de_x_pck_open', { archive, name: key.split('/').pop() }) : { where, key, archive };
        const nd = await pckEditor(o.where, o.key, o.archive, host);
        if (host && nd && nd.body) host._dirty = nd.body._dirty;
        pckPage(true);
      } catch (e) { toast(e.message, 'bad'); }
    };
    b._dirty = () => Object.keys(edits).length > 0;
  }, [
    { spacer: true },
    { label: 'Save', cls: inMods ? 'primary' : '', title: inMods ? `Saves it in ${archive}'s .dat folder: it goes in at the next Build` : 'Saves the Workbench copy', run: (b) => { b._save(false); return true; } },
    ...(archive && !inMods ? [{ label: 'Save and put in ' + archive, cls: 'primary', title: `Saves, and copies it into ${archive}'s .dat folder so the next Build puts it in the game`, run: (b) => { b._save(true); return true; } }] : []),
    { label: 'Close', run: (b) => (b._dirty() && !confirm('Close without saving your changes?')) },
  ], pckEmptyNote);
  return d;
}

// TEXT > Flash screens > Pages view (web/pckview.js)
async function pckPagesOpen(where, key, archive, host) {
  const m = await import('./pckview.js');
  const d = await m.pckPages(host, {
    where, key, archive, get, post, toast, panel, emptyNote: pckEmptyNote,
    toText: async () => { const nd = await pckEditor(where, key, archive, host); if (host && nd && nd.body) host._dirty = nd.body._dirty; },
    onSaved: async (putIn) => {
      pckScreens = null;
      if (where === 'mods' || putIn) refresh();
      loadWorkbench();
      const o = putIn ? await post('de_x_pck_open', { archive, name: key.split('/').pop() }) : { where, key, archive };
      pckPagesOpen(o.where, o.key, o.archive, host);
      pckPage(true);
    },
  });
  if (host && d && d.body) host._dirty = d.body._dirty;
}

async function pckPage(keepPane) {
  const list = document.querySelector('#pckList');
  if (!list) return;
  const pane = document.querySelector('#pckPane');
  if (pane && !pane.childElementCount) pane.append(pckEmptyNote());
  if (!pckScreens) {
    if (!keepPane) { list.innerHTML = ''; list.append(el('div', 'pad muted', 'Reading the Flash screens…')); }
    try { pckScreens = (await get('de_x_pcks')).screens; } catch (e) { list.innerHTML = ''; list.append(el('div', 'pad muted', e.message)); return; }
  }
  const f = document.querySelector('#pckFilter');
  // every screen's fonts filled at once (a Data Editor job; each changed screen goes in its .dat folder)
  if (!document.querySelector('#pckFillEvery')) {
    const b = el('button', '', 'Fill fonts in all screens…');
    b.id = 'pckFillEvery';
    b.style.cssText = 'width:100%;margin-top:6px';
    b.title = 'Every font of every Flash screen given every letter that can be found (the same typeface in the other screens, and the TrueType font picked for missing letters, if one is). Each changed screen is put in its .dat folder: the next Build puts them on the disc.';
    b.onclick = async () => {
      let ttf = null;
      try { ttf = localStorage.getItem('rdr.pck.ttf') || null; } catch (e) { /* none */ }
      if (!confirm('Fill the fonts of all ' + (pckScreens ? pckScreens.length : '') + ' Flash screens?\n\nEvery font gets the letters the same typeface has in the other screens'
        + (ttf ? ', and the rest from the TrueType font picked' : ' (no TrueType font is picked, so only those)')
        + '. Each changed screen grows a little and is put in its .dat folder; the next Build puts them on the disc. Reset to stock (with its box ticked) takes them out again.')) return;
      try {
        await post('de_x_pck_fill_every', { ttf });
      } catch (e) { toast(e.message, 'bad'); return; }
      b.disabled = true;
      const tick = async () => {
        let j;
        try { j = await get('de_job'); } catch (e) { setTimeout(tick, 1500); return; }
        const last = (j.log || []).slice(-1)[0] || '';
        b.textContent = 'Filling… ' + last.split(':')[0];
        if (j.state === 'running') { setTimeout(tick, 1000); return; }
        b.disabled = false; b.textContent = 'Fill fonts in all screens…';
        toast(j.state === 'done' ? last : 'Stopped: ' + last, j.state === 'done' ? 'good' : 'bad');
        pckScreens = null; pckPage(true); refresh();
      };
      tick();
    };
    f.parentNode.append(b);
  }
  const on = list.querySelector('.de-wbrow.on');
  const onKey = on ? on.dataset.k : null;
  const draw = () => {
    const q = (f.value || '').toLowerCase();
    list.innerHTML = '';
    const rows = pckScreens.filter((s) => !q || (s.name + ' ' + s.archive).toLowerCase().includes(q));
    for (const s of rows) {
      const r = el('div', 'de-wbrow');
      r.dataset.k = s.archive + '|' + s.name;
      if (r.dataset.k === onKey) r.classList.add('on');
      r.style.padding = '4px 12px';
      const n = el('span', null, s.name.split('/').pop()); n.style.flex = '1';
      r.append(n, el('span', 'muted', s.archive + (s.edited ? ' · in its .dat folder' : '')));
      r.title = 'Edit the text of ' + s.name + ' (' + s.archive + ')';
      r.onclick = async () => {
        if (pane && pane._dirty && pane._dirty() && !confirm('Leave the screen you are editing without saving?')) return;
        for (const x of list.querySelectorAll('.de-wbrow')) x.classList.remove('on');
        r.classList.add('on');
        try {
          // the screen opens in the Pages view (its Text view button switches to the lines)
          const o = await post('de_x_pck_open', { archive: s.archive, name: s.name });
          await pckPagesOpen(o.where, o.key, o.archive, pane);
        } catch (e) { toast(e.message, 'bad'); }
      };
      list.append(r);
    }
    const tally = document.querySelector('#pckTally');
    if (tally) tally.textContent = rows.length;
  };
  f.oninput = draw;
  draw();
}

export function init(bridge) {
  A = bridge;
  window.rdrDEStartJob = startJob;                               // the header's Build
  window.rdrNewDat = newDatDialog;                               // the level editor's "New level" button
  window.rdrRefreshDE = () => refresh();                         // the Game picker: each game's own statuses
  const logBtn = document.querySelector('#deLogBtn');            // the bar is under every tab
  if (logBtn) logBtn.onclick = logDialog;
  const cancelBtn = document.querySelector('#deCancel');
  if (cancelBtn && !cancelBtn.onclick) {
    cancelBtn.onclick = async () => { try { await post('de_cancel', {}); poll(); } catch (e) { toast(e.message, 'bad'); } };
  }
  if (bridge && bridge.toast) window.rdrToast = bridge.toast;   // for xboxmode.js
  const addChar = document.querySelector('#crAddChar');
  if (addChar) addChar.onclick = addCharacterDialog;
  const mpAdd = document.querySelector('#crMpAdd');
  if (mpAdd) mpAdd.onclick = () => showdownDialog();
  const repAll = document.querySelector('#crReplaceAll');
  if (repAll) repAll.onclick = replaceAllCharactersDialog;
  const mpNew = document.querySelector('#crMpNew');
  if (mpNew) mpNew.onclick = newShowdownCharacter;
  const mpEdit = document.querySelector('#crMpEdit');
  if (mpEdit) mpEdit.onclick = showdownEditor;
  const delChar = document.querySelector('#crDelChar');
  if (delChar) {
    delChar.onclick = deleteCharacterDialog;
    const list = document.querySelector('#crList');
    if (list) list.addEventListener('click', () => setTimeout(updateDeleteButton, 50));
  }
  // TEXT > HUD text: every text table, one click to the editor
  // MEDIA > Menus: the same HUD text editor
  const menuHud = document.querySelector('#menuHudText');
  if (menuHud) menuHud.onclick = () => hudDialog(null);
  // a link straight to the tab: ...#dataeditor (#dataeditor-manual, #dataeditor-settings for those pages)
  if (/^#dataeditor/.test(location.hash) && A && A.showTab) {
    A.showTab('dataeditor');
    const m = location.hash.match(/^#dataeditor-(manual|settings)$/);
    if (m) setTimeout(() => setPage(m[1]), 0);
  }
  onPanel((panel) => {
    if (panel === 'hudtext') { hudTextPage(); return; }
    if (panel === 'pck') { pckPage(); return; }
    if (panel !== 'dataeditor') {
      if (!jobDone) { clearTimeout(pollTimer); pollTimer = null; }   // a running job keeps its status line going
      return;
    }
    wire();
    refresh();
    loadWorkbench();
    loadSaved();
    if (page === 'settings') settingsView();
  });
}
