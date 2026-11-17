"""Mugshots on the Showdown end-of-match screen (swat.dat mprewards.pck), for characters it has none for.

How the screen picks one (read from the file, 2026-11-16):
  * each player card (sprite 131, placed as p0..p3) has a clip "boss" (sprite 114): frame 0 a plain square (tinted
    with the team colour -- what a character with no mugshot shows), frames 1..37 a 128 x 128 mugshot each;
  * the screen's first script (the root's frame-0 action) builds two arrays of character codes, chars0 (22: "none",
    aka .. mok) and chars1 (16: mrk .. zzt), with `new Array(...)` from pushed constants; the card code finds the
    player's code in chars0 (frame x) or chars1 (frame y + 22) and calls boss.gotoAndStop(frame + 1).
  Characters the arrays miss -- a new one, and about a dozen of the game's own (elt, gwk, mdg, mom, mon, mrd, msk, shp,
  tcb, tck, twg, ydr, kti ...) -- show the square.

What this does, without moving anything in the file (every addition is appended; pointers to it are repointed):
  * the first script, copied to the end of the file with the new codes added to chars1 (and to its constant pool),
    the root frame pointed at the copy;
  * the boss clip given a frame per new code (a longer frame table at the end), each placing a new picture at the
    depth every mugshot frame uses (1), so it replaces whatever the frame before showed.
The picture is the character's own mp_ portrait (the shipped mugshots are the same pictures as the mp_ portraits).
"""
import struct

from .pck import PckError

FILE = 'mprewards_new3.pck' # the screen the game loads ("Rewards_new3"); mprewards.pck and the rest are unused versions
CHARS1_FRAME0 = 22          # chars1[y] shows frame y + 22 (gotoAndStop(y + 23))


def clip(s):
    """The mugshot clip's object id: the sprite placed as "bossList" (mprewards_new3: in faceCard > boss), else as
    "boss" (the older mprewards.pck)."""
    found = {}
    for i, (t, o) in s.p.objects.items():
        if t != 2:
            continue
        for fr in s.timeline(o):
            for c in fr:
                if c.get('op') == 'place' and c.get('name') in ('bossList', 'boss') and c.get('char') is not None:
                    found.setdefault(c['name'], c['char'])
    got = found.get('bossList', found.get('boss'))
    if got is None:
        raise PckError('the screen has no mugshot clip (bossList / boss)')
    return got


def _actions(d, start):
    """[(offset, op, body offset, body length)] from `start` to the end action (included)."""
    out, p = [], start
    while p < len(d):
        op = d[p]
        if op == 0:
            out.append((p, 0, p + 1, 0))
            return out
        if op >= 0x80:
            ln = struct.unpack_from('<H', d, p + 2 - 1)[0]
            out.append((p, op, p + 3, ln))
            p += 3 + ln
        else:
            out.append((p, op, p + 1, 0))
            p += 1
    raise PckError('the script runs past the end of the file')


def _pool(d, body, ln):
    n = struct.unpack_from('<H', d, body)[0]
    strs, p = [], body + 2
    for _ in range(n):
        e = d.index(0, p)
        strs.append(bytes(d[p:e]).decode('latin-1'))
        p = e + 1
    return strs


def _push_items(d, body, ln):
    """[(type, raw bytes)] of a push action."""
    out, q, end = [], body, body + ln
    sizes = {1: 4, 2: 0, 3: 0, 4: 1, 5: 1, 6: 8, 7: 4, 8: 1, 9: 2}
    while q < end:
        t = d[q]
        if t == 0:
            e = d.index(0, q + 1)
            out.append((t, bytes(d[q + 1:e + 1])))
            q = e + 1
        else:
            n = sizes[t]
            out.append((t, bytes(d[q + 1:q + 1 + n])))
            q += 1 + n
    return out


def _item_value(item, pool):
    t, b = item
    if t == 8:
        return pool[b[0]]
    if t == 9:
        return pool[struct.unpack('<H', b)[0]]
    if t == 0:
        return b[:-1].decode('latin-1')
    if t == 7:
        return struct.unpack('<i', b)[0]
    if t == 1:
        return struct.unpack('<f', b)[0]
    return None


def _pool_ref(i):
    return (8, bytes([i])) if i < 256 else (9, struct.pack('<H', i))


def arrays(s):
    """(chars0, chars1, action offset) as the screen's first script builds them (s: a pckscene.Scene)."""
    d = s.d
    fr = s.at(s.u32(12))
    cmd = s.at(s.u32(fr + 4))
    if cmd is None or d[cmd] != 3:
        raise PckError('the screen\'s first command is not its script')
    acts = _actions(d, cmd + 16)
    if acts[0][1] != 0x88:
        raise PckError('the script does not start with its constant pool')
    pool = _pool(d, acts[0][2], acts[0][3])
    got = {}
    for (p, op, body, ln) in acts:
        if op == 0x96:
            items = _push_items(d, body, ln)
            if items and _item_value(items[0], pool) in ('chars0', 'chars1') and _item_value(items[-1], pool) == 'Array':
                vals = [_item_value(i, pool) for i in items[1:-2]]
                got[_item_value(items[0], pool)] = list(reversed(vals))      # new Array: the last pushed is first
    if 'chars0' not in got or 'chars1' not in got:
        raise PckError('the script has no chars0 / chars1 arrays')
    return got['chars0'], got['chars1'], cmd


def repair(data):
    """A script copy made by the first version of `add`, which pointed the command's bytecode pointer (+12) at the
    command itself (the game then ran nothing): pointed at the bytecode, 16 bytes on. -> bytes (the same when fine)."""
    from . import pckscene as PS
    s = PS.Scene(data)
    fr = s.at(s.u32(12))
    ptr = s.u32(fr + 4)
    cmd = s.at(ptr)
    if cmd is None or s.d[cmd] != 3 or s.u32(cmd + 12) != ptr:
        return data
    struct.pack_into('<I', s.d, cmd + 12, ptr + 16)
    return s.p.to_bytes()


def add(data, items):
    """`items` [(code, png bytes)] for codes the arrays do not have -> the new .pck bytes. Codes already there are
    left alone (see `refresh` for their pictures)."""
    from . import pckscene as PS
    s = PS.Scene(data)
    c0, c1, cmd = arrays(s)
    items = [(c, png) for c, png in items if c not in c0 and c not in c1]
    if not items:
        return data
    d, p = s.d, s.p
    # ---- the script, copied with chars1 longer
    acts = _actions(d, cmd + 16)
    if any(op in (0x99, 0x9D) for _p, op, _b, _l in acts):
        raise PckError('the screen\'s first script has branches: not rewritten')
    pool = _pool(d, acts[0][2], acts[0][3])
    new_pool = pool + [c for c, _png in items]
    code = bytearray()
    blob = b''.join(s.encode('latin-1') + b'\0' for s in new_pool)
    code += bytes([0x88]) + struct.pack('<HH', 2 + len(blob), len(new_pool)) + blob
    for (q, op, body, ln) in acts[1:]:
        if op == 0x96:
            its = _push_items(d, body, ln)
            if its and _item_value(its[0], pool) == 'chars1' and _item_value(its[-1], pool) == 'Array':
                cnt_t, cnt_b = its[-2]
                n = _item_value(its[-2], pool) + len(items)
                cnt = (7, struct.pack('<i', int(n))) if cnt_t == 7 else (1, struct.pack('<f', float(n)))
                # appended at the array's end = pushed first (after the name): the last new code deepest
                adds = [_pool_ref(len(pool) + k) for k in reversed(range(len(items)))]
                its = [its[0]] + adds + its[1:-2] + [cnt, its[-1]]
                raw = b''.join(bytes([t]) + b for t, b in its)
                code += bytes([0x96]) + struct.pack('<H', len(raw)) + raw
                continue
        code += bytes(d[q:body + ln]) if op >= 0x80 else bytes([op])
    head = bytearray(d[cmd:cmd + 16])
    new_cmd = p.append(bytes(head) + bytes(code))
    struct.pack_into('<I', d, s.at(new_cmd) + 12, new_cmd + 16)                 # its pointer to its bytecode
    fr = s.at(s.u32(12))
    struct.pack_into('<I', d, fr + 4, new_cmd)                                  # the root's frame 0 runs the copy
    # ---- the clip: a frame per new code (chars1[16 + k] -> frame 38 + k)
    BOSS = clip(s)
    t, o = p.objects[BOSS]
    if t != 2:
        raise PckError('object %d is not the mugshot clip' % BOSS)
    nfr = s.u16(o + 8)
    old = s.at(s.u32(o + 12))
    first = CHARS1_FRAME0 + len(c1)
    need = first + len(items)
    if need > nfr:
        table = bytes(d[old:old + 8 * nfr]) + b'\0' * (8 * (need - nfr))
        struct.pack_into('<I', d, o + 12, p.append(table))
        struct.pack_into('<H', d, o + 8, need)
        struct.pack_into('<H', d, o + 16, need)
    for k, (c, png) in enumerate(items):
        frame = first + k
        PS.add_image(s, png, BOSS, frame, 0, 0)
        # the place command add_image appended to that frame: at depth 1, as every mugshot frame places its picture
        s2 = s
        fr2 = s2.at(s2.u32(p.objects[BOSS][1] + 12))
        c_ = s2.u32(fr2 + 8 * frame + 4)
        last = None
        while c_:
            co = s2.at(c_)
            if d[co] in (0, 1):
                last = co
            c_ = s2.u32(co + 4)
        if last is not None:
            struct.pack_into('<H', d, last + 12, 1)
        p.objects = PS.Scene(p.to_bytes()).p.objects         # add_image made the object list longer
    return p.to_bytes()


def refresh(data, items):
    """New pictures for codes that already have a mugshot frame of ours (added by `add`): [(code, png)] -> bytes."""
    from . import pckscene as PS
    s = PS.Scene(data)
    c0, c1, _cmd = arrays(s)
    t, o = s.p.objects[clip(s)]
    fr = s.at(s.u32(o + 12))
    for code, png in items:
        if code in c0:
            frame = c0.index(code)
        elif code in c1:
            frame = CHARS1_FRAME0 + c1.index(code)
        else:
            continue
        c_ = s.u32(fr + 8 * frame + 4)
        bmp = None
        while c_:
            co = s.at(c_)
            if s.d[co] in (0, 1):
                ch = s.u16(co + 14)
                ob = s.p.objects.get(ch)
                if ob and ob[0] == 1:
                    fs, nf = s.at(s.u32(ob[1] + 8)), s.u16(ob[1] + 20)
                    for k in range(nf if fs is not None else 0):
                        ptr = s.u32(fs + 36 * k + 32)
                        if ptr:
                            bmp = s.oid(ptr)
            c_ = s.u32(co + 4)
        if bmp is not None:
            PS.put_picture(s, bmp, png, 'resize')
    return s.p.to_bytes()
