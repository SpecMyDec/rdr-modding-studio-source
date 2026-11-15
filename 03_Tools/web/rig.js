// rig.js — BALANCE ▸ Tuning ▸ Body & Rig.
//
// The eight per-character files that are not .cr and not .swt:
// .dam .sphs .ars .hot .tail .acc .armr .rep — 939 files, 426,648 values.
//
// Four layouts, one route. The server sends `layout` and this file switches on
// it, because the formats genuinely are four different shapes and pretending
// they are one form would mean a grid of unlabelled columns rendered as a
// column of unlabelled inputs:
//
//   grid       .dam            8 limbs x N, no keys at all
//   form       .sphs .hot .tail .acc .armr    named blocks of named fields
//   checklist  .ars            every swat.guns weapon, on or off
//   table      .rep            up to 1,902 slot -> clip rows, filterable
//
// HEADROOM IS THE STORY HERE. The median .sphs copy has ONE spare byte in its
// slot, and 18 of the 199 have none. So the plan is not tucked behind a
// confirm dialog the way it is elsewhere in the product — it is a permanent
// column, it updates on every Check, and a refusal is an expected answer
// rather than an error. Everything routes through transfer.js's `call`, which
// returns a refusal as DATA; app.js's `api()` throws on it, which is exactly
// how five carefully written byte-count messages became unreachable.

import { call, el, onPanel, renderPlan, renderProblem, waiting } from './transfer.js';

let A = null;
let roster = null;          // { characters, kinds }
let stem = null;            // the selected character
let doc = null;             // the GET payload for the open file
let edits = {};             // pending changes, keyed by the server's path

const $ = (s) => document.querySelector(s);

// A dirty value beats the file's value beats nothing. One function so the
// eight layouts cannot disagree about what the user is looking at.
const shown = (path, fallback) => (path in edits ? edits[path] : fallback);

function mark(path, input, note, stock) {
  const changed = path in edits && edits[path] !== stock;
  input.style.borderColor = changed ? 'var(--gold)' : '';
  if (note) note.textContent = changed ? `was ${stock}` : (note.dataset.hint || '');
}

function touch(path, value, input, note, stock) {
  if (String(value) === String(stock)) delete edits[path];
  else edits[path] = String(value);
  mark(path, input, note, stock);
  renderPlan($('#rigPlan'), null);
  const dirty = Object.keys(edits).length;
  $('#btnRigSave').disabled = true;          // Check first, always.
  $('#btnRigCheck').disabled = !dirty;
  $('#rigWhere').textContent = dirty
    ? `${dirty} value${dirty === 1 ? '' : 's'} changed — press Check`
    : whereLabel();
}

function whereLabel() {
  if (!doc) return '';
  return `${doc.name} — ${doc.copies} cop${doc.copies === 1 ? 'y' : 'ies'} on the disc, `
    + `${doc.stored} of ${doc.slot} bytes used (${doc.headroom} spare)`;
}

// ------------------------------------------------------------------ controls

function field(row) {
  const wrap = el('div', 'field');
  const lab = el('label', null, row.label);
  lab.style.width = '150px';
  lab.title = row.path;
  const note = el('span', 'muted');
  note.dataset.hint = row.hint || '';
  note.textContent = row.hint || '';

  let input;
  if (row.control === 'pick' && row.options && row.options.length) {
    input = el('select');
    for (const o of row.options) {
      const opt = el('option', null, o);
      opt.value = o;
      input.append(opt);
    }
    input.value = shown(row.path, row.value);
    input.onchange = () => touch(row.path, input.value, input, note, row.value);
  } else {
    input = el('input');
    input.value = shown(row.path, row.value);
    if (row.control === 'float' || row.control === 'int') input.style.width = '120px';
    input.oninput = () => touch(row.path, input.value, input, note, row.value);
  }
  wrap.append(lab, input, note);
  mark(row.path, input, note, row.value);
  return wrap;
}

// ---------------------------------------------------------------- the layouts

function renderForm(host) {
  for (const sec of doc.sections) {
    const h = el('h3', null, sec.label);
    h.style.cssText = 'margin:16px 0 6px';
    if (sec.kind) h.append(el('span', 'tag', sec.kind));
    host.append(h);
    for (const note of sec.fixed || []) host.append(el('p', 'muted', note));
    for (const row of sec.rows) host.append(field(row));
  }
}

function renderGrid(host) {
  for (const t of doc.tables) {
    const h = el('h3', null, t.label);
    h.style.cssText = 'margin:16px 0 4px';
    host.append(h, el('p', 'muted', t.note));
    const table = el('table');
    table.style.cssText = 'font-size:12px;border-collapse:collapse;margin-top:6px';
    const head = el('tr');
    head.append(el('th', 'muted', 'limb'));
    for (const c of t.columns) {
      const th = el('th', 'muted', c);
      th.style.cssText = 'width:82px;text-align:left';
      head.append(th);
    }
    table.append(head);
    for (const r of t.rows) {
      const tr = el('tr');
      const td = el('td', 'muted', r.label);
      td.style.paddingRight = '10px';
      tr.append(td);
      for (const c of r.cells) {
        const cell = el('td');
        const inp = el('input');
        inp.value = shown(c.path, c.value);
        inp.style.cssText = 'width:74px;font-size:12px';
        inp.title = c.path;
        inp.oninput = () => touch(c.path, inp.value, inp, null, c.value);
        mark(c.path, inp, null, c.value);
        cell.append(inp);
        tr.append(cell);
      }
      table.append(tr);
    }
    host.append(table);
  }
}

function renderChecklist(host) {
  host.append(el('p', 'muted', doc.note));
  const c = doc.counter;
  host.append(el('p', 'muted',
    `${c.key} is ${c.declared} and is recomputed from the list below.`));
  const table = el('table');
  table.style.cssText = 'font-size:12px;margin-top:8px';
  for (const item of doc.items) {
    // `carried` is presence in the file, `flag` is the 0/1 column. They are
    // two different things and were shown as one until the disc said
    // otherwise: 54 of 546 rows are present AND flagged 1.
    const on = shown('__on__' + item.name, item.on ? '1' : '0') === '1';
    const tr = el('tr');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = on;
    const flag = el('select');
    for (const v of ['0', '1']) {
      const o = el('option', null, v);
      o.value = v;
      flag.append(o);
    }
    flag.value = shown('__flag__' + item.name, item.flag);
    flag.disabled = !on;
    box.onchange = () => {
      // Route the tick through `touch` like everything else, so ticking a box
      // and untickng it again leaves NO pending change. Setting `edits`
      // directly left a `__on__X: '0'` behind that matched the file, and the
      // Apply button stayed lit for a write with nothing in it.
      touch('__on__' + item.name, box.checked ? '1' : '0', box, null,
            item.on ? '1' : '0');
      flag.disabled = !box.checked;
      touch('__flag__' + item.name, flag.value, flag, null, item.flag);
      tr.style.opacity = box.checked ? '1' : '0.45';
    };
    flag.onchange = () => touch('__flag__' + item.name, flag.value, flag, null, item.flag);
    const c1 = el('td'); c1.append(box);
    const c2 = el('td', null, item.name); c2.style.paddingRight = '14px';
    const c3 = el('td'); c3.append(flag);
    tr.append(c1, c2, c3);
    tr.style.opacity = on ? '1' : '0.45';
    table.append(tr);
  }
  host.append(table);
}

function renderTable(host) {
  host.append(el('p', 'muted', doc.note));
  const summary = doc.checked
    ? `${doc.rows.length} slots, ${doc.unresolved} naming no .anim on the disc`
    : `${doc.rows.length} slots`;
  host.append(el('p', 'muted', summary));

  const filter = el('input');
  filter.placeholder = 'filter slot or clip...';
  filter.style.cssText = 'width:100%;margin:8px 0';
  host.append(filter);

  const body = el('div');
  host.append(body);
  // 1,902 rows is enough that rebuilding the whole list on every keystroke is
  // felt. Cap what is drawn and say so, rather than drawing all of it slowly.
  const LIMIT = 300;
  const draw = () => {
    body.innerHTML = '';
    const q = filter.value.trim().toLowerCase();
    const hits = doc.rows.filter((r) => !q
      || r.slot.toLowerCase().includes(q) || r.clip.toLowerCase().includes(q));
    const table = el('table');
    table.style.cssText = 'font-size:12px;width:100%';
    for (const r of hits.slice(0, LIMIT)) {
      const tr = el('tr');
      const slot = el('td', 'muted', r.slot);
      slot.style.cssText = 'padding-right:12px;white-space:nowrap';
      const cell = el('td');
      const inp = el('input');
      inp.value = shown(r.path, r.clip);
      inp.style.cssText = 'width:100%;font-size:12px';
      const note = el('span', r.ok ? 'muted' : 'conf lbl inferred');
      note.dataset.hint = r.ok ? '' : 'no .anim of this name on the disc';
      note.textContent = note.dataset.hint;
      inp.oninput = () => touch(r.path, inp.value, inp, note, r.clip);
      mark(r.path, inp, note, r.clip);
      cell.append(inp);
      const nd = el('td'); nd.append(note);
      tr.append(slot, cell, nd);
      table.append(tr);
    }
    body.append(table);
    if (hits.length > LIMIT) {
      body.append(el('p', 'muted',
        `showing ${LIMIT} of ${hits.length} — type to narrow it down`));
    }
    if (!hits.length) body.append(el('p', 'muted', 'nothing matches that.'));
  };
  filter.oninput = draw;
  draw();
}

const LAYOUTS = { form: renderForm, grid: renderGrid, checklist: renderChecklist,
                  table: renderTable, moveset: renderMoveset,
                  skeleton: renderSkeleton };

// Which pair of routes serves a given extension.
//
// `.msd` and `.skel` are character stat files like the other eight, but they
// are not flat field lists, so they have their own readers on the server and
// their own routes. Everything else goes through char_file/char_file_set.
// The two extra documents arrive without `layout`, `title` or `blurb` -- the
// server sends the DATA and this file decides how to draw it -- so those are
// filled in here.
const KIND_ROUTES = {
  msd:  { get: 'msd_file',  set: 'msd_set',  layout: 'moveset',
          title: 'Move set',
          blurb: 'How this character moves, per weapon stance. '
               + 'Paths are full, because RunSpeed exists once per gaitset.' },
  skel: { get: 'skel_file', set: 'skel_set', layout: 'skeleton',
          title: 'Skeleton',
          blurb: 'Each bone offset from its parent. World position is derived '
               + 'and is not editable -- editing it would move the parent.' },
};
const routesFor = (name) =>
  KIND_ROUTES[String(name).split('.').pop().toLowerCase()]
  || { get: 'char_file', set: 'char_file_set' };

// `.msd` — grouped move-set fields, and the speed group again by gaitset.
//
// The server groups Speed a second time under `bands`, because "this
// character's rifle speeds" is the question people actually have and a flat
// list of 270 numbers is not an answer to it. Both views address the SAME
// paths, so `shown()` keeps them in step: type a new RunSpeed in the banded
// view and the flat one shows it too, because neither owns the value.
function renderMoveset(host) {
  for (const g of doc.groups || []) {
    const h = el('h3', null, g.label);
    h.style.cssText = 'margin:16px 0 6px';
    host.append(h);
    if (g.blurb) host.append(el('p', 'muted', g.blurb));

    if (g.bands && g.bands.length) {
      for (const band of g.bands) {
        const bh = el('h4', null, band.stance || band.gaitset);
        bh.style.cssText = 'margin:12px 0 4px;font-size:12px;color:var(--gold)';
        if (band.default) bh.append(el('span', 'tag', 'default'));
        host.append(bh);
        for (const f of band.fields) host.append(cellField(f));
      }
      continue;
    }
    for (const f of g.fields || []) host.append(cellField(f));
  }
  if (!(doc.groups || []).length)
    host.append(el('p', 'muted', 'this move set has no editable fields.'));
}

// `.skel` — one row per bone, three offset components each.
//
// Indented by depth so the hierarchy is visible, because an offset is
// meaningless without knowing whose child it is. `world` is shown and NOT
// editable: it is this bone's offset accumulated through every parent, so
// offering it as a field would invite an edit that silently moves the parent.
function renderSkeleton(host) {
  const rows = doc.rows || [];
  if (!rows.length) {
    host.append(el('p', 'muted', 'this skeleton has no editable bones.'));
    return;
  }
  const table = el('table');
  table.style.cssText = 'font-size:12px;border-collapse:collapse;margin-top:8px';
  const head = el('tr');
  for (const c of ['bone', 'x', 'y', 'z', 'world (derived)']) {
    const th = el('th', 'muted', c);
    th.style.cssText = 'text-align:left;padding-right:10px';
    head.append(th);
  }
  table.append(head);
  for (const r of rows) {
    const tr = el('tr');
    const name = el('td', null, r.label);
    name.style.cssText = 'padding-right:10px;white-space:nowrap;padding-left:'
      + (8 * (r.depth || 0)) + 'px';
    name.title = r.parent ? `child of ${r.parent}` : 'root';
    tr.append(name);
    for (const c of r.cells) {
      const td = el('td');
      const inp = el('input');
      inp.value = shown(c.path, c.value);
      inp.style.cssText = 'width:80px;font-size:12px';
      inp.title = c.path;
      inp.oninput = () => touch(c.path, inp.value, inp, null, c.value);
      mark(c.path, inp, null, c.value);
      td.append(inp);
      tr.append(td);
    }
    const w = el('td', 'muted',
                 (r.world || []).map((n) => (+n).toFixed(2)).join(', '));
    w.style.paddingLeft = '10px';
    w.title = 'accumulated through every parent — derived, not editable';
    tr.append(w);
    table.append(tr);
  }
  host.append(table);
}

// One editable cell from a {path, field, value} row. The `.msd` rows carry
// `field` where the charfile family carries `label`, so this is a thin shim
// rather than a fifth copy of `field()`.
function cellField(f) {
  return field({ label: f.field || f.path.split('/').pop(), path: f.path,
                 value: f.value, hint: f.kind === 'float' ? '' : (f.kind || ''),
                 control: f.kind === 'float' ? 'float' : undefined });
}

// ------------------------------------------------------------------ the file

async function openFile(name) {
  const host = $('#rigBody');
  const stop = waiting(host, `Reading ${name}…`);
  const rt = routesFor(name);
  const r = await call(rt.get + '?name=' + encodeURIComponent(name), 'GET');
  stop();
  if (!r.ok) return renderProblem(host, r.problem);
  doc = r;
  // The two extra formats send data without presentation. Fill in what the
  // shared chrome below expects rather than special-casing it in four places.
  if (rt.layout) {
    doc.layout = rt.layout;
    doc.title = doc.title || `${rt.title} — ${name}`;
    doc.blurb = doc.blurb || rt.blurb;
    // These two live in one copy in SWAT.DAT, unlike the .cr family which is
    // duplicated per level. whereLabel() prints a copy count, so say one.
    if (doc.copies == null) doc.copies = 1;
  }
  edits = {};
  renderPlan($('#rigPlan'), null);
  $('#btnRigSave').disabled = true;
  $('#btnRigCheck').disabled = true;
  $('#rigWhere').textContent = whereLabel();
  drawKinds();
  host.innerHTML = '';
  const head = el('h2', null, doc.title);
  head.style.cssText = 'margin:0 0 2px';
  host.append(head, el('p', 'muted', doc.blurb));
  (LAYOUTS[doc.layout] || renderForm)(host);
}

function drawKinds() {
  const bar = $('#rigKinds');
  bar.innerHTML = '';
  const c = roster.characters.find((x) => x.stem === stem);
  if (!c) { bar.append(el('span', 'muted', 'Pick a character')); return; }
  bar.append(el('span', 'slab', stem));
  for (const k of roster.kinds) {
    const name = c.files[k.ext];
    if (!name) continue;
    const b = el('button', doc && doc.name === name ? 'on' : '', k.title);
    b.title = `${name} — ${k.blurb}`;
    b.onclick = () => openFile(name);
    bar.append(b);
  }
}

function drawList() {
  const host = $('#rigList');
  host.innerHTML = '';
  const q = ($('#rigFilter').value || '').trim().toLowerCase();
  const hits = roster.characters.filter((c) => !q || c.stem.includes(q));
  for (const c of hits) {
    const row = el('div', 'row' + (c.stem === stem ? ' on' : ''));
    row.style.cssText = 'padding:4px 10px;cursor:pointer;display:flex;gap:6px';
    row.append(el('span', null, c.stem), el('span', 'muted', String(c.count)));
    row.title = Object.keys(c.files).map((e) => '.' + e).join(' ');
    row.onclick = () => {
      stem = c.stem;
      doc = null;
      edits = {};
      drawList();
      drawKinds();
      const first = roster.kinds.find((k) => c.files[k.ext]);
      if (first) openFile(c.files[first.ext]);
    };
    host.append(row);
  }
  $('#rigCount').textContent = `${hits.length} of ${roster.characters.length}`;
}

// ------------------------------------------------------------------ the write

// The checklist stores its state under two synthetic prefixes so one edits map
// can hold both halves of a weapon row. The server's contract for .ars is the
// FULL desired list, not a delta, so it is rebuilt here from the document plus
// whatever the user touched.
function payload() {
  if (doc.layout !== 'checklist') return { ...edits };
  const out = {};
  for (const item of doc.items) {
    const on = shown('__on__' + item.name, item.on ? '1' : '0') === '1';
    if (on) out[item.name] = shown('__flag__' + item.name, item.flag);
  }
  return out;
}

async function check() {
  const host = $('#rigPlan');
  const stop = waiting(host, 'Checking every copy…');
  const r = await call(routesFor(doc.name).set, 'POST', {
    name: doc.name, changes: payload(), dry: true,
    everywhere: $('#rigEverywhere').checked,
  });
  stop();
  if (!r.ok) {
    // The refusal message already carries the byte counts and names the
    // archive, so the plan table would only repeat it in smaller type.
    renderProblem(host, r.problem);
    $('#btnRigSave').disabled = true;
    return;
  }
  renderPlan(host, r.plan);
  $('#btnRigSave').disabled = false;
}

async function apply() {
  const host = $('#rigPlan');
  const stop = waiting(host, 'Writing…');
  const r = await call(routesFor(doc.name).set, 'POST', {
    name: doc.name, changes: payload(), dry: false,
    everywhere: $('#rigEverywhere').checked,
  });
  stop();
  if (!r.ok) { renderProblem(host, r.problem); return; }
  renderPlan(host, r.plan);
  A.toast(`${doc.name}: written to ${r.copies} cop${r.copies === 1 ? 'y' : 'ies'}. `
    + 'Ctrl+Z to undo.', 'good');
  A.refreshHistory();
  openFile(doc.name);
}

// --------------------------------------------------------------------- boot

let loaded = false;

async function load() {
  if (loaded) return;
  const host = $('#rigBody');
  const stop = waiting(host, 'Reading the character roster…');
  const r = await call('char_roster', 'GET');
  stop();
  if (!r.ok) return renderProblem(host, r.problem);
  loaded = true;
  roster = r;
  host.innerHTML = '';
  host.append(el('p', 'muted',
    `${r.characters.length} characters, ${r.total} files. Pick one on the left.`));
  drawList();
  drawKinds();
  $('#rigFilter').oninput = drawList;
  $('#btnRigCheck').onclick = check;
  $('#btnRigSave').onclick = apply;
  $('#rigEverywhere').onchange = () => {
    renderPlan($('#rigPlan'), null);
    $('#btnRigSave').disabled = true;
  };
}

export function init(bridge) {
  A = bridge;
  onPanel((panel) => { if (panel === 'rig') load(); });
}
