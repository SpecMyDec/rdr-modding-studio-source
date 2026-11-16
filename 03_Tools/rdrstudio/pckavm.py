"""The ActionScript (AVM1 bytecode) in a `.pck` screen, read for what sounds it plays.

A screen holds no audio: its "sound" objects are empty 8-byte stubs. Its scripts make a Sound and attach a sound by
NAME, and the name is the game's own sound name from its bank definitions (snd_<n>.csv):

    soundScroll = new Sound();  soundScroll.attachSound("Menu_HandScroll");  ...  soundScroll.start();

(read from the main menu's constant pool in the game's memory, PCSX2 save state: the variable names -- soundScroll,
soundLeave -- say nothing; soundLeave plays Ricochet2.) This finds every attachSound call with a constant name: each
ActionConstantPool (0x88) and the code after it, walked with a small stack model (push, getVariable, getMember,
new, callMethod ...). A pool whose code cannot be followed falls back to its order (var, "Sound", name,
"attachSound", then var, name pairs), as the scripts were compiled.
"""

from __future__ import annotations

import struct


def _pool_at(d, k):
    """the strings of a constant pool action at k, and the offset after it; None if it is not one."""
    if d[k] != 0x88 or k + 5 > len(d):
        return None
    ln, n = struct.unpack_from('<HH', d, k + 1)
    end = k + 3 + ln
    if n == 0 or ln < 2 + n or end > len(d):
        return None
    out, p = [], k + 5
    for _ in range(n):
        e = d.find(b'\0', p, end)
        if e < 0:
            return None
        s = bytes(d[p:e])
        if any(c < 0x20 or c > 0x7e for c in s):
            return None
        out.append(s.decode('ascii'))
        p = e + 1
    if p != end:
        return None
    return out, end


def _walk(d, start, pool, found):
    """the actions from `start` (just after a constant pool) until the end action, with a stack model."""
    st = []
    pop = lambda: st.pop() if st else None  # noqa: E731
    p, guard = start, 0
    while p < len(d) and guard < 20000:
        guard += 1
        op = d[p]
        if op == 0:
            return
        if op >= 0x80:
            if p + 3 > len(d):
                return
            ln = struct.unpack_from('<H', d, p + 1)[0]
            body = p + 3
            if op == 0x96:                                    # push
                q = body
                while q < body + ln:
                    t = d[q]; q += 1
                    if t == 0:
                        e = d.find(b'\0', q, body + ln)
                        if e < 0:
                            return
                        st.append(bytes(d[q:e]).decode('latin-1')); q = e + 1
                    elif t == 1:
                        st.append(struct.unpack_from('<f', d, q)[0]); q += 4
                    elif t in (2, 3):
                        st.append(None)
                    elif t == 4:
                        st.append(('reg', d[q])); q += 1
                    elif t == 5:
                        st.append(bool(d[q])); q += 1
                    elif t == 6:
                        q += 8; st.append(0.0)
                    elif t == 7:
                        st.append(struct.unpack_from('<i', d, q)[0]); q += 4
                    elif t == 8:
                        i = d[q]; q += 1; st.append(pool[i] if i < len(pool) else None)
                    elif t == 9:
                        i = struct.unpack_from('<H', d, q)[0]; q += 2; st.append(pool[i] if i < len(pool) else None)
                    else:
                        return
            elif op == 0x88:                                  # another pool: a new block starts
                return
            elif op == 0x87:                                  # store register: keeps the value
                pass
            p = body + ln
            continue
        p += 1
        if op == 0x1C:                                        # getVariable
            n = pop(); st.append(('var', n))
        elif op == 0x4E:                                      # getMember
            n = pop(); o = pop(); st.append(('var', n) if isinstance(n, str) else o)
        elif op in (0x52, 0x3D):                              # callMethod / callFunction
            if op == 0x52:
                name = pop(); obj = pop()
            else:
                name = pop(); obj = None
            n = pop()
            args = [pop() for _ in range(int(n) if isinstance(n, (int, float)) and 0 <= n < 64 else 0)]
            if name == 'attachSound' and args and isinstance(args[0], str):
                var = obj[1] if isinstance(obj, tuple) and obj and obj[0] == 'var' else None
                found.append((var if isinstance(var, str) else None, args[0]))
            st.append(None)
        elif op == 0x40:                                      # new object
            name = pop(); n = pop()
            for _ in range(int(n) if isinstance(n, (int, float)) and 0 <= n < 64 else 0):
                pop()
            st.append(('new', name))
        elif op in (0x1D, 0x3C):                              # setVariable / defineLocal
            pop(); pop()
        elif op == 0x4F:                                      # setMember
            pop(); pop(); pop()
        elif op in (0x17, 0x41):                              # pop / defineLocal2
            pop()
        elif op == 0x4C:                                      # pushDuplicate
            st.append(st[-1] if st else None)


def _from_order(pool):
    """the pool's own order, where the code could not be followed: var, "Sound", name, "attachSound", var, name ..."""
    out = []
    if 'attachSound' not in pool:
        return out
    i = pool.index('attachSound')
    if i >= 3 and pool[i - 2] == 'Sound':
        out.append((pool[i - 3], pool[i - 1]))
    j = i + 1
    while j + 1 < len(pool) and pool[j].startswith('sound') and pool[j][5:6].isupper():
        out.append((pool[j], pool[j + 1]))
        j += 2
    return out


def attached_sounds(d):
    """[(variable or None, sound name)] for every attachSound call with a constant name in the screen's bytes."""
    found = []
    k = 0
    n = len(d)
    while True:
        k = d.find(b'\x88', k)
        if k < 0 or k >= n:
            break
        hit = _pool_at(d, k)
        if hit is None:
            k += 1
            continue
        pool, end = hit
        if 'attachSound' in pool:
            mine = []
            try:
                _walk(d, end, pool, mine)
            except (IndexError, struct.error):
                pass
            found += mine or _from_order(pool)
        k = end
    seen, out = set(), []
    for v, s in found:
        if (v, s) not in seen:
            seen.add((v, s))
            out.append((v, s))
    return out
