#!/usr/bin/env python3
r"""Live Memory Watch overlay (Research code mod).  The GAME evaluates the watch table once per frame (pointer chains
followed with null/range/alignment checks); this tool reads the results block over PINE and draws it.

    py -3 scripts/rdr_watch.py                 # live table in the terminal, ~10 refreshes a second
    py -3 scripts/rdr_watch.py --overlay       # a small always-on-top window you can park over PCSX2 (Tk)
    py -3 scripts/rdr_watch.py --once          # one snapshot (scripts / tests)
    py -3 scripts/rdr_watch.py --savestate X.p2s --once
Which values: 02_Modded/research_config.json "watch": [{"label":"player X","addr":"0x0079E240","type":"f32","chain":["0x54"]}, ...]
(type u32|s32|f32|u16|u8|s16; chain = up to 2 offsets: read the pointer at addr, add offset, [read that pointer, add offset], then read the value).
Status: ok / null ptr (a pointer in the chain was 0, e.g. no player yet) / bad addr (outside RAM or misaligned) -- nothing is read unsafely.
Read-only: only PINE read commands are sent."""
import sys, time
import rdr_research as RR


def lines(res):
    frames, rows = res
    out = ["Memory Watch   frame counter %d" % frames]
    for r in rows:
        out.append("%-16s %-22s @%08X" % (r["label"][:16], r["text"], r["final"]))
    return out


def main(argv):
    cfg = RR.load_config(argv[argv.index("--config") + 1] if "--config" in argv else None)
    src = RR.open_source(argv)
    if "--overlay" in argv:
        import tkinter as tk
        root = tk.Tk()
        root.title("RDR Memory Watch")
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.8)
        except Exception:      # noqa: BLE001
            pass
        root.configure(bg="black")
        lab = tk.Label(root, text="", justify="left", font=("Consolas", 11), fg="#9dff9d", bg="black", anchor="nw")
        lab.pack(padx=8, pady=6)

        def tick():
            try:
                res = RR.read_watch(src, cfg)
                lab.config(text="\n".join(lines(res)) if res else "Memory Watch is not in this game build")
            except Exception as ex:   # noqa: BLE001
                lab.config(text="waiting for PCSX2 (PINE): %s" % ex)
            root.after(100, tick)
        tick()
        root.mainloop()
        return 0
    while True:
        res = RR.read_watch(src, cfg)
        if res is None:
            print("no Memory Watch in this game (build the disc with the 'Memory Watch (Research)' code mod)")
            return 2
        print(("" if "--once" in argv else "\033[2J\033[H") + "\n".join(lines(res)), flush=True)
        if "--once" in argv:
            return 0
        time.sleep(0.1)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
