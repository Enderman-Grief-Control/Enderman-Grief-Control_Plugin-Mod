"""Client scenarios as data packs, the facts read back from the client log, and pure verdicts.

The core scenario rebuilds the server rows' arena inside the integrated world and reports world
state as ``EGC-RESULT`` chat lines, so the client log alone is the evidence: setup, one tagged
Enderman left alone for 60 s (pickup), then four verified dirt holders checked every 30 s
(placement), after restoring the arena from its reference copy so placement never depends on the
pickup result. Data-pack functions run at permission level 2, below ``/tick``, so the client runs
at normal speed. Verdicts here are the 10F form rules rewritten as assertions over parsed facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import loader_log
from ..arena import (ARENA_BLOCKS, ARENA_MAX, ARENA_MIN, CLIENT_PLATFORM, CLIENT_VIEW, FORCELOAD, GRASS,
                     REFERENCE_MAX, REFERENCE_MIN, WORLD_TIME, box, in_arena, pos)
from ..platforms import COORDS

PICKUP_TICKS = 1200  # 60 s at normal speed
PLACEMENT_ROUND_TICKS = 600  # 30 s
PLACEMENT_ROUNDS = 6
PLACEMENT_START_TICKS = 40
HOLDER_SPOTS = ("7.5 100 7.5", "9.5 100 9.5", "7.5 100 9.5", "9.5 100 7.5")
HOLDERS = len(HOLDER_SPOTS)
CORE_WORLD = "egc-client-check"
SETUP_EXPECTED = {"arena": ARENA_BLOCKS, "grass": len(GRASS), "endermen": 1, "carry": 0, "rule_griefing": 1,
                  "rule_spawning": 0}
INITIALIZED = re.compile(r"EndermanGriefControl has been initialized\.")
INTEGRATED = re.compile(r"Starting integrated minecraft server version (\S+)")
RESULT = re.compile(r"\[CHAT\] EGC-RESULT (\S+)(.*)")
RESULT_VALUE = re.compile(r"(\w+)=(-?\d+)")
DENIAL = re.compile(r"\[Enderman\] Denied (pickup|placement) at " + COORDS + r"\.")
MODMENU_BROKEN = re.compile(r"provides a broken implementation of ModMenuApi")
LEVEL = re.compile(r"^\[[\d:]+\] \[([^\]]+)/(WARN|ERROR|FATAL)\]")
THREAD = re.compile(r"^\[[\d:]+\] \[([^\]]+)/[A-Z]+\]")
SAVED = re.compile(r"All dimensions are saved")


def joined_pattern(player: str) -> re.Pattern[str]:
    return re.compile(rf"\]: {re.escape(player)} joined the game")


def score(name: str) -> str:
    return '{"score":{"name":"#%s","objective":"egc"}}' % name


def report(label: str, names: list[str]) -> str:
    parts = ['{"text":"EGC-RESULT %s"}' % label]
    for name in names:
        parts.append('{"text":" %s="}' % name)
        parts.append(score(name))
    return "tellraw @a [" + ",".join(parts) + "]"


def _setup_world(gamerules: dict[str, str]) -> list[str]:
    return [
        f"gamerule {gamerules['mob_spawning']} false",
        f"gamerule {gamerules['daylight_cycle']} false",
        f"gamerule {gamerules['mob_griefing']} true",
        f"gamerule {gamerules['random_tick_speed']} 0",
        f"time set {WORLD_TIME}",
        "weather clear",
        FORCELOAD,
    ]


def core_functions(gamerules: dict[str, str]) -> dict[str, list[str]]:
    """The core pickup/placement scenario (also the negative control, which installs no project jar)."""
    compare = f"if blocks {box(ARENA_MIN, ARENA_MAX)} {pos(REFERENCE_MIN)} all"
    grass_count = ["scoreboard players set #grass egc 0"] + [
        f"execute if block {pos(g)} minecraft:grass_block run scoreboard players add #grass egc 1" for g in GRASS]
    state = [f"execute store result score #arena egc {compare}",
             "execute store result score #endermen egc if entity @e[type=minecraft:enderman]",
             "execute store result score #items egc if entity @e[type=minecraft:item]"]
    holding = ("execute store result score #holding egc if entity "
               '@e[type=minecraft:enderman,tag=egc_place,nbt={carriedBlockState:{Name:"minecraft:dirt"}}]')
    carry = ("execute store result score #carry egc if data entity "
             "@e[type=minecraft:enderman,tag=egc_pickup,limit=1] carriedBlockState")
    return {
        "load": ["scoreboard objectives add egc dummy",
                 "execute unless score #phase egc matches -2147483648..2147483647 run scoreboard players set #phase egc 0"],
        "tick": [
            "execute if score #phase egc matches 0 if entity @a[limit=1] if loaded 8 100 8 if loaded 20 100 8 "
            "run function egc:setup",
            "execute if score #phase egc matches 1.. run scoreboard players add #t egc 1",
            f"execute if score #phase egc matches 1 if score #t egc matches {PICKUP_TICKS}.. run function egc:pickup_check",
            f"execute if score #phase egc matches 2 if score #t egc matches {PLACEMENT_START_TICKS}.. "
            "run function egc:placement_start",
            f"execute if score #phase egc matches 3 if score #t egc matches {PLACEMENT_ROUND_TICKS}.. "
            "run function egc:placement_check",
        ],
        "setup": [
            "scoreboard players set #phase egc 1",
            "scoreboard players set #t egc 0",
            *_setup_world(gamerules),
            f"execute store result score #rule_griefing egc run gamerule {gamerules['mob_griefing']}",
            f"execute store result score #rule_spawning egc run gamerule {gamerules['mob_spawning']}",
            "kill @e[type=!minecraft:player]",
            f"fill {box(ARENA_MIN, ARENA_MAX)} minecraft:glass hollow",
            *[f"setblock {pos(g)} minecraft:grass_block" for g in GRASS],
            f"clone {box(ARENA_MIN, ARENA_MAX)} {pos(REFERENCE_MIN)}",
            f"setblock {pos(CLIENT_PLATFORM)} minecraft:glass",
            f"tp @a {CLIENT_VIEW}",
            'summon minecraft:enderman 8.5 100 8.5 {Tags:["egc_pickup"],PersistenceRequired:1b}',
            carry, *grass_count, *state,
            report("setup", ["arena", "grass", "endermen", "carry", "rule_griefing", "rule_spawning"]),
        ],
        "pickup_check": [
            "scoreboard players set #phase egc 2",
            "scoreboard players set #t egc 0",
            carry,
            "execute store result score #present egc if entity @e[type=minecraft:enderman,tag=egc_pickup]",
            *grass_count, *state,
            report("pickup", ["arena", "grass", "present", "carry", "endermen"]),
            "kill @e[type=minecraft:enderman]",
        ],
        "placement_start": [
            "kill @e[type=minecraft:item]",
            "kill @e[type=minecraft:experience_orb]",
            f"clone {box(REFERENCE_MIN, REFERENCE_MAX)} {pos(ARENA_MIN)}",
            *[f'summon minecraft:enderman {spot} {{Tags:["egc_place"],PersistenceRequired:1b,'
              f'carriedBlockState:{{Name:"minecraft:dirt"}}}}' for spot in HOLDER_SPOTS],
            "scoreboard players set #phase egc 3",
            "scoreboard players set #t egc 0",
            "scoreboard players set #round egc 0",
            holding, *state,
            report("placement-start", ["arena", "holding", "endermen", "items"]),
        ],
        "placement_check": [
            "scoreboard players add #round egc 1",
            "scoreboard players set #t egc 0",
            holding, *state,
            report("placement", ["round", "arena", "holding", "endermen", "items"]),
            f"execute if score #round egc matches {PLACEMENT_ROUNDS}.. run scoreboard players set #phase egc 4",
            'execute if score #phase egc matches 4 run tellraw @a {"text":"EGC-RESULT done"}',
        ],
    }


def holder_z(world_id: int) -> int:
    """Each holder world keeps its holders on its own row, so a carrier line names its world."""
    return 4 + 2 * world_id


def holder_functions(gamerules: dict[str, str], world_id: int) -> dict[str, list[str]]:
    """A holder world: each session a player enters summons one fresh NoAI dirt holder.

    Holders of session N stand at x = 4 + 2N on the world's own row (z = 4 + 2 * world ID). Every
    100 ticks the pack reports the world, session, holder counts, and game time, including the game
    time at which this session's server started (the data pack loads when the server starts).
    """
    z = holder_z(world_id)
    return {
        "load": ["scoreboard objectives add egc dummy",
                 f"scoreboard players set #world egc {world_id}",
                 "scoreboard players add #session egc 1",
                 "execute unless score #setup egc matches -2147483648..2147483647 run scoreboard players set #setup egc 0",
                 "execute store result score #start egc run time query gametime",
                 "scoreboard players set #t egc 0"],
        "tick": ["execute if score #setup egc < #session egc if entity @a[limit=1] if loaded 8 100 8 "
                 "run function egc:session_setup",
                 "scoreboard players add #t egc 1",
                 "execute if score #t egc matches 100.. run function egc:report"],
        "session_setup": [
            "scoreboard players operation #setup egc = #session egc",
            *_setup_world(gamerules),
            f"fill {box(ARENA_MIN, ARENA_MAX)} minecraft:glass hollow",
            f"setblock {pos(CLIENT_PLATFORM)} minecraft:glass",
            f"tp @a {CLIENT_VIEW}",
            "scoreboard players operation #x egc = #session egc",
            "scoreboard players operation #x egc += #session egc",
            "scoreboard players add #x egc 4",
            "execute store result storage egc:ui x int 1 run scoreboard players get #x egc",
            "execute store result storage egc:ui session int 1 run scoreboard players get #session egc",
            "function egc:summon_holder with storage egc:ui",
            "function egc:report",
        ],
        "summon_holder": [f'$summon minecraft:enderman $(x).5 100 {z}.5 {{Tags:["egc_holder","egc_s$(session)"],'
                          'NoAI:1b,PersistenceRequired:1b,carriedBlockState:{Name:"minecraft:dirt"}}'],
        "report": [
            "scoreboard players set #t egc 0",
            "execute store result score #holders egc if entity @e[type=minecraft:enderman,tag=egc_holder]",
            "execute store result score #carrying egc if entity "
            "@e[type=minecraft:enderman,tag=egc_holder,nbt={carriedBlockState:{}}]",
            "execute store result score #now egc run time query gametime",
            report("holder", ["world", "session", "holders", "carrying", "start", "now"]),
        ],
    }


@dataclass
class Report:
    index: int
    label: str
    values: dict[str, int]


@dataclass
class ClientFacts:
    lines: list[str]
    banner: tuple[str, str] | None = None
    mods: dict[str, str] = field(default_factory=dict)
    initialized: bool = False
    integrated: str | None = None
    joined: bool = False
    reports: list[Report] = field(default_factory=list)
    denials: list[tuple[int, str, tuple[int, int, int]]] = field(default_factory=list)
    mixin_errors: list[str] = field(default_factory=list)
    crash: list[str] = field(default_factory=list)
    resolution_failed: bool = False
    modmenu_broken: list[str] = field(default_factory=list)
    warn_error: list[dict[str, Any]] = field(default_factory=list)

    def results(self, label: str) -> list[dict[str, int]]:
        return [report.values for report in self.reports if report.label == label]

    def in_arena(self, kind: str) -> list[tuple[int, tuple[int, int, int]]]:
        return [(index, coords) for index, found, coords in self.denials if found == kind and in_arena(coords)]


def parse_results(lines: list[str]) -> list[Report]:
    reports = []
    for index, line in enumerate(lines):
        match = RESULT.search(line)
        if match:
            reports.append(Report(index, match.group(1), {k: int(v) for k, v in RESULT_VALUE.findall(match.group(2))}))
    return reports


def facts(lines: list[str], player: str) -> ClientFacts:
    found = ClientFacts(lines=lines)
    found.banner = loader_log.banner(lines)
    found.mods = {entry["id"]: entry["version"] for entry in loader_log.mod_tree(lines) if entry["parent"] is None}
    found.initialized = any(INITIALIZED.search(line) for line in lines)
    integrated = next((INTEGRATED.search(line) for line in lines if INTEGRATED.search(line)), None)
    found.integrated = integrated.group(1) if integrated else None
    joined = joined_pattern(player)
    found.joined = any(joined.search(line) for line in lines)
    found.reports = parse_results(lines)
    for index, line in enumerate(lines):
        match = DENIAL.search(line)
        if match:
            found.denials.append((index, match.group(1), (int(match.group(2)), int(match.group(3)), int(match.group(4)))))
        level = LEVEL.search(line)
        if level:
            found.warn_error.append({"line": index, "thread": level.group(1), "level": level.group(2),
                                     "text": line[:400]})
    found.mixin_errors = [failure["text"] for failure in loader_log.mixin_failures(lines)]
    found.crash = loader_log.crash_lines(lines)
    found.resolution_failed = loader_log.resolution_failed(lines)
    found.modmenu_broken = [line[:300] for line in lines if MODMENU_BROKEN.search(line)]
    return found


def core_end(lines: list[str]) -> str | None:
    """Why a core-scenario session can end now, or None to keep waiting."""
    if any(loader_log.CRASH.search(line) or loader_log.RESOLUTION.search(line) for line in lines):
        return "crash or resolution failure"
    reports = parse_results(lines)
    if any(report.label == "done" for report in reports):
        return "all placement rounds reported"
    rounds = [report.values for report in reports if report.label == "placement"]
    if rounds:
        last = rounds[-1]
        changed = last.get("arena") != ARENA_BLOCKS or last.get("holding", HOLDERS) < HOLDERS
        denied = any((m := DENIAL.search(line)) and m.group(1) == "placement" for line in lines)
        if changed or denied:
            return "placement round after a denial or a change"
    return None


def setup_end(lines: list[str]) -> str | None:
    """A control ends once the world reported its setup (or the client crashed)."""
    if any(loader_log.CRASH.search(line) or loader_log.RESOLUTION.search(line) for line in lines):
        return "crash or resolution failure"
    return "setup reported" if any(report.label == "setup" for report in parse_results(lines)) else None


def refusal_end(lines: list[str]) -> str | None:
    """A missing-dependency session ends at the refusal, a crash, or once the world started anyway."""
    if any(loader_log.INCOMPATIBLE in line for line in lines):
        return "Loader refusal"
    if any(loader_log.CRASH.search(line) for line in lines):
        return "crash"
    if any(INTEGRATED.search(line) for line in lines):
        return "integrated server started"
    return None


# Verdicts ------------------------------------------------------------------------------------
# Each returns (status or None, reason or None, assertions). A None status means "derive pass/fail
# from the assertions"; "blocked" means an observation the check needs is missing.

Verdict = tuple[str | None, str | None, list[tuple[str, bool, Any, Any]]]


def startup_verdict(found: ClientFacts, minecraft: str, loader: str, project: dict[str, str] | None,
                    exit_code: int | None, closed_by: str | None) -> Verdict:
    assertions = [("Loader banner names the pinned Minecraft and Loader", found.banner == (minecraft, loader),
                   [minecraft, loader], list(found.banner) if found.banner else None)]
    if project is not None:
        assertions.append(("project listed by Loader", found.mods.get(project["id"]) == project["version"],
                           f"{project['id']} {project['version']}", found.mods.get(project["id"])))
        assertions.append(("project initialized", found.initialized, True, found.initialized))
    assertions.append(("no Mixin error", not found.mixin_errors, [], found.mixin_errors[:3]))
    assertions.append(("no crash or resolution failure", not found.crash and not found.resolution_failed, [],
                       found.crash[:3] + (["Loader resolution failure"] if found.resolution_failed else [])))
    if found.mixin_errors or found.crash or found.resolution_failed:
        return "fail", "client failed during startup", assertions
    assertions.append(("integrated server started on the pinned version", found.integrated == minecraft, minecraft,
                       found.integrated))
    assertions.append(("closed by WM_CLOSE with exit code 0", closed_by == "WM_CLOSE" and exit_code == 0,
                       "WM_CLOSE, exit 0", f"{closed_by}, exit {exit_code}"))
    if found.integrated is None:
        return "blocked", "the integrated server never started and no failure was logged", assertions
    return None, None, assertions


def world_load_verdict(found: ClientFacts, minecraft: str) -> Verdict:
    setup = found.results("setup")
    observed = setup[0] if setup else None
    assertions = [("integrated server on the pinned version", found.integrated == minecraft, minecraft, found.integrated),
                  ("player joined the world", found.joined, True, found.joined)]
    if observed is None:
        return "blocked", "the data pack never reported its setup", assertions
    assertions.append(("data-pack setup report", all(observed.get(k) == v for k, v in SETUP_EXPECTED.items()),
                       SETUP_EXPECTED, observed))
    return None, None, assertions


def pickup_verdict(found: ClientFacts) -> Verdict:
    reports = found.results("pickup")
    if not reports:
        return "blocked", "no pickup report", []
    observed = reports[0]
    denials = found.in_arena("pickup")
    unchanged = observed.get("arena") == ARENA_BLOCKS and observed.get("carry") == 0 and observed.get("grass") == len(GRASS)
    assertions = [("arena unchanged, grass present, Enderman carries nothing", unchanged,
                   {"arena": ARENA_BLOCKS, "grass": len(GRASS), "carry": 0}, observed)]
    if not unchanged:
        return "fail", f"arena or carried state changed: {observed}", assertions
    assertions.append(("pickup denial logged inside the arena", bool(denials), "at least one",
                       f"{len(denials)}; first at {denials[0][1] if denials else None}"))
    assertions.append(("the one tagged Enderman is still present", observed.get("present") == 1 and observed.get("endermen") == 1,
                       {"present": 1, "endermen": 1}, observed))
    if not denials:
        return "blocked", f"no attributable pickup denial in {PICKUP_TICKS // 20} s", assertions
    return None, None, assertions


def placement_verdict(found: ClientFacts) -> Verdict:
    starts, rounds = found.results("placement-start"), found.results("placement")
    if not starts or not rounds:
        return "blocked", "no placement report", []
    start_ok = starts[0].get("holding") == HOLDERS and starts[0].get("arena") == ARENA_BLOCKS
    if not start_ok:
        return "blocked", f"placement setup not verified: {starts[0]}", []
    bad = [r for r in rounds if r.get("arena") != ARENA_BLOCKS or r.get("holding") != HOLDERS]
    denials = found.in_arena("placement")
    assertions = [("every round: arena unchanged and every holder still carries dirt", not bad,
                   {"arena": ARENA_BLOCKS, "holding": HOLDERS}, bad[0] if bad else f"{len(rounds)} round(s) unchanged")]
    if bad:
        return "fail", f"a holder lost its dirt or the arena changed: {bad[0]}", assertions
    assertions.append(("placement denial logged inside the arena", bool(denials), "at least one", len(denials)))
    if not denials:
        return "blocked", f"no attributable placement denial in {len(rounds)} round(s)", assertions
    return None, None, assertions


def griefing_verdict(found: ClientFacts) -> Verdict:
    """Negative control: without the project jar, the scenario must see Endermen grief."""
    pickup = found.results("pickup")
    rounds = found.results("placement")
    picked = bool(pickup) and (pickup[0].get("arena") != ARENA_BLOCKS or pickup[0].get("carry") == 1)
    placed = any(r.get("arena") != ARENA_BLOCKS or r.get("holding", HOLDERS) < HOLDERS for r in rounds)
    assertions = [("griefing observed without the project jar", picked or placed, "pickup or placement changed the arena",
                   {"pickup": pickup[:1], "placement_rounds": rounds})]
    if not pickup and not rounds:
        return "blocked", "the scenario reported nothing", assertions
    return None, None, assertions


def mod_menu_verdict(found: ClientFacts, pinned_version: str, startup_status: str) -> Verdict:
    assertions = [("Mod Menu listed by Loader at the pinned version", found.mods.get("modmenu") == pinned_version,
                   pinned_version, found.mods.get("modmenu")),
                  ("no broken ModMenuApi implementation reported", not found.modmenu_broken, [], found.modmenu_broken)]
    if startup_status != "pass" and not found.modmenu_broken:
        return "blocked", f"client startup did not pass ({startup_status})", assertions
    return None, None, assertions
