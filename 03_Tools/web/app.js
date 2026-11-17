import * as THREE from 'three';
import { OrbitControls } from './OrbitControls.js';
import { TransformControls } from './TransformControls.js';
import { createAnimPlayer, parseSkeleton } from './anim3d.js';

// ---------------------------------------------------------------- utilities

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};
// A dropped connection used to look exactly like "the disc vanished": every
// panel rendered empty with no explanation. Route every call through one
// place so the banner can say what actually happened.
function setOffline(on) {
  const b = $('#offline');
  if (b) b.classList.toggle('on', on);
}

// An edit with no room in its slot is queued for its archive's rebuild, which
// happens on the next Play ("Rebuild and play"). The server says so on the
// response (X-RDR-Rebuild-Queued), whichever tab or module made the edit.
{
  const _fetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const r = await _fetch(...args);
    try {
      const q = r.headers.get('X-RDR-Rebuild-Queued');
      if (q) setTimeout(() => rebuildQueued(q.split(',').filter(Boolean)), 0);
    } catch { /* never in the way of the call */ }
    return r;
  };
}

let rebuildNoteTimer = null;
function rebuildQueued(archives) {
  let n = document.getElementById('rebuildNote');
  if (!n) {
    n = document.createElement('div');
    n.id = 'rebuildNote';
    n.setAttribute('role', 'alert');
    n.style.cssText = 'position:fixed;left:50%;top:58px;transform:translateX(-50%);z-index:95;max-width:620px;'
      + 'background:var(--bg2);border:1px solid #d9a441;border-left:5px solid #d9a441;border-radius:8px;'
      + 'padding:10px 34px 10px 14px;font-size:12.5px;box-shadow:0 6px 24px rgba(0,0,0,.45);color:var(--fg,inherit)';
    const x = document.createElement('button');
    x.className = 'ghost';
    x.textContent = '×';
    x.title = 'Dismiss';
    x.style.cssText = 'position:absolute;right:6px;top:4px;font-size:15px;padding:0 6px';
    x.onclick = () => { n.style.display = 'none'; };
    const t = document.createElement('div');
    t.id = 'rebuildNoteText';
    n.append(t, x);
    document.body.append(n);
  }
  const names = archives.map((a) => a.split(/[\\/]/).pop()).join(', ');
  n.querySelector('#rebuildNoteText').textContent = (archives.length === 1
    ? `${names} has been updated and will rebuild on next launch.`
    : `${names} have been updated and will rebuild on next launch.`)
    + ' The change needs more room than its old space; keep editing, and Rebuild and play puts every queued change on the disc in one go.';
  n.style.display = 'block';
  clearTimeout(rebuildNoteTimer);
  rebuildNoteTimer = setTimeout(() => { n.style.display = 'none'; }, 12000);
  if (typeof refreshDiscState === 'function') refreshDiscState();
}

const api = async (route, opts) => {
  let r;
  try {
    r = await fetch('/api/' + route, opts);
  } catch (e) {
    setOffline(true);
    throw new Error('the Studio is not answering — it may be restarting');
  }
  setOffline(false);
  const j = await r.json();
  if (j && j.error) throw new Error(j.error);
  return j;
};
const post = (route, body) =>
  api(route, { method: 'POST', headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify(body) });

// -------------------------------------------------------------- preferences
//
// Theme, View settings and Game lighting, remembered between launches.
//
// Testers asked for all three, and they were in three different states. Game
// lighting was not remembered at all -- its boxes are declared `checked` in
// the markup and nothing ever read them back, so every launch turned all
// three on again. Four View settings (World, Surfaces, Solidity, and the
// track layout) were plain variables that went back to their defaults on
// reload. The rest lived in localStorage, which is one browser profile on one
// machine, and which the app itself cannot read.
//
// They live in the Studio's own settings.json now -- see rdrstudio/settings.py
// for the file and its rules. localStorage is KEPT as a local mirror for one
// reason: the saved theme is replayed by a synchronous <head> script before
// the first paint, and no fetch can happen that early. The mirror stops the
// palette flashing; the file is what survives a cleared cache, a different
// browser, or a packaged build whose webview starts with a fresh profile.
let prefsReady = false;      // true once the first GET has been applied
let prefsQueue = null;       // changed, not yet accepted by the server
let prefsTimer = null;

// Called by each setter beside the localStorage write it already did. Silent
// until boot has finished applying, so replaying the saved state does not
// immediately save it back.
function remember(key, value) {
  if (!prefsReady) return;
  prefsQueue = prefsQueue || {};
  prefsQueue[key] = value;
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(flushPrefs, 250);
}

async function flushPrefs() {
  if (!prefsQueue) return;
  const body = prefsQueue;
  prefsQueue = null;
  try {
    await post('settings', { settings: body });
  } catch {
    // The Studio is restarting or gone. Keep the change and retry rather
    // than dropping it: a preference that appears to save and then does not
    // come back is the exact complaint this feature exists to answer. Newer
    // values win, so a retry cannot resurrect a stale one.
    prefsQueue = Object.assign(body, prefsQueue || {});
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(flushPrefs, 5000);
  }
}

// Put the saved answers into the live variables.
//
// Deliberately assigns rather than calling the setters. At this point there
// is no level, no camera pivot worth keeping and no track to lay out, and
// three of the setters do exactly those things -- setTrackLayout even
// toasts. Assigning is enough because the variables are read again when
// geometry is built: the collision path reads `worldMode` as it builds each
// mesh, and the render path calls `setRenderMode(renderMode)` when the
// bundle lands.
function applyPrefs(s) {
  const bool = (k, now) => (typeof s[k] === 'boolean' ? s[k] : now);
  const str = (k, now) => (typeof s[k] === 'string' ? s[k] : now);
  const mirror = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

  xray = bool('xray', xray);
  gridOn = bool('gridOn', gridOn);
  helpersFirst = bool('helpersFirst', helpersFirst);
  allowGrow = bool('allowGrow', allowGrow);
  linkCollision = true;  // model+collision are one thing: not optional
  trackLayout = bool('trackLayout', trackLayout);
  navMode = str('navMode', navMode) === 'fly' ? 'fly' : 'orbit';
  worldMode = str('worldMode', worldMode);
  renderMode = str('renderMode', renderMode);
  outMode = str('outMode', outMode);
  if (Array.isArray(s.hiddenBoxTypes))
    hiddenBoxTypes = new Set(s.hiddenBoxTypes.filter((t) => typeof t === 'string'));

  // Game lighting. `setEnvTint` reads `$('#envTint').checked` rather than a
  // variable, so for these the DOM IS the state and has to be set too.
  // Animations: the preferred skeleton / model (the panel reads them through the localStorage mirror)
  for (const [key, local, id] of [['animPrefSkel', 'rdr.anim.prefSkel', '#animPrefSkel'],
                                  ['animPrefModel', 'rdr.anim.prefModel', '#animPrefModel']]) {
    if (typeof s[key] !== 'string') continue;
    mirror(local, s[key]);
    const inp = $(id);
    if (inp) inp.value = s[key];
  }
  bakedLight = bool('bakedLight', bakedLight);
  showSecondPass = bool('secondPass', showSecondPass);
  for (const [id, on] of [['#envTint', bool('envTint', true)],
                          ['#bakedLight', bakedLight],
                          ['#secondPass', showSecondPass]]) {
    const e = $(id);
    if (e) e.checked = on;
  }

  // Keep the localStorage mirror agreeing with the file, so a later launch
  // that cannot reach the Studio still comes up the way it was left.
  mirror('rdr.xray', xray ? '1' : '0');
  mirror('rdr.grid', gridOn ? '1' : '0');
  mirror('rdr.pickhelpers', helpersFirst ? '1' : '0');
  mirror('rdr.allowgrow', allowGrow ? '1' : '0');
  mirror('rdr.nav', navMode);
  mirror('rdr.outmode', outMode);
  mirror('rdr.hidetypes', JSON.stringify([...hiddenBoxTypes]));

  // Redraw the switches that are BUILT from these variables. Each is guarded
  // on its own: a panel that is not in the DOM yet must not cost the rest.
  try { gridGroup.visible = gridOn; } catch {}
  try { syncVpBar(); } catch {}
  try { renderLayers(); } catch {}
  try {
    for (const b of document.querySelectorAll('#outModes [data-outmode]'))
      b.classList.toggle('on', b.dataset.outmode === outMode);
  } catch {}
}

async function loadPrefs() {
  try {
    const got = await api('settings');
    applyPrefs(got.settings || {});
  } catch (e) {
    // Offline, or an older Studio with no settings route. The localStorage
    // mirror still stands, so this costs the four unmirrored settings and
    // nothing else.
    console.warn('preferences: ' + e.message);
  }
  // Set even when the load failed, or nothing the user changes afterwards
  // would ever be saved.
  prefsReady = true;
}

// An export is a file IF AND ONLY IF the server did not answer with JSON.
// Building an <a download> and clicking it blind saved a 400-with-a-JSON-body
// as probe.obj and toasted "Exported probe.obj". Fetch it, look at the
// Content-Type, and only then hand the user a file.
async function saveFile(url, filename) {
  let r;
  try { r = await fetch(url); }
  catch { setOffline(true); toast('The Studio is not answering.', 'bad'); return false; }
  setOffline(false);
  const ct = (r.headers.get('content-type') || '').toLowerCase();
  if (ct.includes('application/json')) {
    let j = {};
    try { j = await r.json(); } catch {}
    const p = j.problem || {};
    toast('Could not export: ' + (p.message || j.error || r.statusText), 'bad');
    return false;
  }
  if (!r.ok) { toast('Could not export: ' + r.status + ' ' + r.statusText, 'bad'); return false; }
  const blob = await r.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(href), 30000);
  return true;
}

// A character mesh's materials: one per texture of its bundle, each on the
// triangles that texture covers (groups: [texture index, first triangle,
// count] from the server). Without the ranges, the whole figure got texture 0
// -- right only for single-texture bundles; a custom model with a texture per
// piece of clothing came out as the holes of its first texture's alpha.
function characterMaterials(geo, groups, urlFor) {
  const mats = [], slotOf = new Map();
  const matFor = (slot) => {
    const k = slot == null ? 0 : slot;
    if (!slotOf.has(k)) {
      const tex = new THREE.TextureLoader().load(urlFor(k));
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.flipY = false;
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      slotOf.set(k, mats.length);
      mats.push(new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, transparent: true, alphaTest: 0.35 }));
    }
    return slotOf.get(k);
  };
  if (groups && groups.length) {
    geo.clearGroups();
    for (const [slot, start, count] of groups) geo.addGroup(start * 3, count * 3, matFor(slot));
    return mats;
  }
  matFor(0);
  return mats[0];
}

let toastTimer = null;
function toast(msg, kind) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = kind || '';
  t.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.style.display = 'none'),
                          (kind === 'bad' || kind === 'warn') ? 9000 : 4500);
}

// Colour per object class.
const KINDS = {
  enemy: { color: 0xc8503f, label: 'Enemies',    on: true  },
  spawn: { color: 0x7fa860, label: 'Player spawns', on: true  },
  // Was "AI / camera boxes", which is wrong for a third of them: this class
  // also holds TRIGGER, FALLDEATH, AUDIO, SHADOWMAP and CHICKENWIRE.
  box:   { color: 0x5b8fa8, label: 'Volumes', on: true },
  line:  { color: 0xd9a441, label: 'Splines & paths',   on: true },
  bound: { color: 0x8a6fb0, label: 'Collision shells', on: false },
  // On by default now. These ARE the buildings and props -- the thing the
  // Home page tells you to click -- and having them off meant clicking a
  // storefront could only ever hit something else.
  zone:  { color: 0x6f8a70, label: 'Buildings & props', on: true },

  // -------------------------------------------------- events, not geometry
  // These come from files that have no matrix -- .sub, .cam, .glows, .aisp --
  // so nothing in the tool ever drew them, even though every one of them is
  // plain text with real coordinates in it. They are read-only markers: the
  // numbers are editable through the Environment tab's field editor, but you
  // cannot duplicate or delete a camera shot from the viewport.
  door:  { color: 0x40e0c0, label: 'Doors to other areas', on: true,
           event: true },
  shot:  { color: 0xe0a0f0, label: 'Cutscene & duel cameras', on: true,
           event: true },
  glow:  { color: 0xf0e070, label: 'Light glows', on: false, event: true },
  aispt: { color: 0x60c0f0, label: 'AI spawn points', on: false, event: true },
};

// ------------------------------------------------- the game's own vocabulary
//
// Every volume and spline names its own purpose in an uppercase token, and
// every one of the 16,851 boxes on this disc carries NO properties at all --
// the name is the entire meaning. The tool used to split on nothing and draw
// one gold box for all of them, so a level said nothing about itself.
//
// Counts below are measured disc-wide by scripts/probe_name_tokens.py and
// scripts/probe_trigger_kinds.py over all 61 level archives. Where a meaning
// is inferred from the token plus where it appears, the gloss says so; where
// it is not known, the gloss says that instead of guessing.
const BOX_TYPES = {
  AIAVOID:    { label: 'AI avoid',        color: 0x8a5a3c, n: 4457,
                gloss: 'Ground the AI will not walk into. Sub-word is a size class (High 3,535 / Medium 662 / Low 251).' },
  AISUBROOM:  { label: 'AI sub-room',     color: 0x4a7a8a, n: 4054,
                gloss: 'Subdivision of a room for AI reasoning.' },
  AIROOM:     { label: 'AI room',         color: 0x5b8fa8, n: 2331,
                gloss: 'A named region the AI treats as one room. 37 sub-words, mostly a size class.' },
  AISNEAK:    { label: 'AI sneak cover',  color: 0x6a8a5a, n: 2253,
                gloss: 'Cover the AI can sneak behind. Do not add or delete boxes.' },
  AIDOOR:     { label: 'AI doorway',      color: 0xb08a4a, n: 1983,
                gloss: 'Link between two AI rooms — a doorway the AI may pass through.' },
  CAMDIFF:    { label: 'Camera region',   color: 0x9a6fb0, n: 583,
                gloss: 'Changes camera/targeting behaviour here. LowTargeting 274, HighTargeting 133, Ledge 52, updown 54.' },
  TRIGGER:    { label: 'Trigger',         color: 0xd94a4a, n: 451,
                gloss: 'An interaction volume — this is where the player gets a prompt or a special move. The sub-word is which one.' },
  SHADOWMAP:  { label: 'Shadow map',      color: 0x555555, n: 273,
                gloss: 'Bounds for the level’s shadow rendering.' },
  CAMCONTEXT: { label: 'Camera context',  color: 0xa87fc0, n: 179,
                gloss: 'Hands the camera to a special mode: Door 78, DirCam 60, Wagon 35, BattleRail 4, Rail 2.' },
  CAMROOM:    { label: 'Camera room',     color: 0x8f6fa8, n: 128,
                gloss: 'Camera treats this as an enclosed space. Roof 68, FortInterior 30.' },
  CAMROOMDIFF:{ label: 'Camera room mod', color: 0x7f5f98, n: 28,
                gloss: 'Camera room with a difficulty/behaviour variant.' },
  FALLDEATH:  { label: 'Kill volume',     color: 0xe03030, n: 26,
                gloss: 'Falling in here kills the player.' },
  CHICKENWIRE:{ label: 'Chickenwire',     color: 0x707070, n: 24,
                gloss: 'Purpose not established. Appears in a handful of levels; no properties to read.' },
  AUDIO:      { label: 'Audio region',    color: 0x4aa890, n: 16,
                gloss: 'Region that drives an audio change.' },
  AUDROOM:    { label: 'Audio room',      color: 0x3f9880, n: 12,
                gloss: 'Room reverb / acoustic space.' },
  TRAINJUMP:  { label: 'Train jump',      color: 0xf0a020, n: 11,
                gloss: 'Where the player can jump between train cars. Train levels only.' },
  JUMPMOUNT:  { label: 'Jump mount',      color: 0xf0c040, n: 8,
                gloss: 'Where the player can jump onto something. Train levels only.' },
  WALKRUNROOM:{ label: 'Walk/run room',   color: 0x6a9a6a, n: 7,
                gloss: 'Forces walk or run movement in this region.' },
  SPECIALSHADOW:{ label: 'Special shadow', color: 0x606060, n: 6,
                gloss: 'Bespoke shadow treatment for one prop.' },
  WALKROOM:   { label: 'Walk room',       color: 0x6a9a6a, n: 4,
                gloss: 'Forces walking in this region.' },
};

// TRIGGER sub-words, from probe_trigger_kinds.py. This is the closest the
// disc comes to naming the player's prompts.
const TRIGGER_KINDS = {
  projectile:    'thrown/shot projectile interaction (295 disc-wide)',
  Window:        'dive or shoot through a window (77)',
  Toss:          'toss/throw move (23)',
  Cannonball:    'cannonball interaction (16)',
  RideableAnimal:'mount a horse or animal (12)',
  Small:         'small interaction volume (9)',
  FallDeath:     'fall that kills (8)',
  Kick:          'kick move (4)',
  Tackle:        'tackle move (4)',
  EnemyBody:     'interact with an enemy body (2)',
  Body:          'interact with a body (1)',
};

// Splines. Species is in the first token for some and the LAST for others
// (`line_armouryC_rail`), so both ends are checked.
const LINE_TYPES = {
  navPath:   { label: 'AI nav path',   color: 0x8a7a4a, n: 8956,
               gloss: 'Route the AI walks. By far the most common spline; usually hundreds of vertices.' },
  rail:      { label: 'Rail / track',  color: 0xf0d060, n: 57,
               gloss: 'A track spine. On train levels a module’s rail length IS its length along the world.' },
  OCCLUDE:   { label: 'Occluder',      color: 0x606060, n: 59,
               gloss: 'Hides geometry behind it — a visibility optimisation.' },
  fx:        { label: 'FX curve',      color: 0xc060a0, n: 233,
               gloss: 'Drives a particle or shader effect.' },
  tile:      { label: 'Tile edge',     color: 0x80a0c0, n: 108,
               gloss: 'Edge of a terrain tile module.' },
  pos:       { label: 'Named marker',  color: 0x40d0a0, n: 86,
               gloss: 'A named point with a heading — e.g. line_pos_horses. Usually only 1-2 vertices.' },
  room:      { label: 'Room bounds',   color: 0x707090, n: 51,
               gloss: 'Room extent.' },
  ptx:       { label: 'Particle line', color: 0xb050c0, n: 31,
               gloss: 'Particle emitter path.' },
  ledgegrab: { label: 'Ledge grab',    color: 0x30c0f0, n: 27,
               gloss: 'A ledge the player can grab — a prompt location.' },
  soundLoop: { label: 'Sound loop',    color: 0x40b0a0, n: 26,
               gloss: 'Looping sound emitter path.' },
  lightflare:{ label: 'Light flare',   color: 0xf0f0a0, n: 18,
               gloss: 'Lens-flare path.' },
  waterTop:  { label: 'Water surface', color: 0x4080d0, n: 14,
               gloss: 'Top of a body of water.' },
  FIRELINE:  { label: 'Fire line',     color: 0xf07030, n: 17,
               gloss: 'Fire spread / line-of-fire spline.' },
  TELEPORT:  { label: 'Teleport',      color: 0xd040d0, n: 5,
               gloss: 'Moves the player elsewhere — an area transition. Only 5 on the disc.' },
  camrail:   { label: 'Camera rail',   color: 0xa070d0, n: 5,
               gloss: 'Track the camera slides along.' },
  RAIL:      { label: 'Rail (one-way)', color: 0xf0d060, n: 57,
               gloss: 'Rail with direction; ONEWAY variants exist.' },
  wheels:    { label: 'Wheel path',    color: 0xc0a060, n: 12,
               gloss: 'Where a vehicle’s wheels sit.' },
};

// ------------------------------- what the per-instance attributes mean
//
// The `attributes N { TYPE NAME VALUE }` block on every .znes and .bnds node
// was parsed past and discarded until now. 5,973 nodes carry one, 75 distinct
// names disc-wide (scripts/probe_attributes.py). Two families:
//
//   * RENDER flags on a building: why it casts no shadow, why it is in the
//     alpha bucket, why its texture scrolls, what breaks when you shoot it.
//   * COLLISION flags on a bound: who collides with it at all.
//
// The collision flag ORDER is not a guess. Exporter-generated bounds are named
// `bound_grp_fffffffffftf`, twelve t/f characters against exactly twelve
// BoundFlag names, and every position maps to exactly one flag at 100% across
// all 485 such nodes -- see scripts/verify_boundflags.py.
const ATTR_GLOSS = {
  // render
  NoShadows:       'This surface casts no shadow. The commonest attribute on the disc (3,631 nodes).',
  HasShadow:       'This surface does cast a shadow (62 nodes; the explicit opposite).',
  TwoSided:        'Drawn from both sides — no backface culling. 666 nodes, mostly cover objects.',
  Shadows2ndPass:  'Its shadow is drawn in the second pass, over the surface rather than into it.',
  AlphaBucket:     'Sorted into the alpha/transparency bucket. 1 or 2 — two different depths of sorting.',
  occlude:         'Used as an occluder: geometry behind it can be skipped.',
  RayShadeDisabled:'Excluded from ray-shaded lighting.',
  LightSet:        'Which light set lights this object.',
  Effect_01_Name:  'The effect this object spawns when it breaks — e.g. broken_chair, explosion_fireball.',
  Effect_01_Offset:'Where that effect spawns, relative to the object.',
  ReferenceInLevel:'This bound is a reference to geometry defined elsewhere.',
  ActivePhysics:   'This object is simulated rather than static (71 nodes — wagons and swinging props).',
  // collision -- who collides with this surface
  BoundFlagPlayer: 'Does the PLAYER collide with this?',
  BoundFlagEnemy:  'Do ENEMIES collide with this?',
  BoundFlagHorse:  'Do HORSES collide with this?',
  BoundFlagCamera: 'Does the CAMERA collide with this?',
  BoundFlagCamClip:'Does the camera CLIP through this?',
  BoundFlagTarget: 'Can this be targeted?',
  BoundFlagShoot:  'Do bullets stop on this?',
  BoundFlagMover:  'Does this move?',
  BoundFlagSneak:  'Can the player sneak against this?',
  BoundFlagDoor:   'Is this a door?',
  BoundFlagWater:  'Is this water? (true on only 3 bounds disc-wide)',
  BoundFlagPlummet:'Is falling into this fatal? (true on only 9 bounds disc-wide)',
};

// The order the twelve collision flags appear in a `bound_grp_...` name.
const BOUND_FLAG_ORDER = [
  'BoundFlagMover', 'BoundFlagCamera', 'BoundFlagTarget', 'BoundFlagShoot',
  'BoundFlagSneak', 'BoundFlagEnemy', 'BoundFlagHorse', 'BoundFlagPlayer',
  'BoundFlagDoor', 'BoundFlagCamClip', 'BoundFlagWater', 'BoundFlagPlummet',
];

// What a node's own name says it is. Returns a stable key plus display info.
function typeInfo(n) {
  const parts = n.name.split('_');
  if (n.kind === 'box') {
    // A few are prefixed (`box_rec_TRIGGER_...`, `box_sph_TRIGGER_...`), so
    // look at the first two segments rather than assuming position.
    for (const p of parts.slice(1, 3)) {
      if (BOX_TYPES[p]) {
        const t = BOX_TYPES[p];
        const sub = parts[parts.indexOf(p) + 1];
        return { key: p, label: t.label, color: t.color, gloss: t.gloss,
                 sub: sub && /^[A-Za-z]/.test(sub) ? sub : null,
                 subGloss: p === 'TRIGGER' ? TRIGGER_KINDS[sub] : null };
      }
    }
    return { key: '(other)', label: 'Volume (untyped name)',
             color: KINDS.box.color,
             gloss: 'The name carries no known type token.' };
  }
  if (n.kind === 'line') {
    for (const p of [parts[1], parts[parts.length - 1], parts[parts.length - 2]]) {
      if (!p) continue;
      const hit = LINE_TYPES[p] || LINE_TYPES[p && p.replace(/\d+$/, '')]
        || (/^navpath$/i.test(p) ? LINE_TYPES.navPath : null);
      if (hit) return { key: hit.label, label: hit.label, color: hit.color,
                        gloss: hit.gloss };
    }
    return { key: '(other)', label: 'Spline (untyped name)',
             color: KINDS.line.color,
             gloss: 'The name carries no known species token.' };
  }
  const spec = KINDS[n.kind];
  return { key: n.kind, label: spec.label, color: spec.color, gloss: null };
}

// ------------------------------------------------------------------- 3D view

const view = $('#view');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
view.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d0b08);
scene.fog = new THREE.Fog(0x0d0b08, 220, 900);

const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 5000);
camera.position.set(60, 45, 60);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

const gizmo = new TransformControls(camera, renderer.domElement);
gizmo.setSize(0.8);
gizmo.addEventListener('dragging-changed', (e) => { controls.enabled = !e.value; });
gizmo.addEventListener('objectChange', onGizmoDrag);
gizmo.addEventListener('mouseUp', commitGizmo);
scene.add(gizmo);

// Dragging the WORLD, not a marker.
//
// A building's scene node is a pure reference: in TOMB all 360 nodes that
// instance drawn geometry sit at exactly [0,0,0], and the surfaces themselves
// are baked in world space on an Object3D that is also at [0,0,0]. So the
// gizmo had nothing to attach to anywhere near the thing you clicked -- it
// appeared at the world origin, under the map. Selecting a building worked
// and the Inspector filled in, while the handle was hundreds of metres away,
// which is exactly "I can't actually move it, even though it knows what I
// selected". Typing into the Shape boxes was the only way to move a building.
//
// The fix is an anchor placed at the surface's own bounding-box centre, with
// the drag folded into the SAME reshape ops those boxes already write. That
// matters for correctness, not just tidiness: `geomedit.rotate_y` and `scale`
// pivot about `centre(mesh)` by default, so the bbox centre is the one place
// a gizmo can sit where the live preview and the written bytes agree.
const worldAnchor = new THREE.Object3D();
scene.add(worldAnchor);
let worldSel = null;    // the world surface the gizmo is currently anchored to
let worldDrag = null;   // snapshot taken on mouseDown, for the live preview
let chunkSel = null;    // a surface selected with no node behind it, by chunk

scene.add(new THREE.HemisphereLight(0xffe9c4, 0x2a2018, 1.15));
const sun = new THREE.DirectionalLight(0xfff0d0, 0.7);
sun.position.set(60, 120, 40);
scene.add(sun);

const grid = new THREE.GridHelper(400, 40, 0x4a3c2c, 0x2a2219);
scene.add(grid);
scene.add(new THREE.AxesHelper(8));

const groups = {};
for (const k of Object.keys(KINDS)) {
  const g = new THREE.Group();
  g.visible = KINDS[k].on;
  scene.add(g);
  groups[k] = g;
}

// X-RAY: draw the helper volumes THROUGH the world.
//
// Boxes, bounds and zones are built with `depthWrite: false` so they do not
// hide the level -- but depth TESTING was still on, so any textured surface in
// front of them erased them completely. Inside a building, which is where the
// interesting trigger volumes are, that means they are simply not there. The
// tool was drawing them correctly and the world was painting over them.
//
// On, a helper ignores depth entirely and sits on top of the art. That is
// wrong as a picture and right as a tool: you are looking for a volume you
// cannot see, and a faint outline through a wall is the whole job. Off, it
// behaves as before, because sometimes you do want to judge what is in front
// of what.
//
// Materials register themselves as they are created, so a toggle reaches
// every helper without walking the scene graph.
let xray = false;
try { xray = localStorage.getItem('rdr.xray') === '1'; } catch {}
const xrayMats = [];

function trackXray(mat, baseOpacity) {
  mat.userData.baseOpacity = baseOpacity ?? mat.opacity;
  xrayMats.push(mat);
  applyXray(mat);
  return mat;
}

function applyXray(mat) {
  mat.depthTest = !xray;
  // Through a wall a 0.16-alpha box is invisible for a second reason -- there
  // is no longer anything behind it to give it contrast. Lifted, but not to
  // opaque: overlapping volumes still have to be readable as separate things.
  mat.opacity = xray ? Math.min(1, mat.userData.baseOpacity * 2.2)
                     : mat.userData.baseOpacity;
  mat.needsUpdate = true;
}

function setXray(on) {
  xray = !!on;
  try { localStorage.setItem('rdr.xray', xray ? '1' : '0'); } catch {}
  remember('xray', xray);
  for (const m of xrayMats) applyXray(m);
  // Seeing a volume through a wall while the wall still swallows the click
  // is the confusing half of a useful feature: x-ray follows into picking.
  if (xray && !helpersFirst) setHelpersFirst(true);
  renderLayers();
}

// Real decoded collision geometry from the level's RSC0 bundle -- the solid
// world. Kept out of KINDS because it is not editable node data.
const worldGroup = new THREE.Group();
scene.add(worldGroup);
// Collision is coarser than the art, but it is not a set of boxes: it is
// named per-object geometry, sometimes grouped. In SHER70 that is 41 meshes
// for 300 drawn surfaces, running from `bound_crack01post_01` at
// 0.6x0.9x1.4 m to a `bound_grp_` at 100x9x100 m, median longest side 9.5 m
// and median 26 triangles. Disc-wide the medians are 8.7 m and 42 triangles.
//
// DoubleSide, not BackSide. Measured over all 1,501 bounds on the disc, only
// 224 are closed surfaces. The other 1,277 are open shells, and an open
// shell has no inside to cull against -- single-sided, it is invisible from
// the side you stand on. A quarter of collision triangles (24.1%) point
// within 45 degrees of straight up, so with BackSide a level's floor drew
// only when the camera was underneath it: MANS's `bound_ground_01` is
// 95x51 m of ground as two triangles, and it never appeared at all.
//
// The note that used to sit here said you are "always INSIDE several at once"
// so the inner faces fill the screen. That was true, but it was the decoder's
// doing, not the data's: a third of the triangles were then phantom slivers
// stretching from each face back to vertex 0 of its own mesh -- 221,914 of
// the 626,338 it emitted. See Rsc.bound.
//
// Still translucent and still never writing depth, so it cannot hide the
// world behind it.
const worldMat = new THREE.MeshStandardMaterial({
  color: 0x9c8b76, roughness: 0.95, metalness: 0.0,
  flatShading: true, side: THREE.DoubleSide,
  // 0.45, up from 0.22 -- at 0.22 the collision hull read as barely-there
  // haze against the textured world. It still cannot hide anything
  // (depthWrite stays off) but it now reads as a surface.
  transparent: true, opacity: 0.45, depthWrite: false,
});
const worldWire = new THREE.MeshBasicMaterial({
  color: 0x5a4a38, wireframe: true, transparent: true, opacity: 0.32,
  depthWrite: false,
});
// Off by default: collision is a debugging view, not the level. Every session
// began by switching it off by hand, which is the tool telling you its default
// is wrong.
let worldMode = 'off';     // solid | wire | off

// A ground grid to place against, and a vertical rule from the selection down
// to it, so you can see what height something is actually at. Declared up here
// with the other scene groups because the render loop reads `gridOn` on its
// first frame, which happens before anything further down has been evaluated.
const gridGroup = new THREE.Group();
gridGroup.visible = false;
scene.add(gridGroup);
// The render loop starts partway down this file and its first frame runs
// DURING module evaluation, before the selection state below is initialised.
// Anything the loop calls that reads that state has to wait for this.
let uiReady = false;
let gridOn = false;
try { gridOn = localStorage.getItem('rdr.grid') === '1'; } catch {}

const placeGrid = new THREE.GridHelper(200, 100, 0xffd9a0, 0x6a5a48);
placeGrid.material.transparent = true;
placeGrid.material.opacity = 0.22;
placeGrid.material.depthWrite = false;
gridGroup.add(placeGrid);

const dropLine = new THREE.Line(
  new THREE.BufferGeometry().setFromPoints(
    [new THREE.Vector3(), new THREE.Vector3()]),
  new THREE.LineBasicMaterial({ color: 0xffd9a0, transparent: true,
                                opacity: 0.5, depthWrite: false }));
gridGroup.add(dropLine);

// The level's actual cast, standing where their enemy nodes put them. An
// enemy's `Type` is the stem of a *_lods.rsc in the same archive, so a
// mission can be shown populated instead of as a field of identical capsules.
// Its own group, not children of the enemy proxies: the capsules stay the
// selectable, gizmo-attachable thing, and this layer is pure display.
const castGroup = new THREE.Group();
castGroup.visible = false;
scene.add(castGroup);
let castInfo = null;

// Decoded VIF display lists -- the level's visible surfaces, with the vertex
// colours the artists baked in.
const renderGroup = new THREE.Group();
scene.add(renderGroup);
const renderMat = new THREE.MeshBasicMaterial({
  vertexColors: true, side: THREE.DoubleSide,
});
const renderPlain = new THREE.MeshStandardMaterial({
  color: 0xb8a68c, roughness: 0.95, flatShading: true, side: THREE.DoubleSide,
});
let renderMode = 'textured';   // textured | vertexcolor | plain | off
// LOD 1 by default: a level can hold 20+ actors, and LOD 0 is the model
// meant for a close-up of one.
let castLod = 1;
// Spawn points are OFF by default. A level carries one per player-slot and
// per player-count -- PERFNEW has 24 -- so drawing the player at all of them
// buries the actual cast under two dozen identical copies of Red.
let castSpawns = false;
let renderInfo = null;
// Keyed by archive + area + index, NOT by index alone. A texture index only
// means anything inside one bundle: index 13 is a 64x64 `openSign` in
// lvl_tomb.rsc and a 256x256 `gunShopWalls` in lvl_gunshop.rsc. Keying on the
// bare index repainted each newly-loaded level with the previous level's
// artwork, and nothing ever cleared the maps.
const texCache = new Map();    // "archive|area|i" -> THREE.Texture
const texMats = new Map();     // "archive|area|i" -> MeshBasicMaterial

function texKey(archive, i) {
  return `${archive}|${level?.area || ''}|${i}`;
}

function textureFor(archive, i) {
  const key = texKey(archive, i);
  if (texCache.has(key)) return texCache.get(key);
  const url = `/api/texture.png?archive=${encodeURIComponent(archive)}`
    + (level?.area ? `&area=${encodeURIComponent(level.area)}` : '')
    + `&i=${i}`;
  const t = new THREE.TextureLoader().load(url);
  // PS2 art is authored for wrapping and nearest-ish sampling at this scale.
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  t.flipY = false;
  texCache.set(key, t);
  return t;
}

// A surface needs one of three materials for the same texture, so the cache
// key carries the variant. `lit` multiplies the mesh's baked vertex lighting
// into the texture, which is what the game does and what makes a grey-brown
// ground read green on the TV. `second` is a second-pass overlay: multiply
// blended, no depth write, pushed back slightly so it does not fight the
// surface it is decorating.
function materialFor(archive, i, variant, tint) {
  const v = variant || '';
  // The game's own per-surface material colour multiplies the texture. It is
  // white on 97% of surfaces, so it only shows up where it matters: 0.8 grey
  // on shadow decals, transparent black on 117 records disc-wide. Every
  // non-white one sits on a second-pass mesh.
  const tk = tint ? tint.map((n) => n.toFixed(3)).join(',') : '';
  // An UNTEXTURED second pass is still a second pass.
  //
  // These fell through to the opaque lit material and were drawn as solid
  // geometry lying on the ground -- which is what the big black patches on
  // RANCH and TOMB were. On RANCH 4 of the 18 second-pass surfaces have no
  // texture, and `zone_haybales_2p_01` is 31 x 60 m of it.
  //
  // A shadow decal with no texture darkens by its material colour alone, so
  // it belongs in the multiply path: a white tint then costs nothing, and the
  // 0.8 grey the game uses on these gives the soft darkening it is there for.
  if (i == null && v.includes('2p')) return secondPassPlain(tk, tint);
  if (i == null) return tint ? plainTinted(tk, tint) : renderPlain;
  const key = texKey(archive, i) + '|' + v + '|' + tk;
  if (texMats.has(key)) return texMats.get(key);
  const second = v.includes('2p');
  const m = new THREE.MeshBasicMaterial({
    map: textureFor(archive, i), side: THREE.DoubleSide,
    transparent: true, alphaTest: second ? 0 : 0.35,
    vertexColors: v.includes('lit'),
  });
  if (second) {
    m.blending = THREE.MultiplyBlending;
    m.depthWrite = false;
    m.polygonOffset = true;
    m.polygonOffsetFactor = -1;
    m.polygonOffsetUnits = -1;
  }
  // Materials are made lazily as surfaces come into view, so a new one has
  // to pick up the level's ambient tint itself -- otherwise half a town
  // ends up lit and half of it raw, depending on load order.
  if (envApplied?.ambient && $('#envTint')?.checked && !second)
    m.color.setRGB(...envApplied.ambient.map((n) => Math.min(1, n * 2)));
  if (tint) {
    m.color.multiply(new THREE.Color(tint[0], tint[1], tint[2]));
    if (tint[3] < 1) { m.opacity = tint[3]; m.transparent = true; }
  }
  texMats.set(key, m);
  return m;
}

// A surface with a material tint but no texture still has to show the tint.
const plainMats = new Map();
// A second pass that carries no texture: multiply by its material colour, so
// it darkens what it sits on instead of painting over it. Same depth and
// offset treatment as the textured second-pass material, for the same reason
// -- it is a decal lying on a surface, not a surface of its own.
const secondPlainMats = new Map();
function secondPassPlain(key, tint) {
  const k = key || 'white';
  if (secondPlainMats.has(k)) return secondPlainMats.get(k);
  const m = new THREE.MeshBasicMaterial({
    color: tint ? new THREE.Color(tint[0], tint[1], tint[2]) : 0xffffff,
    side: THREE.DoubleSide, transparent: true,
    blending: THREE.MultiplyBlending, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
  });
  secondPlainMats.set(k, m);
  return m;
}

function plainTinted(key, tint) {
  if (!key) return renderPlain;
  if (plainMats.has(key)) return plainMats.get(key);
  const m = new THREE.MeshStandardMaterial({
    color: new THREE.Color(tint[0], tint[1], tint[2]),
    roughness: 0.9, side: THREE.DoubleSide,
    transparent: tint[3] < 1, opacity: tint[3],
  });
  plainMats.set(key, m);
  return m;
}

function dropTexture(archive, i) {
  const key = texKey(archive, i);
  texCache.get(key)?.dispose?.();
  texMats.get(key)?.dispose?.();
  texCache.delete(key);
  texMats.delete(key);
}

// Checking every frame is immune to ResizeObserver timing quirks (the pane can
// report zero size while hidden, and the observer will not fire again).
function resize() {
  const w = view.clientWidth | 0, h = view.clientHeight | 0;
  if (!w || !h) return;
  const dpr = Math.min(devicePixelRatio, 2);
  const cw = Math.floor(w * dpr), ch = Math.floor(h * dpr);
  const c = renderer.domElement;
  if (c.width === cw && c.height === ch) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

// ---------------------------------------------------- walkthrough camera

// Stand in the level at player scale and walk it. Eye height is 1.7 m, which
// matches the game's own scale -- spawn points sit at y=0 and door frames
// come out about 2 m.
const walk = {
  on: false, speed: 6, eye: 1.7,   // 6 m/s is a brisk run; Shift gives 4x
  keys: new Set(), yaw: 0, pitch: 0, last: 0,
};

function setWalk(on) {
  walk.on = on;
  controls.enabled = !on;
  gizmo.enabled = !on;
  // Only bring the gizmo back if something is actually selected, so leaving
  // walk mode with an empty selection doesn't park one at the origin.
  gizmo.visible = !on && selected != null;
  const c = renderer.domElement;
  if (on) {
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    walk.yaw = Math.atan2(-dir.x, -dir.z);
    walk.pitch = Math.asin(THREE.MathUtils.clamp(dir.y, -1, 1));
    walk.last = performance.now();
    c.requestPointerLock?.();
  } else if (document.pointerLockElement === c) {
    document.exitPointerLock?.();
  }
  updateHud();
}

document.addEventListener('pointerlockchange', () => {
  if (walk.on && document.pointerLockElement !== renderer.domElement) setWalk(false);
});
addEventListener('pointerlockerror', () => {
  if (walk.on) {
    setWalk(false);
    toast('The browser blocked mouse capture — click Walk again in a second.');
  }
});

renderer.domElement.addEventListener('mousemove', (e) => {
  if (!walk.on || document.pointerLockElement !== renderer.domElement) return;
  walk.yaw -= e.movementX * 0.0022;
  walk.pitch = THREE.MathUtils.clamp(walk.pitch - e.movementY * 0.0022,
                                     -Math.PI / 2 + 0.05, Math.PI / 2 - 0.05);
});

addEventListener('keydown', (e) => {
  if (!walk.on) return;
  if (isTyping(e.target)) return;
  walk.keys.add(e.code);
  if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'Space', 'ShiftLeft'].includes(e.code))
    e.preventDefault();
});
addEventListener('keyup', (e) => walk.keys.delete(e.code));

function stepWalk() {
  const now = performance.now();
  const dt = Math.min(0.1, (now - walk.last) / 1000);
  walk.last = now;

  camera.quaternion.setFromEuler(
    new THREE.Euler(walk.pitch, walk.yaw, 0, 'YXZ'));

  const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  const move = new THREE.Vector3();
  if (walk.keys.has('KeyW')) move.add(fwd);
  if (walk.keys.has('KeyS')) move.sub(fwd);
  if (walk.keys.has('KeyD')) move.add(right);
  if (walk.keys.has('KeyA')) move.sub(right);
  if (walk.keys.has('Space')) move.y += 1;
  if (walk.keys.has('ControlLeft')) move.y -= 1;
  if (!move.lengthSq()) return;
  const fast = walk.keys.has('ShiftLeft') ? 4 : 1;
  camera.position.addScaledVector(move.normalize(), walk.speed * fast * dt);
}

// Free flight in the ordinary editor view: WASD moves the camera along
// whatever direction it is already looking, with the orbit pivot carried
// along so orbiting still works from wherever you stop.
//
// Deliberately NOT a mode. There is no pointer lock, no teleport, nothing to
// enter or leave -- you can fly, then click something, then fly again.
const fly = { keys: new Set(), last: 0, speed: 14, lastDist: 0 };

// orbit = OrbitControls as it comes: the pivot is a point out in the scene.
// fly   = the pivot is held a couple of metres in front of the camera, so a
//         drag turns you roughly on the spot instead of swinging you around
//         something far away.
let navMode = 'orbit';
try { navMode = localStorage.getItem('rdr.nav') || 'orbit'; } catch {}
const FLY_PIVOT = 2.5;      // metres ahead of the camera

function setNavMode(m) {
  navMode = m === 'fly' ? 'fly' : 'orbit';
  try { localStorage.setItem('rdr.nav', navMode); } catch {}
  remember('navMode', navMode);
  if (navMode === 'fly') pinPivot();
  else {
    // Hand the pivot back to something worth orbiting, so switching out of
    // fly does not leave you spinning around a point 2.5 m from your nose.
    const o = selected != null ? meshes.get(selected) : null;
    if (o) controls.target.copy(o.getWorldPosition(new THREE.Vector3()));
  }
  controls.update();
  renderLayers();
  updateHud();
}

function pinPivot() {
  const fwd = new THREE.Vector3();
  camera.getWorldDirection(fwd);
  controls.target.copy(camera.position).addScaledVector(fwd, FLY_PIVOT);
}

function stepFly() {
  const now = performance.now();
  const dt = Math.min(0.1, (now - fly.last) / 1000);
  fly.last = now;
  if (walk.on || !fly.keys.size) return;
  const fwd = new THREE.Vector3();
  camera.getWorldDirection(fwd);
  const right = new THREE.Vector3().crossVectors(fwd, camera.up).normalize();
  const move = new THREE.Vector3();
  if (fly.keys.has('KeyW')) move.add(fwd);
  if (fly.keys.has('KeyS')) move.sub(fwd);
  if (fly.keys.has('KeyD')) move.add(right);
  if (fly.keys.has('KeyA')) move.sub(right);
  if (fly.keys.has('KeyE')) move.y += 1;
  if (fly.keys.has('KeyQ')) move.y -= 1;
  if (!move.lengthSq()) return;
  // Scale with how far out you are, so it is not glacial across a 2 km train
  // level and not uncontrollable inside a room.
  // In fly mode the pivot is re-pinned 2.5 m ahead every frame, so the
  // camera-to-target distance is a constant 2.5 and scaling by it clamped
  // speed to the floor forever. Use the last distance from before the pin.
  const rawDist = camera.position.distanceTo(controls.target);
  if (navMode !== 'fly') fly.lastDist = rawDist;
  const dist = navMode === 'fly' ? (fly.lastDist || 40) : rawDist;
  const sp = fly.speed * Math.max(0.35, Math.min(6, dist / 40))
             * (fly.keys.has('ShiftLeft') ? 4 : 1);
  const step = move.normalize().multiplyScalar(sp * dt);
  camera.position.add(step);
  controls.target.add(step);      // keep the pivot with us
  // In fly mode bring the pivot back to just ahead of the nose, so the next
  // drag turns here rather than orbiting wherever you happened to fly from.
  // Only while not dragging -- re-pinning mid-drag fights the rotation.
  if (navMode === 'fly' && !orbiting) pinPivot();
}

// OrbitControls gives no public "am I dragging", so track its own events.
let orbiting = false;
controls.addEventListener('start', () => { orbiting = true; });
controls.addEventListener('end', () => {
  orbiting = false;
  if (navMode === 'fly' && !walk.on) pinPivot();
});

// ------------------------------------------------- mouse buttons, chorded
//
// A MOUSE IS ONE POINTER CARRYING A BUTTON MASK, and while any button is held
// the other buttons' presses and releases DO NOT fire pointerdown/pointerup.
// They arrive as POINTERMOVE, with `button` naming the button that changed
// and `buttons` carrying the new mask.
//
// Measured, headless Chromium 151 driven over CDP. Hold right, click left,
// release right, verbatim:
//
//     pointerdown  button=2  buttons=2      right pressed
//     pointermove  button=-1 buttons=2      an ordinary move
//     pointermove  button=0  buttons=3      LEFT PRESSED
//     pointermove  button=0  buttons=2      LEFT RELEASED
//     pointerup    button=2  buttons=0      right released
//
// That is Pointer Events L3 "Chorded Button Interactions" working as written,
// and it is the whole reason the two buttons could not be used together. Every
// button-driven feature in this file listened for pointerdown/pointerup only,
// so with the right button held a left click sent NOTHING it was listening
// for: no selection, no place-mode drop, no gizmo grab. In the other
// direction, pressing right while left was already down never set `rmbDown`,
// so WASD would not fly.
//
// `watchButtons` is the one place that knows this. Everything downstream just
// asks for "left went down" and gets it however the browser chose to say so.
const BTN_BIT = [1, 4, 2, 8, 16];      // event.button -> its bit in event.buttons

function watchButtons(target, fn) {
  target.addEventListener('pointerdown', (e) => fn('down', e.button, e));
  target.addEventListener('pointerup', (e) => fn('up', e.button, e));
  target.addEventListener('pointermove', (e) => {
    if (e.button < 0) return;                       // an ordinary move
    const bit = BTN_BIT[e.button] || 0;
    fn((e.buttons & bit) ? 'down' : 'up', e.button, e);
  });
}

// Right mouse = go. Left mouse = touch. Letters = do.
//
// WASD used to fly whether or not a mouse button was down, and 7,500 lines
// below, bare `d` fired POST /api/duplicate. Holding D to strafe right
// therefore spammed duplicates on key repeat. Requiring the right button
// removes the collision at its root instead of papering over one letter.
let rmbDown = false;

function endRmb() {
  if (!rmbDown) return;
  rmbDown = false;
  // Releasing the button stops an ORBIT-mode fly. In fly mode the keys are
  // not borrowed from the mouse, so clearing them here would cut the motion
  // the moment you let go after a look-around.
  if (navMode !== 'fly') fly.keys.clear();
}

watchButtons(window, (kind, btn) => {
  if (btn !== 2) return;
  if (kind === 'down') {
    rmbDown = true;
    // While the right button is down, dragging LOOKS (steers the camera)
    // instead of panning. OrbitControls maps RIGHT to PAN, so the advertised
    // "hold RMB and fly with WASD" gesture shoved the world sideways under
    // the cursor and never turned -- which is what "WASD doesn't work" was.
    controls.mouseButtons.RIGHT = THREE.MOUSE.ROTATE;
  } else {
    endRmb();
    controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
  }
});

// A release that happened where we could not see it -- off the window, or
// eaten by a native context menu -- used to leave the flyer running with
// nothing held. `buttons` is the ground truth and every move carries it.
addEventListener('pointermove', (e) => {
  if (rmbDown && e.pointerType === 'mouse' && !(e.buttons & 2)) endRmb();
});
// Without this an alt-tab mid-fly comes back still flying.
addEventListener('blur', () => { rmbDown = false; });

// Only when the viewport owns the keyboard -- never while typing in a field.
//
// In FLY mode WASD moves on its own; in ORBIT mode it still wants the right
// button held. Choosing a nav mode called "fly" and finding that flying works
// exactly as before is a broken promise, and it is what the mode dropdown did.
//
// The right-button requirement existed for one reason: bare `d` fired
// duplicate, so holding D to strafe spammed clones. Duplicate moved to Ctrl+D
// -- no bare letter mutates the level any more -- so the reason is gone and
// WASDQE are free letters. Orbit keeps the hold because there it is a
// deliberate "I am driving now" gesture rather than a mode you chose.
const TYPING_INPUTS = new Set(['text', 'search', 'number', 'password', 'email', 'url']);

function isTyping(t) {
  if (!t) return false;
  if (t.tagName === 'TEXTAREA' || t.isContentEditable) return true;
  // A focused checkbox, radio or slider is not typing. The View panel is
  // made of them, so ticking X-ray silently turned WASD off until the next
  // click landed somewhere else -- with the explanatory hint suppressed by
  // the same test.
  return t.tagName === 'INPUT' && TYPING_INPUTS.has(t.type);
}

function flyable(e) {
  if (isTyping(e.target)) return false;
  if (!level || walk.on) return false;
  return navMode === 'fly' || rmbDown;
}

// Pressing WASD in orbit mode with no button held used to do nothing at all,
// silently, which is indistinguishable from broken. Say what to do -- once
// per session, because a toast on every keypress is its own kind of broken.
let flyHintShown = false;
addEventListener('keydown', (e) => {
  if (!flyable(e)) {
    if (!isTyping(e.target) && level && !walk.on && !flyHintShown
        && /^(KeyW|KeyA|KeyS|KeyD)$/.test(e.code)
        && $('#tab-editor')?.classList.contains('on')) {
      flyHintShown = true;
      toast('Hold the right mouse button to fly with WASD.', 'warn');
    }
    return;
  }
  if (/^(KeyW|KeyA|KeyS|KeyD|KeyQ|KeyE|ShiftLeft)$/.test(e.code))
    fly.keys.add(e.code);
});
addEventListener('keyup', (e) => fly.keys.delete(e.code));
addEventListener('blur', () => fly.keys.clear());

function standAtSpawn() {
  if (!level) return;
  const spawn = level.nodes.find((n) => n.kind === 'spawn')
    || level.nodes.find((n) => n.kind === 'enemy');
  if (!spawn) return toast('This level has no spawn point to stand at.');
  camera.position.set(spawn.pos[0], spawn.pos[1] + walk.eye, spawn.pos[2]);
  if (spawn.angle != null) walk.yaw = THREE.MathUtils.degToRad(spawn.angle);
  setWalk(true);
  toast('Walk mode — WASD to move, mouse to look, Shift to sprint, ' +
        'Space/Ctrl for up/down, Esc to exit.');
}

// rAF is paused while the page is hidden, so also react to real resize events.
new ResizeObserver(() => resize()).observe(view);
addEventListener('resize', resize);

function draw() {
  resize();
  if (walk.on) stepWalk();
  // Fly first, then let OrbitControls settle: stepFly moves the camera and
  // its pivot together, and update() applies the damping on top. In fly mode
  // the pivot is then re-pinned just ahead of the camera, which is what makes
  // the next drag turn on the spot rather than orbit something distant.
  else { stepFly(); controls.update(); }
  if (gridOn && uiReady) updateGrid();
  renderer.render(scene, camera);
}

(function loop() {
  requestAnimationFrame(loop);
  draw();
})();

// ------------------------------------------------------------------- state

let level = null;         // level JSON from the server
let meshes = new Map();   // uid -> Object3D
let selection = new Set();// every selected uid
let selected = null;      // the "primary" one: gizmo anchor + inspector target
let dirty = new Map();    // uid -> [x,y,z] pending
let dirtyAngle = new Map();  // uid -> heading in degrees, pending
let dragFrom = null;      // uid -> [x,y,z] at drag start, for bulk moves
let openText = null;      // {archive, name, slot, compressed}
let editableFields = {};  // enemy property -> what it means

const HILITE = 0xffd9a0;

function setHighlight(uid, on) {
  const obj = meshes.get(uid);
  if (!obj) return;
  const mat = obj.material;
  if (!mat) return;
  if (on) {
    if (mat.userData.baseColor === undefined)
      mat.userData.baseColor = mat.color.getHex();
    mat.color.setHex(HILITE);
    if (mat.emissive) mat.emissive.setHex(0x3a2c17);
  } else if (mat.userData.baseColor !== undefined) {
    mat.color.setHex(mat.userData.baseColor);
    if (mat.emissive) mat.emissive.setHex(0x000000);
  }
}

// The file matrix stores rows as local axes; three.js Matrix4.set() takes
// row-major input, so element(r,c) = fileRow[c][r].
function applyMatrix(obj, rot, pos) {
  if (rot && rot.length === 9) {
    const m = new THREE.Matrix4();
    m.set(rot[0], rot[3], rot[6], pos[0],
          rot[1], rot[4], rot[7], pos[1],
          rot[2], rot[5], rot[8], pos[2],
          0, 0, 0, 1);
    m.decompose(obj.position, obj.quaternion, obj.scale);
  } else {
    obj.position.set(pos[0], pos[1], pos[2]);
  }
}

// Tile-owned nodes arrive in TILE-LOCAL space with a display offset attached.
// Folding it into `pos` here, once, is what keeps every drawing site in this
// file correct without any of them knowing tiles exist -- there are a dozen
// places that read `n.pos` to position a mesh, and patching each was the
// version of this that would have shipped one that got missed.
//
// The offset is kept on the node so a write can undo it: the file being
// edited is the tile's own, in tile-local coordinates, and sending world
// coordinates back would bake the level's layout into a piece meant to be
// reusable. See unoffset(), used on every commit path.
function applyTileOrigins(data) {
  for (const n of (data?.nodes || [])) {
    if (!n.origin) continue;
    n.pos = [n.pos[0] + n.origin[0], n.pos[1] + n.origin[1],
             n.pos[2] + n.origin[2]];
    if (n.verts) {
      n.verts = n.verts.map((v) => [v[0] + n.origin[0], v[1] + n.origin[1],
                                    v[2] + n.origin[2]]);
    }
  }
}

// World position -> the tile-local numbers that belong in the file.
function unoffset(uid, pos) {
  const o = nodeOf(uid)?.origin;
  return o ? [pos[0] - o[0], pos[1] - o[1], pos[2] - o[2]] : pos;
}

let builtLevelKey = null;
// Increments on every buildLevel. Async loaders capture it and bail after
// each await if it moved on: an in-flight TOMB geometry response (measured
// ~5 s to decode) used to land INSIDE the level you had switched to, drawing
// one map's surfaces inside the other and overwriting the HUD -- the "I
// switched levels and it didn't really work" bug.
let loadGen = 0;

function buildLevel(data, keepView) {
  // uids are per-level, so carrying hidden ones across would hide arbitrary
  // objects in the next map. SAME level, any reload -- light or heavy --
  // keeps them: a reshape triggers a heavy reload, and losing every hide on
  // each gizmo drag threw away exactly the isolation that made the edit
  // possible.
  const key = (data.archive || '') + '|' + (data.area || '');
  if (!keepView && key !== builtLevelKey) {
    hiddenUids.clear(); hiddenChunks.clear();
    // Per-level modes do not follow you to the next level. Track layout in
    // particular detaches the gizmo and disables nudging -- and its checkbox
    // only renders on train levels, so carried anywhere else it was a dead
    // editor with no visible cause.
    trackLayout = false;
  }
  builtLevelKey = key;
  loadGen += 1;
  applyTileOrigins(data);
  level = data;
  refreshLevelName();
  chunkUid = uidChunk = null;   // rebuilt lazily for the new level
  for (const g of Object.values(groups)) {
    while (g.children.length) {
      const c = g.children.pop();
      c.geometry?.dispose?.();
      c.material?.dispose?.();
    }
  }
  meshes.clear();
  dirty.clear();
  dirtyAngle.clear();
  gizmo.detach();
  selection.clear();
  selected = null;
  // A bare-surface selection is per-level too. Left set, the geometry
  // loaders' re-anchor re-opened the OLD level's chunk inside the new one --
  // wrong inspector, wrong gizmo anchor, and a level switch that looked like
  // it half-worked.
  chunkSel = null;
  worldSel = null;
  worldDrag = null;
  placeMode = false;
  renderer.domElement.style.cursor = '';
  const _bn = $('#modeBanner');
  if (_bn) _bn.style.display = 'none';
  // Refused writes revert against these; stale entries from the previous
  // level made a refusal teleport an object to last session's position.
  homePos.clear();

  for (const n of data.nodes) {
    const spec = KINDS[n.kind];
    if (!spec) continue;
    let obj = null;
    // Colour by what the game calls it, not by which file it came from. A
    // TRIGGER and an AIAVOID are both boxes and were both the same gold; now
    // a kill volume is red and a nav path is dull where a rail is bright.
    const ti = typeInfo(n);
    const col = ti.color;

    if (n.kind === 'box') {
      const s = n.size || {};
      const geo = new THREE.BoxGeometry(s.width || 1, s.height || 1, s.depth || 1);
      const mat = trackXray(new THREE.MeshStandardMaterial({
        color: col, transparent: true, opacity: 0.16,
        depthWrite: false, roughness: 0.9,
      }), 0.16);
      obj = new THREE.Mesh(geo, mat);
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo),
        trackXray(new THREE.LineBasicMaterial({
          color: col, transparent: true, opacity: 0.75 }), 0.75));
      obj.add(edges);
      applyMatrix(obj, n.rot, n.pos);

    } else if (n.kind === 'line') {
      const pts = (n.verts || []).map((v) => new THREE.Vector3(v[0], v[1], v[2]));
      if (pts.length < 2) {
        // A 1-vertex spline is a NAMED MARKER, not a path -- `line_pos_horses`
        // is where Brimstone's horses stand. Drawing it as a degenerate line
        // meant it drew as nothing at all, so 86 deliberate markers were
        // invisible. Draw it as an arrow that shows its heading.
        if (!pts.length) continue;
        obj = new THREE.Mesh(
          new THREE.ConeGeometry(0.3, 1.2, 6),
          new THREE.MeshStandardMaterial({ color: col, roughness: 0.5 }));
        obj.position.copy(pts[0]).add(
          new THREE.Vector3(n.pos[0], n.pos[1] + 0.6, n.pos[2]));
      } else {
        obj = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(pts),
          new THREE.LineBasicMaterial({ color: col }));
        // Spline vertices are already world-space; the node matrix is a parent
        // offset, so only the translation is applied.
        obj.position.set(n.pos[0], n.pos[1], n.pos[2]);
        const dots = new THREE.Points(
          new THREE.BufferGeometry().setFromPoints(pts),
          new THREE.PointsMaterial({ color: col, size: 0.55 }));
        obj.add(dots);
      }

    } else if (n.kind === 'enemy') {
      obj = new THREE.Mesh(
        new THREE.CapsuleGeometry(0.35, 1.1, 4, 8),
        new THREE.MeshStandardMaterial({ color: spec.color, roughness: 0.7 }));
      obj.position.set(n.pos[0], n.pos[1] + 0.9, n.pos[2]);
      obj.userData.yOffset = 0.9;
      if (n.angle != null) obj.rotation.y = THREE.MathUtils.degToRad(n.angle);
      const nose = new THREE.Mesh(
        new THREE.ConeGeometry(0.18, 0.5, 6),
        new THREE.MeshBasicMaterial({ color: 0xffd9a0 }));
      nose.rotation.x = Math.PI / 2;
      nose.position.set(0, 0.5, 0.5);
      obj.add(nose);

    } else if (n.kind === 'spawn') {
      obj = new THREE.Mesh(
        new THREE.ConeGeometry(0.6, 1.8, 8),
        new THREE.MeshStandardMaterial({ color: spec.color, roughness: 0.5 }));
      obj.position.set(n.pos[0], n.pos[1] + 0.9, n.pos[2]);
      obj.userData.yOffset = 0.9;
      if (n.angle != null) obj.rotation.y = THREE.MathUtils.degToRad(n.angle);

    } else {
      obj = new THREE.Mesh(
        new THREE.OctahedronGeometry(0.5),
        new THREE.MeshStandardMaterial({ color: spec.color, roughness: 0.8 }));
      applyMatrix(obj, n.rot, n.pos);
      // A baked building or shell has an identity matrix -- its geometry
      // carries the real position -- so this marker would sit at the world
      // origin. Hundreds of them stacked there, every one at top pick
      // priority, meant any click whose ray grazed the origin selected an
      // arbitrary one. The real geometry is the click target; the origin dot
      // is noise.
      if ((n.kind === 'zone' || n.kind === 'bound')
          && Math.abs(n.pos[0]) < 1e-4 && Math.abs(n.pos[1]) < 1e-4
          && Math.abs(n.pos[2]) < 1e-4) {
        obj.visible = false;
        obj.userData.originDot = true;
      }
    }

    obj.userData.uid = n.uid;
    obj.userData.node = n;
    obj.userData.type = ti;
    // How hard did you have to aim to hit this? A trigger box is a big
    // translucent shell you see straight through, so it must not steal the
    // click from the solid building standing inside it -- see PICK_* in the
    // pointerup handler.
    obj.userData.pickRank = n.kind === 'box' ? PICK_VOLUME : PICK_MARKER;
    groups[n.kind].add(obj);
    meshes.set(n.uid, obj);
  }

  applyBoxTypeFilter();   // re-apply the per-type volume switches to new meshes
  renderObjectList();
  renderLayers();
  renderAreas();
  // These used to live only in setRenderMode, which a LIGHT reload never
  // calls -- and a delete or a duplicate does a light reload, because the
  // world bundle has not changed. So the node list updated and the viewport
  // did not: deleting something left it on screen until you reloaded the
  // page. Re-deriving here means every rebuild of the node list re-derives
  // what should be drawn from it.
  refreshDeletedChunks();
  addInstanceCopies();
  applyHidden();
  // Events come from a second request, so they arrive after the geometry and
  // add themselves to the same node list. Not awaited: the level is usable
  // without them.
  loadLevelEvents(data.archive, data.area);

  // `keepView` means "the objects moved, the world did not". Re-decoding
  // render geometry costs ~3 s on TOMB (343 chunks, uncached because every
  // write drops the level), so paying it for an arrow-key nudge made editing
  // feel broken. The world and its textures are unchanged by a placement
  // edit, so keep them.
  if (!keepView) {
    // `content_bounds` excludes the room-extent marker splines. The nominal
    // box is a constant 218 x 187 x 196 m on nearly every level because one
    // template spline, line_room_newgrnd, reaches 137 m in all of them -- so
    // framing to it opened every interior zoomed 3x too far out.
    frameLevel(data.content_bounds || data.bounds);
    userMovedCamera = false;
    renderAssetDock();
    loadGeometry(data.archive);
    loadRenderGeometry(data.archive);
    if (castGroup.visible) loadCast(data.archive);
    else clearCast();
  } else if (castGroup.visible) {
    // A placement edit moved actors; their models have to follow.
    loadCast(data.archive);
  }
  updateHud();
}

// --------------------------------------------------------------- level events
//
// The doors, cutscene cameras, glows and AI spawn points -- everything with a
// real coordinate that is NOT in a file with a matrix, and which therefore
// never appeared in the viewport. See rdrstudio/levelmeta.py for the parsers
// and for what is deliberately NOT drawn:
//
//   * A level FLAG has no coordinate anywhere on this disc, so flags are a
//     list in the Events panel and never a marker. Searching all 1,242 files
//     of TRAINGOO.DAT for `Inst_Jump` returns exactly one hit -- its own
//     .flags file -- so any line drawn from a trigger volume to a flag would
//     be invented.
//   * A .cam shot is positioned, but what makes it fire is a flag. So you get
//     every camera in 3D and can look through it; you do not get "the cutscene
//     plays here", because that is not in the data.
let levelEvents = null;
let eventUid = 5e6;        // synthetic uids, far above any real node's

let eventsFor = null;      // "archive|area" the cached events belong to

async function loadLevelEvents(archive, area) {
  const gen = loadGen;
  for (const k of Object.keys(KINDS))
    if (KINDS[k].event) clearGroup(groups[k]);
  const key = `${archive}|${area || ''}`;
  try {
    // A nudge or a duplicate rebuilds the level but cannot change a .cam or a
    // .sub, so the markers are rebuilt from the cached read rather than
    // refetched -- otherwise every arrow-key press costs a round trip.
    if (eventsFor !== key || !levelEvents || levelEvents.error) {
      const q = `level_events?archive=${encodeURIComponent(archive)}`
              + (area ? `&area=${encodeURIComponent(area)}` : '');
      levelEvents = await api(q);
      eventsFor = key;
    }
    if (!level || level.archive !== archive
        || gen !== loadGen) return;   // level or area changed under us
    buildEventMarkers(levelEvents);
    assignFrames();
    applyTrackLayout();
    renderObjectList();
    renderLayers();
    renderEventsPanel();
    applyLevelSun(levelEvents.sun);
  } catch (e) {
    // Not fatal to the level, but do not swallow it either -- a silent catch
    // here is how the markers went missing with nothing on screen to say so.
    levelEvents = { error: e.message };
    eventsFor = null;
    console.error('level events failed:', e);
    toast('Could not read this level’s events: ' + e.message, 'bad');
  }
}

function clearGroup(g) {
  while (g.children.length) {
    const c = g.children.pop();
    c.traverse?.((o) => { o.geometry?.dispose?.(); });
    c.geometry?.dispose?.();
  }
}

function addEventNode(kind, name, pos, extra) {
  const n = {
    uid: ++eventUid, kind, name, pos,
    source: extra.source || '', props: extra.props || {},
    readonly: true, event: extra,
  };
  level.nodes.push(n);
  return n;
}

function buildEventMarkers(ev) {
  // Doors into other areas. A `.sub` describes ONE master level and its
  // sub-levels, and both sets of coordinates are in that one file: `Enter` is
  // in the master's space, `Leave` is in the sub-level's. So which markers are
  // correct depends on which area is loaded.
  //
  // Standing in Tombstone you want the six shopfronts. Standing in the saloon
  // you want the one way out -- drawing all six there would put the bank's
  // doorway inside the bar, at coordinates that mean nothing in that space.
  const stem = (level.area || '').replace(/\.rsc$/i, '').toLowerCase();
  const inside = (ev.transitions || []).find(
    (t) => (t.level || '').toLowerCase() === stem);

  for (const t of (ev.transitions || [])) {
    // Inside a sub-level, only that sub-level's exit is meaningful.
    if (inside && t !== inside) continue;
    const at = inside ? t.leave : t.enter;
    if (!at) continue;
    const n = addEventNode(
      'door',
      inside ? `↩ back to ${t.master || 'the main level'}` : `→ ${t.display || t.level}`,
      at,
      { source: t.source, kind: 'transition', data: t, outbound: !inside });
    const g = new THREE.Group();
    const frame = new THREE.Mesh(
      new THREE.PlaneGeometry(1.1, 2.1),
      new THREE.MeshBasicMaterial({ color: KINDS.door.color, side: THREE.DoubleSide,
                                    transparent: true, opacity: 0.28 }));
    frame.position.y = 1.05;
    g.add(frame);
    g.add(new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.PlaneGeometry(1.1, 2.1)),
      new THREE.LineBasicMaterial({ color: KINDS.door.color })).translateY(1.05));
    if (t.leave_heading != null) g.rotation.y = t.leave_heading;
    g.position.set(at[0], at[1], at[2]);
    tagEvent(g, n, PICK_MARKER);
    groups.door.add(g);
  }

  // A camera shot: a little frustum from the eye toward the look-at point.
  for (const c of (ev.cameras || [])) {
    if (c.parked) continue;      // PrincessCam and friends live at y=10000
    const n = addEventNode('shot', c.name, c.eye,
      { source: c.source, kind: 'camera', data: c });
    const g = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.ConeGeometry(0.45, 1.1, 4),
      new THREE.MeshBasicMaterial({ color: KINDS.shot.color, wireframe: true }));
    body.rotation.x = Math.PI / 2;
    body.position.z = 0.55;
    g.add(body);
    // The sight line, so you can see WHERE it looks without selecting it.
    g.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(
        [new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, 3)]),
      new THREE.LineBasicMaterial({ color: KINDS.shot.color,
                                    transparent: true, opacity: 0.45 })));
    g.position.set(c.eye[0], c.eye[1], c.eye[2]);
    g.lookAt(new THREE.Vector3(c.target[0], c.target[1], c.target[2]));
    tagEvent(g, n, PICK_MARKER);
    groups.shot.add(g);
  }

  for (const gl of (ev.glows || [])) {
    if (!gl.pos) continue;
    const n = addEventNode('glow', gl.name || gl.texture || 'glow', gl.pos,
      { source: gl.source, kind: 'glow', data: gl });
    const m = new THREE.Mesh(
      new THREE.SphereGeometry(0.6, 10, 8),
      new THREE.MeshBasicMaterial({ color: KINDS.glow.color, wireframe: true }));
    m.position.set(gl.pos[0], gl.pos[1], gl.pos[2]);
    tagEvent(m, n, PICK_MARKER);
    groups.glow.add(m);
  }

  // AI spawn points. On a train level these are where the cast REALLY appears,
  // which matters because most enemies are parked at one shared coordinate.
  for (const grp of (ev.spawn_groups || [])) {
    for (let i = 0; i < grp.points.length; i++) {
      const p = grp.points[i];
      const n = addEventNode('aispt', `${grp.group} #${i + 1}`, p.pos,
        { source: grp.source, kind: 'spawn_point', data: p, group: grp.group });
      const m = new THREE.Mesh(
        new THREE.ConeGeometry(0.28, 1.0, 5),
        new THREE.MeshStandardMaterial({ color: KINDS.aispt.color, roughness: 0.6 }));
      m.position.set(p.pos[0], p.pos[1] + 0.5, p.pos[2]);
      if (p.heading != null) m.rotation.y = THREE.MathUtils.degToRad(p.heading);
      tagEvent(m, n, PICK_MARKER);
      groups.aispt.add(m);
    }
  }
}

// ------------------------------------------------------- the events sheet
//
// The tables that belong to a level but have no place in 3D, plus the one page
// that says what the toolkit still cannot read. Kept as an overlay inside the
// viewport rather than a 15th tab, because every one of them is only ever read
// while looking at the map it describes.
let sheetView = 'flags';

const SHEET_TITLES = {
  flags: 'Level events — flags',
  doors: 'Level events — doors to other areas',
  cams: 'Level events — cameras',
  track: 'Level events — track & consist',
  undec: 'Level events — not decoded yet',
};

function openSheet(which) {
  // A filter left over from the previous page would silently hide rows on the
  // next one, and the box that explains it is not always drawn.
  if (which && which !== sheetView) sheetFilter = '';
  if (which) sheetView = which;
  $('#sheet').classList.add('open');
  for (const b of document.querySelectorAll('#sheetTabs [data-sheet]'))
    b.classList.toggle('on', b.dataset.sheet === sheetView);
  // The heading read "Level events" whichever of the five pages was open, so
  // once you were inside it told you nothing.
  const t = $('#sheetTitle');
  if (t) t.textContent = SHEET_TITLES[sheetView] || 'Level events';
  renderSheet();
}

function closeSheet() { $('#sheet').classList.remove('open'); }

function sheetNote(text) {
  const d = el('div', 'muted');
  d.style.cssText = 'font-size:11px;line-height:1.6;margin-bottom:10px;'
    + 'padding:7px 9px;border-left:2px solid var(--line);background:var(--bg2)';
  d.textContent = text;
  return d;
}

let sheetFilter = '';

// A filter for the sheet. TOMB lists hundreds of flags across several files
// and the only way to find one was to read the whole table, which is exactly
// the case this view exists for.
function sheetFilterBar(placeholder) {
  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:8px';
  const inp = el('input');
  inp.placeholder = placeholder;
  inp.value = sheetFilter;
  inp.style.cssText = 'flex:1;font-size:11px';
  inp.oninput = () => {
    sheetFilter = inp.value;
    renderSheet();
    // renderSheet rebuilds the bar, so put the caret back where it was.
    const again = $('#sheetBody').querySelector('input');
    if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
  };
  bar.append(inp);
  return bar;
}

// The file a row came from, as a link. Reading a value and not being able to
// get to the file that holds it was a dead end in a tool whose whole point is
// editing those files.
function sheetSource(name) {
  const a = el('span', 'nm', name);
  a.style.cssText = 'color:var(--gold);cursor:pointer;text-decoration:underline';
  a.title = `Open ${name} in Files`;
  a.onclick = () => { closeSheet(); openTextFile(level.archive, name); };
  return a;
}

function renderSheet() {
  const body = $('#sheetBody');
  if (!body) return;
  body.innerHTML = '';
  const ev = levelEvents;
  if (!level) return body.append(el('p', 'muted', 'Load a level first.'));
  if (!ev || ev.error)
    return body.append(el('p', 'muted',
      'Could not read this level’s event files' + (ev?.error ? `: ${ev.error}` : '.')));

  if (sheetView === 'flags') {
    body.append(sheetNote(
      'Flags have no coordinates. Listed only.'));
    const all = ev.flags || [];
    if (!all.length) return body.append(el('p', 'muted', 'This level has no .flags file.'));
    body.append(sheetFilterBar('filter flags by name or value…'));
    const q = sheetFilter.trim().toLowerCase();
    const rows = q
      ? all.filter((f) => (f.name || '').toLowerCase().includes(q)
                       || String(f.value).toLowerCase().includes(q))
      : all;
    if (!rows.length)
      return body.append(el('p', 'muted',
        `No flag matches “${sheetFilter}” — ${all.length} in this level.`));
    if (q) {
      // `Node.append` returns undefined, so chaining `.lastChild` off it threw
      // -- after the summary line was already in the DOM and before any table
      // was. The count said "40 of 555 match" above an empty page.
      const sum = el('div', 'muted', `${rows.length} of ${all.length} flags match.`);
      sum.style.cssText = 'font-size:11px;margin-bottom:6px';
      body.append(sum);
    }
    const bySrc = new Map();
    for (const f of rows) {
      if (!bySrc.has(f.source)) bySrc.set(f.source, []);
      bySrc.get(f.source).push(f);
    }
    for (const [src, list] of bySrc) {
      const head = el('div');
      head.style.cssText = 'font-size:11px;margin:10px 0 4px';
      head.append(sheetSource(src), el('span', 'muted', ` — ${list.length} flags`));
      body.append(head);
      const t = el('table');
      for (const f of list) {
        const tr = el('tr');
        tr.append(el('td', '', f.name), el('td', 'muted', String(f.value)));
        t.append(tr);
      }
      body.append(t);
    }
    return;
  }

  if (sheetView === 'doors') {
    body.append(sheetNote(
      'Doors from the .sub file, positioned.'));
    const rows = ev.transitions || [];
    if (!rows.length)
      return body.append(el('p', 'muted', 'This level has no .sub file, so it '
        + 'has no doors into other areas.'));
    for (const t of rows) {
      const card = el('div');
      card.style.cssText = 'border:1px solid var(--line);border-radius:4px;'
        + 'padding:8px 10px;margin-bottom:8px';
      card.append(el('div', 'nm', t.display || t.level));
      card.lastChild.style.cssText = 'color:var(--gold);font-size:12px';
      const meta = el('div', 'muted');
      meta.style.cssText = 'font-size:11px;line-height:1.7;margin-top:3px';
      meta.textContent =
        `loads ${t.level}  ·  door ${t.door_model || '?'} playing `
        + `${t.door_anim || '?'}  ·  ${t.lock === -1 ? 'unlocked' : `lock ${t.lock}`}`
        + (t.enter ? `\nenter at ${t.enter.map((v) => v.toFixed(2)).join(', ')}` : '')
        + (t.leave ? `\nreturn to ${t.leave.map((v) => v.toFixed(2)).join(', ')}` : '');
      meta.style.whiteSpace = 'pre-line';
      card.append(meta);
      const bar = el('div');
      bar.style.cssText = 'display:flex;gap:6px;margin-top:6px';
      const fly = el('button', '', 'Fly to the door');
      fly.style.fontSize = '11px';
      fly.onclick = () => {
        const n = level.nodes.find((x) => x.kind === 'door'
                                       && x.event?.data?.level === t.level);
        if (n) { closeSheet(); select(n.uid); focusOn(n.uid); }
      };
      bar.append(fly);
      const open = el('button', '', `Open ${t.source} as text`);
      open.style.fontSize = '11px';
      open.onclick = () => { closeSheet(); openTextFile(level.archive, t.source); };
      bar.append(open);
      card.append(bar);
      body.append(card);
    }
    return;
  }

  if (sheetView === 'cams') {
    body.append(sheetNote(
      'Camera shots, positioned. Triggers are flags.'));
    const rows = ev.cameras || [];
    if (!rows.length) return body.append(el('p', 'muted', 'No .cam files here.'));
    const t = el('table');
    const hd = el('tr');
    for (const h of ['shot', 'kind', 'lens', ''])
      hd.append(el('th', '', h));
    t.append(hd);
    for (const c of rows) {
      const tr = el('tr');
      tr.append(el('td', '', c.name));
      tr.append(el('td', 'muted', c.cutscene ? 'cutscene'
        : c.duel ? 'duel view' : c.parked ? 'parked (y≈10000)' : 'shot'));
      tr.append(el('td', 'muted', c.lens.toFixed(2)));
      const cell = el('td');
      if (!c.parked) {
        const b = el('button', '', 'Look through');
        b.style.fontSize = '11px';
        b.onclick = () => {
          closeSheet();
          camera.position.set(c.eye[0], c.eye[1], c.eye[2]);
          controls.target.set(c.target[0], c.target[1], c.target[2]);
          controls.update();
          toast(`Looking through ${c.name}`, 'good');
        };
        cell.append(b);
      }
      tr.append(cell);
      t.append(tr);
    }
    body.append(t);
    return;
  }

  if (sheetView === 'track') {
    const fr = ev.frames || [];
    if (!fr.length)
      return body.append(el('p', 'muted',
        'This level has no tile or train layout.'));
    body.append(sheetNote(
      'Order read from the disc. Offsets derived. Details: Guides > Modding guide.'));
    const tiles = fr.filter((f) => f.kind === 'tile');
    const cars = fr.filter((f) => f.kind === 'train_car');
    if (tiles.length) {
      const span = Math.max(...tiles.map((f) => f.offset[0] + f.length));
      const v = ev.speed?.velocity;
      body.append(el('div', 'nm',
        `Track — ${tiles.length} tile slots, ${span.toFixed(0)} m`
        + (v ? `, ${(span / v).toFixed(0)} s at ${v} m/s` : '')));
      body.lastChild.style.cssText = 'color:var(--gold);font-size:12px;margin:6px 0 4px';
      const t = el('table');
      for (const f of tiles) {
        const tr = el('tr');
        tr.append(el('td', 'muted', `${f.offset[0].toFixed(0)} m`));
        tr.append(el('td', '', f.module.replace(/^lvl_[a-z0-9]+_tile_/, '')));
        tr.append(el('td', 'muted', `${f.length.toFixed(0)} m`));
        const flags = [];
        if (f.runtime_random) flags.push('random slot');
        if (Math.abs(f.z_shift) > 1) flags.push(`track steps ${f.z_shift.toFixed(1)} m`);
        if (f.repeats > 1) flags.push(`used ${f.repeats}×`);
        tr.append(el('td', 'muted', flags.join(' · ')));
        t.append(tr);
      }
      body.append(t);
    }
    if (cars.length) {
      body.append(el('div', 'nm', `Trains — ${cars.length} cars`));
      body.lastChild.style.cssText = 'color:var(--gold);font-size:12px;margin:12px 0 4px';
      const t = el('table');
      for (const f of cars) {
        const tr = el('tr');
        tr.append(el('td', 'muted', `${f.offset[0].toFixed(1)} m`));
        tr.append(el('td', '', f.module.replace(/^lvl_[a-z0-9]+_/, '')));
        tr.append(el('td', 'muted', `gap ${(f.gap || 0).toFixed(1)} m`));
        tr.append(el('td', 'muted', `res ${f.resource}`));
        t.append(tr);
      }
      body.append(t);
    }
    return;
  }

  if (sheetView === 'undec') {
    body.append(sheetNote(
      'Formats not decoded yet, by extension.'));
    const box = el('div', 'muted', 'Counting…');
    body.append(box);
    api(`toc?archive=${encodeURIComponent(level.archive)}`).then((toc) => {
      const KNOWN = new Set(['znes', 'bnds', 'bxes', 'lnes', 'spwn', 'enm', 'rsc',
        'rnt', 'sub', 'cam', 'glows', 'shadows', 'flags', 'aisp', 'trn', 'til',
        'znv', 'chase', 'mmb', 'film', 'tex', 'strtbl', 'txt', 'guns', 'cr',
        'sneak', 'veh', 'wheels', 'pist', 'hud', 'skel']);
      const byExt = new Map();
      for (const e of (toc.files || toc)) {
        const ext = (e.name || '').split('.').pop().toLowerCase();
        if (KNOWN.has(ext)) continue;
        const r = byExt.get(ext) || { n: 0, bytes: 0 };
        r.n++; r.bytes += e.bytes || e.size_full || 0;
        byExt.set(ext, r);
      }
      box.remove();
      const rows = [...byExt.entries()].sort((a, b) => b[1].bytes - a[1].bytes);
      if (!rows.length)
        return body.append(el('p', 'muted', 'Everything in this archive is decoded.'));
      const t = el('table');
      const hd = el('tr');
      for (const h of ['extension', 'files', 'bytes']) hd.append(el('th', '', h));
      t.append(hd);
      for (const [ext, r] of rows) {
        const tr = el('tr');
        tr.append(el('td', '', '.' + ext), el('td', 'muted', String(r.n)),
                  el('td', 'muted', r.bytes.toLocaleString()));
        t.append(tr);
      }
      body.append(t);
    }).catch((e) => { box.textContent = 'Could not list the archive: ' + e.message; });
    return;
  }
}

// Counts on the layer rows, so the Events button says how much is behind it.
function renderEventsPanel() {
  const b = $('#btnEvents');
  if (!b || !levelEvents || levelEvents.error) return;
  const n = (levelEvents.flags || []).length;
  const d = (levelEvents.transitions || []).length;
  b.textContent = `Level events${n ? ` · ${n} flags` : ''}${d ? ` · ${d} doors` : ''}`;
}

// ------------------------------------------------- moving-origin levels
//
// TRAIN and TRAINGOO are missions where the world streams past a train, so
// every piece is authored at the origin and the game moves it. Opened in the
// editor they look like a small pile, which is why he asked whether the map is
// bigger than it appears. It is: TRAIN is 1,080 m of track carrying a 126.2 m
// train, TRAINGOO 2,160 m.
//
// What can honestly be laid out, and what cannot:
//
//   TRAIN CARS -- yes, exactly. Their offsets are stated in `lvl_train.trn`,
//     and each car's surfaces name it (`zone_armouryA_01`, 102 of 206 chunks
//     on TRAIN), so a chunk can be assigned to a car by name. That is a join,
//     not a guess.
//   TERRAIN TILES -- no. ZERO of the 206 chunks name a tile, there is no
//     `lvl_train.rnt`, and index order does not line up either. So tiles get
//     their derived positions in the Track sheet and the outliner, and their
//     geometry is left exactly where the game authored it. Drawing terrain at
//     a guessed offset would look authoritative and be wrong.
//
// Layout is VIEW-ONLY. Positions on screen would no longer match the numbers
// in the files, so the gizmo is detached while it is on.
let trackLayout = false;
let nudgeBusy = false;      // one arrow-key write in flight at a time

function frameKey(mod) {
  return String(mod).toLowerCase().replace(/^lvl_[a-z0-9]+_(tile_)?/, '');
}

// Attach every node and every drawn surface to the module it belongs to.
// Nodes join on their source FILENAME (97% on both train levels; the 3% that
// miss are the level-wide files -- skydome, spawn, enemy table, trainmgr).
function assignFrames() {
  const frames = levelEvents?.frames || [];
  if (!frames.length) return;
  const cars = frames.filter((f) => f.kind === 'train_car');
  const keyed = frames.map((f) => [frameKey(f.module), f]);

  for (const n of level.nodes) {
    const src = (n.source || '').toLowerCase();
    const hit = keyed.find(([k]) => k && src.includes(k));
    n.frame = hit ? hit[1].id : null;
  }
  // Surfaces join on the MESH name, and only for cars -- see above.
  for (const o of renderGroup.children) {
    const nm = (o.userData.render?.name || '').toLowerCase();
    const hit = cars.find((f) => nm.includes(frameKey(f.module)));
    o.userData.frame = hit ? hit.id : null;
  }
  assignTileFrames(frames);
}

// Draw the copies you make.
//
// Level surfaces are baked in WORLD space and their nodes normally carry an
// identity matrix, which is why the viewport could get away with drawing every
// chunk once, where its vertices say. A duplicate breaks that: the new node
// points at the same chunk and carries an offset matrix, so the copy exists in
// the file and had nowhere to appear on screen. The Inspector admitted as much
// ("the viewport draws the surface at its authored vertices and ignores this
// matrix") -- which is a note explaining a limitation rather than a reason for
// it. So: any node that instances a drawn chunk with a NON-identity matrix
// gets a clone of that chunk, positioned by its matrix.
const instanceGroup = new THREE.Group();
scene.add(instanceGroup);

function setGrid(on) {
  gridOn = !!on;
  gridGroup.visible = gridOn;
  try { localStorage.setItem('rdr.grid', gridOn ? '1' : '0'); } catch {}
  remember('gridOn', gridOn);
  updateGrid();
}

// Park the grid under whatever is selected, at that object's foot, and snap
// its height to whole metres so it reads as a level rather than a hover.
function updateGrid() {
  if (!gridOn) return;
  const anchor = worldSel ? worldSel.centre
    : (selected != null && meshes.get(selected)
        ? meshes.get(selected).getWorldPosition(new THREE.Vector3())
        : controls.target);
  let foot = anchor.y;
  const mesh = worldSel ? worldSel.mesh
    : (selected != null ? meshForNode(selected) : null);
  if (mesh) {
    const b = new THREE.Box3().setFromObject(mesh);
    if (!b.isEmpty()) foot = b.min.y;
  }
  placeGrid.position.set(Math.round(anchor.x), Math.round(foot),
                         Math.round(anchor.z));
  const p = dropLine.geometry.attributes.position;
  p.setXYZ(0, anchor.x, anchor.y, anchor.z);
  p.setXYZ(1, anchor.x, placeGrid.position.y, anchor.z);
  p.needsUpdate = true;
  dropLine.geometry.computeBoundingSphere();
}

function clearInstances() {
  while (instanceGroup.children.length) {
    const c = instanceGroup.children.pop();
    instanceGroup.remove(c);
  }
}

function addInstanceCopies() {
  clearInstances();
  if (!level || !renderGroup.children.length) return 0;
  const byChunk = new Map();
  for (const o of renderGroup.children)
    if (o.userData.render) byChunk.set(o.userData.render.chunk, o);

  let n = 0;
  for (const node of level.nodes) {
    const ri = node.props && node.props.resourceIndex;
    if (ri == null || ri === '') continue;
    const src = byChunk.get(Math.round(+ri) - 1);
    if (!src) continue;
    const p = node.pos || [0, 0, 0];
    const moved = Math.hypot(p[0], p[1], p[2]) > 1e-6;
    if (!moved) continue;           // the authored instance; already drawn
    const clone = new THREE.Mesh(src.geometry, src.material);
    applyMatrix(clone, node.rot, node.pos);
    clone.userData.render = src.userData.render;
    clone.userData.archive = src.userData.archive;
    clone.userData.instanceOf = node.uid;
    clone.userData.pickRank = PICK_SURFACE;
    clone.renderOrder = src.renderOrder;
    instanceGroup.add(clone);
    n++;
  }
  return n;
}

// Attribute the TERRAIN to track tiles.
//
// This was previously recorded as impossible, on the grounds that no chunk
// name contains a tile name -- which is true, they are all `MOD0_nnn`. The
// join is geometric instead, and it is exact.
//
// A tile is 90 m of track (every slot in the .til says `len 90`), and each
// tile's chunks begin with a long thin strip: the rail, 90 m along X and only
// a couple of metres across. Splitting the unattributed chunks at each of
// those strips gives, on TRAINGOO, **22 groups from 22 rail strips for 22
// distinct tile modules, covering all 121 chunks with none left over**.
//
// What this CANNOT do is say which tile is where in a given playthrough. The
// .til is a random-map spec -- `sets 3`, each `size 8` with `Random 2` -- so
// the game shuffles the palette at runtime. There is no stored answer to
// recover. Laying the groups out in palette order gives a representative
// arrangement, which is what the toast says.
function assignTileFrames(frames) {
  const tiles = frames.filter((f) => f.kind === 'tile');
  if (!tiles.length) return;
  const loose = renderGroup.children
    .filter((o) => o.userData.render && o.userData.frame == null)
    .sort((a, b) => a.userData.render.chunk - b.userData.render.chunk);
  if (!loose.length) return;

  const spanOf = (o) => {
    const g = o.geometry;
    if (!g.boundingBox) g.computeBoundingBox();
    const b = g.boundingBox;
    return [b.max.x - b.min.x, b.max.z - b.min.z];
  };
  let group = -1;
  for (const o of loose) {
    const [sx, sz] = spanOf(o);
    // The rail: as long as the tile and barely wider than the track.
    if (sx >= 89 && sx <= 91 && sz < 6) group++;
    o.userData.tileGroup = group < 0 ? null : group;
    // Tiles repeat in the palette, so a group maps to a SLOT by order.
    o.userData.frame = group >= 0 && group < tiles.length
      ? tiles[group].id : o.userData.frame;
  }
}

function applyTrackLayout() {
  const frames = levelEvents?.frames || [];
  const byId = new Map(frames.map((f) => [f.id, f]));
  // All three axes, not just x. A tile's step comes from its own bot/top seam
  // anchors, and two of them climb: TRAINGOO's `tgostr` steps
  // (90.00, -11.00, -18.53) and MSNCYN's `cynstr` (120.00, +18.21, +4.50).
  // Using x alone drew TRAINGOO's whole third tile set 11 m too high and
  // 18.5 m sideways -- while the tool reported the level as unfolded.
  const ZERO = [0, 0, 0];
  const off = (id) => (trackLayout && id && byId.has(id))
    ? (byId.get(id).offset || ZERO) : ZERO;
  const place = (o, id) => {
    if (o.userData.base === undefined)
      o.userData.base = [o.position.x, o.position.y, o.position.z];
    const d = off(id);
    o.position.set(o.userData.base[0] + (d[0] || 0),
                   o.userData.base[1] + (d[1] || 0),
                   o.userData.base[2] + (d[2] || 0));
  };

  for (const [uid, obj] of meshes) {
    const n = nodeOf(uid);
    if (!n) continue;
    place(obj, n.frame);
  }
  for (const o of renderGroup.children) place(o, o.userData.frame);
  // Re-attaching by hand here would put the handle back on the node proxy at
  // the origin for a world surface. syncSelectionUI knows which of the two
  // anchors this selection wants; it is also the only place that knows.
  if (trackLayout) { gizmo.detach(); worldSel = null; }
  else if (selected != null) syncSelectionUI(false);
  updateHud();
}

function setTrackLayout(on) {
  trackLayout = on;
  remember('trackLayout', trackLayout);
  applyTrackLayout();
  const cars = (levelEvents?.frames || []).filter((f) => f.kind === 'train_car');
  const tiles = (levelEvents?.frames || []).filter((f) => f.kind === 'tile');
  if (on) {
    const span = tiles.length
      ? Math.max(...tiles.map((f) => f.offset[0] + f.length)) : 0;
    toast(
      `Laid out along the track: ${cars.length} cars over `
      + `${Math.max(...cars.map((f) => f.offset[0]), 0).toFixed(0)} m, `
      + `and the terrain unfolded into ${tiles.length} tiles`
      + (span ? ` over ${span.toFixed(0)} m` : '') + '.\n'
      + 'View only. Editing is off until you switch back.', 'good');
  } else {
    toast('Back to authored positions — editing is on again.', 'good');
  }
}

function tagEvent(obj, node, rank) {
  obj.userData.uid = node.uid;
  obj.userData.node = node;
  obj.userData.pickRank = rank;
  obj.userData.type = { key: node.kind, label: KINDS[node.kind].label,
                        color: KINDS[node.kind].color };
  meshes.set(node.uid, obj);
}

// The level's own sun, instead of the one hardcoded for all 61 maps.
function applyLevelSun(s) {
  if (!s || s.kind !== 'directional' || !s.dir) return;
  // DirLight points from the surface toward the light, so the lamp goes the
  // other way. Distance is arbitrary for a directional light; keep it far.
  const d = new THREE.Vector3(-s.dir[0], -s.dir[1], -s.dir[2]).normalize();
  sun.position.copy(d.multiplyScalar(200));
  sun.updateMatrixWorld();
}

// The status bar carries the facts worth seeing regardless of which tab you
// are on: which level, how big it is, whether the disc is yours. The viewport
// HUD stays separate because it describes what you are looking AT, and it is
// only meaningful while the viewport is on screen.
function updateStatusBar() {
  const lv = $('#sbLevel');
  const sc = $('#sbScene');
  if (!lv) return;
  const sh = $('#startHint');
  if (sh) sh.style.display = level ? 'none' : '';
  if (!level) {
    lv.textContent = 'no level loaded';
    sc.textContent = '';
    return;
  }
  lv.textContent = level.name + (level.area ? ' / ' + level.area : '');
  const bits = [`${level.nodes.length} objects`];
  if (renderInfo)
    bits.push(`${renderInfo.decoded}/${renderInfo.chunks} surfaces`,
              `${renderInfo.tris.toLocaleString()} tris`);
  if (worldInfo) bits.push(`${worldInfo.meshes.length} bounds`);
  if (pendingLoads > 0) bits.push(`decoding (${pendingLoads})…`);
  sc.textContent = bits.join(' · ');
}

function updateHud() {
  updateStatusBar();
  if (!level) return;
  const s = level.stats || {};
  const cat = levelCatalog.find((l) => l.archive === level.archive);
  const what = cat
    ? `${cat.mode_label}${cat.missions?.length
        ? ` · mission ${cat.missions.map((m) => m.number).join(', ')} as ${cat.missions[0].character}`
        : ''}`
    : '';
  // What you need to know you are in the right place, and nothing else.
  let txt = `${level.name}${what ? '  [' + what + ']' : ''} — ` +
    `${level.nodes.length} objects` +
    (renderInfo ? ` — ${renderInfo.tris.toLocaleString()} tris` : '');
  // Everything below is the decoder talking about itself. True, useful when
  // something is wrong, and not what you want printed over the level while
  // you are moving a building -- so it folds.
  let more = Object.entries(s).map(([k, v]) => `${v} ${k}`).join(', ');
  if (worldInfo)
    more += `\ncollision: ${worldInfo.meshes.length} meshes, ` +
            `${worldInfo.tris.toLocaleString()} tris`;
  if (renderInfo)
    more += `   |   surfaces: ${renderInfo.decoded}/${renderInfo.chunks} chunks, ` +
            `${renderInfo.tris.toLocaleString()} tris`;
  // A parse warning is not telemetry -- it means part of this level did not
  // read -- so it stays on the line you can actually see.
  if (level.errors?.length) txt += `  |  ${level.errors.length} parse warning(s)`;
  // Decoding a cold level takes a few seconds; say so rather than showing an
  // empty viewport with no explanation.
  if (pendingLoads > 0)
    txt += `\ndecoding geometry and textures…  (${pendingLoads} job(s) running)`;
  if (placeMode)
    txt += `\nPLACE MODE — click anywhere to drop a copy of ` +
           `${nodeOf(selected)?.name || '?'} there.  Esc to stop.`;
  if (walk.on)
    txt += `\nWALK MODE — WASD move · mouse look · Shift sprint · ` +
           `Space/Ctrl up-down · Esc exit   [${camera.position.x.toFixed(1)}, ` +
           `${camera.position.y.toFixed(1)}, ${camera.position.z.toFixed(1)}]`;
  const main = $('#hudMain');
  if (main) { main.textContent = txt; $('#hudMore').textContent = more; }
  else $('#hud').textContent = txt;   // markup without the fold still works
}

// ------------------------------------------------------- collision geometry

let worldInfo = null;

function clearWorld() {
  while (worldGroup.children.length) {
    const c = worldGroup.children.pop();
    c.geometry?.dispose?.();
  }
}

let pendingLoads = 0;

function noteLoading(delta) {
  pendingLoads = Math.max(0, pendingLoads + delta);
  updateHud();
}

async function loadGeometry(archive) {
  const gen = loadGen;
  clearWorld();
  worldInfo = null;
  noteLoading(+1);
  try {
    const g = await api('geometry?' + levelQuery(archive, level?.manifest));
    if (gen !== loadGen) return;   // the level changed while this was in flight
    if (!g.meshes?.length) {
      updateHud();
      return;
    }
    for (const m of g.meshes) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position',
        new THREE.Float32BufferAttribute(m.positions, 3));
      geo.setIndex(m.indices);
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, worldMode === 'wire' ? worldWire : worldMat);
      mesh.userData.world = m;
      // The owning node's uid at the top level too: worldTargetFor scans
      // worldGroup by userData.uid, and without this the gizmo for a clicked
      // shell anchored on the node's suppressed origin marker -- selection
      // appeared to grab something far away.
      if (m.uid != null) mesh.userData.uid = m.uid;
      worldGroup.add(mesh);
    }
    worldGroup.visible = worldMode !== 'off';
    worldInfo = g;
    updateHud();
    if (selected != null) syncSelectionUI(false);
    else if (chunkSel != null) showInspectorChunk(chunkSel);
  } catch (e) {
    toast('Collision geometry unavailable: ' + e.message, 'bad');
  } finally {
    noteLoading(-1);
  }
}

function setWorldMode(mode) {
  worldMode = mode;
  remember('worldMode', worldMode);
  worldGroup.visible = mode !== 'off';
  for (const m of worldGroup.children)
    m.material = mode === 'wire' ? worldWire : worldMat;
  syncVpBar();
}

// The viewport chips and the View-fold selects are the same switches; both
// call the same setters, this keeps whichever pair the user is not touching
// truthful.
function syncVpBar() {
  const a = $('#vpArt'), c = $('#vpCol');
  if (a) a.classList.toggle('on', renderMode !== 'off');
  if (c) c.classList.toggle('on', worldMode !== 'off');
}

function clearRender() {
  while (renderGroup.children.length) {
    const c = renderGroup.children.pop();
    c.geometry?.dispose?.();
  }
}

// When real actors are drawn, the marker cones and capsules sit INSIDE them
// and poke out. Fade them rather than hiding them: they stay visible enough
// to show where an object without a model is, and stay selectable.
function fadeProxies(on) {
  for (const k of ['enemy', 'spawn']) {
    const g = groups[k];
    if (!g) continue;
    g.traverse((o) => {
      if (!o.material) return;
      if (o.userData.baseOpacity === undefined)
        o.userData.baseOpacity = o.material.opacity ?? 1;
      o.material.transparent = on || o.userData.baseOpacity < 1;
      o.material.opacity = on ? 0.18 : o.userData.baseOpacity;
      o.material.depthWrite = !on;
      o.material.needsUpdate = true;
    });
  }
}

function clearCast() {
  while (castGroup.children.length) {
    const c = castGroup.children.pop();
    c.geometry?.dispose?.();
    c.material?.map?.dispose?.();
    c.material?.dispose?.();
  }
  castInfo = null;
}

// Populate the level with its real actors. One decoded mesh per distinct
// enemy Type, instanced at every node that uses it -- PERFNEW has 23 enemies
// but only 6 kinds, so this is 6 decodes, not 23.
async function loadCast(archive) {
  const gen = loadGen;
  clearCast();
  if (!level) return;
  noteLoading(+1);
  try {
    const r = await api('level_characters?' + levelQuery(archive, level.manifest)
      + '&lod=' + castLod);
    if (gen !== loadGen) return;   // the level changed while this was in flight
    const byType = new Map();
    for (const c of r.characters) {
      if (!c.model) continue;
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position',
        new THREE.Float32BufferAttribute(c.model.positions, 3));
      if (c.model.uvs.length * 3 === c.model.positions.length * 2)
        geo.setAttribute('uv', new THREE.Float32BufferAttribute(c.model.uvs, 2));
      geo.setIndex(c.model.indices);
      geo.computeVertexNormals();
      byType.set(c.type, { geo, mat: characterMaterials(geo, c.model.groups, (i) =>
        `/api/character.png?archive=${encodeURIComponent(archive)}&name=${encodeURIComponent(c.file)}&i=${i}`) });
    }

    let placed = 0, spawns = 0;
    for (const n of level.nodes) {
      // Enemies name their actor; spawns are the player, whom levelflow.csv
      // names per mission.
      let t = null;
      if (n.kind === 'enemy') t = (n.props?.Type || '').toString().trim().toLowerCase();
      else if (n.kind === 'spawn' && castSpawns) t = r.player;
      const hit = t && byType.get(t);
      if (!hit || !n.pos) continue;
      const m = new THREE.Mesh(hit.geo, hit.mat);
      m.position.set(n.pos[0], n.pos[1], n.pos[2]);
      if (n.angle != null) m.rotation.y = THREE.MathUtils.degToRad(n.angle);
      m.userData.castFor = n.uid;
      castGroup.add(m);
      if (n.kind === 'spawn') spawns++;
      else placed++;
    }
    castInfo = {
      placed, spawns, player: r.player,
      kinds: r.characters.length,
      decoded: r.characters.filter((c) => c.model).length,
      missing: r.characters.filter((c) => !c.model)
        .map((c) => `${c.type} (${c.why})`),
    };
  } catch (e) {
    castInfo = { placed: 0, kinds: 0, decoded: 0, missing: [e.message] };
  } finally {
    noteLoading(-1);
    fadeProxies(castGroup.visible);
    renderLayers();
  }
}

async function loadRenderGeometry(archive, attempt = 0) {
  clearRender();
  renderInfo = null;
  const gen = loadGen;
  noteLoading(+1);
  let retry = false;
  try {
    let g = await api('render_geometry?' + levelQuery(archive, level?.manifest));
    if (gen !== loadGen) return;   // the level changed while this was in flight
    // `chunks:0` with no source means the bundle could not be read this
    // instant -- almost always because the disc handles were released for
    // PCSX2 mid-request. One retry recovers it; without this the level just
    // sits there empty and looks broken. The retry runs AFTER the finally --
    // returning a recursive promise from inside try ran the finally early and
    // double-decremented the loading counter.
    if (!g.chunks && !g.source && attempt < 1) {
      retry = true;
      return;
    }
    for (const m of g.meshes) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(m.positions, 3));
      if (m.colors?.length === m.positions.length)
        geo.setAttribute('color', new THREE.Float32BufferAttribute(m.colors, 3));
      if (m.uvs?.length * 3 === m.positions.length * 2)
        geo.setAttribute('uv', new THREE.Float32BufferAttribute(m.uvs, 2));
      geo.setIndex(m.indices);
      // A level mesh is a container of sub-meshes that each draw with their
      // own texture. Declaring them as geometry groups lets one Mesh carry a
      // material array, so a storefront gets its siding, boards and window
      // separately instead of all three wearing whichever texture won.
      if (m.groups?.length > 1)
        for (const g2 of m.groups) geo.addGroup(g2.start, g2.count, 0);
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, renderPlain);
      mesh.userData.render = m;
      mesh.userData.archive = archive;
      renderGroup.add(mesh);
    }
    setRenderMode(renderMode);
    renderGroup.visible = renderMode !== 'off';
    renderInfo = g;
    // The surfaces have only just appeared. Both of these ran earlier, when
    // renderGroup was still empty, so the chunk-to-car join and the outliner's
    // "unassigned surfaces" group had nothing to work with.
    if (levelEvents && !levelEvents.error) { assignFrames(); applyTrackLayout(); }
    renderObjectList();
    reframeFromGeometry();
    updateHud();
    // Re-anchor the selection now the surfaces exist. The reload path
    // restores it while this group is still empty, which dropped the gizmo
    // at the world origin and read as "the thing I just moved vanished".
    if (selected != null) syncSelectionUI(false);
    else if (chunkSel != null) showInspectorChunk(chunkSel);
    // Materials exist now, so the level's own ambient and fog can be put on
    // them. Deliberately not awaited: it is a look, not a load step.
    applyLevelEnvironment();
  } catch (e) {
    toast('Render geometry unavailable: ' + e.message, 'bad');
  } finally {
    noteLoading(-1);
  }
  if (retry && gen === loadGen) {
    await new Promise((r) => setTimeout(r, 400));
    return loadRenderGeometry(archive, attempt + 1);
  }
}

let bakedLight = true;
let showSecondPass = true;

function setRenderMode(mode) {
  renderMode = mode;
  remember('renderMode', renderMode);
  renderGroup.visible = mode !== 'off';
  instanceGroup.visible = mode !== 'off';
  syncVpBar();
  for (const m of renderGroup.children) {
    const d = m.userData.render || {};
    const hasVC = !!m.geometry.getAttribute('color');
    const hasUV = !!m.geometry.getAttribute('uv');
    const multi = m.geometry.groups.length > 1;
    // Which variant this surface needs. `d.lit` is false when the mesh's
    // vertex colours are all zero, which is the game's way of saying "this
    // pass is not vertex lit" -- multiplying them in would render it black.
    const variant = (d.second_pass ? '2p' : '')
      + (hasVC && d.lit && bakedLight ? 'lit' : '');
    if (mode === 'textured' && hasUV && multi) {
      // One material per group, in group order. A group with texture null is
      // genuinely untextured in the game data (resource 0) and draws plain.
      m.material = m.geometry.groups.map((_, i) => {
        const g = d.groups[i] || {};
        return materialFor(m.userData.archive, g.texture, variant, g.colour);
      });
      for (let i = 0; i < m.geometry.groups.length; i++)
        m.geometry.groups[i].materialIndex = i;
    } else if (mode === 'textured' && hasUV
               && (d.texture != null || d.second_pass))
      // `|| d.second_pass` so an untextured decal still reaches the decal
      // material rather than dropping through to the opaque one below.
      m.material = materialFor(m.userData.archive, d.texture, variant,
                               d.groups?.[0]?.colour);
    else if (mode === 'vertexcolor' && hasVC) m.material = renderMat;
    else m.material = renderPlain;
    // Second passes must draw after the surface they sit on.
    m.renderOrder = d.second_pass ? 1 : 0;
  }
  // One owner for `.visible`: the second-pass switch and the outliner's
  // per-surface eyes both live in applyHidden, so they cannot overwrite each
  // other depending on which ran last.
  refreshDeletedChunks();
  addInstanceCopies();
  applyHidden();
}

function frameLevel(b) {
  if (!b) return;
  const min = new THREE.Vector3(...b.min), max = new THREE.Vector3(...b.max);
  const c = min.clone().add(max).multiplyScalar(0.5);
  const size = max.clone().sub(min).length() || 40;
  controls.target.copy(c);
  camera.position.set(c.x + size * 0.45, c.y + size * 0.4, c.z + size * 0.45);
  camera.far = Math.max(2000, size * 6);
  camera.updateProjectionMatrix();
  grid.position.set(c.x, min.y, c.z);
  const g = Math.ceil(size / 40) * 40;
  grid.scale.setScalar(Math.max(1, g / 400));
  controls.update();
}

// Has the user taken control of the camera since the level opened? If so, a
// late-arriving reframe would yank the view out from under them.
let userMovedCamera = false;
controls.addEventListener('start', () => { userMovedCamera = true; });

// Surfaces that describe the horizon rather than the level. Excluding them
// matters because they are an order of magnitude bigger than the map: on TOMB
// `zone_skydome_cpv_01` spans 646 m and the backdrop `zone_ground_01` 334 m,
// while the play area is 69 m and the MEDIAN surface is 7 m.
const BACKDROP = /skydome|farland|backdrop/i;

// Re-frame from the drawn geometry -- but only as a FALLBACK.
//
// The node-derived `content_bounds` is normally the better answer, because it
// covers the volumes and paths that bound where the player can actually go.
// Framing to the drawn surfaces instead made TOMB worse, not better: 646 m
// against a correct 69 m, because the sky is geometry too. So this only fires
// when the node box is degenerate -- a level whose objects all sit at the
// origin, which is exactly the case `content_bounds` cannot describe.
function reframeFromGeometry() {
  if (userMovedCamera || walk.on || !level) return;
  const b = level.content_bounds || level.bounds;
  if (b) {
    const span = Math.max(b.max[0] - b.min[0], b.max[1] - b.min[1],
                          b.max[2] - b.min[2]);
    if (span >= 5) return;            // the node box is usable; leave it alone
  }
  const box = new THREE.Box3();
  for (const o of renderGroup.children) {
    if (!o.visible || BACKDROP.test(o.userData.render?.name || '')) continue;
    box.expandByObject(o);
  }
  for (const o of worldGroup.children) if (o.visible) box.expandByObject(o);
  if (box.isEmpty()) return;
  if (box.getSize(new THREE.Vector3()).length() < 1) return;
  frameLevel({ min: box.min.toArray(), max: box.max.toArray() });
  userMovedCamera = false;      // frameLevel drives controls, which fires 'start'
}

// ------------------------------------------------------------- interaction

const ray = new THREE.Raycaster();
ray.params.Line.threshold = 0.6;
ray.params.Points.threshold = 0.6;

// Click priority, lowest wins. Small markers and splines you must aim at beat
// solid surfaces; solid surfaces beat translucent volumes you can see through.
const PICK_MARKER = 0;
const PICK_SURFACE = 1;
const PICK_VOLUME = 2;

// Volumes rank BELOW surfaces by default, and that was the right call for the
// problem it solved: TOMB's 361 see-through trigger boxes float in front of
// everything, so aiming at a storefront kept selecting a room volume and
// buildings felt unselectable.
//
// The opposite case is just as real. When the thing you are hunting IS a
// trigger volume, ranking it last means the wall in front swallows every
// click. Neither order is correct for both jobs, so it is a switch rather
// than a cleverer heuristic -- a heuristic here would be right about 70% of
// the time and unpredictable, which is worse than a control you can see.
//
// With it on, volumes outrank solid surfaces. Markers stay first either way:
// a spline node you had to aim at is never an accident.
let helpersFirst = false;
try { helpersFirst = localStorage.getItem('rdr.pickhelpers') === '1'; } catch {}

function setHelpersFirst(on) {
  helpersFirst = !!on;
  try { localStorage.setItem('rdr.pickhelpers', helpersFirst ? '1' : '0'); } catch {}
  remember('helpersFirst', helpersFirst);
  renderLayers();
}

function rankOf(rank) {
  if (!helpersFirst) return rank;
  if (rank === PICK_VOLUME) return PICK_SURFACE;
  if (rank === PICK_SURFACE) return PICK_VOLUME;
  return rank;
}

// What a click may select: everything, only the drawn world, or only
// collision. Deliberately NOT persisted -- a session that boots with clicks
// limited to collision reads as "selection is broken".
let pickFilter = 'all';

function setPickFilter(which) {
  pickFilter = which;
  if (which === 'collision' && worldMode === 'off') setWorldMode('solid');
  if (which === 'art' && renderMode === 'off') setRenderMode('textured');
  for (const b of document.querySelectorAll('#vpPick button'))
    b.classList.toggle('on', b.dataset.pick === which);
  updateHud();
}

const ptr = new THREE.Vector2();
let downAt = null;

// LEFT selects. The other buttons drive the camera, and this used to take
// whichever one it was given: `downAt` was a single slot shared by every
// button, so pressing left while the right button was already down overwrote
// it, and RELEASING THE RIGHT BUTTON then ran the picker at the left button's
// coordinates -- a right-click that had not travelled 4px selected whatever
// was under it. Going through watchButtons is also what makes a left click
// arrive AT ALL while the fly button is held; see its note.
//
// Alt+LMB is the orbit escape hatch and is not a selection. It is skipped
// here rather than intercepted upstream: a capture-phase listener that calls
// stopImmediatePropagation() on this element would also silence
// OrbitControls and TransformControls, which are listeners on the very same
// element (measured -- capture runs before bubble AT the target, and the flag
// skips the element's remaining listeners in both phases).
watchButtons(renderer.domElement, (kind, btn, e) => {
  if (btn !== 0 || e.altKey) return;
  if (kind === 'down') { downAt = [e.clientX, e.clientY]; return; }
  if (!downAt) return;
  const moved = Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]);
  downAt = null;
  if (moved > 4 || gizmo.dragging) return;

  if (placeMode) {
    if (placeBusy) return;             // a reload is in flight; one at a time
    const p = surfacePoint(e, true);   // strict: real geometry only, no plane
    if (p) placeAt(p, e.shiftKey);
    else toast('Aim at the ground — a click into the sky places nothing.');
    return;   // a click in place mode places; it does not re-select
  }

  const r = renderer.domElement.getBoundingClientRect();
  ptr.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  ptr.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  ray.setFromCamera(ptr, camera);

  const targets = [];
  // Per-mesh visible=false is the volume-type filter; THREE's raycaster does
  // not honour it on its own, so a hidden AI room would still swallow clicks.
  if (pickFilter !== 'art')
    for (const [k, g] of Object.entries(groups))
      if (g.visible) targets.push(...g.children.filter((o) => o.visible !== false));
  // The decoded collision itself is a click target. It never was: only its
  // node marker could be picked, the marker is an octahedron at the world
  // origin for every baked shell, and the layer holding it defaults off --
  // which is why moving collision felt impossible.
  if (pickFilter !== 'art' && worldGroup.visible)
    targets.push(...worldGroup.children.filter((o) => o.visible !== false));
  // The drawn characters are click targets too. Without this you have to aim
  // at the marker cone hidden inside the actor, which is exactly backwards --
  // the thing you can see should be the thing you can select.
  if (pickFilter !== 'collision' && castGroup.visible)
    targets.push(...castGroup.children);
  // THE WORLD ITSELF is a click target. It was not, which made the whole
  // Shape feature unreachable: the Home card says "click a building, then use
  // Shape", and clicking a building hit nothing and deselected instead. The
  // only selectable things were marker cones, so the honest reading of the
  // old behaviour is that you could not select the level at all.
  if (pickFilter !== 'collision' && renderGroup.visible)
    targets.push(...renderGroup.children);
  // Copies are clickable too. `instanceOf` carries the node that placed one,
  // so clicking a copy selects the copy rather than the original.
  if (pickFilter !== 'collision' && renderGroup.visible)
    targets.push(...instanceGroup.children);
  const hits = ray.intersectObjects(targets, true);
  const add = e.shiftKey || e.ctrlKey;
  if (!hits.length) return add ? null : select(null);

  // Resolve by INTENT first, depth second.
  //
  // Depth alone picks the wrong thing here, and not rarely. TOMB has 361
  // trigger/AI boxes drawn at 16% opacity with depthWrite off; their front
  // faces float in mid-air in front of everything. Aim at a storefront and the
  // nearest hit is `box_AIROOM_Medium_northeaststreet_01`, a room volume you
  // can see straight through -- so the building you clicked was never
  // selectable, which is the real reason moving one felt impossible.
  //
  // Ranking says: a cone or spline node you had to aim at beats a solid
  // surface, and a solid surface beats a see-through shell. Within a rank the
  // nearest still wins, so two overlapping boxes behave as you'd expect.
  let pick = null, pickRank = 99, pickDist = Infinity;
  for (const h of hits) {
    let o = h.object;
    while (o && o.userData.uid === undefined && o.userData.castFor === undefined
           && o.userData.render === undefined
           && o.userData.world === undefined) o = o.parent;
    if (!o) continue;
    // Collision ranks with volumes: in the default view the drawn wall wins,
    // and the Collision pick filter (or hiding Art) reaches the shell.
    const rank = rankOf(o.userData.pickRank
              ?? (o.userData.world !== undefined ? PICK_VOLUME
                : o.userData.castFor !== undefined ? PICK_MARKER : PICK_SURFACE));
    if (rank < pickRank || (rank === pickRank && h.distance < pickDist)) {
      pick = o; pickRank = rank; pickDist = h.distance;
    }
    if (pickRank === PICK_MARKER) break;   // nothing can beat this; stop early
  }
  if (!pick) return add ? null : select(null);

  if (pick.userData.uid !== undefined || pick.userData.castFor !== undefined) {
    select(pick.userData.uid !== undefined ? pick.userData.uid
                                          : pick.userData.castFor, add);
    return;
  }
  // A copy you placed: select the node that placed it.
  if (pick.userData.instanceOf !== undefined)
    return select(pick.userData.instanceOf, add);
  // A collision mesh. The server sends the owning node's uid with every
  // shell; shells with no node cannot be moved as a unit, so say which shell
  // it is rather than selecting nothing in silence.
  if (pick.userData.world !== undefined) {
    const w = pick.userData.world;
    if (w.uid != null) return select(w.uid, add);
    return toast(w.name + ' — this shell has no placement node; reshape it '
      + 'through Shape on the surface it belongs to.');
  }
  // A world surface. Select the instance node that draws it, so the Inspector
  // and Shape controls apply to the thing that was actually clicked.
  const uid = uidForChunk(pick.userData.render.chunk);
  if (uid !== undefined) return select(uid, add);
  // No node claims it. That used to end in a toast saying there was nothing
  // to move, which was never true: the shape edit patches the surface itself
  // and never needed an instance. Open it by chunk instead.
  showInspectorChunk(pick.userData.render.chunk);
});

// The chunk <-> node join, both directions. Built once per level rather than
// scanned per click; a Brimstone area has 896 nodes and 343 drawn chunks.
let chunkUid = null;      // chunk  -> uid of the node that instances it
let uidChunk = null;      // uid    -> chunk

function buildChunkJoin() {
  chunkUid = new Map();
  uidChunk = new Map();
  for (const n of (level?.nodes || [])) {
    const ri = n.props && n.props.resourceIndex;
    if (ri == null) continue;
    // resourceIndex is ONE-BASED, like every reference in this engine.
    const c = Math.round(+ri) - 1;
    uidChunk.set(n.uid, c);
    if (!chunkUid.has(c)) chunkUid.set(c, n.uid);
  }
}

function uidForChunk(chunk) {
  if (chunkUid === null) buildChunkJoin();
  return chunkUid.get(chunk);
}

// The drawn surface a node instances, if it draws one. This is what makes a
// building frameable and measurable even though its own node sits at [0,0,0].
function meshForNode(uid) {
  if (uidChunk === null) buildChunkJoin();
  const chunk = uidChunk.get(uid);
  if (chunk === undefined) return null;
  for (const o of renderGroup.children)
    if (o.userData.render && o.userData.render.chunk === chunk) return o;
  for (const o of worldGroup.children)
    if (o.userData.uid === uid) return o;
  return null;
}

// Should the gizmo drag the world instead of the node?
//
// Only when moving the node would visibly do nothing: it instances a drawn
// surface, and its own transform is identity so there is no per-instance
// placement to edit. Nodes that DO carry a real position keep the ordinary
// behaviour -- this deliberately does not take over the whole gizmo.
function worldTargetFor(uid) {
  if (uid == null || !level || trackLayout) return null;
  const n = nodeOf(uid);
  if (!n) return null;
  const p = n.pos || [0, 0, 0];
  if (Math.hypot(p[0], p[1], p[2]) > 1e-6) return null;   // it has a real place
  if (uidChunk === null) buildChunkJoin();
  const chunk = uidChunk.get(uid);
  if (chunk === undefined) return null;
  return worldTargetForChunk(chunk, n.name);
}

// The same anchor, found by chunk rather than by node. Surfaces that NO node
// references -- 49% of the triangles on TRAIN -- are reshapeable too, since
// the edit patches the surface itself and never needed an instance.
function worldTargetForChunk(chunk, fallbackName) {
  if (chunk == null || !level || trackLayout) return null;
  // Drawn surface first, then collision. A `bound_` node has exactly the same
  // problem -- it sits at the origin too -- and collision is what you bump
  // into, so a building moved without it leaves an invisible wall behind.
  let mesh = null, kind = null;
  for (const o of renderGroup.children)
    if (o.userData.render && o.userData.render.chunk === chunk) {
      mesh = o; kind = 'render'; break;
    }
  if (!mesh)
    for (const o of worldGroup.children)
      if (o.userData.world && o.userData.world.resourceIndex === chunk) {
        mesh = o; kind = 'bound'; break;
      }
  if (!mesh) return null;
  const box = new THREE.Box3().setFromObject(mesh);
  if (box.isEmpty()) return null;
  const info = kind === 'render' ? mesh.userData.render : mesh.userData.world;
  return {
    chunk, kind, mesh,
    name: info.name || fallbackName || `chunk ${chunk}`,
    centre: box.getCenter(new THREE.Vector3()),
  };
}

function select(uid, add = false) {
  // No separate collision entity: a shell that belongs to a model IS that
  // model. Clicking one (Collision pick filter, or the layer) selects the
  // model, whose panel carries the solid / walk-through switch.
  if (uid != null && !collisionAlone) {
    const bn = nodeOf(uid);
    if (bn && bn.kind === 'bound' && bn.linked !== undefined && bn.linked !== null
        && nodeOf(bn.linked)) uid = bn.linked;
  }
  chunkSel = null;      // any real selection supersedes a bare-surface one
  if (uid == null) {
    for (const u of selection) setHighlight(u, false);
    selection.clear();
    selected = null;
    // Placing needs a source object; dropping the selection ends it.
    if (placeMode) setPlaceMode(false);
  } else if (add) {
    if (selection.has(uid) && selection.size > 1) {
      selection.delete(uid);
      setHighlight(uid, false);
      if (selected === uid) selected = [...selection][selection.size - 1] ?? null;
    } else {
      selection.add(uid);
      setHighlight(uid, true);
      selected = uid;
    }
  } else {
    for (const u of selection) setHighlight(u, false);
    selection.clear();
    selection.add(uid);
    setHighlight(uid, true);
    selected = uid;
  }

  syncSelectionUI();
}

function syncSelectionUI(scroll = true) {
  // A world surface gets the anchor at its own centre; everything else keeps
  // the marker it always had. One selection can only be one of the two, and
  // multi-select stays on the node path -- reshape ops are per chunk.
  const wt = selection.size === 1 ? worldTargetFor(selected) : null;
  worldSel = wt;
  const obj = wt ? worldAnchor
                 : (selected != null ? meshes.get(selected) : null);
  if (wt) worldAnchor.position.copy(wt.centre);
  if (obj && !trackLayout) gizmo.attach(obj); else gizmo.detach();
  renderInspector();
  for (const row of document.querySelectorAll('#objList .row'))
    row.classList.toggle('sel', selection.has(+row.dataset.uid));
  if (scroll && selected != null)
    document.querySelector(`#objList .row[data-uid="${selected}"]`)
      ?.scrollIntoView({ block: 'nearest' });
}

// Select-all, without doing the per-object work n times.
//
// The old Ctrl+A called select() in a loop, and select() rebuilds the whole
// Inspector and then walks every row in the outliner. On Brimstone that is 900
// inspector rebuilds and 810,000 row visits -- measured at 3.2 seconds with
// the main thread locked. Here the highlighting is per object and everything
// else happens once.
//
// It also now respects the per-object eyes. "Select all visible" that included
// things you had explicitly hidden was simply the wrong answer.
function selectAllVisible() {
  for (const u of selection) setHighlight(u, false);
  selection.clear();
  let skippedHidden = 0;
  for (const n of level.nodes) {
    if (!groups[n.kind]?.visible) continue;
    if (hiddenUids.has(n.uid)) { skippedHidden++; continue; }
    selection.add(n.uid);
    setHighlight(n.uid, true);
  }
  selected = selection.size ? [...selection][selection.size - 1] : null;
  syncSelectionUI(false);
  return skippedHidden;
}

function nodeOf(uid) { return level?.nodes.find((n) => n.uid === uid); }

gizmo.addEventListener('mouseDown', () => {
  if (worldSel) {
    // The surface is drawn from baked world-space vertices, so its own
    // position/rotation/scale all start at identity and are free to carry the
    // live preview. Rotation and scale have to happen about the anchor rather
    // than about the object's origin -- which is the world origin here -- so
    // the mesh is temporarily parented to the anchor for the duration of the
    // drag. That makes the preview pivot exactly where the written ops do.
    worldDrag = {
      chunk: worldSel.chunk,
      name: worldSel.name,
      mesh: worldSel.mesh,
      kind: worldSel.kind,
      home: worldSel.mesh.parent,
      from: worldAnchor.position.clone(),
    };
    worldAnchor.attach(worldSel.mesh);   // keeps world transform, reparents
    return;
  }
  // Snapshot every selected object so the whole set can move by one delta.
  dragFrom = new Map();
  for (const u of selection) {
    const o = meshes.get(u);
    if (o) dragFrom.set(u, [o.position.x, o.position.y, o.position.z]);
  }
});

function onGizmoDrag() {
  if (worldDrag) return onWorldDrag();
  if (selected == null || !dragFrom) return;
  const anchor = meshes.get(selected);
  const from = dragFrom.get(selected);
  if (!anchor || !from) return;
  const d = [anchor.position.x - from[0], anchor.position.y - from[1],
             anchor.position.z - from[2]];

  for (const u of selection) {
    const o = meshes.get(u);
    const f = dragFrom.get(u);
    if (!o || !f) continue;
    if (u !== selected) o.position.set(f[0] + d[0], f[1] + d[1], f[2] + d[2]);
    const off = o.userData.yOffset || 0;
    // Only record a position that actually moved. `objectChange` also fires
    // while ROTATING, where the delta is zero -- recording it unconditionally
    // filled `dirty` with identity positions, so a rotation was discarded and
    // then reported as a successful save, complete with a journal entry and a
    // 3-second reload. A green toast for an edit that did not happen is worse
    // than an error.
    const moved = Math.abs(d[0]) > 1e-6 || Math.abs(d[1]) > 1e-6
      || Math.abs(d[2]) > 1e-6;
    if (moved) {
      // `f` is where this object sat when the drag began, in the same space
      // as o.position -- so it is the exact place a refused write must put
      // it back to. Seeded here rather than in rememberHome() because by now
      // the object has already moved.
      if (f && !homePos.has(u)) homePos.set(u, new THREE.Vector3(f[0], f[1], f[2]));
      dirty.set(u, [o.position.x, o.position.y - off, o.position.z]);
    }
  }

  // Rotation is a real edit, but only for nodes that carry a heading.
  if (gizmo.mode === 'rotate') {
    for (const u of selection) {
      const o = meshes.get(u);
      const n = nodeOf(u);
      if (!o || !n || n.angle == null) continue;
      const deg = THREE.MathUtils.radToDeg(o.rotation.y);
      if (Math.abs(deg - n.angle) > 1e-4) dirtyAngle.set(u, deg);
    }
  }
  // Update the live numbers WITHOUT rebuilding the panel.
  //
  // This used to call renderInspector(), which tears down and re-creates every
  // control -- for a building that is a ~720px stack including the Shape
  // section and a colour picker per texture group -- on every frame of a drag.
  // Besides being wasteful it destroyed the input the user might be in, and it
  // rebuilt the 3D preview canvas mid-gesture. Only three numbers change.
  refreshInspectorPosition();
}

// Live feedback while a world surface is being dragged. The mesh itself needs
// no work -- it is parented to the anchor, so it already follows. This only
// keeps the Shape boxes honest, showing the total that WOULD be written,
// which is the accumulated shape plus what this gesture has added so far.
function onWorldDrag() {
  const t = shapeOf(worldDrag.chunk);
  const d = worldAnchor.position.clone().sub(worldDrag.from);
  const live = {
    tx: t.tx + d.x, ty: t.ty + d.y, tz: t.tz + d.z,
    rot: t.rot + THREE.MathUtils.radToDeg(worldAnchor.rotation.y),
    s: t.s * worldAnchor.scale.x,
  };
  for (const [key, val] of Object.entries(live)) {
    const inp = document.querySelector(`#inspector input[data-shape="${key}"]`);
    if (inp && document.activeElement !== inp)
      inp.value = key === 's' ? val.toFixed(3) : val.toFixed(2);
  }
  // Written straight to the HUD's first line rather than through updateHud(),
  // which rebuilds the whole line from level stats -- not something to do per
  // frame. It must be the LINE, not #hud itself: #hud now owns the telemetry
  // fold and its toggle, and writing textContent on the parent would delete
  // both of them mid-drag.
  ($('#hudMain') || $('#hud')).textContent =
    `${worldDrag.name} — move ${live.tx.toFixed(1)}, ${live.ty.toFixed(1)}, `
    + `${live.tz.toFixed(1)} m`
    + (Math.abs(live.rot) > 1e-4 ? `, turn ${live.rot.toFixed(1)}°` : '')
    + (Math.abs(live.s - 1) > 1e-4 ? `, scale ${live.s.toFixed(3)}×` : '')
    + '   — release to write, Ctrl+Z to undo';
}

// Did we just move the art away from its collision?
//
// Collision is a separate, much coarser mesh -- SHER70 draws 300 surfaces and
// has 41 collision meshes, the big ones 60-100 m across, and their names say
// `bound_grp_` because that is what they are: groups. A barrel's collision is
// not a barrel-shaped object that could follow it; those triangles live inside
// a shared volume covering much of the level.
//
// So this does NOT offer to move collision with it -- moving that group would
// drag every other object's collision along. It says what happened, at the
// moment it happens, instead of leaving you to find the invisible wall by
// walking into it in game.
function collisionLeftBehind(mesh) {
  if (!worldInfo?.meshes?.length || !worldGroup.children.length) return null;
  const box = new THREE.Box3().setFromObject(mesh);
  let hit = null;
  for (const o of worldGroup.children) {
    if (!o.userData.world) continue;
    const b = new THREE.Box3().setFromObject(o);
    if (b.intersectsBox(box)) {
      const s = b.getSize(new THREE.Vector3());
      const covers = s.x * s.y * s.z;
      if (!hit || covers > hit.covers)
        hit = { name: o.userData.world.name || 'a collision mesh', covers };
    }
  }
  if (!hit) return null;
  return `Moved the art, not what you bump into.\n`
    + `${hit.name} still covers where this was. It is a shared collision `
    + `volume (~${Math.round(hit.covers).toLocaleString()} m³), not this `
    + `object's own hitbox, so moving it would move collision for everything `
    + `else inside it too — select it under Collision shells if that is what `
    + `you want.`;
}

// Fold the finished gesture into the chunk's shape and write it.
//
// The shape state holds TOTALS against the vanilla geometry rather than a
// running sum of deltas, and this preserves that: the gesture is added to the
// stored total once, then `applyShape` rebuilds the whole op list from
// vanilla. Reloading the level throws away the preview transform, so the mesh
// is restored to the scene first -- otherwise a failed write would leave the
// building parented to the anchor and it would follow the next drag.
async function commitWorldDrag() {
  const w = worldDrag;
  worldDrag = null;
  const d = worldAnchor.position.clone().sub(w.from);
  const dRot = THREE.MathUtils.radToDeg(worldAnchor.rotation.y);
  const dScale = worldAnchor.scale.x;

  w.home.attach(w.mesh);               // hand it back, keeping its world pose
  worldAnchor.rotation.set(0, 0, 0);
  worldAnchor.scale.set(1, 1, 1);

  const moved = d.length() > 1e-4 || Math.abs(dRot) > 1e-4
    || Math.abs(dScale - 1) > 1e-4;
  if (!moved) { updateHud(); return; }

  // Read what is actually on the disc before adding this gesture to it, so
  // the total is right after an undo or a page reload rather than whatever
  // this tab happened to remember.
  const cur = await currentShape(w.chunk);
  if (cur.shape && cur.exact === false) {
    // The op list is applied to VANILLA, so writing one would throw away a
    // reshape that no move/turn/scale can express -- an OBJ import, say.
    // Refusing is the only honest option: there is no total that preserves it.
    toast(`Not moved: ${cur.why}.\nDragging rewrites this surface from the `
      + `original, which would discard that. Undo the reshape first, or move `
      + `it in your modelling tool.`, 'bad');
    await reloadLevel(true, true);
    return;
  }

  const t = shapeOf(w.chunk);
  t.tx += d.x; t.ty += d.y; t.tz += d.z;
  t.rot += dRot;
  t.s *= dScale;
  const stranded = w.kind === 'render' ? collisionLeftBehind(w.mesh) : null;
  await applyShape(w.chunk, w.name);   // reload restores the true geometry
  if (stranded) toast(stranded, 'warn');
}

// The Position / Instance-matrix inputs, refreshed in place. Skips a field the
// user is currently typing into.
function refreshInspectorPosition() {
  const n = nodeOf(selected);
  if (!n) return;
  const live = dirty.get(selected) || n.pos;
  const inputs = document.querySelectorAll('#inspector .xyz input');
  if (inputs.length === 3)
    for (let i = 0; i < 3; i++)
      if (document.activeElement !== inputs[i])
        inputs[i].value = (+live[i]).toFixed(3);
  const ang = dirtyAngle.get(selected);
  if (ang !== undefined) {
    const f = [...document.querySelectorAll('#inspector .field')]
      .find((x) => /angle/i.test(x.querySelector('label')?.textContent || ''));
    const inp = f && f.querySelector('input');
    if (inp && document.activeElement !== inp) inp.value = (+ang).toFixed(6);
  }
}

async function commitGizmo() {
  if (worldDrag) return commitWorldDrag();
  dragFrom = null;
  const changes = [];
  for (const u of selection) {
    const n = nodeOf(u);
    if (n && n.readonly) continue;   // event markers: display-only, no write
    const change = { uid: u };
    if (dirty.has(u)) change.pos = dirty.get(u);
    if (dirtyAngle.has(u)) change.angle = dirtyAngle.get(u);
    if (change.pos || change.angle !== undefined) changes.push(change);
  }
  dirtyAngle.clear();
  if (!changes.length) {
    // Nothing was recorded, but TransformControls has still transformed the
    // proxy meshes on screen -- a scale drag, or a rotate on a node with no
    // heading, LOOKED like an edit and wrote nothing, then never went back.
    // The half-size building that snaps to full size on reload is this.
    for (const u of selection) {
      const o = meshes.get(u);
      if (o) { o.scale.set(1, 1, 1); o.rotation.set(0, 0, 0); }
    }
    if (gizmo.mode === 'scale')
      toast('Size is edited in the Inspector, not with this handle.');
    else if (gizmo.mode === 'rotate')
      toast('Nothing to save — these objects have no editable heading.');
    return;
  }
  await pushChanges(changes);
}

function pushChange(uid, change) {
  return pushChanges([{ uid, ...change }]);
}

// Returns true only if the bytes actually landed, so a caller that drew an
// optimistic preview knows whether to keep it or take it back.
//
// `label` names the action in the journal and in the Versions list. One call
// is ONE undo step whatever it contains: the server merges every change into
// a single Level.apply, and writer_for opens one journal group for the lot.
// Move a building and the thing you bump into TOGETHER.
//
// They are two separate nodes and the file never says they are related, so
// moving a storefront used to leave the player walking into an invisible wall
// where it had been. The NAMES relate them -- `zone_piano_01` is drawn,
// `bound_piano_01` is solid -- and the server pairs them by stem and hands
// back `linked` on each node. MEASURED across the 35 campaign archives: 826
// of 1,538 collision shells pair to a building -- 53.7%, or 69.4% once the
// 347 merged bound_bound_grp_<flags> shells are set aside, since those cover
// several objects at once and have no single owner. (This line used to read
// "826 of 1,153 (71.6%)". The 826 is right; 1,153 was never counted and is
// not the size of any set in the data.)
//
// This runs inside pushChanges, which is the single funnel every move goes
// through, so the pair lands as ONE journal group and one Ctrl+Z takes both
// back. Doing it at the drag site instead would have made two undo steps and
// a way to leave the two halves apart.
//
// Only translation is carried across. A shell is not always the same shape as
// the thing it wraps, so copying a rotation or a resize could put collision
// somewhere the geometry is not; that stays manual and deliberate.
// OFF by default. The pairing is by name stem, and the owner hit a pair that
// was wrong -- an automatic follow that is sometimes wrong surprises more
// than it helps. Tick "move a building with its collision" to opt in.
let linkCollision = false;  // literal kept = settings DEFAULTS; forced true at load (see settings loader)
// "Edit collision on its own" (a model's Collision panel): a shell can be
// selected and moved by itself, and moving a model leaves its shell where it
// is. It stays on until unticked.
let collisionAlone = false;

function withLinkedCollision(changes) {
  if (!linkCollision || !level || collisionAlone) return changes;
  const have = new Set(changes.map((c) => c.uid));
  const extra = [];
  for (const c of changes) {
    if (!c.pos) continue;                       // translation only
    const n = nodeOf(c.uid);
    const partnerUid = n && n.linked;
    // uid 0 is a real node, so test for absence rather than falsiness. The
    // server had the identical hole; fixing one side alone would have left
    // the pair broken from whichever end still asked `!partnerUid`.
    if (partnerUid === undefined || partnerUid === null
        || have.has(partnerUid)) continue;
    const from = homePos.get(c.uid);
    // The partner's start is wherever it stands NOW -- it has not moved yet
    // this gesture. Requiring a homePos entry meant a shell that had never
    // been individually dragged had none, so the follow silently never
    // fired: the headline feature was dead on the ordinary path.
    const pObj = meshes.get(partnerUid);
    const partnerHome = homePos.get(partnerUid)
      || (pObj && pObj.position.clone());
    if (!from || !partnerHome) continue;        // never drawn: no truth to move
    if (pObj && !homePos.has(partnerUid))
      homePos.set(partnerUid, partnerHome.clone());
    const dx = c.pos[0] - from.x;
    const dy = c.pos[1] - from.y;
    const dz = c.pos[2] - from.z;
    if (!dx && !dy && !dz) continue;
    extra.push({ uid: partnerUid,
                 pos: [partnerHome.x + dx, partnerHome.y + dy,
                       partnerHome.z + dz] });
    have.add(partnerUid);
    // Keep the viewport honest: the shell has to move now, not after the
    // reload, or the preview shows a building parted from its collision.
    const obj = meshes.get(partnerUid);
    if (obj) obj.position.set(partnerHome.x + dx, partnerHome.y + dy,
                              partnerHome.z + dz);
  }
  if (extra.length)
    toast('Collision moved too — one Ctrl+Z takes both back.', 'good');
  return changes.concat(extra);
}

async function pushChanges(changes, label) {
  if (!changes.length || !level) return false;
  label = label || (changes.length === 1
    ? `move ${nodeOf(changes[0].uid)?.name || 'object'}`
    : `move ${changes.length} objects`);
  // Every move in the editor funnels through here, which is the one place
  // world coordinates can be turned back into the tile-local ones the file
  // actually stores. A tile is a reusable piece: its `.znes` says where a
  // crate sits WITHIN the tile, and the level decides where the tile sits.
  // Writing world coordinates would bake this particular arrangement into a
  // piece the game re-places at random every playthrough.
  changes = withLinkedCollision(changes);
  changes = changes.map((c) => (c.pos ? { ...c, pos: unoffset(c.uid, c.pos) } : c));
  const payload = { archive: level.archive, manifest: level.manifest,
                    area: level.area, changes, label, grow: allowGrow,
                    set: moveAsSet && !collisionAlone, alone: collisionAlone };
  try {
    // ONE round trip. There used to be a dry POST first, but the server runs
    // the identical preview at the head of the real call and refuses with the
    // same report before writing anything -- the dry pass only doubled every
    // move's latency.
    const res = await post('move', payload);
    if (!res.applied) {
      const bad = Object.entries(res.report || {}).filter(([, r]) => !r.fits);
      if (bad.length) {
        const [file, r] = bad[0];
        // When the stored size grew but the text did not, the constraint is
        // compression, not digits -- shortening numbers cannot help there.
        const sameLength = r.bytes_after != null && r.bytes_before != null
          && r.bytes_after <= r.bytes_before;
        toast(`Won't fit: ${file} needs ${r.stored} bytes, slot is `
          + `${r.slot}. Tick "allow files to grow" to write it.`, 'bad');
      } else {
        toast('Write refused: ' + (res.error || 'did not fit'), 'bad');
      }
      changes.forEach((c) => revert(c.uid));
      return false;
    }
    changes.forEach((c) => { dirty.delete(c.uid); homePos.delete(c.uid); });
    const w = res.written[0];
    toast(changes.length === 1
      ? `Saved ${nodeOf(changes[0].uid)?.name} → ${w.file}  (${w.used}/${w.slot} bytes)`
      : `Saved ${changes.length} objects → ${res.written.length} file(s)`, 'good');
    {
      // Sets and welded collision are moved by the server; say what came along.
      const extraPieces = (res.set || []).reduce((a, g) => a + g.count - 1, 0);
      let m = '';
      if (extraPieces) m += `${extraPieces} attached piece(s) moved with it. `;
      if (res.collision_note) m += res.collision_note;
      if (m) toast(m + ' One Ctrl+Z takes it all back.',
                   /NOT moved|stayed/.test(m) ? 'warn' : 'good');
    }
    await reloadLevel(true, !!res.reload);
    refreshHistory();
    return true;
  } catch (err) {
    toast('Error: ' + err.message, 'bad');
    changes.forEach((c) => revert(c.uid));
    return false;
  }
}

// A model made of several drawn pieces (a water tower: legs, tank, trough,
// water) moves as ONE thing: the server adds the pieces that sit inside the
// picked model's box. On by default; the inspector's Collision block has the
// switch.
let moveAsSet = true;
try { moveAsSet = localStorage.getItem('rdr.moveasset') !== '0'; } catch {}

function setMoveAsSet(on) {
  moveAsSet = !!on;
  try { localStorage.setItem('rdr.moveasset', moveAsSet ? '1' : '0'); } catch {}
}

// When on, a write that does not fit its slot GROWS the file: the payload
// is relocated into the archive's largest free gap and the directory entry
// rewritten to point at it. Off by default.
let allowGrow = false;
try { allowGrow = localStorage.getItem('rdr.allowgrow') === '1'; } catch {}

function setAllowGrow(on) {
  allowGrow = !!on;
  try { localStorage.setItem('rdr.allowgrow', allowGrow ? '1' : '0'); } catch {}
  remember('allowGrow', allowGrow);
}

// Where each object sat before anyone dragged it. Captured the first time an
// object is marked dirty and cleared once the edit resolves, so a refused
// write can put the object back EXACTLY where it was drawn.
const homePos = new Map();

function rememberHome(uid) {
  const obj = meshes.get(uid);
  if (obj && !homePos.has(uid)) homePos.set(uid, obj.position.clone());
}

function revert(uid) {
  const obj = meshes.get(uid);
  const home = homePos.get(uid);
  if (obj && home) {
    // The recorded position is the truth about where this object was drawn.
    obj.position.copy(home);
  } else if (obj) {
    // Fallback for an object we never recorded. Deliberately narrow: it is
    // only correct when the node's stored position IS its drawn position.
    //
    // This used to be the only path, and it moved things to the world origin.
    // A collision bound's .bnds instance matrix is identity -- its stored
    // position is literally 0,0,0 and its real location lives in the mesh
    // geometry -- so "put it back" threw it to 0,0,0. A tester reported
    // bounds that "get removed but never placed", which is what a box
    // teleporting to the origin looks like when the origin is off screen.
    // Anything else whose transform is not in its node had the same fate.
    const n = nodeOf(uid);
    if (n && n.pos && (n.pos[0] || n.pos[1] || n.pos[2])) {
      const off = obj.userData.yOffset || 0;
      obj.position.set(n.pos[0], n.pos[1] + off, n.pos[2]);
    }
    // All-zero stored position and no recorded home: leave it where it is.
    // Wrong-but-visible beats confidently teleported.
  }
  const nn = nodeOf(uid);
  const back = homePos.get(uid);
  if (nn && nn.pos && back) {
    const off = (obj && obj.userData.yOffset) || 0;
    nn.pos = [back.x, back.y - off, back.z];
  }
  homePos.delete(uid);
  dirty.delete(uid);
  renderInspector();
}

// ------------------------------------------------------------------- panels

function renderLayers() {
  const box = $('#layers');
  // How the view is DRAWN is a different question from what the level
  // CONTAINS, and the first used to be stacked on top of the second: eight
  // controls and a paragraph of keyboard help before the first layer row.
  // They render into their own section now. `|| box` keeps this function
  // working if that section is ever removed from the markup.
  const vbox = $('#viewSettings') || box;
  box.innerHTML = '';
  if (vbox !== box) vbox.innerHTML = '';
  // `level.stats` comes from the server and knows nothing about the event
  // markers, which are added client-side from a second request -- so those
  // rows read 0 while the outliner listed 189 of them. Count what is there.
  const counts = { ...(level?.stats || {}) };
  for (const k of Object.keys(KINDS))
    if (KINDS[k].event) counts[k] = groups[k].children.length;

  // How the camera moves. Orbit turns around a pivot out in the scene, which
  // is right for studying one building and wrong for getting about: fly
  // forward and the pivot goes with you, so the next drag swings you round it
  // in a wide arc instead of looking where you point. Fly keeps the pivot just
  // ahead of the camera, so dragging turns roughly on the spot.
  // Move a building and its collision as one thing. Built here rather than in
  // index.html so there is no id to go missing -- a module-scope lookup of an
  // element that is not there has taken the whole front end down twice.
  const lk = el('div', 'field');
  lk.append(el('label', null, 'Solidity'));
  const lkbox = el('input');
  lkbox.type = 'checkbox';
  lkbox.checked = true;
  lkbox.disabled = true;
  const lklab = el('span', 'muted', ' collision is part of each model: select a model to set Solid / Walk-through');
  lklab.style.fontSize = '11px';
  lk.title = 'Moves the matching bound_ shell with the building, as one '
    + 'undo step. Shared shells (bound_bound_grp_*) are left alone.';
  lk.append(lkbox, lklab);
  vbox.append(lk);
  const gr = el('label');
  gr.style.cssText = 'display:flex;align-items:center;gap:5px;margin-top:3px';
  const grbox = el('input');
  grbox.type = 'checkbox';
  grbox.checked = allowGrow;
  grbox.onchange = () => setAllowGrow(grbox.checked);
  const grlab = el('span', 'muted', ' allow files to grow');
  grlab.style.fontSize = '11px';
  gr.title = 'Oversized writes relocate into free space. Undoable.';
  gr.append(grbox, grlab);
  vbox.append(gr);

  const nav = el('div', 'field');
  nav.append(el('label', null, 'Moving'));
  const nsel = el('select');
  for (const [v, t] of [['orbit', 'Orbit a point'],
                        ['fly', 'Fly (turn in place)']]) {
    const o = el('option', null, t);
    o.value = v;
    if (v === navMode) o.selected = true;
    nsel.append(o);
  }
  nsel.onchange = () => setNavMode(nsel.value);
  nsel.style.flex = '1';
  nav.append(nsel);
  vbox.append(nav);
  vbox.append(el('div', 'muted',
    'WASD moves · Q/E down/up · Shift faster · drag to look'));

  const gl = el('label');
  gl.style.cssText = 'display:block;font-size:11px;color:var(--dim-ink);margin-top:4px';
  const gcb = el('input');
  gcb.type = 'checkbox';
  gcb.checked = gridOn;
  gcb.onchange = () => setGrid(gcb.checked);
  gl.append(gcb, document.createTextNode(' grid + drop line'));
  vbox.append(gl);

  const xl = el('label');
  xl.style.cssText = 'display:block;font-size:11px;color:var(--dim-ink)';
  xl.title = 'Draw volumes through walls.';
  const xcb = el('input');
  xcb.type = 'checkbox';
  xcb.checked = xray;
  xcb.onchange = () => setXray(xcb.checked);
  xl.append(xcb, document.createTextNode(' see volumes through walls (X-ray)'));
  vbox.append(xl);

  const pl = el('label');
  pl.style.cssText = 'display:block;font-size:11px;color:var(--dim-ink)';
  pl.title = 'Clicks pick volumes before world surfaces.';
  const pcb = el('input');
  pcb.type = 'checkbox';
  pcb.checked = helpersFirst;
  pcb.onchange = () => setHelpersFirst(pcb.checked);
  pl.append(pcb, document.createTextNode(' click volumes before world surfaces'));
  vbox.append(pl);

  // Collision world gets a three-way control, not a checkbox.
  const wrap = el('div', 'field');
  wrap.style.marginTop = '6px';
  wrap.append(el('label', null, 'Show collision'));
  const sel = el('select');
  for (const [v, t] of [['solid', 'Solid'], ['wire', 'Wireframe'], ['off', 'Hidden']]) {
    const o = el('option', null, t);
    o.value = v;
    if (v === worldMode) o.selected = true;
    sel.append(o);
  }
  sel.onchange = () => setWorldMode(sel.value);
  sel.style.flex = '1';
  wrap.append(sel);
  vbox.append(wrap);
  vbox.append(el('div', 'muted', 'Debug view only: the invisible collision shapes. A model and its collision move together.'));

  const rw = el('div', 'field');
  rw.style.marginTop = '6px';
  rw.append(el('label', null, 'Surfaces'));
  const rsel = el('select');
  for (const [v, t] of [['textured', 'Textured'], ['vertexcolor', 'Vertex colours'],
                        ['plain', 'Plain'], ['off', 'Hidden']]) {
    const o = el('option', null, t);
    o.value = v;
    if (v === renderMode) o.selected = true;
    rsel.append(o);
  }
  rsel.onchange = () => setRenderMode(rsel.value);
  rsel.style.flex = '1';
  rw.append(rsel);
  vbox.append(rw);
  vbox.append(el('div', 'muted',
    renderInfo
      ? (renderInfo.chunks
          ? `Render geometry — ${renderInfo.decoded}/${renderInfo.chunks} chunks decoded`
          // chunks:0 with no source means the bundle could not be READ, which
          // is transient (the disc handles get released for PCSX2). Saying so
          // beats printing "undefined/0" and looking like a dead level.
          : (renderInfo.source
              ? `Render geometry — none in ${renderInfo.source}`
              : 'Render geometry — bundle unavailable; reload the level'))
      : 'Render geometry (VIF display lists)'));

  // --- moving-origin levels ------------------------------------------------
  // Only TRAIN and TRAINGOO ship a .til or a .trn, so this control appears on
  // exactly the two maps where "the level is bigger than it looks" is true.
  const frames = levelEvents?.frames || [];
  if (frames.length) {
    const cars = frames.filter((f) => f.kind === 'train_car');
    const tiles = frames.filter((f) => f.kind === 'tile');
    const span = tiles.length
      ? Math.max(...tiles.map((f) => f.offset[0] + f.length)) : 0;
    const tw = el('div', 'field');
    tw.style.marginTop = '10px';
    const tcb = document.createElement('input');
    tcb.type = 'checkbox';
    tcb.checked = trackLayout;
    tcb.onchange = () => { setTrackLayout(tcb.checked); renderLayers(); };
    const tl = el('label', null, 'Lay out along the track');
    tl.style.width = 'auto';
    tl.prepend(tcb);
    tw.append(tl);
    vbox.append(tw);
    const note = el('div', 'muted');
    note.style.cssText = 'font-size:11px;margin-top:3px;line-height:1.55';
    note.textContent =
      `This mission streams past a moving train, so every piece is authored at `
      + `the origin. Derived from the level's own files: `
      + (span ? `${span.toFixed(0)} m of track` : 'no tile sequence')
      + (cars.length ? `, ${cars.length} cars over `
          + `${Math.max(...cars.map((f) => f.offset[0])).toFixed(0)} m` : '')
      + (levelEvents.speed ? `, ${levelEvents.speed.velocity} m/s` : '')
      + '. Car positions from the disc. Details: Guides > Modding guide.';
    vbox.append(note);
  }

  // --- the cast -----------------------------------------------------------
  const cw = el('div', 'field');
  cw.style.marginTop = '10px';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = castGroup.visible;
  cb.onchange = () => {
    castGroup.visible = cb.checked;
    fadeProxies(cb.checked);
    if (cb.checked && level) loadCast(level.archive);
    else { clearCast(); renderLayers(); }
  };
  const clbl = el('label', null, 'Characters');
  clbl.style.width = 'auto';
  clbl.prepend(cb);
  cw.append(clbl);
  const lodSel = document.createElement('select');
  lodSel.style.cssText = 'margin-left:auto;width:78px;font-size:11px';
  for (const i of [0, 1, 2, 3])
    lodSel.append(new Option('LOD ' + i, String(i)));
  lodSel.value = String(castLod);
  lodSel.onchange = () => {
    castLod = parseInt(lodSel.value, 10);
    if (castGroup.visible && level) loadCast(level.archive);
  };
  lodSel.title = 'Actor detail level. LOD 1 is cheaper.';
  cw.append(lodSel);
  vbox.append(cw);
  // Spawns as the player: their own switch, because there are many of them
  // and they are all the same person.
  const sw = el('div', 'field');
  const sb = document.createElement('input');
  sb.type = 'checkbox';
  sb.checked = castSpawns;
  sb.onchange = () => {
    castSpawns = sb.checked;
    if (castGroup.visible && level) loadCast(level.archive);
  };
  const slbl = el('label', null, 'Player at spawn points');
  slbl.style.width = 'auto';
  slbl.style.fontSize = '11px';
  slbl.prepend(sb);
  sw.append(slbl);
  sw.title = 'One spawn per player slot. The same character repeats.';
  vbox.append(sw);

  vbox.append(el('div', 'muted',
    castInfo
      ? `${castInfo.placed} actor(s) from ${castInfo.decoded}/`
        + `${castInfo.kinds} type(s)`
        + (castInfo.spawns
            ? ` · ${castInfo.spawns} spawn(s) as ${castInfo.player}` : '')
        + (castInfo.missing.length ? ` — missing: ${castInfo.missing[0]}` : '')
      : 'Draw each enemy as its real model, where it actually stands'));

  // No longer bound to W -- W flies. This button is the deliberate "put me on
  // the ground where the player starts" action, which is a different thing.
  const wb = el('button', 'primary',
                walk.on ? 'Exit walk mode (Esc)' : 'Stand at the spawn point');
  wb.style.cssText = 'width:100%; margin-top:8px';
  wb.title = 'Stand at the player spawn at eye height and walk around';
  wb.onclick = () => (walk.on ? setWalk(false) : standAtSpawn());
  vbox.append(wb);

  for (const [k, spec] of Object.entries(KINDS)) {
    const lab = el('label', 'row');
    lab.style.cursor = 'pointer';
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = groups[k].visible;
    cb.onchange = () => {
      groups[k].visible = cb.checked;
      if (!cb.checked && selected != null && nodeOf(selected)?.kind === k) select(null);
      // The outliner is a view of what is showing, so it has to follow. It
      // used to keep listing the old set until some unrelated input event
      // happened to re-render it.
      renderObjectList();
    };
    const dot = el('span', 'dot');
    dot.style.background = '#' + spec.color.toString(16).padStart(6, '0');
    lab.append(cb, dot, el('span', 'nm', spec.label),
               el('span', 'tag', String(counts[k] || 0)));
    box.append(lab);

    // Under the Volumes row: one line per volume TYPE in this level, each
    // with its colour and its own switch. The colours were always there --
    // an AI room is blue, a trigger is red, a camera region is purple --
    // but nothing on screen said so, and nothing let you look at ONE kind.
    // "The volumes are all different colours and I cannot choose which to
    // look at" is both of those at once.
    if (k === 'box' && groups.box.visible && groups.box.children.length) {
      const present = new Map();     // key -> {label, color, count}
      for (const o of groups.box.children) {
        const n = nodeOf(o.userData.uid);
        if (!n) continue;
        const t = typeInfo(n);
        const e = present.get(t.key) || { label: t.label, color: t.color, count: 0 };
        e.count++;
        present.set(t.key, e);
      }
      const sorted = [...present.entries()].sort((a, b) => b[1].count - a[1].count);
      for (const [key, info] of sorted) {
        const row = el('label', 'row');
        row.style.cssText = 'cursor:pointer;padding-left:26px;font-size:11px';
        const c = el('input');
        c.type = 'checkbox';
        c.checked = !hiddenBoxTypes.has(key);
        c.onchange = () => {
          if (c.checked) hiddenBoxTypes.delete(key);
          else hiddenBoxTypes.add(key);
          try { localStorage.setItem('rdr.hidetypes',
                JSON.stringify([...hiddenBoxTypes])); } catch {}
          remember('hiddenBoxTypes', [...hiddenBoxTypes]);
          applyBoxTypeFilter();
          renderObjectList();
        };
        const d = el('span', 'dot');
        d.style.background = '#' + info.color.toString(16).padStart(6, '0');
        row.append(c, d, el('span', 'nm', info.label),
                   el('span', 'tag', String(info.count)));
        row.title = 'Show or hide every "' + info.label + '" volume';
        box.append(row);
      }
    }
  }
}

// Which volume types are hidden, by typeInfo key. Persisted, applied to the
// meshes rather than the data -- hiding AI rooms does not unload them.
let hiddenBoxTypes = new Set();
try { hiddenBoxTypes = new Set(JSON.parse(localStorage.getItem('rdr.hidetypes') || '[]')); } catch {}

function applyBoxTypeFilter() {
  // Delegates on purpose: applyHidden is the single owner of node-mesh
  // visibility, so the type switches cannot fight the per-object eyes.
  applyHidden();
}

// ------------------------------------------------------------- the outliner
//
// Everything the level contains, in one tree. Three groupings of the SAME node
// set, never a second data source:
//
//   by file  -- the default, and still the right one for editing, because a
//               file is the slot boundary: "which file am I about to grow" is
//               the question this format never stops asking.
//   by type  -- what the game calls each thing (see BOX_TYPES / LINE_TYPES).
//               This is how you find "every kill volume" or "every trigger".
//   by frame -- for levels built from repeated modules; see level.frames.
//
// Rows render lazily per expanded group, so there is no global row cap. The
// old flat list stopped at 600 and the objects it dropped were, by alphabetical
// bad luck, precisely the town's buildings -- pawnshop through workhouse.
const collapsedSources = new Set();
let outMode = 'file';
try { outMode = localStorage.getItem('rdr.outmode') || 'file'; } catch {}

// Per-object visibility, on top of the per-kind layer toggles. A uid in here
// is hidden regardless of its layer.
const hiddenUids = new Set();
const hiddenChunks = new Set();

function applyHidden() {
  // ONE owner for a node mesh's `.visible`: the per-object eye AND the
  // per-volume-type switches resolve here together. They used to be two
  // writers -- applyBoxTypeFilter set visibility, then any eye toggle or
  // level rebuild ran this loop and switched every type-hidden volume back
  // on, so the Layers checkboxes read unticked over volumes that still drew.
  for (const [uid, obj] of meshes) {
    const n = nodeOf(uid);
    const typeHidden = n && n.kind === 'box' && hiddenBoxTypes.has(typeInfo(n).key);
    // originDot: a baked zone/bound marker suppressed at build time -- see
    // buildLevel. It must stay suppressed through every eye toggle.
    obj.visible = !obj.userData.originDot
                  && !hiddenUids.has(uid) && !typeHidden;
  }
  // Placed copies obey the per-object eye of the node that placed them; they
  // used to ignore every visibility system in the product.
  for (const o of instanceGroup.children) {
    if (o.userData.instanceOf !== undefined)
      o.visible = !hiddenUids.has(o.userData.instanceOf);
  }
  for (const o of renderGroup.children) {
    const d = o.userData.render || {};
    // Second-pass overlays have their own switch; do not fight it.
    const byPass = d.second_pass ? showSecondPass : true;
    o.visible = byPass && !hiddenChunks.has(d.chunk)
                && !deletedChunks.has(d.chunk);
  }
}

// Surfaces whose last placement node you deleted.
//
// The viewport draws every chunk in the level bundle, whether or not a node
// references it -- it has to, because plenty of real scenery has no node at
// all (121 surfaces on TRAINGOO are terrain placed by the tile system). The
// side effect was that deleting a building removed its row and its marker and
// left the building standing there, which reads as "the delete did nothing".
//
// So deletion is tracked per level: a chunk goes dark when the node that
// referenced it is gone AND nothing else references it. Undo restores the
// node, `refreshDeletedChunks` sees the reference again, and it comes back.
let deletedChunks = new Set();
let deletedFor = null;      // which level the set belongs to

let baselineRefs = null;    // chunks referenced when this level first loaded

// DERIVED, not recorded.
//
// The first version only hid a chunk if `deleteSelected` had put it in the set
// on the way past, which meant a delete through any other path -- the API, a
// mod load, an undo -- left the surface on screen. Comparing against a
// baseline needs no cooperation from whatever did the deleting: a chunk that
// was referenced when the level loaded and is not referenced now has had its
// node removed, whoever removed it. Undo puts the reference back and the
// surface returns on the next rebuild.
function refreshDeletedChunks() {
  if (!level) { deletedChunks = new Set(); deletedFor = null; baselineRefs = null; return; }
  const referenced = new Set();
  for (const n of level.nodes) {
    const ri = n.props && n.props.resourceIndex;
    if (ri != null && ri !== '') referenced.add(Math.round(+ri) - 1);
  }
  // Chunk indices are per level, so the baseline is per level too.
  const key = `${level.archive}|${level.area || ''}`;
  if (deletedFor !== key || !baselineRefs) {
    deletedFor = key;
    baselineRefs = referenced;          // first sight of this level is the baseline
    deletedChunks = new Set();
    return;
  }
  deletedChunks = new Set(
    [...baselineRefs].filter((c) => !referenced.has(c)));
}

// Called just before a delete: which chunks would be left with no reference?
function chunksLosing(uids) {
  const out = new Set();
  if (!level) return out;
  const going = new Set(uids);
  const stays = new Map();
  for (const n of level.nodes) {
    const ri = n.props && n.props.resourceIndex;
    if (ri == null || ri === '') continue;
    const c = Math.round(+ri) - 1;
    if (!stays.has(c)) stays.set(c, 0);
    if (!going.has(n.uid)) stays.set(c, stays.get(c) + 1);
    else out.add(c);
  }
  for (const c of [...out]) if ((stays.get(c) || 0) > 0) out.delete(c);
  return out;
}

// The groups for the current mode: [{key, label, gloss, color, nodes, chunks}]
function outlinerGroups(filter) {
  const match = (n) => !filter
    || n.name.toLowerCase().includes(filter)
    || n.kind.includes(filter)
    || (n.source || '').toLowerCase().includes(filter);

  const groupsOut = new Map();
  const add = (key, meta, n) => {
    if (!groupsOut.has(key)) groupsOut.set(key, { key, nodes: [], chunks: [], ...meta });
    groupsOut.get(key).nodes.push(n);
  };

  for (const n of (level?.nodes || [])) {
    if (!groups[n.kind]?.visible) continue;
    if (!match(n)) continue;
    if (outMode === 'file') {
      add(n.source || '(no file)', { label: n.source || '(no file)',
                                     color: KINDS[n.kind].color }, n);
    } else if (outMode === 'type') {
      const ti = typeInfo(n);
      add(ti.label, { label: ti.label, color: ti.color, gloss: ti.gloss }, n);
    } else {
      const f = n.frame || '(level-wide)';
      // Frames come from /api/level_events, not from the level payload.
      const meta = (levelEvents?.frames || []).find((x) => x.id === f);
      add(f, {
        label: meta ? meta.label : 'Level-wide (not part of any module)',
        color: KINDS[n.kind].color,
        gloss: meta ? meta.gloss
          : 'Belongs to the level, not a car or tile.',
      }, n);
    }
  }

  // Drawn surfaces that no scene node claims. On TOMB there are none; on TRAIN
  // they are 49% of the visible triangles, so silently omitting them would
  // make the outliner claim the level is half its real size.
  const orphan = [];
  for (const m of (renderInfo?.meshes || [])) {
    if (uidForChunk(m.chunk) !== undefined) continue;
    const label = m.name || `chunk ${m.chunk}`;
    if (filter && !label.toLowerCase().includes(filter)) continue;
    orphan.push(m);
  }
  if (orphan.length) {
    const tris = orphan.reduce((a, m) => a + (m.tris || 0), 0);
    groupsOut.set('__orphan', {
      key: '__orphan', label: 'Unassigned surfaces', color: 0x9a9a9a,
      nodes: [], chunks: orphan,
      gloss: `${orphan.length} surfaces with no placing node. Reshape and recolour only.`,
    });
  }

  const arr = [...groupsOut.values()];
  arr.sort((a, b) => {
    if (a.key === '__orphan') return 1;
    if (b.key === '__orphan') return -1;
    // By type, biggest first: you want to see what a level is MADE of.
    if (outMode === 'type') return (b.nodes.length - a.nodes.length)
      || a.label.localeCompare(b.label);
    return a.label.localeCompare(b.label);
  });
  return arr;
}

function eyeButton(hidden, onToggle) {
  const b = el('span', 'eye' + (hidden ? ' off' : ''), hidden ? '🚫' : '👁');
  b.title = hidden ? 'Show' : 'Hide';
  b.onclick = (e) => { e.stopPropagation(); onToggle(); };
  return b;
}

function renderObjectList() {
  const box = $('#objList');
  const filter = $('#objFilter').value.trim().toLowerCase();
  box.innerHTML = '';
  const countTag = $('#outCount');
  if (!level) { if (countTag) countTag.textContent = ''; return; }

  const gs = outlinerGroups(filter);
  const total = gs.reduce((a, g) => a + g.nodes.length + g.chunks.length, 0);
  // Say how many are NOT here. The heading claims "everything in this level",
  // and with the default layers that was 960 of 1,177 on Brimstone -- the 189
  // AI spawn points, 16 collision shells and 12 glows sat behind switched-off
  // layers with nothing on screen admitting it.
  const hiddenByLayer = (level?.nodes || [])
    .filter((n) => !groups[n.kind]?.visible).length;
  if (countTag)
    countTag.textContent = `${total.toLocaleString()} in ${gs.length} group`
      + (gs.length === 1 ? '' : 's')
      + (hiddenByLayer ? ` · ${hiddenByLayer} hidden by layers` : '');

  // "by frame" only means something on a level built from repeating modules,
  // which is 2 of 61 archives. Elsewhere it would be one group called "the
  // level" -- so the button hides rather than offering an empty hierarchy.
  const hasFrames = !!(levelEvents?.frames || []).length;
  const fb = $('#outModeFrame');
  if (fb) {
    fb.style.display = hasFrames ? '' : 'none';
    if (!hasFrames && outMode === 'frame') {
      outMode = 'file';
      for (const b of document.querySelectorAll('#outModes [data-outmode]'))
        b.classList.toggle('on', b.dataset.outmode === 'file');
    }
  }

  const legend = $('#outLegend');
  if (legend)
    legend.textContent = outMode === 'type'
      ? 'Grouped by type.'
      : outMode === 'frame'
        ? 'Grouped by module.'
        : 'Grouped by file.';

  if (!gs.length) {
    // "Nothing matches" is the wrong answer when the thing IS there but its
    // layer is off -- so check before saying it.
    const hiddenHit = filter
      ? (level?.nodes || []).filter((n) => !groups[n.kind]?.visible
          && n.name.toLowerCase().includes(filter)).length
      : 0;
    box.append(el('div', 'pad muted', filter
      ? (hiddenHit
          ? `${hiddenHit} match(es) on hidden layers. Turn them on under Layers.`
          : 'Nothing matches that filter.')
      : 'Every layer is off. Tick one under Layers.'));
    return;
  }

  for (const g of gs) {
    const open = !collapsedSources.has(g.key);
    const head = el('div', 'row srchead');
    const items = g.nodes.length + g.chunks.length;
    const allHidden = g.nodes.length
      ? g.nodes.every((n) => hiddenUids.has(n.uid))
      : g.chunks.every((m) => hiddenChunks.has(m.chunk));
    head.append(el('span', 'chev', open ? '▾' : '▸'));
    const dot = el('span', 'dot');
    dot.style.background = '#' + (g.color || 0x888888).toString(16).padStart(6, '0');
    head.append(dot, el('span', 'nm', g.label), el('span', 'tag', String(items)));
    head.append(eyeButton(allHidden, () => {
      for (const n of g.nodes)
        allHidden ? hiddenUids.delete(n.uid) : hiddenUids.add(n.uid);
      for (const m of g.chunks)
        allHidden ? hiddenChunks.delete(m.chunk) : hiddenChunks.add(m.chunk);
      applyHidden();
      renderObjectList();
    }));
    if (g.gloss) head.title = g.gloss;
    head.onclick = () => {
      if (open) collapsedSources.add(g.key); else collapsedSources.delete(g.key);
      renderObjectList();
    };
    box.append(head);
    if (g.gloss && open && outMode === 'type')
      box.append(el('div', 'gloss', g.gloss));
    if (!open) continue;

    for (const n of g.nodes) {
      const ti = typeInfo(n);
      const row = el('div', 'row child'
        + (selection.has(n.uid) ? ' sel' : ''));
      row.dataset.uid = n.uid;
      const d = el('span', 'dot');
      d.style.background = '#' + ti.color.toString(16).padStart(6, '0');
      row.append(d, el('span', 'nm', n.name),
                 el('span', 'tag', outMode === 'type' ? (n.source || n.kind)
                                                      : (ti.sub || ti.label)));
      row.append(eyeButton(hiddenUids.has(n.uid), () => {
        hiddenUids.has(n.uid) ? hiddenUids.delete(n.uid) : hiddenUids.add(n.uid);
        applyHidden();
        renderObjectList();
      }));
      // Single click SELECTS and nothing else. It used to fly the camera too,
      // which for a building meant flying to the origin -- away from it.
      // Double click frames, which is the gesture every 3D tool uses.
      row.onclick = (e) => select(n.uid, e.shiftKey || e.ctrlKey);
      row.ondblclick = () => { select(n.uid); focusOn(n.uid); };
      box.append(row);
    }

    for (const m of g.chunks) {
      const row = el('div', 'row child');
      const d = el('span', 'dot');
      d.style.background = '#9a9a9a';
      row.append(d, el('span', 'nm', m.name || `chunk ${m.chunk}`),
                 el('span', 'tag', `${(m.tris || 0).toLocaleString()} tris`));
      row.append(eyeButton(hiddenChunks.has(m.chunk), () => {
        hiddenChunks.has(m.chunk) ? hiddenChunks.delete(m.chunk)
                                  : hiddenChunks.add(m.chunk);
        applyHidden();
        renderObjectList();
      }));
      row.onclick = () => showInspectorChunk(m.chunk);
      row.ondblclick = () => focusChunk(m.chunk);
      box.append(row);
    }
  }
}

function focusChunk(chunk) {
  const mesh = renderGroup.children.find(
    (o) => o.userData.render && o.userData.render.chunk === chunk);
  if (!mesh) return;
  const b = new THREE.Box3().setFromObject(mesh);
  if (b.isEmpty()) return;
  const c = b.getCenter(new THREE.Vector3()), s = b.getSize(new THREE.Vector3());
  const dist = Math.max(8, Math.max(s.x, s.y, s.z) * 1.8);
  const dir = camera.position.clone().sub(controls.target).normalize();
  controls.target.copy(c);
  camera.position.copy(c).add(dir.multiplyScalar(dist));
  controls.update();
}

// Where a node actually IS on screen, and how big it is.
//
// A node's marker is not always where you can see it. Level vertices are
// authored in world space, so a .znes or .bnds instance matrix is identity and
// every building's marker sits at [0,0,0] -- 250 of 250 in MANS, 343 of 343 in
// TOMB. Framing that flew the camera to the origin, i.e. AWAY from the
// building the user had just clicked. So when the marker is at the origin and
// the node draws a real surface, frame the surface instead.
function focusTarget(uid) {
  const n = nodeOf(uid);
  const obj = meshes.get(uid);
  const atOrigin = n && Math.abs(n.pos[0]) < 1e-6
                     && Math.abs(n.pos[1]) < 1e-6 && Math.abs(n.pos[2]) < 1e-6;
  if (atOrigin) {
    const mesh = meshForNode(uid);
    if (mesh) {
      const box = new THREE.Box3().setFromObject(mesh);
      if (!box.isEmpty()) {
        const c = box.getCenter(new THREE.Vector3());
        const s = box.getSize(new THREE.Vector3());
        return { point: c, radius: Math.max(s.x, s.y, s.z) * 0.5 };
      }
    }
  }
  if (!obj) return null;
  return { point: obj.getWorldPosition(new THREE.Vector3()), radius: 2 };
}

function focusOn(uid) {
  const t = focusTarget(uid);
  if (!t) return;
  const dist = Math.max(8, t.radius * 3.2);
  const dir = camera.position.clone().sub(controls.target).normalize();
  if (dir.lengthSq() < 1e-6) dir.set(0.6, 0.5, 0.6).normalize();
  controls.target.copy(t.point);
  camera.position.copy(t.point).add(dir.multiplyScalar(dist));
  controls.update();
}

function numField(label, value, onCommit) {
  const f = el('div', 'field');
  f.append(el('label', null, label));
  const i = el('input');
  i.value = (+value).toFixed(6);
  i.onchange = () => onCommit(parseFloat(i.value));
  f.append(i);
  return f;
}

// The inspector's own 3D preview: the selected object alone, textured,
// away from everything overlapping it in the world. In a dense town that is
// the difference between "I think I clicked the right porch" and seeing it.
let inspViewer = null;
let inspShown = null;

function showInspectorModel(node) {
  const host = $('#inspView'), bar = $('#inspViewBar');
  if (!host) return;
  const ri = node && node.props && node.props.resourceIndex;
  const chunk = ri == null ? null : Math.round(+ri) - 1;   // 1-based on disc
  if (chunk == null || chunk < 0 || !level) {
    host.style.display = bar.style.display = 'none';
    inspShown = null;
    return;
  }
  const key = `${level.archive}|${level.area}|${chunk}`;
  host.style.display = 'block';
  bar.style.display = 'flex';
  if (inspShown === key) return;          // already showing it
  inspShown = key;
  previewChunk(host, $('#inspViewInfo'), level.archive, level.area, chunk,
               () => inspViewer, (v) => { inspViewer = v; },
               () => $('#inspSpin')?.checked !== false);
}

// The typed `attributes { }` block, with what each name means.
//
// Read-only for now, deliberately. These are not fixed-width fields: turning
// `NoShadows false` into `NoShadows true` grows the .znes by four bytes, which
// can overflow its archive slot. Making them editable needs the same dry-run
// fit check the string editor uses, and claiming they are editable before that
// exists would produce silent refusals.
function renderAttributes(box, n) {
  const attrs = Object.entries(n.props || {})
    .filter(([k]) => k.startsWith('attr.'))
    .map(([k, v]) => [k.slice(5), v]);
  if (!attrs.length) return;

  const collision = attrs.filter(([k]) => k.startsWith('BoundFlag'));
  const render = attrs.filter(([k]) => !k.startsWith('BoundFlag'));

  const head = (text) => {
    box.append(el('h3', '', text));
    box.lastChild.style.cssText = 'margin:14px 0 4px';
  };
  const table = (rows, order) => {
    const t = el('table');
    const sorted = order
      ? rows.slice().sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
      : rows;
    for (const [k, v] of sorted) {
      const tr = el('tr');
      const on = String(v).toLowerCase() === 'true';
      const off = String(v).toLowerCase() === 'false';
      const kc = el('td', 'muted', k);
      const vc = el('td', '', String(v));
      if (on) vc.style.color = 'var(--grn)';
      if (off) vc.style.color = 'var(--dim-ink)';
      if (ATTR_GLOSS[k]) tr.title = ATTR_GLOSS[k];
      tr.append(kc, vc);
      t.append(tr);
    }
    box.append(t);
  };

  if (render.length) {
    head('Render flags');
    table(render);
  }
  if (collision.length) {
    head('Collision — who hits this');
    // Shown in the engine's own order, recovered from the exporter's node
    // names (see BOUND_FLAG_ORDER), so it reads the same way every time.
    table(collision, BOUND_FLAG_ORDER);
    const note = el('div', 'muted');
    note.style.cssText = 'font-size:11px;margin-top:4px;line-height:1.5';
    note.textContent = 'Hover a row for meaning.';
    box.append(note);
  }
}

// An Inspector for a drawn surface that no scene node claims. These are real,
// visible level geometry -- 49% of the triangles on TRAIN -- and until now
// there was no way to select one or even see that it existed.
function showInspectorChunk(chunk) {
  select(null);
  const rec = renderInfo?.meshes?.find((m) => m.chunk === chunk);
  const box = $('#inspector');
  box.innerHTML = '';
  if (!rec) { box.append(el('p', 'muted', 'That surface is not decoded.')); return; }

  // These have no node, so there is no uid to select -- but the surface is
  // still right there in the viewport and its shape is still editable, so it
  // still gets a handle. Remembered so a reshape's reload can restore it.
  chunkSel = chunk;
  worldSel = worldTargetForChunk(chunk);
  if (worldSel) {
    worldAnchor.position.copy(worldSel.centre);
    gizmo.attach(worldAnchor);
  }

  box.append(el('div', 'nm', rec.name || `chunk ${chunk}`));
  box.lastChild.style.cssText = 'font-size:13px;color:var(--gold);margin-bottom:2px';
  const meta = el('div', 'muted');
  meta.style.cssText = 'font-size:11px;line-height:1.6;margin-bottom:8px';
  meta.textContent =
    `Unassigned surface · chunk ${chunk} · `
    + `${(rec.tris || 0).toLocaleString()} triangles`
    + (rec.second_pass ? ' · second pass (shadow/detail overlay)' : '');
  box.append(meta);

  const why = el('div', 'muted');
  why.style.cssText = 'font-size:11px;line-height:1.6;margin-bottom:8px';
  why.textContent =
    'Unassigned surface. Move, turn, scale and recolour only.';
  box.append(why);

  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:6px;margin-bottom:4px';
  const fly = el('button', '', 'Fly to it');
  fly.style.fontSize = '11px';
  fly.onclick = () => focusChunk(chunk);
  bar.append(fly);
  box.append(bar);

  shapeControlsForChunk(box, chunk);
  showInspectorModel({ props: { resourceIndex: chunk + 1 } });
}

// Reshaping the world itself, as opposed to moving an instance of it.
// Positions are floats inside the VIF packet, so this is an in-place patch
// the same length as the original -- see rdrstudio/geomedit.py.
//
// The transform is always sent as a full op list applied to the VANILLA
// geometry, never as a nudge on top of the current state. Accumulating
// 32-bit float deltas drifts, and it makes "put it back" impossible.
const shapeState = new Map();     // "archive|area|chunk" -> transform

function shapeKey(chunk) {
  return `${level.archive}|${level.area}|${chunk}`;
}

function shapeOf(chunk) {
  const k = shapeKey(chunk);
  if (!shapeState.has(k))
    shapeState.set(k, { tx: 0, ty: 0, tz: 0, s: 1, rot: 0 });
  return shapeState.get(k);
}

// The shape as it actually stands on the disc, measured against vanilla.
//
// shapeState is only a cache now. It used to be the sole record of what had
// been applied, and that was wrong in two ordinary situations: reloading the
// page lost it, so the next edit computed from vanilla and quietly reverted
// earlier moves; and undo left it stale, so the next edit re-applied the move
// that had just been undone. The server can recover the total by fitting the
// current vertices back to the stock ones, so nothing has to be remembered.
async function currentShape(chunk) {
  try {
    const r = await post('geom_shape', {
      archive: level.archive, area: level.area, chunk });
    if (r.shape) {
      Object.assign(shapeOf(chunk), r.shape);
      return r;
    }
    return r;
  } catch (e) {
    return { shape: null, why: e.message };
  }
}

async function applyShape(chunk, label) {
  const t = shapeOf(chunk);
  const ops = [];
  if (t.s !== 1) ops.push({ op: 'scale', x: t.s });
  if (t.rot) ops.push({ op: 'rotate_y', deg: t.rot });
  if (t.tx || t.ty || t.tz)
    ops.push({ op: 'translate', x: t.tx, y: t.ty, z: t.tz });
  try {
    const r = await post('geom_edit', {
      archive: level.archive, area: level.area, chunk, label, ops,
      set: moveAsSet });
    if (r.changed === false) toast('Nothing to change — it is already there', 'good');
    else {
      let m = `Reshaped ${label} — Ctrl+Z to undo`;
      if (r.set) m += `\n${r.set.count - 1} attached piece(s) moved with it.`;
      if (r.collision_note) m += '\n' + r.collision_note;
      toast(m, /NOT moved|stayed|could not/.test(m) ? 'warn' : 'good');
    }
    await refreshHistory();
    await reloadLevel(true, true);
  } catch (e) {
    toast(e.message, 'bad');
    // The drag already moved the preview; a refusal must put it back, or the
    // viewport shows an edit the disc refused -- which reads as a move that
    // "worked" and then a game that crashes.
    await reloadLevel(true, true);
  }
}

// --- M6: the same transform over a SELECTION, about ONE pivot -------------
//
// The chunks behind the current selection, as mass_edit wants them.
function selectedGeomChunks() {
  const out = [];
  for (const uid of selection) {
    const n = nodeOf(uid);
    const ri = n && n.props && n.props.resourceIndex;
    if (ri == null) continue;
    if (n.kind !== 'zone' && n.kind !== 'bound') continue;
    const chunk = Math.round(+ri) - 1;
    if (chunk >= 0 && !out.includes(chunk)) out.push(chunk);
  }
  return out;
}

async function applyMassShape(chunks, t, label) {
  const ops = [];
  // Scale first, then turn, then move -- the same order the single-model
  // control uses, so the two read the same way.
  if (t.s !== 1) ops.push({ op: 'scale', x: t.s });
  if (t.rot) ops.push({ op: 'rotate_y', deg: t.rot });
  if (t.tx || t.ty || t.tz)
    ops.push({ op: 'translate', x: t.tx, y: t.ty, z: t.tz });
  if (!ops.length) return toast('Nothing to apply', 'bad');
  try {
    // ONE call. Looping geom_edit would scale each model about its own
    // centre and leave the arrangement untouched, and it would be N writes
    // and N undo steps for one action.
    const r = await post('mass_edit', {
      archive: level.archive, area: level.area, chunks, label, ops });
    if (r.changed === false) {
      toast('Back to the original shapes', 'good');
    } else {
      let msg = `Moved ${r.models} models about one pivot — Ctrl+Z to undo`;
      // Say the two things the plan reports rather than dropping them: a
      // per-axis scale is NOT proportional, and the collision shells did not
      // come along.
      if (r.proportional === false)
        msg += '\nPer-axis scale: models whose long axis is not the one you '
             + 'stretched have changed shape.';
      if (r.collision_note) msg += '\n' + r.collision_note;
      toast(msg, r.collision_note ? 'warn' : 'good');
    }
    await refreshHistory();
    await reloadLevel(true, true);
  } catch (e) {
    toast(e.message, 'bad');
    await reloadLevel(true, true);
  }
}

function renderMassShapeControls(box) {
  const chunks = selectedGeomChunks();
  if (chunks.length < 2) return;

  box.append(el('h3', '', 'Shape (all selected)'));
  box.lastChild.style.cssText =
    'margin:12px 0 2px;font-size:12px;color:var(--gold)';
  const note = el('div', 'muted');
  note.style.cssText = 'font-size:11px;margin-bottom:6px;line-height:1.5';
  note.textContent = `${chunks.length} models, moved and scaled about one `
    + 'shared pivot — so the arrangement grows with its contents and each '
    + 'model keeps its own proportions. One write, one undo.';
  box.append(note);

  const warn = el('div', 'muted');
  warn.style.cssText = 'font-size:11px;margin-bottom:6px;line-height:1.5;'
    + 'color:var(--dim-ink)';
  warn.textContent = 'Values are a TOTAL measured from the stock disc, like '
    + 'the single-model Shape control — so applying this replaces any earlier '
    + 'shape edit on these models rather than adding to it.';
  box.append(warn);

  const t = { tx: 0, ty: 0, tz: 0, s: 1, rot: 0 };
  const num = (lab, key, step, min) => {
    const f = el('div', 'field');
    f.append(el('label', null, lab));
    const i = el('input');
    i.type = 'number';
    i.step = step;
    if (min != null) i.min = min;
    i.value = t[key];
    i.style.flex = '1';
    i.oninput = () => { t[key] = parseFloat(i.value) || (key === 's' ? 1 : 0); };
    f.append(i);
    box.append(f);
  };
  num('Move X', 'tx', 0.5);
  num('Move Y', 'ty', 0.5);
  num('Move Z', 'tz', 0.5);
  num('Scale', 's', 0.05, 0.01);
  num('Turn°', 'rot', 5);

  const row = el('div');
  row.style.cssText = 'display:flex;gap:6px;margin-top:6px';
  const fit = el('button', 'ghost', 'Check fit');
  fit.title = 'Plan the edit and report, without writing anything.';
  fit.onclick = async () => {
    const ops = [];
    if (t.s !== 1) ops.push({ op: 'scale', x: t.s });
    if (t.rot) ops.push({ op: 'rotate_y', deg: t.rot });
    if (t.tx || t.ty || t.tz)
      ops.push({ op: 'translate', x: t.tx, y: t.ty, z: t.tz });
    if (!ops.length) return toast('Nothing to check', 'bad');
    try {
      const r = await post('mass_edit', {
        archive: level.archive, area: level.area, chunks, ops, dry: true });
      let m = `${r.models} models about pivot `
        + r.pivot.map((v) => v.toFixed(2)).join(', ');
      m += r.proportional ? '\nProportional (one factor).'
                          : '\nNOT proportional — per-axis scale.';
      if (r.collision_note) m += '\n' + r.collision_note;
      if (r.same_size === false) m += '\nThe bundle changed size.';
      toast(m, 'good');
    } catch (e) { toast(e.message, 'bad'); }
  };
  const go = el('button', '', `Apply to ${chunks.length} models`);
  go.onclick = () => applyMassShape(chunks, t,
                                    `${chunks.length} models`);
  row.append(fit, go);
  box.append(row);
}

// ---------------------------------------------------------------- solidity
//
// One plain "Collision" block per model: Solid / Walk-through (default Solid),
// or one line saying why this model has nothing to switch. Backed by the
// server's /set_solid, which edits the linked shell's walker flags (the
// Camera / Shoot / Target flags are left alone, so a walk-through model still
// blocks bullets and the camera). UNTESTED IN-GAME for the walk-through side.
const WALKER_FLAGS = ['BoundFlagMover', 'BoundFlagPlayer', 'BoundFlagEnemy',
                      'BoundFlagHorse'];

function isSolidNode(b) {
  return !!b && WALKER_FLAGS.some((f) => String(b.props?.['attr.' + f]).toLowerCase() === 'true');
}

function renderCollisionSection(box, n) {
  const sec = el('div');
  sec.style.cssText = 'margin:10px 0 4px;padding:7px 9px;border-left:2px solid var(--line);background:var(--bg2)';
  sec.append(el('div', null, 'Collision'));
  sec.firstChild.style.cssText = 'font-size:12px;color:var(--gold);margin-bottom:3px';
  const line = el('div', 'muted');
  line.style.cssText = 'font-size:11px;line-height:1.5';
  const hasPartner = n.linked !== undefined && n.linked !== null;
  const modelNode = n.kind === 'bound' && hasPartner ? nodeOf(n.linked) : n;
  const shell = n.kind === 'bound' ? n : (hasPartner ? nodeOf(n.linked) : null);
  if (hasPartner && shell) {
    const solid = isSolidNode(shell);
    const row = el('div');
    row.style.cssText = 'display:flex;gap:0;margin:2px 0 4px';
    for (const [v, t] of [[true, 'Solid'], [false, 'Walk-through']]) {
      const b = el('button', null, t);
      b.style.cssText = 'font-size:11px;padding:3px 10px;flex:1;' +
        (solid === v ? 'background:var(--gold);color:#111;' : '');
      b.onclick = async () => {
        if (solid === v) return;
        try {
          await post('set_solid', { archive: level.archive, manifest: level.manifest,
                                    area: level.area, uid: n.uid, solid: v });
          toast(v ? 'Solid again — you will bump into it.'
                  : 'Walk-through — you can pass through it. Ctrl+Z undoes this.', 'good');
          await refreshHistory();
          await reloadLevel(true, false);
        } catch (e) { toast(e.message, 'bad'); }
      };
      row.append(b);
    }
    sec.append(row);
    line.textContent = n.kind === 'bound'
      ? `This is ${modelNode ? modelNode.name : 'a model'}'s collision — it is part of the model and moves with it.`
      : 'Its collision moves with it. Walk-through still blocks bullets and the camera.';
  } else {
    line.textContent = 'Checking…';
    post('set_solid', { archive: level.archive, manifest: level.manifest,
                        area: level.area, uid: n.uid, status: true })
      .then((st) => {
        line.textContent = st.note || 'No separate collision to change.';
        if (st.set && st.set.length) {
          const lab = el('label');
          lab.style.cssText = 'display:flex;gap:6px;align-items:flex-start;font-size:11px;margin-top:5px;cursor:pointer';
          const cb = el('input');
          cb.type = 'checkbox';
          cb.checked = moveAsSet;
          cb.onchange = () => setMoveAsSet(cb.checked);
          const shown = st.set.slice(0, 4).join(', ')
            + (st.set.length > 4 ? ` and ${st.set.length - 4} more` : '');
          lab.append(cb, el('span', null,
            `Move its ${st.set.length} attached piece(s) with it (${shown})`));
          sec.append(lab);
        }
      })
      .catch(() => { line.textContent = 'No linked collision to change here.'; });
  }
  sec.append(line);
  // Collision on its own: select a shell and move it without its model (and
  // the model without its shell). The Collision view must be on to click one.
  const alone = el('label');
  alone.style.cssText = 'display:flex;gap:6px;align-items:flex-start;font-size:11px;margin-top:6px;cursor:pointer';
  const acb = el('input'); acb.type = 'checkbox'; acb.checked = collisionAlone;
  acb.onchange = () => {
    collisionAlone = acb.checked;
    toast(collisionAlone
      ? 'Editing collision on its own: click a collision shell (Collision view on) to select and move it by itself. Models move without their shells.'
      : 'Collision moves with its model again.', 'good');
    if (collisionAlone && n.kind !== 'bound' && hasPartner && shell) select(shell.uid);
  };
  alone.append(acb, el('span', null, 'Edit collision on its own (select and move a collision shell by itself)'));
  sec.append(alone);
  const add = el('button', null, 'Add collision box…');
  add.style.cssText = 'font-size:11px;margin-top:6px;width:100%';
  add.title = 'A new solid box: around this object by default, or anywhere you type. A new collision shell of its own.';
  add.onclick = () => addCollisionBoxDialog(n);
  sec.append(add);
  box.append(sec);
}

// A new collision box anywhere in the level (server: collision_add). Starts
// around the selected object's drawn extent; every number can be changed.
function addCollisionBoxDialog(n) {
  const obj = meshes.get(n.uid);
  let lo = [-1, 0, -1], hi = [1, 2, 1];
  if (obj) {
    const b = new THREE.Box3().setFromObject(obj);
    if (!b.isEmpty()) { lo = b.min.toArray(); hi = b.max.toArray(); }
  } else if (n.pos) { lo = [n.pos[0] - 1, n.pos[1], n.pos[2] - 1]; hi = [n.pos[0] + 1, n.pos[1] + 2, n.pos[2] + 1]; }
  const back = el('div');
  back.style.cssText = 'position:fixed;inset:0;z-index:130;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center';
  const dlg = el('div');
  dlg.style.cssText = 'width:min(460px,94vw);background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:14px 16px';
  dlg.append(el('div', null, 'Add collision box'));
  dlg.firstChild.style.cssText = 'font-size:14px;color:var(--title);margin-bottom:8px';
  const grid = el('div');
  grid.style.cssText = 'display:grid;grid-template-columns:70px repeat(3,1fr);gap:6px;align-items:center;font-size:12px';
  const num = (v) => { const i = el('input'); i.type = 'number'; i.step = '0.1'; i.value = (+v).toFixed(2); i.style.width = '100%'; return i; };
  const c = lo.map((v, i) => (v + hi[i]) / 2), sz = lo.map((v, i) => Math.max(0.25, hi[i] - v));
  const ci = c.map(num), si = sz.map(num);
  grid.append(el('span'), el('span', 'muted', 'X'), el('span', 'muted', 'Y (up)'), el('span', 'muted', 'Z'));
  grid.append(el('span', 'muted', 'Centre'), ...ci, el('span', 'muted', 'Size'), ...si);
  const nm = el('input'); nm.value = String(n.name || 'custom').replace(/^(zone|bound)_/i, '').replace(/(_\d+)+$/, '');
  nm.style.width = '100%';
  const solid = el('input'); solid.type = 'checkbox'; solid.checked = true;
  const sl = el('label'); sl.style.fontSize = '12px'; sl.append(solid, el('span', null, ' Solid (walk-through when off: still stops bullets and the camera)'));
  const note = el('div', 'muted'); note.style.cssText = 'font-size:11px;margin-top:8px';
  note.textContent = 'Starts around the selected object. Named bound_<name>_NN: a name matching a model (e.g. ppear01) ties it to that model, so it moves with it. Ctrl+Z undoes it.';
  const nrow = el('div'); nrow.style.cssText = 'display:grid;grid-template-columns:70px 1fr;gap:6px;align-items:center;margin-top:8px;font-size:12px';
  nrow.append(el('span', 'muted', 'Name'), nm);
  const bar = el('div'); bar.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:12px';
  const cancel = el('button', null, 'Cancel'); const ok = el('button', 'primary', 'Add');
  cancel.onclick = () => back.remove();
  ok.onclick = async () => {
    const cc = ci.map((i) => +i.value), ss = si.map((i) => Math.max(0.25, +i.value));
    const body = { archive: level.archive, manifest: level.manifest, area: level.area,
                   lo: cc.map((v, i) => v - ss[i] / 2), hi: cc.map((v, i) => v + ss[i] / 2),
                   name: nm.value.trim() || 'custom', solid: solid.checked };
    ok.disabled = true;
    try {
      const r = await post('collision_add', body);
      if (r.ok === false) throw new Error(r.error_text);
      toast(`Added ${r.name}` + (r.rebuild ? ' (the level is rebuilt to make room on the next Rebuild and play)' : '') + '. Ctrl+Z undoes it.', 'good');
      back.remove();
      await refreshHistory();
      await reloadLevel(true, true);
    } catch (e) { ok.disabled = false; toast(e.message, 'bad'); }
  };
  bar.append(cancel, ok);
  dlg.append(grid, nrow, el('div'), sl, note, bar);
  back.append(dlg);
  back.addEventListener('mousedown', (e) => { if (e.target === back) back.remove(); });
  document.body.append(back);
}

function renderShapeControls(box, n) {
  const ri = n.props && n.props.resourceIndex;
  if (ri == null || !level) return;
  shapeControlsForChunk(box, Math.round(+ri) - 1);
}

// Keyed on the CHUNK, not on a node -- which is what makes the 44 unnamed
// surfaces on TRAIN and 121 on TRAINGOO editable even though nothing in the
// scene files references them.
function shapeControlsForChunk(box, chunk) {
  if (!level) return;
  const rec = renderInfo?.meshes?.find((m) => m.chunk === chunk);
  const bnd = !rec && worldInfo?.meshes?.find((m) => m.resourceIndex === chunk);
  if (!rec && !bnd) return;
  const name = rec ? rec.name : bnd.name;

  box.append(el('h3', '', bnd ? 'Shape (collision)' : 'Shape'));
  box.lastChild.style.cssText =
    'margin:12px 0 2px;font-size:12px;color:var(--gold)';
  const note = el('div', 'muted');
  note.style.cssText = 'font-size:11px;margin-bottom:6px;line-height:1.5';
  note.textContent = bnd
    ? 'Moves what you bump into.'
    : 'Moves the surface. Drag the handle or type here.';
  box.append(note);

  const t = shapeOf(chunk);
  const row = (label, key, step, suffix) => {
    const d = el('div');
    d.style.cssText = 'display:flex;align-items:center;gap:6px;padding:1px 0';
    const l = el('span', 'muted', label);
    l.style.cssText = 'width:64px;flex:none;font-size:11px';
    const inp = el('input');
    inp.type = 'number';
    inp.step = step;
    inp.value = t[key];
    inp.style.cssText = 'flex:1';
    // Named so a gizmo drag can update these in place -- the boxes and the
    // handle are two views of one number, and they must not disagree.
    inp.dataset.shape = key;
    inp.onchange = () => {
      t[key] = parseFloat(inp.value) || (key === 's' ? 1 : 0);
      applyShape(chunk, name);
    };
    d.append(l, inp);
    if (suffix) {
      const s = el('span', 'muted', suffix);
      s.style.cssText = 'font-size:11px;width:16px';
      d.append(s);
    }
    return d;
  };
  box.append(row('Move X', 'tx', '0.5', 'm'));
  box.append(row('Move Y', 'ty', '0.5', 'm'));
  box.append(row('Move Z', 'tz', '0.5', 'm'));
  box.append(row('Scale', 's', '0.1', '×'));
  box.append(row('Spin', 'rot', '15', '°'));

  // Fill them in from the disc rather than from this tab's memory. Without
  // this the boxes read 0 for a building that has already been moved --
  // after a page reload, or in a second tab -- which is a lie about the file.
  currentShape(chunk).then((r) => {
    if (!box.isConnected) return;          // selection moved on; drop it
    for (const [key, val] of Object.entries(r.shape || {})) {
      const inp = box.querySelector(`input[data-shape="${key}"]`);
      if (inp && document.activeElement !== inp)
        inp.value = key === 's' ? (+val).toFixed(3) : (+val).toFixed(2);
    }
    if (r.shape && r.exact === false) {
      const warn = el('div', 'muted', r.why);
      warn.style.cssText =
        'font-size:11px;margin-top:4px;line-height:1.4;color:var(--bad)';
      box.append(warn);
    }
  });

  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:6px;margin-top:6px;flex-wrap:wrap';
  const reset = el('button', '', 'Back to original');
  reset.style.fontSize = '11px';
  reset.onclick = () => {
    Object.assign(t, { tx: 0, ty: 0, tz: 0, s: 1, rot: 0 });
    applyShape(chunk, name).then(renderInspector);
  };
  bar.append(reset);

  // The Blender bridge. Only a MOD0 surface can do this -- a collision bound
  // has no UVs and its own header to keep consistent.
  if (!bnd) {
    const out = el('button', '', 'Export .obj');
    out.style.fontSize = '11px';
    out.title = 'Save this surface as a Wavefront OBJ you can open in Blender';
    out.onclick = () => {
      const u = `/api/geom_export?archive=${encodeURIComponent(level.archive)}`
        + (level.area ? `&area=${encodeURIComponent(level.area)}` : '')
        + `&chunk=${chunk}&name=${encodeURIComponent(name)}`;
      saveFile(u, name + '.obj').then((ok) => { if (ok)
        toast('Exported ' + name + '.obj. Edit it in Blender, then use '
          + '"Import custom model" (any shape) or "Import .obj" (moved '
          + 'points only).', 'good'); });
    };
    bar.append(out);

    const inp = el('input');
    inp.type = 'file';
    inp.accept = '.obj';
    inp.style.display = 'none';
    inp.onchange = async () => {
      const f = inp.files[0];
      if (!f) return;
      const objText = await f.text();
      const body = { archive: level.archive, area: level.area, chunk,
                     label: name, obj: objText };
      try {
        const dry = await post('geom_import', { ...body, dry: true });
        const r = await post('geom_import', body);
        if (r.changed === false) toast('That OBJ matches the original exactly '
          + '— nothing to write', 'good');
        else toast(`Imported: ${dry.moved} of ${dry.vertices} vertices moved`
          + ' — Ctrl+Z to undo', 'good');
        await refreshHistory();
        await reloadLevel(true, true);
      } catch (e) {
        toast(e.message, 'bad');
      }
      inp.value = '';
    };
    const imp = el('button', '', 'Import .obj');
    imp.style.fontSize = '11px';
    imp.title = 'Load an edited OBJ back onto this surface';
    imp.onclick = () => inp.click();
    bar.append(imp, inp);

    // A CUSTOM model: any vertex/triangle count (writes a new display list).
    // Dry-run first so the person sees the size and whether it fits the
    // archive slot before anything touches the disc.
    const minp = el('input');
    minp.type = 'file';
    minp.accept = '.obj';
    minp.style.display = 'none';
    minp.onchange = async () => {
      const f = minp.files[0];
      if (!f) return;
      const objText = await f.text();
      const body = { archive: level.archive, area: level.area, chunk,
                     label: name, obj: objText };
      try {
        const dry = await post('model_import', { ...body, dry: true });
        const msg = `Replace this model with "${f.name}"?\n\n`
          + `${dry.vertices} vertices, ${dry.triangles} triangles\n`
          + `Uses ${dry.stored} of ${dry.slot} bytes available `
          + (dry.fits ? '(fits)' : '(TOO BIG)') + '\n\n'
          + 'Keeps this model\'s texture.\n'
          + (dry.collision_note || 'Collision: not changed.') + '\n'
          + 'Ctrl+Z undoes it.';
        if (!dry.fits) { toast(`Too big: needs ${dry.stored} bytes, only `
          + `${dry.slot} available. Reduce the triangle count in Blender `
          + '(Decimate) and import again. Nothing was changed.', 'bad');
          minp.value = ''; return; }
        if (!confirm(msg)) { minp.value = ''; return; }
        const r = await post('model_import', body);
        toast((r.message || 'Custom model written.') + ' ' + (r.next || ''),
          'good');
        await refreshHistory();
        await reloadLevel(true, true);
      } catch (e) {
        toast(e.message, 'bad');
      }
      minp.value = '';
    };
    const mimp = el('button', '', 'Import custom model');
    mimp.style.fontSize = '11px';
    mimp.title = 'Replace this surface with an OBJ that has ANY number of '
      + 'vertices (not just moved ones)';
    mimp.onclick = () => minp.click();
    bar.append(mimp, minp);
  }
  box.append(bar);

  if (!bnd) {
    const how = el('details');
    how.style.cssText = 'font-size:11px;margin-top:6px;line-height:1.5';
    how.open = true;
    const sm = el('summary', '', 'How to use a custom model');
    sm.style.cssText = 'cursor:pointer;color:var(--gold)';
    const ol = el('ol');
    ol.style.cssText = 'margin:4px 0 4px 16px;padding:0';
    for (const t of [
      'Pick the level and the model in the list (this panel).',
      'Press "Export .obj" and open the file in Blender.',
      'Reshape it. Keep it roughly the same size and in the same place.',
      'Export from Blender: File > Export > Wavefront (.obj).',
      'Press "Import custom model" and choose that file.',
      'Press Play (top bar) to see it in the game.',
    ]) ol.append(el('li', '', t));
    const lim = el('div', 'muted');
    lim.textContent = 'Limits: the new model must be about as small as the '
      + 'original (the Studio tells you if it is too big). It gets a solid '
      + 'box for collision (fitted to its size, moves with it); the Studio '
      + 'says if there was no room for one. It keeps the '
      + 'original texture. Full guide: Guides (bottom bar) > Custom models.';
    how.append(sm, ol, lim);
    box.append(how);
    const hint = el('div', 'muted');
    hint.style.cssText = 'font-size:11px;margin-top:5px;line-height:1.5';
    hint.textContent = 'Import .obj only moves points (same count). '
      + 'Import custom model accepts any shape.';
    box.append(hint);
    renderMaterialControls(box, chunk, rec);
  }
}

// Each surface carries a base colour the game multiplies into its texture.
// It is white on 97% of surfaces, which is exactly why it is worth exposing:
// the knob is there, unused, on nearly everything.
function renderMaterialControls(box, chunk, rec) {
  const groups = rec.groups || [];
  if (!groups.length) return;
  box.append(el('h3', '', 'Surface colour'));
  box.lastChild.style.cssText =
    'margin:12px 0 2px;font-size:12px;color:var(--gold)';
  const note = el('div', 'muted');
  note.style.cssText = 'font-size:11px;margin-bottom:6px;line-height:1.5';
  note.textContent = 'Tint multiplied into the texture. Always fits.';
  box.append(note);

  const texNames = renderInfo?.textures || [];
  groups.forEach((g, i) => {
    const row = el('div');
    row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:2px 0';
    const label = el('span', 'muted',
      g.texture != null ? (texNames[g.texture]?.name || `#${g.texture}`)
                        : 'untextured');
    label.style.cssText = 'flex:1;font-size:11px;overflow:hidden;'
      + 'text-overflow:ellipsis;white-space:nowrap';
    row.append(label);

    const c = g.colour || [1, 1, 1, 1];
    const hex = (n) => Math.round(Math.min(1, Math.max(0, n)) * 255)
      .toString(16).padStart(2, '0');
    const sw = el('input');
    sw.type = 'color';
    sw.value = '#' + c.slice(0, 3).map(hex).join('');
    sw.style.cssText = 'width:34px;height:22px;padding:0;flex:none';
    sw.title = `rgba(${c.map((n) => n.toFixed(2)).join(', ')})`;
    sw.onchange = async () => {
      const m = sw.value.slice(1).match(/../g).map((h) => parseInt(h, 16) / 255);
      try {
        await post('material_set', {
          archive: level.archive, area: level.area, chunk, part: i,
          colour: [m[0], m[1], m[2], c[3]],
          label: `tint ${rec.name}` });
        toast('Recoloured — Ctrl+Z to undo', 'good');
        await refreshHistory();
        await reloadLevel(true, true);
      } catch (e) { toast(e.message, 'bad'); }
    };
    row.append(sw);
    box.append(row);
  });
}

// A door, a camera shot, a glow or an AI spawn point. Everything it shows
// comes straight out of the file; nothing is inferred, and where the answer is
// "the disc does not say", it says that.
function renderEventInspector(box, n) {
  showInspectorModel(null);
  const d = n.event.data || {};
  box.append(el('div', 'nm', n.name));
  box.lastChild.style.cssText = 'font-size:13px;color:var(--gold)';
  box.append(el('div', 'muted', `${KINDS[n.kind].label} — ${n.source}`));

  const facts = el('div', 'muted');
  facts.style.cssText = 'font-size:11px;line-height:1.7;margin:8px 0;white-space:pre-line';
  const at = (v) => v ? v.map((x) => x.toFixed(2)).join(', ') : '—';

  if (n.kind === 'door') {
    facts.textContent =
      `loads       ${d.level}\n`
      + `shown as    ${d.display}\n`
      + `enter at    ${at(d.enter)}\n`
      + `return to   ${at(d.leave)}\n`
      + `door model  ${d.door_model || '—'}\n`
      + `door anim   ${d.door_anim || '—'}\n`
      + `lock        ${d.lock === -1 ? 'unlocked' : d.lock}`;
  } else if (n.kind === 'shot') {
    facts.textContent =
      `eye        ${at(d.eye)}\n`
      + `looks at   ${at(d.target)}\n`
      + `lens       ${d.lens?.toFixed(3)}\n`
      + `kind       ${d.cutscene ? 'cutscene shot (TGS_)'
                     : d.duel ? 'duel view' : 'camera shot'}`;
  } else if (n.kind === 'glow') {
    facts.textContent =
      `texture    ${d.texture}\nposition   ${at(d.pos)}\n`
      + `scale      ${d.scale?.toFixed(2)}\nfade angle ${d.fade_angle?.toFixed(1)}°`;
  } else {
    facts.textContent =
      `group      ${n.event.group}\nowner      ${d.owner} (${d.kind})\n`
      + `position   ${at(d.pos)}\nheading    ${d.heading?.toFixed(1)}°`;
  }
  box.append(facts);

  const why = el('div', 'muted');
  why.style.cssText = 'font-size:11px;line-height:1.6;margin-bottom:8px';
  why.textContent = n.kind === 'shot'
    ? 'Read-only here. Edit the .cam file as text.'
    : n.kind === 'door'
      ? 'Read-only here. Edit the .sub file as text.'
      : 'Read-only here. Edit the file as text.';
  box.append(why);

  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
  const fly = el('button', '', 'Frame it');
  fly.style.fontSize = '11px';
  fly.onclick = () => focusOn(n.uid);
  bar.append(fly);
  if (n.kind === 'shot') {
    const look = el('button', 'primary', 'Look through it');
    look.style.fontSize = '11px';
    look.onclick = () => {
      camera.position.set(d.eye[0], d.eye[1], d.eye[2]);
      controls.target.set(d.target[0], d.target[1], d.target[2]);
      controls.update();
      toast(`Looking through ${n.name}`, 'good');
    };
    bar.append(look);
  }
  if (n.kind === 'door' && d.level) {
    // Outbound from the master goes into the shop; the marker inside a shop is
    // the way back, so it goes to the master instead.
    const out = n.event.outbound;
    const dest = out ? d.level : masterArea(d);
    const go = el('button', 'primary',
      out ? `Go into ${d.display || d.level}` : `Back to ${d.master || 'the main level'}`);
    go.style.fontSize = '11px';
    go.title = 'Load the area on the other side of this door';
    go.onclick = () => enterArea(dest);
    bar.append(go);
  }
  const open = el('button', '', `Open ${n.source} as text`);
  open.style.fontSize = '11px';
  open.onclick = () => openTextFile(level.archive, n.source);
  bar.append(open);
  box.append(bar);
}

// The master level a `.sub` belongs to, derived from its own filename --
// `lvl_tomb.sub` describes Tombstone, whose area is `lvl_tomb.rsc`.
function masterArea(d) {
  const n = level.nodes.find((x) => x.kind === 'door');
  const src = (n && n.source) || '';
  return src.replace(/\.sub$/i, '');
}

// Walk through a door: load the area it names, in the same archive.
function enterArea(levelName) {
  const stem = String(levelName).toLowerCase().replace(/^lvl_/, '');
  const btn = [...document.querySelectorAll('#areaList .chip, #areaList button')]
    .find((b) => b.textContent.trim().toLowerCase() === stem);
  if (btn) { btn.click(); return toast(`Entered ${stem}`, 'good'); }
  toast(`This archive has no area called "${stem}" — the door names a level `
    + 'that lives in a different .DAT.', 'bad');
}

// Sections the ES modules add to the Inspector. Called with the container and
// the primary node, right after the header, so a module lands its panel where
// it belongs rather than watching for DOM mutations and appending at the end.
// A hook that throws is logged and skipped: one broken section must not cost
// the rest of the panel, which is the same bargain the module loader makes.
const inspectorHooks = [];
function addInspectorSection(fn) { inspectorHooks.push(fn); }
function runInspectorHooks(box, n) {
  for (const fn of inspectorHooks) {
    try { fn(box, n); } catch (e) { console.error('[inspector section]', e); }
  }
}

function renderInspector() {
  const box = $('#inspector');
  box.innerHTML = '';
  if (selected == null) {
    box.append(el('p', 'muted', 'Pick a level, then click an object in the viewport.'));
    showInspectorModel(null);
    runInspectorHooks(box, null);
    return;
  }
  const n = nodeOf(selected);
  const obj = meshes.get(selected);
  if (!n) return;
  // An event marker is read-only in 3D: it comes from a file with no matrix,
  // so there is nothing to drag. Its numbers are still editable, as text, in
  // the file it lives in.
  if (n.readonly) return renderEventInspector(box, n);
  showInspectorModel(selection.size > 1 ? null : n);

  const h = el('div');
  h.style.marginBottom = '8px';
  if (selection.size > 1) {
    const files = new Set([...selection].map((u) => nodeOf(u)?.source));
    h.append(el('div', null, `${selection.size} objects selected`));
    h.append(el('div', 'muted',
      `across ${files.size} file(s) — drag the gizmo to move them together`));
    h.append(el('div', 'muted', `editing anchor: ${n.name}`));
  } else {
    h.append(el('div', null, n.name));
    const ti = typeInfo(n);
    h.append(el('div', 'muted', `${ti.label} — ${n.source}`));
    // What the game calls it, in the game's own words. A box named
    // box_TRIGGER_Window_saloon_01 is a dive-through-a-window prompt, and
    // nothing in the tool used to say so.
    if (ti.sub) {
      const s = el('div', 'muted');
      s.style.cssText = 'font-size:11px;margin-top:2px';
      s.textContent = ti.subGloss ? `${ti.sub} — ${ti.subGloss}` : ti.sub;
      h.append(s);
    }
    if (ti.gloss) {
      const g = el('div', 'muted');
      g.style.cssText = 'font-size:11px;margin-top:3px;line-height:1.55';
      g.textContent = ti.gloss;
      h.append(g);
    }
  }
  box.append(h);
  runInspectorHooks(box, n);

  // A .znes / .bnds instance matrix is IDENTITY in almost every level, because
  // level geometry is authored in world space -- 250 of 250 in MANS, 343 of 343
  // in TOMB, 2 of 209 non-identity in PERFNEW. So for a building, the Position
  // fields read 0/0/0, and typing in them writes a real change to the .znes
  // that the viewport cannot show, because the surface is drawn at its authored
  // vertices. The control that actually moves a building is Shape, which
  // rewrites the vertices -- so put Shape FIRST and say plainly what Position
  // is. It used to be the other way round, 301px apart.
  const worldGeom = (n.kind === 'zone' || n.kind === 'bound')
                 && n.props && n.props.resourceIndex != null;
  if (worldGeom && selection.size === 1) {
    renderShapeControls(box, n);
    box.append(el('hr'));
  }
  // Model + collision are ONE thing (owner, 11/5): the solid / walk-through
  // switch lives here, in the model's own panel -- there is no separate
  // collision object to find, select or remove.
  if ((n.kind === 'zone' || n.kind === 'bound') && selection.size === 1)
    renderCollisionSection(box, n);
  // M6. Only when more than one thing is selected, and only for the ones
  // that are actually world geometry -- renderMassShapeControls draws
  // nothing if fewer than two of the selection have a resourceIndex.
  if (selection.size > 1) {
    const before = box.childElementCount;
    renderMassShapeControls(box);
    if (box.childElementCount > before) box.append(el('hr'));
  }

  const live = dirty.get(selected) || n.pos;
  const wrap = el('div');
  if (worldGeom) {
    // The old note here claimed the viewport ignores this matrix. It no
    // longer does -- copies are drawn at vertex centre + matrix, which is
    // what the data means. One line is enough now that the two agree.
    const ident = Math.abs(n.pos[0]) < 1e-6 && Math.abs(n.pos[1]) < 1e-6
               && Math.abs(n.pos[2]) < 1e-6;
    wrap.append(el('div', 'muted', ident ? 'Offset (baked in world space)'
                                         : 'Offset (x / y / z)'));
    if (ident) {
      const w = el('div', 'muted');
      w.style.cssText = 'font-size:11px;margin:2px 0 4px';
      w.textContent = 'Zero here — use Shape above to move it.';
      wrap.append(w);
    }
  } else {
    wrap.append(el('div', 'muted', 'Position (x / y / z)'));
  }
  const xyz = el('div', 'xyz');
  ['x', 'y', 'z'].forEach((ax, i) => {
    const inp = el('input');
    inp.value = (+live[i]).toFixed(3);
    // Commit on Enter or on leaving the field -- NOT on every keystroke.
    // Writing per keystroke meant typing "-12" wrote at "-" (which parses to
    // nothing), then "-1", then "-12", each one a disc write plus a level
    // reload that rebuilt this panel underneath the cursor. That is what made
    // it feel glitchy and what made negatives so awkward to enter.
    const commit = () => {
      const v = parseFloat(inp.value);
      if (!isFinite(v)) { inp.value = (+ (dirty.get(selected) || n.pos)[i]).toFixed(3); return; }
      const p = [...(dirty.get(selected) || n.pos)];
      if (Math.abs(p[i] - v) < 1e-9) return;      // nothing actually changed
      rememberHome(selected);        // BEFORE moving it, so revert can undo
      p[i] = v;
      dirty.set(selected, p);
      const off = obj.userData.yOffset || 0;
      obj.position.set(p[0], p[1] + off, p[2]);
      pushChange(selected, { pos: p });
    };
    inp.onkeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
      e.stopPropagation();          // keep WASD out of the viewport flyer
    };
    inp.onblur = commit;
    xyz.append(inp);
  });
  wrap.append(xyz);
  box.append(wrap);

  // Heading, in degrees, for the nodes that carry one. Same commit rule.
  if (n.angle != null) {
    const rw = el('div', 'field');
    rw.append(el('label', null, 'Turn'));
    const ri = el('input');
    ri.value = (+(dirtyAngle.get(selected) ?? n.angle)).toFixed(2);
    ri.style.flex = '1';
    const commitAngle = () => {
      const v = parseFloat(ri.value);
      if (!isFinite(v)) { ri.value = (+n.angle).toFixed(2); return; }
      if (Math.abs((dirtyAngle.get(selected) ?? n.angle) - v) < 1e-6) return;
      dirtyAngle.set(selected, v);
      obj.rotation.y = THREE.MathUtils.degToRad(v);
      pushChange(selected, { angle: v });
    };
    ri.onkeydown = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); ri.blur(); }
      e.stopPropagation();
    };
    ri.onblur = commitAngle;
    rw.append(ri, el('span', 'muted', '°'));
    box.append(rw);
  }

  if (n.size) {
    box.append(el('div', 'muted', 'Size'));
    for (const k of ['width', 'height', 'depth']) {
      if (n.size[k] == null) continue;
      box.append(numField(k, n.size[k], (v) => {
        const size = { ...n.size, [k]: v };
        obj.geometry.dispose();
        obj.geometry = new THREE.BoxGeometry(size.width, size.height, size.depth);
        pushChange(selected, { size: { [k]: v } });
      }));
    }
  }

  // No second "Heading" field here: the Turn field above IS the heading
  // control. Two inputs for the same number disagreed the moment one of them
  // was edited -- Turn tracked the pending value, Heading still showed the
  // file's -- and the stale one won whichever you blurred last.

  // The typed attribute block gets its own section, above the raw properties,
  // because it is the only thing in the file that explains WHY a surface
  // renders or collides the way it does -- and because for a collision bound
  // it is twelve rows that would otherwise drown the two fields that matter.
  renderAttributes(box, n);

  if (n.props && Object.keys(n.props).length) {
    box.append(el('div', 'muted', 'Properties'));
    const t = el('table');
    for (const [k, v] of Object.entries(n.props)) {
      if (k.startsWith('attr.')) continue;      // shown above, with glosses
      const tr = el('tr');
      const editable = n.kind === 'enemy' && (k in editableFields);
      const cell = el('td');
      if (editable) {
        const inp = el('input');
        inp.value = String(v);
        inp.style.cssText = 'width:100%; font-size:12px';
        inp.title = editableFields[k];
        inp.onchange = async () => {
          try {
            const r = await post('set_field', {
              archive: level.archive, manifest: level.manifest, area: level.area, uid: selected, key: k, value: inp.value,
            });
            if (!r.applied) {
              toast('Refused: ' + (r.error || "doesn't fit its slot"), 'bad');
              return;
            }
            toast(`${n.name}: ${k} = ${inp.value}`, 'good');
            await reloadLevel(true);
            refreshHistory();
          } catch (e) { toast('Failed: ' + e.message, 'bad'); }
        };
        cell.append(inp);
      } else {
        cell.textContent = String(v);
      }
      tr.append(el('td', 'muted', k), cell);
      t.append(tr);
    }
    box.append(t);
    // Only say this when there IS a greyed row. It used to print under every
    // enemy, including ones whose fields were all editable, so the note
    // contradicted the inputs directly above it.
    // `editableFields` is an OBJECT -- field name to what it means -- which is
    // exactly how the two lines above already use it. This one treated it as
    // an array and called .includes(), throwing a TypeError that aborted
    // renderInspector part-way through. The effect was that selecting an
    // enemy, the commonest object in any level, silently lost Duplicate,
    // Delete and Place copies, with nothing on screen to say why.
    if (n.kind === 'enemy'
        && Object.keys(n.props).some((k) => !(k in (editableFields || {}))))
      box.append(el('div', 'muted',
        'The greyed rows name AI behaviour files that have to already exist, '
        + 'so they cannot be typed over.'));
  }

  const acts = el('div');
  acts.style.cssText = 'margin-top:12px; display:flex; gap:6px; flex-wrap:wrap';

  const dup = el('button', 'primary', 'Duplicate');
  dup.title = 'Clone this object into its file\'s spare slot space';
  dup.onclick = () => duplicateSelected();

  const del = el('button', '', 'Delete');
  del.onclick = () => deleteSelected();

  const place = el('button', placeMode ? 'primary' : '',
                   placeMode ? 'Placing — click the ground (Esc)' : 'Place copies…');
  place.id = 'btnPlace';
  place.title = 'Then click anywhere in the level to drop a copy there';
  place.onclick = () => setPlaceMode(!placeMode);

  acts.append(dup, del, place);
  box.append(acts);

  const nudge = el('div', 'muted');
  nudge.style.cssText = 'margin-top:6px; font-size:11px';
  nudge.textContent =
    'Arrow keys nudge 0.1 m along X/Z, PgUp/PgDn along Y. Hold Shift for 1 m.';
  box.append(nudge);

  const open = el('button');
  open.textContent = 'Open ' + n.source + ' as text';
  open.style.marginTop = '10px';
  open.onclick = () => openTextFile(level.archive, n.source);
  box.append(open);

  // World geometry already got its Shape section at the TOP, where it belongs.
  // Anything else that happens to reference a surface still gets it here.
  if (selection.size === 1 && !worldGeom) renderShapeControls(box, n);
}

// -------------------------------------------------------- place by clicking

// Duplicate-and-drag works but is awkward for "put one THERE". Place mode
// clones the selected object straight onto the surface you click.
let placeMode = false;

// The viewport, not the document, goes fullscreen: the canvas then fills the
// screen while the app's own state and event handlers carry on untouched.
// resize() already runs in the render loop, so the canvas picks up the new
// size on the next frame without any extra plumbing.
function toggleFullscreen() {
  if (document.fullscreenElement === view) {
    document.exitFullscreen?.();
  } else {
    view.requestFullscreen?.().catch((e) =>
      toast('Fullscreen refused: ' + e.message, 'bad'));
  }
}

function setPlaceMode(on) {
  placeMode = on && selected != null;
  renderer.domElement.style.cursor = placeMode ? 'crosshair' : '';
  // A mode where clicks WRITE needs a banner, not a cursor change. The only
  // cues used to be the crosshair and a HUD line, and the owner's report of
  // the result was "clicking copies it or something".
  const bn = $('#modeBanner');
  if (bn) {
    const nm = nodeOf(selected)?.name || 'object';
    bn.textContent = 'Placing a copy of ' + nm
      + ' — click the ground · Shift+click for more · Esc to stop';
    bn.style.display = placeMode ? 'block' : 'none';
  }
  const b = $('#btnPlace');
  if (b) {
    b.textContent = placeMode ? 'Placing — click the ground (Esc)' : 'Place copies…';
    b.classList.toggle('primary', placeMode);
  }
  if (on && selected == null) toast('Select something first, then place copies of it.');
  updateHud();
}

// Raycast against the decoded world so a click lands on real ground, falling
// back to the horizontal plane through the source object when nothing is hit.
let placeBusy = false;

function surfacePoint(ev, strict) {
  const r = renderer.domElement.getBoundingClientRect();
  ptr.x = ((ev.clientX - r.left) / r.width) * 2 - 1;
  ptr.y = -((ev.clientY - r.top) / r.height) * 2 + 1;
  ray.setFromCamera(ptr, camera);

  const targets = [];
  if (worldGroup.visible) targets.push(...worldGroup.children);
  if (pickFilter !== 'collision' && renderGroup.visible)
    targets.push(...renderGroup.children);
  const hits = targets.length ? ray.intersectObjects(targets, true) : [];
  if (hits.length) return hits[0].point;

  // The infinite-plane fallback exists for levels with no decoded ground
  // under the cursor. In place mode it is refused: a click into empty sky
  // used to intersect that plane kilometres out and WRITE a copy there.
  if (strict) return null;
  const src = nodeOf(selected);
  const y = src ? src.pos[1] : 0;
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -y);
  const p = new THREE.Vector3();
  return ray.ray.intersectPlane(plane, p) ? p : null;
}

// Where does this node's geometry actually SIT on screen?
//
// Two cases, and the difference is the whole reason placed copies landed in
// the wrong spot. A node's matrix translates its chunk's vertices:
//
//   render position = vertex centre + matrix translation
//
// Glass panes and other placed props are authored AT THE ORIGIN (measured:
// chunk 107's vertices are centred on [0,0,0], extent 0.5 x 1.1 m), so their
// centre is ~0 and the matrix alone places them. A building is baked in WORLD
// space with an identity matrix, so its centre is its position and the matrix
// contributes nothing. Treating the second like the first put a copy at
// `building + click` instead of at `click`.
function visualCentreOf(uid) {
  const n = nodeOf(uid);
  if (!n) return null;
  const mesh = meshForNode(uid);
  if (!mesh) return new THREE.Vector3(n.pos[0], n.pos[1], n.pos[2]);
  const g = mesh.geometry;
  if (!g.boundingBox) g.computeBoundingBox();
  const c = g.boundingBox.getCenter(new THREE.Vector3());
  return c.add(new THREE.Vector3(n.pos[0], n.pos[1], n.pos[2]));
}

async function placeAt(point, keep) {
  const src = nodeOf(selected);
  if (!src) return;
  placeBusy = true;
  // One copy per arm, like every other editor: the mode exits after a
  // successful drop unless Shift was held. It used to ADVERTISE stickiness
  // while buildLevel silently killed it after one placement -- and every
  // rapid click before the reload landed wrote another copy.
  const again = !!keep;
  // Solve `point = vertexCentre + (oldMatrix + offset)` for offset, so the
  // copy lands under the cursor whichever kind of node it is.
  const here = visualCentreOf(selected)
    || new THREE.Vector3(src.pos[0], src.pos[1], src.pos[2]);
  const offset = [point.x - here.x, point.y - here.y, point.z - here.z];
  try {
    const dry = await post('duplicate', {
      archive: level.archive, manifest: level.manifest, area: level.area, uid: selected,
      offset, dry: true,
    });
    const rep = Object.values(dry.report || {})[0];
    if (rep && !rep.fits) {
      return toast(`No room left in ${src.source} — ${rep.stored} stored bytes ` +
                   `needed, slot is ${rep.slot}. Check the Capacity tab.`, 'bad');
    }
    const res = await post('duplicate', {
      archive: level.archive, manifest: level.manifest, area: level.area, uid: selected, offset,
    });
    if (!res.applied) return toast('Could not place: ' + (res.error || 'no room'), 'bad');
    toast(`Placed ${res.new_name} at ` +
          `${point.x.toFixed(1)}, ${point.y.toFixed(1)}, ${point.z.toFixed(1)}`, 'good');
    await reloadLevel(true);
    refreshHistory();
    if (again && selected != null) setPlaceMode(true);
  } catch (e) { toast('Place failed: ' + e.message, 'bad'); }
  finally { placeBusy = false; }
}

// ------------------------------------------------------- create / remove

async function duplicateSelected() {
  if (selected == null || !level) return;
  // One at a time: each clone changes the file, so uids shift underneath us.
  const targets = [...selection];
  const made = [];
  for (const uid of targets) {
    const n = nodeOf(uid);
    if (!n) continue;
    try {
      const dry = await post('duplicate',
        { archive: level.archive, manifest: level.manifest, area: level.area, uid, dry: true });
      const rep = Object.values(dry.report || {})[0];
      if (rep && !rep.fits) {
        toast(
          `No room in ${n.source}.\nA copy needs ${rep.stored} stored bytes but ` +
          `the slot is ${rep.slot}. Delete something in that file first, or ` +
          `duplicate an object from a roomier one. ` +
          `Check the Capacity tab.${made.length ? `\n${made.length} clone(s) were made before this.` : ''}`,
          'bad');
        break;
      }
      const res = await post('duplicate', { archive: level.archive, manifest: level.manifest, area: level.area, uid });
      if (!res.applied) {
        toast('Could not create: ' + (res.error || 'no room'), 'bad');
        break;
      }
      made.push(res.new_name);
      if (targets.length > 1) await reloadLevel(true);
    } catch (e) { toast('Duplicate failed: ' + e.message, 'bad'); break; }
  }
  if (!made.length) return;
  // The copy lands 1 m away and, at building scale, entirely inside the
  // original -- which looked like nothing happened. Say where it is.
  toast(made.length === 1
    ? `Created ${made[0]} — 1 m from the original. Drag it into place.`
    : `Created ${made.length} objects, each 1 m from its original`, 'good');
  await reloadLevel(true);
  refreshHistory();
  const first = level.nodes.find((x) => x.name === made[0]);
  if (first) { select(first.uid); focusOn(first.uid); }
}

async function deleteSelected() {
  if (selected == null || !level) return;
  const targets = [...selection];
  const names = targets.map((u) => nodeOf(u)?.name).filter(Boolean);
  const what = names.length === 1 ? names[0] : `${names.length} objects`;
  if (!confirm(`Delete ${what}?`)) return;

  let n = 0;
  for (const uid of targets) {
    // Re-resolve by name: earlier deletions renumber the uids.
    const node = level.nodes.find((x) => x.name === names[n]);
    const target = node ? node.uid : uid;
    try {
      const res = await post('delete', { archive: level.archive, manifest: level.manifest, area: level.area, uid: target });
      if (!res.applied) { toast('Could not delete: ' + (res.error || '?'), 'bad'); break; }
      n++;
      await reloadLevel(true);
    } catch (e) { toast('Delete failed: ' + e.message, 'bad'); break; }
  }
  if (n) toast(`Deleted ${n} object(s)`, 'good');
  select(null);
  await reloadLevel(true);
  refreshHistory();
}

// -------------------------------------------------------------- movies

// The 22 FMV cutscenes. They are MPEG-2 Program Streams sitting in PSS/ on the
// ISO itself, so "export" is just handing the bytes over with an .mpg name.
// Whether the browser can PLAY one is a different question: the video is
// ordinary MPEG-2 but the audio is Sony's private-stream ADPCM, which no
// browser decodes. The player is offered, and the save link is always there
// for when it cannot.
let movies = [];

async function initMoviesTab() {
  if (movies.length) return;
  const box = $('#movList');
  box.innerHTML = '<div class="pad muted">Reading the disc…</div>';
  try {
    const r = await api('movies');
    movies = r.movies || [];
  } catch (e) {
    box.innerHTML = '';
    return box.append(el('div', 'pad muted', 'Could not list: ' + e.message));
  }
  box.innerHTML = '';
  for (const m of movies) {
    const row = el('div', 'row');
    row.append(el('span', 'nm', m.name.replace(/\.PSS$/i, '')),
               el('span', 'muted', (m.bytes / 1048576).toFixed(1) + ' MB'));
    row.style.cssText = 'display:flex;justify-content:space-between;gap:8px';
    row.onclick = () => openMovie(m);
    box.append(row);
  }
}

// The `.ipu` screens sit alongside the cutscenes because that is what they are
// to a modder: the other things the game puts on screen whole. They are NOT
// video, and this panel says so rather than implying a player will appear.
let stills = [];

async function initStillsTab() {
  if (stills.length) return;
  const box = $('#stillList');
  box.innerHTML = '<div class="pad muted">Scanning every archive…</div>';
  let r;
  try {
    r = await api('stills');
  } catch (e) {
    box.innerHTML = '';
    box.append(el('div', 'pad muted', 'Could not list: ' + e.message));
    return;
  }
  stills = r.stills || [];
  box.innerHTML = '';
  // This line used to say "pixels are not decoded yet" long after the IPU
  // decoder landed and this very panel started showing every screen decoded.
  // The list must describe what clicking a row actually does.
  box.append(el('div', 'pad muted',
    r.distinct + ' distinct, ' + r.total_entries + ' entries on the disc — '
    + 'click one to see it decoded, save it, or replace it'));
  for (const s of stills) {
    const row = el('div', 'row');
    row.style.cssText = 'display:flex;justify-content:space-between;gap:8px';
    row.append(el('span', 'nm', s.name.replace(/\.ipu$/i, '')),
               el('span', 'muted', s.ok ? s.width + '×' + s.height : 'bad'));
    row.onclick = () => openStill(s);
    box.append(row);
  }
}

function openStill(s) {
  const p = $('#movPlayer');
  p.style.display = 'none';
  p.removeAttribute('src');
  $('#movSave').style.display = 'none';
  $('#movStreams').textContent = '';
  $('#movInfo').textContent = s.ok
    ? s.name + ' — ' + s.width + '×' + s.height
      + ' · ' + (s.bytes / 1024).toFixed(1) + ' KB'
      + ' · ' + s.frames + ' frame' + (s.frames === 1 ? '' : 's')
      + ' · in ' + s.copies + ' archive' + (s.copies === 1 ? '' : 's')
    : s.name + ' — ' + s.error;
  const h = $('#movHint');
  const img = $('#stillImg');
  if (s.ok) {
    // Decoded server-side by rdrstudio/ipudec.py. Shown at native size with
    // the picture's own aspect, not stretched to the panel.
    h.style.display = 'none';
    img.style.display = '';
    img.src = '/api/still_png?name=' + encodeURIComponent(s.name)
            + '&archive=' + encodeURIComponent(s.archive || '');
    img.onerror = () => {
      img.style.display = 'none';
      h.style.display = '';
      h.textContent = 'Could not decode ' + s.name + '.';
    };
  } else {
    img.style.display = 'none';
    h.style.display = '';
    h.textContent = 'This file does not parse as an ipum image: ' + s.error;
  }
  const save = $('#stillSave');
  save.href = '/api/still_export?name=' + encodeURIComponent(s.name)
            + '&archive=' + encodeURIComponent(s.archive || '');
  save.download = s.name;
  save.style.display = s.ok ? '' : 'none';

  stillTarget = s.ok ? s : null;
  $('#stillReplaceBtn').style.display = s.ok ? '' : 'none';
  const drop = $('#stillDrop');
  drop.style.display = 'none';
  drop.innerHTML = '';
  stillPending = null;
}

// ---- replacing a screen with your own picture -----------------------------
// Two steps on purpose. The estimate encodes the picture and reports whether
// it fits BEFORE anything is written, because fitting is not a given: the
// image has to live in the slot the original occupies, and on 36 of the 63
// screens that slot is SMALLER than the file sitting in it.
let stillTarget = null;
let stillPending = null;

async function stillEstimate(file) {
  if (!stillTarget) return;
  const drop = $('#stillDrop');
  drop.style.display = '';
  drop.innerHTML = '<span class="muted">Encoding ' + file.name + '…</span>';
  let b64;
  try {
    b64 = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result).split(',')[1]);
      r.onerror = () => rej(new Error('could not read that file'));
      r.readAsDataURL(file);
    });
  } catch (e) {
    drop.innerHTML = '<span style="color:var(--red)">' + e.message + '</span>';
    return;
  }
  let r;
  try {
    r = await post('still_estimate', { name: stillTarget.name, data_b64: b64 });
  } catch (e) {
    drop.innerHTML = '<span style="color:var(--red)">Could not encode: '
                   + e.message + '</span>';
    return;
  }
  if (!r.ok) {
    drop.innerHTML = '<b style="color:var(--red)">Will not fit.</b> '
                   + (r.error || 'unknown reason');
    stillPending = null;
    return;
  }
  // `data_b64`, NOT `b64`. still_estimate and still_replace are one branch in
  // studio.py reading one key, and the estimate above sends the right name
  // while this object sent the wrong one -- so every estimate said "fits" and
  // every Write it in came back "could not read that image: 'data_b64'". The
  // encoder was never involved; the picture never reached it.
  stillPending = { name: r.name, data_b64: b64 };
  const q = r.quality === 1
    ? '<b style="color:var(--grn)">full quality</b>'
    : '<b style="color:var(--warn)">quality ' + r.quality + ' of 31</b>'
      + ' <span class="muted">(coarsened to fit)</span>';
  drop.innerHTML =
    'Fits — ' + q + '<br><span class="muted">'
    + r.stored.toLocaleString() + ' of the ' + r.slot.toLocaleString()
    + ' byte slot, ' + r.headroom.toLocaleString() + ' spare'
    + (r.archives > 1
        ? ' · budgeted against the tightest of ' + r.archives + ' copies ('
          + r.tightest_archive + ')'
        : '')
    + '</span><br><button id="stillCommit" class="primary" '
    + 'style="margin-top:6px">Write it in</button> '
    + '<span class="muted">goes to your working disc and into Changes, '
    + 'so it can be undone</span>';
  $('#stillCommit').onclick = stillCommit;
}

async function stillCommit() {
  if (!stillPending) return;
  const drop = $('#stillDrop');
  drop.innerHTML = '<span class="muted">Writing…</span>';
  let r;
  try {
    r = await post('still_replace', stillPending);
  } catch (e) {
    drop.innerHTML = '<span style="color:var(--red)">Failed: ' + e.message
                   + '</span>';
    return;
  }
  if (!r.ok) {
    drop.innerHTML = '<span style="color:var(--red)">'
                   + (r.error || 'failed') + '</span>';
    return;
  }
  drop.innerHTML = '<b style="color:var(--grn)">Written</b> to '
    + r.written.length + ' archive' + (r.written.length === 1 ? '' : 's')
    + (r.failed && r.failed.length
        ? ' · <span style="color:var(--red)">' + r.failed.length
          + ' refused</span>' : '');
  stillPending = null;
  // Re-fetch rather than trusting the cached PNG: the point is to see the real
  // decode of what actually went onto the disc.
  const img = $('#stillImg');
  if (img && stillTarget) {
    img.src = '/api/still_png?name=' + encodeURIComponent(stillTarget.name)
            + '&archive=' + encodeURIComponent(stillTarget.archive || '')
            + '&t=' + Date.now();
  }
  if (typeof refreshHistory === 'function') refreshHistory();
}

async function openMovie(m) {
  $('#stillSave').style.display = 'none';
  $('#stillReplaceBtn').style.display = 'none';
  $('#stillDrop').style.display = 'none';
  const si = $('#stillImg');
  if (si) { si.style.display = 'none'; si.removeAttribute('src'); }
  $('#movInfo').textContent = `${m.name} — reading…`;
  $('#movStreams').textContent = '';
  const p = $('#movPlayer');
  p.style.display = 'none';
  $('#movHint').style.display = '';
  $('#movHint').textContent = 'Reading ' + m.name + '…';
  let r;
  try {
    r = await api('movies?name=' + encodeURIComponent(m.name));
  } catch (e) {
    $('#movInfo').textContent = m.name + ' — ' + e.message;
    return;
  }
  if (!r.ok) { $('#movInfo').textContent = m.name + ' — ' + r.error; return; }
  const mins = r.seconds ? `${Math.floor(r.seconds / 60)}:${String(Math.round(r.seconds % 60)).padStart(2, '0')}` : '?';
  $('#movInfo').textContent =
    `${r.name} — ${r.width}x${r.height} · ${mins} · `
    + `${(r.bytes / 1048576).toFixed(1)} MB · ${r.packs} packs`
    + (r.mux_rate_bps ? ` · ${(r.mux_rate_bps / 1e6).toFixed(2)} Mbit/s` : '');
  $('#movStreams').textContent = 'streams — '
    + r.streams.map((s) => `${s.kind}: ${s.packets} packets, `
        + `${(s.bytes / 1048576).toFixed(1)} MB`).join('   ·   ');
  const url = '/api/movie_export?name=' + encodeURIComponent(m.name);
  const save = $('#movSave');
  save.href = url;
  save.download = m.name.replace(/\.PSS$/i, '') + '.mpg';
  save.style.display = '';
  // Try to play it. MPEG-2 in a browser is not a given, so the failure path
  // says what to do rather than leaving a black rectangle.
  p.src = url;
  p.style.display = '';
  $('#movHint').style.display = 'none';
  p.onerror = () => {
    p.style.display = 'none';
    const h = $('#movHint');
    h.style.display = '';
    h.textContent = 'Save the .mpg and open it in VLC.';
  };
}

// --------------------------------------------------------------- sound

// The 155 `.bd` banks, played as WAV. A bank is cut into sounds by its `.hd`,
// which states each one's offset AND the rate it plays at -- so the dropdown
// is only the fallback for a bank whose header will not parse (1 of the 155).
// It is NOT the END flag: that finds boundaries too, but counts each sound's
// trailing terminator block as a sound of its own, which is a different and
// longer list (snd_00: 30 against 58). Listing, playback and import all have
// to index the same way or ▶ plays the wrong clip -- they did not, and it did.
let sndBanks = [];
let sndBank = null;
// The banks organised: grouped by what their definition (snd_<n>.csv) says they are -- Game and system, Menus,
// Characters, Levels -- each by its title, and their sounds by the names the definitions give
// (dataeditor_more.sound_catalog / sound_names / sound_search).
let sndCatalog = null;
const sndOpen = new Set(['Game and system', 'Menus']);
const sndCase = {};                     // lower-case bank name -> its name in BANKS.DAT

async function initAudioTab() {
  if (sndBanks.length) return;
  try {
    const r = await api('sounds');
    sndBanks = r.banks || [];
    for (const n of sndBanks) sndCase[n.toLowerCase()] = n;
  } catch (e) {
    $('#sndBanks').innerHTML = '';
    $('#sndBanks').append(el('div', 'pad muted', 'Could not list banks: ' + e.message));
    return;
  }
  try { sndCatalog = await api('de_x_sound_catalog'); } catch (e) { sndCatalog = null; }
  $('#sndFilter').oninput = renderSndBanks;
  let findTimer = null;
  $('#sndFind').oninput = () => { clearTimeout(findTimer); findTimer = setTimeout(findSounds, 250); };
  $('#sndRate').onchange = () => { if (sndBank) loadBank(sndBank); };
  renderSndBanks();
}

function renderSndBanks() {
  const box = $('#sndBanks');
  const f = ($('#sndFilter').value || '').toLowerCase();
  box.innerHTML = '';
  if (!sndCatalog) {
    const shown = sndBanks.filter((n) => !f || n.toLowerCase().includes(f));
    if (!shown.length) return box.append(el('div', 'pad muted', 'No bank matches.'));
    for (const n of shown) {
      const row = el('div', 'row' + (n === sndBank ? ' sel' : ''));
      row.append(el('span', 'nm', n.replace(/\.bd$/i, '')));
      row.onclick = () => loadBank(n);
      box.append(row);
    }
    return;
  }
  let any = false;
  for (const g of sndCatalog.groups) {
    const banks = g.banks.filter((b) => !f || (b.title + ' ' + b.bd + ' ' + b.full).toLowerCase().includes(f));
    if (!banks.length) continue;
    any = true;
    const open = f || sndOpen.has(g.title);
    const head = el('div', 'row');
    head.style.cssText = 'font-weight:600;cursor:pointer';
    head.textContent = (open ? '▾ ' : '▸ ') + g.title + ' (' + banks.length + ')';
    head.onclick = () => { if (sndOpen.has(g.title)) sndOpen.delete(g.title); else sndOpen.add(g.title); renderSndBanks(); };
    box.append(head);
    if (!open) continue;
    for (const b of banks) {
      const n = sndCase[b.bd] || b.bd;
      const row = el('div', 'row' + (n === sndBank ? ' sel' : ''));
      row.style.paddingLeft = '18px';
      row.title = (b.full || b.title) + ' — ' + b.bd + (b.named ? ` (${b.named} named sounds)` : ' (no names in its definition)');
      const t = el('span', 'nm', b.title); t.style.flex = '1';
      row.append(t, el('span', 'muted', b.bd.replace(/\.bd$/i, '')));
      row.style.display = 'flex'; row.style.gap = '6px';
      row.onclick = () => loadBank(n);
      box.append(row);
    }
  }
  if (!any) box.append(el('div', 'pad muted', 'No bank matches.'));
}

// every named sound of every bank that has the words typed
async function findSounds() {
  const q = ($('#sndFind').value || '').trim();
  const list = $('#sndList');
  if (q.length < 2) { if (sndBank) loadBank(sndBank); else list.innerHTML = '<div class="empty">Pick a bank</div>'; return; }
  let r;
  try { r = await api('de_x_sound_search?q=' + encodeURIComponent(q)); } catch (e) { list.innerHTML = ''; return list.append(el('div', 'pad muted', 'Search failed: ' + e.message)); }
  $('#sndInfo').textContent = `${r.results.length}${r.results.length >= 400 ? '+' : ''} sound(s) named or described "${q}"`;
  list.innerHTML = '';
  if (!r.results.length) return list.append(el('div', 'pad muted', 'No named sound matches.'));
  const t = el('table');
  t.style.cssText = 'width:100%;font-size:12px';
  for (const s of r.results) {
    const bank = sndCase[s.bd] || s.bd;
    const url = `/api/sound_wav?name=${encodeURIComponent(bank)}&index=${s.index}`;
    const tr = el('tr');
    const play = el('button', '', '▶');
    play.style.cssText = 'font-size:11px;padding:1px 7px';
    play.onclick = () => { const p = $('#sndPlayer'); p.src = url; p.play().catch(() => {}); };
    const nm = el('td'); nm.append(el('b', null, s.name));
    if (s.note) nm.append(el('div', 'muted', s.note));
    const bk = el('a', 'nm', s.bank + ' · ' + s.bd.replace(/\.bd$/i, '') + ' #' + s.index);
    bk.style.cssText = 'font-size:11px;cursor:pointer';
    bk.title = 'Open this bank';
    bk.onclick = () => { $('#sndFind').value = ''; loadBank(bank); };
    const dl = el('a', 'nm', 'save'); dl.href = url; dl.download = s.name + '.wav'; dl.style.cssText = 'font-size:11px;cursor:pointer';
    const rep = el('a', 'nm', 'replace'); rep.style.cssText = 'font-size:11px;cursor:pointer';
    rep.onclick = () => replaceSound(bank, { index: s.index });
    const td = (x) => { const d = el('td'); d.append(x); return d; };
    tr.append(td(play), nm, td(bk), td(dl), td(rep));
    t.append(tr);
  }
  list.append(t);
}

async function loadBank(name) {
  sndBank = name;
  renderSndBanks();
  const list = $('#sndList');
  list.innerHTML = '<div class="pad muted">Reading…</div>';
  let r;
  try {
    r = await api('sounds?name=' + encodeURIComponent(name));
  } catch (e) {
    list.innerHTML = '';
    return list.append(el('div', 'pad muted', 'Failed: ' + e.message));
  }
  if (!r.ok) {
    list.innerHTML = '';
    $('#sndInfo').textContent = name;
    return list.append(el('div', 'pad muted', r.error || 'Not decodable.'));
  }
  const fallbackRate = +$('#sndRate').value || 22050;
  let names = {}, title = '';
  try { const nr = await api('de_x_sound_names?bd=' + encodeURIComponent(name)); names = nr.names || {}; title = nr.title ? nr.title + ' — ' : ''; } catch (e) { /* unnamed */ }
  $('#sndInfo').textContent = title +
    `${name} — ${r.sounds.length} sounds · ${(r.bytes / 1024).toFixed(0)} KB · `
    + `ADPCM gate ${r.gate.toFixed(3)}`
    + (r.rates_from === 'hd' ? ' · rates from the .hd'
                             : ' · no .hd, rate is a guess');
  list.innerHTML = '';
  const t = el('table');
  t.style.cssText = 'width:100%;font-size:12px';
  for (const s of r.sounds) {
    // The bank's OWN rate when the .hd gave us one; the dropdown is only a
    // fallback for banks with no header.
    const rate = s.rate || fallbackRate;
    const secs = s.samples / rate;
    const url = `/api/sound_wav?name=${encodeURIComponent(name)}`
              + `&index=${s.index}&rate=${rate}`;
    const tr = el('tr');
    const play = el('button', '', '▶');
    play.style.cssText = 'font-size:11px;padding:1px 7px';
    play.onclick = () => {
      const p = $('#sndPlayer');
      p.src = url;
      p.play().catch(() => {});
    };
    const dl = el('a', 'nm', 'save');
    dl.href = url;
    dl.download = `${name.replace(/\.bd$/i, '')}_${String(s.index).padStart(2, '0')}.wav`;
    dl.style.cssText = 'font-size:11px;cursor:pointer';
    const rep = el('a', 'nm', 'replace');
    rep.style.cssText = 'font-size:11px;cursor:pointer';
    rep.onclick = () => replaceSound(name, s);
    const td = (x, cls) => { const d = el('td', cls || ''); d.append(x); return d; };
    // the names the bank's definition gives this sound (several rows can play one sound)
    const nm = el('td');
    const these = names[String(s.index)] || [];
    if (these.length) {
      nm.append(el('b', null, these[0].name));
      if (these.length > 1) nm.append(el('span', 'muted', ' (also ' + these.slice(1).map((x) => x.name).join(', ') + ')'));
      const note = these.map((x) => x.note).find((x) => x);
      if (note) nm.append(el('div', 'muted', note));
      dl.download = these[0].name + '.wav';
    } else nm.append(el('span', 'muted', '(no name in its definition)'));
    tr.append(td(play), td(document.createTextNode('#' + s.index), 'muted'), nm,
              td(document.createTextNode(secs.toFixed(2) + ' s'), 'muted'),
              td(document.createTextNode((s.rate || '?') + ' Hz'), 'muted'),
              td(dl), td(rep));
    t.append(tr);
  }
  list.append(t);
}

// Replace one sound with a WAV. Dry run first, like every other importer --
// a bank is patched in place and the .hd holds absolute offsets into it, so
// the re-encoded sound must fit the slot the original occupied.
function replaceSound(bank, s) {
  const inp = el('input');
  inp.type = 'file';
  inp.accept = '.wav,audio/wav';
  inp.onchange = async () => {
    const f = inp.files && inp.files[0];
    if (!f) return;
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = '';
    for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
    const wav_b64 = btoa(bin);
    const body = { name: bank, index: s.index, wav_b64 };
    let dry;
    try {
      dry = await post('sound_import', { ...body, dry: true });
    } catch (e) {
      return toast('Refused: ' + e.message, 'bad');
    }
    if (!dry.ok) return toast(dry.error || 'Will not fit.', 'bad');
    if (!confirm(
        `Replace sound #${s.index} in ${bank}?\n\n`
        + `${f.name} encodes to ${dry.encoded_bytes} bytes of the `
        + `${dry.slot_bytes}-byte slot (${dry.spare} spare).\n`
        + `It will play at ${dry.plays_at || '?'} Hz — the bank's own rate for `
        + `this sound, not the WAV's ${dry.wav_rate} Hz.\n\nCtrl+Z undoes it.`))
      return;
    try {
      const r = await post('sound_import', body);
      if (!r.ok) return toast(r.error || 'Refused', 'bad');
      toast(`Replaced sound #${s.index} — ${r.encoded_bytes} of `
            + `${r.slot_bytes} bytes. Ctrl+Z to undo.`, 'good');
      refreshHistory();
      loadBank(bank);
    } catch (e) { toast('Failed: ' + e.message, 'bad'); }
  };
  inp.click();
}

// ------------------------------------------------------------- roadmap

// Just enough Markdown for the status document: headings, tables, lists, rules,
// code spans, bold, links and strikethrough. Everything is escaped first, so a
// stray angle bracket in the notes cannot inject markup.
function mdToHtml(src) {
  const esc = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;')
                      .replace(/>/g, '&gt;');
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/~~([^~]+)~~/g, '<s>$1</s>')
    .replace(/\*([^*]+)\*/g, '<i>$1</i>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<span class="nm">$1</span>');
  const out = [];
  const lines = src.split('\n');
  let list = null, table = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const closeTable = () => {
    if (table) { out.push('</tbody></table>'); table = null; }
  };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    const t = l.trim();
    if (!t) { closeList(); closeTable(); continue; }
    // table row
    if (/^\|.*\|$/.test(t)) {
      const cells = t.slice(1, -1).split('|').map((c) => c.trim());
      if (/^[\s:|-]+$/.test(t)) continue;             // the --- separator row
      if (!table) {
        table = true;
        out.push('<table class="rm"><tbody>');
        out.push('<tr>' + cells.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr>');
        continue;
      }
      out.push('<tr>' + cells.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>');
      continue;
    }
    closeTable();
    let m;
    if ((m = t.match(/^(#{1,4})\s+(.*)$/))) {
      closeList();
      const n = m[1].length;
      out.push(`<h${n} class="rm${n}">${inline(m[2])}</h${n}>`);
      continue;
    }
    if (/^---+$/.test(t)) { closeList(); out.push('<hr>'); continue; }
    if ((m = t.match(/^[-*]\s+(.*)$/))) {
      if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
      out.push(`<li>${inline(m[1])}</li>`);
      continue;
    }
    if ((m = t.match(/^(\d+)[.)]\s+(.*)$/))) {
      if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
      out.push(`<li>${inline(m[2])}</li>`);
      continue;
    }
    if (list) { out.push(`<li>${inline(t)}</li>`); continue; }  // continuation
    out.push(`<p>${inline(t)}</p>`);
  }
  closeList(); closeTable();
  return out.join('\n');
}

// ------------------------------------------------------------ undo / redo

// The long job, named in the status bar while it runs (DESIGN-REVIEW B1).
// /api/job is lock-free, so this answers while the job holds the lock.
function watchJob() {
  const span = $('#sbJob'), btn = $('#sbJobCancel');
  if (!span || watchJob.on) return;
  watchJob.on = true;
  btn.onclick = () => { post('job_cancel', {}).catch(() => {}); };
  setInterval(async () => {
    let j = null;
    try { j = await api('job'); } catch { /* old server, or offline */ }
    const on = !!(j && j.running);
    span.style.display = on ? '' : 'none';
    btn.style.display = on && !j.cancelling ? '' : 'none';
    if (on) span.textContent = `${j.label} — ${Math.round(j.seconds)} s`
      + (j.cancelling ? ' (cancelling)' : '');
  }, 2000);
}

let sbTouched = 0;
function paintDiscCount() {
  const ed = $('#sbEdits');
  if (!ed) return;
  const d = window.__onDisc;
  if (!d || typeof d.files_changed !== 'number') {
    ed.textContent = '';
    return;
  }
  const n = d.files_changed;
  ed.textContent = `${n} file${n === 1 ? '' : 's'} differ${n === 1 ? 's' : ''} from stock`;
  ed.className = 'sb' + (n ? ' warn' : ' dim');
  ed.title = `${sbTouched} file${sbTouched === 1 ? '' : 's'} written this session `
    + '(including writes later reverted). See Changes for diffs.';
}
window.addEventListener('rdr:disc-state', paintDiscCount);

async function refreshHistory() {
  try {
    const h = await api('history');
    $('#btnUndo').disabled = !h.can_undo;
    $('#btnRedo').disabled = !h.can_redo;
    // "Have I changed anything?" is the question you ask most often and it
    // had no persistent answer anywhere in the UI.
    //
    // Count TOUCHED FILES, not history rows. `history()` returns
    // `entries[-200:]` collapsed by group, so its length saturates at 200 and
    // would read "200 edits" forever — the same windowing trap that has
    // already bitten this project once.
    // And say TOUCHED, not CHANGED, because they are not the same number and
    // the difference is large. After a test run the journal had touched 650
    // files while only 64 still differed from vanilla -- everything else had
    // been written and then reverted. "650 files changed" was simply false.
    // DESIGN-REVIEW B3: the header pill said "matches stock" while this said
    // "181 files touched". One number now, the header's own: files that differ
    // from stock, measured by /api/versions (nav.js publishes it as
    // window.__onDisc). The journal's touched count moves to the tooltip.
    sbTouched = h.touched?.length || 0;
    paintDiscCount();
    // SAY WHAT IT WILL UNDO, BEFORE IT DOES IT.
    //
    // The button said "Undo" and nothing else, so the only way to find out
    // what it was about to throw away was to press it and read the toast
    // afterwards. Both recorded work-loss incidents in this project start
    // there. The name of the target now sits on the button.
    const newest = h.entries.slice().reverse().find((e) => !e.undone);
    const ub = $('#btnUndo');
    if (ub) {
      const lab = newest && newest.label ? newest.label : '';
      const short = lab.length > 22 ? lab.slice(0, 21) + '…' : lab;
      // The target is named in the tooltip, not on the button (B3): a
      // button reading "Undo: revert mainoptions.sw…" next to a pill
      // reading "matches stock" looked like two answers to one question.
      ub.textContent = 'Undo';
      ub.title = lab
        ? `Undo “${lab}”`
          + (newest.count > 1 ? ` — ${newest.count} files` : '') + '   (Ctrl+Z)'
        : 'Nothing to undo   (Ctrl+Z)';
    }
    const box = $('#historyList');
    if (box) {
      box.innerHTML = '';
      const rev = h.entries.slice().reverse();
      let steps = 0;
      for (const e of rev) {
        if (!e.undone) steps++;              // how far back this row is
        const row = el('div', 'row');
        row.style.opacity = e.undone ? '0.4' : '1';
        const suffix = e.count > 1 ? `${e.count} files` : e.file;
        row.append(el('span', 'nm', (e.undone ? '(undone) ' : '') + (e.label || 'edit')),
                   el('span', 'tag', suffix));
        // A history row you cannot act on is a receipt. This one reverts,
        // by name, through the same undo stack Ctrl+Z uses -- so it can be
        // redone the same way.
        if (!e.undone) {
          const n = steps;
          row.title = n === 1
            ? `Undo “${e.label || 'this edit'}”`
            : `Undo back to and including “${e.label || 'this edit'}” — `
              + `${n} changes, newest first`;
          row.onclick = () => undoBackTo(n, e.label || 'this edit');
        } else {
          row.style.cursor = 'default';
        }
        box.append(row);
      }
      if (!h.entries.length) box.append(el('div', 'pad muted', 'nothing edited yet'));
    }
    // The measured answer when nav.js has one (it diffs the disc whenever
    // the journal moves); the history count otherwise, worded as what it
    // is. A reverted file is in the history and not in the mod.
    const t = $('#modTouched');
    const d = window.__onDisc;
    if (t) t.textContent = d
      ? (d.files_changed
        ? `${d.files_changed} file(s) differ from stock.`
        : 'The working disc matches stock. Nothing to save yet.')
      : h.touched.length
        ? `${h.touched.length} file(s) edited this session.`
        : 'No changes yet.';
  } catch {}
}

// Step back through the undo stack by name. Nothing new happens here that
// pressing Ctrl+Z n times would not do -- it is the SAME endpoint, n times,
// in the same order -- but it says how many and which before it starts, and
// it stops the moment a step fails rather than carrying on regardless.
async function undoBackTo(steps, label) {
  if (!steps || steps < 1) return;
  const msg = steps === 1
    ? `Undo “${label}”?`
    : `Undo ${steps} changes, back to and including “${label}”?\n\n`
      + 'They are undone newest first, exactly as Ctrl+Z would. '
      + 'Redo puts them back.';
  if (!confirm(msg)) return;
  let done = 0, touchedLevel = false, failed = null;
  try {
    for (let i = 0; i < steps; i++) {
      const r = await post('undo', {});
      if (!r.ok) break;
      done++;
      if (level && level.archive === r.archive) touchedLevel = true;
    }
  } catch (e) { failed = e.message; }
  // One reload at the end, not one per step: reloading a level costs seconds.
  if (touchedLevel) await reloadLevel(true, true);
  refreshHistory();
  announceHistory();
  if (failed) toast(`Undid ${done} of ${steps} — then: ${failed}`, 'bad');
  else toast(done === 1 ? `Undid: ${label}`
                        : `Undid ${done} changes, back to “${label}”`, 'good');
}

// Undo changes files, so any panel showing "what does this file say now" is
// stale the moment this returns. `refreshHistory` only ever repainted the
// history list itself. Measured: swap ncb to mrd, press Ctrl+Z, and the model
// panel still read "Now: mrd" while the server already answered `ncb` --
// the same staleness the swap panel's own cache split was written to prevent,
// arriving through the other door. An event, so a module that is not loaded
// costs nothing and no panel has to be named here.
function announceHistory() {
  window.dispatchEvent(new CustomEvent('rdr:history'));
}

async function doUndo() {
  try {
    const r = await post('undo', {});
    if (!r.ok) return toast('Nothing to undo');
    toast(`Undid: ${r.label}` + (r.count > 1 ? ` (${r.count} files)` : ''), 'good');
    if (level && level.archive === r.archive) await reloadLevel(true, true);
    refreshHistory();
    announceHistory();
  } catch (e) { toast('Undo failed: ' + e.message, 'bad'); }
}

async function doRedo() {
  try {
    const r = await post('redo', {});
    if (!r.ok) return toast('Nothing to redo');
    toast(`Redid: ${r.label}` + (r.count > 1 ? ` (${r.count} files)` : ''), 'good');
    if (level && level.archive === r.archive) await reloadLevel(true, true);
    refreshHistory();
    announceHistory();
  } catch (e) { toast('Redo failed: ' + e.message, 'bad'); }
}

// ---------------------------------------------------------------- assets
//
// The catalogue separates the asset (a model, owned by a map's bundle) from
// the path (one placement of it, a block in a .znes file). Browsing by asset
// is how you find "every lamp on the disc" rather than "what this map has".

// Four axes, because four different things are true of every asset and one
// "category" list mixed them up. A name's `alpha_01_` or `crack02` prefix
// says how a thing is DRAWN or which damage stage it is, never what it is,
// so those became their own filter instead of swallowing the catalogue.
const ASSET_AXES = [
  { key: 'category', title: 'What it is', pick: 'one',
    note: 'Read from the last word in the name: cottageStair is a stair,'
      + ' not a cottage.',
    labels: {
      surface: 'Walls, floors and roofs',
      frame: 'Posts, beams and railings',
      opening: 'Doors, windows and gates',
      access: 'Stairs, porches and bridges',
      space: 'Rooms and interiors',
      building: 'Whole buildings',
      terrain: 'Ground and rock', vegetation: 'Plants and trees',
      furniture: 'Furniture and fittings', prop: 'Loose props',
      vehicle: 'Wagons and rail', character: 'People and animals',
      weapon: 'Weapons', light: 'Lights and fire', sign: 'Signs and flags',
      effect: 'Effects', sky: 'Sky', blocker: 'Invisible blockers',
      unclassified: 'Not classified',
    } },
  { key: 'form', title: 'How it is built', pick: 'one',
    note: 'From the chunk each placement points at. Exact, not a guess.',
    labels: {
      visual: 'Drawn only — you walk through it',
      solid: 'Drawn and solid',
      collision: 'Collision only — invisible',
    } },
  { key: 'reach', title: 'How many maps have it', pick: 'one',
    note: 'A map and its 2- and 4-player twins count once.',
    labels: {
      unique: 'One map only', shared: 'Two or three maps',
      everywhere: 'Four maps or more',
    } },
  { key: 'size', title: 'How big', pick: 'one',
    note: 'Longest edge of the collision box. Most assets have none.',
    labels: {
      tiny: 'Tiny — under 1 unit', small: 'Small — 1 to 5',
      medium: 'Medium — 5 to 20', large: 'Large — 20 to 80',
      huge: 'Huge — over 80', unknown: 'No collision box, so no size',
    } },
  { key: 'flag', title: 'Naming flags', pick: 'many',
    note: 'Bolted onto the name by the artists. These stack.',
    labels: {
      damaged: 'Damage stage (crack01, 02, 03)',
      seethrough: 'See-through (alpha_01)',
      shadowvar: 'Shadow variant (S and NS pair)',
      collshell: 'Collision shell (SB)',
      extrapass: 'Second draw of the same surface (2p)',
      animated: 'Animated (_anim)',
      tile: 'Piece of a train or coach route',
      dynalite: 'Dynamically lit',
    } },
];

let assetFilter = { category: '', form: '', reach: '', size: '' };
let assetFlags = [];      // naming flags; an asset must carry all of them
let assetRows = [];       // last search result
let assetPick = null;     // asset shown in the detail pane

async function loadAssets() {
  if (assetRows.length) return;
  await refreshAssetSummary();
  await runAssetSearch();
}

// One query string for the list and for the counts beside it, so the two can
// never disagree. A facet reading "412" that opens on an empty table is worse
// than no count at all.
function assetQuery() {
  const q = $('#assetSearch').value.trim();
  let s = '';
  if (q) s += '&q=' + encodeURIComponent(q);
  for (const key of ['category', 'form', 'reach', 'size']) {
    if (assetFilter[key]) {
      s += '&' + key + '=' + encodeURIComponent(assetFilter[key]);
    }
  }
  if (assetFlags.length) {
    s += '&flag=' + encodeURIComponent(assetFlags.join(','));
  }
  if ($('#assetShared').checked) s += '&shared=1';
  return s;
}

async function refreshAssetSummary() {
  let s;
  try { s = await api('assets_summary?limit=0' + assetQuery()); } catch (e) {
    return toast('Asset catalogue failed: ' + e.message, 'bad');
  }
  // Every number below except `total` and `areas` counts only what the
  // filters left, so under a filter the block has to say so. It used to read
  // "4,961 assets on the disc" beside a placement count for the eleven that
  // matched, and divide the filtered unclassified count by the whole disc.
  const filtered = s.assets !== s.total;
  const unc = s.facets.category.unclassified || 0;
  const pct = (100 * unc / (s.assets || 1)).toFixed(1);
  const sum = $('#assetSummary');
  sum.innerHTML = '';
  const wrap = el('div', 'muted');
  wrap.style.cssText = 'font-size:11px;line-height:1.6';
  const lines = [
    [s.assets.toLocaleString() + ' assets' + (filtered ? ' match' : ''),
      filtered ? ' — out of ' + s.total.toLocaleString() + ' on the disc.'
        : ' on the disc.'],
    [s.instances.toLocaleString() + ' placements',
      filtered ? ' of them.' : ' across ' + s.areas + ' areas.'],
    [s.shared_maps.toLocaleString() + ' are in more than one map',
      ' importable.'],
    [unc.toLocaleString() + ' (' + pct + '%) unclassified',
      ' by name.'],
  ];
  for (const [head, tail] of lines) {
    const p = el('div');
    p.style.marginBottom = '5px';
    p.append(el('b', 'good', head), document.createTextNode(tail));
    wrap.append(p);
  }
  sum.append(wrap);
  renderAssetFacets(s.facets);
  renderAssetChips();
}

function renderAssetFacets(facets) {
  const box = $('#assetFacets');
  box.innerHTML = '';
  for (const axis of ASSET_AXES) {
    const counts = facets[axis.key] || {};
    box.append(el('h2', '', axis.title));
    const note = el('div', 'muted', axis.note);
    note.style.cssText = 'font-size:11px;padding:0 10px 5px;line-height:1.4';
    box.append(note);
    if (axis.pick === 'one') {
      const total = Object.keys(counts).reduce((a, k) => a + counts[k], 0);
      box.append(assetFacetRow(axis, '', 'Any', total));
    }
    const keys = Object.keys(axis.labels)
      .filter((k) => counts[k])
      .sort((a, b) => counts[b] - counts[a]);
    for (const k of keys) {
      box.append(assetFacetRow(axis, k, axis.labels[k], counts[k]));
    }
  }
}

function assetFacetRow(axis, key, label, n) {
  const on = axis.pick === 'many'
    ? assetFlags.indexOf(key) >= 0
    : assetFilter[axis.key] === key;
  const row = el('div', 'row' + (on ? ' sel' : ''));
  row.append(el('span', 'nm', label), el('span', 'tag', n.toLocaleString()));
  row.onclick = () => {
    if (axis.pick === 'many') {
      const i = assetFlags.indexOf(key);
      if (i >= 0) assetFlags.splice(i, 1); else assetFlags.push(key);
    } else {
      assetFilter[axis.key] = assetFilter[axis.key] === key ? '' : key;
    }
    refreshAssetSummary();
    runAssetSearch();
  };
  return row;
}

// Every filter that is on, in one line, each one removable. Without it the
// left panel scrolls out of view and there is no way to tell why the table
// went nearly empty.
function renderAssetChips() {
  const bar = $('#assetChips');
  if (!bar) return;
  bar.innerHTML = '';
  const active = [];
  for (const axis of ASSET_AXES) {
    if (axis.pick === 'many') {
      for (const f of assetFlags) active.push([axis, f]);
    } else if (assetFilter[axis.key]) {
      active.push([axis, assetFilter[axis.key]]);
    }
  }
  bar.style.display = active.length ? 'flex' : 'none';
  if (!active.length) return;
  for (const [axis, key] of active) {
    const chip = el('button', '', (axis.labels[key] || key) + '  ×');
    chip.style.cssText = 'font-size:11px;padding:2px 7px';
    chip.title = axis.title;
    chip.onclick = () => {
      if (axis.pick === 'many') {
        assetFlags = assetFlags.filter((f) => f !== key);
      } else {
        assetFilter[axis.key] = '';
      }
      refreshAssetSummary();
      runAssetSearch();
    };
    bar.append(chip);
  }
  const clear = el('button', '', 'Clear all');
  clear.style.cssText = 'font-size:11px;padding:2px 7px';
  clear.onclick = () => {
    assetFilter = { category: '', form: '', reach: '', size: '' };
    assetFlags = [];
    refreshAssetSummary();
    runAssetSearch();
  };
  bar.append(clear);
}

function assetLabelFor(axisKey, value) {
  for (const axis of ASSET_AXES) {
    if (axis.key === axisKey) return axis.labels[value] || value || '—';
  }
  return value;
}

async function runAssetSearch() {
  let r;
  try {
    r = await api('assets?limit=500' + assetQuery());
  } catch (e) { return toast('Search failed: ' + e.message, 'bad'); }
  assetRows = r.assets;
  $('#assetCount').textContent =
    `${r.total.toLocaleString()} match${r.total === 1 ? '' : 'es'}`
    + (r.total > r.assets.length ? ` — showing ${r.assets.length}` : '');
  renderAssetList();
}

// Short badges for the flags, so a row reads at a glance without a legend.
const ASSET_FLAG_TAG = {
  damaged: 'dmg', seethrough: 'alpha', shadowvar: 'shadow', collshell: 'shell',
  extrapass: '2p', animated: 'anim', tile: 'tile', dynalite: 'lit',
};

function renderAssetList() {
  const box = $('#assetList');
  box.innerHTML = '';
  const t = el('table');
  t.innerHTML = '<thead><tr><th>Asset</th><th>What it is</th>'
    + '<th>Built</th><th>Flags</th>'
    + '<th class="num">Maps</th><th class="num">Placed</th>'
    + '<th>Found in</th></tr></thead>';
  const body = el('tbody');
  for (const a of assetRows) {
    const tr = el('tr');
    if (assetPick === a.name) tr.className = 'sel';
    const where = a.places.slice(0, 3)
      .map((p) => p.area_name || p.archive.split('/').pop().replace('.DAT', ''))
      .join(', ');

    tr.append(el('td', '', a.name));

    // The reason is on the cell, not hidden in a doc: a category you cannot
    // question is a category you cannot trust.
    const cat = el('td', a.category === 'unclassified' ? 'muted' : '',
                   assetLabelFor('category', a.category));
    cat.title = a.why ? 'Decided by ' + a.why : '';
    tr.append(cat);

    const form = el('td', 'muted', assetLabelFor('form', a.form).split(' —')[0]);
    form.title = assetLabelFor('form', a.form)
      + (a.size ? ' · ' + assetLabelFor('size', a.size) : '')
      + (a.extent ? ' · box ' + a.extent + ' units' : '');
    tr.append(form);

    tr.append(el('td', 'muted',
                 (a.flags || []).map((f) => ASSET_FLAG_TAG[f] || f).join(' ')));

    const maps = el('td', 'num' + (a.maps > 1 ? ' good' : ''), String(a.maps));
    maps.title = a.maps === a.areas ? ''
      : a.areas + ' areas, but only ' + a.maps
        + ' separate maps — the rest are 2- and 4-player copies.';
    tr.append(maps);

    tr.append(el('td', 'num', String(a.instances)));
    tr.append(el('td', 'muted', where + (a.places.length > 3 ? ' …' : '')));
    tr.onclick = () => { assetPick = a.name; renderAssetList(); showAsset(a); };
    body.append(tr);
  }
  t.append(body);
  box.append(t);
}

// The asset library used to be a list of names and counts. Showing the model
// is the whole point of a library -- `crate01` and `crate02` are not the same
// crate, and you cannot tell from the name.
let assetViewer = null;

function assetTexUrl(archive, area) {
  return (i) => `/api/texture.png?archive=${encodeURIComponent(archive)}`
    + (area ? `&area=${encodeURIComponent(area)}` : '') + `&i=${i}`;
}

async function previewAsset(a) {
  const host = $('#assetView');
  const info = $('#assetViewInfo');
  if (!host) return;
  let note = host.querySelector('.vnote');
  if (!note) { note = el('div', 'vnote'); host.append(note); }
  let tex = host.querySelector('.vtex');
  if (!tex) { tex = el('div', 'vtex'); host.append(tex); }

  // Prefer a placement in a level whose bundle we can actually open; the
  // catalogue records the chunk index per placement.
  const p = a.places?.[0];
  if (!p || p.chunk == null) {
    assetViewer?.clear();
    note.textContent = 'No model chunk recorded for this asset.';
    tex.textContent = '';
    info.textContent = a.name;
    return;
  }
  note.textContent = 'Decoding…';
  tex.textContent = '';
  try {
    const d = await api(`model?archive=${encodeURIComponent(p.archive)}`
      + (p.area ? `&area=${encodeURIComponent(p.area)}` : '')
      + `&chunk=${p.chunk}`);
    if (!assetViewer) assetViewer = makeViewer(host);
    assetViewer.setSpin($('#assetSpin')?.checked !== false);
    assetViewer.show(d, assetTexUrl(p.archive, p.area));
    note.textContent = '';
    info.textContent = `${d.verts.toLocaleString()} verts · `
      + `${d.tris.toLocaleString()} tris · ${d.parts} part`
      + (d.parts === 1 ? '' : 's');
    tex.textContent = d.textures.length
      ? d.textures.map((t) => t.name).join(' · ') : 'untextured';
  } catch (e) {
    assetViewer?.clear();
    note.textContent = 'Could not decode: ' + e.message;
    info.textContent = a.name;
  }
}

function showAsset(a) {
  previewAsset(a);
  const box = $('#assetDetail');
  const byArchive = new Map();
  for (const p of a.places) {
    const k = p.archive;
    if (!byArchive.has(k)) byArchive.set(k, []);
    byArchive.get(k).push(p);
  }
  box.innerHTML = '';
  const h = el('h3', '', a.name);
  h.style.cssText = 'margin:0 0 2px;font-size:13px;color:var(--gold)';
  box.append(h);

  // Spell out how this asset was filed, including the rule that decided it.
  // Anyone who disagrees can then go and argue with the rule.
  const facts = [
    assetLabelFor('category', a.category)
      + (a.why ? ' (' + a.why + ')' : ''),
    assetLabelFor('form', a.form),
    a.size ? assetLabelFor('size', a.size)
      + (a.extent ? ', longest edge ' + a.extent : '') : null,
    'in ' + a.maps + ' map' + (a.maps === 1 ? '' : 's')
      + (a.areas !== a.maps ? ' (' + a.areas + ' areas, counting'
        + ' 2- and 4-player copies)' : ''),
    a.instances + ' placement' + (a.instances === 1 ? '' : 's'),
  ].filter(Boolean);
  for (const f of (a.flags || [])) facts.push(assetLabelFor('flag', f));
  const p = el('p', 'muted', facts.join(' · '));
  p.style.cssText = 'margin:0 0 10px;font-size:11px;line-height:1.5';
  box.append(p);

  for (const [archive, places] of byArchive) {
    const card = el('div');
    card.style.cssText = 'border:1px solid var(--line);border-radius:4px;'
      + 'padding:7px 9px;margin-bottom:7px';
    const short = archive.split('/').pop().replace('.DAT', '');
    card.append(el('div', '', short));
    card.firstChild.style.cssText = 'font-size:12px;margin-bottom:4px';
    for (const p of places) {
      const row = el('div');
      row.style.cssText = 'display:flex;align-items:center;gap:6px;'
        + 'font-size:11px;color:var(--dim-ink);padding:2px 0';
      row.append(el('span', '', p.area_name || 'main'));
      row.firstChild.style.flex = '1';
      const open = el('button', '', 'Open');
      open.style.cssText = 'font-size:11px;padding:2px 7px';
      open.onclick = async () => {
        showTab('editor');
        toast('Loading ' + short + '…');
        await loadLevelByRef(archive, null, p.area);
      };
      row.append(open);
      if (level && level.archive === archive
          && (!p.area || p.area === level.area)) {
        const put = el('button', 'primary', 'Place here');
        put.style.cssText = 'font-size:11px;padding:2px 7px';
        put.onclick = () => placeAsset(a.name);
        row.append(put);
      }
      card.append(row);
    }
    box.append(card);
  }
}

// The dock in the editor: assets filtered to what THIS area can actually
// take. Anything else would need a file added to the archive, which is the
// one thing the format does not allow.
function fillAssetDockCats() {
  const sel = $('#assetDockCat');
  if (!sel || sel.options.length) return;
  const any = el('option', '', 'Any kind of asset');
  any.value = '';
  sel.append(any);
  for (const axis of ASSET_AXES) {
    if (axis.key !== 'category') continue;
    for (const k of Object.keys(axis.labels)) {
      const o = el('option', '', axis.labels[k]);
      o.value = k;
      sel.append(o);
    }
  }
  sel.onchange = renderAssetDock;
}

async function renderAssetDock() {
  const box = $('#assetDock');
  if (!box) return;
  if (!level) { box.innerHTML = ''; return; }
  fillAssetDockCats();
  const q = $('#assetDockFilter').value.trim();
  const cat = $('#assetDockCat') ? $('#assetDockCat').value : '';
  const here = $('#assetDockHere').checked;
  box.innerHTML = '<p class="muted" style="padding:8px">Loading…</p>';
  let r;
  try {
    r = await api('assets?limit=300'
      + (q ? '&q=' + encodeURIComponent(q) : '')
      + (cat ? '&category=' + encodeURIComponent(cat) : '')
      + (here ? '&archive=' + encodeURIComponent(level.archive)
              + (level.area ? '&area=' + encodeURIComponent(level.area) : '')
              : ''));
  } catch (e) { box.innerHTML = ''; return; }

  box.innerHTML = '';
  if (!r.assets.length) {
    box.innerHTML = '<p class="muted" style="padding:8px;font-size:11px">'
      + 'Nothing matches. Untick the filter to search the whole disc.</p>';
    return;
  }
  for (const a of r.assets) {
    const row = el('div', 'row');
    row.append(el('span', 'nm', a.name),
               el('span', 'tag', here ? assetLabelFor('category', a.category)
                                      : a.maps + ' maps'));
    // A browse click never writes. This row used to POST place_asset the
    // moment it was clicked -- the only list row in the product whose click
    // wrote to the disc -- while its own tooltip said "click to check".
    const putBtn = el('button', '', 'Place');
    putBtn.style.cssText = 'font-size:11px;padding:1px 7px;flex:none';
    putBtn.title = `Put a ${a.name} at the centre of the view`;
    putBtn.onclick = (ev) => { ev.stopPropagation(); placeAsset(a.name); };
    row.append(putBtn);
    row.title = a.name + (here ? '' : ` — in ${a.maps} map(s)`);
    row.onclick = () => placeAsset(a.name, true);
    box.append(row);
  }
}

// Place an asset at the middle of the view. With dryOnly, only report
// whether it would fit -- the browse click's job.
async function placeAsset(name, dryOnly) {
  if (!level) return toast('Load a level first.', 'bad');
  const target = controls.target;
  const pos = [ +target.x.toFixed(3), +target.y.toFixed(3), +target.z.toFixed(3) ];
  try {
    if (dryOnly) {
      const dry = await post('place_asset', {
        archive: level.archive, area: level.area, name, pos, dry: true,
      });
      const rep = Object.values(dry.report || {})[0];
      return toast(rep && rep.fits === false
        ? `${name} does not fit here (${rep.stored} > ${rep.slot} bytes).`
        : `${name} fits — use its Place button to put one down.`);
    }
    const res = await post('place_asset', {
      archive: level.archive, area: level.area, name, pos,
    });
    if (!res.applied) return toast('Could not place ' + name, 'bad');
    toast(`Placed ${res.new_name} in ${res.target_file} at `
          + pos.map((v) => v.toFixed(1)).join(', '), 'good');
    await reloadLevel(true);
    refreshHistory();
  } catch (e) {
    toast(e.message, 'bad');
  }
}

// -------------------------------------------------------------- campaign

let campaign = null;
let missionSel = null;

// Column indices in levelflow.csv, for surgical single-cell writes.
const MISSION_FIELDS = [
  ['Character', 2, 'who you play as (3-letter creature id)'],
  ['Weapons', 3, 'starting weapons, semicolon separated'],
  ['Reward $', 4, 'cash paid for completing it'],
  ['Max Time', 5, 'seconds allowed for the time challenge'],
  ['Max Damage', 6, 'hp you may lose and still pass'],
  ['Min Accuracy', 7, 'accuracy % needed'],
  ['Min Combo', 8, 'combo total needed'],
  ['Normal', 9, 'reward for finishing'],
  ['Excellent', 10, 'reward for meeting the challenges'],
  ['Hidden', 11, 'secret reward — this is where story-mode cheats live'],
];

async function loadCampaign() {
  const box = $('#missionList');
  try {
    campaign = await api('campaign');
  } catch (e) {
    // Left silent, this tab was four empty boxes with no sentence anywhere.
    box.innerHTML = '';
    box.append(el('div', 'pad muted',
      'Could not read the campaign tables: ' + e.message));
    return;
  }
  box.innerHTML = '';
  campaign.missions.forEach((m, i) => {
    const row = el('div', 'row');
    row.append(el('span', 'nm', `${String(i + 1).padStart(2, '0')}  ${m.Level}`),
               el('span', 'tag', m.Character || ''));
    // lvl_tomb appears five times in levelflow.csv and only the variation
    // number tells the copies apart, so those rows carry it visibly.
    if (parseInt(m.Variation, 10) > 1)
      row.append(el('span', 'tag', 'variation ' + m.Variation));
    row.onclick = () => openMission(i);
    box.append(row);
  });

  const sel = $('#unlockType');
  if (!sel.options.length) {
    for (const t of ['(all)', ...campaign.types]) {
      const o = el('option', null, t);
      o.value = t === '(all)' ? '' : t;
      sel.append(o);
    }
  }
  renderStages();
  renderUnlockables();
  renderItems();
}

// invmasterlist.csv column indices, for surgical single-cell writes.
const ITEM_COLS = { shop: 3, repair: 4, upgrade: 5 };

function renderItems() {
  if (!campaign) return;
  const filter = $('#itemFilter').value.toLowerCase();
  const rows = campaign.items.filter((i) => !filter
    || i.name.toLowerCase().includes(filter)
    || (i.type || '').toLowerCase().includes(filter));
  const tb = $('#itemTable tbody');
  tb.innerHTML = '';
  for (const it of rows.slice(0, 400)) {
    const tr = el('tr');
    tr.append(el('td', null, it.name), el('td', 'muted', it.type));
    for (const key of ['shop', 'repair', 'upgrade']) {
      const td = el('td', 'num');
      const inp = el('input');
      inp.value = it[key] ?? '';
      inp.style.cssText = 'width:70px;text-align:right;font-size:12px';
      inp.onchange = async () => {
        try {
          await post('campaign_cell', {
            file: 'items', row: it.index, col: ITEM_COLS[key], value: inp.value,
          });
          it[key] = inp.value;
          toast(`${it.name}: ${key} cost = ${inp.value}`, 'good');
          refreshHistory();
        } catch (e) { toast('Save failed: ' + e.message, 'bad'); }
      };
      td.append(inp);
      tr.append(td);
    }
    tb.append(tr);
  }
  $('#itemInfo').textContent =
    `${rows.length} of ${campaign.items.length} items — shop / repair / upgrade cost`;
}

// Showdown is the versus / co-op half of the game: 13 stages shipped as
// LVLS_M2 and LVLS_M4 variants, gated by MP unlock criteria.
async function renderStages() {
  const box = $('#stageList');
  box.innerHTML = '';
  let m;
  try { m = await api('modes'); } catch (e) {
    box.append(el('div', 'pad muted',
      'Could not read the stage tables: ' + e.message));
    return;
  }
  for (const st of m.stages) {
    const key = st.name.replace('lvl_', '').toUpperCase();
    const variants = m.levels.filter((l) => l.base === key
      && (l.mode === 'showdown2' || l.mode === 'showdown4'));
    const row = el('div', 'row');
    row.style.cssText = 'display:block; padding:5px 10px';
    // `Default` is the only column that says whether a stage starts unlocked.
    // This used to also treat `criteria === 'NONE'` as open, which reads
    // backwards: NONE means there is no unlock counter, and combined with
    // Default=0 that is a stage with no way IN, not one you already have.
    // Measured: exactly two rows are Default=0 + NONE -- lvl_jail and
    // lvl_sher70 -- and both were being announced as "available from the
    // start" while neither even has a Showdown map on the disc. That is the
    // wrong unlock info a tester reported.
    const open = st.default === '1';
    const noPath = !open && st.criteria === 'NONE';
    row.append(el('div', null, key));
    row.append(el('div', 'muted',
      (open ? 'available from the start'
            : noPath ? 'locked, and no unlock condition is set — leftover data'
                     : `${st.criteria} ≥ ${st.value}`)
      + (variants.length ? ` · ${variants.map((v) => v.mode === 'showdown2' ? '2P' : '4P').join(' ')}`
                         : ' · no map archive')));
    if (variants.length) {
      row.style.cursor = 'pointer';
      row.onclick = () => {
        // One code path opens a level. This used to hunt the sidebar's DOM
        // rows and click one -- and the sidebar only renders rows matching
        // its filter box, so leftover filter text on another tab made this
        // button do nothing, silently.
        showTab('editor');
        loadLevelByRef(variants[0].archive, null, null);
      };
    }
    box.append(row);
  }
  const openCount = m.stages.filter((s) => s.default === '1').length;
  const chOpen = m.characters.filter((c) => c.default === '1').length;
  const info = el('div', 'pad muted');
  info.textContent =
    `${openCount}/${m.stages.length} stages and ${chOpen}/${m.characters.length} ` +
    `characters unlocked by default. Toggle any of them below.`;
  box.append(info);
}

function openMission(i) {
  missionSel = i;
  const m = campaign.missions[i];
  for (const [n, r] of [...document.querySelectorAll('#missionList .row')].entries())
    r.classList.toggle('sel', n === i);
  $('#missionTitle').textContent =
    `Mission ${i + 1} — ${m.Level}`
    + (parseInt(m.Variation, 10) > 1 ? ` (variation ${m.Variation})` : '')
    + (m.archive ? '' : '  (no matching level archive)');
  $('#btnOpenLevel').disabled = !m.archive;

  // the bottom half: the level's enemy roster for this mission's variation (cast.js)
  if (window.rdrMissionEnemies && $('#missionEnemies')) {
    const lvlName = String(m.Level || '').toLowerCase().replace(/^lvl_/, '');
    window.rdrMissionEnemies($('#missionEnemies'), m.archive,
      `lvl_${lvlName}_var_${parseInt(m.Variation, 10) || 1}.enm`);
  }

  const box = $('#missionFields');
  box.innerHTML = '';
  for (const [label, col, blurb] of MISSION_FIELDS) {
    // label | value | note (DESIGN-REVIEW A19).
    const f = el('div', 'wrow');
    const lab = el('label', null, label);
    lab.title = blurb;
    const inp = el('input');
    inp.style.width = '220px';
    inp.style.textAlign = 'left';
    inp.value = m[label] ?? '';
    inp.onchange = async () => {
      try {
        await post('campaign_cell',
          { file: 'levelflow', row: i, col, value: inp.value });
        toast(`${m.Level}: ${label} = ${inp.value}`, 'good');
        m[label] = inp.value;
        refreshHistory();
      } catch (e) { toast('Save failed: ' + e.message, 'bad'); }
    };
    const note = el('span', 'muted');
    note.textContent = blurb;
    f.append(lab, inp, note);
    box.append(f);
  }
}

function renderUnlockables() {
  if (!campaign) return;
  const type = $('#unlockType').value;
  const filter = $('#unlockFilter').value.toLowerCase();
  const rows = campaign.unlockables.filter(
    (u) => (!type || u.type === type) && (!filter || u.name.toLowerCase().includes(filter)));
  const tb = $('#unlockTable tbody');
  tb.innerHTML = '';
  for (const u of rows.slice(0, 400)) {
    const tr = el('tr');
    const cb = el('input');
    cb.type = 'checkbox';
    cb.checked = u.default === '1';
    cb.onchange = async () => {
      try {
        await post('campaign_cell', {
          file: 'unlockables', row: u.index, col: 3, value: cb.checked ? 1 : 0,
        });
        u.default = cb.checked ? '1' : '0';
        toast(`${u.name} ${cb.checked ? 'unlocked' : 'locked'}`, 'good');
        // Rules reads the same unlockables.csv through its own cache. Toggle
        // here, click one subnav button, and it showed the OLD value -- two
        // editors of one file must not each believe their own copy.
        rules = null;
        refreshHistory();
      } catch (e) { cb.checked = !cb.checked; toast('Failed: ' + e.message, 'bad'); }
    };
    const td = el('td');
    td.append(cb);
    tr.append(el('td', null, u.name), el('td', 'muted', u.type), td,
              el('td', 'muted', u.criteria || ''));
    tb.append(tr);
  }
  const unlocked = campaign.unlockables.filter((u) => u.default === '1').length;
  $('#unlockInfo').textContent =
    `${rows.length} shown · ${unlocked} of ${campaign.unlockables.length} unlocked`;
}

// ----------------------------------------------------------- environment
//
// 101 of the 126 file formats on this disc are plain text, including every
// one of the level's environment settings. They were filed as "opaque
// binary" for a long time on no evidence at all. This tab reads them as
// fields and patches values in place, so a file in a tight archive slot
// still fits.

let envFile = null;        // {archive, name}
let envEdits = {};         // path -> new text

async function renderEnvPresets() {
  const box = $('#envPresets');
  if (!box) return;
  box.innerHTML = '';
  if (!level) return;
  let list;
  try { list = await api('env_presets'); } catch { return; }
  for (const p of list) {
    const b = el('button', '', p.label);
    b.style.cssText = 'margin:0 4px 4px 0;font-size:11px';
    b.title = p.note;
    b.onclick = async () => {
      try {
        const dry = await post('env_preset', {
          archive: level.archive, area: level.area, preset: p.key, dry: true });
        if (dry.fits === false)
          return toast(dry.error || 'would not fit', 'bad');
        const r = await post('env_preset', {
          archive: level.archive, area: level.area, preset: p.key });
        toast(`${r.label}: ${r.fields} field(s) in `
          + `${r.files.length} file(s) — Ctrl+Z to undo`, 'good');
        await refreshHistory();
        await applyLevelEnvironment();
        if (envFile) openEnvFile(envFile.archive, envFile.name);
      } catch (e) { toast(e.message, 'bad'); }
    };
    box.append(b);
  }
  const note = el('div', 'muted');
  note.style.cssText = 'font-size:11px;margin-top:2px;line-height:1.5';
  note.textContent = 'Writes the level’s lighting and fog. One undo step.';
  box.append(note);
}

async function loadEnvList() {
  const box = $('#envList');
  const head = $('#envLevel');
  renderEnvPresets();
  if (!level) {
    head.textContent = 'Load a level in the 3D editor first.';
    box.innerHTML = '';
    return;
  }
  head.textContent = `${level.archive.split('/').pop()} · ${level.area || 'main'}`;
  box.innerHTML = '<p class="muted pad" style="font-size:11px">Reading…</p>';
  let d;
  try {
    d = await api('level_props?archive=' + encodeURIComponent(level.archive)
      + (level.area ? '&area=' + encodeURIComponent(level.area) : ''));
  } catch (e) {
    box.innerHTML = `<p class="muted pad">${e.message}</p>`;
    return;
  }
  const ownOnly = $('#envOwnOnly').checked;
  box.innerHTML = '';
  let shown = 0;
  for (const g of d.groups) {
    const files = ownOnly ? g.files.filter((f) => f.own) : g.files;
    if (!files.length) continue;
    const h = el('div', 'srchead', g.title);
    box.append(h);
    for (const f of files) {
      shown++;
      const row = el('div', 'row child');
      row.style.cssText = 'display:flex;gap:8px;font-size:11px;cursor:pointer';
      row.append(el('span', '', f.name));
      row.firstChild.style.flex = '1';
      row.append(el('span', 'muted', f.ext));
      row.onclick = () => {
        for (const o of box.querySelectorAll('.row')) o.classList.remove('sel');
        row.classList.add('sel');
        openEnvFile(level.archive, f.name);
      };
      box.append(row);
    }
  }
  if (!shown)
    box.innerHTML = '<p class="muted pad" style="font-size:11px">'
      + 'No environment files matched. Untick "only this area’s files" '
      + 'to see the whole archive.</p>';
}

async function openEnvFile(archive, name) {
  envFile = { archive, name };
  envEdits = {};
  $('#envTitle').textContent = name;
  const box = $('#envFields');
  box.innerHTML = '<p class="muted">Reading…</p>';
  let d;
  try {
    d = await api(`props?archive=${encodeURIComponent(archive)}`
      + `&name=${encodeURIComponent(name)}`);
  } catch (e) {
    box.innerHTML = `<p class="muted">${e.message}</p>`;
    return;
  }
  $('#envSlot').textContent = d.slot != null
    ? `${d.bytes} bytes · ${d.headroom} spare in slot` : '';
  if (!d.textual) {
    box.innerHTML = '<p class="muted">This one is binary, not text.</p>';
    return;
  }
  box.innerHTML = '';
  let lastBlock = null;
  for (const f of d.fields) {
    const blk = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/')) : '';
    if (blk !== lastBlock) {
      lastBlock = blk;
      if (blk) {
        const h = el('div', 'srchead', blk);
        h.style.marginTop = '8px';
        box.append(h);
      }
    }
    box.append(envFieldRow(f));
  }
  if (d.truncated)
    box.append(el('p', 'muted', `Showing the first ${d.fields.length} of `
      + `${d.total} fields — use the Files tab for the whole thing.`));
  updateEnvButtons();
}

function envFieldRow(f) {
  const row = el('div');
  row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:2px 0';
  const label = el('span', 'muted', f.key);
  label.style.cssText = 'width:210px;flex:none;font-size:11px';
  label.title = f.path;
  row.append(label);

  const inp = el('input');
  inp.value = f.value;
  inp.style.cssText = 'flex:1;font-family:inherit';
  inp.oninput = () => {
    if (inp.value === f.value) delete envEdits[f.path];
    else envEdits[f.path] = inp.value;
    inp.style.borderColor = envEdits[f.path] ? 'var(--gold)' : '';
    updateEnvButtons();
  };
  row.append(inp);

  // A 3-number field in a lighting or fog file is a colour. Showing it as
  // one lets you pick "warmer" instead of solving for it in decimals.
  if (f.kind === 'vector3' && f.numbers && f.numbers.every((n) => n <= 1.001)) {
    const sw = el('input');
    sw.type = 'color';
    sw.style.cssText = 'width:34px;height:22px;padding:0;flex:none';
    const hex = (n) => Math.round(Math.min(1, Math.max(0, n)) * 255)
      .toString(16).padStart(2, '0');
    sw.value = '#' + f.numbers.map(hex).join('');
    sw.oninput = () => {
      const m = sw.value.slice(1).match(/../g).map((h) => parseInt(h, 16) / 255);
      inp.value = m.map((n) => n.toFixed(6)).join('\t');
      inp.oninput();
    };
    row.append(sw);
  }
  return row;
}

function updateEnvButtons() {
  const n = Object.keys(envEdits).length;
  $('#envSave').disabled = !n;
  $('#envRevert').disabled = !n;
  $('#envSave').textContent = n ? `Save ${n} change${n === 1 ? '' : 's'}`
                                : 'Save changes';
}

async function saveEnv() {
  if (!envFile || !Object.keys(envEdits).length) return;
  const body = { ...envFile, edits: envEdits };
  try {
    const dry = await post('props_save', { ...body, dry: true });
    if (!dry.fits)
      return toast(`Would not fit: ${dry.stored} bytes into a `
        + `${dry.slot}-byte slot. Shorten a value.`, 'bad');
    const r = await post('props_save', body);
    toast(`Saved ${r.fields} field(s) — ${r.used}/${r.slot} bytes`, 'good');
    envEdits = {};
    await openEnvFile(envFile.archive, envFile.name);
    await refreshHistory();
    if (level) applyLevelEnvironment();
  } catch (e) {
    toast('Save failed: ' + e.message, 'bad');
  }
}

// Tint the viewport the way the game does. The raw textures are not what
// you see in-game: every level multiplies them by an ambient colour from its
// .lgt and fades them into a fog colour from its .fog. That is why a ground
// texture that looks grey-brown in the Textures tab reads as green on the
// TV, and without this the editor and the game will never agree.
let envApplied = null;

async function applyLevelEnvironment() {
  if (!level) return;
  const key = `${level.archive}|${level.area}`;
  try {
    const d = await api('level_props?archive='
      + encodeURIComponent(level.archive)
      + (level.area ? '&area=' + encodeURIComponent(level.area) : ''));
    // A level ships several .lgt files: `_lvl_` lights the world, `_cre_`
    // lights characters, `_shb_` the shadow blob. Only the first one is the
    // ambient the terrain is multiplied by, and picking any other makes the
    // viewport disagree with the game in a way that looks like a bug.
    const pick = (title, ext) => {
      const g = d.groups.find((x) => x.title === title);
      if (!g) return null;
      const mine = g.files.filter((x) => x.ext === ext && x.own);
      const f = mine.find((x) => /_lvl_/i.test(x.name)) || mine[0]
        || g.files.find((x) => x.ext === ext);
      return f ? f.name : null;
    };
    const lgt = pick('Lighting', 'lgt');
    const fog = pick('Atmosphere', 'fog');
    let ambient = null, fogCol = null, fogNear = null, fogFar = null;

    if (lgt) {
      const p = await api(`props?archive=${encodeURIComponent(level.archive)}`
        + `&name=${encodeURIComponent(lgt)}`);
      const a = p.fields.find((f) => f.key === 'Ambient');
      const s = p.fields.find((f) => f.key === 'AmbientScale');
      if (a?.numbers?.length === 3) {
        const k = s?.numbers?.[0] ?? 1;
        ambient = a.numbers.map((n) => n * k);
      }
    }
    if (fog) {
      const p = await api(`props?archive=${encodeURIComponent(level.archive)}`
        + `&name=${encodeURIComponent(fog)}`);
      const c = p.fields.find((f) => f.key === 'ColorStart');
      const d0 = p.fields.find((f) => f.key === 'DistStart');
      const d1 = p.fields.find((f) => f.key === 'DistFinish'
                                   || f.key === 'DistEnd');
      if (c?.numbers?.length === 3) fogCol = c.numbers;
      fogNear = d0?.numbers?.[0];
      fogFar = d1?.numbers?.[0];
    }
    envApplied = { key, ambient, fogCol, fogNear, fogFar, lgt, fog };
    setEnvTint($('#envTint') ? $('#envTint').checked : true);
  } catch { /* environment is a nicety; never block the level on it */ }
}

function setEnvTint(on) {
  const e = envApplied;
  const info = $('#envTintInfo');
  if (!e) { if (info) info.textContent = ''; return; }
  if (on && e.ambient) {
    const c = new THREE.Color(...e.ambient.map((n) => Math.min(1, n * 2)));
    for (const m of [renderMat, renderPlain]) if (m.color) m.color.copy(c);
    for (const mat of texMats.values()) mat.color.copy(c);
  } else {
    const w = new THREE.Color(0xffffff);
    for (const m of [renderMat, renderPlain]) if (m.color) m.color.copy(w);
    for (const mat of texMats.values()) mat.color.copy(w);
  }
  if (on && e.fogCol) {
    scene.fog = new THREE.Fog(new THREE.Color(...e.fogCol),
                              (e.fogNear ?? 60) * 10, (e.fogFar ?? 400) * 10);
  } else {
    scene.fog = null;
  }
  if (info)
    info.textContent = on && (e.ambient || e.fogCol)
      ? `${e.lgt || ''}${e.lgt && e.fog ? ' + ' : ''}${e.fog || ''}`
      : 'off';
}

// ------------------------------------------------ text the game displays

let stRows = [];
let stPicked = null;

async function runStringSearch() {
  const box = $('#stList');
  const q = $('#stQ').value.trim();
  const slot = $('#stSlot').value || '0';
  box.innerHTML = '<p class="muted pad">Reading every string table…</p>';
  let d;
  try {
    d = await api(`strings?slot=${slot}`
      + (q ? `&q=${encodeURIComponent(q)}` : ''));
  } catch (e) {
    box.innerHTML = `<p class="muted pad">${e.message}</p>`;
    return;
  }
  stRows = d.strings;
  $('#stInfo').textContent = `${d.total} string(s) across ${d.files} table(s)`
    + (d.capped ? ' · showing the first ' + d.strings.length : '');

  // Populate the language picker once, from what the disc actually has.
  const sel = $('#stSlot');
  if (!sel.options.length) {
    for (const [k, v] of Object.entries(d.languages))
      sel.append(new Option(v, k));
    sel.value = slot;
  }

  box.innerHTML = '';
  const table = el('table');
  table.innerHTML = '<thead><tr><th>Name</th><th>Text</th>'
    + '<th class="num">Copies</th></tr></thead>';
  const body = el('tbody');
  for (const r of stRows) {
    const tr = el('tr');
    tr.style.cursor = 'pointer';
    const n = el('td', '', r.name);
    const t = el('td', '', r.text.replace(/\n/g, ' ⏎ '));
    t.style.cssText = 'max-width:520px;overflow:hidden;text-overflow:ellipsis;'
      + 'white-space:nowrap';
    if (r.varies) {
      t.append(el('span', 'muted', '  (differs between archives)'));
      t.style.color = 'var(--gold)';
    }
    const c = el('td', 'num', String(r.copies.length));
    tr.append(n, t, c);
    tr.onclick = () => {
      for (const o of body.querySelectorAll('tr')) o.classList.remove('sel');
      tr.classList.add('sel');
      showStringEditor(r);
    };
    body.append(tr);
  }
  table.append(body);
  box.append(table);
  if (!stRows.length)
    box.innerHTML = '<p class="muted pad">Nothing matched.</p>';
}

function showStringEditor(r) {
  stPicked = r;
  const box = $('#stEdit');
  box.innerHTML = '';
  box.append(el('div', '', r.name));
  box.lastChild.style.cssText = 'font-size:12px;color:var(--gold);'
    + 'margin-bottom:2px;word-break:break-all';
  box.append(el('div', 'muted',
    `font ${r.font || '(none)'} · scale ${r.scale.join(' × ')} · `
    + `${r.copies.length} cop${r.copies.length === 1 ? 'y' : 'ies'}`));
  box.lastChild.style.cssText = 'font-size:11px;margin-bottom:8px';

  const ta = el('textarea');
  ta.value = r.text;
  ta.style.cssText = 'width:100%;height:120px;font-family:inherit;'
    + 'font-size:12px';
  box.append(ta);

  const info = el('div', 'muted');
  info.style.cssText = 'font-size:11px;margin:4px 0 8px';
  const measure = () => {
    const d = ta.value.length - r.text.length;
    info.textContent = `${ta.value.length} characters`
      + (d === 0 ? ' (unchanged length)'
        : d > 0 ? ` — ${d} longer, needs ${d * 2} more bytes per copy`
                : ` — ${-d} shorter`);
  };
  ta.oninput = measure;
  measure();
  box.append(info);

  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:6px';
  const save = el('button', 'primary', 'Save everywhere');
  save.onclick = async () => {
    const body = { name: r.name, text: ta.value, slot: +$('#stSlot').value,
                   copies: r.copies };
    try {
      const dry = await post('string_set', { ...body, dry: true });
      if (!dry.ok) {
        const f = dry.refused[0];
        return toast(`Too long — ${f.file} in ${f.archive.split('/').pop()}: `
          + `${f.why}. Nothing was written.`, 'bad');
      }
      const res = await post('string_set', body);
      toast(`Rewrote ${res.applied} cop${res.applied === 1 ? 'y' : 'ies'}`
        + ' — one Ctrl+Z undoes all of them', 'good');
      await refreshHistory();
      await runStringSearch();
    } catch (e) { toast(e.message, 'bad'); }
  };
  const revert = el('button', '', 'Reset');
  revert.onclick = () => { ta.value = r.text; measure(); };
  bar.append(save, revert);
  box.append(bar);

  const list = el('div', 'muted');
  list.style.cssText = 'font-size:11px;margin-top:10px;line-height:1.6';
  list.textContent = 'Lives in: '
    + r.copies.map((c) => c.archive.split('/').pop()).join(', ');
  box.append(list);
}

// -------------------------------------------- one setting, everywhere
//
// The same field exists in dozens of copies across the disc — one per level,
// per area, per Showdown variant. Editing them one at a time is how you end
// up with a mod that works in three levels and not the other thirty.

let fsHits = [];
const fsPicked = new Set();

// Formats worth offering first. The rest are still typeable.
const FS_EXTS = ['fog', 'lgt', 'water', 'sky', 'rain', 'snow', 'wind', 'grass',
  'glows', 'flares', 'lod', 'shadows', 'fade', 'film', 'pmtl', 'ptx', 'swptx',
  'fx', 'duel', 'inv', 'pow', 'ntr', 'plyr', 'msd', 'cam', 'nav', 'gait',
  'reigns', 'veh', 'trn', 'sub', 'aisp', 'hot', 'sphs', 'ecs', 'accu'];

function fsKeyOf(h) {
  return `${h.archive}|${h.file}|${h.path}`;
}

async function runFieldSearch() {
  const ext = $('#fsExt').value.trim().replace(/^\./, '');
  const key = $('#fsKey').value.trim();
  const box = $('#fsResults');
  if (!ext) return toast('Pick a file format to search in.', 'bad');
  box.innerHTML = '<p class="muted pad">Searching every archive…</p>';
  $('#fsInfo').textContent = '';
  fsHits = [];
  fsPicked.clear();
  let d;
  try {
    d = await api(`field_search?ext=${encodeURIComponent(ext)}`
      + (key ? `&key=${encodeURIComponent(key)}` : ''));
  } catch (e) {
    box.innerHTML = `<p class="muted pad">${e.message}</p>`;
    return;
  }
  fsHits = d.hits;
  $('#fsInfo').textContent =
    `${d.hits.length} field(s) in ${d.files} file(s) · ${d.scanned} scanned`
    + ` · ${d.distinct} distinct value(s)` + (d.capped ? ' · capped' : '');

  // The value histogram is usually the answer on its own.
  const cbox = $('#fsCommon');
  cbox.innerHTML = '';
  for (const c of d.common) {
    const row = el('div');
    row.style.cssText = 'display:flex;gap:8px;font-size:11px;padding:2px 0;'
      + 'cursor:pointer';
    const v = el('span', '', c.value || '(empty)');
    v.style.cssText = 'flex:1;word-break:break-all';
    const n = el('span', 'muted', String(c.count));
    row.append(v, n);
    row.title = 'Tick every field with this value';
    row.onclick = () => {
      fsPicked.clear();
      for (const h of fsHits) if (h.value === c.value) fsPicked.add(fsKeyOf(h));
      $('#fsValue').value = c.value;
      renderFieldResults();
    };
    cbox.append(row);
  }
  if (!d.common.length)
    cbox.innerHTML = '<p class="muted" style="font-size:11px">No matches.</p>';
  renderFieldResults();
}

function renderFieldResults() {
  const box = $('#fsResults');
  box.innerHTML = '';
  if (!fsHits.length) {
    box.innerHTML = '<p class="muted pad">Nothing matched.</p>';
    $('#fsBulk').style.display = 'none';
    return;
  }
  $('#fsBulk').style.display = 'flex';
  $('#fsCount').textContent = String(fsPicked.size);

  const table = el('table');
  table.innerHTML = '<thead><tr><th></th><th>File</th><th>Field</th>'
    + '<th>Value</th><th>Archive</th></tr></thead>';
  const body = el('tbody');
  for (const h of fsHits) {
    const k = fsKeyOf(h);
    const tr = el('tr');
    const tick = el('input');
    tick.type = 'checkbox';
    tick.checked = fsPicked.has(k);
    tick.onchange = () => {
      if (tick.checked) fsPicked.add(k); else fsPicked.delete(k);
      $('#fsCount').textContent = String(fsPicked.size);
    };
    const td = (child) => { const c = el('td'); c.append(child); return c; };
    tr.append(td(tick));
    tr.append(td(document.createTextNode(h.file)));
    tr.append(td(document.createTextNode(h.path)));
    const v = el('td', '', h.value);
    v.style.cssText = 'font-family:inherit;max-width:280px;word-break:break-all';
    tr.append(v);
    tr.append(td(document.createTextNode(h.archive.split('/').pop())));
    body.append(tr);
  }
  table.append(body);
  box.append(table);
}

async function applyFieldBulk() {
  const value = $('#fsValue').value;
  const targets = fsHits.filter((h) => fsPicked.has(fsKeyOf(h)))
    .map((h) => ({ archive: h.archive, file: h.file, path: h.path }));
  if (!targets.length) return toast('Tick some rows first.', 'bad');
  try {
    const dry = await post('field_bulk_set', { value, targets, dry: true });
    if (!dry.ok) {
      const first = dry.refused[0];
      return toast(`Refused — ${dry.refused.length} file(s) would not fit, `
        + `e.g. ${first.file}: ${first.why}. Nothing was written.`, 'bad');
    }
    const r = await post('field_bulk_set', { value, targets });
    toast(`Set ${r.applied} file(s) — one Ctrl+Z undoes all of them`, 'good');
    await refreshHistory();
    await runFieldSearch();
  } catch (e) {
    toast(e.message, 'bad');
  }
}

// -------------------------------------------------------------- textures

let texArea = null;   // which area of the selected archive is on show
let texViewer = null;

// Click a texture, get the models that draw with it and a 3D preview of one.
// The list is exact: it reads the per-surface binding out of each container
// MOD0 rather than inferring anything.
async function showTextureUsers(archive, area, t) {
  const box = $('#texUsers');
  const info = $('#texViewInfo');
  box.innerHTML = '<p class="muted pad" style="font-size:11px">Looking…</p>';
  info.textContent = t.name || `#${t.i}`;
  let d;
  try {
    d = await api('texture_users?archive=' + encodeURIComponent(archive)
      + (area ? '&area=' + encodeURIComponent(area) : '') + '&i=' + t.i);
  } catch (e) {
    box.innerHTML = `<p class="muted pad" style="font-size:11px">${e.message}</p>`;
    return;
  }
  box.innerHTML = '';
  if (!d.users.length) {
    box.innerHTML = '<p class="muted pad" style="font-size:11px">'
      + 'Unused in this area.</p>';
    texViewer?.clear();
    return;
  }
  const head = el('div', 'muted');
  head.style.cssText = 'padding:5px 9px;font-size:11px';
  head.textContent = `${d.total} model${d.total === 1 ? '' : 's'} use it`;
  box.append(head);
  for (const u of d.users) {
    const row = el('div', 'row');
    row.style.cssText = 'display:flex;gap:8px;font-size:11px;cursor:pointer';
    row.append(el('span', '', u.name));
    row.firstChild.style.flex = '1';
    row.append(el('span', 'muted', `${u.parts}p`));
    row.onclick = () => {
      for (const o of box.querySelectorAll('.row')) o.classList.remove('sel');
      row.classList.add('sel');
      previewChunk($('#texView'), $('#texViewInfo'), archive, area, u.chunk,
                   () => texViewer, (v) => { texViewer = v; },
                   () => $('#texSpin')?.checked !== false);
    };
    box.append(row);
  }
  box.querySelector('.row').click();       // show the biggest user right away
}

// Shared body of every "preview this chunk" panel.
async function previewChunk(host, info, archive, area, chunk,
                            get, set, spinOn) {
  let note = host.querySelector('.vnote');
  if (!note) { note = el('div', 'vnote'); host.append(note); }
  let tex = host.querySelector('.vtex');
  if (!tex) { tex = el('div', 'vtex'); host.append(tex); }
  note.textContent = 'Decoding…';
  tex.textContent = '';
  try {
    const d = await api(`model?archive=${encodeURIComponent(archive)}`
      + (area ? `&area=${encodeURIComponent(area)}` : '')
      + `&chunk=${chunk}`);
    let v = get();
    if (!v) { v = makeViewer(host); set(v); }
    v.setSpin(spinOn());
    v.show(d, assetTexUrl(archive, area));
    note.textContent = '';
    info.textContent = `${d.verts.toLocaleString()} verts · `
      + `${d.tris.toLocaleString()} tris · ${d.parts} part`
      + (d.parts === 1 ? '' : 's');
    tex.textContent = d.textures.length
      ? d.textures.map((x) => x.name).join(' · ') : 'untextured';
  } catch (e) {
    get()?.clear();
    note.textContent = 'Could not decode: ' + e.message;
  }
}

let texPicked = null;
function selectTexture(archive, t, img) {
  texPicked = { archive, t, img };
  const b = $('#btnTexReplace');
  if (!b) return;
  b.disabled = t.replaceable === false;
  b.title = t.replaceable === false ? (img.title || 'Not replaceable yet.')
    : `Import a ${t.width}x${t.height} PNG over this texture`;
}

async function loadTextures(archive, area) {
  texPicked = null;
  if ($('#btnTexReplace')) $('#btnTexReplace').disabled = true;
  const grid = $('#texGrid');
  grid.innerHTML = '<div class="muted">decoding…</div>';
  texArea = area === undefined ? texArea : area;
  // The index space, the preview URL and the import target must all name the
  // SAME bundle. Sending the area to some of them and not others would put
  // the grid and the write target in different index spaces -- which is how a
  // "replace this texture" click ends up patching a different picture.
  const q = 'archive=' + encodeURIComponent(archive)
    + (texArea ? '&area=' + encodeURIComponent(texArea) : '');
  try {
    const d = await api('textures?' + q);
    grid.innerHTML = '';
    $('#texInfo').textContent =
      `${d.textures.length} textures in ${d.source || '?'}`;
    await renderTexAreas(archive);
    for (const t of d.textures) {
      const cell = el('div');
      cell.style.cssText = 'width:140px; text-align:center';
      const img = document.createElement('img');
      img.src = `/api/texture.png?${q}&i=${t.i}`;
      img.style.cssText =
        'width:128px; height:128px; image-rendering:pixelated;' +
        'background:#0d0b08; border:1px solid var(--line); border-radius:3px';
      img.loading = 'lazy';
      img.alt = `texture ${t.i}`;
      img.style.cursor = 'pointer';
      img.onclick = () => {
        for (const o of grid.querySelectorAll('img'))
          o.style.borderColor = 'var(--line)';
        img.style.borderColor = 'var(--gold)';
        selectTexture(archive, t, img);
        showTextureUsers(archive, texArea, t);
      };
      cell.append(img);
      const nm = el('div', null, t.name || `#${t.i}`);
      nm.style.cssText = 'font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
      cell.append(nm);
      const dims = el('div', 'muted', `${t.width || '?'}x${t.height || '?'} · ${t.pal_kind}`);
      dims.style.cssText = 'font:var(--t-mono);font-size:11px';
      cell.append(dims);

      // The gate used to be `pal_kind === 'PAL0'`, which is EVERY texture on
      // the disc, so the else branch was dead and the button was offered on
      // thousands that texture.encode_into refuses -- after the user had
      // picked a PNG. The route now says which are really replaceable, and a
      // refusal that has to happen belongs before the file picker, not after.
      // rdr-fmt, CAPABILITIES.md C-027.
      // DESIGN-REVIEW A10: one Replace… in the side panel for the selected
      // tile, not 53 identical buttons. A tile that cannot be replaced is
      // dimmed and says why in its tooltip.
      if (t.replaceable === false) {
        img.style.opacity = '.5';
        img.title = (t.refused === 'swizzled'
          ? 'Stored swizzled (GS tiled order), so not replaceable yet: writing '
            + 'linear pixels into it would scramble the picture.'
          : `${t.bpp === 4 ? '16-colour' : `${t.bpp}-bit`}, not replaceable yet. `
            + 'Only 256-colour (8bpp) textures can be replaced so far.');
      }
      grid.append(cell);
    }
    if (!d.textures.length)
      grid.innerHTML = '<div class="muted">No textures in this level bundle.</div>';
  } catch (e) {
    grid.innerHTML = '';
    toast('Textures unavailable: ' + e.message, 'bad');
  }
}

// Brimstone's archives carry seven bundles; without a switcher here 1,292 of
// the disc's textures could not be viewed or replaced at all.
async function renderTexAreas(archive) {
  const box = $('#texAreas');
  if (!box) return;
  let areas = [];
  try {
    areas = (await api('level?archive=' + encodeURIComponent(archive))).areas || [];
  } catch (e) { /* a level with no areas is fine */ }
  if (areas.length < 2) { box.style.display = 'none'; box.innerHTML = ''; return; }
  box.style.display = '';
  box.innerHTML = '';
  const current = texArea || areas[0].rsc;
  for (const a of areas) {
    const b = el('button', 'vtab' + (a.rsc === current ? ' on' : ''), a.name);
    b.style.cssText = 'font-size:12px;padding:2px 8px';
    b.onclick = () => loadTextures(archive, a.rsc);
    box.append(b);
  }
}

function importTexture(archive, t, imgEl) {
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.accept = 'image/png';
  picker.onchange = async () => {
    const file = picker.files?.[0];
    if (!file) return;
    toast(`Encoding ${file.name} → PS2 256-colour…  this takes a few seconds.`);
    try {
      const buf = new Uint8Array(await file.arrayBuffer());
      let bin = '';
      for (let i = 0; i < buf.length; i += 8192)
        bin += String.fromCharCode.apply(null, buf.subarray(i, i + 8192));
      const r = await post('texture_import',
        { archive, area: texArea, i: t.i, png_b64: btoa(bin) });
      toast(`Replaced ${r.name} (${r.width}x${r.height}) — ${r.used}/${r.slot} bytes of slot.`, 'good');
      // Bust the browser cache so the new pixels show immediately.
      const base = `/api/texture.png?archive=${encodeURIComponent(archive)}`
        + (texArea ? `&area=${encodeURIComponent(texArea)}` : '')
        + `&i=${t.i}`;
      imgEl.src = base + '&t=' + Date.now();
      dropTexture(archive, t.i);
      if (level && level.archive === archive) await reloadLevel(true, true);
      refreshHistory();
    } catch (e) {
      toast('Import failed: ' + e.message, 'bad');
    }
  };
  picker.click();
}

// --------------------------------------------------------------- weapons

let gunData = null;      // {weapons, groups, copies}
let gunCurrent = null;   // {weapon, stock}
let gunEdits = {};       // key -> new value

async function loadGuns() {
  if (!gunData) gunData = await api('guns');
  $('#gunCopies').textContent = `${gunData.copies} copies on disc`;
  const box = $('#gunList');
  const filter = $('#gunFilter').value.toLowerCase();
  box.innerHTML = '';
  for (const w of gunData.weapons) {
    if (filter && !w.name.toLowerCase().includes(filter)) continue;
    const row = el('div', 'row');
    row.dataset.gun = w.name;
    row.append(el('span', 'nm', w.name),
               el('span', 'tag', `dmg ${w.damage ?? '?'} · clip ${w.clip ?? '?'}`));
    row.onclick = () => openGun(w.name);
    box.append(row);
  }
}

async function openGun(name) {
  gunCurrent = await api('gun?name=' + encodeURIComponent(name));
  gunEdits = {};
  for (const r of document.querySelectorAll('#gunList .row'))
    r.classList.toggle('sel', r.dataset.gun === name);
  $('#gunTitle').textContent = name;
  $('#gunFields').classList.remove('isempty');
  renderGunFields();
  wmReset();
}

// ------------------------------------------------------ weapon model import
// Plain buttons over the weapon_import route. Check = dry run (default on the
// server), Import = real write. The route answers 200 with ok:false and a
// problem {message, fix} for refusals, so this uses fetch directly.
let wmText = null, wmName = '';
function wmWarnShared() {
  const box = $('#wmWarn');
  const w = gunCurrent && gunCurrent.weapon;
  const others = (w && w.model && gunData)
    ? gunData.weapons.filter((x) => (x.model || "").trim() === (w.model || "").trim() && x.name !== w.name)
        .map((x) => x.name) : [];
  if (!others.length) { box.style.display = 'none'; box.textContent = ''; return; }
  box.style.display = '';
  box.textContent = `Warning: "${w.model}" is shared. Importing here also changes `
    + others.join(', ') + '. For a weapon that changes only itself, use New weapon\u2026.';
}
function wmReset() {
  wmText = null; wmName = '';
  $('#wmResult').textContent = '';
  wmWarnShared();
  const w = gunCurrent && gunCurrent.weapon;
  $('#wmCur').textContent = w
    ? `${w.name} uses the model "${w.model || '?'}".`
    : 'Pick a weapon on the left.';
  $('#btnWmPick').disabled = !(w && w.model);
  for (const id of ['#btnWmModImport', '#btnWmExportMod', '#btnWmExportXmod']) $(id).disabled = !(w && w.model);
  $('#btnWmCheck').disabled = true;
  $('#btnWmWrite').disabled = true;
  $('#wmStatus').textContent = '';
}
async function wmCall(dry) {
  const w = gunCurrent.weapon;
  const r = await fetch('/api/weapon_import', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target: w.model, obj: wmText, dry,
                           allow_shared: true }) });
  return r.json();
}
function wmShow(j, dry) {
  const box = $('#wmResult');
  box.innerHTML = '';
  const line = (t, c) => { const d = el('div', c || '', t); d.style.marginTop = '4px'; box.append(d); };
  if (j.error) return line(j.error, 'bad');
  if (!j.ok) {
    const p = j.problem || {};
    line(p.message || 'The import was refused.', 'bad');
    if (p.fix) line(p.fix);
    line('Nothing was changed.', 'muted');
    $('#btnWmWrite').disabled = true;
    return;
  }
  const pl = j.plan || {};
  if (j.written) {
    line(j.message || 'Imported.', 'good');
    line(j.next || '', 'muted');
    toast((j.message || 'Imported.') + ' ' + (j.next || ''), 'good');
    $('#btnWmWrite').disabled = true;
    return;
  }
  const wr = (pl.writes || [])[0] || {};
  line(`OK to import: ${pl.triangles} triangles, uses ${wr.stored} of `
    + `${wr.slot} bytes available.`, 'good');
  if ((pl.shared_by || []).length > 1)
    line(`This changes ${pl.shared_by.length} weapons: `
      + pl.shared_by.join(', ') + '.');
  line('Press Import to write it.', 'muted');
  $('#btnWmWrite').disabled = false;
}
$('#btnWmPick').onclick = () => $('#wmFile').click();

// .mod / .xmod: the weapon's model as the game has it, out (the PS2's, or made the Xbox's) and in (an .xmod made the
// PS2's), over the weapon_model_export / weapon_model_import routes
function wmModelStem() {
  const m = (gunCurrent && gunCurrent.weapon && gunCurrent.weapon.model || '').trim();
  return m.replace(/\.[^.]*$/, '');
}
async function wmExport(fmt) {
  const w = gunCurrent && gunCurrent.weapon;
  if (!w || !w.model) return;
  if (await saveFile(`/api/weapon_model_export?target=${encodeURIComponent(w.model.trim())}&format=${fmt}`,
                     wmModelStem() + '.' + fmt))
    toast(`${wmModelStem()}.${fmt} saved (your Downloads folder).`, 'good');
}
$('#btnWmExportMod').onclick = () => wmExport('mod');
$('#btnWmExportXmod').onclick = () => wmExport('xmod');
$('#btnWmModImport').onclick = () => $('#wmModFile').click();
async function wmModCall(body) {
  const r = await fetch('/api/weapon_model_import', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                     body: JSON.stringify(body) });
  return r.json();
}
$('#wmModFile').onchange = async (e) => {
  const f = e.target.files[0];
  e.target.value = '';
  const w = gunCurrent && gunCurrent.weapon;
  if (!f || !w || !w.model) return;
  const box = $('#wmResult');
  box.innerHTML = '';
  const line = (t, c) => { const d = el('div', c || '', t); d.style.marginTop = '4px'; box.append(d); };
  const data = await new Promise((res, rej) => {
    const rd = new FileReader();
    rd.onload = () => res(String(rd.result).split(',')[1] || '');
    rd.onerror = () => rej(rd.error);
    rd.readAsDataURL(f);
  });
  const body = { target: w.model.trim(), filename: f.name, data, dry: true };
  let j;
  try { j = await wmModCall(body); } catch (ex) { return line('The Studio is not answering: ' + ex.message, 'bad'); }
  const pl = j.plan || {};
  if (j.error) return line(j.error, 'bad');
  if (!j.ok) {
    const p = j.problem || {};
    line(p.message || 'The import was refused.', 'bad');
    if (p.fix) line(p.fix);
    line('Nothing was changed.', 'muted');
    return;
  }
  line(`${f.name}: ${pl.triangles} triangles, ${pl.stored} of ${pl.slot} bytes (all ${pl.copies} copies fit).`, 'good');
  for (const n of pl.notes || []) line(n, 'muted');
  const ask = window.rdrAskInPage;
  const go = ask ? await ask('Import ' + f.name,
    `Replace the model "${w.model.trim()}" with ${f.name}` + (f.name.toLowerCase().endsWith('.xmod') ? ' (made the PS2\'s)' : '')
      + ` in all ${pl.copies} copies on the working disc? Ctrl+Z undoes it.`,
    [['Cancel', false], ['Import', true, 'primary']]) : true;
  if (!go) return line('Nothing was changed.', 'muted');
  try { j = await wmModCall({ ...body, dry: false }); } catch (ex) { return line('The Studio is not answering: ' + ex.message, 'bad'); }
  if (!j.ok) { const p = j.problem || {}; line(p.message || j.error || 'The import was refused.', 'bad'); if (p.fix) line(p.fix); return; }
  line(j.message || 'Imported.', 'good');
  line(j.next || '', 'muted');
  toast(j.message || 'Imported.', 'good');
  try { await refreshHistory(); } catch {}
};
$('#wmFile').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  wmText = await f.text(); wmName = f.name;
  $('#wmStatus').textContent = f.name;
  $('#btnWmCheck').disabled = false;
  $('#btnWmWrite').disabled = true;
  $('#wmResult').textContent = '';
  e.target.value = '';
  await $('#btnWmCheck').onclick();
};
$('#btnWmCheck').onclick = async () => {
  if (!wmText || !gunCurrent) return;
  try { wmShow(await wmCall(true), true); }
  catch (e) { wmShow({ error: 'The Studio is not answering: ' + e.message }); }
};
$('#btnWmWrite').onclick = async () => {
  if (!wmText || !gunCurrent) return;
  if (!confirm(`Replace the model "${gunCurrent.weapon.model}" with `
    + `"${wmName}"? Ctrl+Z undoes it.`)) return;
  try { wmShow(await wmCall(false), false); await refreshHistory(); }
  catch (e) { wmShow({ error: 'The Studio is not answering: ' + e.message }); }
};

function renderGunFields() {
  const box = $('#gunFields');
  box.innerHTML = '';
  if (!gunCurrent) return;
  const cur = gunCurrent.weapon.fields;
  const stock = gunCurrent.stock;

  for (const [group, fields] of gunData.groups) {
    const h = el('h3', null, group);
    box.append(h);
    for (const [key, blurb] of fields) {
      if (!(key in cur)) continue;
      // label | value | note, 24px rows (DESIGN-REVIEW A18). The inputs were
      // 510px wide for one-digit numbers.
      const f = el('div', 'wrow');
      const lab = el('label', null, key);
      lab.title = blurb;
      // Enum fields (DisplayWeaponType and friends) get a dropdown of the
      // values the disc actually uses. A tester asked for exactly this --
      // "I can't change the weapon type, that dictates if it's Single pistol,
      // double, shotgun or throwable" -- and a free number box would not have
      // told him that 2 means shotgun.
      const opts = (gunData.choices || {})[key];
      const inp = el(opts ? 'select' : 'input');
      if (opts) {
        const have = Number(gunEdits[key] ?? cur[key]);
        for (const [v, label] of opts) {
          const o = el('option', null, `${v} — ${label}`);
          o.value = String(v);
          inp.append(o);
        }
        // A value no shipped weapon uses is kept and shown rather than
        // silently snapped to the nearest listed one.
        if (!opts.some(([v]) => Number(v) === have)) {
          const o = el('option', null, `${have} — not used by any shipped weapon`);
          o.value = String(have);
          inp.append(o);
        }
        inp.value = String(have);
      } else {
        inp.value = gunEdits[key] ?? cur[key];
      }
      inp.dataset.key = key;
      const onEdit = () => {
        const v = parseFloat(inp.value);
        if (Number.isNaN(v)) return;
        gunEdits[key] = v;
        $('#btnGunSave').disabled = false;
        mark();
      };
      if (opts) inp.onchange = onEdit; else inp.oninput = onEdit;
      const note = el('span', 'muted');
      const mark = () => {
        const v = gunEdits[key] ?? cur[key];
        const s = stock[key];
        const changed = s !== undefined && Math.abs(v - s) > 1e-9;
        inp.style.borderColor = changed ? 'var(--gold)' : '';
        note.textContent = changed ? `stock ${s} — ${blurb}` : blurb;
      };
      f.append(lab, inp, note);
      box.append(f);
      mark();
    }
  }

  const other = Object.entries(cur).filter(([k]) => !gunData.groups
    .some(([, fs]) => fs.some(([fk]) => fk === k)));
  if (other.length) {
    const h = el('h3', null, 'Other fields (read-only here)');
    box.append(h);
    const t = el('table');
    for (const [k, v] of other) {
      const tr = el('tr');
      tr.append(el('td', 'muted', k), el('td', null, String(v)));
      t.append(tr);
    }
    box.append(t);
  }
}

async function saveGun() {
  if (!gunCurrent || !Object.keys(gunEdits).length) return;
  const name = gunCurrent.weapon.name;
  const everywhere = $('#gunEverywhere').checked;
  const body = { name, changes: gunEdits, everywhere };
  try {
    const dry = await post('gun_save', { ...body, dry: true });
    if (dry.failed.length) {
      return toast(
        `${dry.failed.length} of ${dry.archives} copies have no room:\n` +
        dry.failed.slice(0, 3).map((f) => `  ${f.archive}: ${f.error}`).join('\n') +
        `\nNothing was written. Try smaller numbers — fewer digits means fewer bytes.`,
        'bad');
    }
    const res = await post('gun_save', body);
    toast(res.failed.length
      ? `Applied to ${res.applied.length} copies, ${res.failed.length} failed.`
      : `${name} updated in all ${res.applied.length} copies on disc.`,
      res.failed.length ? 'bad' : 'good');
    gunData = null;
    await loadGuns();
    await openGun(name);
    $('#btnGunSave').disabled = true;
    refreshHistory();
  } catch (e) { toast('Apply failed: ' + e.message, 'bad'); }
}

// ------------------------------------------------------------- new weapon
//
// Sits with the rest of the weapons tab rather than in its own module, but
// it talks to the routes2 envelope, so it must NOT go through api()/post():
// those throw on any `error` key, and a routes2 refusal is a normal 200 with
// ok:false carrying the byte counts. Throwing them away is exactly how five
// written-out "won't fit" messages became unreachable.

async function post2(route, body) {
  let r;
  try {
    r = await fetch('/api/' + route, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  } catch {
    setOffline(true);
    return { ok: false, problem: { code: 'server_fault', fix: '', detail: '',
      message: 'The Studio is not answering — it may be restarting.' } };
  }
  setOffline(false);
  const j = await r.json();
  // A route that does not exist in this build still answers in the old shape.
  if (j && j.error && j.ok === undefined)
    return { ok: false, problem: { code: 'server_fault', message: String(j.error),
                                   fix: '', detail: String(j.error) } };
  return j;
}

let nwData = null;      // the GET payload: weapons, arsenals, name verdict
let nwPick = new Set(); // .ars files ticked
let nwTimer = 0;

function nwName() { return ($('#nwName') && $('#nwName').value || '').trim(); }

async function loadNewWeapon(name) {
  const box = $('#nwBody');
  if (!box) return;
  try {
    nwData = await api('new_weapon' + (name ? '?name=' + encodeURIComponent(name) : ''));
  } catch (e) {
    box.innerHTML = '';
    box.append(el('p', 'muted', 'Adding weapons is not in this build.'));
    $('#btnNwCheck').disabled = true;
    return;
  }
  renderNewWeapon();
}

function renderNewWeapon() {
  const box = $('#nwBody');
  box.innerHTML = '';
  if (!nwData) return;

  const intro = el('p', 'muted',
    `Adds weapon ${nwData.weapons.length + 1} as a clone: same stats, sounds and ` +
    `animations as the source, but its OWN model (a plain block to start), so ` +
    `importing a shape changes only this weapon.`);
  intro.title = `The rack holds ${nwData.weapons.length} weapons and the game ` +
    `looks them up by name, so this appends rather than overwriting one.`;
  intro.style.cssText = 'margin:0 0 12px;font-size:12px';
  box.append(intro);

  // -- name
  const fName = el('div', 'field');
  const lName = el('label', null, 'Name');
  lName.style.width = '86px';
  const iName = el('input');
  iName.id = 'nwName';
  iName.placeholder = 'Peacekeeper';
  iName.value = nwData.name || '';
  iName.maxLength = nwData.max_name;
  // The hint goes under the box, not beside it: beside it, a 340px column
  // left the name box itself about 50px wide, and 20px at half-width.
  const nName = el('span', 'muted');
  fName.style.flexWrap = 'wrap';
  iName.style.cssText = 'flex:1 1 140px;min-width:120px';
  nName.style.cssText = 'flex-basis:100%;font-size:11px;margin:2px 0 0 92px';
  fName.append(lName, iName, nName);
  box.append(fName);

  const verdict = () => {
    const v = iName.value.trim();
    if (!v) { nName.textContent = `letters, digits and _ , up to ${nwData.max_name}`;
              iName.style.borderColor = ''; return false; }
    if (nwData.name === v && !nwData.name_ok) {
      nName.textContent = nwData.name_why; iName.style.borderColor = 'var(--red)';
      return false;
    }
    if (nwData.name === v && nwData.taken) {
      nName.textContent = 'that name is already in the rack';
      iName.style.borderColor = 'var(--red)'; return false;
    }
    nName.textContent = `${v.length} of ${nwData.max_name} characters`;
    iName.style.borderColor = '';
    return true;
  };
  verdict();
  iName.oninput = () => {
    verdict();
    $('#btnNwWrite').disabled = true;
    // Re-cost the arsenals for the name actually typed. Most .ars files are
    // 27-30 bytes in a 32-byte slot, so room depends on the name — a
    // 5-character name fits 73 of the 100 and a 31-character one fits 39
    // (measured, vanilla disc). And it is not just the LENGTH: these files
    // are small enough that deflate's cost varies with the letters, so two
    // 11-character names can disagree about which arsenals fit (measured: at
    // 16 of the 29 usable lengths). Only the server can answer it.
    clearTimeout(nwTimer);
    nwTimer = setTimeout(() => loadNewWeapon(iName.value.trim()), 500);
  };

  // -- source
  const fSrc = el('div', 'field');
  const lSrc = el('label', null, 'Cloned from');
  lSrc.style.width = '86px';
  const sel = el('select');
  sel.id = 'nwSource';
  for (const w of nwData.weapons) {
    const o = el('option', null, w);
    o.value = w;
    if (w === (gunCurrent && gunCurrent.weapon.name) || w === nwData.source_default)
      o.selected = true;
    sel.append(o);
  }
  if (gunCurrent) sel.value = gunCurrent.weapon.name;
  sel.style.cssText = 'flex:1;min-width:0';
  sel.title = `Written to all ${nwData.copies} copies of swat.guns`;
  fSrc.append(lSrc, sel);
  box.append(fSrc);

  // -- own model / equip to Red
  const fOwn = el('div', 'field');
  const cOwn = el('input');
  cOwn.type = 'checkbox'; cOwn.id = 'nwOwn'; cOwn.checked = true;
  const lOwn = el('label');
  lOwn.style.cssText = 'display:flex;align-items:center;gap:6px;width:auto';
  lOwn.append(cOwn, document.createTextNode('Give it its own model'));
  lOwn.title = 'Adds <name>.mod (a plain block) to every archive that carries ' +
    'the source model. Untick to share the source\'s model (then importing ' +
    'a shape changes every weapon that uses it). Adding a file cannot be undone.';
  fOwn.append(lOwn);
  box.append(fOwn);
  const fRed = el('div', 'field');
  const cRed = el('input');
  cRed.type = 'checkbox'; cRed.id = 'nwRed'; cRed.checked = true;
  const lRed = el('label');
  lRed.style.cssText = 'display:flex;align-items:center;gap:6px;width:auto';
  lRed.append(cRed, document.createTextNode('Put it in Red\'s hand at the start'));
  lRed.title = 'Adds it as the FIRST line of player.ars, the weapon list the ' +
    'player starts with.';
  fRed.append(lRed);
  box.append(fRed);
  cOwn.onchange = cRed.onchange = () => { $('#btnNwWrite').disabled = true; };

  // -- shop
  const fShop = el('div', 'field');
  const cShop = el('input');
  cShop.type = 'checkbox';
  cShop.id = 'nwShop';
  const lShop = el('label');
  lShop.style.cssText = 'display:flex;align-items:center;gap:6px;width:auto';
  lShop.append(cShop, document.createTextNode('Sell it in the gun shop'));
  lShop.title = nwData.shop_file;
  fShop.append(lShop);
  box.append(fShop);

  // -- arsenals
  const h = el('h3', null, 'Who can carry it');
  h.style.cssText = 'margin:16px 0 4px';
  box.append(h);
  const room = nwData.arsenals.filter((a) => a.fits).length;
  const rp = el('p', 'muted',
    `${room} of ${nwData.arsenals.length} arsenals have room. A shorter name fits more.`);
  rp.style.cssText = 'margin:0;font-size:12px';
  rp.title = 'The rest are stored uncompressed in a 32-byte slot.';
  box.append(rp);

  const list = el('div');
  list.style.cssText = 'max-height:260px;overflow:auto;margin-top:6px';
  for (const a of nwData.arsenals) {
    const row = el('label', 'row');
    row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:2px 12px 2px 0';
    const c = el('input');
    c.type = 'checkbox';
    c.disabled = !a.fits;
    c.checked = nwPick.has(a.file) && a.fits;
    c.onchange = () => {
      if (c.checked) nwPick.add(a.file); else nwPick.delete(a.file);
      $('#btnNwWrite').disabled = true;
    };
    if (!a.fits) nwPick.delete(a.file);
    const nm = el('span', null, a.file.replace('.ars', ''));
    nm.style.cssText = 'flex:1;min-width:0';
    nm.title = a.holds.join(', ');
    const note = el('span', 'muted');
    note.style.cssText = 'font:var(--t-mono);font-size:11px';
    note.textContent = a.fits ? `${a.headroom} B spare`
                              : `over by ${a.stored - a.slot} B`;
    row.append(c, nm, note);
    list.append(row);
  }
  box.append(list);

  const tail = el('p', 'muted', 'Give it to the player in Missions ▸ Weapons.');
  tail.style.fontSize = '12px';
  box.append(tail);
}

function renderNwPlan(plan) {
  const box = $('#nwBody');
  const old = $('#nwPlan');
  if (old) old.remove();
  const wrap = el('div');
  wrap.id = 'nwPlan';
  wrap.style.cssText = 'margin-top:14px;border-top:1px solid var(--line,#333);padding-top:10px';
  wrap.append(el('h3', null, 'What it will write'));

  const by = new Map();
  for (const w of plan.writes) {
    const cur = by.get(w.file);
    if (!cur) by.set(w.file, { n: 1, stored: w.stored, slot: w.slot, headroom: w.headroom });
    else {
      cur.n += 1;
      if (w.headroom < cur.headroom) { cur.stored = w.stored; cur.slot = w.slot; cur.headroom = w.headroom; }
    }
  }
  const t = el('table');
  t.style.width = '100%';
  for (const [file, r] of by) {
    const tr = el('tr');
    const spare = el('td', 'muted', `${r.headroom} B spare`);
    if (r.headroom < 0) spare.style.color = 'var(--red)';
    tr.append(el('td', null, r.n > 1 ? `${file} × ${r.n}` : file),
              el('td', 'muted', `${r.stored} of ${r.slot}`), spare);
    t.append(tr);
  }
  wrap.append(t);
  const hasModel = plan.writes.some((w) => /\.mod$/.test(w.file));
  wrap.append(el('p', 'muted',
    `${plan.writes.length} file writes — ` + (hasModel
      ? 'the new model file is the one part Ctrl+Z cannot take back ' +
        '(Reset to stock does).'
      : 'Ctrl+Z takes the whole thing back.')));
  box.append(wrap);
  box.scrollTop = box.scrollHeight;
}

function nwBody(dry) {
  const ars = new Set(nwPick);
  const red = $('#nwRed') && $('#nwRed').checked;
  if (red) ars.add('player.ars');
  return { source: $('#nwSource').value, name: nwName(),
           arsenals: [...ars], shop: $('#nwShop').checked, dry,
           own_model: !$('#nwOwn') || $('#nwOwn').checked,
           first_in: red ? ['player.ars'] : [] };
}

function nwProblem(res) {
  const p = res.problem || {};
  const old = $('#nwPlan');
  if (old) old.remove();
  $('#nwStatus').textContent = '';
  toast([p.message, p.fix].filter(Boolean).join('\n'), 'bad');
  // A Refusal's extra fields ride at the TOP level of the body, not inside
  // `problem` — so a slot_overflow still shows what it would have cost.
  if (res.plan) renderNwPlan(res.plan);
}

async function checkNewWeapon() {
  if (!nwName()) return toast('Give the weapon a name first.', 'warn');
  $('#nwStatus').textContent = 'checking…';
  const res = await post2('weapon_clone', nwBody(true));
  if (!res.ok) { $('#btnNwWrite').disabled = true; return nwProblem(res); }
  renderNwPlan(res.plan);
  $('#nwStatus').textContent = 'fits';
  $('#btnNwWrite').disabled = false;
}

async function writeNewWeapon() {
  const name = nwName();
  $('#nwStatus').textContent = 'writing…';
  $('#btnNwWrite').disabled = true;
  // Dry-run again against the disc as it is NOW: the check may have been a
  // minute and another edit ago.
  const dry = await post2('weapon_clone', nwBody(true));
  if (!dry.ok) return nwProblem(dry);
  const res = await post2('weapon_clone', nwBody(false));
  if (!res.ok) return nwProblem(res);
  $('#nwStatus').textContent = '';
  toast(`${name} added in ${res.plan.writes.length} files` +
        (res.model ? ` with its own model ${res.model}. Pick it on the left, ` +
          'then import your shape in the Weapon model panel.' : '.'), 'good');
  nwPick.clear();
  gunData = null;
  await loadGuns();
  await openGun(name);
  await loadNewWeapon();
  refreshHistory();
}

// -------------------------------------------------------------- creatures

let crData = null;      // {creatures, groups}
let crCurrent = null;   // {name, fields, stock, copies}
let crEdits = {};
// Pending `.swt` combat edits, keyed by bare field name. Kept apart from
// crEdits because the two halves go to different files and different
// routes -- merging them would need every consumer to re-split them.
let swEdits = {};

async function loadCreatures() {
  if (!crData) crData = await api('creatures');
  const box = $('#crList');
  const filter = $('#crFilter').value.toLowerCase();
  box.innerHTML = '';
  for (const c of crData.creatures) {
    if (filter && !c.name.toLowerCase().includes(filter)) continue;
    const row = el('div', 'row');
    row.dataset.cr = c.name;
    row.append(el('span', 'nm', c.name),
               el('span', 'tag', `g ${c.gravity ?? '?'} · run ${c.run ?? '?'}`));
    row.onclick = () => openCreature(c.name);
    box.append(row);
  }
}

async function openCreature(name) {
  crCurrent = await api('creature?name=' + encodeURIComponent(name));
  crEdits = {};
  swEdits = {};
  for (const r of document.querySelectorAll('#crList .row'))
    r.classList.toggle('sel', r.dataset.cr === name);
  $('#crTitle').textContent = name;
  $('#crCopies').textContent = `${crCurrent.copies} copies on disc`;
  renderCrFields();
  showCreatureModel(name.replace(/\.cr$/i, ''));
  // The model-swap panel is in cast.js and has no other way to learn which
  // creature is open. An event rather than a function call, so a module that
  // failed to load simply never hears it.
  window.dispatchEvent(new CustomEvent('rdr:creature', { detail: name }));
}

// --------------------------------------------------------- mini 3D viewer
//
// A self-contained orbitable preview that any panel can host. Made a factory
// rather than a fourth hand-rolled scene: the creature tab, the asset
// library and the file browser all want the same thing, and each copy would
// otherwise re-solve framing, resize and disposal slightly differently.
//
// `show()` takes an /api/model response verbatim, so it renders per-part
// texture groups the same way the main viewport does.

function makeViewer(host, opts = {}) {
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xfff0d8, 0x2a2018, 2.0));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(2, 4, 3);
  scene.add(key);
  const cam = new THREE.PerspectiveCamera(
    42, Math.max(1, host.clientWidth) / Math.max(1, host.clientHeight),
    0.01, 40000);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(host.clientWidth || 1, host.clientHeight || 1, false);
  renderer.domElement.style.cssText = 'width:100%;height:100%;display:block';
  host.append(renderer.domElement);
  const ctl = new OrbitControls(cam, renderer.domElement);
  ctl.enableDamping = true;

  const grid = new THREE.GridHelper(4, 8, 0x4a3d2c, 0x332a1e);
  grid.material.transparent = true;
  grid.material.opacity = 0.35;
  scene.add(grid);

  let mesh = null, spin = opts.spin !== false, alive = true;
  const v = {
    scene, cam, renderer, controls: ctl,
    get object() { return mesh; },
    setSpin(on) { spin = on; },
    clear() {
      if (!mesh) return;
      scene.remove(mesh);
      mesh.geometry.dispose();
      mesh = null;
    },
    dispose() { alive = false; v.clear(); renderer.dispose();
                renderer.domElement.remove(); },

    // d: an /api/model payload. texUrl(i) -> a PNG url for texture slot i.
    show(d, texUrl) {
      v.clear();
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position',
        new THREE.Float32BufferAttribute(d.positions, 3));
      if (d.uvs?.length * 3 === d.positions.length * 2)
        geo.setAttribute('uv', new THREE.Float32BufferAttribute(d.uvs, 2));
      if (d.colors?.length === d.positions.length)
        geo.setAttribute('color', new THREE.Float32BufferAttribute(d.colors, 3));
      geo.setIndex(d.indices);
      geo.computeVertexNormals();

      const plain = new THREE.MeshStandardMaterial(
        { color: 0xc8bda8, roughness: 0.85, side: THREE.DoubleSide });
      let mat = plain;
      const groups = d.groups || [];
      if (texUrl && geo.getAttribute('uv') && groups.length) {
        const mk = (slot) => {
          if (slot == null) return plain;
          const t = new THREE.TextureLoader().load(texUrl(slot));
          t.colorSpace = THREE.SRGBColorSpace;
          t.flipY = false;
          t.wrapS = t.wrapT = THREE.RepeatWrapping;
          return new THREE.MeshBasicMaterial({ map: t, side: THREE.DoubleSide,
            transparent: true, alphaTest: 0.35 });
        };
        if (groups.length === 1) {
          mat = mk(groups[0].texture);
        } else {
          mat = groups.map((g) => mk(g.texture));
          groups.forEach((g, i) => geo.addGroup(g.start, g.count, i));
        }
      }
      mesh = new THREE.Mesh(geo, mat);
      scene.add(mesh);

      // Recentre on the model's own bounds and pull back far enough that it
      // fits with a margin, so a doorknob and a church each fill the panel.
      geo.computeBoundingSphere();
      const bs = geo.boundingSphere;
      mesh.position.sub(bs.center);
      const rad = Math.max(bs.radius, 1e-3);
      grid.scale.setScalar(rad / 2);
      const dist = rad / Math.sin((cam.fov * Math.PI / 180) / 2) * 1.15;
      cam.near = Math.max(rad / 1000, 0.001);
      cam.far = dist * 8;
      cam.position.set(dist * 0.62, dist * 0.42, dist * 0.62);
      cam.updateProjectionMatrix();
      ctl.target.set(0, 0, 0);
      ctl.update();
      return { verts: d.verts, tris: d.tris, parts: d.parts };
    },
  };

  (function loop() {
    if (!alive) return;
    requestAnimationFrame(loop);
    if (!host.isConnected || !host.clientWidth) return;
    if (host.offsetParent === null) return;      // panel is hidden
    const w = host.clientWidth, h = host.clientHeight;
    if (renderer.domElement.width !== Math.floor(w * renderer.getPixelRatio())) {
      renderer.setSize(w, h, false);
      cam.aspect = w / Math.max(1, h);
      cam.updateProjectionMatrix();
    }
    if (spin && mesh) mesh.rotation.y += 0.004;
    ctl.update();
    renderer.render(scene, cam);
  })();
  return v;
}

// ------------------------------------------------- creature model preview
//
// Its own small scene. The main viewport belongs to the level editor, and
// borrowing it would throw away whatever level was loaded there.

let crScene = null, crCam = null, crRenderer = null, crCtl = null;
let crMesh = null, crBones = null, crChars = null;

function crInit() {
  if (crRenderer) return true;
  const host = $('#crView');
  if (!host || !host.clientWidth) return false;
  crScene = new THREE.Scene();
  crScene.add(new THREE.HemisphereLight(0xfff0d8, 0x2a2018, 2.0));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(2, 4, 3);
  crScene.add(key);
  crCam = new THREE.PerspectiveCamera(
    40, host.clientWidth / host.clientHeight, 0.01, 100);
  crRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  crRenderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  crRenderer.setSize(host.clientWidth, host.clientHeight, false);
  host.append(crRenderer.domElement);
  crCtl = new OrbitControls(crCam, crRenderer.domElement);
  crCtl.enableDamping = true;
  (function loop() {
    requestAnimationFrame(loop);
    if (!$('#tab-creatures').classList.contains('on')) return;
    const w = host.clientWidth, h = host.clientHeight;
    if (w && h && crRenderer.domElement.clientWidth !== w) {
      crRenderer.setSize(w, h, false);
      crCam.aspect = w / h;
      crCam.updateProjectionMatrix();
    }
    crCtl.update();
    crRenderer.render(crScene, crCam);
  })();
  return true;
}

// The Blender bridge for characters. A character's mesh and its skeleton are
// separate files, and both are writable now -- the mesh through this OBJ
// round trip, the skeleton as plain text. Together that is custom characters
// without needing the animation format at all.
async function renderCharacterParts(entry, host, onImported) {
  // `host` lets this mount anywhere a character is on screen. It began as the
  // creature editor's private parts list, which meant exporting a body to
  // Blender required knowing that the Cast tab -- where characters are
  // actually chosen -- was not the place the tool put that button.
  const box = host || $('#crParts');
  if (!box) return;
  box.innerHTML = '';
  if (!entry) return;
  let d;
  try {
    d = await api(`character_parts?archive=${encodeURIComponent(entry.archive)}`
      + `&name=${encodeURIComponent(entry.file)}`);
  } catch (e) {
    box.innerHTML = `<div class="muted" style="font-size:11px">${e.message}</div>`;
    return;
  }
  // The whole character as the game's compiled bundle (.rsc + .rnt), for the
  // RDR Blender add-on -- beside the per-part OBJ round trip below, not
  // instead of it. Import goes into every copy of the bundle on the disc; an
  // archive with no room for it is rebuilt to make room. One Ctrl+Z.
  {
    const row = el('div');
    row.style.cssText = 'display:flex;align-items:center;gap:5px;padding:2px 0 6px;'
      + 'border-bottom:1px solid var(--line);margin-bottom:4px';
    const nm = el('span', '', 'Whole character (.rsc)');
    nm.style.cssText = 'flex:1;font-size:11px';
    nm.title = `${entry.file}: the compiled bundle with every part and texture, as the Blender add-on imports and exports it`;
    const down = el('button', '', '↓ .rsc');
    down.style.fontSize = '11px';
    down.title = 'Download the compiled bundle (a zip: .rsc, .rnt, and the .skel and .cr it uses) for the RDR Blender add-on: File > Import > Red Dead Revolver bundle';
    down.onclick = () => {
      saveFile(`/api/character_rsc?archive=${encodeURIComponent(entry.archive)}&name=${encodeURIComponent(entry.file)}`,
        `${entry.stem}_rsc.zip`).then((ok) => { if (ok) toast(`Exported ${entry.stem}_rsc.zip: unzip it and import the .rsc in Blender`, 'good'); });
    };
    const rfile = el('input');
    rfile.type = 'file';
    rfile.accept = '.rsc,.rnt,.xrsc,.xrnt';
    rfile.multiple = true;
    rfile.style.display = 'none';
    rfile.onchange = async () => {
      const files = [...rfile.files];
      rfile.value = '';
      const rscF = files.find((f) => /\.x?rsc$/i.test(f.name));
      const rntF = files.find((f) => /\.x?rnt$/i.test(f.name));
      if (rscF && /\.xrsc$/i.test(rscF.name) && !rntF) return toast('Pick the .xrnt with the .xrsc (both)', 'bad');
      if (!rscF) return toast('Pick the .rsc (and its .rnt) the Blender add-on exported', 'bad');
      const b64 = async (f) => { const u = new Uint8Array(await f.arrayBuffer()); let x = '';
        for (let i = 0; i < u.length; i += 0x8000) x += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(x); };
      const b = { name: entry.file, archive: entry.archive, rsc_b64: await b64(rscF), rsc_name: rscF.name,
                  rnt_b64: rntF ? await b64(rntF) : null, everywhere: true };
      try {
        const dry = await post('character_rsc_import', { ...b, dry: true });
        if (dry.ok === false) return toast(dry.error_text, 'bad');
        const n = dry.copies.length;
        if (!confirm(`Replace ${entry.file} with ${rscF.name}${rntF ? ' and ' + rntF.name : ''} in ${n} archive${n === 1 ? '' : 's'}?`
          + (rntF ? '' : '\n\nNo .rnt picked: the bundle keeps its old names file, which must still match the new bundle.')
          + (dry.rebuilds ? `\n\n${dry.rebuilds} of them have no room for it: they are rebuilt to make room on the next Rebuild and play.` : '')
          + '\n\nOne Ctrl+Z undoes it.')) return;
        toast(`Importing ${rscF.name} into ${n} archive(s)…`, 'good');
        const r = await post('character_rsc_import', b);
        if (r.ok === false) return toast(r.error_text, 'bad');
        for (const n of r.notes || []) toast(n, 'good');
        toast(`${entry.file}: replaced in ${r.copies.length} archive(s)` + (r.rebuilds ? `, ${r.rebuilds} queued to rebuild on the next launch` : '') + '. Ctrl+Z undoes it.', 'good');
        await refreshHistory();
        crChars = null;
        if (onImported) onImported(); else showCreatureModel(entry.stem);
        renderCharacterParts(entry, host, onImported);
      } catch (e) { toast(e.message, 'bad'); }
    };
    const up = el('button', '', '↑ .rsc');
    up.style.fontSize = '11px';
    up.title = 'Import a bundle exported by the RDR Blender add-on (pick the .rsc and its .rnt, or the Xbox .xrsc and .xrnt) over every copy of this character on the disc';
    up.onclick = () => rfile.click();
    row.append(nm, down, up, rfile);
    box.append(row);
  }
  for (const p of d.parts) {
    const row = el('div');
    row.style.cssText = 'display:flex;align-items:center;gap:5px;padding:2px 0';
    const nm = el('span', '', p.name);
    nm.style.cssText = 'flex:1;font-size:11px;overflow:hidden;'
      + 'text-overflow:ellipsis;white-space:nowrap';
    nm.title = `${p.verts} verts · ${p.tris} tris`
      + (p.step ? ` · ${p.step} m grid, ±${(p.range / 2).toFixed(1)} m range` : '');
    row.append(nm);

    const out = el('button', '', '↓ obj');
    out.style.fontSize = '11px';
    out.title = 'Export as OBJ for Blender. Move vertices only.';
    out.onclick = () => {
      saveFile(`/api/character_export?archive=${encodeURIComponent(entry.archive)}`
        + `&name=${encodeURIComponent(entry.file)}&chunk=${p.chunk}`,
        `${p.name}.obj`).then((ok) => { if (ok)
          toast(`Exported ${p.name}.obj`, 'good'); });
    };

    const file = el('input');
    file.type = 'file';
    file.accept = '.obj';
    file.style.display = 'none';
    file.onchange = async () => {
      const f = file.files[0];
      if (!f) return;
      const objText = await f.text();
      const b = { archive: entry.archive, name: entry.file, chunk: p.chunk,
                  obj: objText };
      try {
        const dry = await post('character_import', { ...b, dry: true });
        if (dry.saturated)
          toast(`Warning: some vertices are past the ±`
            + `${(p.range / 2).toFixed(1)} m limit and will clamp.`, 'bad');
        const r = await post('character_import', b);
        toast(`${p.name}: ${r.moved} of ${dry.vertices} vertices moved`
          + ` (within ${r.worst_error} m) — Ctrl+Z to undo`, 'good');
        await refreshHistory();
        crChars = null;
        // Redraw whichever preview this list is sitting under, so the edit is
        // visible where it was made -- the creature editor's stage when no
        // callback is given, the Cast pane's own preview when it mounted us.
        if (onImported) onImported(); else showCreatureModel(entry.stem);
      } catch (e) { toast(e.message, 'bad'); }
      file.value = '';
    };
    const imp = el('button', '', '↑ obj');
    imp.style.fontSize = '11px';
    imp.title = 'Import an edited OBJ onto this part';
    imp.onclick = () => file.click();
    // EXPERIMENTAL: a mesh with ANY vertex count. Skinning is copied from
    // the nearest original vertex. Not yet confirmed in-game.
    const mfile = el('input');
    mfile.type = 'file';
    mfile.accept = '.obj';
    mfile.style.display = 'none';
    mfile.onchange = async () => {
      const f = mfile.files[0];
      if (!f) return;
      const objText = await f.text();
      const b = { archive: entry.archive, name: entry.file, chunk: p.chunk,
                  obj: objText };
      try {
        const dry = await post('character_model_import', { ...b, dry: true });
        if (!dry.fits) { toast(`Too big: needs ${dry.stored} bytes, only `
          + `${dry.slot} available. Reduce the triangle count in Blender `
          + '(Decimate) and import again. Nothing was changed.', 'bad');
          mfile.value = ''; return; }
        if (!confirm(`Replace "${p.name}" with "${f.name}"?\n\n`
          + `${dry.triangles} triangles. ` + (dry.rebuild
            ? `It needs ${dry.stored} bytes and the original space is ${dry.slot}: the archive is rebuilt to make room on the next Rebuild and play.`
            : `Uses ${dry.stored} of ${dry.slot} bytes available.`)
          + '\n\nEXPERIMENTAL: each point is attached to the '
          + 'bones nearest to it on the original, so very different shapes '
          + 'may bend oddly. Shown working in PCSX2 on one character so far. Ctrl+Z undoes it.')) {
          mfile.value = ''; return; }
        const r = await post('character_model_import', b);
        toast((r.message || 'Imported.') + ' ' + (r.next || ''), 'good');
        await refreshHistory();
        crChars = null;
        if (onImported) onImported(); else showCreatureModel(entry.stem);
      } catch (e) { toast(e.message, 'bad'); }
      mfile.value = '';
    };
    const mimp = el('button', '', '↑ new mesh');
    mimp.style.fontSize = '11px';
    mimp.title = 'EXPERIMENTAL: replace this part with an OBJ of any size '
      + '(must fit the original space). Skinning is copied from the nearest '
      + 'original points. Shown working in PCSX2 on one character so far.';
    mimp.onclick = () => mfile.click();
    if (!p.editable) { out.disabled = imp.disabled = mimp.disabled = true; }
    row.append(out, imp, mimp, file, mfile);
    box.append(row);
  }
  if (!d.parts.length)
    box.innerHTML = '<div class="muted" style="font-size:11px">'
      + 'No editable model parts in this bundle.</div>';
}

async function showCreatureModel(stem, lod) {
  const note = $('#crViewNote');
  const info = $('#crModelInfo');
  if (!crChars) {
    try { crChars = (await api('characters')).characters; } catch { crChars = []; }
  }
  const entry = crChars.find((c) => c.stem === stem);
  if (!entry) {
    note.style.display = 'flex';
    note.textContent = `No model bundle named ${stem}_lods.rsc — this creature `
      + `reuses another character's model, or is not a visible actor.`;
    info.textContent = '';
    $('#crLod').innerHTML = '';
    if (crMesh && crScene) { crScene.remove(crMesh); crMesh = null; }
    if (crBones && crScene) { crScene.remove(crBones); crBones = null; }
    renderCharacterParts(null);
    return;
  }
  renderCharacterParts(entry);
  note.style.display = 'flex';
  note.textContent = 'Decoding…';
  if (!crInit()) return;

  let d;
  try {
    d = await api(`character?archive=${encodeURIComponent(entry.archive)}`
      + `&name=${encodeURIComponent(entry.file)}&lod=${lod || 0}`);
  } catch (e) {
    note.textContent = 'Could not decode: ' + e.message;
    return;
  }
  note.style.display = 'none';

  const sel = $('#crLod');
  if (sel.dataset.stem !== stem) {
    sel.innerHTML = '';
    for (let i = 0; i < d.lods; i++)
      sel.append(new Option(i === 0 ? 'LOD 0 (best)' : `LOD ${i}`, String(i)));
    sel.dataset.stem = stem;
  }
  sel.value = String(d.lod);

  if (crMesh) { crScene.remove(crMesh); crMesh = null; }
  if (crBones) { crScene.remove(crBones); crBones = null; }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(d.positions, 3));
  if (d.uvs.length * 3 === d.positions.length * 2)
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(d.uvs, 2));
  geo.setIndex(d.indices);
  geo.computeVertexNormals();
  // Use the bundle's own texture atlas when there is one -- a flat grey
  // figure hides exactly the detail you would be inspecting it for.
  let mat;
  if (d.textures && geo.getAttribute('uv')) {
    mat = characterMaterials(geo, d.groups, (i) => `/api/character.png?archive=${encodeURIComponent(entry.archive)}`
      + `&name=${encodeURIComponent(entry.file)}&i=${i}`);
  } else {
    mat = new THREE.MeshStandardMaterial(
      { color: 0xc8bda8, roughness: 0.85, side: THREE.DoubleSide });
  }
  crMesh = new THREE.Mesh(geo, mat);
  crScene.add(crMesh);

  // Draw the skeleton too, so a wrong decode is obvious rather than merely
  // ugly: the bone dots have to sit inside the body.
  if (d.bones?.length) {
    const pts = [];
    for (const b of d.bones) pts.push(b.pos[0], b.pos[1], b.pos[2]);
    const bg = new THREE.BufferGeometry();
    bg.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    crBones = new THREE.Points(bg, new THREE.PointsMaterial(
      { color: 0xffb454, size: 0.03 }));
    crScene.add(crBones);
  }

  // Frame from the front, far enough back that the whole figure fits with a
  // margin. The distance comes from the vertical extent and the camera's own
  // field of view, so a horse and a hat each fill the panel sensibly instead
  // of one overflowing and the other being a speck.
  geo.computeBoundingBox();
  const bb = geo.boundingBox;
  const mid = bb.getCenter(new THREE.Vector3());
  const size = bb.getSize(new THREE.Vector3());
  const extent = Math.max(size.y, size.x, 0.2);
  const fov = THREE.MathUtils.degToRad(crCam.fov);
  const dist = (extent / 2) / Math.tan(fov / 2) * 1.45;
  crCtl.target.copy(mid);
  // Characters are modelled facing -Z, so the camera goes on that side or
  // every preview is of someone's back.
  crCam.position.set(mid.x, mid.y + extent * 0.05, mid.z - dist);
  crCtl.update();

  info.innerHTML =
    `<b>${d.stem}</b> — ${d.verts.toLocaleString()} verts, `
    + `${d.tris.toLocaleString()} tris<br>`
    + (d.skinned ? `skinned to <b>${d.bone_count}</b> bones`
                 : 'rigid (no skeleton)')
    + (d.bones?.length ? ` · ${d.bones.length} in the .skel` : '')
    + `<br>${d.textures} texture(s) in the bundle`
    + `<br><span style="opacity:.7">Height `
    + `${(bb.max.y - bb.min.y).toFixed(2)} m — drag to orbit, scroll to zoom.`
    + `</span>`;
}

function renderCrFields() {
  const box = $('#crFields');
  box.innerHTML = '';
  if (!crCurrent) return;
  for (const [group, fields] of crData.groups) {
    const h = el('h3', null, group);
    box.append(h);
    for (const [path, blurb] of fields) {
      if (!(path in crCurrent.fields)) continue;
      const f = el('div', 'wrow');
      const lab = el('label', null, path.split('.').pop());
      lab.title = path;
      const inp = el('input');
      inp.value = crEdits[path] ?? crCurrent.fields[path];
      inp.oninput = () => {
        const v = parseFloat(inp.value);
        if (Number.isNaN(v)) return;
        crEdits[path] = v;
        $('#btnCrSave').disabled = false;
        mark();
      };
      const note = el('span', 'muted');
      const mark = () => {
        const v = crEdits[path] ?? crCurrent.fields[path];
        const stk = crCurrent.stock[path];
        const changed = stk !== undefined && Math.abs(v - stk) > 1e-9;
        inp.style.borderColor = changed ? 'var(--gold)' : '';
        note.textContent = changed ? `stock ${stk} — ${blurb}` : blurb;
      };
      f.append(lab, inp, note);
      box.append(f);
      mark();
    }
  }
  renderCombatFields(box);
}

// The combat sheet, from the character's `.swt`. Rendered into the same panel
// as the `.cr` fields because "how tough is this character" is one question
// and the game splits the answer across two files -- `.cr` has hit points and
// movement, `.swt` has every damage number and no health field at all.
// Editing them separately means knowing that split exists.
function renderCombatFields(box) {
  const groups = crCurrent?.combat;
  if (!groups || !groups.length) return;

  const hr = el('div');
  hr.style.cssText = 'border-top:1px solid var(--line);margin:20px 0 0';
  box.append(hr);
  const head = el('h3', null, 'Combat');
  head.style.cssText = 'margin:14px 0 2px';
  box.append(head);
  const sub = el('div', 'muted');
  sub.style.cssText = 'font-size:11px;margin-bottom:8px';
  sub.textContent = `from ${crCurrent.combat_file} · `
    + `${crCurrent.combat_copies} cop${crCurrent.combat_copies === 1 ? 'y' : 'ies'}`
    + ' on the disc, all written together as one undo step';
  box.append(sub);

  for (const g of groups) {
    const h = el('h4', null, g.label);
    h.style.cssText = 'margin:12px 0 5px;font-size:12px;opacity:.85';
    box.append(h);
    for (const row of g.fields) {
      const f = el('div', 'field');
      f.style.marginBottom = '7px';
      const lab = el('label', null, row.field);
      lab.style.width = '170px';
      lab.title = row.path;
      const inp = el('input');
      inp.value = swEdits[row.field] ?? row.value;
      const note = el('span', 'muted');
      note.style.cssText = 'width:260px; font-size:11px';
      const mark = () => {
        const v = swEdits[row.field] ?? row.value;
        const stk = crCurrent.combat_stock?.[row.field];
        // Compared as NUMBERS where both look numeric -- "100.000000" and
        // "100" are the same value and flagging that as an edit would light
        // up the whole panel. Text fields fall back to a string compare.
        const a = parseFloat(v), b = parseFloat(stk);
        const changed = stk !== undefined
          && (Number.isNaN(a) || Number.isNaN(b) ? String(v) !== String(stk)
                                                 : Math.abs(a - b) > 1e-9);
        inp.style.borderColor = changed ? 'var(--gold)' : '';
        note.textContent = changed ? `stock ${stk} — ${row.hint}` : row.hint;
      };
      inp.oninput = () => {
        const v = inp.value.trim();
        if (row.kind !== 'text' && v !== '' && Number.isNaN(parseFloat(v))) return;
        swEdits[row.field] = v;
        $('#btnCrSave').disabled = false;
        mark();
      };
      f.append(lab, inp, note);
      box.append(f);
      mark();
    }
  }
}

async function saveCreature() {
  if (!crCurrent) return;
  const hasCr = Object.keys(crEdits).length > 0;
  const hasSw = Object.keys(swEdits).length > 0;
  if (!hasCr && !hasSw) return;

  const name = crCurrent.name;
  const everywhere = $('#crEverywhere').checked;
  // Two files, two routes. Both are dry-run FIRST and neither is written
  // unless both would fit -- a half-applied character (new damage, stock
  // health) is a worse outcome than a refusal, and it is the outcome you get
  // if you write the first file and then discover the second has no room.
  const jobs = [];
  if (hasCr) jobs.push({ route: 'creature_save', file: crCurrent.name, changes: crEdits });
  if (hasSw) jobs.push({ route: 'combat_save', file: crCurrent.combat_file, changes: swEdits });

  try {
    for (const j of jobs) {
      const dry = await post(j.route, {
        name: j.file, changes: j.changes, everywhere, dry: true });
      if (dry.failed.length) {
        return toast(
          `${j.file}: ${dry.failed.length} of ${dry.archives} copies have no room:\n` +
          dry.failed.slice(0, 3).map((f) => `  ${f.archive}: ${f.error}`).join('\n') +
          `\nNothing was written.`, 'bad');
      }
      j.copies = dry.archives;
    }
    let wrote = 0;
    // The first write opens the journal group and each later one joins it,
    // so a save that touches both the .cr and the .swt is ONE Ctrl+Z --
    // the promise the banner above the fields makes.
    let group = null;
    for (const j of jobs) {
      const res = await post(j.route, {
        name: j.file, changes: j.changes, everywhere, group });
      group = res.group ?? group;
      wrote += res.applied.length;
    }
    toast(`${name} updated — ${wrote} file cop${wrote === 1 ? 'y' : 'ies'} across `
          + `${jobs.length === 2 ? 'the .cr and .swt' : jobs[0].file}.\n`
          + `Ctrl+Z takes it all back. Load a level in PCSX2 to confirm it.`, 'good');
    crData = null;
    await loadCreatures();
    await openCreature(name);
    $('#btnCrSave').disabled = true;
    refreshHistory();
  } catch (e) { toast('Apply failed: ' + e.message, 'bad'); }
}

// -------------------------------------------------------------- capacity

async function loadCapacity(archive) {
  const tb = $('#capTable tbody');
  tb.innerHTML = '<tr><td colspan="8" class="muted">reading…</td></tr>';
  const d = await api('capacity?archive=' + encodeURIComponent(archive));
  tb.innerHTML = '';
  let totalRoom = 0;
  for (const r of d.files) {
    totalRoom += r.approx_text;
    const tr = el('tr');
    const pct = Math.min(100, (r.stored / r.slot) * 100);
    const meter = el('td');
    const bar = el('div', 'meter');
    const fill = el('i');
    fill.style.width = pct + '%';
    if (r.approx_text < 200) fill.className = 'over';
    bar.append(fill);
    meter.append(bar);
    tr.append(el('td', null, r.file),
              el('td', 'muted', r.kinds.join(', ') || '—'),
              el('td', 'num', String(r.objects)),
              el('td', 'num', r.raw.toLocaleString()),
              el('td', 'num', r.stored.toLocaleString()),
              el('td', 'num', r.slot.toLocaleString()),
              el('td', 'num ' + (r.approx_text > 500 ? 'good' : 'warn'),
                 '~' + r.approx_text.toLocaleString() + 'B'));
    tr.append(meter);
    tr.onclick = () => openTextFile(archive, r.file);
    tb.append(tr);
  }
  $('#capInfo').textContent =
    `${d.files.length} editable files · roughly ${totalRoom.toLocaleString()} bytes of room total`;
}

// ----------------------------------------------------------- checkpoints

async function loadCheckpoints() {
  const box = $('#cpList');
  if (!box) return;   // the checkpoint list moved to Versions (final pass 11/10)
  box.innerHTML = '';
  let cps;
  try { cps = await api('checkpoints'); } catch { return; }
  if (!cps.length) {
    box.append(el('div', 'muted', 'No restore points yet.'));
    return;
  }
  for (const c of cps) {
    const row = el('div', 'row');
    row.style.cssText = 'display:block;padding:6px 8px';
    row.append(el('div', null, c.name || c.file));
    row.append(el('div', 'muted',
      `${c.created} · ${c.files} file(s) · ${(c.bytes / 1024).toFixed(0)} KB`));
    // Destructive at rest, not only under the pointer: this reverts every
    // change made since the checkpoint was taken.
    // Neutral at rest (DESIGN-REVIEW A7); the confirm says what it replaces.
    const b = el('button', '', 'Restore');
    b.style.marginTop = '4px';
    b.onclick = async () => {
      if (!confirm(`Restore "${c.name}"?\n\nThe disc goes back exactly to how ` +
                   `it was then. Anything changed since that isn't in this ` +
                   `checkpoint is reverted to stock. Undoable with Ctrl+Z.`)) return;
      b.disabled = true;
      try {
        const r = await post('checkpoint_restore', { path: c.path });
        toast(`Restored "${r.name}" — ${r.applied} file(s) applied, ` +
              `${r.reverted} reverted to stock.`, r.failed.length ? 'bad' : 'good');
        if (level) await reloadLevel(true, true);
        refreshHistory();
      } catch (e) { toast('Restore failed: ' + e.message, 'bad'); }
      b.disabled = false;
    };
    row.append(b);
    box.append(row);
  }
}

// ------------------------------------------------------------------ mods

async function loadMods() {
  const box = $('#modList');
  if (!box) return;   // saved mods are listed by Versions (final pass 11/10)
  box.innerHTML = '';
  const mods = await api('mods');
  if (!mods.length) {
    box.append(el('div', 'empty', 'No saved mods'));
    return;
  }
  for (const m of mods) {
    const row = el('div', 'row');
    row.style.cssText = 'display:block; padding:8px 10px';
    row.append(el('div', null, m.name || m.file));
    row.append(el('div', 'muted',
      `${m.files} file(s) · ${m.created || '?'}${m.notes ? ' · ' + m.notes : ''}`));
    const b = el('button', '', 'Load onto my disc');
    b.style.marginTop = '6px';
    b.onclick = async (ev) => {
      ev.stopPropagation();
      b.disabled = true;
      try {
        const r = await post('mod_load', { path: m.path });
        toast(r.failed.length
          ? `Applied ${r.applied.length}, but ${r.failed.length} file(s) did not fit.`
          : `Applied "${r.name}" — ${r.applied.length} file(s).`,
          r.failed.length ? 'bad' : 'good');
        if (level) await reloadLevel(true, true);
        refreshHistory();
      } catch (e) { toast('Apply failed: ' + e.message, 'bad'); }
      b.disabled = false;
    };
    row.append(b);
    box.append(row);
  }
}

// -------------------------------------------------------------- level list

let levelCatalog = [];

async function loadLevels() {
  if (!levelCatalog.length) levelCatalog = await api('levels');
  const filter = ($('#levelFilter')?.value || '').toLowerCase();
  // Match on name, mode label and mission number, so "showdown", "bridge"
  // and "mission 6" all find what you would expect.
  const list = levelCatalog.filter((l) => !filter
    || l.name.toLowerCase().includes(filter)
    || l.mode_label.toLowerCase().includes(filter)
    || (l.town || '').toLowerCase().includes(filter)
    || (l.missions || []).some((m) => String(m.number) === filter
        || `mission ${m.number}`.includes(filter)
        || (m.character || '').toLowerCase() === filter));
  const box = $('#levelList');
  box.innerHTML = '';
  if (!list.length) {
    box.append(el('div', 'pad muted', 'no levels match'));
    return;
  }
  let mode = null;
  for (const l of list) {
    if (l.mode !== mode) {
      mode = l.mode;
      const n = list.filter((x) => x.mode === mode).length;
      const h = el('div', 'pad');
      h.style.cssText = 'font-size:11px; letter-spacing:.5px; color:var(--gold);' +
                        'border-top:1px solid var(--line); margin-top:4px';
      h.textContent = `${l.mode_label}  (${n})`;
      box.append(h);
    }
    const row = el('div', 'row');
    // Say what the level IS: campaign mission number, or Showdown variant.
    let tag = '';
    if (l.missions?.length) tag = `mission ${l.missions.map((m) => m.number).join(', ')}`;
    if (l.town) tag += ` · ${l.town}`;
    else if (l.mode === 'custom') tag = (l.kind === 'showdown' ? 'Showdown ' + (l.players || '') : 'single player').trim();
    else if (l.mode === 'showdown2') tag = '2P';
    else if (l.mode === 'showdown4') tag = '4P';
    else if (l.mode === 'bonus') tag = 'no mission';
    if (l.unlock && l.unlock.criteria && l.unlock.criteria !== 'NONE')
      tag += ' · locked';
    row.title = [l.archive,
                 l.missions?.length
                   ? l.missions.map((m) => `mission ${m.number} as ${m.character}`).join('; ')
                   : '',
                 l.unlock ? `unlock: ${l.unlock.criteria} ${l.unlock.value}` : '',
    ].filter(Boolean).join('\n');
    row.append(el('span', 'nm', l.name), el('span', 'tag', tag));
    row.onclick = async () => {
      for (const r of box.querySelectorAll('.row')) r.classList.remove('sel');
      row.classList.add('sel');
      row.querySelector('.nm').textContent = l.name + ' …';
      try {
        buildLevel(await api('level?' + levelQuery(l.archive, null)));
      } catch (e) { toast('Failed to load: ' + e.message, 'bad'); }
      row.querySelector('.nm').textContent = l.name;
    };
    box.append(row);
  }
}

// The archive, the manifest that selects a dev level, and the area. An
// archive can hold several playable areas -- Brimstone's holds the town plus
// six interiors -- so every request that reads geometry must say which one it
// means, or it quietly returns a different level's world.
function levelQuery(archive, manifest, area) {
  // Only inherit the loaded level's area when the request IS for that level.
  // Inheriting it unconditionally leaked the PREVIOUS level's area into the
  // next one -- asking SALOON.DAT for `lvl_perfnew.rsc`, which it does not
  // contain -- so the server found no bundle and the level came back with no
  // surfaces at all until the page was reloaded by hand.
  const a = area !== undefined ? area
    : (level && level.archive === archive ? level.area : null);
  return 'archive=' + encodeURIComponent(archive)
    + (manifest ? '&manifest=' + encodeURIComponent(manifest) : '')
    + (a ? '&area=' + encodeURIComponent(a) : '');
}

async function loadLevelByRef(archive, manifest, area) {
  try {
    buildLevel(await api('level?' + levelQuery(archive, manifest, area || null)));
  } catch (e) { toast('Failed to load: ' + e.message, 'bad'); }
}

// Whole level out to a model set, for another toolchain. NOT the same thing
// as the Inspector's per-surface "Export .obj", which exists so you can
// reshape one surface and bring it back; nothing here comes back.
$('#btnEdPanel').onclick = () => {
  $('#tab-editor').classList.toggle('edpanel');
  resize();
};

// The options live in a small dialog (DESIGN-REVIEW B9); the export itself
// is unchanged.
$('#btnLevelExport').onclick = () => {
  if (!level?.archive) return toast('Load a level first.', 'bad');
  $('#lvlExportDlg').style.display = 'flex';
};
$('#btnLevelExportCancel').onclick = () => { $('#lvlExportDlg').style.display = 'none'; };
$('#lvlExportDlg').onclick = (e) => {
  if (e.target === e.currentTarget) e.currentTarget.style.display = 'none';
};
$('#btnLevelExportGo').onclick = async () => {
  $('#lvlExportDlg').style.display = 'none';
  const arc = level?.archive;
  if (!arc) return toast('Load a level first.', 'bad');
  const btn = $('#btnLevelExport');
  const was = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Exporting…';
  // Optional-chained on purpose: these two inputs live in #lvlExportRow, and
  // a lookup of an element that is not there has taken the whole front end
  // down twice. A missing checkbox has to degrade to "off", not to a throw.
  const col = $('#lvlExportCol')?.checked ? '1' : '0';
  const glb = $('#lvlExportGlb')?.checked ? '1' : '0';
  // "auto" spreads sibling areas apart because on the disc interiors sit
  // inside the exterior's box; "native" hands back the stated coordinates.
  const layout = $('#lvlExportNative')?.checked ? 'native' : 'auto';
  const stem = arc.replace(/[\/\\]/g, '_').replace(/\.DAT$/i, '');
  const fmt = $('#lvlExportFormat')?.value || 'model';
  if (fmt !== 'model') {
    // the level's own files, for the PS2 (.rsc) or made the Xbox's (.xrsc)
    try {
      const name = stem + (fmt === 'xrsc' ? '_xbox' : '_ps2') + '.zip';
      if (await saveFile(`/api/level_export?archive=${encodeURIComponent(arc)}&format=${fmt}`, name))
        toast(`Exported ${name}: the level's files for the ${fmt === 'xrsc' ? 'Xbox' : 'PS2'}.`, 'good');
    } finally { btn.disabled = false; btn.textContent = was; }
    return;
  }
  // Every area in the archive, not just the loaded one -- the point of this
  // button is the whole map, and the switcher above only changes what the
  // VIEWPORT is showing.
  const n = level?.areas?.length || 1;
  if (n > 1) toast(`Exporting all ${n} areas of ${stem}…`);
  try {
    const ok = await saveFile(
      `/api/level_export?archive=${encodeURIComponent(arc)}` +
      `&collision=${col}&glb=${glb}&layout=${layout}`,
      stem + '.zip');
    if (ok) {
      toast(`Exported ${stem}.zip — ${n > 1 ? n + ' areas, ' : ''}` +
            `open the .obj in Blender.`, 'good');
    }
  } finally {
    btn.disabled = false;
    btn.textContent = was;
  }
};

// A Showdown map's name and sub text in the Showdown menu (rdrstudio/dataeditor_more.py level_name).
async function refreshLevelName() {
  const row = $('#lvlNameRow');
  if (!row) return;
  const arc = level?.archive || '';
  if (!/^LVLS_M[24]\//i.test(arc)) { row.style.display = 'none'; return; }
  try {
    const r = await post('de_x_level_name', { archive: arc });
    if ((level?.archive || '') !== arc) return;
    if (!r.found) { row.style.display = 'none'; return; }
    $('#lvlNameTitle').value = r.title || '';
    $('#lvlNameSub').value = r.subtitle || '';
    row.style.display = '';
  } catch { row.style.display = 'none'; }
}
if ($('#lvlNameSave')) $('#lvlNameSave').onclick = async () => {
  const arc = level?.archive;
  if (!arc) return;
  try {
    const r = await post('de_x_level_name_set', { archive: arc, title: $('#lvlNameTitle').value, subtitle: $('#lvlNameSub').value });
    toast(`Showdown menu: "${r.title}" / "${r.subtitle}". Press Build to put it on the disc.`, 'good');
    refreshDiscState();
  } catch (e) { toast('Could not save the name: ' + e.message, 'bad'); }
};

// Sibling areas of the loaded archive, as a switcher.
function renderAreas() {
  const head = $('#areaHead');
  const box = $('#areaList');
  const areas = level?.areas || [];
  const show = areas.length > 1;
  // Whether the section EXISTS is this function's call; whether it is folded
  // open is the fold's. Setting the body's display here unconditionally
  // would silently re-open a section the user had collapsed.
  head.style.display = show ? '' : 'none';
  if (!show) { box.style.display = 'none'; return; }
  box.style.display = head.classList.contains('open') ? '' : 'none';
  box.innerHTML = '';
  for (const a of areas) {
    const b = el('button', a.rsc === level.area ? 'primary' : '', a.name);
    b.style.cssText = 'margin:0 4px 4px 0;font-size:11px;padding:3px 8px';
    b.onclick = async () => {
      if (a.rsc === level.area) return;
      toast('Loading ' + a.name + '…');
      await loadLevelByRef(level.archive, level.manifest, a.rsc);
    };
    box.append(b);
  }
}

// `heavy` forces the world and its textures to be re-decoded as well. Only a
// texture import needs that; a move, create or delete changes placement files
// only, and the world is exactly as it was.
async function reloadLevel(quiet, heavy) {
  if (!level) return;
  const key = (level.archive || '') + '|' + (level.area || '');
  const keep = {
    pos: camera.position.clone(), t: controls.target.clone(),
    sel: [...selection], primary: selected, chunk: chunkSel,
  };
  const data = await api('level?' + levelQuery(level.archive, level.manifest) + '&reload=1');
  // The user switched levels while this refetch was away. Rebuilding from
  // this response would snap the app back to the level they just left --
  // the click that "didn't really work".
  if (key !== builtLevelKey) return;
  buildLevel(data, !heavy && !!renderInfo);
  camera.position.copy(keep.pos);
  controls.target.copy(keep.t);
  controls.update();
  // uids are positional, so only restore selections that still exist.
  const alive = keep.sel.filter((u) => meshes.has(u));
  if (alive.length) {
    select(alive[0]);
    for (const u of alive.slice(1)) select(u, true);
    if (keep.primary != null && meshes.has(keep.primary)) {
      selected = keep.primary;
      // Through syncSelectionUI, not gizmo.attach: this runs after every
      // reshape, and attaching the node proxy directly would drop the handle
      // back at the world origin the moment you finished dragging a building.
      syncSelectionUI(false);
    }
  } else if (keep.chunk != null) {
    // A surface with no node behind it. There is no uid to restore, so it is
    // re-opened by chunk -- otherwise the handle vanished the instant you
    // finished dragging one, which is exactly when you want it back.
    showInspectorChunk(keep.chunk);
  }
  if (!quiet) toast('Level reloaded');
}

// ---------------------------------------------------------------- archives

let archiveList = [];

async function loadArchives() {
  archiveList = await api('archives');
  renderArchives();
}

// 66 rows, and this was the only list in the app with no filter box.
function renderArchives() {
  const box = $('#arcList');
  const q = ($('#arcFilter')?.value || '').toLowerCase().trim();
  box.innerHTML = '';
  for (const a of archiveList) {
    if (q && !a.archive.toLowerCase().includes(q)) continue;
    const row = el('div', 'row');
    row.append(el('span', 'nm', a.archive),
               el('span', 'tag', a.count.toLocaleString()));
    row.title = a.archive;
    // Whether this archive can be REPACKED, which is what decides whether you
    // can add content to it rather than only edit what is there. Three states,
    // and "untested" is deliberately shown rather than rounded to either
    // answer — reporting untested as safe is exactly the mistake that got the
    // all-archive repack called proven before it had been played.
    // DESIGN-REVIEW B5: the name was truncated so that "repack" and an
    // UNTESTED pill could repeat on 64 rows. Untested is the norm now and is
    // said once, in the legend under the filter; only the exceptions carry a
    // marker -- verified (dot) and known to freeze (dash).
    if (a.repack_status && a.repack_status !== 'untested') {
      const rung = a.repack_status === 'verified' ? 'measured' : 'dead';
      row.append(confBadge(rung, rung === 'measured'
        ? 'repack verified in game'
        : 'repack freezes the game (verified)'));
    }
    row.onclick = () => {
      for (const r of box.querySelectorAll('.row')) r.classList.remove('sel');
      row.classList.add('sel');
      loadToc(a.archive);
    };
    box.append(row);
  }
  if (!box.children.length)
    box.innerHTML = '<p class="muted" style="padding:8px;font-size:11px">'
      + 'No archive matches that.</p>';
}

let currentArc = null;
async function loadToc(archive) {
  currentArc = archive;
  const q = $('#tocFilter').value.trim();
  const data = await api(`toc?archive=${encodeURIComponent(archive)}&q=${encodeURIComponent(q)}`);
  $('#tocInfo').textContent =
    `${archive} — showing ${data.rows.length} of ${data.count} matching (${data.total} total)`;
  const tb = $('#tocTable tbody');
  tb.innerHTML = '';
  $('#tocEmpty')?.remove();
  for (const r of data.rows) {
    const tr = el('tr');
    const flags = [r.compressed ? 'zlib' : 'raw', r.aligned ? 'aligned' : ''].filter(Boolean).join(' ');
    for (const [v, cls] of [[r.name], [r.ext], [r.size, 'num'], [r.stored, 'num'],
                            [r.slot, 'num'],
                            [r.headroom, 'num ' + (r.headroom <= 0 ? 'warn' : 'good')],
                            [flags]]) {
      tr.append(el('td', cls, typeof v === 'number' ? v.toLocaleString() : v));
    }
    tr.onclick = () => {
      for (const x of tb.querySelectorAll('tr')) x.classList.remove('sel');
      tr.classList.add('sel');
      openTextFile(archive, r.name);
    };
    tb.append(tr);
  }
}

// ------------------------------------------------------------- text editor

async function openTextFile(archive, name) {
  try {
    const f = await api(`file?archive=${encodeURIComponent(archive)}&name=${encodeURIComponent(name)}`);
    showTab('text');
    openText = f;
    $('#textEmpty')?.remove();
    if ($('#fitMeter')) $('#fitMeter').style.display = '';
    for (const id of ['#btnExport', '#btnImport', '#btnCheck'])
      if ($(id)) $(id).disabled = false;
    $('#textTitle').textContent =
      `${archive} :: ${name}  (${f.kind}, ${f.size} bytes, slot ${f.slot})`;
    const ta = $('#textArea');
    const sel = $('#binView');
    sel.style.display = (f.kind === 'binary' || f.tree) ? '' : 'none';
    if (f.tree) {
      sel.innerHTML = '';
      for (const [v, t] of [['text', 'Text (editable)'], ['tree', 'Bone tree']]) {
        const o = el('option', null, t);
        o.value = v;
        sel.append(o);
      }
      sel.value = 'text';
    } else if (f.kind === 'binary') {
      sel.innerHTML = '';
      const views = [['strings', 'Strings'], ['hex', 'Hex']];
      // An animation is no longer opaque -- fetch the decoded curves and offer
      // them as the default view, because they are the point of the file.
      if (/\.anim$/i.test(name)) {
        try {
          f.decoded = await api(
            `anim?archive=${encodeURIComponent(archive)}&name=${encodeURIComponent(name)}`);
          views.unshift(['curves', 'Curves (decoded)']);
        } catch { /* fall back to strings/hex */ }
      }
      for (const [v, t] of views) {
        const o = el('option', null, t);
        o.value = v;
        sel.append(o);
      }
      sel.value = views[0][0];
    }

    if (f.kind === 'text') {
      ta.value = f.text;
      ta.readOnly = false;
      $('#btnSaveText').disabled = false;
    } else if (f.kind === 'skeleton') {
      ta.value =
        `Skeleton — version ${f.version}, ${f.bones.length} bones` +
        (f.stale_header
          ? `\nNote: the file's own NumBones header says ${f.declared}. ` +
            `That header is stale in the shipped game — bones were added ` +
            `after it was written. ${f.bones.length} is the real count.`
          : '') +
        `\n\nIndented by hierarchy. "offset" is relative to the parent, ` +
        `"world" is accumulated. T/R/S = which translate, rotate and scale ` +
        `channels animation may drive.\n\n` + f.listing;
      ta.readOnly = true;
      $('#btnSaveText').disabled = true;
    } else if (f.kind === 'behaviour') {
      // This used to say opcode meanings are not known, full stop. Half of
      // that is now solved and half is not, and the two halves have to be
      // separated or the panel is simply wrong: the CONDITIONS carry real
      // names out of a 257-entry vocabulary compiled into SLUS_205.00, while
      // no ACTION name ships on this disc at all. The second sentence is
      // driven off the payload's own flag rather than hardcoded, so it
      // self-corrects if that ever changes.
      const s = f.summary || {};
      const named = s.action_names_available === true;
      const bits = [];
      if (s.conditions != null) {
        bits.push(`${s.conditions} conditions`
          + ((s.distinct_conditions || []).length
              ? ` (${s.distinct_conditions.slice(0, 6).join(', ')})` : ''));
      }
      if (s.action_opcodes) bits.push(`${s.action_opcodes.length} action opcodes`);
      if (s.actions_without_handler) {
        bits.push(`${s.actions_without_handler.length} that the retail build `
                  + 'ignores');
      }
      ta.value =
        `Behaviour table.\n` +
        `The CONDITIONS are named: they come from a 257-entry vocabulary ` +
        `compiled into SLUS_205.00, so "dist" below is the game's own word ` +
        `for it, on its own id.\n` +
        (named
          ? `The ACTIONS are named too.\n`
          : `The ACTIONS are not. The action names never shipped on the disc, ` +
            `only their numbers — they lived in the authoring toolchain. ` +
            `Float parameters are safe to retune; control flow still is not ` +
            `authorable.\n`) +
        `\n` +
        (bits.length ? bits.join(' · ') + '\n' : '') +
        `${s.floats} float parameters · ${s.ints} opcodes/indices` +
        `${s.unresolved_text ? ` · ${s.unresolved_text} words that are probably text` : ''}\n` +
        ((s.unnamed_names || []).length
          ? `plain names — states, tables, animations, all normal file ` +
            `content: ${s.unnamed_names.slice(0, 12).join(', ')}\n` : '') +
        `\nA bare integer in the action range can also be plain data (about ` +
        `ten values on the whole disc demonstrably are), so an opcode row is ` +
        `a hint, not a certainty.\n\n` +
        f.listing;
      ta.readOnly = true;
      $('#btnSaveText').disabled = true;
    } else {
      renderBinary();
      ta.readOnly = true;
      $('#btnSaveText').disabled = true;
    }
    checkFit();
  } catch (e) { toast('Open failed: ' + e.message, 'bad'); }
}

// Binary files get two lenses: extracted strings (usually the useful one --
// it is how .strtbl, .actb and .stateb reveal their contents) and raw hex.
function renderBinary() {
  if (!openText) return;
  const ta = $('#textArea');
  // Skeletons: switch between the editable source and the bone tree.
  if (openText.tree) {
    if ($('#binView').value === 'tree') {
      ta.value = (openText.tree_note || '') + '\n\n' + openText.tree;
      ta.readOnly = true;
      $('#btnSaveText').disabled = true;
    } else {
      ta.value = openText.text;
      ta.readOnly = false;
      $('#btnSaveText').disabled = false;
    }
    return;
  }
  if (openText.kind !== 'binary') return;

  if ($('#binView').value === 'curves' && openText.decoded) {
    ta.value = animText(openText.decoded);
    ta.readOnly = true;
    $('#btnSaveText').disabled = true;
    showAnimExport(openText.decoded);
    return;
  }
  hideAnimExport();

  const a = openText.anim;
  const animNote = a
    ? `Animation "${a.magic}" — ${a.frames} frames, ${a.channels} channels` +
      `${a.bones ? ` (${a.bones} bones x 3)` : ''}.\n` +
      // This used to say the curve data was still compressed. It is not
      // compressed at all -- see rdrstudio/anim.py.
      `Switch the view above to Curves to see it decoded.\n\n`
    : '';
  if ($('#binView').value === 'strings') {
    const s = openText.strings || [];
    ta.value = animNote + (s.length
      ? `${s.length} printable strings in ${openText.size} bytes` +
        `${s.length >= 800 ? ' (first 800)' : ''}\n\n` +
        s.map((x) => `${x.off.toString(16).padStart(6, '0')}  ${x.s}`).join('\n')
      : `No printable strings found in ${openText.size} bytes — switch to Hex.`);
  } else {
    ta.value = animNote
      + hexDump(openText.hex, openText.ascii, openText.offset || 0)
      + (openText.size > 4096
        ? `\n\n… showing first 4096 of ${openText.size} bytes. Export to see it all.`
        : '');
  }
}

// The Blender export bar, which only exists while a decoded animation is on
// screen. Two controls, and both are there because the answer is genuinely
// ambiguous rather than to look configurable:
//
//   * SKELETON -- nothing in a .anim names one. Its channel count fixes the
//     bone count exactly, but 11 skeletons in SWAT.DAT have 53 bones, so the
//     tool cannot know which. Picking the wrong one gives correct motion on a
//     body with the wrong proportions.
//   * EULER ORDER -- the mapping says which three channels belong to a bone;
//     it does not say what order the engine composes them in. Unverified.
function showAnimExport(d) {
  let bar = $('#animExport');
  if (!bar) {
    bar = el('div');
    bar.id = 'animExport';
    bar.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;'
      + 'padding:7px 10px;border-top:1px solid var(--line);font-size:11px';
    $('#textArea').parentNode.append(bar);
  }
  bar.innerHTML = '';
  bar.style.display = 'flex';
  if (!d.ok || !(d.skeletons || []).length) {
    // NOT "this codec is not decoded" any more. All three .anim codecs decode
    // -- cm2, cmp and the uncompressed one -- so the branch is still reachable
    // but the old reason was retired under it: reaching it means the file is
    // none of the three, or is damaged. The route says which, and says it
    // better than a hard-coded sentence can, so show what it said.
    // rdr-fmt REQ-003.
    bar.append(el('span', 'muted',
      d.ok ? `No skeleton in this archive has ${d.bones} bones, which is what a `
             + `${d.channels}-channel clip needs — so there is nothing to rig it to.`
           : [d.error, d.note].filter(Boolean).join(' ')
             || `${d.magic ? `“${d.magic}” is not` : 'This is not'} one of the `
                + 'three .anim codecs this tool reads, so there is nothing to '
                + 'export yet.'));
    return;
  }
  // One home for the round trip, not two.
  //
  // This bar used to carry its own skeleton and Euler pickers and its own
  // download button -- a second, half-strength copy of what the Animation tab
  // does, in a different nav group, with no import. Two sets of the same two
  // dropdowns is a thing to keep in sync and a thing to get wrong. It now
  // hands off to the tab where you can also watch the clip and bring an
  // edited BVH back.
  const b = el('button', 'primary', 'Open in Animation ▸');
  b.style.fontSize = '11px';
  b.title = 'Play, export to Blender, import the edited BVH.';
  b.onclick = () => openAnimClip(openText.archive, openText.name);
  bar.append(b);

  const note = el('span', 'muted');
  note.style.cssText = 'flex:1;min-width:220px;line-height:1.5';
  note.textContent = `${d.bones} bones · ${d.keys} keys at `
    + `${d.half_rate ? '15' : '30'} fps (the clip is ${d.frames} game frames`
    + `${d.half_rate ? ', stored at half rate' : ''})`;
  bar.append(note);
}

function hideAnimExport() {
  const bar = $('#animExport');
  if (bar) bar.style.display = 'none';
}

// A decoded animation as text, with each curve drawn as a sparkline.
//
// Deliberately NOT presented as a posed skeleton. The layout is settled and
// verified byte-exact on all 3,771 cm2 files, but which bone a channel triple
// drives, the Euler order and the axis convention are all unknown -- so showing
// a moving character would be inventing three things the disc does not state.
// Numbers and curves are what is actually known.
const SPARK = ' ▁▂▃▄▅▆▇█';

function sparkline(vals) {
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo;
  if (!(span > 0)) return SPARK[0].repeat(vals.length);
  return vals.map((v) =>
    SPARK[Math.min(8, Math.floor((v - lo) / span * 8.999))]).join('');
}

function animText(d) {
  if (!d.ok) {
    return `${d.name}\n\n${d.error}\n\n${d.note || ''}`;
  }
  const L = [];
  L.push(`${d.name} — codec "${d.magic}"`);
  L.push('');
  L.push(`${d.frames} frames, ${d.channels} channels (${d.triples} triples), `
    + `${d.keys} keys stored`);
  L.push(`${d.animated_channels} channels animate; ${d.constant_channels} are `
    + `constant for the whole clip`);
  L.push(`flags 0x${d.flags_raw.toString(16).padStart(2, '0')} — `
    + `${d.loop ? 'loops' : 'does not loop'}, `
    + `${d.half_rate ? 'HALF RATE (stores every other frame)' : 'full rate'}`);
  L.push(`scales: A ${d.scale_a} (channels 0-2), B ${d.scale_b} (channels 3+)`
    + `  [${d.scales_present} of 2 stored in the file]`);
  L.push(`${d.size} bytes, decoder consumed ${d.consumed}`
    + `${d.exact ? ' — exactly, nothing left over' : ' — MISMATCH'}`);
  L.push(`unknown float at +0x0C: ${d.unknown_0c}`);
  L.push('');
  L.push('Channels 0-2 are the ROOT bone’s translation in metres; channels');
  L.push('3+3i..3+3i+2 are bone i’s rotation in radians, with i following the');
  L.push('skeleton’s own depth-first bone order. That mapping is confirmed:');
  L.push('in a walk the largest rotations land on hip, knee, shoulder and');
  L.push('ankle while fingers sit near zero, and left/right pairs come out');
  L.push('antiphase (shoulder r = -0.97, hip r = -0.90) where random channel');
  L.push('pairs are 50/50. Export to BVH below.');
  L.push('');
  L.push('The composition order is YZX -- R = Ry(ch1)*Rz(ch2)*Rx(ch0) -- read');
  L.push('from the engine’s own bone matrix builder at 0x00146B68. In');
  L.push('Blender that is rotation mode ‘XZY’. All six orders are still');
  L.push('offered, since that is one trace of one function and re-exporting');
  L.push('costs nothing.');
  L.push('');
  L.push(`CURVES — ${d.curves_shown} of ${d.animated_channels} animated `
    + `channels, each over its ${d.keys} keys:`);
  L.push('');
  for (const c of (d.curves || [])) {
    const v = c.values;
    const lo = Math.min(...v), hi = Math.max(...v);
    L.push(`  ch ${String(c.channel).padStart(3)}  ${sparkline(v)}`);
    L.push(`         ${lo.toFixed(4)} … ${hi.toFixed(4)}   `
      + `(group ${c.group.startsWith('A') ? 'A' : 'B'})`);
  }
  if ((d.constants || []).length) {
    L.push('');
    L.push('CONSTANT channels (one value for the whole clip):');
    const parts = d.constants.map((c) => `ch${c.channel}=${c.value}`);
    for (let i = 0; i < parts.length; i += 6)
      L.push('  ' + parts.slice(i, i + 6).join('  '));
  }
  return L.join('\n');
}

function hexDump(hex, ascii, base = 0) {
  const bytes = hex.match(/../g) || [];
  const out = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = bytes.slice(i, i + 16);
    out.push((base + i).toString(16).padStart(6, '0') + '  ' +
             chunk.join(' ').padEnd(47) + '  ' + ascii.slice(i, i + 16));
  }
  return out.join('\n');
}

async function checkFit() {
  if (!openText || openText.kind !== 'text') { $('#fitInfo').textContent = ''; return; }
  const res = await post('savetext', {
    archive: openText.archive, name: openText.name,
    text: $('#textArea').value, dry: true,
  });
  const pct = Math.min(100, (res.stored / res.slot) * 100);
  const bar = $('#fitBar');
  bar.style.width = pct + '%';
  bar.className = res.fits && !res.rebuild ? '' : 'over';
  $('#fitInfo').textContent =
    `${res.stored} / ${res.slot} bytes` + (res.rebuild ? ' — over its slot: the archive rebuilds to make room on the next launch'
      : res.fits ? ` (${res.headroom} spare)` : ' — TOO BIG');
  $('#btnSaveText').disabled = !res.fits;
}

// ------------------------------------------------------------------- chrome

// Two tabs that were really one question each got folded together: browsing an
// archive and asking how much room it has; and your mods, your history and
// what the tool has done. A view switcher inside the tab beats a tab apiece.
function setArcView(which) {
  const files = which === 'files';
  $('#arcFilesView').style.display = files ? '' : 'none';
  $('#arcRoomView').style.display = files ? 'none' : 'flex';
  $('#arcViewFiles').classList.toggle('on', files);
  $('#arcViewRoom').classList.toggle('on', !files);
  if (!files) {
    const sel = $('#capLevel');
    if (level && [...sel.options].some((o) => o.value === level.archive))
      sel.value = level.archive;
    if (sel.value) loadCapacity(sel.value);
  }
}

function setWorkView(which) {
  if (which === 'mods') which = 'files';   // the Saved mods view became Versions
  const panes = { history: '#historyList',
                  files: '#touchedList', log: '#logView' };
  for (const [k, sel] of Object.entries(panes))
    $(sel).style.display = k === which ? '' : 'none';
  $('#workViewHistory').classList.toggle('on', which === 'history');
  $('#workViewFiles').classList.toggle('on', which === 'files');
  $('#workViewLog').classList.toggle('on', which === 'log');
  if (which === 'log') refreshLog();
  if (which === 'history') refreshHistory();
  if (which === 'files') refreshTouched();
}

// ------------------------------------------------------- command palette
//
// Sixteen tabs plus a dozen actions is more than anyone should have to scan.
// Ctrl+K goes anywhere by name, which also means the tab bar no longer has to
// be the only way to reach anything.
const PALETTE = [
  ['3D Editor', 'Load a level and edit it in 3D', () => showTab('editor')],
  ['Assets', 'Browse every model on the disc', () => showTab('assets')],
  ['Environment', "This level's lighting, fog and weather", () => showTab('env')],
  ['Textures', 'View and replace textures', () => showTab('textures')],
  /* @feature:strings_editor */
  ['Text', 'Rewrite anything the game says', () => showTab('strings')],
  /* @end:strings_editor */
  ['Weapons', 'Damage, clip, spread, range', () => showTab('weapons')],
  ['Creatures', 'Enemy and animal stats', () => showTab('creatures')],
  ['Animation', 'Watch character animations play on their skeleton',
   () => showTab('anim')],
  ['Campaign', 'Missions, unlockables, shop prices', () => showTab('campaign')],
  ['Game Rules', 'Difficulty, unlocks, Bounty Hunter and cheats',
   () => showTab('rules')],
  ['Unused difficulty dials', 'Two per-mission knobs the game reads and nobody set',
   () => showTab('rules')],
  ['Cheats', 'The ten cheats, and the two that do nothing',
   () => showTab('rules')],
  ['Voices & music', 'Every spoken line, searchable by what is said',
   () => { showTab('audio'); setAudioView('streams'); }],
  ['Settings', 'Find one setting across the whole disc', () => showTab('fields')],
  ['Archives', 'Browse archives and see where there is room',
   () => { showTab('archives'); setArcView('files'); }],
  ['Room to build', 'Slot headroom per level',
   () => { showTab('archives'); setArcView('room'); }],
  ['Changes', 'What your mod changes, file by file',
   () => { showTab('mods'); setWorkView('files'); }],
  ['Edit history', 'Every edit in order',
   () => { showTab('mods'); setWorkView('history'); }],
  ['Activity log', 'What the tool has done this session',
   () => { showTab('mods'); setWorkView('log'); }],
  ...($('#btnPlay') ? [['Play', 'Hand the disc to PCSX2 and launch',
                       () => $('#btnPlay').click()]] : []),
  ...($('#btnBoot') ? [['Boot test', 'Launch and read the emulator log',
                       () => $('#btnBoot').click()]] : []),
  ['Build & share my mod', 'Export a mod pack, mod file, patch or playable ISO',
   () => $('#hdrExport')?.click()],
  ['Load a mod file', 'Add a .rdrmod.json someone sent you',
   () => $('#btnImportMod')?.click()],
  ['Undo', 'Step back one change', () => $('#btnUndo').click()],
  ['Reset to stock', 'Throw away every change', () => $('#btnRestore').click()],
];

let paletteIdx = 0;

function openPalette() {
  $('#palette').classList.add('on');
  const inp = $('#paletteInput');
  inp.value = '';
  renderPalette();
  inp.focus();
}

function closePalette() { $('#palette').classList.remove('on'); }

function paletteMatches() {
  const q = $('#paletteInput').value.trim().toLowerCase();
  if (!q) return PALETTE;
  return PALETTE.filter(([n, d]) =>
    n.toLowerCase().includes(q) || d.toLowerCase().includes(q));
}

function renderPalette() {
  const box = $('#paletteList');
  const hits = paletteMatches();
  if (paletteIdx >= hits.length) paletteIdx = 0;
  box.innerHTML = '';
  hits.forEach(([name, desc, fn], i) => {
    const danger = /reset to stock|restore vanilla|delete|wipe|discard|revert all/i.test(name);
    const row = el('div', 'prow' + (i === paletteIdx ? ' sel' : '')
                          + (danger ? ' prow-danger' : ''));
    // A destructive entry has to read as destructive HERE most of all. The
    // palette is where an action is easiest to fire -- type three letters,
    // press Enter -- and it was painting "Restore vanilla" in the accent blue
    // that means "selected" everywhere else in the tool. The most destructive
    // thing in the product, wearing the safe colour, in the one place it can
    // be triggered without ever seeing it.
    row.append(el('b', '', name),
               el('span', 'muted', desc));
    row.onmouseenter = () => { paletteIdx = i; renderPalette(); };
    row.onclick = () => { closePalette(); fn(); };
    box.append(row);
  });
  if (!hits.length)
    box.innerHTML = '<div class="prow muted">nothing matches</div>';
}

function showTab(name) {
  for (const b of document.querySelectorAll('nav button'))
    b.classList.toggle('on', b.dataset.tab === name);
  for (const t of document.querySelectorAll('.tab'))
    t.classList.toggle('on', t.id === 'tab-' + name);
  if (name === 'editor') resize();
  if (name === 'env') loadEnvList();
  if (name === 'anim') initAnimTab();
  if (name === 'audio') initAudioTab();
  if (name === 'rules') initRulesTab();
  if (name === 'movies') { initMoviesTab(); initStillsTab(); }
  // Hook the replace controls once, the first time the tab is opened.
  if (name === 'movies' && !window.__stillWired) {
    window.__stillWired = true;
    const btn = $('#stillReplaceBtn'), inp = $('#stillFile');
    if (btn && inp) {
      btn.onclick = () => inp.click();
      inp.onchange = () => { if (inp.files[0]) stillEstimate(inp.files[0]); };
    }
  }
  // Leaving the editor while its viewport owns the screen would strand the
  // user looking at a fullscreen canvas with no tabs.
  if (name !== 'editor' && document.fullscreenElement === view)
    document.exitFullscreen?.();
  // Follow the level you are actually editing. These tabs used to keep
  // whatever was selected first, so the tool could show you one level's
  // textures while you edited another -- the only place it contradicted
  // itself about what "the level" meant.
  if (name === 'archives') {
    // Loaded here rather than at startup. It is a three-minute call on a cold
    // cache and nothing outside this tab needs it, so it is paid for by the
    // person who asked to see it -- once, then it is cached in `archiveList`.
    if (!archiveList.length) loadArchives().catch((e) =>
      toast('Could not read the archive list: ' + e.message, 'bad'));
    const sel = $('#capLevel');
    if (level && [...sel.options].some((o) => o.value === level.archive))
      sel.value = level.archive;
    if (sel.value && $('#arcRoomView').style.display !== 'none')
      loadCapacity(sel.value);
  }
  if (name === 'mods') {
    refreshHistory();
    // The Changed files pane stays on whichever view it was left on, so
    // coming back to the tab must not show it a stale file list.
    if ($('#touchedList').style.display !== 'none') refreshTouched();
  }
  if (name === 'weapons') { loadGuns(); loadNewWeapon(); }
  if (name === 'text') fillDropArc();
  if (name === 'strings' && !window.__strFirstOpen) {
    // The only BALANCE tab that opened DEAD: empty list, empty unlabeled
    // language dropdown, until you guessed that searching nothing was the way
    // in. An empty search lists the strings and fills the slot picker.
    window.__strFirstOpen = true;
    runStringSearch();
  }
  if (name === 'creatures') loadCreatures();
  if ((name === 'campaign' || name === 'unlocks') && !campaign) loadCampaign();
  if (name === 'assets') loadAssets();
  if (name === 'textures') {
    const sel = $('#texLevel');
    if (level && [...sel.options].some((o) => o.value === level.archive)
        && sel.value !== level.archive) {
      sel.value = level.archive;
      texArea = level.area;
    } else if (level && sel.value === level.archive
               && level.area && texArea !== level.area) {
      // Same archive, different AREA: switching areas in Build used to leave
      // this tab's grid -- and its Replace button -- on the previous bundle's
      // index space. That is a wrong-target write dressed as staleness.
      texArea = level.area;
    }
    if (sel.value) loadTextures(sel.value);
  }
}
for (const b of document.querySelectorAll('nav button'))
  b.onclick = () => showTab(b.dataset.tab);

$('#arcFilter').oninput = renderArchives;

// Viewport toolbar: the two halves of the level, and what a click may pick.
// (The Art | Collision chips and the Pick filter are gone: models and their
// collision are one thing. The debug view is View settings > Show collision.)
$('#crLod').onchange = (e) => {
  const stem = $('#crLod').dataset.stem;
  if (stem) showCreatureModel(stem, parseInt(e.target.value, 10));
};

let assetSearchTimer = null;
// The facet counts have to follow the search box too: "Whole buildings 412"
// is the wrong number the moment you type "saloon" into the box above it.
const assetRefresh = () => { refreshAssetSummary(); runAssetSearch(); };
$('#assetSearch').oninput = () => {
  clearTimeout(assetSearchTimer);
  assetSearchTimer = setTimeout(assetRefresh, 180);
};
$('#assetShared').onchange = assetRefresh;
$('#assetSpin').onchange = (e) => assetViewer?.setSpin(e.target.checked);
$('#texSpin').onchange = (e) => texViewer?.setSpin(e.target.checked);
$('#inspSpin').onchange = (e) => inspViewer?.setSpin(e.target.checked);
$('#envOwnOnly').onchange = loadEnvList;
$('#envSave').onclick = saveEnv;
$('#envRevert').onclick = () => {
  if (envFile) openEnvFile(envFile.archive, envFile.name);
};
$('#envTint').onchange = (e) => {
  setEnvTint(e.target.checked);
  remember('envTint', e.target.checked);
};
$('#arcViewFiles').onclick = () => setArcView('files');
$('#arcViewRoom').onclick = () => setArcView('room');
$('#lnkVersions').onclick = (e) => { e.preventDefault(); showTab('versions'); };
$('#workViewHistory').onclick = () => setWorkView('history');
$('#workViewFiles').onclick = () => setWorkView('files');
$('#workViewLog').onclick = () => setWorkView('log');

$('#btnPalette').onclick = openPalette;
$('#sbPalette').onclick = openPalette;

// ------------------------------------------------------------------ guides
// The long-form docs ship inside the app and are read here, not as loose
// files beside the exe.
async function openGuide(name) {
  const box = $('#guideView'), pre = $('#guideText');
  box.style.display = 'block';
  pre.textContent = 'Loading…';
  try {
    const j = await api('guide?name=' + encodeURIComponent(name || 'user-guide'));
    pre.textContent = j.ok ? j.text : (j.error || 'Guide not found.');
  } catch (e) { pre.textContent = String(e.message || e); }
  pre.scrollTop = 0;
}
$('#sbGuides').onclick = () => openGuide('user-guide');
$('#guideClose').onclick = () => { $('#guideView').style.display = 'none'; };
$('#guideView').addEventListener('click', (e) => {
  if (e.target.id === 'guideView') e.currentTarget.style.display = 'none';
});
document.addEventListener('click', (e) => {
  const g = e.target.closest && e.target.closest('[data-guide]');
  if (!g) return;
  e.preventDefault();
  openGuide(g.dataset.guide);
});
$('#palette').onclick = (e) => { if (e.target.id === 'palette') closePalette(); };
$('#paletteInput').oninput = () => { paletteIdx = 0; renderPalette(); };
$('#paletteInput').onkeydown = (e) => {
  const hits = paletteMatches();
  if (e.key === 'ArrowDown') {
    paletteIdx = Math.min(paletteIdx + 1, hits.length - 1); renderPalette();
  } else if (e.key === 'ArrowUp') {
    paletteIdx = Math.max(paletteIdx - 1, 0); renderPalette();
  } else if (e.key === 'Enter') {
    const hit = hits[paletteIdx];
    if (hit) { closePalette(); hit[2](); }
  } else if (e.key === 'Escape') {
    closePalette();
  } else {
    return;
  }
  e.preventDefault();
};

/* @feature:strings_editor */
$('#stGo').onclick = runStringSearch;
$('#stQ').onkeydown = (e) => { if (e.key === 'Enter') runStringSearch(); };
$('#stSlot').onchange = runStringSearch;
/* @end:strings_editor */
$('#fsGo').onclick = runFieldSearch;
$('#fsKey').onkeydown = (e) => { if (e.key === 'Enter') runFieldSearch(); };
$('#fsExt').onkeydown = (e) => { if (e.key === 'Enter') runFieldSearch(); };
$('#fsApply').onclick = applyFieldBulk;
$('#fsAll').onclick = () => {
  for (const h of fsHits) fsPicked.add(fsKeyOf(h));
  renderFieldResults();
};
$('#fsNone').onclick = () => { fsPicked.clear(); renderFieldResults(); };
for (const x of FS_EXTS) $('#fsExtList').append(new Option(x));
$('#bakedLight').onchange = (e) => {
  bakedLight = e.target.checked;
  remember('bakedLight', bakedLight);
  setRenderMode(renderMode);
};
$('#secondPass').onchange = (e) => {
  showSecondPass = e.target.checked;
  remember('secondPass', showSecondPass);
  setRenderMode(renderMode);
};

let assetDockTimer = null;
$('#assetDockFilter').oninput = () => {
  clearTimeout(assetDockTimer);
  assetDockTimer = setTimeout(renderAssetDock, 180);
};
$('#assetDockHere').onchange = renderAssetDock;

$('#btnEvents').onclick = () => openSheet();
$('#sheetClose').onclick = closeSheet;
for (const b of document.querySelectorAll('#sheetTabs [data-sheet]'))
  b.onclick = () => openSheet(b.dataset.sheet);

$('#objFilter').oninput = renderObjectList;

// Three views of one node set. Not three data sources -- the same nodes,
// regrouped, which is the discipline that keeps the outliner from drifting out
// of sync with the viewport the way Layers and Objects used to.
for (const b of document.querySelectorAll('#outModes [data-outmode]')) {
  b.onclick = () => {
    outMode = b.dataset.outmode;
    try { localStorage.setItem('rdr.outmode', outMode); } catch {}
    remember('outMode', outMode);
    for (const o of document.querySelectorAll('#outModes [data-outmode]'))
      o.classList.toggle('on', o === b);
    collapsedSources.clear();
    renderObjectList();
  };
  b.classList.toggle('on', b.dataset.outmode === outMode);
}
$('#levelFilter').oninput = loadLevels;
{
  let t = null;     // debounced -- a keystroke is not a request
  $('#tocFilter').oninput = () => {
    clearTimeout(t);
    t = setTimeout(() => currentArc && loadToc(currentArc), 180);
  };
}
$('#btnCheck').onclick = checkFit;
// The ~30 s warning matters only for a content search (DESIGN-REVIEW A2).
$('#searchMode').addEventListener('change', () => {
  $('#searchInfo').textContent = $('#searchMode').value === 'content'
    ? 'Content search takes about 30 s.' : '';
});
$('#binView').onchange = renderBinary;

$('#btnExport').onclick = () => {
  if (!openText) return toast('Open a file first.');
  const url = `/api/file.bin?archive=${encodeURIComponent(openText.archive)}`
            + `&name=${encodeURIComponent(openText.name)}`;
  saveFile(url, openText.name.replace(/\//g, '_')).then((ok) => { if (ok)
    toast(`Exported ${openText.name} (${openText.size} bytes, decompressed).`); });
};

$('#btnImport').onclick = () => {
  if (!openText) return toast('Open a file first.');
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.onchange = async () => {
    const file = picker.files?.[0];
    if (!file) return;
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = '';
    for (let i = 0; i < buf.length; i += 8192)
      bin += String.fromCharCode.apply(null, buf.subarray(i, i + 8192));
    const body = { archive: openText.archive, name: openText.name,
                   data_b64: btoa(bin) };
    try {
      const dry = await post('file_import', { ...body, dry: true });
      if (!dry.fits) {
        return toast(
          `Won't fit: ${file.name} needs ${dry.stored} stored bytes but the ` +
          `slot is ${dry.slot}. Nothing was written.`, 'bad');
      }
      if (!confirm(`Replace ${openText.name} with ${file.name}?\n` +
                   (dry.rebuild ? `It needs ${dry.stored} bytes and the slot is ${dry.slot}: the archive is rebuilt to make room on the next Rebuild and play.`
                                : `${dry.stored}/${dry.slot} bytes of slot used.`))) return;
      const r = await post('file_import', body);
      if (!r.ok) return toast('Import refused: ' + r.error, 'bad');
      toast(`Imported ${file.name} over ${openText.name} — ` +
            `${r.used}/${r.slot} bytes. Ctrl+Z undoes it.`, 'good');
      await openTextFile(openText.archive, openText.name);
      refreshHistory();
    } catch (e) { toast('Import failed: ' + e.message, 'bad'); }
  };
  picker.click();
};
$('#textArea').addEventListener('input', () => {
  clearTimeout(window._fitT);
  window._fitT = setTimeout(checkFit, 400);
});

$('#btnSaveText').onclick = async () => {
  if (!openText) return;
  try {
    const r = await post('savetext', {
      archive: openText.archive, name: openText.name, text: $('#textArea').value,
    });
    toast(`Saved into ISO — ${r.used}/${r.slot} bytes of slot used.`, 'good');
    refreshHistory();
    if (level && level.archive === openText.archive) reloadLevel(true, true);
  } catch (e) { toast('Save failed: ' + e.message, 'bad'); }
};

// Anything that can take more than a moment goes through here. The server
// holds its lock for the duration and reports no percentage, so this shows an
// honest indeterminate bar plus a running clock rather than a fake progress
// number or a toast that disappears mid-operation.
let busyTimer = null;
async function withProgress(label, fn) {
  const box = $('#busy');
  const started = Date.now();
  $('#busyLabel').textContent = label;
  $('#busyTime').textContent = '0.0s';
  box.classList.add('on');
  clearInterval(busyTimer);
  busyTimer = setInterval(() => {
    $('#busyTime').textContent = ((Date.now() - started) / 1000).toFixed(1) + 's';
  }, 100);
  try {
    return await fn();
  } finally {
    clearInterval(busyTimer);
    box.classList.remove('on');
  }
}

// Which game Reset to stock is for: with a backup of the unedited Xbox game files ("unedited assets") there are two
// stocks, so it asks; without one it is the PS2's, as it always was. -> 'ps2' | 'xbox' | null.
function chooseReset() {
  return new Promise((res) => {
    const back = document.createElement('div');
    back.style.cssText = 'position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.55);display:flex;align-items:flex-start;justify-content:center';
    const box = document.createElement('div');
    box.style.cssText = 'margin-top:16vh;width:min(460px,92vw);background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:14px 16px';
    box.innerHTML = '<div style="font:var(--t-head);color:var(--title);margin-bottom:8px">Reset to stock: which game?</div>'
      + '<p class="muted" style="margin:0 0 12px">PS2: the working disc copied again from the stock disc (PCSX2).<br>'
      + 'Xbox: the recomp\'s game files put back as "unedited assets" has them, and the Studio\'s files taken out of its mods folder.<br>'
      + 'Either way every change to that game is lost; the Data Editor\'s mod files are kept as a Saved Mod.</p>';
    const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end';
    const done = (v) => { back.remove(); document.removeEventListener('keydown', esc); res(v); };
    const esc = (e) => { if (e.key === 'Escape') done(null); };
    for (const [label, v, cls] of [['Cancel', null, ''], ['PS2 (PCSX2)', 'ps2', 'danger'], ['Xbox (recomp)', 'xbox', 'danger']]) {
      const btn = document.createElement('button'); btn.textContent = label; if (cls) btn.className = cls;
      btn.onclick = () => done(v); row.append(btn);
    }
    box.append(row); back.append(box);
    back.addEventListener('mousedown', (e) => { if (e.target === back) done(null); });
    document.addEventListener('keydown', esc);
    document.body.append(back);
  });
}

$('#btnRestore').onclick = async () => {
  if (!window.__resetConfirmed) {
    let xs = null;
    try { xs = await post('de_x_xbox_state', {}); } catch { /* no Xbox side */ }
    if (xs && xs.backup_exists) {
      const which = await chooseReset();
      if (!which) return;
      if (which === 'xbox') {
        if (!window.rdrDEStartJob) return toast('The Data Editor is not ready yet', 'bad');
        const ok = await window.rdrDEStartJob('de_x_xbox_reset', { clear_mods: true }, 'Reset to stock (Xbox)');
        toast(ok ? 'The Xbox game is back to its unedited files.' : 'The Xbox reset stopped (see the Log)', ok ? 'good' : 'bad');
        if (ok && window.RDR && window.RDR.refreshLists) window.RDR.refreshLists();
        return;
      }
      window.__resetConfirmed = true;                   // PS2 chosen in the dialog: no second question
    }
  }
  if (!window.__resetConfirmed
      && !confirm('Overwrite the working disc from the stock disc?\n' +
                  'Every change is lost. The stock disc is untouched.')) return;
  window.__resetConfirmed = false;
  const clearMods = window.__resetClearMods !== false;
  window.__resetClearMods = undefined;
  let res = null;
  try {
    res = await withProgress('Restoring the working ISO from vanilla — copying 2.2 GB',
                             () => post('restore', { clear_mods: clearMods }));
  } catch (e) { return toast('Restore failed: ' + e.message, 'bad'); }
  const saved = res && res.saved_mod;
  if (saved) alert(`Working ISO restored to vanilla.

The Data Editor's ${res.cleared} mod file(s) were taken out too, so the next Build will not put them back. They are kept as the Saved Mod "${saved}" (DATA EDITOR > Data Editor, Saved Mods) if you want them again.`);
  else toast('Working ISO restored to vanilla.', 'good');
  level = null;
  chunkUid = uidChunk = null;
  location.reload();
};

// Guarded: the release build strips these two buttons, and a top-level
// `.onclick =` on a missing element throws -- taking every line after it with
// it, which is what stopped levels loading in the first release.
// The .dat files with queued edits (no room in their slots), from disc_state.
let pendingRebuild = [];

// Build (top bar, beside Play): everything waiting to go on the working disc in one go -- the archives queued for a
// rebuild, then the Data Editor's mod files not built yet. Greyed out when there is nothing.
let buildWaiting = null, building = false;
function renderBuildButton(w) {
  const b = $('#btnBuild');
  if (!b) return;
  buildWaiting = w || null;
  const q = (w && w.queued) || [];
  const n = (w && w.data_editor) || 0;
  const xbox = window.rdrPlatform && window.rdrPlatform() === 'xbox';
  b.disabled = building || (w && w.running);
  b.textContent = building ? 'Building…' : (xbox ? 'Build (Xbox)' : 'Build (PS2)');
  if (xbox && !building && !(w && w.running)) {
    b.title = 'Build into the Xbox game: the mod made the Xbox\'s files, into the recomp\'s mods folder'
      + (q.length ? ' (first ' + q.length + ' .dat file(s) with Studio changes are rebuilt on the working disc)' : '');
    return;
  }
  const lines = [];
  if (q.length) lines.push(q.length + ' .dat file(s) to rebuild with Studio changes: ' + q.map((p) => p.archive).join(', '));
  if (n) lines.push(n + ' Data Editor file(s) waiting' + (w.folders && w.folders.length ? ' (' + w.folders.join(', ') + ')' : ''));
  b.title = building ? 'Building…' : (w && w.running) ? 'The Data Editor is busy'
    : 'Build into the PS2 game (the working disc)' + (lines.length ? ':\n' + lines.join('\n') : ': the Data Editor\'s mods folder');
}

async function waitDataEditorJob(label) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 1200));
    let j;
    try { j = await api('de_job'); } catch { continue; }
    if (label && $('#busyLabel')) $('#busyLabel').textContent = label + (j.log && j.log.length ? ' — ' + j.log[j.log.length - 1] : '');
    if (j.state !== 'running') return j;
  }
}

window.rdrRenderBuild = () => renderBuildButton(buildWaiting);

// The build runs behind the app: the log bar at the bottom shows it (and a green Done!), the app can be looked
// around meanwhile, and what would change something waits (xboxmode.js refuses those requests while it runs).
function logBar(text, cls) {
  const s = $('#deStatus');
  if (!s) return;
  s.textContent = text; s.title = text;
  s.style.color = cls === 'done' ? 'var(--grn)' : cls === 'bad' ? 'var(--red)' : '';
  s.style.fontWeight = cls === 'done' ? '600' : '';
}

// the top Build; -> true when it went through (the Xbox's Play builds first: xboxmode.js)
async function runTopBuild() {
  const w = buildWaiting || {};
  const xbox = window.rdrPlatform && window.rdrPlatform() === 'xbox';
  if (building) return false;
  let ok = false;
  building = true; renderBuildButton(w);
  document.body.classList.add('building');
  try {
    if ((w.queued || []).length) {
      logBar('Building: rebuilding ' + w.queued.length + ' .dat file(s) with your Studio changes…');
      await rebuildQueuedArchives();
    }
    {
      const route = xbox ? 'de_x_xbox_build' : 'de_build';
      const label = xbox ? 'Build into the Xbox game' : 'Build into the PS2 game';
      if (!window.rdrDEStartJob) throw new Error('the Data Editor is not ready yet');
      const ok = await window.rdrDEStartJob(route, {}, label);
      if (!ok) throw new Error(label + ' stopped (see the Log)');
    }
    post('note', { text: 'Build complete.' }).catch(() => {});
    if (window.RDR && window.RDR.refreshLists) window.RDR.refreshLists();
    ok = true;
  } catch (e) {
    post('note', { text: 'Build failed: ' + e.message }).catch(() => {});
    logBar('Build failed: ' + e.message, 'bad');
    toast('Build failed: ' + e.message, 'bad');
  } finally {
    building = false;
    document.body.classList.remove('building');
    refreshDiscState();
  }
  return ok;
}
if ($('#btnBuild')) $('#btnBuild').onclick = runTopBuild;
window.rdrBuild = runTopBuild;

async function rebuildQueuedArchives() {
  // One rebuild per archive, every queued file in it, as a job polled here.
  const label = $('#busyLabel');
  let r = await post('rebuild_pending', {});
  if (r && r.ok === false) throw new Error(r.error_text || 'the rebuild was refused');
  let job = r.job || {};
  while (job.state === 'running') {
    const line = `Rebuilding ${job.total} .dat file${job.total === 1 ? '' : 's'} with your changes`
      + (job.archive ? ` — ${job.done + 1} / ${job.total}: ${job.archive}` : '')
      + (/SWAT/i.test(job.archive || '') ? ' (swat.dat takes a few minutes)' : '');
    if (label) label.textContent = line;
    logBar('Building: ' + line);
    await new Promise((res) => setTimeout(res, 1500));
    try { job = (await api('disc_state')).rebuild_job || {}; } catch { /* restarting the disc: ask again */ }
  }
  if (job.state === 'error') throw new Error('rebuilding the queued .dat files failed: ' + job.error);
  return job.rebuilt || [];
}

if ($('#btnPlay')) $('#btnPlay').onclick = async () => {
  try {
    if (pendingRebuild.length) {
      const done = await withProgress('Rebuilding the .dat files with your changes', rebuildQueuedArchives);
      if (done.length) toast(`Rebuilt ${done.join(', ')}. Starting PCSX2…`, 'good');
    }
    await withProgress('Releasing the disc and starting PCSX2',
                       () => post('play', {}));
    toast('PCSX2 launched with your modded disc.\n' +
          'The Studio takes the disc back as soon as you edit something, so ' +
          'quit the emulator before your next change.', 'good');
    refreshDiscState();
  } catch (e) {
    // The likeliest failure for a release user is simply "never configured"
    // -- setup's PCSX2 step is skippable on purpose. That case gets a door,
    // not just a diagnosis.
    if (/PCSX2 not found/i.test(e.message) && $('#btnAppSetup')) {
      toast('No PCSX2 configured yet. Opening App setup — point at your ' +
            'pcsx2.exe and Play works from then on.', 'warn');
      $('#btnAppSetup').click();
    } else {
      toast('Could not start the game: ' + e.message, 'bad');
      alert('Could not start the game\n\n' + e.message);
    }
  }
};

if ($('#btnBoot')) $('#btnBoot').onclick = async () => {
  try {
    // The server picks the length. It used to be asked for 45 s, which is
    // under half the ~100 s the intro alone takes -- long enough for a clean
    // log, far too short to tell a playing disc from a frozen one.
    const r = await withProgress(
      'Boot test running. About three minutes.',
      () => post('boot', {}));
    if (r.error) return toast(r.error, 'bad');
    toast(r.ok
      ? (r.conclusive
         ? `BOOT PASS — ELF executed, no TLB misses, ${r.seconds} s.`
         : `BOOT PASS — but only ${r.seconds} s. ${r.caveat}`)
      : `BOOT FAIL — booted=${r.booted}, TLB misses=${r.tlb_miss}.\n` +
        `That is the bad-mod signature. Reset to stock and re-apply one change at a time.`,
      r.ok ? (r.conclusive ? 'good' : 'warn') : 'bad');
    refreshDiscState();
  } catch (e) { toast('Boot test failed: ' + e.message, 'bad'); }
};

// ---- App setup: discs and emulator, through the desktop shell -------------
//
// window.pywebview.api is injected by the shell into ITS window only. A
// browser tab (dev, or --lan from another machine) has no such object, and
// the modal says so instead of showing buttons that cannot work -- the
// export bug taught what silent controls cost.
const shellApi = () => (window.pywebview && window.pywebview.api) || null;

async function appSetupRefresh() {
  const api = shellApi();
  $('#asBrowserNote').style.display = api ? 'none' : 'block';
  $('#asBody').style.display = api ? 'block' : 'none';
  if (!api) return;
  const s = await api.app_settings();
  $('#asVanilla').textContent = s.vanilla || 'not set';
  $('#asWorking').textContent = s.working || 'not set';
  $('#asPcsx2').textContent = s.pcsx2 || 'not configured';
  try {                                                     // the Xbox recomp (rdrstudio/xboxmode.py)
    const x = await post('de_x_xbox_state', {});
    if ($('#asRecomp')) $('#asRecomp').textContent = x.exe ? x.exe + (x.exe_ok ? '' : '  (not there)') : 'not set';
    if ($('#asRecompAssets')) $('#asRecompAssets').textContent = x.assets ? x.assets + (x.assets_ok ? '' : '  (not there)') : 'not set';
  } catch { /* an older server */ }
  $('#asPcsx2Note').textContent = s.pcsx2_ok ? ''
    : 'Play stays off until this points at a real pcsx2.exe.';
  // DOM-built rather than a template string: paths carry quotes and
  // backslashes, and the build's balance scanner (rightly) refuses regexes
  // inside template literals. Elements dodge both problems at once.
  renderAppFolders(s.folders || []);
  const known = (s.known_working || []).filter((p) => p !== s.working);
  const kbox = $('#asKnown');
  kbox.textContent = known.length ? 'Recent working discs:' : '';
  for (const p of known) {
    const b = document.createElement('button');
    b.className = 'ghost';
    b.style.cssText = 'font-size:11px;margin:2px 4px 0 0';
    b.textContent = p.split(/[\\/]/).pop();
    b.title = p;
    b.onclick = () => switchDisc('working', p);
    kbox.append(document.createElement('br'), b);
  }
}

// App setup > Folders: where mods, checkpoints, exports and the Data Editor
// live. Open shows it in Explorer; Change / Default save the choice, which the
// Studio picks up on a restart (the server reads its folders at start).
function renderAppFolders(folders) {
  const box = $('#asFolders');
  if (!box) return;
  box.textContent = '';
  for (const f of folders) {
    const row = document.createElement('div');
    row.style.cssText = 'margin:0 0 9px';
    const head = document.createElement('div');
    head.style.cssText = 'display:flex;align-items:center;gap:6px';
    const name = document.createElement('b');
    name.style.cssText = 'font-size:12px;flex:1';
    name.textContent = f.label;
    name.title = f.note;
    const mk = (text, title, fn, ghost) => {
      const b = document.createElement('button');
      if (ghost) b.className = 'ghost';
      b.style.fontSize = '11px';
      b.textContent = text; b.title = title; b.onclick = fn;
      return b;
    };
    const api = shellApi();
    head.append(name,
      mk('Open', 'Show this folder in Explorer', async () => {
        const r = await api.open_app_folder(f.key);
        if (r && r.ok === false) toast('Could not open it: ' + r.error, 'bad');
      }),
      mk('Change…', 'Pick another folder (the Studio restarts to use it)', async () => {
        const r = await api.pick_app_folder(f.key);
        if (!r) return;
        if (r.ok === false) return toast(r.error, 'bad');
        appSetupRefresh();
        if (confirm(`${f.label} folder set to\n${r.path}\n\nRestart the Studio now to use it?`)) api.restart_app();
      }),
      mk('Default', 'Back to the default location', async () => {
        const r = await api.reset_app_folder(f.key);
        appSetupRefresh();
        if (r && r.ok && confirm(`${f.label} folder back to\n${r.path}\n\nRestart the Studio now to use it?`)) api.restart_app();
      }, true));
    const p = document.createElement('div');
    p.className = 'mono dim';
    p.style.cssText = 'word-break:break-all;font-size:11px';
    p.textContent = (f.path || 'not set') + (f.exists ? '' : '  (made when first used)');
    p.title = f.note;
    row.append(head, p);
    box.append(row);
  }
}

async function switchDisc(purpose, path) {
  const api = shellApi();
  if (!api) return;
  const r = await api.switch_iso(purpose, path);
  if (!r.ok) return toast('Could not switch: ' + r.error, 'bad');
  toast('Saved. Restarting the Studio on the other disc…', 'good');
  setTimeout(() => api.restart_app(), 900);
}

// ---- Settings > Profiles: every preference kept under a name (rdrstudio/settings.py, "profiles" in settings.json).
// A profile holds the server-side preferences and the front end's own local keys (rdr.*: the theme's resolved
// colours the first paint uses, the panels' remembered choices); loading one puts both back and reloads.
function localPrefs() {
  const out = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith('rdr.') && !k.startsWith('rdr.tmp')) out[k] = localStorage.getItem(k);
    }
  } catch { /* private mode */ }
  return out;
}
async function renderProfiles() {
  const box = $('#asProfileList');
  if (!box) return;
  let r;
  try { r = await api('profiles'); } catch (e) { box.textContent = 'Profiles are not available: ' + e.message; return; }
  box.innerHTML = '';
  const names = Object.keys(r.profiles || {}).sort((a, b) => a.localeCompare(b));
  if (!names.length) { box.append(el('div', 'dim', 'No profiles yet.')); return; }
  for (const n of names) {
    const p = r.profiles[n];
    const row = el('div');
    row.style.cssText = 'display:flex;gap:6px;align-items:center';
    const nm = el('b', null, n); nm.style.flex = '1';
    const when = el('span', 'dim', p.saved || ''); when.style.fontSize = '11px';
    const load = el('button', '', 'Load');
    load.title = 'Switch to this profile (the Studio reloads)';
    load.onclick = async () => {
      if (!confirm(`Load the profile "${n}"? Your current preferences are replaced (save them as a profile first to keep them). The Studio reloads.`)) return;
      try {
        const res = await post('profile_load', { name: n });
        try {
          for (const [k, v] of Object.entries(res.local || {})) localStorage.setItem(k, v);
        } catch { /* private mode: the file still has the server side */ }
        location.reload();
      } catch (e) { toast('Could not load it: ' + e.message, 'bad'); }
    };
    const exp = el('button', 'ghost', 'Export');
    exp.title = 'Save this profile as a .json file';
    exp.onclick = async () => {
      try {
        const got = await api('profile_get?name=' + encodeURIComponent(n));
        const doc = { format: 'rdrstudio-profile/1', name: n, ui: got.profile.ui || {}, local: got.profile.local || {} };
        const a = el('a');
        a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 1)], { type: 'application/json' }));
        a.download = n.replace(/[^A-Za-z0-9_ -]+/g, '_') + '.rdrprofile.json';
        document.body.append(a); a.click(); a.remove();
      } catch (e) { toast(e.message, 'bad'); }
    };
    const del = el('button', 'ghost', 'Delete');
    del.onclick = async () => {
      if (!confirm(`Delete the profile "${n}"?`)) return;
      try { await post('profile_delete', { name: n }); renderProfiles(); } catch (e) { toast(e.message, 'bad'); }
    };
    row.append(nm, when, load, exp, del);
    box.append(row);
  }
}
function wireProfiles() {
  const save = $('#asProfileSave');
  if (!save || save.dataset.wired) return;
  save.dataset.wired = '1';
  save.onclick = async () => {
    const n = ($('#asProfileName').value || '').trim();
    if (!n) { $('#asProfileName').focus(); return toast('Name the profile', 'bad'); }
    try {
      await flushPrefs();
      await post('profile_save', { name: n, local: localPrefs() });
      $('#asProfileName').value = '';
      toast(`Profile "${n}" saved.`, 'good');
      renderProfiles();
    } catch (e) { toast('Could not save it: ' + e.message, 'bad'); }
  };
  $('#asProfileImport').onclick = () => {
    const f = el('input'); f.type = 'file'; f.accept = '.json';
    f.onchange = async () => {
      const file = f.files[0];
      if (!file) return;
      try {
        const doc = JSON.parse(await file.text());
        if (!doc || (doc.format && doc.format !== 'rdrstudio-profile/1')) throw new Error('not a Studio profile file');
        const n = (($('#asProfileName').value || '').trim()) || doc.name || file.name.replace(/\.rdrprofile\.json$|\.json$/i, '');
        await post('profile_import', { name: n, profile: doc });
        toast(`Profile "${n}" imported. Load it to switch to it.`, 'good');
        renderProfiles();
      } catch (e) { toast('Could not import it: ' + e.message, 'bad'); }
    };
    f.click();
  };
}

if ($('#btnAppSetup')) $('#btnAppSetup').addEventListener('click', () => { wireProfiles(); renderProfiles(); });
if ($('#btnAppSetup')) $('#btnAppSetup').onclick = () => {
  $('#appSetup').style.display = 'block';
  appSetupRefresh();
};
if ($('#asOpenWorking')) $('#asOpenWorking').onclick = () => { const a = shellApi(); if (a) a.open_app_folder('working'); };
if ($('#asOpenVanilla')) $('#asOpenVanilla').onclick = () => { const a = shellApi(); if (a) a.open_app_folder('vanilla'); };
if ($('#asClose')) $('#asClose').onclick = () => {
  $('#appSetup').style.display = 'none';
};
if ($('#appSetup')) $('#appSetup').onclick = (e) => {
  if (e.target === $('#appSetup')) $('#appSetup').style.display = 'none';
};

if ($('#asPickPcsx2')) $('#asPickPcsx2').onclick = async () => {
  const api = shellApi(); if (!api) return;
  const d = await api.pick_pcsx2();
  if (d) { await api.set_pcsx2(d.ok ? d.path : ''); appSetupRefresh(); refreshDiscState(); }
};
if ($('#asFindPcsx2')) $('#asFindPcsx2').onclick = async () => {
  const api = shellApi(); if (!api) return;
  const d = await api.find_pcsx2();
  if (d && d.ok) { await api.set_pcsx2(d.path); toast('Found ' + d.path, 'good'); }
  else toast('No PCSX2 found in the usual places. Use Choose…', 'warn');
  appSetupRefresh(); refreshDiscState();
};
if ($('#asClearPcsx2')) $('#asClearPcsx2').onclick = async () => {
  const api = shellApi(); if (!api) return;
  await api.set_pcsx2(''); appSetupRefresh(); refreshDiscState();
};

// the Xbox recomp's folder and its game files, chosen in the system's folder dialog
async function pickRecompFolder(key) {
  const api = shellApi();
  if (!api || !api.pick_any_folder) return toast('Choosing a folder needs the Studio app', 'warn');
  let cur = '';
  try {
    const x = await post('de_x_xbox_state', {});
    cur = key === 'exe' ? (x.exe || '').split(/[\\/]/).slice(0, -1).join('\\') : (x.assets || '');
  } catch { /* none set */ }
  const d = await api.pick_any_folder(cur);
  if (!d) return;
  try {
    await post('de_x_xbox_set', key === 'exe' ? { exe: d } : { assets: d });
    toast(key === 'exe' ? 'Xbox recomp: ' + d : 'Xbox game files: ' + d, 'good');
  } catch (e) { toast(e.message, 'bad'); }
  appSetupRefresh();
}
if ($('#asPickRecomp')) $('#asPickRecomp').onclick = () => pickRecompFolder('exe');
if ($('#asPickRecompAssets')) $('#asPickRecompAssets').onclick = () => pickRecompFolder('assets');
if ($('#asClearRecomp')) $('#asClearRecomp').onclick = async () => {
  try { await post('de_x_xbox_set', { exe: '' }); } catch (e) { return toast(e.message, 'bad'); }
  appSetupRefresh();
};
if ($('#asClearRecompAssets')) $('#asClearRecompAssets').onclick = async () => {
  try { await post('de_x_xbox_set', { assets: '' }); } catch (e) { return toast(e.message, 'bad'); }
  appSetupRefresh();
};

if ($('#asPickWorking')) $('#asPickWorking').onclick = async () => {
  const api = shellApi(); if (!api) return;
  const v = await api.pick_iso('working');
  if (!v) return;
  if (v.verdict === 'reject') return toast(v.message, 'bad');
  if (v.message) toast(v.message, 'warn');
  switchDisc('working', v.path);
};
if ($('#asPickVanilla')) $('#asPickVanilla').onclick = async () => {
  const api = shellApi(); if (!api) return;
  const v = await api.pick_iso('vanilla');
  if (!v) return;
  if (v.verdict === 'reject') return toast(v.message, 'bad');
  switchDisc('vanilla', v.path);
};

// Who currently owns the working ISO. Without this, "the game won't open" is
// invisible state you have to already understand to diagnose.
async function refreshDiscState() {
  const pill = $('#isopill');
  if (!pill) return;
  try {
    const d = await api('disc_state');
    if (d.studio_holds) {
      pill.textContent = 'disc held by Studio';
      pill.className = 'sb dim';
      pill.title = 'Studio holds the disc. Play hands it over.';
    } else if (d.free_for_emulator) {
      pill.textContent = 'disc free';
      pill.className = 'sb ok';
      pill.title = d.handed_over
        ? 'Free. The Studio reclaims it on your next edit.'
        : 'Free. PCSX2 can open it.';
    } else if (d.handed_over) {
      // The good case that used to look like a failure.
      pill.textContent = 'disc held by PCSX2';
      pill.className = 'sb ok';
      pill.title = 'PCSX2 has the disc. Edits resume when it closes.';
    } else {
      pill.textContent = 'disc held by another program';
      pill.className = 'sb bad';
      pill.title = 'Another program holds the disc. Close it to edit.';
    }
    renderDiscPanel(d);
    pendingRebuild = d.pending_rebuild || [];
    renderBuildButton(d.build_waiting);
    const play = $('#btnPlay');
    if (play) {
      // a build is writing the working disc: Play waits (PCSX2 would read a half-written disc)
      play.disabled = !!d.building;
      play.style.opacity = d.building ? '.45' : '';
      if (d.building) {
        play.title = d.building + ' in progress: Play is available when it finishes.';
      } else if (pendingRebuild.length) {
        play.textContent = 'Rebuild and play';
        play.classList.add('rebuildplay');
        play.title = 'These .dat files have changes waiting for more room. They are rebuilt (once each, with every '
          + 'queued change), then PCSX2 starts:\n'
          + pendingRebuild.map((p) => `${p.archive}: ${p.files.join(', ')}`).join('\n');
      } else {
        play.textContent = 'Play';
        play.classList.remove('rebuildplay');
        play.title = 'Start the game in PCSX2.';
      }
    }
  } catch (e) {
    // The Studio is restarting; the banner is already up and the next tick
    // will clear it. Say so on the pill too rather than leaving a stale one.
    pill.textContent = 'disc: ?';
    pill.className = 'sb bad';
    pill.title = 'Cannot reach the Studio.';
  }
}
setInterval(refreshDiscState, 4000);
refreshDiscState();
$('#btnRetry').onclick = () => refreshDiscState();

// The full disc picture, on the Archives tab: which files are in play, who
// holds them, and one button for each thing you can do about it. Previously
// this lived only in a header pill and a tooltip.
async function renderDiscPanel(state) {
  const box = $('#discPanel');
  if (!box) return;
  let cfg;
  try { cfg = await api('config'); } catch { return; }
  // One short line each (DESIGN-REVIEW A2); the long form is the tooltip.
  const who = state.studio_holds ? ['Studio', 'ok', 'Disc held by Studio.',
      'Edits apply immediately; PCSX2 cannot launch until the disc is released.']
    : state.locked_by_other ? ['PCSX2 (or another program)', 'bad',
      'Disc held by another program.',
      'Edits are refused until it closes.']
    : ['nobody', '', 'Disc free.', 'Press Play to launch, or make an edit and '
      + 'the Studio will take it back automatically.'];

  box.innerHTML = '';
  const row = (label, value, kind) => {
    const d = el('div');
    d.style.cssText = 'display:flex;gap:10px;font-size:11px;padding:2px 0';
    const k = el('span', 'muted', label);
    k.style.cssText = 'width:76px;flex:none';
    const v = el('span', kind || '', value);
    v.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;'
      + 'white-space:nowrap;font:var(--t-mono);font-size:11px';
    v.title = value;
    d.append(k, v);
    return d;
  };
  box.append(row('Working ISO', cfg.modded
    + (cfg.modded_exists ? '' : '  — MISSING')));
  box.append(row('Vanilla ISO', cfg.vanilla));
  box.append(row('Emulator', cfg.pcsx2
    + (cfg.pcsx2_exists ? '' : '  — not found')));
  box.append(row('Held by', who[0], who[1]));
  const note = el('p', 'muted');
  note.style.cssText = 'font-size:11px;margin:6px 0 8px';
  note.textContent = who[2];
  note.title = who[3];
  box.append(note);

  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
  const mk = (label, cls, title, fn) => {
    const b = el('button', cls, label);
    b.title = title;
    b.onclick = fn;
    bar.append(b);
  };
  mk('Reconnect', '', 'Re-read the disc and refresh every panel.', async () => {
    await refreshDiscState();
    await loadLevels();
    await loadArchives();
    await refreshHistory();
    toast('Reconnected to ' + cfg.modded, 'good');
  });
  mk('Release disc', '', 'Hand the disc to the emulator: lets PCSX2 open the ISO.',
     async () => { await post('release', {}); await refreshDiscState();
                   toast('Disc released — PCSX2 can open it now.', 'good'); });
  // Reset to stock lives in the header only (DESIGN-REVIEW A7).
  box.append(bar);
}

// ------------------------------------------------------------ changed files
//
// The per-file answer to "what have I done to this disc", read from the
// journal rather than a full-disc scan — the 2.2 GB scan is what got the old
// "What's changed?" button removed. Each row fetches its own diff against
// stock on demand (one file, cheap) and can revert itself. "Written to" is
// the honest phrasing: a file written and then put back is still listed, and
// its diff says "identical" rather than the list quietly disagreeing with
// the disc.
//
// Grouping: by file EXTENSION, deliberately, and not by the assets.py
// taxonomy. That taxonomy reads 3D model *names* for words, and on a 2,080
// file sample of real disc filenames it answers "unclassified" 60.7% of the
// time -- grouping by it would just relabel the wall. The extension is a
// property of the name itself and misses 2 files out of 127,365 on the whole
// disc. It is already in every row of the existing payload, so this is pure
// client-side rendering: no new route, no new response field.
const TOUCH_AUTOFOLD = 60;   // above this many rows, groups start collapsed

// ".TEX" and ".tex" are the same type to a person reading the list. A name
// with no dot, or a trailing dot, or a leading dot only (".DS_Store"-shaped)
// counts as having no extension rather than inventing one.
function touchExt(name) {
  const base = String(name == null ? '' : name).split(/[\\/]/).pop();
  const dot = base.lastIndexOf('.');
  if (dot <= 0 || dot === base.length - 1) return '(no extension)';
  return '.' + base.slice(dot + 1).toLowerCase();
}

// Fold state per type, in sessionStorage so it survives the list rebuilding
// itself -- the pane re-renders on every poll, and having every group spring
// back open each time would be worse than no grouping at all.
function touchFoldKey(ext) {
  return 'rdr.touchfold.' + ext.replace(/[^a-z0-9]+/g, '-').slice(0, 32);
}

function touchFoldGet(ext, dflt) {
  try {
    const v = sessionStorage.getItem(touchFoldKey(ext));
    if (v !== null) return v === '1';
  } catch {}
  return dflt;
}

function touchFoldSet(ext, open) {
  try { sessionStorage.setItem(touchFoldKey(ext), open ? '1' : '0'); } catch {}
}

async function refreshTouched() {
  let box = $('#touchedList');
  if (!box) return;
  box.innerHTML = '';
  let h;
  try { h = await api('history'); } catch (e) {
    box.append(el('p', 'muted pad', 'Could not read the journal: ' + e.message));
    return;
  }
  const touched = h.touched || [];
  if (!touched.length) {
    box.append(el('p', 'muted pad', 'This session has not written to the disc yet.'));
    return;
  }
  // Most-changed type first; ties alphabetical so the order is stable across
  // refreshes and a group does not jump around under the pointer.
  const groups = new Map();
  for (const pair of touched) {
    const ext = touchExt(pair[1]);
    if (!groups.has(ext)) groups.set(ext, []);
    groups.get(ext).push(pair);
  }
  const order = [...groups.entries()].sort(
    (a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));

  // FINAL PASS: this list is the session's WRITE LOG, and it used to sit
  // under the title "Changed files" beside a panel saying "the working disc
  // matches stock" -- 307 changed files and no changes, on one screen. When
  // the disc measures as stock the answer leads and the log folds away.
  const onDisc = window.__onDisc;
  const clean = !!onDisc && !onDisc.files_changed;
  const listBox = box;
  if (clean) {
    const ok = el('p', 'pad');
    ok.style.cssText = 'margin:14px 0 6px';
    ok.textContent = 'Nothing is changed: your working disc matches the stock game.';
    listBox.append(ok);
    const fold = el('details');
    fold.style.cssText = 'margin:0 8px';
    const sm = el('summary', 'muted',
      `${touched.length} file(s) were written this session and put back`);
    sm.style.cssText = 'cursor:pointer;font-size:12px;padding:4px 0';
    fold.append(sm);
    listBox.append(fold);
    box = fold;
  }
  const head = el('p', 'muted pad');
  head.style.cssText = 'font-size:11px;margin:8px 0 4px';
  head.textContent = clean
    ? `${touched.length} file(s), ${order.length} type(s). Each one is `
      + 'identical to stock again.'
    : (onDisc && onDisc.files_changed
      ? `${onDisc.files_changed} file(s) differ from stock. `
      : '')
      + `${touched.length} file(s) written this session, ${order.length} `
      + 'type(s). Show diff tells you what changed in one.';
  box.append(head);

  const setters = [];
  if (order.length > 1) {
    const bar = el('div');
    bar.style.cssText = 'padding:0 8px 6px;display:flex;gap:6px';
    const all = el('button', null, 'Expand all');
    all.onclick = () => { for (const s of setters) s(true); };
    const none = el('button', null, 'Collapse all');
    none.onclick = () => { for (const s of setters) s(false); };
    bar.append(all, none);
    box.append(bar);
  }

  const t = el('table');
  // One row builder, used by every group -- the click behaviour of a row
  // (Show diff, Revert this file) is exactly what it was before grouping.
  const makeRow = (archive, name) => {
    const tr = el('tr');
    tr.style.cursor = 'default';
    tr.append(el('td', 'muted', archive), el('td', null, name));

    const dtd = el('td');
    const showBtn = el('button', null, 'Show diff');
    showBtn.title = 'Compare this one file on the working disc against stock.';
    showBtn.onclick = async () => {
      showBtn.disabled = true;
      showBtn.textContent = 'reading…';
      try {
        const r = await api(`file_diff?archive=${encodeURIComponent(archive)}`
                          + `&name=${encodeURIComponent(name)}`);
        const pre = el('pre');
        pre.style.cssText =
          'margin:6px 0 12px;padding:8px;background:var(--well);border:1px ' +
          'solid var(--line);border-radius:4px;max-height:320px;overflow:auto;' +
          'font:12px/1.4 Consolas,monospace;white-space:pre';
        if (r.identical) {
          pre.textContent =
            'identical to stock — this file was written and then put back';
        } else if (r.binary) {
          pre.textContent = `binary file — ${r.stock_size} → ${r.size} bytes `
            + '(no text diff)';
        } else {
          // Coloured spans, built as DOM nodes: diff lines carry < > & freely.
          for (const l of r.lines) {
            const span = el('span', null, l + '\n');
            if (l.startsWith('+') && !l.startsWith('+++'))
              span.style.color = 'var(--grn)';
            else if (l.startsWith('-') && !l.startsWith('---'))
              span.style.color = 'var(--red)';
            else if (l.startsWith('@@')) span.style.color = 'var(--warn)';
            pre.append(span);
          }
          if (r.truncated) pre.append(el('span', 'muted', '… truncated'));
        }
        const row = el('tr');
        const cell = el('td');
        cell.colSpan = 4;
        cell.append(pre);
        row.append(cell);
        tr.after(row);
        showBtn.textContent = 'shown';
      } catch (e) {
        toast('Diff failed: ' + e.message, 'bad');
        showBtn.disabled = false;
        showBtn.textContent = 'Show diff';
      }
    };
    dtd.append(showBtn);
    tr.append(dtd);

    const td = el('td');
    const b = el('button', '', 'Revert this file');
    b.title = 'Restore this file to stock. Undoable.';
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = 'reverting…';
      try {
        await post('revert_file', { archive, name });
        tr.style.opacity = '0.4';
        b.textContent = 'reverted';
        toast(`${name} restored to stock.`, 'good');
        refreshHistory();
        if (level && level.archive === archive) reloadLevel(true, true);
      } catch (e) {
        b.disabled = false;
        b.textContent = 'Revert this file';
        toast('Revert failed: ' + e.message, 'bad');
      }
    };
    td.append(b);
    tr.append(td);
    return tr;
  };

  // A header <tbody> that always shows, then a body <tbody> that folds. Same
  // table throughout, so the four columns line up across every group, and a
  // diff opened with tr.after() lands inside its own group and folds with it.
  const dflt = touched.length <= TOUCH_AUTOFOLD;
  for (const [ext, rows] of order) {
    const hb = el('tbody');
    const htr = el('tr', 'tgrouphd');
    const htd = el('td');
    htd.colSpan = 4;
    htd.append(el('span', 'chev', '▸'), el('span', null, ext),
               el('span', 'cnt', `${rows.length} file(s)`));
    htr.append(htd);
    hb.append(htr);

    const gb = el('tbody');
    for (const [archive, name] of rows) gb.append(makeRow(archive, name));

    // `persist` is false only for the opening render: a group that is closed
    // merely because the session is large has not been *decided* about, and
    // writing that default down would freeze it once the session grows.
    const set = (open, persist) => {
      gb.style.display = open ? '' : 'none';
      htr.classList.toggle('open', open);
      if (persist !== false) touchFoldSet(ext, open);
    };
    setters.push(set);
    htr.onclick = () => set(!htr.classList.contains('open'));
    htr.title = `Show or hide the ${rows.length} ${ext} file(s).`;
    set(touchFoldGet(ext, dflt), false);
    t.append(hb, gb);
  }
  box.append(t);

  const total = el('p', 'muted pad');
  total.style.cssText = 'font-size:11px;margin:6px 0 8px';
  total.textContent = `Total: ${touched.length} file(s) across `
    + `${order.length} file type(s).`;
  box.append(total);
}

// ------------------------------------------------- place files (drag & drop)
//
// Built for a showcase video, so the contract is visible at all times: the
// target archive is a bold picker, dragging anywhere over the Files tab
// raises an overlay NAMING that archive, and nothing writes without a
// per-file plan on screen first. Replaces are journalled (Ctrl+Z); brand-new
// files are added through the TOC-verifying add path and the plan says
// plainly that an add cannot be undone.

let dropPending = [];       // [{name, b64, plan}]

async function fillDropArc() {
  const sel = $('#dropArc');
  if (!sel || sel.dataset.filled) return;
  try {
    const arcs = await api('archives');
    for (const a of arcs.archives || arcs || []) {
      const nm = a.archive || a.name || a;
      sel.append(new Option(nm, nm));
    }
    sel.dataset.filled = '1';
    sel.value = [...sel.options].some((o) => o.value === 'SWAT.DAT')
      ? 'SWAT.DAT' : (sel.options[0]?.value || '');
  } catch { /* retried on next tab open */ }
}

function dropPlanBox() { return $('#dropPlan'); }

async function stageDropFiles(files) {
  const arc = $('#dropArc').value;
  if (!arc) return toast('Pick an archive to place into first.', 'bad');
  if (!files || !files.length) return;
  const box = dropPlanBox();
  box.innerHTML = '';
  dropPending = [];
  for (const f of files) {
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < buf.length; i += CHUNK)
      bin += String.fromCharCode.apply(null, buf.subarray(i, i + CHUNK));
    const b64 = btoa(bin);
    let plan;
    try {
      plan = await post('file_drop',
        { archive: arc, name: f.name, data_b64: b64, dry: true });
    } catch (e) {
      plan = { error: e.message };
    }
    dropPending.push({ name: f.name, b64, plan });
  }
  renderDropPlan(arc);
}

function renderDropPlan(arc) {
  const box = dropPlanBox();
  box.innerHTML = '';
  if (!dropPending.length) return;
  let blocked = 0;
  for (const p of dropPending) {
    const row = el('div');
    row.style.cssText = 'font-size:11px;padding:3px 0;line-height:1.4;'
      + 'border-top:1px solid var(--line)';
    const d = p.plan || {};
    let verdict;
    if (d.error && !d.mode) { verdict = d.error; blocked += 1; }
    else if (d.mode === 'replace') {
      verdict = d.rebuild
        ? `Too big for its slot: ${arc} is rebuilt to make room on the next Rebuild and play. Undoable.`
        : d.fits
        ? `Replaces the copy in ${arc}. Undoable.`
        : d.grow_possible && allowGrow
          ? `Replaces and grows the file. Undoable.`
          : d.grow_possible
            ? `Too big for its slot. Tick “allow files to grow” in View settings.`
            : `Too big. No free gap in this archive.`;
      if (!d.fits && !(d.grow_possible && allowGrow)) blocked += 1;
    } else {
      const why = (d.why || []).join('; ');
      verdict = why
        ? `Cannot add: ${why}`
        : d.rebuild
        ? `New file in ${arc}: no room as it stands, so it goes in when ${arc} is rebuilt on the next Rebuild and play. Adds cannot be undone.`
        : `New file in ${arc}. Adds cannot be undone.`;
      if (why) blocked += 1;
    }
    row.append(el('b', null, p.name));
    row.append(el('div', 'muted', verdict));
    box.append(row);
  }
  const ok = dropPending.length - blocked;
  const go = el('button', ok ? 'primary' : '', blocked
    ? `Place ${ok} of ${dropPending.length} (${blocked} blocked)`
    : `Place ${ok} file${ok === 1 ? '' : 's'} into ${arc}`);
  go.style.cssText = 'width:100%;margin-top:6px';
  go.disabled = !ok;
  go.onclick = () => applyDropPlan(arc);
  box.append(go);
  const clr = el('button', '', 'Clear');
  clr.style.cssText = 'width:100%;margin-top:4px;font-size:11px';
  clr.onclick = () => { dropPending = []; box.innerHTML = ''; };
  box.append(clr);
}

async function applyDropPlan(arc) {
  const done = [], failed = [];
  for (const p of dropPending) {
    const d = p.plan || {};
    const writable = (d.mode === 'replace' && (d.fits || (d.grow_possible && allowGrow)))
      || (d.mode === 'add' && !(d.why || []).length);
    if (!writable) continue;
    try {
      const r = await post('file_drop',
        { archive: arc, name: p.name, data_b64: p.b64, grow: allowGrow });
      (r.ok ? done : failed).push(p.name + (r.ok ? '' : ` (${r.error || 'refused'})`));
    } catch (e) { failed.push(`${p.name} (${e.message})`); }
  }
  dropPending = [];
  dropPlanBox().innerHTML = '';
  if (done.length)
    toast(`Placed ${done.length} file${done.length === 1 ? '' : 's'} into ${arc}: `
      + done.join(', '), 'good');
  if (failed.length) toast('Not placed: ' + failed.join('; '), 'bad');
  refreshHistory();
  if (currentArc === arc) loadToc(arc);
}

$('#dropZone').onclick = () => $('#dropBrowse').click();
$('#dropBrowse').onchange = () => {
  stageDropFiles([...$('#dropBrowse').files]);
  $('#dropBrowse').value = '';
};
{
  const tab = $('#tab-text');
  const overlay = $('#dropOverlay');
  let depth = 0;
  tab.addEventListener('dragenter', (e) => {
    if (![...e.dataTransfer.types].includes('Files')) return;
    e.preventDefault();
    depth += 1;
    overlay.textContent = 'Drop to place into ' + ($('#dropArc').value || '…');
    overlay.style.display = 'flex';
  });
  tab.addEventListener('dragover', (e) => { e.preventDefault(); });
  tab.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) overlay.style.display = 'none';
  });
  tab.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    overlay.style.display = 'none';
    stageDropFiles([...e.dataTransfer.files]);
  });
}

$('#btnSearch').onclick = doSearch;
$('#searchBox').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
// Results live beside the viewer now, so finding a file and opening it are
// one motion instead of two tabs.
function searchRow(title, sub, onOpen) {
  const row = el('div', 'row');
  row.style.cssText = 'display:block; cursor:pointer; padding:4px 9px';
  const t = el('div', '', title);
  t.style.cssText = 'font-size:11px; word-break:break-all';
  const s = el('div', 'muted', sub);
  s.style.cssText = 'font-size:11px; word-break:break-all';
  row.append(t, s);
  row.onclick = () => {
    for (const o of $('#searchResults').querySelectorAll('.row'))
      o.classList.remove('sel');
    row.classList.add('sel');
    onOpen();
  };
  return row;
}

async function doSearch() {
  const q = $('#searchBox').value.trim();
  const mode = $('#searchMode').value;
  const ext = $('#searchExt').value.trim();
  const box = $('#searchResults');
  const info = $('#searchInfo');

  if (mode === 'content') {
    if (q.length < 3) return toast('Content search needs at least 3 characters.');
    box.innerHTML = '<p class="muted pad" style="font-size:11px">'
      + 'decompressing every file on the disc…</p>';
    const r = await api('search_content?q=' + encodeURIComponent(q)
      + (ext ? '&ext=' + encodeURIComponent(ext) : ''));
    box.innerHTML = '';
    for (const h of r.hits)
      box.append(searchRow(h.name,
        `${h.archive.split('/').pop()} · ${h.context}`,
        () => openTextFile(h.archive, h.name)));
    info.textContent =
      `${r.hits.length} file(s)${r.capped ? ' (capped)' : ''} across `
      + `${r.archives} archives`;
    if (!r.hits.length)
      box.innerHTML = '<p class="muted pad" style="font-size:11px">no matches</p>';
    return;
  }

  if (q.length < 2) return toast('Type at least 2 characters.');
  box.innerHTML = '<p class="muted pad" style="font-size:11px">'
    + 'indexing all 66 archives…</p>';
  // The route now returns {total, shown, truncated, hits} rather than a bare
  // array, so the count on screen can be the TRUE one.
  const res = await api('search?q=' + encodeURIComponent(q) + '&limit=2000');
  let hits = Array.isArray(res) ? res : (res.hits || []);
  const searchTotal = Array.isArray(res) ? hits.length : res.total;
  const searchCut = !Array.isArray(res) && res.truncated;
  // The ext box was read but only ever applied to content search, while the
  // control stayed visible in name mode looking like it did something.
  if (ext) {
    const want = ext.replace(/^\./, '').toLowerCase();
    hits = hits.filter((h) => h.name.toLowerCase().endsWith('.' + want));
  }
  box.innerHTML = '';
  for (const h of hits)
    box.append(searchRow(h.name,
      `${h.n} cop${h.n === 1 ? 'y' : 'ies'} · `
      + h.archives.slice(0, 3).map((a) => a.split('/').pop()).join(', ')
      + (h.n > 3 ? ` +${h.n - 3}` : ''),
      () => openTextFile(h.archives[0], h.name)));
  info.textContent = (ext
      ? `${hits.length} of ${searchTotal} filename(s) end .${ext.replace(/^\./, '')}`
      : `${searchTotal.toLocaleString()} filename(s)`)
    + (searchCut ? ` — showing the first ${hits.length}` : '');
  if (!hits.length)
    box.innerHTML = '<p class="muted pad" style="font-size:11px">no matches</p>';
}

async function refreshLog() {
  try {
    // The pane belongs to the activity log alone now — per-file diffs have
    // their own pane (refreshTouched), so the poll can no longer overwrite
    // anything but its own text. The visibility test is "is the Changes tab
    // open AND is the activity view the one on show": the old test was for a
    // #tab-log that no longer exists, which made this return silently and
    // leave the pane blank.
    if (!$('#tab-mods').classList.contains('on')) return;
    if ($('#logView').style.display === 'none') return;
    const lines = await api('log');
    $('#logPane').textContent = lines.join('\n');
  } catch {}
}
setInterval(refreshLog, 3000);

// Collapsible Keys panel. Remembered, because a reference you have to
// re-open every session is one you stop using.
// Every panel heading folds, and remembers whether it was open.
//
// The 3D editor stacks eight sections down its two side panels and the
// Inspector alone has six. Without this you scroll past four things you are
// not using to reach the one you are. Defaults below are "what you need on
// the way in"; everything else starts closed and stays however you leave it.
// "Areas in this archive" is deliberately NOT here: it only appears at all
// when the archive has more than one area, so when it is on screen it is
// always relevant.
const FOLD_DEFAULT_CLOSED = new Set([
  // 'used by' and 'where it lives' are NOT here: the Surfaces empty state
  // promises "Click a texture to see every model that draws with it", and a
  // default-closed fold made that promise land in a section you could not
  // see was there.
  'game lighting', 'place an asset',
  // Set once and left alone: camera mode, the grid, how collision and
  // surfaces are drawn, character detail. It was all sitting above the first
  // layer row until now.
  'view settings',
]);

// The heading's OWN words, ignoring the <span> badges that carry live counts.
// Keying on the full text meant a section called "Objects (536)" got a
// different key the moment the count changed, silently orphaning the state the
// user had set.
function foldLabel(head) {
  let s = '';
  for (const n of head.childNodes) if (n.nodeType === 3) s += n.textContent;
  return (s || head.textContent).trim().toLowerCase().replace(/\s*▸\s*$/, '');
}

function foldKey(head) {
  return 'rdr.fold.' + foldLabel(head).replace(/[^a-z0-9]+/g, '-').slice(0, 40);
}

function setFold(head, open) {
  // The body is every sibling up to the next heading, so a section can be
  // several elements (a search box, a list, a note) without extra markup.
  for (let n = head.nextElementSibling; n; n = n.nextElementSibling) {
    if (n.tagName === 'H2') break;
    n.style.display = open ? '' : 'none';
  }
  head.classList.toggle('open', open);
  try { localStorage.setItem(foldKey(head), open ? '1' : '0'); } catch {}
}

function initFolds(root) {
  for (const head of (root || document).querySelectorAll('.panel h2')) {
    if (head.dataset.fold) continue;
    head.dataset.fold = '1';
    head.classList.add('fold');
    if (!head.querySelector('.chev')) {
      const c = el('span', 'chev', '▸');
      head.append(c);
    }
    let open = !FOLD_DEFAULT_CLOSED.has(foldLabel(head));
    try {
      const saved = localStorage.getItem(foldKey(head));
      if (saved !== null) open = saved === '1';
    } catch {}
    setFold(head, open);
    head.onclick = () => setFold(head, !head.classList.contains('open'));
  }
}
initFolds();

// The on-screen legend. Collapsed state is remembered, and H toggles it --
// in fullscreen it is the only reference the user has left.
const legend = $('#legend');
function setLegend(min) {
  legend.classList.toggle('min', min);
  $('#btnLegend').textContent = min ? '+' : '–';
  $('#btnLegend').title = min ? 'Show controls (H)' : 'Hide (H)';
  try { localStorage.setItem('rdr.legend', min ? '1' : '0'); } catch {}
}
// The Roadmap tab is gone, and `$('#roadmapReload').onclick = loadRoadmap`
// outlived it here. A top-level assignment against an element that no longer
// exists throws during module evaluation, which kills the WHOLE boot script:
// init() never runs, so the loading overlay sits on its static "reading the
// disc..." text forever with the bar at 2%, while the backend is perfectly
// healthy behind it (warm-up finished in 9.3s; every boot route answered in
// under 0.02s).
//
// Third time this exact shape has cost this project a session, and the second
// in one evening -- the guard on btnDiff was written for precisely this
// reason, then this one was left unguarded an hour later. jsgate only proves
// the file PARSES; a null dereference at module scope is valid syntax.
$('#btnLegend').onclick = (e) => {
  e.stopPropagation();
  setLegend(!legend.classList.contains('min'));
};
// Collapsed on FIRST RUN, then however you left it. The legend is a 236x215
// sheet -- 9.4% of the viewport -- parked over the level, and thirteen
// bindings you have already learned are not worth that every session. H and
// the +/- button bring it straight back, and the stored choice always wins.
try {
  const saved = localStorage.getItem('rdr.legend');
  setLegend(saved === null ? true : saved === '1');
} catch { setLegend(true); }

// The HUD's telemetry line, same rule: folded until asked for, then
// remembered.
{
  const hud = $('#hud'), tog = $('#hudToggle');
  if (hud && tog) {
    const setHudMore = (on) => {
      hud.classList.toggle('more', on);
      tog.textContent = on ? 'details ▾' : 'details ▸';
      try { localStorage.setItem('rdr.hudmore', on ? '1' : '0'); } catch {}
    };
    let on = false;
    try { on = localStorage.getItem('rdr.hudmore') === '1'; } catch {}
    setHudMore(on);
    tog.onclick = (e) => {
      e.stopPropagation();
      setHudMore(!hud.classList.contains('more'));
    };
  }
}

$('#btnFull').onclick = toggleFullscreen;
document.addEventListener('fullscreenchange', () => {
  const on = document.fullscreenElement === view;
  $('#btnFull').textContent = on ? 'Exit full screen' : 'Full screen';
  $('#btnFull').title = on ? 'Leave fullscreen (Esc or F11)'
                           : 'Fullscreen the viewport (F11)';
});

$('#btnUndo').onclick = doUndo;
$('#btnRedo').onclick = doRedo;
$('#gunFilter').oninput = loadGuns;
$('#btnNwCheck').onclick = checkNewWeapon;
$('#btnNwOpen').onclick = () => { $('#nwPanel').style.display = 'flex'; };
$('#btnNwClose').onclick = () => { $('#nwPanel').style.display = 'none'; };
$('#btnNwWrite').onclick = writeNewWeapon;
$('#crFilter').oninput = loadCreatures;
$('#btnCrSave').onclick = saveCreature;
$('#btnCrReset').onclick = () => {
  if (!crCurrent) return;
  crEdits = {};
  swEdits = {};
  for (const [k, v] of Object.entries(crCurrent.stock))
    if (k in crCurrent.fields && crCurrent.fields[k] !== v) crEdits[k] = v;
  // The Combat section is part of the same panel, so Reset covers it too --
  // resetting the .cr half alone would leave edited damage numbers standing
  // while claiming the character is back to stock.
  for (const g of crCurrent.combat || []) {
    for (const row of g.fields) {
      const stk = crCurrent.combat_stock?.[row.field];
      if (stk === undefined) continue;
      const a = parseFloat(row.value), b = parseFloat(stk);
      const differs = Number.isNaN(a) || Number.isNaN(b)
        ? String(row.value) !== String(stk) : Math.abs(a - b) > 1e-9;
      if (differs) swEdits[row.field] = stk;
    }
  }
  const n = Object.keys(crEdits).length + Object.keys(swEdits).length;
  if (!n) return toast('Already at stock values.');
  $('#btnCrSave').disabled = false;
  renderCrFields();
  toast(`${n} field(s) queued back to stock — press Apply.`);
};
$('#unlockType').onchange = renderUnlockables;
$('#unlockFilter').oninput = renderUnlockables;
$('#itemFilter').oninput = renderItems;

$('#btnFreeShop').onclick = async () => {
  if (!campaign) return;
  const priced = campaign.items.filter((i) =>
    ['shop', 'repair', 'upgrade'].some((k) => parseFloat(i[k]) > 0));
  if (!priced.length) return toast('Everything is already free.');
  if (!confirm(`Set shop, repair and upgrade costs to 0 on ${priced.length} ` +
               `item(s)?\n\nThat is ${priced.length * 3} single-cell edits to ` +
               `invmasterlist.csv. Ctrl+Z undoes them one at a time.`)) return;
  const btn = $('#btnFreeShop');
  btn.disabled = true;
  let n = 0;
  try {
    for (const it of priced) {
      for (const key of ['shop', 'repair', 'upgrade']) {
        if (!(parseFloat(it[key]) > 0)) continue;
        await post('campaign_cell',
          { file: 'items', row: it.index, col: ITEM_COLS[key], value: 0 });
        it[key] = '0';
        n++;
      }
    }
    toast(`Zeroed ${n} price(s) across ${priced.length} items.`, 'good');
    renderItems();
    refreshHistory();
  } catch (e) {
    toast(`Stopped after ${n} edit(s): ${e.message}`, 'bad');
  }
  btn.disabled = false;
};

$('#btnOpenLevel').onclick = async () => {
  const m = campaign?.missions[missionSel];
  if (!m?.archive) return;
  // Same rule as the stage rows above: drive the loader, not the sidebar's
  // filtered DOM. With "saloon" left in the filter box this button used to
  // toast "not in the list" about a level that is very much on the disc.
  showTab('editor');
  loadLevelByRef(m.archive, null, null);
};

$('#btnUnlockAll').onclick = async () => {
  const type = $('#unlockType').value;
  const what = type || 'every entry';
  if (!confirm(`Set Default=1 for ${what} in unlockables.csv?\n` +
               `This is the correct switch for making cheats and characters available.`)) return;
  try {
    const r = await post('unlock_all', { type });
    toast(r.changed ? `Unlocked ${r.changed} entries.` : 'Already all unlocked.', 'good');
    campaign = null;
    rules = null;
    await loadCampaign();
    refreshHistory();
  } catch (e) { toast('Failed: ' + e.message, 'bad'); }
};
$('#btnGunSave').onclick = saveGun;
$('#btnGunReset').onclick = () => {
  if (!gunCurrent) return;
  gunEdits = {};
  for (const [k, v] of Object.entries(gunCurrent.stock))
    if (k in gunCurrent.weapon.fields &&
        gunCurrent.weapon.fields[k] !== v) gunEdits[k] = v;
  if (!Object.keys(gunEdits).length) return toast('Already at stock values.');
  $('#btnGunSave').disabled = false;
  renderGunFields();
  toast(`${Object.keys(gunEdits).length} field(s) queued back to stock — press Apply.`);
};
$('#btnRefreshMods').onclick = () => { refreshTouched(); refreshHistory(); };
$('#capLevel').onchange = (e) => loadCapacity(e.target.value);

// #btnSaveMod, #btnShareMod and #btnImportMod are wired by export.js: one
// way to make a mod file, shared with the Export dialog and Versions.

addEventListener('keydown', (e) => {
  const typing = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA';
  // Ctrl+K works even mid-typing -- it is the escape hatch, so it must not
  // depend on where the caret happens to be.
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    return $('#palette').classList.contains('on') ? closePalette() : openPalette();
  }
  if ((e.ctrlKey || e.metaKey) && !typing) {
    if (e.key === 'z') { e.preventDefault(); return doUndo(); }
    if (e.key === 'y' || (e.key === 'Z' && e.shiftKey)) { e.preventDefault(); return doRedo(); }
    if (e.key === 'a' && level) {
      e.preventDefault();
      const skipped = selectAllVisible();
      return toast(`Selected ${selection.size} objects`
        + (skipped ? ` — ${skipped} hidden one(s) left out` : ''));
    }
  }
  if (typing) return;
  // The sheet is a modal overlay, so Escape belongs to it first.
  if (e.key === 'Escape' && $('#sheet').classList.contains('open'))
    return closeSheet();
  // Editor shortcuts belong to the editor. `w` used to switch on walk mode
  // from the Weapons tab, where the viewport is not even on screen, and then
  // swallow every other key until you found your way back and pressed Esc.
  if (!$('#tab-editor').classList.contains('on') && !e.ctrlKey && !e.metaKey)
    return;

  if (e.key === 'F11' && $('#tab-editor').classList.contains('on')) {
    e.preventDefault();
    return toggleFullscreen();
  }
  if (e.key === 'h' && $('#tab-editor').classList.contains('on'))
    return setLegend(!legend.classList.contains('min'));

  // Arrow / PgUp / PgDn nudge the whole selection by a fixed step -- much
  // easier than dragging when you want an exact offset.
  if (selection.size && !walk.on && !trackLayout) {
    const step = e.shiftKey ? 1.0 : 0.1;
    const d = { ArrowLeft: [-step, 0, 0], ArrowRight: [step, 0, 0],
                ArrowUp: [0, 0, -step], ArrowDown: [0, 0, step],
                PageUp: [0, step, 0], PageDown: [0, -step, 0] }[e.key];
    if (d) {
      e.preventDefault();
      // One nudge in flight at a time.
      //
      // Each nudge is a write plus a level reload, and the base position comes
      // from `n.pos`, which only updates when that reload lands. Holding an
      // arrow key fired a burst of presses that all read the SAME base and all
      // wrote the same destination -- so ten keypresses moved the object 0.1 m
      // and left ten identical entries in the undo history.
      if (nudgeBusy) return;
      const changes = [];
      for (const u of selection) {
        const n = nodeOf(u);
        if (!n || n.readonly) continue;
        // Home first, then the optimistic mutation. The other order left a
        // refused nudge exactly where the refusal happened -- and the next
        // Ctrl+Z had nothing recorded to go back to.
        rememberHome(u);
        // Keep the marker under the cursor honest while the write is away.
        const p = [n.pos[0] + d[0], n.pos[1] + d[1], n.pos[2] + d[2]];
        n.pos = p;
        const o = meshes.get(u);
        if (o) o.position.set(p[0], p[1] + (o.userData.yOffset || 0), p[2]);
        changes.push({ uid: u, pos: p });
      }
      if (changes.length) {
        nudgeBusy = true;
        Promise.resolve(pushChanges(changes)).finally(() => { nudgeBusy = false; });
      }
      return;
    }
  }

  if (e.key === 'Escape' && placeMode) return setPlaceMode(false);
  // No bare letter arms a mode where clicks write. `p` used to, with only a
  // crosshair as the tell -- the Inspector button is the way in now.
  if (e.key === 'Escape' && walk.on) return setWalk(false);
  // W no longer teleports you to a spawn point. Flying is a way of getting
  // around the editor; being dropped to head height somewhere else is not.
  // WASD now moves the camera wherever it is pointing, all the time -- see
  // stepFly in the render loop.
  if (walk.on) return;   // movement keys belong to the walker
  if (e.key === 'g') gizmo.setMode('translate');
  if (e.key === 'r') gizmo.setMode('rotate');
  if (e.key === 't') gizmo.setMode('scale');
  if (e.key === 'f' && selected != null) focusOn(selected);
  // No bare letter ever mutates the level. Ctrl+D and Delete are the whole
  // list. Bare `d` is the strafe-right key and used to duplicate on repeat.
  if ((e.key === 'd' || e.key === 'D') && (e.ctrlKey || e.metaKey)
      && selected != null && $('#tab-editor').classList.contains('on')) {
    e.preventDefault(); duplicateSelected();
  }
  if (e.key === 'Delete' && selected != null) deleteSelected();
  if (e.key === 'Escape') select(null);
});

// --------------------------------------------------------------- animation tab
//
// A dedicated place to WATCH animations, not just read their curves. The decode
// and the forward kinematics happen entirely in the browser (anim3d.js) from
// the raw .anim bytes and the raw .skel text, so this needed no new server code.
//
// The standing caveat, shown on screen: the channel-to-bone mapping is
// confirmed but the Euler composition order is not, so the skeleton moves
// correctly overall while some multi-axis joints may twist until that is
// settled -- which is why the order is selectable here.
let animPlayer = null;
let animLoadedArchive = null;
let animCurrent = null;

async function initAnimTab() {
  const arcSel = $('#animArc');
  if (!arcSel.options.length) {
    try {
      const arcs = await api('archives');
      // banks.dat, pupshow.dat and the streams hold sound, cutscene and audio streams, never a clip: not offered
      const list = (arcs.archives || arcs || []).map((a) => a.archive || a.name || a)
        .filter((a) => !/^(BANKS|PUPSHOW|STREAMS?\d*)\.DAT$/i.test(String(a).split('/').pop()));
      // Animations live overwhelmingly in SWAT.DAT (the shared asset bundle),
      // so it is the sensible default; every archive is still selectable.
      list.sort((a, b) => (a === 'SWAT.DAT' ? -1 : b === 'SWAT.DAT' ? 1
        : a.localeCompare(b)));
      for (const a of list) {
        const o = el('option', null, a);
        o.value = a;
        arcSel.append(o);
      }
    } catch (e) { $('#animMsg').textContent = 'Could not list archives: ' + e.message; }
    arcSel.onchange = () => loadAnimList(arcSel.value);
    $('#animFilter').oninput = renderAnimList;
    $('#animSkel').onchange = () => { animSkelPicked = true; if (animCurrent) playAnim(animCurrent); };
    // Preferred skeleton / model: tried first on every clip (kept as rdr.* keys, so profiles carry them too)
    for (const k of ['Skel', 'Model']) {
      const inp = $('#animPref' + k);
      if (!inp) continue;
      inp.value = animPref(k);
      const save = () => {
        try { localStorage.setItem('rdr.anim.pref' + k, inp.value.trim()); } catch { /* private mode */ }
        remember('animPref' + k, inp.value.trim());      // settings.json: kept across launches and in profiles
        if (k === 'Model' && animPlayer && animRigModels.length) {
          const hit = animPrefModel(animRigModels);
          if (hit && $('#animModel').value !== hit) { $('#animModel').value = hit; applyAnimModel(); }
        }
        if (k === 'Skel' && animCurrent && animPrefSkel([...$('#animSkel').options].map((o) => o.value))
            && animPrefSkel([...$('#animSkel').options].map((o) => o.value)) !== $('#animSkel').value) playAnim(animCurrent);
      };
      inp.onchange = save;
      $('#animPref' + k + 'Set').onclick = () => {
        const v = (k === 'Skel' ? $('#animSkel') : $('#animModel')).value;
        if (!v) return toast('Pick a ' + (k === 'Skel' ? 'skeleton' : 'model') + ' above first.', 'bad');
        inp.value = v;
        save();
        toast(`Preferred ${k === 'Skel' ? 'skeleton' : 'model'}: ${v}. It is tried first on every clip.`, 'good');
      };
    }
    // Changing the model does NOT reload the clip. The player keeps playing
    // and swaps the mesh onto the bones it already has, so comparing two
    // characters on one motion is a dropdown, not a round trip.
    $('#animModel').onchange = () => applyAnimModel();
    $('#animShow').onchange = () => {
      if (animPlayer) animPlayer.setShow($('#animShow').value);
    };
    // Populate the Euler dropdown once we have a player's list; seed it now.
    // YZX first: it is the order the engine uses (see bvh.py), so it is
    // both the default and the top of the list.
    for (const o of ['YZX', 'XYZ', 'XZY', 'YXZ', 'ZXY', 'ZYX']) {
      const x = el('option', null, o + (o === 'YZX' ? '  (engine)' : ''));
      x.value = o;
      $('#animEuler').append(x);
    }
    $('#animEuler').onchange = () => {
      if (animPlayer) { animPlayer.setOrder($('#animEuler').value); }
    };
    // F frames the view on the character, as Blender does (DESIGN-REVIEW B8).
    document.addEventListener('keydown', (e) => {
      if ((e.key !== 'f' && e.key !== 'F') || e.ctrlKey || e.metaKey || e.altKey) return;
      if (!$('#tab-anim').classList.contains('on') || !animPlayer) return;
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '')) return;
      animPlayer.reframe();
    });
    $('#animPlay').onclick = () => {
      if (!animPlayer) return;
      if (animPlayer.playing) { animPlayer.pause(); $('#animPlay').textContent = 'Play'; }
      else { animPlayer.play(); $('#animPlay').textContent = 'Pause'; }
    };
    $('#animScrub').oninput = () => {
      if (!animPlayer) return;
      animPlayer.pause();
      $('#animPlay').textContent = 'Play';
      animPlayer.setFrame(+$('#animScrub').value);
    };
    $('#animExportBtn').onclick = exportCurrentAnim;
    $('#animExportGlbBtn').onclick = exportAnimWithModel;
    $('#animImportBtn').onclick = () => {
      if (!animCurrent) return toast('Pick a clip first.');
      $('#animImportFile').click();
    };
    $('#animImportFile').onchange = (e) => {
      const f = e.target.files && e.target.files[0];
      e.target.value = '';                 // re-picking the same file must fire
      if (f) importAnimFile(f);
    };
  }
  if (animLoadedArchive == null) loadAnimList(arcSel.value || 'SWAT.DAT');
}

// Jump to the Animation tab with a specific clip loaded and playing. Used by
// the file viewer, so looking at a .anim in Files is one click from watching
// it rather than a dead end with a download button.
async function openAnimClip(archive, name) {
  showTab('anim');
  const arcSel = $('#animArc');
  // initAnimTab fills the archive list on first open; wait for it.
  for (let i = 0; i < 60 && !arcSel.options.length; i++)
    await new Promise((r) => setTimeout(r, 100));
  if ([...arcSel.options].some((o) => o.value === archive)
      && arcSel.value !== archive) {
    arcSel.value = archive;
    await loadAnimList(archive);
  } else if (animLoadedArchive !== archive) {
    await loadAnimList(archive);
  }
  $('#animFilter').value = name;
  renderAnimList();
  playAnim(name);
}

// ---- the Blender round trip, driven from the clip you are watching

function animIoReport(html, tone) {
  const box = $('#animIoReport');
  box.style.display = 'block';
  box.style.borderColor = tone === 'bad' ? 'var(--bad)'
    : tone === 'good' ? 'var(--good)' : 'var(--line)';
  box.innerHTML = html;
}

async function exportAnimWithModel() {
  if (!animPlayer || !animCurrent) return toast('Pick a clip first.');
  if (!animPlayer.modelInfo) return toast('Pick a character model (Character model, above) first.', 'bad');
  const btn = $('#animExportGlbBtn');
  btn.disabled = true;
  try {
    const r = await animPlayer.exportGlb(animCurrent);
    const stem = animCurrent.replace(/\.anim$/i, '') + '_' + String(animPlayer.modelInfo.stem || 'model').replace(/[^A-Za-z0-9_-]+/g, '_');
    const href = URL.createObjectURL(new Blob([r.bytes], { type: 'model/gltf-binary' }));
    const a = document.createElement('a');
    a.href = href; a.download = stem + '.glb'; a.click();
    setTimeout(() => URL.revokeObjectURL(href), 30000);
    animIoReport(`Exported <b>${animEsc(stem)}.glb</b>: ${r.verts.toLocaleString()} vertices, ${r.bones} bones, `
      + `${r.keys} keys, ${r.textures} texture${r.textures === 1 ? '' : 's'}.`
      + '<br>Blender: File &gt; Import &gt; glTF 2.0 (.glb) — the model comes in posed and animated.', 'good');
  } catch (e) {
    toast('Could not export it: ' + e.message, 'bad');
  } finally {
    btn.disabled = false;
  }
}

function exportCurrentAnim() {
  if (!animCurrent) return toast('Pick a clip first.');
  const sk = $('#animSkel').value;
  if (!sk) return toast('No skeleton in this archive fits this clip, so there '
    + 'is nothing to rig it to.');
  const eu = $('#animEuler').value || 'YZX';
  saveFile(`/api/anim_bvh?archive=${encodeURIComponent(animLoadedArchive)}`
    + `&name=${encodeURIComponent(animCurrent)}`
    + `&skeleton=${encodeURIComponent(sk)}&euler=${eu}`,
    animCurrent.replace(/\.anim$/i, '') + '.bvh').then((ok) => { if (ok)
      animIoReport(`Exported <b>${animCurrent}</b> against ${sk}, Euler ${eu}.`
        + '<br>Edit it in Blender, then Import .bvh to bring it back.', 'good'); });
}

// Four things `bvh_to_anim` now does on the user's behalf, each reported for
// the same reason it is done at all: the point of the change is that it does
// NOT happen quietly. The keys ride through the `anim_import` route untouched,
// so this is the only place they surface. Filed by rdr-3d in LANE-REQUESTS.md.
//
// Everything here is composed from the BVH the user supplied -- joint names
// come straight out of the file -- so every value is escaped before it reaches
// innerHTML.
const ANIM_CODECS = { cm2: 'cm2', cmp: 'cmp', raw: 'uncompressed' };
const animEsc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;');

function animImportNotes(rep) {
  const out = [];

  if (rep.conversion_note) out.push(animEsc(rep.conversion_note));
  if (rep.snap_note) out.push(animEsc(rep.snap_note));

  if (rep.flattened_channel_count) {
    // `flattened_max_units` is the largest wobble in the unit `to_bvh` PRINTS:
    // metres for channels 0-2, degrees above them. The report names the
    // channels, so the unit is only safe to state when none of them is a root
    // translation -- which is the usual case and not one to assume.
    const ch = rep.flattened_channels || [];
    const deg = ch.length && ch.every((c) => c >= 3);
    const worst = rep.flattened_max_units;
    const how = (worst == null || !isFinite(worst)) ? ''
      : deg ? ` — the largest was ${(+worst).toFixed(3)}°`
            : ` — the largest was ${(+worst).toFixed(4)}`;
    out.push(`${rep.flattened_channel_count} channel(s) that this clip holds `
      + `still came back with a wobble too small to be an edit${how}, and `
      + 'were kept still.');
  }

  if (rep.ignored_position_count) {
    const named = (rep.ignored_position_joints || []).map(animEsc);
    const more = rep.ignored_position_count - named.length;
    out.push(`${rep.ignored_position_count} non-root joint(s) carried position `
      + 'channels that never move, so they were ignored'
      + (named.length ? `: ${named.join(', ')}` : '')
      + (more > 0 ? ` and ${more} more` : '')
      + '. That is Blender\'s “Root Translation Only” left unticked; the clip '
      + 'stores translation for the root alone.');
  }
  return out;
}

function animImportNotesHtml(rep) {
  const bits = [];
  if (rep.codec)
    bits.push('<span class="muted">Codec:</span> '
      + animEsc(ANIM_CODECS[rep.codec] || rep.codec));
  const notes = animImportNotes(rep);
  if (!bits.length && !notes.length) return '';
  return '<div class="muted" style="margin-top:5px;line-height:1.5">'
    + bits.join('<br>')
    + (bits.length && notes.length ? '<br>' : '')
    + notes.map((l) => '• ' + l).join('<br>')
    + '</div>';
}

// Dry run first, then apply -- the same contract the geometry and texture
// importers use. The dry run is what makes the slot check meaningful: most
// .anim entries are deflated, and different bytes compress differently, so
// "the same length" is not the same as "still fits".
async function importAnimFile(file) {
  const name = animCurrent;
  const text = await file.text();
  // NO euler here. This used to send the EXPORT dropdown's order as an
  // assertion the incoming file had to match, which refused every clip that
  // had been through Blender: Blender writes the channel order of the
  // armature's own rotation mode on the way out, usually X Y Z, so the
  // import failed with "declares XROTATION YROTATION ZROTATION but
  // YROTATION ZROTATION XROTATION was asserted" no matter what you did.
  // The file states its own order and the importer now converts from it.
  const payload = {
    archive: animLoadedArchive, name, bvh: text,
    skeleton: $('#animSkel').value || undefined,
    label: `import ${name} from ${file.name}`,
  };
  animIoReport(`Checking ${file.name}…`);
  let dry;
  try {
    dry = await post('anim_import', { ...payload, dry: true });
  } catch (e) {
    return animIoReport(`<b>Refused.</b><br>${e.message}`, 'bad');
  }
  if (dry.ok === false)
    return animIoReport(`<b>Will not fit.</b><br>${dry.error}`, 'bad');
  if (dry.written === false)
    // The notes belong here most of all: "nothing to write" is usually the
    // RESULT of a flatten or a snap, and without them it reads as the tool
    // having ignored the file.
    return animIoReport('Every sample of that BVH is as it already is on the '
      + 'disc, so there is nothing to write.' + animImportNotesHtml(dry),
      'good');

  const pct = dry.total_samples
    ? (100 * dry.changed_samples / dry.total_samples).toFixed(1) : '0';
  animIoReport(
    `<b>${file.name}</b> → ${name}<br>`
    + `${dry.changed_samples.toLocaleString()} of `
    + `${dry.total_samples.toLocaleString()} samples change (${pct}%)<br>`
    + (dry.grows
      ? `${dry.stored.toLocaleString()} bytes, more than its ${dry.slot.toLocaleString()}-byte slot: `
        + `${animEsc(dry.archive)} is rebuilt to make room on the next Build (or Play)`
      : `${dry.stored.toLocaleString()} of ${dry.slot.toLocaleString()} bytes `
        + `(${dry.headroom.toLocaleString()} spare`
        + `${dry.compressed ? ', measured compressed' : ''})`)
    + animImportNotesHtml(dry)
    + ((dry.copies || []).length ? `<br>The same clip in ${dry.copies.map(animEsc).join(', ')} gets it too.` : '')
    + '<div class="muted" style="margin-top:5px">Nothing has been written '
    + 'yet.</div>');

  const go = el('button', 'primary', 'Write it');
  go.style.cssText = 'font-size:11px;margin-top:6px;width:100%';
  go.onclick = async () => {
    go.disabled = true;
    go.textContent = 'Writing…';
    try {
      const r = await post('anim_import', payload);
      if (!r.ok) return animIoReport(`<b>Refused.</b><br>${r.error}`, 'bad');
      if (!r.written) return animIoReport(r.note || 'Nothing to write.', 'good');
      const also = (r.copies || []).length
        ? `<br>Also written to the same clip in ${r.copies.map(animEsc).join(', ')} (a level plays the copy in its own archive).`
        : '';
      const left = (r.copies_left || []).length
        ? `<br>Left alone (changed there on its own): ${r.copies_left.map(animEsc).join(', ')}.`
        : '';
      animIoReport(r.grows
        ? `Wrote <b>${name}</b> — it grew past its slot, so ${animEsc(r.archive)} is rebuilt with it on the next `
          + 'Build (or Play). Ctrl+Z to undo.'
        : `Wrote <b>${name}</b> — ${r.used.toLocaleString()} of `
          + `${r.slot.toLocaleString()} bytes. Ctrl+Z to undo.` + also + left, 'good');
      if (r.grows) refreshDiscState();
      toast(`Imported ${file.name} into ${name}.`, 'good');
      refreshHistory();
      if (animCurrent === name) playAnim(name);   // watch it immediately
    } catch (e) {
      animIoReport(`<b>Refused.</b><br>${e.message}`, 'bad');
    }
  };
  $('#animIoReport').append(go);
}

let animClips = [];
let animTruncated = false;

async function loadAnimList(archive) {
  animLoadedArchive = archive;
  $('#animList').innerHTML = '<div class="pad muted">Loading…</div>';
  $('#animSkel').innerHTML = '';
  try {
    // Query the two extensions separately with the toc's own `q` filter. The
    // toc route caps its reply at 4,000 rows, and SWAT.DAT holds 47,115 files
    // with the animations starting well past that index -- so an unfiltered
    // fetch would silently hide most clips (chr_for_wlk sits at 5,354).
    // EXACT extension, and a limit big enough for the real count. `q` is a
    // substring match, so `q=.anim` also matched the 2,420 `.animsnd` files
    // and they filled the row budget first; combined with the route's old flat
    // 4,000 cap that cut SWAT.DAT's clips mid-alphabet, hiding everything
    // after `red_flt_...` -- most of the player character's own animations.
    const [aToc, sToc] = await Promise.all([
      api(`toc?archive=${encodeURIComponent(archive)}&ext=anim&limit=20000`),
      api(`toc?archive=${encodeURIComponent(archive)}&ext=skel&limit=20000`),
    ]);
    const names = (t) => (t.rows || t.files || t || []).map((f) => f.name || f);
    animClips = names(aToc).sort();
    animTruncated = !!aToc.truncated;
    // The picker is NOT filled here any more. It used to receive every .skel
    // in the archive -- 236 of them in SWAT.DAT -- and then each clip tagged
    // the ones it could not use " — wrong bone count". That is a list where
    // most entries are mistakes, and the tool put them there. It is filled per
    // clip now, from the server's own list of rigs that fit, in playAnim.
    renderAnimList();
  } catch (e) {
    $('#animList').innerHTML = '';
    $('#animMsg').textContent = 'Could not read ' + archive + ': ' + e.message;
  }
}

function renderAnimList() {
  const box = $('#animList');
  const f = ($('#animFilter').value || '').toLowerCase();
  box.innerHTML = '';
  const shown = animClips.filter((n) => !f || n.toLowerCase().includes(f));
  if (!shown.length) {
    box.append(el('div', 'pad muted',
      animClips.length ? 'No clip matches that filter.'
                       : 'No .anim files in this archive.'));
    return;
  }
  for (const n of shown) {
    const row = el('div', 'row' + (n === animCurrent ? ' sel' : ''));
    row.append(el('span', 'nm', n.replace(/\.anim$/i, '')));
    row.onclick = () => playAnim(n);
    box.append(row);
  }
  // Say what is on screen and what is not. The old code silently showed the
  // first 1,200 and printed a total that was itself already truncated.
  if (animTruncated)
    box.append(el('div', 'pad muted',
      `${shown.length} shown — the archive holds more than the tool asked for. `
      + 'Narrow the filter to be sure you are seeing everything.'));
}

// ---- the character model, skinned onto the clip's skeleton
//
// The pieces were all already here: rsc.py decodes the bundles, texture.py
// decodes the PS2 textures, and the Creatures tab already draws a textured
// character. What was missing was per-vertex bone influences over the wire --
// /api/character_rig adds those and nothing else new.
//
// The gate on which models are offered is the bone count, and it is not a
// preference. A model's per-vertex bone is an INDEX into its skeleton's file
// order, so a skeleton of a different length does not merely look wrong, it
// addresses bones that are not there.
let animRigArchive = null;      // where the offered models are read from
let animRigModels = [];

// Which bundle should a clip open with? The clip's own stem first, which is
// what the name suggests -- `srd_load_a` -> `srd`. The skeleton's own stem is
// the fallback, because the skeleton is what the clip is actually posed on --
// and that is what catches the srd case, where `srd_load_a` drives 53 bones
// and `srd` is a 59-bone model, so the name lies.
//
// Measured over all 4,187 clips in SWAT.DAT, running this exact rule against
// the model list the picker would show: the clip's own stem decides 3,245
// (77.5%), the skeleton's model 798 (19.1%), and only 2 clips fall all the
// way through to "first compatible". The remaining 142 are offered no model
// at all -- 78 whose skeleton size no model is rigged to, 44 with no fitting
// skeleton, 20 that do not decode to a bone count.
function inferModelStem(clip, skelName, models) {
  const has = (s) => s && models.some((m) => m.stem === s);
  const stem = clip.replace(/\.anim$/i, '').toLowerCase();
  if (has(stem)) return stem;
  const head = stem.split('_')[0];
  if (has(head)) return head;
  const sk = (skelName || '').replace(/\.skel$/i, '').toLowerCase();
  if (has(sk)) return sk;
  return models.length ? models[0].stem : '';
}

// The preferred skeleton / model (Animations panel), tried before anything else when it fits the clip.
let animSkelPicked = false;          // the skeleton dropdown was just changed by hand: that pick wins once
function animPref(k) {
  try { return (localStorage.getItem('rdr.anim.pref' + k) || '').trim(); } catch { return ''; }
}
function animPrefSkel(candidates) {
  const p = animPref('Skel').toLowerCase();
  if (!p) return null;
  const bare = (x) => x.toLowerCase().replace(/\.skel$/, '');
  return candidates.find((c) => c.toLowerCase() === p)
    || candidates.find((c) => bare(c) === bare(p)) || null;
}
function animPrefModel(models) {
  const p = animPref('Model').toLowerCase().replace(/_lods\.rsc$/, '');
  if (!p) return null;
  const m = models.find((x) => String(x.stem).toLowerCase() === p);
  return m ? m.stem : null;
}

let animModelLast = null;           // the model on screen: its rig payload and texture URLs
let animSkelTextLast = '';          // the skeleton the clips are playing on, as text

async function fillAnimModels(arc, boneCount, clip, skelName) {
  const sel = $('#animModel');
  const note = $('#animModelNote');
  const keep = sel.value;
  sel.innerHTML = '';
  sel.disabled = false;
  $('#animShow').disabled = false;
  animRigModels = [];
  let d = null;
  try {
    d = await api(`character_rigs?archive=${encodeURIComponent(arc)}`
      + `&bones=${boneCount}&all=1&skel=${encodeURIComponent(skelName || '')}`);
  } catch { d = null; }
  // Character bundles live in level archives AND in SWAT.DAT -- all 310
  // distinct `*_lods.rsc` names are in SWAT.DAT, measured -- so an archive
  // with none of the right size is not a dead end. The test is on the models
  // that FIT, not on how many the archive holds: SALOON.DAT carries six
  // characters and not one of them is rigged to 53 bones.
  if ((!d || !d.models || !d.models.length) && arc !== 'SWAT.DAT') {
    try {
      d = await api(`character_rigs?archive=SWAT.DAT&bones=${boneCount}&all=1&skel=${encodeURIComponent(skelName || '')}`);
    } catch { d = null; }
  }
  animRigArchive = d ? d.archive : null;
  animRigModels = (d && d.models) || [];

  const none = el('option', null, 'None — skeleton only');
  none.value = '';
  sel.append(none);
  let byNameHeader = false;
  for (const m of animRigModels) {
    if (m.by_name && !byNameHeader) {
      // every other character: retargeted by bone name onto this clip
      const h = el('option', null, '── matched by bone name ──');
      h.disabled = true;
      sel.append(h);
      byNameHeader = true;
    }
    const o = el('option', null, m.by_name
      ? `${m.stem} — ${m.fit}`
      : `${m.stem} — ${m.bone_count} bones, ${m.textures} texture` + (m.textures === 1 ? '' : 's'));
    o.value = m.stem;
    sel.append(o);
  }
  if (!animRigModels.length) {
    note.textContent = 'No model matches this rig. Skeleton only.';
    return;
  }
  sel.value = animPrefModel(animRigModels) || (animRigModels.some((m) => m.stem === keep)
    ? keep : inferModelStem(clip, skelName, animRigModels));
  await applyAnimModel();
}

async function applyAnimModel() {
  const sel = $('#animModel');
  const note = $('#animModelNote');
  if (!animPlayer) return;
  const stem = sel.value;
  if (!stem) {
    animPlayer.clearModel();
    note.textContent = 'Bones only. Pick a character to see the model.';
    return;
  }
  const arc = animRigArchive || 'SWAT.DAT';
  const file = stem + '_lods.rsc';
  note.textContent = 'Decoding ' + file + '…';
  const row = animRigModels.find((m) => m.stem === stem) || {};
  let rig;
  try {
    if (row.by_name) {
      // on its OWN skeleton, driven bone by bone by name (anim3d.js retarget)
      rig = await api(`character_rig?archive=${encodeURIComponent(arc)}`
        + `&name=${encodeURIComponent(file)}&lod=0&skel=${encodeURIComponent(row.skel || (stem + '.skel'))}&skel_archive=`);
    } else
    // Bind the mesh against the skeleton the CLIP is playing on, not the
    // model's own file. They are usually the same file; when they are not,
    // this is what keeps mesh and bones agreeing at every joint instead of
    // tearing by the difference between two characters' limb lengths. It is
    // also what lets the 81 bundles that have no .skel of their own be shown
    // at all.
    rig = await api(`character_rig?archive=${encodeURIComponent(arc)}`
      + `&name=${encodeURIComponent(file)}&lod=0`
      + `&skel=${encodeURIComponent($('#animSkel').value || '')}`
      + `&skel_archive=${encodeURIComponent(animLoadedArchive || '')}`);
  } catch (e) {
    animPlayer.clearModel();
    note.textContent = `${file} would not decode: ${e.message}`;
    return;
  }
  const texUrl = (slot) =>
    `/api/character.png?archive=${encodeURIComponent(arc)}`
    + `&name=${encodeURIComponent(file)}&i=${slot}`;
  let mi;
  try {
    mi = animPlayer.setModel(rig, texUrl, { retarget: !!row.by_name });
  } catch (e) {
    animPlayer.clearModel();
    animModelLast = null;
    note.textContent = e.message;
    return;
  }
  // kept for "Export all animations" with the current model (animexport.js, via window.RDR.animModelState)
  animModelLast = { rig, texUrl, retarget: !!row.by_name, stem };
  animPlayer.setShow($('#animShow').value || 'model');

  // Say exactly what is skinned and what is not. The body IS skinned --
  // per-vertex, up to four (bone, weight) pairs blended on the GPU. The face
  // and the hat are not: they carry no per-vertex bone in the display list at
  // all, so they are rigidly attached to the head bone. Calling that skinning
  // would be a lie about how the character deforms.
  const skinned = mi.verts - mi.rigid;
  const rigid = (rig.parts || []).filter((p) => p.attach !== 'skin');
  note.innerHTML =
    `<b>${mi.stem}</b> — ${mi.verts.toLocaleString()} verts, `
    + `${mi.tris.toLocaleString()} tris, ${mi.textures} texture`
    + (mi.textures === 1 ? '' : 's') + '.<br>'
    + `${skinned.toLocaleString()} vertices are skinned per-vertex (up to 4 `
    + 'bone weights each).'
    + (rigid.length
        ? `<br>${mi.rigid.toLocaleString()} more — `
          + rigid.map((p) => p.name).join(', ')
          + ' attach rigidly to the head bone.'
        : '')
    + (mi.retarget
        ? `<br><span style="color:var(--warn)">On its own skeleton: ${mi.retarget.matched} of ${mi.retarget.total} `
          + 'bones follow the clip by bone name; the rest stay at rest.</span>'
        : rig.own_skel === false
        ? `<br><span style="color:var(--warn)">Posed on ${rig.skel}, `
          + 'the skeleton this clip drives.</span>'
        : '')
    // A last-resort check on the wiring itself: the bind positions the server
    // posed the mesh into must be the ones the player is animating. They come
    // from the same .skel by construction, so anything but zero here means a
    // parameter got dropped somewhere.
    + (mi.drift > 1e-4
        ? `<br><span style="color:var(--warn)">Body and bones do not `
          + 'line up. Reload the clip.</span>'
        : '');
}

async function playAnim(clip) {
  animCurrent = clip;
  renderAnimList();
  const arc = animLoadedArchive;
  const msg = $('#animMsg');
  msg.textContent = 'Decoding…';
  if (animPlayer) { animPlayer.dispose(); animPlayer = null; }
  $('#animStage').innerHTML = '';
  for (const id of ['#animPlay', '#animScrub', '#animEuler'])
    $(id).disabled = true;
  // The model panel describes a character standing in the view. With the view
  // torn down it must not keep describing the last one.
  $('#animModelNote').textContent = '';

  try {
    // Ask the server which skeletons FIT this clip (it parses them), so the
    // picker can default sensibly. This route already existed; it is read-only.
    let candidates = [];
    let info = null;
    try {
      info = await api(`anim?archive=${encodeURIComponent(arc)}`
        + `&name=${encodeURIComponent(clip)}`);
      // ok:false does not mean "no skeletons" -- the server names what fits a
      // clip even when it will not decode it. All three codecs decode on both
      // sides now (cm2, cmp and the uncompressed one), so this is the rare
      // path rather than the normal one it used to be.
      candidates = info.skeletons || [];
    } catch { /* no server answer: fall through and try client-side */ }

    // Point the skeleton dropdown at a candidate if the user has not chosen
    // one that already fits -- and when that overrides a choice, SAY WHY,
    // where the eye already is.
    //
    // Nothing in a .anim names its skeleton. The channel count fixes the bone
    // count (channels == 3 + 3*bones), and that is the only link -- so a
    // skeleton of the wrong length is not a worse choice, it is an impossible
    // one, and refusing it is correct. What was wrong was doing it quietly:
    // srd_load_a drives 53 bones while srd.skel has 59, so picking srd.skel
    // made the dropdown snap back to bdg.skel with the reason in grey 10px
    // under it. That reads as "this picker ignores me".
    //
    // Measured over SWAT.DAT: 3,730 of the 3,771 cm2 clips (98.9%) have more
    // than one legal skeleton, 27 have exactly one and 14 have none, so
    // "which of the ones that fit" is a real question and the picker says
    // how many there are.
    // FILL THE PICKER WITH WHAT FITS, AND NOTHING ELSE.
    //
    // Every name the server returns can drive this clip -- exact rigs, longer
    // ones whose surplus bones stay at rest, shorter ones whose surplus
    // channels the engine itself drops, and accessory rigs sliced out of a
    // character's own skeleton. So there is no wrong choice to warn about and
    // no verdict to print beside each option.
    //
    // What used to be here: the dropdown was pre-filled at archive load with
    // all 236 skeletons in SWAT.DAT, then every clip re-labelled them
    // " — fits" or " — wrong bone count", and if you picked a wrong one the
    // value snapped back with an explanation underneath. Three separate
    // apologies for a list the tool had no business showing.
    const skelSel = $('#animSkel');
    const keep = skelSel.value;
    skelSel.innerHTML = '';
    skelSel.disabled = false;
    for (const s of candidates) {
      const o = el('option', null, s);
      o.value = s;
      skelSel.append(o);
    }
    // Keep the user's choice across clips when it still fits, otherwise take
    // the server's first -- which is ranked to be the clip's OWN character.
    // ...unless a preferred skeleton fits: it comes first (a hand pick in the dropdown still wins that once).
    const prefSkel = animPrefSkel(candidates);
    skelSel.value = (animSkelPicked && candidates.includes(keep)) ? keep
      : prefSkel || (candidates.includes(keep) ? keep : (candidates[0] || ''));
    animSkelPicked = false;
    skelSel.disabled = candidates.length < 2;

    // The Export button knows before it is pressed. The only rig BVH cannot
    // write is a multi-root one, and 2 of the disc's 236 are -- they are still
    // offered here because the viewer plays them fine.
    const exportable = (info && info.exportable) || candidates;
    const canWrite = exportable.includes(skelSel.value);
    const bvhBtn = $('#animExportBtn');
    if (bvhBtn) {
      bvhBtn.disabled = !canWrite;
      bvhBtn.title = canWrite ? ''
        : 'This rig cannot export to BVH. Pick another to export.';
    }


    const [animBuf, skelText] = await Promise.all([
      fetch(`/api/file.bin?archive=${encodeURIComponent(arc)}`
        + `&name=${encodeURIComponent(clip)}`).then((r) => r.arrayBuffer()),
      // Not file.bin: a rig the picker offers is not always a file. Accessory
      // rigs are a slice of a character's own .skel, offered as
      // "ct2 :: skel_hair", and rig_skel serialises those as well as passing
      // real files straight through. Fetching by filename left 28 clips
      // (measured) loading whatever sorted first and then failing.
      skelSel.value
        ? fetch(`/api/rig_skel?archive=${encodeURIComponent(arc)}`
          + `&skel=${encodeURIComponent(skelSel.value)}`
          + `&name=${encodeURIComponent(clip)}`
          + `&bones=${encodeURIComponent((info && info.bones) || 0)}`)
          .then((r) => (r.ok ? r.text() : ''))
        : Promise.resolve(''),
    ]);

    animSkelTextLast = skelText || '';
    if (!skelText) {
      msg.innerHTML = 'No matching skeleton. Open it in Files for the curves.';
      $('#animModel').innerHTML = '';
      $('#animModelNote').textContent = 'No skeleton, so no model.';
      return;
    }

    animPlayer = createAnimPlayer($('#animStage'), animBuf, skelText,
      { order: $('#animEuler').value || 'YZX',
        onFrame: (k) => {
          $('#animScrub').value = k;
          const i = animPlayer.info;
          $('#animFrameLbl').textContent = `key ${k + 1} / ${i.keys}`;
        } });

    const i = animPlayer.info;
    for (const id of ['#animPlay', '#animScrub', '#animEuler', '#animExportBtn', '#animExportGlbBtn'])
      $(id).disabled = false;
    $('#animScrub').max = i.keys - 1;
    $('#animScrub').value = 0;
    $('#animEuler').value = animPlayer.order;
    $('#animPlay').textContent = 'Pause';
    $('#animFrameLbl').textContent = `key 1 / ${i.keys}`;
    // Everything in the picker fits, so this says what you are looking at and
    // how many other bodies you could look at it on. No verdicts, no "try
    // another if" -- there is nothing in the list that would be a mistake.
    const skelNote = (info && info.skeleton_notes
                      && info.skeleton_notes[skelSel.value]) || '';
    $('#animSkelNote').textContent =
      `${skelSel.value}, ${i.bones} bones.`
      + (candidates.length > 1
         ? ` ${candidates.length - 1} other rig${candidates.length === 2
             ? '' : 's'} available.` : '')
      + (skelNote ? ` ${skelNote}` : '');
    msg.innerHTML =
      `Playing <b>${clip}</b>: ${i.frames} frames, ${i.channels} channels, `
      + `${i.keys} keys at ${i.keyRate}/s. F frames the view.`;
    msg.title = 'If a joint twists, try another Euler order.';

    // The character itself, on those same bones. Done last and awaited
    // separately so a model that will not decode leaves the clip playing on
    // the skeleton rather than taking the whole view down with it.
    await fillAnimModels(arc, i.bones, clip, skelSel.value);
  } catch (e) {
    msg.textContent = 'Could not play this clip: ' + e.message;
    // The note describes the pose on screen; with no pose it must not keep
    // describing the previous clip's.
    $('#animSkelNote').textContent = '';
  }
}

// Handle for debugging from the browser console.
window.__rdr = {
  THREE, scene, camera, renderer, controls, gizmo, groups, meshes, draw,
  worldGroup, setWorldMode, get worldInfo() { return worldInfo; },
  renderGroup, setRenderMode, get renderInfo() { return renderInfo; },
  get levelEvents() { return levelEvents; }, loadLevelEvents, openSheet,
  castGroup, loadCast, get castInfo() { return castInfo; },
  get level() { return level; },
  get selected() { return selected; },
  select, buildLevel, reloadLevel, frameLevel,
};

// ============================================================== GAME RULES
//
// rdrstudio/progression.py on screen. One scroll, four sections, in the order
// they matter rather than in file order: the two unused difficulty dials come
// FIRST because they are the find, and a find behind a sub-tab is a find
// nobody sees.
//
// Every claim carries the module's own confidence tag, and that is not
// decoration. The difficulty unlock LADDER was reconstructed from two message
// strings; nobody has read the code that grants the unlock. The user will edit
// bytes against whatever this screen asserts, so an inference dressed as a
// fact is the one failure mode here that actually costs him an afternoon.

let rules = null;
let rulesUnlockType = 'character';

const RULES_SECTIONS = [
  ['knobs',   'Unused difficulty dials'],
  ['bhunter', 'Missions & Bounty Hunter'],
  ['unlocks', 'Unlockables'],
  ['diff',    'Difficulty & cheats'],
];

// ── THE CONFIDENCE LADDER ──────────────────────────────────────────────
//
// Five rungs and no sixth. The tool used to say `unproven` on Home,
// `(EXPERIMENTAL)` on Creatures, `experimental` in the preset data, `verified`
// in one table and `assumed` in another -- five vocabularies for four
// distinctions, three of which carried no visual commitment at all. They all
// map onto this ladder now, so the same claim reads the same way wherever it
// appears.
//
// Where a caller has its own reason it wins; these are the fallbacks, written
// to the standard of the `dead` copy, which is the best sentence in the
// product and stays exactly as it is.
const CONF_WHY = {
  measured: 'Measured. Read straight off the disc.',
  established: 'Documented. From PS2 or format documentation.',
  inferred: 'Inferred. Not confirmed in game.',
  untested: 'Unverified in game.',
  dead: 'Inert. The game never reads it.',
};
const CONF_ALIAS = {
  experimental: 'untested', unproven: 'untested',
  verified: 'measured', assumed: 'inferred', reconstructed: 'inferred',
};

function confBadge(kind, why) {
  const rung = CONF_ALIAS[kind] || kind || '';
  const b = el('span', 'conf ' + rung, rung);
  // The marker has no visible word, so the tooltip leads with it.
  const t = why ? `${rung ? rung[0].toUpperCase() + rung.slice(1) : ''}: ${why}`
                : CONF_WHY[rung];
  if (t) b.title = t;
  return b;
}

// Local cell helpers. Deliberately not called `td` -- that name is already
// taken by a local inside loadBank, and a second one at module scope would be
// a trap for whoever edits either.
function rtd(text, cls) {
  const d = el('td', cls || '');
  d.textContent = text == null ? '' : String(text);
  return d;
}
function rtdn(...nodes) {
  const d = el('td', '');
  for (const n of nodes) d.append(n);
  return d;
}
function rtable(headers) {
  const wrap = el('div', 'tw');
  const t = el('table');
  const hr = el('tr');
  for (const h of headers) hr.append(el('th', '', h));
  const head = el('thead');
  head.append(hr);
  const body = el('tbody');
  t.append(head, body);
  wrap.append(t);
  return [wrap, body];
}
function rulesSection(id, title, lede) {
  const s = el('div', 'sect');
  s.id = 'rsec-' + id;
  s.append(el('h3', '', title));
  if (lede) s.append(el('p', 'lede', lede));
  return s;
}
// lvl_tomb appears five times in levelflow.csv and only the variation number
// tells the copies apart, so a mission is NEVER labelled by its level alone.
function missionLabel(row) {
  return `${row.level} (variation ${row.variation})`;
}

async function initRulesTab(force) {
  if (rules && !force) return;
  const body = $('#rulesBody');
  body.innerHTML = '';
  body.append(el('div', 'muted', 'Reading the game’s rule tables…'));
  let got;
  try {
    const [sum, lf, un, diff, ch, unlock] = await Promise.all([
      api('progression'),
      api('progression_levelflow'),
      api('progression_unlockables'),
      api('progression_difficulty'),
      api('progression_cheats'),
      api('progression_can_unlock_difficulty'),
    ]);
    got = { sum, lf, un, diff, ch, unlock };
  } catch (e) {
    body.innerHTML = '';
    body.append(el('div', 'muted',
                   'Could not read the rule tables: ' + e.message));
    return;
  }
  rules = got;
  renderRules();
}

function renderRules() {
  const body = $('#rulesBody');
  body.innerHTML = '';
  body.append(rulesKnobs(), rulesBHunter(), rulesUnlocks(), rulesDifficulty());

  $('#rulesInfo').textContent =
    `${rules.lf.count} missions · ${rules.un.count} unlockables · ` +
    `${rules.diff.count} difficulties · ${rules.ch.count} cheats`;

  const nav = $('#rulesNav');
  nav.innerHTML = '';
  for (const [id, label] of RULES_SECTIONS) {
    const row = el('div', 'row');
    row.append(el('span', 'nm', label));
    row.onclick = () => {
      const t = $('#rsec-' + id);
      if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    nav.append(row);
  }

  const facts = $('#rulesFacts');
  facts.innerHTML = '';
  facts.append(el('div', '', 'What this screen found'));
  for (const h of rules.sum.highlights || []) {
    const d = el('div');
    d.style.marginTop = '10px';
    const t = el('div');
    t.append(el('b', '', h.title), confBadge(h.confidence));
    d.append(t, el('div', 'muted', h.detail));
    facts.append(d);
  }
  // A file whose header drifted from what the module expects is the one thing
  // that must never be swallowed -- every column index below would be wrong.
  const notes = (rules.lf.notes || []).concat(rules.un.notes || []);
  for (const n of notes) {
    const d = el('div', 'warn');
    d.style.marginTop = '10px';
    d.textContent = typeof n === 'string' ? n : JSON.stringify(n);
    facts.append(d);
  }
}

// Every write in this tab is two steps. Both CSVs are stored compressed in a
// fixed 2048-byte slot, so "will it fit" is a real question with a real
// number -- the dry run answers it, and the number is shown rather than
// assumed. Nothing is committed until the second click.
async function ruleWrite(route, payload, cell, onDone) {
  cell.className = 'muted';
  cell.textContent = 'checking…';
  let r;
  try {
    r = await post(route, Object.assign({}, payload, { dry: true }));
  } catch (e) {
    // progression.WriteRefused messages are written for the user and already
    // say what to do instead. Printed verbatim, never reworded.
    cell.className = 'warn';
    cell.textContent = e.message;
    return;
  }
  cell.className = '';
  cell.innerHTML = '';
  cell.append(el('div', 'fitline',
    `${r.payload_bytes} of ${r.slot_bytes} bytes · ${r.headroom} spare`));
  if (r.untested) {
    const w = el('div');
    w.append(confBadge('untested', r.warning),
             el('span', 'muted', ' effect unknown until it boots'));
    cell.append(w);
  }
  const go = el('button', '', 'Write it in');
  go.style.cssText = 'font-size:11px;padding:1px 8px;margin-top:4px';
  go.onclick = async () => {
    go.disabled = true;
    try {
      const w = await post(route, Object.assign({}, payload, { dry: false }));
      cell.innerHTML = '';
      cell.append(el('span', 'good',
        `written · ${w.payload_bytes} of ${w.slot_bytes} bytes`));
      toast(r.untested
        ? 'Written. Unverified in game.'
        : 'Written.', r.untested ? 'warn' : '');
      // The Campaign sub-view reads the same tables through its own cache;
      // a write here must not leave it showing the value from before.
      campaign = null;
      rules = null;
      refreshHistory();
      if (onDone) onDone(w);
    } catch (e) {
      go.disabled = false;
      cell.className = 'warn';
      cell.textContent = e.message;
    }
  };
  cell.append(go);
}

function rulesKnobs() {
  const lf = rules.lf;
  const col = {};
  for (const c of lf.columns || []) col[c.key] = c;
  const h = col.bhunter_health || {}, d = col.bhunter_deadeye || {};

  const s = rulesSection('knobs',
    'Two difficulty dials the developers wired up and never turned',
    'Read by the game, 0 on every shipped mission. Never used.');

  const box = el('div', 'hilite');
  const line = el('div');
  line.append(
    document.createTextNode((h.header || 'BHunter Health') + ': '),
    el('b', '', `${h.constant == null ? '?' : h.constant} on all ${lf.count} missions`),
    document.createTextNode('   ·   ' + (d.header || 'BHunter Dead-Eye') + ': '),
    el('b', '', `${d.constant == null ? '?' : d.constant} on all ${lf.count} missions`),
    confBadge('measured', 'counted from the file just now, not remembered'));
  box.append(line, el('div', 'muted',
    'Untested territory. Units unknown. Details: Guides > Modding guide.'));
  s.append(box);

  const [wrap, tb] = rtable(
    ['#', 'Mission', 'Bounty Hunter', 'Health', 'Dead-Eye', '', 'Fit']);
  for (const row of lf.rows) {
    const tr = el('tr');
    const status = el('td', 'muted');
    status.style.whiteSpace = 'normal';
    status.style.minWidth = '210px';
    const hIn = el('input', 'cell');
    hIn.value = row.bhunter_health;
    const dIn = el('input', 'cell');
    dIn.value = row.bhunter_deadeye;
    const btn = el('button', '', 'Validate');
    btn.style.cssText = 'font-size:11px;padding:1px 8px';
    btn.onclick = () => ruleWrite('progression_bhunter',
      { row: row.index, health: hIn.value, deadeye: dIn.value }, status);
    tr.append(rtd(row.index, 'muted num'), rtd(missionLabel(row)),
              rtd(row.bhunter_enabled ? 'yes' : 'no',
                  row.bhunter_enabled ? 'good' : 'muted'),
              rtdn(hIn), rtdn(dIn), rtdn(btn), status);
    tb.append(tr);
  }
  s.append(wrap);

  // The same kind of find, one file over: knobs the parser knows and no
  // shipped row uses. Worth the same treatment, not a footnote.
  const un = rules.un.criteria || {};
  const unused = un.recognised_but_unused || [];
  if (unused.length) {
    const box2 = el('div', 'hilite');
    box2.style.marginTop = '16px';
    const t = el('div');
    t.append(el('b', '', `${unused.length} unlock conditions the game knows and never uses`),
             confBadge('measured',
               'the table at 0x' + Number(un.table_address || 0).toString(16)
               + ' holds ' + (un.recognised_by_game || []).length
               + ' names; the file uses ' + (un.used_in_file || []).length));
    box2.append(t, el('div', 'muted', unused.join(', ')),
      el('div', 'muted', 'Parse but unverified in game.'));
    s.append(box2);
  }
  return s;
}

function rulesBHunter() {
  const lf = rules.lf;
  const bh = rules.diff.bounty_hunter || {};
  const s = rulesSection('bhunter', 'Missions & Bounty Hunter',
    `${lf.count} missions in play order. ${lf.bhunter_count} of them are `
    + 'whitelisted for Bounty Hunter.');

  const box = el('div', 'hilite');
  const t = el('div');
  t.append(el('b', '', 'Bounty Hunter is a mode flag, not a fifth difficulty'),
           confBadge(bh.confidence || 'established', bh.note));
  box.append(t, el('div', 'muted', bh.note || ''));
  s.append(box);

  const [wrap, tb] = rtable(
    ['#', 'Mission', 'In Bounty Hunter', 'Weapons it gives you', 'Boss',
     'Reward', 'Fit']);
  for (const row of lf.rows) {
    const tr = el('tr');
    const status = el('td', 'muted');
    status.style.whiteSpace = 'normal';
    status.style.minWidth = '210px';

    const sel = el('select', 'cell');
    for (const pair of [['1', 'yes'], ['0', 'no']]) {
      const o = el('option', null, pair[1]);
      o.value = pair[0];
      sel.append(o);
    }
    sel.value = row.bhunter === '1' ? '1' : '0';
    sel.onchange = () => ruleWrite('progression_mission',
      { row: row.index, key: 'bhunter', value: sel.value }, status);

    const wIn = el('input', 'cell');
    wIn.style.width = '220px';
    wIn.value = row.bhunter_weapons || '';
    wIn.title = 'semicolon-separated list';
    wIn.onchange = () => ruleWrite('progression_mission',
      { row: row.index, key: 'bhunter_weapons', value: wIn.value }, status);

    tr.append(rtd(row.index, 'muted num'), rtd(missionLabel(row)),
              rtdn(sel), rtdn(wIn), rtd(row.boss || '—', 'muted'),
              rtd(row.reward ? '$' + row.reward : '', 'muted'), status);
    tb.append(tr);
  }
  s.append(wrap);
  s.append(el('div', 'lede',
    'These two columns ARE used by the shipped game, unlike the two above — '
    + 'a change here takes effect as soon as it is written.'));
  return s;
}

function rulesUnlocks() {
  const un = rules.un;
  const crit = un.criteria || {};
  const s = rulesSection('unlocks', 'Unlockables — what the game hands out',
    `${un.count} entries. "From the start" is the master switch; `
    + `${(un.unlocked_by_default || []).length} of them ship already unlocked.`);

  const chips = el('div');
  chips.style.marginBottom = '10px';
  for (const type of Object.keys(un.groups || {})) {
    const c = el('span', 'chip' + (type === rulesUnlockType ? ' on' : ''),
                 `${type} ${un.groups[type].length}`);
    c.style.cursor = 'pointer';
    c.onclick = () => { rulesUnlockType = type; renderRules(); };
    chips.append(c);
  }
  s.append(chips);

  // The two '???' columns are DEAD and are not offered as inputs. Saying so
  // out loud beats leaving two nameless boxes on screen for someone to try.
  for (const dc of un.dead_columns || []) {
    const d = el('div', 'hilite');
    d.style.opacity = '.75';
    const t = el('div');
    t.append(el('b', '', `Column ${dc.column} "${dc.header}" — dead`),
             confBadge('dead', dc.note));
    d.append(t, el('div', 'muted', dc.note));
    s.append(d);
  }

  const bulk = el('div');
  bulk.style.margin = '10px 0';
  const bulkStatus = el('span', 'muted');
  bulkStatus.style.marginLeft = '10px';
  const bulkBtn = el('button', '', `Unlock all ${rulesUnlockType}`);
  bulkBtn.onclick = async () => {
    // Reuses the tool's existing unlock_all, which rewrites the whole column
    // in one edit -- 147 separate slot-checked writes would be absurd.
    bulkBtn.disabled = true;
    bulkStatus.className = 'muted';
    bulkStatus.textContent = 'writing…';
    try {
      const r = await post('unlock_all', { type: rulesUnlockType });
      bulkStatus.className = 'good';
      bulkStatus.textContent = r.changed
        ? `${r.changed} unlocked · ${r.used} of ${r.slot} bytes`
        : 'already all unlocked';
      refreshHistory();
      initRulesTab(true);
    } catch (e) {
      bulkStatus.className = 'warn';
      bulkStatus.textContent = e.message;
    }
    bulkBtn.disabled = false;
  };
  bulk.append(bulkBtn, bulkStatus);
  s.append(bulk);

  const [wrap, tb] = rtable(
    ['#', 'Name', 'Picture', 'From the start', 'Earned by', 'Value',
     'Xbox', 'Fit']);
  const known = crit.recognised_by_game || [];
  const none = crit.no_criterion_token || 'NONE';
  const unused = {};
  for (const n of crit.recognised_but_unused || []) unused[n] = true;
  for (const row of (un.groups || {})[rulesUnlockType] || []) {
    const tr = el('tr');
    const status = el('td', 'muted');
    status.style.whiteSpace = 'normal';
    status.style.minWidth = '210px';

    const def = el('select', 'cell');
    for (const pair of [['1', 'yes'], ['0', 'no']]) {
      const o = el('option', null, pair[1]);
      o.value = pair[0];
      def.append(o);
    }
    def.value = row.unlocked_by_default ? '1' : '0';
    def.onchange = () => ruleWrite('progression_unlockable',
      { row: row.index, key: 'default', value: def.value }, status);

    const cs = el('select', 'cell');
    for (const name of [none].concat(known)) {
      const o = el('option', null,
        unused[name] ? name + '  (game knows it, never uses it)' : name);
      o.value = name;
      cs.append(o);
    }
    cs.value = row.criteria || none;
    cs.onchange = () => ruleWrite('progression_unlockable',
      { row: row.index, key: 'criteria', value: cs.value }, status);

    const val = el('input', 'cell');
    val.value = row.value || '';
    val.onchange = () => ruleWrite('progression_unlockable',
      { row: row.index, key: 'value', value: val.value }, status);

    // Measured off the live route: 190 of the 221 rows physically stop at
    // column 5 and only 31 carry the Xbox cell. The writer refuses an edit
    // past the end of a row rather than adding commas, so the input is
    // disabled and says why, instead of failing after the click.
    const xb = el('input', 'cell');
    xb.value = row.xbox || '';
    if (row.columns < 9) {
      xb.disabled = true;
      xb.title = `this row stops at column ${row.columns - 1}; there is no `
               + 'Xbox cell on it, and adding commas would reshape the row';
    } else {
      xb.onchange = () => ruleWrite('progression_unlockable',
        { row: row.index, key: 'xbox', value: xb.value }, status);
    }

    tr.append(rtd(row.index, 'muted num'), rtd(row.name),
              rtd(row.texture, 'muted'), rtdn(def), rtdn(cs), rtdn(val),
              rtdn(xb), status);
    tb.append(tr);
  }
  s.append(wrap);
  return s;
}

function rulesDifficulty() {
  const d = rules.diff, ch = rules.ch;
  const s = rulesSection('diff', 'Difficulty & cheats',
    `${d.count} difficulties. The menu builds exactly that many buttons — a `
    + 'fifth needs a code patch, not a data edit.');

  const [wrap, tb] = rtable(['#', 'Name in the game', 'Where the name came from']);
  for (const lv of d.levels || []) {
    const tr = el('tr');
    tr.append(rtd(lv.index, 'muted num'), rtd(lv.name),
              rtdn(el('span', 'muted', lv.name_source || ''),
                   confBadge(lv.name_confidence)));
    tb.append(tr);
  }
  s.append(wrap);

  // THE LADDER MUST NOT LOOK LIKE THE TABLE ABOVE IT. The four names were
  // read out of swat.strtbl a second ago; this was reconstructed from two
  // message strings and never read from the code that grants the unlock.
  const lad = el('div');
  lad.style.marginTop = '14px';
  const lh = el('div');
  lh.append(el('b', '', 'How the harder modes are earned'),
            confBadge(d.ladder_confidence || 'inferred',
                      'reconstructed, not read from the granting code'));
  lad.append(lh);
  for (const step of d.ladder || []) {
    const b = el('div', 'infbox');
    b.append(el('div', '',
      `${step.requires}  →  unlocks ${(step.grants || []).join(' + ')}`));
    if (step.why) {
      const why = el('div', 'muted');
      why.style.marginTop = '4px';
      why.textContent = step.why;
      b.append(why);
    }
    b.append(el('div', 'muted', step.evidence_string_id || ''));
    const ev = el('span', 'ev');
    ev.textContent = step.evidence_text || '';
    b.append(ev);
    lad.append(b);
  }
  s.append(lad);

  const u = rules.unlock || {};
  const note = el('div', 'hilite');
  note.style.marginTop = '14px';
  const nt = el('div');
  nt.append(el('b', '', 'Can this tool just unlock Hard for you? No.'),
            confBadge(u.confidence || 'established'));
  note.append(nt, el('div', 'muted', u.reason || ''));
  if ((u.alternatives || []).length) {
    const alts = el('ul');
    alts.style.cssText = 'margin:6px 0 0; padding-left:18px; font-size:12px';
    for (const a of u.alternatives) alts.append(el('li', 'muted', a));
    note.append(alts);
  }
  s.append(note);

  const ct = el('div');
  ct.style.marginTop = '20px';
  ct.append(el('b', '', `Cheats (${ch.count})`));
  s.append(ct);
  s.append(el('p', 'lede',
    `${ch.live} live, ${ch.dead} dead in the retail build.`));

  const [cw, ctb] = rtable(['Cheat', 'Name in the game', 'From the start',
                            'State', 'Fit']);
  for (const c of ch.cheats || []) {
    const tr = el('tr');
    if (!c.live) tr.className = 'deadrow';
    const status = el('td', 'muted');
    status.style.whiteSpace = 'normal';
    status.style.minWidth = '210px';

    const def = el('select', 'cell');
    for (const pair of [['1', 'yes'], ['0', 'no']]) {
      const o = el('option', null, pair[1]);
      o.value = pair[0];
      def.append(o);
    }
    def.value = c.unlocked_by_default ? '1' : '0';
    def.onchange = () => ruleWrite('progression_unlockable',
      { row: c.csv_row, key: 'default', value: def.value }, status);

    const state = c.live
      ? rtdn(el('span', 'muted', 'live'),
             confBadge(c.confidence, c.confidence_note))
      : rtdn(el('span', 'muted', 'dead'),
             confBadge('dead', c.dead_reason || 'no consumer in the retail build'));
    // The executable carries a display name for only three of the ten; the
    // rest are named through widgets the tool has not read. An em-dash that
    // says so beats a blank cell that looks like a bug.
    const shown = rtd(c.label || '—', c.label ? '' : 'muted');
    if (!c.label)
      shown.title = 'no display name for this one was found in the executable';
    tr.append(rtd(c.short), shown, rtdn(def), state, status);
    ctb.append(tr);
  }
  s.append(cw);
  const deadNames = (ch.cheats || []).filter((c) => !c.live)
                                     .map((c) => c.short).join(' and ');
  if (deadNames) {
    s.append(el('div', 'lede',
      deadNames + ': inert. The game never acts on them.'));
  }
  return s;
}

$('#rulesReload').onclick = () => initRulesTab(true);

// ========================================================= STREAMED AUDIO
//
// The 8,361 `.stm` files -- 13.9 hours of voice, music and ambience, 52% of
// the disc. Headers only for the listing: decoding is pure Python at 1-2 M
// samples/s, so the whole corpus would take about a quarter of an hour.
// Audio is fetched one file at a time, on click, and never to draw a list.

let streamGroups = null, streamSummary = null, streamGroupKey = null;
let streamSearchTimer = null;

function setAudioView(which) {
  const banks = which === 'banks';
  $('#audViewBanks').classList.toggle('on', banks);
  $('#audViewStreams').classList.toggle('on', !banks);
  $('#audioBanks').style.display = banks ? 'flex' : 'none';
  $('#audioStreams').style.display = banks ? 'none' : 'flex';
  $('#audViewNote').textContent = banks
    ? 'short sound effects, SPU-ADPCM'
    : '8,361 streams — 13.9 hours of voice, music and ambience';
  if (banks) initAudioTab(); else initStreamsView();
}

async function initStreamsView() {
  if (streamGroups) return;
  const box = $('#strGroups');
  box.innerHTML = '<div class="pad muted">Reading 8,361 stream headers…</div>';
  let r;
  try {
    r = await api('streams_groups');
  } catch (e) {
    box.innerHTML = '';
    box.append(el('div', 'pad muted', 'Could not list the streams: ' + e.message));
    return;
  }
  streamGroups = r.groups || [];
  streamSummary = r.summary || {};

  const kind = $('#strKind');
  kind.innerHTML = '';
  const all = el('option', null, 'every kind');
  all.value = '';
  kind.append(all);
  const byKind = streamSummary.by_kind || {};
  for (const k of Object.keys(byKind)) {
    const o = el('option', null, `${k} (${byKind[k]})`);
    o.value = k;
    kind.append(o);
  }
  kind.onchange = renderStreamGroups;
  $('#strOrphan').onchange = renderStreamGroups;
  const lp = $('#strLoops');
  if (lp) lp.onchange = renderStreamGroups;
  $('#strSearch').oninput = () => {
    clearTimeout(streamSearchTimer);
    streamSearchTimer = setTimeout(runStreamSearch, 220);
  };
  renderStreamGroups();
  wireStreamAdd();

  const s = streamSummary;
  $('#strInfo').textContent =
    `${(s.total || 0).toLocaleString()} streams · `
    + `${(s.hours || 0).toFixed(1)} hours · `
    + `${s.unreferenced} that no sound bank asks for`
    + (s.looping ? ` · ${s.looping} that loop` : '');
  $('#strBody').innerHTML = '';
  $('#strBody').append(streamsWelcome());
}

// Add new streams: .stm as they are, .wav encoded to .stm (dataeditor_more.stream_add), into streams.dat's Data
// Editor folder -- the next Build adds them to STREAMS.DAT.
async function refreshStreamAdded() {
  try {
    const r = await api('de_x_stream_added');
    $('#strAdded').textContent = r.files.length ? `${r.files.length} added, waiting for the next Build` : '';
    $('#strAdded').title = r.files.join('\n');
  } catch (e) { /* the Data Editor is not set up */ }
}
function wireStreamAdd() {
  const b = $('#strAdd');
  if (!b || b.dataset.wired) return;
  b.dataset.wired = '1';
  refreshStreamAdded();
  const cr = $('#strCreate');
  if (cr) cr.onclick = async () => {
    const m = await import('./stmcreator.js');
    m.openStmCreator({ post, toast, el, onSaved: refreshStreamAdded, stream: streamGroupKey ? '' : '' });
  };
  b.onclick = () => {
    const inp = el('input');
    inp.type = 'file'; inp.multiple = true; inp.accept = '.stm,.wav,audio/wav';
    inp.onchange = async () => {
      const files = [];
      for (const f of inp.files) {
        const buf = new Uint8Array(await f.arrayBuffer());
        let bin = '';
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        files.push({ name: f.name, data: btoa(bin) });
      }
      if (!files.length) return;
      b.disabled = true; b.textContent = 'Adding…';
      try {
        const r = await post('de_x_stream_add', { files });
        if (r.ok === false) throw new Error(r.error_text || 'refused');
        const lines = r.added.map((a) => `${a.name}: ${a.seconds} s, ${a.rate} Hz, ${a.channels === 2 ? 'stereo' : 'mono'}`
          + (a.replaces ? ' (replaces the game\'s stream of that name)' : ' (new)'));
        alert('Added to streams.dat\'s Data Editor folder; the next Build puts them in STREAMS.DAT:\n\n' + lines.join('\n')
          + (r.added.some((a) => !a.replaces) ? '\n\nA new name plays only where something asks for it (a sound bank definition, a level). To hear one in place of a game stream, give it that stream\'s name.' : ''));
        refreshStreamAdded();
      } catch (e) { toast('Add failed: ' + e.message, 'bad'); }
      b.disabled = false; b.textContent = 'Add .stm / .wav…';
    };
    inp.click();
  };
}

// An empty state, not a landing page (DESIGN-REVIEW B6/A2).
function streamsWelcome() {
  const s = streamSummary || {};
  const w = el('div', 'empty');
  w.style.flexDirection = 'column';
  w.append(el('div', '', 'Pick a group'));
  if (s.unreferenced != null) {
    const n = el('div', '', `${s.unreferenced} streams are never referenced. `
      + 'Tick “unused only” to browse them.');
    n.style.cssText = 'font-size:11px;margin-top:6px';
    n.title = "Every one of SWAT.DAT's 47,115 entries was token-scanned, "
      + 'not just the sound banks. Play and export only; stream import is '
      + 'not available yet.';
    w.append(n);
  }
  return w;
}

// The groups organised as the banks are: by what they are -- Music, Cutscenes, Levels and ambience, Characters,
// Story dialogue -- from the kinds of stream in them and the sound bank that claims them (label_source).
const streamOpen = new Set();
function streamSection(g) {
  const src = g.label_source || '';
  const kinds = g.kinds || [];
  if (/^voice and unique sounds for/i.test(src)) return 'Characters';
  if (/^music for/i.test(src)) return 'Music';
  if (/level|lvl_|specific objects/i.test(src)) return 'Levels and ambience';
  if (kinds.includes('music') && !kinds.includes('bark') && !kinds.includes('dialogue')) return 'Music';
  if (kinds.includes('cutscene') || /^\d+_/.test(g.key)) return 'Cutscenes';
  if (kinds.length && kinds.every((k) => k === 'ambience')) return 'Levels and ambience';
  return 'Story dialogue';
}
const STREAM_SECTIONS = ['Music', 'Cutscenes', 'Levels and ambience', 'Characters', 'Story dialogue'];

function renderStreamGroups() {
  const box = $('#strGroups');
  const kind = $('#strKind').value;
  const orphan = $('#strOrphan').checked;
  box.innerHTML = '';
  const loopsOnly = $('#strLoops') && $('#strLoops').checked;
  const shown = (streamGroups || []).filter((g) => {
    if (kind && !(g.kinds || []).includes(kind)) return false;
    if (orphan && !g.unreferenced) return false;
    if (loopsOnly && !g.looping) return false;
    return true;
  });
  if (!shown.length)
    return box.append(el('div', 'pad muted', 'Nothing matches those filters.'));
  const filtering = !!(kind || orphan || loopsOnly);
  const label = (g) => {
    const l = g.label || g.key;
    // a lower-case label is the file prefix itself: give it a capital
    return l === g.key ? l.replace(/^./, (c) => c.toUpperCase()).replace(/_/g, ' ') : l;
  };
  for (const sec of STREAM_SECTIONS) {
    const groups = shown.filter((g) => streamSection(g) === sec).sort((x, y) => label(x).localeCompare(label(y)));
    if (!groups.length) continue;
    const open = filtering || streamOpen.has(sec) || groups.some((g) => g.key === streamGroupKey);
    const mins = groups.reduce((t, g) => t + g.seconds, 0) / 60;
    const head = el('div', 'row');
    head.style.cssText = 'font-weight:600;cursor:pointer;padding:6px 10px';
    head.textContent = `${open ? '▾' : '▸'} ${sec} (${groups.length} · ${mins >= 60 ? (mins / 60).toFixed(1) + ' h' : mins.toFixed(0) + ' min'})`;
    head.onclick = () => { if (open) { streamOpen.delete(sec); if (groups.some((g) => g.key === streamGroupKey)) streamGroupKey = null; } else streamOpen.add(sec); renderStreamGroups(); };
    box.append(head);
    if (!open) continue;
    for (const g of groups) {
      const row = el('div', 'row' + (g.key === streamGroupKey ? ' sel' : ''));
      row.style.cssText = 'display:block; padding:5px 10px 5px 20px';
      const top = el('div');
      top.append(el('b', '', label(g)));
      if (g.unreferenced) {
        const b = el('span', 'chip', `${g.unreferenced} unused`);
        b.style.marginLeft = '6px';
        top.append(b);
      }
      row.append(top);
      row.append(el('div', 'muted',
        `${g.key} · ${g.count} files · ${(g.seconds / 60).toFixed(1)} min`
        + (g.looping ? ` · ${g.looping} loop` : '')));
      // The label is chosen by how much of the group one sound bank claims, so
      // it is an inference. The evidence goes in the tooltip rather than being
      // dropped -- a wrong label with no provenance is worse than no label.
      if (g.label_source) row.title = g.label_source;
      row.onclick = () => showStreamGroup(g);
      box.append(row);
    }
  }
}

function streamRow(st) {
  const d = el('div', 'stm');
  const play = el('button', '', '▶');
  play.style.cssText = 'font-size:11px;padding:0 7px';
  play.onclick = () => playStream(st.name);
  const say = el('div', 'say');
  const words = st.caption || st.phrase;
  if (words) {
    // A caption the audio team wrote IS the shipped transcript. A caption
    // squeezed out of the filename is not, and must not be able to pass for
    // one -- so it renders in italics and says where it came from.
    if (st.caption_source === 'filename') {
      say.append(el('i', '', '“' + words + '”'),
                 el('span', 'chip', 'from the filename'));
    } else {
      say.append(document.createTextNode('“' + words + '”'));
    }
    say.append(el('div', 'fn', st.name));
  } else {
    say.append(el('span', 'fn', st.name));
  }
  if (!st.referenced) {
    const b = el('span', 'chip', 'no sound bank uses this');
    b.title = 'No sound bank references this stream.';
    say.append(b);
  }
  // Looping is the one property of a stream you cannot hear from the first
  // few seconds and cannot see in the filename, and it is exactly what
  // somebody replacing a music bed needs to know before they replace it.
  // 179 of the 8,361 streams carry it.
  if (st.looping) {
    const lb = el('span', 'chip', 'loops');
    lb.style.borderColor = 'var(--gold)';
    lb.title = 'This stream loops. The header carries a restart point and '
      + 'the codec state to resume from -- a loop here does not rewind to '
      + 'silence, it restores the predictor and step index the stream was '
      + 'holding at that point. Replacing it without that descriptor gives '
      + 'you a stream that restarts at zero with a click.';
    say.append(lb);
  }
  // Every bank sound has a save link; the streams had only ▶, though the
  // server already answers the same GET with a finished WAV. Same capability,
  // same control.
  const save = el('a', 'nm', 'save');
  save.href = '/api/streams_wav?name=' + encodeURIComponent(st.name);
  save.download = st.name.replace(/\.stm$/i, '') + '.wav';
  save.style.cssText = 'font-size:11px;cursor:pointer;flex:none';
  save.title = 'Decode this stream and save it as a WAV file.';
  d.append(play, say, save, el('span', 'dur', (st.seconds || 0).toFixed(2) + 's'));
  return d;
}

function showStreamGroup(g) {
  streamGroupKey = g.key;
  renderStreamGroups();
  const body = $('#strBody');
  body.innerHTML = '';
  body.append(el('h3', '', g.label || g.key));
  body.append(el('p', 'lede',
    `${g.key} · ${g.count} files · ${(g.seconds / 60).toFixed(1)} minutes · `
    + (g.kinds || []).join(', ')
    + (g.unreferenced ? ` · ${g.unreferenced} that nothing asks for` : '')));
  if (g.label_source) {
    const src = el('div', 'infbox');
    src.append(confBadge('inferred',
                 'named by whichever sound bank claims most of the group'));
    const ev = el('span', 'ev');
    ev.textContent = g.label_source;
    src.append(ev);
    body.append(src);
  }
  for (const sec of g.sections || []) {
    const head = el('div', 'cue');
    head.textContent = `${sec.name} — ${sec.count} · ${sec.kind}`;
    body.append(head);
    for (const st of sec.streams || []) body.append(streamRow(st));
  }
}

async function runStreamSearch() {
  const q = $('#strSearch').value.trim();
  const body = $('#strBody');
  if (!q) {
    body.innerHTML = '';
    body.append(streamsWelcome());
    return;
  }
  body.innerHTML = '';
  body.append(el('div', 'muted', 'Searching…'));
  let r;
  try {
    r = await api('streams_search?q=' + encodeURIComponent(q));
  } catch (e) {
    body.innerHTML = '';
    return body.append(el('div', 'muted', 'Search failed: ' + e.message));
  }
  body.innerHTML = '';
  body.append(el('h3', '', `${r.count} hits for “${q}”`));
  body.append(el('p', 'lede',
    "Matches the game's own captions as well as filenames"
    + (r.truncated ? ' — showing the first 400.' : '.')));
  for (const st of r.hits) body.append(streamRow(st));
  if (!r.hits.length) body.append(el('div', 'muted', 'Nothing said that.'));
}

// One file per click. A 90-second stereo music cue is about 3.5 seconds of
// pure-Python ADPCM, so say so rather than looking hung.
function playStream(name) {
  const p = $('#strPlayer');
  const info = $('#strInfo');
  info.textContent = 'decoding ' + name + '…';
  p.src = '/api/streams_wav?name=' + encodeURIComponent(name);
  p.oncanplay = () => { info.textContent = name; };
  p.onerror = () => { info.textContent = 'could not decode ' + name; };
  p.play().catch(() => {});
}

$('#audViewBanks').onclick = () => setAudioView('banks');
$('#audViewStreams').onclick = () => setAudioView('streams');

// ============================================================== FILTERS
//
// Every filter box in the product was missing the same three things, and the
// gap was not cosmetic: an active filter looked identical to an empty one,
// there was no way to clear it except selecting the text, and the browser
// restored the last one you typed when the page reloaded. Twice that made the
// whole app read as broken -- a leftover "tower" turned a 1,169-object level
// into "0 in 0 groups · Nothing matches that filter", with nothing on screen
// admitting a filter was involved.
//
// So: a filter that is doing something LOOKS like it, says how much it is
// hiding, can be cleared in one click, and never survives a reload. Fold state
// and every other stored preference are untouched -- it is only the filter
// VALUES that are deliberately forgotten.
const FILTERS = [
  ['#levelFilter',     '#levelList',        '.row'],
  ['#objFilter',       '#objList',          '.row:not(.srchead)'],
  ['#assetDockFilter', '#assetDock',        '.row'],
  ['#gunFilter',       '#gunList',          '.row'],
  ['#crFilter',        '#crList',           '.row'],
  ['#animFilter',      '#animList',         '.row'],
  ['#sndFilter',       '#sndBanks',         '.row'],
  ['#arcFilter',       '#arcList',          '.row'],
  ['#tocFilter',       '#tocTable tbody',   'tr'],
  ['#unlockFilter',    '#unlockTable tbody', 'tr'],
  ['#itemFilter',      '#itemTable tbody',  'tr'],
  // Searches rather than filters -- the server does the matching and the
  // result count is already printed beside them -- so these get the active
  // style and the clear button, but no count of their own.
  ['#assetSearch', null, null],
  ['#searchBox',   null, null],
  ['#stQ',         null, null],
  ['#strSearch',   null, null],
];

const filterTotals = new Map();

function refreshFilterChip(inp, chip, contSel, rowSel) {
  const term = inp.value.trim();
  inp.classList.toggle('filtering', term !== '');
  if (!contSel) { chip.classList.toggle('on', term !== ''); return; }
  const cont = document.querySelector(contSel);
  const n = cont ? cont.querySelectorAll(rowSel).length : 0;
  // The total is whatever the list holds when nothing is filtering it, which
  // is also the state every list starts in now.
  if (!term) filterTotals.set(inp.id, n);
  const total = filterTotals.get(inp.id);
  chip.firstChild.textContent =
    total != null && total >= n ? `showing ${n} of ${total} · `
                                : `showing ${n} · `;
  chip.classList.toggle('on', term !== '');
}

for (const [sel, contSel, rowSel] of FILTERS) {
  const inp = document.querySelector(sel);
  if (!inp) continue;
  // Belt and braces against the browser restoring the last value on reload:
  // tell it not to, and clear whatever it restored before anything renders.
  inp.setAttribute('autocomplete', 'off');
  inp.value = '';
  const chip = el('span', 'fchip');
  chip.append(document.createTextNode(''));
  const clr = el('button', '', 'clear');
  clr.type = 'button';
  clr.title = 'Empty this filter and show everything again';
  clr.onclick = () => {
    inp.value = '';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    refreshFilterChip(inp, chip, contSel, rowSel);
    inp.focus();
  };
  chip.append(clr);
  inp.insertAdjacentElement('afterend', chip);
  inp.addEventListener('input',
    () => refreshFilterChip(inp, chip, contSel, rowSel));
  // The list behind a filter is rebuilt by a dozen different code paths --
  // loading a level, switching archive, an undo. Watching the container keeps
  // the count honest without every one of them having to know about this.
  if (contSel) {
    let timer = null;
    const watch = () => {
      const cont = document.querySelector(contSel);
      if (!cont) return;
      new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(
          () => refreshFilterChip(inp, chip, contSel, rowSel), 200);
      }).observe(cont, { childList: true, subtree: true });
    };
    watch();
  }
  refreshFilterChip(inp, chip, contSel, rowSel);
}

// ------------------------------------------------------------------- start

(async function init() {
  resize();
  try {
    const c = await api('config');
    const p = $('#pcsx2pill');
    p.textContent = c.pcsx2_exists ? 'PCSX2 ready' : 'PCSX2 missing';
    p.className = 'sb ' + (c.pcsx2_exists ? 'ok' : 'bad');
    if (!c.pcsx2_exists && $('#btnAppSetup')) {
      // FINAL PASS: a red "missing" with nowhere to go. Settings is the fix.
      p.textContent = 'PCSX2 not set — click to choose';
      p.title = 'Play needs PCSX2. Click to point the Studio at it.';
      p.style.cursor = 'pointer';
      p.onclick = () => $('#btnAppSetup').click();
    }
    // The ISO pill now reports who OWNS the disc, refreshed on a timer --
    // see refreshDiscState. Nothing static here.
    await refreshDiscState();
  } catch (e) { toast('Backend not reachable: ' + e.message, 'bad'); }
  try { editableFields = await api('editable_fields'); } catch {}
  // Before any level loads: the collision and render paths read `worldMode`,
  // `renderMode`, `bakedLight` and `showSecondPass` as they build geometry,
  // so the saved answers have to be in place by then or the first level comes
  // up with the defaults and only the NEXT one is right.
  await loadPrefs();
  // These three are the boot sequence, and they used to be unguarded. When the
  // Studio died mid-request one of them threw here -- BEFORE window.RDR was
  // published and before the module loader ran -- so every ES module failed to
  // import and the product came up as chrome with nothing in it. The module
  // bootstrap below already refuses to let one broken module cost the rest;
  // this gives the same bargain to the backend, so a hiccup costs a toast and
  // an empty list rather than the whole front end.
  // Hold on the boot overlay while the server warms its caches, with a real
  // bar. The server does the same work either way -- this just makes it
  // visible instead of letting the first click pay for it silently. Skippable,
  // because the tool genuinely works during warm-up, only slower; and any
  // terminal state (done, error, idle -- idle means a CLI server that never
  // started a warm-up) drops through to the normal boot.
  const overlay = $('#bootOverlay');
  if (overlay) {
    let skipped = false;
    $('#bootSkip').onclick = () => { skipped = true; };
    const t0 = Date.now();
    while (!skipped && Date.now() - t0 < 180000) {
      let w;
      try { w = await api('warmup'); } catch { break; }
      if (!w || w.state !== 'running') break;
      const pct = w.total ? Math.max(2, Math.round(100 * w.done / w.total)) : 2;
      $('#bootBar').style.width = pct + '%';
      $('#bootStage').textContent = w.stage || 'working…';
      $('#bootDetail').textContent = w.seconds ? `${w.seconds}s` : '';
      await new Promise((r) => setTimeout(r, 400));
    }
    // DESIGN-REVIEW B1: a long job (the disc compare) holds the lock that
    // every load below waits on. The overlay used to sit at 2% saying
    // "reading the disc…", which was not what the server was doing. It says
    // what it IS doing now, and offers Cancel.
    $('#bootCancel').onclick = () => { post('job_cancel', {}).catch(() => {}); };
    while (!skipped) {
      let j;
      try { j = await api('job'); } catch { break; }
      if (!j || !j.running) break;
      $('#bootStage').textContent = j.label + (j.cancelling ? ' — cancelling…' : '');
      $('#bootDetail').textContent = `${Math.round(j.seconds)} s`;
      $('#bootCancel').style.display = j.cancelling ? 'none' : '';
      await new Promise((r) => setTimeout(r, 500));
    }
    overlay.style.display = 'none';
  }
  watchJob();

  // Levels first and ALONE, because it is the only one of these the editor is
  // useless without.
  //
  // `loadArchives` used to be awaited here, between levels and history, and it
  // is the most expensive call in the product: /api/archives walks all 66
  // archives, and the route drops each one from the cache as it finishes
  // (studio.py, `_cache.pop`), so it re-parses everything from a 2.2 GB ISO
  // AND leaves the cache empty for whatever runs next. Measured on a cold
  // cache: over 180 seconds, with `levels` behind it taking 20 more.
  //
  // Every disc-touching route serialises on one lock, and the front end polls
  // the journal and the disc pill every 3 seconds -- so a 180-second call does
  // not just delay startup, it lets ~60 polls queue behind it and the server
  // never catches up. The window comes up with an empty level list and stays
  // that way, which is exactly what it was reported as: "not loading any
  // levels". It was loading them, eventually, behind three minutes of work
  // nobody asked for at startup.
  try {
    await loadLevels();
  } catch (e) {
    toast('Could not load the level list: ' + e.message
          + '\nThe editor is up; use Reconnect once the Studio answers.', 'bad');
  }
  // Not awaited: the Archives tab loads its own data when you open it, and
  // history is a background nicety. Failures here are logged, not fatal.
  refreshHistory().catch(() => {});

  // Populate the capacity tab's level picker from the same list.
  try {
    const levels = await api('levels');
    const mans = levels.find((l) => l.name === 'MANS');
    for (const id of ['#capLevel', '#texLevel']) {
      const sel = $(id);
      for (const l of levels) {
        const o = el('option', null, `${l.group.replace('LVLS_', '')} / ${l.name}`);
        o.value = l.archive;
        sel.append(o);
      }
      if (mans) sel.value = mans.archive;
    }
    $('#texLevel').onchange = (e) => loadTextures(e.target.value, null);
    $('#btnTexReplace').onclick = () => {
      if (texPicked) importTexture(texPicked.archive, texPicked.t, texPicked.img);
    };
  } catch {}

  // Everything the render loop might reach into now exists.
  uiReady = true;

  // --------------------------------------------------------- module bootstrap
  //
  // Everything new lives in its own ES module. A syntax error in one of them
  // logs here and disables that one feature; it cannot blank the product,
  // which is what happened twice when new code went into this file.
  window.RDR = {
    api, post, toast, el, $, saveFile, showTab, withProgress,
    THREE, camera, scene, renderer, controls, meshes, selection,
    nodeOf, select, focusOn, reloadLevel, refreshHistory, renderInspector,
    showCreatureModel, renderCharacterParts,
    surfacePoint, setPlaceMode, openTextFile, typeInfo, loadCapacity,
    loadLevelByRef, levelQuery, pushChange, pushChanges, renderAttributes,
    addInspectorSection, watchButtons, loadToc,
    get level() { return level; },
    get selected() { return selected; },
    get placeMode() { return placeMode; },
    // The archive whose table of contents the Archives tab is showing, so a
    // module adding to that archive can default to what is on screen.
    get currentArchive() { return currentArc; },
    // After the Data Editor writes the disc: new levels (new .dat files) and new
    // characters show in the level list, Creatures and the character views.
    async refreshLists() {
      levelCatalog = [];
      crData = null;
      crChars = null;
      try { await loadLevels(); } catch (e) { console.error('[refreshLists]', e); }
      if (document.querySelector('#tab-creatures.on')) { try { await loadCreatures(); } catch (e) { console.error(e); } }
    },
    // Animations > Export all animations, with the current model: what is on screen.
    animModelState() {
      if (!animModelLast || !animPlayer || !animPlayer.modelInfo || !animSkelTextLast) return null;
      return { ...animModelLast, skelText: animSkelTextLast, skelName: $('#animSkel').value,
               order: $('#animEuler').value || 'YZX', archive: animLoadedArchive };
    },
    // Add Character (dataeditor.js) reloads the creature list and opens the new one.
    async refreshCreatures(pick) {
      crData = null;
      await loadCreatures();
      const row = pick && [...document.querySelectorAll('#crList .row')].find((n) => n.dataset.cr === pick);
      if (row) { row.click(); row.scrollIntoView({ block: 'center' }); }
    },
  };
  // ?nomods=1 boots the shipped product with none of the new modules, which
  // is the fastest way to answer "is this the new code or was it already
  // like that" without editing a file.
  const qs = new URLSearchParams(location.search);
  const mods = qs.has('nomods') ? []
    : qs.get('mods') ? qs.get('mods').split(',')
    : ['transfer', 'nav', 'controls', 'cast', 'attrs', 'logic',
       'versions', 'codemods', 'export', 'arcadd', 'animexport', 'dataeditor',
       // Each of these drives one gated panel and the release build deletes
       // the file, so the name has to go with it -- an import() of a missing
       // module is caught and logged, which is a silence that reads exactly
       // like a working build. build.py FEATURE_MODULES is the other half.
       /* @feature:behaviour_tables */ 'states', /* @end:behaviour_tables */
       /* @feature:menu_edit */ 'menus', /* @end:menu_edit */
       /* @feature:csv_editor */ 'csvedit', /* @end:csv_editor */
       /* @feature:char_files */ 'rig', /* @end:char_files */
      ];
  for (const m of mods) {
    try { (await import(`./${m}.js`)).init(window.RDR); }
    catch (e) { console.error(`[${m}] disabled:`, e); }
  }
  window.dispatchEvent(new CustomEvent('rdr:ready'));
})();
