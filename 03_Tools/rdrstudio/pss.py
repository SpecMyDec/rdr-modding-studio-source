"""`.PSS` full-motion video -- Sony's PlayStation Stream, an MPEG-2 Program Stream.

22 files, 187.6 MB, sitting directly in `PSS/` on the ISO rather than inside a
Dave archive. These are the cutscenes: the intro, the titles, the attract loop
and the per-mission story scenes.

They are not a studio format. The first bytes of every one are `00 00 01 BA`,
the MPEG pack-start code, followed by `00 00 01 BB`, the system header -- so a
`.PSS` is an ordinary MPEG-2 Program Stream and any normal player will open it
once it has the right extension. Sony's variant adds private streams for
subtitles and for the PS2's own ADPCM audio, which is why the audio track is
sometimes not what a desktop player expects.

What this module does is read the pack layer to report what is actually inside
one -- which elementary streams, how big, roughly how long -- and hand back the
bytes for export. It does not decode video; there is no reason to reimplement
MPEG-2 when the file can simply be played.
"""
import struct
from typing import Any

PACK_START = b"\x00\x00\x01\xBA"
SYSTEM_HEADER = b"\x00\x00\x01\xBB"
PACKET_PREFIX = b"\x00\x00\x01"

# Stream id ranges in a Program Stream.
STREAM_KINDS = {
    0xBD: "private 1 (PS2 ADPCM audio / subtitles)",
    0xBE: "padding",
    0xBF: "private 2",
}


def _kind(sid):
    if 0xC0 <= sid <= 0xDF:
        return "MPEG audio %d" % (sid - 0xC0)
    if 0xE0 <= sid <= 0xEF:
        return "MPEG video %d" % (sid - 0xE0)
    return STREAM_KINDS.get(sid, "stream 0x%02X" % sid)


def is_pss(head):
    """Cheap gate: a Program Stream starts with a pack header."""
    return head[:4] == PACK_START


def scan(data, limit_packs=None):
    """Walk the pack layer and summarise the elementary streams.

    Returns a dict with the stream inventory, the SCR-derived duration and
    the mux rate. Parsing stops at the first thing that is not a start code,
    which is how a truncated read is handled rather than by guessing.
    """
    if not is_pss(data):
        raise ValueError("not an MPEG Program Stream (no 00 00 01 BA)")

    streams: dict[int, dict[str, Any]] = {}
    i = 0
    packs = 0
    first_scr = last_scr = None
    mux_rate = None
    n = len(data)

    while i + 14 <= n:
        if data[i:i + 4] != PACK_START:
            break
        # MPEG-2 pack header: 4-byte start code, then a 6-byte SCR field with
        # marker bits scattered through it, then a 22-bit mux rate.
        b = data[i + 4:i + 10]
        if (b[0] & 0xC0) != 0x40:
            break                       # MPEG-1 pack, or corrupt
        scr = (((b[0] >> 3) & 0x07) << 30) | ((b[0] & 0x03) << 28) \
            | (b[1] << 20) | (((b[2] >> 3) & 0x1F) << 15) \
            | ((b[2] & 0x03) << 13) | (b[3] << 5) | (b[4] >> 3)
        if first_scr is None:
            first_scr = scr
        last_scr = scr
        if mux_rate is None:
            mr = struct.unpack_from(">I", data, i + 10)[0]
            mux_rate = (mr >> 10) & 0x3FFFFF
        stuffing = data[i + 13] & 0x07
        j = i + 14 + stuffing
        packs += 1

        # Packets inside this pack, until the next pack start.
        while j + 6 <= n and data[j:j + 3] == PACKET_PREFIX:
            sid = data[j + 3]
            if sid == 0xBA:
                break
            plen = struct.unpack_from(">H", data, j + 4)[0]
            if sid != 0xBB:
                rec = streams.setdefault(sid, dict(id=sid, kind=_kind(sid),
                                                   packets=0, bytes=0))
                rec["packets"] += 1
                rec["bytes"] += plen
            j += 6 + plen
        if j <= i:
            break
        i = j
        if limit_packs and packs >= limit_packs:
            break

    secs = None
    if first_scr is not None and last_scr is not None and last_scr > first_scr:
        secs = (last_scr - first_scr) / 90000.0      # SCR ticks at 90 kHz

    return dict(packs=packs, bytes=n, seconds=secs,
                mux_rate_bps=(mux_rate * 400) if mux_rate else None,
                streams=sorted(streams.values(), key=lambda s: s["id"]))


def video_size(data):
    """Width and height from the first MPEG-2 sequence header.

    The sequence header is `00 00 01 B3` followed by 12 bits of width and 12
    of height. Searched for inside the video packets rather than assumed, so a
    file that carries no sequence header reports None instead of a guess.
    """
    k = data.find(b"\x00\x00\x01\xB3")
    if k < 0 or k + 8 > len(data):
        return None
    b = data[k + 4:k + 8]
    w = (b[0] << 4) | (b[1] >> 4)
    h = ((b[1] & 0x0F) << 8) | b[2]
    if not (16 <= w <= 4096 and 16 <= h <= 4096):
        return None
    return (w, h)
