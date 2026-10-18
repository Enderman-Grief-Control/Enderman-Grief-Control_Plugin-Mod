"""Paper and Fabric differences: install layout, commands, log/chat wording, and settings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COORDS = r"\((-?\d+), (-?\d+), (-?\d+)\)"
DONE_RE = re.compile(r'Done \((\d+[.,]\d+)s\)! For help, type "help"')
POLICIES = ("auto-clear", "alert", "off")
YAML_BOOLEAN_WORDS = {"y", "yes", "n", "no", "true", "false", "on", "off"}

# Chat wording comes from the shared messaging module and is identical on both platforms.
CHAT_DENIED = {
    "pickup": re.compile(r"\[Enderman\] Denied pickup x(\d+)\."),
    "placement": re.compile(r"\[Enderman\] Denied placement x(\d+)\."),
}
CHAT_ALERT = re.compile(r"\[Enderman\] holding a block at " + COORDS + r"\.")
CHAT_CLEARED = re.compile(r"\[Enderman\] holding block cleared x(\d+)\.")
CHAT_ANY_PROJECT = re.compile(r"\[Enderman\] .*")


def normalize_policy(raw: str | None, fallback: str | None) -> str | None:
    """Mirror ``HeldBlockHandling.fromConfig``: unknown or missing values use the fallback."""
    if raw is None:
        return fallback
    value = raw.strip().lower().replace("_", "-")
    return value if value in POLICIES else fallback


def parse_bukkit_yaml(text: str) -> dict[str, tuple[str, bool]]:
    """Flatten a Bukkit config into ``a.b`` keys mapped to (raw scalar, was_quoted).

    Only block mappings with scalar leaves are needed for the plugin's settings; list items and
    flow collections other than ``{}`` are ignored.
    """
    flat: dict[str, tuple[str, bool]] = {}
    stack: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        key = key.strip().strip("'\"")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([part for _indent, part in stack] + [key])
        value = value.split(" #", 1)[0].strip()
        if not value:
            stack.append((indent, key))
        elif value != "{}":
            quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""
            flat[path] = (value[1:-1] if quoted else value, quoted)
    return flat


def _yaml_policy(entry: tuple[str, bool] | None, fallback: str | None) -> str | None:
    if entry is None:
        return fallback
    value, quoted = entry
    if not quoted and value.lower() in YAML_BOOLEAN_WORDS:
        return fallback  # Bukkit reads an unquoted off/on as a boolean, which fromConfig rejects.
    return normalize_policy(value, fallback)


def _yaml_bool(entry: tuple[str, bool] | None, default: bool) -> bool:
    if entry is None:
        return default
    return entry[0].lower() in ("true", "yes", "on", "y")


def _enabled(word: str) -> bool:
    return word == "enabled"


@dataclass(frozen=True)
class Command:
    text: str
    expected: str  # exact response text; a regex when ``pattern`` is true
    pattern: bool = False

    def matches(self, response: str) -> bool:
        response = response.strip()
        if self.pattern:
            return re.fullmatch(self.expected, response, re.DOTALL) is not None
        return response == self.expected


class Platform:
    name: str
    denial_log: re.Pattern[str]
    alert_log: re.Pattern[str]
    clear_log: re.Pattern[str]
    guest_status: Command
    defaults_for_tests: dict[str, Any]

    def __init__(self, artifact: dict[str, Any]):
        self.artifact = artifact

    # Install layout -------------------------------------------------------------------------
    def artifact_dir(self, server_dir: Path) -> Path:
        raise NotImplementedError

    def config_path(self, server_dir: Path) -> Path:
        raise NotImplementedError

    def server_command(self, java: str, xmx: str) -> list[str]:
        raise NotImplementedError

    def startup_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        raise NotImplementedError

    def banner_pattern(self) -> re.Pattern[str]:
        raise NotImplementedError

    def server_identity(self, banner: re.Match[str], target: dict[str, Any],
                        server_minecraft: str) -> tuple[bool, str]:
        """Whether the startup banner names the pinned server; returns (ok, expected text)."""
        raise NotImplementedError

    # Settings --------------------------------------------------------------------------------
    def status_command(self) -> str:
        return "enderman status"

    def parse_status(self, text: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def read_config(self, server_dir: Path) -> dict[str, Any]:
        raise NotImplementedError

    def policy(self, mode: str) -> Command:
        raise NotImplementedError

    def protection(self, enabled: bool) -> Command:
        raise NotImplementedError

    def log_denials(self, enabled: bool) -> Command:
        state = "enabled" if enabled else "disabled"
        return Command(f"enderman set log-denials {str(enabled).lower()}", f"Log denials is now {state}.")

    def log_removals(self, enabled: bool) -> Command:
        state = "enabled" if enabled else "disabled"
        return Command(f"enderman set log-removals {str(enabled).lower()}", f"Log removals is now {state}.")

    def reload(self) -> Command:
        return Command("enderman reload", "EndermanGriefControl configuration reloaded.")

    def test_baseline(self) -> list[Command]:
        """Protection on, denial/removal logging on, carrier policy auto-clear."""
        return [self.protection(True), self.log_denials(True), self.log_removals(True), self.policy("auto-clear")]

    def op_sequence(self) -> list[Command]:
        raise NotImplementedError

    def rcon_sequence(self) -> list[Command]:
        raise NotImplementedError

    def persistence_changes(self) -> tuple[list[Command], dict[str, Any]]:
        raise NotImplementedError

    def persistence_restore(self) -> list[Command]:
        raise NotImplementedError


class PaperPlatform(Platform):
    name = "paper"
    world = "world"
    denial_log = re.compile(r"\[EndermanGriefControl\] Denied (pickup|placement) at " + COORDS + r"\.")
    alert_log = re.compile(r"\[EndermanGriefControl\] holding a block at " + COORDS + r"\.")
    clear_log = re.compile(r"\[EndermanGriefControl\] cleared a holder at " + COORDS + r"\.")
    guest_status = Command("/enderman status world", "You do not have permission to use this command.")

    def artifact_dir(self, server_dir: Path) -> Path:
        return server_dir / "plugins"

    def config_path(self, server_dir: Path) -> Path:
        return server_dir / "plugins" / self.artifact["descriptor"]["name"] / "config.yml"

    def server_command(self, java: str, xmx: str) -> list[str]:
        return [java, f"-Xmx{xmx}", "-jar", "paper.jar", "--nogui"]

    def startup_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        name = re.escape(self.artifact["descriptor"]["name"])
        version = re.escape(self.artifact["descriptor"]["version"])
        return [
            ("plugin enabling", re.compile(rf"Enabling {name} v{version}\b")),
            ("plugin enabled", re.compile(rf"\[{name}\] EndermanGriefControl has been enabled\.")),
        ]

    def banner_pattern(self) -> re.Pattern[str]:
        return re.compile(r"This server is running Paper version (\S+)")

    def server_identity(self, banner: re.Match[str], target: dict[str, Any],
                        server_minecraft: str) -> tuple[bool, str]:
        # e.g. "1.21-130-master@b1b5d4c" or "1.21.11-132-ver/1.21.11@c5eb079"
        expected = f"{server_minecraft}-{target['paper_build']}-"
        return banner.group(1).startswith(expected), f"Paper version {expected}..."

    def parse_status(self, text: str) -> dict[str, Any] | None:
        main = re.search(r"Default: (enabled|disabled), log denials: (enabled|disabled), "
                         r"log removals: (enabled|disabled), held-block: ([a-z-]+)", text)
        if not main:
            return None
        world = re.search(rf"(?:^|\s){self.world}: (enabled|disabled)", text)
        world_policy = re.search(rf"(?:^|\s){self.world} held-block: ([a-z-]+)", text)
        return {
            "default_enabled": _enabled(main.group(1)),
            "log_denials": _enabled(main.group(2)),
            "log_removals": _enabled(main.group(3)),
            "policy_default": main.group(4),
            "world_enabled": _enabled(world.group(1)) if world else None,
            "policy_world": world_policy.group(1) if world_policy else None,
        }

    def read_config(self, server_dir: Path) -> dict[str, Any]:
        flat = parse_bukkit_yaml(self.config_path(server_dir).read_text(encoding="utf-8"))
        policy_default = _yaml_policy(flat.get("default-held-block-handling"), "auto-clear")
        world_entry = flat.get(f"worlds.{self.world}")
        policy_world_entry = flat.get(f"held-block-worlds.{self.world}")
        return {
            "default_enabled": _yaml_bool(flat.get("default-enabled"), True),
            "log_denials": _yaml_bool(flat.get("logging.enabled"), False),
            "log_removals": _yaml_bool(flat.get("logging.removals"), True),
            "policy_default": policy_default,
            "world_enabled": _yaml_bool(world_entry, True) if world_entry else None,
            "policy_world": _yaml_policy(policy_world_entry, policy_default) if policy_world_entry else None,
        }

    def policy(self, mode: str) -> Command:
        return Command(f"enderman held-block {self.world} {mode}",
                       f"Held-block handling for world '{self.world}' is now {mode}.")

    def protection(self, enabled: bool) -> Command:
        state = "enabled" if enabled else "disabled"
        return Command(f"enderman toggle {self.world} {str(enabled).lower()}", f"World '{self.world}' is now {state}.")

    def default_enabled(self, enabled: bool) -> Command:
        state = "enabled" if enabled else "disabled"
        return Command(f"enderman set default {str(enabled).lower()}", f"Default is now {state}.")

    def default_policy(self, mode: str) -> Command:
        return Command(f"enderman set held-block-default {mode}", f"Default held-block handling is now {mode}.")

    def op_sequence(self) -> list[Command]:
        return [
            Command(f"/enderman status {self.world}",
                    rf"World '{self.world}': enabled, held-block: (auto-clear|alert|off)", pattern=True),
            Command("/" + self.policy("alert").text, self.policy("alert").expected),
            Command("/" + self.protection(False).text, self.protection(False).expected),
            Command("/" + self.protection(True).text, self.protection(True).expected),
            Command("/" + self.log_denials(True).text, self.log_denials(True).expected),
            Command("/" + self.policy("auto-clear").text, self.policy("auto-clear").expected),
            Command("/" + self.reload().text, self.reload().expected),
        ]

    def rcon_sequence(self) -> list[Command]:
        return [
            self.default_enabled(True), self.log_denials(True), self.log_removals(True),
            self.default_policy("auto-clear"), self.protection(True), self.policy("auto-clear"),
        ]

    def persistence_changes(self) -> tuple[list[Command], dict[str, Any]]:
        return ([
            self.default_enabled(False), self.protection(True), self.log_denials(False),
            self.log_removals(False), self.default_policy("alert"), self.policy("auto-clear"),
        ], {
            "default_enabled": False, "world_enabled": True, "log_denials": False,
            "log_removals": False, "policy_default": "alert", "policy_world": "auto-clear",
        })

    def persistence_restore(self) -> list[Command]:
        return [self.default_enabled(True), self.default_policy("auto-clear")] + self.test_baseline()


class FabricPlatform(Platform):
    name = "fabric"
    denial_log = re.compile(r"\[Enderman\] Denied (pickup|placement) at " + COORDS + r"\.")
    alert_log = re.compile(r"\[Enderman\] holding a block at " + COORDS + r"\.")
    clear_log = re.compile(r"\[Enderman\] holding cleared at " + COORDS + r"\.")
    guest_status = Command("/enderman status", r"Unknown or incomplete command.*", pattern=True)
    config_name = "no-enderman-grief.json"

    def artifact_dir(self, server_dir: Path) -> Path:
        return server_dir / "mods"

    def config_path(self, server_dir: Path) -> Path:
        return server_dir / "config" / self.config_name

    def server_command(self, java: str, xmx: str) -> list[str]:
        return [java, f"-Xmx{xmx}", "-jar", "fabric-server-launch.jar", "nogui"]

    def startup_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        mod_id = re.escape(self.artifact["descriptor"]["id"])
        version = re.escape(self.artifact["descriptor"]["version"])
        return [
            ("mod listed by loader", re.compile(rf"\b{mod_id} {version}\b")),
            ("mod initialized", re.compile(r"EndermanGriefControl has been initialized\.")),
        ]

    def banner_pattern(self) -> re.Pattern[str]:
        return re.compile(r"Loading Minecraft (\S+) with Fabric Loader (\S+)")

    def server_identity(self, banner: re.Match[str], target: dict[str, Any],
                        server_minecraft: str) -> tuple[bool, str]:
        expected = (server_minecraft, target["loader"])
        return banner.groups() == expected, f"Minecraft {expected[0]} with Fabric Loader {expected[1]}"

    def parse_status(self, text: str) -> dict[str, Any] | None:
        main = re.search(r"Prevention: (enabled|disabled), log denials: (enabled|disabled), "
                         r"log removals: (enabled|disabled), held-block: ([a-z-]+)", text)
        if not main:
            return None
        return {
            "enabled": _enabled(main.group(1)),
            "log_denials": _enabled(main.group(2)),
            "log_removals": _enabled(main.group(3)),
            "policy": main.group(4),
        }

    def read_config(self, server_dir: Path) -> dict[str, Any]:
        data = json.loads(self.config_path(server_dir).read_text(encoding="utf-8"))
        return {
            "enabled": bool(data.get("enabled", True)),
            "log_denials": bool(data.get("loggingEnabled", False)),
            "log_removals": bool(data.get("logRemovals", True)),
            "policy": normalize_policy(data.get("heldBlockHandling"), "auto-clear"),
        }

    def policy(self, mode: str) -> Command:
        return Command(f"enderman set held-block {mode}", f"Held-block handling is now {mode}.")

    def protection(self, enabled: bool) -> Command:
        state = "enabled" if enabled else "disabled"
        return Command(f"enderman toggle {str(enabled).lower()}", f"Enderman grief prevention is now {state}.")

    def op_sequence(self) -> list[Command]:
        return [
            Command("/enderman status",
                    r"Prevention: enabled, log denials: (enabled|disabled), log removals: (enabled|disabled), "
                    r"held-block: (auto-clear|alert|off)", pattern=True),
            Command("/" + self.policy("alert").text, self.policy("alert").expected),
            Command("/" + self.protection(False).text, self.protection(False).expected),
            Command("/" + self.protection(True).text, self.protection(True).expected),
            Command("/" + self.log_denials(True).text, self.log_denials(True).expected),
            Command("/" + self.log_removals(True).text, self.log_removals(True).expected),
            Command("/" + self.policy("auto-clear").text, self.policy("auto-clear").expected),
            Command("/" + self.reload().text, self.reload().expected),
        ]

    def rcon_sequence(self) -> list[Command]:
        return [self.log_denials(True), self.log_removals(True), self.policy("auto-clear"), self.protection(True)]

    def persistence_changes(self) -> tuple[list[Command], dict[str, Any]]:
        return ([
            self.protection(False), self.log_denials(False), self.log_removals(False), self.policy("alert"),
        ], {"enabled": False, "log_denials": False, "log_removals": False, "policy": "alert"})

    def persistence_restore(self) -> list[Command]:
        return self.test_baseline()


def platform_for(name: str, artifact: dict[str, Any]) -> Platform:
    if name == "paper":
        return PaperPlatform(artifact)
    if name == "fabric":
        return FabricPlatform(artifact)
    raise ValueError(f"unknown platform {name!r}")
