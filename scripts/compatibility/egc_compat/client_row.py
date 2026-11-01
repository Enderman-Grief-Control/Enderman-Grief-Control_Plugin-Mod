"""Fabric client/integrated-world rows: automated by default, a manual evidence form as fallback.

An automated row launches the official client with the pinned Loader profile, offline (dummy
token), in a fresh game directory under the run's output, straight into a disposable flat world
through quick play. The selected install profile's jars go into ``mods/``; a data pack rebuilds the
server rows' arena and reports world state into the client log, which is the evidence. Core runs
install exactly the documented jars and never attach the screen observer. Their one input is a
``/tick sprint`` chat command once the world is set up (maintainer decision, 2026-11-01): every
scenario wait counts ticks, so it shortens the session without changing what is judged, and
``--no-sprint`` keeps normal speed. The ``modmenu`` profile adds the four extended checks on rows
that require them; those sessions always run at normal speed.

Client rows need a Windows host with an interactive desktop: posted window messages and WM_CLOSE
are Win32. On any other host every check is ``blocked (client_access)`` before anything launches.

The manual form (``--client-evidence``) remains a marked fallback. Its identity fields must match
the selected row, a form "pass" needs an observation and an existing evidence file, and its
results are labeled ``evidence_source: manual``: the hashes it reports are the observer's claims.
"""

from __future__ import annotations

import json
import platform as host_platform
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import RUNNER_VERSION, loader_log
from .acquire import AcquisitionError, HttpLimits, hash_file
from .client import scenario
from .client.allowlist import classify
from .client.launch import (GUI_SCALE, PLAYER, WINDOW_SIZE, ClientAcquirer, ClientFiles, extract_natives,
                            launch_arguments, write_argfile)
from .client.session import ClientSession
from .client.ui import post_chat_command
from .client.world import allow_commands, install_world, pack_file, template, write_pack
from .jarinfo import read_descriptor
from .limits import LimitSet
from .manifest import Manifest, Selection
from .procs import ProcessRegistry
from .profiles import acquire_jar, install_jars, jar_set, jar_set_assertions, profile_record
from .results import (CLIENT_CHECKS, CLIENT_CONTROL_CHECKS, CLIENT_CORE_CHECKS, CLIENT_EXTENDED_CHECKS,
                      CLIENT_NEGATIVE_CHECKS, REFUSAL_CHECKS, RESULT_SCHEMA_VERSION, STATUSES, Check, CheckSet,
                      compute_overall, utc_now)
from .server_row import java_identity

CONFIG_NAME = "no-enderman-grief.json"
GAME_DIR = "game"
# Denial logging on and carrier policy off, so placement denials are visible and nothing is cleared.
CORE_CONFIG = {"enabled": True, "loggingEnabled": True, "logRemovals": True, "denialRateLimitSeconds": 10,
               "heldBlockHandling": "off"}
OPTIONS = {
    "pauseOnLostFocus": "false", "onboardAccessibility": "false", "soundCategory_master": "0.0",
    "renderDistance": "4", "simulationDistance": "5", "maxFps": "30", "enableVsync": "false",
    "fullscreen": "false", "tutorialStep": "none", "skipMultiplayerWarning": "true", "narrator": "0",
    "realmsNotifications": "false", "joinedFirstServer": "true", "guiScale": str(GUI_SCALE),
}


@dataclass
class ClientLimits(LimitSet):
    http_connect_s: float = 15
    http_read_s: float = 60
    http_retries: int = 2
    row_s: float = 2700
    core_s: float = 900
    control_s: float = 300
    refusal_s: float = 180
    settle_s: float = 3
    close_s: float = 90
    crash_exit_s: float = 45
    extended_s: float = 1500
    step_s: float = 20
    sprint_ticks: int = 5000  # covers pickup (1,200), placement start (40), and six 600-tick rounds
    sprint_confirm_s: float = 15


class ClientBlocked(Exception):
    def __init__(self, reason: str, failure_class: str = "harness_or_protocol"):
        super().__init__(reason)
        self.reason = reason
        self.failure_class = failure_class


def client_check_names(selection: Selection, control: bool, negative: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(core checks, extended checks) the selected profile and mode require."""
    if negative:
        return CLIENT_NEGATIVE_CHECKS, ()
    if control:
        return CLIENT_CONTROL_CHECKS, ()
    if selection.profile.refusal_ids:
        return REFUSAL_CHECKS, ()
    with_mod_menu = "modmenu" in selection.profile.dependencies
    core = CLIENT_CHECKS if with_mod_menu else CLIENT_CORE_CHECKS
    extended = CLIENT_EXTENDED_CHECKS if with_mod_menu and selection.target.get("extended_checks_required") else ()
    return core, extended


def pack_paths(run_dir: Path, extended: bool) -> list[Path]:
    """The deepest data-pack file of each world a client run writes, for the Windows path check."""
    paths = [pack_file(run_dir / GAME_DIR / "saves" / scenario.CORE_WORLD, scenario.CORE_PACK,
                       max(scenario.CORE_FUNCTIONS, key=len))]
    if extended:
        from .client.extended import UI_GAME_DIR, UI_PACK, WORLDS
        paths += [pack_file(run_dir / UI_GAME_DIR / "saves" / name, UI_PACK, max(scenario.HOLDER_FUNCTIONS, key=len))
                  for name, _world_id in WORLDS.values()]
    return paths


def apply_verdict(check: Check, verdict: scenario.Verdict, failure_class: str = "unclassified") -> Check:
    status, reason, assertions = verdict
    for name, ok, expected, observed in assertions:
        check.check(name, ok, expected, observed)
    if status == "blocked":
        return check.finish("blocked", reason, "timeout_or_no_observation")
    if status == "fail":
        return check.finish("fail", reason, failure_class)
    return check.finish(None, reason, failure_class if any(not a["ok"] for a in check.assertions) else None)


class ClientRow:
    def __init__(self, *, manifest: Manifest, selection: Selection, out_dir: Path, cache_dir: Path,
                 limits: ClientLimits, java: str, javac: str | None, control: bool, negative: bool, run_id: str,
                 command_line: list[str], limit_overrides: dict[str, Any], git: dict[str, Any],
                 assets_from: Path | None = None, sprint: bool = True):
        self.manifest = manifest
        self.selection = selection
        self.target = selection.target
        self.profile = selection.profile
        self.out_dir = out_dir
        self.cache_dir = cache_dir
        self.limits = limits
        self.java = java
        self.javac = javac
        self.control = control
        self.negative = negative
        self.run_id = run_id
        self.command_line = command_line
        self.limit_overrides = limit_overrides
        self.git = git
        self.assets_from = assets_from
        self.sprint_requested = sprint
        self.game_dir = out_dir / GAME_DIR
        self.evidence_dir = out_dir / "evidence"
        self.http = HttpLimits(limits.http_connect_s, limits.http_read_s, limits.http_retries)
        self.registry = ProcessRegistry()
        self.jar_specs = jar_set(manifest, selection, control or negative)
        self.jar_records: list[dict[str, Any]] = []
        core, extended = client_check_names(selection, control, negative)
        self.checks = CheckSet(core + extended)
        self.extended_names = extended
        self.run_errors: list[dict[str, Any]] = []
        self.environment: dict[str, Any] = {}
        self.sessions: list[dict[str, Any]] = []
        self.observer: dict[str, Any] | None = None
        self.files: ClientFiles | None = None
        self.started_utc = utc_now()
        self.deadline = time.monotonic() + limits.row_s
        self.deadline_hit = threading.Event()
        self._finished = threading.Event()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(exist_ok=True)
        self._log_lock = threading.Lock()
        self._runner_log = (out_dir / "runner.log").open("a", encoding="utf-8", newline="\n")

    # Reporting -------------------------------------------------------------------------------
    def say(self, message: str) -> None:
        line = f"{utc_now()} {message}"
        print(line, flush=True)
        with self._log_lock:
            if not self._runner_log.closed:
                self._runner_log.write(line + "\n")
                self._runner_log.flush()

    def run_error(self, stage: str, message: str, failure_class: str = "harness_or_protocol") -> None:
        self.say(f"run error [{stage}]: {message}")
        self.run_errors.append({"stage": stage, "message": message, "failure_class": failure_class, "t": utc_now()})

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def start_watchdog(self) -> None:
        def watch() -> None:
            if self._finished.wait(self.remaining()):
                return
            self.deadline_hit.set()
            self.say(f"watchdog: row time limit of {self.limits.row_s:g}s exceeded; stopping owned processes")
            self.registry.kill_all()

        threading.Thread(target=watch, name="client-row-deadline", daemon=True).start()

    # Preparation -----------------------------------------------------------------------------
    @property
    def gamerules(self) -> dict[str, str]:
        return self.manifest.adapter(self.target["adapter"])["gamerules"]

    def acquire(self) -> dict[str, Any]:
        acquirer = ClientAcquirer(self.manifest, self.selection, self.cache_dir, self.http, self.deadline, self.say,
                                  self.assets_from)
        self.files = acquirer.acquire()
        self.environment["client"] = self.files.record
        server_jar, server_sha1 = acquirer.server_jar(self.files.version)
        world, world_record = template(self.cache_dir, self.selection.minecraft, server_jar, server_sha1,
                                       self.registry, self.java, self.deadline, self.say)
        self.environment["world_template"] = world_record
        sources = {spec.artifact_id: acquire_jar(spec, self.manifest, self.cache_dir, self.http, self.deadline)
                   for spec in self.jar_specs}
        return {"world": world, "sources": sources, "acquirer": acquirer}

    def prepare_game_dir(self, sources: dict[str, Any], config: dict[str, Any]) -> None:
        for folder in ("config", "saves"):
            (self.game_dir / folder).mkdir(parents=True, exist_ok=True)
        self.jar_records = install_jars(self.jar_specs, sources, self.game_dir / "mods")
        (self.game_dir / "options.txt").write_text(
            f"version:{self.files.pack['world_version']}\n" + "".join(f"{k}:{v}\n" for k, v in OPTIONS.items()),
            encoding="utf-8")
        (self.game_dir / "config" / CONFIG_NAME).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self.environment["options"] = OPTIONS
        self.environment["config_before"] = config

    def jar_assertions(self, check: Check) -> bool:
        for name, ok, want, got in jar_set_assertions(self.game_dir / "mods", self.jar_specs, self.jar_records, "fabric"):
            check.check(name, ok, want, got)
        return all(assertion["ok"] for assertion in check.assertions)

    def launch(self, name: str, quick_play: str | None, extra_jvm: list[str] | None = None,
               game_dir: Path | None = None) -> ClientSession:
        game_dir = game_dir or self.game_dir
        natives = extract_natives(self.files, self.out_dir / "natives")
        arguments = launch_arguments(self.files, game_dir, self.out_dir / "natives", quick_play, extra_jvm)
        argfile = self.out_dir / f"launch-{name}.args"
        redacted = write_argfile(arguments, argfile)
        self.environment.setdefault("launches", {})[name] = {"arguments": redacted, "natives": natives,
                                                              "game_dir": str(game_dir), "quick_play": quick_play,
                                                              "window": list(WINDOW_SIZE), "gui_scale": GUI_SCALE}
        session = ClientSession(self.registry, name, [self.java, f"@{argfile}"], game_dir, self.evidence_dir / name,
                                self.say)
        session.start()
        return session

    def finish_session(self, session: ClientSession, crashed: bool, game_dir: Path | None = None,
                       crash_exit_s: float | None = None) -> list[str]:
        session.close(self.limits.settle_s, crashed, self.limits.close_s,
                      self.limits.crash_exit_s if crash_exit_s is None else crash_exit_s)
        session.collect({"config-after.json": (game_dir or self.game_dir) / "config" / CONFIG_NAME})
        self.sessions.append(session.record.to_json())
        self.say(f"client session {session.name}: {session.record.end_reason}; closed by {session.record.closed_by}, "
                 f"exit code {session.record.exit_code}")
        return session.lines()

    # Checks ----------------------------------------------------------------------------------
    def project(self) -> dict[str, str] | None:
        if self.control or self.negative:
            return None
        return self.selection.artifact["descriptor"]

    def classify_failure(self, lines: list[str]) -> tuple[str, str, dict[str, Any] | None]:
        jars = loader_log.installed_jars(self.jar_records, self.game_dir / "mods")
        attribution = loader_log.attribute(lines, jars)
        failure_class, note = loader_log.failure_class(attribution, self.control or self.negative)
        return failure_class, note, attribution.to_json() if attribution else None

    def evaluate_core(self, lines: list[str], session: ClientSession) -> None:
        found = scenario.facts(lines, PLAYER)
        self.environment["log_facts"] = {"banner": found.banner, "mods": found.mods, "integrated": found.integrated,
                                         "joined": found.joined, "reports": [r.__dict__ for r in found.reports][:40]}
        minecraft, loader = self.selection.minecraft, self.target["loader"]
        startup = self.checks["client_startup"]
        failure_class, note, attribution = self.classify_failure(lines)
        if attribution:
            startup.observe(note, attribution=attribution)
        apply_verdict(startup, scenario.startup_verdict(found, minecraft, loader, self.project(),
                                                        session.record.exit_code, session.record.closed_by),
                      failure_class)
        if startup.status == "fail":
            self.checks.block_unfinished(f"client startup failed: {startup.reason}", failure_class)
            return
        apply_verdict(self.checks["integrated_world_load"].start(), scenario.world_load_verdict(found, minecraft))
        if self.negative:
            apply_verdict(self.checks["griefing_observed"].start(), scenario.griefing_verdict(found),
                          "harness_or_protocol")
            return
        if self.control:
            return
        apply_verdict(self.checks["pickup"].start(), scenario.pickup_verdict(found), "artifact")
        apply_verdict(self.checks["placement"].start(), scenario.placement_verdict(found), "artifact")
        if "mod_menu_load" in self.checks.checks:
            pinned = self.manifest.artifact(self.target["dependencies"]["modmenu"])["descriptor"]["version"]
            apply_verdict(self.checks["mod_menu_load"].start(),
                          scenario.mod_menu_verdict(found, pinned, startup.status), "artifact")
        self.evaluate_client_errors(found)

    def evaluate_client_errors(self, found: scenario.ClientFacts) -> None:
        check = self.checks["client_errors"].start()
        known, unknown = classify(found.lines, found.warn_error)
        check.observe("reviewed WARN/ERROR lines", reviewed=[{k: r[k] for k in ("text", "reason", "shown_by")}
                                                              for r in known][:40])
        check.check("no crash or Mixin error", not found.crash and not found.mixin_errors, [],
                    (found.crash + found.mixin_errors)[:3])
        if found.crash or found.mixin_errors:
            check.finish("fail", "the client crashed or a Mixin failed", "artifact")
        elif unknown:
            check.observe("unreviewed WARN/ERROR lines", lines=[r["text"] for r in unknown][:40])
            check.finish("unknown", f"{len(unknown)} WARN/ERROR line(s) are not in the reviewed allowlist; review "
                         "them against a control before this check can pass")
        else:
            check.check("every WARN/ERROR line is reviewed", True, "allowlisted", f"{len(known)} line(s)")
            check.finish()

    def evaluate_refusal(self, lines: list[str], check: Check) -> None:
        expected = self.profile.refusal_ids or ()
        depends = read_descriptor(self.game_dir / "mods" / self.selection.artifact["filename"], "fabric").get("depends") or {}
        for mod_id in expected:
            check.check(f"project descriptor declares {mod_id} in depends", mod_id in depends, f"depends.{mod_id}",
                        depends.get(mod_id))
        facts = loader_log.refusal_facts(lines, expected)
        found = scenario.facts(lines, PLAYER)
        check.observe("Loader refusal facts", **facts)
        check.check("the game never started (no integrated server)", found.integrated is None, None, found.integrated)
        check.check("Loader logged Incompatible mods found!", facts["incompatible_mods_found"], True,
                    facts["incompatible_mods_found"])
        for mod_id, line in facts["named"].items():
            check.check(f"refusal names {mod_id}", line is not None, f"requires ... of {mod_id}, which is missing!", line)
        reports = sorted(p.name for p in (self.game_dir / "crash-reports").glob("*")) \
            if (self.game_dir / "crash-reports").is_dir() else []
        check.check("no Mixin error", not facts["mixin_errors"], [], facts["mixin_errors"])
        check.check("no crash report or crash marker", not reports and not facts["crash_markers"], [],
                    reports + facts["crash_markers"])
        failed = [assertion["name"] for assertion in check.assertions if not assertion["ok"]]
        if failed:
            check.finish("fail", f"not a clean refusal; these did not hold: {', '.join(failed)}", "artifact")
        elif self.sessions and self.sessions[-1]["end_reason"] == "deadline":
            check.finish("blocked", "no refusal within the session limit", "timeout_or_no_observation")
        else:
            check.finish()

    # Orchestration ---------------------------------------------------------------------------
    def run_core(self, prepared: dict[str, Any]) -> None:
        refusal = self.profile.refusal_ids is not None and not (self.control or self.negative)
        first = self.checks["dependency_refusal" if refusal else "client_startup"].start()
        self.prepare_game_dir(prepared["sources"], CORE_CONFIG)
        if not self.jar_assertions(first):
            first.finish("fail", "installed jars do not match the profile", "artifact")
            self.checks.block_unfinished("installed jars do not match the profile", "artifact")
            return
        world = install_world(prepared["world"], self.game_dir / "saves", scenario.CORE_WORLD)
        # Refusal and --control sessions end at or before setup; only the core scenario has waits to sprint.
        sprint = self.sprint_requested and not refusal and not self.control
        self.environment["sprint"] = {"enabled": sprint}
        if sprint:
            self.environment["sprint"].update(allow_commands(world))
        self.environment["datapack"] = write_pack(world, scenario.CORE_PACK, scenario.core_functions(self.gamerules),
                                                  self.files.pack["pack_version"], "EGC client check")
        session = self.launch("core", scenario.CORE_WORLD)
        end, timeout = self.core_end_condition(refusal)
        session_deadline = min(self.deadline, time.monotonic() + timeout)
        if sprint:
            self.start_sprint(session, session_deadline)
        reason = session.wait_for_end(end, timeout, session_deadline)
        crashed = reason in ("crash or resolution failure", "crash", "Loader refusal")
        # Loader's error screen never exits on its own; a crash gets time to finish its report.
        lines = self.finish_session(session, crashed, crash_exit_s=5 if reason == "Loader refusal" else None)
        if sprint:
            report = next((line for line in lines if scenario.SPRINT_REPORT.search(line)), None)
            self.environment["sprint"]["report"] = report[:200] if report else "none (the session ended first)"
        if self.deadline_hit.is_set():
            raise ClientBlocked(f"row time limit of {self.limits.row_s:g}s exceeded", "timeout_or_no_observation")
        if refusal:
            self.evaluate_refusal(lines, first)
        else:
            self.evaluate_core(lines, session)

    def core_end_condition(self, refusal: bool) -> tuple[Any, float]:
        """The core session's end condition and time limit for this mode."""
        if refusal:
            return scenario.refusal_end, self.limits.refusal_s
        if self.control:
            return scenario.setup_end, self.limits.control_s
        return scenario.core_end, self.limits.core_s

    def start_sprint(self, session: ClientSession, deadline: float) -> None:
        """Type ``/tick sprint`` once the data pack has set up its world, and confirm it from the log.

        Every wait in the core scenario counts ticks, so a sprint changes only how long the session
        takes; one that never starts costs time, never a result. Nothing is resent: a lost key would
        leave the session at normal speed, and a retry could only add input.
        """
        record = self.environment["sprint"]
        ready = session.wait_until(scenario.setup_end, self.limits.core_s, deadline)
        if ready != "setup reported" or not session.running():
            record["outcome"] = f"not typed: {ready}"
            self.say(f"sprint {record['outcome']}")
            return
        time.sleep(self.limits.settle_s)  # the loading screen closes after the setup report
        from .client import win32
        since = len(session.lines())
        command = f"/tick sprint {self.limits.sprint_ticks}"
        hwnd = win32.game_window(session.process.pid)
        try:
            if hwnd is None:
                raise OSError("the game window was not found")
            post_chat_command(hwnd, command)
        except OSError as error:
            record["outcome"] = f"not typed: {error}"
            self.say(f"sprint {record['outcome']}")
            return
        record.update(command=command, typed_utc=utc_now())
        found = session.wait_until(
            lambda lines: next((line for line in lines[since:] if scenario.SPRINTING.search(line)), None),
            self.limits.sprint_confirm_s, deadline)
        if scenario.SPRINTING.search(found):
            record.update(outcome="confirmed", confirmed_line=found[:200])
        else:
            record["outcome"] = f"not confirmed: {found}"
        self.say(f"sprint {record['outcome']}")

    def run_extended(self, prepared: dict[str, Any]) -> None:
        startup = self.checks["client_startup"]
        if startup.status != "pass":
            self.checks.block_unfinished(f"extended checks need a passing client startup ({startup.status})",
                                         startup.failure_class or "timeout_or_no_observation")
            return
        from .client.extended import ExtendedRun
        ExtendedRun(self, prepared).run()

    def mode_label(self) -> str:
        if self.negative:
            return "negative control"
        return "control" if self.control else self.selection.artifact_id

    def run(self) -> dict[str, Any]:
        self.say(f"row {self.selection.key} profile {self.profile.name} artifact {self.mode_label()}")
        self.start_watchdog()
        try:
            from .client import win32
            if not win32.available():
                raise ClientBlocked("client rows drive the game through Win32 window messages and run only on a "
                                    "Windows host with an interactive desktop", "client_access")
            java = java_identity(self.java)
            self.environment["java"] = java
            if java.get("major") != self.target["java_major"]:
                raise ClientBlocked(f"Java major {java.get('major')} does not match pinned {self.target['java_major']}",
                                    "environment_or_download")
            try:
                prepared = self.acquire()
            except AcquisitionError as error:
                self.run_error("acquire", str(error), "environment_or_download")
                raise ClientBlocked(f"acquisition failed: {error}", "environment_or_download") from error
            self.run_core(prepared)
            if self.extended_names:
                self.run_extended(prepared)
        except ClientBlocked as blocked:
            self.checks.block_unfinished(blocked.reason, blocked.failure_class)
        except KeyboardInterrupt:
            self.run_error("run", "interrupted by the operator")
            self.checks.block_unfinished("run interrupted", "harness_or_protocol")
        except Exception as error:  # noqa: BLE001 - every crash must still produce a result
            if self.deadline_hit.is_set():
                self.checks.block_unfinished("row time limit exceeded", "timeout_or_no_observation")
            else:
                self.run_error("run", f"runner exception: {type(error).__name__}: {error}")
                self.checks.block_unfinished(f"runner exception: {error}", "harness_or_protocol")
        return self.finish()

    def evidence_files(self) -> list[dict[str, Any]]:
        files = [path for path in sorted(self.out_dir.iterdir())
                 if path.is_file() and path.name not in ("result.json", "summary.md")]
        files += sorted(path for path in self.evidence_dir.rglob("*") if path.is_file())
        return [{"path": str(path.relative_to(self.out_dir)).replace("\\", "/"), "sha256": hash_file(path),
                 "bytes": path.stat().st_size} for path in files]

    def finish(self) -> dict[str, Any]:
        killed, survivors = self.registry.kill_all()
        if killed and not self.deadline_hit.is_set():
            self.run_error("cleanup", f"killed leftover owned processes: {', '.join(killed)}")
        if survivors:
            self.run_error("cleanup", f"owned processes could not be confirmed stopped: {', '.join(survivors)}")
        self._finished.set()
        self.environment.update({
            "os": f"{host_platform.system()} {host_platform.release()} ({host_platform.version()})",
            "python": host_platform.python_version(),
            "sessions": self.sessions,
            "deadline_hit": self.deadline_hit.is_set(),
        })
        self.say("closing evidence")
        with self._log_lock:
            self._runner_log.close()
        evidence = self.evidence_files()
        overall, failure_class = compute_overall(self.checks.checks, self.checks.required, self.run_errors)
        failed = [check for check in self.checks.checks.values() if check.status == "fail"]
        if failed and overall != "fail":
            overall, failure_class = "fail", failed[0].failure_class
        print(f"{utc_now()} overall: {overall}" + (f" ({failure_class})" if failure_class else ""), flush=True)
        project = None
        if not (self.control or self.negative):
            installed = next((r for r in self.jar_records if r["role"] == "project"), {})
            project = {"artifact_id": self.selection.artifact_id, "kind": self.selection.artifact["kind"],
                       "filename": self.selection.artifact["filename"],
                       "expected_sha256": self.selection.artifact["sha256"],
                       "installed_sha256": installed.get("installed_sha256"), "source": installed.get("source")}
        core = [name for name in self.checks.required if name not in self.extended_names]
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "started_utc": self.started_utc,
            "ended_utc": utc_now(),
            "runner": {"version": RUNNER_VERSION, "command": self.command_line, **self.git},
            "manifest": {"path": str(self.manifest.path), "sha256": self.manifest.sha256},
            "selection": {"target_key": self.selection.key, "platform": "fabric", "side": "client",
                          "minecraft": self.selection.minecraft,
                          "artifact_id": None if (self.control or self.negative) else self.selection.artifact_id,
                          "artifact_overridden": self.selection.artifact_overridden, "control": self.control,
                          "negative_control": self.negative, "profile": self.profile.name,
                          "fixture": self.selection.fixture},
            "evidence_source": "automated",
            "profile": profile_record(self.selection, self.jar_records, self.control or self.negative),
            "target_pins": self.target,
            "artifact": project,
            "environment": self.environment,
            "limits": {"effective": self.limits.to_json(), "overrides": self.limit_overrides},
            "observer": self.observer,
            "checks": {name: self.checks[name].to_json() for name in core},
            "extended_checks": {name: self.checks[name].to_json() for name in self.extended_names},
            "required_checks": list(self.checks.required),
            "overall": overall,
            "failure_class": failure_class,
            "run_errors": self.run_errors,
            "evidence": {"out_dir": str(self.out_dir), "game_dir": str(self.game_dir), "files": evidence},
        }


# Manual evidence form (fallback) ---------------------------------------------------------------
FORM_VERSION = 2
MOD_MENU_CHECKS = ("mod_menu_load", "mod_menu_save_cancel")


def evidence_template(manifest: Manifest, selection: Selection) -> dict[str, Any]:
    target = selection.target
    mod_menu_id = (target.get("dependencies") or {}).get("modmenu")
    return {
        "form_version": FORM_VERSION,
        "target_key": selection.key,
        "install_profile": selection.profile.name,
        "artifact_id": selection.artifact_id,
        "artifact_sha256": selection.artifact["sha256"],
        "manifest_sha256": manifest.sha256,
        "observer": "",
        "observed_utc": "",
        "environment": {
            "minecraft": selection.minecraft,
            "loader": target.get("loader"),
            "profile_id": target.get("profile_id"),
            "java_major": target.get("java_major"),
            "java": "",
            "launcher": "",
            "jars": {mod_id: {"artifact_id": artifact_id, "sha256": manifest.artifact(artifact_id)["sha256"]}
                     for mod_id, artifact_id in selection.dependency_ids().items()},
            "mod_menu_artifact_id": mod_menu_id if "modmenu" in selection.profile.dependencies else None,
            "mod_menu_sha256": (manifest.artifact(mod_menu_id)["sha256"]
                                if mod_menu_id and "modmenu" in selection.profile.dependencies else None),
            "os": "",
        },
        "checks": {
            name: {"status": "unknown", "observation": "", "evidence": []}
            for name in CLIENT_CHECKS + CLIENT_EXTENDED_CHECKS
        },
        "extended_checks_required": target.get("extended_checks_required"),
        "instructions": (
            "Install exactly the listed artifact and the profile's listed jars (verify each SHA-256) in a fresh "
            "profile with the pinned Loader. Set status to pass/fail/blocked/unknown per check, describe what you "
            "saw, and list evidence files (logs, screenshots) relative to this form. A pass without an observation "
            "and an existing evidence file is recorded as blocked. Mod Menu checks need the modmenu profile."
        ),
    }


def form_problems(form: dict[str, Any], manifest: Manifest, selection: Selection) -> list[str]:
    target = selection.target
    environment = form.get("environment") or {}
    expected = {
        "form_version": (form.get("form_version"), FORM_VERSION),
        "target_key": (form.get("target_key"), selection.key),
        "install_profile": (form.get("install_profile"), selection.profile.name),
        "artifact_id": (form.get("artifact_id"), selection.artifact_id),
        "artifact_sha256": (form.get("artifact_sha256"), selection.artifact["sha256"]),
        "manifest_sha256": (form.get("manifest_sha256"), manifest.sha256),
        "environment.minecraft": (environment.get("minecraft"), selection.minecraft),
        "environment.loader": (environment.get("loader"), target.get("loader")),
        "environment.profile_id": (environment.get("profile_id"), target.get("profile_id")),
        "environment.java_major": (environment.get("java_major"), target.get("java_major")),
    }
    return [f"{field} is {got!r}, expected {want!r}" for field, (got, want) in expected.items() if got != want]


def mod_menu_problem(form: dict[str, Any], manifest: Manifest, selection: Selection) -> str | None:
    if "modmenu" not in selection.profile.dependencies:
        return f"the {selection.profile.name!r} profile installs no Mod Menu, so Mod Menu coverage cannot be claimed"
    mod_menu_id = selection.dependency_ids()["modmenu"]
    environment = form.get("environment") or {}
    pinned = manifest.artifact(mod_menu_id)["sha256"]
    if environment.get("mod_menu_artifact_id") != mod_menu_id or environment.get("mod_menu_sha256") != pinned:
        return f"form does not report the pinned Mod Menu jar {mod_menu_id} ({pinned})"
    return None


def record_check(check: Check, entry: dict[str, Any], form_dir: Path, blocked_reason: str | None) -> list[dict]:
    status = entry.get("status", "unknown")
    observation = (entry.get("observation") or "").strip()
    files = []
    for relative in entry.get("evidence") or []:
        path = (form_dir / relative).resolve()
        if path.is_file() and path.is_relative_to(form_dir.resolve()):
            files.append({"path": relative, "sha256": hash_file(path), "bytes": path.stat().st_size})
    check.evidence = files
    if observation:
        check.observe(observation)
    if status not in STATUSES:
        check.finish("blocked", f"form status {status!r} is not recognized", "client_access")
    elif status == "fail":
        # A reported failure always counts, even where the check would otherwise be blocked.
        check.finish("fail", observation or "reported as failed", "unclassified")
    elif blocked_reason is not None:
        check.finish("blocked", blocked_reason, "client_access")
    elif status == "pass":
        check.check("manual observation recorded", bool(observation), "observation", observation)
        check.check("evidence file present", bool(files), "at least one existing file inside the form directory",
                    [f["path"] for f in files])
        if observation and files:
            check.finish()
        else:
            check.finish("blocked", "pass without observation/evidence", "client_access")
    elif status == "blocked":
        check.finish("blocked", observation or "reported as blocked", "client_access")
    else:
        check.finish("unknown", observation or "not recorded")
    return files


def run_client_form(*, manifest: Manifest, selection: Selection, out_dir: Path, form_path: Path, run_id: str,
                    command_line: list[str], git: dict[str, Any]) -> dict[str, Any]:
    """A client row from a manual evidence form: the marked fallback when automation cannot run."""
    started = utc_now()
    names = CLIENT_CHECKS + CLIENT_EXTENDED_CHECKS
    checks = {name: Check(name) for name in names}
    extended_required = bool(selection.target.get("extended_checks_required")) \
        and "modmenu" in selection.profile.dependencies
    required = names if extended_required else CLIENT_CHECKS
    run_errors: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    form = json.loads(form_path.read_text(encoding="utf-8"))
    problems = form_problems(form, manifest, selection)
    if problems:
        run_errors += [{"stage": "client_evidence", "message": problem, "failure_class": "client_access"}
                       for problem in problems]
        for check in checks.values():
            check.finish("blocked", "manual evidence form rejected: " + "; ".join(problems), "client_access")
    else:
        mod_menu_blocked = mod_menu_problem(form, manifest, selection)
        for name, check in checks.items():
            entry = (form.get("checks") or {}).get(name) or {}
            blocked = mod_menu_blocked if name in MOD_MENU_CHECKS else None
            evidence += record_check(check.start(), entry, form_path.parent, blocked)
    overall, failure_class = compute_overall(checks, required, run_errors)
    failed = [check for check in checks.values() if check.status == "fail"]
    if failed and overall != "fail":
        overall, failure_class = "fail", failed[0].failure_class
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "started_utc": started,
        "ended_utc": utc_now(),
        "runner": {"version": RUNNER_VERSION, "command": command_line, **git},
        "manifest": {"path": str(manifest.path), "sha256": manifest.sha256},
        "selection": {"target_key": selection.key, "platform": selection.platform, "side": selection.side,
                      "minecraft": selection.minecraft, "artifact_id": selection.artifact_id,
                      "artifact_overridden": selection.artifact_overridden, "control": False,
                      "profile": selection.profile.name, "fixture": selection.fixture},
        "evidence_source": "manual",
        "profile": {**selection.profile.to_json(), "control": False,
                    "jars": [{"role": "dependency", "mod_id": mod_id, "artifact_id": artifact_id,
                              "expected_sha256": manifest.artifact(artifact_id)["sha256"],
                              "reported": (form.get("environment") or {}).get("jars", {}).get(mod_id)}
                             for mod_id, artifact_id in selection.dependency_ids().items()]},
        "target_pins": selection.target,
        "artifact": {"artifact_id": selection.artifact_id, "filename": selection.artifact["filename"],
                     "expected_sha256": selection.artifact["sha256"],
                     "reported_sha256": form.get("artifact_sha256"),
                     "note": "manual evidence: the reported hash is the observer's claim, not computed by the runner"},
        "environment": form.get("environment"),
        "client_evidence_form": str(form_path),
        "checks": {name: checks[name].to_json() for name in CLIENT_CHECKS},
        "extended_checks": {name: checks[name].to_json() for name in CLIENT_EXTENDED_CHECKS},
        "required_checks": list(required),
        "overall": overall,
        "failure_class": failure_class,
        "run_errors": run_errors,
        "evidence": {"out_dir": str(out_dir), "files": evidence},
    }
