"""Can the current shape of a chunk be read back off the disc?

Geometry edits are always derived from the VANILLA chunk, so the ops the
client sends are a total rather than a delta. That total used to be
remembered only in the browser, which made it wrong in two ordinary
situations: reload the page and it was gone, so the next drag computed from
vanilla and silently reverted earlier moves; press undo and it went stale,
so the next drag re-applied the move that had just been undone.

`geomedit.recover` removes the need to remember it. These checks are chosen
so a plausible-but-wrong implementation fails:

  1. **Identity.** An untouched chunk must recover as exactly nothing --
     zero move, no turn, scale 1. A fit that drifts here would show phantom
     edits on every vanilla level.
  2. **Round trip.** Apply a known move/turn/scale, then recover it. The
     numbers must come back to the metre and the degree, and with the same
     SIGN -- `rotate_y` is a negative mathematical rotation, so an unnegated
     angle passes a symmetric test and turns buildings the wrong way.
  3. **Composition.** Two edits in a row recover as the composed total, not
     as the last one, because that total is what gets re-sent to the server.
  4. **Residual honesty.** A chunk reshaped in a way no yaw-and-scale can
     describe must report `exact: false`, not a confident wrong answer.
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdrstudio import config                         # noqa: E402
from rdrstudio.game import Game                      # noqa: E402
from rdrstudio.level import Level                    # noqa: E402
from rdrstudio import geomedit                       # noqa: E402
from rdrstudio.studio import _reread                 # noqa: E402

# (A hardcoded absolute path to the WORKING iso used to be assigned here.
# It was never read -- dead since this script only ever opens the
# stock disc -- but a literal path to one machine's working ISO sitting
# in a file is a trap for the next person who reaches for it, so it is
# gone rather than merely unused. Caught by rdr-re.)
# Derived, not a literal from the machine that wrote this. The hardcoded
# drive path made this script runnable only under Windows python; it
# passed in the suite for that reason alone and died anywhere else.
VANILLA = Path(config.VANILLA_ISO)
ARCHIVE = "LVLS_SNG/TOMB.DAT"


def main():
    ok = True

    def check(what, got, want=True):
        nonlocal ok
        good = (got == want)
        ok &= good
        print("  %-58s %s" % (what, "ok" if good else "FAIL (%r)" % (got,)))

    game = Game(VANILLA, writable=False)
    lvl = Level(game, ARCHIVE)
    rsc = lvl._load_rsc()
    models = [i for i, c in enumerate(rsc.chunks)
              if c.tag == "MOD0" and rsc.model(i)
              and geomedit.editable(rsc.model(i))]
    print("%s: %d editable MOD0 chunks\n" % (ARCHIVE, len(models)))
    check("found editable chunks to test", len(models) > 20)

    # ---- 1. identity
    worst_id = 0.0
    for c in models[:60]:
        m = rsc.model(c)
        sh, resid = geomedit.recover(m, m)
        worst_id = max(worst_id, abs(sh["tx"]), abs(sh["ty"]), abs(sh["tz"]),
                       abs(sh["rot"]), abs(sh["s"] - 1.0), resid)
    print("  identity: worst deviation over 60 chunks = %.3e" % worst_id)
    check("an untouched chunk recovers as no change at all", worst_id < 1e-4)

    # ---- 2 and 3. round trip and composition
    rng = random.Random(20260730)
    worst_t = worst_r = worst_s = 0.0
    tested = 0
    for c in models[:40]:
        stock = rsc.model(c)
        raw = rsc.raw(c)
        tx, ty, tz = (rng.uniform(-40, 40) for _ in range(3))
        deg = rng.uniform(-170, 170)
        sc = rng.uniform(0.4, 2.5)

        # Same order applyShape uses -- scale, then turn, then move -- and
        # composed the way the server composes it. Each op rewrites the
        # vertices it is given, so passing the vanilla mesh to every step
        # would make each one overwrite the last and only the final op would
        # survive. The server re-reads between steps (studio.py `_reread`);
        # not doing the same here tests a pipeline that does not exist.
        about = geomedit.centre(stock)
        mm = stock
        out = geomedit.scale(raw, mm, sc, about=about)
        mm = dict(stock, verts=_reread(out, stock))
        out = geomedit.rotate_y(out, mm, math.radians(deg), about=about)
        mm = dict(stock, verts=_reread(out, stock))
        out = geomedit.translate(out, mm, tx, ty, tz)
        moved = _remodel(rsc, c, out)
        if moved is None:
            continue
        sh, resid = geomedit.recover(stock, moved)
        tested += 1
        worst_t = max(worst_t, abs(sh["tx"] - tx), abs(sh["ty"] - ty),
                      abs(sh["tz"] - tz))
        d = (sh["rot"] - deg + 180) % 360 - 180
        worst_r = max(worst_r, abs(d))
        worst_s = max(worst_s, abs(sh["s"] - sc))

    print("\n  over %d chunks with a known random transform applied:" % tested)
    print("     worst move error  = %.3e m" % worst_t)
    print("     worst turn error  = %.3e deg" % worst_r)
    print("     worst scale error = %.3e" % worst_s)
    check("tested a useful number of chunks", tested > 15)
    check("move recovers to the millimetre", worst_t < 1e-3)
    check("turn recovers to the degree, with the right sign", worst_r < 1e-2)
    check("scale recovers exactly", worst_s < 1e-4)

    # ---- 4. residual honesty: a reshape no similarity can describe
    c = models[0]
    stock = rsc.model(c)
    raw = rsc.raw(c)
    squashed = geomedit.transform(raw, stock,
                                  lambda x, y, z: (x * 2.0, y * 0.25, z))
    m2 = _remodel(rsc, c, squashed)
    if m2 is not None:
        sh, resid = geomedit.recover(stock, m2)
        print("\n  a non-uniform squash reports residual %.3f m" % resid)
        check("a reshape no move/turn/scale describes is flagged", resid > 1e-3)
    else:
        print("\n  (could not re-decode the squashed chunk; check skipped)")

    # ---- 5. collision bounds, which have their own encoding
    bnds = [i for i, c in enumerate(rsc.chunks) if c.tag == "BND0"]
    print("\n  %d BND0 collision chunks" % len(bnds))
    worst_bt = worst_br = 0.0
    done = 0
    for c in bnds[:12]:
        try:
            raw = rsc.raw(c)
            stock = geomedit.bound_verts(raw)
        except Exception:
            continue
        if not stock["verts"]:
            continue
        tx, ty, tz = (rng.uniform(-30, 30) for _ in range(3))
        deg = rng.uniform(-150, 150)
        about = geomedit.bound_centre(rsc.bound(c))
        out = geomedit.rotate_bound_y(raw, math.radians(deg), about)
        out = geomedit.translate_bound(out, tx, ty, tz)
        sh, resid = geomedit.recover(stock, geomedit.bound_verts(out))
        if sh is None:
            continue
        done += 1
        worst_bt = max(worst_bt, abs(sh["tx"] - tx), abs(sh["ty"] - ty),
                       abs(sh["tz"] - tz))
        worst_br = max(worst_br, abs((sh["rot"] - deg + 180) % 360 - 180))
    print("     over %d bounds: worst move %.3e m, worst turn %.3e deg"
          % (done, worst_bt, worst_br))
    check("collision bounds recover too", done > 3 and worst_bt < 1e-3
          and worst_br < 1e-2)

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    game.close()
    return 0 if ok else 1


def _remodel(rsc, chunk, raw):
    """Decode a chunk from replacement bytes.

    Mirrors `Rsc.model`, which reads `self.raw(index)`; the only difference is
    where the bytes come from. Kept in step with it deliberately -- decoding
    the edited packet by a different route would not test the same thing.
    """
    from rdrstudio import vif
    from rdrstudio.rsc import _merge_parts
    parts = rsc.parts(chunk)
    if parts:
        return _merge_parts(vif, raw, parts, raw[1])
    return vif.extract_best(raw)


if __name__ == "__main__":
    sys.exit(main())
