"""`.ipu` -- the full-screen still images: menus, logos, loading screens.

`.ipu` is named after the PS2's Image Processing Unit, the hardware MPEG-2
macroblock decoder. The game drives it through Sony's libipu: the unpacked
executable carries the library's own version string, `PsIIlibipu  2800`, and
the error text `Need to re-setup libipu since sceMpegGetPicture was aborted`.
So these are Sony-format IPU pictures, not a Rockstar invention.

They are STILLS, despite the container being general enough for motion -- the
magic is `ipum`, "IPU movie", and every file on the disc declares a frame
count of 1. Full-motion video on this disc is `.PSS` (see pss.py), which is a
completely different thing.

Header, 16 bytes:

    0x00  char[4]  "ipum"
    0x04  u32      byte count of everything from 0x08 onward
    0x08  u16      width
    0x0A  u16      height
    0x0C  u32      1 in all 63 distinct files
    0x10  ...      intra-coded bitstream

Confidence, stated plainly because it varies field by field:

* magic, size, width, height -- READ AND CHECKED. `size + 8 == len(file)`
  holds for all 63, and the disc labels its own answer twice:
  `jr_colt45_512x256.ipu` parses as 512x256 and `showdown512x448.ipu` as
  512x448.
* the u32 at 0x0C -- OBSERVED to be 1 everywhere and nothing more. Frame count
  is the natural reading for a container called "ipum", but with no file that
  differs there is nothing on this disc that could distinguish it from a
  version or flags word. Not relied on.

**The pixels are not decoded here.** A start-code scan finds no sequence
header (`00 00 01 B3`) and no picture header (`00 00 01 00`) -- the bitstream
is macroblock data alone, because the picture parameters that a normal MPEG-2
stream states in its headers are instead programmed into the IPU by the game.
That is why renaming one to `.m2v` does not open it. The decoder that supplies
those parameters (recovered from the game's own IPU driver) is
rdrstudio/ipudec.py; measured on this disc, it decodes all 63 distinct files.

This module stays the header layer: identify the files, report their real
dimensions, and hand out the exact bytes.
"""
import struct

MAGIC = b"ipum"
HEADER = 16


class NotIpu(ValueError):
    pass


def parse(data):
    """Header fields, or raise NotIpu. Never guesses at a malformed file."""
    if len(data) < HEADER:
        raise NotIpu("only %d bytes -- too short for a header" % len(data))
    if data[:4] != MAGIC:
        raise NotIpu("magic is %r, not %r" % (data[:4], MAGIC))
    size, w, h, frames = struct.unpack_from("<IHHI", data, 4)
    if size + 8 != len(data):
        # Reported, not corrected. Every file on the disc satisfies this, so a
        # file that does not is telling us the reading is wrong somewhere.
        raise NotIpu("size field %d + 8 != file length %d" % (size, len(data)))
    if not (0 < w <= 2048 and 0 < h <= 2048):
        raise NotIpu("implausible dimensions %dx%d" % (w, h))
    return dict(width=w, height=h, frames=frames, payload_bytes=size,
                bitstream_offset=HEADER,
                bitstream_bytes=len(data) - HEADER)


def is_ipu(data):
    try:
        parse(data)
        return True
    except NotIpu:
        return False


def describe(name, data):
    """A row for the tool's listing. Failure is a row too, not an exception."""
    row = dict(name=name, bytes=len(data), ok=False, error=None,
               width=None, height=None, frames=None, decodable=False)
    try:
        info = parse(data)
    except NotIpu as exc:
        row["error"] = str(exc)
        return row
    # `decodable` was False for every row long after ipudec.py landed; a
    # caller reading the API instead of the UI was told the opposite of what
    # still_png does. Measured: all 63 distinct files on the disc decode.
    row.update(ok=True, width=info["width"], height=info["height"],
               frames=info["frames"], decodable=True)
    row["note"] = ("MPEG-2 intra bitstream -- dimensions are read from the "
                   "header, and still_png serves the decoded picture "
                   "(rdrstudio/ipudec.py)")
    return row
