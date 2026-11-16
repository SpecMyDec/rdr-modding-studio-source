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
function status(msg) { $('#deStatus').textContent = msg || ''; $('#deStatus').title = msg || ''; }

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
  if (j.state === 'running') {
    status(j.label + '…  ' + (lastJobLog[lastJobLog.length - 1] || ''));
    pollTimer = setTimeout(poll, 800);
    return;
  }
  setBusy(false);
  status(j.state === 'done' ? (j.label + ': done' + (j.seconds != null ? ' in ' + j.seconds + ' s' : '') + '.  ' + (lastJobLog[lastJobLog.length - 1] || ''))
    : j.state === 'refused' ? (j.label + ': refused (see the Log)') : j.state === 'error' ? (j.label + ': stopped (see the Log)') : '');
  if (j.state === 'done' && /Build|Restore/.test(j.label || '')) {
    toast(j.label + ' finished', 'good');
    if (A && A.refreshLists) A.refreshLists();  // new levels and characters show in the other tabs
  }
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

function renderFiles() {
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
        { label: 'Delete (to the Recycle Bin)', run: deleteSelected },
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
  $('#deEmpty').style.display = rows.length || removed.length ? 'none' : '';
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
    if (dats.length) {
      status('Importing ' + dats.length + ' .dat file(s)…');
      const payload = [];
      for (const f of dats) payload.push({ name: f.name, data: await readAsB64(f) });
      const r = await post('de_import', { files: payload, into });
      for (const n of r.results) toast(n.note, 'good');
      if (r.results.length) folder = r.results[r.results.length - 1].archive;
    }
    if (rest.length) {
      const payload = [];
      for (const f of rest) payload.push({ name: f.name, data: await readAsB64(f) });
      const r = await post('de_add', { folder: into, files: payload });
      toast(r.added.length + ' file(s) added to ' + (into === 'master' ? 'Master' : into), 'good');
    }
  } catch (e) { toast(e.message, 'bad'); }
  status('');
  refresh();
}

async function transfer(from, paths, toWhere, to, move) {
  if (!paths.length) return;
  try {
    const r = await post('de_x_transfer', { from, paths, to_where: toWhere, to, move });
    toast(`${r.done.length} item(s) ${move ? 'moved' : 'copied'} to ${toWhere === 'workbench' ? 'the Workbench' : (to === 'Master' ? 'Master' : to)}`, 'good');
  } catch (e) { toast(e.message, 'bad'); }
  if (from === 'mods' || toWhere === 'mods') { selected.clear(); refresh(); }
  if (from === 'workbench' || toWhere === 'workbench') loadWorkbench();
}

async function deleteSelected() {
  const keys = [...selected];
  if (!keys.length) { toast('Select the files to delete first (Ctrl+click picks several)', 'bad'); return; }
  dialog('Delete ' + keys.length + ' file(s)?', (b) => {
    const l = el('div', null, keys.slice(0, 14).join('\n') + (keys.length > 14 ? '\n…' : ''));
    l.style.cssText = 'white-space:pre;font:var(--t-mono);color:var(--dim-ink)';
    b.append(l, el('p', 'muted', 'They go to the Recycle Bin. The game\'s own files come back at the next Build.'));
  }, [{ label: 'Cancel' }, { label: 'Delete', cls: 'danger', run: async () => {
    try {
      await post('de_x_remove', { where: 'mods', paths: keys });
      selected.clear();
      if (viewing && keys.includes(viewing.key)) clearViewer();
      refresh();
    } catch (e) { toast(e.message, 'bad'); }
  } }]);
}

async function deleteNewFiles() {
  if (folderKind(folder) !== 'archive') return toast('Select one of the game\'s .dat folders first', 'bad');
  const fresh = data.files.filter((f) => f.folder === folder && f.new_to_archive);
  if (!fresh.length) return toast(folder + ' has no files new to the archive', 'good');
  if (!confirm(`Delete the ${fresh.length} file(s) in ${folder} that the archive has no file of the name of?\n\n`
    + fresh.slice(0, 12).map((f) => f.name).join('\n') + (fresh.length > 12 ? '\n…' : '') + '\n\nThey go to the Recycle Bin.')) return;
  try { const r = await post('de_x_delete_new', { folder }); toast(r.deleted.length + ' file(s) to the Recycle Bin', 'good'); refresh(); } catch (e) { toast(e.message, 'bad'); }
}

async function openFolder(where, rel) {
  try { await post('de_x_open', { where, path: rel || '' }); } catch (e) { toast(e.message, 'bad'); }
}

// ------------------------------------------------------------------ dialogs of the working disc page

function restoreDialog() {
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
    for (const [l, c] of [['Kind', kind], ['Name', name], ['Start from', tpl], ['Title', title], ['Subtitle', sub]]) {
      grid.append(el('span', 'muted', l), c);
    }
    b.append(grid);
    b.append(el('p', 'muted', 'Makes the archives in the mods folder from the game\'s own files (a Showdown map also '
      + 'gets its menu row, unlock and thumbnail; a story level gets 4x4 spawns and High Noon duel spots). Build puts '
      + 'them on the working disc.'));
    b._get = () => ({ kind: kind.value, name: name.value.trim(), template: tpl.value, title: title.value.trim(), subtitle: sub.value.trim() });
  }, [{ label: 'Cancel' }, { label: 'Make', cls: 'primary', run: (b) => {
    const v = b._get();
    if (!/^[a-z0-9_]{1,8}$/i.test(v.name)) { toast('Give a name: letters, digits and _ only', 'bad'); return true; }
    startJob('de_newdat', v, 'New .dat');
  } }]);
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
};

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
      + '<option value="ink_black">Ink transparency, no template: greyscale, dark parts solid black ink, light parts clear (its own size)</option>'
      + '<option value="ink_white">Ink transparency, no template: greyscale, dark parts solid white ink, light parts clear (its own size)</option>'
      + '<option value="level_sp">level.tex image: single player journal page (img0_*.tex, 128x128, black ink)</option>'
      + '<option value="level_mp">level.tex image: multiplayer level select (levelselect_*.tex, 256x128, white ink)</option>'
      + '<option value="mp_portrait">mp portrait: multiplayer character portrait (mp_&lt;code&gt;.tex, your mp template)</option>'
      + '<option value="book_red">book_red picture: your picture in the book_red frame and paper (your GIMP template)</option>'
      + '<option value="book">Weapon book page: greyscale, dark ink solid and white see-through (book_w*)</option>';
    const keyRow = el('div');
    keyRow.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    const key = el('input'); key.type = 'color'; key.value = '#000000';
    const tol = el('input'); tol.type = 'number'; tol.value = 16; tol.min = 0; tol.max = 255; tol.style.width = '64px';
    const soft = el('input'); soft.type = 'number'; soft.value = 48; soft.min = 0; soft.max = 255; soft.style.width = '64px';
    keyRow.append(el('span', 'muted', 'Colour'), key, el('span', 'muted', 'Tolerance:'), tol, el('span', 'muted', 'Softness:'), soft);
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
    const PREFIX = { mp_portrait: 'mp_', level_sp: 'img0_', level_mp: 'levelselect_' };
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
    const nameBox = el('div');
    nameBox.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    nameBox.append(which, nameRow);
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
    async function picture(f) {
      if (!pictures[f.name]) pictures[f.name] = await loadImg(URL.createObjectURL(f));
      return pictures[f.name];
    }
    function fillPreviewList() {
      which.innerHTML = '';
      for (const f of files) which.append(new Option(f.name, f.name));
      which.style.display = files.length > 1 ? '' : 'none';
      previewFile = files[0] || null;
      syncZoom();
      draw();
    }
    which.onchange = () => { previewFile = files.find((f) => f.name === which.value) || null; syncZoom(); draw(); };
    const placeKey = (f) => alpha.value + '|' + f.name;
    const placeOf = (f) => (bookPlace[placeKey(f)] = bookPlace[placeKey(f)] || { s: 1, dx: 0, dy: 0 });
    function syncZoom() {
      if (previewFile) { zoom.value = placeOf(previewFile).s; zLab.textContent = 'Zoom ' + Math.round(placeOf(previewFile).s * 100) + '%'; }
      syncName();
    }
    zoom.oninput = () => { if (!previewFile) return; placeOf(previewFile).s = +zoom.value; syncZoom(); draw(); };
    reset.onclick = () => { if (!previewFile) return; bookPlace[placeKey(previewFile)] = { s: 1, dx: 0, dy: 0 }; syncZoom(); draw(); };
    // the picture, placed on a page of this size, greyscaled
    async function placedGrey(W, H) {
      const pc = document.createElement('canvas'); pc.width = W; pc.height = H;
      if (!previewFile) return pc;
      const im = await picture(previewFile);
      if (!im) return pc;
      const p = placeOf(previewFile);
      const k = Math.max(W / im.width, H / im.height) * p.s;
      const nw = im.width * k, nh = im.height * k;
      const g2 = pc.getContext('2d');
      g2.filter = 'grayscale(1)';
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
                el('span', 'muted', 'Clear colour'), keyRow, el('span', 'muted', 'Format'), fmt, el('span', 'muted', 'Options'), opt);
    b.append(grid, prevBox, out);
    const showKey = () => {
      keyRow.style.opacity = /^(key|book)$/.test(alpha.value) ? '1' : '.45';
      cropLab.style.display = alpha.value === 'book_red' ? '' : 'none';
      prevBox.style.display = PICTURE_PAGES[alpha.value] ? 'flex' : 'none';
      // the shipped textures of these kinds have no mipmaps and an exact size: set for them, not asked
      const preset = ['mp_portrait', 'level_sp', 'level_mp'].includes(alpha.value);
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
          to: toWb.checked ? 'workbench' : folder, alpha: alpha.value, key: key.value.slice(1), tolerance: +tol.value,
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
    { label: 'Remove (to the Recycle Bin)', disabled: !wb.sel.size, run: wbRemove },
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
  if (!confirm(`Move ${ps.length} item(s) from the Workbench to the Recycle Bin?\n\n` + ps.slice(0, 10).join('\n') + (ps.length > 10 ? '\n…' : ''))) return;
  try { await post('de_x_remove', { where: 'workbench', paths: ps }); wb.sel.clear(); if (viewing && viewing.where === 'workbench' && ps.includes(viewing.key)) clearViewer(); loadWorkbench(); }
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
    for (const m of box._mods) list.append(new Option(`${m.name}   (${m.files} mod file(s), ${m.workbench} on the Workbench, ${m.when})`, m.name));
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

async function deleteMod() {
  const box = $('#deModBox');
  const n = box.value || box._current;
  if (!n) return toast('Pick a saved Mod in the Mod box first', 'bad');
  if (!confirm('Delete the saved Mod ' + n + '? It goes to the Recycle Bin (your mods folder and Workbench stay as they are).')) return;
  try { await post('de_x_mod_delete', { name: n }); toast(n + ' deleted', 'good'); loadSaved(); } catch (e) { toast(e.message, 'bad'); }
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
  $('#deBuild').onclick = () => startJob('de_build', {}, 'Build into the working disc');
  $('#deRestore').onclick = restoreDialog;
  $('#deNewDat').onclick = newDatDialog;
  $('#deFromGame').onclick = fromGameDialog;
  $('#deDelete').onclick = deleteSelected;
  $('#deDelNew').onclick = deleteNewFiles;
  $('#deOpenFolder').onclick = () => openFolder('mods', folderRel(folder));
  $('#deConvert').onclick = convertDialog;
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

async function addCharacterDialog() {
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
    pick.accept = '.rsc,.rnt,.tex,' + KINDS.map(([k]) => '.' + k).join(',');
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
    grid.append(srcLabel, src, fileLabel, pickWrap, copyLabel, copyBox, el('span', 'muted', 'New code'), code);
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
    b._get = () => ({ own: rOwn.checked, source: src.value, code: code.value.trim().toLowerCase(),
                      copy: KINDS.map(([k]) => k).filter((k) => copyChecks[k].c.checked && !copyChecks[k].c.disabled) });
  }, [{ label: 'Cancel' }, { label: 'Add Character', cls: 'primary', run: async (b, btn) => {
    const v = b._get();
    if (!/^[a-z][a-z0-9_]{1,11}$/.test(v.code)) { toast('The new code: 2 to 12 letters, digits or _, starting with a letter', 'bad'); return true; }
    const body = { source: v.source, code: v.code };
    if (v.own) body.copy = v.copy;
    if (v.own) {
      const rsc = files.find((f) => /\.rsc$/i.test(f.name));
      const rnt = files.find((f) => /\.rnt$/i.test(f.name));
      if (!rsc || !rnt) { toast('Pick your model\'s .rsc and its .rnt (both)', 'bad'); return true; }
      body.rsc_b64 = await readAsB64(rsc);
      body.rnt_b64 = await readAsB64(rnt);
      const sk = files.find((f) => /\.skel$/i.test(f.name));
      if (sk) body.skel_b64 = await readAsB64(sk);
      // the rest: character files of yours (used instead of the copies) and its .tex textures (kept in the Workbench)
      body.extra = [];
      for (const f of files) if (f !== rsc && f !== rnt && f !== sk) body.extra.push({ name: f.name, data: await readAsB64(f) });
    }
    btn.disabled = true; btn.textContent = 'Rebuilding SWAT.DAT…';
    try {
      const r = await post('creature_add', body);
      toast('New character ' + v.code + ': ' + r.added.length + ' files added to SWAT.DAT' + (v.own ? ' (your model)' : '') + '; all its files are in the Workbench (' + v.code + ')', 'good');
      if (A && A.refreshLists) await A.refreshLists();
      if (A && A.refreshCreatures) await A.refreshCreatures(r.name);
    } catch (e) { toast('Not added: ' + e.message, 'bad'); btn.disabled = false; btn.textContent = 'Add Character'; return true; }
  } }], 660);
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
  const addChar = document.querySelector('#crAddChar');
  if (addChar) addChar.onclick = addCharacterDialog;
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
    if (panel !== 'dataeditor') { clearTimeout(pollTimer); pollTimer = null; return; }
    wire();
    refresh();
    loadWorkbench();
    loadSaved();
    if (page === 'settings') settingsView();
  });
}
