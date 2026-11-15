// logic.js — THE THREE READ-FIRST LOGIC WORKSPACES.
//
// LOGIC ▸ Behaviour  the AI condition vocabulary. The route has served it
//                    since it was written and no button ever called it.
// LOGIC ▸ Links      the doors that join levels. This IS the queued area-link
//                    graph, and the graph and the table read the same route.
// LOGIC ▸ Scripting  mission scripts. Ships READ-ONLY, at rung inferred,
//                    because the action opcodes have no names on the disc.

import { call, notBuilt, renderProblem, renderPlan, waiting, rungBadge,
         onPanel, el } from './transfer.js';

let A = null;

// ------------------------------------------------------ LOGIC ▸ Behaviour

let vocab = null;

async function initBehaviour() {
  const body = document.querySelector('#aivBody');
  if (!body || body.dataset.wired) return;
  body.dataset.wired = '1';
  const stop = waiting(body, 'Reading the AI vocabulary…');
  try { vocab = await A.api('aivocab'); }
  catch (e) { stop(); return body.append(el('p', 'muted', e.message)); }
  stop();

  const stats = document.querySelector('#aivStats');
  stats.innerHTML = '';
  // Numbers straight off the route, not from a document: the design note said
  // 257 conditions and the running server says 247.
  // These two arrive as LISTS, not counts. Printing the list where a number
  // belongs put 39 comma-separated names into a table cell.
  const many = (v) => (Array.isArray(v) ? v.length : v);
  const rows = [
    ['Conditions named', vocab.count, null],
    ['With no name on the disc', many(vocab.unnamed_ids),
     Array.isArray(vocab.unnamed_ids) ? vocab.unnamed_ids.join(', ') : null],
    ['Never used by any file', many(vocab.never_used),
     Array.isArray(vocab.never_used) ? vocab.never_used.join(', ') : null],
    ['Action opcodes', `${vocab.action_min}–${vocab.action_max}`, null],
    ['Action names on the disc', vocab.action_names_available ? 'yes' : 'none', null],
  ];
  const t = el('table');
  for (const [k, v, detail] of rows) {
    const tr = el('tr');
    const vd = el('td', null, String(v));
    if (detail) vd.title = detail;
    tr.append(el('td', 'muted', k), vd);
    t.append(tr);
  }
  stats.append(t);

  const render = () => {
    const q = (document.querySelector('#aivFilter').value || '').toLowerCase();
    body.innerHTML = '';
    const head = el('div');
    head.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:8px';
    head.append(el('b', null, 'What the AI can test'));
    body.append(head);
    const tab = el('table');
    tab.style.width = '100%';
    const hr = el('tr');
    for (const h of ['Condition', 'id', 'Scope', 'Files that use it'])
      hr.append(el('th', 'muted', h));
    tab.append(hr);
    let n = 0;
    for (const c of vocab.conditions || []) {
      if (q && !String(c.name).toLowerCase().includes(q)) continue;
      const tr = el('tr');
      tr.append(el('td', null, c.name),
                el('td', 'muted', String(c.id)),
                el('td', 'muted', c.scope || ''),
                el('td', c.unused ? 'muted' : '',
                   c.unused ? 'never used' : String(c.shipped_files)));
      tab.append(tr);
      n++;
    }
    body.append(tab);
    if (!n) body.append(el('p', 'muted', 'Nothing matches that.'));
  };
  document.querySelector('#aivFilter').oninput = render;
  render();
}

// ----------------------------------------------------------- LOGIC ▸ Links

let linksData = null;

async function initLinks() {
  const sel = document.querySelector('#linkArc');
  if (!sel) return;
  if (sel.dataset.wired) {
    // Follow the loaded level on re-entry unless the user picked one here.
    if (!sel.dataset.userPick && A.level
        && sel.value !== A.level.archive
        && [...sel.options].some((o) => o.value === A.level.archive)) {
      sel.value = A.level.archive;
      sel.dataset.rerender = '1';
      sel.dispatchEvent(new Event('change'));
    }
    return;
  }
  try {
    const arcs = await A.api('archives');
    for (const a of arcs.archives || arcs || []) {
      const name = a.archive || a.name || a;
      if (!/^LVLS_/.test(name)) continue;
      const o = el('option', null, name);
      o.value = name;
      sel.append(o);
    }
  } catch {
    // Unwired on failure so the next visit retries -- the flag used to be set
    // before the await, which left a dead picker for the whole session.
    return;
  }
  sel.dataset.wired = '1';
  if (A.level && [...sel.options].some((o) => o.value === A.level.archive))
    sel.value = A.level.archive;

  const tableBtn = document.querySelector('#linkViewTable');
  const graphBtn = document.querySelector('#linkViewGraph');
  const setView = (which) => {
    tableBtn.classList.toggle('on', which === 'table');
    graphBtn.classList.toggle('on', which === 'graph');
    document.querySelector('#linkBody').style.display = which === 'table' ? '' : 'none';
    document.querySelector('#linkGraph').style.display = which === 'graph' ? '' : 'none';
    if (which === 'graph') renderGraph();
  };
  tableBtn.onclick = () => setView('table');
  graphBtn.onclick = () => setView('graph');

  sel.onchange = () => {
    if (sel.dataset.rerender) delete sel.dataset.rerender;
    else sel.dataset.userPick = '1';
    load();
  };
  load();
}

async function load() {
  const arc = document.querySelector('#linkArc').value;
  const list = document.querySelector('#linkList');
  const body = document.querySelector('#linkBody');
  list.innerHTML = '';
  const stop = waiting(body, 'Reading the doors…');
  const r = await call('links?archive=' + encodeURIComponent(arc), 'GET');
  stop();
  if (!r.ok) return renderProblem(body, r.problem);
  linksData = r;
  document.querySelector('#linkCount').textContent = r.doors.length;
  if (!r.doors.length) {
    // Only 10 .sub files exist across 61 level archives, so 51 of 61 levels
    // land here. Without saying that, an empty panel reads as broken.
    body.innerHTML = '';
    const box = el('div', 'infbox');
    box.append(el('b', null, 'This level has no doors of its own.'));
    box.append(el('div', null, 'Ten archives carry door files. Pick one of those.'));
    body.append(box);
    return;
  }
  for (const d of r.doors) {
    const row = el('div', 'row');
    row.append(el('span', 'nm', d.display || d.level));
    row.append(el('span', 'tag', `${d.headroom} B spare`));
    row.onclick = () => showDoor(d);
    list.append(row);
  }
  showDoor(r.doors[0]);
}

function showDoor(d) {
  const body = document.querySelector('#linkBody');
  body.innerHTML = '';
  const head = el('div');
  head.style.cssText = 'display:flex;align-items:center;gap:8px';
  head.append(el('b', null, `Door to ${d.display || d.level}`), rungBadge(d.rung || 'measured'));
  body.append(head);
  body.append(el('div', 'muted', linksData.file || ''));

  // The values above are measured; the WRITE is not. No changed door has
  // ever been booted, and the panel has to say that before the Apply
  // button, not after a broken save.
  const wr = el('div', 'infbox');
  const wh = el('div');
  wh.append(el('b', null, 'Edits here are slot-checked and undoable — and none '
    + 'has ever been booted. '), rungBadge('untested'));
  wr.append(wh, el('div', null,
    'Change a door, load the level in PCSX2 and walk through it before '
    + 'trusting it. Ctrl+Z takes the edit back.'));
  wr.style.cssText = 'font-size:11px;margin:6px 0';
  body.append(wr);

  // Headroom, shown BEFORE anyone types rather than after they are refused.
  // Measured across every door file on the disc: 17 to 35 spare bytes.
  const room = el('div', 'muted', `${d.headroom} bytes spare in this file.`);
  room.style.cssText = 'font-size:11px;margin:6px 0';
  body.append(room);

  const changes = {};
  const rows = el('div');
  rows.style.cssText = 'display:flex;flex-direction:column;gap:5px;margin-top:6px';

  const field = (label, node) => {
    const r = el('div', 'field');
    r.style.cssText = 'display:flex;align-items:center;gap:8px';
    const l = el('label', null, label);
    l.style.cssText = 'width:150px;flex:none;font-size:12px';
    r.append(l, node);
    rows.append(r);
    return r;
  };

  // Changing where a door goes is legal only to a level that exists, so it is
  // a list built from the manifests and it refuses rather than truncating.
  const dest = document.createElement('select');
  dest.style.cssText = 'flex:1;min-width:0;font-size:12px';
  for (const t of linksData.destinations || []) {
    const o = el('option', null, `${t.display} (${t.level})`);
    o.value = t.level;
    if (t.level === d.level) o.selected = true;
    dest.append(o);
  }
  dest.onchange = () => { changes.level = dest.value; commit.replan(); };
  field('Goes to', dest);

  // Only the two values the disc actually uses are offered: measured across
  // all 36 doors, Lock is -1 on 33 and 0 on 3, and nothing else. A third
  // option ("key 1") used to sit here with a value no file carries and no
  // boot test behind it -- an invented setting dressed as a real one.
  const lock = document.createElement('select');
  lock.style.cssText = 'flex:1;font-size:12px';
  lock.title = 'Only these two values appear on any door on the disc.';
  const lockOpts = [[-1, 'unlocked'], [0, 'locked']];
  if (![-1, 0].includes(Number(d.lock)))
    lockOpts.push([Number(d.lock), `${d.lock} — as the file has it`]);
  for (const [v, lab] of lockOpts) {
    const o = el('option', null, lab);
    o.value = String(v);
    if (Number(d.lock) === v) o.selected = true;
    lock.append(o);
  }
  lock.onchange = () => { changes.lock = parseInt(lock.value, 10); commit.replan(); };
  field('Locked', lock);

  const vec = (label, get, set) => {
    const w = el('div', 'xyz');
    w.style.flex = '1';
    const v = get();
    ['x', 'y', 'z'].forEach((ax, i) => {
      const inp = document.createElement('input');
      inp.type = 'number';
      inp.step = '0.1';
      inp.value = (+v[i]).toFixed(3);
      inp.onkeydown = (e) => e.stopPropagation();
      inp.onchange = () => {
        const cur = get().slice();
        cur[i] = parseFloat(inp.value) || 0;
        set(cur);
        commit.replan();
      };
      w.append(inp);
    });
    field(label, w);
  };
  let enterPos = (d.enter?.pos || [0, 0, 0]).slice();
  let leavePos = (d.leave?.pos || [0, 0, 0]).slice();
  vec('Where you come in', () => enterPos,
      (v) => { enterPos = v; changes.enter = { ...(changes.enter || {}), pos: v }; });
  vec('Where you come out', () => leavePos,
      (v) => { leavePos = v; changes.leave = { ...(changes.leave || {}), pos: v }; });

  const head2 = document.createElement('input');
  head2.type = 'number';
  head2.step = '1';
  head2.value = (+(d.leave?.heading ?? 0)).toFixed(2);
  head2.style.cssText = 'flex:1;font-size:12px';
  head2.onkeydown = (e) => e.stopPropagation();
  head2.onchange = () => {
    changes.leave = { ...(changes.leave || {}), heading: parseFloat(head2.value) || 0 };
    commit.replan();
  };
  field('Facing when you arrive', head2);
  body.append(rows);

  const facts = el('table');
  facts.style.cssText = 'margin-top:8px;font-size:11px';
  for (const [k, v] of [['Door model', d.model], ['Opening animation', d.anim],
                        ['Door type', d.door_type], ['Level starts here', d.start_level ? 'yes' : 'no']]) {
    const tr = el('tr');
    tr.append(el('td', 'muted', k), el('td', null, String(v)));
    facts.append(tr);
  }
  body.append(facts);

  const planHost = el('div');
  planHost.style.cssText = 'margin-top:8px;font-size:12px';
  const btn = el('button', 'primary', 'Apply');
  btn.disabled = true;
  btn.title = 'Change something first.';
  const acts = el('div');
  acts.style.cssText = 'margin-top:6px';
  acts.append(btn);
  body.append(planHost, acts);

  const payload = (dry) => ({ archive: linksData.archive, file: linksData.file,
                              id: d.id, changes, dry });
  let timer = null;
  const commit = {
    replan() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (!Object.keys(changes).length) return;
        const stop = waiting(planHost, 'Checking it fits…');
        const r = await call('link_set', 'POST', payload(true));
        stop();
        if (r.ok && r.plan) {
          renderPlan(planHost, r.plan);
          btn.disabled = r.plan.fits === false;
          btn.title = r.plan.fits === false ? 'It does not fit its slot.' : '';
        } else {
          renderProblem(planHost, r.problem
            || { code: 'server_fault', message: 'Refused.', fix: '', detail: '' });
          btn.disabled = true;
        }
      }, 140);
    },
  };
  btn.onclick = async () => {
    btn.disabled = true;
    const r = await call('link_set', 'POST', payload(false));
    if (!r.ok) return renderProblem(planHost, r.problem);
    A.toast(r.plan?.undo?.label || 'Applied.', 'good');
    A.refreshHistory();
    load();
  };
}

// The graph and the table read the same route. Nodes are levels, edges are
// doors, laid out on a circle -- enough to see what joins to what without
// pulling in a layout library the CSP would block anyway.
function renderGraph() {
  const host = document.querySelector('#linkGraph');
  host.innerHTML = '';
  if (!linksData) return;
  const levels = new Map();
  const here = (linksData.archive || '').split('/').pop().replace(/\.DAT$/i, '');
  levels.set(here, { label: here, self: true });
  for (const d of linksData.doors) levels.set(d.level, { label: d.display || d.level });
  const ids = [...levels.keys()];
  const R = 150, cx = 200, cy = 180;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 400 360');
  svg.style.cssText = 'width:100%;max-width:520px;height:auto';
  const at = (i) => {
    if (ids[i] === here) return [cx, cy];
    const k = ids.filter((x) => x !== here).indexOf(ids[i]);
    const m = Math.max(1, ids.length - 1);
    const a = (k / m) * Math.PI * 2 - Math.PI / 2;
    return [cx + R * Math.cos(a), cy + R * Math.sin(a)];
  };
  ids.forEach((id, i) => {
    if (id === here) return;
    const [x, y] = at(i);
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('x1', cx); line.setAttribute('y1', cy);
    line.setAttribute('x2', x); line.setAttribute('y2', y);
    line.setAttribute('stroke', 'var(--line)');
    line.setAttribute('stroke-width', '1.5');
    svg.append(line);
  });
  ids.forEach((id, i) => {
    const [x, y] = at(i);
    const c = document.createElementNS(ns, 'circle');
    c.setAttribute('cx', x); c.setAttribute('cy', y);
    c.setAttribute('r', id === here ? 9 : 6);
    c.setAttribute('fill', id === here ? 'var(--gold)' : 'var(--bg2c)');
    c.setAttribute('stroke', 'var(--line)');
    svg.append(c);
    const t = document.createElementNS(ns, 'text');
    t.setAttribute('x', x); t.setAttribute('y', y - 12);
    t.setAttribute('text-anchor', 'middle');
    t.setAttribute('font-size', '10');
    t.setAttribute('fill', 'currentColor');
    t.textContent = levels.get(id).label;
    svg.append(t);
  });
  const h = el('div');
  h.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:8px';
  h.append(el('b', null, `${linksData.doors.length} doors out of ${here}`),
           rungBadge('measured'));
  host.append(h, svg);
}

// ------------------------------------------------------- LOGIC ▸ Scripting
//
// A browser, not an editor, and honest about why: the executable names every
// condition a script can test and NONE of the actions it can take, so a
// script reads as names, floats and bare numbers. The three panels the HTML
// has carried all along -- archive picker, filter, list -- are wired now;
// they used to sit on the page with nothing behind them.

let scrListedFor = null;

// DESIGN-REVIEW A2/A4: a banner in bold, a code block and two more
// paragraphs became one line; the evidence moved to its tooltip.
function scriptIntro() {
  const body = document.querySelector('#scrBody');
  body.innerHTML = '';
  const lo = vocab?.action_min ?? 1001, hi = vocab?.action_max ?? 1571;
  const line = el('div', 'muted', `Read-only. Action opcodes ${lo}–${hi} are not named yet.`);
  line.style.fontSize = '12px';
  line.title = (vocab
    ? `The executable names every one of the ${vocab.count} conditions a script `
      + 'can test, and none of the actions it can take. '
      + (vocab.action_note || '')
    : '')
    + (document.querySelector('#tab-states')
      ? ' The float parameters inside a behaviour state can be retuned in '
        + 'LOGIC ▸ Cast ▸ Behaviour tables.' : '');
  body.append(line);
  const empty = el('div', 'empty', 'Pick a script');
  empty.style.height = 'auto';
  empty.style.minHeight = '200px';
  body.append(empty);
}

async function initScripting() {
  const sel = document.querySelector('#scrArc');
  if (!sel) return;
  if (sel.dataset.wired) {
    if (!sel.dataset.userPick && A.level
        && sel.value !== A.level.archive
        && [...sel.options].some((o) => o.value === A.level.archive)) {
      sel.value = A.level.archive;
      loadScripts(true);
      return;
    }
    loadScripts(false);
    return;
  }
  if (!vocab) { try { vocab = await A.api('aivocab'); } catch {} }
  scriptIntro();
  // /api/archives is the only route that lists archives and it opens all 66
  // containers -- measured near a minute cold elsewhere in this app. Say
  // something while it runs, or the panel is two empty boxes.
  const list = document.querySelector('#scrList');
  const stop = waiting(list, 'Reading the disc…');
  try {
    const arcs = await A.api('archives');
    for (const a of arcs.archives || arcs || []) {
      const name = a.archive || a.name || a;
      // Scripts live in the level archives and in SWAT.DAT (8,608 .actb
      // there alone); the media and front-end archives hold none.
      if (!/^LVLS_/.test(name) && name !== 'SWAT.DAT') continue;
      const o = el('option', null, name);
      o.value = name;
      sel.append(o);
    }
    if (A.level && [...sel.options].some((o) => o.value === A.level.archive))
      sel.value = A.level.archive;
    // With no level loaded, open on an archive that HAS scripts (P4b): the
    // first level archive in the list holds none. SWAT.DAT holds 8,608.
    else if ([...sel.options].some((o) => o.value === 'SWAT.DAT'))
      sel.value = 'SWAT.DAT';
    sel.dataset.wired = '1';
  } catch {
    A.toast('Could not read the archive list.', 'bad');
  } finally {
    stop();
  }
  sel.onchange = () => { sel.dataset.userPick = '1'; loadScripts(true); };
  {
    let t = null;   // debounced -- same reason as every other disc-read filter
    document.querySelector('#scrFilter').oninput = () => {
      clearTimeout(t);
      t = setTimeout(() => loadScripts(false), 180);
    };
  }
  loadScripts(true);
}

async function loadScripts(force) {
  const sel = document.querySelector('#scrArc');
  const host = document.querySelector('#scrList');
  const arc = sel.value;
  if (!arc) {
    host.innerHTML = '';
    host.append(el('p', 'muted', sel.options.length
      ? 'Choose an archive.'
      : 'Could not read the archive list. Leave this panel and come back '
        + 'to retry.'));
    return;
  }
  const filter = (document.querySelector('#scrFilter').value || '')
    .trim().toLowerCase();
  const key = arc + '|' + filter;
  if (!force && scrListedFor === key) return;
  scrListedFor = key;
  const stop = waiting(host, 'Reading the archive…');
  // Two exact-extension asks, because `q` is a substring and cannot say
  // ".actb and .acttbl only". SWAT.DAT holds 9,144 of the two together, so
  // the cap plus the filter box is how the list stays usable.
  const qs = filter ? '&q=' + encodeURIComponent(filter) : '';
  const [a, b] = await Promise.all([
    call('toc?archive=' + encodeURIComponent(arc) + '&ext=actb&limit=400' + qs, 'GET'),
    call('toc?archive=' + encodeURIComponent(arc) + '&ext=acttbl&limit=200' + qs, 'GET'),
  ]);
  stop();
  host.innerHTML = '';
  if (a.problem || b.problem)
    return renderProblem(host, a.problem || b.problem);
  const rows = [...(a.rows || []), ...(b.rows || [])]
    .sort((x, y) => (x.name < y.name ? -1 : 1));
  const total = (a.count || 0) + (b.count || 0);
  const hint = el('p', 'muted', total
    ? `${total.toLocaleString()} scripts in this archive`
      + (rows.length < total
        ? ` — showing ${rows.length}. Type in the filter box to narrow it.`
        : '')
    : (filter ? 'Nothing in this archive matches that.'
              : 'This archive holds no mission scripts.'));
  hint.style.cssText = 'font-size:11px;padding:4px 8px';
  host.append(hint);
  for (const r of rows) {
    const row = el('div', 'row');
    row.append(el('span', 'nm', r.name));
    row.title = `${r.size.toLocaleString()} bytes · `
      + `${r.stored.toLocaleString()} of ${r.slot.toLocaleString()} stored`;
    row.onclick = () => openScript(arc, r.name);
    host.append(row);
  }
}

async function openScript(archive, name) {
  const body = document.querySelector('#scrBody');
  const stop = waiting(body, 'Reading the script…');
  const f = await call('file?archive=' + encodeURIComponent(archive)
    + '&name=' + encodeURIComponent(name), 'GET');
  stop();
  if (f.problem) return renderProblem(body, f.problem);
  body.innerHTML = '';

  const head = el('div');
  head.style.cssText = 'display:flex;align-items:center;gap:8px';
  head.append(el('b', null, name), rungBadge('inferred'));
  const ex = el('button', '', 'Export…');
  ex.style.cssText = 'font-size:12px;margin-left:auto';
  ex.title = 'Save a copy of this file as it is on the disc right now.';
  // Through saveFile, never a blind <a download>: the route answers a
  // missing or unreadable file with JSON, and a blind click saves that JSON
  // under the script's name (the note above saveFile in app.js records the
  // time it happened).
  ex.onclick = () => A.saveFile('/api/file.bin?archive='
    + encodeURIComponent(archive) + '&name=' + encodeURIComponent(name),
    name.replace(/\//g, '_'));
  head.append(ex);
  body.append(head);
  body.append(el('div', 'muted', `${(f.size || 0).toLocaleString()} bytes · `
    + `${(f.stored || 0).toLocaleString()} of `
    + `${(f.slot || 0).toLocaleString()} stored bytes in its slot`));

  if (f.kind === 'text') {
    // A few script-family files are plain text; showing their own bytes
    // beats declaring them unreadable.
    const pre = el('pre', null, f.text || '');
    pre.style.cssText = 'font-size:11px;line-height:1.5;margin-top:10px;'
      + 'white-space:pre;overflow:auto';
    body.append(pre);
    return;
  }
  if (f.kind !== 'behaviour' || !f.summary) {
    const box = el('div', 'infbox');
    box.append(el('b', null, 'This file does not read as a script.'));
    box.append(el('div', null, 'It is missing the token-stream header every '
      + '.actb on the disc carries, so there is nothing to list.'));
    body.append(box);
    return;
  }

  const s = f.summary;
  const box = el('div', 'infbox');
  box.append(el('b', null,
    'The conditions have names. The actions are bare numbers.'));
  box.append(el('div', null, 'Read from the file. Action opcodes have no names.'));
  const bits = [
    `${s.conditions || 0} named condition use${s.conditions === 1 ? '' : 's'}`,
    `${(s.action_opcodes || []).length} distinct action opcodes`,
    `${s.floats || 0} floats`,
    `${s.ints || 0} whole numbers`,
  ];
  if (s.unresolved_text)
    bits.push(`${s.unresolved_text} words that are probably text`);
  box.append(el('div', 'ev', bits.join(' · ')));
  body.append(box);

  // The server's listing is the honest render: offset, kind, meaning, one
  // line per token, truncated at 4,000 tokens and saying so when it is.
  const pre = el('pre', null, f.listing || '');
  pre.style.cssText = 'font-size:11px;line-height:1.5;margin-top:10px;'
    + 'white-space:pre;overflow:auto';
  body.append(pre);
}

// ----------------------------------------------------------------- wiring

export function init(bridge) {
  A = bridge;
  onPanel((panel) => {
    if (panel === 'behaviour') initBehaviour();
    if (panel === 'links') initLinks();
    if (panel === 'scripting') initScripting();
  });
}
