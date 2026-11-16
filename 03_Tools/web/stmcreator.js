// stmcreator.js -- MEDIA > Audio > Voices & music > Create a stream: a .stm made from any audio, with a loop.
//
// The format has no loop END: a stream loops when it runs out, back to its restart point, with the decoder state it
// had there (rdrstudio/stm.py). So the server CUTS the audio at the loop end and encodes in two passes for that
// state, and both loop points are put on the driver's read grid -- 128 samples at 12/24 kHz, 256 at 48 kHz -- the
// grid every one of the game's 179 looping streams is on (a 48 kHz loop on the 64-byte grid alone restarts wrong:
// it plays to the end, then garbage). The editor snaps to the same grid, so the preview loops as the game will.
//
// The waveform: white, the loop region green with its waveform light green, the loop start a green line, its end a
// red line, the playhead yellow. Shift + left click sets the loop start, Shift + right click its end; the wheel
// zooms, Ctrl + drag pans, a click moves the playhead, Space plays / pauses.

export async function openStmCreator(ctx) {
  const { post, toast, el, onSaved } = ctx;
  const back = el('div');
  back.style.cssText = 'position:fixed;inset:0;z-index:140;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center';
  const box = el('div');
  box.style.cssText = 'width:min(1100px,96vw);background:var(--bg2);border:1px solid var(--line);border-radius:6px;display:flex;flex-direction:column;max-height:94vh';
  back.append(box);
  document.body.append(back);

  // ---- the bar
  const bar = el('div');
  bar.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid var(--line)';
  const title = el('b', null, 'Create a stream (.stm)');
  const openB = el('button', '', 'Open audio…');
  openB.title = '.wav, .mp3, .flac, .ogg, or a .stm to edit its loop';
  const gameB = el('button', '', 'Open a game stream…');
  gameB.title = 'One of the disc\'s streams, by name (to change its loop, or to make yours from it)';
  const rateSel = el('select');
  for (const [v, l] of [[48000, '48000 Hz (the game\'s music)'], [24000, '24000 Hz (voices, effects)'], [12000, '12000 Hz']]) rateSel.append(new Option(l, v));
  const chSel = el('select');
  chSel.append(new Option('Stereo', '2'), new Option('Mono', '1'));
  const loopCb = el('input'); loopCb.type = 'checkbox'; loopCb.checked = true;
  const loopLab = el('label'); loopLab.style.cssText = 'display:flex;gap:4px;align-items:center';
  loopLab.append(loopCb, el('span', null, 'Loop'));
  const info = el('span', 'muted'); info.style.fontSize = '11px';
  bar.append(title, openB, gameB, rateSel, chSel, loopLab, info);

  // ---- the waveform
  const wrap = el('div');
  wrap.style.cssText = 'position:relative;height:300px;margin:8px 12px;border:1px solid var(--line);background:#262626';
  const cv = el('canvas');
  cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;cursor:crosshair';
  cv.tabIndex = 0;
  wrap.append(cv);
  const hint = el('div', 'muted', 'Shift + left click: loop start · Shift + right click: loop end · wheel: zoom · Ctrl + drag: pan · click: playhead · Space: play / pause');
  hint.style.cssText = 'font-size:11px;padding:0 12px';
  const times = el('div'); times.style.cssText = 'font-size:12px;padding:4px 12px;display:flex;gap:18px;flex-wrap:wrap';

  // ---- the foot
  const foot = el('div');
  foot.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;border-top:1px solid var(--line)';
  const nameIn = el('input'); nameIn.placeholder = 'stream name (letters, digits, _)'; nameIn.style.width = '260px';
  nameIn.title = 'The name the game asks for. A name the disc has already replaces that stream (e.g. RailroadedA_Music_CD7T4); a new name plays only where something asks for it.';
  const saveB = el('button', 'primary', 'Save to streams.dat');
  saveB.title = 'Into streams.dat\'s Data Editor folder: the next Build puts it on the disc';
  const dlB = el('button', '', 'Download .stm');
  const status = el('span', 'muted'); status.style.cssText = 'font-size:11px;flex:1';
  const closeB = el('button', '', 'Close');
  foot.append(el('span', 'muted', 'Name'), nameIn, saveB, dlB, status, closeB);
  box.append(bar, wrap, hint, times, foot);

  // ---- state
  let source = null;            // the opened file's bytes (re-decoded when the rate changes)
  let srcLoop = null;           // a .stm's own loop, in seconds
  let buf = null;               // AudioBuffer at the chosen rate
  let data = [];                // Float32Array per channel
  let n = 0, rate = 48000;
  let ls = 0, le = 0;           // loop, samples
  let ph = 0;                   // playhead, samples
  let v0 = 0, spp = 1;          // view: first sample, samples per pixel
  let peaks = null;             // [mins, maxs] per block of 64
  let audio = null, srcNode = null, playing = false, t0 = 0, off0 = 0, raf = 0;
  const unit = () => 128 * Math.max(1, Math.floor(rate / 24000));
  const snap = (v) => Math.max(0, Math.round(v / unit()) * unit());
  const fmt = (s) => { const t = s / rate; const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(3).padStart(6, '0')}`; };

  function close() { stop(); cancelAnimationFrame(raf); back.remove(); document.removeEventListener('keydown', onKey); }
  closeB.onclick = close;

  async function decodeAt(r) {
    if (!source) return;
    const ac = new OfflineAudioContext(2, 1, r);
    const b = await ac.decodeAudioData(source.slice(0));
    const oldRate = rate, oldN = n;
    buf = b; rate = r; n = b.length;
    data = [];
    for (let c = 0; c < b.numberOfChannels; c++) data.push(b.getChannelData(c));
    if (b.numberOfChannels < 2) { chSel.value = '1'; chSel.disabled = true; } else chSel.disabled = false;
    // loop points: the .stm's own, or kept through a rate change, or the whole sound
    if (srcLoop) { ls = snap(srcLoop.start * r); le = Math.min(snap(srcLoop.end * r), Math.floor(n / unit()) * unit()); srcLoop = null; loopCb.checked = true; }
    else if (oldN && le) { ls = snap(ls * r / oldRate); le = Math.min(snap(le * r / oldRate), Math.floor(n / unit()) * unit()); }
    else { ls = 0; le = Math.floor(n / unit()) * unit(); }
    ph = 0;
    buildPeaks();
    v0 = 0; spp = Math.max(n / Math.max(1, cv.clientWidth), 0.05);
    status.textContent = '';
    draw();
  }

  function buildPeaks() {
    const B = 64, m = Math.ceil(n / B);
    const mins = new Float32Array(m), maxs = new Float32Array(m);
    for (let k = 0; k < m; k++) {
      let lo = 1, hi = -1;
      const e = Math.min(n, (k + 1) * B);
      for (const ch of data) for (let i = k * B; i < e; i++) { const v = ch[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
      mins[k] = lo; maxs[k] = hi;
    }
    peaks = [mins, maxs];
  }

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const W = Math.max(1, Math.round(cv.clientWidth * dpr)), H = Math.max(1, Math.round(cv.clientHeight * dpr));
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    const g = cv.getContext('2d');
    g.fillStyle = '#262626'; g.fillRect(0, 0, W, H);
    if (!n) {
      g.fillStyle = '#888'; g.font = `${13 * dpr}px sans-serif`;
      g.fillText('Open audio (.wav, .mp3, .flac, .ogg) or a .stm', 14 * dpr, 24 * dpr);
      times.textContent = '';
      return;
    }
    const pxS = spp / dpr;                 // samples per device pixel
    const x = (s) => (s - v0) / pxS;
    const loopOn = loopCb.checked;
    if (loopOn) {
      g.fillStyle = '#1e5a1e';
      const a = Math.max(0, x(ls)), b = Math.min(W, x(le));
      if (b > a) g.fillRect(a, 0, b - a, H);
    }
    const mid = H / 2, amp = H * 0.46;
    for (let px = 0; px < W; px++) {
      const s0 = v0 + px * pxS, s1 = s0 + pxS;
      if (s1 < 0 || s0 >= n) continue;
      let lo = 1, hi = -1;
      if (pxS >= 64 && peaks) {
        const k0 = Math.max(0, Math.floor(s0 / 64)), k1 = Math.min(peaks[0].length, Math.ceil(s1 / 64));
        for (let k = k0; k < k1; k++) { if (peaks[0][k] < lo) lo = peaks[0][k]; if (peaks[1][k] > hi) hi = peaks[1][k]; }
      } else {
        const i0 = Math.max(0, Math.floor(s0)), i1 = Math.min(n, Math.max(i0 + 1, Math.ceil(s1)));
        for (const ch of data) for (let i = i0; i < i1; i++) { const v = ch[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
      }
      if (hi < lo) continue;
      const inLoop = loopOn && s0 >= ls && s0 < le;
      g.fillStyle = inLoop ? '#9ed69e' : '#e6e6e6';
      const y0 = mid - hi * amp, y1 = mid - lo * amp;
      g.fillRect(px, y0, 1, Math.max(1, y1 - y0));
    }
    const line = (s, col, w) => { const xx = x(s); if (xx < -2 || xx > W + 2) return; g.fillStyle = col; g.fillRect(xx - w / 2, 0, w, H); };
    if (loopOn) { line(ls, '#2ecc2e', 2 * dpr); line(le, '#e02020', 2 * dpr); }
    line(ph, '#f0d020', 2 * dpr);
    const px = x(ph);
    g.fillStyle = '#f0d020';
    g.beginPath(); g.moveTo(px - 6 * dpr, 0); g.lineTo(px + 6 * dpr, 0); g.lineTo(px, 9 * dpr); g.closePath(); g.fill();
    times.innerHTML = '';
    const t = (lab, v, col) => { const s = el('span'); s.innerHTML = `<span class="muted">${lab}</span> <b style="color:${col || 'inherit'}">${v}</b>`; times.append(s); };
    t('Length', fmt(n));
    t('Playhead', fmt(ph), '#c9a227');
    if (loopOn) {
      t('Loop start', fmt(ls) + ` (${ls})`, '#2ecc2e');
      t('Loop end', fmt(le) + ` (${le})`, '#e02020');
      t('The stream', `ends at the loop end (${fmt(le)}) and restarts at ${fmt(ls)}`);
    }
    info.textContent = `${rate} Hz · ${data.length === 2 ? 'stereo' : 'mono'} source · loop points on ${unit()}-sample steps`;
  }

  // ---- playing (Web Audio; loops exactly as the game will, since the points are on its grid)
  function stop() {
    if (srcNode) { try { srcNode.stop(); } catch (e) { /* done */ } srcNode.disconnect(); srcNode = null; }
    playing = false;
  }
  function play() {
    if (!buf) return;
    audio = audio || new AudioContext();
    stop();
    srcNode = audio.createBufferSource();
    srcNode.buffer = buf;
    if (loopCb.checked && le > ls) { srcNode.loop = true; srcNode.loopStart = ls / rate; srcNode.loopEnd = le / rate; if (ph >= le) ph = ls; }
    srcNode.connect(audio.destination);
    off0 = ph / rate; t0 = audio.currentTime;
    srcNode.start(0, off0);
    srcNode.onended = () => { if (srcNode) { playing = false; } };
    playing = true;
    tick();
  }
  function tick() {
    cancelAnimationFrame(raf);
    if (!playing || !audio) { draw(); return; }
    let t = off0 + (audio.currentTime - t0);
    if (loopCb.checked && le > ls && t >= le / rate) t = ls / rate + ((t - ls / rate) % ((le - ls) / rate));
    if (t * rate >= n) { playing = false; ph = 0; draw(); return; }
    ph = Math.floor(t * rate);
    // keep the playhead in view
    const W = cv.clientWidth;
    if ((ph - v0) / spp > W * 0.95 || ph < v0) v0 = Math.max(0, ph - W * spp * 0.1);
    draw();
    raf = requestAnimationFrame(tick);
  }
  const toggle = () => { if (playing) { stop(); draw(); } else play(); };

  // ---- the mouse and keys
  const sampleAt = (e) => { const r = cv.getBoundingClientRect(); return Math.max(0, Math.min(n, v0 + (e.clientX - r.left) * spp)); };
  let drag = null;
  cv.addEventListener('contextmenu', (e) => e.preventDefault());
  cv.addEventListener('mousedown', (e) => {
    if (!n) return;
    cv.focus();
    e.preventDefault();
    if (e.ctrlKey && e.button === 0) { drag = { pan: true, x: e.clientX, v: v0 }; return; }
    if (e.shiftKey && (e.button === 0 || e.button === 2)) {
      const which = e.button === 0 ? 'start' : 'end';
      loopCb.checked = true;
      setLoop(which, sampleAt(e));
      drag = { loop: which };
      return;
    }
    if (e.button === 0) { ph = Math.floor(sampleAt(e)); if (playing) play(); else draw(); drag = { head: true }; }
  });
  function setLoop(which, s) {
    const lim = Math.floor(n / unit()) * unit();
    if (which === 'start') ls = Math.min(snap(s), Math.max(0, le - unit()));
    else le = Math.min(lim, Math.max(snap(s), ls + unit()));
    if (playing) play(); else draw();
  }
  window.addEventListener('mousemove', (e) => {
    if (!drag || !back.isConnected) return;
    if (drag.pan) { v0 = Math.max(0, Math.min(n - spp * cv.clientWidth * 0.5, drag.v - (e.clientX - drag.x) * spp)); draw(); }
    else if (drag.loop) setLoop(drag.loop, sampleAt(e));
    else if (drag.head) { ph = Math.floor(sampleAt(e)); draw(); }
  });
  window.addEventListener('mouseup', () => { if (drag && drag.head && playing) play(); drag = null; });
  cv.addEventListener('wheel', (e) => {
    if (!n) return;
    e.preventDefault();
    const at = sampleAt(e), r = cv.getBoundingClientRect();
    spp = Math.max(0.05, Math.min(n / Math.max(1, cv.clientWidth), spp * Math.exp(e.deltaY * 0.0015)));
    v0 = Math.max(0, at - (e.clientX - r.left) * spp);
    draw();
  }, { passive: false });
  function onKey(e) {
    if (!back.isConnected) return;
    if (e.code !== 'Space') return;
    const t = e.target;
    if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && t !== cv) return;
    e.preventDefault();
    toggle();
  }
  document.addEventListener('keydown', onKey);
  new ResizeObserver(() => draw()).observe(wrap);
  loopCb.onchange = () => { if (playing) play(); else draw(); };
  rateSel.onchange = async () => { const was = playing; stop(); await decodeAt(+rateSel.value); if (was) play(); };

  // ---- opening
  const b64of = (u8) => { let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000)); return btoa(s); };
  const u8of = (b64) => { const s = atob(b64); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; };
  async function openBytes(bytes, fileName, isStm) {
    stop();
    status.textContent = 'Reading…';
    try {
      if (isStm) {
        const r = await post('de_x_stm_open', { data: b64of(new Uint8Array(bytes)) });
        if (r.ok === false) throw new Error(r.error_text || 'refused');
        await useStm(r);
      } else {
        source = bytes;
        srcLoop = null;
        await decodeAt(+rateSel.value);
      }
      if (!nameIn.value) nameIn.value = fileName.replace(/\.[^.]*$/, '').toLowerCase().replace(/[^a-z0-9_]+/g, '_');
    } catch (e) { status.textContent = ''; toast('Could not open it: ' + e.message, 'bad'); }
  }
  async function useStm(r) {
    source = u8of(r.wav_b64).buffer;
    rateSel.value = String(r.rate);
    srcLoop = r.loop ? { start: r.loop.start / r.rate, end: r.loop.end / r.rate } : null;
    loopCb.checked = !!r.loop;
    await decodeAt(r.rate);
  }
  openB.onclick = () => {
    const f = el('input'); f.type = 'file'; f.accept = '.wav,.mp3,.flac,.ogg,.stm,audio/*';
    f.onchange = async () => { const file = f.files[0]; if (!file) return; await openBytes(await file.arrayBuffer(), file.name, /\.stm$/i.test(file.name)); };
    f.click();
  };
  gameB.onclick = async () => {
    const name = prompt('The stream\'s name (as listed under Voices & music, e.g. RailroadedA_Music_CD7T4):', ctx.stream || '');
    if (!name) return;
    stop();
    status.textContent = 'Reading…';
    try {
      const r = await post('de_x_stm_open', { stream: name.trim() });
      if (r.ok === false) throw new Error(r.error_text || 'refused');
      await useStm(r);
      nameIn.value = name.trim().replace(/\.stm$/i, '').toLowerCase();
    } catch (e) { status.textContent = ''; toast(e.message, 'bad'); }
  };

  // ---- making it
  async function make(save) {
    if (!n) return toast('Open some audio first', 'bad');
    const name = nameIn.value.trim();
    if (!/^[A-Za-z0-9_]+(\.stm)?$/.test(name)) { nameIn.focus(); return toast('Name it with letters, digits and _', 'bad'); }
    const nch = +chSel.value === 1 || data.length < 2 ? 1 : 2;
    const chans = [];
    for (let c = 0; c < nch; c++) {
      const out = new Int16Array(n);
      for (let i = 0; i < n; i++) {
        const v = nch === 1 && data.length > 1 ? (data[0][i] + data[1][i]) / 2 : data[c][i];
        out[i] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
      }
      chans.push(b64of(new Uint8Array(out.buffer)));
    }
    status.textContent = 'Encoding…';
    saveB.disabled = dlB.disabled = true;
    try {
      const r = await post('de_x_stm_make', { name, rate, chans, loop: loopCb.checked ? { start: ls, end: le } : null, save });
      if (r.ok === false) throw new Error(r.error_text || 'refused');
      const lp = r.loop ? ` · loops ${r.loop.start_s}s → ${r.loop.end_s}s (the stream ends there)` : ' · no loop';
      if (save) {
        status.textContent = `${r.name} saved: ${r.seconds}s, ${r.rate} Hz, ${r.channels === 2 ? 'stereo' : 'mono'}${lp}. `
          + (r.replaces ? 'It replaces the game\'s stream of that name. ' : 'A new name: it plays only where something asks for it. ')
          + 'The next Build puts it on the disc.';
        if (onSaved) onSaved();
      } else {
        const blob = new Blob([u8of(r.stm_b64)], { type: 'application/octet-stream' });
        const a = el('a'); a.href = URL.createObjectURL(blob); a.download = r.name; document.body.append(a); a.click(); a.remove();
        status.textContent = `${r.name}: ${r.seconds}s${lp}.`;
      }
    } catch (e) { status.textContent = ''; toast(e.message, 'bad'); }
    saveB.disabled = dlB.disabled = false;
  }
  saveB.onclick = () => make(true);
  dlB.onclick = () => make(false);
  draw();
}
