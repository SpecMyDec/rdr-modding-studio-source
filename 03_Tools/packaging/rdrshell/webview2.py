"""Is the Edge WebView2 runtime installed?

The whole window is a WebView2 control. Without the runtime, pywebview's
edgechromium backend fails somewhere inside pythonnet and the user gets a
blank window, or nothing at all, with no clue why.

WebView2 is present by default on Windows 11 and on any Windows 10 that has
had a recent Edge, but it is NOT universal on older Windows 10 -- and "older
Windows 10" is exactly the machine that still has a PS2 emulator on it. So the
app checks before it builds a window, and if it is missing it says so in a
native message box with the download link, which is a sentence the user can
act on instead of a blank rectangle they cannot.

Detection is the registry key Microsoft documents for exactly this purpose:
a non-empty `pv` value under the WebView2 runtime's update client GUID. Three
locations, because per-machine 64-bit, per-machine 32-bit and per-user
installs each write a different one.
"""

import ctypes
import os

# The Evergreen WebView2 Runtime's client id. Fixed by Microsoft; it is the
# documented way to detect the runtime and it does not change between versions.
CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

_KEYS = [
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\%s" % CLIENT),
    ("HKLM", r"SOFTWARE\Microsoft\EdgeUpdate\Clients\%s" % CLIENT),
    ("HKCU", r"Software\Microsoft\EdgeUpdate\Clients\%s" % CLIENT),
]

DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

MISSING_TEXT = (
    "RDR Modding Studio draws its window with the Microsoft Edge WebView2 "
    "runtime, and this computer does not have it.\n\n"
    "It is a free Microsoft download, it takes about a minute, and it is "
    "already on most Windows 11 machines:\n\n"
    "    %s\n\n"
    "Install the \"Evergreen Bootstrapper\", then start RDR Modding Studio "
    "again.\n\n"
    "(Nothing is wrong with your download. The tool itself is fine -- it just "
    "has nowhere to draw yet.)" % DOWNLOAD_URL
)


def version():
    """The installed runtime version string, or None."""
    try:
        import winreg
    except ImportError:
        return None
    roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    for root, path in _KEYS:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY, 0):
            try:
                with winreg.OpenKey(roots[root], path, 0,
                                    winreg.KEY_READ | view) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
                    if pv and pv not in ("0.0.0.0", ""):
                        return str(pv)
            except OSError:
                continue
    return None


def present():
    return version() is not None


def complain(text=MISSING_TEXT):
    """A native message box, because there is no web view to put a page in.

    MB_ICONWARNING | MB_OK | MB_SETFOREGROUND.
    """
    try:
        ctypes.windll.user32.MessageBoxW(
            None, text, "RDR Modding Studio", 0x30 | 0x0 | 0x10000)
    except Exception:                                      # noqa: BLE001
        pass
    try:
        os.startfile(DOWNLOAD_URL)                         # noqa: S606
    except Exception:                                      # noqa: BLE001
        pass


def preflight(skip=False):
    """Returns the version string if we can proceed, else None after telling
    the user why not. `skip` exists so --selfcheck (which never opens a
    window) is not blocked by a missing runtime."""
    v = version()
    if v or skip:
        return v
    complain()
    return None


if __name__ == "__main__":
    print(version() or "WebView2 runtime NOT found")
