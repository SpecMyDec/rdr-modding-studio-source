// pckview.js -- TEXT > Flash screens > Pages: a .pck screen drawn as the game draws it, every object in a tree
// (as FFDEC shows a .swf), and its layout and pictures edited in place.
//
// The scene comes from rdrstudio/pckscene.py (de_x_pck_scene): the root timeline, sprites, buttons, shapes (convex
// polygons, nearly all filled with a bitmap), static texts (drawn from each font's own glyph atlas, as the game
// does), edit texts and bitmaps. Changes go back with de_x_pck_apply: a placement's packed matrix (the parts it
// holds), a text's matrix, a button record's matrix, a picture (same size). Twips are 1/20 px.

import { el } from './transfer.js';

const ID = [1, 0, 0, 1, 0, 0];
const mul = (A, B) => [A[0] * B[0] + A[2] * B[1], A[1] * B[0] + A[3] * B[1], A[0] * B[2] + A[2] * B[3],
  A[1] * B[2] + A[3] * B[3], A[0] * B[4] + A[2] * B[5] + A[4], A[1] * B[4] + A[3] * B[5] + A[5]];
const inv = (M) => {
  const det = M[0] * M[3] - M[1] * M[2] || 1e-9;
  const a = M[3] / det, b = -M[1] / det, c = -M[2] / det, d = M[0] / det;
  return [a, b, c, d, -(a * M[4] + c * M[5]), -(b * M[4] + d * M[5])];
};
const apply = (M, x, y) => [M[0] * x + M[2] * y + M[4], M[1] * x + M[3] * y + M[5]];
const twipM = (m) => (m ? [m[0], m[1], m[2], m[3], m[4] / 20, m[5] / 20] : ID);
const rgba = (hex, alpha) => {
  const h = hex || '000000ff';
  const a = (parseInt(h.slice(6, 8), 16) / 255) * (alpha == null ? 1 : alpha);
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
};
const TYPE_LABEL = { sprite: 'Sprites', button: 'Buttons', shape: 'Shapes', bitmap: 'Bitmaps', text: 'Texts', edit: 'Edit texts', font: 'Fonts', sound: 'Sounds' };

export async function pckPages(host, ctx) {
  const { where, key, archive, get, post, toast, panel, onSaved, toText, emptyNote } = ctx;
  const q = 'where=' + where + '&key=' + encodeURIComponent(key);
  let S;
  try { S = await get('de_x_pck_scene?' + q); } catch (e) { toast(e.message, 'bad'); return; }
  const inMods = where === 'mods';
  const changes = new Map();            // a key per thing changed -> the change
  const byAt = new Map();               // place command offset -> its command (the scene's own object)
  const names = {};                     // character -> instance names it is placed with
  const allTimelines = [S.root, ...Object.values(S.objects).filter((o) => o.type === 'sprite').map((o) => o.frames)];
  for (const frames of allTimelines) for (const fr of frames) for (const c of fr) {
    if (c.op !== 'place') continue;
    byAt.set(c.at, c);
    if (c.name && c.char != null) (names[c.char] = names[c.char] || new Set()).add(c.name);
  }
  let version = Date.now();             // per opened view (and bumped on change): the browser's cached copy of a
                                        // picture is never shown after a save
  let ttfList = null;
  let sounds = null;                    // de_x_pck_sounds: the sounds the scripts play, the front-end bank
  const TTF_KEY = 'rdr.pck.ttf';
  const missingTTF = () => { try { return localStorage.getItem(TTF_KEY) || null; } catch (e) { return null; } };
  // letters a text lacks are added to its font on save only when this is ticked (shared with the Text view)
  const AUTO_KEY = 'rdr.pck.autofill';
  const autofill = () => { try { return localStorage.getItem(AUTO_KEY) === '1'; } catch (e) { return false; } };
  const origRuns = {}, origEdit = {};
  for (const [id, o] of Object.entries(S.objects)) {
    if (o.type === 'text') origRuns[id] = JSON.parse(JSON.stringify(o.runs));
    if (o.type === 'edit') origEdit[id] = o.text;
  }
  const imgs = new Map();
  const tints = new Map();
  let redrawSoon = null;
  let userMoved = false;                // zoomed or panned by hand: images arriving do not re-fit the view
  const later = () => { if (!userMoved) fitted = false; if (!redrawSoon) redrawSoon = requestAnimationFrame(() => { redrawSoon = null; draw(); }); };
  const replaced = new Map();           // bitmap id -> object URL of the picture that replaces it (until saved)
  function image(kind, id, k) {
    const ck = kind + '|' + id + '|' + (k || 0) + '|' + version + '|' + (replaced.get(id) && kind === 'bitmap' ? replaced.get(id) : '');
    let im = imgs.get(ck);
    if (!im) {
      im = new Image();
      im.onload = later;
      im.src = kind === 'bitmap' && replaced.get(id) ? replaced.get(id)
        : `/api/de_x_pck_image?${q}&kind=${kind}&id=${id}&k=${k || 0}&v=${version}`;
      imgs.set(ck, im);
    }
    return im.complete && im.naturalWidth ? im : null;
  }
  function tinted(fid, k, color) {
    const im = image('atlas', fid, k);
    if (!im) return null;
    const ck = fid + '|' + k + '|' + color + '|' + version;
    let c = tints.get(ck);
    if (!c) {
      c = document.createElement('canvas'); c.width = im.naturalWidth; c.height = im.naturalHeight;
      const g = c.getContext('2d');
      g.drawImage(im, 0, 0);
      g.globalCompositeOperation = 'source-in';
      g.fillStyle = rgba(color.slice(0, 6) + 'ff');
      g.fillRect(0, 0, c.width, c.height);
      tints.set(ck, c);
    }
    return c;
  }

  // ------------------------------------------------------------------ what is shown
  let view = { kind: 'screen', id: null };   // screen | object
  let frame = 0;                              // of the root, or of the sprite shown alone
  let selected = null;                        // {path, hit}
  let showBoxes = false;
  const hidden = new Set();                   // placements hidden while editing (the view only, never saved)
  let zoom = 1, panX = 0, panY = 0, fitted = false;
  let hits = [];

  function displayList(frames, f) {
    const dl = new Map();
    for (let i = 0; i <= Math.min(f, frames.length - 1); i++) for (const c of frames[i]) {
      if (c.op === 'place') {
        const cur = dl.get(c.depth);
        if (c.char != null) {
          dl.set(c.depth, { cmd: c, char: c.char, m: c.m || (cur && cur.char === c.char ? cur.m : null), mcmd: c.m ? c : (cur && cur.char === c.char ? cur.mcmd : null), cx: c.cx || null });
        } else if (cur) {
          if (c.m) { cur.m = c.m; cur.mcmd = c; }
          if (c.cx) cur.cx = c.cx;
        }
      } else if (c.op === 'remove') dl.delete(c.depth);
    }
    return [...dl.entries()].sort((a, b) => a[0] - b[0]).map(([depth, v]) => ({ depth, ...v, m: v.mcmd ? v.mcmd.m : v.m }));
  }

  function alphaOf(cx) { return cx ? Math.max(0, Math.min(1, cx.mul[3] + cx.add[3] / 255)) : 1; }

  function addHit(M, x0, y0, x1, y1, path, id) {
    const pts = [apply(M, x0, y0), apply(M, x1, y0), apply(M, x1, y1), apply(M, x0, y1)];
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    hits.push({ x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys), path, id });
  }

  function drawChar(g, V, id, M, alpha, depth, path) {
    const o = S.objects[id];
    if (!o || depth > 16) return;
    const T = mul(V, M);
    if (o.type === 'shape') {
      let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
      for (const c of o.cmds) {
        const p = c.pts;
        if (p.length < 4) continue;
        g.setTransform(...T);
        g.beginPath();
        for (let i = 0; i < p.length; i += 2) {
          const x = p[i] / 20, y = p[i + 1] / 20;
          bx0 = Math.min(bx0, x); by0 = Math.min(by0, y); bx1 = Math.max(bx1, x); by1 = Math.max(by1, y);
          if (i) g.lineTo(x, y); else g.moveTo(x, y);
        }
        if (c.op === 'fill') {
          g.closePath();
          const f = o.fills[c.style - 1];
          if (!f) continue;
          const bo = f.bitmap != null ? S.objects[f.bitmap] : null;
          if (bo && bo.type === 'bitmap') {
            const im = image('bitmap', f.bitmap);
            if (!im) continue;
            g.save();
            g.clip();
            g.globalAlpha = alpha;
            g.transform(...inv(f.m));
            g.imageSmoothingEnabled = true;
            g.drawImage(im, 0, 0, (bo.w || 1) / (bo.tw || bo.w || 1), (bo.h || 1) / (bo.th || bo.h || 1));
            g.restore();
          } else if (f.type === 0) {
            g.fillStyle = rgba(f.color, alpha);
            g.fill();
          }
        } else if (c.op === 'line') {
          const s = o.strokes[c.style - 1];
          if (!s) continue;
          g.strokeStyle = rgba(s.color, alpha);
          g.lineWidth = Math.max(0.5, s.width / 20);
          g.stroke();
        }
      }
      if (bx0 < Infinity) addHit(T, bx0, by0, bx1, by1, path, id);
    } else if (o.type === 'sprite') {
      const f = view.kind === 'object' && view.id === id && depth === 0 ? frame : 0;
      for (const it of displayList(o.frames, f)) {
        if (hidden.has(it.cmd)) continue;
        drawChar(g, V, it.char, mul(M, twipM(it.m)), alpha * alphaOf(it.cx), depth + 1,
                 path.concat({ kind: 'place', it, parent: M, owner: id }));
      }
    } else if (o.type === 'button') {
      for (const r of o.records) {
        if (!(r.states & 1)) continue;
        drawChar(g, V, r.char, mul(M, twipM(r.m)), alpha * alphaOf(r.cx), depth + 1,
                 path.concat({ kind: 'button', rec: r, parent: M, owner: id }));
      }
    } else if (o.type === 'text') {
      const TM = mul(M, twipM(o.m));
      const TT = mul(V, TM);
      g.setTransform(...TT);
      g.globalAlpha = alpha;
      let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
      for (const run of o.runs) {
        const font = S.objects[run.font];
        if (!font || !font.sizes || !font.sizes.length) continue;
        let k = 0;
        font.sizes.forEach((b, i) => { if (Math.abs(b.size - run.size) < Math.abs(font.sizes[k].size - run.size)) k = i; });
        const b = font.sizes[k], sc = run.size / b.size;
        const atlas = tinted(run.font, k, run.color);
        let pen = run.x;
        for (const [gi, adv] of run.g) {
          const m = b.glyphs[gi];
          if (gi < 0) {
            const dx = pen / 20, dy = (run.y - run.size * 0.7) / 20;
            g.setLineDash([2, 2]); g.strokeStyle = 'rgba(230,120,40,.9)'; g.lineWidth = 0.6;
            g.strokeRect(dx + 0.5, dy, adv / 20 - 1, run.size * 0.7 / 20); g.setLineDash([]);
          } else if (m && m[2] && m[3]) {
            const dx = (pen + m[4] * run.size / 1024) / 20, dy = (run.y + m[5] * run.size / 1024) / 20;
            if (atlas) g.drawImage(atlas, m[0], m[1], m[2], m[3], dx, dy, m[2] * sc, m[3] * sc);
            bx0 = Math.min(bx0, dx); by0 = Math.min(by0, dy); bx1 = Math.max(bx1, dx + m[2] * sc); by1 = Math.max(by1, dy + m[3] * sc);
          }
          pen += adv;
        }
      }
      g.globalAlpha = 1;
      if (bx0 < Infinity) addHit(TT, bx0, by0, bx1, by1, path.concat({ kind: 'text', id, M: TM, parent: M }), id);
    } else if (o.type === 'edit') {
      const [x0, y0, w, h] = o.boxpx;
      g.setTransform(...T);
      const font = S.objects[o.font];
      if (o.text && font && font.sizes && font.sizes.length) {
        let k = 0;
        font.sizes.forEach((b, i) => { if (Math.abs(b.size - o.size) < Math.abs(font.sizes[k].size - o.size)) k = i; });
        const b = font.sizes[k], sc = o.size / b.size;
        const atlas = tinted(o.font, k, o.color);
        g.globalAlpha = alpha;
        // the box's lines: its own breaks, then words wrapped to the box (as a multi-line field does)
        const adv = (ch) => { const gi = font.chars.indexOf(ch); return b.glyphs[gi] ? b.glyphs[gi][6] * o.size / 1024 / 20 : o.size * 0.3 / 20; };
        const lines = [];
        for (const para of o.text.split(/\r\n|\r|\n/)) {
          let cur = '';
          for (const word of para.split(/(?<= )/)) {
            const tryLine = cur + word;
            if (cur && [...tryLine.trimEnd()].reduce((s, ch) => s + adv(ch), 0) > w - 4 && h > o.size / 20 * 1.6) { lines.push(cur); cur = word; } else cur = tryLine;
          }
          lines.push(cur);
        }
        lines.forEach((line, li) => {
          const gl = [...line].map((ch) => font.chars.indexOf(ch));
          const width = gl.reduce((s, gi) => s + (b.glyphs[gi] ? b.glyphs[gi][6] * o.size / 1024 : o.size * 0.3), 0) / 20;
          let pen = (x0 + 2) * 20 + (o.align === 1 ? (w - 4) * 20 - width * 20 : o.align === 2 ? ((w - 4) * 20 - width * 20) / 2 : 0);
          const base = (y0 + 2) * 20 + o.size * 0.82 + li * (o.size + o.leading);
          for (const gi of gl) {
            const m = b.glyphs[gi];
            if (m) {
              if (atlas && m[2]) g.drawImage(atlas, m[0], m[1], m[2], m[3], (pen + m[4] * o.size / 1024) / 20, (base + m[5] * o.size / 1024) / 20, m[2] * sc, m[3] * sc);
              pen += m[6] * o.size / 1024;
            } else pen += o.size * 0.3;
          }
        });
        g.globalAlpha = 1;
      }
      if (showBoxes || !o.text) {
        g.setLineDash([3, 3]); g.strokeStyle = 'rgba(40,120,220,.6)'; g.lineWidth = 1 / Math.max(1e-3, Math.hypot(T[0], T[1]));
        g.strokeRect(x0, y0, w, h); g.setLineDash([]);
        if (!o.text && o.name && (showBoxes || view.kind === 'object')) {
          g.fillStyle = 'rgba(40,120,220,.7)'; g.font = Math.max(4, h * 0.45) + 'px sans-serif';
          g.fillText(o.name, x0 + 2, y0 + h * 0.7, Math.max(1, w - 4));
        }
      }
      addHit(T, x0, y0, x0 + w, y0 + h, path, id);
    } else if (o.type === 'bitmap') {
      const im = image('bitmap', id);
      g.setTransform(...T);
      g.globalAlpha = alpha;
      if (im) g.drawImage(im, 0, 0);
      g.globalAlpha = 1;
      addHit(T, 0, 0, o.w, o.h, path, id);
    } else if (o.type === 'font') {
      let y = 0;
      g.setTransform(...T);
      o.sizes.forEach((b, k) => {
        const im = image('atlas', id, k);
        g.fillStyle = 'rgba(255,255,255,.06)';
        if (im) { g.fillRect(0, y, im.naturalWidth, im.naturalHeight); const t = tinted(id, k, '202020ff'); if (t) g.drawImage(t, 0, y); }
        if (im) { addHit(T, 0, y, im.naturalWidth, y + im.naturalHeight, path, id); y += im.naturalHeight + 8; }
      });
    }
  }

  // ------------------------------------------------------------------ the page
  let cv, wrap, tree, props, frameBar, frameSl, frameLab, hint;
  const d = panel(host, 'Flash screen: ' + key.split('/').pop() + (inMods ? ` (in ${archive}'s folder)` : ' (Workbench' + (archive ? ', from ' + archive : '') + ')'), (b) => {
    b.style.padding = '0';
    const top = el('div', 'bar');
    top.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:6px 12px';
    const textB = el('button', '', 'Text view'); textB.title = 'The text of this screen, line by line (with a sketch of each text)';
    textB.onclick = () => { if (changes.size && !confirm('Leave without saving your layout / picture changes?')) return; toText(); };
    const screenB = el('button', '', 'Whole screen'); screenB.title = 'The screen as the game shows it (its main timeline)';
    screenB.onclick = () => { view = { kind: 'screen', id: null }; frame = 0; selected = null; fitted = false; userMoved = false; syncFrames(); showProps(); markTree(); draw(); };
    const fitB = el('button', '', 'Fit'); fitB.onclick = () => { fitted = false; userMoved = false; draw(); };
    const boxes = el('input'); boxes.type = 'checkbox';
    boxes.onchange = () => { showBoxes = boxes.checked; draw(); };
    const boxLab = el('label'); boxLab.append(boxes, el('span', null, ' Show text boxes'));
    hint = el('span', 'muted', 'Click to select, drag to move; corner handles scale (Shift: keep proportions), the top knob rotates; double-click a text to edit it; the wheel zooms.');
    const impB = el('button', '', 'Import image…');
    impB.title = 'A new picture of your own (.png, .jpg, or one of the game\'s .tex textures) put on the screen (or in the sprite shown): it is saved into the file at once';
    impB.onclick = importImage;
    const ttfSel = el('select'); ttfSel.style.maxWidth = '210px';
    ttfSel.title = 'Letters a text needs that its font lacks are taken from the same typeface in the game\'s other screens; what none has is drawn from this TrueType font';
    ttfSel.append(new Option('Missing letters: other screens only', ''));
    ttfSel.onchange = () => { try { localStorage.setItem(TTF_KEY, ttfSel.value); } catch (e) { /* the view only */ } };
    (async () => {
      try { ttfList = ttfList || (await get('de_x_pck_ttfs')).fonts; } catch (e) { ttfList = []; }
      for (const f of ttfList) ttfSel.append(new Option('Missing letters from: ' + f.name, f.id));
      ttfSel.value = missingTTF() || '';
    })();
    const fillAllB = el('button', '', 'Fill every font');
    fillAllB.title = 'Gives every font of this screen every letter that can be found: the same typeface in the game\'s other screens (and the TrueType font picked, if one is). Saved at once; the file grows.';
    fillAllB.onclick = async () => {
      if (changes.size && !confirm('Your other changes are saved first. Go on?')) return;
      fillAllB.disabled = true; fillAllB.textContent = 'Filling…';
      try {
        if (changes.size) { await post('de_x_pck_apply', { where, key, changes: [...changes.values()], ttf: missingTTF() }); changes.clear(); }
        const r = await post('de_x_pck_fill_all', { where, key, ttf: missingTTF() });
        toast(r.note, 'good');
        if (onSaved) onSaved(false);
      } catch (e) { toast(e.message, 'bad'); fillAllB.disabled = false; fillAllB.textContent = 'Fill every font'; }
    };
    const afB = el('input'); afB.type = 'checkbox'; afB.checked = autofill();
    const afL = el('label'); afL.append(afB, el('span', null, ' Fill missing letters on save'));
    afL.title = 'When ticked, a letter a text needs that its font lacks is added to the font when you save (from the same typeface in the game\'s other screens, then the TrueType font picked). Unticked, such a save is refused.';
    afB.onchange = () => { try { localStorage.setItem(AUTO_KEY, afB.checked ? '1' : ''); } catch (e) { /* the view only */ } showProps(); };
    top.append(textB, screenB, fitB, boxLab, impB, fillAllB, afL, ttfSel, hint);
    const main = el('div');
    main.style.cssText = 'flex:1;min-height:0;display:flex;border-top:1px solid var(--line)';
    tree = el('div', 'scroll');
    tree.style.cssText = 'width:250px;flex:none;border-right:1px solid var(--line);font-size:12px;padding:4px 0';
    const mid = el('div');
    mid.style.cssText = 'flex:1;min-width:0;display:flex;flex-direction:column';
    wrap = el('div');
    wrap.style.cssText = 'flex:1;min-height:0;position:relative;overflow:hidden;background:repeating-conic-gradient(#3a3a3a 0 25%, #333 0 50%) 0 0/16px 16px';
    cv = el('canvas');
    cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;cursor:default';
    wrap.append(cv);
    frameBar = el('div', 'bar');
    frameBar.style.cssText = 'display:flex;gap:8px;align-items:center;padding:4px 12px;border-top:1px solid var(--line)';
    frameSl = el('input'); frameSl.type = 'range'; frameSl.min = 0; frameSl.style.flex = '1';
    frameLab = el('span', 'muted');
    frameSl.oninput = () => { frame = +frameSl.value; frameLab.textContent = `Frame ${frame + 1} of ${+frameSl.max + 1}`; selected = null; showProps(); draw(); };
    frameBar.append(el('span', 'muted', 'Timeline'), frameSl, frameLab);
    mid.append(wrap, frameBar);
    props = el('div', 'scroll');
    props.style.cssText = 'width:290px;flex:none;border-left:1px solid var(--line);padding:8px 10px;display:flex;flex-direction:column;gap:6px;font-size:12px';
    main.append(tree, mid, props);
    b.append(top, main);
    b._dirty = () => changes.size > 0;
    b._save = save;
  }, [
    { spacer: true },
    { label: 'Save', cls: inMods ? 'primary' : '', title: inMods ? `Saves it in ${archive}'s .dat folder: it goes in at the next Build` : 'Saves the Workbench copy', run: () => { save(false); return true; } },
    ...(archive && !inMods ? [{ label: 'Save and put in ' + archive, cls: 'primary', title: `Saves, and copies it into ${archive}'s .dat folder so the next Build puts it in the game`, run: () => { save(true); return true; } }] : []),
    { label: 'Close', run: (b) => (b._dirty() && !confirm('Close without saving your changes?')) },
  ], emptyNote);
  const note = (t) => { if (d.body._note) d.body._note.textContent = t; };
  const changed = () => note(changes.size ? changes.size + ' change(s), not saved' : '');

  // ------------------------------------------------------------------ the tree
  const treeRows = [];
  function buildTree() {
    tree.innerHTML = '';
    treeRows.length = 0;
    const row = (label, depth, onclick, sel) => {
      const r = el('div', 'de-wbrow');
      r.style.cssText = `padding:2px 8px 2px ${8 + depth * 14}px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis`;
      r.textContent = label; r.title = label;
      r.onclick = onclick;
      r._sel = sel;
      tree.append(r);
      treeRows.push(r);
      return r;
    };
    row(`▣ Screen (${S.root.length} frame${S.root.length === 1 ? '' : 's'}, ${Math.round(S.width)}×${Math.round(S.height)})`, 0,
        () => { view = { kind: 'screen', id: null }; frame = 0; selected = null; fitted = false; userMoved = false; syncFrames(); showProps(); markTree(); draw(); }, 'screen');
    const groups = {};
    for (const [id, o] of Object.entries(S.objects)) (groups[o.type] = groups[o.type] || []).push([+id, o]);
    for (const t of ['sprite', 'button', 'shape', 'bitmap', 'text', 'edit', 'font']) {
      const list = groups[t];
      if (!list) continue;
      const head = row(`${TYPE_LABEL[t]} (${list.length})`, 0, null, null);
      head.style.fontWeight = '600';
      const kids = [];
      let open = t === 'sprite' || t === 'bitmap' || t === 'text';
      head.textContent = (open ? '▾ ' : '▸ ') + head.textContent;
      for (const [id, o] of list) {
        let label = `${t} ${id}`;
        if (names[id]) label += ' · ' + [...names[id]].slice(0, 2).join(', ');
        if (t === 'bitmap') label += ` · ${o.w}×${o.h}`;
        if (t === 'text') label += ' · ' + o.runs.map((r) => (S.objects[r.font] && S.objects[r.font].chars ? r.g.map(([gi]) => S.objects[r.font].chars[gi] || '?').join('') : '')).join(' ').slice(0, 40);
        if (t === 'edit') label += ' · ' + (o.text ? JSON.stringify(o.text.slice(0, 30)) : (o.name || '(filled by the game)'));
        if (t === 'font') label += ` · ${o.chars.length} letters, ${o.sizes.map((s) => s.size / 20 + 'px').join('/')}`;
        if (t === 'sprite') label += ` · ${o.frames.length} frame${o.frames.length === 1 ? '' : 's'}`;
        if (o.error) label += ' · ' + o.error;
        const r = row(label, 1, () => { view = { kind: 'object', id }; frame = 0; selected = null; fitted = false; userMoved = false; syncFrames(); showProps(); markTree(); draw(); }, 'o' + id);
        r.style.display = open ? '' : 'none';
        kids.push(r);
      }
      head.onclick = () => { open = !open; head.textContent = (open ? '▾ ' : '▸ ') + head.textContent.slice(2); kids.forEach((k) => { k.style.display = open ? '' : 'none'; }); };
    }
    // the sounds: the screen's scripts play them by name from the game's front-end sound bank
    const sh = row('▸ Sounds (…)', 0, null, null);
    sh.style.fontWeight = '600';
    sh.title = 'The sounds this screen plays. A .pck holds no audio: its scripts play the game\'s sounds by name (attachSound), from its sound banks.';
    (async () => {
      try { sounds = sounds || await get('de_x_pck_sounds?' + q); } catch (e) { sh.textContent = '▸ Sounds (' + e.message + ')'; return; }
      let open = false;
      const kids = sounds.sounds.map((x) => {
        const r = row(`${x.name} · ${x.sound}` + (x.banks.length ? '' : ' (no bank defines it)'), 1,
                      () => { view = { kind: 'sound', name: x.name }; selected = null; fitted = false; syncFrames(); showProps(); markTree(); draw(); }, 's' + x.name);
        r.style.display = 'none';
        return r;
      });
      // the rows were added at the end of the tree: move them under their heading
      let at = sh;
      for (const k of kids) { at.after(k); at = k; }
      sh.textContent = '▸ Sounds (' + sounds.sounds.length + ')';
      sh.onclick = () => { open = !open; sh.textContent = (open ? '▾ ' : '▸ ') + sh.textContent.slice(2); kids.forEach((k) => { k.style.display = open ? '' : 'none'; }); };
    })();
    markTree();
  }
  function markTree() {
    const want = view.kind === 'screen' ? 'screen' : view.kind === 'sound' ? 's' + view.name : 'o' + view.id;
    for (const r of treeRows) r.classList.toggle('on', r._sel === want);
  }
  function syncFrames() {
    const frames = view.kind === 'screen' ? S.root : view.kind === 'sound' ? null : (S.objects[view.id] && S.objects[view.id].frames);
    const n = frames ? frames.length : 0;
    frameBar.style.display = n > 1 ? 'flex' : 'none';
    frameSl.max = Math.max(0, n - 1); frameSl.value = frame;
    frameLab.textContent = `Frame ${frame + 1} of ${n}`;
  }

  // ------------------------------------------------------------------ drawing
  function scene(g, V) {
    hits = [];
    if (view.kind === 'screen') {
      for (const it of displayList(S.root, frame)) {
        if (hidden.has(it.cmd)) continue;
        drawChar(g, V, it.char, twipM(it.m), alphaOf(it.cx), 0, [{ kind: 'place', it, parent: ID, owner: 0 }]);
      }
    } else if (view.kind === 'object') {
      drawChar(g, V, view.id, ID, 1, 0, []);
    }
  }
  function bounds() {
    // a dry run to find what is drawn
    const off = document.createElement('canvas').getContext('2d');
    scene(off, ID);
    if (!hits.length) return view.kind === 'screen' ? [0, 0, S.width, S.height] : [-50, -50, 50, 50];
    let x0 = Math.min(...hits.map((h) => h.x0)), y0 = Math.min(...hits.map((h) => h.y0));
    let x1 = Math.max(...hits.map((h) => h.x1)), y1 = Math.max(...hits.map((h) => h.y1));
    if (view.kind === 'screen') { x0 = Math.min(x0, 0); y0 = Math.min(y0, 0); x1 = Math.max(x1, S.width); y1 = Math.max(y1, S.height); }
    return [x0, y0, x1, y1];
  }
  function draw() {
    if (!cv.isConnected) return;
    const dpr = window.devicePixelRatio || 1;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    if (!W || !H) return;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    if (!fitted) {
      const [x0, y0, x1, y1] = bounds();
      zoom = Math.min((W - 40) / Math.max(1, x1 - x0), (H - 40) / Math.max(1, y1 - y0), 8);
      panX = W / 2 - zoom * (x0 + x1) / 2; panY = H / 2 - zoom * (y0 + y1) / 2;
      fitted = true;
    }
    const g = cv.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, cv.width, cv.height);
    const V = [zoom * dpr, 0, 0, zoom * dpr, panX * dpr, panY * dpr];
    if (view.kind === 'screen') {
      g.setTransform(...V);
      g.fillStyle = '#000'; g.fillRect(0, 0, S.width, S.height);
    }
    scene(g, V);
    g.setTransform(1, 0, 0, 1, 0, 0);
    if (view.kind === 'screen') {
      g.strokeStyle = 'rgba(255,255,255,.35)'; g.lineWidth = 1;
      g.strokeRect(panX * dpr, panY * dpr, S.width * zoom * dpr, S.height * zoom * dpr);
    }
    if (selected) {
      const h = selected.hit;
      // the selection is by its placement: show every hit that shares it
      const own = hits.filter((x) => samePlace(x.path, selected.path));
      g.strokeStyle = '#f0b030'; g.lineWidth = 2; g.setLineDash([5, 4]);
      for (const x of own.length ? own : [h]) g.strokeRect(x.x0, x.y0, x.x1 - x.x0, x.y1 - x.y0);
      g.setLineDash([]);
      gizmo = null;
      if (editableOf(selected.path)) {
        const bx = own.length ? own : [h];
        const x0 = Math.min(...bx.map((b) => b.x0)), y0 = Math.min(...bx.map((b) => b.y0));
        const x1 = Math.max(...bx.map((b) => b.x1)), y1 = Math.max(...bx.map((b) => b.y1));
        const dpr = window.devicePixelRatio || 1, hs = 5 * dpr;
        gizmo = { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, corners: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], rot: [(x0 + x1) / 2, y0 - 26 * dpr] };
        g.fillStyle = '#f0b030'; g.strokeStyle = '#000'; g.lineWidth = 1;
        for (const [x, y] of gizmo.corners) { g.fillRect(x - hs, y - hs, 2 * hs, 2 * hs); g.strokeRect(x - hs, y - hs, 2 * hs, 2 * hs); }
        g.beginPath(); g.moveTo(gizmo.rot[0], y0); g.lineTo(gizmo.rot[0], gizmo.rot[1]); g.strokeStyle = '#f0b030'; g.stroke();
        g.beginPath(); g.arc(gizmo.rot[0], gizmo.rot[1], hs * 1.2, 0, Math.PI * 2); g.fillStyle = '#f0b030'; g.fill(); g.strokeStyle = '#000'; g.stroke();
      }
    } else gizmo = null;
  }
  const editableOf = (path) => {
    if (!path) return null;
    const lvl = selected && selected.level != null ? selected.level : null;
    const cands = path.filter((p) => (p.kind === 'place' && p.it.mcmd && p.it.mcmd.m) || p.kind === 'button' || p.kind === 'text');
    if (lvl != null && path[lvl]) return path[lvl];
    return cands.length ? cands[cands.length - 1] : null;
  };
  function samePlace(a, b) {
    const ea = editableOf(a), eb = editableOf(b);
    if (!ea || !eb) return false;
    if (ea.kind === 'place' && eb.kind === 'place') return ea.it.mcmd === eb.it.mcmd && ea.it.cmd === eb.it.cmd;
    if (ea.kind === 'button' && eb.kind === 'button') return ea.rec === eb.rec;
    if (ea.kind === 'text' && eb.kind === 'text') return ea.id === eb.id && ea.M === eb.M;
    return false;
  }

  // ------------------------------------------------------------------ mouse
  let drag = null;
  let gizmo = null;
  cv.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const k = Math.exp(-e.deltaY * 0.0015);
    panX = mx - (mx - panX) * k; panY = my - (my - panY) * k; zoom *= k; userMoved = true;
    draw();
  }, { passive: false });
  cv.addEventListener('mousedown', (e) => {
    const r = cv.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    if (e.button === 1) { drag = { pan: true, x: mx, y: my, px: panX, py: panY }; e.preventDefault(); return; }
    if (e.button !== 0) return;
    const hx = mx * dpr, hy = my * dpr;
    if (gizmo && selected) {
      const near = (p) => Math.hypot(p[0] - hx, p[1] - hy) < 9 * dpr;
      const ed = editableOf(selected.path);
      const mode = near(gizmo.rot) ? 'rotate' : gizmo.corners.some(near) ? 'scale' : null;
      if (mode && ed) { drag = { mode, x: mx, y: my, cx: gizmo.cx / dpr, cy: gizmo.cy / dpr, ed, start: matOf(ed).slice(), moved: true }; return; }
    }
    let hit = null;
    for (let i = hits.length - 1; i >= 0; i--) { const h = hits[i]; if (hx >= h.x0 && hx <= h.x1 && hy >= h.y0 && hy <= h.y1) { hit = h; break; } }
    if (!hit) { selected = null; showProps(); draw(); drag = { pan: true, x: mx, y: my, px: panX, py: panY }; return; }
    if (!selected || selected.hit.path !== hit.path) selected = { hit, path: hit.path, level: null };
    showProps();
    draw();
    const ed = editableOf(selected.path);
    if (ed) drag = { x: mx, y: my, ed, start: matOf(ed).slice(), moved: false };
  });
  window.addEventListener('mousemove', (e) => {
    if (!drag || !cv.isConnected) return;
    const r = cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    if (drag.pan) { panX = drag.px + mx - drag.x; panY = drag.py + my - drag.y; if (Math.abs(mx - drag.x) + Math.abs(my - drag.y) > 2) userMoved = true; draw(); return; }
    if (drag.mode) {
      // scale / rotate about the selection's middle, in the parent's space
      const { cx, cy } = drag;
      const P = mul([zoom, 0, 0, zoom, panX, panY], drag.ed.parent || ID);
      const cp = apply(inv(P), cx, cy);
      let X;
      if (drag.mode === 'scale') {
        let sx = (mx - cx) / (Math.abs(drag.x - cx) < 1 ? 1 : drag.x - cx), sy = (my - cy) / (Math.abs(drag.y - cy) < 1 ? 1 : drag.y - cy);
        if (e.shiftKey) { const u = Math.max(Math.abs(sx), Math.abs(sy)); sx = Math.sign(sx || 1) * u; sy = Math.sign(sy || 1) * u; }
        X = [sx, 0, 0, sy, 0, 0];
      } else {
        const a = Math.atan2(my - cy, mx - cx) - Math.atan2(drag.y - cy, drag.x - cx);
        X = [Math.cos(a), Math.sin(a), -Math.sin(a), Math.cos(a), 0, 0];
      }
      const n = mul(mul([1, 0, 0, 1, cp[0], cp[1]], mul(X, [1, 0, 0, 1, -cp[0], -cp[1]])), twipM(drag.start));
      const m = [n[0], n[1], n[2], n[3], n[4] * 20, n[5] * 20];
      if (drag.ed.kind === 'place') { m[4] = Math.round(m[4]); m[5] = Math.round(m[5]); }
      setMat(drag.ed, m);
      draw();
      return;
    }
    const dx = (mx - drag.x) / zoom, dy = (my - drag.y) / zoom;
    if (!drag.moved && Math.hypot(mx - drag.x, my - drag.y) < 3) return;
    drag.moved = true;
    // the move in the placement's parent space
    const P = inv(drag.ed.parent || ID);
    const lx = P[0] * dx + P[2] * dy, ly = P[1] * dx + P[3] * dy;
    const m = drag.start.slice();
    if (drag.ed.kind === 'place' || drag.ed.kind === 'button' || drag.ed.kind === 'text') { m[4] = drag.start[4] + lx * 20; m[5] = drag.start[5] + ly * 20; }
    if (drag.ed.kind === 'place') { m[4] = Math.round(m[4]); m[5] = Math.round(m[5]); }
    setMat(drag.ed, m);
    draw();
  });
  window.addEventListener('mouseup', () => { if (drag && drag.moved) showProps(); drag = null; });
  cv.addEventListener('dblclick', () => {
    const t = props.querySelector('[data-textedit]');
    if (t) { t.focus(); t.select && t.select(); }
  });
  new ResizeObserver(() => draw()).observe(wrap);

  // ------------------------------------------------------------------ editing
  function matOf(ed) {
    if (ed.kind === 'place') return ed.it.mcmd.m;
    if (ed.kind === 'button') return ed.rec.m;
    if (ed.kind === 'text') return S.objects[ed.id].m;
    return ID;
  }
  function setMat(ed, m) {
    if (ed.kind === 'place') {
      const c = ed.it.mcmd;
      c.m = m;
      changes.set('place' + c.at, { kind: 'place', at: c.at, m });
    } else if (ed.kind === 'button') {
      ed.rec.m = m;
      changes.set('button' + ed.rec.at, { kind: 'button', at: ed.rec.at, m });
    } else if (ed.kind === 'text') {
      S.objects[ed.id].m = m;
      changes.set('textm' + ed.id, { kind: 'textm', id: ed.id, m });
    }
    changed();
  }

  function field(label, value, onset, opts) {
    const r = el('label');
    r.style.cssText = 'display:flex;gap:6px;align-items:center';
    const s = el('span', 'muted', label); s.style.width = '90px';
    const i = el('input'); i.type = 'number'; i.step = (opts && opts.step) || '1'; i.value = value; i.style.width = '100px';
    if (opts && opts.disabled) { i.disabled = true; i.title = opts.disabled; }
    i.onchange = () => { const v = parseFloat(i.value); if (!isNaN(v)) onset(v); };
    r.append(s, i);
    if (opts && opts.unit) r.append(el('span', 'muted', opts.unit));
    return r;
  }
  function describe(id) {
    const o = S.objects[id];
    if (!o) return 'nothing';
    return o.type + ' ' + id + (names[id] ? ' (' + [...names[id]].join(', ') + ')' : '');
  }

  function showProps() {
    props.innerHTML = '';
    if (selected) {
      const path = selected.path;
      const leaf = selected.hit.id;
      props.append(el('b', null, 'Selected: ' + describe(leaf)));
      // where it sits: every level that can move, outermost first
      const levels = path.map((p, i) => [p, i]).filter(([p]) => (p.kind === 'place' && p.it.mcmd && p.it.mcmd.m) || p.kind === 'button' || p.kind === 'text');
      if (levels.length > 1) {
        props.append(el('span', 'muted', 'Move it at the level of:'));
        const sel = el('select');
        const ed0 = editableOf(path);
        levels.forEach(([p, i]) => {
          const lab = p.kind === 'place' ? `${describe(p.it.char)} in ${p.owner ? 'sprite ' + p.owner : 'the screen'} (depth ${p.it.depth})`
            : p.kind === 'button' ? `${describe(p.rec.char)} in button ${p.owner}` : `text ${p.id} itself`;
          const op = new Option(lab, i);
          if (p === ed0) op.selected = true;
          sel.append(op);
        });
        sel.onchange = () => { selected.level = +sel.value; showProps(); draw(); };
        props.append(sel);
      }
      const ed = editableOf(path);
      const hp = [...path].reverse().find((p) => p.kind === 'place');
      if (hp) {
        const hb = el('button', 'ghost', 'Hide it (while editing)');
        hb.title = 'Hides this placement in the view only, to get at what is under it (the game hides popups like this by script). Not saved.';
        hb.onclick = () => { hidden.add(hp.it.cmd); selected = null; showProps(); draw(); };
        props.append(hb);
      }
      if (!ed) {
        props.append(el('div', 'muted', 'This is placed without a matrix of its own: it cannot be moved here.'));
      } else {
        const m = matOf(ed);
        const isPlace = ed.kind === 'place';
        props.append(el('div', 'muted', isPlace ? `Transform (placement at depth ${ed.it.depth}${ed.it.cmd.name ? ', named ' + ed.it.cmd.name : ''})` : ed.kind === 'button' ? 'Transform (button record)' : 'Transform (the text\'s matrix)'));
        const sx = Math.hypot(m[0], m[1]) || 1e-6, rot = Math.atan2(m[1], m[0]);
        const sy = (m[0] * m[3] - m[1] * m[2]) / sx;
        const compose = (nsx, nsy, nrot) => { const n = m.slice(); n[0] = nsx * Math.cos(nrot); n[1] = nsx * Math.sin(nrot); n[2] = -nsy * Math.sin(nrot); n[3] = nsy * Math.cos(nrot); setMat(ed, n); draw(); showProps(); };
        props.append(field('X', +(m[4] / 20).toFixed(2), (v) => { const n = m.slice(); n[4] = isPlace ? Math.round(v * 20) : v * 20; setMat(ed, n); draw(); }, { step: '0.05', unit: 'px' }));
        props.append(field('Y', +(m[5] / 20).toFixed(2), (v) => { const n = m.slice(); n[5] = isPlace ? Math.round(v * 20) : v * 20; setMat(ed, n); draw(); }, { step: '0.05', unit: 'px' }));
        props.append(field('Scale X', +(sx * 100).toFixed(2), (v) => compose(v / 100, sy, rot), { step: '1', unit: '%' }));
        props.append(field('Scale Y', +(sy * 100).toFixed(2), (v) => compose(sx, v / 100, rot), { step: '1', unit: '%' }));
        props.append(field('Rotation', +(rot * 180 / Math.PI).toFixed(2), (v) => compose(sx, sy, v * Math.PI / 180), { step: '1', unit: '°' }));
        const tb = el('div'); tb.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
        const flipH = el('button', '', 'Flip ↔'); flipH.onclick = () => compose(-sx, sy, rot);
        const flipV = el('button', '', 'Flip ↕'); flipV.onclick = () => compose(sx, -sy, rot);
        const reset = el('button', 'ghost', 'Reset scale / rotation'); reset.onclick = () => compose(1, 1, 0);
        tb.append(flipH, flipV, reset);
        props.append(tb);
        if (isPlace) props.append(el('div', 'muted', 'Stored as the game stores it: whole twips (1/20 px), scale and rotation in steps of 1/256. A placement the game stored without scale or rotation gets a new matrix that has them (the file grows a few bytes).'));
      }
      const o = S.objects[leaf];
      if (o && o.type === 'text') props.append(textBox(leaf));
      if (o && o.type === 'edit') props.append(editBox(leaf));
      const fillsOf = o && o.type === 'shape' ? [...new Set(o.fills.map((f) => f.bitmap).filter((x) => x != null))] : [];
      for (const bid of fillsOf) props.append(pictureBox(bid));
      return;
    }
    if (view.kind === 'sound' && sounds) {
      props.append(soundBox(sounds.sounds.find((x) => x.name === view.name)));
      return;
    }
    if (view.kind === 'object') {
      const o = S.objects[view.id];
      props.append(el('b', null, describe(view.id)));
      if (o.type === 'bitmap') props.append(pictureBox(view.id));
      if (o.type === 'font') {
        props.append(el('div', 'muted', `${o.chars.length} letters; atlases (the letters as the game draws them) for ${o.sizes.map((s) => s.size / 20 + ' px').join(', ')}.`));
        const c = el('div'); c.style.cssText = 'font:var(--t-mono);word-break:break-all;background:var(--well);padding:6px';
        c.textContent = o.chars; props.append(c);
        props.append(fillBox(view.id));
      }
      if (o.type === 'sprite') props.append(el('div', 'muted', `${o.frames.length} frame(s). Click a thing in it to move it.`));
      if (o.type === 'shape') {
        for (const bid of [...new Set(o.fills.map((f) => f.bitmap).filter((x) => x != null))]) props.append(pictureBox(bid));
        props.append(el('div', 'muted', `${o.cmds.length} polygon(s), ${o.fills.length} fill(s), ${o.strokes.length} line style(s).`));
      }
      return;
    }
    props.append(el('b', null, 'The screen'));
    if (hidden.size) {
      const sb = el('button', '', `Show the ${hidden.size} hidden thing(s) again`);
      sb.onclick = () => { hidden.clear(); showProps(); draw(); };
      props.append(sb);
    }
    props.append(el('div', 'muted', `${Math.round(S.width)} × ${Math.round(S.height)}, ${S.root.length} frame(s). Click a thing on it to select it; drag it to move it. The tree on the left has every object: sprites, buttons, shapes, the pictures (export, replace), texts, fonts.`));
    props.append(el('div', 'muted', 'Edit texts the game fills in (scores, names, button prompts) show as their boxes only; tick "Show text boxes" to see them all.'));
  }

  // ------------------------------------------------------------------ text, edited where it is
  const runText = (run) => { const f = S.objects[run.font]; return f && f.chars ? run.g.map(([gi]) => (gi >= 0 ? f.chars[gi] || '\ufffd' : '?')).join('') : ''; };
  function guessAlign(runs) {
    if (runs.length < 2) return 'left';
    const w = (r) => r.g.reduce((s, x) => s + x[1], 0);
    const spread = (v) => Math.max(...v) - Math.min(...v);
    const l = spread(runs.map((r) => r.x)), c = spread(runs.map((r) => r.x + w(r) / 2)), r = spread(runs.map((r_) => r_.x + w(r_)));
    return Math.min(l, c, r) === l ? 'left' : Math.min(l, c, r) === c ? 'center' : 'right';
  }
  function nearBlock(font, size) {
    let k = 0;
    font.sizes.forEach((b, i) => { if (Math.abs(b.size - size) < Math.abs(font.sizes[k].size - size)) k = i; });
    return font.sizes[k];
  }
  // the lines re-laid as the server will lay them: the font's advances plus the line's own letter spacing
  function relay(id, lines, align) {
    const o = S.objects[id];
    o.runs = origRuns[id].map((r, i) => {
      const want = lines[i] != null ? lines[i] : runText(r);
      if (want === runText(r)) return JSON.parse(JSON.stringify(r));
      const font = S.objects[r.font];
      const b = nearBlock(font, r.size);
      const fa = (gi) => (b.glyphs[gi] ? Math.round(b.glyphs[gi][6] * r.size / 1024) : Math.round(r.size * 0.5));
      const ds = r.g.map(([gi, a]) => a - fa(gi)).sort((x, y) => x - y);
      const sp = ds.length ? ds[ds.length >> 1] : 0;
      const g = [];
      for (const ch of want) {
        const gi = font.chars.indexOf(ch);
        if (gi < 0 && ch === ' ') { if (g.length) g[g.length - 1][1] += Math.round(r.size * 0.27); continue; }
        g.push([gi, gi < 0 ? Math.round(r.size * 0.55) : Math.max(0, fa(gi) + sp)]);
      }
      const ow = r.g.reduce((s, x) => s + x[1], 0), nw = g.reduce((s, x) => s + x[1], 0);
      const dx = align === 'center' ? Math.floor((ow - nw) / 2) : align === 'right' ? ow - nw : 0;
      return { ...r, x: r.x + dx, g };
    });
  }
  function missingNote(chars) {
    const d_ = el('div');
    d_.style.cssText = 'color:var(--warn, #d9a33a);font-size:12px';
    if (chars.length) d_.textContent = autofill()
      ? `Not in its font yet: ${chars.join(' ')} — added when you save, from the same typeface in the game's other screens${missingTTF() ? ', else from the TrueType font picked above' : ' (pick a TrueType font above for any no screen has)'}.`
      : `Not in its font: ${chars.join(' ')} — tick "Fill missing letters on save" above to add them, else the save is refused.`;
    return d_;
  }
  function textBox(id) {
    const o = S.objects[id];
    const box = el('div');
    box.style.cssText = 'border:1px solid var(--line);padding:6px;display:flex;flex-direction:column;gap:4px;margin-top:4px';
    box.append(el('b', null, 'Text'));
    const cur = changes.get('text' + id);
    const orig = origRuns[id];
    const alignSel = el('select');
    for (const [v, l] of [['left', 'Lines keep their left edge'], ['center', 'Lines stay centred'], ['right', 'Lines keep their right edge']]) alignSel.append(new Option(l, v));
    const guessed = guessAlign(orig);
    alignSel.value = cur ? cur.align : guessed;
    box.append(alignSel);
    const note = el('div');
    const inputs = orig.map((r, i) => {
      const inp = el('input'); inp.value = cur ? cur.lines[i] : runText(r); inp.style.width = '100%';
      if (i === 0) inp.dataset.textedit = '1';
      box.append(inp);
      return inp;
    });
    const sync = () => {
      const lines = inputs.map((x) => x.value);
      const same = lines.every((v, i) => v === runText(orig[i])) && alignSel.value === guessed;
      if (same) changes.delete('text' + id); else changes.set('text' + id, { kind: 'text', id: +id, lines, align: alignSel.value });
      relay(id, lines, alignSel.value);
      const miss = new Set();
      orig.forEach((r, i) => { const f = S.objects[r.font]; for (const ch of lines[i]) if (ch !== ' ' && !(f.chars || '').includes(ch)) miss.add(ch); });
      note.replaceChildren(missingNote([...miss]));
      changed(); draw();
    };
    inputs.forEach((x) => { x.oninput = sync; });
    alignSel.onchange = sync;
    box.append(note, el('div', 'muted', 'No length limit: a text longer than its room moves to the end of the file (it grows). One box per line, as the screen has them.'));
    return box;
  }
  function editBox(id) {
    const o = S.objects[id];
    const box = el('div');
    box.style.cssText = 'border:1px solid var(--line);padding:6px;display:flex;flex-direction:column;gap:4px;margin-top:4px';
    box.append(el('b', null, 'Edit text'));
    if (!origEdit[id]) box.append(el('div', 'muted', `The game fills this one in (variable "${o.name || '?'}"); a text typed here is its starting text, which the game may still replace.`));
    const ta = el('textarea'); ta.value = o.text || ''; ta.dataset.textedit = '1';
    ta.style.cssText = 'width:100%;height:70px;resize:vertical;background:var(--well);color:var(--ink);border:1px solid var(--line);padding:4px';
    const note = el('div');
    ta.oninput = () => {
      o.text = ta.value;
      if (ta.value === (origEdit[id] || '')) changes.delete('edit' + id); else changes.set('edit' + id, { kind: 'edit', id: +id, text: ta.value });
      const f = S.objects[o.font];
      note.replaceChildren(missingNote([...new Set([...ta.value].filter((ch) => !'\n\r\t'.includes(ch) && !((f && f.chars) || '').includes(ch)))]));
      changed(); draw();
    };
    box.append(ta, note);
    return box;
  }

  // a picked picture file -> { b64: PNG base64, url } (a game .tex is converted to a .png first)
  async function pictureFile(file) {
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = '';
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    let b64 = btoa(bin);
    if (/\.tex$/i.test(file.name)) b64 = (await post('de_x_tex_to_png', { data: b64 })).png;
    const raw = atob(b64), arr = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return { b64, url: URL.createObjectURL(new Blob([arr], { type: 'image/png' })) };
  }

  // ------------------------------------------------------------------ a new image of your own
  function importImage() {
    const f = el('input'); f.type = 'file'; f.accept = '.png,image/png,.jpg,.jpeg,.tex';
    f.onchange = async () => {
      const file = f.files[0];
      if (!file) return;
      let pic;
      try { pic = await pictureFile(file); } catch (e) { return toast(e.message, 'bad'); }
      // where: the sprite shown (its frame), else the screen; the middle of what is in view
      const parent = view.kind === 'object' && S.objects[view.id] && S.objects[view.id].type === 'sprite' ? view.id : 0;
      const W = wrap.clientWidth, H = wrap.clientHeight;
      const c = apply(inv([zoom, 0, 0, zoom, panX, panY]), W / 2, H / 2);
      if (view.kind === 'object' && !parent) return toast('Show the whole screen or a sprite to put an image in', 'bad');
      try {
        const r = await post('de_x_pck_apply', { where, key, ttf: missingTTF(),
          changes: [...changes.values(), { kind: 'add_image', png: pic.b64, parent, frame, x: c[0], y: c[1] }] });
        changes.clear();
        toast(r.note + ' — the image is the new top thing ' + (parent ? 'in sprite ' + parent : 'on the screen') + '; select it to move or scale it.', 'good');
        if (onSaved) onSaved(false);
      } catch (e) { toast(e.message, 'bad'); }
    };
    f.click();
  }

  // Filling a font with the letters it lacks (rdrstudio/pckfont.py): the same typeface from the game's other
  // screens first, then a TrueType font for the rest. It grows the file, so it is written at once, not queued.
  function fillBox(fid) {
    const box = el('div');
    box.style.cssText = 'border:1px solid var(--line);padding:6px;display:flex;flex-direction:column;gap:6px;margin-top:6px';
    box.append(el('b', null, 'Fill this font'));
    box.append(el('div', 'muted', 'Gives it the letters it lacks: first the same typeface at the same size from the game\'s other screens (letter for letter as the game has them), then what is still missing drawn from a TrueType font you pick. The screen\'s file grows by the new atlas: test it in the game.'));
    const sel = el('select'); sel.style.width = '100%';
    sel.append(new Option('No TrueType font: the other screens\' letters only', ''));
    const addB = el('button', '', 'Add a font file…');
    addB.title = 'A .ttf or .otf of your own (it is kept in the Data Editor\'s Fonts folder)';
    const sc = el('input'); sc.type = 'checkbox';
    const scLab = el('label'); scLab.append(sc, el('span', null, ' Lowercase as small capitals'));
    scLab.title = 'Draws the new lowercase letters as capitals as tall as the font\'s own lowercase (title fonts are often like this)';
    const prevB = el('button', '', 'Preview');
    const fillB = el('button', 'primary', 'Fill');
    fillB.disabled = true;
    const out = el('div'); out.style.cssText = 'display:flex;flex-direction:column;gap:4px';
    const row = el('div'); row.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap'; row.append(addB, prevB, fillB);
    box.append(el('span', 'muted', 'TrueType font for what the other screens do not have:'), sel, scLab, row, out);
    let scTouched = false;
    sc.onchange = () => { scTouched = true; fillB.disabled = true; };
    sel.onchange = () => { fillB.disabled = true; };
    const loadList = async (pick) => {
      if (!ttfList) { try { ttfList = (await get('de_x_pck_ttfs')).fonts; } catch (e) { ttfList = []; } }
      while (sel.options.length > 1) sel.remove(1);
      for (const f of ttfList) sel.append(new Option(f.name + (f.id.startsWith('added:') ? ' (added)' : ''), f.id));
      if (pick) sel.value = pick;
    };
    loadList();
    addB.onclick = () => {
      const f = el('input'); f.type = 'file'; f.accept = '.ttf,.otf';
      f.onchange = async () => {
        const file = f.files[0];
        if (!file) return;
        const buf = new Uint8Array(await file.arrayBuffer());
        let bin = '';
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        try { const r = await post('de_x_pck_ttf_add', { name: file.name, data: btoa(bin) }); ttfList = null; await loadList(r.id); fillB.disabled = true; } catch (e) { toast(e.message, 'bad'); }
      };
      f.click();
    };
    const body = () => ({ where, key, id: fid, ttf: sel.value || null, smallcaps: scTouched ? sc.checked : null });
    prevB.onclick = async () => {
      out.innerHTML = '';
      out.append(el('span', 'muted', 'Looking through the game\'s screens…'));
      try {
        const r = await post('de_x_pck_font_plan', body());
        out.innerHTML = '';
        if (!scTouched) sc.checked = !!r.small_caps;
        for (const s of r.sizes) {
          const fromS = Object.keys(s.from_screens);
          const where_ = [...new Set(Object.values(s.from_screens).map((n) => n.split('/').pop()))].slice(0, 4).join(', ');
          out.append(el('div', null, `${s.size / 20} px: has ${s.have.length}` + (fromS.length ? ` · ${fromS.length} from other screens (${fromS.sort().join('')}) — ${where_}` : ' · none from other screens')
            + (s.from_ttf ? ` · ${s.from_ttf.length} drawn from the TrueType font` : '') + (s.missing ? ` · still missing: ${s.missing}` : '')));
        }
        if (r.nothing) { out.append(el('div', 'muted', r.nothing)); return; }
        out.append(el('div', 'muted', `After: ${r.chars_after.length} letters; the file grows ${Math.ceil(r.grows / 1024)} KB. The new atlas (${r.sizes[0].size / 20} px):`));
        const im = el('img'); im.src = r.preview;
        im.style.cssText = 'max-width:100%;background:#c8b994;image-rendering:pixelated';
        out.append(im);
        fillB.disabled = false;
      } catch (e) { out.innerHTML = ''; out.append(el('div', 'muted', e.message)); }
    };
    fillB.onclick = async () => {
      if (changes.size && !confirm('Filling saves the font into the file now; your other changes are saved first. Go on?')) return;
      try {
        if (changes.size) { await post('de_x_pck_apply', { where, key, changes: [...changes.values()] }); changes.clear(); }
        const r = await post('de_x_pck_font_fill', body());
        toast(r.note + (inMods ? '' : '. Save and put in ' + archive + ' to take it to the game.'), 'good');
        if (onSaved) onSaved(false);
      } catch (e) { toast(e.message, 'bad'); }
    };
    return box;
  }

  // ------------------------------------------------------------------ sounds (the front-end bank's)
  function soundBox(x) {
    const box = el('div');
    box.style.cssText = 'display:flex;flex-direction:column;gap:6px';
    if (!x) return box;
    box.append(el('b', null, x.name));
    box.append(el('div', null, `Plays ${x.sound}`));
    box.append(el('div', 'muted', `From this screen's script: ${x.name} = new Sound(); ${x.name}.attachSound("${x.sound}"). A .pck holds no audio of its own: the name is one of the game's sounds, in its sound banks.`));
    if (!x.banks.length) {
      box.append(el('div', 'muted', 'No bank definition (snd_*.csv) names this sound.'));
      return box;
    }
    // the banks that define it: where a replacement goes
    const picks = x.banks.map((b) => {
      const c = el('input'); c.type = 'checkbox'; c.checked = true;
      const l = el('label'); l.style.cssText = 'display:flex;gap:6px;align-items:flex-start';
      l.append(c, el('span', null, `${b.bd.replace('.bd', '')} — wave ${b.index}${b.note ? ' (' + b.note + ')' : ''}`));
      box.append(l);
      return { b, c };
    });
    if (x.banks.length > 1) box.append(el('div', 'muted', 'More than one bank defines it (which the game has loaded depends on where you are): a replacement goes into the ticked ones.'));
    // another sound of the first bank, to hear or to replace instead
    const first = x.banks[0].bd;
    const rows = (sounds.bank_rows[first] || []);
    const sel = el('select'); sel.style.width = '100%';
    for (const r of rows) sel.append(new Option(`${r.name} (wave ${r.index})${r.note ? ' — ' + r.note : ''}`, r.name));
    sel.value = rows.some((r) => r.name === x.banks[0].name) ? x.banks[0].name : (rows[0] || {}).name;
    box.append(el('span', 'muted', `Sound (from ${first.replace('.bd', '')}): the one it plays is selected; pick another to hear it or replace it instead`), sel);
    const targetIn = (bd) => (sounds.bank_rows[bd] || []).find((r) => r.name.toLowerCase() === String(sel.value).toLowerCase());
    const au = el('audio'); au.controls = true; au.style.width = '100%';
    const exB = el('a', 'btn', 'Export .wav');
    exB.style.cssText = 'padding:3px 8px;border:1px solid var(--line);border-radius:4px;text-decoration:none;color:var(--ink)';
    const load = () => {
      const r = targetIn(first);
      if (!r) return;
      au.src = `/api/sound_wav?archive=BANKS.DAT&name=${first}&index=${r.index}&v=${version}`;
      exB.href = `/api/sound_wav?archive=BANKS.DAT&name=${first}&index=${r.index}`;
      exB.download = `${r.name}.wav`;
    };
    sel.onchange = load;
    load();
    const playB = el('button', '', '▶ Play'); playB.onclick = () => au.play();
    const repB = el('button', 'primary', 'Replace with a .wav…');
    repB.title = 'Your .wav in place of the selected sound, in the ticked banks, on the working disc at once. It must not be longer than the sound it replaces (a bank is patched in place). Everything that plays it changes.';
    repB.onclick = () => {
      const f = el('input'); f.type = 'file'; f.accept = '.wav,audio/wav';
      f.onchange = async () => {
        const file = f.files[0];
        if (!file) return;
        const targets = picks.filter((p) => p.c.checked).map((p) => ({ bd: p.b.bd, row: targetIn(p.b.bd) })).filter((t) => t.row);
        if (!targets.length) return toast('Tick a bank that has the selected sound', 'bad');
        if (!confirm(`Replace ${sel.value} with ${file.name} in ${targets.map((t) => t.bd.replace('.bd', '')).join(', ')}?\n\nIt is written into the game's sound banks on the working disc now, for everything that plays it.`)) return;
        const buf = new Uint8Array(await file.arrayBuffer());
        let bin = '';
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        const done = [];
        for (const t of targets) {
          try {
            const r = await post('sound_import', { archive: 'BANKS.DAT', name: t.bd, index: t.row.index, wav_b64: btoa(bin) });
            done.push(`${t.bd.replace('.bd', '')} wave ${t.row.index}: ${r.encoded_bytes} of ${r.slot_bytes} bytes`);
          } catch (e) { toast(`${t.bd}: ${e.message}`, 'bad'); return; }
        }
        version = Date.now();
        toast(`Replaced ${sel.value} (${done.join('; ')}).`, 'good');
        load();
      };
      f.click();
    };
    const row_ = el('div'); row_.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap'; row_.append(playB, exB, repB);
    box.append(au, row_);
    box.append(el('div', 'muted', 'Reset to stock puts the stock banks back. MEDIA > Audio has the same banks.'));
    return box;
  }

  function pictureBox(bid) {
    const o = S.objects[bid];
    const box = el('div');
    box.style.cssText = 'border:1px solid var(--line);padding:6px;display:flex;flex-direction:column;gap:6px';
    box.append(el('b', null, `Bitmap ${bid} · ${o.w}×${o.h} · ${o.colors || '?'} colours${replaced.has(bid) ? ' · replaced (not saved)' : ''}`));
    const pv = el('img');
    pv.src = replaced.get(bid) || `/api/de_x_pck_image?${q}&kind=bitmap&id=${bid}&v=${version}`;
    pv.style.cssText = 'max-width:100%;max-height:200px;object-fit:contain;align-self:flex-start;background:repeating-conic-gradient(#555 0 25%, #444 0 50%) 0 0/12px 12px';
    const row = el('div'); row.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
    const ex = el('a', 'btn', 'Export PNG');
    ex.href = `/api/de_x_pck_image?${q}&kind=bitmap&id=${bid}&v=${version}`;
    ex.download = `${key.split('/').pop().replace(/\.pck$/i, '')}_bitmap${bid}.png`;
    ex.style.cssText = 'padding:3px 8px;border:1px solid var(--line);border-radius:4px;text-decoration:none;color:var(--ink)';
    const modeSel = el('select');
    modeSel.append(new Option('Made the old picture\'s size (the file keeps its size)', 'resize'));
    modeSel.append(new Option('Its own resolution, where the old one was', 'fit'));
    modeSel.append(new Option('Its own size (what shows it is resized to match)', 'own'));
    const pend = changes.get('bitmap' + bid);
    modeSel.value = pend && pend.mode ? pend.mode : 'resize';
    modeSel.onchange = () => { const c = changes.get('bitmap' + bid); if (c) { c.mode = modeSel.value; changed(); } };
    box.append(modeSel);
    const rep = el('button', '', 'Replace…');
    rep.title = 'A picture of your own in its place (.png, .jpg, or one of the game\'s .tex textures), as chosen above (any size; reduced to the game\'s colours)';
    rep.onclick = () => {
      const f = el('input'); f.type = 'file'; f.accept = '.png,image/png,.jpg,.jpeg,.tex';
      f.onchange = async () => {
        const file = f.files[0];
        if (!file) return;
        let pic;
        try { pic = await pictureFile(file); } catch (e) { return toast(e.message, 'bad'); }
        changes.set('bitmap' + bid, { kind: 'bitmap', id: bid, png: pic.b64, mode: modeSel.value });
        replaced.set(bid, pic.url);
        changed(); showProps(); draw();
      };
      f.click();
    };
    row.append(ex, rep);
    if (replaced.has(bid)) {
      const undo = el('button', 'ghost', 'Undo');
      undo.onclick = () => { changes.delete('bitmap' + bid); replaced.delete(bid); changed(); showProps(); draw(); };
      row.append(undo);
    }
    box.append(pv, row);
    return box;
  }

  async function save(putIn) {
    const list = [...changes.values()];

    try {
      const r = await post('de_x_pck_apply', { where, key, changes: list, put_in: putIn ? archive : null, ttf: missingTTF(), autofill: autofill() });
      toast(r.note, 'good');
      changes.clear();
      if (onSaved) onSaved(putIn);
    } catch (e) { toast(e.message, 'bad'); }
  }

  // Up / Down: the previous / next item of the tree, shown (not while typing in a box)
  const onKey = (e) => {
    if (!tree.isConnected) { document.removeEventListener('keydown', onKey); return; }
    if ((e.key !== 'ArrowUp' && e.key !== 'ArrowDown') || e.altKey || e.ctrlKey || e.metaKey) return;
    if (!tree.offsetParent) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    const rows = [...tree.querySelectorAll('.de-wbrow')].filter((r) => r._sel && r.style.display !== 'none');
    if (!rows.length) return;
    const cur = rows.findIndex((r) => r.classList.contains('on'));
    const next = cur < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, cur + (e.key === 'ArrowDown' ? 1 : -1)));
    e.preventDefault();
    if (next === cur) return;
    rows[next].scrollIntoView({ block: 'nearest' });
    rows[next].click();
  };
  document.addEventListener('keydown', onKey);

  buildTree();
  syncFrames();
  showProps();
  requestAnimationFrame(draw);
  return d;
}
