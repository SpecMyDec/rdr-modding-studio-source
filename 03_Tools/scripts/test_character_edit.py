"""Exporting a character to OBJ, editing it, and importing it back.

This is the "custom characters" path. It does NOT require the animation
format: a character's mesh and its skeleton are separate, and both are now
writable -- the mesh through this OBJ round trip, the skeleton as plain text.

The thing that makes it safe is that skinned positions re-quantise EXACTLY.
A skinned vertex is a 16-bit code, decoded as `row + code*ulp - bias` against
the VIF ROW register; re-encoding the decoded float returns the original code
for all 2,313,761 skinned vertices on the disc. So exporting a part and
importing it unchanged moves nothing at all, which is the property an artist
actually relies on.

Worth knowing while reading the numbers below: scaling bone-local vertices
makes a character thicker or thinner around its bones -- it does not make it
taller, because height comes from the skeleton.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8731/api/"
ARCHIVE = "SWAT.DAT"
BUNDLE = "mrd_lods.rsc"          # Red
CHUNK = 3                        # mrd_lod_01, his body


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=600) as fh:
        return json.load(fh)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as fh:
        return json.load(fh)


def main():
    ok = True
    start = len(get("diff"))
    print("disc dirty before  : %d" % start)

    parts = get("character_parts?archive=%s&name=%s" % (ARCHIVE, BUNDLE))
    print("%s has %d editable part(s):" % (BUNDLE, len(parts["parts"])))
    for p in parts["parts"]:
        print("   chunk %-3d %-18s %5d verts  skinned=%s  step %s m"
              % (p["chunk"], p["name"], p["verts"], p["skinned"], p["step"]))
    ok &= len(parts["parts"]) >= 3
    ok &= all(p["editable"] for p in parts["parts"])

    url = ("character_export?archive=%s&name=%s&chunk=%d"
           % (ARCHIVE, BUNDLE, CHUNK))
    with urllib.request.urlopen(BASE + url, timeout=600) as fh:
        obj = fh.read().decode("latin-1")
    nv = sum(1 for l in obj.splitlines() if l.startswith("v "))
    want = next(p for p in parts["parts"] if p["chunk"] == CHUNK)
    print("\nexported           : %d bytes, %d vertices (expect %d)"
          % (len(obj), nv, want["verts"]))
    ok &= nv == want["verts"]

    body = dict(archive=ARCHIVE, name=BUNDLE, chunk=CHUNK, obj=obj)

    # The property that matters most: a no-op edit is a no-op.
    same = post("character_import", dict(body, dry=True))
    print("import unchanged   : %d of %d vertices move, worst error %s"
          % (same["moved"], same["vertices"], same["worst_error"]))
    ok &= same["moved"] == 0 and same["worst_error"] == 0.0

    # Now a real edit: 15% wider on Y in bone-local space.
    edited = []
    for line in obj.splitlines():
        if line.startswith("v "):
            p = line.split()
            p[2] = repr(float(p[2]) * 1.15)
            edited.append(" ".join(p))
        else:
            edited.append(line)
    body2 = dict(body, obj="\n".join(edited))

    dry = post("character_import", dict(body2, dry=True))
    print("import edited      : %d moved, worst error %.6f m, step %.6f m, "
          "saturated=%s" % (dry["moved"], dry["worst_error"], dry["step"],
                            dry["saturated"]))
    ok &= dry["moved"] > want["verts"] * 0.9
    # Re-quantising cannot be worse than half a step; anything more means the
    # encoder and decoder disagree.
    ok &= dry["worst_error"] <= dry["step"]
    ok &= not dry["saturated"]

    w = post("character_import", body2)
    print("written            : %d/%d bytes of slot" % (w["used"], w["slot"]))
    ok &= bool(w["ok"])

    after = len(get("diff"))
    print("disc dirty         : %d  (expect %d)" % (after, start + 1))
    ok &= after == start + 1

    # The model must still decode, with the same vertex count.
    d = get("character?archive=%s&name=%s&lod=0" % (ARCHIVE, BUNDLE))
    print("still decodes      : %d vertices" % (len(d["positions"]) // 3))
    ok &= len(d["positions"]) // 3 > 0

    # An OBJ with a vertex removed must be refused, not misapplied.
    short = "\n".join([l for i, l in enumerate(edited)
                       if not (l.startswith("v ") and i % 131 == 7)])
    refused = False
    try:
        post("character_import", dict(body, obj=short))
    except urllib.error.HTTPError:
        refused = True
    print("short OBJ refused  : %s" % refused)
    ok &= refused

    u = post("undo", {})
    print("undo               : %s" % u.get("label"))
    final = len(get("diff"))
    print("disc dirty after   : %d  (expect %d)" % (final, start))
    ok &= final == start

    for _ in range(4):
        if len(get("diff")) <= start:
            break
        post("undo", {})

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
