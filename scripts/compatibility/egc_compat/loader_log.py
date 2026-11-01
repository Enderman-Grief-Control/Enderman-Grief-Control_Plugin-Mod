"""Fabric Loader log facts shared by server and client rows, and failure attribution.

Attribution answers "which installed jar supplied the mod that failed?". The project jar and
Fabric API can carry same-named modules (for example ``fabric-entity-events-v1``), and Loader keeps
only the newest copy, so matching a Mixin config name alone could blame the project jar for a
Fabric API failure. The loaded-mod tree Loader prints ("Loading N mods:") names the copy it chose
and the top-level jar that carried it; when no tree was printed (a resolution failure stops Loader
first), the failing mod's ID and version are matched against each installed jar's nested-mod tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .jarinfo import fabric_mod_tree

BANNER = re.compile(r"Loading Minecraft (\S+) with Fabric Loader (\S+)")
MOD_LIST = re.compile(r"Loading \d+ mods:")
MOD_ENTRY = re.compile(r"^([\t |]*)(?:-|\|--|\\--) (\S+) (\S+)\s*$")
MIXIN_ERROR = re.compile(r"(MixinApplyError|InvalidInjectionException|InvalidMixinException|"
                         r"Critical injection failure|Mixin apply for mod \S+ failed|"
                         r"Mixin transformation of \S+ failed|MixinTransformerError|"
                         r"@Shadow \S+ .{0,200}?was not located)")
MIXIN_APPLY = re.compile(r"Mixin apply for mod (\S+) failed (\S+?):(\S+) from mod (\S+)")
CRASH = re.compile(r"(Minecraft has crashed!|---- Minecraft Crash Report ----|#@!@# Game crashed!)")
RESOLUTION = re.compile(r"(Incompatible mods found|Mod resolution failed|Some of your mods are incompatible)")
REQUIRING_MOD = re.compile(r"Mod '[^']*' \(([^)]+)\) (\S+) (?:requires|is incompatible with|conflicts with)")
INCOMPATIBLE = "Incompatible mods found!"
# Mod IDs Loader provides itself; a failure rooted here is the game or Loader, not an installed jar.
BUILTIN_ROOTS = {"fabricloader", "minecraft", "java", "mixinextras"}


def banner(lines: list[str]) -> tuple[str, str] | None:
    for line in lines:
        match = BANNER.search(line)
        if match:
            return match.group(1), match.group(2)
    return None


def mod_tree(lines: list[str]) -> list[dict[str, Any]]:
    """Entries of the first "Loading N mods:" listing: id, version, and the top-level mod carrying it."""
    start = next((index for index, line in enumerate(lines) if MOD_LIST.search(line)), None)
    if start is None:
        return []
    entries: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    for line in lines[start + 1:]:
        match = MOD_ENTRY.match(line)
        if not match:
            break
        column = len(match.group(1).expandtabs(4))
        while stack and stack[-1][0] >= column:
            stack.pop()
        root = stack[0][1] if stack else match.group(2)
        entries.append({"id": match.group(2), "version": match.group(3), "root": root,
                        "parent": stack[-1][1] if stack else None})
        stack.append((column, match.group(2)))
    return entries


def mixin_failures(lines: list[str]) -> list[dict[str, Any]]:
    failures = []
    for index, line in enumerate(lines):
        if not MIXIN_ERROR.search(line):
            continue
        apply = MIXIN_APPLY.search(line)
        failures.append({"line": index, "text": line[:400], "mod": apply.group(1) if apply else None,
                         "config": apply.group(2) if apply else None})
    return failures


def crash_lines(lines: list[str]) -> list[str]:
    return [line[:300] for line in lines if CRASH.search(line)]


def resolution_failed(lines: list[str]) -> bool:
    return any(RESOLUTION.search(line) for line in lines)


def refusal_facts(lines: list[str], expected_ids: tuple[str, ...]) -> dict[str, Any]:
    """What a clean Loader refusal for missing ``expected_ids`` needs, read from the log."""
    named = {}
    for mod_id in expected_ids:
        escaped = re.escape(mod_id)
        pattern = re.compile(rf"(requires .*? of {escaped}, which is missing!|Install {escaped}\b)")
        hit = next((line[:300] for line in lines if pattern.search(line)), None)
        named[mod_id] = hit
    return {"incompatible_mods_found": any(INCOMPATIBLE in line for line in lines), "named": named,
            "mixin_errors": [failure["text"] for failure in mixin_failures(lines)][:5],
            "crash_markers": crash_lines(lines)[:5]}


@dataclass
class Installed:
    role: str  # "project" or "dependency"
    artifact_id: str
    path: Path
    tree: list[dict[str, Any]] = field(default_factory=list)

    @property
    def top_id(self) -> str | None:
        return self.tree[0]["id"] if self.tree else None


def installed_jars(records: list[dict[str, Any]], directory: Path) -> list[Installed]:
    found = []
    for record in records:
        path = directory / record["filename"]
        try:
            tree = fabric_mod_tree(path)
        except (OSError, ValueError, KeyError):
            tree = []
        found.append(Installed(record["role"], record["artifact_id"], path, tree))
    return found


@dataclass
class Attribution:
    kind: str  # "mixin", "resolution"
    mod: str | None
    version: str | None
    config: str | None = None
    root: str | None = None
    supplied_by: str | None = None
    role: str | None = None  # "project", "dependency", "game", or None when unknown
    line: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


def _supplier(mod: str, version: str | None, root: str | None, jars: list[Installed]) -> tuple[str | None, str | None]:
    if root in BUILTIN_ROOTS:
        return None, "game"
    if root is not None:
        for jar in jars:
            if jar.top_id == root:
                return jar.artifact_id, jar.role
    # No loaded-mod tree: match the failing mod (and version, when known) inside each jar. Without
    # a version, a mod carried by more than one jar stays unattributed rather than guessed.
    carriers = [jar for jar in jars if any(entry["id"] == mod and (version is None or entry["version"] == version)
                                           for entry in jar.tree)]
    if len(carriers) == 1:
        return carriers[0].artifact_id, carriers[0].role
    return None, None


def _attribute_mixin(lines: list[str], failures: list[dict[str, Any]], jars: list[Installed]) -> Attribution:
    first = next((failure for failure in failures if failure["mod"]), failures[0])
    mod = first["mod"]
    if mod is None:  # no "Mixin apply for mod" line: find a jar declaring a config named in the error
        context = "\n".join(lines[first["line"]:first["line"] + 8])
        mod = next((entry["id"] for jar in jars for entry in jar.tree
                    if any(name and name in context for name in entry.get("mixins", []))), None)
    entry = {item["id"]: item for item in mod_tree(lines)}.get(mod or "") or {}
    supplied_by, role = _supplier(mod, entry.get("version"), entry.get("root"), jars) if mod else (None, None)
    return Attribution("mixin", mod, entry.get("version"), first["config"], entry.get("root"), supplied_by, role,
                       first["text"])


def _attribute_resolution(lines: list[str], jars: list[Installed]) -> Attribution:
    for line in lines:
        match = REQUIRING_MOD.search(line)
        if match:
            supplied_by, role = _supplier(match.group(1), match.group(2), None, jars)
            return Attribution("resolution", match.group(1), match.group(2), supplied_by=supplied_by,
                               role=role, line=line[:400])
    return Attribution("resolution", None, None, line=next(line for line in lines if RESOLUTION.search(line))[:400])


def attribute(lines: list[str], jars: list[Installed]) -> Attribution | None:
    """Blame the first Mixin or dependency-resolution failure on the jar that supplied it."""
    failures = mixin_failures(lines)
    if failures:
        return _attribute_mixin(lines, failures, jars)
    if resolution_failed(lines):
        return _attribute_resolution(lines, jars)
    return None


def failure_class(attribution: Attribution | None, control: bool) -> tuple[str, str]:
    """(failure class, note). A dependency failure is the environment only once a control shows it."""
    if attribution is None:
        return "unclassified", "no Mixin or resolution failure found; run a --control row to separate the environment"
    what = (f"{attribution.kind} failure in {attribution.mod or 'an unidentified mod'}"
            + (f" {attribution.version}" if attribution.version else ""))
    if attribution.role == "project":
        return "artifact", f"{what}, supplied by the project jar {attribution.supplied_by}"
    if attribution.role == "game":
        return "game_or_loader", f"{what}, provided by the game or Loader ({attribution.root})"
    if attribution.role == "dependency":
        if control:
            return "game_or_loader", f"{what}, supplied by the profile dependency {attribution.supplied_by}"
        return "unclassified", (f"{what}, supplied by the profile dependency {attribution.supplied_by}; "
                                "the profile's --control run separates the documented environment from the artifact")
    return "unclassified", f"{what}; no installed jar could be identified as its source"
