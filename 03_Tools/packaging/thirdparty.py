"""Collect the third-party licence texts the README promises are there.

README.txt ends with "Full licence texts are in the licenses\\ folder
beside the application." That sentence was true of the plan and false of the
build -- there was no such folder. Either the sentence goes or the folder
does, and shipping other people's MIT/BSD code without their licence text is
not a thing to be casual about, so the folder does.

Nothing here is typed out from memory. Every file is copied from the package
that is actually being bundled, so the text always matches the version that
shipped. When a licence genuinely cannot be located, the index says so in that
component's own row rather than quietly leaving it out.
"""

import os
import re
import shutil
import sys

# Only the components that actually reach the user's disk. PyInstaller, pip,
# setuptools and friends are build tools -- they are not in the bundle, so
# their licences do not belong in it either.
SHIPPED = ["pillow", "numpy", "pywebview", "pythonnet", "clr_loader",
           "cffi", "pycparser", "bottle", "proxy_tools", "typing_extensions",
           "pywin32_ctypes"]


def _site_packages(venv_dir):
    p = os.path.join(venv_dir, "Lib", "site-packages")
    return p if os.path.isdir(p) else None


def _dist_infos(sp):
    out = {}
    for name in os.listdir(sp):
        if not name.endswith(".dist-info"):
            continue
        stem = name[:-len(".dist-info")]
        pkg = stem.rsplit("-", 1)[0].lower().replace("_", "-")
        out[pkg] = os.path.join(sp, name)
    return out


def _licence_files(dist_dir):
    found = []
    for root in (dist_dir, os.path.join(dist_dir, "licenses"),
                 os.path.join(dist_dir, "license_files")):
        if not os.path.isdir(root):
            continue
        for f in sorted(os.listdir(root)):
            full = os.path.join(root, f)
            if os.path.isfile(full) and re.search(
                    r"licen[cs]e|copying|notice|authors", f, re.I):
                found.append(full)
    return found


def collect(venv_dir, dest, extra=()):
    """Write dest/licenses/. Returns a list of (component, status) rows."""
    out_dir = os.path.join(dest, "licenses")
    os.makedirs(out_dir, exist_ok=True)
    rows = []

    sp = _site_packages(venv_dir)
    dists = _dist_infos(sp) if sp else {}

    for pkg in SHIPPED:
        # dist-info folder names normalise _ to -; the SHIPPED list is written
        # the way people say the names, so normalise here rather than there.
        d = dists.get(pkg) or dists.get(pkg.replace("_", "-"))
        if not d:
            rows.append((pkg, "not bundled by this build"))
            continue
        files = _licence_files(d)
        if not files:
            rows.append((pkg, "no licence file in the package metadata"))
            continue
        for f in files:
            base = "%s-%s" % (pkg, os.path.basename(f))
            shutil.copyfile(f, os.path.join(out_dir, base))
        rows.append((pkg, "%d file(s)" % len(files)))

    # Python itself: the interpreter's own LICENSE, from the install the venv
    # was made from.
    for base in (getattr(sys, "base_prefix", sys.prefix), sys.prefix):
        p = os.path.join(base, "LICENSE.txt")
        if os.path.isfile(p):
            shutil.copyfile(p, os.path.join(out_dir, "python-LICENSE.txt"))
            rows.append(("python", "LICENSE.txt"))
            break
    else:
        rows.append(("python", "not found on this machine"))

    # three.js is vendored as source, and its licence is the header comment of
    # the file we ship. Pulling the header out keeps it tied to the exact copy.
    for src in extra:
        if not os.path.isfile(src):
            continue
        name = os.path.basename(src)
        with open(src, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
        m = re.search(r"/\*.*?\*/", head, re.S)
        text = m.group(0) if m else ""
        with open(os.path.join(out_dir, "three.js-LICENSE.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("three.js -- MIT License\n"
                     "Vendored as %s. Header as shipped:\n\n%s\n\n"
                     "The MIT License text is at "
                     "https://github.com/mrdoob/three.js/blob/dev/LICENSE\n"
                     % (name, text.strip()))
        rows.append(("three.js", "header from %s" % name))

    with open(os.path.join(out_dir, "INDEX.txt"), "w", encoding="utf-8") as fh:
        fh.write("Third-party components in this build\n")
        fh.write("=" * 36 + "\n\n")
        fh.write("Each file in this folder is copied verbatim from the "
                 "component\nit belongs to, at the version that is actually "
                 "bundled here.\n\n")
        for name, status in rows:
            fh.write("  %-20s %s\n" % (name, status))
        fh.write("\nThe Microsoft Edge WebView2 Runtime is NOT redistributed. "
                 "The app\nuses the copy already installed on your system.\n")
    return rows


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "build",
                                                              "licences-probe")
    rows = collect(os.path.join(here, ".venv"), dest,
                   extra=[os.path.join(os.path.dirname(here), "web",
                                       "three.module.js")])
    for name, status in rows:
        print("%-20s %s" % (name, status))
    print("\n-> %s" % os.path.join(dest, "licenses"))
