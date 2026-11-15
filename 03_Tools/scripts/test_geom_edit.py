"""Reshaping the world, live, through the Studio -- and putting it back.

The offline check (`verify_geomedit.py`) proves the byte maths. This proves
the whole path: read a real building out of a real level, move it, scale it,
spin it, confirm the level bundle still decodes to the same vertex and
triangle counts, then undo and confirm the disc is byte-clean again.

The interesting property is that vertex and triangle counts must NOT change.
A transform that altered them would mean the packet had been re-serialised
rather than patched -- which is the failure that overflows an archive slot.
"""
import json
import sys
import urllib.parse
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
ARCHIVE = "LVLS_SNG/TOMB.DAT"
AREA = "lvl_tomb.rsc"
CHUNK = 104          # a Brimstone storefront: 564 verts across 8 textures


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=300) as fh:
        return json.load(fh)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as fh:
        return json.load(fh)


def model():
    return get("model?archive=%s&area=%s&chunk=%d"
               % (urllib.parse.quote(ARCHIVE), AREA, CHUNK))


def box(m):
    p = m["positions"]
    return [(round(min(p[i::3]), 3), round(max(p[i::3]), 3)) for i in range(3)]


def edit(label, ops):
    return post("geom_edit", dict(archive=ARCHIVE, area=AREA, chunk=CHUNK,
                                  label=label, ops=ops))


def main():
    ok = True
    start_dirty = len(get("diff"))
    m0 = model()
    b0 = box(m0)
    print("%s / %s chunk %d" % (ARCHIVE, AREA, CHUNK))
    print("  disc dirty before : %d" % start_dirty)
    print("  vanilla           : %d verts, %d tris, %d parts"
          % (m0["verts"], m0["tris"], m0["parts"]))
    print("  bounds            : %s" % (b0,))

    cases = [
        ("move up 5m", [{"op": "translate", "y": 5.0}],
         lambda b: abs((b[1][0] - b0[1][0]) - 5.0) < 1e-3),
        ("double size", [{"op": "scale", "x": 2.0}],
         lambda b: abs((b[0][1] - b[0][0]) - 2 * (b0[0][1] - b0[0][0])) < 1e-2),
        ("spin 90", [{"op": "rotate_y", "deg": 90}],
         lambda b: abs((b[0][1] - b[0][0]) - (b0[2][1] - b0[2][0])) < 1e-2),
        ("move+scale+spin", [{"op": "scale", "x": 1.5},
                             {"op": "rotate_y", "deg": 45},
                             {"op": "translate", "x": 3.0, "z": -2.0}],
         lambda b: b != b0),
    ]

    for label, ops, check in cases:
        r = edit(label, ops)
        m = model()
        b = box(m)
        same_counts = (m["verts"] == m0["verts"] and m["tris"] == m0["tris"]
                       and m["parts"] == m0["parts"])
        good = bool(r.get("ok")) and same_counts and check(b)
        ok &= good
        print("\n  %-16s %s" % (label, "OK" if good else "FAILED"))
        print("    bounds          : %s" % (b,))
        print("    counts unchanged: %s" % same_counts)
        # Each case transforms the VANILLA geometry, so the previous edit is
        # replaced rather than compounded -- undo once per case.
        post("undo", {})

    # The Blender bridge: export, edit as an external tool would, import.
    print("\n  --- OBJ round trip ---")
    url = ("geom_export?archive=%s&area=%s&chunk=%d&name=test"
           % (urllib.parse.quote(ARCHIVE), AREA, CHUNK))
    with urllib.request.urlopen(BASE + url, timeout=300) as fh:
        obj = fh.read().decode("latin-1")
    nv = sum(1 for l in obj.splitlines() if l.startswith("v "))
    print("  exported          : %d bytes, %d vertices" % (len(obj), nv))
    ok &= nv == m0["verts"]

    lifted = []
    for line in obj.splitlines():
        if line.startswith("v "):
            p = line.split()
            p[2] = repr(float(p[2]) + 2.0)
            lifted.append(" ".join(p))
        else:
            lifted.append(line)
    body = dict(archive=ARCHIVE, area=AREA, chunk=CHUNK,
                label="obj test", obj="\n".join(lifted))
    dry = post("geom_import", dict(body, dry=True))
    print("  dry run           : %d of %d vertices move"
          % (dry["moved"], dry["vertices"]))
    ok &= dry["moved"] == m0["verts"]

    post("geom_import", body)
    m = model()
    b = box(m)
    print("  imported          : y %.3f..%.3f (was %.3f..%.3f)"
          % (b[1][0], b[1][1], b0[1][0], b0[1][1]))
    ok &= abs((b[1][0] - b0[1][0]) - 2.0) < 1e-3
    ok &= m["verts"] == m0["verts"] and m["tris"] == m0["tris"]
    post("undo", {})

    # An OBJ that lost a vertex must be refused, not silently misapplied.
    short = "\n".join(l for l in lifted[:-1] if True)
    short = "\n".join([l for i, l in enumerate(lifted)
                       if not (l.startswith("v ") and i % 97 == 3)])
    refused = False
    try:
        post("geom_import", dict(body, obj=short))
    except urllib.error.HTTPError:
        refused = True
    print("  short OBJ refused : %s" % refused)
    ok &= refused

    back = model()
    print("\n  after undo        : %s" % (box(back),))
    restored = box(back) == b0
    print("  restored          : %s" % ("OK" if restored else "MISMATCH"))
    ok &= restored

    final = len(get("diff"))
    print("  disc dirty after  : %d  (expect %d)" % (final, start_dirty))
    ok &= final == start_dirty

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
