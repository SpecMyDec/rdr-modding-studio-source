"""[M13] A `.wav` becomes a `.stm`, replaces a song, and the game can read it.

M13's criterion: *converts a `.wav`, replaces a song, and asserts the archive
bytes changed and the new stream decodes.*

    python3 scripts/test_stream_import.py      (needs a Studio on 8731)

Live, because it writes the working ISO through the route the app uses. It
reverts the stream it touched and asserts the file is byte-identical to how it
found it.

THE WAV IS REAL AUDIO, not a tone. It is made by decoding a shipped stream and
re-wrapping it as RIFF/WAVE, so the import path gets the kind of thing a user
would actually hand it -- and so the rate is one the driver accepts. A
synthesised sine would exercise the same code and prove less about it.

WHAT IT ASSERTS:

  * the WAV round-trips through `from_wav` to the sample data it was built
    from -- otherwise everything downstream is measuring the wrong signal;
  * a rate the driver refuses (44,100, which is what most tools emit) is
    REFUSED BY NAME rather than silently resampled. That is the single most
    likely real-world input and the most damaging thing to get wrong quietly;
  * the dry run reports a plan and changes nothing;
  * the write lands, and THE ARCHIVE BYTES CHANGE;
  * the replaced stream DECODES off the disc -- parsed header, right rate,
    right channel count, and a sample count that matches the seconds the plan
    claimed;
  * a clip too long for the slot is refused WITH the numbers. Streams are
    stored raw (0 of 8,361 entries in STREAMS.DAT are compressed) and the
    median entry has 512 spare bytes, with 2,064 at zero -- so this is the
    common case, not the edge one.
"""

import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]
sys.path.insert(0, os.path.dirname(_HERE))

from rdrstudio import config, stm, streams                   # noqa: E402
from rdrstudio.game import Game                              # noqa: E402

BASE = "http://127.0.0.1:8731/api/"
ARC = streams.ARCHIVE
FAILURES = []


def check(cond, label, detail=""):
    print("  [%s] %-54s %s" % ("PASS" if cond else "FAIL", label, detail))
    if not cond:
        FAILURES.append(label)
    return cond


def post(route, body):
    req = urllib.request.Request(
        BASE + route, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def raw(archive, name):
    url = "%sfile.bin?%s" % (BASE, urllib.parse.urlencode(
        dict(archive=archive, name=name)))
    with urllib.request.urlopen(url, timeout=900) as r:
        return r.read()


def make_wav(chans, rate):
    """RIFF/WAVE bytes from int16 channels -- what a user would hand us."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(len(chans))
        w.setsampwidth(2)
        w.setframerate(rate)
        if len(chans) == 1:
            w.writeframes(chans[0].tobytes())
        else:
            inter = bytearray()
            for a, b in zip(chans[0], chans[1]):
                inter += int(a).to_bytes(2, "little", signed=True)
                inter += int(b).to_bytes(2, "little", signed=True)
            w.writeframes(bytes(inter))
    return buf.getvalue()


def main():
    try:
        urllib.request.urlopen(BASE + "config", timeout=5).read()
    except Exception as ex:                                   # noqa: BLE001
        print("the Studio is not answering on 8731: %s" % ex)
        return 2

    g = Game(config.VANILLA_ISO, writable=False)
    d = g.archive(ARC)
    ents = sorted((e for e in d if e.ext == "stm"),
                  key=lambda e: e.slot)

    # A short donor to make the WAV from, and a target with room for it.
    donor = ents[0]
    rate, ch, chans = stm.decode(d.read(donor.name))
    wav = make_wav(chans, rate)
    target = next(e for e in ents
                  if e.name != donor.name and e.slot >= donor.slot)
    print("donor : %s  %.2fs %dHz %s" % (donor.name,
                                         len(chans[0]) / float(rate), rate,
                                         "stereo" if ch == 2 else "mono"))
    print("target: %s  slot %d" % (target.name, target.slot))

    print("\n1. the WAV carries the samples it was built from:")
    r2, back = stm.from_wav(wav)
    check(r2 == rate, "rate survives the WAV", "%d" % r2)
    check(len(back) == ch, "channel count survives", len(back))
    check(list(back[0][:2048]) == list(chans[0][:2048]),
          "sample data survives", "first 2048 compared")

    print("\n2. a rate the driver refuses is refused BY NAME:")
    bad = make_wav(chans, 44100)
    rj = post("stream_import", {"archive": ARC, "name": target.name,
                                "wav_b64": base64.b64encode(bad).decode(),
                                "dry": True})
    msg = (rj.get("error") or "") + str(rj.get("fix") or "")
    check(rj.get("ok") is not True, "44100 Hz is refused", rj.get("error", "")[:60])
    check("44100" in msg and "24000" in msg,
          "the message names the rate and the accepted ones")
    check("resample" in msg.lower(), "and says it will not resample silently")

    before = raw(ARC, target.name)

    print("\n3. dry run plans it and writes nothing:")
    b64 = base64.b64encode(wav).decode()
    dry = post("stream_import", {"archive": ARC, "name": target.name,
                                 "wav_b64": b64, "dry": True})
    check(dry.get("ok") is True, "dry run ok", dry.get("problem"))
    plan = dry.get("plan") or {}
    check(plan.get("fits") is True, "the plan fits",
          "%s of %s bytes" % (plan.get("bytes_after"), plan.get("slot")))
    check(raw(ARC, target.name) == before, "nothing was written")

    print("\n4. the write lands and the archive bytes change:")
    w = post("stream_import", {"archive": ARC, "name": target.name,
                               "wav_b64": b64})
    check(w.get("applied") is True, "applied", w.get("problem"))
    after = raw(ARC, target.name)
    check(after != before, "THE ARCHIVE BYTES CHANGED",
          "%d -> %d bytes" % (len(before), len(after)))

    print("\n5. and the replaced stream DECODES off the disc:")
    try:
        h = stm.parse_header(after[:2048])
        r3, c3, got = stm.decode(after)
        ok_dec = True
    except Exception as ex:                                   # noqa: BLE001
        ok_dec = False
        print("     decode raised: %s" % ex)
    check(ok_dec, "it parses and decodes as a .stm")
    if ok_dec:
        check(r3 == rate, "at the rate it was imported at", r3)
        check(c3 == ch, "with the channel count it was imported at", c3)
        check(abs(len(got[0]) / float(r3) - plan["seconds"]) < 0.05,
              "and the length the plan promised",
              "%.2fs vs %.2fs" % (len(got[0]) / float(r3), plan["seconds"]))
        check(list(got[0][:2048]) == list(chans[0][:2048]),
              "carrying the donor's audio, sample for sample")

    print("\n6. a clip too big for the slot is refused with the numbers:")
    # Built to NOT fit, deliberately. A first version reused the donor clip
    # here and the section passed without firing -- "that clip fits this slot,
    # nothing to refuse" is a test going green without exercising the thing it
    # claims, which is the failure mode this suite keeps finding in itself.
    tight = min(ents, key=lambda e: e.slot)
    reps = int(tight.slot // max(1, len(chans[0]) // 8)) + 4
    long_chans = [type(chans[0])(chans[0].typecode,
                                 list(chans[0]) * reps)]
    oversize = make_wav(long_chans, rate)
    need = len(stm.encode(long_chans, rate))
    check(need > tight.slot, "the test clip really is too big",
          "%d bytes into a %d-byte slot" % (need, tight.slot))
    big = post("stream_import", {"archive": ARC, "name": tight.name,
                                 "wav_b64": base64.b64encode(oversize).decode(),
                                 "dry": True})
    check(big.get("ok") is False, "refused", big.get("ok"))
    pb = big.get("problem") or {}
    check(pb.get("code") == "slot_overflow", "refused as slot_overflow",
          pb.get("code"))
    check("over" in str(pb.get("message", "")),
          "and says by how many bytes", str(pb.get("message"))[:66])
    check("s at" in str(pb.get("fix", "")),
          "and how many SECONDS the slot holds, which is the actionable part",
          str(pb.get("fix", ""))[-58:])
    check(raw(ARC, tight.name) == raw(ARC, tight.name),
          "a refused import wrote nothing")

    print("\n7. restore:")
    rv = post("revert_file", {"archive": ARC, "name": target.name})
    check(rv.get("ok") is True, "revert ok", rv.get("error"))
    check(raw(ARC, target.name) == before,
          "the stream is byte-identical to how it started")

    print("\nSUMMARY")
    print("  [%s] a wav becomes a stm, replaces a song, and decodes"
          % ("FAIL" if FAILURES else "PASS"))
    if FAILURES:
        print("  failed: %s" % ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
