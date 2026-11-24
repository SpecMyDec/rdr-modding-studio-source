"""Converted files: every file a build makes the other game's (PS2 -> Xbox in the Xbox build, Xbox -> PS2 in an import
or the PS2 build) kept in the Workbench under "Converted files", and found again there the next time the same input
comes up, so a build converts each file once.

    <workbench>/Converted files/To Xbox/<archive>/<file>    the latest conversion of each file, to look at or reuse
    <workbench>/Converted files/To PS2/...
    <workbench>/Converted files/.store/<sha1>               the outputs by content
    <workbench>/Converted files/.index-<direction>.json     {input key: [[output name, sha1], ...]}

An input key is the input's name and the SHA-1 of its bytes, plus whatever else its conversion depends on (a level's
files convert together: their key covers all of them).
"""
from __future__ import annotations

import hashlib
import json
import os

FOLDER = 'Converted files'


def sha1(data):
    return hashlib.sha1(data).hexdigest()


CONVERTER = 2  # 2: PS2 meshes made into Xbox strips (triangle lists were read as one strip: spikes), 2026-11-17


class ConvCache:
    def __init__(self, workbench, direction):
        self.root = os.path.join(workbench, FOLDER)
        self.direction = direction                              # 'To Xbox' / 'To PS2'
        self.store = os.path.join(self.root, '.store')
        self.index_path = os.path.join(self.root, '.index-%s.json' % direction.lower().replace(' ', '-'))
        try:
            self.index = json.load(open(self.index_path, encoding='utf-8'))
        except (OSError, ValueError):
            self.index = {}
        self.dirty = False
        self.hits = 0
        self.stored = 0

    @staticmethod
    def key(name, data, context=''):
        # CONVERTER: raised when a converter's output changes, so no earlier (wrong) conversion is reused
        return '%s|%s|%s|c%d' % (name.lower(), sha1(data), context, CONVERTER)

    def get(self, key):
        """{output name: bytes} of an earlier conversion of this input, or None."""
        rec = self.index.get(key)
        if not rec:
            return None
        out = {}
        for name, h in rec:
            p = os.path.join(self.store, h[:2], h)
            try:
                out[name] = open(p, 'rb').read()
            except OSError:
                return None
        self.hits += 1
        return out

    def put(self, key, outputs, where=''):
        """outputs {output name (a path inside its archive): bytes}; `where` the folder it shows under (its archive)."""
        rec = []
        for name, data in outputs.items():
            h = sha1(data)
            p = os.path.join(self.store, h[:2], h)
            if not os.path.isfile(p):
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, 'wb') as f:
                    f.write(data)
            show = os.path.join(self.root, self.direction, *(where.split('/') if where else ['by name']),
                                *name.split('/'))
            try:
                os.makedirs(os.path.dirname(show), exist_ok=True)
                with open(show, 'wb') as f:
                    f.write(data)
            except OSError:
                pass
            rec.append([name, h])
        self.index[key] = rec
        self.dirty = True
        self.stored += 1

    def save(self):
        if not self.dirty:
            return
        os.makedirs(self.root, exist_ok=True)
        tmp = self.index_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.index, f)
        os.replace(tmp, self.index_path)
        self.dirty = False
