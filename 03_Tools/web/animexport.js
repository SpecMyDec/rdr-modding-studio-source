// animexport.js — EXPORT ALL ANIMATIONS TO BLENDER, IN ONE CLICK.
//
// The Animation tab exports one clip at a time. Testers wanted the lot: every
// clip on the disc as a .bvh, a folder per archive and skeleton, plus an
// index.csv that names every clip, its rig, frame and joint counts, and every
// clip that could not be exported with the reason. The work is in
// rdrstudio/animbatch.py; this is the dialog: pick everything or one
// archive, watch it run, cancel it, read the result.

import { call, el, renderProblem } from './transfer.js';

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
  // A run may already be going (dialog closed and reopened).
  poll(true);
}

function close() {
  const d = $('#animAllDlg');
  if (d) d.style.display = 'none';
}

async function start() {
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
