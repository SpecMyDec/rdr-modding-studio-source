// codemods.js -- PROJECT > Code Mods.
//
// Noclip, Freeze World and Camera Lock, built into the game's own executable and Cheats menu.
// The hooks and addresses are ported from community noclip research; this
// panel is the checklist and the one build button. It writes ONE new file,
// 02_Modded/RDR_CODEMODS.iso -- never the stock disc, never the working disc.
//
// Status honesty: each row says PROVEN or NOT YET TESTED, from codemods.PROVEN on the server --
// only what was seen working in PCSX2 is in there (05_Docs/CODEMODS.md has the evidence).

import { call, renderProblem, onPanel, el } from './transfer.js';

let A = null;
let data = null;
let timer = null;


function stateText(st) {
  if (!st || !st.exists) return 'not there';
  if (st.error) return st.error;
  if (st.features === null) return 'unknown executable';
  if (!st.features.length) return 'no code mods';
  return 'has: ' + st.features.join(', ');
}

function render() {
  const host = document.querySelector('#cmBody');
  host.innerHTML = '';
  if (!data) return;
  const intro = el('p', 'muted',
    'Pick the mods, press Build: the Studio makes a patched COPY of the disc ' +
    '(RDR_CODEMODS.iso) with matching rows in the game’s Pause > Cheats menu. Your stock and working ' +
    'discs are never touched. Play that copy in PCSX2, not the working disc.');
  intro.style.cssText = 'max-width:640px';
  host.append(intro);

  const list = el('div');
  list.style.cssText = 'margin:10px 0';
  for (const f of data.features) {
    const row = el('label');
    row.style.cssText = 'display:block;padding:8px 10px;border:1px solid var(--line);margin:6px 0;max-width:640px;cursor:pointer';
    const cb = el('input');
    cb.type = 'checkbox';
    cb.dataset.key = f.key;
    cb.checked = !f.research;   // research instruments are OFF until you tick them
    cb.style.marginRight = '8px';
    const t = el('b', null, f.title);
    if (f.research) { const tag = el('span', 'muted', ' [Research tool - not a cheat]'); tag.style.cssText = 'color:#9c6b00;font-size:12px'; t.append(tag); }
    const proof = data.proven?.[f.key];
    const status = el('span', 'muted', '  ' + (proof ? '· tested in game' : '· not tested in game yet'));
    if (proof) status.title = 'Tested ' + proof;
    const h = el('div', 'muted', f.help + '  Controls: ' + f.controls + (f.row ? '  (Cheats row: "' + f.row + '")' : '  (no row: it is always on)'));
    h.style.cssText = 'margin:4px 0 0 24px;font-size:12px';
    row.append(cb, t, status, h);
    list.append(row);
  }
  host.append(list);

  const baseRow = el('div');
  baseRow.style.cssText = 'margin:8px 0;max-width:640px';
  baseRow.append(el('span', null, 'Build on top of: '));
  const sel = el('select');
  sel.id = 'cmBase';
  for (const [v, label] of [['working', 'my working disc (keeps my other mods)'], ['stock', 'the stock disc']]) {
    const o = el('option', null, label);
    o.value = v;
    sel.append(o);
  }
  baseRow.append(sel);
  host.append(baseRow);

  const st = el('div', 'muted');
  st.style.cssText = 'font-size:12px;margin:6px 0;max-width:640px;line-height:1.5';
  st.innerHTML = '';
  st.append(
    el('div', null, 'Stock disc: ' + stateText(data.stock)),
    el('div', null, 'Working disc: ' + stateText(data.working)),
    el('div', null, 'Output: ' + data.output_path + ' - ' + stateText(data.built))
  );
  host.append(st);

  // The Build button used to sit below seven tall rows, off-screen at 768px.
  // It lives in the tab's top bar now, always in view.
  const bar = document.querySelector('#cmBarSlot') || el('div');
  bar.innerHTML = '';
  const go = el('button', 'primary', 'Build patched disc');
  go.id = 'cmBuild';
  const job = data.job || {};
  go.disabled = job.state === 'running';
  go.onclick = build;
  bar.append(go);
  if (!bar.id) host.append(bar);

  // Share as a small pack: no game bytes, the recipient's Studio rebuilds the patch from THEIR stock disc.
  const pk = el('div');
  pk.style.cssText = 'margin:10px 0;max-width:640px;border-top:1px solid var(--line);padding-top:8px';
  pk.append(el('div', 'muted', 'Or share just the mods as a small mod pack (.zip, no game data). ' +
    'The other person applies it with Build & share > Apply a mod pack on their own stock disc; ' +
    'their Studio builds the patched disc. Your normal file changes ride along in the same pack.'));
  pk.firstChild.style.cssText = 'font-size:12px;margin-bottom:6px';
  const prow = el('div');
  prow.style.cssText = 'display:flex;gap:8px;align-items:center';
  const pn = el('input');
  pn.id = 'cmPackName';
  pn.placeholder = 'pack name';
  pn.value = 'Code mods';
  pn.style.cssText = 'flex:1;min-width:0';
  const pa = el('input');
  pa.id = 'cmPackAuthor';
  pa.placeholder = 'your name (optional)';
  pa.style.cssText = 'flex:1;min-width:0';
  const pb = el('button', null, 'Export as mod pack');
  pb.id = 'cmPackExport';
  pb.style.flex = 'none';
  pb.onclick = exportPack;
  prow.append(pn, pa, pb);
  pk.append(prow);
  const pr = el('div', 'muted');
  pr.id = 'cmPackResult';
  pr.style.cssText = 'font-size:12px;margin-top:6px';
  pk.append(pr);
  host.append(pk);

  const fit = data.fit;
  if (fit && fit.fits === false) {
    const w = el('p', 'bad', 'Warning: the Cheats page does not fit this disc: ' + (fit.error || (fit.page_deflated + ' > ' + fit.smallest_slot + ' bytes')));
    host.append(w);
  }

  const logBox = el('pre');
  logBox.id = 'cmLog';
  logBox.style.cssText = 'max-width:640px;white-space:pre-wrap;font-size:12px;border:1px solid var(--line);padding:8px;min-height:60px';
  logBox.textContent = (job.log || []).join('\n') || 'Nothing built yet this session.';
  host.append(logBox);

  const notes = el('details');
  notes.innerHTML = '<summary style="cursor:pointer;color:var(--gold)">Good to know</summary>' +
    '<ul style="max-width:640px;font-size:12px;line-height:1.5">' +
    '<li>Load <b>RDR_CODEMODS.iso</b> in PCSX2 (not the working disc). The mods start Off every boot; switch them on in <b>Pause &gt; Cheats</b> or with the button combos.</li>' +
    '<li>Combos: L3+R3 noclip, R3+Triangle freeze, R3+Circle camera lock. Noclip: left stick flies level to the camera, X up, Square down, R2 fast, L2 slow.</li>' +
    '<li>The patched game executable has a different CRC from the stock one, so PCSX2 treats it as a different game: per-game settings (and a memory card assigned by CRC) do not carry over.</li>' +
    '<li>The disc’s backup file listing (UDF) still points at the original executable; PCSX2 and the PS2 BIOS read the main listing, which is updated. Real hardware is not tested.</li>' +
    '<li>Mission triggers still fire while you fly through them; do not save right after noclipping through one.</li>' +
    '</ul>';
  notes.style.maxWidth = '640px';
  host.append(notes);

  clearTimeout(timer);
  if (job.state === 'running') timer = setTimeout(poll, 2000);
}

async function poll() {
  const j = await call('codemods_status', 'GET');
  if (j && j.state) {
    if (data) data.job = j;
    if (j.state === 'running') {
      const box = document.querySelector('#cmLog');
      if (box) box.textContent = (j.log || []).join('\n');
      timer = setTimeout(poll, 2000);
    } else {
      refresh();
    }
  }
}

async function build() {
  const feats = [...document.querySelectorAll('#cmBody input[data-key]')]
    .filter((c) => c.checked).map((c) => c.dataset.key);
  if (!feats.length) {
    document.querySelector('#cmLog').textContent = 'Tick at least one code mod first.';
    return;
  }
  const base = document.querySelector('#cmBase').value;
  const r = await call('codemods_build', 'POST', { features: feats, base });
  if (!r.ok) {
    document.querySelector('#cmLog').textContent = r.problem?.message || r.problem || 'Could not start the build.';
    return;
  }
  document.querySelector('#cmBuild').disabled = true;
  document.querySelector('#cmLog').textContent = 'Started...';
  timer = setTimeout(poll, 1500);
}

async function exportPack() {
  const out = document.querySelector('#cmPackResult');
  const feats = [...document.querySelectorAll('#cmBody input[data-key]')]
    .filter((c) => c.checked).map((c) => c.dataset.key);
  if (!feats.length) { out.textContent = 'Tick at least one code mod first.'; return; }
  const name = document.querySelector('#cmPackName').value.trim() || 'Code mods';
  const body = { name, author: document.querySelector('#cmPackAuthor').value.trim(),
    description: 'Code mods: ' + feats.join(', '), features: feats };
  out.textContent = 'Exporting...';
  const dry = await call('pack_export', 'POST', { ...body, dry: true });
  if (dry && dry.ok === false) { out.textContent = ''; renderProblem(out, dry.problem); return; }
  const r = await call('pack_export', 'POST', { ...body, dry: false });
  if (r && r.ok === false) { out.textContent = ''; renderProblem(out, r.problem); return; }
  out.textContent = 'Saved ' + r.path + ' (' + r.size + ' bytes; code mods: ' + (r.codemods || []).join(', ') +
    '; ' + r.files + ' file change(s) included). It holds no game data.';
}

async function refresh() {
  const host = document.querySelector('#cmBody');
  const r = await call('codemods', 'GET');
  if (r && r.ok === false) { host.innerHTML = ''; renderProblem(host, r.problem); return; }
  data = r;
  render();
}

export function init(bridge) {
  A = bridge;
  onPanel((panel) => {
    if (panel !== 'codemods') { clearTimeout(timer); return; }
    refresh();
  });
}
