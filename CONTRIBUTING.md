# Contributing

- **No game data, ever.** Not in commits, issues or PRs: no ISOs, archives, extracted files,
  textures, audio, or screenshots of private paths. Tests read the disc at run time instead.
- **A finding is only real if a probe reproduces it across the whole disc**, not one file. New
  format knowledge goes in `03_Tools/research/probe_*.py` plus a note in `05_Docs/RESEARCH/`
  or `05_Docs/CAPABILITIES.md` (VERIFIED-TRUE / VERIFIED-FALSE / UNTESTED).
- **Writers must round-trip byte-exactly** and refuse rather than truncate. Add a test that
  checks the written bytes, not just a success message.
- Run `py -3 run_tests.py --quick` from `03_Tools` before a PR and paste the SUMMARY block.
  Only one suite at a time: tests share the working ISO.
- Mark confidence honestly in UI text (measured / established / inferred / untested).
- Keep personal paths, usernames and machine names out of code; `packaging/build.py` refuses
  a release build that carries them.

## Code-mod tests

`scripts/test_codemods*.py` run the patched boot code in an R5900 (PS2 EE) interpreter
(`emu64.py` + `mips.py`). That interpreter comes from separate community research and is not
redistributed here. Put a compatible `emu64.py` and `mips.py` in `03_Tools/scripts/r5900/` to
run them; without it they print SKIP.
