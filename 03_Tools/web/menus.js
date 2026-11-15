// menus.js — FRONT.DAT's screens and the in-game HUD.
//
// 215 UI files, 1,111 entries on the disc once you count every copy. They are
// not one format: 187 are the `<TAG:field:field>` markup, 12 are ordinary
// props files wearing a UI extension, 13 are bare preload lists and 3 cannot
// be read at all (two are zero bytes, one is UTF-16). The server routes on
// CONTENT, and this panel renders whichever of the three came back rather
// than guessing from the extension.
//
// The thing this panel exists to prevent: renaming a button in `pause.swui`
// froze story-level loading. Values are safe, names are not, because the
// engine resolves widgets by name -- 69 of 210 screen names are verbatim
// strings inside the executable. So a coordinate edit saves with no ceremony,
// and a rename has to be looked at first. Warned, not forbidden: the refusal
// names what it found and tells you the one word that overrides it.

import { call, notBuilt, onPanel, renderPlan, renderProblem, waiting, el }
  from './transfer.js';

let A = null;
let rows = [];            // the browse list
let cur = null;           // the open file's GET payload
let edits = {};           // { path: new text }
let started = false;

const $ = (s) => document.querySelector(s);

// A field path is `BUTTON#3.f1` and a token path adds `.t0`. Those go into
// dataset values and element ids, never into an interpolated selector -- `#`
// in a path would make any `querySelector` built from it a broken id lookup.
const keyOf = (p) => p;

function fmt(n) { return n == null ? '—' : Number(n).toLocaleString(); }

// ------------------------------------------------------------------ browsing

async function loadMenus() {
  if (!started) { started = true; wire(); }
  const host = $('#menuList');
  if (!host) return;
  host.innerHTML = '';
  host.append(el('p', 'muted', 'Reading every UI file on the disc…'));
  const r = await call('menus?archive=' + encodeURIComponent(scope()), 'GET');
  if (!r.ok) {
    if (notBuilt(r)) return renderNotBuilt(host);
    return renderProblem(host, r.problem);
  }
  rows = r.rows || [];
  const tally = $('#menuTally');
  if (tally) {
    const d = r.dialects || {};
    // A count on the right, not a sentence in the heading (DESIGN-REVIEW A4).
    tally.textContent = `${r.total} files`;
    tally.title = `${d.tags || 0} markup, ${d.props || 0} props, `
      + `${d.list || 0} preload lists, ${d.unreadable || 0} unreadable`;
  }
  renderList();
}

function scope() {
  const sel = $('#menuScope');
  return sel ? sel.value : 'FRONT.DAT';
}

function renderList() {
  const host = $('#menuList');
  if (!host) return;
  host.innerHTML = '';
  const q = ($('#menuFilter')?.value || '').trim().toLowerCase();
  const shown = rows.filter((r) => !q || r.name.toLowerCase().includes(q));
  if (!shown.length) {
    host.append(el('p', 'muted', 'No UI file matches that.'));
    return;
  }
  for (const r of shown) {
    const row = el('div', 'row');
    if (cur && cur.name === r.name) row.classList.add('sel');
    row.append(el('span', 'nm', r.name));
    // Copy count is the honest headline for these files: `pause.swui` is in
    // 62 archives and one edit touches every one of them.
    const tag = el('span', 'tag',
      `${r.copies} cop${r.copies === 1 ? 'y' : 'ies'} · ${fmt(r.headroom)} B`);
    if (r.dialect === 'unreadable') tag.style.color = 'var(--red)';
    row.append(tag);
    row.title = `${r.dialect} · ${fmt(r.bytes)} bytes · `
      + `${fmt(r.stored)} of a ${fmt(r.slot)}-byte slot`;
    row.onclick = () => openMenu(r.name);
    host.append(row);
  }
}

function renderNotBuilt(host) {
  host.innerHTML = '';
  const box = el('div', 'infbox');
  box.append(el('b', null, 'Menu editing is not in this build.'));
  box.append(el('div', null,
    'It is an internal feature until an edited screen has been booted.'));
  host.append(box);
}

// -------------------------------------------------------------- one screen

async function openMenu(name) {
  cur = null; edits = {};
  const host = $('#menuFields');
  if (!host) return;
  waiting(host, 'Reading ' + name);
  // Send the scope. Without it the server falls back to the first archive
  // that holds the file -- so with SWAT.DAT selected, opening a shared screen
  // could load FRONT.DAT's copy, and "Only this archive's copy" then WROTE to
  // FRONT.DAT while every label on screen said SWAT.DAT.
  const sc = scope();
  const r = await call('menu?name=' + encodeURIComponent(name)
    + (sc && sc !== 'all' ? '&archive=' + encodeURIComponent(sc) : ''), 'GET');
  if (!r.ok) {
    if (notBuilt(r)) return renderNotBuilt(host);
    return renderProblem(host, r.problem);
  }
  cur = r;
  renderList();                    // repaint so the selected row highlights
  const t = $('#menuTitle');
  // Say WHOSE copy is on screen -- several archives carry the same file, and
  // the scope decides which one an edit lands in.
  if (t) t.textContent = name + (r.archive ? '  —  ' + r.archive : '');
  const meta = $('#menuMeta');
  if (meta) {
    meta.innerHTML = '';
    meta.append(el('span', 'tag', r.dialect));
    meta.append(el('span', 'muted',
      ` ${fmt(r.bytes)} bytes · ${fmt(r.stored)} of a ${fmt(r.slot)}-byte slot`
      + ` · ${fmt(r.headroom)} spare · in ${r.copies} archive`
      + `${r.copies === 1 ? '' : 's'}`));
  }
  // If the executable could not be read the rename check lost half its
  // evidence. Saying so is the difference between "no danger found" and
  // "did not look".
  const warn = $('#menuEvidence');
  if (warn) {
    warn.textContent = r.evidence_note || '';
    warn.style.display = r.evidence_note ? '' : 'none';
  }
  renderFields();
  setDirty(false);
}

function renderFields() {
  const host = $('#menuFields');
  if (!host) return;
  host.innerHTML = '';
  if (!cur) return;
  if (!cur.parsable) {
    const box = el('div', 'infbox');
    box.append(el('b', null, 'This file cannot be edited here.'));
    box.append(el('div', null, cur.why || ''));
    host.append(box);
    return;
  }
  if (cur.dialect === 'tags') return renderTags(host);
  if (cur.dialect === 'list') return renderLines(host);
  return renderProps(host);
}

// The markup dialect: one block per tag, one input per field, and a nested
// row of inputs when a field is a comma-separated tuple so a coordinate can
// be nudged without retyping the other two.
function renderTags(host) {
  let live = 0;
  for (const t of cur.tags || []) {
    if (t.commented && !showCommented()) continue;
    if (!t.commented) live++;
    const box = el('div', 'field');
    box.style.cssText = 'display:block;border-top:1px solid var(--line);'
      + 'padding:7px 0';
    const head = el('div');
    head.style.cssText = 'display:flex;gap:8px;align-items:baseline';
    const nm = el('b', null, '<' + t.tag + '>');
    head.append(nm);
    const ln = el('span', 'muted', 'line ' + t.line);
    ln.style.fontSize = '11px';
    head.append(ln);
    if (t.commented) {
      const c = el('span', 'conf lbl dead', 'commented out');
      c.title = 'This tag is behind a ; so the game never reads it.';
      head.append(c);
    }
    box.append(head);
    const src = el('div', 'ev', t.text);
    src.style.cssText = 'font-size:11px;margin:3px 0 5px;opacity:.75';
    box.append(src);
    for (const f of t.fields) box.append(fieldRow(t, f));
    host.append(box);
  }
  if (!live) host.append(el('p', 'muted', 'No live tags in this file.'));
}

function fieldRow(tag, f) {
  const row = el('div');
  row.style.cssText = 'display:flex;gap:8px;align-items:center;margin:2px 0';
  const lab = el('label', null, f.label || ('field ' + f.index));
  lab.style.cssText = 'width:210px;font-size:11px;flex:none';
  // The tooltip carries the provenance: which of these names was read out of
  // the developers' own `example.swui` and which is only a shape that held.
  lab.title = f.label_from
    ? `${f.path} — from ${f.label_from}`
      + (f.label_support ? `; ${f.label_support}` : '')
    : f.path + ' — no documented meaning; shape only';
  row.append(lab);

  const commented = !!tag.commented;
  // A tuple gets one box per number so x, y and z are separately nudgeable.
  if (f.tokens.length > 1) {
    for (const tk of f.tokens) row.append(inputFor(tag, f, tk.path, tk.text,
                                                   commented, 62));
  } else {
    row.append(inputFor(tag, f, f.path, f.value, commented, 260));
  }
  const kind = el('span', 'muted', f.kind);
  kind.style.cssText = 'font-size:11px;flex:none';
  row.append(kind);
  return row;
}

function inputFor(tag, f, path, value, commented, width) {
  const inp = el('input');
  inp.value = value;
  inp.dataset.path = keyOf(path);
  inp.style.cssText = `width:${width}px;font-size:11px`;
  if (commented) {
    inp.disabled = true;
    inp.title = 'Inside a comment — the game never reads it.';
    return inp;
  }
  const isName = f.kind === 'text';
  if (isName) inp.style.borderLeft = '3px solid var(--amber, #c90)';
  inp.title = isName
    ? 'Name field. Rename checks for lookups first.'
    : 'A number. Safe to change.';
  inp.oninput = () => {
    const v = inp.value;
    if (v === value) delete edits[path]; else edits[path] = v;
    inp.style.borderColor = v === value ? '' : 'var(--gold)';
    setDirty(Object.keys(edits).length > 0);
  };
  return inp;
}

// The preload lists (`*cache*.swui`): one screen name per line, and every one
// of them is a lookup key, so they are all treated as names.
function renderLines(host) {
  const note = el('p', 'muted',
    'A preload list. Every line names a screen the game loads ahead of time, '
    + 'so every line is a name — changing one is a rename.');
  host.append(note);
  for (const ln of cur.lines || []) {
    const row = el('div', 'field');
    const lab = el('label', null, 'line ' + ln.line);
    lab.style.cssText = 'width:80px;font-size:11px';
    const inp = el('input');
    inp.value = ln.value;
    inp.dataset.path = keyOf(ln.path);
    inp.style.cssText = 'width:260px;font-size:11px;'
      + 'border-left:3px solid var(--amber, #c90)';
    inp.oninput = () => {
      if (inp.value === ln.value) delete edits[ln.path];
      else edits[ln.path] = inp.value;
      inp.style.borderColor = inp.value === ln.value ? '' : 'var(--gold)';
      setDirty(Object.keys(edits).length > 0);
    };
    row.append(lab, inp);
    host.append(row);
  }
}

// The props dialect — `.swuibook` page setups and the popups. props.py already
// reads these; the payload is its `summary()`, so the shape is the same one
// the Look and Creatures panels render.
function renderProps(host) {
  host.append(el('p', 'muted',
    'This one is a props file, not menu markup — the same grammar as the '
    + 'lighting and creature files.'));
  for (const f of cur.fields || []) {
    const row = el('div', 'field');
    const lab = el('label', null, f.path.split('/').pop());
    lab.style.cssText = 'width:210px;font-size:11px';
    lab.title = f.path;
    const inp = el('input');
    inp.value = f.value;
    inp.dataset.path = keyOf(f.path);
    inp.style.cssText = 'width:260px;font-size:11px';
    if (f.kind === 'text') inp.style.borderLeft = '3px solid var(--amber, #c90)';
    inp.oninput = () => {
      if (inp.value === f.value) delete edits[f.path];
      else edits[f.path] = inp.value;
      inp.style.borderColor = inp.value === f.value ? '' : 'var(--gold)';
      setDirty(Object.keys(edits).length > 0);
    };
    const note = el('span', 'muted', f.kind);
    note.style.fontSize = '11px';
    row.append(lab, inp, note);
    host.append(row);
  }
}

function showCommented() { return !!$('#menuShowCommented')?.checked; }

function setDirty(on) {
  const b = $('#btnMenuCheck');
  if (b) b.disabled = !on;
  const s = $('#btnMenuSave');
  if (s) s.disabled = true;          // a save is only unlocked by a check
  const c = $('#menuDirty');
  if (c) c.textContent = on
    ? `${Object.keys(edits).length} pending change`
      + `${Object.keys(edits).length === 1 ? '' : 's'}`
    : '';
}

// -------------------------------------------------------------- check & save

let lastPlan = null;
let confirmRenames = false;

async function check() {
  const host = $('#menuPlan');
  if (!host || !cur) return;
  waiting(host, 'Checking every copy');
  const r = await call('menu_set', 'POST', body(true));
  lastPlan = null;
  if (!r.ok) {
    renderProblem(host, r.problem);
    // A rename refusal carries the graded list, and it is the whole point of
    // the panel: show what was found and offer the override in place.
    if (r.renames?.length) host.append(renameBox(r.renames));
    if (r.plan) host.append(renderPlan(el('div'), r.plan));
    const sv = $('#btnMenuSave');
    if (sv) sv.disabled = true;
    return;
  }
  lastPlan = r.plan;
  host.innerHTML = '';
  if (r.risks?.length) host.append(riskBox(r.risks));
  host.append(renderPlan(el('div'), r.plan));
  const s = $('#btnMenuSave');
  if (s) {
    s.disabled = !r.plan.fits;
    s.textContent = `Save to ${r.copies} cop`
      + `${r.copies === 1 ? 'y' : 'ies'}`;
  }
}

function renameBox(list) {
  const box = el('div', 'infbox');
  box.style.borderLeftColor = 'var(--red)';
  box.append(el('b', null,
    'These are names the game looks up, not captions:'));
  for (const r of list) {
    const line = el('div');
    line.style.cssText = 'margin:5px 0;font-size:11px';
    const a = el('code', null, r.old);
    const b = el('code', null, r.new);
    line.append(el('span', null, '<' + r.tag + '>  '), a,
                el('span', null, '  →  '), b);
    const why = el('div', 'muted', r.why);
    why.style.cssText = 'font-size:11px;margin-top:2px';
    line.append(why);
    box.append(line);
  }
  const lab = el('label');
  lab.style.cssText = 'display:flex;gap:6px;align-items:center;margin-top:8px';
  const cb = el('input');
  cb.type = 'checkbox';
  cb.checked = confirmRenames;
  cb.onchange = () => { confirmRenames = cb.checked; check(); };
  lab.append(cb, el('span', null,
    'Rename anyway — I know this can hang the screen or the level it loads.'));
  box.append(lab);
  return box;
}

function riskBox(list) {
  const box = el('div', 'infbox');
  box.append(el('b', null, `${list.length} change`
    + `${list.length === 1 ? '' : 's'} touch text rather than numbers`));
  for (const r of list) {
    const line = el('div', 'muted');
    line.style.cssText = 'font-size:11px;margin-top:3px';
    line.textContent = `<${r.tag}> ${r.old} → ${r.new} — ${r.why}`;
    box.append(line);
  }
  return box;
}

function body(dry) {
  return {
    name: cur.name,
    archive: cur.archive,
    changes: edits,
    scope: $('#menuOneOnly')?.checked ? 'one' : 'all',
    confirm_renames: confirmRenames,
    dry: !!dry,
  };
}

async function save() {
  if (!cur || !lastPlan) return;
  const host = $('#menuPlan');
  waiting(host, 'Writing');
  // Dry run again immediately before the write. The disc can have moved --
  // another panel, an undo -- and a stale plan is exactly the thing that must
  // never reach `Studio.write`.
  const pre = await call('menu_set', 'POST', body(true));
  if (!pre.ok || !pre.plan?.fits) {
    renderProblem(host, pre.problem
      || { code: 'slot_overflow', message: 'That no longer fits.', fix: '' });
    return;
  }
  const r = await call('menu_set', 'POST', body(false));
  if (!r.ok) {
    renderProblem(host, r.problem);
    if (r.renames?.length) host.append(renameBox(r.renames));
    return;
  }
  host.innerHTML = '';
  host.append(renderPlan(el('div'), r.plan));
  A.toast(`Saved ${cur.name} to ${r.copies} cop`
    + `${r.copies === 1 ? 'y' : 'ies'}. Ctrl+Z to undo.`, 'good');
  A.refreshHistory?.();
  edits = {};
  confirmRenames = false;
  await openMenu(cur.name);
}

// ------------------------------------------------------------------- wiring

function wire() {
  $('#menuFilter')?.addEventListener('input', renderList);
  $('#menuScope')?.addEventListener('change', loadMenus);
  $('#menuShowCommented')?.addEventListener('change', renderFields);
  const c = $('#btnMenuCheck');
  if (c) c.onclick = check;
  const s = $('#btnMenuSave');
  if (s) s.onclick = save;
}

export function init(bridge) {
  A = bridge;
  onPanel((panel) => { if (panel === 'menus') loadMenus(); });
}
