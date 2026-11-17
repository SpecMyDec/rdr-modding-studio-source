// states.js — LOGIC ▸ Cast ▸ BEHAVIOUR TABLES.
//
// The Cast tab is the palette: which character, which behaviour. This is the
// editor for one behaviour, and it is deliberately two panels with two
// different confidences, because the data has two different confidences:
//
//   BINDINGS   a `.tbl` names nine-ish `.stateb` files. That set is closed and
//              readable off the archive's own table of contents, so every
//              binding is a DROPDOWN. Never free text: a misspelt state name
//              is not an enemy that misbehaves, it is a filename the game
//              looks up and does not find.
//   NUMBERS    a `.stateb` is a token stream. Its floats are editable four
//              bytes for four, so a write can never overflow a slot -- and the
//              opcodes around them have NO NAMES ANYWHERE ON THIS DISC. The
//              panel says so at the top and on every row. Control flow is not
//              authorable here and this UI must not imply that it is.
//
// The third act is Clone: copy a `.tbl` to a new name so one enemy can be
// given its own behaviour. That is the only edit in the product that adds a
// file rather than patching one, which makes it the only one Ctrl+Z cannot
// take back -- so the sheet says that in the plan, not in a footnote.

// No `notBuilt` guard anywhere below, on purpose. When these routes are not in
// the build `call()` already returns the `not_in_this_build` refusal as data,
// and `renderProblem` prints the sentence that goes with it. A second branch
// would only be a second place to say the same thing worse.
import { call, renderProblem, renderPlan, openSheet, rungBadge,
         waiting, onPanel, el } from './transfer.js';

let A = null;

let cur = null;            // the behaviour_table payload
let curArchive = null;
let edits = {};            // path -> new state name
let stateDoc = null;       // the behaviour_state payload
let stateEdits = {};       // byte offset -> new float
let planTimer = null;
let statePlanTimer = null;

const q = (sel) => document.querySelector(sel);

// Byte counts, grouped, the same way renderPlan writes them.
const num = (v) => (v == null ? '—' : Number(v).toLocaleString());

function hex(n) {
  return '0x' + Number(n).toString(16).toUpperCase().padStart(4, '0');
}

// ------------------------------------------------------------- the list

// The in-flight fill, so a second visit to the panel waits on the first
// instead of starting its own and appending every archive twice.
let arcFill = null;

async function fillArchives() {
  const sel = q('#btArc');
  if (!sel || sel.dataset.filled) return;
  if (arcFill) return arcFill;
  arcFill = (async () => {
    // /api/archives opens all 66 containers to report each one's largest free
    // run. Measured at 50 s on a loaded machine, and there is no cheaper route
    // that lists them -- so say something, or the panel spends a minute
    // looking like two empty boxes.
    const host = q('#btList');
    const stop = host ? waiting(host, 'Reading the disc…') : () => {};
    try {
      const arcs = await A.api('archives');
      for (const a of arcs.archives || arcs || []) {
        const name = a.archive || a.name || a;
        // Only the archives that hold behaviour tables at all. SWAT.DAT is in
        // the list because it holds 2,705 of the disc's 3,275 of them.
        if (!/^LVLS_/.test(name) && name !== 'SWAT.DAT') continue;
        const o = el('option', null, name);
        o.value = name;
        sel.append(o);
      }
      // Marked filled ONLY once the options are in the DOM. Setting the flag
      // before the await meant a single failed call -- disc handed to PCSX2,
      // server restarting, anything -- left a picker that could never refill,
      // because every later visit took the early return above and the empty
      // catch said nothing. The panel was then dead for the session with no
      // sentence anywhere explaining why.
      sel.dataset.filled = '1';
      if (A.level && [...sel.options].some((o) => o.value === A.level.archive))
        sel.value = A.level.archive;
      // No level loaded: open where the tables are, not on the first level
      // archive, which holds none (same fix as Scripting, DESIGN-REVIEW P4b).
      else if ([...sel.options].some((o) => o.value === 'SWAT.DAT'))
        sel.value = 'SWAT.DAT';
    } catch (ex) {
      // Deliberately no message here: `initStatesTab` calls `loadTables` next
      // and that owns `#btList`, so anything written here is overwritten a
      // tick later. `loadTables` says it instead, off the empty picker.
      A.toast('Could not read the archive list.', 'bad');
    } finally {
      stop();
      arcFill = null;
    }
  })();
  return arcFill;
}

let listedFor = null;

async function loadTables(force) {
  const arc = q('#btArc').value;
  const host = q('#btList');
  if (!arc) {
    host.innerHTML = '';
    // An EMPTY picker and an unchosen one are different failures and used to
    // read the same. "Choose an archive" in front of a dropdown with nothing
    // in it is the single most confusing sentence this panel could print.
    host.append(el('p', 'muted', q('#btArc').options.length
      ? 'Choose an archive.'
      : 'Could not read the archive list. Leave this panel and come back to '
        + 'try again.'));
    return;
  }
  const filter = (q('#btFilter').value || '').trim().toLowerCase();
  const key = arc + '|' + filter;
  if (!force && listedFor === key) return;
  listedFor = key;

  const stop = waiting(host, 'Reading the archive…');
  // every table (SWAT.DAT has 2,705; the server's default page is 400)
  const r = await call('behaviour_tables?archive=' + encodeURIComponent(arc) + '&limit=100000'
    + (filter ? '&q=' + encodeURIComponent(filter) : ''), 'GET');
  stop();
  host.innerHTML = '';
  if (!r.ok) return renderProblem(host, r.problem);

  q('#btCount').textContent = r.total;
  if (!r.tables.length) {
    host.append(el('p', 'muted', filter
      ? 'Nothing in this archive matches that.'
      : 'This archive holds no behaviour tables.'));
    return;
  }
  if (r.shown < r.total) {
    const more = el('p', 'muted',
      r.total - r.shown + ' more — type in the filter box to narrow it.');
    more.style.cssText = 'font-size:11px;padding:4px 8px';
    host.append(more);
  }
  for (const t of r.tables) {
    const row = el('div', 'row');
    row.append(el('span', 'nm', t.id));
    if (t.states != null) row.append(el('span', 'tag', t.states + ' states'));
    row.title = t.file + '\n' + num(t.stored) + ' of ' + num(t.slot)
      + ' stored bytes, ' + num(t.headroom) + ' spare';
    row.onclick = () => openTable(arc, t.file);
    host.append(row);
  }
}

// ------------------------------------------------------------ the bindings

async function openTable(archive, file) {
  const host = q('#btBody');
  curArchive = archive;
  edits = {};
  q('#btTitle').textContent = file;
  q('#btApply').disabled = true;
  q('#btClone').disabled = true;
  q('#btRename').disabled = true;
  q('#btExportTbl').disabled = true;
  const stop = waiting(host, 'Reading the table…');
  const r = await call('behaviour_table?archive=' + encodeURIComponent(archive)
    + '&name=' + encodeURIComponent(file), 'GET');
  stop();
  if (!r.ok) { cur = null; return renderProblem(host, r.problem); }
  cur = r;
  q('#btClone').disabled = false;
  q('#btRename').disabled = false;
  q('#btExportTbl').disabled = false;
  q('#btCopies').textContent = r.copies.length > 1
    ? r.copies.length + ' copies on the disc' : 'only copy on the disc';
  renderBindings();
}

function renderBindings() {
  const host = q('#btBody');
  host.innerHTML = '';
  if (!cur) {
    host.append(el('p', 'muted', 'Pick a table on the left.'));
    return;
  }

  const head = el('div');
  head.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:2px';
  // `untested`, not `measured`, and the difference is the whole point. What is
  // MEASURED is the list below: 27,263 of the 27,544 bindings the disc ships
  // resolve to a .stateb in their own archive, so the options are exact. What
  // is UNTESTED is the game reading a changed one — nobody has booted it. The
  // badge tracks the weaker of the two, always.
  head.append(el('b', null, cur.file), rungBadge('untested'));
  host.append(head);
  const where = el('div', 'muted',
    cur.states.length + ' bindings · ' + cur.palette.length
    + ' state files in this archive · ' + num(cur.stored) + ' of '
    + num(cur.slot) + ' stored bytes, ' + num(cur.headroom) + ' spare');
  where.style.fontSize = '11px';
  host.append(where);
  const rung = el('div', 'muted', 'Unverified in game.');
  rung.style.cssText = 'font-size:11px;margin-top:4px';
  host.append(rung);

  if (cur.unresolved.length) {
    // Never silently correct the file. 281 shipped bindings name a state that
    // is not in their archive, and every one of them is in SWAT.DAT.
    const w = el('div', 'infbox');
    w.style.borderLeftColor = 'var(--warn)';
    w.append(el('b', null, cur.unresolved.length
      + ' of these name a state file that is not in this archive.'));
    w.append(el('div', null, 'They are shown as the file spells them and left '
      + 'alone unless you change them: ' + cur.unresolved.join(', ')));
    w.style.fontSize = '11px';
    host.append(w);
  }

  const planHost = el('div');
  planHost.id = 'btPlan';
  planHost.style.cssText = 'margin-top:10px;font-size:12px';

  for (const s of cur.states) {
    const row = el('div', 'field');
    row.style.cssText = 'display:flex;align-items:center;gap:6px;margin-top:5px';
    const lab = el('label', null, s.key.replace(/state$/i, ''));
    lab.style.cssText = 'width:96px;flex:none;font-size:12px';
    lab.title = s.path + ' (line ' + s.line + ')';
    row.append(lab, bindingControl(s));
    host.append(row);
  }

  const note = el('p', 'muted', 'Choices are this archive’s own state files.');
  note.style.cssText = 'font-size:11px;margin-top:10px';
  host.append(note, planHost);
}

function bindingControl(s) {
  const wrap = el('div');
  wrap.style.cssText = 'display:flex;align-items:center;gap:5px;flex:1;min-width:0';

  const sel = document.createElement('select');
  sel.style.cssText = 'flex:1;min-width:0;font-size:12px';
  let seen = false;
  for (const stem of cur.palette) {
    const o = el('option', null, stem);
    o.value = stem;
    if (stem.toLowerCase() === String(s.value).toLowerCase()) {
      o.selected = true;
      seen = true;
    }
    sel.append(o);
  }
  if (!seen && s.value) {
    // The file's own value survives even when nothing on the disc matches it.
    const o = el('option', null, s.value + '  (not in this archive)');
    o.value = s.value;
    o.selected = true;
    sel.append(o);
  }
  // CASE-INSENSITIVE, and that is not pedantry. Archive entry names are stored
  // lower case, so the palette reads `sher70_always` while the file itself
  // writes `Sher70_Always` — which is the ordinary case, not an edge one. An
  // exact comparison here made "put it back the way it was" register as a
  // change, and the panel would then offer to write a differently-cased copy
  // of a value nobody touched.
  sel.onchange = () => {
    const same = sel.value.toLowerCase() === String(s.value).toLowerCase();
    if (same) delete edits[s.path];
    else edits[s.path] = sel.value;
    sel.style.borderColor = same ? '' : 'var(--gold)';
    replan();
  };

  // Opening the state file is the other half of this panel, so it is one
  // click from the binding rather than a second search.
  const go = el('button', '', '→');
  go.title = 'Show the numbers inside ' + (s.value || 'this state');
  go.style.cssText = 'flex:none;padding:1px 7px';
  go.onclick = () => openState(curArchive, sel.value);
  wrap.append(sel, go);
  return wrap;
}

function replan() {
  clearTimeout(planTimer);
  planTimer = setTimeout(async () => {
    const host = q('#btPlan');
    const btn = q('#btApply');
    if (!host) return;
    const keys = Object.keys(edits);
    if (!keys.length) {
      host.innerHTML = '';
      btn.disabled = true;
      btn.title = 'Change a binding first.';
      return;
    }
    const stop = waiting(host, 'Checking it fits…');
    const r = await plan(true);
    stop();
    if (r.ok && r.plan) {
      renderPlan(host, r.plan);
      btn.disabled = r.plan.fits === false;
      btn.title = r.plan.fits === false ? 'It does not fit its slot.' : '';
      return;
    }
    renderProblem(host, r.problem);
    btn.disabled = true;
  }, 140);
}

function plan(dry) {
  return call('behaviour_table_set', 'POST', {
    archive: curArchive, name: cur.file, changes: edits,
    everywhere: q('#btEverywhere').checked, dry,
  });
}

async function applyBindings() {
  const btn = q('#btApply');
  const host = q('#btPlan');
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'Applying…';
  const r = await plan(false);
  btn.textContent = was;
  if (!r.ok) return renderProblem(host, r.problem);
  A.toast(r.plan?.undo?.label || 'Applied.', 'good');
  A.refreshHistory();
  await openTable(curArchive, cur.file);
}

// -------------------------------------------------------------- the numbers

async function openState(archive, name) {
  const host = q('#btState');
  stateEdits = {};
  q('#btStateApply').disabled = true;
  q('#btExportState').disabled = true;
  q('#btRenameState').disabled = true;
  q('#btStateTitle').textContent = name;
  const stop = waiting(host, 'Reading the state file…');
  const r = await call('behaviour_state?archive=' + encodeURIComponent(archive)
    + '&name=' + encodeURIComponent(name), 'GET');
  stop();
  if (!r.ok) { stateDoc = null; return renderProblem(host, r.problem); }
  stateDoc = r;
  q('#btExportState').disabled = false;
  q('#btRenameState').disabled = false;
  q('#btStateCopies').textContent = r.copies.length > 1
    ? r.copies.length + ' copies on the disc' : 'only copy on the disc';
  renderNumbers();
}

function renderNumbers() {
  const host = q('#btState');
  host.innerHTML = '';
  if (!stateDoc) {
    host.append(el('p', 'muted', 'Pick a state with the → button.'));
    return;
  }
  const d = stateDoc;

  const head = el('div');
  head.style.cssText = 'display:flex;align-items:center;gap:8px';
  head.append(el('b', null, d.file), rungBadge(d.rung || 'untested'));
  host.append(head);

  // THE SENTENCE THIS PANEL EXISTS TO SAY. It is not a footnote and it is not
  // behind a disclosure triangle: the numbers are writable and their meaning
  // is unknown, and a UI that shows the first without the second is lying by
  // omission.
  const box = el('div', 'infbox');
  box.append(el('b', null, 'Editable numbers. Effects unknown.'));
  box.append(el('div', null,
    'Safe to retune. Unverified in game. Details: Guides > Modding guide.'));
  const ev = el('div', 'ev',
    d.counts.floats + ' float parameters · ' + d.counts.ints
    + ' opcodes and indices · ' + d.counts.names + ' inline names · '
    + d.counts.opcodes + ' of the integers land in the action range'
    + (d.counts.unresolved_text
        ? ' · ' + d.counts.unresolved_text + ' words that are probably text'
        : ''));
  box.append(ev);
  host.append(box);

  const which = el('p', 'muted', 'Float detection is heuristic. Writes always fit.');
  which.style.cssText = 'font-size:11px;margin-top:8px';
  host.append(which);

  const planHost = el('div');
  planHost.id = 'btStatePlan';
  planHost.style.cssText = 'margin-top:10px;font-size:12px';

  if (!d.numbers.length) {
    const none = el('p', 'muted', 'No float parameters in this file.');
    none.style.marginTop = '10px';
    host.append(none, planHost);
    return;
  }

  const table = el('div');
  table.style.marginTop = '8px';
  for (const n of d.numbers) {
    const row = el('div', 'field');
    row.style.cssText = 'display:flex;align-items:baseline;gap:8px;margin-top:6px';

    const lab = el('label', null, hex(n.off));
    lab.style.cssText = 'width:64px;flex:none;font-size:11px;font-family:monospace';
    lab.title = 'byte ' + n.off;

    const inp = document.createElement('input');
    inp.type = 'number';
    inp.step = 'any';
    inp.value = String(n.value);
    inp.style.cssText = 'width:110px;flex:none;font-size:12px';
    inp.onkeydown = (e) => e.stopPropagation();

    const note = el('span', 'muted');
    note.style.cssText = 'font-size:11px;min-width:0';
    // Built as text, never interpolated: a state name can carry any character
    // the archive alphabet allows, and this line is the only place a file's
    // own words reach the page.
    note.textContent = n.near
      ? 'after the name ' + n.near + ' (' + n.near_tokens_back + ' tokens back)'
      : 'no inline name near it';
    note.title = 'before: ' + (n.before || '—') + '\nafter: ' + (n.after || '—');

    inp.oninput = () => {
      const v = parseFloat(inp.value);
      if (Number.isNaN(v) || v === n.value) delete stateEdits[n.off];
      else stateEdits[n.off] = v;
      inp.style.borderColor = stateEdits[n.off] != null ? 'var(--gold)' : '';
      replanState();
    };

    row.append(lab, inp, note);
    table.append(row);
  }
  host.append(table, planHost);
}

function replanState() {
  clearTimeout(statePlanTimer);
  statePlanTimer = setTimeout(async () => {
    const host = q('#btStatePlan');
    const btn = q('#btStateApply');
    if (!host) return;
    if (!Object.keys(stateEdits).length) {
      host.innerHTML = '';
      btn.disabled = true;
      return;
    }
    const stop = waiting(host, 'Checking it fits…');
    const r = await statePlan(true);
    stop();
    if (r.ok && r.plan) {
      renderPlan(host, r.plan);
      btn.disabled = r.plan.fits === false;
      return;
    }
    renderProblem(host, r.problem);
    btn.disabled = true;
  }, 140);
}

function statePlan(dry) {
  return call('behaviour_state_set', 'POST', {
    archive: curArchive, name: stateDoc.file, changes: stateEdits,
    everywhere: q('#btStateEverywhere').checked, dry,
  });
}

async function applyNumbers() {
  const btn = q('#btStateApply');
  const host = q('#btStatePlan');
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'Applying…';
  const r = await statePlan(false);
  btn.textContent = was;
  if (!r.ok) return renderProblem(host, r.problem);
  A.toast(r.plan?.undo?.label || 'Applied.', 'good');
  A.refreshHistory();
  await openState(curArchive, stateDoc.file);
}

// ------------------------------------------------------------------ cloning

function cloneTable() {
  if (!cur) return;
  const source = cur.file;
  const archive = curArchive;
  const base = source.slice(0, source.lastIndexOf('.')) + '_copy';
  let name = base;

  const sheet = openSheet({
    title: 'Give this enemy its own behaviour table',
    subject: {
      title: 'A copy of ' + source,
      where: archive,
      format: cur.states.length + ' bindings, unchanged from the original',
      // `untested` and it stays that way until someone boots it. The
      // structure is checked hard -- the table of contents is re-decoded and
      // compared name for name after the insert -- but SLUS_205.00 is a
      // packed executable nobody has read, so "nothing looks a file up by
      // index" remains an assumption that has survived, not a fact.
      rung: 'untested',
    },
    primary: 'Add it',
    startLabel: 'Name it, then this will say what it costs.',
    renderOptions(host) {
      const f = el('div', 'field');
      f.style.cssText = 'display:flex;align-items:center;gap:8px';
      const lab = el('label', null, 'New name');
      lab.style.cssText = 'width:80px;flex:none';
      const inp = document.createElement('input');
      inp.value = base;
      inp.style.cssText = 'flex:1;min-width:0';
      inp.onkeydown = (e) => e.stopPropagation();
      const suffix = el('span', 'muted', '.tbl');
      f.append(lab, inp, suffix);
      host.append(f);
      const why = el('p', 'muted',
        'Assign it to an enemy in Build. Adds cannot be undone.');
      why.style.cssText = 'font-size:11px;margin-top:8px';
      host.append(why);
      let t = null;
      const recheck = () => {
        clearTimeout(t);
        t = setTimeout(async () => {
          name = inp.value.trim();
          sheet.busy('Checking there is room…');
          const r = await call('behaviour_table_clone', 'POST',
            { archive, source, name, dry: true });
          if (!r.ok) return sheet.refuse(r.problem);
          sheet.plan(r.plan);
          const n = el('div', 'ev', r.note);
          n.style.marginTop = '8px';
          document.querySelector('#xferPlan').append(n);
        }, 200);
      };
      inp.oninput = recheck;
      recheck();
    },
    async onApply(api) {
      api.busy('Adding it…');
      const r = await call('behaviour_table_clone', 'POST',
        { archive, source, name, dry: false });
      if (!r.ok) return api.refuse(r.problem);
      api.done('<b style="color:var(--grn)">Added ' + r.file + '</b>. '
        + r.states + ' bindings. Adds cannot be undone.');
      listedFor = null;
      await loadTables(true);
      A.refreshHistory();
    },
  });
}

// ------------------------------------------------------------------ renaming
//
// The fourth act, and the most dangerous one in this tab, for three reasons
// the sheet states rather than implies:
//
//   IT RE-INDEXES THE ARCHIVE.  The engine resolves a file by binary search
//     over the TOC, so a record renamed in place would leave the array
//     unsorted -- and bsearch on an unsorted array does not merely lose the
//     moved entry, it can miss arbitrary others. The record MOVES, and the
//     plan says how far.
//   IT CANNOT BE UNDONE.  Undo rewrites file CONTENT into a slot; it does not
//     put a name back. Same property as Clone and it gets the same prominence.
//   IT IS FINITE.  There is no remove(), so the old name's bytes stay as dead
//     space. The sheet shows the name-block room left, because a tight archive
//     runs out and that should be visible before the write, not during.
//
// Referrers are the other half. An enemy names its table by stem, so renaming
// one that an .enm points at breaks the lookup exactly the way a misspelt
// state name does -- which is why bindings above are a dropdown. Both numbers
// are shown: "2 files, 20 enemies". They mislead in opposite directions on
// their own.
function renameTable() {
  if (!cur) return;
  const archive = curArchive;
  const source = cur.file;
  const stem = source.slice(0, source.lastIndexOf('.'));
  let name = stem;
  let confirmReferrers = false;
  let last = null;

  const sheet = openSheet({
    title: 'Rename this behaviour table',
    subject: {
      title: source,
      where: archive,
      format: cur.states.length + ' bindings, unchanged by a rename',
      // Same honesty as Clone: the TOC is re-decoded and re-checked for
      // sortedness after the move, but SLUS_205.00 is a packed executable
      // nobody has read, so "nothing looks a file up by index" is still an
      // assumption that has survived rather than a fact.
      rung: 'untested',
    },
    primary: 'Rename it',
    startLabel: 'Type a new name and this will say what it costs.',
    renderOptions(host) {
      const f = el('div', 'field');
      f.style.cssText = 'display:flex;align-items:center;gap:8px';
      const lab = el('label', null, 'New name');
      lab.style.cssText = 'width:80px;flex:none';
      const inp = document.createElement('input');
      inp.value = stem;
      inp.style.cssText = 'flex:1;min-width:0';
      inp.onkeydown = (e) => e.stopPropagation();
      f.append(lab, inp, el('span', 'muted', '.tbl'));
      host.append(f);

      const warn = el('p', 'muted');
      warn.style.cssText = 'font-size:11px;margin-top:8px;line-height:1.6';
      warn.textContent = 'Renames cannot be undone — undo puts a file\u2019s '
        + 'contents back, not its name.';
      host.append(warn);

      // Only built when the check says referrers exist. A permanent empty
      // checkbox would teach people to tick it without reading.
      const refBox = el('div');
      refBox.style.cssText = 'margin-top:8px';
      host.append(refBox);

      let t = null;
      const recheck = () => {
        clearTimeout(t);
        t = setTimeout(async () => {
          name = inp.value.trim();
          refBox.innerHTML = '';
          confirmReferrers = false;
          sheet.busy('Checking names and referrers…');
          const r = await call('behaviour_table_rename', 'POST',
            { archive, name: source, to: name,
              confirm_referrers: true, dry: true });
          last = r;
          if (!r.ok) return sheet.refuse(r.problem);
          // The sheet keeps Apply disabled on a missing or
          // fits:false plan, which is why the route costs the
          // referrer rewrites rather than returning bare ok.
          sheet.plan(r.plan);
          const host2 = document.querySelector('#xferPlan');
          const note = el('div', 'ev');
          note.style.cssText = 'margin-top:8px;line-height:1.7';
          const moved = (r.index_from != null && r.index_to != null
                         && r.index_from !== r.index_to)
            ? `The archive is re-indexed: this record moves from position `
              + `${r.index_from} to ${r.index_to}. No file data moves.`
            : 'The archive is re-indexed.';
          note.append(el('div', null, moved));
          note.append(el('div', null,
            `${r.spare_name_bytes} bytes of name space left in this archive `
            + `— there is no delete, so the old name stays as dead space.`));
          host2.append(note);

          if (r.rewrites_needed) {
            const lab2 = el('label');
            lab2.style.cssText = 'display:flex;gap:6px;align-items:flex-start;'
              + 'font-size:11px;line-height:1.6';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.onchange = () => { confirmReferrers = cb.checked; };
            const text = el('span', null,
              `Repoint the enemies that name it: ${r.rewrites_needed} `
              + `file${r.rewrites_needed === 1 ? '' : 's'}, `
              + `${r.enemies_affected} `
              + `enem${r.enemies_affected === 1 ? 'y' : 'ies'}. `
              + 'Without this they would name a table that no longer exists.');
            lab2.append(cb, text);
            refBox.append(lab2);
            const which = el('div', 'muted');
            which.style.cssText = 'font-size:11px;margin-top:4px';
            which.textContent = (r.enm_referrers || []).join(', ');
            refBox.append(which);
          }
        }, 250);
      };
      inp.oninput = recheck;
      recheck();
    },
    async onApply(api) {
      if (last && last.rewrites_needed && !confirmReferrers) {
        return api.refuse({
          code: 'not_editable',
          message: `${last.enemies_affected} enemies still name ${source}.`,
          fix: 'Tick the box to repoint them, or pick a table nothing binds.',
          detail: '',
        });
      }
      api.busy('Renaming…');
      const r = await call('behaviour_table_rename', 'POST',
        { archive, name: source, to: name,
          confirm_referrers: confirmReferrers, dry: false });
      if (!r.ok) return api.refuse(r.problem);
      const n = (r.rewritten || []).length;
      api.done('<b style="color:var(--grn)">Renamed to ' + r.to + '</b>. '
        + (n ? n + ' referrer file' + (n === 1 ? '' : 's') + ' repointed. ' : '')
        + 'Renames cannot be undone.');
      listedFor = null;
      cur = null;
      await loadTables(true);
      A.refreshHistory();
    },
  });
}

// Renaming a `.stateb` is the MIRROR IMAGE of renaming a `.tbl`, and the
// difference is measured rather than stylistic.
//
// Every one of the 1,315 `.stateb` in the level archives is named by at least
// one `.tbl` binding -- there are no orphans -- and none is named inline in a
// token stream. So where a referenced table is REFUSED (some of its referrers
// are token streams nobody can rewrite safely), a referenced state is
// REPOINTED: refusing over referrers would refuse all 1,315 and there would
// be no verb. There is therefore no "repoint them?" checkbox here. It always
// repoints, and the plan says which files.
//
// What refuses instead is the SLOT, and often enough that the sheet treats it
// as ordinary rather than exceptional: a referrer `.tbl` is deflated into a
// fixed slot, so a SAME-LENGTH rename can still overflow it. 8.3% of
// same-length state renames across the level archives are refused for exactly
// that.
function renameState() {
  if (!stateDoc) return;
  const archive = curArchive;
  const source = stateDoc.file;
  const stem = source.slice(0, source.lastIndexOf('.'));
  let name = stem;

  const sheet = openSheet({
    title: 'Rename this state file',
    subject: {
      title: source,
      where: archive,
      format: 'every table that binds it is repointed in the same step',
      rung: 'untested',
    },
    primary: 'Rename it',
    startLabel: 'Type a new name and this will say what it costs.',
    renderOptions(host) {
      const f = el('div', 'field');
      f.style.cssText = 'display:flex;align-items:center;gap:8px';
      const lab = el('label', null, 'New name');
      lab.style.cssText = 'width:80px;flex:none';
      const inp = document.createElement('input');
      inp.value = stem;
      inp.style.cssText = 'flex:1;min-width:0';
      inp.onkeydown = (e) => e.stopPropagation();
      f.append(lab, inp, el('span', 'muted', '.stateb'));
      host.append(f);

      const warn = el('p', 'muted');
      warn.style.cssText = 'font-size:11px;margin-top:8px;line-height:1.6';
      warn.textContent = 'Renames cannot be undone — undo puts a file\u2019s '
        + 'contents back, not its name. A same-length name can still be '
        + 'refused: the tables that bind it are compressed into fixed slots.';
      host.append(warn);

      const info = el('div');
      info.style.marginTop = '8px';
      host.append(info);

      let t = null;
      const recheck = () => {
        clearTimeout(t);
        t = setTimeout(async () => {
          name = inp.value.trim();
          info.innerHTML = '';
          sheet.busy('Costing every table that binds it…');
          const r = await call('behaviour_state_rename', 'POST',
            { archive, name: source, to: name, dry: true });
          if (!r.ok) {
            sheet.refuse(r.problem);
            // A slot refusal still carries the costed rows, and they are the
            // whole explanation -- show them rather than only the sentence.
            if (r.plan && (r.plan.writes || []).length) {
              const p = el('div', 'muted');
              p.style.cssText = 'font-size:11px;margin-top:6px;line-height:1.6';
              p.textContent = (r.blocked_by_slot || []).length
                ? 'Refused by: ' + (r.blocked_by_slot || []).join(', ')
                : '';
              info.append(p);
            }
            return;
          }
          sheet.plan(r.plan);
          const n = el('div', 'ev');
          n.style.cssText = 'margin-top:8px;line-height:1.7';
          n.append(el('div', null,
            `${(r.tbl_referrers || []).length} table(s) bind it, `
            + `${r.bindings} binding(s) in total — all repointed in this step.`));
          n.append(el('div', null,
            `${r.spare_name_bytes} bytes of name space left in this archive.`));
          document.querySelector('#xferPlan').append(n);
        }, 250);
      };
      inp.oninput = recheck;
      recheck();
    },
    async onApply(api) {
      api.busy('Renaming and repointing…');
      const r = await call('behaviour_state_rename', 'POST',
        { archive, name: source, to: name, dry: false });
      if (!r.ok) return api.refuse(r.problem);
      const n = (r.rewritten || []).length;
      api.done('<b style="color:var(--grn)">Renamed to ' + r.to + '</b>. '
        + n + ' table' + (n === 1 ? '' : 's') + ' repointed. '
        + 'Renames cannot be undone.');
      listedFor = null;
      stateDoc = null;
      await loadTables(true);
      A.refreshHistory();
    },
  });
}

// ------------------------------------------------------------------ wiring

// Export is a raw copy of the file as it sits on the disc right now -- the
// same bytes the game reads, saved wherever the browser saves things.
function exportCurrent(name) {
  if (!name || !curArchive) return;
  // Through saveFile, never a blind <a download>: the route answers a
  // missing or unreadable file with JSON, and a blind click saves that JSON
  // under the table's name (the note above saveFile in app.js records the
  // time it happened).
  A.saveFile('/api/file.bin?archive=' + encodeURIComponent(curArchive)
    + '&name=' + encodeURIComponent(name), name.replace(/\//g, '_'));
}

let wired = false;

async function initStatesTab() {
  if (wired) {
    // A first visit whose archive list failed leaves the picker empty; this is
    // the retry, and it is why the panel is recoverable rather than dead.
    if (!q('#btArc').dataset.filled) await fillArchives();
    // Follow the loaded level on re-entry, like Surfaces does, unless the
    // user picked an archive here themselves. Seeding once meant this tab
    // could quietly stay on last hour's level while the header named today's.
    const sel = q('#btArc');
    if (!sel.dataset.userPick && A.level
        && sel.value !== A.level.archive
        && [...sel.options].some((o) => o.value === A.level.archive)) {
      sel.value = A.level.archive;
      sel.dataset.rerender = '1';
      sel.dispatchEvent(new Event('change'));
      return;
    }
    loadTables(false);
    return;
  }
  wired = true;
  await fillArchives();
  q('#btArc').onchange = () => {
    const sel = q('#btArc');
    if (sel.dataset.rerender) delete sel.dataset.rerender;
    else sel.dataset.userPick = '1';
    listedFor = null;
    // Close the open table too. The Apply button posts against `curArchive`,
    // which is set when a table is OPENED -- so with a table from archive A on
    // screen and the picker moved to B, Apply wrote into A while every label
    // said B. A wrong-target write, not a cosmetic staleness.
    cur = null; curArchive = null; edits = {};
    q('#btTitle').textContent = 'Pick a table';
    q('#btBody').innerHTML = '';
    q('#btApply').disabled = true;
    q('#btClone').disabled = true;
    q('#btExportTbl').disabled = true;
    loadTables(true);
  };
  {
    // Debounced: every keystroke used to queue a disc read against a server
    // that answers one request at a time -- typing 8 characters cost 8 reads.
    let t = null;
    q('#btFilter').oninput = () => {
      clearTimeout(t);
      t = setTimeout(() => loadTables(false), 180);
    };
  }
  q('#btApply').onclick = applyBindings;
  q('#btStateApply').onclick = applyNumbers;
  q('#btClone').onclick = cloneTable;
  q('#btRename').onclick = renameTable;
  q('#btRenameState').onclick = renameState;
  q('#btExportTbl').onclick = () => exportCurrent(cur && cur.file);
  q('#btExportState').onclick = () => exportCurrent(stateDoc && stateDoc.file);
  renderBindings();
  renderNumbers();
  loadTables(true);
}

export function init(bridge) {
  A = bridge;
  onPanel((panel) => { if (panel === 'states') initStatesTab(); });
  // The Cast palette's "Edit this table's bindings" lands here: switch the
  // archive if needed, then open the table it named. The tab itself has
  // already been switched by the caller; this only aims the panel.
  window.addEventListener('rdr:behaviour-table', async (e) => {
    const d = e.detail || {};
    if (!d.archive || !d.file) return;
    await initStatesTab();
    const sel = q('#btArc');
    if (![...sel.options].some((o) => o.value === d.archive)) return;
    if (sel.value !== d.archive) {
      sel.value = d.archive;
      listedFor = null;
      loadTables(true);
    }
    openTable(d.archive, d.file);
  });
}
