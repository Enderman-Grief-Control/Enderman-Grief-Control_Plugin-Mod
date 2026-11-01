"""The four extended client checks, as observer-confirmed flows over two client sessions.

Session 1 is one client process with two holder worlds, A and B. It opens the mod's config screen
through Mod Menu, checks Cancel and Save, sets Alert, then travels A -> title -> B -> title -> A
and waits in A for a periodic monitor pass. Session 2 relaunches the client in the same game
directory. The assertions follow the Phase 0 contract in the current source:

- ``mod_menu_save_cancel``: Cancel leaves the file byte-identical and the saved values on screen;
  Save writes exactly the staged values, clears nothing when the staged policy is Off, and any
  carrier work runs on the server thread.
- ``settings_persistence``: the saved values show in world A, world B, and on the title screen; a
  relaunch starts from identical bytes, shows the same values, and alerts the holders on join.
- ``world_transition``: every world entry is confirmed by its data pack's world ID, every exit logs
  "All dimensions are saved", and the process exits 0 after WM_CLOSE.
- ``monitor_state``: each session discovers its own holders on the server thread soon after join,
  never names another world's holder, does nothing for a title-screen Save, and makes its first
  periodic pass no earlier than 2,400 server ticks after its server started (game time from the
  data pack, since the pause menu stops the integrated server).

A step the observer never confirms blocks the remaining checks with ``harness_or_protocol``.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..acquire import hash_file
from ..profiles import install_jars
from . import scenario
from .observer import REQUIRED, Snapshot, Snapshots, build_agent, derive, tiny_from_jar, write_properties
from .ui import UiBlocked, UiDriver
from .world import install_world, write_pack

if TYPE_CHECKING:
    from ..client_row import ClientRow

CONFIG_NAME = "no-enderman-grief.json"
CONFIG_SCREEN = "endermangriefcontrol.fabric.client.EndermanGriefControlConfigScreen"
MODS_SCREEN = "com.terraformersmc.modmenu.gui.ModsScreen"
SAVE = "Save & Quit"
SERVER_THREAD = "Server thread"
SAVED_VALUES = {"prevent": "ON", "holders": "Alert", "removals": "ON", "denied": "ON"}
WORLDS = {"A": ("egc-world-a", 1), "B": ("egc-world-b", 2)}
UI_GAME_DIR = "game-ui"
UI_PACK = "egcui"
# The state of a player who had protection off: saved OFF / Auto-Clear.
INITIAL_CONFIG = {"enabled": False, "loggingEnabled": True, "logRemovals": True, "denialRateLimitSeconds": 10,
                  "heldBlockHandling": "auto-clear"}
LABELS = {"prevent": "Prevent Enderman Grief", "holders": "Stuck Holders", "removals": "Log Removals",
          "denied": "Log Denied Attempts"}
RESOLUTION_PERIOD_TICKS = 2400  # HeldBlockMonitor: a periodic discovery/resolution pass every 120 s
JOIN_WINDOW_TICKS = 600  # discovery "soon after join"
REPORT_TICKS = scenario.HOLDER_REPORT_TICKS
# Screens and widgets the flows need beyond the observer's own required members.
FLOW_MEMBERS = ("class.PauseScreen", "class.TitleScreen", "class.SelectWorldScreen", "class.EditBox",
                "class.AbstractSelectionList", "method.AbstractSelectionList.getSelected",
                "method.AbstractSelectionList.getRowTop", "method.AbstractSelectionList.getRowBottom",
                "method.AbstractSelectionList.getRowLeft", "method.AbstractSelectionList.getRowWidth",
                "method.EditBox.getValue")
CARRIER = re.compile(r"^\[[\d:]+\] \[([^\]]+)/INFO\]: \[Enderman\] (holding a block at|holding cleared at) "
                     r"\((-?\d+), (-?\d+), (-?\d+)\)\.")
EXCEPTION = re.compile(r"(Exception|\[[^\]]+/ERROR\])")
# The title screen checks Realms; with the offline dummy token that always fails (seen in 10F's
# session and in every extended session since), so those lines are not the Save's doing.
OFFLINE_REALMS = re.compile(r"[Rr]ealms")


def config_values(snapshot: Snapshot) -> dict[str, str]:
    """The config screen's shown values, read from its widget messages ("Label: Value")."""
    values = {}
    for key, label in LABELS.items():
        widget = snapshot.widget(prefix=f"{label}: ")
        if widget is not None:
            values[key] = widget["message"][len(label) + 2:]
    return values


def carrier_lines(lines: list[str], since: int = 0, until: int | None = None) -> list[dict[str, Any]]:
    found = []
    for index, line in enumerate(lines[since:until], start=since):
        match = CARRIER.search(line)
        if match:
            found.append({"index": index, "thread": match.group(1),
                          "kind": "alert" if match.group(2).startswith("holding a block") else "clear",
                          "pos": (int(match.group(3)), int(match.group(4)), int(match.group(5))), "text": line[:200]})
    return found


def holder_reports(lines: list[str], since: int = 0) -> list[scenario.Report]:
    """Holder reports at or after line ``since``, with indexes into the whole log."""
    return [report for report in scenario.parse_results(lines) if report.label == "holder" and report.index >= since]


def settings(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """The settings the config screen edits; the file also holds message defaults the mod writes."""
    return None if config is None else {key: config.get(key) for key in INITIAL_CONFIG}


def finish_after_block(checks: Any, reason: str) -> None:
    """Close the unfinished checks once a UI step went unconfirmed.

    A check that already recorded a contradiction through confirmed steps stays a failure; any
    other check is blocked, never passed.
    """
    for check in checks:
        if check.finished:
            continue
        failed = [assertion["name"] for assertion in check.assertions if not assertion["ok"]]
        if failed:
            check.finish("fail", f"assertion(s) failed: {', '.join(failed)}; a later UI step was not confirmed: "
                                 f"{reason}", "unclassified")
        else:
            check.finish("blocked", f"UI step not confirmed: {reason}", "harness_or_protocol")


def javac_for(java: str, javac: str | None) -> str:
    """An explicit javac, else the one beside the java executable, else javac on PATH."""
    if javac:
        return javac
    java_path = Path(java)
    if java_path.parent != Path("."):
        sibling = java_path.with_name("javac.exe" if java_path.suffix.lower() == ".exe" else "javac")
        if sibling.exists():
            return str(sibling)
    return "javac"


def first_periodic_alert(lines: list[str], world_id: int, session: int) -> dict[str, Any] | None:
    """The first carrier line after the join window of one world session, bracketed by game time.

    Returns the alert with the ``now`` of the reports just before and after it, and the session's
    ``start``; None when no such alert was logged.
    """
    reports = [(report.index, report.values) for report in scenario.parse_results(lines)
               if report.label == "holder" and report.values.get("world") == world_id
               and report.values.get("session") == session]
    if not reports:
        return None
    start = reports[0][1]["start"]
    first_index, last_index = reports[0][0], reports[-1][0]
    for carrier in carrier_lines(lines, first_index, last_index + 1):
        before = [values for index, values in reports if index < carrier["index"]]
        after = [values for index, values in reports if index > carrier["index"]]
        if not before or not after:
            continue
        if after[0]["now"] - start <= JOIN_WINDOW_TICKS:
            continue  # discovery soon after join, not a periodic pass
        return {**carrier, "start": start, "before_now": before[-1]["now"], "after_now": after[0]["now"]}
    return None


class ExtendedRun:
    def __init__(self, row: ClientRow, prepared: dict[str, Any]):
        self.row = row
        self.prepared = prepared
        self.checks = {name: row.checks[name].start() for name in row.extended_names}
        self.game_dir = row.out_dir / UI_GAME_DIR
        self.config = self.game_dir / "config" / CONFIG_NAME
        self.names: dict[str, str] = {}
        self.observer: dict[str, Any] = {}
        self.project_id = row.selection.artifact["descriptor"]["id"]
        self.cleared: set[tuple[int, int, int]] = set()  # holder positions seen cleared, across both sessions
        self.session = None
        self.ui: UiDriver | None = None

    # Setup -----------------------------------------------------------------------------------
    def prepare(self) -> list[str]:
        """Agent jar, name map, and the extended game directory with worlds A and B."""
        row = self.row
        agent, identity = build_agent(javac_for(row.java, row.javac), row.cache_dir)
        version = row.files.version
        mappings = self.prepared["acquirer"].client_mappings(version)
        intermediary = next(lib.file for lib in row.files.libraries if "/intermediary/" in lib.path)
        self.names, missing = derive(mappings.read_text(encoding="utf-8"), tiny_from_jar(intermediary))
        self.observer = {**identity, "agent_path": str(agent), "client_mappings_sha1": version["downloads"]["client_mappings"]["sha1"],
                         "intermediary_sha256": hash_file(intermediary), "missing_members": missing, "sessions": {}}
        row.observer = self.observer
        for folder in ("config", "saves"):
            (self.game_dir / folder).mkdir(parents=True, exist_ok=True)
        install_jars(row.jar_specs, self.prepared["sources"], self.game_dir / "mods")
        (self.game_dir / "options.txt").write_text((row.game_dir / "options.txt").read_text(encoding="utf-8"),
                                                   encoding="utf-8")
        self.config.write_text(json.dumps(INITIAL_CONFIG, indent=2) + "\n", encoding="utf-8")
        packs = {}
        for _key, (name, world_id) in WORLDS.items():
            world = install_world(self.prepared["world"], self.game_dir / "saves", name)
            packs[name] = write_pack(world, UI_PACK, scenario.holder_functions(row.gamerules, world_id),
                                     row.files.pack["pack_version"], "EGC holder world")
        row.environment["extended"] = {"config_before": INITIAL_CONFIG, "worlds": dict(WORLDS.values()),
                                       "datapacks": packs}
        return missing

    def start_session(self, name: str, quick_play: str) -> None:
        row = self.row
        evidence = row.evidence_dir / name
        evidence.mkdir(parents=True, exist_ok=True)
        output = evidence / "observer.jsonl"
        properties = row.out_dir / f"observer-{name}.properties"
        names_sha256 = write_properties(properties, self.names, output)
        agent = self.observer["agent_path"]
        self.observer["sessions"][name] = {"names_sha256": names_sha256, "snapshots": f"{name}/observer.jsonl"}
        self.session = row.launch(name, quick_play, [f"-javaagent:{agent}={properties}"], game_dir=self.game_dir)
        self.ui = UiDriver(lambda: self.session.process.pid if self.session.running() else None, Snapshots(output),
                           self.game_dir, evidence, row.say, row.limits.step_s)

    # Log helpers -----------------------------------------------------------------------------
    def lines(self) -> list[str]:
        return self.session.lines()

    def wait_log(self, what: str, predicate: Callable[[list[str]], Any], timeout_s: float) -> Any:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and self.session.running():
            found = predicate(self.lines())
            if found:
                return found
            time.sleep(0.5)
        raise UiBlocked(f"not observed in the client log: {what}")

    def wait_report(self, world_id: int, session: int | None = None, since: int = 0, timeout_s: float = 90,
                    count: int = 1) -> list[scenario.Report]:
        def matching(lines: list[str]) -> list[scenario.Report] | None:
            found = [r for r in holder_reports(lines, since) if r.values.get("world") == world_id
                     and (session is None or r.values.get("session") == session)]
            return found if len(found) >= count else None

        return self.wait_log(f"a holder report from world {world_id}" + (f" session {session}" if session else ""),
                             matching, timeout_s)

    def saves(self) -> int:
        return sum(1 for line in self.lines() if scenario.SAVED.search(line))

    def check(self, name: str, label: str, ok: bool, expected: Any, observed: Any) -> bool:
        if name in self.checks:
            self.checks[name].check(label, ok, expected, observed)
        return ok

    # UI flows --------------------------------------------------------------------------------
    def on(self, key: str) -> Callable[[Snapshot], bool]:
        """A predicate for one screen: "config", "mods", or a vanilla screen from the name map."""
        name = {"config": CONFIG_SCREEN, "mods": MODS_SCREEN}.get(key) or self.names.get(f"class.{key}")
        return lambda snapshot: snapshot.screen_class == name

    @staticmethod
    def in_world(snapshot: Snapshot) -> bool:
        return snapshot.data.get("screen") is None

    def open_pause(self) -> None:
        self.ui.press_key("Escape", self.on("PauseScreen"), "the pause screen")

    def mod_row(self, snapshot: Snapshot) -> dict[str, Any] | None:
        return next((row for lst in snapshot.lists if lst.get("field") == "modList"
                     for row in lst.get("rows", []) if row.get("mod") == self.project_id), None)

    def open_config(self) -> dict[str, str]:
        """From the pause or title screen (or Mod Menu's list): our mod -> configure; returns the shown values."""
        ui = self.ui
        listed = self.on("mods")
        if not listed(ui.current()):
            ui.press_widget("the Mods button", lambda s: s.widget(prefix="Mods"), listed, "Mod Menu's list")
        mods = ui.wait(f"{self.project_id} listed in Mod Menu", lambda s: listed(s) and (
            s.data.get("selected_mod") == self.project_id or self.mod_row(s) is not None))
        if mods.data.get("selected_mod") != self.project_id:
            ui.press_widget(f"the {self.project_id} entry", self.mod_row,
                            lambda s: s.data.get("selected_mod") == self.project_id, f"{self.project_id} selected")
        screen = ui.press_widget("the configure button", lambda s: s.widget(field_name="configureButton"),
                                 self.on("config"), "the mod's config screen")
        values = config_values(screen)
        if set(values) != set(LABELS):
            raise UiBlocked(f"config screen widgets not recognized: {screen.messages()}")
        return values

    def stage(self, key: str, value: str) -> dict[str, str]:
        """Cycle one config button until it shows ``value``; each click must change what is shown."""
        label = LABELS[key]
        for _ in range(4):
            current = config_values(self.ui.current())
            if current.get(key) == value:
                return current
            self.ui.press_widget(label, lambda s: s.widget(prefix=f"{label}: "),
                                 lambda s, before=current.get(key): config_values(s).get(key) not in (None, before),
                                 f"{label} cycled")
        raise UiBlocked(f"{LABELS[key]} never showed {value}")

    def press(self, message: str, then: Callable[[Snapshot], bool], expect: str) -> Snapshot:
        return self.ui.press_widget(message, lambda s: s.widget(message=message), then, expect)

    def close_mods(self, parent: str) -> None:
        snapshot = self.ui.current()
        if snapshot.widget(message="Done") is not None:
            self.press("Done", self.on(parent), f"back on the {parent}")
        else:
            self.ui.press_key("Escape", self.on(parent), f"back on the {parent}")

    def resume(self) -> None:
        self.press("Back to Game", self.in_world, "back in the world")

    def quit_to_title(self, world: str) -> None:
        """Pause screen -> Save and Quit to Title, confirmed by the title screen and a new world save."""
        start = len(self.lines())
        self.ui.press_widget("Save and Quit to Title", lambda s: s.widget(prefix="Save and Quit"),
                             self.on("TitleScreen"), "the title screen", timeout_s=90)
        line = self.wait_log("All dimensions are saved", lambda lines: next(
            (text for text in lines[start:] if scenario.SAVED.search(text)), None), 60)
        self.check("world_transition", f"{world} -> title: the world saved", True, "All dimensions are saved",
                   line[:200])

    def enter_world(self, key: str, session: int) -> list[scenario.Report]:
        """Title -> Singleplayer -> search -> select -> play; confirmed by the world's data-pack report."""
        name, world_id = WORLDS[key]
        ui = self.ui
        since = len(self.lines())
        listing = self.press("Singleplayer", self.on("SelectWorldScreen"), "the world list")
        edit_box = self.names.get("class.EditBox")

        def search(snapshot: Snapshot) -> dict[str, Any] | None:
            return next((w for w in snapshot.widgets if w.get("class") == edit_box), None)

        box = search(listing)
        if box is None:
            raise UiBlocked("the world list has no search box")
        if not box.get("focused"):
            ui.press_widget("the world search box", search, lambda s: bool((search(s) or {}).get("focused")),
                            "the search box focused")
        ui.type_text(name)
        filtered = ui.wait(f"only {name} listed", lambda s: bool(s.lists) and s.lists[0].get("entries") == 1
                           and (search(s) or {}).get("value") == name, after_seq=listing.seq)
        if not filtered.lists[0].get("rows"):
            raise UiBlocked("the world list reports no row geometry")
        ui.press_widget(f"the {name} entry", lambda s: (s.lists[0].get("rows") or [None])[0] if s.lists else None,
                        lambda s: bool(s.lists) and s.lists[0].get("selected") == 0, f"{name} selected")
        ui.press_widget("Play Selected World", lambda s: s.widget(message="Play Selected World"), self.in_world,
                        f"inside {name}", timeout_s=90)
        reports = self.wait_report(world_id, session, since)
        self.check("world_transition", f"entered {name} (data pack world {world_id}, session {session})", True,
                   {"world": world_id, "session": session}, reports[0].values)
        return reports

    # The flows -------------------------------------------------------------------------------
    def save_cancel(self) -> None:
        """World A, session 1: Cancel, then Save with a policy of Off (``mod_menu_save_cancel``)."""
        name = "mod_menu_save_cancel"
        self.wait_report(1, 1, timeout_s=180)
        self.ui.screenshot("world-a")
        self.open_pause()
        shown = self.open_config()
        self.ui.screenshot("config-initial")
        self.check(name, "config screen shows the saved OFF / Auto-Clear", shown.get("prevent") == "OFF"
                   and shown.get("holders") == "Auto-Clear", {"prevent": "OFF", "holders": "Auto-Clear"}, shown)
        self.stage("prevent", "ON")
        staged = self.stage("holders", "Alert")
        self.ui.screenshot("cancel-staged")
        self.check(name, "Cancel: the staged values are on screen", staged.get("prevent") == "ON"
                   and staged.get("holders") == "Alert", {"prevent": "ON", "holders": "Alert"}, staged)
        before_bytes, before_line = hash_file(self.config), len(self.lines())
        self.press("Cancel", self.on("mods"), "the config screen closed by Cancel")
        time.sleep(2)
        self.check(name, "Cancel: config bytes unchanged", hash_file(self.config) == before_bytes, before_bytes,
                   hash_file(self.config))
        reopened = self.open_config()
        self.ui.screenshot("reopened-after-cancel")
        self.check(name, "Cancel: reopening shows the saved values", reopened.get("prevent") == "OFF"
                   and reopened.get("holders") == "Auto-Clear", {"prevent": "OFF", "holders": "Auto-Clear"}, reopened)
        cancel_carrier = carrier_lines(self.lines(), before_line)
        self.check(name, "Cancel: no carrier line logged", not cancel_carrier, [], [c["text"] for c in cancel_carrier])
        self.stage("prevent", "ON")
        staged = self.stage("holders", "Off")
        self.ui.screenshot("save-staged")
        self.check(name, "Save: the staged values are on screen", staged.get("prevent") == "ON"
                   and staged.get("holders") == "Off", {"prevent": "ON", "holders": "Off"}, staged)
        save_line = len(self.lines())
        self.press(SAVE, self.on("mods"), "the config screen closed by Save")
        expected = {**INITIAL_CONFIG, "enabled": True, "heldBlockHandling": "off"}
        # The commit may run on the server thread, so allow it a moment to reach the file.
        self.wait_quietly(lambda: settings(self.read_config()) == expected, 15)
        saved = settings(self.read_config())
        self.check(name, "Save: the file holds exactly the staged values", saved == expected, expected, saved)
        self.close_mods("PauseScreen")
        self.resume()
        reports = self.wait_report(1, 1, since=len(self.lines()), count=2, timeout_s=60)
        self.ui.screenshot("world-a-after-save")
        carriers = carrier_lines(self.lines(), save_line)
        clears = [c["text"] for c in carriers if c["kind"] == "clear"]
        self.check(name, "Save: no holder cleared", not clears, [], clears)
        last = reports[-1].values
        self.check(name, "Save: the holder still carries its block", last.get("carrying") == last.get("holders") == 1,
                   {"holders": 1, "carrying": 1}, last)
        threads = sorted({c["thread"] for c in carriers})
        self.check(name, "carrier work runs on the server thread", all(t == SERVER_THREAD for t in threads),
                   [SERVER_THREAD], threads)
        self.checks[name].finish()  # every assertion of this check is recorded; later steps cannot change it

    def wait_quietly(self, condition: Callable[[], bool], timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.5)
        return condition()

    def read_config(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def expect_shown(self, where: str, expected: dict[str, str]) -> None:
        shown = self.open_config()
        self.ui.screenshot(f"config-{where}")
        self.check("settings_persistence", f"the saved values show {where}", shown == expected, expected, shown)
        self.press("Cancel", self.on("mods"), "the config screen closed by Cancel")

    def session_one(self) -> None:
        self.start_session("ui-s1", WORLDS["A"][0])
        self.save_cancel()
        # Alert in world A, then the same values in A, on the title screen, and in B.
        self.open_pause()
        self.open_config()
        self.stage("holders", "Alert")
        self.press(SAVE, self.on("mods"), "the config screen closed by Save")
        self.expect_shown("in world A", SAVED_VALUES)
        self.close_mods("PauseScreen")
        self.quit_to_title("A")
        before_title_save = len(self.lines())
        shown = self.open_config()
        self.ui.screenshot("config-title")
        self.check("settings_persistence", "the saved values show on the title screen", shown == SAVED_VALUES,
                   SAVED_VALUES, shown)
        self.press(SAVE, self.on("mods"), "the title-screen config saved")
        time.sleep(3)
        window = self.lines()[before_title_save:]
        carriers = carrier_lines(window)
        errors = [line[:200] for line in window if EXCEPTION.search(line) and not OFFLINE_REALMS.search(line)]
        self.check("monitor_state", "title-screen Save with no world: no carrier action", not carriers, [],
                   [c["text"] for c in carriers])
        self.check("monitor_state", "title-screen Save with no world: no exception", not errors, [], errors)
        self.close_mods("TitleScreen")
        self.visit_b()
        self.return_to_a()

    def visit_b(self) -> None:
        reports = self.enter_world("B", 1)
        self.join_discovery("B", reports[0], expected={(6, 100, scenario.holder_z(2))})
        self.open_pause()
        self.expect_shown("in world B", SAVED_VALUES)
        self.close_mods("PauseScreen")
        self.quit_to_title("B")

    def return_to_a(self) -> None:
        reports = self.enter_world("A", 2)
        z = scenario.holder_z(1)
        self.join_discovery("A", reports[0], expected={(6, 100, z), (8, 100, z)})
        start = reports[0].values["start"]
        target = start + RESOLUTION_PERIOD_TICKS + 3 * REPORT_TICKS
        self.wait_log(f"world A game time past {target}", lambda lines: any(
            r.values.get("world") == 1 and r.values.get("session") == 2 and r.values.get("now", 0) >= target
            for r in holder_reports(lines)), (RESOLUTION_PERIOD_TICKS + 600) / 20 + 60)
        periodic = first_periodic_alert(self.lines(), 1, 2)
        if periodic is None:
            self.checks["monitor_state"].observe("no periodic alert after the join window", start=start, waited_to=target)
            self.check("monitor_state", "a periodic pass was observed in world A's second session", False,
                       "an alert after the join window", None)
        else:
            self.checks["monitor_state"].observe("first periodic alert", **{k: v for k, v in periodic.items() if k != "index"})
            self.check("monitor_state", f"first periodic alert no earlier than {RESOLUTION_PERIOD_TICKS} ticks after "
                       "the server started", periodic["after_now"] - start >= RESOLUTION_PERIOD_TICKS,
                       f">= {RESOLUTION_PERIOD_TICKS} ticks after start {start}",
                       f"between {periodic['before_now'] - start} and {periodic['after_now'] - start} ticks")
        self.ui.screenshot("world-a-return")
        self.open_pause()
        self.quit_to_title("A (second visit)")

    def join_discovery(self, key: str, report: scenario.Report, expected: set[tuple[int, int, int]]) -> None:
        """The session alerts exactly its own world's holders, on the server thread, soon after join.

        Holders an earlier step was seen clearing no longer carry anything, so they are not expected;
        that clear is already judged where it happened, and is not counted twice.
        """
        name, world_id = WORLDS[key]
        session = report.values["session"]
        start = report.values["start"]
        self.wait_report(world_id, session, since=report.index, count=JOIN_WINDOW_TICKS // REPORT_TICKS)
        lines = self.lines()
        self.cleared |= {c["pos"] for c in carrier_lines(lines) if c["kind"] == "clear"}
        expected = expected - self.cleared
        window = [r for r in scenario.parse_results(lines) if r.label == "holder" and r.values.get("world") == world_id
                  and r.values.get("session") == session and r.values["now"] - start <= JOIN_WINDOW_TICKS]
        end = window[-1].index if window else report.index
        alerts = [c for c in carrier_lines(lines, report.index - 5, end + 1) if c["kind"] == "alert"]
        z = scenario.holder_z(world_id)
        self.check("monitor_state", f"{name} session {session}: its holders alerted soon after join",
                   expected <= {c["pos"] for c in alerts}, sorted(expected), sorted({c["pos"] for c in alerts}))
        foreign = [c["text"] for c in carrier_lines(lines, report.index - 5, end + 1) if c["pos"][2] != z]
        self.check("monitor_state", f"{name} session {session}: no carrier line names another world's holder",
                   not foreign, [], foreign)
        threads = sorted({c["thread"] for c in alerts})
        self.check("monitor_state", f"{name} session {session}: discovery on the server thread",
                   (bool(threads) or not expected) and all(t == SERVER_THREAD for t in threads), [SERVER_THREAD],
                   threads)
        if key == "B" or session > 1:
            self.check("settings_persistence", f"Alert mode alerts the holders on joining {name}",
                       expected <= {c["pos"] for c in alerts}, sorted(expected), sorted({c["pos"] for c in alerts}))

    def close_session(self, reason: str = "flow finished") -> int | None:
        session = self.session
        session.record.end_reason = reason
        session.record.seconds_to_end = round(time.monotonic() - session._started, 1)
        self.row.finish_session(session, crashed=False, game_dir=self.game_dir)
        return session.record.exit_code

    def session_two(self, left_by_first: str) -> None:
        """Relaunch in the same game directory: identical bytes, same values, alerts on join."""
        before = hash_file(self.config)
        self.check("settings_persistence", "the relaunch starts from the bytes the first session left",
                   before == left_by_first, left_by_first, before)
        self.start_session("ui-s2", WORLDS["B"][0])
        reports = self.wait_report(2, 2, timeout_s=180)
        self.join_discovery("B", reports[0], expected={(6, 100, scenario.holder_z(2)), (8, 100, scenario.holder_z(2))})
        self.open_pause()
        self.expect_shown("after a relaunch", SAVED_VALUES)
        self.close_mods("PauseScreen")
        self.quit_to_title("B (relaunch)")
        code = self.close_session()
        after = hash_file(self.config)
        self.check("settings_persistence", "the relaunched session left the file unchanged (Cancel only)",
                   after == before, before, after)
        self.check("world_transition", "the relaunched client exits 0 after WM_CLOSE", code == 0, 0, code)

    def run(self) -> None:
        row = self.row
        try:
            missing = self.prepare()
            needed = [key for key in (*REQUIRED, *FLOW_MEMBERS) if key in missing]
            if needed:
                raise UiBlocked(f"the observer's name map is missing members: {needed}")
            self.session_one()
            code = self.close_session()
            self.check("world_transition", "the client exits 0 after WM_CLOSE", code == 0, 0, code)
            left = hash_file(self.config)
            self.checks["settings_persistence"].observe("config after session 1", sha256=left,
                                                         values=self.read_config())
            self.session_two(left)
        except UiBlocked as blocked:
            row.say(f"extended checks blocked: {blocked}")
            if self.session is not None and self.session.running():
                self.close_session("flow blocked")
            finish_after_block(self.checks.values(), str(blocked))
        for check in self.checks.values():
            if not check.finished:
                check.finish()
