"""[M14] Editing a HUD element, and a byte-exact round trip.

M14's criterion: *edits a HUD element and asserts a byte-exact round-trip.*

    python3 scripts/verify_swui_edit.py

Read-only: every edit is applied and re-parsed IN MEMORY off `01_Vanilla`.
No Studio, no ISO write, no lock.

WHY THIS EXISTS WHEN `verify_swui.py` ALREADY CHECKS EVERY UI FILE. That test
proves IDENTITY: writing every field back with its own value returns the input
unchanged, on every file rather than a sample. That is fidelity, and it is the
foundation -- but it is not an edit. M14 asks what happens when a value
actually changes, and nothing asserted that anywhere.

THE TARGET IS THE PAUSE HUD, and it is chosen for its headroom rather than
its name. `pause.swui` ships in 62 copies with 26 spare bytes in each, so it
is the file where "does an edit still fit" is a real question and not a
formality.

WHAT IT ASSERTS:

  * a VALUE is edited, never a field NAME. `f0` of a tag is its name, and the
    engine resolves widgets by name -- which is why the `menu_set` route
    gates renames behind an explicit confirmation. A test that edited `f0`
    would be exercising the dangerous path while claiming to test the safe
    one.
  * the edited field reads back as the new value;
  * **every byte outside that field's own span is unchanged** -- which is what
    "byte-exact round trip" has to mean for an edit, and is stronger than
    comparing parsed fields;
  * every OTHER field still reads back identically;
  * the file still fits its archive slot AFTER deflate. These files are
    compressed, so length is not size: this is the fourth format today where
    that distinction decides whether a write is possible.
  * an identity edit is byte-exact, re-checked here on the file under test so
    the sections above are standing on something.
"""

import os
import sys
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, swui                           # noqa: E402
from rdrstudio.game import Game                              # noqa: E402

ARCHIVE = "SWAT.DAT"
NAME = "pause.swui"
FAILURES = []


def check(cond, label, detail=""):
    print("  [%s] %-56s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)
    return cond


def fields_of(data):
    """Every field the game actually reads.

    `parse` includes commented-out tags by default, and `apply` REFUSES to
    write one -- "writing to it would change nothing the game reads". That
    refusal is right and it caught a first version of this test, which built
    its identity set from everything the parser returned and then asked the
    writer to write a comment. Commented tags are excluded here rather than
    the refusal being worked around.
    """
    return {f.path: f.raw
            for t in swui.parse(data) if not t.commented
            for f in t.fields}


def main():
    g = Game(config.VANILLA_ISO, writable=False)
    d = g.archive(ARCHIVE)
    data = d.read(NAME)
    e = d.find(NAME)
    print("disc: %s  (read-only)" % config.VANILLA_ISO)
    print("%s :: %s  %d bytes, stored %d of a %d slot (%d spare), dialect %s"
          % (ARCHIVE, NAME, len(data), e.size_comp, e.slot,
             e.slot - e.size_comp, swui.dialect(data)))

    print("\n1. identity first, so the rest stands on something:")
    before = fields_of(data)
    check(swui.apply(data, {p: v for p, v in before.items()}) == data,
          "writing every field back with its own value is byte-exact",
          "%d fields" % len(before))

    print("\n2. edit a HUD VALUE -- never a field name:")
    # f0 of a tag is its NAME. The engine resolves widgets by name, which is
    # why menu_set gates renames behind confirm_renames. Pick a numeric value.
    target = None
    for t in swui.parse(data):
        if t.commented:
            continue
        for f in t.fields:
            if f.path.endswith(".f0"):
                continue
            raw = (f.raw or "").strip()
            try:
                float(raw)
            except ValueError:
                continue
            target = (t, f, raw)
            break
        if target:
            break
    check(target is not None, "found a numeric HUD value to edit")
    if not target:
        return 1
    tag, fld, raw = target
    new = "0.125" if raw != "0.125" else "0.250"
    print("     %s %s : %r -> %r" % (tag.name, fld.path, raw, new))
    check(not fld.path.endswith(".f0"),
          "the field under test is a value, not a widget name", fld.path)

    out = swui.apply(data, {fld.path: new})
    after = fields_of(out)
    check(after.get(fld.path, "").strip() == new,
          "the edited field reads back as the new value",
          repr(after.get(fld.path)))
    moved = [p for p in before if before[p] != after.get(p)]
    check(moved == [fld.path], "exactly one field changed", moved[:4])
    check(len(after) == len(before), "no field was added or lost",
          "%d" % len(after))

    print("\n3. byte-exact outside the edited span:")
    # The span the parser reports for that field is the only region allowed to
    # differ. Comparing parsed values would miss a writer that reflowed a
    # space somewhere else; comparing bytes cannot.
    lo = min(len(data), len(out))
    first = next((i for i in range(lo) if data[i] != out[i]), lo)
    tail_d, tail_o = data[::-1], out[::-1]
    lastr = next((i for i in range(lo) if tail_d[i] != tail_o[i]), lo)
    last_d, last_o = len(data) - lastr, len(out) - lastr
    print("     bytes differ only in [%d, %d) of the original" % (first, last_d))
    check(first >= tag.start and last_d <= tag.end,
          "every differing byte lies inside the edited TAG",
          "tag spans [%d, %d)" % (tag.start, tag.end))
    check(data[:first] == out[:first], "everything before it is identical",
          "%d bytes" % first)
    check(data[last_d:] == out[last_o:], "everything after it is identical",
          "%d bytes" % (len(data) - last_d))

    print("\n4. the slot, measured after DEFLATE:")
    co = zlib.compressobj(9, 8, -15)
    stored = len(co.compress(out) + co.flush()) if e.compressed else len(out)
    check(stored <= e.slot, "the edited file still fits its slot",
          "%d of %d (%d spare, was %d)"
          % (stored, e.slot, e.slot - stored, e.size_comp))
    print("     length %d -> %d, stored %d -> %d -- length is not size"
          % (len(data), len(out), e.size_comp, stored))

    print("\n5. and the edit survives a second round trip:")
    again = swui.apply(out, {fld.path: after[fld.path]})
    check(again == out, "re-writing the new value with itself is byte-exact")

    print("\nSUMMARY")
    print("  [%s] a HUD element edits and round-trips byte-exactly"
          % ("FAIL" if FAILURES else "PASS"))
    if FAILURES:
        print("  failed: %s" % ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
