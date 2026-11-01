"""Official client launch metadata, hash-verified acquisition, and the launch argument file.

Trust chain: the manifest pins the Mojang version JSON by SHA-1, and that JSON pins the client jar,
every vanilla library, the asset index, the client mappings, and the server jar used for world
templates. The Fabric launch profile is fetched from Fabric meta but accepted only when its main
class and library set equal the client target's own pins, and each Loader library is checked
against its pinned SHA-256. Every download goes through ``acquire.py`` (size caps, deadlines, path
containment); asset objects are verified by SHA-1 on every run.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as host_platform
import re
import shutil
import sys
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..acquire import (AcquisitionError, HttpLimits, contained, download, fetch_pinned, fs_path, get_json,
                       hash_file, verify_file)
from ..manifest import Manifest, Selection, safe_library_path

PLAYER = "EGCClient"
RESOURCES = "https://resources.download.minecraft.net"
WINDOW_SIZE = (854, 480)
GUI_SCALE = 2
LAUNCHER_NAME = "egc-compat-runner"
MAX_META_BYTES = 8 * 1024 * 1024


def host_os() -> str:
    return {"win32": "windows", "darwin": "osx"}.get(sys.platform, "linux")


def host_arch() -> str:
    machine = host_platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine


def rules_allow(rules: list[dict] | None, features: dict[str, bool] | None = None,
                os_name: str | None = None, arch: str | None = None) -> bool:
    """Mojang's library/argument rules: the last matching rule decides; no rules means allowed."""
    if not rules:
        return True
    os_name, arch = os_name or host_os(), arch or host_arch()
    allowed = False
    for rule in rules:
        os_rule = rule.get("os") or {}
        matched = ("name" not in os_rule or os_rule["name"] == os_name) \
            and ("arch" not in os_rule or os_rule["arch"] == arch) \
            and all(bool((features or {}).get(name)) == value for name, value in (rule.get("features") or {}).items())
        if matched:
            allowed = rule["action"] == "allow"
    return allowed


def maven_path(name: str) -> str:
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = f"-{parts[3]}" if len(parts) > 3 else ""
    return f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{classifier}.jar"


def library_key(name: str) -> str:
    """group:artifact[:classifier]: the identity used for child-wins de-duplication (version dropped)."""
    parts = name.split(":")
    return ":".join(parts[:2] + parts[3:])


def offline_uuid(name: str) -> str:
    raw = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())  # noqa: S324 - Mojang's offline scheme
    raw[6] = (raw[6] & 0x0F) | 0x30
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw)).hex


@dataclass
class Library:
    name: str
    path: str  # Maven path
    url: str
    source: str  # "fabric" or "mojang"
    sha1: str | None = None
    sha256: str | None = None
    size: int | None = None
    file: Path | None = None


def plan_libraries(profile: dict, version: dict, target: dict, os_name: str | None = None,
                   arch: str | None = None) -> list[Library]:
    """Fabric profile libraries first, then vanilla, keeping the first of each group:artifact[:classifier].

    Fabric libraries take their SHA-256 from the client target's pins, never from the fetched profile.
    """
    pins = {library["path"]: library["sha256"] for library in target["loader_libraries"]}
    seen: set[str] = set()
    planned: list[Library] = []
    for entry in profile["libraries"]:
        key = library_key(entry["name"])
        if key in seen:
            continue
        seen.add(key)
        path = maven_path(entry["name"])
        if path not in pins:
            raise AcquisitionError(f"Fabric library {path} is not pinned for this client row")
        planned.append(Library(entry["name"], path, entry["url"].rstrip("/") + "/" + path, "fabric",
                               sha256=pins[path], size=entry.get("size")))
    for entry in version["libraries"]:
        artifact = (entry.get("downloads") or {}).get("artifact")
        if not artifact or not rules_allow(entry.get("rules"), os_name=os_name, arch=arch):
            continue
        key = library_key(entry["name"])
        if key in seen:
            continue
        seen.add(key)
        planned.append(Library(entry["name"], artifact["path"], artifact["url"], "mojang", sha1=artifact["sha1"],
                               size=artifact["size"]))
    for library in planned:
        if not safe_library_path(library.path):
            raise AcquisitionError(f"unsafe library path {library.path!r}")
    return planned


def check_profile(profile: dict, target: dict, minecraft: str) -> list[str]:
    """Problems when a fetched Fabric launch profile is not the one this row pins."""
    problems = []
    if profile.get("id") != target["profile_id"] or profile.get("inheritsFrom") != minecraft:
        problems.append(f"profile is {profile.get('id')} for {profile.get('inheritsFrom')}, expected {target['profile_id']}")
    if profile.get("mainClass") != target["loader_main_class"]:
        problems.append(f"main class {profile.get('mainClass')!r}, pinned {target['loader_main_class']!r}")
    fetched = sorted(maven_path(entry["name"]) for entry in profile.get("libraries", []))
    pinned = sorted(library["path"] for library in target["loader_libraries"])
    if fetched != pinned:
        problems.append(f"library set {fetched} differs from the pinned {pinned}")
    return problems


def pack_info(client_jar: Path) -> dict[str, Any]:
    """World and data-pack versions from the client jar's own version.json."""
    with zipfile.ZipFile(fs_path(client_jar)) as jar:
        info = json.loads(jar.read("version.json"))
    return {"id": info["id"], "world_version": info["world_version"], "pack_version": info["pack_version"]}


def quote_arg(arg: str) -> str:
    """One argument for a java @argfile: quoted, with backslashes and quotes escaped."""
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass
class ClientFiles:
    version: dict
    profile: dict
    client_jar: Path
    libraries: list[Library]
    assets_root: Path
    asset_index: str
    pack: dict[str, Any]
    record: dict[str, Any] = field(default_factory=dict)


class ClientAcquirer:
    """Fetches and verifies everything one client row launches, into the runner cache."""

    def __init__(self, manifest: Manifest, selection: Selection, cache_dir: Path, http: HttpLimits,
                 deadline: float | None, say: Callable[[str], None], assets_from: Path | None = None):
        self.manifest = manifest
        self.target = selection.target
        self.minecraft = selection.minecraft
        self.cache_dir = cache_dir
        self.client_cache = cache_dir / "client"
        self.http = http
        self.deadline = deadline
        self.say = say
        self.assets_from = assets_from

    def pinned(self, url: str, filename: str, **hashes: Any) -> Path:
        return fetch_pinned(url, filename, self.cache_dir, self.manifest.user_agent, self.http,
                            deadline=self.deadline, **hashes).path

    def version_json(self) -> dict:
        path = self.pinned(self.target["version_json_url"], f"{self.minecraft}.json",
                           sha1=self.target["version_json_sha1"])
        version = json.loads(path.read_text(encoding="utf-8"))
        client = version["downloads"]["client"]
        if (client["sha1"], client["size"]) != (self.target["minecraft_client_sha1"], self.target["minecraft_client_size"]):
            raise AcquisitionError("the pinned version JSON names a different client jar than the target pins")
        return version

    def fabric_profile(self) -> tuple[dict, str]:
        """The Fabric launch profile, accepted only when it matches the row's pins; cached once accepted."""
        cached = contained(self.client_cache, "meta", f"{self.target['profile_id']}.json")
        if cached.is_file():
            raw = cached.read_bytes()
        else:
            raw = json.dumps(get_json(self.target["profile_url"], self.manifest.user_agent, self.http)).encode("utf-8")
        profile = json.loads(raw.decode("utf-8"))
        problems = check_profile(profile, self.target, self.minecraft)
        if problems:
            cached.unlink(missing_ok=True)
            raise AcquisitionError(f"Fabric profile rejected: {'; '.join(problems)}")
        if not cached.is_file():
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(raw)
        return profile, hashlib.sha256(raw).hexdigest()

    def libraries(self, profile: dict, version: dict) -> list[Library]:
        planned = plan_libraries(profile, version, self.target)
        for library in planned:
            hashes = {"sha256": library.sha256} if library.sha256 else {"sha1": library.sha1}
            library.file = self.pinned(library.url, library.path.rsplit("/", 1)[-1], size=library.size, **hashes)
        return planned

    def client_jar(self, version: dict) -> Path:
        client = version["downloads"]["client"]
        return self.pinned(client["url"], f"minecraft-{self.minecraft}-client.jar",
                           sha1=self.target["minecraft_client_sha1"], size=self.target["minecraft_client_size"])

    def server_jar(self, version: dict) -> tuple[Path, str]:
        server = version["downloads"]["server"]
        return (self.pinned(server["url"], f"minecraft-{self.minecraft}-server.jar", sha1=server["sha1"],
                            size=server["size"]), server["sha1"])

    def client_mappings(self, version: dict) -> Path:
        mappings = version["downloads"]["client_mappings"]
        return self.pinned(mappings["url"], f"client-{self.minecraft}.txt", sha1=mappings["sha1"],
                           size=mappings["size"])

    def assets(self, version: dict) -> tuple[Path, str, dict[str, int]]:
        """Every object of the pinned asset index, SHA-1 verified, in launcher layout."""
        info = version["assetIndex"]
        root = self.client_cache / "assets"
        index_path = contained(root, "indexes", f"{info['id']}.json")
        if not (index_path.is_file() and hash_file(index_path, "sha1") == info["sha1"]):
            download(info["url"], index_path, self.manifest.user_agent, self.http, max_bytes=info.get("size"),
                     deadline=self.deadline)
            verify_file(index_path, sha1=info["sha1"], size=info.get("size"))
        objects = {entry["hash"]: entry["size"] for entry in json.loads(index_path.read_text(encoding="utf-8"))["objects"].values()}
        invalid = [sha1 for sha1 in objects if not re.fullmatch(r"[0-9a-f]{40}", sha1)]
        if invalid:
            raise AcquisitionError(f"asset index lists invalid object hashes {invalid[:3]}")
        stats = {"objects": len(objects), "present": 0, "copied": 0, "downloaded": 0}
        source_root = (self.assets_from / "objects") if self.assets_from else None
        # Resolved once: every object name is 40 hex digits, so a plain join cannot leave this folder,
        # and concurrent resolve() calls racing directory creation are avoided.
        objects_dir = contained(root, "objects")

        def ensure(item: tuple[str, int]) -> str:
            sha1, size = item
            target = objects_dir / sha1[:2] / sha1
            if target.is_file() and target.stat().st_size == size and hash_file(target, "sha1") == sha1:
                return "present"
            source = source_root / sha1[:2] / sha1 if source_root else None
            if source is not None and source.is_file() and source.stat().st_size == size \
                    and hash_file(source, "sha1") == sha1:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                kind = "copied"
            else:
                download(f"{RESOURCES}/{sha1[:2]}/{sha1}", target, self.manifest.user_agent, self.http,
                         max_bytes=size, deadline=self.deadline)
                kind = "downloaded"
            verify_file(target, sha1=sha1, size=size)
            return kind

        with ThreadPoolExecutor(8) as pool:
            for kind in pool.map(ensure, objects.items()):
                stats[kind] += 1
        return root, info["id"], stats

    def acquire(self) -> ClientFiles:
        version = self.version_json()
        profile, profile_sha256 = self.fabric_profile()
        libraries = self.libraries(profile, version)
        client_jar = self.client_jar(version)
        self.say(f"client {self.minecraft}: {len(libraries)} libraries and the client jar verified; checking assets")
        assets_root, asset_index, stats = self.assets(version)
        record = {
            "version_json_sha1": self.target["version_json_sha1"],
            "client_jar": {"path": str(client_jar), "sha1": hash_file(client_jar, "sha1"),
                           "pinned_sha1": self.target["minecraft_client_sha1"]},
            "fabric_profile": {"id": profile["id"], "sha256": profile_sha256, "main_class": profile["mainClass"]},
            "libraries": [{"name": lib.name, "path": lib.path, "source": lib.source,
                           "sha256": hash_file(lib.file)} for lib in libraries],
            "assets": {"index": asset_index, "from": str(self.assets_from) if self.assets_from else "download", **stats},
        }
        return ClientFiles(version, profile, client_jar, libraries, assets_root, asset_index, pack_info(client_jar),
                           record)


def extract_natives(files: ClientFiles, destination: Path) -> list[str]:
    """Windows DLLs from the natives jars, the way a launcher unpacks them, into a run-local folder."""
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for library in files.libraries:
        if not library.name.endswith(":natives-windows") or library.file is None:
            continue
        with zipfile.ZipFile(fs_path(library.file)) as jar:
            for member in jar.namelist():
                if member.lower().endswith(".dll"):
                    target = contained(destination, Path(member).name)
                    target.write_bytes(jar.read(member))
                    written.append(target.name)
    return sorted(written)


def launch_arguments(files: ClientFiles, game_dir: Path, natives: Path, quick_play: str | None,
                     extra_jvm: list[str] | None = None, memory: str = "2G") -> list[str]:
    """The full java argument list: JVM options, the Fabric main class, then game arguments."""
    version, profile = files.version, files.profile
    classpath = [str(library.file) for library in files.libraries] + [str(files.client_jar)]
    features = {"has_custom_resolution": True, "has_quick_plays_support": True,
                "is_quick_play_singleplayer": quick_play is not None}
    values = {
        "auth_player_name": PLAYER, "version_name": profile["id"], "game_directory": str(game_dir),
        "assets_root": str(files.assets_root), "assets_index_name": files.asset_index,
        "auth_uuid": offline_uuid(PLAYER), "auth_access_token": "0", "clientid": "0", "auth_xuid": "0",
        "user_type": "msa", "version_type": "release", "resolution_width": str(WINDOW_SIZE[0]),
        "resolution_height": str(WINDOW_SIZE[1]), "quickPlayPath": str(game_dir / "quickPlay" / "log.json"),
        "quickPlaySingleplayer": quick_play or "", "natives_directory": str(natives),
        "launcher_name": LAUNCHER_NAME, "launcher_version": "2", "classpath": os.pathsep.join(classpath),
    }

    def expand(items: list) -> list[str]:
        out: list[str] = []
        for item in items:
            if isinstance(item, str):
                out.append(item)
            elif rules_allow(item.get("rules"), features):
                value = item["value"]
                out.extend(value if isinstance(value, list) else [value])
        return [re.sub(r"\$\{(\w+)\}", lambda match: values.get(match.group(1), match.group(0)), arg) for arg in out]

    jvm = [f"-Xmx{memory}", "-Xms512M", *(extra_jvm or []), *expand(version["arguments"]["jvm"]),
           *profile["arguments"]["jvm"]]
    return [*jvm, profile["mainClass"], *expand(version["arguments"]["game"]), *profile["arguments"]["game"]]


def write_argfile(arguments: list[str], path: Path) -> list[str]:
    """Write the @argfile; return the arguments with the class path elided, for the result."""
    path.write_text("\n".join(quote_arg(arg) for arg in arguments) + "\n", encoding="utf-8")
    redacted = list(arguments)
    if "-cp" in redacted:
        redacted[redacted.index("-cp") + 1] = "<classpath: see environment.client.libraries>"
    return redacted
