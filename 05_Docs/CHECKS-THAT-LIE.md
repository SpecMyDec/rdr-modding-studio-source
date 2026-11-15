# Checks that lie

Written 2026-09-18 by rdr-menu, after a night in which **six separate checks
reported success while being satisfied by something other than what was meant.**
Four of them were mine. Three were caught by another lane reading my work.

`CAPABILITIES.md` registers this shape in the **formats** — an inherited
"cannot" that turns out to be false. This file is the same disease in the
**tooling and the process**, where it is harder to see because the thing
reporting success is usually something we wrote ourselves.

**Every lane may append.** One heading per check, with the evidence. Do not
rewrite someone else's entry; add yours.

**The numbers collide, exactly like REQ numbers do — assert, do not look.**
Flagged by rdr-3d 2026-09-19 after it nearly happened: they read the highest
heading as 6, composed four entries, and by the time they wrote, another lane
had appended 7-10. **Their writing script re-read the max and aborted because it
had moved**, so nothing was clobbered and there is no second `## 7`. Without
that assertion they would have written over four entries that had arrived in
between.

`STATE-CONTRACT.md` documents the same hazard for `LANE-REQUESTS.md`,
where the mitigation is *"before you pick a number, re-read the file"* — and
REQ-013, REQ-014, REQ-031 and REQ-036 were each taken twice in one day anyway.
**A habit that needs everyone to look first is the thing that fails.** So:

    # in the script that writes, not in your head
    before = max(int(m) for m in re.findall(r"^## (\d+)\.", text, re.M))
    ...compose...
    assert before == max_again(), "someone appended while I was writing"

and afterwards, the same cheap detection the contract uses for REQ numbers:

    grep -oE "^## [0-9]+" 05_Docs/CHECKS-THAT-LIE.md | sort -V | uniq -d

rdr-3d argued for a timestamp-plus-lane key instead, which needs no
coordination at all, and they are right that it is the stronger fix. **Keeping
the integers anyway, deliberately:** entries are already cited by number across
four lanes' messages and in `PROGRESS-rdr-menu.md`, and renumbering would mean
rewriting other lanes' entries — the one thing this file's own convention
forbids. The assertion above buys most of the safety at none of that cost. If
the file ever outgrows it, the renumbering is a decision for whoever is
maintaining it then, not a unilateral one now.

---

## 1. `SUITE-GREEN.txt` asserted green and nothing ever wrote it

**Deleted 2026-09-18 (REQ-084).** It said *"full Windows test suite GREEN"* for
commit `42f9e79` — **456 commits back**, dated 26 August.
`grep -rn "SUITE-GREEN" --include=*.py --include=*.sh --include=*.bat` returned
**nothing**: no script, no suite, no gate wrote it. A hand-made snapshot taken
once and never automated.

It had already caught someone: `docs/REVIEWER-LOG.md:85` records a milestone ticked
*while noting the file was stale*. It was known, and left in place — which is
how it became a trap for the next reader, who was me.

**The shape:** a file whose NAME asserts the thing you want to be true.
"SUITE-GREEN.txt exists" was satisfied by something other than the suite being
green. Caught by rdr-app before anything was relayed.

**What to do instead:** the suite's own SUMMARY block is the artefact, and a run
with no SUMMARY was truncated rather than clean. If you want a machine-readable
gate result, have `run_tests.py` emit it; do not write one by hand.

## 2. A docstring claimed a refusal that did not exist

`frontmenu.install_screen()` builds a disc that soft-locks the player. The
module docstring said it *"refuses to run unless you ask for it by name."*
**There was no such guard.** I wrote the sentence describing an intention and
never implemented it.

A reader would have relied on it. It now takes `i_know_this_soft_locks=True`
and raises otherwise — verified that the refusal actually fires.

**What to do instead:** when a comment describes a safety property, go and run
the thing it protects. rdr-app found this by asking about a constant three
lines away; I would not have looked otherwise.

## 3. "The measured floor" was one observation

rdr-play saw a menu row at y=308 render clear of the button prompts. Within an
hour I had written *"a measured floor"* into the progress notes and
*"measured ... and a row at 326 would likely have collided"* into a test.

**308 was observed safe. 326 was never observed at all.** I had hardened an
inference into a constant, and then changed a layout that had been confirmed on
a television on the strength of it. The change was a regression: a 20px gap
became 4px and two lines merged into one band.

**What to do instead:** attach evidence strength to every number, both
directions — **derived**, **observed once**, or **bracketed** (a value seen good
AND a value seen bad). The bracket is worth far more than the value:

    heading  240 BAD .. 250 GOOD    floor is in 240..250
    pitch     16 BAD ..  18 GOOD    limit is in 16..18
    last row 308 GOOD ..   ?        boundary still unknown, left open on purpose

## 4. `pcsx2-qt.exe` in `tasklist` is not a person playing

I read a running emulator and a disc labelled "the developer's play disc" off the
process list and told the reviewer he was mid-session. He was not: `idle.clients` was 0
and had been for four minutes, and rdr-play's service stops the emulator by
itself after ten.

rdr-app had built a waiter on the same inference and it queued behind a teardown
artefact **twice**; rdr-play documented the `idle` block in their `PROTOCOL.md`
and shipped `pcsx2.ours` because of it.

**Read that as a postmortem, not foresight — rdr-play's own correction to this
entry.** They did not reason their way to the process/person distinction: their
idle logic shut the developer's game down mid-session, and the flag exists because they
broke it and then fixed it. An entry in this file that reads as someone being
clever in advance is itself a check that lies.

**What to do instead**, read-only from WSL, needs nothing from anyone:

    curl -s http://127.0.0.1:8296/api/status
      .idle.clients     0 = nobody attached. A PERSON, not a process.
      .pcsx2.ours       true = rdr-play launched it
      .idle.overrides   {} normally; non-empty = a test is mid-run

**And ask the right question: not "is rdr-play idle" but "is anyone
connected".** Their sharpening, 2026-09-19. It is the more useful check because
**they will not always be the one using the machine** — the developer connects from
his phone, and a large write to `D:` during his session degrades it with no way
for him to tell that is what happened. `idle.clients` answers that; a peer
lane's idleness does not.

## 5. The mute was applied to a PCSX2 nobody boots

The reviewer muted `<PCSX2 folder>`, the shared install. rdr-play runs a **private
copy** at `run/pcsx2`, which was at `StandardVolume=100`, `OutputMuted=false`.
Every boot that evening ran at full volume with the developer's TV on, while everyone
involved believed it was silenced.

Caught by rdr-play, who muted the private copy with PCSX2 stopped so it could
not rewrite the file, and left the shared install's edit untouched.

**And the catch depended on the instruction being relayed at all** — their
point, not mine. I passed "audio is muted tonight, do not unmute" on to them
because I would rather repeat something than assume they had it. They only went
and looked at the file *because* somebody stated a condition as though it were
in force. Relaying a constraint you have no particular reason to think the other
side is missing is cheap, and it is what surfaced this one.

**The shape:** the action succeeded. It just landed on a thing that is not in
the path. Check that the file you edited is the file that runs.

## 6. A feature verified on a surface the user never touches

Mine, and the most expensive. I reported the Mod List menu shipped and
boot-verified: byte checks, a hardware frame capture, a 0px-drift row-band scan.
The developer booted it and said the row was missing. **Both were true.** It rendered
on the front-end Options page; he was in a game, and Start -> Options reaches
`gameoptions.swui`, a **different file** with no list on it.

Every check I ran was sound and none was on his page.

**What to do instead:** before calling something verified, ask *which surface
will the person actually be on*, and whether that surface is even the same file.
On this disc `mainoptions.swui` exists **twice** (FRONT.DAT and SWAT.DAT) and
the in-game equivalent is a third file in **62 archives**. `Game.locate(name)`
answers "how many copies are there" in one call; `patch_everywhere` is this
repo's idiom for editing all of them and reporting the ones that would not fit.

**THE SAME FACT APPLIES TO READING, AND THAT HALF IS EASIER TO MISS.**
rdr-fmt, 2026-09-19, the day after reading this entry and saying it was "now in
my head permanently": they wrote a comparison that read **one** copy of a file
they knew existed twice, and reported byte-identity between two discs from it.
One of those discs was half-built and the difference was in the copy they did
not read.

Their own diagnosis, and it is the sharper statement: *the lesson I took was
about WRITING to every copy; the failure was READING one. I generalised it only
in the direction I had been burned in.*

So `Game.locate(name)` belongs on both sides. A verification that reads one of
N copies is not a weaker check -- it is a check that cannot see the defect it
was run to find.

**One qualification, so it does not become a ritual:** the ambiguity only bites
when the copies DIFFER. Where they are byte-identical, which one the engine
resolves cannot change what renders, and demanding frames per copy is theatre.
Check that they agree; then one reading answers for all of them.

**And when a user says it is missing, they are reporting their surface, not
arguing with yours.** Taking the developer seriously instead of re-checking my own disc
is what found the second page.

---

## 7. A check about MY bytes, for a fault in the GAME's reads

**rdr-fmt.** Relocating a grown archive past the original volume end, I asserted
`archive ends before the final sector`. It passed. It passed on **four
consecutive broken discs**, truthfully, while `CAST20` sat on the attract loop
and the emulog filled with:

    isoFile error: Block index is past the end of file! (1148068 >= 1148068)
    x8475, from t=262.7s

The file was exactly 1,148,068 sectors, so the game was asking for **one sector
past the end of the image**, over and over. Measured afterwards: slack between
the archive's last sector and the UDF anchor was **zero on all four** relocated
discs, against 840,886 sectors on the unrelocated one. A read-ahead, or a length
rounded up to a sector boundary, runs past the archive without the archive
overrunning anything.

My check asked *do my bytes overrun?* The fault was *does the reader go past
them?* Both are reasonable questions and only one was the one that mattered.

**The shape:** a check scoped to the wrong ACTOR. It is not a weak check — it is
a sound check aimed at the party you control rather than the party that fails.
rdr-play reasoned from the error to a read-ahead without ever seeing my layout;
I had the layout and did not ask the question. Fix was 64 sectors of slack and a
`slack >= 64` assertion, but the transferable part is: **name who performs the
action your check is about, and confirm it is you.**

---

## 8. The marker I added to observe the experiment broke the experiment

**rdr-fmt, and it cost four discs and a whole causal story.** To make clones
legible in a 360-degree sweep I renamed them `CROWDTEST1..60`. That disc showed
three bodies. `060_SCATTER` — same geometry, same `Table`, same counts, clone
names left as `<Original>_x<k>` — showed sixty, which the developer played and enjoyed.

`Name` in a `.enm` is **resolved**, not a label. Derived names spawn; invented
ones do not.

I then took the sweep of the instrumented disc as ground truth and built on it:
cross-referenced `Sher70_Activate_*` flags out of `lvl_sher70.flags`, concluded
activation was a separate gate clones could never pass, declared the approach
dead, told a peer to stand down, and reframed a hang investigation. All of it
from one disc whose only distinguishing feature was **my own instrumentation**.
The argument was also weak on its own terms — only 3 of 19 vanilla enemies have
an `Activate_` flag, so it proved the 16 flagless ones could not spawn either,
which is plainly false. Evidence that proves too much is a warning.

**What to do instead:** a marker added for diagnosis is an edit, and an edit is a
suspect. When an instrumented build disagrees with an uninstrumented one,
**suspect the instrument first**. `060_SCATTER` was on the same drive and one
boot would have settled it before any of it.

---

## 9. A budget measured on the axis I could see, not the one that binds

**rdr-fmt.** Importing 16 character bundles into a level archive, I sized them
against `TOMB4.DAT`'s **total archive size** — 21.02 MB uncompressed, the largest
the engine demonstrably loads as a level. My disc came in at 20.59 MB,
comfortably under, and every check was green. It threw
`Heap Overrun (5728 bytes requested)` at 327.8s and never reached the street.

Measured on the axis that actually binds — **character-bundle bytes**:

    ROYDUSK6.DAT   13 chars   6.52 MB   <- heaviest shipped cast
    SHER70 stock   12 chars   5.58 MB
    my disc        28 chars  12.24 MB   <- 88% over

Under the ceiling I checked, nearly double the ceiling that mattered.

**The shape:** a quantitative check whose *threshold* is real and whose
*quantity* is the wrong one. This is worse than no budget, because the number
made it feel measured. Before trusting a budget, ask what the failing component
actually allocates against — and prefer a ceiling taken from **shipped data on
that same axis**, which is what eventually gave the right answer.

---

## 10. A string-replace that matched nothing, and a script that reported success

**rdr-fmt, ten minutes ago, in this file, while writing entry 8 about exactly
this.** I appended entries 7-9 and two habits in one script. The entries landed.
The habits never reached disk. The script printed:

    appended entries 7-9 and two habits

The entries used an anchor I had asserted on. The habits used
`s.replace("**Never replace a state confirmed on hardware**", ...)` -- and the
real text is `**Never replace a state confirmed on hardware with an unverified
change**`. My anchor closed the bold early, matched nothing, and
`str.replace` **returns the string unchanged rather than raising**. The `print`
at the end was unconditional, so it described what I intended rather than what
happened. rdr-menu found it by diffing my commit and seeing that every bold line
in it belonged to 7/8/9.

Worth noting what did NOT go wrong: we edited the same file minutes apart on the
shared index and neither clobbered the other -- `git show` on both commits shows
zero deletions. The append-only convention held. The loss was entirely my own
silent no-op.

**The shape:** `str.replace`, `sed`, and every "edit by matching text" tool
**succeed at doing nothing**. A success message printed after one is a statement
about control flow, not about the file.

**What to do instead:** assert the anchor exists *before* replacing, assert the
result changed *after*, and print from the file rather than from intent. One
line each:

    assert anchor in s, "anchor not found - do not write"
    ...
    assert new_text in s
    # and afterwards: grep the file, do not trust the script's own echo

This is entry 2's disease -- a claim about behaviour that was never true -- with
the claim coming from my own tooling, in the commit where I documented it.

---

## 11. A revert check that passed BECAUSE the data was destroyed

`scripts/drive_m16_routes.py`, before `78846b2`. It restored with `revert_file`
— which restores from **vanilla** — and then asserted:

    check("shell back to stock bytes", shell_off_disc() == stock)

with `stock` read from `VANILLA_ISO`. On a disc whose `lvl_int2.rsc` was already
modified, the revert destroyed that edit, and **this assertion then passed
precisely because it had.** The file certified the disc correct in the same run
in which it ate an edit. Only a changed-file set comparison ever complained — so
the run that destroyed data reported FAIL, the next run on the now-clean disc
passed, and the loss read as a flake.

**The shape:** an assertion whose success condition is identical to the damage
condition. Reverting to vanilla and then comparing against vanilla cannot fail.

**What to do instead:** compare against the bytes **found** at the start, not
against vanilla. `78846b2` captures `found = shell_off_disc()` before touching
anything; the vanilla read stays, but only for the old-mover comparison it is
legitimately for. Proven both directions: the repaired drivers preserve a real
pre-existing edit (18/18, `283c940`), the pre-repair drivers destroy it (13/13)
while printing `[ok  ] shell back to stock bytes`.

## 12. A checker that went red because the subject printed "no FAIL"

My own harness, closing REQ-075:

    ck("probe reports no FAIL", "FAIL" in out, False)

The probe passed 24/24 and exited 0. The check went red anyway, because the
probe's output contains `counted: 44 ok, 0 FAIL` and the label
`their test reports no FAIL`. **Two lines that report success, matched by a
substring search for failure.** I had made the same mistake hours earlier with
`out.count(" ok")`, which returned 44 against a real 43.

**The shape:** matching the WORD rather than the STATE. rdr-fmt's inverted grep
the same night is its mirror — theirs selected runs that *complain* rather than
runs that *destroy*. Same defect, opposite sign.

**What to do instead:** ask the subject for its own tally and assert on that —
`re.search(r"(\d+) checks, (\d+) failed", out)`, require 0 — plus match
`FAIL got=`, the string the reporter emits **only** on a real failure.

## 13. 149 "modified entries" that were re-compression residue

A sweep compared **stored** bytes and reported 149 modified entries on the
working disc. `diff()` reported **0**. Both were right: a file patched and later
reverted comes back content-identical but **re-deflated**, so its stored form
never byte-matches vanilla again. Measured the same day, same disc: 149
stored-byte differences, **0 content differences**.

That 149 was then cited as a live precondition — a disc "carrying mods" — to
justify a run that in fact exercised a pristine one, and separately as evidence
that something had destroyed edits. Both claims were withdrawn; the withdrawal
then over-shot and had to be partly retracted, because 75 **real** content edits
had been lost, proven by a checkpoint holding files 8 bytes longer
**decompressed**, which re-deflation cannot produce.

**The shape:** asking about one thing, answering with a measurement of another,
then quoting the number as though it had answered the question.

**What to do instead:** for "is this disc modified", use `get("diff")`, which
decompresses and compares content; a **decompressed-length** difference is what
residue cannot fake. And make the precondition a **hard gate** — the closing run
for REQ-075 refuses to start its probe at all unless the precondition
establishes, rather than producing a green result about the wrong disc.

## 14. An HTTP probe used to answer a socket question

Waiting for port 8731 to free, I polled `/api/config`, got a connection error,
printed `vacant after 0s`, and started a Studio — which died on
`OSError: [Errno 98] Address already in use`.

**"Does something answer HTTP here" is not "can I have this socket."** A process
still starting, a non-HTTP listener, or a socket in `TIME_WAIT` all answer the
first question the way a free port does. rdr-fmt hit the mirror earlier the same
day: they polled the port, got an answer, and adopted a Studio that was already
a corpse — believing its pid for an hour.

**What to do instead:** bind it. `socket.bind(("127.0.0.1", port))` in a
try/except answers the question you actually have. Poll the **child**
(`Popen.poll()`), not the port, to know whether your own process lives — then
`wait()` for it, because `kill()` is not instant and a writer that is still
dying still holds its lock.

## 15. Describing what your own check enforces, from memory

**rdr-play's, written up at their request** -- `05_Docs` is outside their lane,
so the observation is theirs and the transcription is mine.

They told their own side that the reviewer's edit to the shared PCSX2 ini had moved its
checksum and would therefore **fail** their gate's "shared install untouched"
check, and declined to re-baseline it. Declining was right. The severity was
wrong: that branch calls `info()`, not `bad()`, and only `bad()` increments the
failure count. **The condition they had flagged as a blocker could never have
blocked anything** -- and they had written the check themselves.

Mine is the same move one level out. I knew what
`archive ends before the final sector` was *for*, so I never re-read what it
actually *asserted*, and it passed truthfully on four consecutive broken discs
(entry 7).

**Why this is not one of the others.** Those are checks that report success
while something other than the intended thing satisfies them -- **the check lies
to you**. This is the opposite direction: **you misreport the check.** The code
is correct; the claim about the code is not. So re-running it never surfaces the
error, and a green result never surfaces it either, because nothing is wrong
with it. It surfaces only when someone reads the source, or when the condition
fires and does not do what you said it would.

**What to do instead:** re-read the assertion before describing its severity,
especially when you wrote it. "This will fail the gate", "this would have caught
that", "this covers X" are all claims about code that is right there. The
temptation is strongest for your own checks, because what you remember is the
intent you had while writing them -- and the intent is what you end up
describing.

## 16. "No session" meant "no session I can see", and I said it six times

**rdr-fmt.** the reviewer asked repeatedly whether the developer had played the disc. I read
rdr-play's service each time and answered, with increasing confidence, that he
had **not started**:

    pcsx2 running : False     clients : 0
    secs since input : 2216.9

He had. He was at the laptop with a controller, and he found the mod list from
the pause menu. rdr-menu settled it in one message by **asking him**.

`clients` counts websocket connections to the phone page. It cannot see a
laptop session at all. rdr-play told me this explicitly -- *"tell the reviewer he has
not played through the service, and no unaccounted emulator has run, rather
than he has not played"* -- and I repeated the caveat once, accurately, and
then went back to asserting the stronger claim on later checks because the
number kept reading zero. **A caveat you state once and then drop is not a
caveat.**

The second signal, "no unexplained `pcsx2-qt.exe` has run", was the one that
could have seen him, and it only samples when I look. Two instruments, neither
covering the case, and a confident answer built from both.

**The shape:** an instrument that covers one PATH being read as covering the
QUESTION. Same family as `tasklist` in entry 4 -- and I had that entry in front
of me, having just written two of my own about checks aimed slightly off the
thing that fails.

**What to do instead: ask the person.** There was a human three metres from the
machine who knew the answer, and every instrument I had was a proxy for him.
When the question is "did a person do something", a person is not a fallback
after the telemetry is exhausted -- they are the measurement. Cheaper than what
I did, and it is the only source that resolves when two instruments disagree or
when, as here, neither can see the case at all.

## 17. A key that was ABSENT, which my parser showed me as "nobody is here"

**rdr-fmt, with rdr-play.** I polled `/api/status` for a `peers` list to tell a
LAN device from a lane script. My reader printed:

    peers: null

I took that as "the field exists and nobody is connected" and reported it as
evidence. The key was **not in the response at all** -- `/api/status` returned
only `pcsx2`, `capture`, `idle`, `savestates`, `turbo`. `dict.get()` renders a
missing key and a present-but-empty one identically, and so does most JSON
handling in most languages.

**Why it is worse than a wrong value.** There is no server-side evidence: the
endpoint does not log, does not error, and the field never exists to BE wrong.
Nothing on either side can distinguish "this endpoint cannot answer your
question" from "the answer is nobody", and those are the two readings that
matter. Had I not gone looking in `/healthz`, I would have polled that key
indefinitely and never seen it fail once.

**What to do instead:** when a field's emptiness is the signal you are acting
on, assert the KEY EXISTS separately from reading its value --
`assert "peers" in d` before `d.get("peers")`. And prove the instrument can say
yes: I only trusted it after seeing `["127.0.0.1"]` come back with a client
attached. An instrument that has only ever returned "no" has not been tested.

Fixed same day: `/api/status` now carries `peers` and `clients`, fed from the
same function as `/healthz`, and `[]` now genuinely means nobody. Verified from
both endpoints: identical.

## 18. Supplying a discriminator without checking it discriminates

**rdr-play's, recorded at their request.**

I asked how to tell a the developer session from a lane session. They gave me `ours`,
with: *"If the developer taps a row on the page, the service launches it and `ours` is
True."* True as written. I built a watch on the converse -- `ours=True`
therefore a person -- and it fired on a lane launch three times, because lanes
launch through the service constantly.

They declined to let it be filed as my bad inference, and their reasoning is
the entry: if it is recorded as mine, the lesson is "check converses", which is
true and generic. Recorded as theirs, it is specific and usable:

> **When someone tells you what they are going to do with a signal, check that
> the signal answers THAT question, not the one you were asked.**

The field answered "did the service launch this". The question was "is a person
here". Both parties knew the purpose, and the gap survived the handover because
the fact supplied was true.

Compare entry 6, wrong surface, and entry 7, wrong actor. This is the wrong
QUESTION, handed over intact and in good faith.

## 19. A guard that was correct until it was no longer the only user

**rdr-play (rdrplay-ui), relayed by rdr-menu because that lane does not write
in this repo.** They ran a watchdog that relaunched their disc whenever the
emulator went idle with nobody connected. Its purpose was real: stop the developer
opening a dead page. It had already fired once legitimately.

**The rule was `idle AND no clients`, and nothing about it was wrong** — while
they were the only person using the machine. The moment the reviewer began hand-booting
discs for a boot-proof, that same rule meant: *wait for his disc to finish, see
idle, launch mine over the top of it.* To him it would have looked like a disc
failing for no reason — on a build he had just been told was good.

**The shape: a check whose PRECONDITION expired silently.** Not weak, not
aimed at the wrong actor, not reading a stale value. It went on correctly
answering the question it was written to answer, while the assumption that made
that the right question stopped being true. Nothing in the guard could notice.

**Their fix, which is the generalisable part:** scope the guard to what you own.
It now fires only when the emulator is idle, nobody is connected, **AND the
last disc that ran was theirs** — otherwise it prints that it is standing down.
That last clause turns "am I still the only user" from an assumption into a
thing that is checked.

**And they told the reviewer it may already have cost him a run** rather than waiting
to find out whether it had. A hazard that can fire every ten minutes against
someone else's work is worth reporting before the damage is known.

---

## 20. It rendered, so every check short of pressing the button said yes

**rdr-menu.** A "Mod List" row was added to the front-end Options page. It drew
in the right place. The reviewer booted the disc, saw the row, opened the page, backed
out, and reported it working. It went into the developer's picker on that basis.

**Selecting it soft-locks the console.** rdr-play measured it: the page draws
over the top of Options, the screen goes black, frame delta **0.000 across 18
seconds**, every pad button pressed with no response. 31 threads, 18.8 percent
CPU, **zero disc reads** — alive and spinning, not crashed, and no way out from
inside.

**The cause was three commented-out lines.** That page is driven by a class
that stores widget pointers and writes through them. I commented out its three
stock buttons to tidy the page; the class looked one up, got NULL, and hung.
The otherwise-identical disc with those buttons left in opens and backs out
cleanly.

**I had written the risk down before shipping it** — *"the class stores widget
pointers and writes through them... only a boot settles it"* — in the code, in
the commit message, and in the note handing the disc over. rdr-fmt named the
shape exactly: **the knowledge was present and did not reach the decision.**

**The shape: PRESENCE mistaken for FUNCTION.** Every check available said the
row was there — bytes on disc, band positions, a rendered frame. None of them
pressed it. "The feature appears" is a narrower question than "the feature
works", and on a menu row the gap between the two is the entire feature.

**What to do instead:** for anything interactive the acceptance condition is
*use it and come back*, not *see it*. And when a boot-proof is delegated, say
which button must be pressed — "does the row appear" and "does the row work"
are different requests, and the first is the one that gets answered if you do
not ask for the second.

---

## 21. The check read its expectation off the thing it was checking

Caught by **rdr-app, 2026-09-19**, reviewing the `[M23]` row they had just
registered for me. Mine, and the second one in this file where the fix for a
real defect installed a quieter one.

`verify_modlist_row.py` used to carry the expected mod names as a literal list.
That could assert *correctness* -- these exact names, or fail -- but it could
not run against a disc built by another lane with its own names, and that gap
is how a half-built disc reached the developer twice (entry 13). So I made the
expectation come from the disc:

    MODS  = None                                    # :113
    names = MODS or frontmenu.read_names_disc(mod)  # :173

Every downstream check then asks whether all copies of the page agree with
`names`. **They do, necessarily, because `names` came from one of them.**

    what it proves    every copy carries the same list, none left stock
    what it cannot    that the list is the one anyone chose

Overwrite the shipped disc's names with any other three and the row reads the
new names off the disc, finds the copies agree, and returns **19/19 green on a
page whose text nobody picked**. The output is the tell and the disguise at
once: it prints the names it found, under a heading that reads as though it
expected them.

The milestone label is where this does its damage. "[M23] the main menu carries
its mod list" is heard as a claim about content, by people who will never read
line 173. The check is honest in its comment and misleading in its name.

**A fair trade, not a regression** -- and worth saying, because the instinct on
reading this is to put the literal list back. The literal list could not run
against the disc we ship, which was the defect actually biting. Both versions
are one-sided. What closes it is an expectation the disc cannot supply: a names
manifest written beside `modlist-disc.txt` at build time and read by the gate,
so the row compares **disc against record** instead of disc against itself.

Be precise about what that buys, because it is easy to oversell: the record's
authority is the build invocation, so it proves *the disc matches the last
intentional build*, not *the names are right*. A hand edit or a second tool
writing the page would then red the gate, which is the point. It also adds a
failure the current row cannot have -- a stale manifest reddening a good disc --
and that is the correct direction to fail in.

Until the manifest exists, **do not quote a green [M23] as evidence that the
right names shipped.** rdr-app has said they will not describe it that way to
The reviewer, and neither will I.

**Two things to settle before anyone builds that manifest** -- both rdr-app's,
2026-09-19, sharpening their own proposal after I pushed back on it.

*No fallback. Refuse.* A manifest introduces a failure the current row cannot
have: the record goes missing or stale, and a good disc reds. The next person
to hit that will reach for the obvious kindness -- fall back to reading the
names off the disc when the record is absent -- and **that fallback is this
entry, restored exactly.** It would make the gate self-referential again on
precisely the occasions the record was supposed to cover. Refuse instead, in
the same message shape as the other refusals in these tools, so they read as
one policy rather than three coincidences. The asymmetry justifying it is this
project's whole history: a false red costs an hour, and a false green put a
half-built disc in front of the developer twice.

*The label still outruns the evidence, even with the manifest.* "The main menu
carries its mod list" would become "carries the list the last build intended" --
build with the wrong names and disc and record are wrong together, green. If
that is the claim, the qualifier belongs in the roadmap where the reviewer reads it,
not in line 173 of a test. A check whose honesty lives only in its source has
not told anyone anything.

The same shape, one layer down, in the builder that feeds it: `mods =
sys.argv[2:] or MODS` at `build_modlist_row.py:67` silently supplies this
lane's default names, which no longer match the shipped disc -- rdr-fmt built
it with theirs. A no-names rebuild swaps them, and the gate greenlights the
result. Requiring the disc argument does not catch it; only comparing against a
record does.

## 22. A check that asserts a side effect instead of the thing itself

**Written by rdr-play, 2026-09-19; appended by rdr-menu at their request.**
Their lane's ROADMAP scope does not cover writing here, and they declined to
make an exception for a doc they were invited to edit -- "the value of the rule
is that it does not have exceptions I judge for myself." The words below are
theirs.

`[M8]` proves that launching a disc clears the turbo latch. It fired
`/api/launch` and then asserted only that `turbo.active` had become false.

`turbo.active` is false after a launch that **failed**, too. So the test
passed whether or not an emulator started.

Launches here do fail. Firing one straight after a stop can catch the
exiting PCSX2 still holding its own binary, and `CreateProcess` returns
`[WinError 5] Access is denied`. It happened three times on 2026-09-19 --
09:28, 09:32, 13:34 -- and `[M8]` passed all three. The 13:34 one left no
emulator and a stale `last_error` on a machine the gate had just reported
`VERIFY OK`. It surfaced because the UI lane read `/api/status`, distrusted
"idle with an access-denied error", and asked. No check caught it.

**The shape:** the assertion was downstream of the thing it was meant to
prove. Turbo clearing is a *consequence* of launching, and the consequence
also holds when nothing launches. Entry 21 is the same defect approached
from the other side -- expected values sourced from the artefact under test,
so the check confirms self-consistency rather than correctness. Both stay
green through precisely the failure they exist to catch.

**The fix that matters is not the retry.** The spawn now waits for the old
process and retries the lock window, so the race should stop happening. But
the test was strengthened first and separately: it now asserts the launch
returned 200/202, that an emulator is *actually running* afterwards, and
that `last_error` is clear. A fix without that would have removed the
symptom while leaving the blind check in place.

**Test for it:** ask what else makes the assertion true. If "the operation
did not happen" is on that list, the check certifies the failure.

### rdr-menu's note on the pair

Entries 21 and 22 are one defect from two sides, and the reason to read them
together is that **the remedies do not compose.** rdr-play's captures of the
shipped disc and my build-time record are independent of each other, and both
describe what the disc *does*. A build run with the wrong names leaves disc and
record agreeing, and the capture faithfully photographs the wrong names. Two
green signals, no coverage against that failure.

Only the developer saying *"those are the mods I wanted listed"* closes it, and
neither lane can manufacture that from inside the tooling. Stacking independent
checks feels like depth and is not, when they share a ceiling. Say the ceiling
out loud each time, or the next person adds a third signal and believes it.

## 23. A true report that reads as a different truth

Mine, 2026-09-19, an hour after writing entry 21 and caused by the fix in it.
Every other entry here is a check that lies by passing. This one is a check
that was **completely correct and still sent me at the wrong thing.**

Seeding the new names record for the shipped disc, I wrote the three names in
the wrong ORDER -- I had the set from rdr-play's hardware capture and typed the
order from memory. The gate caught it immediately, which is the system working.
Here is what it printed:

    FAIL  every copy renders the same block -- DISAGREES IN ['FRONT.DAT', 'SWAT.DAT']

**That is the signature of the defect that shipped to the developer twice.** A disc
with `mainoptions.swui` patched in FRONT.DAT and left stock in SWAT.DAT prints
exactly that line. The honest response to seeing it is to go tearing into the
disc -- and the disc was perfect. Pinning the names on the command line
returned 19/19, exit 0.

**Two independent causes, one output.** The check compares every copy against
an expected block; if the expectation is wrong, all copies disagree with it,
and "they disagree with the expectation" surfaces in the same words as "they
disagree with each other". The message names the copies because when this check
was written there was only one way to fail it.

That is what a new source of truth does to old error messages. The record was
added to make the gate stronger, and it silently widened the meaning of a
failure the messages had been written for. **Nothing about the check became
wrong; its vocabulary became ambiguous.**

### What to do about it

Fix the message, not the check: a mismatch against the record must say so in
different words from a disagreement between copies, and name the record. Filed
as a follow-up rather than done in the moment -- the suite was mid-run and this
file is read by the row it would change. *(rdr-menu: land it with the next
frontmenu commit.)*

And the general form, which is the reason this is worth an entry at all:

**When you add a new way for a check to fail, re-read the failure messages it
already had.** They were written when the check had fewer causes, and they will
keep describing the old one confidently. A green that means the wrong thing
wastes a release; a red that means the wrong thing wastes an afternoon and
spends its credibility -- and a gate that cries the wrong wolf is the gate
people start arguing with.

Flagged by rdr-app as belonging here, on the grounds that the failure mimics
the one failure this lane most needs to be able to see.

### Second instance, the same day -- rdr-play's `[M6]`

**Theirs, 2026-09-19, sent for appending here for the same scope reason as
entry 22.** Two independent hits in one day is what makes this a shape rather
than an anecdote.

`[M6]` failed with *"the idle shutdown was not recorded in /api/status"*. True
and accurate. The line printed immediately above it read:

    idle : emulator shut down after 16s of no clients: True

Both correct. The emulator had stopped; the counter did read zero. But the
message was written when the only way to reach it was "the idle monitor did not
fire", so it reads as *the shutdown did not happen* -- and sitting under a line
saying it had, the natural conclusion is that the TEST is confused rather than
the service. They dismissed it as flakiness across several gate runs.

It was real. `graceful_stop()` polls every 0.5s while the emulator dies inside
it, so the process was observably gone before `idle_stop_count` was
incremented: a half-second window where `/api/status` reported no emulator and
no reason for it. Exactly the state a UI would have to render, and the
assertion was right to fire. Fixed at `39c7809` by recording the stop BEFORE
performing it, so the observable order matches reality -- and the message is
unchanged and unambiguous again, because there is once more only one way to
reach it.

**The tell, which is the part worth carrying away.** In both instances the
signal was a *contradiction between two lines of the same report*: one line
said the thing happened, the next said it was not recorded. When that happens,
**the interesting object is the gap between them, not either line.** Neither
line is lying and arguing about which to believe wastes the afternoon. It is
cheap to look for and it is what neither of us did first -- mine cost a near-dig
into a perfectly good disc, theirs cost several runs of dismissing a real
defect as flake.

Note the two failure directions. A message that has gained a second cause can
push you to blame the artefact (mine) or to blame the test (theirs). The
dangerous one is theirs: **dismissing a true red as flakiness is how a real
defect survives a gate that caught it.**

## 24. A check that passed over an empty set, because the check above it broke

**rdr-fmt, 2026-09-19.** `verify_disc_claims.py`, first run, checking that
`RDR_CROWD_060_MENU.iso` carries the 60-enemy roster its Options page
advertises. Two lines out of the same run, four lines apart:

    [FAIL] the 19 vanilla enemies are still there      60 un-cloned names
    [PASS] every clone is <Original>_xN off a name that exists
                                                       0 clones, 0 orphaned

Both are about the same 41 objects. The FAIL is real in form and wrong in
substance — the disc is fine; every enemy name in this level carries an
instance suffix (`CameraGuy#1`, and the clone is `CameraGuy_x1#1`), and my
pattern `^(.+)_x(\d+)$` did not strip it, so it matched nothing. What matters
here is the line below it. **`every clone is well-formed` passed because there
were no clones to be ill-formed.** `all()` over an empty list is true.

**The check that existed to catch a broken roster was disarmed by the bug it
existed to catch.** A clone that is not named `<Original>_xN` never spawns —
the name is resolved, not a free label — so "all 41 clones are well-formed" is
precisely the assertion standing between us and a disc that advertises 60
enemies and shows 19. It reported success while its input was empty.

**Why this is not entry 21.** Entry 21 is a check taking its *expectation*
from the thing under test. Here the expectation was a literal and correct. What
was empty was the **input**: an upstream filter I wrote produced the set that
the downstream assertion then quantified over, and when the filter broke, the
assertion's verdict was decided by my regex rather than by the disc. The two
compose badly — a wrong expectation at least still reads something.

**The fix is to assert the cardinality before the property:**

    check(len(clones) == ROSTER - ROSTER_VANILLA,     # 41 of 60 ARE clones
          "%d of the %d are clones, not stock" % ...)
    check(not orphan, "every clone is <Original>_xN ...")

Now a pattern that stops matching goes red at the count, instead of going green
at the shape. Same principle for any `for all X in S` where **S is produced by
code you wrote** — a parser, a filter, a regex, a `glob`, a `locate()`. Assert
`len(S)` against a number that does not come from S.

**Two things this run got right, which is why it was cheap.** The overall
verdict was a FALSE RED, which is the direction to fail in — the run exited 1
and nothing shipped. And the expectations were literals in the file, so the
disagreement was visible at all; had the counts been read off the disc the two
lines would have agreed with each other and both been green.

**The part that should worry the next person:** the red was on the *other*
line. Reverse the two bugs — a pattern that matches correctly but a roster that
genuinely came out stock — and the vacuous PASS is the line that survives into
the summary, under a heading that says the clones are fine. Nobody reads the
line above a green one.

Caught by running it, not by review; disclosed to rdr-menu before they quoted
the corrected numbers to the reviewer, since a check has to be worth quoting before its
output is.

## 25. The refusal was real. The reason it gave was invented.

**rdr-app's, found 2026-09-19 resuming after the tmux crash.** The Studio can
fail to write the working ISO for four reasons and only one of them is the
emulator: the disc is missing, the disc carries the read-only attribute a
copied vanilla disc inherits, another process holds the writer lock (REQ-078),
or PCSX2 has it. `Session._locked_reason` was written to tell them apart, with
a comment explaining that telling someone to close a PCSX2 that is not running
is advice they cannot act on.

One layer up, `routes2._disc_busy` threw that sentence away and substituted
*"PCSX2 has the working disc. Close PCSX2 and try again."*, passing the real
reason as `problem.detail` -- which `renderProblem` renders behind a collapsed
**Technical detail** triangle. Five route sites never asked at all: they raised
a hand-written `RuntimeError("PCSX2 holds the working ISO")`, a cause no code
had checked.

**What makes it belong in this file is what stayed green.** Everything anyone
had thought to assert was true the whole time:

    the route refuses                     TRUE, always
    the refusal code is disc_busy         TRUE, always
    the refusal is not a 500              TRUE, always
    _busy() files it as a refusal         TRUE, always
    the real reason is in the payload     TRUE -- in `detail`, folded up

Reverting the fix reddens 12 checks in the new row and **none** of the five
lines above. A test for "it refuses" cannot see this, and "it refuses" is what
anyone writes. The assertion that catches it has to name the thing nobody
thought was in question: *the sentence the user reads is the cause that is
true.*

**The fourth cause was found by the fix, not by the report.** A MISSING working
ISO also fails `os.access(W_OK)`, so a disc that was not there reported "marked
read-only" and told the user to clear a tick in the Properties of a file that
does not exist. Three causes had been separated with care and a fourth was
sitting inside one of them. **Ask of any branch that names a cause: what else
reaches this line?**

**The general shape, since this is the second time this repo has had it.**
`api.py`'s own docstring records the first: five carefully written byte-count
messages became unreachable dead code behind a generic `"Could not encode:"`
prefix, and the module exists so *"the message the server took the trouble to
write is the message the user reads."* It was happening again one layer above
the module built to prevent it. **A careful message is not delivered by being
written.** Follow it to the pixel, or assert what comes out the far end.

## 26. The safety net ran. Nobody ever asked it whether it worked.

**rdr-re's, found 2026-09-19 resuming after the tmux crash.**
`scripts/test_journal.py` wipes the working ISO on purpose -- it needs a clean
baseline to reason about its own diffs -- and it is careful about it. It banks
a checkpoint of everything the user has first, reverts, runs, and puts it all
back at the end. The comment above that bank names the day it destroyed a
512-file preset, so the hazard was understood by whoever wrote it.

Today it banked 75 files at 13:04, reverted all 75, and **did not put them
back**. The row read **PASS**.

Two properties let that through, and only the second needs anything unusual to
go wrong:

    skippable    the restore was the LAST STATEMENT of the checks, so
                 anything that raised above it jumped over it
    unasserted   `ok` was decided BEFORE the restore, and the restore's
                 answer was printed and never read

`checkpoint_restore` reports what it **applied**, not what the disc now
**holds**, and a restore that applies nothing answers `ok`. So the test could
not have failed over this however badly the hand-back went. Everything anyone
had asserted stayed true:

    the checkpoint was banked        TRUE
    the four checks passed           TRUE
    the restore was called           TRUE (probably -- see below)
    the restore answered ok          TRUE, and would have on zero files
    the row is green                 TRUE, and meaningless

**The shape.** Every entry in this file so far is a check whose subject lied to
it. This one is a check that never looked at half of what it does. The wipe and
the hand-back are the same feature -- the hand-back is the only thing that
makes the wipe acceptable -- and the test asserted the four things it was *for*
and nothing about the one it *costs*. A test's cleanup is not test scaffolding;
when it writes to something the user owns, it is the most dangerous code in the
file, and it is the least likely to be asserted.

**The test to apply: ask what this test would have to do to destroy something,
and then ask which assertion would go red if it did.** If the answer is none,
the destruction is out of scope of a green run, whatever the row is called.

**What I could NOT establish, and it is the interesting part.** Whether the
restore ran and applied nothing, or never ran at all, is not recoverable from
the artefacts: the session journal keeps only the last action per file, and for
all 75 that is the 13:04 revert, which both stories predict. I spent a while
trying to tell them apart before noticing that the fix is the same either way
-- *assert the disc is back* -- and that wanting to know which is not the same
as needing to. Entry 3 in reverse: there the trouble was one observation read as a
property, here it is one observation with two explanations -- and the useful
move was to stop trying to separate them.

**Also, the 13:04 reverts were RIGHT.** The wipe is the design. Reading the
journal for the first time it looks exactly like the `test_guns.py` defect of
REQ-079 -- a test reverting the whole disc in setup, entry 11's shape -- and it
is not the same thing at all. The difference is one `post("checkpoint", ...)` two lines above,
and a defect and a correct design that differ by two lines is worth slowing
down for.

## 27. Two blind instruments agreed, and the agreement was the evidence

**Three lanes, 2026-09-19, inside one hour.** rdr-play reported that PCSX2 had
hung before creating its render window and named the Citrix Indirect Display
Adapter as the cause. rdr-menu measured it independently and got the same
answer. Two lanes, separate probes, same result — and it was worth nothing,
because both probes were the same blind instrument.

From a PowerShell or python spawned out of **WSL**, a Windows process sees a
different window station. rdr-menu tested the instrument instead of trusting
it, which is the whole move:

    (Get-Process).Count                                  ->  262
    (Get-Process | ? {$_.MainWindowHandle -ne 0}).Count  ->    0

**Zero windowed processes out of 262**, on a desktop with a browser and an
editor open. rdr-play then reproduced it and asked the service — which runs in
the interactive session and can actually see — at the same moment:
`/debug/windows` returned three. So the probe cannot answer anything but 0, and
`MainWindowHandle 0` had been cited in a commit message and a STATUS section as
a measurement. Two further probes died with it: `Start-Process notepad` and
`mspaint` "exiting on their own" are the same artifact, not a signal.

**Why agreement was worse than a single bad number.** The probe fails toward a
*plausible* answer rather than an error. A crash or an exception would have
been read as a broken instrument; `0` was read as a finding. So two independent
runs of it agreeing reads exactly like corroboration, and the second lane's
number is what made the first lane's conclusion confident enough to travel.

**It travelled through me, and that part is rdr-app's to own.** I relayed
rdr-play's *cause and conclusion* — "capture is broken machine-wide, the
adapter does it, your proof cannot be executed" — to rdr-menu, who stopped work
on it. Their frame-capture rule is how that lane proves anything, so the relay
did not just misinform them, it removed their only instrument. **An observation
travels safely; a cause and a conclusion do not.** Relay what was seen, name
the inference as an inference, or wait for it to hold.

**What survived, separated cleanly, because the hang was real:** 0.15s CPU
total, ~36MB resident, the emulog stopping dead at the graphics-adapter list,
and the *service's* own report of no window. All of those are either
window-station independent or measured from somewhere that can see. Withdrawn:
`MainWindowHandle 0` and both `Start-Process` checks. A WSL-spawned tool is
still trustworthy for process identity — pid, RSS, CPU, start time — which is
why the relay of "the husk 20416 is gone, 14740 and 5036 have started since"
stood while everything around it fell over.

**The rule the three of us converged on:** never ask a WSL-spawned process
about windows, focus or anything else the desktop owns — ask the service. And
before quoting any probe as evidence, **run it once against the condition it is
supposed to ALLOW.** That is item 7 of this file, and all three of us walked
past it on the same afternoon.

## Habits that caught things tonight

**Send the expected values WITH a verification request.** rdr-play named this as
what let their scan *fail* rather than describe: four y-values handed over
before they looked. It costs a line and is the difference between a check and a
report. Ask for a scan rather than a glance on anything cosmetic — and on the
*unchanged* case too, so the next change has a numeric baseline instead of a
memory.

**Run the other side's negative control, not your own.** rdr-fmt's: *"the list
is present"* is exactly the check that passes when it is reading its own
expectations. Confirming the vanilla page genuinely lacks the names is what
makes the positive result mean anything.

**Ask the peers before starting an assigned task.** The same task was issued to
two lanes **six times** on 2026-09-18, every one caught only because somebody
sent a message before the first edit. rdr-re put it well: *a mandate is a
description of the kind of work a lane does, not a claim on a specific task.*

**Ask rather than quietly fixing someone else's artefact.** I challenged a
"God Mode" row I believed had bypassed its gate. I was missing a message, not
finding a problem — the A/B had been re-run on that exact disc and passed. Had
I stripped the name, a **true** claim would have come off a disc that earned it
and the developer would have been told he had less than he does. That is a quieter
failure than the one I was trying to prevent.

**The endpoint is not the signal; the trajectory is.** rdr-play's, and it is
the sharpest statement of this whole file. *"Front end at the end"* was true of
a passing disc and a failing one. *"Loads, no hang"* got promoted to *"reaches
the street"* in a picker label. A wedged emulator and a real load failure both
present as no street. In each case the terminal state was satisfied by
something other than what was meant, and the answer was to look at what
happened **in between** rather than where it finished. Their god-mode A/B is
the model: sampling the health bar across 570 seconds against a control that
died in 30, rather than checking whether Red was alive at the end.

**A pass needs a control that CAN fail, and say so before the run.** rdr-fmt's
god-mode test. "Red does not die", and even "the health bar does not move", are
both satisfied by a fight nobody was participating in -- standing where no enemy
has line of sight produces a perfect god-mode result on a disc with **no god
mode at all**. So the rule was fixed in advance: PASS only if the **control**
bar moves AND the treated one does not, and **FAIL if neither moves**. It passed
on that rule -- control dead in ~30s of street time, treated disc flat across
570s. rdr-play then improved on it: I had asked for muzzle-flash evidence that
Red was under fire, they found their own flash detector reads 0 on frames with a
plainly visible flash, refused to quote a number they knew was wrong, and used
the control instead -- *the spot is demonstrably lethal, so survival there
cannot mean nobody was shooting.* **Design the pair so the control evidences the
treatment**, rather than adding a second instrument you then have to trust.

**Never replace a state confirmed on hardware with an unverified change**,
however small. If it is polish, let that govern the sequencing and not just the
language.

## Two mechanical notes worth having

**`flock -n /tmp/rdr-suite.lock` is the only authority on the suite lock.** Both
`tasklist` and `pgrep` read clear on 2026-09-18 while a Windows suite held it
through `cmd.exe`. `lsof /tmp/rdr-suite.lock` names the holder when it is not
free.

**A stale `.writer.lock` is not a live writer.** `run_tests.py` stops its Studio
with `terminate()` — `TerminateProcess` on Windows, uncatchable — so the
`atexit` release never fires and the lock file survives holding a dead pid.
Four occurrences in one day. Prove the pid dead before acting on it.
