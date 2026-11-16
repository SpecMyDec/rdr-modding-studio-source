// export.js — GETTING WORK OUT, AND A FRIEND'S WORK IN.
//
// Every edit is on the working ISO the moment it is applied, so there is no
// separate build step: the working ISO already is the build. What the Studio
// never had was the last step. The only way out was a .rdrmod.json saved
// into a folder nobody was told the name of, the only way in was to copy one
// into that folder by hand, and a playable disc meant finding
// RDR_MODDED.iso in Explorer.
//
// One dialog, reachable from the header on every screen, with the ways out in
// the order you would share them: a mod pack (a zip of only the changed files,
// the one to post online), a mod file for other Studio users, a patch anyone
// can apply to their own copy, the whole ISO for yourself.

import { call, el, renderProblem } from './transfer.js';
import { saveVersion } from './versions.js';

let A = null;
let info = null;

const $ = (s) => document.querySelector(s);

function fmtSize(n) {
  if (n == null) return '?';
  if (n >= 1e9) return (n / 1e9).toFixed(2) + ' GB';
  if (n >= 1e5) return (n / 1e6).toFixed(1) + ' MB';
  return Math.max(1, Math.round(n / 1024)) + ' KB';
}

function build() {
  if ($('#exportDlg')) return $('#exportDlg');
  const wrap = el('div');
  wrap.id = 'exportDlg';
  wrap.style.cssText = 'display:none;position:fixed;inset:0;z-index:90;'
    + 'background:rgba(0,0,0,.55);overflow:auto';
  wrap.onclick = (e) => { if (e.target === wrap) close(); };
  const box = el('div');
  box.style.cssText = 'max-width:600px;margin:3vh auto 24px;background:var(--bg2);'
    + 'border:1px solid var(--line);border-radius:6px;padding:16px 20px';
  const h = el('h2', null, 'Build & share your mod');
  h.style.cssText = 'margin:0 0 2px;font:var(--t-head);color:var(--title)';
  const sub = el('p', 'dim', 'Every edit is already on your working disc, and Play runs it. '
    + 'Nothing needs building; this is how you take the mod out.');
  sub.style.cssText = 'margin:0 0 14px;font-size:12px';
  box.append(h, sub);

  // FINAL PASS: grouped by what the person wants, in the order they want it.
  // It was four equal rows led by the mod pack, with "playable ISO" -- the
  // answer to "how do I play my mod outside the Studio" -- at the bottom.
  const section = (text) => {
    const s = el('div', 'muted', text);
    s.style.cssText = 'font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
      + 'margin:12px 0 2px';
    return s;
  };
  box.append(section('Play it'));
  box.append(card('Playable ISO', '.iso',
    'Your whole modded game as one file. Runs in PCSX2 as it is.',
    'expIsoName', 'Export ISO', exportIso));
  box.append(section('Share it (no game data inside)'));
  box.append(packCard());
  box.append(card('Patch', '.xdelta',
    'For people without the Studio: they apply it to their own copy of the '
    + 'game with Delta Patcher or xdelta3.',
    'expPatchName', 'Export patch', exportPatch));
  box.append(section('Keep it'));
  box.append(card('Saved version', '.rdrmod.json',
    'A named copy of your mod you can come back to. It is listed under '
    + 'My mod > Versions.',
    null, 'Save a version…', async () => {
      close();
      saveVersion();
    }));

  const foot = el('div');
  foot.style.cssText = 'border-top:1px solid var(--line);margin-top:14px;'
    + 'padding-top:12px';
  const where = el('div', 'muted');
  where.id = 'expWhere';
  where.style.cssText = 'font-size:11px;word-break:break-all';
  const recent = el('div');
  recent.id = 'expRecent';
  recent.style.cssText = 'font-size:11px;margin-top:6px';
  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:8px;margin-top:12px;flex-wrap:wrap';
  const open = el('button', null, 'Open exports folder');
  open.id = 'expOpen';
  open.onclick = () => openFolder('exports');
  const load = el('button', null, 'Load a mod file…');
  load.title = 'Add a .rdrmod.json someone sent you. It is listed under '
    + 'My mod > Versions, where you can put it on your disc.';
  load.onclick = () => importMod();
  const applyPk = el('button', null, 'Apply a mod pack…');
  applyPk.id = 'expApplyPack';
  applyPk.title = 'Add a mod pack .zip someone sent you to your working disc.';
  applyPk.onclick = () => applyPack();
  const spacer = el('span');
  spacer.style.flex = '1';
  const shut = el('button', null, 'Close');
  shut.onclick = close;
  bar.append(open, load, applyPk, spacer, shut);
  foot.append(where, recent, bar);
  box.append(foot);

  const result = el('div');
  result.id = 'expResult';
  result.style.cssText = 'margin-top:10px;font-size:12px';
  box.insertBefore(result, foot);

  wrap.append(box);
  document.body.append(wrap);
  return wrap;
}

// DESIGN-REVIEW A16: three plain rows split by a hairline, neutral buttons.
// They were three bordered cards inside a bordered dialog, each with a
// primary button -- four "main actions" on one screen.
function card(title, ext, blurb, inputId, label, fn) {
  const c = el('div');
  c.style.cssText = 'border-top:1px solid var(--line);padding:10px 0';
  const t = el('div');
  t.append(el('span', null, title));
  const x = el('span', 'muted', '  ' + ext);
  x.style.cssText = 'font:var(--t-mono);font-size:11px';
  t.append(x);
  const b = el('div', 'muted', blurb);
  b.style.cssText = 'font-size:11px;margin:2px 0 8px;line-height:1.45';
  const row = el('div');
  row.style.cssText = 'display:flex;gap:8px;align-items:center';
  if (inputId) {
    const i = el('input');
    i.id = inputId;
    i.placeholder = 'file name';
    i.style.cssText = 'flex:1;min-width:0';
    row.append(i);
  } else {
    const s = el('span');
    s.style.flex = '1';
    row.append(s);
  }
  const go = el('button', null, label);
  go.style.flex = 'none';
  go.onclick = fn;
  row.append(go);
  c.append(t, b, row);
  return c;
}

async function refresh() {
  const r = await call('share_info', 'GET');
  if (!r.ok) { renderProblem($('#expResult'), r.problem); return; }
  info = r;
  const stamp = r.stamp || '';
  if (!$('#expPatchName').value) $('#expPatchName').value = 'RDR_mod_' + stamp;
  if (!$('#expIsoName').value) $('#expIsoName').value = 'RDR_MODDED_' + stamp;
  $('#expWhere').textContent = 'Exports go to ' + r.folder;
  $('#expOpen').style.display = r.can_open_folder ? '' : 'none';
  const box = $('#expRecent');
  box.innerHTML = '';
  for (const f of (r.exports || []).slice(0, 4)) {
    const line = el('div', 'muted', `${f.name} · ${fmtSize(f.size)} · ${f.modified}`);
    box.append(line);
  }
}

function say(html, tone) {
  const host = $('#expResult');
  host.innerHTML = '';
  const d = el('div');
  d.style.cssText = 'padding:8px 10px;border-radius:5px;border:1px solid '
    + (tone === 'bad' ? 'var(--red)' : 'var(--line)');
  d.innerHTML = html;
  host.append(d);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;',
    '>': '&gt;', '"': '&quot;' }[c]));
}

async function runExport(route, name, label) {
  // Every refusal returns null. It used to return renderProblem's value,
  // which is truthy, so a refused export was reported as saved.
  const dry = await call(route, 'POST', { name, dry: true });
  if (!dry.ok) { renderProblem($('#expResult'), dry.problem); return null; }
  const r = await A.withProgress(label, () => call(route, 'POST',
    { name, dry: false }));
  if (!r.ok) { renderProblem($('#expResult'), r.problem); return null; }
  await refresh();
  return r;
}

async function exportPatch() {
  const r = await runExport('export_patch', $('#expPatchName').value.trim(),
    'Writing the patch — reading both discs');
  if (!r) return;
  say(`<b style="color:var(--grn)">Patch saved.</b> ${esc(fmtSize(r.size))}, `
    + `${esc(fmtSize(r.changed_bytes))} of changed data, ${r.seconds}s.<br>`
    + `<span class="mono">${esc(r.path)}</span><br>`
    + `<span class="muted">A .txt beside it says which disc it expects.</span>`
    + (r.note ? `<br><span style="color:var(--warn)">${esc(r.note)}</span>` : ''));
  $('#expPatchName').value = '';
  refresh();
}

async function exportIso() {
  const r = await runExport('export_iso', $('#expIsoName').value.trim(),
    'Copying the working disc — 2.2 GB, then checking the copy');
  if (!r) return;
  say(`<b style="color:var(--grn)">ISO saved and checked.</b> `
    + `${esc(fmtSize(r.size))} in ${r.seconds}s.<br>`
    + `<span class="mono">${esc(r.path)}</span>`);
  $('#expIsoName').value = '';
  refresh();
}

// MOD PACK. A zip of only the files you changed, so a mod can be posted
// without the game. The recipient's Studio checks it against THEIR stock disc.
function packCard() {
  const c = el('div');
  c.style.cssText = 'border-top:1px solid var(--line);padding:10px 0';
  const t = el('div');
  t.append(el('span', null, 'Mod pack'));
  const x = el('span', 'muted', '  .zip');
  x.style.cssText = 'font:var(--t-mono);font-size:11px';
  t.append(x);
  const b = el('div', 'muted', 'Every change: the files you changed on the disc and every file in the Data Editor\'s '
    + '.dat folders (built or not, new archives too), no game data. Post it anywhere; others add it with Import mod.');
  b.style.cssText = 'font-size:11px;margin:2px 0 8px;line-height:1.45';
  const row1 = el('div');
  row1.style.cssText = 'display:flex;gap:8px;margin-bottom:6px';
  const nm = el('input');
  nm.id = 'expPackName';
  nm.placeholder = 'mod name';
  nm.style.cssText = 'flex:1;min-width:0';
  const au = el('input');
  au.id = 'expPackAuthor';
  au.placeholder = 'your name (optional)';
  au.style.cssText = 'flex:1;min-width:0';
  row1.append(nm, au);
  const row2 = el('div');
  row2.style.cssText = 'display:flex;gap:8px;align-items:center';
  const ds = el('input');
  ds.id = 'expPackDesc';
  ds.placeholder = 'what it does (optional)';
  ds.style.cssText = 'flex:1;min-width:0';
  const go = el('button', null, 'Export changes');
  go.id = 'expPackGo';
  go.style.flex = 'none';
  go.onclick = exportPack;
  row2.append(ds, go);
  const wbl = el('label', 'muted');
  wbl.style.cssText = 'display:flex;gap:6px;align-items:center;font-size:11px;margin-top:6px';
  const wb = el('input'); wb.type = 'checkbox'; wb.id = 'expPackWorkbench';
  wbl.append(wb, el('span', null, 'Include the Data Editor\'s Workbench too (its working copies; can be large)'));
  c.append(t, b, row1, row2, wbl);
  return c;
}

async function exportPack() {
  const name = $('#expPackName').value.trim();
  if (!name) { $('#expPackName').focus(); return say('Give the mod a name.', 'bad'); }
  const body = { name, author: $('#expPackAuthor').value.trim(),
    description: $('#expPackDesc').value.trim(),
    include_workbench: !!($('#expPackWorkbench') && $('#expPackWorkbench').checked) };
  const dry = await call('pack_export', 'POST', { ...body, dry: true });
  if (!dry.ok) return renderProblem($('#expResult'), dry.problem);
  const r = await A.withProgress('Comparing your disc with stock — about a minute',
    () => call('pack_export', 'POST', { ...body, dry: false }));
  if (!r.ok) return renderProblem($('#expResult'), r.problem);
  const skipped = (r.not_included || []).length
    ? `<br><span style="color:var(--warn)">Not included (brand-new .dat files): `
      + `${esc(r.not_included.join(', '))}</span>` : '';
  const deN = r.data_editor || {};
  say(`<b style="color:var(--grn)">Mod pack saved.</b> ${r.files} file(s) `
    + `(${r.modified} changed, ${r.added} new)`
    + ((deN.mods || deN.workbench) ? `, ${deN.mods || 0} Data Editor .dat folder file(s)` + (deN.workbench ? `, ${deN.workbench} Workbench file(s)` : '') : '')
    + `, ${esc(fmtSize(r.size))}.<br>`
    + `<span class="mono">${esc(r.path)}</span><br>`
    + `<span class="muted">Post that .zip. It holds no game data.</span>`
    + skipped);
  refresh();
}

function readB64(file) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(',')[1] || '');
    fr.onerror = () => rej(fr.error);
    fr.readAsDataURL(file);
  });
}

export function applyPack() {
  const pick = el('input');
  pick.type = 'file';
  pick.accept = '.zip';
  pick.style.display = 'none';
  document.body.append(pick);
  pick.onchange = async () => {
    const f = pick.files && pick.files[0];
    pick.remove();
    if (!f) return;
    let b64;
    try { b64 = await readB64(f); } catch (e) {
      return A.toast('Could not read that file: ' + e.message, 'bad');
    }
    if (!$('#exportDlg') || $('#exportDlg').style.display !== 'block') open();
    const r = await A.withProgress('Checking the mod pack against your stock disc',
      () => call('pack_apply', 'POST', { zip_b64: b64, dry: true }));
    if (!r.ok) return renderProblem($('#expResult'), r.problem);
    showPackPlan(b64, r);
  };
  pick.click();
}

function showPackPlan(b64, r) {
  const host = $('#expResult');
  host.innerHTML = '';
  const box = el('div');
  box.style.cssText = 'padding:8px 10px;border-radius:5px;border:1px solid var(--line)';
  const p = r.pack || {};
  const n = (r.counts.apply || 0) + (r.counts.add || 0);
  const conf = r.conflicts || [];
  const head = el('div');
  head.innerHTML = `<b>${esc(p.name || 'Mod pack')}</b>`
    + (p.author ? ` <span class="muted">by ${esc(p.author)}</span>` : '');
  box.append(head);
  if (p.description) box.append(el('div', 'muted', p.description));
  const cmods = p.codemods || [];
  const sum = el('div', null, `${n} file(s) to add to your disc`
    + (r.counts.already ? `, ${r.counts.already} already there` : '') + '.');
  sum.style.marginTop = '4px';
  box.append(sum);
  const deIn = r.data_editor || {};
  const deN = (deIn.mods || 0) + (deIn.workbench || 0);
  if (deN) {
    const d = el('div', null, `${deIn.mods || 0} file(s) into the Data Editor's .dat folders`
      + (deIn.workbench ? `, ${deIn.workbench} into its Workbench` : '')
      + ' (files of the same name there are replaced). The next Build puts the .dat folder files on the disc.');
    d.style.marginTop = '4px';
    box.append(d);
  }
  if (cmods.length) {
    const cm = el('div', null, 'Code mods: ' + cmods.join(', ') + '. Your Studio builds a NEW disc '
      + '(02_Modded/RDR_CODEMODS.iso) from your own stock game; your working disc stays free of code mods.');
    cm.style.cssText = 'margin-top:4px;color:var(--warn)';
    box.append(cm);
  }
  if ((p.not_included || []).length) {
    box.append(el('div', 'muted', 'The author did not include brand-new .dat '
      + 'files: ' + p.not_included.join(', ')));
  }
  if (conf.length) {
    const w = el('div');
    w.style.cssText = 'margin-top:6px;color:var(--warn)';
    w.append(el('b', null, `${conf.length} file(s) were already changed on your disc:`));
    const ul = el('div', 'mono');
    ul.style.cssText = 'font-size:11px;max-height:110px;overflow:auto;color:var(--ink)';
    for (const c of conf.slice(0, 40)) {
      ul.append(el('div', null, `${c.name} — ${c.reason}`));
    }
    w.append(ul);
    box.append(w);
  }
  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:8px;margin-top:8px';
  const go = el('button', conf.length ? null : 'primary',
    conf.length ? `Apply and overwrite ${conf.length}` : 'Apply');
  go.disabled = !n && !conf.length && !cmods.length && !deN;
  go.onclick = async () => {
    go.disabled = true;
    const done = await A.withProgress('Putting the mod on your disc',
      () => call('pack_apply', 'POST', { zip_b64: b64, dry: false,
        allow_conflicts: conf.length > 0 }));
    if (!done.ok) return renderProblem(host, done.problem);
    const bad = (done.failed || []).length;
    say((bad ? `<b style="color:var(--warn)">Applied with ${bad} problem(s).</b> `
      : `<b style="color:var(--grn)">Applied and checked.</b> `)
      + `${done.written} file(s) written and read back`
      + (done.added ? ` (${done.added} new)` : '')
      + (done.already ? `, ${done.already} already there` : '') + '.'
      + ((done.data_editor_written && (done.data_editor_written.mods || done.data_editor_written.workbench))
        ? `<br>${done.data_editor_written.mods} file(s) into the Data Editor's .dat folders`
          + (done.data_editor_written.workbench ? `, ${done.data_editor_written.workbench} into its Workbench` : '')
          + ': <b>Build</b> (top bar) puts them on the disc.' : '')
      + (bad ? '<br>' + done.failed.slice(0, 5).map(
          (x) => esc(x.name + ': ' + x.error)).join('<br>') : '')
      + (done.codemods_build ? (done.codemods_build.started
          ? '<br>Building the code-mod disc: <span class="mono">' + esc(done.codemods_build.output)
            + '</span> (a few minutes; progress on My mod > Code Mods).'
          : '<br><b style="color:var(--warn)">Code-mod disc NOT built: '
            + esc(done.codemods_build.problem || '') + '</b>') : '')
      + '<br><span class="muted">Ctrl+Z undoes it (new files stay).</span>',
      bad ? 'bad' : undefined);
    window.dispatchEvent(new CustomEvent('rdr:disc-state'));
    window.dispatchEvent(new CustomEvent('rdr:mods-changed'));
  };
  const no = el('button', null, 'Cancel');
  no.onclick = () => { host.innerHTML = ''; };
  bar.append(go, no);
  box.append(bar);
  host.append(box);
}

async function openFolder(which) {
  const r = await call('export_open', 'POST', { which });
  if (!r.ok) A.toast(r.problem.message + (r.problem.fix ? '\n' + r.problem.fix : ''));
}

export function importMod() {
  // In the document, not detached: the Studio's other file inputs are, and
  // probe_fileinput.py measured THAT shape opening a dialog in the desktop
  // window.
  const pick = el('input');
  pick.type = 'file';
  pick.accept = '.json,.rdrmod.json';
  pick.style.display = 'none';
  document.body.append(pick);
  pick.onchange = async () => {
    const f = pick.files && pick.files[0];
    pick.remove();
    if (!f) return;
    let text;
    try { text = await f.text(); } catch (e) {
      return A.toast('Could not read that file: ' + e.message, 'bad');
    }
    const r = await call('mod_import', 'POST', { doc: text, filename: f.name });
    if (!r.ok) return A.toast(r.problem.message, 'bad');
    A.toast(`Added "${r.name}" — ${r.files} file(s). `
      + 'Put it on your disc from My mod > Versions.', 'good');
    window.dispatchEvent(new CustomEvent('rdr:mods-changed'));
  };
  pick.click();
}

export function open() {
  build().style.display = 'block';
  $('#expResult').innerHTML = '';
  refresh();
}

function close() {
  const d = $('#exportDlg');
  if (d) d.style.display = 'none';
}

// New mod: the current one kept as a pack of everything, then a clean start (modpack.post_new_mod).
async function newMod() {
  const dry = await call('new_mod', 'POST', { dry: true, name: 'x' });
  if (!dry.ok) return A.toast(dry.problem.message, 'bad');
  const name = prompt('A name for the new mod:', '');
  if (name == null) return;
  const keep = confirm(`Keep "${dry.current}" first?\n\nOK: it is saved as a mod pack of everything (disc changes, `
    + `.dat folders, Workbench) in your exports folder; Import mod brings it back.\nCancel: it is not kept.`);
  if (!confirm(`Start "${name || 'New mod'}"?\n\nThe Data Editor's .dat folders and Workbench are emptied and the `
    + `working disc is reset to stock.` + (keep ? '' : '\n\nThe current mod is NOT kept.'))) return;
  const r = await A.withProgress('Starting a new mod' + (keep ? ': keeping the current one, then resetting the disc' : ''),
    () => call('new_mod', 'POST', { dry: false, name, keep }));
  if (!r.ok) return A.toast(r.problem.message, 'bad');
  alert(`"${r.name}" started: a clean working disc, empty .dat folders and Workbench.`
    + (r.saved ? `\n\nThe previous mod is kept as:\n${r.saved}\n(Import mod brings it back.)` : ''));
  location.reload();
}

export function init(bridge) {
  A = bridge;
  const b = $('#hdrExport');
  if (b) b.onclick = open;
  const nm = $('#hdrNewMod');
  if (nm) nm.onclick = () => newMod().catch((e) => A.toast(e.message, 'bad'));
  const ip = $('#hdrImportPack');
  if (ip) ip.onclick = applyPack;
  const save = $('#btnSaveMod');
  if (save) save.onclick = () => saveVersion();
  const share = $('#btnShareMod');
  if (share) share.onclick = open;
  const ap = $('#btnApplyPack');
  if (ap) ap.onclick = applyPack;
  const imp = $('#btnImportMod');
  if (imp) imp.onclick = importMod;
  addEventListener('rdr:mods-changed', () => {
    if ($('#tab-mods')?.classList.contains('on')) A.showTab('mods');
  });
  addEventListener('rdr:disc-state', () => A.refreshHistory?.());
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('#exportDlg')?.style.display === 'block') close();
  });
}
