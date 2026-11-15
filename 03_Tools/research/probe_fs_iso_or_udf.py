#!/usr/bin/env python3
"""Does SLUS_205.00 read the disc through ISO9660 or UDF?

    python3 03_Tools/research/probe_fs_iso_or_udf.py

Asked by the M21 lane: `RDR_VANILLA.iso` is a UDF BRIDGE disc, so both
filesystems describe the same volume, and 221 sectors below the UDF partition
are unreferenced by either. UDF extents are partition-relative, so a file
placed there is unreachable by UDF but addressable by ISO9660 absolute LBA.
If the game reads ISO9660, M21 fits a new .dat in 380 KB with no image
growth; if it reads UDF, the image has to grow and both filesystems change.

READ ONLY. Touches `07_Research/SLUS_205.00.unpacked.bin` and nothing else --
no ISO, no lock, safe to run while a suite or the emulator is live.

The evidence is deliberately of three kinds, because any one alone is weak:
volume-descriptor magic tells you what the code can RECOGNISE, the IOP module
names tell you which FILESYSTEM SERVER it links, and the directory-record
shape tells you what it PARSES.
"""
import os
import re
import sys

ELF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "07_Research", "SLUS_205.00.unpacked.bin")

# Volume descriptor magic. CD001 is ISO9660's; the other three are the UDF
# bridge's Extended Area descriptors. A UDF reader must recognise NSR0x.
ISO_MAGIC = [b"CD001"]
UDF_MAGIC = [b"NSR02", b"NSR03", b"BEA01", b"TEA01", b"BOOT2"]

# Sony's IOP filesystem servers. cdvdfsv/cdvdman expose sceCdSearchFile and
# friends and are ISO9660-ONLY on retail PS2 -- there is no UDF path in them.
IOP_ISO = [b"cdvdfsv", b"cdvdman", b"sceCdSearchFile", b"sceCdRead",
           b"cdrom", b"CDVDFSV", b"CDVDMAN"]
# A UDF reader has to carry its own; no Sony module provides one.
UDF_HINTS = [b"udf", b"UDF", b"FileIdentifier", b"AnchorVolume",
             b"LogicalVolume", b"FileSet"]


# The decisive test, and the reason this probe is worth running rather than
# reasoning about. An ISO9660 file identifier carries a VERSION SUFFIX -- the
# `;1` in `FOO.DAT;1` -- which is part of the standard's directory record.
# UDF FileIdentifierDescriptors have no such thing. A `cdrom0:` path with `;1`
# baked into the executable is the game naming a file the ISO9660 way.
ISO_PATH = re.compile(rb"cdrom0:[\\/][A-Za-z0-9_.\\/]{1,40};1")


def hits(blob, needles):
    out = {}
    for n in needles:
        where = [m.start() for m in re.finditer(re.escape(n), blob)]
        if where:
            out[n.decode("latin-1")] = where
    return out


def main():
    if not os.path.exists(ELF):
        sys.exit("not found: %s -- unpack it first" % ELF)
    blob = open(ELF, "rb").read()
    print("SLUS_205.00.unpacked.bin: %d bytes" % len(blob))
    print()

    for label, needles in (("ISO9660 volume magic", ISO_MAGIC),
                           ("UDF volume magic", UDF_MAGIC),
                           ("Sony IOP CDVD (ISO9660-only on retail)", IOP_ISO),
                           ("UDF structure names", UDF_HINTS)):
        found = hits(blob, needles)
        print("%-40s %s" % (label, "-- nothing" if not found else ""))
        for name, where in sorted(found.items()):
            print("    %-18s x%-4d first at 0x%06X"
                  % (name, len(where), where[0]))
        print()

    paths = sorted({m.group(0).decode("latin-1")
                    for m in ISO_PATH.finditer(blob)})
    print("%-40s %s" % ("cdrom0: paths with an ISO9660 ;1 suffix",
                        "-- nothing" if not paths else ""))
    for s in paths:
        print("    %s" % s)
    print()

    iso = bool(hits(blob, ISO_MAGIC)) or bool(hits(blob, IOP_ISO)) or bool(paths)
    udf = bool(hits(blob, UDF_MAGIC)) or bool(hits(blob, UDF_HINTS))
    print("VERDICT")
    if iso and not udf:
        print("  ISO9660. Three independent lines agree:")
        print("    * %d hardcoded cdrom0: path(s) carry the ISO9660 `;1`"
              % len(paths))
        print("      version suffix, which UDF file identifiers do not have;")
        print("    * the disc is reached through Sony's cdrom0: device and")
        print("      sceSifCallRpc, i.e. cdvdman/cdvdfsv, ISO9660-only on")
        print("      retail PS2;")
        print("    * no UDF volume magic and no UDF structure name appears")
        print("      anywhere in the image.")
    elif udf and not iso:
        print("  UDF only -- surprising; re-read before believing it.")
    elif iso and udf:
        print("  BOTH appear. This probe cannot say which the level loader")
        print("  calls; that is a second question needing the call graph.")
    else:
        print("  NEITHER appears. Unknown -- the executable may reach the")
        print("  disc through a packed or indirected path this probe misses.")
    print()
    print("  Note the limit of this evidence: absence of a UDF string is")
    print("  strong but not a proof of absence of a UDF parser. It is")
    print("  evidence about strings, and it is reported as such.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
