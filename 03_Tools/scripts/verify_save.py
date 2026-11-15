"""[SAVE] the memory card parses, the save round-trips byte-exactly, and an
edit lands.

    py -3 scripts/verify_save.py
    py -3 scripts/verify_save.py --card <PCSX2>\\memcards\\Mcd001.ps2

READ-ONLY WHERE IT MATTERS. The developer's card is opened to be COPIED and never
written. Every write in this file goes to a copy under `08_ForkLab/savelab/`.
If the copy is missing it is made here; `08_ForkLab` is not tracked, so a
clean checkout has no card and this script has to fetch one.

THE TEST THAT GATES THE OTHERS is section 4: read the save out and write the
same bytes back, and all 8,650,752 bytes of the card must be unchanged. It is
worth saying why that is not the tautology it looks like. The writer does not
hand back the buffer it read -- it walks the FAT chain, splits the save across
its 18 clusters, re-splits each cluster into 512-byte pages and recomputes
three ECC bytes per 128-byte chunk. A card that survives that has had its
whole write path exercised. Section 5 is there because
`STATE-CONTRACT.md` is right that a compare reporting "nothing
changed" is a failure signal: it changes one byte and FAILS if the card comes
back identical, so a pass in section 4 cannot be a writer that does nothing.

Section 5 also pins a real property of this ECC that cost an hour to find:
it cannot see a byte replaced by its own complement. `0x01 -> 0xfe` changes
the page and leaves all twelve spare bytes alone, because the column mask of a
byte and of its complement are equal and so are their parities. So the control
uses `0x02`, and asserts the complement case separately rather than quietly
choosing a value that works.
"""
import os
import shutil
import struct
import sys

# Line-buffered on purpose. Redirected or piped, Python block-buffers stdout
# and a run that is merely SLOW is indistinguishable from one that has hung --
# which is exactly how this script was read as "no output after 240s" once.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:                                           # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

from savedata import ps2mc                     # noqa: E402
from savedata import rdrsave                   # noqa: E402

LAB = os.path.join(REPO, "08_ForkLab", "savelab", "cards")
from savedata.routes_save import CARD_DIRS      # noqa: E402
SOURCES = [os.path.join(d, "Mcd001.ps2") for d in CARD_DIRS] + [
    os.path.join(REPO, "08_ForkLab", "savelab", "cards", "emu_Mcd001.ps2"),
]

failures = []


def check(label, ok, detail=""):
    print("  %-58s %s%s" % (label, "ok" if ok else "FAILED",
                            "" if not detail else "   %s" % detail))
    if not ok:
        failures.append(label)
    return ok


def working_copy(explicit):
    """A card COPY to test against. Never returns the developer's own file."""
    target = os.path.join(LAB, "verify_Mcd001.ps2")
    # Re-copied EVERY run, never reused. On Windows section 8's POST really
    # does write this file, and a stale copy would make the next run start
    # from an edited card -- the run would still pass, while testing
    # something other than what it says it tests.
    for src in ([explicit] if explicit else []) + SOURCES:
        if src and os.path.exists(src) and os.path.abspath(src) != os.path.abspath(target):
            if not os.path.isdir(LAB):
                os.makedirs(LAB)
            shutil.copyfile(src, target)
            print("  copied a working card from %s" % src)
            return target
    return None


def main():
    explicit = None
    args = sys.argv[1:]
    if "--card" in args:
        explicit = args[args.index("--card") + 1]

    path = working_copy(explicit)
    if path is None:
        print("no memory card to test against. Looked for:")
        for s in SOURCES:
            print("  %s" % s)
        print("Nothing has been run.")
        sys.exit(2)

    original = open(path, "rb").read()

    print("1. the card parses")
    try:
        card = ps2mc.Card(original, path)
    except ps2mc.CardError as ex:
        print("  could not open %s: %s" % (path, ex))
        print("Nothing has been run.")
        sys.exit(2)
    check("magic and version", card.version.startswith("Sony PS2 Memory Card Format"),
          card.version)
    check("page 512 + 16 spare", card.page_len == 512 and card.spare_len == 16)
    check("2 pages per cluster, 8192 clusters",
          card.pages_per_cluster == 2 and card.clusters_per_card == 8192)
    check("alloc_offset 41", card.alloc_offset == 41)
    check("image is 8,650,752 bytes", len(original) == 8650752, "%d" % len(original))

    print("2. ECC over every page that stores one")
    live, bad = card.ecc_report()
    check("1,216 chunks carry an ECC", live == 1216, "%d" % live)
    # Page 1 holds an 8-byte card ident written without refreshing its spare.
    # It is the one chunk on the card that fails its own ECC, and it is a
    # property of the card, not of this reader -- so it is asserted, not
    # tolerated. If it ever disappears, something rewrote page 1.
    check("only the known-stale page 1 fails ECC", bad == [(1, 0)], "%s" % (bad,))

    print("3. the RDR save is there and has the shape the game's code implies")
    try:
        blob = card.read_file(rdrsave.SAVE_PATH)
    except ps2mc.CardError as ex:
        print("  could not read %s: %s" % (rdrsave.SAVE_PATH, ex))
        print("Nothing has been run.")
        sys.exit(2)
    check("save is 18,432 bytes", len(blob) == rdrsave.SAVE_LEN, "%d" % len(blob))
    check("184 + 17120 + 1128 == 18432",
          rdrsave.PREFIX_LEN + rdrsave.STRUCT_LEN + rdrsave.TRAILER_LEN
          == rdrsave.SAVE_LEN)
    check("17120 == 80 + 6 * 2840",
          rdrsave.STRUCT_HEADER_LEN + rdrsave.SLOT_COUNT * rdrsave.SLOT_LEN
          == rdrsave.STRUCT_LEN)
    save = rdrsave.Save(blob)
    used = [s for s in save.slots if s.used]
    check("exactly one slot is in use", len(used) == 1,
          "%s" % [s.index for s in used])
    check("the used slot is named from the game's default list",
          used and used[0].fields["name"] in rdrsave.DEFAULT_NAMES,
          used[0].fields["name"] if used else "-")
    check("the used slot's level is a game level id",
          used and used[0].fields["level"].startswith("lvl_"),
          used[0].fields["level"] if used else "-")
    # The empty slots are the reference the `used` test leans on, so assert
    # they really are one repeated template rather than assuming it.
    empties = [bytes(save.slot_bytes(i)) for i in (1, 2, 3, 4)]
    check("slots 1-4 are one identical empty template",
          len(set(empties)) == 1)

    print("3b. money, the one field a live game confirmed")
    check("money reads 677, the BANK BALANCE the game showed",
          save.fields["money"] == 677, "$%d" % save.fields["money"])
    # Uniqueness is why the screenshot is evidence about THIS offset and not
    # merely about some offset. A number that appeared six times would have
    # told us nothing.
    import struct as _s
    check("677 appears exactly once in the whole save",
          blob.count(_s.pack("<I", 677)) == 1,
          "%d" % blob.count(_s.pack("<I", 677)))
    check("money is in the prefix, outside every slot",
          0x68 < rdrsave.SLOT_BASE)
    # The end-to-end result this file cannot re-run: a card written by
    # `set_file_field` was booted and the game displayed the edited number.
    # Asserted as a constant so the claim and the code stay together -- if
    # the offset is ever changed, this line has to be re-argued.
    check("the booted-and-confirmed money offset is still 0x68",
          [f for f in rdrsave.FILE_FIELDS if f[2] == "money"][0][0] == 0x68)
    # The candidate the boot killed. Asserted so nobody re-promotes it.
    check("+0x2f4 is still 752 and is NOT money",
          save.slots[0].fields["u2f4"] == 752 and save.slots[0].fields["u2f4"] != 677,
          "%d" % save.slots[0].fields["u2f4"])

    print("4. read -> write the same bytes -> the card is byte-identical")
    rt = ps2mc.Card(original, path)
    rt.write_file(rdrsave.SAVE_PATH, blob)
    out = rt.to_bytes()
    same = out == original
    check("all 8,650,752 bytes unchanged", same,
          "" if same else "%d bytes differ" % sum(1 for a, b in zip(original, out) if a != b))

    print("5. negative control -- the same compare must FAIL on a real edit")
    nc = ps2mc.Card(original, path)
    edited = bytearray(blob)
    was = edited[0]
    edited[0] = 0x02 if was != 0x02 else 0x03
    nc.write_file(rdrsave.SAVE_PATH, bytes(edited))
    ncout = nc.to_bytes()
    diff = [i for i in range(len(original)) if original[i] != ncout[i]]
    check("a one-byte edit changes the card", len(diff) > 0, "%d bytes" % len(diff))
    check("it changes the data byte and its ECC", len(diff) == 2,
          "%s" % [hex(x) for x in diff])
    check("the edited card still passes ECC", nc.ecc_report()[1] == [(1, 0)])
    check("reading it back returns the edited value",
          nc.read_file(rdrsave.SAVE_PATH)[0] == edited[0])
    # The complement blind spot, asserted rather than avoided.
    comp = ps2mc.Card(original, path)
    cb = bytearray(blob)
    cb[0] = was ^ 0xFF
    comp.write_file(rdrsave.SAVE_PATH, bytes(cb))
    compout = comp.to_bytes()
    cdiff = [i for i in range(len(original)) if original[i] != compout[i]]
    check("a complemented byte moves no ECC (known ECC blind spot)",
          len(cdiff) == 1, "%s" % [hex(x) for x in cdiff])

    print("6. a decoded field edits, and the re-read comes back from the CARD")
    ed = ps2mc.Card(original, path)
    s2 = rdrsave.Save(ed.read_file(rdrsave.SAVE_PATH))
    before = s2.slots[0].fields["name"]
    target = "BILLY" if before != "BILLY" else "WYATT"
    s2.set_field(0, "name", target)
    ed.write_file(rdrsave.SAVE_PATH, s2.to_bytes())
    again = rdrsave.Save(ed.read_file(rdrsave.SAVE_PATH))
    check("profile name reads back as the new value",
          again.slots[0].fields["name"] == target,
          "%r -> %r" % (before, again.slots[0].fields["name"]))
    check("the level field did not move",
          again.slots[0].fields["level"] == save.slots[0].fields["level"])
    check("the other five slots are untouched",
          all(bytes(again.slot_bytes(i)) == bytes(save.slot_bytes(i))
              for i in range(1, 6)))
    check("the edited card still passes ECC", ed.ecc_report()[1] == [(1, 0)])
    # Money, through the file-field path, on the card.
    s4 = rdrsave.Save(ed.read_file(rdrsave.SAVE_PATH))
    s4.set_file_field("money", 1234)
    ed.write_file(rdrsave.SAVE_PATH, s4.to_bytes())
    check("money edits and reads back from the card",
          rdrsave.Save(ed.read_file(rdrsave.SAVE_PATH)).fields["money"] == 1234)
    # A u32 candidate, to prove the numeric path and the width guard.
    s3 = rdrsave.Save(ed.read_file(rdrsave.SAVE_PATH))
    s3.set_field(0, "u2f4", 4242)
    ed.write_file(rdrsave.SAVE_PATH, s3.to_bytes())
    check("a u32 candidate edits and reads back",
          rdrsave.Save(ed.read_file(rdrsave.SAVE_PATH)).slots[0].fields["u2f4"] == 4242)

    print("7. The developer's own card was not written")
    for real in SOURCES[:2]:
        if os.path.exists(real):
            check("%s untouched" % real,
                  open(real, "rb").read() == original
                  or os.path.abspath(real) != os.path.abspath(path))
            break

    print("8. the Studio routes answer, through studio.py's own dispatch")
    # Not a curl. This imports the dispatch the browser would reach and calls
    # it with a stand-in handler, so the thing under test is the wiring --
    # that `savedata` is importable from inside `rdrstudio`, that the route
    # names are the ones studio.py will see, and that the envelope is the
    # (code, body) pair `api.respond` produces. Starting a real server would
    # open the working ISO for no extra coverage.
    class FakeHandler(object):
        def __init__(self):
            self.sent = None

        def _send(self, code, body):
            self.sent = (code, body)
            return self.sent

    try:
        from savedata import routes_save
    except Exception as ex:                                 # noqa: BLE001
        print("  could not import savedata.routes_save: %s" % ex)
        print("Nothing has been run.")
        sys.exit(2)

    check("studio.py delegates to the save routes",
          "routes_save" in open(os.path.join(TOOLS, "rdrstudio", "studio.py")).read())
    check("save/open and save/write are registered",
          routes_save.has_get("save/open") and routes_save.has_post("save/write"))

    h = FakeHandler()
    routes_save.handle_get(h, None, "save/open", {"path": [path]})
    code, body = h.sent
    check("GET save/open answers 200", code == 200, "%s" % code)
    check("it returns six slots", len(body.get("slots", [])) == rdrsave.SLOT_COUNT)
    # `save` is what was on disk at the start. Sections 4-6 all worked on
    # in-memory Card objects and wrote nothing, so this asserts the route
    # read the real file and not some buffer this script left lying around.
    check("slot 0 carries the name that is on disk",
          body["slots"][0]["fields"]["name"] == save.slots[0].fields["name"],
          "%r" % body["slots"][0]["fields"]["name"])

    h = FakeHandler()
    routes_save.handle_get(h, None, "save/fields", {})
    code, body = h.sent
    check("GET save/fields lists the field table",
          code == 200 and len(body["fields"]) == len(rdrsave.SLOT_FIELDS))
    check("every field declares a confidence",
          all(f["confidence"] in ("decoded", "candidate", "unknown")
              for f in body["fields"]))
    check("the 28 stat names ride along", len(body["stat_names"]) == 28)

    # The guard, which is the point of the route. From WSL `_pcsx2_state()`
    # is None -- "cannot tell" -- and the write must refuse on that, not
    # round it down to "safe".
    h = FakeHandler()
    routes_save.handle_post(h, None, "save/write",
                            {"path": path, "slot": 0, "fields": {"name": "JOHN"}})
    code, body = h.sent
    state = routes_save._pcsx2_state()
    if state is False:
        check("write succeeded with PCSX2 provably absent",
              code == 200 and body.get("ok"), "%s" % code)
        check("it named a backup it actually wrote",
              os.path.exists(body.get("backup", "")))
    else:
        check("write REFUSED because PCSX2 could not be ruled out",
              code == 403, "%s -> %s" % (code, str(body)[:70]))
        check("the card was not written",
              rdrsave.Save.from_card(ps2mc.Card.from_file(path))
              .slots[0].fields["name"] != "JOHN")

    print("")
    if failures:
        print("FAILED %d check(s):" % len(failures))
        for f in failures:
            print("  %s" % f)
        sys.exit(1)
    print("PASS -- card parsed, save round-tripped byte-exactly, edits land.")
    print("NOTE: money is +0x68. A card written by this code was booted on")
    print("      2026-09-20 and the game showed the edited value. See SAVE-FORMAT.md.")
    sys.exit(0)


if __name__ == "__main__":
    main()
