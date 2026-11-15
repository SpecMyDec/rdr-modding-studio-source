"""Repacking a Dave archive: close the slack that sits between its files.

Patching in place can only ever *replace* bytes. Repacking is what turns an
archive's largest free run from a couple of KB into 211 KB (FRONT.DAT) or
12.95 MB (SWAT.DAT), so it is the difference between editing content and
adding it.

The archive keeps its exact total size -- it is a fixed-size file inside the
ISO and the ISO has no gaps between its files -- so the reclaimed bytes
collect at the END, which is exactly where new content goes.

    from rdrstudio import repack
    p = repack.plan(dv)
    print(p.moved, "of", len(p.blocks), "blocks,", p.reclaimed, "bytes freed")
    dv.backing.write_at(0, repack.apply(dv, p))

Whether a repack is CORRECT and whether it is SAFE are different questions,
and only the emulator answers the second: SWAT.DAT's freezing layout passed
every offline check, including reading all 47,115 files back byte-identically,
because the TOC was never the route that broke. `dave.repack_status()` carries
what is known per archive. This module refuses outright on "breaks-the-game"
and honours `dave.SECTOR_PINNED`; anything else is the caller's judgement.

Lifted out of scripts/test_repack_boot.py, which is where the working
implementation was proven (FRONT.DAT, STREAMS.DAT, BANKS.DAT, PUPSHOW.DAT
booted; SWAT.DAT booted once logo01.pck was pinned).

One behaviour changed on the way in, and it is not cosmetic: zero-length
entries are now planned. The old packer dropped them and then died with a
KeyError on 61 of the 66 archives -- every level archive on the disc. Those
five that worked are exactly the five that have ever been repack-tested. No
level archive has been repacked yet, by anything, so nothing here says a
repacked level LOADS; scripts/verify_repack.py only says one rebuilds and
reads back. See the comment on `span` below.
"""

import struct

from .dave import SECTOR, SECTOR_PINNED, repack_status

# MEASURED, not assumed. An earlier packer said "every original offset is
# sector aligned" and packed at 2048 -- which made the archive BIGGER than the
# original, because it is not true. FRONT.DAT's real alignment histogram is 236
# offsets at 2048 but 38 at 32, 22 at 64, 12 at 128 and 6 at 256; BANKS.DAT
# goes down to 16. The 2004 packer aligned to a sector when that happened to be
# free and to 16 bytes otherwise, so 16 is the alignment the game demonstrably
# accepts.
#
# Preserving each file's original alignment instead was tried as a safety
# measure and is not one: it reclaims 0-1 KB per archive instead of
# 211 KB - 13 MB, because the slack IS the alignment padding. Preserve
# everything and you have repacked nothing.
ALIGN = 16


class RepackRefused(Exception):
    """This archive will not be repacked, and the message says why."""


class Block:
    """One run of stored bytes, and where a repack would put it.

    A block is not a file. Identical files share a single offset in the
    original (the 2004 packer deduplicated them), so one block can be pointed
    at by several TOC entries -- and that sharing is preserved, because
    breaking it would inflate the archive and defeat the point.
    """

    __slots__ = ("src", "dst", "size", "pinned")

    def __init__(self, src, dst, size, pinned=False):
        self.src = src
        self.dst = dst
        self.size = size          # longest span any entry stores here
        self.pinned = pinned

    @property
    def moved(self):
        return self.dst != self.src

    def __repr__(self):
        return "<Block 0x%X -> 0x%X %d bytes%s>" % (
            self.src, self.dst, self.size, " PINNED" if self.pinned else "")


class Plan:
    """A complete before/after layout. Compute once, inspect, then apply."""

    def __init__(self, label, size, entry_count, blocks, end, keep_first,
                 ignored_pins):
        self.label = label
        self.size = size                  # archive total, unchanged by apply()
        self.entry_count = entry_count
        self.blocks = blocks
        self.end = end                    # first free byte after packing
        self.keep_first = keep_first
        self.ignored_pins = ignored_pins  # (name, offset) matched but not aligned
        self.reclaimed = size - end
        self.moved = sum(1 for b in blocks if b.moved)
        self.pinned = [b for b in blocks if b.pinned]

    def __len__(self):
        return len(self.blocks)

    def __iter__(self):
        return iter(self.blocks)

    def summary(self):
        """JSON-able, for the web tool."""
        return dict(
            archive=self.label,
            status=repack_status(self.label),
            size=self.size,
            entries=self.entry_count,
            blocks=len(self.blocks),
            moved=self.moved,
            reclaimed=self.reclaimed,
            free_from=self.end,
            keep_first=self.keep_first,
            pinned=[dict(src=b.src, dst=b.dst, size=b.size)
                    for b in self.pinned],
            ignored_pins=[dict(name=n, offset=o) for n, o in self.ignored_pins],
        )

    def __repr__(self):
        return "<Plan %s %d/%d blocks moved, %d bytes freed>" % (
            self.label, self.moved, len(self.blocks), self.reclaimed)


def _pinned_offsets(dv, pin):
    """Which offsets must land on a 2048-byte sector, and which asks we ignored.

    A name is only pinned if the file ALREADY sat on a sector boundary.
    Pinning one that never did would be inventing a constraint, and the whole
    point of the pin list is that it is empirical: moving logo01.pck 624 bytes
    off its sector, with 47,100 of SWAT.DAT's 47,101 other blocks untouched,
    freezes the game at the title screen while every file still reads back
    byte-identically -- because those files are DMA'd off the disc rather than
    read through the TOC.
    """
    want = {n.lower() for n in pin}
    offsets, ignored = set(), []
    for e in dv.entries:
        base = e.name.rsplit("/", 1)[-1].lower()
        if base not in want:
            continue
        if e.offset % SECTOR == 0:
            offsets.add(e.offset)
        else:
            ignored.append((e.name, e.offset))
    return offsets, ignored


def plan(dv, keep_first=0, pin=None, allow_unsafe=False):
    """Work out where every block would go. Returns a `Plan`; writes nothing.

    Flat 16-byte packing, except that anything named in `dave.SECTOR_PINNED`
    keeps a 2048-byte alignment. Pass `pin` to widen that set for an
    experiment -- it defaults to SECTOR_PINNED, which is "the ones found so
    far", not a proven-complete list: the bisect that found logo01.pck returns
    the HIGHEST offending block, so a sector-sensitive file lower down in
    SWAT.DAT would still be unknown.

    `keep_first` leaves the first N blocks exactly where they are and packs
    only what follows, for bisecting WITHIN an archive. N=0 is a full repack;
    N >= the block count is a no-op.

    `allow_unsafe` is for research scripts that need to reproduce a known-bad
    layout on a throwaway disc. The web tool must never pass it.
    """
    status = repack_status(dv.label)
    if status == "breaks-the-game" and not allow_unsafe:
        raise RepackRefused(
            "%s: repacking this archive breaks the game (status %r). It was "
            "tested and the game does not boot. Patch files in place instead."
            % (dv.label, status))

    if pin is None:
        pin = SECTOR_PINNED
    pinned, ignored = _pinned_offsets(dv, pin)

    # Longest span stored at each offset, computed ONCE. This used to be a
    # `max(... for e in dv.entries if e.offset == off)` inside the placement
    # loop, which is O(files x blocks) -- fine for FRONT.DAT's 317 and about
    # 2.2 billion comparisons for SWAT.DAT's 47,115.
    #
    # The default is -1, not 0, and that is not a detail. 62 of the 66 archives
    # contain a zero-length entry -- `cr_diamond_0.arfx` is in 61 of them --
    # and in every level archive its offset is used by no other file. With a
    # default of 0 those offsets never enter the dict, the TOC rewrite then
    # raises KeyError, and scripts/test_repack_all.py reported that as "SKIP".
    # Which means the run that claimed to repack EVERY archive quietly repacked
    # five of them: FRONT, BANKS, STREAMS, PUPSHOW, SWAT. Not one level.
    span: dict[int, int] = {}
    for e in dv.entries:
        n = e.size_comp or e.size_full
        if n > span.get(e.offset, -1):
            span[e.offset] = n

    # Nothing may be written over the header, TOC or name block. All 66
    # archives on the retail disc already start their first block exactly at
    # this boundary, so the clamp never fires there -- it is here for an
    # archive that has been modified.
    floor = SECTOR + dv.info_size + dv.name_size
    order = sorted(span)
    if not order:
        return Plan(dv.label, dv.size, len(dv.entries), [], floor, keep_first,
                    ignored)

    blocks = []
    cur = max(order[0], floor)
    for i, off in enumerate(order):
        n = span[off]
        if i < keep_first:
            # Untouched. `cur` must still advance past it, or the first moved
            # block would be written on top of a block that never moved.
            blocks.append(Block(off, off, n, off in pinned))
            cur = max(cur, off + (n + ALIGN - 1) // ALIGN * ALIGN)
            continue
        a = SECTOR if off in pinned else ALIGN
        cur = (cur + a - 1) // a * a
        blocks.append(Block(off, cur, n, off in pinned))
        # A zero-length block consumes nothing, so it shares its destination
        # with whatever comes next. It keeps a valid in-range offset, which is
        # all a 0-byte file needs, but its patch slot becomes 0 -- those files
        # cannot be grown in place after a repack. Use the reclaimed tail.
        cur += (n + ALIGN - 1) // ALIGN * ALIGN

    if cur > dv.size:
        raise RepackRefused(
            "%s: the packed layout needs %d bytes and the archive is %d. A "
            "repack must never change the archive's size." % (dv.label, cur,
                                                              dv.size))
    return Plan(dv.label, dv.size, len(dv.entries), blocks, cur, keep_first,
                ignored)


def headroom(dv, **kw):
    """Bytes a repack would free, all of them in one run at the end.

    Raises RepackRefused for the same archives `plan` does, so a caller that
    only wants a number to display should check `dave.repack_status()` first.
    """
    return plan(dv, **kw).reclaimed


def apply(dv, layout, raw=None):
    """Build the repacked archive. Returns bytes of EXACTLY the original size.

    Writes nothing -- handing the result to `dv.backing.write_at(0, ...)` is
    the caller's decision, and on an ISO backing that is a real disc edit.

    `raw` is the original archive bytes; read from the backing if omitted
    (290 MB for SWAT.DAT, so pass it in if you already have it).
    """
    if layout.size != dv.size or layout.entry_count != len(dv.entries):
        raise RepackRefused(
            "plan does not match this archive: plan is %d bytes / %d entries, "
            "archive is %d / %d" % (layout.size, layout.entry_count, dv.size,
                                    len(dv.entries)))
    if raw is None:
        raw = dv.backing.read_at(0, dv.size)
    if len(raw) != dv.size:
        raise RepackRefused("read %d bytes of %s, expected %d"
                            % (len(raw), dv.label, dv.size))

    remap = {b.src: b.dst for b in layout.blocks}

    out = bytearray(len(raw))
    head = SECTOR + dv.info_size + dv.name_size
    out[0:head] = raw[0:head]

    for e in dv.entries:
        if e.offset not in remap:
            raise RepackRefused(
                "%s: entry %r sits at 0x%X, which the plan does not cover -- "
                "the plan was built from a different parse of this archive"
                % (dv.label, e.name, e.offset))
        struct.pack_into("<IIII", out, SECTOR + e.index * 16,
                         e.name_offset, remap[e.offset],
                         e.size_full, e.size_comp)

    for b in layout.blocks:
        # A slice assignment past the end EXTENDS a bytearray rather than
        # failing, which is how the first attempt at this silently produced an
        # archive 132 KB larger than the one it was replacing.
        if b.dst + b.size > len(out):
            raise RepackRefused(
                "%s: block from 0x%X would run past the archive end (0x%X + %d "
                "> %d)" % (dv.label, b.src, b.dst, b.size, len(out)))
        out[b.dst:b.dst + b.size] = raw[b.src:b.src + b.size]

    if len(out) != dv.size:
        raise RepackRefused("%s: repack changed the archive size (%d -> %d)"
                            % (dv.label, dv.size, len(out)))
    return bytes(out)
