# RDR Modding Studio -- User guide

```
RDR MODDING STUDIO
A modding tool and 3D level editor for Red Dead Revolver (PlayStation 2)

The full disclaimer is at the end of this guide. The short version: the
download contains no game data, you need your own disc image, and nothing
here is guaranteed.


START IT
--------

1. Unzip the whole folder somewhere you can find again -- Desktop or Documents
   is fine. Keep it together: the "_internal" folder must stay next to the
   .exe. Copying the .exe out on its own will not work.

2. Double-click "RDR Modding Studio.exe".

3. Windows will probably say "Windows protected your PC". That is not a virus
   warning. It means the app is not code-signed, which is a certificate that
   costs a few hundred pounds a year that this project does not have. Click
   "More info", then "Run anyway".

4. Answer the four setup screens once (below). After that it opens
   straight into the tool.

You do not need Python, or any other install. Everything it needs is in the
folder.


THE FOUR SETUP SCREENS, ONCE
----------------------------

  1  Before you start   The disclaimer, and a box to tick.

  2  Your disc          Point at your Red Dead Revolver .iso. It is opened
                        read-only. It checks the file really is the right
                        game -- it reads the disc's own boot ID and opens the
                        archives -- and tells you what it found.

  3  Working copy       It copies your disc once, and every edit happens on
                        that copy. Takes 15-30 seconds. It suggests the drive
                        your .iso is already on, because 2.2 GB should not
                        land on a full C: by default. You can choose anywhere.

  4  PCSX2 (optional)   Point at your emulator if you have one, and the Play
                        and Boot test buttons start working. Skip it and
                        everything else in the tool behaves exactly the same;
                        you can add it later from App setup, the gear button
                        in the top bar.



YOU NEED YOUR OWN COPY OF THE GAME
----------------------------------

This download contains no models, no textures, no audio, no text, no level
data -- nothing from the disc. It is only the tool.

You need a disc image of Red Dead Revolver (USA, SLUS-20500) made from a copy
you own. None is provided and none will ever be linked.


WHAT IT CAN DO
--------------

The tool is arranged as eight workspaces along the top. Ctrl+K jumps to any
screen by name.

  SAVE        The Save Editor, first on the left: your PS2 memory card, not
              the disc. Money is proven in-game. Fields whose meaning is not
              confirmed are folded away and marked; the card is backed up
              before every write.

  LEVEL       Open any of the 61 levels in 3D. Move, rotate, scale, duplicate
              and delete what is in them. Swap which character an enemy is and
              what it does, edit every field on it, and place new ones.
              Import your own model (.obj) over a level model, with collision
              that follows it. Replace any texture. Change the lighting.

  ANIMATIONS  Watch any clip on its skeleton. Export one, or all of them, to
              Blender (.bvh) and import your edit back.

  LOGIC       Who is in the game and how they behave: the cast, the behaviour
              tables, the missions and the rules -- including what Bounty
              Hunter mode changes and what unlocks when.

  BALANCE     The numbers: weapon damage, enemy health, shop prices. Clone a
              weapon into a new one; give a weapon your own model
              (experimental: very small meshes only, see
              Guides > Custom models).

  MEDIA       Every sound effect and voice line, the cutscenes and the still
              screens.

  DISC        The files and archives themselves. Drop your own files in, or
              search the disc and edit any file.

  MY MOD      Changes: every file your mod touches, and your edit history.
              Versions: named copies of your mod to come back to.
              Code Mods: noclip, freeze world, camera lock, slow-mo and an
              always-there Cheats menu, patched into a COPY of the disc.

Two things worth knowing before you change anything:

  * Every write is planned before it happens. You are shown what it will
    write, how many bytes it needs, whether it fits, and how many steps of
    undo it costs. If it does not fit, it refuses instead of truncating.

  * Everything carries a confidence mark. Some of what this tool knows about
    a twenty-year-old game's file formats was measured, and some of it is
    inference. The marks tell you which is which, everywhere, and they are
    not decoration.


SHARING WHAT YOU MADE
---------------------

There is no build step. Every change is on your working disc the moment you
apply it, and Play boots that disc. When you want to hand it to someone,
press "Build & share" at the top:

  Mod pack (.zip)           The one to post online. Only the files you
                            changed plus a list of what they replace; no game
                            data. Others add it with "Apply a mod pack" onto
                            their own disc, and are warned about conflicts.
                            Code Mods can ride in the same pack.
  Mod file (.rdrmod.json)   A saved version of your mod, for a backup or for
                            someone who also has this tool ("Load a mod
                            file").
  Patch (.xdelta)           For anyone. They apply it to their own copy of
                            the game with Delta Patcher or xdelta3, so you
                            never share the game itself. A .txt beside it
                            says exactly which disc it expects.
  Playable ISO (.iso)       The whole disc, for yourself or your emulator.

Exports land in Documents\RDR Mods\exports.


IF SOMETHING GOES WRONG
-----------------------

  "Reset to stock" in the top right puts your working disc back exactly as it
  came off your original. It is the one action the tool asks you to type a
  word to confirm, because it is the only one that cannot be undone.

  Undo works on everything else, one step at a time.

  MY MOD > Versions lets you name the state of the disc and come back to it
  later, and shows what is on the disc right now compared to what you saved.


IF IT DOES NOT START
--------------------

  Nothing happens at all, or a blank window appears
      Almost always the Microsoft Edge WebView2 runtime is missing. This app
      draws its window with it. It is a free Microsoft download and it is
      already on most Windows 11 machines:
          https://developer.microsoft.com/microsoft-edge/webview2/
      Install the "Evergreen Bootstrapper" and try again. The app should tell
      you this itself, in a message box, if it detects the runtime is absent.

  It closes immediately
      Check "_internal" is next to the .exe. If you moved the .exe on its own,
      move the whole folder back together.

  You want to see why
      Open the folder, type cmd in the address bar, press Enter, then:
          "RDR Modding Studio.exe" --debug

  Your antivirus ate it
      Apps built this way are sometimes quarantined on sight. If the .exe
      disappeared after unzipping, that is what happened -- allow it and
      unzip again.

  You want to start over
          "RDR Modding Studio.exe" --reset
      forgets your disc and your settings and shows the setup screens again.
      It does not delete anything you made.


WHERE IT PUTS THINGS
--------------------

  Documents\RDR Mods\
      Your versions, mods and exports. Small, portable, yours -- these are
      the things worth backing up or sending to someone.

  AppData\Local\RDR Modding Studio\
      Settings and logs, and by default the 2.2 GB working copy of the disc
      (setup screen 3 lets you put that somewhere else, and usually should).

  Your original .iso
      Never written to. Not once, by anything.

Uninstalling, or deleting the app folder, leaves both of those alone. If you
installed with the setup program, its uninstaller asks before removing the
working copy and never touches Documents\RDR Mods.


THIS IS A WORK IN PROGRESS
--------------------------

It is genuinely useful today and it is not finished. Some things are held
back because they are not ready rather than because they are secret -- editing
the doors between areas, the mission scripting browser, and the text editor
are all working but not good enough to hand over yet.

Where the tool is unsure of something it says so, with a small badge:
measured, established, inferred, untested. A value marked inferred was
reasoned out rather than read off the disc, and could be wrong.

If something breaks, MY MOD > Changes and MY MOD > Versions together will
tell you what state your working disc is in.

The Modding guide and Custom models guide (Guides button, bottom bar) hold
the longer explanations the tool's one-line notes point at.


DISCLAIMER
==========

A modding tool and 3D level editor for Red Dead Revolver (PlayStation 2,
USA, SLUS-20500).


NO GAME DATA IS INCLUDED
------------------------
This download contains no code, art, audio, text, models or level data from
Red Dead Revolver. Not one byte. Everything the tool shows you is read live,
at the moment you look at it, out of a disc image that you supply.

Nothing in this package can be used to play the game, and this package cannot
produce a playable game on its own.


YOU MUST SUPPLY YOUR OWN DISC IMAGE
-----------------------------------
Red Dead Revolver is Copyright (C) 2004 Take-Two Interactive Software, Inc.
Developed by Rockstar San Diego, from an unreleased Capcom project.

You need a disc image dumped from a copy of the game that you own. Whether
making that dump is permitted where you live is a question about your own
laws, and you must answer it for yourself.

This tool will not help you obtain a disc image. No download link will be
provided, no request for one will be answered, and none is needed to make
the tool work if you own the disc.


YOUR ORIGINAL IS NEVER MODIFIED
-------------------------------
The disc image you point at on first run is opened READ-ONLY and copied
exactly once. Every edit you make happens on that copy. If the copy is
damaged you can make a new one from the original in a single click, and
"Reset to stock" does exactly that.

The tool never writes to the file you selected. It never sends any file
anywhere. It has no telemetry, no account, no update check, and makes no
network connections of any kind. The page you look at is served from
127.0.0.1 by the app itself, and every part of it -- including the 3D engine
-- is inside this download.


IT CAN STILL BREAK YOUR WORKING COPY
------------------------------------
Every write is previewed before it happens, is checked to fit the space the
original file occupied, and is recorded so it can be undone. That is a lot
of safety, and it is not the same as a guarantee.

This tool edits the internals of a twenty-year-old console game whose formats
were reverse-engineered rather than documented. Some of what it believes about
those formats is inference. Keep checkpoints. Do not point it at the only
copy of anything you care about.


SOME OF WHAT IT TELLS YOU IS A GUESS
------------------------------------
Every finding in the interface carries one of five marks:

    = measured      read directly off the disc and verified
    + established   consistent everywhere it has been checked
    ? inferred      a reasonable reading that has not been proven
    ! untested      believed to work; never actually run in the game
    x dead end      tried, and it did not work

Anything not marked "= measured" may be wrong. The marks are not decoration
and they are not modesty -- they are the honest state of the research.


NO WARRANTY
-----------
This software is provided "as is", without warranty of any kind, express or
implied, including but not limited to the warranties of merchantability,
fitness for a particular purpose and non-infringement. In no event shall the
author be liable for any claim, damages or other liability, whether in an
action of contract, tort or otherwise, arising from, out of or in connection
with the software or the use or other dealings in the software.

That covers, specifically and without limiting the general statement above:
damage to your disc images, damage to your emulator configuration, loss of
mods or checkpoints, and anything the modified game does when you run it.


NO AFFILIATION
--------------
Not endorsed by, sponsored by, affiliated with or connected to Take-Two
Interactive Software, Rockstar Games, Angel Studios or Capcom. "Red Dead
Revolver", "Red Dead", "Rockstar Games" and all related marks and characters
are the property of their respective owners and are used here only to say
truthfully which game this tool edits.


THIRD-PARTY COMPONENTS
----------------------
    three.js          MIT License, Copyright (c) 2010-2026 three.js authors
    Python            Python Software Foundation License
    Pillow            MIT-CMU License
    NumPy             BSD 3-Clause License
    pywebview         BSD 3-Clause License
    Microsoft Edge WebView2 Runtime -- not redistributed; the app uses the
    copy already present on your system.

Full licence texts are in the licenses\ folder beside the application.


By installing or running this software you confirm that you own a copy of
Red Dead Revolver, that you dumped the disc image yourself, and that you
understand this software comes with no warranty of any kind.
```
