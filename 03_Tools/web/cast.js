// cast.js — SWAP WHICH CHARACTER AN ENEMY IS, AND PUT A NEW ONE ANYWHERE.
//
// These are the two sentences the owner wrote. The data was never the obstacle:
// an enemy is a plain-text block and the two fields that decide what it is
// are `Type` (which creature) and `Table` (which behaviour). The tool parsed
// both and offered a bare text box for one and nothing at all for the other.
//
// TYPE AND TABLE ARE ORTHOGONAL. 190 behaviour tables are used with more than
// one character and 99 of 123 characters are driven by more than one table,
// so this shows two adjacent dropdowns and never one "character" field.
//
// Everything writes through the frozen contract when it is up. Until then the
// palette is still exact, because it is read off the archive's own table of
// contents -- the legal `Type` for an archive is precisely the set of `.cr`
// files inside it.

import { call, notBuilt, renderProblem, renderPlan, openSheet, closeSheet,
         rungBadge, waiting, onPanel, el } from './transfer.js';

let A = null;
const castCache = new Map();     // archive -> cast payload
let placing = null;              // {type, table, label} while placing a new enemy

const FIELDS = [
  ['Type', 'Character', 'pick', 'cast.characters', true],
  ['Table', 'Behaviour', 'pick', 'cast.behaviours', true],
  ['Name', 'Name', 'text', null, true],
  ['Comment', 'Note to yourself', 'text', null, true],
  ['Alignment', 'Side it fights for', 'int', null, true],
  ['Group', 'Wave it belongs to', 'text', null, true],
  ['PopFreq', 'Chance it appears', 'int', null, true],
  ['BossType', 'Boss rank', 'int', null, true],
  ['MyBoss', 'Follows this boss', 'text', null, true],
  ['Cullable', 'Despawns when far away', 'bool', null, true],
  ['Pushable', 'Can be shoved', 'bool', null, true],
  ['TextureVariation', 'Skin', 'pick', 'cast.skins', false],
  ['PowerUp', 'Drops on death', 'pick', 'cast.drops', false],
  ['WeaponList', 'Weapons carried', 'list', 'cast.drops', false],
  ['StartWeapon', 'Weapon in hand', 'int', null, false],
  ['ThrownWeaponAmmo', 'Thrown ammo', 'int', null, false],
  ['Inventory', 'Inventory', 'text', null, false],
  ['SpawnHorse', 'Horse it rides', 'text', null, false],
  ['IsBeast', 'Is an animal', 'bool', null, false],
  ['WhichHead', 'Head variant', 'int', null, false],
  ['NeedsSound', 'Loads its own voice', 'bool', null, false],
];

// -------------------------------------------------------------- the palette

// GET /api/cast when it exists. Until it does, the same answer is derivable
// from the archive's table of contents and is exactly as exact: the legal
// `Type` for archive A is {stem | stem.cr is in A}, and all 336 .cr across
// the 61 level archives resolve their model and skeleton inside their own
// archive. Measured here: SHER70's TOC holds exactly the twelve the design
// document names -- chk cls ggl hdh mon pld snp srd twg ugl wsk zzt -- and
// twenty .tbl. The Table values are read from this level's own .enm first, so
// they carry the exact casing the file uses, then topped up from the .tbl
// stems for behaviours nothing here happens to run yet.
async function getCast(archive, area) {
  const key = archive + '|' + (area || '');
  if (castCache.has(key)) return castCache.get(key);
  let out = await call('cast?archive=' + encodeURIComponent(archive)
    + (area ? '&area=' + encodeURIComponent(area) : ''), 'GET');
  if (!out.ok) out = await castFromToc(archive);
  castCache.set(key, out);
  return out;
}

// The table of contents is the fallback palette's source and it is not small
// -- 1,513 rows for SHER70 -- so read each archive once per session.
const tocCache = new Map();

async function castFromToc(archive) {
  let rows = [];
  try {
    if (!tocCache.has(archive)) {
      const r = await A.api('toc?archive=' + encodeURIComponent(archive) + '&limit=6000');
      tocCache.set(archive, r.rows || []);
    }
    rows = tocCache.get(archive);
  } catch {
    return { ok: false, problem: { code: 'not_found',
      message: 'Could not read that archive.', fix: '', detail: '' } };
  }
  const stem = (n) => n.slice(0, n.lastIndexOf('.'));
  const crs = rows.filter((f) => f.name.endsWith('.cr')).map((f) => stem(f.name));
  const tbls = rows.filter((f) => f.name.endsWith('.tbl')).map((f) => stem(f.name));
  const texs = rows.filter((f) => f.name.endsWith('.tex')).map((f) => stem(f.name));

  // Count what this level actually uses, and harvest the file's own casing.
  const used = new Map(), tblCase = new Map();
  for (const n of (A.level?.nodes || [])) {
    if (n.kind !== 'enemy' || !n.props) continue;
    if (A.level.archive !== archive) break;
    const t = String(n.props.Type || '').toLowerCase();
    used.set(t, (used.get(t) || 0) + 1);
    const b = n.props.Table;
    if (b) tblCase.set(String(b).toLowerCase(), b);
  }
  const characters = crs.map((id) => ({
    id, file: id + '.cr', label: id, render: id, skel: id, anim: null,
    has_model: true, has_skel: true, borrowed: false,
    used: used.get(id) || 0, rung: 'measured',
  }));
  const behaviours = tbls.map((s) => ({
    id: tblCase.get(s) || s, file: s + '.tbl', label: s, states: null,
    used: tblCase.has(s) ? 1 : 0, rung: 'measured',
  }));
  let roster_source = 'local';
  let roster_note = '';
  if (!rows.some((f) => f.name.endsWith('.enm'))) {
    roster_source = 'swat';
    // 26 of the 61 level archives are Showdown archives with no .enm of their
    // own. Their roster lives only in SWAT.DAT, so the Enemies layer is empty
    // here for a reason, and the panel has to give the reason.
    roster_note = 'This level has no enemy list of its own — its roster lives '
      + 'in SWAT.DAT, so enemies cannot be placed here yet.';
  }
  return {
    ok: true, archive, roster_source, roster_note,
    characters, behaviours,
    skins: texs.map((id) => ({ id, file: id + '.tex' })),
    drops: [], _local: true,
  };
}

// The TypeNames in swat.guns are the legal PowerUp names.
let gunsCache = null;
async function getDrops() {
  if (gunsCache) return gunsCache;
  try {
    const g = await A.api('guns');
    const list = g.guns || g || [];
    gunsCache = list
      .map((x) => ({ id: x.TypeName || x.type_name || x.name || x.type, kind: 9 }))
      .filter((x) => x.id);
  } catch { gunsCache = []; }
  return gunsCache;
}

// ---------------------------------------------------------- the enemy card

function thumbFor(archive, stem) {
  const img = document.createElement('img');
  img.src = `/api/character.png?archive=${encodeURIComponent(archive)}`
    + `&name=${encodeURIComponent(stem + '_lods.rsc')}&i=0`;
  img.style.cssText = 'width:32px;height:32px;object-fit:cover;border-radius:4px;'
    + 'border:1px solid var(--line);background:var(--well);flex:none';
  img.onerror = () => { img.style.visibility = 'hidden'; };
  img.alt = stem;
  return img;
}

function fieldsFromNode(n) {
  const p = n.props || {};
  return FIELDS.map(([key, label, control, options_from, required]) => {
    const present = key in p;
    let value = present ? p[key] : null;
    let arity = 1;
    if (control === 'list') {
      // ARITY IS READ FROM THE FILE AND NEVER ASSUMED. The design document's
      // sample shows seven values and the capability study says eight;
      // SHER70's CameraGuy measured NINE here today. A control with a fixed
      // number of slots would be wrong for at least two of those three.
      value = present ? String(value).trim().split(/\s+/) : [];
      arity = value.length;
    }
    return { key, label, value, control, options_from, options: null,
             editable: true, present, required, default: null,
             min: null, max: null, arity };
  });
}

async function getEnemy(archive, uid, area, manifest, node) {
  const q = 'enemy?archive=' + encodeURIComponent(archive) + '&uid=' + uid
    + (area ? '&area=' + encodeURIComponent(area) : '')
    + (manifest ? '&manifest=' + encodeURIComponent(manifest) : '');
  const r = await call(q, 'GET');
  if (r.ok && r.fields) return r;
  // Read the same 21 rows out of what the level route already parsed. Every
  // value shown comes off the disc either way; only the write path differs.
  return { ok: true, _local: true, uid, archive, file: node.source,
           name: node.name, spawn: { pos: node.pos, angle: node.angle },
           fields: fieldsFromNode(node) };
}

// One dry run, one plan, one Apply. `dry` defaults to true in the contract,
// so a write has to say so out loud.
function planSet(archive, area, manifest, uid, changes, dry) {
  return call('enemy_set', 'POST',
    { archive, area, manifest, uid, changes, dry: dry !== false });
}

// When enemy_set is not up, `set_field` still writes the fields the shipped
// server already allows -- Type among them, which is the character swap in
// full. Table is refused server-side by design, and that refusal is shown
// rather than hidden.
async function legacySet(archive, area, manifest, uid, key, value) {
  try {
    const r = await A.post('set_field', { archive, manifest, area, uid, key, value });
    if (!r.applied) {
      return { ok: false, problem: { code: 'not_editable',
        message: r.error || `${key} could not be written.`,
        fix: 'This build cannot write that field yet.',
        detail: '' } };
    }
    return { ok: true, applied: true, legacy: true };
  } catch (e) {
    return { ok: false, problem: { code: 'not_editable', message: e.message,
      fix: '', detail: '' } };
  }
}

const boolish = (v) => v === 1 || v === '1' || v === true || v === 'true';

function control(f, cast, onChange) {
  const wrap = el('div');
  wrap.style.cssText = 'display:flex;align-items:center;gap:6px;flex:1;min-width:0';
  const arc = cast.archive || A.level?.archive || '';
  if (f.control === 'pick') {
    const sel = document.createElement('select');
    sel.style.cssText = 'flex:1;min-width:0;font-size:12px';
    let list = [];
    if (f.options_from === 'cast.characters')
      list = (cast.characters || []).map((c) => [c.id,
        `${c.label}${c.borrowed ? ' (model: ' + c.render + ')' : ''}`
        + (c.used ? `  · ${c.used} here` : '')]);
    else if (f.options_from === 'cast.behaviours')
      list = (cast.behaviours || []).map((b) => [b.id,
        b.label + (b.used ? '  · used here' : '')]);
    else if (f.options_from === 'cast.skins')
      list = (cast.skins || []).map((s) => [s.id, s.id]);
    else if (f.options_from === 'cast.drops')
      list = (cast.drops || []).map((d) => [d.id, d.id]);
    else if (f.options) list = f.options.map((o) => [o, o]);

    if (!f.present || f.value == null) {
      const o = el('option', null, '— not set —');
      o.value = '';
      sel.append(o);
    }
    // PowerUp is stored on disc as "kind name" -- "9 Six_Shooter" -- but the
    // server already splits it and sends the name alone. Stripping again here
    // turned "Six_Shooter" into "": slice(1) of a one-token string is empty,
    // nothing matched, and the select fell back to index 0. The tool then said
    // this Sheriff drops a Rifle_Testudine when the disc plainly says a
    // revolver. Cosmetic -- writes are per-field deltas so the untouched value
    // survived -- but it misreported the game to the one person who cannot
    // check: someone who does not read the file directly.
    //
    // So only strip when a leading kind number is genuinely there.
    let cur = f.value;
    if (f.key === 'PowerUp' && typeof cur === 'string') {
      const parts = cur.trim().split(/\s+/);
      if (parts.length > 1 && /^\d+$/.test(parts[0])) {
        cur = parts.slice(1).join(' ');
      }
    }
    let seen = false;
    for (const [v, lab] of list) {
      const o = el('option', null, lab);
      o.value = v;
      if (String(v).toLowerCase() === String(cur ?? '').toLowerCase()) {
        o.selected = true; seen = true;
      }
      sel.append(o);
    }
    if (cur != null && cur !== '' && !seen) {
      // Never silently drop a value the file actually holds.
      const o = el('option', null, `${cur}  (not in this archive)`);
      o.value = cur;
      o.selected = true;
      sel.append(o);
    }
    if (!list.length) {
      sel.disabled = true;
      sel.title = 'No legal values found in this archive.';
    }
    wrap.append(sel);
    let th = null;
    if (f.options_from === 'cast.characters') {
      th = thumbFor(arc, sel.value || String(cur || ''));
      wrap.prepend(th);
    }
    sel.onchange = () => {
      if (th) {
        th.style.visibility = '';
        th.src = `/api/character.png?archive=${encodeURIComponent(arc)}`
          + `&name=${encodeURIComponent(sel.value + '_lods.rsc')}&i=0`;
      }
      onChange(sel.value === '' ? null : sel.value);
    };
  } else if (f.control === 'bool') {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = boolish(f.value);
    cb.onchange = () => onChange(cb.checked ? 1 : 0);
    wrap.append(cb);
  } else if (f.control === 'list') {
    const vals = Array.isArray(f.value) ? f.value.slice()
      : String(f.value ?? '').trim().split(/\s+/).filter(Boolean);
    const row = el('div');
    row.style.cssText = 'display:flex;gap:3px;flex-wrap:wrap;flex:1;align-items:center';
    vals.forEach((v, i) => {
      const inp = document.createElement('input');
      inp.value = v;
      inp.style.cssText = 'width:34px;font-size:11px;padding:2px 3px';
      inp.onkeydown = (e) => e.stopPropagation();
      inp.onchange = () => { vals[i] = inp.value; onChange(vals.join(' ')); };
      row.append(inp);
    });
    const c = el('span', 'muted', vals.length ? `${vals.length} slots` : 'not in the file');
    c.style.fontSize = '11px';
    c.title = 'The number of slots is read from the file, never assumed.';
    row.append(c);
    wrap.append(row);
  } else {
    const inp = document.createElement('input');
    inp.value = f.value == null ? '' : String(f.value);
    inp.style.cssText = 'flex:1;min-width:0;font-size:12px';
    if (f.control === 'int' || f.control === 'float') inp.type = 'number';
    inp.onkeydown = (e) => e.stopPropagation();
    inp.onchange = () => onChange(inp.value);
    wrap.append(inp);
  }
  return wrap;
}

async function buildCard(host, node) {
  const lv = A.level;
  if (!lv) return;
  const cast = await getCast(lv.archive, lv.area);
  if (!cast.ok) return renderProblem(host, cast.problem);
  cast.archive = lv.archive;
  // The drop list is only wanted by two of the twenty-one rows, and the
  // server answers one request at a time, so it must not hold up the two
  // fields this panel exists for.
  if (!cast.drops || !cast.drops.length) {
    getDrops().then((d) => { cast.drops = d; }).catch(() => {});
  }

  const enemy = await getEnemy(lv.archive, node.uid, lv.area, lv.manifest, node);
  const fields = enemy.fields;
  const changes = {};

  const card = el('div');
  card.dataset.enemyCard = String(node.uid);

  const head = el('div');
  head.style.cssText = 'display:flex;align-items:center;gap:6px;margin-bottom:2px';
  head.append(el('b', null, node.name));
  card.append(head);
  const where = el('div', 'muted', enemy.file);
  where.style.fontSize = '11px';
  card.append(where);

  if (cast.roster_note) {
    const nb = el('div', 'infbox', cast.roster_note);
    nb.style.fontSize = '11px';
    card.append(nb);
  }

  // THE TWO FIELDS HE NAMED: first, adjacent, separately labelled, because
  // Character and Behaviour are two different questions about one enemy.
  const top = el('div');
  top.style.cssText = 'margin-top:8px;display:flex;flex-direction:column;gap:6px';
  const rest = el('div');
  rest.style.cssText = 'display:flex;flex-direction:column;gap:5px;margin-top:4px';

  const planHost = el('div');
  planHost.style.cssText = 'margin-top:8px;font-size:12px';
  const applyBtn = el('button', 'primary', 'Apply');
  applyBtn.disabled = true;
  applyBtn.title = 'Change something first.';

  let planTimer = null;
  const replan = () => {
    clearTimeout(planTimer);
    planTimer = setTimeout(async () => {
      const keys = Object.keys(changes);
      if (!keys.length) {
        planHost.innerHTML = '';
        applyBtn.disabled = true;
        applyBtn.title = 'Change something first.';
        return;
      }
      const stop = waiting(planHost, 'Checking it fits…');
      const r = await planSet(lv.archive, lv.area, lv.manifest, node.uid, changes, true);
      stop();
      if (r.ok && r.plan) {
        renderPlan(planHost, r.plan);
        applyBtn.disabled = r.plan.fits === false;
        applyBtn.title = r.plan.fits === false ? 'It does not fit its slot.' : '';
        return;
      }
      if (!notBuilt(r)) {
        renderProblem(planHost, r.problem
          || { code: 'server_fault', message: 'Refused.', fix: '', detail: '' });
        applyBtn.disabled = true;
        return;
      }
      // Say what will happen instead of pretending. The legacy write path
      // below still covers everything the shipped server allows.
      const ef = window.__editableFields || {};
      const legacyOk = keys.every((k) => k in ef);
      planHost.innerHTML = '';
      const box = el('div', 'infbox');
      box.append(el('b', null,
        `${keys.length} field${keys.length === 1 ? '' : 's'}: ${keys.join(', ')}`));
      box.append(el('div', null, legacyOk
        ? 'Writes one field at a time — one undo step per field, each '
          + 'slot-checked.'
        : `This build does not write ${keys.filter((k) => !(k in ef)).join(', ')}.`));
      planHost.append(box);
      applyBtn.disabled = !legacyOk;
      applyBtn.title = legacyOk ? '' : 'Not writable in this build.';
    }, 120);
  };

  for (const f of fields) {
    const row = el('div', 'field');
    row.style.cssText = 'display:flex;align-items:center;gap:8px';
    const lab = el('label', null, f.label);
    lab.style.cssText = 'width:130px;flex:none;font-size:12px';
    lab.title = f.key;
    if (!f.present) lab.style.color = 'var(--dim-ink)';
    row.append(lab, control(f, cast, (v) => { changes[f.key] = v; replan(); }));
    (f.key === 'Type' || f.key === 'Table' ? top : rest).append(row);
  }
  card.append(top);

  const more = document.createElement('details');
  more.style.marginTop = '8px';
  const sum = document.createElement('summary');
  sum.style.cssText = 'font-size:11px;color:var(--dim-ink);cursor:pointer';
  sum.textContent = `The other ${fields.length - 2} fields`;
  more.append(sum, rest);
  card.append(more);
  card.append(planHost);

  const acts = el('div');
  acts.style.cssText = 'margin-top:8px;display:flex;gap:6px;align-items:center';
  applyBtn.onclick = async () => {
    const keys = Object.keys(changes);
    applyBtn.disabled = true;
    const before = applyBtn.textContent;
    applyBtn.textContent = 'Applying…';
    let r = await planSet(lv.archive, lv.area, lv.manifest, node.uid, changes, false);
    if (notBuilt(r)) {
      // One field at a time through the route that exists. The cost in undo
      // steps is stated rather than glossed.
      for (const k of keys) {
        r = await legacySet(lv.archive, lv.area, lv.manifest, node.uid, k,
                            changes[k] == null ? '' : String(changes[k]));
        if (!r.ok) break;
      }
      if (r.ok) {
        A.toast(`${node.name}: ${keys.join(', ')} — `
          + `${keys.length} undo step${keys.length === 1 ? '' : 's'}`, 'good');
      }
    }
    applyBtn.textContent = before;
    if (!r.ok) { renderProblem(planHost, r.problem); return; }
    if (r.plan) A.toast(r.plan.undo?.label || 'Applied.', 'good');
    for (const k of keys) delete changes[k];
    await A.reloadLevel(true);
    A.refreshHistory();
  };
  acts.append(applyBtn);

  const revert = el('button', '', 'Discard');
  revert.title = 'Throw away the pending changes and re-read the file.';
  revert.onclick = () => A.renderInspector();
  acts.append(revert);
  card.append(acts);

  host.innerHTML = '';
  host.append(card);
}

// ------------------------------------------------- inspector interception

// app.js owns #inspector. Rather than change how it renders, watch it: when
// it has just drawn an enemy, put the real editor above and take out the
// duplicate rows underneath. Nothing in app.js has to move.
function watchInspector() {
  const box = document.querySelector('#inspector');
  if (!box) return;
  let busy = false;
  const sync = () => {
    if (busy) return;
    const uid = A.selected;
    const n = uid == null ? null : A.nodeOf(uid);
    if (!n || n.kind !== 'enemy' || (A.selection?.size || 1) > 1) return;
    if (box.querySelector(`[data-enemy-card="${uid}"]`)) return;
    busy = true;
    const host = el('div');
    host.dataset.castHost = '1';
    box.prepend(host);
    const stop = waiting(host, "Reading this level's cast…");
    // The old rows said the same thing worse: one bare text box for Type and
    // a greyed row for everything else.
    for (const t of box.querySelectorAll('table'))
      if (!t.closest('[data-cast-host]')) t.remove();
    for (const d of box.querySelectorAll('.muted'))
      if (/greyed rows name AI behaviour/.test(d.textContent)) d.remove();
    for (const d of box.querySelectorAll('div'))
      if (d.textContent === 'Properties' && d.classList.contains('muted')) d.remove();
    buildCard(host, n)
      .catch((e) => {
        host.innerHTML = '';
        host.append(el('p', 'muted', 'Cast: ' + e.message));
      })
      .finally(() => { stop(); busy = false; });
  };
  new MutationObserver(sync).observe(box, { childList: true });
  sync();
}

// ------------------------------------------------------- place a new enemy

function beginPlace(type, table, label) {
  placing = { type, table, label };
  const c = A.renderer?.domElement;
  if (c) c.style.cursor = 'crosshair';
  updatePlaceHint();
}

function endPlace() {
  placing = null;
  const c = A.renderer?.domElement;
  if (c) c.style.cursor = '';
  updatePlaceHint();
}

function updatePlaceHint() {
  let h = document.querySelector('#castPlaceHint');
  if (!h) {
    h = el('div');
    h.id = 'castPlaceHint';
    h.style.cssText = 'position:absolute;left:50%;transform:translateX(-50%);'
      + 'bottom:74px;z-index:8;background:var(--bg2);border:1px solid var(--gold);'
      + 'border-radius:6px;padding:5px 12px;font-size:12px;display:none';
    document.querySelector('#view')?.append(h);
  }
  h.style.display = placing ? 'block' : 'none';
  if (placing) h.textContent = `Placing ${placing.label} — click the ground (Esc)`;
}

async function doPlace(point) {
  const lv = A.level;
  const p = [+point.x.toFixed(3), +point.y.toFixed(3), +point.z.toFixed(3)];
  const type = placing.type, table = placing.table, label = placing.label;
  const body = { archive: lv.archive, area: lv.area, pos: p, angle: 0,
                 type, table, name: null, file: null, template_uid: null,
                 fields: null };
  const sheet = openSheet({
    title: 'Place a new enemy',
    subject: {
      title: `${label} at ${p[0]}, ${p[1]}, ${p[2]}`,
      where: `${lv.archive.split('/').pop().replace(/\.DAT$/i, '')}`
             + " ▸ this level's enemy list",
      format: `Character ${type} · Behaviour ${table}`,
      // Nothing placed by this path has ever been booted in the game. A plan
      // that fits is not a game that runs.
      rung: 'untested',
    },
    primary: 'Place it',
    startLabel: 'Checking it fits…',
  });
  endPlace();

  const dry = await call('enemy_place', 'POST', { ...body, dry: true });
  if (dry.ok && dry.plan) {
    sheet.plan(dry.plan);
    const go = document.querySelector('#xferGo');
    go.onclick = async () => {
      go.disabled = true;
      sheet.busy('Placing…');
      const w = await call('enemy_place', 'POST', { ...body, dry: false });
      if (!w.ok) return sheet.refuse(w.problem);
      sheet.done(`<b style="color:var(--grn)">Placed ${w.new_name || label}</b>`);
      await A.reloadLevel(true);
      A.refreshHistory();
    };
    return;
  }
  if (!notBuilt(dry)) return sheet.refuse(dry.problem);

  // /api/enemy_place is not up. The mechanism it will use is available today
  // through two shipped routes -- clone the smallest local Enemy block, then
  // rewrite its Type -- so offer that, and say plainly what it costs, which
  // is more than one undo step and cannot rewrite Behaviour.
  const local = (lv.nodes || []).filter((n) => n.kind === 'enemy');
  if (!local.length) {
    return sheet.refuse({ code: 'no_enm_here',
      message: 'This level has no enemy list of its own.',
      fix: 'Its roster lives in SWAT.DAT, and this build cannot place '
         + 'enemies into that file yet.', detail: '' });
  }
  // Smallest block = fewest property lines, to minimise the bytes spliced in.
  const template = local.slice().sort(
    (a, b) => Object.keys(a.props || {}).length - Object.keys(b.props || {}).length)[0];
  sheet.plan({
    writes: [], fits: true,
    summary: `Clones ${template.name} in ${template.source}, then sets Character `
      + `to ${type}`,
    undo: { steps: 2, label: `placed a ${type} from ${template.name}` },
  });
  const go = document.querySelector('#xferGo');
  go.disabled = false;
  go.onclick = async () => {
    go.disabled = true;
    sheet.busy('Placing…');
    try {
      const src = A.nodeOf(template.uid);
      const offset = [p[0] - src.pos[0], p[1] - src.pos[1], p[2] - src.pos[2]];
      const d = await A.post('duplicate', { archive: lv.archive, manifest: lv.manifest,
        area: lv.area, uid: template.uid, offset, dry: true });
      const rep = Object.values(d.report || {})[0];
      if (rep && !rep.fits) {
        return sheet.refuse({ code: 'slot_overflow',
          message: `No room in ${template.source} — a copy needs ${rep.stored} `
            + `stored bytes and the slot is ${rep.slot}.`,
          fix: 'Delete an enemy in that file first, or look at DISC ▸ Archives.',
          detail: '' });
      }
      const res = await A.post('duplicate', { archive: lv.archive, manifest: lv.manifest,
        area: lv.area, uid: template.uid, offset });
      if (!res.applied) {
        return sheet.refuse({ code: 'slot_overflow',
          message: res.error || 'The clone did not fit.', fix: '', detail: '' });
      }
      await A.reloadLevel(true);
      const made = (A.level.nodes || []).find((n) => n.name === res.new_name);
      if (made && String(made.props?.Type || '').toLowerCase() !== type.toLowerCase()) {
        const set = await legacySet(lv.archive, lv.area, lv.manifest, made.uid,
                                    'Type', type);
        if (!set.ok) {
          sheet.done('<b style="color:var(--warn)">Placed, but the character '
            + 'could not be swapped.</b>');
          await A.reloadLevel(true);
          return;
        }
      }
      sheet.done(`<b style="color:var(--grn)">Placed ${res.new_name}</b> as `
        + `${type} — 2 undo steps.`);
      await A.reloadLevel(true);
      A.refreshHistory();
    } catch (e) {
      sheet.refuse({ code: 'server_fault', message: 'Placing failed.',
                     fix: '', detail: e.message });
    }
  };
}

function wireViewportPlacing() {
  const c = A.renderer?.domElement;
  if (!c) return;
  // Place on the UP of a still click, exactly like selection. This used to
  // fire on pointerDOWN, so starting a camera drag while armed dropped an
  // enemy at the press point -- writing to the disc as a side effect of
  // trying to look around.
  let down = null;
  let armedFor = null;
  c.addEventListener('pointerdown', (e) => {
    if (!placing || e.button !== 0 || e.altKey) { down = null; return; }
    down = [e.clientX, e.clientY];
    armedFor = A.level && A.level.archive + '|' + A.level.area;
    e.stopImmediatePropagation();
    e.preventDefault();
  }, true);
  c.addEventListener('pointerup', (e) => {
    if (!placing || e.button !== 0 || !down) return;
    const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]);
    down = null;
    if (moved > 4) return;                    // that was a drag, not a click
    // The level changed under an armed mode: disarm instead of writing an
    // enemy into whichever level happens to be on screen now.
    if (A.level && armedFor !== A.level.archive + '|' + A.level.area)
      return endPlace();
    e.stopImmediatePropagation();
    e.preventDefault();
    const pt = A.surfacePoint(e, true);
    if (!pt) return A.toast('Aim at the ground.', 'bad');
    doPlace(pt);
  }, true);
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && placing) { endPlace(); e.stopImmediatePropagation(); }
  }, true);
}

// ------------------------------------------------------------ Library dock

// A placement palette parked outside the editor was the clearest information-
// architecture error in the build. It lives beside the viewport now and holds
// every catalogue that answers "what can I put here": props, this archive's
// characters, and this archive's behaviours.
async function buildLibrary() {
  const head = document.querySelector('#assetDockHead');
  const body = document.querySelector('#assetDockBody');
  if (!head || !body || head.dataset.lib) return;
  head.dataset.lib = '1';
  head.textContent = '';
  // A fold, because the Library is tall and sits under the outliner -- the
  // panel you actually navigate with. Collapsed state persists.
  const caret = el('span', '', '▾');
  caret.style.cssText = 'display:inline-block;width:12px;transition:transform .12s';
  const title = el('span', '', 'Library');
  head.append(caret, title);
  head.style.cursor = 'pointer';
  head.title = 'Show or hide the Library';
  const KEY = 'rdr.libOpen';
  const setOpen = (open) => {
    body.style.display = open ? '' : 'none';
    caret.style.transform = open ? '' : 'rotate(-90deg)';
    try { localStorage.setItem(KEY, open ? '1' : '0'); } catch (e) {}
  };
  head.onclick = () => setOpen(body.style.display === 'none');
  let start = '1';
  try { start = localStorage.getItem(KEY) ?? '1'; } catch (e) {}

  const strip = el('div', 'switch');
  strip.style.cssText = 'margin-bottom:6px';
  const panes = {};
  const mk = (id, label) => {
    const b = el('button', id === 'props' ? 'on' : '', label);
    b.dataset.pane = id;
    b.onclick = () => {
      for (const x of strip.children) x.classList.toggle('on', x === b);
      for (const k of Object.keys(panes)) panes[k].style.display = k === id ? '' : 'none';
      if (id !== 'props') fillCast(id);
    };
    strip.append(b);
    return b;
  };
  mk('props', 'Props');
  mk('chars', 'Characters');
  // No Behaviours tab. A behaviour is not a thing you place -- it is a
  // property of an enemy, and it is already a picker in the inspector. As a
  // third tab it was a list nobody could act on.

  // The dock that is already there IS the props pane; wrap it rather than
  // rebuild it, so the existing asset search keeps working untouched.
  const propsPane = el('div');
  while (body.firstChild) propsPane.append(body.firstChild);
  panes.props = propsPane;
  body.append(strip, propsPane);
  setOpen(start === '1');

  for (const id of ['chars']) {
    const p = el('div');
    p.style.display = 'none';
    panes[id] = p;
    body.append(p);
  }
  body.style.display = '';

  let filledFor = null;
  async function fillCast(which) {
    const lv = A.level;
    const pane = panes[which];
    if (!pane) return;
    if (!lv) {
      pane.innerHTML = '';
      pane.append(el('p', 'muted', 'Open a level to see the characters it can use.'));
      return;
    }
    const key = lv.archive + '|' + which;
    if (filledFor === key) return;
    filledFor = key;
    pane.innerHTML = '';
    pane.append(el('p', 'muted', 'Reading the archive…'));
    const cast = await getCast(lv.archive, lv.area);
    pane.innerHTML = '';
    if (!cast.ok) return renderProblem(pane, cast.problem);
    if (cast.roster_note) {
      const nb = el('div', 'infbox', cast.roster_note);
      nb.style.fontSize = '11px';
      pane.append(nb);
    }
    const items = which === 'chars' ? cast.characters : cast.behaviours;
    const hint = el('div', 'muted', which === 'chars'
      ? `${items.length} characters in this archive — pick one, then click the ground`
      : `${items.length} behaviours in this archive`);
    hint.style.cssText = 'font-size:11px;margin-bottom:4px';
    pane.append(hint);
    const list = el('div', 'scroll');
    list.style.maxHeight = '200px';
    for (const it of items) {
      const row = el('div', 'row');
      row.style.cssText = 'display:flex;align-items:center;gap:6px';
      if (which === 'chars') row.append(thumbFor(lv.archive, it.render || it.id));
      row.append(el('span', 'nm', it.label || it.id));
      if (it.used) row.append(el('span', 'tag', `${it.used} here`));
      if (it.borrowed) row.append(el('span', 'tag', 'model: ' + it.render));
      row.title = it.file + (it.states
        ? '\n' + Object.entries(it.states).map(([k, v]) => `${k}: ${v}`).join('\n')
        : '');
      if (which === 'chars') {
        row.onclick = () => {
          const beh = cast.behaviours.find((b) => b.used) || cast.behaviours[0];
          if (!beh) return A.toast('This archive has no behaviour tables.', 'bad');
          beginPlace(it.id, beh.id, it.label || it.id);
        };
      }
      list.append(row);
    }
    pane.append(list);
  }

  // Follow whatever level is open. `fillCast` short-circuits when the archive
  // has not changed, so this costs nothing when nothing has happened.
  setInterval(() => {
    const on = strip.querySelector('button.on');
    if (on && on.dataset.pane !== 'props') fillCast(on.dataset.pane);
  }, 3000);
}

// ----------------------------------------------------------- LOGIC ▸ Cast

async function initCastTab() {
  const arcSel = document.querySelector('#castArc');
  if (!arcSel) return;
  if (arcSel.dataset.wired) {
    // Already built: FOLLOW the loaded level on every re-entry, the way the
    // Surfaces tab does -- unless the user picked an archive here themselves
    // this session. Seeding exactly once meant the header could say TOMB4
    // while this tab silently stayed on the level from an hour ago.
    if (!arcSel.dataset.userPick && A.level
        && arcSel.value !== A.level.archive
        && [...arcSel.options].some((o) => o.value === A.level.archive)) {
      arcSel.value = A.level.archive;
      arcSel.dataset.rerender = '1';
      arcSel.dispatchEvent(new Event('change'));
    }
    return;
  }
  try {
    const arcs = await A.api('archives');
    for (const a of arcs.archives || arcs || []) {
      const name = a.archive || a.name || a;
      if (!/^LVLS_/.test(name) && name !== 'SWAT.DAT') continue;
      const o = el('option', null, name);
      o.value = name;
      arcSel.append(o);
    }
  } catch {
    // One failed /api/archives call must not kill the picker for the whole
    // session -- unwired, the next visit tries again. states.js documents
    // this exact failure; the fix now lives in all of its siblings.
    return;
  }
  // Wired only AFTER the options landed, for the same reason.
  arcSel.dataset.wired = '1';
  if (A.level && [...arcSel.options].some((o) => o.value === A.level.archive))
    arcSel.value = A.level.archive;
  else {
    // FINAL PASS: with no level loaded this opened on the first archive by
    // name, LVLS_M2/BRIDGE2 -- a Showdown map with one character and no
    // behaviours, so the tab looked empty. Open on a campaign level instead.
    const first = [...arcSel.options].find((o) => /^LVLS_(?!M[24]\/)/.test(o.value));
    if (first) arcSel.value = first.value;
  }

  const render = async () => {
    const arc = arcSel.value;
    const chars = document.querySelector('#castChars');
    const behs = document.querySelector('#castBehs');
    if (!arc) {
      chars.innerHTML = '';
      chars.append(el('p', 'muted', 'Choose an archive to see its cast.'));
      return;
    }
    chars.innerHTML = '';
    chars.append(el('p', 'muted', 'Reading…'));
    behs.innerHTML = '';
    const cast = await getCast(arc, null);
    chars.innerHTML = '';
    behs.innerHTML = '';
    if (!cast.ok) return renderProblem(chars, cast.problem);
    cast.archive = arc;
    document.querySelector('#castRoster').textContent = cast.roster_note
      || 'Characters and behaviours change independently.';
    document.querySelector('#castCharCount').textContent = cast.characters.length;
    document.querySelector('#castBehCount').textContent = cast.behaviours.length;
    const q = (document.querySelector('#castFilter').value || '').toLowerCase();
    for (const c of cast.characters) {
      if (q && !String(c.id).toLowerCase().includes(q)) continue;
      const row = el('div', 'row');
      row.style.cssText = 'display:flex;align-items:center;gap:8px';
      row.append(thumbFor(arc, c.render || c.id), el('span', 'nm', c.label || c.id));
      if (c.borrowed) row.append(el('span', 'tag', 'model: ' + c.render));
      if (c.used) row.append(el('span', 'tag', `${c.used} used`));
      row.onclick = () => showDetail('character', c, cast);
      chars.append(row);
    }
    for (const b of cast.behaviours) {
      if (q && !String(b.label || b.id).toLowerCase().includes(q)) continue;
      const row = el('div', 'row');
      row.append(el('span', 'nm', b.label || b.id));
      if (b.used) row.append(el('span', 'tag', 'used here'));
      row.onclick = () => showDetail('behaviour', b, cast);
      behs.append(row);
    }
  };
  arcSel.onchange = () => {
    // A change from the user is remembered so re-entering the tab does not
    // overwrite their pick; a change dispatched by the follow-the-level code
    // above is not a pick. Either way the detail pane empties: it described a
    // character in the archive that just left the screen, and its export
    // buttons would still have written to it.
    if (arcSel.dataset.rerender) delete arcSel.dataset.rerender;
    else arcSel.dataset.userPick = '1';
    const det = document.querySelector('#castDetail');
    if (det) det.innerHTML = '';
    render();
  };
  document.querySelector('#castFilter').oninput = render;
  render();
}

function showDetail(kind, it, cast) {
  const box = document.querySelector('#castDetail');
  box.innerHTML = '';
  const h = el('div');
  h.append(el('b', null, it.label || it.id), rungBadge(it.rung || 'measured'));
  box.append(h, el('div', 'muted', it.file));
  if (kind === 'character') {
    const t = el('table');
    for (const [k, v] of [['Written into Type as', it.id],
                          ['Model', it.render], ['Skeleton', it.skel],
                          ['Used in this level', it.used]]) {
      if (v == null) continue;
      const tr = el('tr');
      tr.append(el('td', 'muted', k), el('td', null, String(v)));
      t.append(tr);
    }
    box.append(t);
    // THE CHARACTER, WHERE THE CHARACTER IS. Picking one here used to show a
    // 32px thumbnail; the actual model, and the buttons that take a body to
    // Blender and back, lived in a different tab. The person choosing a
    // character is the person who wants to look at it and edit it, so the
    // live preview and the per-part OBJ export/import mount right here --
    // the same code the creature editor uses, not a copy of it.
    mountCastModel(box, cast.archive, it);
  } else {
    box.append(el('div', 'muted', 'Written into Table as ' + it.id));
    if (it.states) {
      const t = el('table');
      let unread = 0;
      for (const [k, v] of Object.entries(it.states)) {
        // A null here means the quick reader did not find that line -- some
        // tables spell the keys in lower case -- not that the slot is empty.
        // The Behaviour tables editor reads every spelling.
        if (v == null) { unread += 1; continue; }
        const tr = el('tr');
        tr.append(el('td', 'muted', k.replace(/State$/, '')), el('td', null, v));
        t.append(tr);
      }
      box.append(t);
      if (unread) {
        const u = el('p', 'muted', `${unread} more binding${unread === 1 ? '' : 's'} `
          + 'not shown here — the editor below reads all of them.');
        u.style.fontSize = '11px';
        box.append(u);
      }
    } else {
      box.append(el('p', 'muted',
        'Which states this table binds could not be read just now.'));
    }
    // The palette answers "what may an enemy be"; the editor for WHAT A
    // BEHAVIOUR DOES is one click away, not a separate search. Only offered
    // when that panel is in this build -- the release strip removes it whole.
    if (document.querySelector('#tab-states')) {
      const go = el('button', '', 'Edit this table’s bindings…');
      go.style.cssText = 'margin-top:8px;font-size:12px';
      go.title = 'Opens ' + it.file + ' in Behaviour tables, where every '
        + 'binding is a dropdown of this archive’s own state files.';
      go.onclick = () => {
        A.showTab('states');
        window.dispatchEvent(new CustomEvent('rdr:behaviour-table',
          { detail: { archive: cast.archive, file: it.file } }));
      };
      box.append(go);
    }
  }
}

// ------------------------------------------- THE MODEL, IN THE DETAIL PANE
//
// The bundle a character draws with is named by its .cr's FileNameRender, so
// the lookup key is `it.render` -- a character whose render stem is another
// character's (39 of the 181 reuse one) previews that shared body, which is
// exactly what the game shows.

let castCharsIndex = null;       // /api/characters payload, fetched once

async function mountCastModel(box, archive, it) {
  const stage = el('div');
  stage.style.cssText = 'width:100%;height:225px;position:relative;'
    + 'margin-top:8px;background:var(--well);border:1px solid var(--line);'
    + 'border-radius:4px;overflow:hidden';
  const note = el('div', 'muted', 'Loading model…');
  note.style.cssText = 'position:absolute;inset:0;display:flex;align-items:'
    + 'center;justify-content:center;font-size:11px;padding:10px;text-align:center';
  stage.append(note);
  const partsHead = el('div', 'muted',
    'Model parts. Export OBJ, edit in Blender, import. Move vertices only.');
  partsHead.style.cssText = 'font-size:11px;margin-top:7px;line-height:1.5';
  const parts = el('div');
  box.append(stage, partsHead, parts);

  if (!castCharsIndex) {
    try { castCharsIndex = (await A.api('characters')).characters; }
    catch { castCharsIndex = []; }
  }
  const stem = it.render || it.id;
  const entry = castCharsIndex.find((c) => c.stem === stem);
  if (!entry) {
    note.textContent = `No model bundle named ${stem}_lods.rsc on the disc — `
      + 'this character is not a visible actor.';
    partsHead.style.display = 'none';
    return;
  }

  // The export/import rows come from the same function the creature editor
  // mounts, so the two panes cannot drift apart. After an import, redraw THIS
  // preview -- the edit should be visible where it was made.
  A.renderCharacterParts(entry, parts,
    () => { if (stage.isConnected) drawModel(); });

  const THREE = A.THREE;
  let renderer = null, raf = 0;

  function dispose() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    if (renderer) {
      renderer.dispose();
      renderer.forceContextLoss?.();
      renderer.domElement.remove();
      renderer = null;
    }
  }

  async function drawModel() {
    let d;
    try {
      d = await A.api(`character?archive=${encodeURIComponent(entry.archive)}`
        + `&name=${encodeURIComponent(entry.file)}&lod=0`);
    } catch (e) {
      note.style.display = 'flex';
      note.textContent = 'Could not decode this model: ' + e.message;
      return;
    }
    if (!stage.isConnected) return;      // user already clicked elsewhere
    note.style.display = 'none';
    dispose();

    const scene = new THREE.Scene();
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(d.positions, 3));
    if (d.uvs && d.uvs.length * 3 === d.positions.length * 2)
      geo.setAttribute('uv', new THREE.Float32BufferAttribute(d.uvs, 2));
    geo.setIndex(d.indices);
    geo.computeVertexNormals();
    let mat;
    if (d.textures && geo.getAttribute('uv')) {
      const tex = new THREE.TextureLoader().load(
        `/api/character.png?archive=${encodeURIComponent(entry.archive)}`
        + `&name=${encodeURIComponent(entry.file)}&i=0`);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.flipY = false;
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      mat = new THREE.MeshBasicMaterial(
        { map: tex, side: THREE.DoubleSide, transparent: true, alphaTest: 0.35 });
    } else {
      mat = new THREE.MeshStandardMaterial(
        { color: 0xc8bda8, roughness: 0.85, side: THREE.DoubleSide });
      scene.add(new THREE.HemisphereLight(0xffffff, 0x333333, 1.1));
    }
    const mesh = new THREE.Mesh(geo, mat);
    scene.add(mesh);

    geo.computeBoundingBox();
    const bb = geo.boundingBox;
    const c = bb.getCenter(new THREE.Vector3());
    const size = bb.getSize(new THREE.Vector3()).length() || 1;
    const w = stage.clientWidth || 260, hpx = stage.clientHeight || 225;
    const cam = new THREE.PerspectiveCamera(35, w / hpx, size / 100, size * 10);
    cam.position.set(c.x, c.y + size * 0.1, c.z + size * 1.05);
    cam.lookAt(c);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(w, hpx);
    stage.append(renderer.domElement);

    const tick = () => {
      // Self-disposing: the pane redraws for every row click, so the mount
      // with no owner cleans itself up the frame after it leaves the page --
      // a page of leaked WebGL contexts kills the 3D editor's own canvas.
      if (!stage.isConnected) { dispose(); return; }
      mesh.rotation.y += 0.008;
      renderer.render(scene, cam);
      raf = requestAnimationFrame(tick);
    };
    tick();
  }
  drawModel();
}

// ------------------------------------- USE ANOTHER CHARACTER'S MODEL
//
// One `.cr` field decides what a character looks like, and the whole of the
// look moves with it: body, hat, face and the morph targets that drive the
// face's expressions all live in the one `*_lods.rsc` the field names. 84 of
// the 122 bundles a `.cr` can name carry both an MPH0 chunk and a `_face`
// MOD0, 38 carry neither, and none carries one without the other -- so the
// bundle is the unit, and moving geometry between bundles instead is what
// leaves a face addressing a mesh it was not authored against.
//
// The picker is short on purpose. Only models with the SAME bone count are
// offered, because `FileNameAnim` does not move with the swap and every clip
// carries one curve triple per bone. 139 of the 181 creatures share the
// 52-bone rig, so "short" still means about ninety choices for most of them.

let swapDoc = null;              // the last /api/model_targets payload
let swapFor = null;              // which .cr it describes

function swapHost() {
  // Absent when the feature is stripped from a release build. A missing panel
  // is the gate; there is no flag to check and no button to disable.
  return document.querySelector('#crSwap');
}

async function loadSwapPanel(name, showModel, quiet) {
  const host = swapHost();
  if (!host) return;
  swapFor = name;
  // `quiet` is the undo path: the panel is already showing this creature and
  // only its VALUES can have moved, so it keeps what is on screen until the
  // new answer arrives. Blanking to a spinner on every history event would
  // flicker the panel for edits that have nothing to do with this character.
  const stop = quiet ? () => {} : (host.innerHTML = '',
                                   waiting(host, 'Reading the model palette…'));
  if (!quiet) swapDoc = null;
  const doc = await call('model_targets?name=' + encodeURIComponent(name));
  stop();
  if (swapFor !== name) return;            // a faster click already won
  if (!doc.ok) {
    // A quiet refresh must never replace a good panel with an error: the user
    // did not ask for this fetch, and undoing an unrelated edit while the
    // server hiccups is not a reason to throw away what is on screen.
    if (quiet) return;
    if (notBuilt(doc)) {
      host.append(el('div', 'muted',
        'Model swapping is not in this build.'));
      return;
    }
    renderProblem(host, doc.problem);
    return;
  }
  const was = quiet && swapDoc ? swapDoc.current.render : null;
  swapDoc = doc;
  renderSwapPanel();
  // A quiet refresh only redraws the viewer if the undo actually moved the
  // pointer. Otherwise every Ctrl+Z anywhere in the app would re-decode this
  // character for nothing.
  if (quiet) {
    if (was && was !== doc.current.render) A.showCreatureModel?.(doc.current.render);
    return;
  }
  // Follow the pointer for the preview too. 59 of the 181 creatures render as
  // some other stem, and the viewer keyed off the .cr's own name -- so every
  // one of them showed "no model bundle named mmrd_lods.rsc" while the game
  // has been drawing them from `mrd_lods.rsc` all along.
  //
  // `showModel` is passed after a swap, including a swap BACK to stock, where
  // the render stem equals the .cr's own name and the test below would skip a
  // redraw the viewer still needs. Decoding a character is expensive enough
  // that asking for it twice is worth avoiding.
  const want = showModel || doc.current.render;
  if (want && (showModel || want !== doc.stem)) A.showCreatureModel?.(want);
}

function renderSwapPanel() {
  const host = swapHost();
  if (!host || !swapDoc) return;
  const d = swapDoc;
  host.innerHTML = '';

  const now = el('div');
  now.style.cssText = 'font-size:11px;margin-bottom:7px';
  now.append(el('span', 'muted', 'Now: '), el('b', null, d.current.render));
  now.append(el('span', 'muted',
    ` — ${d.current.bones} bones, `
    + (d.current.face ? 'face and morph targets' : 'no separate face')
    + (d.current.render === d.current.stock ? '' : ` (stock: ${d.current.stock})`)));
  host.append(now);

  // The Studio hands the working ISO to PCSX2 during a play session and reads
  // the pristine one meanwhile, so this line can be a stock value rather than
  // the current one. Saying so beats quietly being wrong.
  if (d.live === false)
    host.append(Object.assign(el('div', 'muted',
      'PCSX2 has the working disc, so this is the stock value.'),
      { style: 'font-size:11px;margin-bottom:6px' }));

  if (!d.targets.length) {
    host.append(el('div', 'muted',
      `Nothing else on the disc is rigged to ${d.current.bones} bones, so `
      + 'there is no model this character could wear.'));
    return;
  }

  const sel = el('select');
  sel.style.cssText = 'width:100%;font-size:11px';
  for (const t of d.targets) {
    const bits = [t.face ? 'face + morphs' : 'no face'];
    bits.push(`in ${t.archives} of ${d.archives} archive`
      + (d.archives === 1 ? '' : 's'));
    // "stock" is how the disc shipped and is the way back; "current" is where
    // the file points now. They are the same until the first swap, and after
    // one they are the two rows that matter most.
    if (t.current) bits.push('current');
    if (t.stock && !t.current) bits.push('stock');
    sel.append(new Option(`${t.id} — ${bits.join(' · ')}`, t.id));
  }
  const first = d.targets.find((t) => !t.current) || d.targets[0];
  sel.value = first.id;
  host.append(sel);

  const note = el('div', 'muted');
  note.style.cssText = 'font-size:11px;line-height:1.55;margin:6px 0';
  const mark = () => {
    const t = d.targets.find((x) => x.id === sel.value);
    note.innerHTML = '';
    if (!t) return;
    note.append(`Writes FileNameRender and FileNameSkel — the two are the same `
      + `value in all 181 .cr files, so a swap changes both. `);
    if (t.archives < d.archives)
      note.append(el('b', null,
        `${d.archives - t.archives} of ${d.archives} copies stay as they are: `
        + `their archive has no ${t.id}_lods.rsc.`));
    if (t.hitbox_missing.length)
      note.append(el('b', null,
        ` Hit boxes name bones ${t.id}'s skeleton does not have.`));
  };
  sel.onchange = mark;
  mark();
  host.append(note);

  const go = el('button', 'primary', 'Use this model…');
  go.style.fontSize = '12px';
  go.onclick = () => openSwapSheet(sel.value);
  host.append(go);
}

function openSwapSheet(target) {
  const d = swapDoc;
  const row = d.targets.find((t) => t.id === target);
  let optHost = null;
  const body = { name: d.name, target };

  const sheet = openSheet({
    title: "Use another character's model",
    subject: {
      title: `${d.stem} renders as ${target}`,
      where: `${d.name} in ${row.archives} of ${d.archives} archive`
             + (d.archives === 1 ? '' : 's'),
      format: 'FileNameRender and FileNameSkel, patched in place',
      // Never "measured". 59 .cr files already borrow a model, which proves
      // the engine follows the pointer -- and every one of those 59 borrows a
      // variant of the same character. An arbitrary pairing has never run.
      rung: row.current ? 'measured' : 'untested',
    },
    primary: 'Use this model',
    renderOptions(host) {
      optHost = host;
      host.append(el('p', 'muted', 'Checking…'));
    },
    async onOpen(api) {
      api.busy('Working out what this costs…');
      const r = await call('model_swap', 'POST', { ...body, dry: true });
      if (!r.ok) { renderWarnings(optHost, []); return api.refuse(r.problem); }
      renderWarnings(optHost, r.warnings);
      api.plan(r.plan);
    },
    async onApply(api) {
      api.busy('Writing…');
      const r = await call('model_swap', 'POST', { ...body, dry: false });
      if (!r.ok) return api.refuse(r.problem);
      api.done(`Done — ${d.stem} now renders as ${target} in `
        + `${r.plan.writes.length} archive(s). Ctrl+Z to undo.`);
      A.toast(`${d.stem} now uses ${target}'s model — load a level in PCSX2 `
        + 'to see it. Ctrl+Z to undo.', 'good');
      A.refreshHistory();
      loadSwapPanel(d.name, target);
      setTimeout(closeSheet, 1200);
    },
  });
  return sheet;
}

function renderWarnings(host, warnings) {
  if (!host) return;
  host.innerHTML = '';
  if (!warnings || !warnings.length) {
    host.append(el('p', 'muted', 'No options — this is one field in one file.'));
    return;
  }
  for (const w of warnings) {
    const box = el('div', 'infbox');
    box.style.cssText = 'margin-bottom:6px;border-left-color:'
      + (w.ok ? 'var(--grn)' : 'var(--gold)');
    box.append(el('div', null, w.text));
    host.append(box);
  }
}

export function init(bridge) {
  A = bridge;
  // app.js owns the Creatures tab and tells us which creature is open. An
  // event, so this module failing to load costs the panel and nothing else.
  window.addEventListener('rdr:creature', (e) => loadSwapPanel(e.detail));
  // Undo and redo rewrite the .cr under the panel. Without this the panel kept
  // reporting the model the user had just taken back -- measured: swap ncb to
  // mrd, Ctrl+Z, panel says "Now: mrd", server says ncb.
  window.addEventListener('rdr:history', () => {
    if (swapFor && swapHost()) loadSwapPanel(swapFor, null, true);
  });
  // app.js keeps this for its own inspector; the legacy write path reads it
  // to know which fields the shipped server will accept.
  A.api('editable_fields').then((f) => { window.__editableFields = f; }).catch(() => {});
  watchInspector();
  wireViewportPlacing();
  buildLibrary();
  onPanel((panel) => { if (panel === 'cast') initCastTab(); });
}
