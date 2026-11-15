#!/usr/bin/env python3
r"""Dump the Function-Hit Logger (Research code mod) from a running PCSX2, a savestate or a RAM dump.

    py -3 scripts/rdr_hitlog.py                       # live over PINE: per-function counts, last args/caller/return, last 128 calls
    py -3 scripts/rdr_hitlog.py --watch               # refresh every second (counts moving = the function is live)
    py -3 scripts/rdr_hitlog.py --savestate X.p2s     # from a savestate (SavestateCompressionType = 0)
    py -3 scripts/rdr_hitlog.py --out log.txt         # also write the readable log to a file
    py -3 scripts/rdr_hitlog.py --json                # machine-readable
Which functions are hooked is decided at BUILD time (Code Mods tab; 02_Modded/research_config.json "hitlog").
Read-only: only PINE read commands are sent."""
import json, sys, time
import rdr_research as RR


def main(argv):
    cfg = RR.load_config(argv[argv.index("--config") + 1] if "--config" in argv else None)
    src = RR.open_source(argv)
    while True:
        d = RR.read_hitlog(src, cfg)
        if d is None:
            print("no Function-Hit Logger in this game (build the disc with the 'Function-Hit Logger (Research)' code mod)")
            return 2
        txt = json.dumps(d, indent=1) if "--json" in argv else RR.hitlog_text(d)
        print(("\033[2J\033[H" if "--watch" in argv else "") + txt, flush=True)
        if "--out" in argv:
            open(argv[argv.index("--out") + 1], "w", encoding="utf-8").write(RR.hitlog_text(d) + "\n")
        if "--watch" not in argv:
            return 0
        time.sleep(1.0)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
