// nav.js — THE WORKSPACE INFORMATION ARCHITECTURE.
//
// Nineteen equal doors became six workspaces and twenty-four destinations,
// all of them on screen at once. The rule that did not change: NOTHING IS
// HIDDEN. Merging is allowed; hiding is not, so the three modes that absorbed
// a second tab carry a visible sub-view strip rather than a dropdown.
//
// The nav MARKUP is static in index.html on purpose. app.js already wires
// every `nav button[data-tab]` to showTab at load, so if this module ever
// fails to parse, navigation still works -- it just loses the sub-strips, the
// workspace highlight and the header level picker.

import { call, notBuilt, onPanel } from './transfer.js';

let A = null;

// A composite mode is one destination that owns more than one panel. The
// panels are siblings, the strip is permanent, and every sub-view is one
// click from cold -- which is the whole difference between merging and hiding.
const COMPOSITES = {
  // The palette and the editor for one thing in it. Cast answers "what may
  // this enemy be"; Behaviour tables answers "and what does that behaviour
  // actually bind to". Same subject, two ends, so one destination.
  cast: {
    label: 'Cast',
    views: [['cast', 'Characters & behaviours', 'What an enemy may be'],
            ['states', 'Behaviour tables',
             'Which state machines a table binds, and the numbers inside them']],
  },
  // "the same subject from the other end", as the shipped source already said
  campaign: {
    label: 'Missions',
    views: [['campaign', 'Campaign', 'What each mission gives you'],
            ['unlocks', 'Unlocks & shop', 'The unlock switches and the shop prices'],
            ['rules', 'Game Rules', 'What the game unlocks, and how hard it gets']],
  },
  // One search over one tree. "Find setting" was never a place.
  weapons: {
    label: 'Weapons & stats',
    views: [['weapons', 'Weapons', 'Damage, clip, spread, range'],
            ['creatures', 'Creatures', 'Enemy and animal stats'],
            ['rig', 'Body & Rig',
             'Collision spheres, hit boxes, arsenal, physics chains, '
             + 'animation slots'],
            ['fields', 'Find setting', 'One value across the whole disc']],
  },
  // Hidden returned 292 of 127,365 files by substring match: 0.23% of the
  // disc. That is a saved search, and it is a chip now.
  text: {
    label: 'Files',
    views: [['text', 'Every file', 'Search the disc and edit any file'],
            ['hidden', 'Cut & dev content', 'What was left on the disc']],
  },
};

// Every destination in the product, by workspace, for Ctrl+K and for the
// active-workspace highlight.
const WHERE = {
  editor: 'LEVEL', env: 'LEVEL', textures: 'LEVEL',
  cast: 'LOGIC', states: 'LOGIC', campaign: 'LOGIC', rules: 'LOGIC', unlocks: 'LOGIC',
  behaviour: 'LOGIC', links: 'LOGIC', scripting: 'LOGIC',
  weapons: 'BALANCE', creatures: 'BALANCE', fields: 'BALANCE', strings: 'BALANCE',
  csv: 'BALANCE', rig: 'BALANCE',
  anim: 'ANIMATION', audio: 'MEDIA', movies: 'MEDIA', menus: 'MEDIA',
  text: 'DISC', hidden: 'DISC', archives: 'DISC',
  versions: 'PROJECT', mods: 'PROJECT', codemods: 'PROJECT',
  assets: 'LEVEL', saves: 'SAVE',
};

// Which nav button lights up for a tab that has no button of its own.
const PRIMARY = { rules: 'campaign', unlocks: 'campaign', creatures: 'weapons', fields: 'weapons',
                  rig: 'weapons', hidden: 'text', assets: 'editor',
                  states: 'cast' };

function syncSubnav(tab) {
  const bar = document.querySelector('#subnav');
  if (!bar) return;
  const primary = PRIMARY[tab] || tab;
  const c = COMPOSITES[primary];
  bar.innerHTML = '';
  if (!c) { bar.classList.remove('on'); return; }
  const lab = A.el('span', 'slab', c.label);
  bar.append(lab);
  for (const [id, label, title] of c.views) {
    // A feature-gated sub-view has its whole <section> stripped out of the
    // release build, so its button would lead to a blank screen. Absent
    // beats disabled: if the panel is not in the document, the button is not
    // in the strip. Every "both" sub-view is unaffected -- its section is
    // always there.
    if (!document.querySelector('#tab-' + id)) continue;
    const b = A.el('button', tab === id ? 'on' : '', label);
    b.title = title;
    b.onclick = () => A.showTab(id);
    bar.append(b);
  }
  // A strip of one is not a choice. Files kept a lone "Every file" button
  // after Cut & dev content left the build.
  bar.classList.toggle('on', bar.querySelectorAll('button').length > 1);
}

// Row 1 of the nav. A workspace name is a tab: it opens the mode you were
// last on in that workspace, or its first one. Spans, not buttons, because
// app.js wires every `nav button` to showTab(dataset.tab) at load.
const lastInWs = {};
function wireWorkspaceTabs() {
  for (const col of document.querySelectorAll('nav .ws')) {
    const lab = col.querySelector('.wslabel');
    if (!lab) continue;
    lab.setAttribute('role', 'tab');
    lab.tabIndex = 0;
    const go = () => {
      const remembered = lastInWs[col.dataset.ws];
      if (remembered && document.getElementById('tab-' + remembered))
        return A.showTab(remembered);
      const btn = col.querySelector('button[data-tab]');
      if (btn) btn.click();
    };
    lab.onclick = go;
    lab.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
    };
  }
}

function syncWorkspace(tab) {
  const ws = WHERE[tab];
  if (ws) lastInWs[ws] = tab;
  for (const col of document.querySelectorAll('nav .ws'))
    col.classList.toggle('active', col.dataset.ws === ws);
  // A sub-view has no button of its own, so light its parent's.
  const primary = PRIMARY[tab] || tab;
  for (const b of document.querySelectorAll('nav button[data-tab]'))
    b.classList.toggle('on', b.dataset.tab === primary);
}

// ------------------------------------------------------- the header level

// A FACT in the title bar, not a second picker. This used to be a dropdown of
// all 61 levels -- in the same window as the editor's own Levels list, which
// has the filter, the grouping and the counts. Two controls for one choice,
// synced by a poll so they would not disagree; the owner's first question was
// why there were two. The list picks. This shows what it picked, and clicking
// it takes you to the list with the filter ready to type into.
function fillLevelPicker() {
  const badge = document.querySelector('#hdrLevel');
  if (!badge) return;
  badge.onclick = () => {
    A.showTab('editor');
    const f = document.querySelector('#levelFilter');
    if (f) { f.focus(); f.select(); }
  };
  setInterval(() => {
    const lv = A.level;
    // The payload has name/area/archive; the group only exists in the archive
    // path (LVLS_SNG/TOMB4.DAT), so it is read from there.
    const grp = lv && /^LVLS_([A-Z0-9]+)\//i.exec(lv.archive || '');
    const label = lv
      ? (grp ? grp[1] + ' / ' : '') + (lv.name || lv.archive)
        + (lv.area ? '  ·  ' + lv.area : '')
      : 'no level loaded';
    if (badge.textContent !== label) badge.textContent = label;
    badge.style.opacity = lv ? '1' : '.6';
  }, 1000);
}

// ---------------------------------------------------------- the disc pill

// Computed every poll and NEVER cached. This is the fact the user has lost work
// from twice, so a stale answer here is worse than no answer.
// ------------------------------------------------------------- appearance
//
// The stylesheet states the contract this uses: "Everything below is derived
// from these; there are no loose hexes in the stylesheet, so a palette change
// is this block only." So nothing here restyles a component -- it sets the
// custom properties on :root and every one of the 24 panels follows.
//
// STOCK IS THE ABSENCE OF AN OVERRIDE. Choosing Workbench calls
// removeProperty, not setProperty with the same hex, so the shipped look is
// always the stylesheet's own bytes and cannot drift from a copy kept here.
//
// The accent is not stored as the colour you picked. --gold is used as TEXT
// in 27 places, and the stylesheet is explicit about the contrast ratio of
// every ink it defines, so a picked colour is lightened (or, on a light
// surface, darkened) until it clears 4.5:1 against the surface behind it, and
// the panel reports the ratio it reached. Picking "readable orange" instead of
// getting unreadable orange is the whole difference between a colour picker
// and a colour trap.

const hex2rgb = (h) => {
  const s = String(h).replace('#', '').trim();
  const full = s.length === 3 ? s[0] + s[0] + s[1] + s[1] + s[2] + s[2] : s;
  const n = parseInt(full, 16) || 0;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const rgb2hex = (c) => '#' + c.map((v) => {
  const x = Math.round(Math.max(0, Math.min(255, v))).toString(16);
  return x.length < 2 ? '0' + x : x;
}).join('');
const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                          a[2] + (b[2] - a[2]) * t];
const chan = (v) => {
  const x = v / 255;
  return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
};
const lum = (c) => 0.2126 * chan(c[0]) + 0.7152 * chan(c[1]) + 0.0722 * chan(c[2]);
const contrast = (a, b) => {
  const x = lum(a), y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};

// Walk the colour toward white on a dark surface, toward black on a light
// one, until it is legible. Returns the colour unchanged if it already is.
function readable(c, bg, want) {
  const target = lum(bg) > 0.18 ? [0, 0, 0] : [255, 255, 255];
  let out = c;
  for (let t = 0; t <= 1.0001; t += 0.04) {
    out = mix(c, target, t);
    if (contrast(out, bg) >= want) break;
  }
  return out;
}

// Surface sets. Only the greys move; semantic colour and the type ramp are
// left alone, which is the same separation the stylesheet's own header states.
// An empty object means "the stylesheet as shipped".
const THEMES = {
  workbench: { label: 'Workbench (as shipped)', vars: {} },
  graphite: {
    label: 'Graphite — neutral, no blue bias',
    vars: {
      '--bg': '#202020', '--bg2': '#272727', '--bg3': '#333333',
      '--line': '#444444', '--ink': '#d2d2d2', '--dim': '#838383',
      '--dim-ink': '#9e9e9e', '--nav-label': '#a0a0a0',
      '--nav-idle': '#b1b1b1', '--title': '#ededed', '--void': '#191919',
      '--well': '#151515', '--bg2b': '#222222', '--bg2c': '#2c2c2c',
      '--hover': '#3b3b3b', '--grouphd': '#242424', '--faint': '#5f5f5f',
      '--sheet-a': '#272727d9', '--sheet-b': '#272727cc',
      '--sheet-c': '#272727ee', '--sheet-d': '#27272799',
      '--void-a': '#191919cc',
    },
  },
  midnight: {
    label: 'Midnight — darker, for a dim room',
    vars: {
      '--bg': '#14171a', '--bg2': '#191d21', '--bg3': '#232930',
      '--line': '#2e353c', '--ink': '#c6ced5', '--dim': '#78818a',
      '--dim-ink': '#96a1aa', '--nav-label': '#98a3ac',
      '--nav-idle': '#a8b2ba', '--title': '#e0e7ee', '--void': '#0e1113',
      '--well': '#0b0e10', '--bg2b': '#16191c', '--bg2c': '#1e2328',
      '--hover': '#2c343b', '--grouphd': '#171b1f', '--faint': '#525c64',
      '--sheet-a': '#191d21d9', '--sheet-b': '#191d21cc',
      '--sheet-c': '#191d21ee', '--sheet-d': '#191d2199',
      '--void-a': '#0e1113cc',
    },
  },
  daylight: {
    label: 'Daylight — light panels, dark viewport',
    vars: {
      '--scheme': 'light',
      '--bg': '#eef0f2', '--bg2': '#f6f7f8', '--bg3': '#e2e5e8',
      '--line': '#c3c9ce', '--ink': '#23282c', '--dim': '#778087',
      '--dim-ink': '#545c62', '--nav-label': '#565e64',
      '--nav-idle': '#4a5257', '--title': '#12161a',
      // The 3D well stays dark on purpose: it is a viewport, not a panel, and
      // every DCC tool keeps it that way. The scene's own clear colour is set
      // in app.js and is not a CSS variable, so a light well would have shown
      // a light frame around a near-black canvas.
      '--void': '#22262a', '--well': '#ffffff',
      '--bg2b': '#f0f1f3', '--bg2c': '#e9ebed', '--hover': '#dfe3e7',
      '--grouphd': '#e6e9eb', '--faint': '#aab1b6',
      '--danger-ink': '#a3231a', '--conf-est': '#5a3fa0',
      '--conf-dead': '#565e64',
      '--grn': '#2f7d3c', '--red': '#b1372c', '--warn': '#8a6410',
      '--ok-bg': '#d8eddd', '--bad-bg': '#f9dbd7', '--bad-fg': '#5c1a13',
      '--alarm-bg': '#f4c9c3', '--alarm-fg': '#571711',
      '--sheet-a': '#f6f7f8ee', '--sheet-b': '#f6f7f8dd',
      '--sheet-c': '#f6f7f8f7', '--sheet-d': '#f6f7f8bb',
      '--void-a': '#22262acc',
    },
  },
};

const ACCENTS = [
  ['#5b9dd9', 'Workbench blue'], ['#e0955a', 'Rust'],
  ['#6fbf7a', 'Sage'], ['#d9a94f', 'Brass'],
  ['#c98a9b', 'Dust rose'], ['#a68fd6', 'Violet'],
  ['#4fb8b0', 'Teal'], ['#b5b5b5', 'No accent (grey)'],
];

const LOOK_KEY = 'rdr.look';
const STOCK_ACCENT = '#5b9dd9';

function readLook() {
  try {
    const j = JSON.parse(localStorage.getItem(LOOK_KEY) || 'null');
    if (j && j.v === 1) return j;
  } catch { /* private mode */ }
  return { v: 1, theme: 'workbench', accent: STOCK_ACCENT, vars: {} };
}

// Resolve theme + accent into the flat variable list the <head> bootstrap
// replays. Returns { vars, ratio } -- the ratio is measured, not promised.
function resolveLook(themeId, accentHex) {
  const theme = THEMES[themeId] || THEMES.workbench;
  const vars = { ...theme.vars };

  // The surface the accent has to be legible against. Read it from the theme
  // being applied, not from the live document -- at this point the document
  // may still be wearing the previous one.
  const bgHex = vars['--bg'] || '#1e2124';
  const bg = hex2rgb(bgHex);
  const light = lum(bg) > 0.18;

  let ratio = null;
  if (accentHex && accentHex.toLowerCase() !== STOCK_ACCENT) {
    const picked = hex2rgb(accentHex);
    const ink = readable(picked, bg, 4.5);
    ratio = contrast(ink, bg);
    vars['--gold'] = rgb2hex(ink);
    vars['--blue'] = rgb2hex(ink);
    // The three derived tokens the stylesheet already names as "selection +
    // accent, derived from --gold". The mix weights reproduce the shipped
    // hexes to within a couple of levels, checked against them.
    vars['--selbg'] = rgb2hex(mix(bg, ink, light ? 0.22 : 0.28));
    if (light) {
      // A light panel wants a filled primary button, so the face is the
      // accent itself, darkened until white type sits on it safely.
      const face = readable(picked, [255, 255, 255], 4.5);
      vars['--accent-bg'] = rgb2hex(face);
      vars['--accent-fg'] = '#ffffff';
    } else {
      vars['--accent-bg'] = rgb2hex(mix(bg, ink, 0.32));
      vars['--accent-fg'] = rgb2hex(mix([255, 255, 255], ink, 0.15));
    }
  } else if (light) {
    // Stock accent on a light surface still needs the darker treatment, or
    // every heading and link in the product drops below AA.
    const ink = readable(hex2rgb(STOCK_ACCENT), bg, 4.5);
    ratio = contrast(ink, bg);
    vars['--gold'] = rgb2hex(ink);
    vars['--blue'] = rgb2hex(ink);
    vars['--selbg'] = rgb2hex(mix(bg, ink, 0.22));
    vars['--accent-bg'] = rgb2hex(readable(hex2rgb(STOCK_ACCENT),
                                           [255, 255, 255], 4.5));
    vars['--accent-fg'] = '#ffffff';
  }
  return { vars, ratio };
}

function paintLook(vars) {
  const root = document.documentElement;
  // Clear whatever the last look set before setting the new one, or a
  // variable that one theme overrides and the next does not would stick.
  for (const name of [...root.style].filter((p) => p.indexOf('--') === 0))
    root.style.removeProperty(name);
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
}

async function wireAppearance() {
  const sel = document.querySelector('#asTheme');
  const swatches = document.querySelector('#asAccents');
  const custom = document.querySelector('#asAccentCustom');
  const note = document.querySelector('#asLookNote');
  const reset = document.querySelector('#asLookReset');
  if (!sel || !swatches || !custom || !note) return;

  const state = readLook();

  // The chosen look lives in the Studio's settings.json as well as in the
  // localStorage mirror -- see the preferences block in app.js for why both.
  // The file wins when they disagree: the mirror is per browser profile, the
  // file is the app's own answer and the one a fresh profile has to inherit.
  try {
    const saved = (await A.api('settings')).settings || {};
    if (typeof saved.theme === 'string') state.theme = saved.theme;
    if (typeof saved.accent === 'string') state.accent = saved.accent;
  } catch { /* offline, or an older Studio: the mirror stands */ }

  const save = () => {
    const { vars, ratio } = resolveLook(state.theme, state.accent);
    paintLook(vars);
    try {
      localStorage.setItem(LOOK_KEY, JSON.stringify(
        { v: 1, theme: state.theme, accent: state.accent, vars }));
    } catch { /* private mode: it still applies, it just will not persist */ }
    // Only the two answers, not the resolved variables: resolveLook above is
    // the single place the colour maths lives, and storing its OUTPUT in the
    // file as well would be a second copy to drift.
    A.post('settings', { settings: { theme: state.theme,
                                     accent: state.accent } })
      .catch(() => { /* the mirror has it; the next save carries it over */ });
    note.textContent = '';
    const t = THEMES[state.theme] || THEMES.workbench;
    note.append(A.el('div', null, ratio
      ? `${t.label.split(' —')[0]} · accent ${state.accent} — adjusted to `
        + `${rgb2hex(hex2rgb(vars['--gold']))} for ${ratio.toFixed(2)}:1 `
        + `against the panel, which clears AA for body text.`
      : `${t.label.split(' —')[0]} · the accent as shipped.`));
    note.append(A.el('div', null,
      'Applied to the colour variables, so panels added later follow too.'));
    for (const b of swatches.children)
      b.style.outline = b.dataset.hex === state.accent
        ? '2px solid var(--ink)' : '';
  };

  for (const [id, t] of Object.entries(THEMES)) {
    const o = A.el('option', null, t.label);
    o.value = id;
    if (id === state.theme) o.selected = true;
    sel.append(o);
  }
  sel.onchange = () => { state.theme = sel.value; save(); };

  swatches.innerHTML = '';
  for (const [hex, name] of ACCENTS) {
    const b = document.createElement('button');
    b.dataset.hex = hex;
    b.title = name;
    b.setAttribute('aria-label', name);
    b.style.cssText = 'width:22px;height:22px;padding:0;border-radius:4px;'
      + 'outline-offset:2px;background:' + hex;
    b.onclick = () => { state.accent = hex; custom.value = hex; save(); };
    swatches.append(b);
  }
  custom.value = state.accent;
  custom.oninput = () => { state.accent = custom.value; save(); };

  if (reset) {
    reset.onclick = () => {
      state.theme = 'workbench';
      state.accent = STOCK_ACCENT;
      sel.value = 'workbench';
      custom.value = STOCK_ACCENT;
      save();
    };
  }
  save();
}

async function pollDisc() {
  const pill = document.querySelector('#hdrDisc');
  if (!pill) return;
  const tick = async () => {
    const r = await call('versions', 'GET');
    if (notBuilt(r)) {
      // Fall back to the count the shipped route already gives, rather than
      // showing nothing: "unknown" reads as broken. `history` is the cheap
      // one -- `diff` walks the whole disc and measured over 30 s here, so it
      // must never sit on a poll timer.
      try {
        const d = await A.api('history');
        const nfiles = (d.touched || []).length;
        pill.textContent = nfiles
          ? `${nfiles} file${nfiles === 1 ? '' : 's'} changed, no version`
          : 'Working disc matches stock';
        pill.className = 'pill ' + (nfiles ? 'warn' : 'ok');
        pill.title = 'Changed files this session.';
        pill.onclick = () => A.showTab('versions');
        pill.style.cursor = 'pointer';
      } catch { pill.textContent = '…'; }
      return;
    }
    if (!r.ok) { pill.textContent = '…'; return; }
    const d = r.on_disc || {};
    // Shared with the Changes tab, which used to count history instead.
    window.__onDisc = d;
    window.dispatchEvent(new CustomEvent('rdr:disc-state'));
    if (d.id && d.matches) {
      pill.textContent = `On disc: “${d.name}”`;
      pill.className = 'pill ok';
    } else if (d.id) {
      pill.textContent = `“${d.name}” + ${d.files_changed} unsaved`;
      pill.className = 'pill warn';
    } else if (d.files_changed) {
      pill.textContent = `${d.files_changed} file`
        + `${d.files_changed === 1 ? '' : 's'} differ from stock, no version`;
      pill.className = 'pill warn';
    } else {
      pill.textContent = 'Working disc matches stock';
      pill.className = 'pill ok';
    }
    pill.onclick = () => A.showTab('versions');
    pill.style.cursor = 'pointer';
  };
  tick();
  // The Studio answers ONE request at a time and /api/versions is a full disc
  // diff, so putting it on a short timer starves everything else in the
  // product. This fact only changes when something is written, so refresh it
  // when something is written -- the journal poll is the honest trigger --
  // and otherwise leave the server alone.
  let lastHistory = -1;
  setInterval(async () => {
    try {
      const h = await A.api('history');
      const n = (h.entries || []).length;
      if (n === lastHistory) return;
      lastHistory = n;
      tick();
    } catch {}
  }, 20000);
  pill.title = 'Open Versions.';
  pill.addEventListener('click', () => setTimeout(tick, 400));
}

// ------------------------------------------------------------------ Ctrl+K

// Ctrl+K is an accelerator, not navigation, and it must name every one of the
// twenty-four destinations -- otherwise it becomes the place things hide.
// DESIGN-REVIEW A17: the destination's own name in ink, where it lives in
// dim type on the right -- not "BALANCE ▸ Weapons & stats ▸ Weapons" in bold
// accent on every row. Once per row; the observer passes over rows again.
function splitPath(row) {
  if (row.dataset.split) return;
  const b = row.querySelector('b');
  if (!b) return;
  row.dataset.split = '1';
  const parts = b.textContent.split(' ▸ ');
  if (parts.length < 2) return;
  b.textContent = parts.pop();
  row.append(A.el('span', 'ppath', parts.join(' ▸ ')));
}

function extendPalette() {
  const list = [
    ['LEVEL ▸ 3D editor', 'The level in 3D', 'editor'],
    ['LEVEL ▸ Lighting', 'Lighting, fog and weather', 'env'],
    ['LEVEL ▸ Textures', 'Textures on this level', 'textures'],
    ['LOGIC ▸ Cast', 'Characters and behaviours', 'cast'],
    ['LOGIC ▸ Cast ▸ Behaviour tables',
     'What a behaviour binds, and the numbers inside it', 'states'],
    ['LOGIC ▸ Missions', 'Campaign and game rules', 'campaign'],
    ['LOGIC ▸ Missions ▸ Game Rules', 'Difficulty, unlocks, cheats', 'rules'],
    ['LOGIC ▸ Behaviour', 'The AI condition vocabulary', 'behaviour'],
    ['LOGIC ▸ Links', 'Doors between levels', 'links'],
    ['LOGIC ▸ Scripting', 'Mission scripts, read-only', 'scripting'],
    ['BALANCE ▸ Weapons & stats', 'Weapon numbers', 'weapons'],
    ['BALANCE ▸ Weapons & stats ▸ Creatures', 'Enemy and animal stats', 'creatures'],
    ['BALANCE ▸ Weapons & stats ▸ Body & Rig',
     'Collision, hit boxes, arsenal, physics chains, animation slots', 'rig'],
    ['BALANCE ▸ Weapons & stats ▸ Find setting', 'One value across the whole disc', 'fields'],
    ['BALANCE ▸ Text', 'Anything the game says', 'strings'],
    ['BALANCE ▸ Tables', 'Sound banks, music, shops, the multiplayer roster', 'csv'],
    ['LOGIC ▸ Missions ▸ Unlocks & shop', 'Unlock switches and shop prices', 'unlocks'],
    ['ANIMATIONS ▸ Animations', 'Watch clips on their skeleton', 'anim'],
    ['MEDIA ▸ Audio', 'Voices, music and banks', 'audio'],
    ['MEDIA ▸ Cutscenes', 'Cutscenes and still screens', 'movies'],
    ['MEDIA ▸ Menus', 'The main menu and the in-game HUD', 'menus'],
    ['DISC ▸ Files', 'Search the disc, edit any file', 'text'],
    ['DISC ▸ Files ▸ Cut & dev content', 'What was left on the disc', 'hidden'],
    ['DISC ▸ Archives', 'Where there is room', 'archives'],
    ['MY MOD ▸ Versions', 'Saved copies: save, restore, compare', 'versions'],
    ['MY MOD ▸ Code Mods', 'Noclip, freeze, camera lock, slow-mo, cheats menu', 'codemods'],
    ['MY MOD ▸ Changes', 'Changed files and edit history', 'mods'],
    ['SAVE ▸ Save Editor', 'Money and progress on a PS2 memory card', 'saves'],
    ['LEVEL ▸ Library', 'Everything you can place', 'editor'],
  ];
  const host = document.querySelector('#paletteList');
  if (!host) return;
  // app.js owns the PALETTE array and re-renders this list on every
  // keystroke. Rather than touch that array, append the workspace-named
  // destinations after each render, filtered by the same query.
  // The rows app.js renders still carry the old tab names. Leaving both sets
  // in one list would put "3D Editor" next to "LEVEL ▸ 3D editor", which is the
  // two-words-for-one-thing problem this whole pass is undoing. They are
  // RENAMED IN PLACE rather than hidden, because app.js's arrow-key index
  // counts rows and a hidden row would still be selectable by Enter. The
  // rows that are actions rather than destinations -- Play, Boot test, Undo
  // -- keep their names, because they were never destinations.
  const RENAMED = new Map(Object.entries({
    '3D Editor': ['LEVEL ▸ 3D editor', 'editor'],
    Assets: ['LEVEL ▸ Library', 'editor'],
    Environment: ['LEVEL ▸ Lighting', 'env'],
    Textures: ['LEVEL ▸ Textures', 'textures'],
    Text: ['BALANCE ▸ Text', 'strings'],
    Weapons: ['BALANCE ▸ Weapons & stats ▸ Weapons', 'weapons'],
    Creatures: ['BALANCE ▸ Weapons & stats ▸ Creatures', 'creatures'],
    Settings: ['BALANCE ▸ Weapons & stats ▸ Find setting', 'fields'],
    Animation: ['ANIMATIONS ▸ Animations', 'anim'],
    'Voices & music': ['MEDIA ▸ Audio', 'audio'],
    Campaign: ['LOGIC ▸ Missions', 'campaign'],
    'Game Rules': ['LOGIC ▸ Missions ▸ Game Rules', 'rules'],
    Files: ['DISC ▸ Files', 'text'],
    Archives: ['DISC ▸ Archives', 'archives'],
    Hidden: ['DISC ▸ Files ▸ Cut & dev content', 'hidden'],
    Changes: ['MY MOD ▸ Changes', 'mods'],
    'Restore vanilla': ['Reset to stock', null],
  }));
  const obs = new MutationObserver(() => {
    if (host.dataset.busy) return;
    host.dataset.busy = '1';
    const q = (document.querySelector('#paletteInput')?.value || '')
      .trim().toLowerCase();
    const claimed = new Set();
    for (const row of host.children) {
      const b = row.querySelector('b');
      if (!b) continue;
      const warn = b.textContent.startsWith('⚠ ');
      const hit = RENAMED.get(b.textContent.replace(/^⚠ /, '').trim());
      if (!hit) continue;
      b.textContent = (warn ? '⚠ ' : '') + hit[0];
      // Stamp it, so the next pass -- there is always a next pass, because
      // appending rows is itself a mutation -- still knows this destination
      // is already on the list and does not add a second copy of it.
      if (hit[1]) { row.dataset.navdest = hit[1]; claimed.add(hit[1]); }
    }
    for (const row of host.children) splitPath(row);
    const seen = new Set([...host.querySelectorAll('[data-navdest]')]
      .map((n) => n.dataset.navdest).concat([...claimed]));
    for (const [name, desc, tab] of list) {
      if (seen.has(tab)) continue;
      // Same rule as the sub-view strip above: a feature-gated destination has
      // its whole <section> cut out of the release build, so listing it here
      // offers a row that navigates nowhere. Measured on the current source:
      // all 26 destinations have a section in the internal build, so this only
      // ever removes one that was already dead. It covers roadmap, strings,
      // csv, menus, links, scripting and states.
      if (!document.querySelector('#tab-' + tab)) continue;
      if (q && !(name.toLowerCase().includes(q) || desc.toLowerCase().includes(q)))
        continue;
      const row = A.el('div', 'prow');
      row.dataset.navdest = tab;
      row.append(A.el('b', null, name), A.el('span', 'muted', desc));
      row.onclick = () => {
        A.showTab(tab);
        document.querySelector('#palette').classList.remove('on');
      };
      splitPath(row);
      host.append(row);
    }
    delete host.dataset.busy;
  });
  obs.observe(host, { childList: true });
}

export function init(bridge) {
  A = bridge;
  const apply = () => {
    const on = document.querySelector('.tab.on');
    const tab = on ? on.id.replace('tab-', '') : 'editor';
    syncSubnav(tab);
    syncWorkspace(tab);
  };
  // showTab is app.js's, and it toggles .tab.on. Watch that instead of
  // wrapping it, so nothing in app.js has to change. One shared, debounced
  // watcher serves every module -- see transfer.js.
  onPanel(apply);
  wireWorkspaceTabs();
  fillLevelPicker();
  pollDisc();
  extendPalette();
  wireAppearance();
}
