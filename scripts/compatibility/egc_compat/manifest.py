"""Load, validate, and select from the reviewed compatibility lock manifest.

The manifest is a lock file, not a query: every runnable target pins an exact official server
build or loader/launcher plus hashes, and every artifact pins its SHA-256. Anything missing makes
the manifest invalid, so the runner never silently falls back to a "latest" build, a neighboring
Minecraft version, or a different jar.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PLATFORMS = ("paper", "fabric")
SIDES = {"paper": ("server",), "fabric": ("server", "client")}
MINECRAFT_VERSIONS = (
    "1.21", "1.21.1", "1.21.2", "1.21.3", "1.21.4", "1.21.5",
    "1.21.6", "1.21.7", "1.21.8", "1.21.9", "1.21.10", "1.21.11",
)
ARTIFACT_KINDS = ("published", "candidate", "dependency")
PAPER_CHANNELS = ("STABLE", "BETA", "ALPHA", "RECOMMENDED")
GAMERULE_ROLES = ("mob_griefing", "mob_spawning", "daylight_cycle", "random_tick_speed")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
# Manifest strings that become file names or path segments: a plain basename, no separators,
# drive letters, alternate data streams (':'), traversal, or leading dot.
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.jar$")
SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
SAFE_LIBRARY_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*(/[A-Za-z0-9][A-Za-z0-9._+-]*)*\.jar$")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


def safe_filename(name: str) -> bool:
    return bool(SAFE_FILENAME_RE.match(name)) and ".." not in name \
        and name.split(".", 1)[0].lower() not in WINDOWS_RESERVED


def safe_library_path(path: str) -> bool:
    return bool(SAFE_LIBRARY_PATH_RE.match(path)) and ".." not in path.split("/") \
        and all(part.split(".", 1)[0].lower() not in WINDOWS_RESERVED for part in path.split("/"))


class ManifestError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class SelectionError(Exception):
    """The requested row/artifact cannot be run from this manifest."""


def target_key(platform: str, side: str, minecraft: str) -> str:
    return f"{platform}/{side}/{minecraft}"


def expected_target_keys() -> list[str]:
    return [
        target_key(platform, side, minecraft)
        for platform in PLATFORMS
        for side in SIDES[platform]
        for minecraft in MINECRAFT_VERSIONS
    ]


@dataclass(frozen=True)
class Manifest:
    path: Path
    sha256: str
    data: dict[str, Any]

    @property
    def user_agent(self) -> str:
        return self.data["user_agent"]

    def artifact(self, artifact_id: str) -> dict[str, Any]:
        return self.data["artifacts"][artifact_id]

    def target(self, key: str) -> dict[str, Any]:
        return self.data["targets"][key]

    def adapter(self, adapter_id: str) -> dict[str, Any]:
        return self.data["adapters"][adapter_id]


@dataclass(frozen=True)
class Selection:
    key: str
    platform: str
    side: str
    minecraft: str
    target: dict[str, Any]
    artifact_id: str
    artifact: dict[str, Any]
    artifact_overridden: bool

    @property
    def unavailable(self) -> dict[str, Any] | None:
        return self.target.get("unavailable")

    @property
    def server_minecraft(self) -> str:
        """The server release under test; differs from ``minecraft`` only with a recorded basis."""
        return self.target.get("server_minecraft", self.minecraft)

    @property
    def server_basis(self) -> str | None:
        return self.target.get("server_basis")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError([f"duplicate JSON key {key!r}"])
        result[key] = value
    return result


def parse_manifest_bytes(raw: bytes, path: Path = Path("<memory>")) -> Manifest:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError([f"manifest is not valid UTF-8 JSON: {error}"]) from error
    errors = validate(data)
    if errors:
        raise ManifestError(errors)
    return Manifest(path=path, sha256=hashlib.sha256(raw).hexdigest(), data=data)


def load_manifest(path: Path) -> Manifest:
    return parse_manifest_bytes(path.read_bytes(), path)


def _require(errors: list[str], where: str, obj: dict[str, Any], key: str, kind: type | tuple,
             pattern: re.Pattern[str] | None = None, positive: bool = False) -> Any:
    if key not in obj or obj[key] is None:
        errors.append(f"{where}: missing {key}")
        return None
    value = obj[key]
    if kind is int and isinstance(value, bool):
        errors.append(f"{where}: {key} must be an integer, not boolean")
        return None
    if not isinstance(value, kind):
        errors.append(f"{where}: {key} has type {type(value).__name__}")
        return None
    if isinstance(value, str) and not value.strip():
        errors.append(f"{where}: {key} is empty")
        return None
    if pattern is not None and not pattern.match(value):
        errors.append(f"{where}: {key} {value!r} does not match {pattern.pattern}")
        return None
    if positive and value <= 0:
        errors.append(f"{where}: {key} must be positive")
        return None
    return value


def _require_https(errors: list[str], where: str, obj: dict[str, Any], key: str) -> None:
    value = _require(errors, where, obj, key, str)
    if value is not None and not value.startswith("https://"):
        errors.append(f"{where}: {key} must be an https URL")


def validate(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest root must be an object"]
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    _require(errors, "manifest", data, "user_agent", str)

    adapters = data.get("adapters")
    if not isinstance(adapters, dict) or not adapters:
        errors.append("manifest: adapters must be a non-empty object")
        adapters = {}
    for adapter_id, adapter in adapters.items():
        where = f"adapters.{adapter_id}"
        if not isinstance(adapter, dict):
            errors.append(f"{where}: must be an object")
            continue
        gamerules = adapter.get("gamerules")
        if not isinstance(gamerules, dict):
            errors.append(f"{where}: missing gamerules object")
            continue
        for role in GAMERULE_ROLES:
            _require(errors, f"{where}.gamerules", gamerules, role, str)

    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("manifest: artifacts must be a non-empty object")
        artifacts = {}
    seen_hashes: dict[str, str] = {}
    for artifact_id, artifact in artifacts.items():
        where = f"artifacts.{artifact_id}"
        if not isinstance(artifact, dict):
            errors.append(f"{where}: must be an object")
            continue
        platform = _require(errors, where, artifact, "platform", str)
        if platform is not None and platform not in PLATFORMS:
            errors.append(f"{where}: unknown platform {platform!r}")
        kind = _require(errors, where, artifact, "kind", str)
        if kind is not None and kind not in ARTIFACT_KINDS:
            errors.append(f"{where}: unknown kind {kind!r}")
        filename = _require(errors, where, artifact, "filename", str)
        if filename is not None and not safe_filename(filename):
            errors.append(f"{where}: filename {filename!r} must be a plain .jar basename "
                          "(no path separators, drive, stream, traversal, or reserved name)")
        has_url = "url" in artifact
        has_local = "local_path" in artifact
        if has_url == has_local:
            errors.append(f"{where}: needs exactly one of url or local_path")
        elif has_url:
            _require_https(errors, where, artifact, "url")
        else:
            _require(errors, where, artifact, "local_path", str)
        _require(errors, where, artifact, "size", int, positive=True)
        sha256 = _require(errors, where, artifact, "sha256", str, SHA256_RE)
        if sha256 is not None:
            if sha256 in seen_hashes:
                errors.append(f"{where}: sha256 duplicates artifact {seen_hashes[sha256]}")
            seen_hashes[sha256] = artifact_id
        descriptor = artifact.get("descriptor")
        if not isinstance(descriptor, dict):
            errors.append(f"{where}: missing descriptor object")
        else:
            required = ("name", "version") if platform == "paper" else ("id", "version")
            for field in required:
                _require(errors, f"{where}.descriptor", descriptor, field, str)
        if kind in ("published", "candidate"):
            _require(errors, where, artifact, "test_mode", str)
        if kind == "published":
            _require(errors, where, artifact, "release_tag", str)
            _require(errors, where, artifact, "source_commit", str, COMMIT_RE)
        if kind == "candidate":
            _require(errors, where, artifact, "source_commit", str, COMMIT_RE)

    targets = data.get("targets")
    if not isinstance(targets, dict):
        errors.append("manifest: targets must be an object")
        targets = {}
    expected = expected_target_keys()
    missing = [key for key in expected if key not in targets]
    extra = [key for key in targets if key not in expected]
    if missing:
        errors.append(f"targets: missing {', '.join(missing)}")
    if extra:
        errors.append(f"targets: unexpected {', '.join(extra)}")

    for key, target in targets.items():
        if key in extra:
            continue
        where = f"targets.{key}"
        if not isinstance(target, dict):
            errors.append(f"{where}: must be an object")
            continue
        platform, side, _minecraft = key.split("/")
        artifact_id = _require(errors, where, target, "artifact_id", str)
        if artifact_id is not None:
            artifact = artifacts.get(artifact_id)
            if not isinstance(artifact, dict):
                errors.append(f"{where}: artifact_id {artifact_id!r} is not a manifest artifact")
            elif artifact.get("platform") != platform:
                errors.append(f"{where}: artifact {artifact_id!r} is for {artifact.get('platform')}, not {platform}")
            elif artifact.get("kind") == "dependency":
                errors.append(f"{where}: artifact {artifact_id!r} is a dependency, not a project artifact")

        unavailable = target.get("unavailable")
        if unavailable is not None:
            if not isinstance(unavailable, dict):
                errors.append(f"{where}: unavailable must be an object")
            else:
                _require(errors, f"{where}.unavailable", unavailable, "reason", str)
                _require_https(errors, f"{where}.unavailable", unavailable, "evidence_url")
                _require(errors, f"{where}.unavailable", unavailable, "checked_utc", str)
            continue

        adapter_id = _require(errors, where, target, "adapter", str)
        if adapter_id is not None and adapter_id not in adapters:
            errors.append(f"{where}: adapter {adapter_id!r} is not defined")
        _require(errors, where, target, "java_major", int, positive=True)

        if side == "server":
            server_minecraft = target.get("server_minecraft", key.split("/")[2])
            if server_minecraft not in MINECRAFT_VERSIONS:
                errors.append(f"{where}: server_minecraft {server_minecraft!r} is not a 1.21.x release")
            elif server_minecraft != key.split("/")[2]:
                # Only for a release whose players are served by another release's server build.
                _require(errors, where, target, "server_basis", str)
            _require(errors, where, target, "client_protocol", str)
            _require_https(errors, where, target, "minecraft_server_url")
            _require(errors, where, target, "minecraft_server_sha1", str, SHA1_RE)
            _require(errors, where, target, "minecraft_server_size", int, positive=True)
        if platform == "paper":
            _require(errors, where, target, "paper_build", int, positive=True)
            channel = _require(errors, where, target, "channel", str)
            if channel is not None and channel not in PAPER_CHANNELS:
                errors.append(f"{where}: unknown Paper channel {channel!r}")
            _require_https(errors, where, target, "build_api_url")
            _require_https(errors, where, target, "download_url")
            _require(errors, where, target, "size", int, positive=True)
            _require(errors, where, target, "sha256", str, SHA256_RE)
        elif side == "server":
            _require(errors, where, target, "loader", str, SAFE_VERSION_RE)
            _require(errors, where, target, "installer", str, SAFE_VERSION_RE)
            _require_https(errors, where, target, "launcher_url")
            _require(errors, where, target, "launcher_size", int, positive=True)
            _require(errors, where, target, "launcher_sha256", str, SHA256_RE)
            _require(errors, where, target, "loader_main_class", str)
            libraries = _require(errors, where, target, "loader_libraries", list)
            if libraries is not None and not libraries:
                errors.append(f"{where}: loader_libraries is empty")
            for index, library in enumerate(libraries or []):
                if not isinstance(library, dict):
                    errors.append(f"{where}.loader_libraries[{index}]: must be an object")
                    continue
                path = _require(errors, f"{where}.loader_libraries[{index}]", library, "path", str)
                if path is not None and not safe_library_path(path):
                    errors.append(f"{where}.loader_libraries[{index}]: unsafe path {path!r}")
                _require(errors, f"{where}.loader_libraries[{index}]", library, "sha256", str, SHA256_RE)
        else:
            _require(errors, where, target, "loader", str, SAFE_VERSION_RE)
            _require(errors, where, target, "profile_id", str)
            _require_https(errors, where, target, "profile_url")
            _require(errors, where, target, "minecraft_client_sha1", str, SHA1_RE)
            _require(errors, where, target, "minecraft_client_size", int, positive=True)
            if not isinstance(target.get("extended_checks_required"), bool):
                errors.append(f"{where}: extended_checks_required must be true or false")
            if "mod_menu_artifact_id" not in target:
                errors.append(f"{where}: missing mod_menu_artifact_id (use null when Mod Menu is not claimed)")
            else:
                mod_menu_id = target["mod_menu_artifact_id"]
                if mod_menu_id is not None:
                    mod_menu = artifacts.get(mod_menu_id)
                    if not isinstance(mod_menu, dict) or mod_menu.get("kind") != "dependency" \
                            or mod_menu.get("platform") != "fabric":
                        errors.append(f"{where}: mod_menu_artifact_id {mod_menu_id!r} must name a Fabric dependency artifact")
    return errors


def select(manifest: Manifest, platform: str, side: str, minecraft: str,
           artifact_override: str | None = None) -> Selection:
    if platform not in PLATFORMS:
        raise SelectionError(f"unknown platform {platform!r}; expected one of {', '.join(PLATFORMS)}")
    if side not in SIDES[platform]:
        raise SelectionError(f"{platform} has no {side!r} side; expected one of {', '.join(SIDES[platform])}")
    if minecraft not in MINECRAFT_VERSIONS:
        raise SelectionError(f"Minecraft {minecraft!r} is not a 1.21.x target; expected one of {', '.join(MINECRAFT_VERSIONS)}")
    key = target_key(platform, side, minecraft)
    target = manifest.target(key)
    artifact_id = artifact_override or target["artifact_id"]
    artifacts = manifest.data["artifacts"]
    if artifact_id not in artifacts:
        raise SelectionError(f"artifact {artifact_id!r} is not in the manifest; add it to the reviewed lock first")
    artifact = artifacts[artifact_id]
    if artifact["platform"] != platform:
        raise SelectionError(f"artifact {artifact_id!r} is a {artifact['platform']} artifact, not {platform}")
    if artifact["kind"] == "dependency":
        raise SelectionError(f"artifact {artifact_id!r} is a dependency, not a project artifact")
    return Selection(
        key=key,
        platform=platform,
        side=side,
        minecraft=minecraft,
        target=target,
        artifact_id=artifact_id,
        artifact=artifact,
        artifact_overridden=artifact_override is not None and artifact_override != target["artifact_id"],
    )
