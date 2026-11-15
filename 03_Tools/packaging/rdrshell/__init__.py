"""rdrshell -- the desktop wrapper around RDR Modding Studio.

Nothing in here knows anything about Red Dead Revolver. It does four jobs:

    paths     where a *downloaded* app is allowed to keep things
    verify    is the file the user picked really the right disc image
    server    start the existing Studio in this process on a free port
    shell     the window, and the first-run flow that fills `paths` in

The Studio itself (rdrstudio/) is imported unchanged. This package adds no
requirement to it and patches only two module-level constants -- the web root
and the config paths -- both of which are read at call time.
"""

__version__ = "0.1.0-prototype"
