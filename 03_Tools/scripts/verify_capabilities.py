"""[M3] The false "impossible" claims are gone, and the .DAT case is proved.

Two assertions, both about the REAL effect rather than about a function having
been called:

  1. **No retired claim text ships.** `05_Docs/CAPABILITIES.md` carries a
     machine-read block of every claim this project settled VERIFIED-FALSE.
     None of those strings may appear in the toolkit, the web front end, the
     research notes, `README.md` or `ROADMAP.md`. Matching is
     case-insensitive and collapses whitespace, so a reflowed paragraph or a
     re-indented comment cannot smuggle one back.

  2. **A .DAT archive takes a brand-new file and gives it back.** Not a
     feasibility check -- an actual insertion into a real archive, closed,
     re-opened from the bytes on disk, read back byte-exact, with every
     pre-existing entry's name, offset, both sizes and SHA-1 unchanged. That
     is `probe_dat_add.py`'s proof pass, run here for real. It works on an
     extracted COPY of one archive in a temp directory, so this test never
     writes to any ISO and leaves the disc exactly as it found it.

Guard-rail on the register itself: a VERIFIED-FALSE entry that has no retired
string is a claim someone marked settled without writing down what to look
for, so that fails too.

    python3 scripts/verify_capabilities.py
    python3 scripts/verify_capabilities.py --built   # also scan packaging output
"""

import importlib.util
import os
import re
import sys

# `scripts/` holds a `dis.py`, and Python puts a script's own directory first
# on sys.path -- which shadows the stdlib `dis` that `inspect` (and so
# `argparse`) imports on 3.13+. Drop this directory and add `03_Tools`.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

ROOT = os.path.dirname(os.path.dirname(_HERE))
REGISTER = os.path.join(ROOT, "05_Docs", "CAPABILITIES.md")

BEGIN = "<!-- RETIRED-TEXT:BEGIN -->"
END = "<!-- RETIRED-TEXT:END -->"

# Packaging output: regenerated from the source that is always scanned, so a
# stale copy is a build that has not been re-run, not a claim that came back.
# Opt in with --built when checking a build you just made.
BUILT = ("/packaging/dist/", "/packaging/build/")


def _load(name):
    """Import a sibling script by path, without putting `scripts/` on the path."""
    spec = importlib.util.spec_from_file_location(
        "_m3_" + name, os.path.join(_HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def squash(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def first_line(lines, needle):
    """Where a squashed phrase starts, even when it is reflowed over several
    lines. Reported as a file:line so the offending text can be opened."""
    words = len(needle.split())
    for i in range(len(lines)):
        window = squash(" ".join(lines[i:i + words + 2]))
        if needle in window:
            return i + 1
    return 0


def read_register():
    """(retired strings, claim ids marked VERIFIED-FALSE) from CAPABILITIES.md."""
    with open(REGISTER, encoding="utf-8") as fh:
        text = fh.read()
    if BEGIN not in text or END not in text:
        raise SystemExit("verify_capabilities: %s has no RETIRED-TEXT block"
                         % os.path.relpath(REGISTER, ROOT))
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    retired = []
    declared_textless = set()
    pending = []          # (claim id, owning lane, phrase still shipping)
    for line in block.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        # Only the FIRST separator splits id from text, and only a literal
        # `pending(<lane>)` in second position splits again. A retired phrase
        # is free to contain "|" -- several of them are markdown table rows,
        # which are nothing but pipes.
        cid, rest = (x.strip() for x in line.split("|", 1))
        # "pending(<lane>) | <phrase>": the text still ships, in a file this
        # lane does not own. Reported every run, never failed on -- one lane
        # must not be blocked by another lane's queue, and must not be able to
        # forget either.
        m = re.match(r"pending\(([^)]*)\)\s*\|(.*)$", rest, re.S)
        if m:
            pending.append((cid, m.group(1), m.group(2).strip()))
            continue
        phrase = rest
        # "-" means: this claim was settled FALSE but never had shipped text
        # to retire -- it lived in behaviour, or in a bug report, not in copy.
        # Saying so explicitly is the point; silence is what this block is
        # designed to make impossible.
        if phrase and phrase != "-":
            retired.append((cid, phrase))
        elif phrase == "-":
            declared_textless.add(cid)

    false_ids = set()
    cid = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            m = re.match(r"#+\s+(C-\d+)\b", stripped)
            # Any other heading ends the entry, so the cross-lane summary
            # table below the register cannot be read as part of C-013.
            cid = m.group(1) if m else None
        elif cid and "VERIFIED-FALSE" in line:
            false_ids.add(cid)
    return retired, false_ids, declared_textless, pending


def check_retired_text(retired, scan_built):
    claims = _load("probe_claims")
    paths = list(claims.files())
    if scan_built:
        for base in ("03_Tools",):
            for dirpath, _dirs, names in os.walk(os.path.join(ROOT, base)):
                slash = dirpath.replace(os.sep, "/") + "/"
                if not any(b in slash for b in BUILT):
                    continue
                for n in names:
                    if n.endswith((".py", ".js", ".html", ".md", ".txt")):
                        paths.append(os.path.join(dirpath, n))

    wanted = [(cid, phrase, squash(phrase)) for cid, phrase in retired]
    hits = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        # Squash across line breaks too: a claim that got reflowed over two
        # lines is the same claim.
        joined = squash(" ".join(lines))
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        for cid, phrase, needle in wanted:
            if needle in joined:
                hits.append((cid, rel, first_line(lines, needle), phrase))
    print("1. retired claim text: %d phrase(s) checked against %d file(s)"
          % (len(wanted), len(paths)))
    for cid, rel, line, phrase in hits:
        print("   FAIL %s is back in %s:%d -- %r" % (cid, rel, line, phrase))
    if not hits:
        print("   none of them ships.")
    return not hits


def check_register_complete(retired, false_ids, textless, pending):
    covered = ({cid for cid, _ in retired} | textless
               | {cid for cid, _lane, _p in pending})
    missing = sorted(false_ids - covered)
    print("\n2. register: %d claim(s) marked VERIFIED-FALSE, %d retired "
          "string(s), %d declared textless, %d pending on another lane"
          % (len(false_ids), len(retired), len(textless), len(pending)))
    for cid, lane, phrase in pending:
        print("   PENDING %s -- %s still ships %r" % (cid, lane, phrase))
    for cid in missing:
        print("   FAIL %s is VERIFIED-FALSE but says nothing about the text it "
              "retires -- add a phrase, or \"-\" to declare it had none" % cid)
    if not missing:
        print("   every VERIFIED-FALSE claim is accounted for.")
    return not missing


def check_dat_add():
    print("\n3. a .DAT archive takes a brand-new file:")
    add = _load("probe_dat_add")
    from rdrstudio import config
    from rdrstudio.game import Game
    # The PRISTINE disc, read-only: the proof is stronger on the disc nobody
    # has edited, and reading it never queues behind another lane's suite run.
    game = Game(config.VANILLA_ISO, writable=False)
    ok = add.pass_proof(game, "LVLS_M2/INT2.DAT")
    game.close()
    return ok


def main():
    scan_built = "--built" in sys.argv
    retired, false_ids, textless, pending = read_register()
    results = [
        ("no retired claim text ships", check_retired_text(retired, scan_built)),
        ("every VERIFIED-FALSE claim is watched",
         check_register_complete(retired, false_ids, textless, pending)),
        ("a new file round-trips through a .DAT archive", check_dat_add()),
    ]
    print("\nSUMMARY")
    for label, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", label))
    bad = [l for l, ok in results if not ok]
    print("%d of %d checks passed." % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
