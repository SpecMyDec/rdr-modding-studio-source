// csvedit.js — THE DISC'S 1,022 TABLES, AS TABLES.
//
// Every .csv on the disc was reachable before this only through DISC ▸ Files,
// as a wall of raw text with commas in it. Nothing said which of the 256
// `snd_<num>.csv` was music, nothing said whose music, and `mpcharbounty.csv`
// read as 48 rows of `maka`, `mani`, `mbdg` with no way to learn that the
// third one is Dan.
//
// Three views over one tab, because they are three questions about the same
// 1,022 files and hiding any of them behind another click would be the
// product's own rule broken:
//
//   Table              pick a file, edit cells, see what saving costs
//   Music by level     which .csv holds THIS level's music (and effects)
//   Multiplayer roster the 48 codes with the name the game actually shows
//
// The server owns every fact here. This file finds no headers, resolves no
// names and counts no bytes; `sheets.py` does all three and this renders what
// it says.

import { call, notBuilt, el, onPanel, renderPlan, renderProblem, waiting }
  from './transfer.js';

let A = null;
let started = false;

let cat = null;          // GET csv_files
let doc = null;          // GET csv  — the open file
let edits = {};          // "row.col" -> new text, pending
let plan = null;         // the last dry run
let soundMap = null;     // GET csv_sound, cached for the session
let view = 'sheet';
let checkTimer = null;
let checkSeq = 0;

const $ = (s) => document.querySelector(s);
const n = (v) => (v == null ? '—' : Number(v).toLocaleString());

// ------------------------------------------------------------------ the list

async function loadList() {
  const host = $('#csvList');
  if (!host) return;
  const stop = waiting(host, 'Reading the disc index…');
  const r = await call('csv_files', 'GET');
  stop();
  if (!r.ok) {
    renderProblem(host, r.problem);
    return;
  }
  cat = r;
  const sel = $('#csvFamily');
  sel.innerHTML = '';
  const all = el('option', null, 'Everything (' + r.total + ')');
  all.value = '';
  sel.append(all);
  for (const f of r.families) {
    const o = el('option', null, f.label + ' (' + f.count + ')');
    o.value = f.id;
    sel.append(o);
  }
  sel.onchange = renderList;
  $('#csvSearch').oninput = renderList;
  $('#csvCount').textContent = r.total + ' files';
  renderList();
}

function familyWhy(id) {
  const f = (cat?.families || []).find((x) => x.id === id);
  return f ? f.why : '';
}

function familyLabel(id) {
  const f = (cat?.families || []).find((x) => x.id === id);
  return f ? f.label : id;
}

function renderList() {
  const host = $('#csvList');
  if (!host || !cat) return;
  const q = ($('#csvSearch').value || '').trim().toLowerCase();
  const fam = $('#csvFamily').value;
  host.innerHTML = '';
  let shown = 0;
  for (const f of cat.files) {
    if (fam && f.family !== fam) continue;
    if (q && !f.name.includes(q)) continue;
    shown += 1;
    if (shown > 400) continue;
    const row = el('div', 'row' + (doc && doc.name === f.name ? ' sel' : ''));
    row.append(el('span', 'nm', f.name));
    const t = el('span', 'tag',
      f.copies > 1 ? f.copies + ' copies' : '1 copy');
    t.style.marginLeft = 'auto';
    row.append(t);
    // Headroom is the fact that decides whether an edit is possible at all,
    // so it rides on the row rather than waiting to be discovered on save.
    // It is the TIGHTEST copy's -- a write goes to every archive and is
    // refused if any one of them overflows, so the roomiest copy's figure
    // would be an invitation to a refusal.
    const h = el('span', 'tag', f.headroom === 0 ? 'full' : '+' + n(f.headroom));
    if (f.headroom === 0) h.style.color = 'var(--rust)';
    row.append(h);
    row.title = f.name + ' — ' + familyLabel(f.family) + '\n'
      + n(f.bytes) + ' bytes; the tightest of ' + f.copies + ' cop'
      + (f.copies === 1 ? 'y' : 'ies') + ' stores ' + n(f.stored)
      + ' in a ' + n(f.slot) + '-byte slot';
    row.onclick = () => openFile(f.name);
    host.append(row);
  }
  if (!shown) host.append(el('p', 'muted', 'Nothing matches.'));
  else if (shown > 400) {
    const more = el('p', 'muted',
      'Showing 400 of ' + shown + '. Narrow the search.');
    more.style.padding = '6px 10px';
    host.append(more);
  }
}

// ------------------------------------------------------------- the open file

async function openFile(name) {
  setView('sheet');
  const host = $('#csvBody');
  const stop = waiting(host, 'Reading ' + name + '…');
  const r = await call('csv?name=' + encodeURIComponent(name), 'GET');
  stop();
  if (!r.ok) {
    doc = null;
    renderProblem(host, r.problem);
    return;
  }
  doc = r;
  edits = {};
  plan = null;
  renderList();
  renderSheet();
  renderAbout();
  renderRoom();
  renderPlanBox();
  syncButtons();
}

function cellValue(row, col) {
  const key = row + '.' + col;
  if (key in edits) return edits[key];
  return doc.rows[row].cells[col];
}

function isChanged(row, col) {
  const key = row + '.' + col;
  return key in edits && edits[key] !== doc.rows[row].cells[col];
}

// A cell is a <td> until it is clicked, and an <input> only while it has
// focus. The widest file on the disc, snd_2898.csv, is 486 rows and 9,277
// cells; that many live <input> elements is a second of layout every time the
// panel is drawn, and the sheet is mostly read.
function editCell(td, row, col) {
  if (td.querySelector('input')) return;
  const inp = el('input');
  inp.value = cellValue(row, col);
  inp.style.cssText = 'width:100%;min-width:60px;font-family:Consolas,monospace;'
    + 'font-size:12px;padding:1px 3px';
  td.textContent = '';
  td.append(inp);
  inp.focus();
  inp.select();
  const commit = () => {
    const key = row + '.' + col;
    const was = doc.rows[row].cells[col];
    if (inp.value === was) delete edits[key];
    else edits[key] = inp.value;
    paintCell(td, row, col);
    syncButtons();
    scheduleCheck();
  };
  inp.onblur = commit;
  inp.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
    if (e.key === 'Escape') { e.preventDefault(); inp.value = cellValue(row, col); inp.blur(); }
  };
}

function paintCell(td, row, col) {
  td.textContent = cellValue(row, col);
  const changed = isChanged(row, col);
  td.style.color = changed ? 'var(--gold)' : '';
  td.style.fontWeight = changed ? '600' : '';
  td.title = changed
    ? 'was “' + doc.rows[row].cells[col] + '” — row ' + (row + 1)
      + ', column ' + (col + 1)
    : 'row ' + (row + 1) + ', column ' + (col + 1);
}

function renderSheet() {
  const host = $('#csvBody');
  host.innerHTML = '';
  if (!doc) {
    host.append(el('div', 'empty', 'Pick a table'));
    return;
  }
  $('#csvWhere').textContent = doc.name + ' · ' + doc.family_label;

  if (doc.truncated) {
    const w = el('div', 'infbox');
    w.append(el('b', null, 'Showing the first ' + n(doc.rows.length)
      + ' of ' + n(doc.total_rows) + ' rows.'));
    host.append(w);
  }

  const ann = doc.annotation;
  let table = null;
  let body = null;
  let curSection = null;

  // `context` is the run of comment lines above the first section. It gets a
  // table so the row numbers stay in one column with everything else, but no
  // caption -- calling a block of `#` comments "Settings" would be a label
  // for something that is not there.
  const startTable = (sec, context) => {
    if (!context) {
      const cap = el('div', 'muted');
      cap.style.cssText = 'margin:14px 0 4px;font-size:11px;'
        + 'text-transform:uppercase;letter-spacing:.6px';
      cap.textContent = sec && sec.header !== null
        ? 'Table — ' + sec.rows + ' row' + (sec.rows === 1 ? '' : 's')
          + ', header on line ' + (sec.header + 1)
        : 'Settings — name and value, no header row';
      // Only worth saying when the file is not the ordinary one-table shape.
      if (doc.sections.length > 1 || (sec && sec.header > 0)) host.append(cap);
    }
    table = el('table');
    table.style.marginBottom = '6px';
    body = el('tbody');
    table.append(body);
    host.append(table);
  };

  for (const row of doc.rows) {
    if (row.section !== curSection && row.kind !== 'comment'
        && row.kind !== 'blank') {
      curSection = row.section;
      startTable(doc.sections[row.section]);
    }
    if (!table) startTable(null, true);

    const tr = el('tr');
    tr.style.cursor = 'default';

    const gutter = el('td', 'muted', String(row.i + 1));
    gutter.style.cssText = 'width:34px;text-align:right;color:var(--dim);'
      + 'font-size:11px;user-select:none';
    tr.append(gutter);

    if (row.kind === 'comment' || row.kind === 'blank') {
      // The comment itself, not the row of padding commas after it. Joining
      // the cells back up would also lose the quoting that keeps a comma
      // inside the comment from reading as a column break.
      const td = el('td', 'muted',
        row.kind === 'blank' ? '' : (row.cells.find((c) => c.trim()) || ''));
      td.colSpan = 40;
      td.style.cssText = 'color:var(--dim);font-style:italic;'
        + 'white-space:pre-wrap;padding:2px 8px';
      // A comment or a spacer line is part of the file and is shown, but it is
      // not a cell and pretending it is one would put an editable box around
      // something the game never reads as data.
      tr.append(td);
      body.append(tr);
      continue;
    }

    if (ann) {
      const who = ann.labels[row.i];
      const td = el('td', null, who || '');
      td.style.cssText = 'color:var(--rust);font-weight:600;max-width:150px;'
        + 'overflow:hidden;text-overflow:ellipsis';
      td.title = who ? ann.why : '';
      tr.append(td);
    }

    row.cells.forEach((_, col) => {
      const td = el(row.kind === 'header' ? 'th' : 'td');
      if (row.kind === 'header') {
        td.style.position = 'static';
        td.style.color = 'var(--title)';
      }
      paintCell(td, row.i, col);
      td.style.cursor = 'text';
      td.onclick = () => editCell(td, row.i, col);
      tr.append(td);
    });
    body.append(tr);
  }

  if (ann) {
    const note = el('p', 'muted', ann.why);
    note.style.cssText = 'margin-top:10px;font-size:11px;max-width:640px';
    host.append(note);
  }
}

// ------------------------------------------------------------ the side panel

function renderAbout() {
  const host = $('#csvAbout');
  host.innerHTML = '';
  if (!doc) return;
  host.append(el('b', null, doc.family_label));
  if (doc.family_why) host.append(el('p', 'muted', doc.family_why));

  const facts = el('table', 'keys');
  const row = (k, v) => {
    const tr = el('tr');
    tr.append(el('td', 'muted', k), el('td', null, v));
    facts.append(tr);
  };
  row('Rows', n(doc.total_rows));
  row('Header', doc.header === null ? 'none found'
    : 'line ' + (doc.header + 1));
  row('Shape', doc.tabular ? 'one plain table'
    : doc.sections.length + ' sections stacked in one file');
  row('Copies', n(doc.copies.length));
  row('Size', n(doc.bytes) + ' bytes');
  host.append(facts);

  // The disc's only level -> music link, said from the file's end of it.
  const uses = levelsUsing(doc.name);
  if (uses.length) {
    const h = el('p', null, '');
    h.style.marginTop = '10px';
    h.append(el('b', null, 'Used by ' + uses.length + ' level'
      + (uses.length === 1 ? '' : 's')));
    host.append(h);
    const list = el('div', 'muted');
    list.style.cssText = 'font-size:11px;line-height:1.6';
    list.textContent = uses.join(', ');
    host.append(list);
  }
}

function levelsUsing(name) {
  if (!soundMap) return [];
  const out = [];
  for (const lv of soundMap.levels) {
    for (const role of ['music', 'streams', 'sfx']) {
      if (lv[role].file === name) { out.push(lv.level); break; }
    }
    if (lv.config === name) out.push(lv.level);
  }
  return [...new Set(out)];
}

function renderRoom() {
  const host = $('#csvRoom');
  host.innerHTML = '';
  if (!doc || !doc.copies.length) return;
  $('#csvCopies').textContent = doc.copies.length
    + (doc.copies.length === 1 ? ' copy' : ' copies');
  const worst = doc.copies[0];        // sheets.copies() sorts worst-first
  const lead = el('p', 'muted');
  lead.style.fontSize = '11px';
  lead.textContent = worst.headroom === 0
    ? 'Tightest copy has no spare bytes.'
    : 'Tightest copy has ' + n(worst.headroom) + ' spare bytes.';
  host.append(lead);
  const t = el('table');
  t.style.fontSize = '11px';
  for (const c of doc.copies) {
    const tr = el('tr');
    tr.style.cursor = 'default';
    tr.append(el('td', 'muted', c.archive.split('/').pop()),
              el('td', 'num', n(c.stored) + ' / ' + n(c.slot)),
              el('td', c.headroom === 0 ? 'warn' : 'num',
                 c.headroom === 0 ? 'full' : '+' + n(c.headroom)));
    t.append(tr);
  }
  host.append(t);
}

function renderPlanBox() {
  const host = $('#csvPlan');
  host.innerHTML = '';
  const count = Object.keys(edits).length;
  if (!count) {
    host.append(el('p', 'muted', 'No changes yet.'));
    return;
  }
  if (!plan) {
    host.append(el('p', 'muted', 'Checking ' + count + ' cell'
      + (count === 1 ? '' : 's') + '…'));
    return;
  }
  if (plan.problem) {
    renderProblem(host, plan.problem);
    // A refusal that says "it does not fit" without the numbers leaves the
    // user no way to judge how far over they are. `api.overflow` carries the
    // plan for exactly this, so show it under the sentence.
    if (plan.plan) {
      const box = el('div');
      box.style.marginTop = '8px';
      host.append(box);
      renderPlan(box, plan.plan);
    }
    return;
  }
  renderPlan(host, plan.plan);
}

// -------------------------------------------------------------- check & save

function scheduleCheck() {
  clearTimeout(checkTimer);
  plan = null;
  renderPlanBox();
  checkTimer = setTimeout(runCheck, 400);
}

// Every edit is dry-run before Save is pressable, so the byte cost is on
// screen the whole time rather than arriving as a refusal after the click.
// `dry` defaults to TRUE on the wire, so this cannot write by accident.
async function runCheck() {
  if (!doc || !Object.keys(edits).length) { plan = null; renderPlanBox(); return; }
  const seq = ++checkSeq;
  const r = await call('csv_set', 'POST', { name: doc.name, changes: edits });
  if (seq !== checkSeq) return;        // a later keystroke already won
  plan = r.ok ? r : { problem: r.problem, plan: r.plan };
  renderPlanBox();
  syncButtons();
}

function syncButtons() {
  const count = Object.keys(edits).length;
  const fits = !!(plan && plan.ok && plan.plan && plan.plan.fits);
  $('#csvSave').disabled = !count || !fits;
  $('#csvRevert').disabled = !count;
  $('#csvExport').disabled = !doc;
  $('#csvDirty').textContent = !count ? ''
    : count + ' cell' + (count === 1 ? '' : 's') + ' changed'
      + (plan ? '' : ', checking…');
}

async function save() {
  if (!doc) return;
  const btn = $('#csvSave');
  btn.disabled = true;
  const r = await call('csv_set', 'POST',
    { name: doc.name, changes: edits, dry: false });
  if (!r.ok) {
    plan = { problem: r.problem, plan: r.plan };
    renderPlanBox();
    A.toast(r.problem.message, 'bad');
    syncButtons();
    return;
  }
  const failed = r.failed || [];
  A.toast(doc.name + ': ' + r.copies + ' cop'
    + (r.copies === 1 ? 'y' : 'ies') + ' written'
    + (failed.length ? ', ' + failed.length + ' refused' : '')
    + '. Ctrl+Z to undo.', failed.length ? 'warn' : 'good');
  A.refreshHistory();
  openFile(doc.name);
}

function revert() {
  edits = {};
  plan = null;
  renderSheet();
  renderPlanBox();
  syncButtons();
}

// ------------------------------------------------------- music, by level

async function renderSound() {
  const host = $('#csvBody');
  host.innerHTML = '';
  $('#csvWhere').textContent = 'Which file holds each level’s music';
  const stop = waiting(host, 'Reading every level’s sound config…');
  const r = soundMap || await call('csv_sound', 'GET');
  stop();
  if (!r.ok) { renderProblem(host, r.problem); return; }
  soundMap = r;

  const lead = el('p', 'muted');
  lead.style.maxWidth = '760px';
  lead.textContent = 'Levels sharing a SndBegin share these files. '
    + 'Details: Guides > Modding guide.';
  host.append(lead);

  const t = el('table');
  const head = el('tr');
  for (const h of ['Level', 'Music', 'Streams', 'Sound effects', 'Config',
                   'Also used by']) head.append(el('th', null, h));
  const thead = el('thead');
  thead.append(head);
  const tb = el('tbody');
  t.append(thead, tb);

  const fileCell = (slot) => {
    const td = el('td');
    if (!slot.file) { td.append(el('span', 'muted', '—')); return td; }
    if (!slot.present) {
      const s = el('span', 'muted', slot.file + ' (absent)');
      s.title = 'Not on the disc.';
      td.append(s);
      return td;
    }
    const a = el('a', null, slot.file);
    a.href = '#';
    a.style.cssText = 'color:var(--gold);text-decoration:none';
    a.onclick = (e) => { e.preventDefault(); openFile(slot.file); };
    td.append(a);
    return td;
  };

  for (const lv of r.levels) {
    const tr = el('tr');
    tr.style.cursor = 'default';
    const name = el('td', null, lv.level);
    name.style.color = 'var(--title)';
    name.title = (lv.archives || []).join(', ');
    tr.append(name);
    tr.append(fileCell(lv.music), fileCell(lv.streams), fileCell(lv.sfx));
    tr.append(fileCell({ file: lv.config, present: true }));
    const shares = el('td', 'muted', lv.shares_with.join(', ') || '—');
    shares.style.whiteSpace = 'normal';
    tr.append(shares);
    tb.append(tr);
  }
  host.append(t);
}

// ------------------------------------------------------- multiplayer roster

async function renderRoster() {
  const host = $('#csvBody');
  host.innerHTML = '';
  $('#csvWhere').textContent = 'Who is playable in multiplayer';
  const stop = waiting(host, 'Resolving codes against swat.strtbl…');
  const r = await call('csv_roster', 'GET');
  stop();
  if (!r.ok) { renderProblem(host, r.problem); return; }

  const lead = el('p', 'muted');
  lead.style.maxWidth = '760px';
  lead.textContent = r.resolved + ' of ' + r.total + ' names resolve. '
    + 'Bounty: mpcharbounty.csv. Unlocks: unlockables.csv.';
  host.append(lead);

  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:8px;margin:8px 0 12px';
  for (const f of ['mpcharbounty.csv', 'unlockables.csv']) {
    const b = el('button', null, 'Edit ' + f);
    b.onclick = () => openFile(f);
    bar.append(b);
  }
  host.append(bar);

  const t = el('table');
  const head = el('tr');
  for (const h of ['Character', 'Code', 'Bounty', 'Unlocked by', 'Value',
                   'On by default']) head.append(el('th', null, h));
  const thead = el('thead');
  thead.append(head);
  const tb = el('tbody');
  t.append(thead, tb);
  for (const c of r.characters) {
    const tr = el('tr');
    tr.style.cursor = 'default';
    const who = el('td', null, c.name || '(not in the string table)');
    who.style.color = c.name ? 'var(--title)' : 'var(--dim)';
    tr.append(who);
    tr.append(el('td', 'muted', c.code),
              el('td', 'num', c.bounty == null ? '—' : c.bounty),
              el('td', null, c.criteria || '—'),
              el('td', 'num', c.value == null ? '—' : c.value),
              el('td', null, c.default === '1' ? 'yes' : 'no'));
    tb.append(tr);
  }
  host.append(t);
}

// -------------------------------------------------------------------- views

function setView(name) {
  view = name;
  for (const [id, v] of [['csvViewSheet', 'sheet'], ['csvViewSound', 'sound'],
                         ['csvViewRoster', 'roster']]) {
    const b = $('#' + id);
    if (b) b.classList.toggle('on', v === name);
  }
  if (name === 'sheet') renderSheet();
  else if (name === 'sound') renderSound();
  else renderRoster();
}

async function openTab() {
  if (started) return;
  started = true;
  $('#csvViewSheet').onclick = () => setView('sheet');
  $('#csvViewSound').onclick = () => setView('sound');
  $('#csvViewRoster').onclick = () => setView('roster');
  $('#csvSave').onclick = save;
  $('#csvRevert').onclick = revert;
  // Raw bytes off the working disc via the existing file.bin route — every
  // .csv lives in SWAT.DAT, so the archive never varies. What is saved is the
  // disc's CURRENT copy: saved edits are in it, pending unsaved cells are not.
  $('#csvExport').onclick = () => {
    if (!doc) return;
    A.saveFile('/api/file.bin?archive=SWAT.DAT&name='
      + encodeURIComponent(doc.name), doc.name)
      .then((ok) => { if (ok) A.toast('Exported ' + doc.name + '.', 'good'); });
  };
  await loadList();
  // Fetched once, in the background: the Table view uses it to say which
  // levels an open sound file belongs to, and that answer should be there
  // before anyone asks.
  call('csv_sound', 'GET').then((r) => { if (r.ok) { soundMap = r; renderAbout(); } });
  renderSheet();
  renderAbout();
}

export function init(bridge) {
  A = bridge;
  onPanel((panel) => { if (panel === 'csv') openTab(); });
}
