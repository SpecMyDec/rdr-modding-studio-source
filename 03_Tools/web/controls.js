// controls.js — THE CONTROL MODEL.
//
// Right mouse = go. Left mouse = touch. Letters = do.
//
// The collision this states away: with an object selected, bare `D` — the
// strafe-right key — fired POST /api/duplicate, and on key repeat, so holding
// D to strafe spammed duplicates. The fix itself is two substitutions in
// app.js (flyable() now requires the right button, and duplicate moved to
// Ctrl+D). What lives here is the rest of the model: the escape hatch that
// makes an object-covered viewport still orbitable, and the one-line legend
// that states the rule where you need it.
//
// Two invariants make the rule hold rather than merely describe it:
//   1. No bare letter ever mutates the level. Ctrl+D and Delete are the
//      complete list of mutating keys.
//   2. Shift is a modifier and never a verb.
//
// A third was claimed by the rule and not delivered: GO AND TOUCH AT THE SAME
// TIME. Holding right to fly and left-clicking to select did nothing, in
// either order, because a mouse is one pointer carrying a button mask and the
// browser does not send pointerdown/pointerup for a button pressed while
// another is already held -- it sends pointermove. Measured; the trace and
// the fix live at watchButtons() in app.js and in TransformControls.js.
// Everything downstream now reads button transitions from one place, so
// "right mouse = go" and "left mouse = touch" are simultaneous facts rather
// than alternating modes.

let A = null;

// Alt+LMB orbits anywhere, even over an object, which is the escape hatch
// for when the gizmo is sitting exactly where you want to drag.
//
// This used to call stopImmediatePropagation() from a capture-phase listener,
// with the comment "stop the picker seeing it; OrbitControls still does".
// MEASURED (headless Chromium 151): at the target element, capture listeners
// run BEFORE bubble listeners whatever order they were registered in, and the
// immediate-stop flag then skips that element's remaining listeners in BOTH
// phases. OrbitControls, TransformControls and the picker are all bubble-phase
// listeners on this same canvas -- so the gesture reached none of them and
// Alt+LMB did nothing at all. The picker and the gizmo now decline an
// alt-click themselves, which needs no assumption about listener order, and
// this is left only to make sure the camera is live when it arrives.
function wireAltOrbit() {
  const c = A.renderer?.domElement;
  if (!c || !A.controls) return;
  c.addEventListener('pointerdown', (e) => {
    if (e.button === 0 && e.altKey) A.controls.enabled = true;
  }, true);
}

// DESIGN-REVIEW B4: the key line was drawn over the HUD's "details" chip and
// cut off mid-word at 1366. It is one plain line in the status bar now, shown
// while the 3D editor is open. The Controls box in the viewport still lists
// every key, and survives fullscreen.
function wireHudLegend() {
  const sb = document.querySelector('#statusbar');
  if (!sb || document.querySelector('#ctlRule')) return;
  const span = document.createElement('span');
  span.id = 'ctlRule';
  span.className = 'sb dim';
  span.textContent = 'RMB+WASD fly · LMB select · G R T gizmo · '
    + 'Ctrl+D duplicate · Del delete';
  const spacer = sb.querySelector('.spacer');
  sb.insertBefore(span, spacer);
  const sync = () => {
    const on = document.querySelector('#tab-editor')?.classList.contains('on');
    span.style.display = on ? '' : 'none';
  };
  sync();
  new MutationObserver(sync).observe(document.querySelector('#tab-editor'),
    { attributes: true, attributeFilter: ['class'] });
}

export function init(bridge) {
  A = bridge;
  wireAltOrbit();
  wireHudLegend();
}
