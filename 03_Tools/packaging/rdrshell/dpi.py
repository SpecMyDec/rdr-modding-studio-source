"""Draw at the screen's real pixel density.

Measured on the developer's machine: the panel is 1920x1080 running at 125% scale.
A process that has not declared DPI awareness is told the screen is 1536x864,
draws at that size, and has everything it drew stretched 1.25x by the desktop
compositor. Text is resampled rather than rendered, and a window asked for at
900 px arrives as 1125 physical pixels of upscaled 900-pixel content.

    GetSystemMetrics, unaware : 1536 x 864
    GetSystemMetrics, aware   : 1920 x 1080

Two things make this its own module rather than a few lines in shell.py:

  1. It has to run before pywebview is imported. That import starts pythonnet
     and the .NET WinForms host, and the first of those to touch the display
     locks the process's awareness in. Called later the API returns an error
     and the app stays blurry.
  2. PyInstaller's windowed bootloader does not declare awareness in the
     exe manifest, so a frozen build is unaware by default even though a
     hand-written one would not be.

Shipping should ALSO put dpiAware/dpiAwareness in the exe manifest, which is
the only way to be aware before the very first window; this call is the
belt-and-braces that works without a manifest edit.
"""

import os


def make_dpi_aware():
    """Returns the mode that was set, or None. Never raises."""
    if os.name != "nt":
        return None
    import ctypes
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE. Windows 8.1+.
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:                                      # noqa: BLE001
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:                                      # noqa: BLE001
        pass
    return None


def report():
    """What the process currently thinks the screen is. For diagnostics."""
    if os.name != "nt":
        return {}
    import ctypes
    u = ctypes.windll.user32
    return {"screen_as_seen": "%d x %d" % (u.GetSystemMetrics(0),
                                           u.GetSystemMetrics(1))}


if __name__ == "__main__":
    print("before:", report())
    print("set   :", make_dpi_aware())
    print("after :", report())
