"""Load, validate, and select from the reviewed compatibility lock manifest.

The manifest is a lock file, not a query: every runnable target pins an exact official server
build or loader/launcher plus hashes, and every artifact pins its SHA-256. Anything missing makes
the manifest invalid, so the runner never silently falls back to a "latest" build, a neighboring
Minecraft version, or a different jar.

Schema 2 adds install profiles: the exact set of jars a documented install contains. A profile
names the mod IDs it installs beside the project jar, and each target pins one dependency artifact
per mod ID its applicable profiles need, so a run under any profile installs only pinned jars.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fabric_version

SCHEMA_VERSION = 2
PLATFORMS = ("paper", "fabric")
SIDES = {"paper": ("server",), "fabric": ("server", "client")}
MINECRAFT_VERSIONS = (
    "1.21", "1.21.1", "1.21.2", "1.21.3", "1.21.4", "1.21.5",
    "1.21.6", "1.21.7", "1.21.8", "1.21.9", "1.21.10", "1.21.11",
)
ARTIFACT_KINDS = ("published", "candidate", "dependency", "fixture")
PROJECT_KINDS = ("published", "candidate", "fixture")
BUILD_METHODS = ("local-build", "registered")
DEFAULT_PROFILE = "standard"
PAPER_CHANNELS = ("STABLE", "BETA", "ALPHA", "RECOMMENDED")
GAMERULE_ROLES = ("mob_griefing", "mob_spawning", "daylight_cycle", "random_tick_speed")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA512_RE = re.compile(r"^[0-9a-f]{128}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PROFILE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
MOD_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
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
class Profile:
    """One install profile: what a user installs beside the project jar, and the expected outcome."""

    name: str
    platform: str
    documented: bool
    sides: tuple[str, ...]
    dependencies: tuple[str, ...]
    expect: Any  # "run", or {"loader_refusal": [mod IDs]}

    @property
    def refusal_ids(self) -> tuple[str, ...] | None:
        """The mod IDs Loader must name when refusing, for a profile that expects a refusal."""
        if isinstance(self.expect, dict):
            return tuple(self.expect["loader_refusal"])
        return None

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "documented": self.documented, "sides": list(self.sides),
                "dependencies": list(self.dependencies), "expect": self.expect}


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

    def profile(self, platform: str, name: str) -> Profile:
        raw = self.data["profiles"][platform][name]
        return Profile(name=name, platform=platform, documented=raw["documented"], sides=tuple(raw["sides"]),
                       dependencies=tuple(raw["dependencies"]), expect=raw["expect"])

    def profiles(self, platform: str, side: str, documented_only: bool = False) -> list[Profile]:
        found = [self.profile(platform, name) for name in self.data["profiles"][platform]]
        return [profile for profile in found
                if side in profile.sides and (profile.documented or not documented_only)]


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
    profile: Profile

    @property
    def unavailable(self) -> dict[str, Any] | None:
        return self.target.get("unavailable")

    @property
    def fixture(self) -> bool:
        """A harness self-test jar; its results never count toward a claim."""
        return self.artifact.get("kind") == "fixture"

    def dependency_ids(self) -> dict[str, str]:
        """Mod ID to pinned artifact ID, for every dependency the selected profile installs."""
        pins = self.target.get("dependencies") or {}
        return {mod_id: pins[mod_id] for mod_id in self.profile.dependencies}

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


def _require_libraries(errors: list[str], where: str, target: dict[str, Any]) -> None:
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


def _validate_adapters(errors: list[str], data: dict[str, Any]) -> dict[str, Any]:
    adapters = data.get("adapters")
    if not isinstance(adapters, dict) or not adapters:
        errors.append("manifest: adapters must be a non-empty object")
        return {}
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
    return adapters


def _validate_expect(errors: list[str], where: str, platform: str, expect: Any) -> None:
    if expect == "run":
        return
    refusal = expect.get("loader_refusal") if isinstance(expect, dict) and set(expect) == {"loader_refusal"} else None
    if platform != "fabric" or not isinstance(refusal, list) or not refusal \
            or not all(isinstance(mod_id, str) and MOD_ID_RE.match(mod_id) for mod_id in refusal):
        errors.append(f"{where}: expect must be \"run\" or (Fabric only) {{\"loader_refusal\": [mod IDs]}}")


def _validate_profiles(errors: list[str], data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Profiles per platform; returns only well-formed ones, so later rules can rely on them."""
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(PLATFORMS):
        errors.append(f"manifest: profiles must be an object with exactly {', '.join(PLATFORMS)}")
        profiles = {platform: (profiles or {}).get(platform, {}) if isinstance(profiles, dict) else {}
                    for platform in PLATFORMS}
    valid: dict[str, dict[str, Any]] = {platform: {} for platform in PLATFORMS}
    for platform in PLATFORMS:
        entries = profiles.get(platform)
        if not isinstance(entries, dict) or not entries:
            errors.append(f"profiles.{platform}: must be a non-empty object")
            continue
        for name, profile in entries.items():
            where = f"profiles.{platform}.{name}"
            before = len(errors)
            if not PROFILE_NAME_RE.match(name):
                errors.append(f"{where}: profile names are lower-case words joined by '-'")
            if not isinstance(profile, dict):
                errors.append(f"{where}: must be an object")
                continue
            if not isinstance(profile.get("documented"), bool):
                errors.append(f"{where}: documented must be true or false")
            sides = profile.get("sides")
            if not isinstance(sides, list) or not sides or len(set(sides)) != len(sides) \
                    or not set(sides) <= set(SIDES[platform]):
                errors.append(f"{where}: sides must be a non-empty list drawn from {', '.join(SIDES[platform])}")
            dependencies = profile.get("dependencies")
            if not isinstance(dependencies, list) or len(set(map(str, dependencies or []))) != len(dependencies or []) \
                    or not all(isinstance(mod_id, str) and MOD_ID_RE.match(mod_id) for mod_id in dependencies):
                errors.append(f"{where}: dependencies must be a list of distinct mod IDs")
            _validate_expect(errors, where, platform, profile.get("expect"))
            if len(errors) == before:
                valid[platform][name] = profile
        for side in SIDES[platform]:
            if not any(side in profile["sides"] and profile["documented"] for profile in valid[platform].values()):
                errors.append(f"profiles.{platform}: no documented profile for the {side} side")
        standard = valid[platform].get(DEFAULT_PROFILE)
        if standard is None or set(standard["sides"]) != set(SIDES[platform]):
            errors.append(f"profiles.{platform}: the {DEFAULT_PROFILE!r} profile must exist for every side")
    return valid


def _validate_build(errors: list[str], where: str, build: Any) -> None:
    if not isinstance(build, dict):
        errors.append(f"{where}: build must be an object")
        return
    method = build.get("method")
    if method not in BUILD_METHODS:
        errors.append(f"{where}.build: method must be one of {', '.join(BUILD_METHODS)}")
    elif method == "local-build":
        commands = _require(errors, f"{where}.build", build, "commands", list)
        if commands is not None and (not commands or not all(isinstance(c, list) and c for c in commands)):
            errors.append(f"{where}.build: commands must be a non-empty list of argument lists")
        for key in ("java", "build_tool", "built_utc"):
            _require(errors, f"{where}.build", build, key, str)
        if build.get("clean_tree") is not True:
            errors.append(f"{where}.build: clean_tree must be true (candidates are built only from a clean tree)")
    else:
        for key in ("registered_from", "registered_utc"):
            _require(errors, f"{where}.build", build, key, str)


def _validate_dependency_artifact(errors: list[str], where: str, artifact: dict[str, Any]) -> None:
    for key in ("modrinth_project_id", "modrinth_version_id"):
        _require(errors, where, artifact, key, str)
    versions = artifact.get("game_versions")
    if not isinstance(versions, list) or not versions or not all(isinstance(v, str) for v in versions):
        errors.append(f"{where}: game_versions must be a non-empty list of Minecraft versions")
    _require(errors, where, artifact, "sha512", str, SHA512_RE)
    if "sha1" in artifact:
        _require(errors, where, artifact, "sha1", str, SHA1_RE)
    declares = artifact.get("declares")
    if not isinstance(declares, dict) or set(declares) != {"minecraft", "fabricloader"}:
        errors.append(f"{where}: declares must record the jar's own minecraft and fabricloader predicates")
        return
    for key, predicate in declares.items():
        try:
            fabric_version.matches(predicate, "1.21")
        except ValueError as error:
            errors.append(f"{where}.declares.{key}: {error}")


def _validate_artifact(errors: list[str], where: str, artifact: dict[str, Any], seen_hashes: dict[str, str],
                       artifact_id: str) -> None:
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
        _require(errors, where, artifact, "source_commit", str, COMMIT_RE)
    if kind == "published":
        _require(errors, where, artifact, "release_tag", str)
    if kind == "candidate" and "build" in artifact:
        _validate_build(errors, where, artifact["build"])
    if kind == "dependency":
        if platform != "fabric":
            errors.append(f"{where}: dependency artifacts are Fabric mods")
        _validate_dependency_artifact(errors, where, artifact)
    if kind == "fixture":
        _require(errors, where, artifact, "purpose", str)


def _validate_artifacts(errors: list[str], data: dict[str, Any]) -> dict[str, Any]:
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("manifest: artifacts must be a non-empty object")
        return {}
    seen_hashes: dict[str, str] = {}
    for artifact_id, artifact in artifacts.items():
        where = f"artifacts.{artifact_id}"
        if not isinstance(artifact, dict):
            errors.append(f"{where}: must be an object")
            continue
        _validate_artifact(errors, where, artifact, seen_hashes, artifact_id)
    return artifacts


def _validate_target_dependencies(errors: list[str], where: str, key: str, target: dict[str, Any],
                                  artifacts: dict[str, Any], profiles: dict[str, dict[str, Any]]) -> None:
    """Every mod ID an applicable profile installs maps to a pinned dependency for this exact row."""
    platform, side, minecraft = key.split("/")
    needed = {mod_id for profile in profiles[platform].values() if side in profile["sides"]
              for mod_id in profile["dependencies"]}
    pins = target.get("dependencies", {})
    if not isinstance(pins, dict):
        errors.append(f"{where}: dependencies must be an object of mod ID to artifact ID")
        return
    for mod_id in sorted(needed - set(pins)):
        errors.append(f"{where}: dependencies has no pin for {mod_id!r}, which a {side} profile installs")
    for mod_id in sorted(set(pins) - needed):
        errors.append(f"{where}: dependencies pins {mod_id!r}, which no {side} profile installs")
    for mod_id, artifact_id in pins.items():
        artifact = artifacts.get(artifact_id) if isinstance(artifact_id, str) else None
        if not isinstance(artifact, dict) or artifact.get("kind") != "dependency" or artifact.get("platform") != platform:
            errors.append(f"{where}: dependencies.{mod_id} {artifact_id!r} must name a {platform} dependency artifact")
            continue
        if (artifact.get("descriptor") or {}).get("id") != mod_id:
            errors.append(f"{where}: dependencies.{mod_id} names {artifact_id!r}, whose descriptor id is "
                          f"{(artifact.get('descriptor') or {}).get('id')!r}")
        if minecraft not in (artifact.get("game_versions") or []):
            errors.append(f"{where}: dependencies.{mod_id} {artifact_id!r} does not list Minecraft {minecraft}")
        declares = artifact.get("declares") if isinstance(artifact.get("declares"), dict) else {}
        for predicate_key, version in (("minecraft", minecraft), ("fabricloader", target.get("loader"))):
            try:
                if version is not None and not fabric_version.matches(declares.get(predicate_key), version):
                    errors.append(f"{where}: dependencies.{mod_id} {artifact_id!r} declares {predicate_key} "
                                  f"{declares.get(predicate_key)!r}, which excludes {version}")
            except ValueError:
                pass  # reported with the artifact


def _validate_target(errors: list[str], key: str, target: dict[str, Any], artifacts: dict[str, Any],
                     adapters: dict[str, Any], profiles: dict[str, dict[str, Any]]) -> None:
    where = f"targets.{key}"
    platform, side, minecraft = key.split("/")
    artifact_id = _require(errors, where, target, "artifact_id", str)
    if artifact_id is not None:
        artifact = artifacts.get(artifact_id)
        if not isinstance(artifact, dict):
            errors.append(f"{where}: artifact_id {artifact_id!r} is not a manifest artifact")
        elif artifact.get("platform") != platform:
            errors.append(f"{where}: artifact {artifact_id!r} is for {artifact.get('platform')}, not {platform}")
        elif artifact.get("kind") == "dependency":
            errors.append(f"{where}: artifact {artifact_id!r} is a dependency, not a project artifact")
        elif artifact.get("kind") == "fixture":
            errors.append(f"{where}: artifact {artifact_id!r} is a fixture; fixtures are selected only by --artifact")

    unavailable = target.get("unavailable")
    if unavailable is not None:
        if not isinstance(unavailable, dict):
            errors.append(f"{where}: unavailable must be an object")
        else:
            _require(errors, f"{where}.unavailable", unavailable, "reason", str)
            _require_https(errors, f"{where}.unavailable", unavailable, "evidence_url")
            _require(errors, f"{where}.unavailable", unavailable, "checked_utc", str)
        return

    adapter_id = _require(errors, where, target, "adapter", str)
    if adapter_id is not None and adapter_id not in adapters:
        errors.append(f"{where}: adapter {adapter_id!r} is not defined")
    _require(errors, where, target, "java_major", int, positive=True)
    _validate_target_dependencies(errors, where, key, target, artifacts, profiles)

    if side == "server":
        server_minecraft = target.get("server_minecraft", minecraft)
        if server_minecraft not in MINECRAFT_VERSIONS:
            errors.append(f"{where}: server_minecraft {server_minecraft!r} is not a 1.21.x release")
        elif server_minecraft != minecraft:
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
        _require_libraries(errors, where, target)
    else:
        _require(errors, where, target, "loader", str, SAFE_VERSION_RE)
        _require(errors, where, target, "profile_id", str)
        _require_https(errors, where, target, "profile_url")
        # The pinned version JSON pins the client's libraries, asset index, and mappings by SHA-1.
        _require_https(errors, where, target, "version_json_url")
        _require(errors, where, target, "version_json_sha1", str, SHA1_RE)
        _require(errors, where, target, "minecraft_client_sha1", str, SHA1_RE)
        _require(errors, where, target, "minecraft_client_size", int, positive=True)
        _require(errors, where, target, "loader_main_class", str)
        _require_libraries(errors, where, target)
        if not isinstance(target.get("extended_checks_required"), bool):
            errors.append(f"{where}: extended_checks_required must be true or false")
        if "mod_menu_artifact_id" in target:
            errors.append(f"{where}: mod_menu_artifact_id is replaced by dependencies.modmenu in schema 2")


def validate(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest root must be an object"]
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    _require(errors, "manifest", data, "user_agent", str)
    adapters = _validate_adapters(errors, data)
    profiles = _validate_profiles(errors, data)
    artifacts = _validate_artifacts(errors, data)

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
        if not isinstance(target, dict):
            errors.append(f"targets.{key}: must be an object")
            continue
        _validate_target(errors, key, target, artifacts, adapters, profiles)
    for minecraft in MINECRAFT_VERSIONS:
        # A player installs the same Fabric API on both sides, so both rows pin the same jar.
        pins = [(targets.get(target_key("fabric", side, minecraft)) or {}).get("dependencies") or {}
                for side in SIDES["fabric"]]
        apis = {pin.get("fabric-api") for pin in pins if isinstance(pin, dict) and "fabric-api" in pin}
        if len(apis) > 1:
            errors.append(f"targets: fabric server and client {minecraft} pin different Fabric API jars "
                          f"({', '.join(sorted(map(str, apis)))})")
    return errors


def select(manifest: Manifest, platform: str, side: str, minecraft: str,
           artifact_override: str | None = None, profile: str = DEFAULT_PROFILE) -> Selection:
    if platform not in PLATFORMS:
        raise SelectionError(f"unknown platform {platform!r}; expected one of {', '.join(PLATFORMS)}")
    if side not in SIDES[platform]:
        raise SelectionError(f"{platform} has no {side!r} side; expected one of {', '.join(SIDES[platform])}")
    if minecraft not in MINECRAFT_VERSIONS:
        raise SelectionError(f"Minecraft {minecraft!r} is not a 1.21.x target; expected one of {', '.join(MINECRAFT_VERSIONS)}")
    if profile not in manifest.data["profiles"][platform]:
        raise SelectionError(f"{platform} has no install profile {profile!r}; expected one of "
                             f"{', '.join(manifest.data['profiles'][platform])}")
    chosen = manifest.profile(platform, profile)
    if side not in chosen.sides:
        raise SelectionError(f"profile {profile!r} applies to {', '.join(chosen.sides)}, not {side}")
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
        profile=chosen,
    )
