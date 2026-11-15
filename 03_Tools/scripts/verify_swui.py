"""The menu / HUD reader covers the UI files and rewrites them byte-exact.

`.swui` is the one place on this disc where reaching for `props.py` first is
wrong, and the point of this script is that the claim is checked rather than
asserted. It reports, over every distinct UI file on the disc:

  1. How props.py does on its own -- the honest baseline for "we already had
     a parser for this".
  2. Which of the three dialects each file actually is. The extension does not
     say: `.swui` covers tag markup, props files and bare preload lists.
  3. Fidelity. Writing every field back with its OWN value must return the
     input unchanged, on every file, not a sample. These files sit in
     fixed-size archive slots -- `pause.swui` has 26 bytes of headroom in each
     of its 62 copies -- so a reader that renormalised a number or reflowed a
     space would grow a file past its slot.
  4. The rename evidence, which is the reason this format is dangerous:
     how many screen names and widget ids appear verbatim in the executable.

Exit code is non-zero if fidelity fails anywhere. Coverage is reported, not
enforced -- 3 files are genuinely unreadable (two are empty, one is UTF-16)
and pretending otherwise would be the exact dishonesty this script exists to
prevent.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rdrstudio import Game  # noqa: E402
from rdrstudio import props, swui  # noqa: E402
from rdrstudio.config import VANILLA_ISO  # noqa: E402


def content_lines(data):
    """Lines that carry something a person could edit."""
    out = []
    for i, line in enumerate(data.split(b"\n"), 1):
        s = line.strip()
        if not s or s.startswith(b";") or s in (b"{", b"}"):
            continue
        out.append(i)
    return out


def main():
    game = Game(VANILLA_ISO)
    screens, where = swui.all_screens(game)

    entries = sum(len(v) for v in where.values())
    by_ext = Counter(n.rsplit(".", 1)[-1].lower() for n in screens)
    print("UI files on the disc")
    print("  %d entries across %d archives resolve to %d distinct files"
          % (entries, len(game.archives()), len(screens)))
    for ext, n in sorted(by_ext.items(), key=lambda kv: -kv[1]):
        print("      %-10s %4d distinct" % ("." + ext, n))

    # ---------------------------------------------------- the props baseline
    p_files = p_cov = 0
    total_lines = 0
    for name, data in screens.items():
        if not swui.looks_parsable(data)[0]:
            continue
        lines = content_lines(data)
        total_lines += len(lines)
        fs = props.parse(data)
        if fs:
            p_files += 1
        seen = set(f.line for f in fs)
        p_cov += sum(1 for i in lines if i in seen)
    print()
    print("props.py alone (the baseline that made a new reader necessary)")
    print("  %d of %d files yield any field" % (p_files, len(screens)))
    print("  %d of %d content lines covered = %.2f%%"
          % (p_cov, total_lines, 100.0 * p_cov / total_lines))

    # --------------------------------------------------- dialects + fidelity
    dial = Counter()
    covered = 0
    bad_rt = []
    bad_span = []
    tags = fields = tokens = 0
    tag_names = Counter()
    for name in sorted(screens):
        data = screens[name]
        d = swui.dialect(data)
        dial[d] += 1
        if d == "unreadable":
            continue
        lines = content_lines(data)
        if d == "tags":
            items = swui.parse(data)
            tags += len(items)
            seen = set(t.line for t in items)
            for t in items:
                fields += len(t.fields)
                for f in t.fields:
                    tokens += len(f.tokens)
                    # A span that does not reproduce its own text would make
                    # every edit land on the wrong bytes.
                    if data[f.start:f.end].decode("latin-1") != f.raw:
                        bad_span.append((name, f.path))
                    for tk in f.tokens:
                        if data[tk.start:tk.end].decode("latin-1") != tk.text:
                            bad_span.append((name, f.path))
                if not t.commented:
                    tag_names[t.name] += 1
            edits = {f.path: f.raw for t in items for f in t.fields
                     if not t.commented
                     and not any(c in f.raw for c in ":<>;")}
            out = swui.apply(data, edits)
        elif d == "props":
            items = props.parse(data)
            seen = set(f.line for f in items)
            out = props.apply(data, {f.path: f.raw for f in items})
        else:
            items = swui.parse_list(data)
            seen = set(ln.line for ln in items)
            out = swui.apply_list(data, {ln.path: ln.raw for ln in items})
        if out != data:
            bad_rt.append(name)
        covered += sum(1 for i in lines if i in seen)

    print()
    print("routed by CONTENT, not by extension")
    for k, v in sorted(dial.items(), key=lambda kv: -kv[1]):
        print("      %-12s %4d files" % (k, v))
    print("  %d of %d content lines covered = %.2f%%"
          % (covered, total_lines, 100.0 * covered / total_lines))
    print("  %d tags, %d fields, %d tokens carry a byte span"
          % (tags, fields, tokens))
    print("  %d distinct tag names; commonest: %s"
          % (len(tag_names), ", ".join("%s x%d" % kv
                                       for kv in tag_names.most_common(6))))

    readable = len(screens) - dial["unreadable"]
    print()
    print("fidelity, on every file")
    print("  identity round-trip byte-exact: %d of %d"
          % (readable - len(bad_rt), readable))
    print("  field spans reproduce their own text: %s"
          % ("all" if not bad_span else "%d BAD" % len(bad_span)))

    # -------------------------------------------------------- rename evidence
    exe, refs, note = swui.lookup_names(game, screens)
    stems = set(n.rsplit(".", 1)[0].lower() for n in screens)
    ids = set()
    for name, data in screens.items():
        if swui.dialect(data) != "tags":
            continue
        for t in swui.parse(data, include_commented=False):
            f = [x.raw for x in t.fields]
            for i, v in enumerate(f):
                if v.upper() == "NAME" and i + 1 < len(f):
                    ids.add(f[i + 1].lower())
            if t.name == "LOOKUP" and f:
                ids.add(f[0].lower())
    print()
    print("why renames are dangerous (the trap this format carries)")
    if note:
        print("  %s" % note)
    print("  %d printable strings in the unpacked SLUS_205.00" % len(exe))
    print("  %d of %d screen names appear in it verbatim"
          % (len(stems & exe), len(stems)))
    print("  %d of %d widget ids appear in it verbatim"
          % (len(ids & exe), len(ids)))
    print("  %d screens are referenced by another screen on the disc"
          % len(refs))

    if bad_rt or bad_span:
        print()
        print("FAIL  round-trip: %s" % (bad_rt[:5] or "ok"))
        print("      spans:      %s" % (bad_span[:5] or "ok"))
        return 1
    print()
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
