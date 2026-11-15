"""The eight per-character formats read, and rewrite themselves byte-exact.

`.dam .sphs .ars .hot .tail .acc .armr .rep` -- 939 files in SWAT.DAT, 1,426
copies across the disc. The properties checked here, on every file, not a
sample:

  1. Structure. Every file yields the rows the format promises, and the
     declared counter (`NumTypes`, `NumBoundSpheres`, `NumHotSpots`,
     `NumArmor`, `NumAccessories`) agrees with what was actually found.

  2. Fidelity, which is the one that matters. Writing EVERY field back with
     its own current value must return the input unchanged, byte for byte.
     These entries have a median of one to twenty-five bytes of slot headroom
     -- `.sphs` median is ONE -- so a parser that renormalised `0.150000` to
     `0.15`, or reflowed a tab, or dropped a trailing space, would silently
     shrink or grow files that cannot afford either.

  3. One REAL edit per file changes exactly the bytes it addressed and not one
     more. Round-tripping a value to itself proves the spans are consistent;
     it does not prove they point at the right thing. A no-op parser passes
     property 2 perfectly.

  4. Cross-references resolve: `.sphs`/`.hot` parents against the same-stem
     `.skel`, `.ars` names against `swat.guns` TypeName, `.rep` clips against
     the disc's `.anim` roster. These are reported, not enforced -- the stock
     disc itself has 1,496 dangling `.rep` clips, so a check that failed on
     them would be a check that fails on Rockstar.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game  # noqa: E402
from rdrstudio import charfile as CF, skeleton  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def _rewrite_everything(ext, data, doc):
    """Every value in the document, written back as itself."""
    changes = {}
    if ext == "dam":
        for t in doc["tables"]:
            for r in t["rows"]:
                for c in r["cells"]:
                    changes[c["path"]] = c["value"]
    elif ext == "ars":
        changes = {i["name"]: i["flag"] for i in doc["items"] if i["on"]}
    elif ext == "rep":
        changes = {r["path"]: r["clip"] for r in doc["rows"]}
    else:
        for s in doc["sections"]:
            for r in s["rows"]:
                changes[r["path"]] = r["value"]
    return CF.splice(CF._decode(data), CF.edits_for(ext, data, changes)), changes


def _one_real_edit(ext, doc):
    """(path, new_value, old_value) for a single genuine change, or None.

    Chosen to be the same text width wherever the format allows it, because
    that is what the editor actually does -- `render_number` pins the decimal
    count of the value it replaces.
    """
    if ext == "dam":
        c = doc["tables"][0]["rows"][0]["cells"][0]
        return c["path"], "0.7", c["value"]
    if ext == "ars":
        on = [i for i in doc["items"] if i["on"]]
        return on[0]["name"], "1" if on[0]["flag"] == "0" else "0", on[0]["flag"]
    if ext == "rep":
        r = doc["rows"][0]
        return r["path"], "zz_" + r["clip"][3:], r["clip"]
    # The candidate has to be one the WRITER will render as something other
    # than what is already there. `render_number` pins the decimal count of
    # the old value, so asking for 0.123456 where the file says `3` gets you
    # `0` -- a "change" that changes nothing and would have been reported as a
    # broken span rather than a bad test.
    for s in doc["sections"]:
        for r in s["rows"]:
            if r["control"] not in ("float", "int"):
                continue
            for cand in ("0.123456", "3", "7", "1.5", "42"):
                if CF.render_number(r["value"], cand) != r["value"].strip():
                    return r["path"], cand, r["value"]
    return None


def main():
    game = Game(VANILLA_ISO)
    swat = game.archive("SWAT.DAT")

    bones_cache = {}

    def bones_for(name):
        stem = name.rsplit(".", 1)[0]
        if stem not in bones_cache:
            try:
                bones_cache[stem] = {b.name for b in
                                     skeleton.parse(swat.read(stem + ".skel"))[2]}
            except Exception:
                bones_cache[stem] = set()
        return bones_cache[stem]

    catalogue = []
    guns = swat.read("swat.guns").decode("latin-1")
    for line in guns.splitlines():
        bits = line.replace(":", " ").replace("=", " ").split()
        if len(bits) >= 2 and bits[0] == "TypeName":
            catalogue.append(bits[1])

    anims = set()
    for arc in game.archives():
        try:
            for e in game.archive(arc):
                if e.ext == "anim":
                    anims.add(e.name[:-5].lower())
        except Exception:
            continue

    seen = Counter()
    fields = Counter()
    read_fail, identity_fail, rewrite_fail = [], [], []
    counter_fail = []
    fuzz_fail, fuzz_skipped, fuzz_span = [], [], {}
    parents_ok = parents_total = 0
    parents_nocheck = 0
    ars_unknown = []
    rep_ok = rep_total = 0

    for e in swat:
        ext = (e.ext or "").lower()
        if ext not in CF.KINDS:
            continue
        seen[ext] += 1
        data = swat.read(e)
        try:
            ctx = {}
            if ext in ("sphs", "hot"):
                ctx["bones"] = bones_for(e.name)
            if ext == "ars":
                ctx["catalogue"] = catalogue
            if ext == "rep":
                ctx["anims"] = anims
            doc = CF.read(ext, data, **ctx)
        except Exception as ex:
            read_fail.append((e.name, "%s: %s" % (type(ex).__name__, ex)))
            continue

        if CF.splice(CF._decode(data), []) != data:
            identity_fail.append((e.name, "empty splice changed the bytes"))

        # `.rep` and `.tail` are exempt on purpose and the exemption is a
        # measured fact, not a shrug. NumReplacements is 3500 in all 192 `.rep`
        # while the real row count runs 697-1,902, so it is a table capacity;
        # `.tail` writes one NumNodes per chain and a file can hold six.
        c = doc.get("counter") or {}
        if (ext not in ("rep", "tail") and c.get("declared") is not None
                and str(c["actual"]) != c["declared"]):
            counter_fail.append((e.name, c["key"], c["declared"], c["actual"]))

        try:
            out, changes = _rewrite_everything(ext, data, doc)
        except Exception as ex:
            rewrite_fail.append((e.name, "%s: %s" % (type(ex).__name__, ex)))
            continue
        fields[ext] += len(changes)
        if out != data:
            where = next((i for i in range(min(len(out), len(data)))
                          if out[i] != data[i]), min(len(out), len(data)))
            rewrite_fail.append((e.name, "byte %d: %r -> %r"
                                 % (where, data[where:where + 24],
                                    out[where:where + 24])))

        # One real edit. It must change something, and it must change ONE
        # region -- a parser whose spans are off by a field would still pass
        # the rewrite-to-itself check above, because both sides would be wrong
        # in the same way.
        pick = _one_real_edit(ext, doc)
        if pick is None:
            fuzz_skipped.append(e.name)
        else:
            path, new, old = pick
            if ext == "ars":
                want = {i["name"]: i["flag"] for i in doc["items"] if i["on"]}
                want[path] = new
                edits = CF.edits_for(ext, data, want)
            else:
                edits = CF.edits_for(ext, data, {path: new})
            got = CF.splice(CF._decode(data), edits)
            lo = next((i for i in range(min(len(got), len(data)))
                       if got[i] != data[i]), None)
            if lo is None:
                fuzz_fail.append((e.name, "edit %s=%s changed nothing (was %s)"
                                  % (path, new, old)))
            else:
                hi_g, hi_d = len(got) - 1, len(data) - 1
                while hi_g >= lo and hi_d >= lo and got[hi_g] == data[hi_d]:
                    hi_g -= 1
                    hi_d -= 1
                span = max(hi_d - lo, hi_g - lo) + 1
                fuzz_span[ext] = max(fuzz_span.get(ext, 0), span)
                if span > 64:
                    fuzz_fail.append((e.name, "edit %s=%s disturbed %d bytes"
                                      % (path, new, span)))

        if ext in ("sphs", "hot"):
            known = ctx["bones"]
            for s in doc["sections"]:
                for r in s["rows"]:
                    if r["label"] != "parent":
                        continue
                    if not known:
                        parents_nocheck += 1
                    else:
                        parents_total += 1
                        parents_ok += r["value"] in known
        if ext == "ars":
            for i in doc["items"]:
                if i["on"] and not i["in_catalogue"]:
                    ars_unknown.append((e.name, i["name"]))
        if ext == "rep":
            rep_total += len(doc["rows"])
            rep_ok += sum(1 for r in doc["rows"] if r["ok"])

    print("files read, by kind")
    for ext in CF.ORDER:
        print("   .%-5s %4d files, %7d values rewritten"
              % (ext, seen[ext], fields[ext]))
    print("   %-6s %4d files, %7d values"
          % ("total", sum(seen.values()), sum(fields.values())))

    print("\none real edit per file, widest region it disturbed")
    print("   " + "  ".join("%s %d B" % (k, v) for k, v in
                            sorted(fuzz_span.items())))
    if fuzz_skipped:
        print("   no editable value found in %d file(s): %s"
              % (len(fuzz_skipped), fuzz_skipped[:4]))

    print("\ncross-references")
    print("   .sphs/.hot parent bones resolved : %d / %d  (%d more could not be "
          "checked: no same-stem .skel exists anywhere on the disc, so those "
          "files get free text, not a picker)"
          % (parents_ok, parents_total, parents_nocheck))
    print("   .ars names not in swat.guns      : %d" % len(ars_unknown))
    print("   .rep clips naming a real .anim   : %d / %d  (%d dangling on the "
          "stock disc)" % (rep_ok, rep_total, rep_total - rep_ok))
    print("   swat.guns TypeName entries       : %d" % len(catalogue))
    print("   .anim stems disc-wide            : %d" % len(anims))

    for label, rows in (("could not read", read_fail),
                        ("identity splice changed bytes", identity_fail),
                        ("declared counter disagrees", counter_fail),
                        ("rewrite was not byte-exact", rewrite_fail),
                        ("one edit did not land where it was aimed", fuzz_fail)):
        if rows:
            print("\n%s (%d):" % (label, len(rows)))
            for r in rows[:8]:
                print("   %s" % (r,))

    ok = (not read_fail and not identity_fail and not rewrite_fail
          and not counter_fail and not ars_unknown and not fuzz_fail
          and not fuzz_skipped and sum(seen.values()) == 939
          and parents_total and parents_ok == parents_total)
    print("\n%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
