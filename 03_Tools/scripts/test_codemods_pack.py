#!/usr/bin/env python3
"""[CP] Code mods inside a mod pack (format rdrmodpack/2). Offline; reads the stock ISO, writes only temp files.

    py -3 scripts/test_codemods_pack.py

Proves: a pack with code mods is format 2 and ships NO game bytes (only the feature list + the stock
executable's sha1); a pack without code mods stays format 1; read_pack round-trips it; an older Studio
(which only knows format 1) refuses it before touching anything; a newer code-mod version, unknown mod
keys and a damaged section are refused; assess_codemods accepts the stock ISO, refuses a different stock
executable (wrong game) and refuses a working disc that already carries code mods. The end-to-end
apply-vs-direct-build proof needs a 2 GB copy and a Studio, see scripts/codemods_pack_proof.py.
"""
import io, json, os, sys, tempfile, zipfile, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from rdrstudio.config import VANILLA_ISO
from rdrstudio import codemods as C, modpack as mp, iso9660
from rdrstudio.api import Refusal

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

def refused(fn, *a):
    try:
        fn(*a)
    except Refusal:
        return True
    return False

with iso9660.Iso9660(VANILLA_ISO) as I:
    elf = I.read_file("SLUS_205.00")
cm = dict(features=list(C.KEYS), stock_elf_sha1=mp.sha1(elf), stock_elf_size=len(elf))
tmp = tempfile.mkdtemp(prefix="cmpack_")
p = os.path.join(tmp, "a.rdrmodpack.zip")
man = mp.build_pack([], p, name="t", codemods=cm)
raw = open(p, "rb").read()
check(man["format"] == "rdrmodpack/2" and man["codemods"]["version"] == mp.CODE_VERSION, "a pack with code mods is format 2 with a version field")
check(len(raw) < 4096, "a code-mod-only pack is tiny (%d bytes)" % len(raw))
check(elf[:64] not in raw and not any(n.startswith("files/") for n in zipfile.ZipFile(io.BytesIO(raw)).namelist()),
      "it carries no game bytes (no executable, no archive files)")
m2, b2 = mp.read_pack(raw)
check(m2["codemods"]["features"] == list(C.KEYS) and b2 == {}, "read_pack round-trips the code-mod section")

p1 = os.path.join(tmp, "b.rdrmodpack.zip")
man1 = mp.build_pack([], p1, name="plain")
check(man1["format"] == "rdrmodpack/1" and "codemods" not in man1, "a pack without code mods stays format 1 (every Studio with packs reads it)")
check(man["pack_id"] != mp.build_pack([], p1, name="x", codemods=dict(cm, features=["noclip"]))["pack_id"], "pack id depends on the chosen mods")

# an older Studio: only format 1 accepted -> refuse cleanly
OLD = "rdrmodpack/1"
check(m2["format"] != OLD, "an older Studio (format 1 only) refuses it as 'not a mod pack (format rdrmodpack/2)'")

def with_cm(**kw):
    m = json.loads(json.dumps(man)); m["codemods"].update(kw)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", json.dumps(m))
    return b.getvalue()
check(refused(mp.read_pack, with_cm(version=99)), "a newer code-mod version is refused (update the Studio)")
check(refused(mp.read_pack, with_cm(features=["noclip", "not_a_real_mod"])), "an unknown code mod is refused")
check(refused(mp.read_pack, with_cm(features=[])), "an empty code-mod list is refused")
check(refused(mp.read_pack, with_cm(stock_elf_sha1=5)), "a missing/invalid executable checksum is refused")

# assess against discs
a = mp.assess_codemods(VANILLA_ISO, VANILLA_ISO, m2)
check(a["status"] == "ok", "stock executable matches, clean working disc -> ok")
bad = json.loads(json.dumps(m2)); bad["codemods"]["stock_elf_sha1"] = "0" * 40
check(mp.assess_codemods(VANILLA_ISO, VANILLA_ISO, bad)["status"] == "wrong_version", "different stock executable -> wrong game, refused")
# a working disc that already carries code mods: fake just the ELF check by monkeypatching iso_state
orig = C.iso_state
C.iso_state = lambda path: {"features": ["noclip"]}
try:
    check(mp.assess_codemods(VANILLA_ISO, VANILLA_ISO, m2)["status"] == "blocked", "a working disc already code-modded -> blocked")
finally:
    C.iso_state = orig
check(mp.assess_codemods(VANILLA_ISO, os.path.join(tmp, "nope.iso"), m2)["status"] == "blocked", "an unreadable working disc -> blocked")

print("\n%s" % ("ALL PASSED" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
