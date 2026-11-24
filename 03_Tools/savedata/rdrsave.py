"""The Red Dead Revolver save itself -- what is decoded, and what is not.

`ps2mc.py` gets the bytes off the card. This module says what they mean, and
it is deliberately stingy about that: a field is named here only when
something outside the save file agrees. Everything else is carried as a typed
CANDIDATE or left alone. `05_Docs/SAVE-FORMAT.md` is the long version.

THE FILE. `BASLUS-20500RDR/BASLUS-20500RDR`, 18,432 bytes, one per card.

    0      .. 184    prefix, the ACTIVE game -- holds MONEY at +0x68
    184    .. 264    struct header       80 bytes, UNKNOWN
    264    .. 17304  six profile slots   6 x 2840
    17304  .. 18432  trailer             1,128 bytes, UNKNOWN

MEASURED LIVE: file bytes 0..17304 are mirrored in EE RAM at **0x0073DE28**,
offset for offset -- 99.7% identical while the game ran, and every difference
was a field the game was actively changing or slot 5's scratch tail. The final
1,128 bytes are NOT in that region and come from somewhere else.

WHY THOSE NUMBERS ARE NOT A GUESS. They were found twice, independently.
First by measurement: `?none` repeats in the file at a stride of exactly 2,840
and a pair of 12-byte runs sits at +392 and +1372 of every stride, which fixes
the slot size and puts the first slot at 264. Then by the game's own code:
`0x003F04F8` copies **17,120** bytes out of the global at `0x0073DE28`, and

    184 + 17120 + 1128 == 18432        17120 == 80 + 6 * 2840

closes on both sides. The two derivations never saw each other. That is the
only reason this module states the layout as fact -- see
[[one-measurement-is-not-a-property]]; the stride alone would have been one
measurement.

SIX SLOTS, AND WHY THEY ARE PROFILES. The executable holds six default profile
names in a row at `0x004170F4` -- RED, BILLY, JOHN, CAROLYN, WYATT, JIMMY --
followed by `profile%d`, `profile_message` and `ProfileColor`. Six names, six
slots, and the one used slot on the developer's card carries the string `RED` at
+0x714. That is why `name` is a decoded field and not a candidate.

MONEY IS AT +0x68, AND IT IS NOT IN A SLOT. On 2026-09-20 the game was booted
from a COPY of this card and driven to its SHOP screen, which read
**BANK BALANCE $677** (`08_ForkLab/savelab/shots/09_shop_bank_balance_677.png`).
The card holds 677 at file offset **0x68**, and 677 appears exactly ONCE in all
18,432 bytes. So money lives in the 184-byte prefix -- the ACTIVE game's state
-- not in a profile slot.

**`+0x2f4` is falsified.** 752 at slot 0 +0x2f4 was the leading money candidate
on file evidence alone, and it is wrong: the game showed 677. That is what the
boot was for. Whatever +0x2f4 counts, it is not the money the shop spends, and
it goes back to being an unlabelled integer rather than being quietly
re-pointed at some other plausible meaning.

The stats enum is still only an enum: `StatNameToIndex` at `0x001831E0` walks a
28-entry alphabetical table, so MONEY_COLLECTED is index 15 and MONEY_SPENT is
16 -- a name-to-index map, not an address. Nothing yet says where those 28
values live.

**No checksum blocks an edit, and this was settled by booting one.** Six
algorithms over four spans matched no word in the file, which was only weak
evidence. Then a card written by this module -- money changed to 4242 -- was
booted on 2026-09-20 and the game's shop showed $4242. Either there is no
checksum over this data or the game does not check it on load. Either way
nothing here needs to fix one up.
"""

import struct

SAVE_PATH = "BASLUS-20500RDR/BASLUS-20500RDR"
SAVE_LEN = 18432

# THE XBOX FILE (2026-11-24). The Xbox game keeps the same save as `swat.sav` in its save folder
# (E:\UDATA\5454001a\<save>\swat.sav -- the recomp's hdd folder beside RDR.exe), 17,160 bytes: the PS2 file's
# first 17,160 bytes, offset for offset -- the 184-byte prefix (money at +0x68), the 80-byte header and the six
# 2,840-byte slots from 264 (each slot's `?none` at +1891 on both, the slots at the same 2,840 stride) -- with slot
# 5's last 144 bytes (its scratch tail) and the PS2's 1,128-byte trailer not written. Every field named here ends
# by +1,844 of its slot, inside what the Xbox file holds. Read and written at its own length.
XBOX_SAVE_NAME = "swat.sav"
XBOX_SAVE_LEN = 17160

# THE XBOX SIGNATURE (2026-11-24). The last 20 bytes of swat.sav are an HMAC-SHA1 of the 17,140 before them, and
# the game shows "no saves" when they do not match: an edit written without them made the game skip the save. In
# the recomp the key is 16 zero bytes -- its kernel's signature keys are left zero, so the game's XCalcSignature
# ends up keyed with zeros (found by trying the keys on a save the game wrote: only that one reproduces its
# signature). Every Xbox save written here is signed again.
XBOX_SIG_OFF = XBOX_SAVE_LEN - 20
XBOX_SIG_KEY = bytes(16)


def xbox_signature(data, key=XBOX_SIG_KEY):
    """The 20-byte signature of an Xbox swat.sav (its first 17,140 bytes)."""
    import hashlib
    import hmac
    return hmac.new(key, bytes(data[:XBOX_SIG_OFF]), hashlib.sha1).digest()

PREFIX_LEN = 184
STRUCT_OFF = 184
STRUCT_LEN = 17120
STRUCT_HEADER_LEN = 80
SLOT_BASE = 264
SLOT_LEN = 2840
SLOT_COUNT = 6
TRAILER_OFF = 17304
TRAILER_LEN = 1128

# The six names the game offers, in table order at 0x004170F4.
DEFAULT_NAMES = ["RED", "BILLY", "JOHN", "CAROLYN", "WYATT", "JIMMY"]

# `StatNameToIndex`, 0x001831E0: 28 entries, alphabetical, index is the enum.
# Recorded because it is real and it is the thread anyone continuing this work
# should pull. It is NOT wired to an offset -- see the module docstring.
STAT_NAMES = [
    "BACK_SHOTS", "BEST_COMBO", "CARDS_COLLECTED", "DISTANCE_TRAVELED",
    "DUELS_WON", "ENEMIES_KILLED", "FAVORITE_CHARACTER", "FAVORITE_POKER_RULES",
    "FAVORITE_WEAPON", "FOCUS_GAINED", "GAMES_PLAYED", "GATLING_SHOTS",
    "HATED_ENEMY", "HEAD_SHOTS", "MELEE_HITS", "MONEY_COLLECTED",
    "MONEY_SPENT", "NUM_SPEC_MOVES_USED", "PCT_JOURNAL_COMPLETE",
    "PEOPLE_POISONED", "PEOPLE_THROWN", "SHOTS_FIRED", "SHOTS_HIT",
    "TIME_FROZEN", "TIME_ON_FIRE", "TIME_PLAYING", "TIME_POISONED",
    "TIMES_KILLED",
]

# Fields that live in the 184-byte prefix, outside any profile slot. These
# are the ACTIVE game's state rather than a saved profile's.
#
# (offset in file, kind, key, label, confidence, why)
FILE_FIELDS = [
    (0x68, "u32", "money", "Money", "decoded",
     "The game was booted from a copy of this card on 2026-09-20 and its SHOP "
     "screen read BANK BALANCE $677 (08_ForkLab/savelab/shots/"
     "09_shop_bank_balance_677.png). The card holds 677 at file +0x68, and 677 "
     "occurs exactly ONCE in all 18,432 bytes. The save file is mirrored "
     "byte-for-byte in EE RAM at 0x0073DE28, so the number on screen and the "
     "number at this offset are the same word."),
]

DECODED = "decoded"      # something outside this file agrees
CANDIDATE = "candidate"  # the type is certain, the meaning is not
UNKNOWN = "unknown"

# (offset in slot, kind, key, label, confidence, why)
SLOT_FIELDS = [
    (0x020, "u32", "in_use", "Slot in use", CANDIDATE,
     "8 in the one used slot, 0 in all five empty ones. One example of each; "
     "the value 8 is unexplained, so this reads it but never writes it."),
    (0x094, "f32", "f094", "float +0x94", CANDIDATE, "1.0 used, 0.0 empty"),
    (0x0cc, "f32", "f0cc", "float +0xcc", CANDIDATE, "0.88 used, 0.0 empty"),
    (0x0e4, "f32", "f0e4", "float +0xe4", CANDIDATE, "0.85 used, 0.0 empty"),
    (0x0f8, "f32", "f0f8", "float +0xf8", CANDIDATE, "0.9056 used, 0.0 empty"),
    (0x120, "f32", "f120", "float +0x120", CANDIDATE, "1.0 used, 0.0 empty"),
    (0x194, "str8", "level", "Last level (a record)", DECODED,
     "holds `lvl_hng`, which is one of the 82 `lvl_*` identifiers in the "
     "game's own string table, so the field is a level id -- but it does not "
     "choose the level that loads: an Xbox save set to `lvl_hng` loaded "
     "lvl_sher70 (2026-11-24). It reads as the last level played; the level "
     "the game continues on comes from somewhere else (its story progress)."),
    (0x19c, "str8", "s19c", "string +0x19c", CANDIDATE, "holds `new`"),
    (0x1e8, "u32", "u1e8", "u32 +0x1e8", CANDIDATE,
     "7 on the played profile. The ONE numeric field profile creation sets: "
     "watching the game make a new profile (BILLY) live, this came out 5 "
     "while every other number in the slot stayed 0. So 5 is its born value "
     "and 7 is where play took it. Still unnamed -- knowing a field's default "
     "is not knowing what it counts."),
    (0x1ec, "u32", "u1ec", "u32 +0x1ec", CANDIDATE, "11"),
    (0x1f0, "u32", "u1f0", "u32 +0x1f0", CANDIDATE, "9"),
    (0x2dc, "f32", "p_x", "float +0x2dc", CANDIDATE,
     "95.06. With +0x2e0 and +0x2e8 this looks like a position, and the gap "
     "at +0x2e4 is why that stays a guess."),
    (0x2e0, "f32", "p_y", "float +0x2e0", CANDIDATE, "232.31"),
    (0x2e8, "f32", "p_z", "float +0x2e8", CANDIDATE, "221.67"),
    (0x2f4, "u32", "u2f4", "u32 +0x2f4", CANDIDATE,
     "752. This WAS the leading money candidate and it is now FALSIFIED: with "
     "this save loaded the game showed $677, not $752, and 677 is at file "
     "+0x68. Still unknown, still the head of thirteen consecutive integers -- "
     "but whatever it counts, it is not the money the shop spends."),
    (0x2f8, "u32", "u2f8", "u32 +0x2f8", CANDIDATE, "102"),
    (0x2fc, "u32", "u2fc", "u32 +0x2fc", CANDIDATE, "78"),
    (0x300, "u32", "u300", "u32 +0x300", CANDIDATE, "38"),
    (0x304, "u32", "u304", "u32 +0x304", CANDIDATE, "16"),
    (0x308, "u32", "u308", "u32 +0x308", CANDIDATE, "20"),
    (0x30c, "u32", "u30c", "u32 +0x30c", CANDIDATE, "4"),
    (0x314, "u32", "u314", "u32 +0x314", CANDIDATE, "75"),
    (0x318, "u32", "u318", "u32 +0x318", CANDIDATE, "2"),
    (0x31c, "u32", "u31c", "u32 +0x31c", CANDIDATE, "5"),
    (0x320, "u32", "u320", "u32 +0x320", CANDIDATE, "5"),
    (0x324, "u32", "u324", "u32 +0x324", CANDIDATE, "2"),
    (0x328, "f32", "f328", "float +0x328", CANDIDATE, "84.0"),
    (0x714, "str8", "name", "Profile name", DECODED,
     "holds `RED`, the first of the six default profile names the executable "
     "lists at 0x004170F4 -- six names, six slots."),
]

_BY_KEY = dict((f[2], f) for f in SLOT_FIELDS)

# How much of a string field we will read or write. 32 is the span between
# +0x194 and the next thing we have ever seen set; it is a cap this module
# imposes, not a length the game is known to use.
STR_MAX = 32


class SaveError(Exception):
    """The blob is not an RDR save, or an edit will not fit."""


def _read_str(buf, off):
    end = buf.find(b"\x00", off, off + STR_MAX)
    if end < 0:
        end = off + STR_MAX
    return buf[off:end].decode("ascii", "replace")


class Slot(object):
    """One of the six profile slots, decoded as far as it honestly goes."""

    def __init__(self, index, raw):
        self.index = index
        self.raw = raw
        self.fields = {}
        for off, kind, key, label, conf, why in SLOT_FIELDS:
            if kind == "u32":
                val = struct.unpack_from("<I", raw, off)[0]
            elif kind == "f32":
                val = struct.unpack_from("<f", raw, off)[0]
            else:
                val = _read_str(raw, off)
            self.fields[key] = val

    @property
    def used(self):
        """Whether the game has written anything here.

        Judged by comparing against an all-empty slot, not by trusting
        +0x20 -- an empty slot is byte-identical across all five of them, so
        `raw != empty` is the stronger test and it needs no theory of what 8
        means.
        """
        return self.fields["in_use"] != 0 or self.fields["name"] != ""

    def describe(self):
        return "slot %d  %-8s %-10s %s" % (
            self.index, self.fields["name"] or "-",
            self.fields["level"] or "-", "used" if self.used else "empty")


class Save(object):
    """The 18,432-byte save blob."""

    def __init__(self, blob):
        if len(blob) not in (SAVE_LEN, XBOX_SAVE_LEN):
            raise SaveError("save is %d bytes, expected %d (PS2) or %d (Xbox swat.sav)"
                            % (len(blob), SAVE_LEN, XBOX_SAVE_LEN))
        self.platform = "xbox" if len(blob) == XBOX_SAVE_LEN else "ps2"
        self.blob = bytearray(blob)
        # an Xbox save: was it signed as the recomp's game signs (None on PS2)?
        self.signed = (bytes(self.blob[XBOX_SIG_OFF:]) == xbox_signature(self.blob)) if self.platform == "xbox" else None
        self.slots = [Slot(i, bytes(self.slot_bytes(i))) for i in range(SLOT_COUNT)]
        self.fields = self._read_file_fields()

    def _read_file_fields(self):
        out = {}
        for off, kind, key, _label, _conf, _why in FILE_FIELDS:
            if kind == "u32":
                out[key] = struct.unpack_from("<I", self.blob, off)[0]
            elif kind == "f32":
                out[key] = struct.unpack_from("<f", self.blob, off)[0]
            else:
                out[key] = _read_str(self.blob, off)
        return out

    def set_file_field(self, key, value):
        """Write a field that lives outside any profile slot, e.g. money."""
        for off, kind, k, label, _conf, _why in FILE_FIELDS:
            if k != key:
                continue
            if kind == "u32":
                v = int(value)
                if not 0 <= v <= 0xFFFFFFFF:
                    raise SaveError("%s: %d does not fit in a u32" % (label, v))
                struct.pack_into("<I", self.blob, off, v)
            elif kind == "f32":
                struct.pack_into("<f", self.blob, off, float(value))
            else:
                raise SaveError("%s: string file fields are not writable" % label)
            self.fields = self._read_file_fields()
            return
        raise SaveError("no file field named %r" % key)

    @classmethod
    def from_card(cls, card):
        return cls(card.read_file(SAVE_PATH))

    def slot_bytes(self, index):
        if not 0 <= index < SLOT_COUNT:
            raise SaveError("slot %d is out of range 0..%d" % (index, SLOT_COUNT - 1))
        off = SLOT_BASE + index * SLOT_LEN
        return self.blob[off:off + SLOT_LEN]

    def set_field(self, index, key, value):
        """Write one decoded or candidate field. Same width, always."""
        if key not in _BY_KEY:
            raise SaveError("no field named %r" % key)
        off, kind, _, label, conf, _why = _BY_KEY[key]
        base = SLOT_BASE + index * SLOT_LEN + off
        if kind == "u32":
            v = int(value)
            if not 0 <= v <= 0xFFFFFFFF:
                raise SaveError("%s: %d does not fit in a u32" % (label, v))
            struct.pack_into("<I", self.blob, base, v)
        elif kind == "f32":
            struct.pack_into("<f", self.blob, base, float(value))
        else:
            s = str(value).encode("ascii", "replace")
            if len(s) >= STR_MAX:
                raise SaveError("%s: %r is longer than %d bytes"
                                % (label, value, STR_MAX - 1))
            # Only the characters plus one terminator. Whatever the game left
            # past the terminator is left exactly where it was -- this writer
            # does not clear a field it does not understand.
            self.blob[base:base + len(s) + 1] = s + b"\x00"
        self.slots[index] = Slot(index, bytes(self.slot_bytes(index)))

    def to_bytes(self):
        if self.platform == "xbox":
            # signed again over what it holds now: the game skips a save whose signature does not match
            self.blob[XBOX_SIG_OFF:] = xbox_signature(self.blob)
        return bytes(self.blob)

    def report(self):
        """The money line, then one line per slot, for a terminal."""
        out = ["money  $%d" % self.fields.get("money", 0)]
        out += [s.describe() for s in self.slots]
        return out
