"""Red Dead Revolver skeletons (.skel), text:

    Version: 101
    NumBones 52
    bone root {
        offset 0.000000 1.114487 0.016990
        transX
        ...                 (the channels the bone animates)
        bone spine_01 {
            ...
        }
    }

A bone's offset is from its parent, in the game's Y up space; the bind pose has no rotation, so a bone's position is
its offsets summed down the chain. Bones are numbered in file order, which is what a model's matrices index.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_CHANNELS = ('transX', 'transY', 'transZ', 'rotX', 'rotY', 'rotZ')


@dataclass
class Bone:
    name: str
    parent: int  # index, -1 for a root
    offset: tuple  # from the parent
    channels: list[str] = field(default_factory=lambda: list(DEFAULT_CHANNELS))
    position: tuple = (0.0, 0.0, 0.0)  # summed, model space


@dataclass
class Skeleton:
    version: str = '101'
    bones: list[Bone] = field(default_factory=list)

    def index(self, name: str) -> int:
        for i, b in enumerate(self.bones):
            if b.name == name:
                return i
        return -1

    def children(self, i: int) -> list[int]:
        return [j for j, b in enumerate(self.bones) if b.parent == i]


def read(text: str) -> Skeleton:
    sk = Skeleton()
    stack: list[int] = []
    for raw in text.splitlines():
        words = raw.split()
        if not words:
            continue
        head = words[0]
        if head == 'Version:' and len(words) > 1:
            sk.version = words[1]
        elif head == 'bone' and len(words) > 1:
            parent = stack[-1] if stack else -1
            sk.bones.append(Bone(words[1], parent, (0.0, 0.0, 0.0), channels=[]))
            stack.append(len(sk.bones) - 1)
        elif head == 'offset' and stack and len(words) >= 4:
            sk.bones[stack[-1]].offset = tuple(float(w) for w in words[1:4])
        elif head == '}' and stack:
            stack.pop()
        elif stack and head not in ('NumBones', '{'):
            sk.bones[stack[-1]].channels.append(head)
    for b in sk.bones:
        p = sk.bones[b.parent].position if b.parent >= 0 else (0.0, 0.0, 0.0)
        b.position = tuple(p[k] + b.offset[k] for k in range(3))
    return sk


def write(sk: Skeleton) -> str:
    out = [f'Version: {sk.version}', f'NumBones {len(sk.bones)}']

    def emit(i: int, depth: int):
        b = sk.bones[i]
        tab = '\t' * depth
        out.append(f'{tab}bone {b.name} {{')
        out.append(f'{tab}\t' + 'offset ' + ' '.join(f'{v:.6f}' for v in b.offset))
        for ch in b.channels:
            out.append(f'{tab}\t{ch} ')
        for c in sk.children(i):
            emit(c, depth + 1)
        out.append(f'{tab}}}')

    for i, b in enumerate(sk.bones):
        if b.parent < 0:
            emit(i, 0)
    return '\r\n'.join(out) + '\r\n'


def load(path) -> Skeleton:
    with open(path, 'r', errors='replace') as f:
        return read(f.read())
