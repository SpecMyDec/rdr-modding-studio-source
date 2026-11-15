// arcadd.js — DISC ▸ Archives ▸ "Add a file…".
//
// The backend has been able to add a brand-new entry to an archive for a
// while (dave.add: sorted insert under the packed alphabet's own collation,
// verified by re-parse and read-back), and until now no button spoke to it.
// This module is that button. It goes through the transfer sheet so the
// check-first contract reads exactly like every other import in the product:
// pick a target, see the frozen plan and the insert position, then write.
//
// The one honest difference from every other write: an added file cannot be
// removed again — the format has no "delete entry" — so the sheet says out
// loud that Ctrl+Z will not take this one back, and the plan's undo row
// shows 0 steps rather than pretending.

import { call, openSheet, el } from './transfer.js';

let A = null;
let archives = null;          // cached /api/archives rows, fetched on demand

async function archiveNames() {
  if (archives) return archives;
  const rows = await A.api('archives');
  archives = (rows || []).map((r) => r.archive || r).filter(Boolean);
  return archives;
}

// Uploaded bytes -> base64, in chunks: one big apply() call overflows the
// argument stack on multi-megabyte files.
function toB64(buf) {
  let bin = '';
  for (let i = 0; i < buf.length; i += 8192)
    bin += String.fromCharCode.apply(null, buf.subarray(i, i + 8192));
  return btoa(bin);
}

function openAddSheet() {
  const state = { archive: A.currentArchive || '', name: '', bytes: null,
                  fileName: '' };
  let planned = false;        // the Apply press is only honoured after a dry run
  let timer = null;

  const sheet = openSheet({
    title: 'Add a file',
    subject: {
      title: 'A brand-new entry in an archive',
      where: 'Added at its sorted position. Existing files are untouched.',
      format: 'Cannot be undone. Reset to stock removes it.',
      rung: 'untested',
    },
    primary: 'Add it',
    renderOptions(host) {
      const row = (label, control) => {
        const d = el('div');
        d.style.cssText = 'display:flex;align-items:center;gap:8px;margin:0 0 7px';
        const l = el('label', 'muted', label);
        l.style.cssText = 'width:64px;flex:none;font-size:11px';
        d.append(l, control);
        host.append(d);
        return control;
      };

      const arcSel = el('select');
      arcSel.style.cssText = 'flex:1;min-width:0';
      arcSel.title = 'Which archive the new file goes into.';
      row('Archive', arcSel);
      arcSel.append(el('option', 'muted', 'loading the archive list…'));
      archiveNames().then((names) => {
        arcSel.innerHTML = '';
        for (const n of names) {
          const o = el('option', null, n);
          o.value = n;
          arcSel.append(o);
        }
        if (state.archive
            && names.indexOf(state.archive) >= 0) arcSel.value = state.archive;
        state.archive = arcSel.value || '';
        replan();
      }).catch((e) => {
        arcSel.innerHTML = '';
        arcSel.append(el('option', null, 'could not list archives'));
        sheet.refuse({ code: 'server_fault',
                       message: 'Could not read the archive list.',
                       fix: '', detail: e.message });
      });
      arcSel.onchange = () => { state.archive = arcSel.value; replan(); };

      const nameInp = el('input');
      nameInp.placeholder = 'e.g. mymod_notes.txt';
      nameInp.style.cssText = 'flex:1;min-width:0';
      nameInp.title = 'Lower-case letters, digits and . _ - / ( ) # $ ~';
      row('Name', nameInp);
      nameInp.oninput = () => {
        state.name = nameInp.value.trim();
        clearTimeout(timer);
        timer = setTimeout(replan, 450);
      };

      const pick = el('button', null, 'Choose content…');
      pick.title = 'The bytes the new file will hold, from a file on this '
        + 'computer. Checking is free and writes nothing.';
      const picked = el('span', 'muted', 'no content chosen');
      picked.style.fontSize = '11px';
      const wrap = el('span');
      wrap.style.cssText = 'flex:1;display:flex;align-items:center;gap:8px';
      wrap.append(pick, picked);
      row('Content', wrap);
      pick.onclick = () => {
        const inp = document.createElement('input');
        inp.type = 'file';
        inp.onchange = async () => {
          const f = inp.files && inp.files[0];
          if (!f) return;
          state.bytes = new Uint8Array(await f.arrayBuffer());
          state.fileName = f.name;
          picked.textContent =
            f.name + ' — ' + state.bytes.length.toLocaleString() + ' bytes';
          // Convenience, not policy: an empty name takes the picked file's,
          // lower-cased the way every entry on the disc already is.
          if (!state.name) {
            state.name = f.name.toLowerCase();
            nameInp.value = state.name;
          }
          replan();
        };
        inp.click();
      };
    },
    startLabel: 'Pick an archive, a name and the content — then this prices '
      + 'the write and shows where the entry will land.',
    async onApply(api) {
      if (!planned) return;
      api.busy('Writing ' + state.name + ' into ' + state.archive + '…');
      const r = await call('file_add', 'POST', {
        archive: state.archive, name: state.name,
        data_b64: toB64(state.bytes), dry: false,
      });
      if (!r.ok) return api.refuse(r.problem);
      const pos = 'position ' + (r.index + 1) + ' of ' + r.entries;
      api.done('<b>Added ' + r.file + '</b> to ' + state.archive
        + ' at sorted ' + pos + '.<br>'
        + (r.verified
            ? 'Verified on the disc.'
            : 'Read-back mismatch. Reset to stock.')
        + '<br><span class="muted">Adds cannot be undone.</span>');
      A.toast('Added ' + r.file + ' to ' + state.archive + '.', 'good');
      A.refreshHistory();
      // The table of contents on screen is now missing one row; repaint it.
      if (A.currentArchive === state.archive && A.loadToc) A.loadToc(state.archive);
    },
  });

  async function replan() {
    if (!state.archive || !state.name || !state.bytes) {
      planned = false;
      sheet.busy('Pick an archive, a name and the content — then this prices '
        + 'the write and shows where the entry will land.');
      return;
    }
    planned = false;
    sheet.busy('Checking ' + state.name + ' against ' + state.archive + '…');
    const r = await call('file_add', 'POST', {
      archive: state.archive, name: state.name,
      data_b64: toB64(state.bytes), dry: true,
    });
    if (!r.ok) return sheet.refuse(r.problem);
    planned = true;
    sheet.plan(r.plan);
    // The two facts the plan table has no row for: where in the sorted table
    // of contents the entry lands, and the permanent cost the server priced.
    const host = document.querySelector('#xferPlan');
    if (host) {
      const extra = el('div', 'muted');
      extra.style.cssText = 'font-size:11px;margin-top:6px;line-height:1.55';
      extra.textContent = 'Goes in at sorted position ' + (r.index + 1)
        + ' of ' + r.entries_after
        + (r.compressed ? ', stored deflate-compressed. ' : ', stored raw. ')
        + (r.note || '');
      host.append(extra);
    }
  }
}

export function init(bridge) {
  A = bridge;
  const btn = document.querySelector('#arcAddBtn');
  // Absent beats disabled: a release build strips the bar along with the
  // route it speaks to, and this module simply has nothing to wire.
  if (!btn) return;
  btn.onclick = openAddSheet;
}
