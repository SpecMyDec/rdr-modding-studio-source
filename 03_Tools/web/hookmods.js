// hookmods.js -- the Script Hooks tab: the code the Studio puts into the game for you that can be switched (the
// trainer, the unlock of added maps and characters), each one on or off for the PS2 and the Xbox, and the trainer's
// controller mapping (rdrstudio/hookmods.py). The second lineup, its background, own textures and Coliseum are always
// on and not listed.
import { onPanel } from './transfer.js';

const $ = (s) => document.querySelector(s);
let data = null;
let editing = null;      // the control whose buttons are being picked
let picked = [];
let listening = null;    // the controller listen's timer

async function call(route, body) {
  const r = await fetch('/api/' + route, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                           body: JSON.stringify(body || {}) });
  const j = await r.json();
  if (j && j.ok === false) throw new Error(j.error_text || j.error || 'refused');
  return j;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function toast(msg, kind) { if (window.rdrToast) window.rdrToast(msg, kind); }

function xboxName(ps2) {
  const b = (data.buttons || []).find((x) => x.ps2 === ps2);
  return b ? b.xbox : ps2;
}
const PRETTY = { CROSS: '✕ Cross', CIRCLE: '○ Circle', SQUARE: '□ Square', TRIANGLE: '△ Triangle' };
function ps2Name(n) { return PRETTY[n] || n; }
function comboText(combo, xbox) {
  return combo.split('+').map((n) => (xbox ? xboxName(n) : ps2Name(n))).join(' + ');
}

// the browser's standard controller layout -> the PS2 names the trainer uses
const PAD_INDEX = ['CROSS', 'CIRCLE', 'SQUARE', 'TRIANGLE', 'L1', 'R1', 'L2', 'R2', 'SELECT', 'START', 'L3', 'R3',
                   'UP', 'DOWN', 'LEFT', 'RIGHT'];

function stopListening() {
  if (listening) { clearInterval(listening); listening = null; }
}

// hold the buttons on any controller plugged into this PC, then let go: the buttons held together become the combo
function listen(statusEl) {
  stopListening();
  let best = [];
  let started = false;
  const t0 = Date.now();
  statusEl.textContent = 'Hold the buttons on your controller, then let go…';
  listening = setInterval(() => {
    const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
    const held = new Set();
    for (const p of pads)
      p.buttons.forEach((b, i) => { if (i < PAD_INDEX.length && (b.pressed || b.value > 0.5)) held.add(PAD_INDEX[i]); });
    if (held.size) {
      started = true;
      if (held.size >= best.length) best = Array.from(held);
      statusEl.textContent = 'Held: ' + best.map(ps2Name).join(' + ');
    } else if (started) {
      stopListening();
      picked = PAD_INDEX.filter((n) => best.includes(n));
      renderControls();
      return;
    }
    if (Date.now() - t0 > 10000) {
      stopListening();
      statusEl.textContent = pads.length ? 'Nothing pressed: try again.'
        : 'No controller seen: plug one in and press any button on it first (the browser only sees a pad once it is pressed).';
    }
  }, 50);
}

function renderHooks() {
  const host = $('#hmHooks');
  host.innerHTML = '<table class="hmtable" style="width:100%;border-collapse:collapse">'
    + '<tr class="muted"><th style="width:56px">PS2</th><th style="width:56px">Xbox</th>'
    + '<th style="text-align:left;padding:6px 0 6px 10px">Script hook</th></tr>'
    + data.hooks.map((h) => {
      const box = (plat) => {
        if (!h[plat]) return '<td style="text-align:center" class="muted" title="Not on this game">—</td>';
        return '<td style="text-align:center"><input type="checkbox" data-id="' + esc(h.id) + '" data-plat="' + plat + '"'
          + (h.on[plat] ? ' checked' : '') + ' title="' + (plat === 'ps2' ? 'PS2' : 'Xbox') + ': on / off"></td>';
      };
      const note = h.ps2 && !h.wanted ? ' <span class="muted">(this mod does not need it yet)</span>' : '';
      return '<tr style="border-top:1px solid var(--line,#333)">' + box('ps2') + box('xbox')
        + '<td style="padding:10px 0 10px 10px"><b>' + esc(h.title) + '</b>' + note
        + '<br><span class="muted">' + esc(h.text) + '</span></td></tr>';
    }).join('') + '</table>';
  host.querySelectorAll('input[data-id]').forEach((box) => {
    box.addEventListener('change', async () => {
      box.disabled = true;
      try {
        data = await call('de_x_hooks_set', { id: box.dataset.id, platform: box.dataset.plat, enabled: box.checked });
        render();
        const h = data.hooks.find((x) => x.id === box.dataset.id);
        toast((box.dataset.plat === 'ps2' ? 'PS2' : 'Xbox') + ': ' + h.title + (box.checked ? ' on' : ' off'));
      } catch (e) {
        box.checked = !box.checked;
        box.disabled = false;
        toast('Script Hooks: ' + e.message, 'err');
      }
    });
  });
}

function renderControls() {
  const host = $('#hmControls');
  const trainer = data.hooks.find((h) => h.id === 'trainer');
  const off = trainer && !trainer.on.ps2 && !trainer.on.xbox;
  let html = '<h3 style="margin:22px 0 4px">Trainer controls</h3><p class="muted" style="margin:0 0 8px">'
    + 'A combo is held together to switch its trainer mod on or off; the flying ones are held while flying. '
    + 'Named for the PS2 controller, with the Xbox button beside it.'
    + (off ? ' <b>The trainer is off on both games.</b>' : '') + '</p>'
    + '<table style="width:100%;border-collapse:collapse">';
  for (const c of data.controls) {
    const games = [c.ps2 ? 'PS2' : '', c.xbox ? 'Xbox' : ''].filter(Boolean).join(' + ');
    html += '<tr style="border-top:1px solid var(--line,#333)"><td style="padding:8px 12px 8px 0;width:200px"><b>'
      + esc(c.label) + '</b><br><span class="muted">' + games + '</span></td>';
    if (editing === c.id) {
      html += '<td colspan="2" style="padding:8px 0"><div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px">'
        + data.buttons.map((b) => '<button class="hmbtn' + (picked.includes(b.ps2) ? ' primary' : '') + '" data-b="'
          + esc(b.ps2) + '" title="Xbox: ' + esc(b.xbox) + '">' + esc(ps2Name(b.ps2)) + '</button>').join('')
        + '</div><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'
        + '<button id="hmListen">Press on controller…</button>'
        + '<button id="hmSave" class="primary"' + (picked.length ? '' : ' disabled') + '>Save</button>'
        + '<button id="hmDefault">Default (' + esc(comboText(c.default, false)) + ')</button>'
        + '<button id="hmCancel">Cancel</button>'
        + '<span id="hmListenStatus" class="muted">' + (picked.length ? 'PS2: ' + esc(comboText(picked.join('+'), false))
          + ' &middot; Xbox: ' + esc(comboText(picked.join('+'), true)) : 'Pick the buttons, or press them on a controller.')
        + '</span></div></td>';
    } else {
      html += '<td style="padding:8px 12px 8px 0">PS2: <b>' + esc(comboText(c.combo, false)) + '</b>'
        + (c.xbox ? '<br><span class="muted">Xbox: ' + esc(comboText(c.combo, true)) + '</span>' : '')
        + (c.combo !== c.default ? ' <span class="muted">(changed)</span>' : '') + '</td>'
        + '<td style="text-align:right;width:90px"><button data-edit="' + esc(c.id) + '">Change…</button></td>';
    }
    html += '</tr>';
  }
  html += '</table><p class="muted" style="margin-top:8px">The PS2 takes a new mapping at the next Build into the PS2 '
    + 'game; the Xbox straight away (the recomp reads it within a second).</p>';
  host.innerHTML = html;
  host.querySelectorAll('button[data-edit]').forEach((b) => b.addEventListener('click', () => {
    stopListening();
    editing = b.dataset.edit;
    picked = data.controls.find((c) => c.id === editing).combo.split('+');
    renderControls();
  }));
  host.querySelectorAll('button[data-b]').forEach((b) => b.addEventListener('click', () => {
    const n = b.dataset.b;
    picked = picked.includes(n) ? picked.filter((x) => x !== n) : picked.concat([n]);
    renderControls();
  }));
  const save = async (combo) => {
    stopListening();
    try {
      data = await call('de_x_hooks_control', { id: editing, combo });
      const c = data.controls.find((x) => x.id === editing);
      editing = null;
      render();
      toast(c.label + ': ' + comboText(c.combo, false));
    } catch (e) { toast('Script Hooks: ' + e.message, 'err'); }
  };
  const q = (id) => host.querySelector('#' + id);
  if (q('hmSave')) q('hmSave').onclick = () => save(PAD_INDEX.filter((n) => picked.includes(n)).join('+'));
  if (q('hmDefault')) q('hmDefault').onclick = () => save('default');
  if (q('hmCancel')) q('hmCancel').onclick = () => { stopListening(); editing = null; renderControls(); };
  if (q('hmListen')) q('hmListen').onclick = () => listen(q('hmListenStatus'));
}

function render() {
  const host = $('#hmBody');
  if (!host || !data) return;
  if (!$('#hmHooks')) {
    host.innerHTML = '<p class="muted">Code the Studio puts into the game for you. Untick one to leave it out of '
      + 'that game: on the PS2 at the next Build (PS2), on the Xbox straight away (the recomp reads the switches as it '
      + 'goes). The second Showdown lineup, its background, own textures for players 2-4 and Coliseum on the PS2 are '
      + 'always on.</p><div id="hmHooks"></div><div id="hmControls"></div>';
  }
  renderHooks();
  renderControls();
}

async function refresh() {
  try { data = await call('de_x_hooks', {}); render(); }
  catch (e) { const host = $('#hmBody'); if (host) host.textContent = 'Script Hooks: ' + e.message; }
}

onPanel((panel) => { if (panel === 'hookmods') refresh(); else stopListening(); });
