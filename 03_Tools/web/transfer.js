// transfer.js — ONE SHAPE FOR EVERY IMPORT AND EVERY EXPORT.
//
// Before this the same two acts wore six affordance styles, eight verbs for
// import and six for export, and five different shapes of pre-commit summary,
// two of which reported no size and no slot at all. There are two verbs now,
// Export and Import, one bar and one sheet, and the sheet's third region is
// the frozen `plan` object rendered literally.
//
// This module is also where the new wire envelope lives. `api()` in app.js
// throws on any `error` key, which is why five already-written "won't fit"
// messages have never been reachable. `call()` treats a refusal as an answer.

let A = null;                 // the app.js bridge

// ------------------------------------------------------------ the envelope

// A refusal is HTTP 200 with ok:false and a problem object. Only a network
// failure throws. Everything else comes back as data the caller can render.
// The server answers one request at a time, and a call that is only going to
// 404 still costs its place in that queue -- measured at 17 s for one while a
// level decode was in flight. A route that has answered "no such route" once
// is not going to grow one this session, so remember and stop asking.
const absent = new Set();
const routeName = (route) => route.split('?')[0];

// The Studio answers one request at a time, so two panels asking the same
// GET at the same moment do not cost one wait -- they cost two, in series.
// The header pill and the Versions panel both want /api/versions, which is a
// full disc diff. One flight, both callers.
const inflight = new Map();

export function call(route, method, body) {
  if (method && method !== 'GET') return callOnce(route, method, body);
  if (inflight.has(route)) return inflight.get(route);
  const p = callOnce(route, method, body)
    .finally(() => inflight.delete(route));
  inflight.set(route, p);
  return p;
}

async function callOnce(route, method, body) {
  if (absent.has(routeName(route))) {
    return problem('not_in_this_build',
                   'This part of the Studio is not in this build yet.',
                   'The panel below shows what it will do once it is.',
                   'no such route: ' + routeName(route));
  }
  const opts = method === 'GET' || !method
    ? undefined
    : { method, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}) };
  let r;
  try {
    r = await fetch('/api/' + route, opts);
  } catch {
    return problem('server_fault', 'The Studio is not answering.',
                   'It may be restarting. Your edits are on disk.');
  }
  let j;
  try { j = await r.json(); }
  catch {
    return problem('server_fault', 'The Studio sent something unreadable.', '',
                   'HTTP ' + r.status + ' ' + r.statusText);
  }
  // A route that has not been written yet answers with the old 404 shape.
  if (j && j.error && j.ok === undefined) {
    const missing = /no such route/i.test(j.error);
    if (missing) absent.add(routeName(route));
    return problem(missing ? 'not_in_this_build' : 'server_fault',
                   missing ? 'This part of the Studio is not in this build yet.'
                           : String(j.error),
                   missing ? 'The panel below shows what it will do once it is.' : '',
                   String(j.error));
  }
  return j;
}

export function problem(code, message, fix, detail) {
  return { ok: false, problem: { code, message, fix: fix || '', detail: detail || '' } };
}

// A refusal that is only "the backend has not shipped this route yet" is a
// build fact, not a user error, and must not be dressed up as one.
export const notBuilt = (r) => !r.ok && r.problem?.code === 'not_in_this_build';

const FROZEN_CODES = new Set([
  'bad_request', 'not_found', 'unknown_type', 'unknown_table', 'unknown_value',
  'no_enm_here', 'slot_overflow', 'not_editable', 'disc_busy', 'token_expired',
  'not_in_this_build', 'server_fault',
]);

// ------------------------------------------------------------------ pieces

export const el = (t, c, x) => {
  const n = document.createElement(t);
  if (c) n.className = c;
  if (x != null) n.textContent = x;
  return n;
};

const RUNGS = {
  measured: 'measured', established: 'established', inferred: 'inferred',
  untested: 'untested', dead: 'dead',
};
// The five rungs survive verbatim -- hue AND glyph, from index.html's own
// .conf rules. Nothing new invents a sixth.
const RUNG_WHY = {
  measured: 'Measured. Read straight off the disc.',
  established: 'Documented. From PS2 or format documentation.',
  inferred: 'Inferred. Not confirmed in game.',
  untested: 'Unverified in game.',
  dead: 'Inert. The game never reads it.',
};
export function rungBadge(rung) {
  const r = RUNGS[rung] || 'inferred';
  const b = el('span', 'conf ' + r, r);
  b.title = RUNG_WHY[r];
  return b;
}

// problem.detail is the raw technical string. It goes behind a disclosure and
// never inline: `ValueError:` and `<_io.BytesIO object at 0x...>` are not
// sentences anyone can act on.
export function renderProblem(host, p) {
  host.innerHTML = '';
  const box = el('div', 'infbox');
  box.style.borderLeftColor = 'var(--red)';
  box.append(el('b', null, p.message || 'That was refused.'));
  if (p.fix) {
    const f = el('div', null, p.fix);
    f.style.cssText = 'margin-top:5px';
    box.append(f);
  }
  if (!FROZEN_CODES.has(p.code)) {
    const c = el('div', 'muted', 'code: ' + p.code);
    c.style.cssText = 'font-size:11px;margin-top:5px';
    box.append(c);
  }
  if (p.detail) {
    const d = document.createElement('details');
    d.style.marginTop = '6px';
    const sm = document.createElement('summary');
    sm.textContent = 'Technical detail';
    sm.style.cssText = 'font-size:11px;color:var(--dim-ink);cursor:pointer';
    const pre = el('div', 'ev', p.detail);
    d.append(sm, pre);
    box.append(d);
  }
  host.append(box);
  return host;
}

const n = (v) => (v == null ? '—' : Number(v).toLocaleString());

// The Studio answers one request at a time, so a dry run can genuinely take
// tens of seconds behind a level decode. A label with a running clock is the
// difference between "working" and "hung"; a toast could not say either,
// which is why a toast is never used to mean "wait".
export function waiting(host, label) {
  host.innerHTML = '';
  const p = el('p', 'muted', label);
  const t = el('span', 'muted', '');
  t.style.marginLeft = '6px';
  p.append(t);
  host.append(p);
  const t0 = Date.now();
  const id = setInterval(() => {
    const s = (Date.now() - t0) / 1000;
    t.textContent = s > 1.5 ? `${s.toFixed(0)} s` : '';
  }, 500);
  // Stopping takes the label away too, so a caller that appends its result
  // does not leave "Comparing… 20 s" sitting above the answer.
  return () => { clearInterval(id); p.remove(); };
}

// REGION 3, and it is the same six rows every time, for every verb, for every
// format. It never changes and never disappears, because "what will this cost
// me" is the question every write asks and it used to be answered five
// different ways.
export function renderPlan(host, plan, effect) {
  host.innerHTML = '';
  if (!plan) {
    host.append(el('p', 'muted', 'Nothing planned yet.'));
    return host;
  }
  const t = el('table');
  t.style.cssText = 'width:100%;font-size:12px';
  const row = (k, v) => {
    const tr = el('tr');
    const kd = el('td', 'muted', k);
    kd.style.cssText = 'width:96px;vertical-align:top;padding:2px 0';
    const vd = el('td');
    vd.style.padding = '2px 0';
    if (typeof v === 'string') vd.textContent = v; else vd.append(v);
    tr.append(kd, vd);
    t.append(tr);
    return vd;
  };

  const writes = plan.writes || [];
  const arcs = new Set(writes.map((w) => w.archive));
  row('Writes', writes.length
    ? `${writes.length} file${writes.length === 1 ? '' : 's'} in `
      + `${arcs.size} archive${arcs.size === 1 ? '' : 's'}`
    : 'nothing');

  const after = writes.reduce((s, w) => s + (w.bytes_after || 0), 0);
  const stored = writes.reduce((s, w) => s + (w.stored || 0), 0);
  const slot = writes.reduce((s, w) => s + (w.slot || 0), 0);
  const head = writes.reduce((s, w) => s + (w.headroom || 0), 0);
  row('New size', writes.length ? `${n(after)} bytes  (${n(stored)} stored)` : '—');
  row('Slot', writes.length ? `${n(slot)} bytes` : '—');

  // The bar is the honest one: it is drawn from stored/slot, and it goes red
  // the moment the plan does not fit -- which is the moment the primary
  // button below stops being pressable.
  const barCell = row('', el('span'));
  barCell.innerHTML = '';
  const bar = el('div');
  bar.style.cssText = 'height:9px;border:1px solid var(--line);border-radius:5px;'
    + 'overflow:hidden;background:var(--well);max-width:280px';
  const fill = el('i');
  const pct = slot ? Math.min(100, (stored / slot) * 100) : 0;
  fill.style.cssText = `display:block;height:100%;width:${pct.toFixed(1)}%;`
    + `background:${plan.fits === false ? 'var(--red)' : 'var(--grn)'}`;
  bar.append(fill);
  const cap = el('span', 'muted', plan.fits === false
    ? `over by ${n(stored - slot)} bytes`
    : `${n(head)} spare`);
  cap.style.cssText = 'font-size:11px;margin-left:8px';
  const wrap = el('div');
  wrap.style.cssText = 'display:flex;align-items:center;gap:0';
  wrap.append(bar, cap);
  barCell.append(wrap);

  row('Effect', plan.summary || effect || '—');
  const u = plan.undo || {};
  row('Undo', `${u.steps == null ? 1 : u.steps} step`
    + `${u.steps === 1 || u.steps == null ? '' : 's'}`
    + (u.label ? ` — “${u.label}”` : ''));
  host.append(t);

  if (writes.length > 1) {
    const d = document.createElement('details');
    d.style.marginTop = '6px';
    const sm = document.createElement('summary');
    sm.textContent = `The ${writes.length} files`;
    sm.style.cssText = 'font-size:11px;color:var(--dim-ink);cursor:pointer';
    const inner = el('table');
    inner.style.cssText = 'width:100%;font-size:11px;margin-top:4px';
    for (const w of writes) {
      const tr = el('tr');
      tr.append(el('td', 'muted', w.file),
                el('td', null, `${n(w.stored)} / ${n(w.slot)}`),
                el('td', w.fits ? '' : 'bad', w.fits ? 'fits' : 'over'));
      inner.append(tr);
    }
    d.append(sm, inner);
    host.append(d);
  }
  return host;
}

// ------------------------------------------------------------- the sheet

let sheetEl = null;

function ensureSheet() {
  if (sheetEl) return sheetEl;
  const s = el('div');
  s.id = 'xferSheet';
  s.style.cssText =
    'position:fixed;inset:0;z-index:120;background:rgba(0,0,0,.55);'
    + 'display:none;align-items:center;justify-content:center';
  s.innerHTML = `
    <div id="xferBox" style="width:min(560px,92vw);max-height:88vh;overflow:auto;
         background:var(--bg2);border:1px solid var(--line);border-radius:6px;
         box-shadow:0 18px 60px rgba(0,0,0,.6)">
      <!-- DESIGN-REVIEW A15: no SUBJECT / SOURCE & OPTIONS / WHAT THIS WILL
           DO labels framing a single text box. Title, what, how, cost. -->
      <div style="padding:14px 16px 4px">
        <div id="xferVerb" style="font:var(--t-head);color:var(--title)"></div>
        <div id="xferSubject" style="margin-top:8px"></div>
        <div id="xferOptions" style="margin-top:10px"></div>
        <div id="xferPlan" style="margin-top:10px"></div>
      </div>
      <div style="padding:12px 16px;display:flex;gap:8px;justify-content:flex-end">
        <button id="xferCancel">Cancel</button>
        <button id="xferGo" class="primary" disabled>Apply</button>
      </div>
    </div>`;
  document.body.append(s);
  s.addEventListener('click', (e) => { if (e.target === s) closeSheet(); });
  s.querySelector('#xferCancel').onclick = closeSheet;
  sheetEl = s;
  return s;
}

export function closeSheet() {
  if (sheetEl) sheetEl.style.display = 'none';
}

// One component, both verbs, every format. There is no window.confirm()
// anywhere in the new code: a native confirm cannot carry a slot bar.
export function openSheet(cfg) {
  const s = ensureSheet();
  s.style.display = 'flex';
  s.querySelector('#xferVerb').textContent = cfg.title || 'Import';

  const subj = s.querySelector('#xferSubject');
  subj.innerHTML = '';
  const line1 = el('div');
  line1.append(el('span', null, cfg.subject?.title || '—'));
  // Only a rung that is not the norm is worth a marker (DESIGN-REVIEW A3/A15).
  if (cfg.subject?.rung && cfg.subject.rung !== 'measured')
    line1.append(rungBadge(cfg.subject.rung));
  subj.append(line1);
  if (cfg.subject?.where) subj.append(el('div', 'muted', cfg.subject.where));
  if (cfg.subject?.format) {
    const f = el('div', 'muted', cfg.subject.format);
    f.style.fontSize = '11px';
    subj.append(f);
  }

  const opts = s.querySelector('#xferOptions');
  opts.innerHTML = '';
  if (cfg.renderOptions) cfg.renderOptions(opts);

  const planHost = s.querySelector('#xferPlan');
  const go = s.querySelector('#xferGo');
  go.textContent = cfg.primary || 'Apply';
  go.disabled = true;
  go.className = cfg.destructive ? 'danger' : 'primary';

  const api = {
    // Region 3 is replaced IN PLACE and persists. Import results used to land
    // in three unrelated places depending on which importer you used.
    plan(plan, effect) {
      renderPlan(planHost, plan, effect);
      go.disabled = !(plan && plan.fits !== false);
    },
    refuse(p) { renderProblem(planHost, p); go.disabled = true; },
    busy(label) {
      waiting(planHost, label || 'Planning…');
      go.disabled = true;
    },
    done(html) {
      planHost.innerHTML = '';
      const d = el('div');
      d.innerHTML = html;
      planHost.append(d);
      go.disabled = true;
    },
    close: closeSheet,
  };
  if (cfg.startLabel === null) { planHost.innerHTML = ''; go.disabled = true; }
  else api.busy(cfg.startLabel || 'Choose a source, then this will say what it costs.');
  go.onclick = () => cfg.onApply?.(api);
  cfg.onOpen?.(api);
  return api;
}

// ------------------------------------------------------------ the bar

// Always the same order: Export, Import, spacer, <panel commit>. Always
// <button>, always 13px. Always disabled with a title naming the reason when
// nothing is selected -- never enabled-then-toast.
export function transferBar(host, cfg) {
  const bar = el('div', 'bar');
  bar.style.cssText = 'gap:6px;border-top:1px solid var(--line);'
    + 'border-bottom:none;padding:6px 8px;flex:none';
  const mk = (label, fn, reason, cls) => {
    const b = el('button', cls || '', label);
    b.style.fontSize = '13px';
    if (reason) { b.disabled = true; b.title = reason; }
    else b.onclick = fn;
    bar.append(b);
    return b;
  };
  if (cfg.exportLabel) mk(cfg.exportLabel, cfg.onExport, cfg.disabledReason);
  if (cfg.importLabel) mk(cfg.importLabel, cfg.onImport, cfg.disabledReason);
  bar.append(Object.assign(el('span'), { style: 'flex:1' }));
  if (cfg.commitLabel) mk(cfg.commitLabel, cfg.onCommit, cfg.commitDisabledReason,
                          cfg.commitClass || 'primary');
  host.append(bar);
  return bar;
}

// A file for a file, a message for a message. The response is a file IF AND
// ONLY IF Content-Type is not application/json -- the export equivalent of
// the refusal envelope.
export async function exportVia(kind, target, options, filename) {
  let r;
  try {
    r = await fetch('/api/transfer_export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, target, options: options || {} }),
    });
  } catch { A?.toast('The Studio is not answering.', 'bad'); return false; }
  const ct = (r.headers.get('content-type') || '').toLowerCase();
  if (ct.includes('application/json')) {
    let j = {};
    try { j = await r.json(); } catch {}
    const p = j.problem || {};
    A?.toast('Could not export: '
      + (p.message || j.error || `HTTP ${r.status}`), 'bad');
    return false;
  }
  const dispo = r.headers.get('content-disposition') || '';
  const m = /filename="?([^";]+)"?/i.exec(dispo);
  const blob = await r.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = m ? m[1] : (filename || 'export.bin');
  a.click();
  setTimeout(() => URL.revokeObjectURL(href), 30000);
  return true;
}

// ------------------------------------------- the one Tier-1 destructive act

// Red plus a typed word, for exactly one action in the whole product. Every
// other Apply is a default button, because red that means four things means
// nothing.
function wireResetToStock() {
  const b = document.querySelector('#btnRestore');
  if (!b || b.dataset.tier1) return;
  b.dataset.tier1 = '1';
  const original = b.onclick;
  b.onclick = (ev) => {
    ev.preventDefault();
    ev.stopImmediatePropagation();
    openSheet({
      title: 'Reset to stock',
      subject: {
        title: 'Replaces the working disc with the stock disc. This cannot be undone.',
      },
      primary: 'Reset',
      destructive: true,
      renderOptions(host) {
        const inp = el('input');
        inp.placeholder = 'Type RESET';
        inp.style.cssText = 'width:160px;margin-top:6px';
        host.append(inp);
        // the Data Editor's own mod files (.pck, .dat folder files): else the next Build puts them back
        const cm = el('input'); cm.type = 'checkbox'; cm.checked = true;
        const cml = el('label');
        cml.style.cssText = 'display:flex;gap:6px;align-items:flex-start;margin-top:8px;max-width:440px';
        cml.append(cm, el('span', null, 'Also take the Data Editor\'s mod files out (the files put in its .dat folders, '
          + 'e.g. edited Flash screens) and the editors\' working copies (Workbench: Flash screens, HUD text), so the '
          + 'Studio shows the stock ones again. They are kept as a Saved Mod "Before reset ..." first. Left in, the '
          + 'next Build or Play puts the mod files back on the disc.'));
        host.append(cml);
        window.__resetClearMods = true;
        cm.onchange = () => { window.__resetClearMods = cm.checked; };
        inp.oninput = () => {
          const go = sheetEl.querySelector('#xferGo');
          go.disabled = inp.value.trim().toUpperCase() !== 'RESET';
        };
        setTimeout(() => inp.focus(), 40);
      },
      startLabel: null,
      // The sheet IS the confirmation, so the old native confirm stands down
      // for this one press. It is still there if this module ever fails to
      // load, which is the point of the flag rather than a deletion.
      onApply() { window.__resetConfirmed = true; closeSheet(); original?.call(b, ev); },
    });
  };
}

// ------------------------------------------------------------ two verbs

// TWO VERBS, AND ONLY TWO. Export leaves the tool, Import enters the game.
// The noun is the thing in the game and the format is a chip. Counted in the
// shipped build: eight verbs for import and six for export, including
// `Replace`, `Load`, `save`, `save .mpg`, `save .ipu`, `Apply to working
// ISO`, `Write it`, `Write it in` and `Save into ISO`. All retired.
const RENAMES = [
  // "…" because each opens a file dialog (DESIGN-REVIEW A11).
  ['#movSave', 'Export MPG…'],
  ['#stillSave', 'Export IPU…'],
  ['#stillReplaceBtn', 'Import picture…'],
  ['#btnExport', 'Export…'],
  ['#btnImport', 'Import…'],
  ['#animExportBtn', 'Export BVH…'],
  ['#animImportBtn', 'Import BVH…'],
  ['#btnSaveText', 'Apply'],
  ['#btnSaveMod', 'Save a version…'],
];

function normaliseVerbs() {
  for (const [sel, label] of RENAMES) {
    const n = document.querySelector(sel);
    if (n && n.textContent.trim() !== label) n.textContent = label;
  }
  // Every Apply is a default button. Red meant four different things, while
  // the most unguarded bulk write in the product was primary blue.
  const st = document.querySelector('#btnSaveText');
  if (st) st.classList.remove('danger');
}

// ------------------------------------------- the two exports that were missing

// A texture could be seen and replaced but never saved, and the 8,361 voice
// and music streams were served Content-Disposition: inline, so they could
// not be saved by hand either. Both are one call to the same route.
// Remember what the texture panel is showing, from the picture it shows it
// in. Registered EXACTLY once -- this function is re-run on every tab change,
// and a listener added per call accumulates until the tab dies.
let lastTex = null;
function watchTextureClicks() {
  if (watchTextureClicks.done) return;
  watchTextureClicks.done = true;
  document.addEventListener('click', (e) => {
    const img = e.target.closest?.('img');
    if (!img || !/\/api\/texture\.png\?/.test(img.src || '')) return;
    const u = new URL(img.src, location.origin);
    lastTex = { archive: u.searchParams.get('archive'),
                area: u.searchParams.get('area'),
                i: parseInt(u.searchParams.get('i'), 10) };
  }, true);
}

function wireMissingExports() {
  watchTextureClicks();
  const texBar = document.querySelector('#texViewInfo')?.parentElement;
  if (texBar && !texBar.dataset.xfer) {
    texBar.dataset.xfer = '1';
    const b = el('button', '', 'Export PNG…');
    b.style.cssText = 'font-size:12px;margin-right:8px';
    b.title = 'Click a texture first.';
    b.onclick = async () => {
      if (!lastTex) return A.toast('Click a texture first.', 'bad');
      const was = b.textContent;
      b.textContent = 'Exporting…';
      await exportVia('texture', lastTex, {}, 'texture.png');
      b.textContent = was;
    };
    texBar.insertBefore(b, texBar.firstChild);
  }

  const strBar = document.querySelector('#strPlayer')?.parentElement;
  if (strBar && !strBar.dataset.xfer) {
    strBar.dataset.xfer = '1';
    const b = el('button', '', 'Export WAV…');
    b.style.cssText = 'font-size:12px;margin-right:8px';
    b.onclick = async () => {
      const src = document.querySelector('#strPlayer')?.src || '';
      const m = /[?&]name=([^&]+)/.exec(src);
      if (!m) return A.toast('Play a clip first, then export it.', 'bad');
      const name = decodeURIComponent(m[1]);
      const was = b.textContent;
      b.textContent = 'Exporting…';
      await exportVia('stream', { name }, {},
                      name.replace(/\.[^.]+$/, '') + '.wav');
      b.textContent = was;
    };
    strBar.insertBefore(b, document.querySelector('#strPlayer'));
  }
}

// -------------------------------------------------- one watcher, not seven
//
// Six modules want to know "which panel is showing now". Six MutationObservers
// over `main`, each firing on every class change anywhere inside a level whose
// outliner holds 950 rows, is a self-inflicted storm -- and any one of them
// that writes a class re-triggers the other five. So: ONE observer, one
// debounce, one queue, and a same-answer check so an unchanged panel costs
// nothing at all.
const watchers = [];
let watchTimer = null;
let lastPanel = null;

function activePanel() {
  const on = document.querySelector('.tab.on');
  return on ? on.id.replace('tab-', '') : null;
}

function fireWatchers(force) {
  const panel = activePanel();
  if (!force && panel === lastPanel) return;
  lastPanel = panel;
  for (const fn of watchers) {
    try { fn(panel); } catch (e) { console.error('[watch]', e); }
  }
}

export function onPanel(fn) {
  watchers.push(fn);
  try { fn(activePanel()); } catch (e) { console.error('[watch]', e); }
}

function startWatching() {
  const main = document.querySelector('main') || document.body;
  new MutationObserver(() => {
    clearTimeout(watchTimer);
    watchTimer = setTimeout(() => fireWatchers(false), 60);
  }).observe(main, { subtree: true, attributes: true, attributeFilter: ['class'] });
}

export function init(bridge) {
  A = bridge;
  // The header button is wired by app.js at load, so re-wrap it after that.
  wireResetToStock();
  normaliseVerbs();
  wireMissingExports();
  window.addEventListener('rdr:ready', () => {
    wireResetToStock();
    normaliseVerbs();
    wireMissingExports();
  });
  // Some of these buttons are built by the panel that owns them when it first
  // opens, so hold the lexicon on every panel change rather than setting once.
  onPanel(() => { normaliseVerbs(); wireMissingExports(); });
  startWatching();
}
