// animexport.js — EXPORT ALL ANIMATIONS TO BLENDER, IN ONE CLICK.
//
// The Animation tab exports one clip at a time. Testers wanted the lot: every
// clip on the disc as a .bvh, a folder per archive and skeleton, plus an
// index.csv that names every clip, its rig, frame and joint counts, and every
// clip that could not be exported with the reason. The work is in
// rdrstudio/animbatch.py; this is the dialog: pick everything or one
// archive, watch it run, cancel it, read the result.

import { call, el, renderProblem } from './transfer.js';
import { bakeClipGlb, decodeAnim, parseSkeleton } from './anim3d.js';

let A = null;
let timer = null;
const $ = (s) => document.querySelector(s);

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;',
    '>': '&gt;', '"': '&quot;' }[c]));
}

function build() {
  if ($('#animAllDlg')) return $('#animAllDlg');
  const wrap = el('div');
  wrap.id = 'animAllDlg';
  wrap.style.cssText = 'display:none;position:fixed;inset:0;z-index:90;'
    + 'background:rgba(0,0,0,.55);overflow:auto';
  wrap.onclick = (e) => { if (e.target === wrap) close(); };
  const box = el('div');
  box.style.cssText = 'max-width:560px;margin:9vh auto 24px;'
    + 'background:var(--bg2);border:1px solid var(--line);'
    + 'border-radius:6px;padding:16px 20px';
  const h = el('h2', null, 'Export all animations');
  h.style.cssText = 'margin:0 0 2px;font:var(--t-head);color:var(--title)';
  const sub = el('p', 'dim', 'Every clip becomes a .bvh Blender opens '
    + 'directly, filed by archive and skeleton, with an index.csv listing '
    + 'each clip and any that could not be exported.');
  sub.style.cssText = 'margin:0 0 12px;font-size:12px;line-height:1.5';
  const form = el('div');
  form.id = 'animAllForm';
  form.innerHTML = `
    <div class="field"><label>Export as</label>
      <select id="animAllMode" style="width:100%">
        <option value="bvh">.bvh — the skeleton only</option>
        <option value="model">.glb — with the current model</option></select>
      <div id="animAllModeNote" class="muted" style="font-size:11px;margin-top:3px"></div></div>
    <div class="field"><label>Which clips</label>
      <select id="animAllArc" style="width:100%"></select></div>
    <div class="field"><label>Folder name</label>
      <input id="animAllName" style="width:100%" placeholder="animations_…"></div>
    <div class="field"><label>Rotation order</label>
      <select id="animAllEuler" style="width:100%">
        <option>YZX</option><option>XYZ</option><option>XZY</option>
        <option>YXZ</option><option>ZXY</option><option>ZYX</option></select>
      <div class="muted" style="font-size:11px;margin-top:3px">YZX is the
        engine's own order; Blender calls it XZY.</div></div>`;
  const where = el('div', 'muted');
  where.id = 'animAllWhere';
  where.style.cssText = 'font-size:11px;word-break:break-all;margin:6px 0';
  const prog = el('div');
  prog.id = 'animAllProg';
  prog.style.cssText = 'display:none;margin:10px 0';
  prog.innerHTML = `<div style="height:8px;background:var(--line);
      border-radius:4px;overflow:hidden"><div id="animAllBar" style="height:8px;
      width:0;background:var(--acc, #4a90d9)"></div></div>
    <div id="animAllText" class="muted" style="font-size:11px;margin-top:4px;
      word-break:break-all"></div>`;
  const res = el('div');
  res.id = 'animAllResult';
  res.style.cssText = 'font-size:12px;line-height:1.55;margin-top:8px';
  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:8px;margin-top:12px;flex-wrap:wrap';
  const go = el('button', 'primary', 'Export');
  go.id = 'animAllGo';
  go.onclick = start;
  const cancel = el('button', null, 'Cancel export');
  cancel.id = 'animAllCancel';
  cancel.style.display = 'none';
  cancel.onclick = async () => {
    cancel.disabled = true;
    if (glbRun) { glbRun.cancel = true; return; }
    await call('job_cancel', 'POST', {});
  };
  const open = el('button', null, 'Open exports folder');
  open.onclick = async () => {
    const r = await call('export_open', 'POST', { which: 'exports' });
    if (!r.ok) A.toast(r.problem.message);
  };
  const done = el('button', null, 'Close');
  done.id = 'animAllClose';
  done.style.marginLeft = 'auto';
  done.onclick = close;
  bar.append(go, cancel, open, done);
  box.append(h, sub, form, where, prog, res, bar);
  wrap.append(box);
  document.body.append(wrap);
  return wrap;
}

async function open() {
  const dlg = build();
  dlg.style.display = 'block';
  const r = await call('anims_export_info', 'GET');
  if (!r.ok) return renderProblem($('#animAllResult'), r.problem);
  const sel = $('#animAllArc');
  sel.innerHTML = '';
  const all = el('option', null, `Everything on the disc (${r.total} clips)`);
  all.value = '';
  sel.append(all);
  for (const a of r.archives) {
    const o = el('option', null, `${a.archive} (${a.clips})`);
    o.value = a.archive;
    sel.append(o);
  }
  if (!$('#animAllName').value) {
    const d = new Date(), p = (n) => String(n).padStart(2, '0');
    $('#animAllName').value = 'animations_' + d.getFullYear() + p(d.getMonth() + 1)
      + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes());
  }
  $('#animAllWhere').textContent = 'Saved under ' + r.folder;
  modeNote();
  $('#animAllMode').onchange = modeNote;
  // A run may already be going (dialog closed and reopened).
  poll(true);
}

// ---- .glb with the current model ------------------------------------------
//
// Done here in the page, not by the server's batch: posing the skinned model is
// the viewer's code (anim3d.js), so each clip is baked with it -- bakeClipGlb,
// the same writer as "Export animation with current model" -- and handed to the
// server to save. Every clip that plays on the skeleton the model is on (the
// engine's rule: the same bone count, or one either side) goes in
// animations/<model>.anim/, one <clip>.glb each, with an index.csv.

let glbRun = null;

function modeNote() {
  const st = A.animModelState && A.animModelState();
  const note = $('#animAllModeNote');
  const model = $('#animAllMode').value === 'model';
  $('#animAllName').disabled = model;
  $('#animAllEuler').disabled = model;
  if (!model) { note.textContent = ''; return; }
  note.textContent = st
    ? `${st.stem} on ${st.skelName}: every clip that plays on that skeleton, in a folder "${st.stem}.anim". `
      + `The rotation order is the viewer's (${st.order}).`
    : 'Load a clip and pick a character model in the Animations panel first.';
}

async function startGlb() {
  const st = A.animModelState && A.animModelState();
  if (!st) return A.toast('Load a clip and pick a character model first.', 'bad');
  const nbones = parseSkeleton(st.skelText).length;
  const folder = st.stem + '.anim';
  const res = $('#animAllResult');
  res.innerHTML = '';
  // the clips: one archive, or every archive the info lists
  const info = await call('anims_export_info', 'GET');
  if (!info.ok) return renderProblem(res, info.problem);
  const arcs = $('#animAllArc').value ? [$('#animAllArc').value] : info.archives.map((a) => a.archive);
  glbRun = { cancel: false };
  const run = glbRun;
  busy(true);
  const text = (t) => { $('#animAllText').textContent = t; };
  text('Listing the clips…');
  const clips = [];
  for (const arc of arcs) {
    try {
      const t = await A.api(`toc?archive=${encodeURIComponent(arc)}&ext=anim&limit=20000`);
      for (const f of (t.rows || t.files || t || [])) clips.push([arc, f.name || f]);
    } catch { /* an archive that does not list: skipped */ }
  }
  // the model's textures, once
  const pngs = {};
  for (const g of st.rig.groups || []) {
    if (g.texture === null || g.texture === undefined || g.texture in pngs) continue;
    try { pngs[g.texture] = new Uint8Array(await (await fetch(st.texUrl(g.texture))).arrayBuffer()); }
    catch { pngs[g.texture] = null; }
  }
  const seen = new Set();
  const rows = [['clip', 'archive', 'keys', 'bones', 'result']];
  let done = 0, wrote = 0, skipped = 0, failed = 0, outDir = '';
  const t0 = performance.now();
  for (const [arc, clip] of clips) {
    if (run.cancel) break;
    done++;
    $('#animAllBar').style.width = Math.round(100 * done / clips.length) + '%';
    text(`${done} / ${clips.length} clips · ${wrote} written · ${arc} ${clip}`);
    if (seen.has(clip.toLowerCase())) { skipped++; continue; }    // the same clip in another archive
    let buf;
    try {
      buf = await (await fetch(`/api/file.bin?archive=${encodeURIComponent(arc)}&name=${encodeURIComponent(clip)}`)).arrayBuffer();
    } catch (e) { failed++; rows.push([clip, arc, '', '', 'not read: ' + e.message]); continue; }
    let a;
    try { a = decodeAnim(buf); } catch (e) { failed++; rows.push([clip, arc, '', '', 'not decoded: ' + e.message]); continue; }
    if (Math.abs(a.bones - nbones) > 1) { skipped++; continue; }   // another skeleton's clip
    try {
      const r = bakeClipGlb({ animBuf: buf, skelText: st.skelText, rig: st.rig, pngs, order: st.order,
                              retarget: st.retarget, clipName: clip });
      let bin = '';
      for (let i = 0; i < r.bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, r.bytes.subarray(i, i + 0x8000));
      const sv = await call('anim_glb_save', 'POST', { folder, name: clip.replace(/\.anim$/i, '') + '.glb', data: btoa(bin) });
      if (!sv.ok) throw new Error((sv.problem && sv.problem.message) || 'not saved');
      outDir = sv.folder || outDir;
      seen.add(clip.toLowerCase());
      wrote++;
      rows.push([clip, arc, a.keys, a.bones, 'ok']);
    } catch (e) { failed++; rows.push([clip, arc, a.keys, a.bones, String(e.message || e)]); }
  }
  const cell = (v) => '"' + String(v).split('"').join('""') + '"';
  const csv = rows.map((r) => r.map(cell).join(',')).join('\r\n') + '\r\n';
  const idx = await call('anim_glb_save', 'POST', { folder, name: 'index.csv', text: csv });
  if (idx.ok) outDir = idx.folder || outDir;
  busy(false);
  const secs = Math.round((performance.now() - t0) / 1000);
  res.innerHTML = (run.cancel ? '<b style="color:var(--warn)">Cancelled.</b> ' : '<b style="color:var(--grn)">Done.</b> ')
    + `${wrote} clip(s) on ${esc(st.stem)} exported as .glb in ${secs}s`
    + (skipped ? ` (${skipped} skipped: another skeleton's, or the same clip in another archive)` : '') + '.'
    + `<br><span class="mono" style="word-break:break-all">${esc(outDir)}</span>`
    + '<br><span class="muted">Blender: File &gt; Import &gt; glTF 2.0. index.csv lists every clip.</span>'
    + (failed ? `<br><span style="color:var(--warn)">${failed} clip(s) could not be exported (in index.csv with the reason).</span>` : '');
  glbRun = null;
}

function close() {
  const d = $('#animAllDlg');
  if (d) d.style.display = 'none';
}

async function start() {
  if ($('#animAllMode').value === 'model') return startGlb();
  const arc = $('#animAllArc').value;
  const body = { folder: $('#animAllName').value.trim(),
    euler: $('#animAllEuler').value };
  if (arc) body.archives = [arc];
  const dry = await call('anims_export', 'POST', { ...body, dry: true });
  if (!dry.ok) return renderProblem($('#animAllResult'), dry.problem);
  const r = await call('anims_export', 'POST', { ...body, dry: false });
  if (!r.ok) return renderProblem($('#animAllResult'), r.problem);
  $('#animAllResult').innerHTML = '';
  poll(false);
}

function busy(on) {
  $('#animAllGo').disabled = on;
  $('#animAllCancel').style.display = on ? '' : 'none';
  $('#animAllCancel').disabled = false;
  $('#animAllProg').style.display = on ? 'block' : $('#animAllProg').style.display;
}

async function poll(quiet) {
  clearTimeout(timer);
  const st = await call('anims_export_status', 'GET');
  if (!st.ok) return;
  if (st.state === 'idle') return;
  const running = st.state === 'running';
  if (quiet && !running) return;
  busy(running);
  const pct = st.total ? Math.round(100 * st.done / st.total) : 0;
  $('#animAllBar').style.width = pct + '%';
  $('#animAllText').textContent = `${st.done} / ${st.total} clips · `
    + `${st.seconds}s · ${st.current || ''}`;
  if (running) { timer = setTimeout(() => poll(false), 700); return; }
  showResult(st);
}

function showResult(st) {
  const host = $('#animAllResult');
  if (st.state === 'error') {
    host.innerHTML = `<b style="color:var(--red)">The export stopped.</b> `
      + esc(st.error || '');
    return;
  }
  const m = st.summary || {};
  const head = st.state === 'cancelled'
    ? `<b style="color:var(--warn)">Cancelled.</b> ${m.exported} clip(s) were `
      + 'written before it stopped.'
    : `<b style="color:var(--grn)">Done.</b> ${m.exported} of ${m.total} `
      + `clip(s) exported in ${m.seconds}s.`;
  let html = head + `<br><span class="mono" style="word-break:break-all">`
    + `${esc(m.outdir || st.outdir)}</span><br><span class="muted">index.csv `
    + 'lists every clip, its skeleton, frames and joints.</span>';
  if (m.failed) {
    html += `<br><span style="color:var(--warn)">${m.failed} clip(s) could not `
      + 'be exported (listed in index.csv with the reason).</span>';
    const f = (m.failures || []).slice(0, 5)
      .map((x) => `${esc(x.archive)} ${esc(x.clip)}: ${esc(x.why)}`);
    if (f.length) html += '<br><span class="muted" style="font-size:11px">'
      + f.join('<br>') + '</span>';
  }
  host.innerHTML = html;
}

export function init(bridge) {
  A = bridge;
  const b = $('#animExportAllBtn');
  if (b) b.onclick = open;
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('#animAllDlg')?.style.display === 'block') close();
  });
}
