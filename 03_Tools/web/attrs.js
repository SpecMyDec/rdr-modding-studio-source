// attrs.js — THE 57,645 TYPED ATTRIBUTES, THE ROTATION, AND THE SPLINES.
//
// Three things the toolkit already parsed and could not change:
//
//   * every typed attribute on a box, bound, zone or line -- 57,645 of them,
//     parsed, glossed, colour-coded and rendered as dead text;
//   * `Node.rot`, nine numbers with byte spans on every one of those nodes,
//     which 8,489 boxes and 364 zones carry non-identity and no route wrote;
//   * spline vertices, 11,413 of them including 129 patrol routes and the
//     TrainTrack shape, all with spans and no way to move a point.
//
// Each is one route away, and each lands in the Inspector next to the thing
// it describes rather than in a panel of its own.

import { call, notBuilt, renderProblem, renderPlan, waiting, rungBadge, el }
  from './transfer.js';

let A = null;

// ------------------------------------------------------------- one section

function section(title, rung) {
  const box = el('div');
  box.style.cssText = 'margin-top:10px;border-top:1px solid var(--line);padding-top:8px';
  const h = el('div');
  h.style.cssText = 'display:flex;align-items:center;gap:6px;margin-bottom:5px';
  h.append(el('b', null, title));
  if (rung) h.append(rungBadge(rung));
  box.append(h);
  return box;
}

// A planned write, an Apply that is dead until the plan fits, and one undo
// step. The same shape as everywhere else, deliberately.
function committer(host, planFn, applyFn, label) {
  const planHost = el('div');
  planHost.style.cssText = 'margin-top:6px;font-size:12px';
  const btn = el('button', 'primary', label || 'Apply');
  btn.disabled = true;
  btn.title = 'Change something first.';
  const acts = el('div');
  acts.style.cssText = 'margin-top:6px;display:flex;gap:6px';
  acts.append(btn);
  host.append(planHost, acts);

  let timer = null;
  const replan = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const body = planFn();
      if (!body) {
        planHost.innerHTML = '';
        btn.disabled = true;
        btn.title = 'Change something first.';
        return;
      }
      const stop = waiting(planHost, 'Checking it fits…');
      const r = await body;
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
  };
  btn.onclick = async () => {
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = 'Applying…';
    const r = await applyFn();
    btn.textContent = was;
    if (!r.ok) return renderProblem(planHost, r.problem);
    A.toast(r.plan?.undo?.label || 'Applied.', 'good');
    await A.reloadLevel(true);
    A.refreshHistory();
  };
  return { replan, planHost, btn };
}

// -------------------------------------------- E5, the typed attributes

async function renderAttrEditor(host, node) {
  const lv = A.level;
  const q = `attrs?archive=${encodeURIComponent(lv.archive)}&uid=${node.uid}`
    + (lv.area ? `&area=${encodeURIComponent(lv.area)}` : '');
  const stop = waiting(host, 'Reading the attributes…');
  const r = await call(q, 'GET');
  stop();
  if (!r.ok) {
    if (notBuilt(r)) { host.remove(); return; }
    return renderProblem(host, r.problem);
  }
  if (!r.attrs || !r.attrs.length) { host.remove(); return; }

  host.innerHTML = '';
  const sec = section(`Attributes (${r.attrs.length})`, r.attrs[0].rung || 'measured');
  const changes = {};
  const rows = el('div');
  rows.style.cssText = 'display:flex;flex-direction:column;gap:4px';

  for (const a of r.attrs) {
    const row = el('div', 'field');
    row.style.cssText = 'display:flex;align-items:center;gap:8px';
    const lab = el('label', null, a.name);
    lab.style.cssText = 'width:132px;flex:none;font-size:12px';
    lab.title = `${a.type} — the file's own type token`;
    row.append(lab);

    // Values are ALWAYS strings on the wire: the file stores tokens and only
    // the value span is rewritten, so the column layout survives.
    if (a.control === 'bool') {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = String(a.value).toLowerCase() === 'true' || a.value === '1';
      cb.disabled = !a.editable;
      cb.onchange = () => {
        changes[a.name] = cb.checked
          ? (a.options?.[0] === 'true' ? 'true' : '1')
          : (a.options?.[0] === 'true' ? 'false' : '0');
        commit.replan();
      };
      row.append(cb);
    } else if (a.control === 'pick') {
      const wrap = el('div');
      wrap.style.cssText = 'display:flex;gap:5px;flex:1;min-width:0';
      const sel = document.createElement('select');
      sel.style.cssText = 'flex:1;min-width:0;font-size:12px';
      for (const o of a.options || []) {
        const op = el('option', null, o);
        op.value = o;
        if (o === a.value) op.selected = true;
        sel.append(op);
      }
      // The type set is open, so a closed list would be a lie. The free-text
      // box sits beside the picker rather than replacing it.
      const free = document.createElement('input');
      free.placeholder = 'or type one';
      free.style.cssText = 'width:110px;font-size:11px';
      free.onkeydown = (e) => e.stopPropagation();
      sel.onchange = () => { free.value = ''; changes[a.name] = sel.value; commit.replan(); };
      free.onchange = () => {
        if (free.value.trim()) { changes[a.name] = free.value.trim(); commit.replan(); }
      };
      sel.disabled = free.disabled = !a.editable;
      wrap.append(sel, free);
      row.append(wrap);
    } else {
      const inp = document.createElement('input');
      inp.value = a.value;
      inp.style.cssText = 'flex:1;min-width:0;font-size:12px';
      if (a.control === 'int' || a.control === 'float') inp.type = 'number';
      inp.disabled = !a.editable;
      inp.onkeydown = (e) => e.stopPropagation();
      inp.onchange = () => { changes[a.name] = inp.value; commit.replan(); };
      row.append(inp);
    }
    const ty = el('span', 'muted', a.type);
    ty.style.cssText = 'font-size:11px;flex:none;width:52px;text-align:right';
    row.append(ty);
    rows.append(row);
  }
  sec.append(rows);
  host.append(sec);

  const body = (dry) => ({ archive: lv.archive, area: lv.area, uid: node.uid,
                           changes, dry });
  const commit = committer(sec,
    () => (Object.keys(changes).length
      ? call('attr_set', 'POST', body(true)) : null),
    () => call('attr_set', 'POST', body(false)));
}

// ------------------------------------------------------ E6, the rotation

// The nine numbers are the first nine of the node's matrix block, IN FILE
// ORDER. The wire format claims nothing about row- versus column-major; the
// interpretation below is inferred, and it is marked as such, because if it
// is transposed the box turns the wrong way in the viewport and nothing on
// the disc is harmed.
function matrixToEuler(m) {
  const t = new A.THREE.Matrix4().set(
    m[0], m[1], m[2], 0,
    m[3], m[4], m[5], 0,
    m[6], m[7], m[8], 0,
    0, 0, 0, 1);
  const e = new A.THREE.Euler().setFromRotationMatrix(t, 'YXZ');
  const d = 180 / Math.PI;
  return [e.x * d, e.y * d, e.z * d];
}

function eulerToMatrix(deg) {
  const r = Math.PI / 180;
  const e = new A.THREE.Euler(deg[0] * r, deg[1] * r, deg[2] * r, 'YXZ');
  const t = new A.THREE.Matrix4().makeRotationFromEuler(e);
  const a = t.elements;      // three.js stores column-major
  return [a[0], a[4], a[8], a[1], a[5], a[9], a[2], a[6], a[10]]
    .map((v) => +v.toFixed(6));
}

function renderRotation(host, node) {
  const lv = A.level;
  if (!node.rot || node.rot.length < 9) return host.remove();
  const identity = node.rot.every((v, i) =>
    Math.abs(v - (i % 4 === 0 ? 1 : 0)) < 1e-6);

  const sec = section('Rotation', 'inferred');
  const note = el('div', 'muted', identity
    ? 'Square to the world.'
    : 'Turned. These nine numbers are the matrix as the file stores them.');
  note.style.cssText = 'font-size:11px;margin-bottom:5px';
  sec.append(note);

  const deg = matrixToEuler(node.rot);
  const cur = deg.slice();
  const grid = el('div');
  grid.style.cssText = 'display:flex;gap:6px';
  ['Pitch', 'Yaw', 'Roll'].forEach((axis, i) => {
    const w = el('div');
    w.style.cssText = 'flex:1;min-width:0';
    w.append(el('div', 'muted', axis));
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.step = '1';
    inp.value = deg[i].toFixed(2);
    inp.style.cssText = 'width:100%;font-size:12px';
    inp.onkeydown = (e) => e.stopPropagation();
    inp.onchange = () => {
      const v = parseFloat(inp.value);
      if (!isFinite(v)) return;
      cur[i] = v;
      // Show it in the viewport before it is written, so a transposed
      // interpretation is visible rather than theoretical.
      const mesh = A.meshes.get(node.uid);
      if (mesh) {
        const r = Math.PI / 180;
        mesh.rotation.set(cur[0] * r, cur[1] * r, cur[2] * r, 'YXZ');
      }
      commit.replan();
    };
    w.append(inp);
    grid.append(w);
  });
  sec.append(grid);

  const keep = document.createElement('label');
  keep.style.cssText = 'display:block;font-size:11px;color:var(--dim-ink);margin-top:5px';
  const kb = document.createElement('input');
  kb.type = 'checkbox';
  kb.checked = true;
  keep.append(kb, document.createTextNode(' keep each number the same width (contains growth)'));
  sec.append(keep);

  const body = (dry) => ({ archive: lv.archive, area: lv.area, uid: node.uid,
                           matrix9: eulerToMatrix(cur), keep_width: kb.checked, dry });
  const commit = committer(sec,
    () => call('node_rotate', 'POST', body(true)),
    () => call('node_rotate', 'POST', body(false)),
    'Apply rotation');
  kb.onchange = () => commit.replan();

  host.innerHTML = '';
  host.append(sec);
}

// -------------------------------------------------------- E7, the splines

function renderVertices(host, node) {
  const lv = A.level;
  if (!node.verts || node.verts.length < 2) return host.remove();
  const sec = section(`Shape — ${node.verts.length} points`, 'measured');
  const pick = document.createElement('select');
  pick.style.cssText = 'width:100%;font-size:12px';
  node.verts.forEach((v, i) => {
    const o = el('option', null,
      `point ${i + 1} of ${node.verts.length}  (${v.map((x) => (+x).toFixed(1)).join(', ')})`);
    o.value = String(i);
    pick.append(o);
  });
  sec.append(pick);

  const xyz = el('div', 'xyz');
  xyz.style.marginTop = '5px';
  const inputs = [];
  for (let i = 0; i < 3; i++) {
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.step = '0.1';
    inp.onkeydown = (e) => e.stopPropagation();
    inp.onchange = () => commit.replan();
    inputs.push(inp);
    xyz.append(inp);
  }
  const load = () => {
    const v = node.verts[+pick.value];
    inputs.forEach((inp, i) => { inp.value = (+v[i]).toFixed(3); });
  };
  pick.onchange = () => { load(); commit.replan(); };
  load();
  sec.append(xyz);

  const body = (dry) => ({
    archive: lv.archive, area: lv.area, uid: node.uid, index: +pick.value,
    pos: inputs.map((i) => parseFloat(i.value) || 0), keep_width: true, dry,
  });
  const commit = committer(sec,
    () => call('vertex_set', 'POST', body(true)),
    () => call('vertex_set', 'POST', body(false)),
    'Move this point');

  host.innerHTML = '';
  host.append(sec);
}

// --------------------------------------- proportional scaling, as a group
//
// The fourth thing the toolkit already parsed and could not change: a
// SELECTION. `Level.resize` -> `scene.edit_node_size` has always been able to
// rewrite a box's width/height/depth Num spans, and `Level.move` its position
// spans, but the only way to reach either was one number, in one object, at a
// time. Making a street bigger meant typing four numbers into each of thirty
// boxes -- thirty disc writes, thirty presses of Ctrl+Z.
//
// ONE UNDO STEP is the point, not a nicety. Every change here goes out in a
// single POST /api/move; the server merges them into one Level.apply and
// writer_for opens exactly one journal group for the whole batch
// (studio.py:398), and journal.undo pops the trailing run that shares a group
// (journal.py:131). So a forty-object scale is one Ctrl+Z -- and because
// Level.apply dry-runs every file first, one slot overflow anywhere refuses
// the entire operation before a byte is written.
//
// Positions here are WORLD/display coordinates, which is what `node.pos` holds
// once applyTileOrigins has folded in the tile offset. pushChanges runs every
// pos back through unoffset() before it goes on the wire, so a tile-owned
// crate is written in the tile-local numbers its own file stores. Nothing in
// this section may subtract `origin` itself; doing it twice is the bug.

const AXES = ['width', 'height', 'depth'];

// What a scale can actually change in this selection, and what it must not.
function survey() {
  const move = [], size = [];
  let baked = 0, readOnly = 0, splines = 0;
  for (const uid of (A.selection || [])) {
    const n = A.nodeOf(uid);
    if (!n) continue;
    // An event marker comes from a file with no matrix -- .sub, .cam, .glows,
    // .aisp -- so there is no position span to rewrite.
    if (n.readonly) { readOnly++; continue; }
    if (n.size && AXES.some((k) => n.size[k] != null)) size.push(uid);
    // A zone or bound that instances world geometry ships an IDENTITY matrix:
    // 250 of 250 in MANS, 343 of 343 in TOMB. Its real place is baked into the
    // surface's own vertices, so an offset written here would move it in game
    // by a distance the viewport cannot draw -- and about a group centroid
    // that offset is large. Shape is the control for those; they are counted
    // and named instead of being quietly mangled.
    const p = n.pos || [0, 0, 0];
    if ((n.kind === 'zone' || n.kind === 'bound')
        && n.props && n.props.resourceIndex != null
        && Math.abs(p[0]) < 1e-6 && Math.abs(p[1]) < 1e-6
        && Math.abs(p[2]) < 1e-6) { baked++; continue; }
    if (n.kind === 'line') splines++;
    move.push(uid);
  }
  return { move, size, baked, readOnly, splines };
}

// The shared centroid: the mean of the positions this scale is allowed to
// move. Deliberately NOT the bounding-box centre of everything selected --
// the baked-at-origin nodes above would drag it to [0,0,0] and throw the
// whole group across the level.
function centroidOf(uids) {
  if (!uids.length) return null;
  const c = [0, 0, 0];
  for (const uid of uids) {
    const p = A.nodeOf(uid).pos;
    c[0] += +p[0]; c[1] += +p[1]; c[2] += +p[2];
  }
  return [c[0] / uids.length, c[1] / uids.length, c[2] / uids.length];
}

// Where each mesh was drawn before the preview touched it, and WHICH object
// that was. The identity matters: reloading the level rebuilds every mesh, so
// copying a remembered position onto a new one would undo the write on screen
// and make a successful save look like it did nothing.
const home = new Map();

function remember(uid) {
  const obj = A.meshes.get(uid);
  if (!obj || home.has(uid)) return null;
  home.set(uid, { obj, pos: obj.position.clone(), scale: obj.scale.clone() });
  return home.get(uid);
}

function unpreview() {
  for (const [uid, h] of home) {
    const obj = A.meshes.get(uid);
    if (obj && obj === h.obj) { obj.position.copy(h.pos); obj.scale.copy(h.scale); }
  }
  home.clear();
}

// The live preview moves each mesh by a DELTA from where it was drawn rather
// than recomputing an absolute position. Meshes do not all sit on their node's
// coordinates: an enemy is lifted 0.9 m, and a one-vertex spline marker is
// drawn at vertex + pos + 0.6 m. A delta is right for every one of them
// without this code having to know which is which.
function preview(s, c, f, aboutOwn, doSize) {
  if (!aboutOwn && c) {
    for (const uid of s.move) {
      const h = home.get(uid) || remember(uid);
      if (!h) continue;
      const p = A.nodeOf(uid).pos;
      const d = f - 1;
      h.obj.position.set(h.pos.x + d * (p[0] - c[0]),
                         h.pos.y + d * (p[1] - c[1]),
                         h.pos.z + d * (p[2] - c[2]));
    }
  }
  if (doSize) {
    for (const uid of s.size) {
      const h = home.get(uid) || remember(uid);
      // Scaling the Object3D rather than rebuilding BoxGeometry keeps the
      // wireframe child in step and keeps a decomposed non-identity matrix
      // (8,489 boxes on this disc carry one) intact.
      if (h) h.obj.scale.copy(h.scale).multiplyScalar(f);
    }
  }
}

function changesFor(s, c, f, aboutOwn, doSize) {
  const by = new Map();
  if (!aboutOwn && c) {
    for (const uid of s.move) {
      const p = A.nodeOf(uid).pos;
      const to = [c[0] + (p[0] - c[0]) * f,
                  c[1] + (p[1] - c[1]) * f,
                  c[2] + (p[2] - c[2]) * f];
      // An object sitting ON the centroid does not move, and with one object
      // selected that is every object. Sending the position anyway would
      // rewrite the spans with the same numbers -- spending slot budget, and
      // re-formatting a coordinate the file may have stored to a different
      // precision, for a change of nothing.
      if (Math.abs(to[0] - p[0]) < 1e-6 && Math.abs(to[1] - p[1]) < 1e-6
          && Math.abs(to[2] - p[2]) < 1e-6) continue;
      by.set(uid, { uid, pos: to });
    }
  }
  if (doSize) {
    for (const uid of s.size) {
      const n = A.nodeOf(uid);
      const size = {};
      for (const k of AXES) if (n.size[k] != null) size[k] = +n.size[k] * f;
      const e = by.get(uid) || { uid };
      e.size = size;
      by.set(uid, e);
    }
  }
  return [...by.values()];
}

function renderGroupScale(box) {
  // Whatever preview is on screen belongs to the selection that is being
  // replaced. This runs on every Inspector rebuild, which is exactly when
  // that stops being true.
  unpreview();
  if (!A.level || !A.selection || !A.selection.size) return;

  const first = survey();
  // A single object with no size can only be moved, and moving one thing
  // about its own centre is the identity. Nothing to offer.
  if (A.selection.size < 2 && !first.size.length) return;

  const sec = section(A.selection.size > 1
    ? `Scale these ${A.selection.size} together` : 'Scale', 'measured');

  let factor = 1;
  let aboutOwn = false;
  let doSize = true;

  const row = el('div', 'field');
  row.style.cssText = 'display:flex;align-items:center;gap:8px';
  const lab = el('label', null, 'Factor');
  lab.style.cssText = 'width:52px;flex:none';
  const num = document.createElement('input');
  num.type = 'number';
  num.step = '0.05';
  num.min = '0.01';
  num.value = '1';
  num.style.cssText = 'width:78px;flex:none';
  const slide = document.createElement('input');
  slide.type = 'range';
  slide.min = '0.1';
  slide.max = '3';
  slide.step = '0.01';
  slide.value = '1';
  slide.style.flex = '1';
  row.append(lab, num, slide);

  const modes = el('div');
  modes.style.cssText = 'margin-top:6px;display:flex;flex-direction:column;gap:3px';
  const mk = (checked, text, title) => {
    const l = el('label');
    l.style.cssText = 'display:flex;gap:6px;align-items:flex-start;font-size:12px';
    l.title = title;
    const r = document.createElement('input');
    r.type = 'radio';
    r.name = 'rdrScaleAbout';
    r.checked = checked;
    l.append(r, el('span', null, text));
    modes.append(l);
    return r;
  };
  const rGroup = mk(true, 'about the group’s centre',
    'Objects move apart or together as well as changing size. This is what '
    + '"make this street bigger" means.');
  const rOwn = mk(false, 'each about its own centre',
    'Nothing moves. Only the volumes change size, in place.');

  const szLab = el('label');
  szLab.style.cssText = 'display:flex;gap:6px;align-items:center;font-size:12px;margin-top:6px';
  const szBox = document.createElement('input');
  szBox.type = 'checkbox';
  szBox.checked = true;
  szLab.append(szBox, el('span', null, 'resize volumes (width / height / depth)'));

  const note = el('div', 'muted');
  note.style.cssText = 'margin-top:7px;font-size:11px;line-height:1.5';

  const btn = el('button', 'primary', 'Scale');
  btn.disabled = true;
  const reset = el('button', '', 'Reset');
  const acts = el('div');
  acts.style.cssText = 'margin-top:8px;display:flex;gap:6px';
  acts.append(btn, reset);

  sec.append(row, modes, szLab, note, acts);

  // Recomputed on every touch rather than captured once: a drag or a nudge
  // between opening this panel and pressing Scale would otherwise be scaled
  // from stale coordinates.
  const live = () => {
    const s = survey();
    return { s, c: centroidOf(s.move) };
  };

  const describe = () => {
    const { s, c } = live();
    const bits = [];
    const nMove = aboutOwn ? 0 : s.move.length;
    const nSize = doSize ? s.size.length : 0;
    if (nMove) bits.push(`${nMove} will move`);
    if (nSize) bits.push(`${nSize} will change size`);
    const lines = [];
    lines.push(bits.length
      ? `×${factor.toFixed(3)} — ${bits.join(', ')}.`
      : 'Nothing in this selection would change.');
    if (!aboutOwn && c) {
      lines.push(`Centre ${c[0].toFixed(1)}, ${c[1].toFixed(1)}, `
        + `${c[2].toFixed(1)} — the mean of what can move.`);
    }
    if (s.baked) {
      lines.push(`${s.baked} building(s) left alone: their position is baked `
        + `into the geometry, so use Shape on those.`);
    }
    if (s.readOnly) {
      lines.push(`${s.readOnly} event marker(s) left alone: no matrix to write.`);
    }
    if (s.splines && !aboutOwn) {
      lines.push(`${s.splines} spline(s) will be carried, not stretched — `
        + `their vertices are edited per point, below.`);
    }
    note.textContent = '';
    for (const t of lines) note.append(el('div', null, t));
    btn.disabled = Math.abs(factor - 1) < 1e-6 || !bits.length;
  };

  const setFactor = (v, from) => {
    if (!isFinite(v) || v <= 0) return;
    factor = Math.max(0.01, v);
    if (from !== 'num') num.value = String(+factor.toFixed(3));
    if (from !== 'slide' && factor >= 0.1 && factor <= 3)
      slide.value = String(factor);
    const { s, c } = live();
    unpreview();
    preview(s, c, factor, aboutOwn, doSize);
    describe();
  };

  num.oninput = () => setFactor(parseFloat(num.value), 'num');
  num.onkeydown = (e) => e.stopPropagation();   // keep WASD out of the flyer
  slide.oninput = () => setFactor(parseFloat(slide.value), 'slide');
  rGroup.onchange = () => { aboutOwn = false; setFactor(factor, 'mode'); };
  rOwn.onchange = () => { aboutOwn = true; setFactor(factor, 'mode'); };
  szBox.onchange = () => { doSize = szBox.checked; setFactor(factor, 'mode'); };
  reset.onclick = () => { slide.value = '1'; setFactor(1, 'slide'); };

  btn.onclick = async () => {
    const { s, c } = live();
    const changes = changesFor(s, c, factor, aboutOwn, doSize);
    if (!changes.length) return A.toast('Nothing here can be scaled.', 'warn');
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = 'Scaling…';
    const label = `scale ${changes.length} object(s) ×${factor.toFixed(3)}`;
    // ONE call, ONE journal group, ONE Ctrl+Z -- see the note at the top.
    const ok = await A.pushChanges(changes, label);
    btn.textContent = was;
    // Written: the level reloaded and every mesh in `home` is a dead object,
    // so drop the record rather than restoring onto its replacement.
    // Refused: pushChanges already put the positions back, and this returns
    // the box scales it does not know about.
    if (ok) home.clear(); else unpreview();
  };

  describe();
  box.append(sec);
}

// ------------------------------------------------- inspector interception

function watchInspector() {
  const box = document.querySelector('#inspector');
  if (!box) return;
  let busy = false;
  const sync = () => {
    if (busy) return;
    const uid = A.selected;
    const n = uid == null ? null : A.nodeOf(uid);
    if (!n || n.kind === 'enemy' || n.readonly) return;
    if ((A.selection?.size || 1) > 1) return;
    // A section that finds nothing to show removes itself, so "have I already
    // done this node" cannot be answered by looking for its own markup. The
    // container stays either way, and that is what the guard reads.
    const held = box.querySelector('[data-attr-uid]');
    if (held && held.dataset.attrUid === String(uid)) return;
    busy = true;
    if (held) held.remove();
    const wrap = el('div');
    wrap.dataset.attrUid = String(uid);
    box.append(wrap);
    const mk = (fn) => {
      const h = el('div');
      wrap.append(h);
      try { const p = fn(h, n); if (p && p.catch) p.catch(() => h.remove()); }
      catch { h.remove(); }
    };
    mk(renderAttrEditor);
    mk(renderRotation);
    mk(renderVertices);
    busy = false;
  };
  new MutationObserver(sync).observe(box, { childList: true });
  sync();
}

export function init(bridge) {
  A = bridge;
  watchInspector();
  // A real hook rather than a second MutationObserver: the group scale has to
  // land ABOVE the position fields, and it has to run for a MULTI-selection,
  // which is the one case watchInspector deliberately declines.
  A.addInspectorSection?.(renderGroupScale);
}
