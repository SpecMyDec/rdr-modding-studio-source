// The PS2 / Xbox switch (bottom right of the status bar).
//
// One mod, both games: the Data Editor's mods folder is the mod either way. On PS2, Build puts it on the working
// disc and Play starts PCSX2. On Xbox, Build writes it -- converted to the Xbox's files -- into the recomp's
// mods\RDR Modding Studio folder, and Play starts the recomp's RDR.exe (rdrstudio/xboxmode.py). The other editors
// keep showing the PS2 disc: what they change goes to the Xbox through the same build.

const $ = (s) => document.querySelector(s);

async function call(route, body) {
  const r = await fetch('/api/' + route, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                           body: JSON.stringify(body || {}) });
  const j = await r.json();
  if (j && j.error) throw new Error(j.error);
  if (j && j.ok === false) throw new Error(j.error_text || 'refused');
  return j;
}

function toast(msg, kind) {
  if (window.rdrToast) window.rdrToast(msg, kind);
  else console.log(msg);
}

let state = { platform: 'ps2' };

function apply(st) {
  state = st || state;
  const xbox = state.platform === 'xbox';
  document.body.classList.toggle('xbox-mode', xbox);
  const sw = $('#deGameSel');
  if (sw) sw.value = state.platform;
  if (sw) {
    sw.title = xbox
      ? 'Xbox: Build writes the mod into ' + (state.out || 'the recomp\'s mods folder')
        + ' and Play starts ' + (state.exe || 'RDR.exe') + '. Click PS2 to go back.'
      : 'PS2: Build puts the mod on the working disc and Play starts PCSX2. Click Xbox to build and play it on the '
        + 'Xbox recomp instead (the same mod).';
  }
  const pill = $('#xboxpill');
  if (pill) {
    pill.style.display = xbox ? '' : 'none';
    // in the status bar where PCSX2's is on the PS2 side, read the same way
    pill.textContent = state.exe_ok ? 'RDR.exe ' + (state.running ? 'running' : 'ready') : 'RDR.exe ?';
    pill.className = 'sb ' + (state.exe_ok ? (state.running ? 'warn' : 'ok') : 'warn');
    pill.style.color = '';
    pill.title = state.exe_ok ? state.exe + (state.reference ? '\nUnedited Xbox game: ' + state.reference
      : '\nNo Xbox reference set (Data Editor Settings): the recomp\'s assets folder is used as the unedited game')
      : 'No RDR.exe set: click Xbox to point at it';
  }
  { const p = $('#pcsx2pill'); if (p) p.style.display = xbox ? 'none' : ''; }   // the disc pill stays: the PS2 side's state
  const play = $('#btnPlay');
  if (play) play.title = xbox ? 'Build into the Xbox game (your edits as they are now), then start it (the recomp\'s RDR.exe)'
    : 'Start the game in PCSX2.';
  if (window.rdrRenderBuild) window.rdrRenderBuild();
  const build = $('#deBuild');
  if (build) {
    build.textContent = xbox ? 'Build into the Xbox game' : 'Build into the PS2 game';
    build.title = xbox ? 'Make the mod the Xbox\'s files and put them in the recomp\'s mods folder ('
      + (state.out || 'mods\\RDR Modding Studio') + ')'
      : 'Rebuild the .dat files your mods go in and put them on the working disc';
  }
}

// A question in the page (no native dialog): buttons [[label, value, cls]] -> the value clicked, or null (Escape)
function askInPage(title, text, buttons) {
  return new Promise((res) => {
    const back = document.createElement('div');
    back.style.cssText = 'position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.55);display:flex;align-items:flex-start;justify-content:center';
    const box = document.createElement('div');
    box.style.cssText = 'margin-top:16vh;width:min(520px,92vw);background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:14px 16px';
    const h = document.createElement('div'); h.textContent = title;
    h.style.cssText = 'font:var(--t-head);color:var(--title);margin-bottom:8px';
    const p = document.createElement('p'); p.className = 'muted'; p.textContent = text;
    p.style.cssText = 'margin:0 0 12px;white-space:pre-wrap;word-break:break-word';
    const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end';
    const done = (v) => { back.remove(); document.removeEventListener('keydown', esc); res(v); };
    const esc = (e) => { if (e.key === 'Escape') done(null); };
    for (const [label, v, cls] of buttons) {
      const b = document.createElement('button'); b.textContent = label; if (cls) b.className = cls;
      b.onclick = () => done(v); row.append(b);
    }
    box.append(h, p, row); back.append(box);
    document.addEventListener('keydown', esc);
    document.body.append(back);
  });
}

// for the other pages (a native confirm()'s Cancel closed the app window)
window.rdrAskInPage = askInPage;

// A line of text asked in the page (no native prompt): -> the text, or null
function askTextInPage(title, text, value) {
  return new Promise((res) => {
    const input = document.createElement('input');
    input.value = value || ''; input.style.cssText = 'width:100%;margin:6px 0 4px';
    const back = document.createElement('div');
    back.style.cssText = 'position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.55);display:flex;align-items:flex-start;justify-content:center';
    const box = document.createElement('div');
    box.style.cssText = 'margin-top:16vh;width:min(520px,92vw);background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:14px 16px';
    const h = document.createElement('div'); h.textContent = title; h.style.cssText = 'font:var(--t-head);color:var(--title);margin-bottom:6px';
    const p = document.createElement('div'); p.className = 'muted'; p.textContent = text;
    const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:8px';
    const done = (v) => { back.remove(); res(v); };
    const cancel = document.createElement('button'); cancel.textContent = 'Cancel'; cancel.onclick = () => done(null);
    const ok = document.createElement('button'); ok.textContent = 'OK'; ok.className = 'primary'; ok.onclick = () => done(input.value.trim() || null);
    input.onkeydown = (e) => { if (e.key === 'Enter') ok.click(); if (e.key === 'Escape') cancel.click(); };
    row.append(cancel, ok); box.append(h, p, input, row); back.append(box);
    document.body.append(back); input.focus();
  });
}

let backupAsked = false;
// The first time the Xbox game is edited: a copy of its game files as they are now ("unedited assets")
async function maybeAskBackup() {
  if (backupAsked || state.platform !== 'xbox' || !state.assets_ok || state.backup_exists || state.backup_asked) return;
  backupAsked = true;
  // asked in the page itself: the app's own confirm() closed the whole window on Cancel, and a check run's headless
  // page answered it by itself. Only a click on "Don't ask again" is remembered.
  const answer = await askInPage('First time editing the Xbox game',
    'Make a backup of its unedited game files first? They are copied, as they are now, to:\n' + state.backup
    + '\n\nfrom the recomp\'s game files:\n' + state.assets + '\n\nReset to stock (Xbox) puts the game back from it.',
    [['Don\'t ask again', 'never'], ['Not now', 'later'], ['Back up now', 'yes', 'primary']]);
  if (answer === 'never') {
    try { await call('de_x_xbox_set', { backup_asked: true }); } catch { /* asked again next time */ }
    return;
  }
  if (answer !== 'yes') return;                            // not now: asked again next start
  if (window.rdrDEStartJob) {
    const ok = await window.rdrDEStartJob('de_x_xbox_backup', {}, 'Back up the Xbox game files');
    toast(ok ? 'The unedited Xbox game files are in ' + state.backup : 'The backup stopped (see the Log)', ok ? 'good' : 'bad');
    refresh();
  }
}

async function refresh() {
  try { apply(await call('de_x_xbox_state')); } catch { return; /* an older server: no switch */ }
  maybeAskBackup();
}

async function pick(p) {
  if (p === state.platform) return;
  const sel = $('#deGameSel');
  const back = () => { if (sel) sel.value = state.platform; };
  try {
    let st = state;
    if (p === 'xbox' && !st.exe_ok) {
      const exe = await askTextInPage('The Xbox recomp', 'Where is the recomp\'s RDR.exe? (its folder or the file)', st.exe || '');
      if (!exe) { back(); return; }
      st = await call('de_x_xbox_set', { exe });
    }
    apply(await call('de_x_xbox_set', { platform: p }));
    maybeAskBackup();
    toast(p === 'xbox'
      ? 'Xbox: Build writes the mod into the recomp\'s mods folder and Play starts RDR.exe.'
      : 'PS2: Build puts the mod on the working disc and Play starts PCSX2.', 'good');
  } catch (e) { back(); toast(e.message, 'bad'); }
}

// While a build runs (body.building): everything can be looked at, nothing changed. Requests that would change
// something are answered here with a refusal instead of reaching the Studio; reading goes on as usual.
const READ_OK = /^(de_job|de_cancel|de_build|de_x_xbox_build|de_x_xbox_state|rebuild_pending|disc_state|note|de_x_mp_lineup|de_x_lineup_info|de_x_creature_stats|de_x_voice_lines|de_x_sound_sets|de_x_alt_looks|de_x_sd_options|streams_search|de_overview|de_x_xbox_play)$/;
const WRITES = /(_set\b|set_|save|apply|write|patch|import|delete|remove|upload|transfer|rename|clone|restore|replace|paste|new_|newdat|_add\b|mp_add|picture|edit|commit|undo|redo|from_game|adopt|place|move|swap|grow|unlock|_bg\b|mode|crop|convert|export_mod|mod_load|mod_save|play$)/i;
const origFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  try {
    const method = ((init && init.method) || 'GET').toUpperCase();
    if (document.body.classList.contains('building') && method !== 'GET') {
      const url = typeof input === 'string' ? input : input.url;
      const route = (url.split('/api/')[1] || '').split('?')[0];
      if (route && !READ_OK.test(route) && WRITES.test(route)) {
        const body = JSON.stringify({ ok: false, error_text: 'A build is running: look around all you like, changes wait until it is done.' });
        return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
    }
  } catch { /* never in the way */ }
  return origFetch(input, init);
};

// Play on the Xbox: before the page's own handler (capture), which would start PCSX2. It builds into the Xbox game
// first (the top Build: queued edits, then the Xbox build), so the game always starts with the mod as it is now, and
// starts RDR.exe only when that build went through.
let playing = false;
document.addEventListener('click', async (e) => {
  if (state.platform !== 'xbox') return;
  const b = e.target.closest && e.target.closest('#btnPlay');
  if (!b) return;
  e.stopImmediatePropagation();
  e.preventDefault();
  if (playing || document.body.classList.contains('building')) return toast('A build is running: Play waits for it.');
  playing = true;
  try {
    if (state.running) throw new Error('The Xbox game (RDR.exe) is running: quit it first, then Play builds and starts it');
    if (window.rdrBuild) {
      toast('Building into the Xbox game first…');
      const ok = await window.rdrBuild();
      if (!ok) throw new Error('The build did not go through (see the Log): the game was not started');
    }
    const r = await call('de_x_xbox_play');
    toast('Started the Xbox game: ' + r.launched, 'good');
  } catch (err) { toast(err.message, 'bad'); }
  finally { playing = false; }
}, true);

function init() {
  const sw = $('#deGameSel');
  if (!sw) return;
  sw.addEventListener('change', () => pick(sw.value));
  window.rdrPlatform = () => state.platform;
  refresh();
  setInterval(refresh, 15000);   // RDR.exe running or not
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
