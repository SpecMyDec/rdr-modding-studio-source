"""Which part of the game a level belongs to.

Red Dead Revolver ships two separate games on one disc and the archives do not
say which is which -- `LVLS_SNG`, `LVLS_M2` and `LVLS_M4` sit side by side:

    Campaign    LVLS_SNG, 35 archives. 28 of them are named by the 32 missions
                in levelflow.csv; the other 7 are duel / bonus / cutscene
                stages the mission list never references.
    Showdown    LVLS_M2 and LVLS_M4 -- the same 13 versus stages in 2-player
                and 4-player variants. Which ones are available, and the 48
                playable characters, are gated by the MP unlock criteria in
                unlockables.csv (ENEMIES_KILLED, HEAD_SHOTS, DUELS_WON ...).

Mapping a Showdown archive to its unlock row needs the trailing 2/4 stripped:
BRIDGE2 and BRIDGE4 are both the `lvl_bridge` stage.
"""

from . import campaign
from .level import list_levels

CAMPAIGN = "campaign"
SHOWDOWN2 = "showdown2"
SHOWDOWN4 = "showdown4"
BONUS = "bonus"
FRONTEND = "frontend"

MODE_LABELS = {
    CAMPAIGN: "Campaign",
    SHOWDOWN2: "Showdown - 2 player",
    SHOWDOWN4: "Showdown - 4 player",
    BONUS: "Campaign (unused by the mission list)",
    FRONTEND: "Main menu",
}

# The main menu is a level too. FRONT.DAT holds one `.lvl`, a `.bnds`, three
# `.znes`, three `.lnes`, 19 `.mod` and an `.rsc` -- the 3D scene behind the
# title screen -- and `Level(game, "FRONT.DAT")` has always parsed it: 29
# nodes, 25 zones, geometry and all.
#
# What kept it out of the editor is that it is not under `LVLS_*`, so
# `list_levels` does not return it. That is correct for `list_levels` and must
# stay that way: the same list drives the campaign/Showdown mission mapping,
# the disc-wide asset sweep and the link index, and FRONT.DAT belongs in none
# of them -- it is the front end, not a game mode. The level editor is the one
# caller that wants it, so the CATALOGUE adds it and `list_levels` is left
# alone.
FRONT_ARCHIVE = "FRONT.DAT"


def base_name(level_name, group=None):
    """BRIDGE2 / BRIDGE4 -> BRIDGE, so variants of a stage group together.

    Only applied to the Showdown archives. Campaign levels legitimately end in
    digits (THEATER2, TOMB2..TOMB5) and stripping those would invent a stage
    relationship that does not exist.
    """
    n = level_name.upper()
    if group in ("LVLS_M2", "LVLS_M4") and len(n) > 1 and n[-1] in "24":
        n = n[:-1]
    return n


def mission_archive_key(level_name, variation, archives):
    """Which archive a levelflow row loads.

    The rule the game uses: take the level name without `lvl_`, append the
    Variation number when it is above 1, and keep the whole thing within the
    8-character ISO name -- so the BASE is truncated to make room for the
    suffix, not the finished string. That one rule explains every case:

        lvl_tomb      var 1 -> tomb                -> TOMB
        lvl_tomb      var 5 -> tomb5               -> TOMB5
        lvl_traingood var 1 -> traingood[:8]       -> TRAINGOO
        lvl_royfinal  var 7 -> royfina + 7         -> ROYFINA7
    """
    base = level_name.lower().replace("lvl_", "").strip()
    try:
        var = int(float(variation))
    except (TypeError, ValueError):
        var = 1
    suffix = str(var) if var > 1 else ""
    name = base[:8 - len(suffix)] + suffix
    for cand in (name, base + suffix, base, base[:8]):
        if cand in archives:
            return cand
    return None


def catalog(game):
    """Every level archive with the mode and progression data attached."""
    data = campaign.summary(game)

    # Resolve each MISSION to one archive, then invert.
    archives = {a.split("/", 1)[1][:-4].lower(): a for a in list_levels(game)
                if a.startswith("LVLS_SNG")}
    by_level = {}
    for i, m in enumerate(data["missions"], 1):
        arc_key = mission_archive_key(m["Level"], m["Variation"], archives)
        if arc_key:
            by_level.setdefault(arc_key, []).append(dict(number=i, **m))

    # Showdown stage unlocks, keyed by stage name with lvl_ stripped.
    stages = {}
    for u in data["unlockables"]:
        if u["type"] == "stage":
            stages[u["name"].lower().replace("lvl_", "")] = u

    out = []
    for archive in list_levels(game):
        group, fname = archive.split("/", 1)
        name = fname[:-4]
        entry = dict(archive=archive, name=name, group=group,
                     base=base_name(name, group))

        if group == "LVLS_M2":
            entry["mode"] = SHOWDOWN2
        elif group == "LVLS_M4":
            entry["mode"] = SHOWDOWN4
        else:
            entry["mode"] = CAMPAIGN

        if entry["mode"] == CAMPAIGN:
            hits = by_level.get(name.lower(), [])
            if hits:
                entry["missions"] = [dict(number=h["number"],
                                          character=h["Character"],
                                          weapons=h["Weapons"],
                                          reward=h["Reward $"],
                                          hidden=h["Hidden"]) for h in hits]
            else:
                entry["mode"] = BONUS
                entry["missions"] = []
        else:
            entry["missions"] = []
            u = stages.get(entry["base"].lower())
            if u:
                entry["unlock"] = dict(name=u["name"], default=u["default"],
                                       criteria=u["criteria"], value=u["value"],
                                       row=u["index"])

        # The town. "TOMB" is Brimstone -- its archives carry the shops, bank
        # and street lamps (br_generalstore.inv, lvl_tomb_streetlamps.znes),
        # revisited at missions 6/9/11/14/27 as the story burns it down.
        if entry["base"].startswith("TOMB"):
            entry["town"] = "Brimstone"

        entry["mode_label"] = MODE_LABELS[entry["mode"]]
        out.append(entry)

    # Last in the list, in its own group. It is a level the editor can open,
    # not a mission, so it carries no missions and no unlock row.
    if FRONT_ARCHIVE in game.archives():
        out.append(dict(archive=FRONT_ARCHIVE, name=FRONT_ARCHIVE[:-4],
                        group="", base=FRONT_ARCHIVE[:-4], mode=FRONTEND,
                        mode_label=MODE_LABELS[FRONTEND], missions=[]))

    order = {CAMPAIGN: 0, BONUS: 1, SHOWDOWN2: 2, SHOWDOWN4: 3, FRONTEND: 4}
    out.sort(key=lambda e: (order[e["mode"]],
                            e["missions"][0]["number"] if e["missions"] else 99,
                            e["name"]))
    return out


def summary(game):
    """Catalog plus the Showdown-side progression tables."""
    data = campaign.summary(game)
    levels = catalog(game)
    counts = {}
    for e in levels:
        counts[e["mode"]] = counts.get(e["mode"], 0) + 1
    return dict(
        levels=levels,
        counts=counts,
        labels=MODE_LABELS,
        stages=[u for u in data["unlockables"] if u["type"] == "stage"],
        characters=[u for u in data["unlockables"] if u["type"] == "character"],
        cheats=[u for u in data["unlockables"] if u["type"] == "cheat"],
        journal=[u for u in data["unlockables"] if u["type"] == "journal"],
    )
