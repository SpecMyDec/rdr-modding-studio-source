"""Harvest every "this is not possible" claim in the project.

M3's sweep, mechanised. A claim is any line of prose -- doc, comment,
docstring, UI string or error message -- that tells the reader the formats,
the disc or the toolkit CANNOT do something. Those lines steer the roadmap
and the UI, and this project has already shipped at least one that is false,
so every one of them has to be found before any of them can be settled.

What this does NOT do is decide. It collects candidates and prints them with
a file:line anchor; `05_Docs/CAPABILITIES.md` is where each one gets a verdict
and the probe that settled it. Anything that is merely a runtime guard-rail
("close PCSX2 and try again", "pick an archive first") is noise here, so the
obvious shapes of those are filtered out -- deliberately loosely, because a
missed claim is expensive and a false positive costs one line of reading.

    python3 03_Tools/scripts/probe_claims.py             # the candidates
    python3 03_Tools/scripts/probe_claims.py --count     # just the totals
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Where a claim can hide. 09_Website is the developer's and off limits; 01_Vanilla and
# 02_Modded hold discs; 04_Extracted is disc-derived.
ROOTS = ("03_Tools", "05_Docs", "07_Research")
TOP_FILES = ("README.md", "ROADMAP.md")
SUFFIXES = (".py", ".js", ".html", ".md", ".txt")

# Vendored or generated: not ours to make claims in, and not shipped copy.
SKIP_PARTS = (
    "/__pycache__/",
    "/three.module.js",
    "/node_modules/",
    "/.git/",
    # Third-party and build products. The packaging SOURCE is scanned (it
    # carries shipped strings); its virtualenv and its outputs are not --
    # `dist/` and `build/` are regenerated from the source this sweep does
    # cover, so a claim fixed at the source is fixed in the next build.
    "/packaging/.venv/",
    "/packaging/dist/",
    "/packaging/build/",
    "/03_Tools/ref2/",
    # This sweep's own machinery and the lane paperwork QUOTE claims in order
    # to settle them. Scanning them turns every verdict into a fresh
    # candidate, so the register would grow every time it was updated.
    "/scripts/probe_claims.py",
    "/scripts/verify_capabilities.py",
    "/05_Docs/CAPABILITIES.md",
    "/05_Docs/LANE-REQUESTS.md",
    "/05_Docs/BRIEF-",
    "/05_Docs/PROGRESS-",
    "/05_Docs/TESTER-NOTES",
    "/ROADMAP-TESTER.md",
    "/STATE-CONTRACT.md",
)

# The vocabulary the brief names, plus the shapes this codebase actually uses.
CLAIM = re.compile(
    r"\b("
    r"cannot|can not|can't|could not be|couldn't be|"
    r"not possible|impossible|"
    r"unsupported|not supported|"
    r"read-only|read only|"
    r"we don'?t know|not known|never been read|"
    r"no way to|there is no way|"
    r"not authorable|not recoverable|not derivable|"
    r"never shipped|does not ship|"
    r"still closed|remains closed|opaque|"
    r"never works|does nothing|has no effect|"
    r"only possible to|the only thing"
    r")\b",
    re.I,
)

# Runtime guard-rails: true statements about the machine right now, not about
# the formats. These are not claims and never go in the register.
GUARD = re.compile(
    r"("
    r"pcsx2|emulator|"
    r"open a file first|pick an? \w+ first|choose an? \w+|"
    r"working iso|read-only tick|properties|"
    r"try again|nothing was written|"
    r"permission|errno|oserror|ioerror|traceback|"
    r"browser|localstorage|clipboard|download"
    r")",
    re.I,
)


def files():
    for base in ROOTS:
        for dirpath, dirnames, names in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "node_modules", ".git",
                                        ".venv", "dist", "build", "ref2")]
            for n in sorted(names):
                p = os.path.join(dirpath, n)
                if not n.endswith(SUFFIXES):
                    continue
                if any(s in p.replace(os.sep, "/") for s in SKIP_PARTS):
                    continue
                yield p
    for n in TOP_FILES:
        p = os.path.join(ROOT, n)
        if os.path.exists(p):
            yield p


def harvest():
    out = []
    for path in files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            text = line.strip()
            if not text or len(text) > 400:
                continue
            m = CLAIM.search(text)
            if not m:
                continue
            guarded = bool(GUARD.search(text))
            out.append(dict(file=rel, line=i, text=text,
                            word=m.group(1).lower(), guard=guarded))
    return out


def main():
    rows = harvest()
    claims = [r for r in rows if not r["guard"]]
    if "--count" in sys.argv:
        print("candidate lines: %d" % len(rows))
        print("after guard-rail filter: %d" % len(claims))
        return
    show = rows if "--all" in sys.argv else claims
    cur = None
    for r in show:
        if r["file"] != cur:
            cur = r["file"]
            print("\n== %s" % cur)
        print("  %5d  [%s] %s" % (r["line"], r["word"], r["text"][:200]))
    print("\n%d candidate claim line(s)%s."
          % (len(show), "" if "--all" in sys.argv else
             " after filtering %d runtime guard-rails" % (len(rows) - len(claims))))


if __name__ == "__main__":
    main()
