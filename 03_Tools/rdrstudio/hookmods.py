"""Script Hooks: the code the Studio puts into the game by itself, each one switched on or off per game (the Script
Hooks tab), and the trainer's controller mapping. The second lineup, its background, own textures and Coliseum are
always on (FORCED: not listed, and an "off" left from an older Studio is ignored).

PS2: the Data Editor's build adds a hook script to the working disc's executable when it is on (the second lineup and
its background, own textures, Coliseum when the mod needs them: dataeditor.lineup_wanted; the trainer whenever it is
on, with the combos mapped here: codemods.controls).
Xbox: the recomp has them built in (RDR-XBERecomp src/game/lineup.cpp, alttex.cpp, trainer.cpp, src/app/trainer.cpp);
rdrstudio_hooks.cfg in the recomp's mods\\RDR Modding Studio folder names the ones switched off ("disabled <id>") and
the trainer's controls ("control <action> <buttons>"), which those hooks read.

Settings: data_editor/hook_mods.json {"off": {"ps2": [ids], "xbox": [ids]}, "controls": {action: "R3+TRIANGLE"}}.
"""
from __future__ import annotations

import json
import os

PLATFORMS = ('ps2', 'xbox')

# id, title, what it does, on the PS2, on the Xbox
HOOKS = [
    ('trainer', 'Trainer',
     'Noclip, Freeze World, Camera Lock and Slow-Mo (PS2) / Free Cam (Xbox), switched on and off in play with the '
     'controller combos below. Flying: the left stick moves, Up / Down rise and sink, Fast / Slow change the speed.',
     True, True),
    ('lineup2', 'Second Showdown lineup',
     'A second page on the Showdown character select for the characters on lineup 2; L1 / R1 (Xbox: the bumpers or '
     'triggers, 1 / 2, Q / E) pan between the two.', True, True),
    ('lineup2bg', 'Second lineup background',
     'Lineup 2 uses its own background (ui_characterlineup2l24b / r24b) instead of lineup 1\'s.', True, True),
    ('alttex', 'Own textures for players 2-4',
     'Players 2, 3 and 4 who pick a character with several textures wear their own version of each one (the Showdown '
     'editor\'s alternate looks); one with none keeps the character\'s own textures instead of being drawn wrong.',
     True, True),
    ('colseum', 'Coliseum on the PS2',
     'The Xbox-only Showdown stage The Coliseum is offered on the PS2 (the game turns it down by name). The Xbox game '
     'has it already.', True, False),
    ('unlocknew', 'Unlock added maps and characters',
     'Showdown maps and characters mods add are unlocked on every profile (a save made before them has no unlock '
     'for them, so they would stay hidden).', True, True),
]
HOOK_IDS = [h[0] for h in HOOKS]
# the trainer on the PS2: these code mods (codemods.py) on the working disc's executable
TRAINER_KEYS = ('noclip', 'freeze', 'camlock', 'slowmo')

# action, label, on the PS2, on the Xbox, default (the PS2 trainer's own)
CONTROLS = [
    ('noclip', 'Noclip on / off', True, True, 'L3+R3'),
    ('freeze', 'Freeze World on / off', True, True, 'R3+TRIANGLE'),
    ('camlock', 'Camera Lock on / off', True, True, 'R3+CIRCLE'),
    ('slowmo', 'Slow-Mo on / off', True, False, 'R3+SQUARE'),
    ('freecam', 'Free Cam on / off', False, True, 'R3+SQUARE'),
    ('up', 'Fly up', True, True, 'CROSS'),
    ('down', 'Fly down', True, True, 'SQUARE'),
    ('fast', 'Fly fast (4x)', True, True, 'R2'),
    ('slow', 'Fly slow (1/4x)', True, True, 'L2'),
]
CONTROL_IDS = [c[0] for c in CONTROLS]
# PS2 name -> Xbox name, for the tab
BUTTONS = [('CROSS', 'A'), ('CIRCLE', 'B'), ('SQUARE', 'X'), ('TRIANGLE', 'Y'), ('L1', 'LB'), ('R1', 'RB'),
           ('L2', 'LT'), ('R2', 'RT'), ('L3', 'LS click'), ('R3', 'RS click'), ('SELECT', 'Back'), ('START', 'Start'),
           ('UP', 'D-pad up'), ('DOWN', 'D-pad down'), ('LEFT', 'D-pad left'), ('RIGHT', 'D-pad right')]
BUTTON_NAMES = [b[0] for b in BUTTONS]
XBOX_FILE = 'rdrstudio_hooks.cfg'


def _path(de):
    return os.path.join(de.home, 'hook_mods.json')


def _load(de):
    try:
        d = json.load(open(_path(de), encoding='utf-8'))
    except (OSError, ValueError):
        d = {}
    off = d.get('off')
    if not isinstance(off, dict):                               # the first version: one list for both games
        old = list(d.get('disabled', []))
        off = {'ps2': old, 'xbox': list(old)}
    controls = d.get('controls') if isinstance(d.get('controls'), dict) else {}
    return {'off': {p: list(off.get(p, [])) for p in PLATFORMS}, 'controls': controls}


def _save(de, d):
    os.makedirs(de.home, exist_ok=True)
    with open(_path(de), 'w', encoding='utf-8') as f:
        json.dump({'off': {p: sorted(set(d['off'][p])) for p in PLATFORMS}, 'controls': d['controls']}, f, indent=1)


# always on, not shown in the tab (the user's call, 2026-11-17): what a mod needs to show its characters and maps
FORCED = ('lineup2', 'lineup2bg', 'alttex', 'colseum')


def disabled(de, platform='ps2'):
    return set(_load(de)['off'][platform]) - set(FORCED)


def norm_combo(spec):
    """'r3 + triangle' -> 'R3+TRIANGLE'; ValueError for a name that is not a button."""
    names = [n.strip().upper() for n in str(spec).split('+') if n.strip()]
    if not names:
        raise ValueError('pick at least one button')
    bad = [n for n in names if n not in BUTTON_NAMES]
    if bad:
        raise ValueError('not a button: %s' % ', '.join(bad))
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return '+'.join(seen)


def controls(de):
    """{action: 'R3+TRIANGLE'}: the mapping, the defaults where none is set."""
    mine = _load(de)['controls']
    out = {}
    for a, _l, _p, _x, default in CONTROLS:
        try:
            out[a] = norm_combo(mine.get(a, default))
        except ValueError:
            out[a] = default
    return out


def listing(de):
    d = _load(de)
    try:
        wanted = de.lineup_wanted(raw=True)
    except Exception:                                           # noqa: BLE001
        wanted = set()
    hooks = [{'id': i, 'title': t, 'text': x, 'ps2': p, 'xbox': b,
              'on': {'ps2': p and i not in disabled(de, 'ps2'), 'xbox': b and i not in disabled(de, 'xbox')},
              'wanted': i == 'trainer' or i in wanted or (i == 'unlocknew' and _adds(de))}
             for i, t, x, p, b in HOOKS if i not in FORCED]
    ctl = controls(de)
    return {'hooks': hooks,
            'controls': [{'id': a, 'label': l, 'ps2': p, 'xbox': x, 'combo': ctl[a], 'default': dflt}
                         for a, l, p, x, dflt in CONTROLS],
            'buttons': [{'ps2': a, 'xbox': b} for a, b in BUTTONS]}


def _adds(de):
    try:
        return bool(de.unlock_added_names())
    except Exception:                                           # noqa: BLE001
        return False


def set_enabled(de, hook, platform, on):
    if hook not in HOOK_IDS or hook in FORCED:
        raise ValueError('no script hook %r to switch' % hook)
    if platform not in PLATFORMS:
        raise ValueError('no game %r' % platform)
    d = _load(de)
    off = set(d['off'][platform])
    (off.discard if on else off.add)(hook)
    d['off'][platform] = sorted(off)
    _save(de, d)
    write_xbox(de)
    return listing(de)


def set_control(de, action, combo):
    if action not in CONTROL_IDS:
        raise ValueError('no trainer control %r' % action)
    d = _load(de)
    if combo in (None, '', 'default'):
        d['controls'].pop(action, None)
    else:
        d['controls'][action] = norm_combo(combo)
    _save(de, d)
    write_xbox(de)
    return listing(de)


def write_xbox(de, folder=None):
    """The Xbox side: rdrstudio_hooks.cfg in the recomp's mods\\RDR Modding Studio folder (or `folder`)."""
    if folder is None:
        try:
            from .xboxmode import xbox as _xbox
            st = _xbox(de).state()
            folder = _xbox(de).out_dir(st['exe']) if st['exe_ok'] else None
        except Exception:                                       # noqa: BLE001
            folder = None
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    off = disabled(de, 'xbox')
    ctl = controls(de)
    with open(os.path.join(folder, XBOX_FILE), 'w', encoding='utf-8') as f:
        f.write('# the RDR Modding Studio\'s Script Hooks (its Script Hooks tab); the recomp reads this\n')
        for i, _t, _x, _p, xb in HOOKS:
            if xb and i in off:
                f.write('disabled %s\n' % i)
        for a, _l, _p, xb, _d in CONTROLS:
            if xb:
                f.write('control %s %s\n' % (a, ctl[a]))
