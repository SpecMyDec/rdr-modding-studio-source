#!/usr/bin/env python3
"""rdr -- one entry point for the Red Dead Revolver toolkit.

    py -3 rdr.py <command> [args]

Run with no arguments for the command list. Everything reads the vanilla ISO
by default and never writes to it; commands that modify take --modded.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from rdrstudio import Game, guns                    # noqa: E402
from rdrstudio import config as cfg                  # noqa: E402
from rdrstudio import texture as T                   # noqa: E402
from rdrstudio.export import export_level            # noqa: E402
from rdrstudio.level import Level, list_levels       # noqa: E402
from rdrstudio.rsc import Rsc, find_rsc_names        # noqa: E402


def _game(args, writable=False):
    path = args.modded if getattr(args, "modded", False) else args.iso
    return Game(path, writable=writable)


def _script(name, *extra):
    return subprocess.call([sys.executable, os.path.join(HERE, name)] + list(extra),
                           cwd=HERE)


# --------------------------------------------------------------- commands

def cmd_info(args):
    g = _game(args)
    print("volume    : %s" % g.iso.volume_id)
    print("sectors   : %d" % g.iso.total_sectors)
    print("path      : %s" % g.path)
    arcs = g.archives()
    print("archives  : %d" % len(arcs))
    swat = g.archive("SWAT.DAT")
    print("SWAT.DAT  : %d entries%s"
          % (swat.count, "" if swat.count == 47115 else "  (expected 47115!)"))
    print("levels    : %d" % len(list_levels(g)))
    return 0


def cmd_archives(args):
    g = _game(args)
    print("%-26s %-6s %8s %10s" % ("archive", "magic", "files", "MB"))
    for a in g.archives():
        d = g.archive(a)
        print("%-26s %-6s %8d %10.1f" % (a, d.magic, d.count, d.size / 1048576.0))
        g._cache.pop(a.upper(), None)
    return 0


def cmd_ls(args):
    g = _game(args)
    d = g.archive(args.archive)
    pat = (args.pattern or "").lower()
    rows = [e for e in d if not pat or pat in e.name]
    rows.sort(key=lambda e: e.name)
    print("%-42s %9s %9s %8s %9s" % ("name", "size", "stored", "slot", "headroom"))
    for e in rows[:args.limit]:
        print("%-42s %9d %9d %8d %9d"
              % (e.name[:42], e.size_full, e.size_comp, e.slot, e.headroom))
    print("\n%d of %d entries" % (min(len(rows), args.limit), d.count))
    return 0


def cmd_cat(args):
    g = _game(args)
    data = g.read(args.archive, args.file)
    try:
        sys.stdout.write(data.decode("latin-1"))
    except Exception:
        print("<binary, %d bytes>" % len(data))
    return 0


def cmd_find(args):
    g = _game(args)
    hits = g.glob("*%s*" % args.pattern.lower())
    for name in sorted(hits)[:args.limit]:
        print("%-44s %d copies" % (name, len(hits[name])))
    print("\n%d matching filenames" % len(hits))
    return 0


def cmd_levels(args):
    """Levels grouped by which half of the game they belong to."""
    from rdrstudio import modes
    g = _game(args)
    cat = modes.catalog(g)
    if args.mode:
        cat = [e for e in cat if e["mode"] == args.mode]
    current = None
    for e in cat:
        if e["mode"] != current:
            current = e["mode"]
            n = sum(1 for x in cat if x["mode"] == current)
            print("\n%s  (%d)" % (e["mode_label"], n))
            print("-" * 58)
        note = ""
        if e["missions"]:
            note = "mission %s as %s" % (
                ", ".join(str(m["number"]) for m in e["missions"]),
                e["missions"][0]["character"])
            if e.get("town"):
                note += " (%s)" % e["town"]
        elif e.get("unlock"):
            u = e["unlock"]
            note = ("available from the start" if u["criteria"] == "NONE"
                    else "unlock: %s >= %s" % (u["criteria"], u["value"]))
        print("  %-12s %-26s %s" % (e["name"], note, e["archive"]))
    return 0


def cmd_level(args):
    g = _game(args)
    lvl = Level(g, args.archive)
    print("level     : %s  (%s)" % (lvl.name, lvl.lvl_file))
    print("objects   : %d  %s" % (len(lvl.nodes), lvl.stats()))
    lo, hi = lvl.bounds()
    print("bounds    : x[%.1f..%.1f] y[%.1f..%.1f] z[%.1f..%.1f]"
          % (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))
    geo = lvl.geometry()
    print("collision : %d meshes, %d tris" % (len(geo["meshes"]), geo["tris"]))
    ren = lvl.render_geometry()
    print("surfaces  : %d/%d chunks, %d tris"
          % (ren["decoded"], ren["chunks"], ren["tris"]))
    print("textures  : %d" % len(ren.get("textures", [])))
    if lvl.errors:
        print("warnings  : %d" % len(lvl.errors))
    return 0


def cmd_textures(args):
    g = _game(args)
    targets = ([args.archive] if args.archive
               else [a for a in list_levels(g) if a.startswith("LVLS_SNG")])
    total = ok = 0
    for arc in targets:
        d = g.archive(arc)
        name = arc.rsplit("/", 1)[-1][:-4]
        rsc = None
        for n in find_rsc_names(d, name):
            try:
                r = Rsc(d.read(n), n)
            except Exception:
                continue
            if r.tally().get("MIP0"):
                rsc = r
                break
        if rsc is None:
            continue
        dest = os.path.join(args.out, name)
        os.makedirs(dest, exist_ok=True)
        n_ok = 0
        table = rsc.texture_table()
        for i, (_, info, mip, pal) in enumerate(table):
            try:
                res = T.decode(rsc.raw(mip.index), T.parse_palette(rsc.raw(pal.index)))
            except Exception:
                res = None
            if not res:
                continue
            w, h, rgba = res
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in info["name"])
            T.write_png(os.path.join(dest, "%03d_%s_%dx%d.png" % (i, safe, w, h)),
                        w, h, rgba)
            n_ok += 1
        print("  %-22s %3d / %-3d" % (name, n_ok, len(table)))
        ok += n_ok
        total += len(table)
        g._cache.pop(arc.upper(), None)
    print("\n%d / %d textures -> %s" % (ok, total, args.out))
    return 0


def cmd_texfiles(args):
    g = _game(args)
    d = g.archive(args.archive)
    dest = os.path.join(args.out, args.archive.replace("/", "_"))
    os.makedirs(dest, exist_ok=True)
    ok = fail = 0
    for e in d:
        if e.ext != "tex":
            continue
        try:
            res = T.decode_tex_file(d.read(e))
        except Exception:
            res = None
        if not res:
            fail += 1
            continue
        w, h, rgba = res
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in e.name[:-4])
        T.write_png(os.path.join(dest, "%s_%dx%d.png" % (safe, w, h)), w, h, rgba)
        ok += 1
    print("%d decoded, %d failed -> %s" % (ok, fail, dest))
    return 0


def cmd_export(args):
    # .glb is written by default, and that is the point of the command now.
    # OBJ has no scene graph, so it cannot say "these 547 meshes are the art
    # and those 39 are the collision" -- glTF can, as two parent nodes, which
    # Blender turns into two Empties you can switch off with one click. That
    # separation is the whole reason to take a level out of the game, so
    # leaving it behind a flag meant the Desktop shortcut handed people the
    # one format that drops it.
    g = _game(args)
    out = args.out or os.path.join(cfg.ROOT, "07_Research", "obj")
    stats = export_level(g, args.archive, out,
                         collision=not args.no_collision,
                         surfaces=not args.no_surfaces,
                         areas=args.areas.split(",") if args.areas else None,
                         layout="native" if args.native else "auto",
                         glb=not args.no_glb)
    print("wrote %s" % stats["obj"])
    if stats.get("glb"):
        g_ = stats["glb"]
        print("      %s  (%d meshes, %d nodes, %.1f MB)"
              % (g_["path"], g_["meshes"], g_["nodes"], g_["bytes"] / 1048576.0))
    print("  collision meshes : %d" % stats["collision_meshes"])
    print("  surface meshes   : %d" % stats["surface_meshes"])
    print("  vertices / tris  : %d / %d" % (stats["verts"], stats["tris"]))
    print("  textures         : %d" % stats["textures"])
    return 0


def cmd_guns(args):
    g = _game(args)
    data = g.read("SWAT.DAT", "swat.guns")
    if not args.name:
        print("%-26s %8s %6s %7s %8s" % ("weapon", "damage", "clip", "rate", "range"))
        for w in guns.summary(data):
            print("%-26s %8s %6s %7s %8s"
                  % (w["name"], w["damage"], w["clip"], w["rate"], w["range"]))
        return 0
    w = guns.find(data, args.name)
    if w is None:
        print("no weapon named %r" % args.name)
        return 1
    for k, v in w.fields.items():
        print("  %-28s %s" % (k, v["text"]))
    return 0


def cmd_campaign(args):
    from rdrstudio import campaign
    g = _game(args)
    data = campaign.summary(g)
    if args.unlocks:
        rows = data["unlockables"]
        if args.type:
            rows = [u for u in rows if u["type"] == args.type]
        print("%-34s %-10s %-8s %s" % ("name", "type", "default", "criteria"))
        for u in rows:
            print("%-34s %-10s %-8s %s"
                  % (u["name"], u["type"], u["default"], u["criteria"]))
        print("\n%d shown, %d of %d unlocked overall"
              % (len(rows), data["unlocked"], len(data["unlockables"])))
        return 0
    print("%-3s %-16s %-5s %-26s %6s %8s %s"
          % ("#", "level", "char", "weapons", "reward", "maxtime", "hidden reward"))
    for i, m in enumerate(data["missions"], 1):
        print("%-3d %-16s %-5s %-26s %6s %8s %s"
              % (i, m["Level"], m["Character"], m["Weapons"][:26],
                 m["Reward $"], m["Max Time"], m["Hidden"]))
    print("\n%d missions, %d unlockables (%d unlocked), %d items"
          % (len(data["missions"]), len(data["unlockables"]),
             data["unlocked"], len(data["items"])))
    return 0


def cmd_behaviour(args):
    """Inspect .actb/.stateb behaviour tables, or census their vocabulary."""
    import collections
    from rdrstudio import actb
    g = _game(args)

    if args.file:
        arc = args.archive or "SWAT.DAT"
        data = g.read(arc, args.file)
        if not actb.is_actb(data):
            print("%s is not a behaviour table (no 'version' header)" % args.file)
            return 1
        print(actb.listing(data))
        return 0

    freq = collections.Counter()
    names = collections.Counter()
    files = ok = 0
    for arc in ([args.archive] if args.archive else g.archives()):
        try:
            d = g.archive(arc)
        except Exception:
            continue
        for e in d:
            if e.ext not in ("actb", "stateb", "acttbl"):
                continue
            files += 1
            try:
                data = d.read(e)
            except Exception:
                continue
            if not actb.is_actb(data):
                continue
            try:
                _, _, toks = actb.parse(data, limit=5000)
            except Exception:
                continue
            ok += 1
            for t in toks:
                if t.kind == "int" and 1000 <= t.value <= 3000:
                    freq[t.value] += 1
                elif t.kind == "name":
                    names[t.value] += 1
        g._cache.pop(arc.upper(), None)

    print("behaviour files: %d  (%d parsed)" % (files, ok))
    print("distinct opcodes: %d\n" % len(freq))
    print("most-referenced names -- what the AI actually tests:")
    for n, c in names.most_common(args.limit):
        print("   %-28s %d" % (n, c))
    print("\nmost-used opcodes (meanings unknown -- they index the ELF):")
    for v, c in freq.most_common(12):
        print("   %-6d %d" % (v, c))
    return 0


def cmd_script(args):
    """Run a Python script against the disc.

    Deliberately a CLI command and not a button in the Studio: the Studio can
    be served on the LAN, and an "exec this Python" HTTP endpoint would be
    remote code execution for anyone on the network. Running a script is a
    thing you do from your own shell, on a file you wrote.
    """
    writable = bool(args.write)
    if writable and not args.modded:
        print("refusing to write to the vanilla ISO -- add --modded "
              "(before the subcommand)")
        return 1
    g = Game(cfg.MODDED_ISO if args.modded else args.iso, writable=writable)
    vanilla = Game(cfg.VANILLA_ISO)

    written = []

    def write(archive, name, data):
        if not writable:
            raise PermissionError(
                "script is read-only; pass --write (and --modded) to patch")
        if isinstance(data, str):
            data = data.encode("latin-1")
        used, slot = g.archive(archive).patch(name, data)
        written.append((archive, name, used, slot))
        return used, slot

    def patch_everywhere(name, fn):
        out = []
        for arc in g.locate(name):
            d = g.archive(arc)
            try:
                new = fn(d.read(name))
                out.append((arc,) + tuple(write(arc, name, new)))
            except Exception as ex:
                out.append((arc, "SKIP: %s" % ex))
        return out

    from rdrstudio import campaign, creature, modes, texture, vif
    from rdrstudio.export import export_level
    from rdrstudio.level import Level
    from rdrstudio.rsc import Rsc

    env = dict(
        game=g, vanilla=vanilla,
        read=lambda a, n: g.read(a, n),
        stock=lambda a, n: vanilla.read(a, n),
        write=write, patch_everywhere=patch_everywhere,
        locate=g.locate, archives=g.archives, glob=g.glob,
        Level=Level, Rsc=Rsc, guns=guns, creature=creature, campaign=campaign,
        modes=modes, texture=texture, vif=vif, export_level=export_level,
    )

    if args.code:
        src, origin = args.code, "<-c>"
    else:
        src, origin = open(args.file, encoding="utf-8").read(), args.file
    exec(compile(src, origin, "exec"), env)

    if written:
        print("\n-- wrote %d file(s) --" % len(written))
        for arc, name, used, slot in written:
            print("   %-24s %-30s %d/%d bytes" % (arc, name, used, slot))
    elif writable:
        print("\n(no files written)")
    return 0


def cmd_grep(args):
    """Search inside decompressed file contents, disc-wide."""
    g = _game(args)
    needle = args.pattern.encode("latin-1", "ignore")
    low = needle.lower()
    archives = [args.archive] if args.archive else g.archives()
    hits = 0
    for arc in archives:
        try:
            d = g.archive(arc)
        except Exception:
            continue
        for e in d:
            if args.ext and e.ext != args.ext.lower().lstrip("."):
                continue
            try:
                data = d.read(e)
            except Exception:
                continue
            hay = data.lower() if not args.case else data
            pos = hay.find(low if not args.case else needle)
            if pos < 0:
                continue
            ctx = data[max(0, pos - 40):pos + len(needle) + 60]
            text = "".join(chr(c) if 32 <= c < 127 else "." for c in ctx)
            print("%-24s %-34s @%-7d %s" % (arc, e.name[:34], pos, text))
            hits += 1
            if hits >= args.limit:
                print("\n-- stopped at %d hits (raise --limit) --" % args.limit)
                return 0
        g._cache.pop(arc.upper(), None)
    print("\n%d hit(s)" % hits)
    return 0


def cmd_studio(args):
    return _script("RDR_Studio.py", *(["--lan"] if args.lan else []))


def cmd_test(args):
    return _script("run_tests.py", *(["--quick"] if args.quick else []))


def cmd_verify(args):
    return _script(os.path.join("scripts", "verify_disc.py"), args.iso)


def cmd_extract(args):
    return _script(os.path.join("scripts", "extract_all.py"), "--iso", args.iso,
                   "--out", args.out)


def cmd_unpack_elf(args):
    """Write out the DECOMPRESSED game executable.

    As shipped, SLUS_205.00 is aPLib-compressed with an XOR layer, so none of
    the game's own code or strings can be read from it. This produces the
    4.5 MB image the console actually runs, based at 0x00100000 -- which is
    what makes the engine's own decoders readable.
    """
    from rdrstudio.iso9660 import Iso9660
    from rdrstudio import elfpack

    iso = Iso9660(args.iso)
    try:
        e = iso.get("SLUS_205.00")
        packed = iso.read_at(e.offset, e.size)
        img, base = elfpack.unpack_elf(packed)
    finally:
        iso.close()

    with open(args.out, "wb") as fh:
        fh.write(img)
    print("SLUS_205.00: %d bytes packed -> %d bytes unpacked (%.1fx)"
          % (len(packed), len(img), len(img) / float(len(packed))))
    print("base address 0x%08X, so image offset N is address 0x%08X + N"
          % (base, base))
    print("wrote %s" % args.out)
    # Landmarks, so the output is obviously the real thing and not noise.
    for probe in (b"s:/swat/assets/current", b"cm3\x00cm2\x00cmp\x00anim\x00"):
        at = img.find(probe)
        if at >= 0:
            print("  %-30r at 0x%08X" % (probe[:26], base + at))
    print("\nthe animation decoder is at 0x002D2EB0 -- see "
          "07_Research/FORMAT_NOTES.md")
    return 0


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        prog="rdr", description="Red Dead Revolver (PS2) modding toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  py -3 rdr.py info
  py -3 rdr.py ls SWAT.DAT .guns
  py -3 rdr.py cat LVLS_SNG/MANS.DAT lvl_mans.spwn
  py -3 rdr.py find swat.guns
  py -3 rdr.py level LVLS_SNG/MANS.DAT
  py -3 rdr.py export LVLS_SNG/MANS.DAT      # -> OBJ for Blender
  py -3 rdr.py textures                      # all levels -> PNG
  py -3 rdr.py guns Old_Pistol
  py -3 rdr.py campaign                      # the 32 missions in play order
  py -3 rdr.py campaign --unlocks --type cheat
  py -3 rdr.py grep Old_Pistol --ext guns     # search file CONTENTS
  py -3 rdr.py script -c "print(len(archives()))"
  py -3 rdr.py script mymod.py --modded --write
  py -3 rdr.py studio --lan
  py -3 rdr.py test
  py -3 rdr.py unpack-elf                    # decompress the game's own code

scripting environment:
  game / vanilla        open discs (vanilla is always read-only)
  read(a,n) stock(a,n)  decompressed bytes from working / pristine disc
  write(a,n,data)       patch in place  (needs --write --modded)
  patch_everywhere(n,f) apply f(bytes)->bytes to every copy of a file
  locate(n) archives() glob(pat)
  Level Rsc guns creature campaign modes texture vif export_level
""")
    ap.add_argument("--iso", default=cfg.VANILLA_ISO, help="disc to read")
    ap.add_argument("--modded", action="store_true",
                    help="operate on the working ISO instead of vanilla")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(fn=fn)
        return p

    add("info", cmd_info, "disc identity and totals")
    add("archives", cmd_archives, "list all archives")

    p = add("ls", cmd_ls, "list files in an archive")
    p.add_argument("archive")
    p.add_argument("pattern", nargs="?")
    p.add_argument("--limit", type=int, default=60)

    p = add("cat", cmd_cat, "print a file from an archive")
    p.add_argument("archive")
    p.add_argument("file")

    p = add("find", cmd_find, "find a filename across every archive")
    p.add_argument("pattern")
    p.add_argument("--limit", type=int, default=60)

    p = add("levels", cmd_levels, "list levels by mode (campaign / showdown)")
    p.add_argument("--mode", choices=["campaign", "bonus", "showdown2", "showdown4"])

    p = add("level", cmd_level, "summarise a level")
    p.add_argument("archive")

    p = add("textures", cmd_textures, "decode RSC textures to PNG")
    p.add_argument("archive", nargs="?")
    p.add_argument("--out", default=os.path.join(cfg.ROOT, "07_Research", "tex"))

    p = add("texfiles", cmd_texfiles, "decode loose .tex files to PNG")
    p.add_argument("archive")
    p.add_argument("--out", default=os.path.join(cfg.ROOT, "07_Research", "texfiles"))

    p = add("export", cmd_export,
            "export a level to .glb (+ OBJ + MTL + textures)")
    p.add_argument("archive")
    p.add_argument("--out")
    p.add_argument("--no-collision", action="store_true",
                   help="art only -- leave the collision shells out")
    p.add_argument("--no-surfaces", action="store_true",
                   help="collision only -- leave the art out")
    p.add_argument("--no-glb", action="store_true",
                   help="OBJ only; drops the SURFACES/COLLISION grouping, "
                        "because OBJ has no scene graph to carry it")
    p.add_argument("--native", action="store_true",
                   help="write every chunk at its stored coordinates and move "
                        "nothing, instead of laying sibling areas out clear of "
                        "each other")
    p.add_argument("--areas",
                   help="comma-separated bundle or area names; default is "
                        "every area in the level")

    p = add("guns", cmd_guns, "list weapons, or show one in full")
    p.add_argument("name", nargs="?")

    p = add("campaign", cmd_campaign, "mission order, or the unlockables list")
    p.add_argument("--unlocks", action="store_true", help="show unlockables")
    p.add_argument("--type", help="character / stage / cheat / journal")

    p = add("behaviour", cmd_behaviour,
            "inspect .actb/.stateb, or census the AI vocabulary")
    p.add_argument("file", nargs="?", help="a .actb/.stateb to list")
    p.add_argument("--archive", help="limit to one archive")
    p.add_argument("--limit", type=int, default=30)

    p = add("script", cmd_script, "run a Python script against the disc")
    p.add_argument("file", nargs="?", help="script file to run")
    p.add_argument("-c", "--code", help="inline code instead of a file")
    p.add_argument("--write", action="store_true",
                   help="allow patching (use with --modded)")

    p = add("grep", cmd_grep, "search inside file CONTENTS across the disc")
    p.add_argument("pattern")
    p.add_argument("--archive", help="limit to one archive")
    p.add_argument("--ext", help="limit to one extension")
    p.add_argument("--case", action="store_true", help="case sensitive")
    p.add_argument("--limit", type=int, default=60)

    p = add("studio", cmd_studio, "launch the 3D editor")
    p.add_argument("--lan", action="store_true")

    p = add("test", cmd_test, "run the test suite")
    p.add_argument("--quick", action="store_true")

    add("verify", cmd_verify, "full-disc integrity sweep")

    p = add("extract", cmd_extract, "unpack every archive to disk")
    p.add_argument("--out", default=cfg.EXTRACTED)

    p = add("unpack-elf", cmd_unpack_elf,
            "decompress SLUS_205.00 (aPLib+XOR) so the engine is readable")
    p.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "07_Research",
        "SLUS_205.00.unpacked.bin"))

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
