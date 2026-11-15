"""A JavaScript syntax gate for the build, with no Node.js on the machine.

Why this exists, stated plainly: a syntax error in the front end blanks the
entire product, and that has happened twice. The build strips feature-gated
spans out of .js files before freezing (see build.py --build release), and an
unbalanced sentinel produces exactly that failure -- silently, in a zip, on
someone else's computer.

There is no JavaScript parser in the Python standard library, and Node is not
installed on the Windows build machine (`node --version` -> not recognised,
measured) though it is present in the WSL checkout -- so which engines answer
depends on where this runs, and the verdict always names them. So:

  * The scanner below always runs. It tokenises well enough to balance
    delimiters: it understands line and block comments, all three string
    forms, template literals including nested ${ } interpolation, and regex
    literals via the standard previous-significant-token heuristic.
  * If `node` IS available it runs too, and both must accept the file. Node is
    a real parser and catches what the scanner cannot -- but it does NOT
    simply beat the scanner, which is what this file used to assume. `node
    --check` reads a `.js` path as CommonJS, and the CommonJS module wrapper
    swallows a stray `}`: measured on the real app.js with one brace appended,
    node says ok and the scanner finds it on line 12065. See check_file.

What the scanner CAN prove: every ( [ { opened is closed, in the right order,
and no string, template or block comment runs off the end of the file. That is
precisely the failure mode a bad strip creates -- which is why it runs even
when node is there to disagree with it.

What it CANNOT prove: that the JavaScript is otherwise valid. `let let = ;`
passes. It is a gate against the build breaking the file, not a linter -- and
the build says which engine produced the verdict, because a weaker check that
pretends to be a stronger one is worse than no check.
"""

import os
import shutil
import subprocess
import sys

PAIRS = {")": "(", "]": "[", "}": "{"}
OPENS = set("([{")

# After any of these, a `/` starts a regular expression rather than a division.
# Anything else (an identifier, a number, a `)`, a `]`) means division.
REGEX_OK_WORDS = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "case", "do", "else", "yield", "await", "throw",
}
REGEX_OK_PUNCT = set("([{,;:=!&|?+-*%~^<>")


class Problem(Exception):
    def __init__(self, line, msg):
        super().__init__("line %d: %s" % (line, msg))
        self.line = line
        self.msg = msg


def check_text(src, name="<js>"):
    """Raise Problem, or return the number of tokens balanced."""
    i, n = 0, len(src)
    line = 1
    stack = []                     # (char, line)
    tmpl = []                      # depth of `{` when a template opened
    prev = ""                      # last significant token, for the regex test
    counted = 0

    def err(msg):
        raise Problem(line, msg)

    while i < n:
        c = src[i]

        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f\v":
            i += 1
            continue

        # ---- comments
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            if j < 0:
                err("block comment is never closed")
            line += src.count("\n", i, j)
            i = j + 2
            continue

        # ---- strings
        if c in "\"'":
            start = line
            i += 1
            while i < n and src[i] != c:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "\n":
                    raise Problem(start, "string is never closed")
                i += 1
            if i >= n:
                raise Problem(start, "string is never closed")
            i += 1
            prev = "str"
            counted += 1
            continue

        # ---- template literals, with ${ } interpolation
        if c == "`":
            start = line
            i += 1
            while True:
                if i >= n:
                    raise Problem(start, "template literal is never closed")
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                    i += 1
                    continue
                if src[i] == "`":
                    i += 1
                    break
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    # Hand the interpolation back to the main loop by pushing a
                    # brace; the matching `}` pops it and we resume the string
                    # part here. Recursion is the honest way to say that.
                    depth = 1
                    j = i + 2
                    sub_start = j
                    while j < n and depth:
                        ch = src[j]
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                        elif ch in "\"'`":
                            q = ch
                            j += 1
                            while j < n and src[j] != q:
                                j += 2 if src[j] == "\\" else 1
                        elif ch == "\n":
                            line += 1
                        j += 1
                    if depth:
                        raise Problem(start, "${ in a template is never closed")
                    check_text(src[sub_start:j - 1], name)
                    i = j
                    continue
                i += 1
            prev = "str"
            counted += 1
            continue

        # ---- regex literal
        if c == "/":
            if prev == "" or prev in REGEX_OK_WORDS or (
                    len(prev) == 1 and prev in REGEX_OK_PUNCT):
                start = line
                i += 1
                in_class = False
                while i < n:
                    ch = src[i]
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == "\n":
                        raise Problem(start, "regular expression is never closed")
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    i += 1
                if i >= n:
                    raise Problem(start, "regular expression is never closed")
                i += 1
                while i < n and src[i].isalpha():
                    i += 1
                prev = "re"
                counted += 1
                continue
            prev = "/"
            i += 1
            continue

        # ---- delimiters
        if c in OPENS:
            stack.append((c, line))
            prev = c
            i += 1
            counted += 1
            continue
        if c in PAIRS:
            if not stack:
                err("stray '%s' with nothing open" % c)
            got, at = stack.pop()
            if got != PAIRS[c]:
                err("'%s' closes a '%s' opened on line %d" % (c, got, at))
            prev = c
            i += 1
            counted += 1
            continue

        # ---- words and everything else
        if c.isalpha() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            prev = src[i:j]
            i = j
            continue

        prev = c
        i += 1

    if stack:
        got, at = stack[-1]
        raise Problem(at, "'%s' opened here is never closed" % got)
    if tmpl:
        raise Problem(line, "template literal is never closed")
    return counted


def check_file(path):
    """(ok, engine, message). Both engines must accept the file.

    `node --check` is a real parser and catches things the scanner never
    could, but it has one blind spot that happens to be the exact failure
    this gate exists to catch. For a `.js` path it parses as CommonJS, and the
    CommonJS module wrapper absorbs a stray closing brace: the `}` closes the
    wrapper early, the rest still parses, and node exits 0.

    Measured, on the real web/app.js with a single `}` appended:

        node --check      ok
        balance scanner   line 12065: stray '}' with nothing open

    The same bytes named `.mjs` fail node correctly -- it is the extension,
    not the content. So a build machine WITH node installed was less protected
    than one without, for precisely the front end this gate guards, and the
    claim that node's verdict beats the scanner's is false in this direction.

    Both now run and the verdicts are ANDed. The scanner accepts all 17 files
    of the real front end as they stand, so this costs a working build
    nothing; whichever engine rejects a file is the one named in the message.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as ex:
        return False, "read", str(ex)

    try:
        n = check_text(src, os.path.basename(path))
        balanced, bal_msg = True, "%d delimiters balanced" % n
    except Problem as ex:
        balanced, bal_msg = False, str(ex)

    node = shutil.which("node")
    if not node:
        return balanced, "balance scanner", bal_msg

    p = subprocess.run([node, "--check", path], capture_output=True, text=True)
    if p.returncode != 0:
        first = (p.stderr or p.stdout or "").strip().splitlines()
        return False, "node --check", first[0] if first else "syntax error"
    if not balanced:
        return (False, "balance scanner",
                "%s  (node --check passed it: a .js file is parsed as "
                "CommonJS and the module wrapper hides this)" % bal_msg)
    return True, "node --check + balance scanner", "ok, %s" % bal_msg


def check_html(path):
    """Inline <script> blocks in an .html file.

    index.html carries inline script, and a syntax error there blanks the
    product exactly as surely as one in app.js does, so it goes through the
    same gate. Blocks with a `src` are skipped (they are checked as files) and
    so are non-JavaScript types such as importmap or text/template.
    """
    import re
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            html = fh.read()
    except OSError as ex:
        return False, "read", str(ex)

    n_blocks = 0
    total = 0
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>",
                         html, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        low = attrs.lower()
        if " src=" in low or low.startswith("src="):
            continue
        mt = re.search(r'type\s*=\s*["\']?([^"\'\s>]+)', low)
        if mt and mt.group(1) not in ("text/javascript", "module",
                                      "application/javascript"):
            continue
        line0 = html.count("\n", 0, m.start(2)) + 1
        n_blocks += 1
        try:
            total += check_text(body, os.path.basename(path))
        except Problem as ex:
            return (False, "balance scanner",
                    "inline <script> starting at line %d: %s"
                    % (line0, ex.msg))
    return (True, "balance scanner",
            "%d inline script block(s), %d delimiters balanced"
            % (n_blocks, total))


def check_dir(root, out=sys.stdout):
    """Every .js and .html under root. Returns (ok, [(file, engine, msg, ok)])."""
    rows = []
    ok = True
    for base, _, files in os.walk(root):
        for f in sorted(files):
            if f.endswith(".html"):
                p = os.path.join(base, f)
                good, engine, msg = check_html(p)
                rows.append((os.path.relpath(p, root), engine, msg, good))
                if not good:
                    ok = False
                    out.write("  SYNTAX FAIL  %s  %s\n"
                              % (os.path.relpath(p, root), msg))
                continue
            if not f.endswith(".js"):
                continue
            p = os.path.join(base, f)
            good, engine, msg = check_file(p)
            rows.append((os.path.relpath(p, root), engine, msg, good))
            if not good:
                ok = False
                out.write("  SYNTAX FAIL  %s  (%s)  %s\n"
                          % (os.path.relpath(p, root), engine, msg))
    return ok, rows


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "web")
    if os.path.isfile(target):
        good, engine, msg = check_file(target)
        print("%s  %s  [%s] %s" % ("PASS" if good else "FAIL",
                                   os.path.basename(target), engine, msg))
        sys.exit(0 if good else 1)
    ok, rows = check_dir(target)
    for rel, engine, msg, good in rows:
        print("%s  %-24s [%s] %s" % ("PASS" if good else "FAIL", rel, engine, msg))
    print("\n%d/%d files parse" % (sum(1 for r in rows if r[3]), len(rows)))
    sys.exit(0 if ok else 1)


# --------------------------------------------------------------- strip safety

LOOKUP = None          # compiled on first use, see dangling_ids


def _known_ids(web_dir):
    """Every element id that will exist at runtime: the ones written in the
    markup, plus the ones the JavaScript creates for itself."""
    import io
    import os
    import re
    ids = set()
    html_path = os.path.join(web_dir, "index.html")
    if os.path.isfile(html_path):
        with io.open(html_path, encoding="utf-8", errors="replace") as fh:
            ids |= set(re.findall(r'\bid\s*=\s*["\'](\w[\w:-]*)["\']', fh.read()))
    for fn in sorted(os.listdir(web_dir)):
        if not fn.endswith(".js"):
            continue
        with io.open(os.path.join(web_dir, fn), encoding="utf-8",
                     errors="replace") as fh:
            src = fh.read()
        # el.id = 'x'  |  id="x" inside a template literal  |  setAttribute
        ids |= set(re.findall(r'\.id\s*=\s*["\'](\w[\w:-]*)["\']', src))
        ids |= set(re.findall(r'\bid\s*=\s*\\?["\'](\w[\w:-]*)\\?["\']', src))
        ids |= set(re.findall(
            r'setAttribute\(\s*["\']id["\']\s*,\s*["\'](\w[\w:-]*)["\']', src))
    return ids


def _decomment(src):
    """`src` with comments blanked out, newlines preserved so line numbers
    still count. Without this the gate reports its own explanatory comments as
    findings, and a gate that cries wolf is one you learn to skip."""
    out_chars = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            q = c
            out_chars.append(c)
            i += 1
            while i < n:
                if src[i] == "\\":
                    out_chars.append("  ")
                    i += 2
                    continue
                out_chars.append(src[i])
                if src[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out_chars.append(" " * (j - i))
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out_chars.append("".join(ch if ch == "\n" else " "
                                     for ch in src[i:j]))
            i = j
            continue
        out_chars.append(c)
        i += 1
    return "".join(out_chars)


def dangling_ids(web_dir, out=None):
    """Does the shipped JavaScript reach for an element that does not exist?

    This exists because it has now shipped TWICE, and the second time the check
    written after the first was sitting right here, never called.

    First: gating the emulator buttons stripped `#btnPlay` out of index.html
    while app.js kept `$('#btnPlay').onclick = ...` at top level.
    Second: removing the Roadmap tab deleted its markup by hand and left
    `$('#roadmapReload').onclick = loadRoadmap;` at top level in app.js.

    Both are `null.onclick =`. A throw at MODULE SCOPE aborts the rest of the
    script, so `init()` never runs, no levels load, and the app sits on its
    loading overlay forever. The markup is fine and the syntax is valid, which
    is why every other gate passes it -- `node --check` included.

    The first version of this check only looked at ids removed by @feature
    sentinels, so a hand-deleted tab walked straight past it. This one compares
    against the ids that actually exist in what is about to ship, which covers
    both causes and any future one.

    Run it on the STAGED tree (post-strip), not the source, because the
    release and internal builds ship different markup.

    Returns (ok, fatal, warn). Only `fatal` fails the build: a lookup at column
    0 runs during boot. An indented one is inside a function body and breaks
    only if something calls it -- worth printing, not worth blocking a release.
    """
    global LOOKUP
    import io
    import os
    import re
    out = out or sys.stdout
    if LOOKUP is None:
        LOOKUP = re.compile(
            r"""(?:\$\(\s*['"]\#(\w[\w:-]*)['"]\s*\)"""
            r"""|document\.getElementById\(\s*['"](\w[\w:-]*)['"]\s*\)"""
            r"""|document\.querySelector\(\s*['"]\#(\w[\w:-]*)['"]\s*\))"""
            r"""\s*\.\s*(\w+)""", re.X)

    if not os.path.isfile(os.path.join(web_dir, "index.html")):
        return True, [], []
    known = _known_ids(web_dir)

    fatal, warn = [], []
    for fn in sorted(os.listdir(web_dir)):
        if not fn.endswith(".js"):
            continue
        with io.open(os.path.join(web_dir, fn), encoding="utf-8",
                     errors="replace") as fh:
            src = _decomment(fh.read())
        for m in LOOKUP.finditer(src):
            eid = m.group(1) or m.group(2) or m.group(3)
            if eid in known:
                continue
            line = src.count("\n", 0, m.start()) + 1
            indent = m.start() - (src.rfind("\n", 0, m.start()) + 1)
            row = (fn, line, eid, m.group(4))
            (fatal if indent == 0 else warn).append(row)

    if fatal:
        out.write("  DANGLING ID  this build would hang on its loading "
                  "overlay:\n")
        for fn, line, eid, prop in fatal:
            out.write("     %s:%d sets $('#%s').%s at module scope, but no "
                      "such element ships\n" % (fn, line, eid, prop))
    else:
        out.write("  element ids %d known, none dereferenced at module scope "
                  "without existing%s\n"
                  % (len(known),
                     "" if not warn else "  (%d dead in-function ref%s)"
                     % (len(warn), "" if len(warn) == 1 else "s")))
    for fn, line, eid, prop in warn:
        out.write("     dead ref  %s:%d  $('#%s').%s\n" % (fn, line, eid, prop))
    return not fatal, fatal, warn
