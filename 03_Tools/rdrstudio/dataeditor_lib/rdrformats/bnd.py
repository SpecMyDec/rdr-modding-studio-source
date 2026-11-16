"""BND0: a level's collision ("bound"), the same bytes on the Xbox and the PS2.

    0x00 u32 0x0FFFFF00 (PMT0 starts the same way)   0x04 u8 8, u8 material count, u8 1, u8 0
    0x08 3f box min    0x14 3f box max    0x20 3f centre (the box's middle)    0x2C 3f 0
    0x38 f radius (half the box's diagonal)   0x3C f the farthest vertex from the origin
    0x40 f 0.035 x the box's shortest side    0x44 f 0.3 x that
    0x48 0, 0x4C u32 vertices, 0x50 u32 faces, 0x54 0, 0x58 -1, 0x5C 0, 0x60 -1, 0x64 0
    0x68 vertices (3f each, world space)     0x6C faces (32 bytes each)
    0x70 materials: u32 PMT0 chunk number each (the level's physics materials: 1 = default, wood, rock ...)
    0x74 -1, 0x78 0, 0x7C the octree's root node, 0x80 a bit a face (u32 words; all 0 shipped), 0x84 0, 0x88 0,
    0x8C 2
    face: 3f plane normal, f area whose low byte is the face's material (an index into the list), 8 u16 corners
          (0xFFFF unused; shipped faces have 3 or 4)
    octree node (32 bytes): 3f min, 3f max, u32 offset of a block of 8 child nodes, u32 offset of a leaf
          (both 0: nothing inside). The root is a cube round the box's centre, its side the box's longest; children
          in x, then y, then z order (x fastest), each half its parent.
    leaf: 3f min, 3f max (its faces' box), u16 count, u16 face numbers, padded to 4 bytes (0xCDCD)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = 0x0FFFFF00


@dataclass
class Bound:
    vertices: list = field(default_factory=list)  # (x, y, z) game space (world)
    faces: list = field(default_factory=list)  # (corner indices, material index)
    materials: list = field(default_factory=lambda: [1])  # PMT0 chunk numbers
    normals: list = field(default_factory=list)  # as read (written faces get theirs computed)
    areas: list = field(default_factory=list)
    header: bytes = b''


def read(d: bytes) -> Bound:
    if struct.unpack_from('<I', d, 0)[0] != MAGIC:
        raise ValueError('not a BND0')
    nm = d[5]
    nv, nf = struct.unpack_from('<II', d, 0x4C)
    vo, fo, mo = struct.unpack_from('<3I', d, 0x68)
    b = Bound(header=d[:0x90])
    b.vertices = [struct.unpack_from('<3f', d, vo + 12 * i) for i in range(nv)]
    b.materials = list(struct.unpack_from(f'<{nm}I', d, mo))
    for k in range(nf):
        o = fo + 32 * k
        nx, ny, nz = struct.unpack_from('<3f', d, o)
        area_bits = struct.unpack_from('<I', d, o + 12)[0]
        corners = [c for c in struct.unpack_from('<8H', d, o + 16) if c != 0xFFFF]
        b.faces.append((corners, area_bits & 0xFF))
        b.normals.append((nx, ny, nz))
        b.areas.append(struct.unpack('<f', struct.pack('<I', area_bits & ~0xFF))[0])
    return b


def read_tree(d: bytes):
    """The octree as nested dicts: {'min', 'max', 'children': [8] or None, 'leaf': (min, max, faces) or None}."""
    def node(o):
        lo, hi = struct.unpack_from('<3f', d, o), struct.unpack_from('<3f', d, o + 12)
        kids, leaf = struct.unpack_from('<II', d, o + 24)
        n = {'min': lo, 'max': hi, 'children': None, 'leaf': None, 'at': o}
        if kids:
            n['children'] = [node(kids + 32 * i) for i in range(8)]
        if leaf:
            llo, lhi = struct.unpack_from('<3f', d, leaf), struct.unpack_from('<3f', d, leaf + 12)
            cnt = struct.unpack_from('<H', d, leaf + 24)[0]
            n['leaf'] = (llo, lhi, list(struct.unpack_from(f'<{cnt}H', d, leaf + 26)), leaf)
        return n
    return node(struct.unpack_from('<I', d, 0x7C)[0])
