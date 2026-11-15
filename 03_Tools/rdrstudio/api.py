"""The new-route envelope: success, refusal, fault, and the frozen `plan`.

Every route added by the 2026-08 redesign answers in one shape. The old ~115
routes are untouched and keep theirs; nothing here is imported by them.

Three answers, and only three:

    success   HTTP 200  {"ok": true,  ...}
    refusal   HTTP 200  {"ok": false, "problem": {...}}
    fault     HTTP 500  {"ok": false, "problem": {"code": "server_fault", ...}}

A refusal is NOT an exception. That distinction is the whole point of this
module: the old routes signalled "this will not fit" by raising, the front end
caught it, and five carefully written byte-count messages became unreachable
dead code behind a generic "Could not encode:" prefix. Here a refusal is a
normal 200 answer carrying a `problem`, so the message the server took the
trouble to write is the message the user reads.

`problem.detail` is the raw technical string -- exception reprs, tracebacks,
`<_io.BytesIO object at 0x...>`. It is for a disclosure triangle, never for
the sentence a person reads first.
"""

import json
import time

# The complete, frozen set. A code outside this set is a bug; the constant is
# here so a typo fails loudly in the one test that checks it.
CODES = (
    "bad_request",        # malformed body / missing required key
    "not_found",          # archive, file, uid or id does not exist
    "unknown_type",       # Type is not a .cr in this archive
    "unknown_table",      # Table does not resolve to a .tbl in this archive
    "unknown_value",      # any other picker value outside its legal set
    "no_enm_here",        # this archive has no local .enm (the 26-archive case)
    "slot_overflow",      # the write does not fit its slot
    "not_editable",       # the field exists but is refused for a stated reason
    "disc_busy",          # PCSX2 holds the working ISO
    "token_expired",      # a transfer token was reused or timed out
    "not_in_this_build",  # feature omitted from this build
    "server_fault",       # a bug
)

RUNGS = ("measured", "established", "inferred", "untested", "dead")
CONTROLS = ("pick", "text", "int", "float", "bool", "vec3", "list")
OPTIONS_FROM = (None, "cast.characters", "cast.behaviours", "cast.skins",
                "cast.drops", "enum")


class Refusal(Exception):
    """Something the user can fix. Renders as HTTP 200 with ok:false.

    Carrying `plan` matters: a slot_overflow refusal must still show what it
    would have cost, or the user is told "it does not fit" with no number and
    no way to judge how far over they are.
    """

    def __init__(self, code, message, fix="", detail="", **extra):
        Exception.__init__(self, message)
        if code not in CODES:
            raise ValueError("refusal code %r is not in the frozen set" % code)
        self.code = code
        self.message = message
        self.fix = fix
        self.detail = detail
        self.extra = extra

    def body(self):
        d = dict(ok=False, problem=dict(code=self.code, message=self.message,
                                        fix=self.fix, detail=self.detail))
        d.update(self.extra)
        return d


class DiscLocked(RuntimeError):
    """The working ISO cannot be written, with the reason already in the
    user's terms.

    A RuntimeError on purpose: `routes2._busy` files it as a refusal by
    reading the text, and every existing `except RuntimeError` keeps working.
    What it adds is the two halves separately, so the route layer can put the
    REAL cause where the user reads it instead of substituting a guess and
    demoting the truth to `detail`. `Session.locked_problem()` is what fills
    it in; there are three causes and only one of them is the emulator.
    """

    def __init__(self, message, fix=""):
        RuntimeError.__init__(self, (message + " " + fix).strip())
        self.message = message
        self.fix = fix


def ok(**fields):
    d = dict(ok=True)
    d.update(fields)
    return d


def fault(ex, where=""):
    import traceback
    return dict(ok=False, problem=dict(
        code="server_fault",
        message="Something went wrong%s." % (" " + where if where else ""),
        fix="",
        detail="".join(traceback.format_exception_only(type(ex), ex)).strip()
               or "%s: %s" % (type(ex).__name__, ex)))


def is_dry(body):
    """`dry` defaults to TRUE when the key is absent.

    Writing requires the caller to say so out loud. Constraint 3 expressed in
    the wire format rather than in a code review: forgetting to dry-run is not
    discouraged here, it is impossible.
    """
    v = body.get("dry", True)
    if v is None:
        return True
    return bool(v)


def require(body, *keys):
    missing = [k for k in keys if body.get(k) is None]
    if missing:
        raise Refusal("bad_request",
                      "This needs %s." % _english(missing),
                      "Send %s in the request body."
                      % _english(missing, quote=True))
    return [body[k] for k in keys]


def _english(items, quote=False):
    items = ['"%s"' % i for i in items] if quote else list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------------- the plan

def plan(writes, summary, label, steps=1):
    """The frozen plan object.

    `writes` rows come straight out of `Level.preview()` -- this is a
    projection of a report that already exists, not a second slot-checker.
    Sorted by file, always, so two runs of the same edit render identically.
    """
    rows = sorted(writes, key=lambda w: w["file"])
    return dict(writes=rows,
                fits=all(w["fits"] for w in rows),
                summary=summary,
                undo=dict(steps=steps, label=label))


def write_row(archive, filename, before, after, stored, slot):
    return dict(archive=archive, file=filename,
                bytes_before=int(before), bytes_after=int(after),
                stored=int(stored), slot=int(slot),
                headroom=int(slot) - int(stored), fits=int(stored) <= int(slot))


def rows_from_report(archive, report):
    """`Level.preview()`'s per-file dict -> plan.writes rows."""
    return [write_row(archive, fn, r["bytes_before"], r["bytes_after"],
                      r["stored"], r["slot"])
            for fn, r in report.items()]


def stored_size(entry, data):
    """How many bytes `data` occupies in this archive entry's slot."""
    if getattr(entry, "compressed", False):
        import zlib
        co = zlib.compressobj(9, 8, -15)
        return len(co.compress(data) + co.flush())
    return len(data)


def overflow(p):
    """The refusal for a plan that does not fit, with the numbers in it."""
    worst = None
    for w in p["writes"]:
        if not w["fits"] and (worst is None or w["headroom"] < worst["headroom"]):
            worst = w
    if worst is None:
        return Refusal("slot_overflow", "This does not fit.", "", "", plan=p)
    over = worst["stored"] - worst["slot"]
    return Refusal(
        "slot_overflow",
        "%s needs %s bytes of a %s-byte slot (over by %s)."
        % (worst["file"], "{:,}".format(worst["stored"]),
           "{:,}".format(worst["slot"]), "{:,}".format(over)),
        "Shorten a name or a comment, remove an optional field, or put this "
        "in a file with more room.",
        "", plan=p)


# ------------------------------------------------------------------- dispatch

def respond(handler, fn, where=""):
    """Run a new-route body and send whatever shape it produced.

    One place, so no new route can accidentally answer in the old style, and
    so a genuine bug becomes a 500 fault with its detail tucked away rather
    than a stack trace pasted into a toast.
    """
    try:
        body = fn()
    except Refusal as r:
        return handler._send(200, r.body())
    except Exception as ex:                       # noqa: BLE001 - fault path
        import traceback
        traceback.print_exc()
        return handler._send(500, fault(ex, where))
    if body is None:
        # The route already wrote its own response -- `transfer_export` sends
        # raw file bytes with a real Content-Type. Sending a second response
        # on the same socket after it would corrupt the first one, so None
        # means "handled, say nothing more".
        return None
    if isinstance(body, tuple):                   # (code, body)
        return handler._send(*body)
    return handler._send(200, body)


# ----------------------------------------------------------------- tokens

class TokenStore:
    """Single-use, short-lived handles on an already-encoded transfer.

    The checked plan is the applied plan. `transfer_apply` must not re-encode
    and must not re-read the payload, because a second encode is a second
    chance to produce different bytes than the ones the user was shown and
    agreed to.
    """

    TTL = 600.0

    def __init__(self):
        self._items = {}
        self._seq = 0

    def put(self, payload):
        self._sweep()
        self._seq += 1
        tok = "t_%08x" % ((int(time.time() * 1000) ^ (self._seq * 2654435761))
                          & 0xFFFFFFFF)
        self._items[tok] = (time.time(), payload)
        return tok, int(self.TTL)

    def take(self, tok):
        self._sweep()
        item = self._items.pop(tok, None)
        if item is None:
            raise Refusal(
                "token_expired",
                "That transfer is no longer valid.",
                "Choose the file again -- checking is free and never writes.")
        return item[1]

    def _sweep(self):
        cutoff = time.time() - self.TTL
        for k in [k for k, v in self._items.items() if v[0] < cutoff]:
            self._items.pop(k, None)


def dumps(obj):
    return json.dumps(obj)
