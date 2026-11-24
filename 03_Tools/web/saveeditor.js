// saveeditor.js — HIS MEMORY CARD, NOT THE DISC.
//
// Every other tab in this Studio edits the game. This one edits the player's own
// save, which is the only file the Studio touches that cannot be rebuilt from
// the ISO. So the panel is arranged around what is PROVEN rather than what is
// convenient, and it says the unwelcome part in the banner instead of burying
// it in a tooltip:
//
//   * money is decoded (file +0x68, read off the shop in a live boot) and an
//     edited card was booted and accepted -- that one field, once, on a copy;
//     most of the other numbers are still unnamed, and are labelled as such;
//   * writing is refused unless PCSX2 is provably closed.
//
// The two columns are the honest split. "Decoded" holds the two fields where
// something outside the save file agrees — the level id is one of the game's
// own `lvl_*` identifiers, the profile name is one of the six defaults the
// executable lists. "Unverified" holds everything whose TYPE is certain and
// whose MEANING is not. They are editable either way, because a byte you can
// write and read back is how the second column becomes the first, but they
// are never mixed: a guess rendered beside a fact reads as a fact.

import { call, onPanel, el } from './transfer.js';

let cards = [];
let openPath = null;
let save = null;
let meta = null;
let slotIx = 0;
let loaded = false;

const $ = (s) => document.querySelector(s);

// DESIGN-REVIEW A2: this was a 110-word "Read this once" paragraph in a
// warning box. The facts it carried are the three below; the evidence is in
// 05_Docs/SAVE-EDITOR.md.
function banner() {
  const host = $('#saveBanner');
  if (!host) return;
  host.innerHTML = '';
  const b = el('div', 'muted',
    'Money is verified in-game. Other fields are unverified. '
    + 'A backup is made before every write.');
  b.title = 'An edited card (money 4242) was booted and the shop showed $4242. '
    + 'Backups go to 06_Backups/saves/.';
  host.appendChild(b);
}

function pcsx2Line(state, kind) {
  const host = $('#savePcsx2');
  if (!host) return;
  host.innerHTML = '';
  const d = el('div');
  d.style.fontSize = '12px';
  if (kind === 'xbox') {
    // an Xbox save (swat.sav in the recomp's hdd folder): the Xbox game must be closed instead (2026-11-24)
    d.style.color = state === false ? 'var(--grn)' : 'var(--warn)';
    d.textContent = state === false ? 'Xbox save: the Xbox game (RDR.exe) is not running — writing is allowed.'
      : state === true ? 'Xbox save: the Xbox game (RDR.exe) is running. Quit it before writing.'
      : 'Xbox save: cannot tell whether the Xbox game is running, so writing is refused.';
    host.appendChild(d);
    return;
  }
  if (state === false) {
    d.style.color = 'var(--grn)';
    d.textContent = 'PCSX2 is not running — writing is allowed.';
  } else if (state === true) {
    d.style.color = 'var(--warn)';
    d.textContent = 'PCSX2 is running. Close it before writing.';
  } else {
    d.style.color = 'var(--warn)';
    d.textContent = 'Cannot tell whether PCSX2 is running from here, so '
      + 'writing is refused. (A Studio started from WSL cannot see Windows '
      + 'processes; run it from Windows to write.)';
  }
  host.appendChild(d);
}

// DESIGN-REVIEW B7: readable cards only. The rest -- files in the memcards
// folder that are not PS2 card images -- fold into one row instead of each
// printing a raw CardError over three lines.
let othersOpen = false;
function cardRow(c) {
  const row = el('div', 'row' + (c.path === openPath ? ' sel' : ''));
  row.style.cssText = 'flex-direction:column;align-items:stretch;gap:0;padding:4px 10px'
    + (c.rdr ? '' : ';cursor:default;opacity:.6');
  row.append(el('span', 'nm', c.name));
  const p = el('span', 'muted', c.path);
  p.style.cssText = 'font:var(--t-mono);font-size:11px;overflow:hidden;'
    + 'text-overflow:ellipsis;white-space:nowrap';
  row.append(p);
  row.title = c.rdr ? c.path : c.path + '\n' + (c.note || 'no RDR save');
  if (c.rdr) row.onclick = () => openCard(c.path);
  return row;
}
function renderCards() {
  const host = $('#saveCards');
  host.innerHTML = '';
  const good = cards.filter((c) => c.rdr);
  const rest = cards.filter((c) => !c.rdr);
  for (const c of good) host.appendChild(cardRow(c));
  if (!good.length) host.appendChild(el('div', 'empty', 'No RDR save found'));
  if (rest.length) {
    const notCards = rest.every((c) => /^CardError/.test(c.note || ''));
    const hd = el('div', 'row');
    hd.style.cssText = 'color:var(--dim-ink);font-size:12px;margin-top:4px';
    hd.textContent = `${rest.length} file${rest.length === 1 ? '' : 's'} `
      + (notCards ? `${rest.length === 1 ? 'is' : 'are'} not PS2 cards`
                  : 'without an RDR save') + (othersOpen ? ' ▾' : ' ▸');
    hd.onclick = () => { othersOpen = !othersOpen; renderCards(); };
    host.appendChild(hd);
    if (othersOpen) for (const c of rest) host.appendChild(cardRow(c));
  }
}

function renderSlots() {
  const host = $('#saveSlots');
  host.innerHTML = '';
  if (!save) return;
  for (const s of save.slots) {
    const row = el('div', 'row' + (s.index === slotIx ? ' sel' : ''));
    const nm = el('span', 'nm', 'Slot ' + s.index + '  '
      + (s.used ? (s.fields.name || '(unnamed)') : 'empty'));
    if (!s.used) nm.style.color = 'var(--dim-ink)';
    row.append(nm, el('span', 'tag', s.used ? (s.fields.level || '') : ''));
    row.onclick = () => { slotIx = s.index; renderSlots(); renderFields(); };
    host.appendChild(row);
  }
}

// DESIGN-REVIEW A9: an editor grid, not a web form -- name | value | type |
// offset, 24px rows, max 720px. A 1,110px input used to hold "677", and a
// label like "float +0xcc" repeated the offset printed beside it.
const KIND = { u32: 'u32', f32: 'float', str8: 'text', u8: 'byte', u16: 'u16' };
function fieldRow(f, value) {
  const wrap = el('div', 'fgrid');
  const unnamed = /^(float|u32|u16|u8|byte|int|str\w*) \+0x/i.test(f.label || '');
  const lab = el('label', unnamed ? 'muted' : '', unnamed ? 'unnamed' : f.label);
  lab.title = f.why;
  const inp = el('input');
  inp.value = (value === undefined || value === null) ? '' : String(value);
  inp.dataset.key = f.key;
  inp.dataset.kind = f.kind;
  if (f.kind === 'str8') inp.classList.add('txt');
  const kind = el('span', 'muted', KIND[f.kind] || f.kind);
  const off = el('span', 'muted', '+0x' + f.offset.toString(16));
  wrap.append(lab, inp, kind, off);
  return wrap;
}

function renderFields() {
  const dec = $('#saveDecoded'), cand = $('#saveCandidates'),
        file = $('#saveFileFields');
  dec.innerHTML = ''; cand.innerHTML = ''; file.innerHTML = '';
  if (!save || !meta) return;
  for (const f of (meta.file_fields || []))
    file.appendChild(fieldRow(f, (save.fields || {})[f.key]));
  const slot = save.slots[slotIx];
  for (const f of meta.fields) {
    const row = fieldRow(f, slot.fields[f.key]);
    (f.confidence === 'decoded' ? dec : cand).appendChild(row);
  }
}

function collectFile() {
  const out = {};
  for (const inp of document.querySelectorAll('#saveFileFields input')) {
    const k = inp.dataset.key;
    let v = inp.value;
    if (inp.dataset.kind === 'u32') { v = parseInt(v, 10); if (!Number.isFinite(v)) continue; }
    else if (inp.dataset.kind === 'f32') { v = parseFloat(v); if (!Number.isFinite(v)) continue; }
    if (String(v) !== String((save.fields || {})[k])) out[k] = v;
  }
  return out;
}

function collect() {
  const out = {};
  for (const inp of document.querySelectorAll('#saveDecoded input, #saveCandidates input')) {
    const k = inp.dataset.key, kind = inp.dataset.kind;
    const was = save.slots[slotIx].fields[k];
    let v = inp.value;
    if (kind === 'u32') { v = parseInt(v, 10); if (!Number.isFinite(v)) continue; }
    else if (kind === 'f32') { v = parseFloat(v); if (!Number.isFinite(v)) continue; }
    // Only what actually changed, so a write names exactly what it did.
    if (String(v) !== String(was)) out[k] = v;
  }
  return out;
}

function say(msg, tone) {
  const host = $('#saveStatus');
  host.textContent = msg;
  host.style.color = tone || 'inherit';
}

async function openCard(path) {
  say('opening…');
  const r = await call('save/open?path=' + encodeURIComponent(path));
  if (r.error) { say(r.error, 'var(--warn)'); return; }
  openPath = path; save = r; slotIx = 0;
  pcsx2Line(r.pcsx2, r.kind);
  if (r.kind === 'xbox' && r.signed === false) {
    // a save the game would skip (an edit written unsigned): writing it here signs it again
    const w = el('div', '', 'This Xbox save is not signed as the game expects, so the game shows no save. Write any change here and it is signed again.');
    w.style.cssText = 'font-size:12px;color:var(--warn);margin-top:4px';
    $('#savePcsx2').appendChild(w);
  }
  renderCards(); renderSlots(); renderFields();
  say('opened ' + path);
}

async function write() {
  if (!openPath) { say('open a card first', 'var(--warn)'); return; }
  const fields = collect();
  const fileFields = collectFile();
  const total = Object.keys(fields).length + Object.keys(fileFields).length;
  if (!total) { say('nothing changed', 'var(--warn)'); return; }
  const names = Object.keys(fields).concat(Object.keys(fileFields)).join(', ');
  if (!confirm('Write ' + names + ' to\n' + openPath
               + '\n\n(slot fields go to slot ' + slotIx + '; money and the '
               + 'rest of "This save" are not per-profile)'
               + '\n\nA full copy of the ' + (save && save.kind === 'xbox' ? 'Xbox save' : 'card') + ' goes to 06_Backups/saves/ first.'))
    return;
  say('writing…');
  const r = await call('save/write', 'POST',
                       { path: openPath, slot: slotIx, fields,
                         file_fields: fileFields });
  if (r.error) { say(r.error, 'var(--warn)'); return; }
  say('written. Backup: ' + r.backup + ' — ' + r.warning, 'var(--grn)');
  await openCard(openPath);
}

async function load() {
  if (loaded) return;
  loaded = true;
  banner();
  const [cs, fs] = await Promise.all([call('save/cards'), call('save/fields')]);
  if (cs.error || fs.error) {
    say((cs.error || fs.error), 'var(--warn)');
    return;
  }
  cards = cs.cards || []; meta = fs;
  pcsx2Line(cs.pcsx2);
  renderCards();
  $('#saveWrite').onclick = write;
  $('#saveRefresh').onclick = () => { loaded = false; save = null; load(); };
  const auto = cards.find((c) => c.rdr);
  if (auto) openCard(auto.path);
}

onPanel((panel) => { if (panel === 'saves') load(); });
