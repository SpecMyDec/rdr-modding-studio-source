// versions.js — ONE STORY OVER CHECKPOINTS AND MODS.
//
// A checkpoint and a mod are the same file format wearing two coats: the same
// writer produces both and the same apply path restores both. Yet one was
// blue with no confirmation and one undo step PER FILE, and the other was red
// with a confirmation and one step total. There is one thing now, and it is a
// Version.
//
// The banner is the anxious part, so it is the measured part: it is computed
// every poll and never cached, because "what is actually on the disc right
// now" is the question work has been lost to twice.

import { call, notBuilt, renderProblem, renderPlan, openSheet, waiting,
         rungBadge, onPanel, el } from './transfer.js';

let A = null;
let data = null;
let compare = [];

function banner(on_disc) {
  const host = document.querySelector('#verBanner');
  host.innerHTML = '';
  const d = on_disc || {};
  let text, tone;
  if (d.id && d.matches) {
    tone = 'var(--grn)';
    text = `Disc: “${d.name}”, exact match`;
  } else if (d.id) {
    tone = 'var(--warn)';
    text = `Disc: “${d.name}” + ${d.files_changed} unsaved `
         + `change${d.files_changed === 1 ? '' : 's'}`;
  } else if (d.files_changed) {
    tone = 'var(--warn)';
    text = `Disc: ${d.files_changed} file${d.files_changed === 1 ? '' : 's'} `
      + 'differ from stock, not saved to any version';
  } else {
    tone = 'var(--grn)';
    text = 'Disc: stock, unchanged';
  }
  const line = el('div');
  const dot = el('span', 'sdot');
  dot.style.color = tone;
  line.append(dot, document.createTextNode(text));
  host.append(line);
  if (d.drifted?.length) {
    const list = el('div', 'muted',
      d.drifted.map((p) => (Array.isArray(p) ? p[1] : p)).join(' · '));
    list.style.cssText = 'font-size:11px;margin-top:4px';
    host.append(list);
  }
}

// Restore points the tools made on their own -- the suite's
// auto_before_roundtrip, the journal test, the session autosave. Display
// only: they restore exactly like the rest.
const isAuto = (v) => v.autosave || /^auto[_: ]/i.test(v.name || '')
  || v.notes === 'restore point' || v.notes === 'automated';

let autoOpen = false;

function row(v) {
  const tr = el('tr');
  const mark = el('td');
  mark.style.width = '18px';
  if (v.on_disc) {
    const dot = el('span', 'sdot');
    dot.style.color = 'var(--grn)';
    dot.title = 'This is what the working disc holds now.';
    mark.append(dot);
  }
  const name = el('td');
  name.append(el('span', null, v.name));
  if (v.notes) {
    const n = el('span', 'muted', '  ' + v.notes);
    n.style.fontSize = '11px';
    name.append(n);
  }
  const when = el('td', 'muted num', v.created);
  when.style.fontSize = '11px';
  const files = el('td', 'muted num',
    `${v.files} file${v.files === 1 ? '' : 's'}`);
  files.style.fontSize = '11px';

  // Neutral, and shown on hover or focus (DESIGN-REVIEW A7). The red lives
  // in the confirmation sheet, which lists every file that would change.
  const acts = el('td', 'acts');
  acts.style.textAlign = 'right';
  const cmp = el('button', '', 'Compare');
  cmp.onclick = () => pickCompare(v);
  acts.append(cmp);
  if (!v.autosave) {
    const back = el('button', '', 'Restore');
    back.onclick = () => restore(v);
    const del = el('button', '', 'Delete');
    del.onclick = () => remove(v);
    acts.append(back, del);
  }
  tr.append(mark, name, when, files, acts);
  return tr;
}

function rows(versions) {
  const host = document.querySelector('#verList');
  host.innerHTML = '';
  if (!versions.length) {
    host.append(emptyLine('No saved versions yet. "Save as new version" keeps a named copy of your mod you can come back to.'));
    return;
  }
  const t = el('table', 'vers');
  t.style.width = '100%';
  const mine = versions.filter((v) => !isAuto(v));
  const auto = versions.filter(isAuto);
  if (!mine.length) {
    const e = emptyLine('No saved versions yet. "Save as new version" keeps a named copy of your mod you can come back to.');
    e.style.cssText = 'min-height:64px;flex:none;height:auto;padding:22px 0';
    host.append(e);
  }
  for (const v of mine) t.append(row(v));
  if (auto.length) {
    const hd = el('tr', 'tgrouphd' + (autoOpen ? ' open' : ''));
    const td = el('td');
    td.colSpan = 5;
    td.append(el('span', 'chev', '▸'),
      document.createTextNode(`${auto.length} automatic restore point`
        + `${auto.length === 1 ? '' : 's'}`));
    hd.append(td);
    hd.onclick = () => { autoOpen = !autoOpen; rows(versions); };
    t.append(hd);
    if (autoOpen) for (const v of auto) t.append(row(v));
  }
  host.append(t);
}

function emptyLine(text) {
  return el('div', 'empty', text);
}

async function refresh() {
  const list = document.querySelector('#verList');
  const stop = waiting(list, 'Reading the disc…');
  const r = await call('versions', 'GET');
  stop();
  if (!r.ok) {
    document.querySelector('#verBanner').innerHTML = '';
    return renderProblem(list, r.problem);
  }
  data = r;
  banner(r.on_disc);
  rows(r.versions || []);
}

// ------------------------------------------------------------------ verbs

function save() {
  let name = '', notes = '';
  const sheet = openSheet({
    title: 'Save as new version',
    subject: {
      title: 'Everything on the working disc that differs from stock',
      where: data?.on_disc?.files_changed
        ? `${data.on_disc.files_changed} file(s) changed`
        : 'the working disc',
      format: 'A version file in Documents',
      rung: 'measured',
    },
    primary: 'Save version',
    renderOptions(host) {
      const n = document.createElement('input');
      n.placeholder = 'what this version is';
      n.style.cssText = 'width:100%';
      const t = document.createElement('textarea');
      t.placeholder = 'notes (optional)';
      t.style.cssText = 'width:100%;height:54px;margin-top:6px';
      host.append(n, t);
      const plan = async () => {
        name = n.value.trim(); notes = t.value;
        if (!name) return sheet.busy('Give it a name first.');
        sheet.busy('Checking…');
        const r = await call('version_save', 'POST', { name, notes, dry: true });
        if (!r.ok) return sheet.refuse(r.problem);
        sheet.plan(r.plan);
      };
      n.oninput = () => { clearTimeout(n._t); n._t = setTimeout(plan, 250); };
      t.oninput = n.oninput;
      setTimeout(() => n.focus(), 40);
    },
    startLabel: 'Give it a name first.',
    async onApply(api) {
      api.busy('Saving…');
      const r = await call('version_save', 'POST', { name, notes, dry: false });
      if (!r.ok) return api.refuse(r.problem);
      api.done('<b style="color:var(--grn)">Saved.</b>');
      refresh();
    },
  });
}

// The most important dry run in the product: it lists every file that would
// change, because this is the action that loses work.
function restore(v) {
  const sheet = openSheet({
    title: 'Restore a version',
    subject: {
      title: v.name,
      where: `${v.files} file(s) · saved ${v.created}`,
      format: 'Everything the disc holds that this version does not is replaced',
      rung: 'measured',
    },
    primary: 'Restore',
    destructive: true,
    startLabel: 'Working out every file that would change…',
    async onApply(api) {
      api.busy('Restoring…');
      const r = await call('version_restore', 'POST', { id: v.id, dry: false });
      if (!r.ok) return api.refuse(r.problem);
      api.done('<b style="color:var(--grn)">Restored.</b> '
        + `Undo is ${r.plan?.undo?.steps ?? 1} step.`);
      A.refreshHistory();
      refresh();
    },
    async onOpen(api) {
      const r = await call('version_restore', 'POST', { id: v.id, dry: true });
      if (!r.ok) return api.refuse(r.problem);
      api.plan(r.plan);
    },
  });
}

function remove(v) {
  openSheet({
    title: 'Delete a version',
    subject: { title: v.name, where: v.path || '', format: 'The working disc is not touched',
               rung: 'measured' },
    primary: 'Delete',
    destructive: true,
    async onOpen(api) {
      const r = await call('version_delete', 'POST', { id: v.id, dry: true });
      if (!r.ok) return api.refuse(r.problem);
      api.plan(r.plan);
    },
    async onApply(api) {
      const r = await call('version_delete', 'POST', { id: v.id, dry: false });
      if (!r.ok) return api.refuse(r.problem);
      api.done('<b>Deleted.</b>');
      refresh();
    },
  });
}

// ---------------------------------------------------------------- compare

function pickCompare(v) {
  compare = [v.id, compare[1] || 'stock'];
  renderCompare(true);
}

// DESIGN-REVIEW B1: opening the tab used to start a working-disc-versus-stock
// compare on its own, and the Studio answers one request at a time, so the
// whole app froze behind it. The panel now opens idle and compares on click.
async function renderCompare(run = false) {
  const host = document.querySelector('#verDiff');
  host.innerHTML = '';
  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap';
  const mkSel = (i) => {
    const s = document.createElement('select');
    s.style.cssText = 'font-size:11px;max-width:150px';
    for (const [val, lab] of [['disc', 'the working disc now'], ['stock', 'the stock disc']])
      { const o = el('option', null, lab); o.value = val; s.append(o); }
    for (const v of data?.versions || []) {
      const o = el('option', null, v.name);
      o.value = v.id;
      s.append(o);
    }
    s.value = compare[i] || (i === 0 ? 'disc' : 'stock');
    s.onchange = () => { compare[i] = s.value; renderCompare(); };
    if (!compare[i]) compare[i] = s.value;
    return s;
  };
  const go = el('button', '', 'Compare');
  go.onclick = () => renderCompare(true);
  bar.append(mkSel(0), el('span', 'muted', 'vs'), mkSel(1), go);
  host.append(bar);
  if (!run) return;

  const a = compare[0] || 'disc';
  const b = compare[1] || 'stock';
  const body = el('div');
  host.append(body);
  const stop = waiting(body, 'Comparing…');
  // Reading the working disc can hold the Studio for minutes cold; the
  // server checks this between archives (DESIGN-REVIEW B1).
  const cancel = el('button', '', 'Cancel');
  cancel.onclick = () => { cancel.disabled = true; call('job_cancel', 'POST', {}); };
  if (a === 'disc' || b === 'disc') body.append(cancel);
  const r = await call(`version_diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
                       'GET');
  stop();
  cancel.remove();
  if (!r.ok) return renderProblem(body, r.problem);
  if (r.cancelled) {
    body.append(el('p', 'muted', 'Compare cancelled.'));
    return;
  }
  const c = r.counts || {};
  const sum = el('div', 'muted',
    `${c.added || 0} added · ${c.removed || 0} removed · ${c.changed || 0} changed `
    + `· ${c.same || 0} the same`);
  sum.style.cssText = 'font-size:11px;margin-bottom:6px';
  body.append(sum);
  const t = el('table');
  t.style.width = '100%';
  let n = 0;
  for (const f of r.files || []) {
    if (f.state === 'same') continue;
    const tr = el('tr');
    tr.append(el('td', null, f.file),
              el('td', 'muted', f.archive),
              el('td', f.state === 'removed' ? 'bad' : '', f.state));
    t.append(tr);
    n++;
  }
  body.append(t);
  if (!n) body.append(el('p', 'muted', 'Nothing differs between those two.'));
}

// The same sheet from outside this tab: the Export dialog's "Mod file" and
// the Changes tab both save through here, so there is one way to make a mod.
export function saveVersion() {
  save();
}

export function init(bridge) {
  A = bridge;
  onPanel((panel) => {
    if (panel !== 'versions') return;
    if (document.querySelector('#verList').dataset.wired) return;
    document.querySelector('#verList').dataset.wired = '1';
    document.querySelector('#verSave').onclick = save;
    document.querySelector('#verRefresh').onclick = refresh;
    refresh();
    renderCompare();
  });
}
