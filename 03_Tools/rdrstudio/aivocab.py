"""The AI condition vocabulary -- what makes a behaviour table readable.

A .actb / .stateb is a token stream (see actb.py). Two kinds of token carry
meaning that a raw dump cannot show:

  * inline NAMES, which for the *test* half of a rule are drawn from a fixed
    vocabulary compiled into the executable: `dist`, `seetarget`, `acttime`,
    `healthpercent` ... 257 of them.
  * bare INTEGERS in 1001..1571, which are ACTION opcodes.

This module names the first kind and refuses to name the second -- with one
exception found later: the argument of opcode 1560 (see HUD_COMMANDS).

WHY ACTION OPCODES STAY NUMBERS
-------------------------------
The action names are not on the disc. Measured, not assumed:

  * all 257 condition strings are pointed at by a 32-bit pointer somewhere in
    the 4,497,384-byte unpacked executable -- necessarily, since the two arrays
    below are what point at them. Zero exceptions.
  * `nofire` -- an inline name in 3,126 of the 5,777 shipped .stateb files
    (4,498 occurrences), and in none of the 8,608 .actb or 536 .acttbl --
    occurs ZERO times in that executable.
  * that is not one anecdote. Of the 60 most common inline names in shipped
    behaviour files that are NOT conditions, 49 are absent from the executable
    outright. The 11 that appear (`reset`, `attack`, `dead`, `always` ...) are
    common English words: none of them is a standalone C string with a single
    pointer to it anywhere in the image, so none is part of a name table.

So the condition dictionary shipped and the action/state dictionary did not:
the offline authoring toolchain resolved those names to numbers and kept the
mapping. Anything this module printed for opcode 1019 would be invented. It
prints `1019`, plus whether the retail dispatcher has a real handler for it,
and says why there is no name. See `describe_action`.

One honest caveat on `nofire` specifically. In a .stateb it sits exactly where
`reset` and `Cue` sit -- it reads as a STATE name, not necessarily the name of
an action opcode. It proves authoring-level identifiers exist in shipped data
and are absent from the executable; it does not prove opcode 1019 was ever
spelled `nofire`. The 49-of-60 result above is what carries the conclusion.

WHERE THE DATA CAME FROM
------------------------
Two NULL-terminated arrays of {const char *name; u32 id} in the executable
(ELF base 0x00100000, so file offset = address - 0x00100000):

    0x004F9568   199 entries   per-creature properties
    0x004F9BA8    58 entries   group / world properties

They are baked in below as plain data. Deliberately: the tool has to work from
a disc alone, and 07_Research/SLUS_205.00.unpacked.bin is a research artefact
that will not be on a user's machine. scripts/verify_aivocab.py re-extracts
both tables from the executable when it is present and asserts the baked copy
matches byte for byte.

THINGS THAT SURPRISED ME
------------------------
  * 257 entries but only 236 distinct ids and 248 distinct names. Ten alias
    groups share an id (`dist`/`distance`, `dir`/`direction`, ...). Nine of the
    ten are decided outright -- one spelling is used by shipped data and the
    other by nothing at all. The tenth, id 8, uses both (`dist` 3,794 files,
    `distance` 65). So canonical = whichever spelling shipped data uses more,
    ties broken by table order; `lookup` accepts either spelling regardless.
  * Nine names appear in BOTH tables, always on the same id. Eleven ids appear
    in both; eight carry identical names and three differ only by a `player`
    prefix (`toughness` / `playertoughness`). So an id means the same thing in
    both scopes and a scope-free lookup is safe -- but the scope is reported
    anyway, because the group table reads the *player's* value.
  * The ids are not dense. 21 values in 1..256 have no name at all (0, 4, 5,
    14, 15, 64, 65, 66, 91..97, 101, 134, 174..176, 178). An id in a gap is not
    a bug in this table -- it is a property the retail build does not expose by
    name -- so `name_for_condition` returns None and `annotate` says
    "no name in the vocabulary" rather than guessing at a neighbour.
  * 47 of the 248 spellings never appear in any of the 14,921 shipped
    behaviour files. Eight of those 47 are the losing half of an alias pair, so
    39 vocabulary entries are genuinely unreferenced -- that is `NEVER_USED`.
    A finding, not an error: verify_aivocab.py confirms the extraction is
    exact, so these are dev-only or cut properties (`isbear`, `onbear`,
    `ischasingtrain`, `hasgatlinggun`, `hascard`, six `duel_*` counters, ...).

  * 296 of the 571 action slots have a handler, and shipped data uses 288 of
    them -- but it also uses 10 opcode values that land on the do-nothing stub.
    Those 10 are most likely the token walker reading plain data (a frame
    count, a room number) that happens to fall in 1001..1571, which is why
    `annotate` marks every opcode `certain=False`. INFERENCE, not measurement:
    ~3% noise on bare-int opcode detection is consistent with that.

USE
---
    from rdrstudio import actb, aivocab
    rows = aivocab.annotate(actb.parse(data))
    print(aivocab.listing(data))
"""

import re
import struct
from typing import Any

# --------------------------------------------------------------- baked data

# Address of each array in the executable, kept only so the docstring's claim
# is checkable and verify_aivocab.py has something to re-read.
CREATURE_TABLE_ADDR = 0x004F9568
GROUP_TABLE_ADDR = 0x004F9BA8
ELF_BASE = 0x00100000

SCOPE_CREATURE = "creature"
SCOPE_GROUP = "group"

# "name:id", in the order the arrays store them.
_CREATURE_RAW = """
    angle:30 atcorner:31 blocked:24 blockedback:21 blockedfront:20
    blockedleft:23 blockedright:22 critical:55 criticaltargeting:55 detect:3
    dist:8 distance:8 escaping:19 gundrawn:42 gunholstered:162 health:1
    height:33 hitbound:2 hitprop:16 hitshortprop:38 hittallprop:39 justhit:9
    knowsplayerexists:50 lastseenposlinkroom:49 lastseenposroom:48
    lastseentime:47 linkroom:44 moveprobe:36 moveprobedist:37 newroom:45
    newroomtime:46 onhorse:13 reloading:54 roomnum:43 seeplayer:6
    seetarget:7 shooting:28 sneaking:10 sneakingtime:53 targetingme:32
    faceanimating:56 healthpercent:67 crackstate:29 reacting:68
    difficultylevel:69 onlowcover:71 grabbing:74 grabbed:73 covergood:80
    tryingtosneak:81 flying:85 currentweapon:83 haspistol:84 hasdblgun:86
    hasknife:89 hasshotgun:87 hasrifle:88 hasdynamite:90 numweapons:98
    onscreen:99 tacklesuper:100 armorsuper:102 onground:103 beingdragged:104
    timeonscreen:105 timeoffscreen:106 alignment:109 onmount:111
    canmount:112 hasrider:113 ammoleft:110 exists:34 numalive:17
    numaliveandspawned:107 numactive:51 numkilled:40 jumping:119
    vaulting:120 fanning:121 rightstick:122 relativefacing:123
    coveracquired:124 sneakboxnum:125 numarmor:126 numarmorworking:127
    shouldering:128 shouldered:129 lifting:130 lifted:131 throwing:132
    thrown:133 playingspecial:137 subroomtype:138 subroomname:139
    newsubroom:140 newsubroomtime:141 name:143 type:144 pressed:145
    justpressed:57 dir:146 direction:146 mag:147 magnitude:147 groupname:148
    value:151 initialvalue:152 spunout:153 climbing:154 deathtoll:155
    joytimeleft:156 joytimeright:157 partnercommand:158 partnercmd:158
    partnermenuopen:159 toughness:75 valor:76 maxvalor:77 avoiding:160
    arcturning:163 loadedbullets:164 superdodgeresults:165 isactive:166
    isplaying:167 blocking:170 teamwon:169 isplayer:172 knockeddown:41
    gettingup:52 heading:177 justhitbody:204 justhithead:205
    justhitlegrightlower:206 justhitlegrightupper:207 justhitarmleft:208
    justhitlegleftlower:209 justhitlegleftupper:210 justhitarmright:211
    isspeccnntgting:179 isspeccnnfiring:180 engagegatgun:181
    firefocusinrange:182 ammo:183 doneaiming:78 controlled:79 readytoaim:82
    command:18 lassoing:186 lassoed:185 haskey:187 nearcloseddoor:189
    whipattacking:192 infirebarrier:193 inringoffire:238 inpoisonbreath:239
    infirebreath:240 iscoffinswinging:242 onbear:195 isbear:196
    isrocktosstgting:197 isrocktosstossing:244 hasbow:198 hasmolotov:199
    hasgatlinggun:200 hascard:201 plummeting:202 haskey0:212 haskey1:213
    haskey2:214 haskey3:215 haskey4:216 canchasetrain:72 ischasingtrain:115
    flareattached:217 duel_state:219 duel_drawmeterzone:220
    duel_firemeterzone:221 duel_gundist:222 duel_reachmeterloops:223
    duel_drawmeterloops:224 duel_firemeterloops:225 duel_reachpenalty:226
    duel_drawpenalty:227 duel_hp:228 duel_shots_fired:229
    duel_hits_fired:230 duel_misses_fired:231 duel_legshots_fired:232
    duel_bodyshots_fired:233 duel_headshots_fired:234 duel_timetogun:235
    tougharmor:26 isstunned:241 ismounting:250 istraindismounting:251
    onfire:58 poisoned:59 playertalkingtome:255 highestcombo:254
"""

_GROUP_RAW = """
    time:11 acttime:12 numalive:17 numkilled:40 numaliveandspawned:107
    numleftinactivepool:188 numactive:51 command:18 movecount:25
    shootcount:27 actfile:35 probability:70 playertoughness:75
    playervalor:76 playermaxvalor:77 navstatus:108 actstartposdist:114
    levelname:116 levelvar:117 numdynamiteactive:118 camdidhit:135
    camdoingtrans:136 shopopen:142 menuopen:149 menuselection:150
    partnercommand:158 partnercmd:158 partnermenuopen:159 shutters:161
    numstreamsavailable:168 numhumans:171 nummembers:256 playerspecial:173
    camisanimating:184 pathname:190 railname:194 timetopicknewpath:191
    hudtime:203 playerlegendpoints:218 playertile:236
    puppetshow_isplaying:237 isduckwalking:243 mpgamemode:245
    mpstartwithduel:246 isduelrunning:247 numplayerlocks:248
    duelsurvivors:249 bountyhuntermode:252 numgamescomplete:253
    highestcombo:254 HasSpecial:60 HasSpecialMove:60 HasMeleeSpecial:61
    HasMeleeSpecialMove:61 MySpecial:62 MySpecialMove:62 MyMeleeSpecial:63
    MyMeleeSpecialMove:63
"""

# name -> number of the 14,921 distinct shipped .actb/.stateb/.acttbl
# filenames that contain it as an inline token. Measured once by
# scripts/verify_aivocab.py --scan, which recomputes and asserts these.
_SHIPPED_RAW = """
    HasMeleeSpecial:1 HasSpecial:4 MyMeleeSpecial:1 MySpecial:1 actfile:74
    actstartposdist:56 acttime:7207 alignment:62 ammo:4 ammoleft:35
    angle:539 armorsuper:1 atcorner:471 avoiding:74 beingdragged:3
    blocked:40 blockedback:23 blockedfront:52 blockedleft:27 blockedright:29
    blocking:6 bountyhuntermode:81 camdidhit:46 camdoingtrans:531
    camisanimating:2 canchasetrain:12 canmount:28 climbing:48 command:1454
    controlled:8 covergood:595 crackstate:50 critical:90 currentweapon:272
    deathtoll:1 detect:570 difficultylevel:59 direction:4 dist:3794
    distance:65 doneaiming:50 duel_drawmeterzone:86 duel_drawpenalty:2
    duel_firemeterzone:85 duel_gundist:87 duel_hits_fired:1 duel_hp:169
    duel_reachmeterloops:71 duel_shots_fired:77 duel_state:112
    duel_timetogun:71 duelsurvivors:6 engagegatgun:68 escaping:58
    exists:2969 fanning:4 flying:50 gettingup:10 grabbed:388 grabbing:4
    groupname:88 gundrawn:918 gunholstered:1 hasbow:8 hasdblgun:28
    hasdynamite:51 haskey:17 haskey0:1 haskey2:1 haskey3:3 haskey4:6
    hasknife:20 hasmolotov:13 haspistol:194 hasrider:51 hasrifle:46
    hasshotgun:21 heading:1 health:125 healthpercent:1084 height:77
    highestcombo:5 hitbound:338 hitprop:30 hitshortprop:2 hittallprop:1
    hudtime:31 infirebarrier:1 inpoisonbreath:1 isactive:437
    iscoffinswinging:3 isduelrunning:10 ismounting:1 isplaying:276
    isrocktosstgting:21 isrocktosstossing:5 isspeccnnfiring:4
    isspeccnntgting:7 isstunned:20 istraindismounting:1 joytimeright:1
    jumping:18 justhit:1041 justhitarmleft:8 justhitarmright:7 justhitbody:4
    justhithead:35 justhitlegleftlower:8 justhitlegleftupper:4
    justhitlegrightlower:8 justhitlegrightupper:4 knockeddown:200
    knowsplayerexists:59 lassoed:10 lassoing:8 lastseentime:553 levelname:43
    levelvar:102 lifting:4 loadedbullets:36 magnitude:15 maxvalor:2
    menuopen:8 menuselection:46 movecount:912 moveprobe:49 moveprobedist:16
    mpgamemode:7 mpstartwithduel:7 name:505 navstatus:673 nearcloseddoor:15
    newroomtime:62 newsubroomtime:1 numactive:19 numalive:389
    numaliveandspawned:241 numarmor:6 numarmorworking:10 numdynamiteactive:2
    numhumans:10 numkilled:97 numleftinactivepool:175 nummembers:1
    numplayerlocks:22 numstreamsavailable:568 numweapons:611 onfire:6
    onground:95 onhorse:33 onlowcover:3 onmount:183 onscreen:381
    partnercommand:14 partnermenuopen:1 pathname:11 playerlegendpoints:2
    playerspecial:2 playertalkingtome:36 playertile:14 playingspecial:4
    plummeting:12 pressed:403 probability:814 puppetshow_isplaying:324
    railname:57 reacting:287 readytoaim:47 relativefacing:114 reloading:93
    roomnum:141 seeplayer:90 seetarget:894 shootcount:865 shooting:40
    shopopen:45 shouldered:35 shouldering:111 shutters:1 sneakboxnum:23
    sneaking:1341 sneakingtime:5 spunout:91 subroomname:1096 subroomtype:642
    superdodgeresults:9 tacklesuper:8 targetingme:735 teamwon:82 throwing:31
    time:1859 timeoffscreen:261 timeonscreen:9 timetopicknewpath:7
    toughness:4 tryingtosneak:106 type:300 valor:37 value:3284 vaulting:1
    whipattacking:1
"""

# ------------------------------------------------------------ action opcodes

ACTION_MIN = 1001
ACTION_MAX = 1571

# Inclusive opcode ranges whose jump-table slot at 0x00524490 points somewhere
# other than the shared do-nothing stub at 0x002DC374. 296 of the 571 slots;
# the other 275 all share that stub, i.e. the retail build ignores them.
_ACTION_HANDLER_RANGES = (
    (1001, 1020),
    (1030, 1033),
    (1035, 1035),
    (1050, 1050),
    (1060, 1061),
    (1065, 1068),
    (1070, 1070),
    (1080, 1080),
    (1088, 1088),
    (1090, 1090),
    (1100, 1100),
    (1110, 1110),
    (1120, 1122),
    (1130, 1130),
    (1140, 1140),
    (1152, 1153),
    (1160, 1161),
    (1170, 1170),
    (1172, 1176),
    (1180, 1180),
    (1191, 1191),
    (1193, 1193),
    (1195, 1195),
    (1200, 1200),
    (1205, 1206),
    (1209, 1210),
    (1215, 1215),
    (1220, 1220),
    (1230, 1230),
    (1232, 1233),
    (1238, 1239),
    (1241, 1241),
    (1245, 1246),
    (1260, 1260),
    (1265, 1265),
    (1270, 1270),
    (1272, 1272),
    (1280, 1280),
    (1282, 1282),
    (1284, 1284),
    (1286, 1286),
    (1290, 1290),
    (1292, 1292),
    (1294, 1294),
    (1296, 1296),
    (1298, 1298),
    (1300, 1300),
    (1302, 1303),
    (1305, 1306),
    (1308, 1311),
    (1313, 1313),
    (1315, 1315),
    (1317, 1317),
    (1319, 1320),
    (1322, 1322),
    (1324, 1326),
    (1328, 1328),
    (1331, 1331),
    (1333, 1333),
    (1335, 1335),
    (1337, 1337),
    (1339, 1339),
    (1341, 1341),
    (1343, 1349),
    (1351, 1353),
    (1355, 1355),
    (1357, 1357),
    (1359, 1365),
    (1367, 1367),
    (1369, 1369),
    (1371, 1371),
    (1373, 1373),
    (1375, 1375),
    (1377, 1377),
    (1379, 1379),
    (1381, 1381),
    (1385, 1391),
    (1393, 1393),
    (1395, 1397),
    (1399, 1399),
    (1401, 1401),
    (1403, 1403),
    (1405, 1408),
    (1410, 1410),
    (1412, 1412),
    (1414, 1414),
    (1416, 1416),
    (1418, 1419),
    (1421, 1421),
    (1423, 1424),
    (1426, 1426),
    (1428, 1428),
    (1430, 1430),
    (1432, 1434),
    (1436, 1436),
    (1438, 1440),
    (1442, 1442),
    (1444, 1444),
    (1446, 1446),
    (1448, 1478),
    (1481, 1544),
    (1546, 1561),
    (1563, 1569),
    (1571, 1571),
)


def _parse_pairs(raw):
    out = []
    for tok in raw.split():
        name, _, num = tok.rpartition(":")
        out.append((name, int(num)))
    return out


class Condition(object):
    """One vocabulary entry, resolved."""

    __slots__ = ("name", "id", "scope", "aliases", "shipped_files")

    def __init__(self, name, id, scope, aliases, shipped_files):
        self.name = name              # canonical spelling
        self.id = id
        self.scope = scope            # "creature" | "group"
        self.aliases = aliases        # other spellings of the same id, may be ()
        self.shipped_files = shipped_files

    def __repr__(self):
        return "<Condition %s id=%d %s>" % (self.name, self.id, self.scope)

    def to_json(self):
        return dict(name=self.name, id=self.id, scope=self.scope,
                    aliases=list(self.aliases), shipped_files=self.shipped_files,
                    unused=self.shipped_files == 0)


def _build():
    shipped = dict(_parse_pairs(_SHIPPED_RAW))
    entries = []          # (scope, name, id) in table order
    for scope, raw in ((SCOPE_CREATURE, _CREATURE_RAW), (SCOPE_GROUP, _GROUP_RAW)):
        for name, cid in _parse_pairs(raw):
            entries.append((scope, name, cid))

    # Canonical spelling of an alias group = the one shipped data uses most.
    # Ties (both zero) fall back to table order, which is why `order` is in the
    # sort key -- otherwise the choice would differ between Python runs.
    groups: dict[Any, list[Any]] = {}
    for order, (scope, name, cid) in enumerate(entries):
        groups.setdefault((scope, cid), []).append((order, name))

    by_key = {}           # (scope, id) -> Condition
    by_name = {}          # lowercase spelling -> Condition
    for key, members in groups.items():
        members.sort(key=lambda m: (-shipped.get(m[1], 0), m[0]))
        canon = members[0][1]
        aliases = tuple(n for _, n in members[1:])
        c = Condition(canon, key[1], key[0], aliases, shipped.get(canon, 0))
        by_key[key] = c
        for _, n in members:
            by_name[n.lower()] = c
    ordered = tuple(by_key[k] for k in sorted(by_key, key=lambda k: (k[0], k[1])))
    return ordered, by_key, by_name, len(entries)


CONDITIONS, _BY_KEY, _BY_NAME, _ENTRY_COUNT = _build()

#: every id in 1..256 that no vocabulary entry claims.
UNNAMED_IDS = tuple(i for i in range(0, 257)
                    if (SCOPE_CREATURE, i) not in _BY_KEY
                    and (SCOPE_GROUP, i) not in _BY_KEY)

#: canonical names that occur in no shipped behaviour file.
NEVER_USED = tuple(sorted(c.name for c in CONDITIONS if c.shipped_files == 0))


# ------------------------------------------------------------------- lookups

def conditions_for_id(cid):
    """Every scope that names this id. Usually one, sometimes both."""
    return tuple(_BY_KEY[k] for k in ((SCOPE_CREATURE, cid), (SCOPE_GROUP, cid))
                 if k in _BY_KEY)


def condition(cid, scope=None):
    """The Condition for an id, or None if nothing names it.

    With no scope, the creature table wins: it is the bigger table and the one
    a per-creature .actb is reading. The group entry for a shared id means the
    same property measured on the player.
    """
    if scope is not None:
        return _BY_KEY.get((scope, cid))
    hits = conditions_for_id(cid)
    return hits[0] if hits else None


def name_for_condition(cid, scope=None):
    """Canonical name for a condition id, or None. Aliases via `aliases_for_condition`."""
    c = condition(cid, scope)
    return c.name if c is not None else None


def aliases_for_condition(cid, scope=None):
    c = condition(cid, scope)
    return c.aliases if c is not None else ()


def lookup(name):
    """Condition for a spelling (canonical or alias, case-insensitive), or None."""
    return _BY_NAME.get(str(name).lower())


def condition_id(name):
    c = lookup(name)
    return c.id if c is not None else None


def is_condition_name(name):
    return str(name).lower() in _BY_NAME


# ------------------------------------------------------------------- actions

# ------------------------------------------------- HUD / cutscene commands
#
# Found 2026-11-12. Action opcode 1560 takes one integer, and that integer is
# an index into a run of 61 C strings in the executable (0x00527C35,
# ShowFocusBar .. DoneTalking). The run has no pointer to it, which is why it
# was set aside before; the proof that its order IS the numbering is elsewhere:
#
#   * corpus: 1,756 uses of 1560 in 991 shipped files, every argument 0..60.
#     51 (StartPuppetshowFadein) is followed by 1522 (play a cutscene) 91
#     times of 103; hidefocusbar.actb uses 1 (HideFocusBar); 37 (StopSubtitles)
#     sits in *talk* files and 60 (DoneTalking) in *dialog* files.
#   * code: the action executor at 0x002E5260 does `sltiu v0, v1, 61` and
#     jumps through a 61-slot table at 0x00525450. Slot 0 sets bit 2 of the
#     HUD word and slot 1 clears it; 2/3 set/clear bit 1; 4/5 bit 4; 12/13
#     bit 0x40; 38/40 lock/unlock with mask 8 and 39/41 with mask 0x80 --
#     Show/Hide and Lock/Unlock pairs land exactly where the names say.
#
# This names the ARGUMENT of one opcode. The other opcodes still have no name
# on the disc; nothing here changes that.
HUD_COMMAND_OP = 1560
HUD_POOL_ADDR = 0x00527C35
HUD_SWITCH_ADDR = 0x002E5260
HUD_SWITCH_TABLE = 0x00525450
HUD_COMMANDS = tuple("""
ShowFocusBar HideFocusBar ShowHealthBar HideHealthBar ShowMoney HideMoney
ShowCombo HideCombo ShowEnemyHealth HideEnemyHealth ShowAllyHealth
HideAllyHealth ShowReticle HideReticle ForceCombo UnforceCombo ForceMoney
UnforceMoney StompCamera ShowCinemaShutters HideCinemaShutters ForceNumLocks
UnforceNumLocks ForceGameplayFilm UnforceGameplayFilm SetPlayerNotoriety
ShowDuelMiss ShowDuelHit ShowDuelCrit ShowDuelNone DuelAnimCursor
ShowAimCursor HideAimCursor ClearScreen SuppressDuelCursor
UnSuppressDuelCursor PlaySubtitles StopSubtitles LockPlayerMovement
LockPlayerSpecials UnlockPlayerMovement UnlockPlayerSpecials
UnsuppressCannonIcon SuppressCannonIcon DuelLockSlowMo DuelUnLockSlowMo
DuelTryEndSlowMo EndPuppetshowCrossfade EndPuppetshowFadeout
EndPuppetshowCut StartPuppetshowCrossfade StartPuppetshowFadein
StartPuppetshowCut FadeInRightNow FadeOutRightNow EndPuppetshowFreezeFrame
UnfreezeCrossfade UnfreezeCut FreezeFrame RestartDuelMeters DoneTalking
""".split())


def hud_command(n):
    """Name of HUD / cutscene command `n` (the argument of opcode 1560)."""
    if isinstance(n, int) and 0 <= n < len(HUD_COMMANDS):
        return HUD_COMMANDS[n]
    return None


def extract_hud_commands(path):
    """Re-read the 61 names out of the unpacked executable. For verification."""
    with open(path, "rb") as fh:
        img = fh.read()
    o = HUD_POOL_ADDR - ELF_BASE
    out = []
    while len(out) < len(HUD_COMMANDS):
        e = img.index(b"\x00", o)
        if e > o:
            out.append(img[o:e].decode("latin-1"))
        o = e + 1
    return tuple(out)


def is_action_opcode(value):
    return isinstance(value, int) and ACTION_MIN <= value <= ACTION_MAX


def action_has_handler(op):
    """Does the retail dispatcher do anything for this opcode?"""
    return any(lo <= op <= hi for lo, hi in _ACTION_HANDLER_RANGES)


ACTION_NO_NAME = (
    "action opcode -- no name for it exists on the disc. The executable "
    "carries a name for every condition and for no action; 49 of the 60 "
    "commonest non-condition names in shipped behaviour data appear nowhere "
    "in it. The authoring toolchain kept that dictionary."
)


def describe_action(op):
    """A caption for an action opcode that never invents a name."""
    if not is_action_opcode(op):
        return "%d: not in the action range %d..%d" % (op, ACTION_MIN, ACTION_MAX)
    if op == HUD_COMMAND_OP:
        return ("action %d: HUD / cutscene command (the number after it says "
                "which one)" % op)
    if action_has_handler(op):
        return "action %d (handler present, name unknown -- not on the disc)" % op
    return ("action %d (NO handler in the retail build -- slot points at the "
            "do-nothing stub)" % op)


# ------------------------------------------------------------------ annotate

#: A bare integer that happens to land in 1001..1571 is not necessarily an
#: opcode -- it can be a frame count, a distance, an id. Rows tagged this way
#: carry `certain=False` so a caller can grey them out.
ACTION_GUESS_NOTE = (
    "in the action range, but a bare integer in a token stream can also be "
    "plain data -- treat as a hint"
)


def _tokens(parsed):
    """Accept actb.parse()'s 3-tuple, its token list, or already-jsonified rows."""
    if isinstance(parsed, tuple) and len(parsed) == 3:
        parsed = parsed[2]
    out = []
    for t in parsed:
        if isinstance(t, dict):
            out.append((t.get("off", t.get("offset")), t.get("kind"), t.get("value")))
        else:
            out.append((t.offset, t.kind, t.value))
    return out


def annotate(parsed):
    """Fill names into a parsed behaviour table.

    Takes what `actb.parse` returns and gives back a list of plain dicts --
    the same tokens, same offsets, same values, plus:

        vocab     the Condition (as JSON) when a name token is in the
                  vocabulary, else None
        action    {op, handler, certain} when an int looks like an opcode
        note      why a token could not be named -- always present when
                  `vocab` and `action` are both absent and the token is a
                  name or an in-range int
        label     one line of English, ready to print

    Nothing is mutated and nothing is dropped: a caller can render this list
    straight down the page and get the same rows the raw dump had.
    """
    rows = []
    prev = None
    for off, kind, value in _tokens(parsed):
        row = dict(off=off, kind=kind, value=value)
        after_hud, prev = prev == HUD_COMMAND_OP, (value if kind == "int" else None)
        if after_hud and kind == "int" and hud_command(value):
            row["hud"] = hud_command(value)
            row["label"] = "%d = %s (HUD / cutscene command)" % (value, row["hud"])
            rows.append(row)
            continue
        if kind == "name":
            c = lookup(value)
            if c is not None:
                row["vocab"] = c.to_json()
                extra = ""
                if c.name.lower() != str(value).lower():
                    extra = " [alias of %s]" % c.name
                elif c.aliases:
                    extra = " [aka %s]" % ", ".join(c.aliases)
                if c.shipped_files == 0:
                    extra += " [unused in shipped data]"
                row["label"] = "condition %s = id %d (%s)%s" % (
                    value, c.id, c.scope, extra)
            else:
                # Not a failure. Most inline names in these files are state
                # names, table names, animation names or action names -- the
                # vocabulary only ever covered the test side of a rule.
                row["vocab"] = None
                row["note"] = "not in the condition vocabulary"
                row["label"] = "name %r (not a condition)" % value
        elif kind == "int" and is_action_opcode(value):
            has = action_has_handler(value)
            row["action"] = dict(op=value, handler=has, certain=False)
            row["note"] = ACTION_NO_NAME + "; " + ACTION_GUESS_NOTE
            row["label"] = describe_action(value)
        elif kind == "float":
            row["label"] = "%g" % value
        else:
            # The kind column already says what it is; repeating it just makes
            # the interesting rows harder to spot in a 4,000-line dump.
            row["label"] = str(value)
        rows.append(row)
    return rows


def annotate_condition_id(cid, scope=None):
    """For callers holding a bare id (an AI-script condition, a save field).

    Always returns a dict, so a UI never has to special-case the miss.
    """
    c = condition(cid, scope)
    if c is None:
        return dict(id=cid, name=None, scope=scope,
                    note="no name in the vocabulary (%d of 0..256 are unnamed)"
                         % len(UNNAMED_IDS))
    d = c.to_json()
    d["also"] = [x.to_json() for x in conditions_for_id(cid) if x is not c]
    return d


# -------------------------------------------------------------------- render

def listing(data, limit=4000):
    """actb.listing, with the names filled in. Same rows, same offsets."""
    from . import actb
    version, count, tokens = actb.parse(data, limit=limit)
    rows = annotate((version, count, tokens))
    out = ["version %.2f, header count %d, %d tokens" % (version, count, len(rows)),
           "%d named conditions, %d action opcodes"
           % (sum(1 for r in rows if r.get("vocab")),
              sum(1 for r in rows if r.get("action"))),
           "",
           "  offset  kind    meaning"]
    for r in rows:
        out.append("  %06X  %-6s  %s" % (r["off"], r["kind"], r["label"]))
    if len(rows) >= limit:
        out.append("  ... truncated at %d tokens" % limit)
    return "\n".join(out)


def summary(data):
    """Counts a UI can show above a listing."""
    from . import actb
    rows = annotate(actb.parse(data, limit=200000))
    named = [r for r in rows if r.get("vocab")]
    acts = [r for r in rows if r.get("action")]
    used = sorted({r["vocab"]["name"] for r in named})
    return dict(
        tokens=len(rows),
        conditions=len(named),
        distinct_conditions=used,
        action_opcodes=sorted({r["action"]["op"] for r in acts}),
        actions_without_handler=sorted({r["action"]["op"] for r in acts
                                        if not r["action"]["handler"]}),
        unnamed_names=sorted({r["value"] for r in rows
                              if r["kind"] == "name" and r.get("vocab") is None}),
        action_names_available=False,
    )


# ---------------------------------------------------------------- extraction

def extract_from_executable(path):
    """Re-read both arrays out of the unpacked executable. For verification.

    Not used at runtime -- the tables above are baked precisely so the tool
    never needs this file. Returns {scope: [(name, id), ...]}.
    """
    with open(path, "rb") as fh:
        img = fh.read()

    def cstr(va):
        o = va - ELF_BASE
        return img[o:img.index(b"\x00", o)].decode("latin-1")

    out = {}
    for scope, addr in ((SCOPE_CREATURE, CREATURE_TABLE_ADDR),
                        (SCOPE_GROUP, GROUP_TABLE_ADDR)):
        va = addr
        rows = []
        while True:
            ptr, cid = struct.unpack_from("<II", img, va - ELF_BASE)
            if ptr == 0:          # both arrays are NULL-terminated
                break
            rows.append((cstr(ptr), cid))
            va += 8
        out[scope] = rows
    return out


def extract_action_handlers(path, table_addr=0x00524490):
    """Which of the 571 action slots have a handler. For verification."""
    with open(path, "rb") as fh:
        img = fh.read()
    slots = struct.unpack_from("<571I", img, table_addr - ELF_BASE)
    counts: dict[int, int] = {}
    for s in slots:
        counts[s] = counts.get(s, 0) + 1
    stub = max(counts, key=lambda k: counts[k])
    return set(ACTION_MIN + i for i, s in enumerate(slots) if s != stub), stub


NAME_TOKEN = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
