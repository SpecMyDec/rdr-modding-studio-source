"""Health check: is everything where the toolkit expects it?"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rdrstudio import config as cfg  # noqa: E402


def mb(n):
    return "%.1f MB" % (n / 1048576.0)


def check(label, path, need_file=True):
    exists = os.path.isfile(path) if need_file else os.path.isdir(path)
    mark = "OK  " if exists else "MISS"
    extra = ""
    if exists and need_file:
        extra = "  " + mb(os.path.getsize(path))
        if not os.access(path, os.W_OK):
            extra += "  [read-only]"
    print("  [%s] %-16s %s%s" % (mark, label, path, extra))
    return exists


def _note(label, text):
    """Report something absent that is allowed to be absent."""
    print("  [ -- ] %-16s %s" % (label, text))
    return False


def main():
    print("RDR Modding Studio - setup check\n")
    ok = True
    ok &= check("vanilla ISO", cfg.VANILLA_ISO)
    ok &= check("modded ISO", cfg.MODDED_ISO)
    # PCSX2 is OPTIONAL and always has been for the shipped app -- setup offers
    # to skip it and everything except Play and Boot test works without it. It
    # was counted as required here only because config.py used to hardcode one
    # machine's install path, so it could never be absent on that machine.
    # Failing the whole check for a missing emulator also took the test suite
    # down with it, which is a lot of noise for a feature the product calls
    # optional out loud.
    have_emu = check("PCSX2", cfg.PCSX2_EXE) if cfg.PCSX2_EXE else \
        _note("PCSX2", "not configured -- optional; set it in the app's "
                       "App setup, or RDR_PCSX2_EXE")
    if have_emu:
        check("emulog", cfg.emulog())
    check("extracted", cfg.EXTRACTED, need_file=False)
    print("  [%s] python           %s" % ("OK  ", sys.version.split()[0]))

    if not ok:
        print("\nA disc image is missing. Point RDR_ROOT at your project "
              "folder, or run the app's setup.")
        return 1

    print("\nOpening the working ISO...")
    from rdrstudio import Game
    g = Game(cfg.MODDED_ISO)
    print("  volume    : %s" % g.iso.volume_id)
    print("  sectors   : %d" % g.iso.total_sectors)
    print("  archives  : %d" % len(g.archives()))
    swat = g.archive("SWAT.DAT")
    print("  SWAT.DAT  : %d entries (expect 47115)" % swat.count)
    if swat.count != 47115:
        print("  WARNING: unexpected entry count -- is this the USA retail disc?")

    if os.path.exists(cfg.VANILLA_ISO) and os.access(cfg.VANILLA_ISO, os.W_OK):
        print("\n  NOTE: the vanilla ISO is writable. Mark it read-only so it")
        print("        can never be damaged:")
        print('        Set-ItemProperty "%s" -Name IsReadOnly -Value $true' % cfg.VANILLA_ISO)

    print("\nAll good. Run:  python RDR_Studio.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
