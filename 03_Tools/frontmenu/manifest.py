"""The record of what the last intentional build put on a disc.

`verify_modlist_row.py` used to take its expected mod names from the disc it
was checking (`names = MODS or read_names_disc(mod)`), which made it a
consistency check wearing a correctness check's name: every copy of the page
agreed with a list read off one of those copies, so overwriting the names
returned 19/19 green on a page nobody chose. See CHECKS-THAT-LIE.md entry 21,
and rdr-app who caught it.

This file is the expectation the disc cannot supply. The builder writes it; the
gate reads it and compares the disc against it.

WHAT IT PROVES, EXACTLY -- do not let this grow in the retelling. The record is
written from the same names the builder wrote to the disc, so its authority is
the build invocation. It proves **the disc still matches the last intentional
build**. It catches a hand edit, a second tool writing that page, and a copy
left half-rewritten. It CANNOT catch a build run with the wrong names: then the
disc and the record are wrong together and the gate stays green.

NO FALLBACK. If the record is missing, unreadable, or describes a different
disc, the caller must REFUSE -- not read the names off the disc instead. That
fallback is the original bug restored, and restored on exactly the occasions
the record existed to cover. A false red costs an hour; a false green put a
half-built disc in front of the developer twice. Reddening on a stale record is the
direction we want to fail in, so do not 'fix' it by softening it.
"""

import io
import os
import time

NAME = "modlist-names.txt"


class Problem(Exception):
    """Raised inside this module, never past `read`."""


def path(root):
    return os.path.join(root, "02_Modded", NAME)


def write(root, disc, names, seeded=None):
    """Record the names this build put on `disc`. Returns the path written.

    `seeded` is for the ONE case where no build wrote the record: an existing
    disc that predates this file. Pass the provenance of the names and it goes
    in the file, because a seeded record is weaker than a built one and the
    next reader is entitled to know which they have.
    """
    p = path(root)
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(u"# written by build_modlist_row.py -- the names the last\n")
        fh.write(u"# intentional build put on this disc. the gate compares the\n")
        fh.write(u"# disc against this record and REFUSES if it is missing or\n")
        fh.write(u"# names a different disc. it does not fall back to reading\n")
        fh.write(u"# the names off the disc: see CHECKS-THAT-LIE.md entry 21.\n")
        fh.write(u"disc: %s\n" % os.path.basename(disc))
        fh.write(u"built: %s\n" % time.strftime("%Y-%m-%dT%H:%M:%S"))
        if seeded:
            fh.write(u"# SEEDED, not written by a build: %s\n" % seeded)
            fh.write(u"# the next real build overwrites this and the record\n")
            fh.write(u"# becomes independent of the disc.\n")
        for n in names:
            fh.write(u"name: %s\n" % n)
    return p


def read(root, expect_disc=None):
    """(ok, record, message). `record` is (disc, names) when ok.

    Returns a verdict rather than raising, like the other checkers here --
    `jsgate.py` is the pattern. `message` always says what to do about it.
    """
    p = path(root)
    try:
        if not os.path.exists(p):
            raise Problem(
                "no names record at %s -- the disc cannot supply its own "
                "expectation (CHECKS-THAT-LIE 21). rebuild with "
                "frontmenu/build_modlist_row.py <disc.iso> <names...>, or "
                "pass the names on the command line" % NAME)

        disc, names = None, []
        for line in io.open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("disc:"):
                disc = line[5:].strip()
            elif line.startswith("name:"):
                names.append(line[5:].strip())

        if not disc:
            raise Problem("%s has no 'disc:' line -- cannot tell which disc it "
                          "describes" % NAME)
        if not names:
            raise Problem("%s names no mods -- an empty record is not an "
                          "expectation" % NAME)

        if expect_disc is not None:
            want = os.path.basename(expect_disc)
            if want.lower() != disc.lower():
                raise Problem(
                    "%s describes %s, but the disc named is %s. refusing "
                    "rather than guessing which is stale -- rebuild, or name "
                    "the disc the record describes" % (NAME, disc, want))

        return True, (disc, names), "%d name(s) recorded for %s" % (len(names), disc)
    except Problem as ex:
        return False, None, "%s" % ex
    except Exception as ex:                                   # noqa: BLE001
        return False, None, "could not read %s: %s" % (p, ex)
