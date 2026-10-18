"""Run one dedicated-server row end to end.

Stages: acquire pinned downloads, install the exact artifact into a fresh localhost-only
workspace, start the server, connect RCON and two protocol clients, build a controlled arena,
run the behavior checks, restart once for persistence, then stop and record evidence. A result is
written even when a stage crashes, times out, or is interrupted.
"""

from __future__ import annotations

import json
import platform as host_platform
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable

from . import RUNNER_VERSION
from .acquire import (AcquisitionError, HttpLimits, contained, copy_local_pinned, fetch_pinned, fs_path,
                      hash_file)
from .bot import BotClient, client_preflight
from .install_check import verify_install
from .jarinfo import check_artifact, fabric_mod_tree
from .manifest import Manifest, Selection
from .platforms import (CHAT_ALERT, CHAT_CLEARED, CHAT_DENIED, DONE_RE, Command, Platform,
                        platform_for)
from .procs import Line, ManagedProcess, ProcessRegistry, allocate_port
from .ratelimit import Announcement, evaluate
from .rcon import RconClient, RconError
from .results import CONTROL_CHECKS, SERVER_CHECKS, Check, CheckSet, compute_overall, utc_now

# Controlled arena, all inside force-loaded chunks (0,0) and (1,0) of a flat world, far above
# the natural surface. A glass shell keeps the Enderman inside, blocks sky light, and leaves
# grass as the only holdable block it can reach. An identical copy built REFERENCE_DY higher
# lets one `execute if blocks` query prove that nothing in the arena changed.
ARENA_MIN = (4, 99, 4)
ARENA_MAX = (12, 105, 12)
REFERENCE_DY = 12
ARENA_SPAWN = "8.5 100 8.5"
HOLDER_BLOCK = (8, 100, 8)
GRASS = ((6, 100, 6), (8, 100, 6), (10, 100, 6), (6, 100, 8),
         (10, 100, 8), (6, 100, 10), (8, 100, 10), (10, 100, 10))
SHEEP_MIN = (18, 98, 6)
SHEEP_MAX = (22, 102, 10)
SHEEP_GRASS = tuple((x, 99, z) for x in (19, 20, 21) for z in (7, 8, 9))
SHEEP_SPAWN = "20.5 100 8.5"
CLIENT_SPOT = "14.5 102 8.5"
# Creative, not spectator: Paper's entity activation range does not keep mobs near a spectator
# fully active (a sheep next to one never ate grass), while Endermen cannot target a creative
# player and sheep are not tempted by an empty-handed one.
CLIENT_GAMEMODE = "creative"
FORCELOAD = "forceload add 0 0 31 15"
OP_NAME = "CompatOp"
GUEST_NAME = "CompatGuest"
WORLD_TIME = 18000  # midnight: Endermen do not teleport away from the arena at night
# A denial meant to be held back by the cooldown must be logged at least this long before the
# cooldown ends, counted from when the clients received the first announcement.
COOLDOWN_MARGIN_S = 2.0

# "Test passed, count: N" before 1.21.11; "Test passed. Count: N" from 1.21.11. Anything else blocks.
TEST_PASSED = re.compile(r"^Test passed(?:(?:, count|\. Count): (\d+))?\.?$")
GAMETIME = re.compile(r"The time is (\d+)")
GAMERULE_VALUE = re.compile(r"(true|false|-?\d+)\s*\.?$")
MIXIN_ERROR = re.compile(r"(MixinApplyError|InvalidInjectionException|InvalidMixinException|"
                         r"Critical injection failure|Mixin apply for mod \S+ failed|"
                         r"Mixin transformation of \S+ failed|MixinTransformerError|"
                         r"@Shadow \S+ .{0,200}?was not located)")
FABRIC_RESOLUTION_ERROR = re.compile(r"(Incompatible mods found|Mod resolution failed|"
                                     r"requires .* which is missing|Some of your mods are incompatible)")
RCON_READY = re.compile(r"RCON running on")
RCON_PASSWORD_LINE = re.compile(r"^rcon\.password=[^\r\n]*", re.M)


class StageBlocked(Exception):
    def __init__(self, reason: str, failure_class: str = "harness_or_protocol"):
        super().__init__(reason)
        self.reason = reason
        self.failure_class = failure_class


@dataclass
class Limits:
    http_connect_s: float = 15
    http_read_s: float = 60
    http_retries: int = 2
    server_start_s: float = 180
    client_spawn_s: float = 60
    rcon_s: float = 10
    chat_s: float = 10
    trial_s: float = 30
    trial_ticks: int = 20000
    trials: int = 3
    carrier_ticks: int = 3000
    other_mob_ticks: int = 6000
    cooldown_s: float = 10
    settle_s: float = 1.0
    row_s: float = 1200
    stop_s: float = 30

    @classmethod
    def from_overrides(cls, overrides: list[str]) -> tuple["Limits", dict[str, Any]]:
        limits = cls()
        applied: dict[str, Any] = {}
        known = {field.name: field.type for field in fields(cls)}
        for override in overrides:
            key, separator, raw = override.partition("=")
            if not separator or key not in known:
                raise ValueError(f"unknown limit override {override!r}; known: {', '.join(known)}")
            value: Any = int(raw) if known[key] in (int, "int") else float(raw)
            if value <= 0:
                raise ValueError(f"limit {key} must be positive")
            setattr(limits, key, value)
            applied[key] = value
        return limits, applied

    def to_json(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def in_box(coords: tuple[int, int, int], low: tuple[int, int, int], high: tuple[int, int, int]) -> bool:
    return all(lo <= value <= hi for value, lo, hi in zip(coords, low, high))


def box(low: tuple[int, int, int], high: tuple[int, int, int]) -> str:
    return " ".join(str(value) for value in (*low, *high))


def redact_rcon_password(properties: Path) -> bool:
    """Redact by key, for a workspace whose own run was killed before it could; True if changed."""
    if not properties.exists():
        return False
    text = properties.read_bytes().decode("utf-8")  # bytes keep the file's own line endings
    redacted = RCON_PASSWORD_LINE.sub("rcon.password=REDACTED", text)
    if redacted != text:
        properties.write_bytes(redacted.encode("utf-8"))
    return redacted != text


def pos(coords: tuple[int, int, int]) -> str:
    return " ".join(str(value) for value in coords)


def java_identity(java: str) -> dict[str, Any]:
    try:
        completed = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": java, "error": str(error), "major": None}
    text = (completed.stderr or completed.stdout).strip()
    match = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    major = None
    if match:
        major = int(match.group(1))
        if major == 1 and match.group(2):
            major = int(match.group(2))
    return {"command": java, "version_output": text.splitlines(), "major": major}


def tool_version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        return (completed.stdout or completed.stderr).strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


class ServerRow:
    def __init__(self, *, manifest: Manifest, selection: Selection, out_dir: Path, cache_dir: Path,
                 limits: Limits, java: str, node: str, xmx: str, control: bool, run_id: str,
                 command_line: list[str], limit_overrides: dict[str, Any], git: dict[str, Any]):
        self.manifest = manifest
        self.selection = selection
        self.target = selection.target
        self.out_dir = out_dir
        self.server_dir = out_dir / "server"
        self.evidence_dir = out_dir / "evidence"
        self.cache_dir = cache_dir
        self.limits = limits
        self.java = java
        self.node = node
        self.xmx = xmx
        self.control = control
        self.run_id = run_id
        self.command_line = command_line
        self.limit_overrides = limit_overrides
        self.git = git
        self.platform: Platform = platform_for(selection.platform, selection.artifact)
        self.adapter = manifest.adapter(self.target["adapter"])
        self.http = HttpLimits(limits.http_connect_s, limits.http_read_s, limits.http_retries)
        self.registry = ProcessRegistry()
        self.checks = CheckSet(CONTROL_CHECKS if control else SERVER_CHECKS)
        self.setup = Check("world_setup")
        self.run_errors: list[dict[str, Any]] = []
        self.environment: dict[str, Any] = {}
        self.artifact_record: dict[str, Any] = {}
        self.server_runs: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {}
        self.server: ManagedProcess | None = None
        self.rcon: RconClient | None = None
        self.bots: dict[str, BotClient] = {}
        self.ports: dict[str, int] = {}
        self.rcon_password = secrets.token_urlsafe(24)
        self.started_utc = utc_now()
        self.deadline = time.monotonic() + limits.row_s
        self.deadline_hit = threading.Event()
        self._finished = threading.Event()
        self.restart_attempted = False
        self.install_seeded = False
        self.install_record: dict[str, Any] = {}
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(exist_ok=True)
        self._log_lock = threading.Lock()
        self._runner_log = (out_dir / "runner.log").open("a", encoding="utf-8", newline="\n")
        self._rcon_log = (out_dir / "rcon-transcript.jsonl").open("a", encoding="utf-8", newline="\n")

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

    def deadline_reason(self) -> str:
        return f"row time limit of {self.limits.row_s:g}s exceeded"

    def ensure_time(self) -> None:
        if self.deadline_hit.is_set() or time.monotonic() > self.deadline:
            raise StageBlocked(self.deadline_reason(), "timeout_or_no_observation")

    def start_watchdog(self) -> None:
        """Enforce the whole-row limit even inside a blocking call.

        At the deadline the watchdog closes RCON and kills every owned process, which unblocks any
        wait in the main thread; the main thread then reports the row as timed out and finishes
        cleanup and the result as usual.
        """
        def watch() -> None:
            if self._finished.wait(max(0.0, self.deadline - time.monotonic())):
                return
            self.deadline_hit.set()
            self.say(f"watchdog: {self.deadline_reason()}; stopping owned processes")
            rcon = self.rcon
            if rcon is not None:
                rcon.close()
            self.registry.kill_all()

        threading.Thread(target=watch, name="row-deadline", daemon=True).start()

    # Server I/O ----------------------------------------------------------------------------
    def require_server(self) -> ManagedProcess:
        self.ensure_time()
        if self.server is None or not self.server.running():
            code = None if self.server is None else self.server.exit_code
            raise StageBlocked(f"server is not running (exit code {code})", "unclassified")
        return self.server

    def cmd(self, text: str) -> str:
        if self.rcon is None:
            raise StageBlocked("RCON is not connected")
        self.require_server()
        started = time.monotonic()
        try:
            response = self.rcon.command(text)
            error = None
        except RconError as failure:
            response, error = None, str(failure)
        self._rcon_log.write(json.dumps({"t": utc_now(), "command": text, "response": response, "error": error,
                                         "elapsed_s": round(time.monotonic() - started, 3)}) + "\n")
        self._rcon_log.flush()
        if error is not None:
            self.run_error("rcon", f"{text!r}: {error}")
            raise StageBlocked(f"RCON command {text!r} failed: {error}")
        return response.strip()

    def run_command(self, command: Command, check: Check | None = None, label: str | None = None) -> bool:
        response = self.cmd(command.text)
        ok = command.matches(response)
        if check is not None:
            check.check(label or f"RCON `{command.text}`", ok, command.expected, response, command=command.text)
        elif not ok:
            raise StageBlocked(f"RCON {command.text!r} answered {response!r}, expected {command.expected!r}")
        return ok

    def query(self, text: str) -> int:
        """Run an ``execute if`` query: matching count, 0 for "Test failed"; anything else blocks."""
        response = self.cmd(text)
        match = TEST_PASSED.match(response)
        if match:
            return int(match.group(1)) if match.group(1) else 1
        if response == "Test failed":
            return 0
        raise StageBlocked(f"query {text!r} returned unexpected {response!r}")

    def gametime(self) -> int:
        response = self.cmd("time query gametime")
        match = GAMETIME.search(response)
        if not match:
            raise StageBlocked(f"cannot read game time from {response!r}")
        return int(match.group(1))

    def log_offset(self) -> int:
        return self.require_server().output.offset()

    def denials(self, kind: str, since: int, arena_only: bool) -> list[tuple[Line, tuple[int, int, int]]]:
        found = []
        for line, match in self.require_server().output.find(self.platform.denial_log, since):
            coords = (int(match.group(2)), int(match.group(3)), int(match.group(4)))
            if match.group(1) == kind and (not arena_only or in_box(coords, ARENA_MIN, ARENA_MAX)):
                found.append((line, coords))
        return found

    def advance(self, ticks: int, seconds: float, stop_when: Callable[[], bool] | None = None,
                freeze_tag: str | None = None) -> dict[str, Any]:
        """Sprint normal server ticks until ``ticks`` pass, ``seconds`` elapse, or ``stop_when``."""
        start_tick = self.gametime()
        target = start_tick + ticks
        started = time.monotonic()
        self.cmd(f"tick sprint {ticks}")
        reason = "time limit"
        while True:
            if stop_when is not None and stop_when():
                reason = "observed"
                break
            if self.gametime() >= target:
                reason = "ticks reached"
                break
            if time.monotonic() - started > seconds:
                break
            time.sleep(0.2)
        if freeze_tag is not None:
            self.set_ai(freeze_tag, False)
        frozen_at = time.monotonic()
        self.cmd("tick sprint stop")
        return {"start_tick": start_tick, "end_tick": self.gametime(), "requested_ticks": ticks,
                "elapsed_s": round(time.monotonic() - started, 3), "stopped": reason, "frozen_mono": frozen_at}

    # Entities ------------------------------------------------------------------------------
    @staticmethod
    def selector(tag: str) -> str:
        return f"@e[type=minecraft:enderman,tag={tag},limit=1]"

    def count(self, selector: str) -> int:
        return self.query(f"execute if entity {selector}")

    def endermen(self) -> int:
        return self.count("@e[type=minecraft:enderman]")

    def summon_enderman(self, tag: str, carrying: str | None) -> None:
        nbt = [f'Tags:["{tag}"]', "PersistenceRequired:1b", "NoAI:1b"]
        if carrying:
            nbt.append(f'carriedBlockState:{{Name:"minecraft:{carrying}"}}')
        self.cmd(f"summon minecraft:enderman {ARENA_SPAWN} {{{','.join(nbt)}}}")
        if self.count(f"@e[type=minecraft:enderman,tag={tag}]") != 1:
            raise StageBlocked(f"summoned Enderman {tag} was not found")
        if self.endermen() != 1:
            raise StageBlocked("another Enderman is present in the world")
        if carrying is None and self.carrying(tag):
            raise StageBlocked(f"Enderman {tag} unexpectedly carries a block")
        if carrying is not None and not self.carrying(tag, carrying):
            raise StageBlocked(f"Enderman {tag} was not summoned carrying {carrying}")

    def carrying(self, tag: str, block: str | None = None) -> bool:
        path = "carriedBlockState" + (f'{{Name:"minecraft:{block}"}}' if block else "")
        return self.query(f"execute if data entity {self.selector(tag)} {path}") > 0

    def set_ai(self, tag: str, enabled: bool) -> None:
        self.cmd(f"data merge entity {self.selector(tag)} {{NoAI:{0 if enabled else 1}b}}")

    def clear_mobs(self) -> None:
        self.cmd("kill @e[type=minecraft:enderman]")
        self.cmd("kill @e[type=minecraft:sheep]")
        self.advance(60, 15)
        self.cmd("kill @e[type=minecraft:item]")
        if self.endermen() != 0 or self.count("@e[type=minecraft:item]") != 0:
            raise StageBlocked("could not clear test entities between checks")

    def arena_unchanged(self) -> bool:
        low, high = ARENA_MIN, ARENA_MAX
        reference = (low[0], low[1] + REFERENCE_DY, low[2])
        return self.query(f"execute if blocks {box(low, high)} {pos(reference)} all") > 0

    # Clients -------------------------------------------------------------------------------
    def chat_offsets(self) -> dict[str, int]:
        return {name: bot.offset() for name, bot in self.bots.items()}

    def require_clients(self) -> None:
        for name, bot in self.bots.items():
            if not bot.process.running() or bot.disconnected(self.state.get("bot_since", {}).get(name, 0)):
                raise StageBlocked(f"protocol client {name} is disconnected")
        if len(self.bots) != 2:
            raise StageBlocked("protocol clients are not connected")

    def wait_both(self, pattern: re.Pattern[str], since: dict[str, int], timeout_s: float | None = None) -> dict[str, Any]:
        received = {}
        for name, bot in self.bots.items():
            event = bot.wait_chat(pattern, since[name], timeout_s or self.limits.chat_s)
            received[name] = event
        return received

    def chats_matching(self, name: str, pattern: re.Pattern[str], since: int) -> list[Any]:
        return [event for event in self.bots[name].chats(since)
                if pattern.fullmatch(event.data.get("text", "").strip())]

    def connect_clients(self, run_index: int) -> None:
        protocol = self.target["client_protocol"]
        for name in (OP_NAME, GUEST_NAME):
            transcript = self.out_dir / f"bot-{name}-run{run_index}.jsonl"
            self.bots[name] = BotClient(self.registry, self.node, name, "127.0.0.1", self.ports["game"],
                                        protocol, transcript)
        self.state["bot_since"] = {name: 0 for name in self.bots}
        for name, bot in self.bots.items():
            event = bot.wait_spawn(self.limits.client_spawn_s)
            if event is None or event.kind != "spawn":
                last = bot.events()[-3:]
                raise StageBlocked(f"protocol client {name} did not spawn with protocol {protocol}: "
                                   f"{[(e.kind, e.data) for e in last]}")
        self.cmd(f"op {OP_NAME}")
        for name in self.bots:
            self.cmd(f"gamemode {CLIENT_GAMEMODE} {name}")
            self.cmd(f"tp {name} {CLIENT_SPOT}")
        self.environment.setdefault("clients", []).append({
            "run": run_index,
            "requested_protocol_version": protocol,
            "negotiated": {name: bot.protocol() for name, bot in self.bots.items()},
            "companion": {name: [event.data for event in bot.events("starting")][:1]
                          for name, bot in self.bots.items()},
            "errors": {name: [event.data.get("message", "")[:300] for event in bot.events("error")]
                       for name, bot in self.bots.items()},
        })

    def disconnect_clients(self) -> None:
        for bot in self.bots.values():
            bot.quit()
        self.bots = {}

    # Acquisition and workspace ---------------------------------------------------------------
    def install_cache_dir(self) -> Path:
        key = self.target.get("sha256") or self.target.get("launcher_sha256")
        return self.cache_dir / "installs" / f"{self.selection.platform}-{self.selection.minecraft}-{key[:16]}"

    def install_cache_entries(self) -> list[str]:
        if self.selection.platform == "paper":
            return ["cache", "libraries"]
        return [".fabric/server", "libraries"]

    def acquire(self) -> dict[str, Path]:
        target = self.target
        user_agent = self.manifest.user_agent
        paths: dict[str, Path] = {}
        if self.selection.platform == "paper":
            cached = fetch_pinned(target["download_url"],
                                  f"paper-{self.selection.server_minecraft}-{target['paper_build']}.jar",
                                  self.cache_dir, user_agent, self.http, sha256=target["sha256"], size=target["size"],
                                  deadline=self.deadline)
            paths["server"] = cached.path
            self.environment["paper"] = {"build": target["paper_build"], "channel": target["channel"],
                                         "build_api_url": target["build_api_url"], "download_url": target["download_url"],
                                         "sha256": cached.sha256, "size": cached.size, "from_cache": cached.from_cache}
        else:
            cached = fetch_pinned(target["launcher_url"], f"fabric-server-{self.selection.minecraft}-{target['loader']}-"
                                  f"{target['installer']}.jar", self.cache_dir, user_agent, self.http,
                                  sha256=target["launcher_sha256"], size=target["launcher_size"],
                                  deadline=self.deadline)
            paths["server"] = cached.path
            self.environment["fabric"] = {"loader": target["loader"], "installer": target["installer"],
                                          "launcher_url": target["launcher_url"], "launcher_sha256": cached.sha256,
                                          "launcher_size": cached.size, "from_cache": cached.from_cache}
        if not self.control:
            artifact = self.selection.artifact
            if "url" in artifact:
                jar = fetch_pinned(artifact["url"], artifact["filename"], self.cache_dir, user_agent, self.http,
                                   sha256=artifact["sha256"], size=artifact["size"], deadline=self.deadline)
            else:
                local = Path(artifact["local_path"])
                if not local.is_absolute():
                    local = self.manifest.path.parent / local
                jar = copy_local_pinned(local, artifact["filename"], self.cache_dir,
                                        sha256=artifact["sha256"], size=artifact["size"])
            paths["artifact"] = jar.path
            self.artifact_record.update({"source": jar.source, "cached_sha256": jar.sha256, "from_cache": jar.from_cache})
        return paths

    def write_properties(self) -> None:
        properties = {
            "server-ip": "127.0.0.1",
            "server-port": self.ports["game"],
            "enable-rcon": "true",
            "rcon.port": self.ports["rcon"],
            "rcon.password": self.rcon_password,
            "broadcast-rcon-to-ops": "false",
            "broadcast-console-to-ops": "false",
            "online-mode": "false",
            "enforce-secure-profile": "false",
            "level-type": "minecraft\\:flat",
            "level-seed": "egc-compat",
            "generate-structures": "false",
            "difficulty": "easy",
            "gamemode": CLIENT_GAMEMODE,
            "spawn-protection": "0",
            "view-distance": "4",
            "simulation-distance": "4",
            "max-players": "4",
            "pause-when-empty-seconds": "-1",
            "allow-flight": "true",
            "motd": "egc-compat",
            "enable-query": "false",
            "white-list": "false",
        }
        text = "#Generated by the EGC compatibility runner; throwaway credentials\n"
        text += "".join(f"{key}={value}\n" for key, value in properties.items())
        (self.server_dir / "server.properties").write_text(text, encoding="utf-8")

    def prepare_workspace(self, paths: dict[str, Path]) -> None:
        if self.server_dir.exists():
            raise StageBlocked(f"workspace {self.server_dir} already exists; use a fresh --out", "environment_or_download")
        self.server_dir.mkdir(parents=True)
        launcher = "paper.jar" if self.selection.platform == "paper" else "fabric-server-launch.jar"
        shutil.copy2(paths["server"], self.server_dir / launcher)
        (self.server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        self.ports = {"game": allocate_port(), "rcon": allocate_port()}
        while self.ports["rcon"] == self.ports["game"]:
            self.ports["rcon"] = allocate_port()
        self.write_properties()
        self.seed_install()
        if not self.control:
            artifact_dir = self.platform.artifact_dir(self.server_dir)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            try:
                installed = contained(artifact_dir, self.selection.artifact["filename"])
            except AcquisitionError as error:
                raise StageBlocked(str(error), "harness_or_protocol") from error
            shutil.copy2(paths["artifact"], installed)
            self.artifact_record["installed_path"] = str(installed)
            self.artifact_record["installed_sha256"] = hash_file(installed)

    def seed_install(self) -> None:
        """Reuse a cached install only if every file matches an official hash; otherwise drop it.

        A rejected seed is deleted from the cache and the workspace, and the official launcher
        or Paperclip installs from scratch. Runs before Java starts.
        """
        seed = fs_path(self.install_cache_dir())
        if not seed.is_dir():
            return
        workspace = fs_path(self.server_dir)
        for entry in self.install_cache_entries():
            if (seed / entry).is_dir():
                shutil.copytree(seed / entry, workspace / entry)
        problems = verify_install(self.server_dir, self.selection.platform, self.target,
                                  self.selection.server_minecraft)
        if not problems:
            self.install_seeded = True
            self.install_record["seed"] = "verified against official hashes and reused"
            return
        self.say(f"install cache rejected ({len(problems)} problem(s)); installing fresh: {problems[:3]}")
        self.install_record["seed"] = "rejected and deleted"
        self.install_record["seed_problems"] = problems[:50]
        for entry in self.install_cache_entries():
            shutil.rmtree(workspace / entry, ignore_errors=True)
        shutil.rmtree(seed, ignore_errors=True)

    def verify_artifact(self, check: Check) -> None:
        if self.control:
            check.check("no project artifact installed (vanilla control)", True, "none", "none")
            return
        installed = Path(self.artifact_record["installed_path"])
        expected = self.selection.artifact["sha256"]
        actual = hash_file(installed)
        self.artifact_record["installed_sha256"] = actual
        check.check("installed artifact SHA-256 matches lock", actual == expected, expected, actual)
        for name, ok, want, got in check_artifact(installed, self.selection.platform, self.selection.artifact):
            check.check(f"artifact {name}", ok, want, got)
        others = [path.name for path in self.platform.artifact_dir(self.server_dir).glob("*.jar")
                  if path.name != installed.name]
        check.check("only one top-level project artifact", not others, [], others)

    # Server lifecycle ------------------------------------------------------------------------
    def start_server(self, run_index: int, check: Check) -> bool:
        output = self.out_dir / f"server-run{run_index}.log"
        command = self.platform.server_command(self.java, self.xmx)
        self.say(f"starting server run {run_index}: {' '.join(command)}")
        self.server = self.registry.start(f"server-run{run_index}", command, self.server_dir, output)
        started = time.monotonic()
        done = self.server.output.wait_for(DONE_RE, 0, self.limits.server_start_s,
                                           abort=lambda: not self.server.running())
        record: dict[str, Any] = {"run": run_index, "command": command, "output": output.name,
                                  "pid": self.server.pid, "started_utc": utc_now()}
        self.server_runs.append(record)
        if done is None:
            # A crash may still be flushing output; give it a moment before judging.
            self.server.wait(5)
            record["exit_code"] = self.server.exit_code
            record["done"] = None
            return False
        line, match = done
        record["done"] = {"line": line.text, "seconds": match.group(1), "elapsed_s": round(time.monotonic() - started, 3)}
        banner = self.server.output.find(self.platform.banner_pattern(), 0, line.index + 1)
        if banner:
            record["banner"] = banner[0][0].text
            ok, expected = self.platform.server_identity(banner[0][1], self.target, self.selection.server_minecraft)
            check.check(f"run {run_index}: server banner matches the pinned server", ok, expected,
                        banner[0][0].text, log_offset=banner[0][0].index)
        else:
            check.check(f"run {run_index}: server banner matches the pinned server", False,
                        self.platform.banner_pattern().pattern, None)
        check.check(f"run {run_index}: server reached Done", True, "Done line", line.text, log_offset=line.index)
        if not self.control:
            for name, pattern in self.platform.startup_patterns():
                found = self.server.output.find(pattern, 0, line.index + 1)
                check.check(f"run {run_index}: {name}", bool(found), pattern.pattern,
                            found[0][0].text if found else None, log_offset=found[0][0].index if found else None)
        mixin_errors = self.server.output.find(MIXIN_ERROR, 0)
        check.check(f"run {run_index}: no Mixin errors", not mixin_errors, [],
                    [entry[0].text for entry in mixin_errors[:5]])
        return True

    def connect_rcon(self) -> None:
        server = self.require_server()
        server.output.wait_for(RCON_READY, 0, 30, abort=lambda: not server.running())
        last_error = None
        for _attempt in range(10):
            try:
                self.rcon = RconClient("127.0.0.1", self.ports["rcon"], self.rcon_password, self.limits.rcon_s)
                return
            except RconError as error:
                last_error = error
                time.sleep(1)
        raise StageBlocked(f"RCON did not accept a connection: {last_error}")

    def stop_server(self, run_index: int) -> int | None:
        server = self.server
        if server is None:
            return None
        record = next((run for run in self.server_runs if run["run"] == run_index), {"run": run_index})
        if server.running():
            if self.rcon is not None:
                try:
                    self.rcon.command("stop")
                except RconError:
                    pass
            if server.wait(self.limits.stop_s) is None:
                self.run_error("stop", f"server run {run_index} did not stop within {self.limits.stop_s:g}s; killed")
                if not server.kill():
                    self.run_error("stop", f"server run {run_index} is still running after kill: {server.kill_error}")
        if self.rcon is not None:
            self.rcon.close()
            self.rcon = None
        server.close_streams()
        record["exit_code"] = server.exit_code
        record["killed"] = server.killed
        record["ended_utc"] = utc_now()
        if server.exit_code not in (0, None) and not server.killed:
            self.run_error("stop", f"server run {run_index} exited with code {server.exit_code}", "unclassified")
        self.snapshot_logs(run_index)
        return server.exit_code

    def snapshot_logs(self, run_index: int) -> None:
        latest = self.server_dir / "logs" / "latest.log"
        if latest.exists():
            shutil.copy2(latest, self.evidence_dir / f"latest-run{run_index}.log")
        crashes = self.server_dir / "crash-reports"
        if crashes.is_dir():
            for report in crashes.iterdir():
                shutil.copy2(report, self.evidence_dir / f"run{run_index}-{report.name}")

    def classify_startup_failure(self) -> tuple[str, str]:
        lines = [line.text for line in self.server.output.lines()] if self.server else []
        output = "\n".join(lines)
        if self.control:
            return "unclassified", "vanilla control failed to start; diagnose the environment"
        if self.selection.platform == "fabric":
            # Only the Mixin error lines and their stack context, not the loader's mod listing.
            context = "\n".join("\n".join(lines[index:index + 8])
                                for index, text in enumerate(lines) if MIXIN_ERROR.search(text))
            names: list[str] = []
            try:
                for mod in fabric_mod_tree(Path(self.artifact_record["installed_path"])):
                    names.extend(mod["mixins"])
            except (OSError, KeyError, ValueError):
                pass
            implicated = sorted({name for name in names if name and name in context})
            if implicated:
                return "artifact", f"Mixin failure in the artifact's bundled config(s): {', '.join(implicated)}"
            if FABRIC_RESOLUTION_ERROR.search(output):
                return "artifact", "Fabric Loader rejected the artifact's declared dependencies"
        else:
            name = self.selection.artifact["descriptor"]["name"]
            if re.search(rf"(Could not load|Error occurred while enabling) .*{re.escape(name)}", output):
                return "artifact", "Paper reported a plugin load/enable error"
        return "unclassified", "run a --control row to separate environment from artifact failures"

    # Stages --------------------------------------------------------------------------------
    def stage_startup(self) -> None:
        check = self.checks["startup"].start()
        java = java_identity(self.java)
        self.environment["java"] = java
        expected_major = self.target["java_major"]
        if java.get("major") != expected_major:
            check.finish("blocked", f"Java major {java.get('major')} does not match pinned {expected_major}",
                         "environment_or_download")
            raise StageBlocked("Java runtime does not match the target pin", "environment_or_download")
        preflight = client_preflight(self.node)
        self.environment["client_preflight"] = preflight
        if preflight["problems"]:
            reason = "protocol client preflight failed: " + "; ".join(preflight["problems"])
            check.finish("blocked", reason, "environment_or_download")
            raise StageBlocked(reason, "environment_or_download")
        self.ensure_time()
        try:
            paths = self.acquire()
        except AcquisitionError as error:
            self.run_error("acquire", str(error), "environment_or_download")
            check.finish("blocked", f"acquisition failed: {error}", "environment_or_download")
            raise StageBlocked(f"acquisition failed: {error}", "environment_or_download") from error
        self.ensure_time()
        self.prepare_workspace(paths)
        self.verify_artifact(check)
        if any(not assertion["ok"] for assertion in check.assertions):
            check.finish("fail", "installed artifact identity does not match the lock", "artifact")
            raise StageBlocked("artifact identity mismatch", "artifact")
        self.ensure_time()
        if not self.start_server(1, check):
            record = self.server_runs[-1]
            if self.deadline_hit.is_set():
                check.finish("blocked", self.deadline_reason(), "timeout_or_no_observation")
                raise StageBlocked(self.deadline_reason(), "timeout_or_no_observation")
            if self.server is not None and self.server.running():
                self.server.kill()
                check.finish("blocked", f"server did not reach Done within {self.limits.server_start_s:g}s",
                             "timeout_or_no_observation")
                raise StageBlocked("server start timed out", "timeout_or_no_observation")
            failure_class, note = self.classify_startup_failure()
            check.check("run 1: server reached Done", False, "Done line", f"exited with code {record.get('exit_code')}")
            self.snapshot_logs(1)
            check.observe(note)
            check.finish("fail", f"server exited with code {record.get('exit_code')} before Done; {note}", failure_class)
            raise StageBlocked("startup failed", failure_class)
        self.connect_rcon()
        check.check("run 1: RCON responsive", self.gametime() >= 0, "game time", "readable")
        self.connect_clients(1)
        check.check("run 1: two protocol clients spawned", all(bot.spawned() for bot in self.bots.values()),
                    [OP_NAME, GUEST_NAME], [name for name, bot in self.bots.items() if bot.spawned()])
        self.state["startup_check"] = check

    def stage_world_setup(self) -> None:
        self.ensure_time()
        check = self.setup.start()
        gamerules = self.adapter["gamerules"]
        wanted = (("mob_spawning", "false"), ("daylight_cycle", "false"), ("mob_griefing", "true"),
                  ("random_tick_speed", "0"))
        for role, value in wanted:
            name = gamerules[role]
            self.cmd(f"gamerule {name} {value}")
            response = self.cmd(f"gamerule {name}")
            match = GAMERULE_VALUE.search(response)
            check.check(f"gamerule {name} = {value}", bool(match) and match.group(1) == value, value, response,
                        command=f"gamerule {name}")
        self.cmd("kill @e[type=!minecraft:player]")
        self.cmd(f"time set {WORLD_TIME}")
        self.cmd("weather clear")
        self.cmd(FORCELOAD)
        loaded = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.query("execute if loaded 8 100 8") and self.query("execute if loaded 20 100 8"):
                loaded = True
                break
            time.sleep(0.5)
        check.check("arena chunks loaded", loaded, True, loaded, command="execute if loaded 8 100 8")
        self.cmd(f"fill {box(ARENA_MIN, ARENA_MAX)} minecraft:glass hollow")
        for grass in GRASS:
            self.cmd(f"setblock {pos(grass)} minecraft:grass_block")
        reference = (ARENA_MIN[0], ARENA_MIN[1] + REFERENCE_DY, ARENA_MIN[2])
        self.cmd(f"clone {box(ARENA_MIN, ARENA_MAX)} {pos(reference)}")
        self.cmd(f"fill {box(SHEEP_MIN, SHEEP_MAX)} minecraft:glass hollow")
        self.cmd(f"fill {pos(SHEEP_GRASS[0])} {pos(SHEEP_GRASS[-1])} minecraft:grass_block")
        check.check("query probe: present block", self.query(f"execute if block {pos(ARENA_MIN)} minecraft:glass") == 1,
                    "Test passed", "Test passed")
        check.check("query probe: absent block", self.query(f"execute if block {pos(ARENA_MIN)} minecraft:dirt") == 0,
                    "Test failed", "Test failed")
        check.check("arena matches reference copy", self.arena_unchanged(), True, True)
        missing = [grass for grass in GRASS + SHEEP_GRASS
                   if not self.query(f"execute if block {pos(grass)} minecraft:grass_block")]
        check.check("grass blocks placed", not missing, [], missing)
        for name in self.bots:
            self.cmd(f"tp {name} {CLIENT_SPOT}")
        if not self.control:
            for command in self.platform.test_baseline():
                self.run_command(command, check)
            status = self.platform.parse_status(self.cmd(self.platform.status_command()))
            check.check("status parsed", status is not None, "status line", status)
        check.finish()
        if check.status != "pass":
            raise StageBlocked(f"world setup failed: {check.reason}")

    def denial_trials(self, check: Check, kind: str, carrying: str | None) -> dict[str, Any] | None:
        """Run fresh tagged trials until a denial is logged or the protected state changes.

        A changed arena or carried state ends the trial too, so prevention that silently stops
        working is a fail rather than a missing observation.
        """
        for trial in range(1, self.limits.trials + 1):
            self.ensure_time()
            tag = f"egc_{kind}_{trial}"
            self.summon_enderman(tag, carrying)
            offset = self.log_offset()

            def state_ok() -> bool:
                return not self.carrying(tag) if carrying is None else self.carrying(tag, carrying)

            self.set_ai(tag, True)
            progress = self.advance(
                self.limits.trial_ticks, self.limits.trial_s, freeze_tag=tag,
                stop_when=lambda: bool(self.denials(kind, offset, True)) or not self.arena_unchanged() or not state_ok())
            time.sleep(self.limits.settle_s)
            outcome = {"tag": tag, "lines": self.denials(kind, offset, True), "progress": progress,
                       "arena_ok": self.arena_unchanged(), "state_ok": state_ok()}
            check.observe(f"trial {trial}", entity_tag=tag, ticks=progress, attributable_denials=len(outcome["lines"]),
                          arena_unchanged=outcome["arena_ok"], carried_state_as_expected=outcome["state_ok"])
            if outcome["lines"] or not outcome["arena_ok"] or not outcome["state_ok"]:
                return outcome
            self.clear_mobs()
        return None

    @staticmethod
    def record_denial(check: Check, kind: str, outcome: dict[str, Any]) -> None:
        lines = outcome["lines"]
        first = lines[0] if lines else None
        check.check(f"{kind} denial logged inside the arena", bool(lines),
                    f"'Denied {kind}' line with arena coordinates",
                    f"{len(lines)} line(s); first: {first[0].text}" if first else "none",
                    log_offset=first[0].index if first else None, coords=list(first[1]) if first else None,
                    entity_tag=outcome["tag"])
        check.check("arena blocks unchanged", outcome["arena_ok"], True, outcome["arena_ok"],
                    command="execute if blocks <arena> <reference copy> all")

    def check_pickup(self) -> None:
        check = self.checks["pickup"].start()
        self.require_clients()
        self.clear_mobs()
        chat_start = self.chat_offsets()
        outcome = self.denial_trials(check, "pickup", None)
        if outcome is None:
            check.finish("blocked", f"no attributable pickup attempt in {self.limits.trials} trial(s)",
                         "timeout_or_no_observation")
            return
        tag = outcome["tag"]
        self.record_denial(check, "pickup", outcome)
        check.check("tagged Enderman carries nothing", outcome["state_ok"], "no carriedBlockState",
                    "none" if outcome["state_ok"] else "carrying a block", entity_tag=tag)
        missing = [list(g) for g in GRASS if not self.query(f"execute if block {pos(g)} minecraft:grass_block")]
        check.check("eligible grass still present", not missing, [], missing)
        others = self.endermen()
        check.check("no other Enderman present", others == 1, 1, others)
        if outcome["lines"]:
            self.state["pickup"] = {"tag": tag, "chat_start": chat_start,
                                    "frozen_mono": outcome["progress"]["frozen_mono"]}
        check.finish()

    def probe_pickup_cooldown(self) -> None:
        """Hold back at least one denial inside the cooldown, pause past it, then trigger a flush."""
        pickup = self.state.get("pickup")
        if not pickup:
            return
        tag, chat_start = pickup["tag"], pickup["chat_start"]
        first = self.wait_both(CHAT_DENIED["pickup"], chat_start)
        record: dict[str, Any] = {"first": {name: event.data["text"] if event else None for name, event in first.items()}}
        self.state["cooldown"] = record
        if not all(first.values()):
            return
        announced = max(event.monotonic for event in first.values())
        pause_start = pickup["frozen_mono"] + self.limits.settle_s
        if len(self.denials("pickup", 0, False)) < 2:
            # The pickup trial froze the Enderman on its first denial, so nothing is pending yet.
            # Let it try again inside the cooldown; the flush must then roll up a held-back denial.
            self.set_ai(tag, True)
            window_s = announced + self.limits.cooldown_s - COOLDOWN_MARGIN_S - time.monotonic()
            top_up = self.advance(self.limits.trial_ticks, max(0.0, window_s), freeze_tag=tag,
                                  stop_when=lambda: len(self.denials("pickup", 0, False)) >= 2)
            record["held_back_progress"] = top_up
            pause_start = top_up["frozen_mono"] + self.limits.settle_s
        resume_at = announced + self.limits.cooldown_s + 1.0
        time.sleep(max(0.0, resume_at - time.monotonic()))
        lines_before = len(self.denials("pickup", 0, False))
        pause_end = time.monotonic()
        offset = self.log_offset()
        self.set_ai(tag, True)
        progress = self.advance(self.limits.trial_ticks, self.limits.trial_s,
                                stop_when=lambda: bool(self.denials("pickup", offset, False)), freeze_tag=tag)
        time.sleep(self.limits.settle_s)
        after = {name: bot.wait_for(lambda event: event.kind == "chat" and event.monotonic > pause_end
                                    and bool(CHAT_DENIED["pickup"].fullmatch(event.data.get("text", "").strip())),
                                    chat_start[name], self.limits.chat_s)
                 for name, bot in self.bots.items()}
        total = len(self.denials("pickup", 0, False))
        record.update({"lines_before_pause": lines_before, "lines_total": total, "resume_progress": progress,
                       "pause_s": round(pause_end - pause_start, 3), "flush_received": {
                           name: event.data["text"] if event else None for name, event in after.items()}})
        verdicts = {}
        for name in self.bots:
            announcements = [Announcement(int(CHAT_DENIED["pickup"].fullmatch(e.data["text"].strip()).group(1)), e.monotonic)
                             for e in self.chats_matching(name, CHAT_DENIED["pickup"], chat_start[name])]
            verdict = evaluate(total, announcements, self.limits.cooldown_s, paused_after_line=lines_before,
                               paused_window=(pause_start, pause_end))
            verdicts[name] = {"ok": verdict.ok, "problems": list(verdict.problems),
                              "counts": [a.count for a in announcements],
                              "triggering_lines": list(verdict.triggering_lines),
                              "flush_count": verdict.flush_count}
        record["verdicts"] = verdicts

    def check_placement(self) -> None:
        check = self.checks["placement"].start()
        self.require_clients()
        self.clear_mobs()
        self.run_command(self.platform.policy("off"))
        chat_start = self.chat_offsets()
        outcome = self.denial_trials(check, "placement", "dirt")
        if outcome is None:
            check.finish("blocked", f"no attributable placement attempt in {self.limits.trials} trial(s)",
                         "timeout_or_no_observation")
            return
        tag = outcome["tag"]
        self.record_denial(check, "placement", outcome)
        if outcome["lines"] and self.selection.platform == "paper":
            # Paper logs the target block; Fabric logs the Enderman's own position.
            target = outcome["lines"][0][1]
            is_air = self.query(f"execute if block {pos(target)} minecraft:air") == 1
            check.check("logged placement target is still air", is_air, "air", "air" if is_air else "not air",
                        coords=list(target))
        check.check("tagged Enderman still carries dirt", outcome["state_ok"], "carriedBlockState dirt",
                    "dirt" if outcome["state_ok"] else "not carrying dirt", entity_tag=tag)
        others = self.endermen()
        check.check("no other Enderman present", others == 1, 1, others)
        if outcome["lines"]:
            received = self.wait_both(CHAT_DENIED["placement"], chat_start)
            self.state["placement_chat"] = {name: event.data["text"] if event else None
                                            for name, event in received.items()}
        check.finish()

    def carrier_holder(self, check: Check | None = None) -> str:
        """A stationary Enderman carrying dirt, already discovered by the carrier monitor.

        Policy changes only re-resolve holders the monitor already tracks, so a fresh holder is
        followed by a reload, which runs discovery immediately while a player is online.
        """
        holder = self.state.get("holder")
        if holder:
            return holder
        self.clear_mobs()
        self.run_command(self.platform.policy("off"))
        self.summon_enderman("egc_holder", "dirt")
        self.run_command(self.platform.reload(), check, "reload forces holder discovery" if check else None)
        self.state["holder"] = "egc_holder"
        return "egc_holder"

    def project_carrier_events(self, since_log: int, since_chat: dict[str, int]) -> dict[str, Any]:
        server = self.require_server()
        return {
            "alert_log": [line.text for line, _ in server.output.find(self.platform.alert_log, since_log)],
            "clear_log": [line.text for line, _ in server.output.find(self.platform.clear_log, since_log)],
            "alert_chat": {name: [e.data["text"] for e in self.chats_matching(name, CHAT_ALERT, since_chat[name])]
                           for name in self.bots},
            "clear_chat": {name: [e.data["text"] for e in self.chats_matching(name, CHAT_CLEARED, since_chat[name])]
                           for name in self.bots},
        }

    def check_carrier_off(self) -> None:
        check = self.checks["carrier_off"].start()
        self.require_clients()
        self.state.pop("holder", None)
        log_start, chat_start = self.log_offset(), self.chat_offsets()
        tag = self.carrier_holder(check)
        progress = self.advance(self.limits.carrier_ticks, max(self.limits.trial_s, 60))
        check.observe("advanced past one periodic discovery/resolution pass", ticks=progress)
        time.sleep(self.limits.settle_s)
        events = self.project_carrier_events(log_start, chat_start)
        holding = self.carrying(tag, "dirt")
        check.check("holder still carries dirt", holding, True, holding, entity_tag=tag)
        check.check("no carrier alert or clear logged", not events["alert_log"] and not events["clear_log"],
                    [], events["alert_log"] + events["clear_log"], log_offset=log_start)
        check.check("no carrier alert or clear chat", not any(events["alert_chat"].values())
                    and not any(events["clear_chat"].values()), {}, {**events["alert_chat"], **events["clear_chat"]})
        check.finish()

    def check_carrier_alert(self) -> None:
        check = self.checks["carrier_alert"].start()
        self.require_clients()
        tag = self.carrier_holder()
        coords = f"({', '.join(str(v) for v in HOLDER_BLOCK)})"
        log_start, chat_start = self.log_offset(), self.chat_offsets()
        self.run_command(self.platform.policy("alert"), check, "set carrier policy alert")
        logged = self.require_server().output.wait_for(re.compile(re.escape(f"holding a block at {coords}.")),
                                                      log_start, self.limits.chat_s)
        received = self.wait_both(re.compile(re.escape(f"[Enderman] holding a block at {coords}.")), chat_start)
        check.check("alert logged for the holder", logged is not None, f"holding a block at {coords}.",
                    logged[0].text if logged else None, log_offset=logged[0].index if logged else None)
        for name, event in received.items():
            check.check(f"{name} received the alert", event is not None, f"[Enderman] holding a block at {coords}.",
                        event.data["text"] if event else None)
        holding = self.carrying(tag, "dirt")
        check.check("holder still carries dirt", holding, True, holding, entity_tag=tag)
        events = self.project_carrier_events(log_start, chat_start)
        check.check("alert did not clear the holder", not events["clear_log"], [], events["clear_log"])
        self.state["alert_chat"] = {name: event.data["text"] if event else None for name, event in received.items()}
        check.finish()

    def check_carrier_auto_clear(self) -> None:
        check = self.checks["carrier_auto_clear"].start()
        self.require_clients()
        tag = self.carrier_holder()
        coords = f"({', '.join(str(v) for v in HOLDER_BLOCK)})"
        log_start, chat_start = self.log_offset(), self.chat_offsets()
        self.run_command(self.platform.policy("auto-clear"), check, "set carrier policy auto-clear")
        cleared = self.require_server().output.wait_for(self.platform.clear_log, log_start, self.limits.chat_s)
        received = self.wait_both(re.compile(r"\[Enderman\] holding block cleared x1\."), chat_start)
        check.check("clear logged for the holder", cleared is not None and coords in cleared[0].text,
                    f"clear at {coords}", cleared[0].text if cleared else None,
                    log_offset=cleared[0].index if cleared else None)
        for name, event in received.items():
            check.check(f"{name} received the clear announcement", event is not None,
                        "[Enderman] holding block cleared x1.", event.data["text"] if event else None)
        holding = self.carrying(tag)
        check.check("holder no longer carries a block", not holding, False, holding, entity_tag=tag)
        items = self.query(f"execute positioned {pos(HOLDER_BLOCK)} if entity @e[type=minecraft:item,distance=..16]")
        check.check("no item dropped by the clear", items == 0, 0, items)
        self.state["clear_chat"] = {name: event.data["text"] if event else None for name, event in received.items()}
        self.state.pop("holder", None)
        check.finish()

    def check_other_mob(self) -> None:
        check = self.checks["other_mob"].start()
        if not self.control:
            self.require_clients()
            status = self.platform.parse_status(self.cmd(self.platform.status_command()))
            protected = status is not None and (status.get("enabled", status.get("world_enabled")) is not False)
            check.observe("protection state during control", status=status)
            if not protected:
                raise StageBlocked("protection is not enabled for the other-mob control")
        self.clear_mobs()
        rule = self.adapter["gamerules"]["mob_griefing"]
        griefing = self.cmd(f"gamerule {rule}")
        if not griefing.rstrip(". ").endswith("true"):
            raise StageBlocked(f"{rule} is not true: {griefing!r}")
        self.cmd(f"fill {pos(SHEEP_GRASS[0])} {pos(SHEEP_GRASS[-1])} minecraft:grass_block")
        eaten: list[tuple[int, int, int]] = []
        for trial in range(1, self.limits.trials + 1):
            self.ensure_time()
            self.cmd(f'summon minecraft:sheep {SHEEP_SPAWN} {{Tags:["egc_sheep"],PersistenceRequired:1b}}')
            progress = self.advance(self.limits.other_mob_ticks, max(self.limits.trial_s, 60))
            eaten = [grass for grass in SHEEP_GRASS if self.query(f"execute if block {pos(grass)} minecraft:dirt")]
            alive = self.count("@e[type=minecraft:sheep,tag=egc_sheep]")
            check.observe(f"trial {trial}", ticks=progress, grass_to_dirt=[list(g) for g in eaten], sheep_alive=alive)
            if eaten:
                break
            self.cmd("kill @e[type=minecraft:sheep]")
        check.check("sheep converted grass to dirt with protection on", bool(eaten), "at least one grass->dirt",
                    [list(g) for g in eaten])
        if not eaten:
            check.finish("fail", f"no grass eaten in {self.limits.trials} x {self.limits.other_mob_ticks} ticks; "
                         "run a --control row to rule out the environment", "unclassified")
            return
        check.finish()

    def check_commands(self) -> None:
        check = self.checks["commands_permissions"].start()
        self.require_clients()
        guest = self.bots[GUEST_NAME]
        since = guest.offset()
        guest.chat(self.platform.guest_status.text)
        expected = self.platform.guest_status
        pattern = re.compile(expected.expected if expected.pattern else re.escape(expected.expected))
        event = guest.wait_chat(pattern, since, self.limits.chat_s)
        check.check(f"non-op `{expected.text}` is denied", event is not None, expected.expected,
                    [e.data["text"] for e in guest.chats(since)][:3])
        op = self.bots[OP_NAME]
        for command in self.platform.op_sequence():
            since = op.offset()
            op.chat(command.text)
            pattern = re.compile(command.expected if command.pattern else re.escape(command.expected))
            event = op.wait_chat(pattern, since, self.limits.chat_s)
            check.check(f"op `{command.text}`", event is not None, command.expected,
                        event.data["text"] if event else [e.data["text"] for e in op.chats(since)][:3])
        for command in self.platform.rcon_sequence():
            self.run_command(command, check)
        status = self.platform.parse_status(self.cmd(self.platform.status_command()))
        disk = self.platform.read_config(self.server_dir)
        check.check("RCON status matches the config on disk", status == disk, disk, status)
        check.finish()

    def check_persistence(self) -> None:
        check = self.checks["persistence"].start()
        self.require_clients()
        changes, expected = self.platform.persistence_changes()
        for command in changes:
            self.run_command(command, check)
        status = self.platform.parse_status(self.cmd(self.platform.status_command()))
        disk = self.platform.read_config(self.server_dir)
        check.check("status shows nondefault values", status == expected, expected, status)
        check.check("disk shows nondefault values", disk == expected, expected, disk)
        shutil.copy2(self.platform.config_path(self.server_dir), self.evidence_dir / "config-before-restart"
                     f"{self.platform.config_path(self.server_dir).suffix}")
        self.run_command(self.platform.reload(), check)
        status = self.platform.parse_status(self.cmd(self.platform.status_command()))
        check.check("status after reload", status == expected, expected, status)
        self.restart(check)
        status = self.platform.parse_status(self.cmd(self.platform.status_command()))
        disk = self.platform.read_config(self.server_dir)
        shutil.copy2(self.platform.config_path(self.server_dir), self.evidence_dir / "config-after-restart"
                     f"{self.platform.config_path(self.server_dir).suffix}")
        check.check("status after restart", status == expected, expected, status)
        check.check("disk after restart", disk == expected, expected, disk)
        for command in self.platform.persistence_restore():
            self.run_command(command)
        check.finish()

    def restart(self, check: Check) -> None:
        self.restart_attempted = True
        startup = self.state.get("startup_check")
        self.disconnect_clients()
        exit_code = self.stop_server(1)
        check.check("run 1 stopped cleanly", exit_code == 0, 0, exit_code)
        if startup is not None:
            startup.check("run 1 stopped cleanly", exit_code == 0, 0, exit_code)
        target = startup if startup is not None else check
        if not self.start_server(2, target):
            if self.deadline_hit.is_set():
                # The watchdog killed the restarting server; that is not a startup failure.
                raise StageBlocked(self.deadline_reason(), "timeout_or_no_observation")
            target.check("run 2: server reached Done", False, "Done line",
                         f"exit code {self.server_runs[-1].get('exit_code')}")
            raise StageBlocked("server did not restart", "unclassified")
        self.connect_rcon()
        self.connect_clients(2)
        if startup is not None:
            startup.check("run 2: two protocol clients spawned", all(b.spawned() for b in self.bots.values()),
                          [OP_NAME, GUEST_NAME], [name for name, b in self.bots.items() if b.spawned()])
        for name in self.bots:
            self.cmd(f"tp {name} {CLIENT_SPOT}")

    @staticmethod
    def record_cooldown(check: Check, cooldown: dict[str, Any] | None) -> list[str]:
        """Assert the pickup cooldown verdicts; return the observations that are missing."""
        if not cooldown or "verdicts" not in cooldown:
            return ["pickup denial/cooldown"]
        check.observe("pickup cooldown probe", **{k: v for k, v in cooldown.items() if k != "verdicts"})
        missing = []
        for name, verdict in cooldown["verdicts"].items():
            check.check(f"{name}: pickup announcements follow the cooldown/roll-up contract", verdict["ok"],
                        "x1 first, nothing inside the pause, then x<pending> on the next denial",
                        verdict)
            # An x1 flush fits the contract but shows no suppression or roll-up: missing evidence.
            if verdict["ok"] and (verdict.get("flush_count") or 0) < 2:
                missing.append(f"a pickup denial held back by the cooldown ({name})")
        return missing

    def check_announcements(self) -> None:
        check = self.checks["announcements_cooldown"].start()
        missing = self.record_cooldown(check, self.state.get("cooldown"))
        for label, key, expected in (("placement denial", "placement_chat", "[Enderman] Denied placement x1."),
                                     ("carrier alert", "alert_chat", None),
                                     ("carrier clear", "clear_chat", "[Enderman] holding block cleared x1.")):
            received = self.state.get(key)
            if not received:
                missing.append(label)
                continue
            for name, text in received.items():
                ok = text is not None and (expected is None or text == expected)
                check.check(f"{name}: {label} announced", ok, expected or "[Enderman] holding a block at (...).", text)
        if any(not assertion["ok"] for assertion in check.assertions):
            check.finish()
        elif missing:
            check.finish("blocked", f"missing observations from: {', '.join(missing)}", "timeout_or_no_observation")
        else:
            check.finish()

    # Orchestration -------------------------------------------------------------------------
    def guarded(self, name: str, action: Callable[[], None]) -> None:
        check = self.checks.checks.get(name)
        if check is not None and check.finished:
            return
        try:
            self.ensure_time()
            action()
        except StageBlocked as blocked:
            self.say(f"{name}: blocked: {blocked.reason}")
            if check is not None and not check.finished:
                check.finish("blocked", blocked.reason, blocked.failure_class)
            if self.server is None or not self.server.running():
                raise
        if check is not None and not check.finished:
            check.finish()
        if check is not None:
            self.say(f"{name}: {check.status} - {check.reason}")

    def run(self) -> dict[str, Any]:
        self.say(f"row {self.selection.key} artifact {'(control)' if self.control else self.selection.artifact_id}")
        self.start_watchdog()
        try:
            try:
                self.stage_startup()
            except StageBlocked as blocked:
                startup = self.checks["startup"]
                if not startup.finished:
                    startup.finish("blocked", blocked.reason, blocked.failure_class)
                self.checks.block_unfinished(f"startup did not complete: {blocked.reason}", blocked.failure_class)
                return self.finish()
            try:
                self.stage_world_setup()
            except StageBlocked as blocked:
                if not self.setup.finished:
                    self.setup.finish("blocked", blocked.reason, blocked.failure_class)
                self.checks.block_unfinished(f"world setup failed: {blocked.reason}", blocked.failure_class)
                self.finish_startup()
                return self.finish()
            if self.control:
                self.guarded("other_mob", self.check_other_mob)
                self.finish_startup(require_restart=False)
                return self.finish()
            self.guarded("pickup", self.check_pickup)
            try:
                self.probe_pickup_cooldown()
            except StageBlocked as blocked:
                self.say(f"cooldown probe blocked: {blocked.reason}")
            self.guarded("placement", self.check_placement)
            self.guarded("carrier_off", self.check_carrier_off)
            self.guarded("carrier_alert", self.check_carrier_alert)
            self.guarded("carrier_auto_clear", self.check_carrier_auto_clear)
            self.guarded("other_mob", self.check_other_mob)
            self.guarded("commands_permissions", self.check_commands)
            self.guarded("persistence", self.check_persistence)
            self.guarded("announcements_cooldown", self.check_announcements)
            self.finish_startup()
        except StageBlocked as blocked:
            reason, failure_class = blocked.reason, blocked.failure_class
            if self.deadline_hit.is_set():
                # Whatever the watchdog interrupted, the cause is the row limit.
                reason, failure_class = self.deadline_reason(), "timeout_or_no_observation"
            code = self.server.exit_code if self.server is not None else None
            self.run_error("run", f"{reason} (server exit code {code})", failure_class)
            self.checks.block_unfinished(f"run stopped: {reason}", failure_class)
            self.finish_startup()
        except KeyboardInterrupt:
            self.run_error("run", "interrupted by the operator", "harness_or_protocol")
            self.checks.block_unfinished("run interrupted", "harness_or_protocol")
            self.finish_startup()
        except Exception as error:  # noqa: BLE001 - every crash must still produce a result
            if self.deadline_hit.is_set():
                self.run_error("run", f"{self.deadline_reason()} ({type(error).__name__})", "timeout_or_no_observation")
                self.checks.block_unfinished(f"run stopped: {self.deadline_reason()}", "timeout_or_no_observation")
            else:
                self.run_error("run", f"runner exception: {type(error).__name__}: {error}", "harness_or_protocol")
                self.checks.block_unfinished(f"runner exception: {error}", "harness_or_protocol")
            self.finish_startup()
        return self.finish()

    def finish_startup(self, require_restart: bool = True) -> None:
        startup = self.checks["startup"]
        if startup.finished:
            return
        if require_restart and not self.restart_attempted:
            startup.finish("blocked", "the same-jar restart was not reached", "timeout_or_no_observation")
            return
        startup.finish()

    def stop_current_server(self) -> None:
        if self.server is None:
            return
        run_index = self.server_runs[-1]["run"] if self.server_runs else 1
        if self.server.running():
            self.stop_server(run_index)
        elif "ended_utc" not in (self.server_runs[-1] if self.server_runs else {}):
            self.server.close_streams()
            if self.server_runs:
                self.server_runs[-1]["exit_code"] = self.server.exit_code
                self.server_runs[-1]["killed"] = self.server.killed
                self.server_runs[-1]["ended_utc"] = utc_now()
            self.snapshot_logs(run_index)

    def redact_credentials(self) -> None:
        properties = self.server_dir / "server.properties"
        if properties.exists():
            text = properties.read_text(encoding="utf-8")
            properties.write_text(text.replace(self.rcon_password, "REDACTED"), encoding="utf-8")

    def cleanup(self) -> None:
        """Independent steps: a failure in one never skips process termination or redaction."""
        try:
            self.safely("client shutdown", self.disconnect_clients, None, stage="cleanup")
            self.safely("server stop", self.stop_current_server, None, stage="cleanup")
        finally:
            rcon = self.rcon
            self.rcon = None
            if rcon is not None:
                rcon.close()
            killed, survivors = self.registry.kill_all()
            if killed and not self.deadline_hit.is_set():
                self.run_error("cleanup", f"killed leftover owned processes: {', '.join(killed)}")
            if survivors:
                self.run_error("cleanup", f"owned processes could not be confirmed stopped: {', '.join(survivors)}")
            self.safely("credential redaction", self.redact_credentials, None, stage="cleanup")

    def install_eligible_for_cache(self, identity: dict[str, Any]) -> bool:
        """Only a run that reached Done with a verified jar identity and a clean install is cached."""
        return (any(run.get("done") for run in self.server_runs) and identity.get("matches_pin") is True
                and self.install_record.get("problems") == [] and not self.deadline_hit.is_set())

    def harvest_install_cache(self) -> None:
        seed = self.install_cache_dir()
        if seed.exists() or not self.server_dir.exists():
            return
        staging = fs_path(seed.with_name(seed.name + ".staging"))
        shutil.rmtree(staging, ignore_errors=True)
        try:
            for entry in self.install_cache_entries():
                source = fs_path(self.server_dir / entry)
                if source.is_dir():
                    shutil.copytree(source, staging / entry)
            if staging.exists():
                staging.rename(fs_path(seed))
        except OSError as error:
            self.say(f"install cache not saved: {str(error)[:300]}")
            shutil.rmtree(staging, ignore_errors=True)

    def minecraft_server_identity(self) -> dict[str, Any]:
        minecraft = self.selection.server_minecraft
        candidates = ([self.server_dir / "cache" / f"mojang_{minecraft}.jar"] if self.selection.platform == "paper"
                      else [self.server_dir / ".fabric" / "server" / f"{minecraft}-server.jar"])
        pinned = {"pinned_sha1": self.target["minecraft_server_sha1"],
                  "pinned_size": self.target["minecraft_server_size"]}
        for candidate in candidates:
            if fs_path(candidate).is_file():
                sha1 = hash_file(candidate, "sha1")
                size = fs_path(candidate).stat().st_size
                return {"path": candidate.relative_to(self.out_dir).as_posix(), "sha1": sha1, "size": size,
                        "matches_pin": sha1 == pinned["pinned_sha1"] and size == pinned["pinned_size"], **pinned}
        return {"path": None, "matches_pin": None, **pinned}

    def verify_identity_and_install(self) -> dict[str, Any]:
        """Post-run checks that a would-be pass needs: the official jar and a clean install tree."""
        reached_done = any(run.get("done") for run in self.server_runs)
        identity = self.safely("Minecraft server jar identity", self.minecraft_server_identity, {})
        if identity.get("matches_pin") is False:
            self.run_error("verify", f"official Minecraft server jar {identity.get('path')} (SHA-1 "
                           f"{identity.get('sha1')}, {identity.get('size')} bytes) does not match the pin",
                           "environment_or_download")
        elif reached_done and identity.get("matches_pin") is not True:
            # Never reached Done: the original startup/acquisition result stands on its own.
            self.run_error("verify", "the official Minecraft server jar was not observed, so the server under "
                           "test is unverified", "environment_or_download")
        if self.server_runs and self.server_dir.exists():
            problems = self.safely("install verification", lambda: verify_install(
                self.server_dir, self.selection.platform, self.target, self.selection.server_minecraft), None)
            self.install_record["problems"] = problems
            if problems:
                self.run_error("verify", f"{len(problems)} installed file(s) do not match official hashes: "
                               f"{problems[:3]}", "environment_or_download")
        return identity

    def libraries(self) -> list[dict[str, Any]]:
        root = fs_path(self.server_dir / "libraries")
        if not root.is_dir():
            return []
        return [{"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": hash_file(path),
                 "bytes": path.stat().st_size} for path in sorted(root.rglob("*.jar"))]

    def safely(self, label: str, action: Callable[[], Any], default: Any, stage: str = "report") -> Any:
        """Run one cleanup/collection step; a failure is a run error, never a lost result."""
        try:
            return action()
        except Exception as error:  # noqa: BLE001
            self.run_error(stage, f"{label}: {type(error).__name__}: {str(error)[:300]}")
            return default

    def evidence_files(self) -> list[dict[str, Any]]:
        files = []
        for path in sorted(self.out_dir.iterdir()):
            if path.is_file() and path.name not in ("result.json", "summary.md"):
                files.append(path)
        files += sorted(path for path in self.evidence_dir.rglob("*") if path.is_file())
        return [{"path": str(path.relative_to(self.out_dir)).replace("\\", "/"), "sha256": hash_file(path),
                 "bytes": path.stat().st_size} for path in files]

    def finish(self) -> dict[str, Any]:
        self.safely("cleanup", self.cleanup, None, stage="cleanup")
        self._finished.set()
        identity = self.verify_identity_and_install()
        if self.install_eligible_for_cache(identity):
            self.safely("install cache", self.harvest_install_cache, None)
        self.environment.update({
            "os": f"{host_platform.system()} {host_platform.release()} ({host_platform.version()})",
            "python": host_platform.python_version(),
            "node": tool_version([self.node, "--version"]),
            "minecraft_server_jar": identity,
            "install": {"cache_path": str(self.install_cache_dir()), "seeded": self.install_seeded,
                        **self.install_record},
            "libraries": self.safely("library identities", self.libraries, []),
            "ports": self.ports,
            "server_runs": self.server_runs,
            "deadline_hit": self.deadline_hit.is_set(),
        })
        self.say("closing evidence")
        # Close every writer before hashing, so recorded digests match the saved bytes.
        with self._log_lock:
            self._rcon_log.close()
            self._runner_log.close()
        evidence_files = self.safely("evidence hashes", self.evidence_files, [])
        overall, failure_class = compute_overall(self.checks.checks, self.checks.required, self.run_errors)
        print(f"{utc_now()} overall: {overall}" + (f" ({failure_class})" if failure_class else ""), flush=True)
        artifact = None if self.control else {
            "artifact_id": self.selection.artifact_id,
            "kind": self.selection.artifact["kind"],
            "filename": self.selection.artifact["filename"],
            "expected_sha256": self.selection.artifact["sha256"],
            **self.artifact_record,
        }
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "started_utc": self.started_utc,
            "ended_utc": utc_now(),
            "runner": {"version": RUNNER_VERSION, "command": self.command_line, **self.git},
            "manifest": {"path": str(self.manifest.path), "sha256": self.manifest.sha256},
            "selection": {"target_key": self.selection.key, "platform": self.selection.platform,
                          "side": self.selection.side, "minecraft": self.selection.minecraft,
                          "server_minecraft": self.selection.server_minecraft,
                          "server_basis": self.selection.server_basis,
                          "artifact_id": None if self.control else self.selection.artifact_id,
                          "artifact_overridden": self.selection.artifact_overridden, "control": self.control},
            "target_pins": self.target,
            "artifact": artifact,
            "environment": self.environment,
            "limits": {"effective": self.limits.to_json(), "overrides": self.limit_overrides},
            "world_setup": self.setup.to_json(),
            "checks": {name: check.to_json() for name, check in self.checks.checks.items()},
            "required_checks": list(self.checks.required),
            "overall": overall,
            "failure_class": failure_class,
            "run_errors": self.run_errors,
            "evidence": {"out_dir": str(self.out_dir), "workspace": str(self.server_dir), "files": evidence_files},
        }
