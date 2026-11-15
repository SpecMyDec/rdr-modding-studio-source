"""[M7] Character stats: nine formats, each edited, written, and read back.

The tester asked for stat editing on `.swt .msd .dam .hot .rep .skel .sphs .tail
.acc`. `.cr` was already done. This is the acceptance check for the other
nine, and it asserts the real effect rather than the absence of an exception.

For each format, on a COPY of one real archive:

  1. read the file out of the archive;
  2. choose a field through that format's OWN editor -- not a byte poke, so a
     format whose editor cannot reach a field fails here rather than passing
     on a write nobody could have made through the app;
  3. change it to a different value of the same width;
  4. write it back through `dave.patch`, the same call every shipped edit
     makes;
  5. close the archive and RE-OPEN it from the bytes on disk;
  6. assert three things:
       * the archive's stored bytes for that entry actually changed;
       * re-parsing the file returns the NEW value;
       * every byte of the file except the edited span is identical.

The third is the one that matters and the one a keyword grep cannot fake: an
editor that rewrites indentation, reflows a column or normalises a float is
byte-lossy, and on files whose slots hold 15 bytes of headroom that is the
difference between an edit that fits and an archive that will not take it.

SAFETY. The pristine disc is opened read-only and one archive is extracted to
a temp directory, which is the only thing written. No ISO is modified, so this
needs no suite lock and leaves the disc exactly as it found it.

    python3 03_Tools/scripts/verify_charstats.py
"""

import os
import shutil
import sys
import tempfile

# `scripts/` holds a `dis.py`, and Python puts a script's own directory first
# on sys.path -- which shadows the stdlib `dis` that `inspect` (and so
# `argparse`) imports on 3.13+. Drop this directory and add `03_Tools`.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import charfile as CF
from rdrstudio import config, msd as MSD, skeledit as SKE, swt as SWT
from rdrstudio.dave import Dave, FileBacking
from rdrstudio.game import Game

# The smallest archive carrying all nine formats. 57 archives do; this is the
# 1.6 MB one, so the copy costs nothing.
ARCHIVE = "LVLS_M2/INT2.DAT"

# charfile kinds whose editable cells are numbers.
NUMERIC_KINDS = ("dam", "hot", "sphs", "tail", "acc")


def _isnum(v):
    try:
        float(str(v))
        return True
    except (TypeError, ValueError):
        return False


def _bump(text):
    """A different number of exactly the same width.

    Same width keeps the file length identical, which is what lets the test
    assert "every other byte is untouched" without an offset shift explaining
    away a real corruption.
    """
    t = str(text).strip()
    return t[:-1] + ("8" if t[-1] != "8" else "7")


def _first_numeric_cell(doc):
    """(path, value) of the first numeric cell anywhere in a charfile doc.

    The docs are grids, tables or forms depending on the kind, so this walks
    rather than assuming a shape. Text cells are skipped on purpose: `.hot`
    and `.sphs` open with a bone NAME, and renaming a bone to prove a byte
    round-trip would be a valid test of the wrong thing.
    """
    stack = [doc]
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            value = node.get("value")
            if ("path" in node and _isnum(value)
                    and str(value).strip()[-1:].isdigit()):
                return node["path"], str(value).strip()
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None, None


# --------------------------------------------------------------- per format
#
# Each returns (description, new_bytes, verify) where `verify(data)` re-reads
# the written file and returns what the edited field now says.

def case_swt(data):
    groups = SWT.read(data)
    row = next(r for g in groups for r in g["fields"]
               if r["kind"] in ("num", "int"))
    new = _bump(row["value"])
    out = SWT.set_fields(data, {row["field"]: new})
    return ("%s = %s -> %s" % (row["field"], row["value"], new), out, new,
            lambda d: SWT.summary(d).get(row["field"]), len(row["value"]))


def case_msd(data):
    speed = next(g for g in MSD.read(data) if g["key"] == "speed")
    cell = speed["bands"][0]["fields"][1]
    new = _bump(cell["value"])
    out = MSD.set_fields(data, {cell["path"]: new})
    short = cell["path"].split("/", 2)[-1]
    return ("%s = %s -> %s" % (short, cell["value"], new), out, new,
            lambda d: MSD.summary(d).get(cell["path"]), len(cell["value"]))


def case_charfile(ext):
    def run(data):
        doc = CF.read(ext, data)
        path, value = _first_numeric_cell(doc)
        if path is None:
            raise AssertionError("no numeric cell in this .%s" % ext)
        new = _bump(value)
        out = CF.setter_for(ext)(data, {path: new})

        def verify(d):
            p, _v = path, None
            found = [None]
            stack = [CF.read(ext, d)]
            while stack:
                node = stack.pop(0)
                if isinstance(node, dict):
                    if node.get("path") == p:
                        found[0] = str(node.get("value")).strip()
                        break
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
            return found[0]

        return ("%s = %s -> %s" % (path, value, new), out, new, verify,
                len(value))
    return run


def case_rep(data):
    """`.rep` maps a reaction slot to an animation clip, so its edit is a NAME.

    The replacement is another clip already named in the same file and of the
    same length -- so the write stays same-width AND stays a clip the game
    actually has, which a mangled string would not.
    """
    doc = CF.read("rep", data)
    rows = [r for r in doc["rows"] if r.get("clip")]
    first = rows[0]
    new = next(r["clip"] for r in rows[1:]
               if len(r["clip"]) == len(first["clip"])
               and r["clip"] != first["clip"])
    out = CF.setter_for("rep")(data, {first["path"]: new})

    def verify(d):
        for r in CF.read("rep", d)["rows"]:
            if r["path"] == first["path"]:
                return r.get("clip")
        return None

    return ("%s: %s -> %s" % (first["path"], first["clip"], new), out, new,
            verify, len(first["clip"]))


def case_skel(data):
    """Through `skeledit`, which is the surface a route would use.

    Deliberately NOT `skeleton.set_offset`: that writes x, y and z together,
    and a test should exercise the call the app will actually make. One
    component, one field.
    """
    doc = SKE.doc(data)
    row = doc["rows"][1] if len(doc["rows"]) > 1 else doc["rows"][0]
    cell = row["cells"][0]
    new_value = "1.25"
    out = SKE.set_fields(data, {cell["path"]: new_value})

    def verify(d):
        for r in SKE.doc(d)["rows"]:
            if r["label"] == row["label"]:
                return r["cells"][0]["value"]
        return None

    return ("%s = %s -> %s" % (cell["path"], cell["value"], new_value), out,
            new_value, verify, cell["width"])


CASES = [
    ("swt", case_swt),
    ("msd", case_msd),
    ("dam", case_charfile("dam")),
    ("hot", case_charfile("hot")),
    ("rep", case_rep),
    ("skel", case_skel),
    ("sphs", case_charfile("sphs")),
    ("tail", case_charfile("tail")),
    ("acc", case_charfile("acc")),
]


def diff_spans(a, b):
    """The byte ranges where two equal-length blobs differ."""
    spans = []
    i = 0
    while i < len(a):
        if a[i] != b[i]:
            j = i
            while j < len(a) and a[j] != b[j]:
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def run_case(path, ext, build):
    """Edit, write, re-open from disk, and check all three assertions."""
    dave = Dave(FileBacking(path, writable=True), label=ARCHIVE)
    entry = next((e for e in dave if e.ext == ext), None)
    if entry is None:
        dave.backing.close()
        return False, ext, "no .%s in %s" % (ext, ARCHIVE), ""
    name = entry.name
    before = dave.read(entry)
    raw_before = dave.raw(entry)
    try:
        what, after, expected, verify, want_extent = build(before)
    except Exception as ex:                                    # noqa: BLE001
        dave.backing.close()
        return False, name, "editor refused: %s: %s" % (type(ex).__name__, ex), ""

    if len(after) != len(before):
        dave.backing.close()
        return (False, name, "edit changed the file length: %d -> %d"
                % (len(before), len(after)), what)

    dave.patch(name, after)
    dave.backing.close()

    # Re-open from the bytes on disk. Nothing carries over in memory.
    again = Dave(FileBacking(path, writable=False), label=ARCHIVE)
    entry2 = again.find(name)
    raw_after = again.raw(entry2)
    read_back = again.read(entry2)
    again.backing.close()

    problems = []
    if raw_after == raw_before:
        problems.append("the archive's stored bytes did not change")
    if read_back != after:
        problems.append("the file did not read back as written")

    got = verify(read_back)
    if isinstance(expected, tuple):
        ok_value = got is not None and all(
            abs(g - e) < 1e-3 for g, e in zip(got, expected))
    else:
        ok_value = str(got) == str(expected)
    if not ok_value:
        problems.append("re-read gives %r, expected %r" % (got, expected))

    spans = diff_spans(before, read_back)
    edited = sum(e - s for s, e in spans)
    if not spans:
        problems.append("nothing changed in the file at all")
    else:
        extent = spans[-1][1] - spans[0][0]
        if extent > want_extent:
            problems.append(
                "changed bytes span %d positions but the edited field is %d "
                "wide -- something outside it was rewritten (first change at "
                "%d, last at %d)"
                % (extent, want_extent, spans[0][0], spans[-1][1]))

    note = "%s  [%d byte(s) in %d span(s)]" % (what, edited, len(spans))
    return (not problems), name, "; ".join(problems), note


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else config.VANILLA_ISO
    game = Game(iso, writable=False)
    print("disc: %s  (read-only)" % iso)
    print("archive: %s, extracted to a temp copy -- no ISO is written\n"
          % ARCHIVE)

    tmp = tempfile.mkdtemp(prefix="rdr-charstats-")
    results = []
    try:
        blob = game.iso.read_file(ARCHIVE)
        for ext, build in CASES:
            path = os.path.join(tmp, "%s-%s" % (ext, os.path.basename(ARCHIVE)))
            with open(path, "wb") as fh:
                fh.write(blob)
            ok, name, problem, note = run_case(path, ext, build)
            results.append((ok, ext, name, problem, note))
            print("  [%s] .%-5s %-22s %s"
                  % ("PASS" if ok else "FAIL", ext, name, note))
            if problem:
                print("         %s" % problem)
            os.remove(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in results if not r[0]]
    print("\nSUMMARY")
    for ok, ext, name, _p, _n in results:
        print("  [%s] .%s edits, writes and reads back byte-exactly"
              % ("PASS" if ok else "FAIL", ext))
    print("%d of %d formats passed." % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
