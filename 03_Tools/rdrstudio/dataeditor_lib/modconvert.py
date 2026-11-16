"""A mod for one Red Dead Revolver turned into the same mod for the other (PS2 <-> Xbox).

    python ps2build.py --convert <mods folder or .rdrmod> --to <new mods folder> --platform xbox|ps2
                       --iso <the TARGET game's unedited ISO/folder> --src-iso <the SOURCE game's> [--cache --src-cache]

--platform is the game converted TO. Each file of the mod (laid out as the Data Editor keeps them: Master, the .dat
folders, new archives) is written to the same place in the new folder:
  - a file the same as the source game's own (in its archive, or under a new archive's name: a new level's copied
    files) becomes the target game's own file of that name (.rsc -> .xrsc, ...), renamed as in the mod
  - textures (.tex <-> .xtex): decoded and encoded again, in the format the target game's file of that name has
  - models (.mod <-> .xmod): props are the same on both; skinned ones (characters, weapons) are bone relative on the
    PS2, model space on the Xbox: moved with the character's skeleton (.skel)
  - character bundles (<code>_lods.rsc <-> .xrsc, with .rnt/.xrnt): meshes and textures read and built again
  - level bundles (lvl_<name>.rsc <-> .xrsc): built over the target game's own bundle of that level; the meshes,
    textures and collision the mod changed or added are converted and put where the target's zone files have them
    (the two games number a level's chunks differently), and the mod's own zone files (.znes <-> .xznes) and bound
    files renumbered
  - everything else (text, the files the games share) copied as it is
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import struct
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ps2build  # noqa: E402

# the extensions of the files the games keep in their own formats: PS2 -> Xbox
EXT = {'.rsc': '.xrsc', '.rnt': '.xrnt', '.znes': '.xznes', '.tex': '.xtex', '.mod': '.xmod', '.shadows': '.xshadows',
       '.lsh': '.xlsh', '.film': '.xfilm', '.hud': '.xhud', '.mmb': '.xmmb', '.rain': '.xrain', '.flares': '.xflares',
       '.alpha': '.xalpha'}
BACK = {v: k for k, v in EXT.items()}


class Game:
    """One game's unedited files: its archives, which archive holds each name, a file's bytes."""

    def __init__(self, path, cache, xbox):
        self.xbox = xbox
        self.cache = cache
        ns = argparse.Namespace(iso=path, cache=cache)
        os.makedirs(cache, exist_ok=True)
        self.disc = ps2build.open_game(path)
        self.index, self.dats, _stamp, self.holders = ps2build.load_index(ns, self.disc)
        self.archives = self.index['archives']
        self._data = {}

    def data(self, archive):
        if archive not in self._data:
            if len(self._data) > 6:
                self._data.pop(next(iter(self._data)))
            self._data[archive] = self.disc.read(self.dats[archive])
        return self._data[archive]

    def has(self, archive, name):
        return archive in self.archives and name.lower() in self.holders and archive in self.holders[name.lower()]

    def get(self, archive, name):
        """A file's bytes from an archive (None if it has none of that name)."""
        if archive not in self.archives:
            return None
        if archive not in self.holders.get(name.lower().rsplit('/', 1)[-1], ()):
            return None
        return ps2build.read_entry(self.data(archive), name)[1]

    def find(self, name, prefer=()):
        """(archive, bytes) of a file of that name: in the preferred archives first, else any."""
        held = self.holders.get(name.lower(), set())
        for a in list(prefer) + sorted(held, key=lambda x: (x.count('/'), x)):
            if a in held:
                return a, ps2build.read_entry(self.data(a), name)[1]
        return None, None


def target_name(name, to_xbox):
    stem, ext = os.path.splitext(name)
    m = EXT if to_xbox else BACK
    return stem + m.get(ext.lower(), ext)


def is_text(data):
    return b'\0' not in data[:4096]


# ---------------------------------------------------------------------------------------------------------------
# textures

def convert_texture(data, to_xbox, like=None):
    """A .tex as .xtex or back: decoded, encoded again in the format of the target game's own file (like), else as
    the game picks (palette types stay palettes; DXT to 256 colours on the PS2)."""
    from rdrformats import tex
    t = tex.read(data)
    img = t.mips[0]
    kind, mips, flags = t.type, len(t.mips) > 1, t.flags
    if like:
        lw, lh, lt, llv = struct.unpack_from('<4H', like)
        kind, mips, flags = lt, llv > 1, like[8:14]
        if (lw, lh) != (t.width, t.height):
            img = ps2build.resize_to(img, lw, lh)
    elif not to_xbox and kind in (tex.DXT1, tex.DXT3, tex.DXT5):
        kind = None  # the PS2 has no DXT: 256 colours (with alpha when it has some)
    if kind not in (tex.P8, tex.PA8, tex.P4, tex.PA4, tex.DXT1, tex.DXT5, tex.RGBA8888, tex.RGB888):
        kind = None
    img = match_style(img, kind, tex.read(like).mips[0] if like else None)
    return tex.encode(img, kind, mips=mips, flags=flags, platform='xbox' if to_xbox else 'ps2')


def match_style(img, kind, ref):
    """Some effects are drawn differently on the two games: the PS2's dust, smoke and glows are white with the shape
    in the alpha, the Xbox's the shape in the colour (no alpha, DXT1). The picture moved to the target's way:
      - target without alpha (DXT1, P8, P4): the alpha multiplied into the colour (black where it was clear)
      - target white with the shape in the alpha (its colour all near one value): the brightness as the alpha"""
    import numpy as np
    from rdrformats import tex
    a = img[..., 3].astype(np.float32)
    if a.min() < 250 and kind in (tex.DXT1, tex.P8, tex.P4):
        out = img.copy()
        out[..., :3] = (img[..., :3] * (a[..., None] / 255.0) + 0.5).astype(np.uint8)
        out[..., 3] = 255
        return out
    if ref is not None and kind in (tex.PA8, tex.PA4, tex.DXT5) and a.min() >= 250:
        rgb = ref[..., :3].astype(np.float32)
        if ref[..., 3].min() < 250 and rgb.std(axis=(0, 1)).max() < 12:  # the target's: one colour, shape in alpha
            lum = img[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
            out = np.empty_like(img)
            out[..., :3] = rgb.mean(axis=(0, 1)).astype(np.uint8)
            out[..., 3] = np.clip(lum + 0.5, 0, 255).astype(np.uint8)
            return out
    return img


# ---------------------------------------------------------------------------------------------------------------
# skeletons and models

def skeleton_for(code, mods_files, games):
    """A character's bone positions (model space) from its .skel: the mod's, else either game's."""
    from rdrformats import skel
    name = code.lower() + '.skel'
    text = None
    for k, path in mods_files.items():
        if k.rsplit('/', 1)[-1] == name:
            text = open(path, encoding='latin-1').read()
            break
    if text is None:
        for g in games:
            _a, raw = g.find(name)
            if raw:
                text = raw.decode('latin-1')
                break
    if text is None:
        return None
    return [b.position for b in skel.read(text).bones]


def model_code(stem):
    code = re.split(r'_lod|_nolods|_lods', stem.lower())[0]
    if code.startswith('hat_'):
        code = code[4:]
    return code.replace('_face', '')


def rebase(model, positions, to_xbox, version=None):
    """A skinned model moved between bone relative (PS2) and model space (Xbox) with its skeleton's bone
    positions. Models of one bone (hats, faces: head relative on both) are left as they are."""
    from rdrformats import builder
    if positions is None or (model.header_totals and model.header_totals[2] <= 1):
        return model
    if to_xbox:  # bone relative -> model space
        tris, mats = builder.triangles_of(model, positions)
        opts = builder.BuildOptions(version=version or model.version, bone_relative=False,
                                    bone_count=model.header_totals[2] if model.header_totals else None)
    else:
        tris, mats = builder.triangles_of(model)
        opts = builder.BuildOptions(version=version or model.version, bone_relative=True, bone_positions=positions,
                                    bone_count=model.header_totals[2] if model.header_totals else None)
    return builder.build(tris, mats, opts)


def convert_model(data, name, to_xbox, mods_files, games, notes):
    from rdrformats import mod
    m = mod.read(data)
    if not m.header_totals or m.header_totals[2] <= 1:
        return data  # a prop (or head relative hat/face): the same on both games
    pos = skeleton_for(model_code(os.path.splitext(name)[0]), mods_files, games)
    if pos is None:
        notes.append(f'{name}: no skeleton ({model_code(os.path.splitext(name)[0])}.skel) found: copied unmoved')
        return data
    return mod.write(rebase(m, pos, to_xbox))


# ---------------------------------------------------------------------------------------------------------------
# character bundles

def read_bundle(data, names_data, from_xbox):
    """(textures [(name, rgba rows, as both games' writers take them)], meshes [(name, model as read, bones)]) of a character bundle."""
    import numpy as np
    from rdrformats import rsc, ps2rsc
    chunks = rsc.read_chunks(data)
    names = rsc.read_names(names_data) if names_data else []
    tex_names, textures = {}, []
    for i, c in enumerate(chunks, 1):
        if c.tag != 'TEX0':
            continue
        if from_xbox:
            name, rgba = rsc.read_texture(chunks, i)
        else:
            name, rgba = ps2rsc.texture_of(chunks, i)
        tex_names[i] = name
        if rgba is not None:
            textures.append((name, rgba))
    meshes = []
    for tag, num, name in names:
        if tag != 'MOD0' or not 0 < num <= len(chunks):
            continue
        if from_xbox:
            mesh = rsc.read_mod0(chunks[num - 1].data)
            meshes.append((name, rsc.mod0_to_model(mesh, tex_names)))
        else:
            mesh = ps2rsc.read_mod0(chunks[num - 1].data)
            meshes.append((name, ps2rsc.to_model(mesh, tex_names)))
    return textures, meshes


def fit_ps2(rgba, side=256):
    """A texture the PS2 can hold: power of two sides, at most 256 (4 MB of video memory), at least 16."""
    h, w = rgba.shape[:2]

    def p2(n):
        k = 16
        while k * 2 <= n and k < side:
            k *= 2
        return k
    return ps2build.resize_to(rgba, p2(w), p2(h))


def convert_bundle(data, names_data, stem, to_xbox, mods_files, games, notes):
    """A character bundle (<code>_lods) and its names for the other game: (bundle, names)."""
    import numpy as np
    from rdrformats import ps2rsc, rscwrite
    textures, meshes = read_bundle(data, names_data, not to_xbox)
    pos = skeleton_for(model_code(stem), mods_files, games)
    if pos is None:
        notes.append(f'{stem}: no skeleton ({model_code(stem)}.skel) found: its meshes are not moved between bone '
                     'relative (PS2) and model space (Xbox)')
    out = []
    for name, m in meshes:
        out.append((name, rebase(m, pos, to_xbox, version='2.12')))
    if to_xbox:
        return rscwrite.build(textures, out)  # both games' readers and writers keep rows the same way round
    return ps2rsc.build([(n, fit_ps2(img)) for n, img in textures], out)


# ---------------------------------------------------------------------------------------------------------------
# level bundles

ZONE_RE = re.compile(r'(node\s+(\S+)\s*\{\s*(?:numLods\s+(\d+)\s*)?resourceIndex\s+)(-?\d+)(\s*resourceIndexXbox\s+)(-?\d+)')


def zone_numbers(texts, xbox):
    """{chunk number: node name (lods: name_lodN)} from zone files' texts."""
    out = {}
    for text in texts:
        for m in ZONE_RE.finditer(text):
            num = int(m[6] if xbox else m[4])
            if num <= 0:
                continue
            for k in range(int(m[3] or 1)):
                out.setdefault(num + k, m[2] if k == 0 else f'{m[2]}_lod{k + 1}')
    return out


def renumber_zone(text, mapping, from_xbox, to_xbox):
    """A zone file's node numbers moved to the other game's: the source field read, the target field written, the
    other set to 0 (as each game's own files have them)."""
    def sub(m):
        src = int(m[6] if from_xbox else m[4])
        tgt = mapping.get(src, 0) if src > 0 else 0
        ps2, xb = (0, tgt) if to_xbox else (tgt, 0)
        return f'{m[1]}{ps2}{m[5]}{xb}'
    return ZONE_RE.sub(sub, text)


def level_textures(chunks, xbox):
    from rdrformats import rsc, ps2rsc
    import numpy as np
    out = {}
    for i, c in enumerate(chunks, 1):
        if c.tag != 'TEX0':
            continue
        try:
            if xbox:
                name, rgba = rsc.read_texture(chunks, i)
            else:
                name, rgba = ps2rsc.texture_of(chunks, i)
        except Exception:
            continue
        out[i] = (name, rgba)
    return out


def level_model(chunks, num, xbox, tex_names):
    from rdrformats import rsc, ps2rsc
    if xbox:
        return rsc.mod0_to_model(rsc.read_mod0(chunks[num - 1].data), tex_names)
    return ps2rsc.to_model(ps2rsc.read_mod0(chunks[num - 1].data), tex_names)


def convert_level(stem, data, src_clean, tgt_clean, src_zone_texts, tgt_zone_texts, mod_zone_texts, to_xbox, notes):
    """A changed level bundle for the other game, over the target's own bundle of the level: (bundle, {source chunk
    number: target chunk number} for the mod's zone and bound files)."""
    import numpy as np
    from rdrformats import rsc, ps2rsc, rscwrite, tex as texfmt
    from_xbox = not to_xbox
    S, S0, T0 = rsc.read_chunks(data), rsc.read_chunks(src_clean), rsc.read_chunks(tgt_clean)
    s_names = zone_numbers(src_zone_texts + mod_zone_texts, from_xbox)
    t_by_name = {n: k for k, n in zone_numbers(tgt_zone_texts, to_xbox).items()}
    s_tex = level_textures(S, from_xbox)
    s_tex_names = {i: v[0] for i, v in s_tex.items()}
    s0_tex = {i: c.data for i, c in enumerate(S0, 1) if c.tag in ('TEX0', 'MIP0', 'PAL0')}
    tgt_tex_nums = {}
    at = 0x24 if to_xbox else 0xA0
    for i, c in enumerate(T0, 1):
        if c.tag == 'TEX0' and b'\0' in c.data[at:]:
            tgt_tex_nums.setdefault(c.data[at:c.data.index(b'\0', at)].decode('latin-1').lower(), i)

    def pal_mip(c):
        return struct.unpack_from('<II', c.data, 0x14 if from_xbox else 0x90)
    mapping, replace_meshes, new_meshes, new_src, replace_tex, new_tex, bounds = {}, {}, [], [], {}, [], []
    n0 = len(S0)
    # textures the mod changed (any of a TEX0's three chunks) or added
    for i, (name, rgba) in s_tex.items():
        pal, mip = pal_mip(S[i - 1])
        changed = i > n0 or any(k > n0 or S[k - 1].data != S0[k - 1].data for k in (i, pal, mip))
        if not changed or rgba is None:
            continue
        num = tgt_tex_nums.get(name.lower())
        img = rgba if to_xbox else fit_ps2(rgba)
        if num:
            replace_tex[num] = img
        else:
            new_tex.append((name, img))
    for i, c in enumerate(S, 1):
        if c.tag == 'MOD0':
            if i <= n0 and c.data == S0[i - 1].data:
                name = s_names.get(i)
                if name in t_by_name:
                    mapping[i] = t_by_name[name]
                continue
            model = level_model(S, i, from_xbox, s_tex_names)
            name = s_names.get(i)
            if i <= n0 and name in t_by_name:
                replace_meshes[t_by_name[name]] = model
                mapping[i] = t_by_name[name]
            elif i <= n0:
                notes.append(f'{stem}: changed mesh chunk {i} ({name or "no zone names it"}) has no place in the '
                             f'target game\'s bundle: left out')
            else:
                new_meshes.append(model)
                new_src.append(i)
        elif c.tag == 'BND0':
            if i <= n0 and c.data == S0[i - 1].data:
                mapping.setdefault(i, i)  # the games' bound files share numbers
                continue
            bounds.append((i, c.data))
    if to_xbox:
        out, added = rscwrite.build_level(T0, replace_meshes, new_meshes, new_tex, replace_tex, tgt_tex_nums)
        align = rscwrite.ALIGN
    else:
        out, added = ps2rsc.build_level(T0, replace_meshes, new_meshes, new_tex, replace_tex, tgt_tex_nums)
        align = ps2rsc.PS2_ALIGN
    mapping.update(zip(new_src, added))
    if bounds:
        chunks = [(c.tag, c.data) for c in rsc.read_chunks(out)]
        for i, body in bounds:
            if i <= n0 and i <= len(chunks) and chunks[i - 1][0] == 'BND0':
                chunks[i - 1] = ('BND0', body)  # a changed shell, where both games have it
                mapping[i] = i
            else:
                chunks.append(('BND0', body))
                mapping[i] = len(chunks)
        out = rscwrite.write_bundle(chunks, align)
    return out, mapping


# ---------------------------------------------------------------------------------------------------------------

def remake_new_levels(fresh, src, tgt, dst_dir, to_xbox, dave, notes):
    """New archives (New .dat's levels and Showdown maps) made again on the target game as New .dat makes them there:
    the target's shared Showdown files, its template map's own files and the story level's place, all the target's
    own (the two games name some of these files differently: the Xbox's fonts are berryliumx / damagedx, its sound
    banks .xbd / .xhd, ...). Returns the archives remade; their unchanged files are not converted one by one."""
    import contextlib
    import io
    remade = set()
    done_names = set()
    for arch, folder in sorted(fresh.items()):
        group, base = arch.rsplit('/', 1)
        stem = os.path.splitext(base)[0]
        showdown = group in ('lvls_m2', 'lvls_m4')
        name = stem[:-1] if showdown else stem
        if (group if not showdown else 'm', name) in done_names:
            remade.add(arch)
            continue
        bundle = next((f for f in os.listdir(folder) if f.lower() in (f'lvl_{stem}.rsc', f'lvl_{stem}.xrsc')), None)
        got = renamed_original(bundle.lower(), f'lvl_{stem}', open(os.path.join(folder, bundle), 'rb').read(), src, arch) if bundle else None
        if got is None:
            # an edited level bundle: the place it was made from by its other files (the .lvl)
            lvl = next((f for f in os.listdir(folder) if f.lower() == f'lvl_{stem}.lvl'), None)
            got = renamed_original(lvl.lower(), f'lvl_{stem}', open(os.path.join(folder, lvl), 'rb').read(), src, arch) if lvl else None
        if got is None:
            notes.append(f'{arch}: not made from a level of the game: its files are converted one by one')
            continue
        sarch, _sname, old = got
        template = old[4:]
        if showdown and sarch.startswith(('lvls_m2/', 'lvls_m4/')):
            template = template.rstrip('24')
        ns = argparse.Namespace(new_level='showdown' if showdown else 'sng', name=name, template=template, title=None,
                                subtitle=None, replace=True, mods=dst_dir, cache=tgt.cache, dave=dave)
        if not showdown:
            old_dir = os.path.join(dst_dir, *arch.split('/'))
            if os.path.isdir(old_dir):
                shutil.rmtree(old_dir)
        ps2build.XBOX[0] = to_xbox
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                ps2build.new_level(ns, tgt.disc, tgt.index, tgt.dats)
        except Exception as e:
            notes.append(f'{arch}: could not be made again on the target game ({e}): its files are converted one by one')
            continue
        done_names.add((group if not showdown else 'm', name))
        remade.add(arch)
        made = [l for l in out.getvalue().splitlines() if l.startswith('Made ')]
        print(f'  {arch}: made again from the {"Xbox" if to_xbox else "PS2"} game\'s own files (from {old[4:]}): ' +
              '; '.join(m[5:].split(':')[0] for m in made))
    return remade


def convert(src_dir, dst_dir, src, tgt, to_xbox, log=print, only_changed=False, dave=None):
    """Every file of a mods folder converted into dst_dir. Returns (counts, notes)."""
    loose, targeted, every, master = ps2build.mod_files(src_dir, list(src.archives) + list(
        ps2build.new_archives(src_dir, src.archives)))
    fresh = ps2build.new_archives(src_dir, src.archives)
    counts = {'game file': 0, 'renamed game file': 0, 'texture': 0, 'model': 0, 'bundle': 0, 'level': 0, 'zone': 0,
              'merged': 0, 'copied': 0, 'left out': 0}
    notes = []
    remade = remake_new_levels(fresh, src, tgt, dst_dir, to_xbox, dave or HERE, notes) if fresh else set()
    folder_of = {}
    for archive, items in targeted.items():
        for inner, path in items:
            folder_of[path] = archive
    level_maps = {}  # (archive, stem) -> mapping
    done = set()
    # level bundles first: their chunk numbers renumber the mod's zone and bound files
    order = sorted(every.items(), key=lambda kv: (0 if re.match(r'.*lvl_[^/]*\.x?rsc$', kv[0]) else 1, kv[0]))
    for key, path in order:
        if path in done:
            continue
        rel_dir = os.path.dirname(key)
        name = os.path.basename(path)
        archive = folder_of.get(path)
        tname = target_name(name, to_xbox)
        out_path = os.path.join(dst_dir, *rel_dir.split('/'), tname) if rel_dir else os.path.join(dst_dir, tname)
        data = open(path, 'rb').read()
        low, ext = name.lower(), os.path.splitext(name)[1].lower()
        new_arch = archive in fresh if archive else False

        def write(b, kind):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, 'wb') as f:
                f.write(b)
            counts[kind] += 1
            done.add(path)
        # 1. the source game's own file: the target's own
        prefer = [archive] if archive else []
        if archive and not new_arch:
            clean = src.get(archive, name)
        else:
            _a, clean = src.find(name, prefer) if not new_arch else (None, None)
        if clean is not None and clean == data:
            if only_changed and archive and not new_arch:
                counts['unchanged'] = counts.get('unchanged', 0) + 1  # the target game has its own already
                done.add(path)
                continue
            _a, tdata = tgt.find(tname, prefer)
            if tdata is not None:
                write(tdata, 'game file')
                continue
        # 2. a new archive's copy of a game file under the new level's name (New .dat)
        if new_arch:
            stem = 'lvl_' + os.path.splitext(archive.rsplit('/', 1)[-1])[0].lower()
            got = renamed_original(low, stem, data, src, archive)
            if got and archive in remade:
                counts['from the new level'] = counts.get('from the new level', 0) + 1  # the target's own is there
                done.add(path)
                continue
            if got:
                sarch, sname, old = got
                tdata = tgt.get(sarch, target_name(sname, to_xbox)) or tgt.find(target_name(sname, to_xbox), [sarch])[1]
                if tdata is not None:
                    if is_text(tdata):
                        pat = re.compile(re.escape(old) + r'(?=[_.]|$)', re.I)
                        tdata = pat.sub(stem, tdata.decode('latin-1')).encode('latin-1')
                    write(tdata, 'renamed game file')
                    continue
        # 3. converted by kind
        try:
            if ext in ('.tex', '.xtex'):
                like = tgt.find(tname, prefer)[1]
                write(convert_texture(data, to_xbox, like), 'texture')
            elif ext in ('.mod', '.xmod'):
                write(convert_model(data, name, to_xbox, every, [src, tgt], notes), 'model')
            elif ext in ('.rsc', '.xrsc') and low.startswith('lvl_'):
                stem = os.path.splitext(low)[0]
                b, mapping = level_convert(stem, path, data, archive, new_arch, every, src, tgt, to_xbox, notes)
                if b is None:
                    counts['left out'] += 1
                    done.add(path)
                    continue
                write(b, 'level')
                level_maps[(rel_dir, stem)] = mapping
            elif ext in ('.rsc', '.xrsc'):
                stem = os.path.splitext(name)[0]
                rnt = os.path.join(os.path.dirname(path), stem + ('.xrnt' if not to_xbox else '.rnt'))
                names = open(rnt, 'rb').read() if os.path.isfile(rnt) else src.find(stem + ('.xrnt' if not to_xbox else '.rnt'), prefer)[1]
                bundle, nm = convert_bundle(data, names, stem, to_xbox, every, [src, tgt], notes)
                write(bundle, 'bundle')
                npath = os.path.join(os.path.dirname(out_path), stem + ('.xrnt' if to_xbox else '.rnt'))
                with open(npath, 'wb') as f:
                    f.write(nm)
                if os.path.isfile(rnt):
                    done.add(rnt)
            elif ext in ('.rnt', '.xrnt'):
                stem = os.path.splitext(name)[0]
                if os.path.isfile(os.path.join(os.path.dirname(path), stem + ('.xrsc' if not to_xbox else '.rsc'))):
                    continue  # written with its bundle
                notes.append(f'{key}: names without their bundle: left out')
                counts['left out'] += 1
            elif ext in ('.znes', '.xznes'):
                text = data.decode('latin-1')
                stem = next((s for (d, s) in level_maps if d == rel_dir and low.startswith(s)), None)
                mapping = level_maps.get((rel_dir, stem)) if stem else None
                if mapping is None:
                    mapping = zone_map_from_game(low, archive, src, tgt, to_xbox)
                write(renumber_zone(text, mapping, not to_xbox, to_xbox).encode('latin-1'), 'zone')
            elif ext == '.bnds':
                text = data.decode('latin-1')
                stem = next((s for (d, s) in level_maps if d == rel_dir and low.startswith(s)), None)
                mapping = level_maps.get((rel_dir, stem)) if stem else None
                if mapping:
                    text = re.sub(r'(resourceIndex\s+)(-?\d+)(\s*resourceIndexXbox\s+)(-?\d+)',
                                  lambda m: f'{m[1]}{mapping.get(int(m[2]), int(m[2]))}{m[3]}{mapping.get(int(m[2]), int(m[2]))}', text)
                write(text.encode('latin-1'), 'copied')
            else:
                if is_text(data) and clean is not None:
                    # a changed text file whose original is not the same on both games: the mod's changes laid
                    # over the target game's own (its lines the source game lacks kept)
                    _ta, tclean = tgt.find(tname, prefer)
                    if tclean is not None and tclean != clean:
                        merged, ok = merge_text(clean, data, tclean)
                        if not ok:
                            notes.append(f'{key}: the two games\' originals differ and some changes did not fit: '
                                         'check it against the target game\'s file')
                        write(merged, 'merged')
                        continue
                write(data, 'copied')
        except Exception as e:  # one file does not stop the rest
            notes.append(f'{key}: not converted ({type(e).__name__}: {e}): left out')
            counts['left out'] += 1
            done.add(path)
    # Master's settings
    modes = os.path.join(src_dir, ps2build.MODES_FILE)
    if os.path.isfile(modes):
        lines = [l.split('\t') for l in open(modes, encoding='utf-8').read().splitlines() if '\t' in l]
        os.makedirs(dst_dir, exist_ok=True)
        with open(os.path.join(dst_dir, ps2build.MODES_FILE), 'w', encoding='utf-8') as f:
            for n, m in lines:
                f.write(target_name(n, to_xbox) + '\t' + m + '\n')
    return counts, notes


def merge_text(base, mine, theirs):
    """mine's changes to base (line by line) applied to theirs, a version of base with changes of its own:
    (bytes, every change placed)."""
    import difflib
    b, m, t = (x.decode('latin-1').splitlines(True) for x in (base, mine, theirs))
    # base line -> theirs line, through their equal runs
    where = {}
    for i, j, n in difflib.SequenceMatcher(None, b, t, autojunk=False).get_matching_blocks():
        for k in range(n):
            where[i + k] = j + k
    out = list(t)
    ok = True
    edits = [op for op in difflib.SequenceMatcher(None, b, m, autojunk=False).get_opcodes() if op[0] != 'equal']
    for tag, i1, i2, j1, j2 in reversed(edits):
        if i1 < i2:
            if not all(k in where for k in range(i1, i2)):
                ok = False
                continue
            t1, t2 = where[i1], where[i2 - 1] + 1
        else:  # an insert: after the line before it
            if i1 == 0:
                t1 = t2 = 0
            elif i1 - 1 in where:
                t1 = t2 = where[i1 - 1] + 1
            else:
                ok = False
                continue
        out[t1:t2] = m[j1:j2]
    return ''.join(out).encode('latin-1'), ok


def renamed_original(low, stem, data, src, archive):
    """For a file of a new archive (New .dat): (archive, name, level name) of the game file it is a copy of, the
    level's name swapped (lvl_mymap2_x -> lvl_ranch2_x, or a story level's lvl_tomb_x), else None."""
    group = archive.rsplit('/', 1)[0] + '/'
    cands = []
    for a in src.archives:
        if not a.startswith('lvls_'):
            continue
        for k in src.index['archives'][a]['names']:
            kl = k.lower()
            if kl.startswith('lvl_') and kl.endswith('.lvl'):
                cands.append((0 if a.startswith(group) else 1, a, kl[:-4]))
    if stem not in low:
        # a file every map shares (no level name in it): the same name in an archive of its folder
        for _p, a, _s in sorted(set(cands)):
            raw = src.get(a, low)
            if raw is not None and raw == data:
                return a, low, stem
        return None
    for _p, a, s in sorted(set(cands)):
        n = low.replace(stem, s)
        raw = src.get(a, n)
        if raw is None:
            continue
        if raw == data:
            return a, n, s
        if is_text(raw):
            pat = re.compile(re.escape(s) + r'(?=[_.]|$)', re.I)
            if pat.sub(stem, raw.decode('latin-1')).encode('latin-1') == data:
                return a, n, s
    return None


def zone_map_from_game(low, archive, src, tgt, to_xbox):
    """For a changed zone file with no converted level beside it: source chunk numbers to the target's, by the node
    names in the two games' zone files of that level."""
    stem = re.match(r'(lvl_[a-z0-9]+)', low)
    if not stem or not archive:
        return {}
    stem = stem[1]
    zs = '.xznes' if not to_xbox else '.znes'
    zt = '.xznes' if to_xbox else '.znes'
    st = [src.get(archive, n).decode('latin-1') for n in src.index['archives'].get(archive, {}).get('names', [])
          if n.lower().startswith(stem) and n.lower().endswith(zs)]
    tt = [tgt.get(archive, n).decode('latin-1') for n in tgt.index['archives'].get(archive, {}).get('names', [])
          if n.lower().startswith(stem) and n.lower().endswith(zt)]
    s = zone_numbers(st, not to_xbox)
    t = {n: k for k, n in zone_numbers(tt, to_xbox).items()}
    return {k: t[n] for k, n in s.items() if n in t}


def level_convert(stem, path, data, archive, new_arch, every, src, tgt, to_xbox, notes):
    sx, tx = ('.xrsc', '.rsc') if not to_xbox else ('.rsc', '.xrsc')
    zs, zt = ('.xznes', '.znes') if not to_xbox else ('.znes', '.xznes')
    sarch, sstem = archive, stem
    if new_arch:
        # the source bundle of the same place: the one sharing the most chunks
        from rdrformats import rsc
        S = rsc.read_chunks(data)
        best = (0, None, None)
        for a in src.archives:
            if not a.startswith('lvls_'):
                continue
            for k in src.index['archives'][a]['names']:
                kl = k.lower()
                if kl.startswith('lvl_') and kl.endswith(sx) and '_' not in kl[4:-len(sx)]:
                    raw = src.get(a, kl)
                    try:
                        C = rsc.read_chunks(raw)
                    except ValueError:
                        continue
                    same = sum(1 for x, y in zip(S, C) if x.data == y.data)
                    if same > best[0]:
                        best = (same, a, kl[:-len(sx)])
        if not best[1]:
            notes.append(f'{stem}: no level of the game is its base: left out')
            return None, None
        sarch, sstem = best[1], best[2]
    src_clean = src.get(sarch, sstem + sx) if sarch else None
    tgt_clean = tgt.get(sarch, sstem + tx) if sarch else None
    if src_clean is None or tgt_clean is None:
        notes.append(f'{stem}: the games\' own {sstem} bundles were not found: left out')
        return None, None
    if src_clean == data:
        return tgt_clean, {}

    def zones(game, ext, xbox_side):
        return [game.get(sarch, n).decode('latin-1') for n in game.index['archives'][sarch]['names']
                if n.lower().startswith(sstem + '_') and n.lower().endswith(ext) or n.lower() == sstem + ext]
    mod_zones = [open(p, encoding='latin-1').read() for k, p in every.items()
                 if os.path.dirname(p) == os.path.dirname(path) and k.endswith(zs)]
    return convert_level(stem, data, src_clean, tgt_clean, zones(src, zs, not to_xbox), zones(tgt, zt, to_xbox),
                         mod_zones, to_xbox, notes)


def main(a):
    """--convert, from ps2build.py's arguments."""
    to_xbox = a.platform == 'xbox'
    if not a.src_iso:
        print('--src-iso: the unedited game the mod was made for (PS2 ISO, or the Xbox game folder)')
        return 1
    src_dir = a.convert
    tmp = None
    if os.path.isfile(src_dir):  # a .rdrmod (zip): its files
        tmp = tempfile.mkdtemp(prefix='rdrconvert_')
        with zipfile.ZipFile(src_dir) as z:
            z.extractall(tmp)
        inner = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
        src_dir = os.path.join(tmp, 'RDRMods') if os.path.isdir(os.path.join(tmp, 'RDRMods')) else tmp
    print(f'Converting {a.convert} to the {"Xbox" if to_xbox else "PS2"} game into {a.to}')
    print('Reading the two games (the first time indexes them)...')
    src_cache = a.src_cache or os.path.join(HERE, '_ps2cache' if to_xbox else '_xboxcache')
    src = Game(a.src_iso, src_cache, not to_xbox)
    tgt = Game(a.iso, a.cache, to_xbox)
    counts, notes = convert(src_dir, a.to, src, tgt, to_xbox, dave=a.dave or HERE)
    for n in notes:
        print('  ' + n)
    print('Converted: ' + ', '.join(f'{v} {k}{"s" if v != 1 and k not in ("left out", "copied", "merged", "unchanged", "from the new level") else ""}'
                                      for k, v in counts.items() if v))
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if counts['left out'] == 0 else 0


# ---------------------------------------------------------------------------------------------------------------
# dropping archives and files into the mods folder (the Data Editor's .dat folders)

PS2_EXTS = ('.rsc', '.rnt', '.znes', '.tex', '.mod')
XBOX_EXTS = ('.xrsc', '.xrnt', '.xznes', '.xtex', '.xmod')


def which_game(folder):
    """'ps2', 'xbox' or None (nothing that tells): by the files only one game has."""
    p = x = 0
    for _r, _d, fs in os.walk(folder):
        for f in fs:
            e = os.path.splitext(f)[1].lower()
            p += e in PS2_EXTS
            x += e in XBOX_EXTS
    return None if p == x == 0 else ('xbox' if x > p else 'ps2')


def archive_place(name, into, game):
    """Where a dropped archive (tomb.dat) goes in the mods folder: the game's archive of that name (in the folder it
    was dropped on when several have it), else a new archive in the folder dropped on (lvls_m2 ...) or by its
    player count (mymap2.dat -> lvls_m2, mymap4.dat -> lvls_m4, else lvls_sng)."""
    name = name.lower()
    into = (into or '').replace('\\', '/').strip('/').lower()
    hits = [a for a in game.archives if a.rsplit('/', 1)[-1] == name]
    if into.endswith('.dat') and into.rsplit('/', 1)[-1] == name:
        return into
    if len(hits) == 1:
        return hits[0]
    group = into.split('/')[0] if into.startswith('lvls_') else ''
    for h in hits:
        if h.startswith(group + '/'):
            return h
    if hits:
        return hits[0]
    if not group:
        stem = os.path.splitext(name)[0]
        group = 'lvls_m2' if stem.endswith('2') else 'lvls_m4' if stem.endswith('4') else 'lvls_sng'
    return f'{group}/{name}'


def import_main(a):
    """--import PATHS --into FOLDER: .dat archives (files, or folders of their files) and loose files into the mods
    folder, made the game's (--platform) own when they are the other game's. An archive the game has keeps only the
    files that differ from the game's own."""
    to_xbox = a.platform == 'xbox'
    me = 'xbox' if to_xbox else 'ps2'
    tgt = Game(a.iso, a.cache, to_xbox)
    tmp = tempfile.mkdtemp(prefix='rdrimport_')
    dave = None
    into = (a.into or '').replace('\\', '/').strip('/')
    placed = []
    try:
        for path in a.import_:
            path = path.rstrip('\\/')
            base = os.path.basename(path)
            if os.path.isfile(path) and base.lower().endswith(('.dat', '.zip')):
                rel = archive_place(base, into, tgt)
                if dave is None:
                    dave = ps2build.load_dave(a.dave)
                    ps2build.quiet_prompts(dave)
                dst = os.path.join(tmp, *rel.split('/'))
                print(f'{base}: extracting...')
                dave.read_dave(path, dst)
                placed.append(rel)
            elif os.path.isdir(path) and (base.lower().endswith('.dat') or getattr(a, 'as_name', None)):
                rel = archive_place(base if base.lower().endswith('.dat') else a.as_name, into, tgt)
                shutil.copytree(path, os.path.join(tmp, *rel.split('/')), dirs_exist_ok=True)
                placed.append(rel)
            elif os.path.isdir(path):
                shutil.copytree(path, os.path.join(tmp, *into.split('/'), base) if into else os.path.join(tmp, base),
                                dirs_exist_ok=True)
            elif os.path.isfile(path):
                d = os.path.join(tmp, *into.split('/')) if into else tmp
                os.makedirs(d, exist_ok=True)
                shutil.copy2(path, d)
        for rel in placed:
            print(f'  {rel.rsplit("/", 1)[-1]} -> {rel}' + ('' if rel in tgt.archives else ' (a new archive)'))
        game = which_game(tmp)
        if game and game != me:
            other = 'PS2' if to_xbox else 'Xbox'
            if not a.src_iso or not os.path.exists(a.src_iso):
                print(f'These are {other} files: set the {other} game in Settings (switch games at the bottom right) so '
                      'they can be converted')
                return 1
            print(f'These are {other} files: converting them to the {"Xbox" if to_xbox else "PS2"} game\'s...')
            src = Game(a.src_iso, a.src_cache or os.path.join(HERE, '_xboxcache' if not to_xbox else '_ps2cache'),
                       not to_xbox)
            counts, notes = convert(tmp, a.mods, src, tgt, to_xbox, only_changed=True, dave=a.dave or HERE)
            for n in notes:
                print('  ' + n)
            print('Added: ' + ', '.join(f'{v} {k}' for k, v in counts.items() if v))
            return 0
        # the game's own files: copied, an existing archive's unchanged ones left out
        added = same = 0
        for root, _d, fs in os.walk(tmp):
            rel_dir = os.path.relpath(root, tmp).replace(os.sep, '/')
            rel_dir = '' if rel_dir == '.' else rel_dir
            arch = next((r for r in placed if rel_dir == r or rel_dir.startswith(r + '/')), None)
            if arch is None and rel_dir.lower() in tgt.archives:
                arch = rel_dir.lower()
            for f in fs:
                src_path = os.path.join(root, f)
                if arch in tgt.archives:
                    inner = (rel_dir[len(arch):].strip('/') + '/' + f).strip('/')
                    clean = tgt.get(arch, inner) or tgt.get(arch, f)
                    if clean is not None and clean == open(src_path, 'rb').read():
                        same += 1
                        continue
                out = os.path.join(a.mods, *rel_dir.split('/'), f) if rel_dir else os.path.join(a.mods, f)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(src_path, out)
                added += 1
        print(f'Added {added} file(s)' + (f'; {same} the same as the game\'s own left out' if same else ''))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
