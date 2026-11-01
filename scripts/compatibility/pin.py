#!/usr/bin/env python3
"""Resolve official build/loader pins and hashes into the compatibility lock manifest.

This is a maintainer tool, not part of a test run. It queries the PaperMC downloads service,
Fabric meta, Mojang's version manifest, and Modrinth, then prints (or with --write, saves) a
manifest whose targets carry exact builds, URLs, sizes, and hashes. Review the diff before
committing it: a run only ever uses what the committed manifest pins.

  pin.py targets         server builds, Loader launchers, client version JSON and Loader libraries
  pin.py dependencies    the dependency jars install profiles name (Fabric API, Mod Menu)

Existing pins are kept unless --refresh is given. A Paper target keeps an explicit paper_build;
without one, the newest STABLE build for that exact version is chosen, or the newest build of any
channel when no STABLE build exists (recorded with its channel for review). A Minecraft version
the PaperMC service does not know is written as an "unavailable" target, never a neighbor.

With no mode, both run in that order. A dependency pin is the newest Modrinth release of that
project that lists the row's exact Minecraft version for Fabric. The download must match Modrinth's SHA-512 and SHA-1, its own
fabric.mod.json must name the expected mod and version, and its declared minecraft and
fabricloader predicates must accept the row and its pinned Loader; otherwise nothing is pinned.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import urllib.parse
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from egc_compat import fabric_version  # noqa: E402
from egc_compat.acquire import AcquisitionError, HttpLimits, _open, get_json  # noqa: E402
from egc_compat.manifest import MINECRAFT_VERSIONS, SIDES, validate  # noqa: E402

PAPER_API = "https://fill.papermc.io/v3/projects/paper/versions"
FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_MAVEN = "https://maven.fabricmc.net"
MODRINTH_API = "https://api.modrinth.com/v2"
MAX_LAUNCHER_BYTES = 8 * 1024 * 1024
MAX_DEPENDENCY_BYTES = 32 * 1024 * 1024
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
# Modrinth project of each mod ID an install profile may name.
MODRINTH_PROJECTS = {"fabric-api": "P7dR8mSH", "modmenu": "mOgUt4GM"}
PAPER_RUNNABLE_FIELDS = ("paper_build", "channel", "build_api_url", "download_url", "size", "sha256")
FIELD_ORDER = (
    "artifact_id", "unavailable", "server_minecraft", "server_basis", "adapter", "java_major", "client_protocol",
    *PAPER_RUNNABLE_FIELDS,
    "loader", "installer", "launcher_url", "launcher_size", "launcher_sha256", "loader_main_class",
    "profile_id", "profile_url", "dependencies", "version_json_url", "version_json_sha1",
    "minecraft_server_url", "minecraft_server_sha1", "minecraft_server_size",
    "minecraft_client_sha1", "minecraft_client_size", "extended_checks_required", "loader_libraries",
)
DEPENDENCY_FIELD_ORDER = (
    "platform", "kind", "filename", "url", "size", "sha256", "sha512", "sha1", "descriptor", "declares",
    "modrinth_project_id", "modrinth_version_id", "date_published", "game_versions", "pinned_utc",
)


def field_rank(field: str) -> tuple[int, str]:
    return (FIELD_ORDER.index(field) if field in FIELD_ORDER else len(FIELD_ORDER), field)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# The pinned minecraft-data aliases these releases to 1.21 (protocol 767), but 1.21.2 shares
# protocol 768 with 1.21.3 and 1.21.7 shares protocol 772 with 1.21.8. The runner records the
# negotiated protocol, and a mismatch shows up as a failed bot login, never as a pass.
CLIENT_PROTOCOL_VERSION = {"1.21.2": "1.21.3", "1.21.7": "1.21.8"}


# Releases whose players are served by another release's Paper build. PaperMC publishes no 1.21.2
# server: 1.21.3 is the same-protocol (768) hotfix, and 1.21.2 players join Paper 1.21.3. The
# claim under test is Minecraft 1.21.2 compliance, so that row runs Paper 1.21.3 with a protocol
# 768 client. Any other missing Paper version is still recorded as unavailable, never a neighbor.
PAPER_SERVER_FOR = {
    "1.21.2": ("1.21.3", "PaperMC publishes no 1.21.2 server; Minecraft 1.21.2 players join Paper 1.21.3, "
                         "the same-protocol (768) hotfix, so this row tests the plugin on Paper 1.21.3 "
                         "with a protocol 768 client."),
}


EXTENDED_CLIENT_ROWS = ("1.21", "1.21.5", "1.21.11")


def adapter_for(minecraft: str) -> str:
    return "snake-case-gamerules" if minecraft == "1.21.11" else "camel-case-gamerules"


class Resolver:
    def __init__(self, user_agent: str, limits: HttpLimits):
        self.user_agent = user_agent
        self.limits = limits
        self._mojang: dict[str, dict] = {}
        self._mojang_index: dict[str, dict] | None = None

    def mojang_entry(self, minecraft: str) -> dict:
        """The official version manifest's entry (URL and SHA-1 of the version JSON)."""
        if self._mojang_index is None:
            index = get_json(MOJANG_MANIFEST, self.user_agent, self.limits)
            self._mojang_index = {entry["id"]: entry for entry in index["versions"]}
        return self._mojang_index[minecraft]

    def mojang(self, minecraft: str) -> dict:
        """The version JSON, checked against the SHA-1 the official version manifest lists for it."""
        if minecraft not in self._mojang:
            entry = self.mojang_entry(minecraft)
            response = _open(entry["url"], self.user_agent, self.limits)
            try:
                body = response.read(MAX_LAUNCHER_BYTES + 1)
            finally:
                response.close()
            if hashlib.sha1(body).hexdigest() != entry["sha1"]:
                raise AcquisitionError(f"version JSON for {minecraft} does not match its listed SHA-1")
            self._mojang[minecraft] = json.loads(body.decode("utf-8"))
        return self._mojang[minecraft]

    def paper(self, minecraft: str, target: dict, notes: list[str]) -> dict:
        server_minecraft, basis = PAPER_SERVER_FOR.get(minecraft, (minecraft, None))
        if basis:
            target["server_minecraft"] = server_minecraft
            target["server_basis"] = basis
            notes.append(f"paper/server/{minecraft}: runs on the Paper {server_minecraft} server ({basis})")
        builds_url = f"{PAPER_API}/{server_minecraft}/builds"
        try:
            builds = get_json(builds_url, self.user_agent, self.limits)
        except AcquisitionError as error:
            if "version_not_found" not in str(error):
                raise
            notes.append(f"paper/server/{minecraft}: PaperMC has no such version; marked unavailable")
            for field in PAPER_RUNNABLE_FIELDS + ("adapter", "java_major", "client_protocol",
                                                   "minecraft_server_url", "minecraft_server_sha1",
                                                   "minecraft_server_size"):
                target.pop(field, None)
            target["unavailable"] = {
                "reason": f"The PaperMC downloads service reports version_not_found for Minecraft {minecraft}; "
                          "no official Paper server build exists for this exact version.",
                "evidence_url": builds_url,
                "checked_utc": now_utc(),
            }
            return target
        if not isinstance(builds, list) or not builds:
            raise AcquisitionError(f"no Paper builds listed for {minecraft}")
        by_id = {build["id"]: build for build in builds}
        build_id = target.get("paper_build")
        if build_id is None:
            stable = [build for build in builds if build["channel"] == "STABLE"]
            chosen = stable[0] if stable else builds[0]
            if not stable:
                notes.append(f"paper/server/{minecraft}: no STABLE build; pinned newest {chosen['channel']} "
                             f"build {chosen['id']} for review")
        else:
            if build_id not in by_id:
                raise AcquisitionError(f"Paper {minecraft} build {build_id} is not listed")
            chosen = by_id[build_id]
            newest_stable = next((build for build in builds if build["channel"] == "STABLE"), None)
            if newest_stable and newest_stable["id"] != build_id:
                notes.append(f"paper/server/{minecraft}: kept pinned build {build_id} ({chosen['channel']}); "
                             f"newest STABLE is {newest_stable['id']}")
        download = chosen["downloads"]["server:default"]
        version_info = get_json(f"{PAPER_API}/{server_minecraft}", self.user_agent, self.limits)
        target.pop("unavailable", None)
        target.update({
            "paper_build": chosen["id"],
            "channel": chosen["channel"],
            "build_api_url": f"{builds_url}/{chosen['id']}",
            "download_url": download["url"],
            "size": download["size"],
            "sha256": download["checksums"]["sha256"],
            "java_major": max(21, int(version_info["version"]["java"]["version"]["minimum"])),
        })
        self._server_jar(minecraft, target, server_minecraft)
        return target

    def _server_jar(self, minecraft: str, target: dict, server_minecraft: str | None = None) -> None:
        """Pin the official server jar the row's server runs; the client protocol follows the row."""
        info = self.mojang(server_minecraft or minecraft)
        server = info["downloads"]["server"]
        target.update({
            "minecraft_server_url": server["url"],
            "minecraft_server_sha1": server["sha1"],
            "minecraft_server_size": server["size"],
        })
        target["java_major"] = max(target.get("java_major", 0), int(info["javaVersion"]["majorVersion"]))
        target.setdefault("client_protocol", CLIENT_PROTOCOL_VERSION.get(minecraft, minecraft))
        target.setdefault("adapter", adapter_for(minecraft))

    def fabric_launcher(self, url: str, minecraft: str, loader: str) -> tuple[int, str]:
        """Download and check the launcher before pinning it.

        It must be a jar whose ``install.properties`` names this exact game and Loader, so an
        error page, a wrong version, or a mixed-up hash can never become a pin.
        """
        response = _open(url, self.user_agent, self.limits)
        try:
            body = response.read(MAX_LAUNCHER_BYTES + 1)
        finally:
            response.close()
        if len(body) > MAX_LAUNCHER_BYTES:
            raise AcquisitionError(f"{url} is larger than a Fabric server launcher")
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as jar:
                text = jar.read("install.properties").decode("utf-8")
        except (zipfile.BadZipFile, KeyError) as error:
            raise AcquisitionError(f"{url} is not a Fabric server launcher: {error}") from error
        properties = {key: value for key, _, value in (line.partition("=") for line in text.splitlines()) if value}
        expected = {"game-version": minecraft, "fabric-loader-version": loader}
        if any(properties.get(key) != value for key, value in expected.items()):
            raise AcquisitionError(f"{url} launcher is for {properties}, expected {expected}")
        return len(body), hashlib.sha256(body).hexdigest()

    def maven_sha256(self, base_url: str, path: str) -> str:
        """The checksum the official Fabric Maven publishes, for libraries meta lists without one."""
        if base_url.rstrip("/") != FABRIC_MAVEN:
            raise AcquisitionError(f"no SHA-256 for {path} and its repository {base_url!r} is not {FABRIC_MAVEN}")
        response = _open(f"{FABRIC_MAVEN}/{path}.sha256", self.user_agent, self.limits)
        try:
            digest = response.read(200).decode("ascii").split()[0].lower()
        finally:
            response.close()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AcquisitionError(f"unexpected checksum for {path}: {digest!r}")
        return digest

    def fabric_loader_check(self, minecraft: str, loader: str) -> None:
        info = get_json(f"{FABRIC_META}/{minecraft}/{loader}", self.user_agent, self.limits)
        if info.get("loader", {}).get("version") != loader:
            raise AcquisitionError(f"Fabric meta does not list loader {loader} for {minecraft}")

    def loader_libraries(self, profile: dict) -> list[dict]:
        """Official SHA-256 for every library a Fabric meta launch profile lists.

        Meta omits the hash for some libraries (intermediary, the loader itself); those come from
        the checksum file Fabric Maven publishes beside each jar.
        """
        libraries = []
        for library in profile["libraries"]:
            group, artifact, version = library["name"].split(":")[:3]
            path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.jar"
            library_sha256 = library.get("sha256") or self.maven_sha256(library.get("url", ""), path)
            libraries.append({"path": path, "sha256": library_sha256})
        return libraries

    def fabric_server(self, minecraft: str, target: dict) -> dict:
        loader = target["loader"]
        installer = target["installer"]
        self.fabric_loader_check(minecraft, loader)
        launcher_url = f"{FABRIC_META}/{minecraft}/{loader}/{installer}/server/jar"
        launcher_size, launcher_sha256 = self.fabric_launcher(launcher_url, minecraft, loader)
        # The runner checks the installed libraries against these (and the Mojang jar's own list)
        # before reusing them.
        profile = get_json(f"{FABRIC_META}/{minecraft}/{loader}/server/json", self.user_agent, self.limits)
        target.update({
            "launcher_url": launcher_url,
            "launcher_size": launcher_size,
            "launcher_sha256": launcher_sha256,
            "loader_main_class": profile["mainClass"],
            "loader_libraries": self.loader_libraries(profile),
            "java_major": 21,
        })
        self._server_jar(minecraft, target)
        return target

    def fabric_client(self, minecraft: str, target: dict) -> dict:
        loader = target["loader"]
        self.fabric_loader_check(minecraft, loader)
        entry = self.mojang_entry(minecraft)
        info = self.mojang(minecraft)
        client = info["downloads"]["client"]
        profile_url = f"{FABRIC_META}/{minecraft}/{loader}/profile/json"
        profile = get_json(profile_url, self.user_agent, self.limits)
        if profile.get("id") != f"fabric-loader-{loader}-{minecraft}" or profile.get("inheritsFrom") != minecraft:
            raise AcquisitionError(f"{profile_url} is for {profile.get('id')}, not Loader {loader} on {minecraft}")
        # The version JSON pins the client jar, libraries, asset index, and mappings by SHA-1;
        # the client's Loader libraries get their own pins rather than reusing the server row's.
        target.update({
            "profile_id": profile["id"],
            "profile_url": profile_url,
            "version_json_url": entry["url"],
            "version_json_sha1": entry["sha1"],
            "minecraft_client_sha1": client["sha1"],
            "minecraft_client_size": client["size"],
            "loader_main_class": profile["mainClass"],
            "loader_libraries": self.loader_libraries(profile),
            "java_major": max(21, int(info["javaVersion"]["majorVersion"])),
        })
        target.setdefault("adapter", adapter_for(minecraft))
        # Save/Cancel, persistence, world-transition and monitor checks gate the lower, middle
        # and upper client rows (PRD 10A/10F); a recorded failure rejects any client row.
        target.setdefault("extended_checks_required", minecraft in EXTENDED_CLIENT_ROWS)
        return target

    # Dependencies --------------------------------------------------------------------------
    def modrinth_release(self, mod_id: str, minecraft: str) -> dict:
        """The newest Modrinth release of ``mod_id`` that lists this exact Minecraft version for Fabric."""
        query = urllib.parse.urlencode({"loaders": json.dumps(["fabric"]), "game_versions": json.dumps([minecraft])})
        versions = get_json(f"{MODRINTH_API}/project/{MODRINTH_PROJECTS[mod_id]}/version?{query}",
                            self.user_agent, self.limits)
        return newest_release(versions, minecraft)

    def dependency_artifact(self, mod_id: str, release: dict) -> dict:
        """Download, verify, and describe one Modrinth release file as a dependency artifact."""
        primary = [file for file in release["files"] if file.get("primary")] or release["files"][:1]
        if len(primary) != 1:
            raise AcquisitionError(f"{mod_id} {release['version_number']} has no single primary file")
        file = primary[0]
        response = _open(file["url"], self.user_agent, self.limits)
        try:
            body = response.read(MAX_DEPENDENCY_BYTES + 1)
        finally:
            response.close()
        return describe_dependency(mod_id, release, file, body)


def newest_release(versions: list[dict], minecraft: str) -> dict:
    releases = [version for version in versions if version.get("version_type") == "release"
                and minecraft in version.get("game_versions", []) and "fabric" in version.get("loaders", [])]
    if not releases:
        raise AcquisitionError(f"no Fabric release lists Minecraft {minecraft}")
    return max(releases, key=lambda version: version["date_published"])


def describe_dependency(mod_id: str, release: dict, file: dict, body: bytes) -> dict:
    """A dependency artifact for verified bytes; refuses a jar that is not the release it claims."""
    if len(body) > MAX_DEPENDENCY_BYTES or len(body) != file["size"]:
        raise AcquisitionError(f"{file['url']} is {len(body)} bytes, Modrinth lists {file['size']}")
    if hashlib.sha512(body).hexdigest() != file["hashes"]["sha512"] \
            or hashlib.sha1(body).hexdigest() != file["hashes"]["sha1"]:
        raise AcquisitionError(f"{file['url']} does not match Modrinth's SHA-512 and SHA-1")
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as jar:
            descriptor = json.loads(jar.read("fabric.mod.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError) as error:
        raise AcquisitionError(f"{file['url']} is not a Fabric mod: {error}") from error
    if descriptor.get("id") != mod_id or descriptor.get("version") != release["version_number"]:
        raise AcquisitionError(f"{file['url']} declares {descriptor.get('id')} {descriptor.get('version')}, "
                               f"expected {mod_id} {release['version_number']}")
    depends = descriptor.get("depends") or {}
    return {
        "platform": "fabric",
        "kind": "dependency",
        "filename": file["filename"],
        "url": file["url"],
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "sha512": file["hashes"]["sha512"],
        "sha1": file["hashes"]["sha1"],
        "descriptor": {"id": mod_id, "version": descriptor["version"]},
        "declares": {"minecraft": depends.get("minecraft"), "fabricloader": depends.get("fabricloader")},
        "modrinth_project_id": release["project_id"],
        "modrinth_version_id": release["id"],
        "date_published": release["date_published"],
        "game_versions": release["game_versions"],
        "pinned_utc": now_utc(),
    }


def dependency_artifact_id(mod_id: str, version: str) -> str:
    return f"{mod_id}-{version.replace('+', '-')}"


def accepts(artifact: dict, minecraft: str, loader: str) -> list[str]:
    """Problems when the jar's own predicates exclude the row or its pinned Loader."""
    problems = []
    for key, version in (("minecraft", minecraft), ("fabricloader", loader)):
        if not fabric_version.matches(artifact["declares"][key], version):
            problems.append(f"{artifact['descriptor']['id']} {artifact['descriptor']['version']} declares {key} "
                            f"{artifact['declares'][key]!r}, which excludes {version}")
    return problems


def complete_dependency(artifact: dict | None) -> bool:
    return isinstance(artifact, dict) and all(key in artifact for key in ("sha512", "declares", "modrinth_project_id"))


class DependencyPinner:
    """Pins each dependency a Fabric target's profiles install; one Modrinth query per mod and row."""

    def __init__(self, data: dict, resolver: Resolver, refresh: bool, notes: list[str]):
        self.data = data
        self.artifacts = data["artifacts"]
        self.resolver = resolver
        self.refresh = refresh
        self.notes = notes
        self.releases: dict[tuple[str, str], dict] = {}

    def needed(self, side: str) -> list[str]:
        return list(dict.fromkeys(mod_id for profile in self.data["profiles"]["fabric"].values()
                                  if side in profile["sides"] for mod_id in profile["dependencies"]))

    def pin(self, key: str, target: dict, mod_id: str) -> str:
        minecraft = key.split("/")[2]
        current = target.get("dependencies", {}).get(mod_id)
        if not self.refresh and complete_dependency(self.artifacts.get(current or "")):
            return current
        print(f"resolving {mod_id} for {key}", file=sys.stderr)
        if (mod_id, minecraft) not in self.releases:
            self.releases[(mod_id, minecraft)] = self.resolver.modrinth_release(mod_id, minecraft)
        release = self.releases[(mod_id, minecraft)]
        artifact_id = dependency_artifact_id(mod_id, release["version_number"])
        if self.refresh or not complete_dependency(self.artifacts.get(artifact_id)):
            self.artifacts[artifact_id] = self.resolver.dependency_artifact(mod_id, release)
        problems = accepts(self.artifacts[artifact_id], minecraft, target["loader"])
        if problems:
            raise AcquisitionError(f"{key}: refusing {artifact_id}: {'; '.join(problems)}")
        if current != artifact_id:
            self.notes.append(f"{key}: {mod_id} pinned to {artifact_id}")
        return artifact_id

    def run(self) -> None:
        for key, target in self.data["targets"].items():
            platform, side, _minecraft = key.split("/")
            if platform == "fabric" and "unavailable" not in target:
                target["dependencies"] = {mod_id: self.pin(key, target, mod_id) for mod_id in self.needed(side)}
        self.prune()

    def prune(self) -> None:
        """Drop dependency pins no target names, and keep each pin's fields in a stable order."""
        used = {artifact_id for target in self.data["targets"].values()
                for artifact_id in (target.get("dependencies") or {}).values()}
        for artifact_id in [aid for aid, artifact in self.artifacts.items() if artifact.get("kind") == "dependency"]:
            if artifact_id not in used:
                self.notes.append(f"dropped unused dependency pin {artifact_id}")
                del self.artifacts[artifact_id]
                continue
            artifact = self.artifacts[artifact_id]
            self.artifacts[artifact_id] = {field: artifact[field] for field in sorted(
                artifact, key=lambda f: DEPENDENCY_FIELD_ORDER.index(f) if f in DEPENDENCY_FIELD_ORDER else 99)}


def needs_pins(key: str, target: dict) -> bool:
    platform, side, _ = key.split("/")
    if "unavailable" in target:
        return False
    if platform == "paper":
        fields = PAPER_RUNNABLE_FIELDS + ("minecraft_server_sha1",)
    elif side == "server":
        fields = ("launcher_url", "launcher_sha256", "minecraft_server_sha1", "loader_libraries")
    else:
        fields = ("profile_url", "version_json_sha1", "minecraft_client_sha1", "loader_libraries",
                  "extended_checks_required")
    return any(target.get(field) is None for field in fields)


def pin_targets(data: dict, resolver: Resolver, args: argparse.Namespace, notes: list[str]) -> None:
    targets = data.setdefault("targets", {})
    ordered: dict[str, dict] = {}
    for platform in ("paper", "fabric"):
        for side in SIDES[platform]:
            for minecraft in MINECRAFT_VERSIONS:
                key = f"{platform}/{side}/{minecraft}"
                target = dict(targets.get(key, {}))
                target.setdefault("artifact_id", args.default_paper_artifact if platform == "paper"
                                  else args.default_fabric_artifact)
                if platform == "fabric":
                    target.setdefault("loader", args.loader)
                    if side == "server":
                        target.setdefault("installer", args.installer)
                if args.refresh or needs_pins(key, target):
                    print(f"resolving {key}", file=sys.stderr)
                    if args.refresh and platform == "paper" and "unavailable" in target:
                        target.pop("unavailable")
                    if platform == "paper":
                        resolver.paper(minecraft, target, notes)
                    elif side == "server":
                        resolver.fabric_server(minecraft, target)
                    else:
                        resolver.fabric_client(minecraft, target)
                ordered[key] = {field: target[field] for field in sorted(target, key=field_rank)}
    data["targets"] = ordered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", nargs="?", choices=("all", "targets", "dependencies"), default="all",
                        help="what to resolve (default: targets, then dependencies)")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parent / "manifest.json")
    parser.add_argument("--write", action="store_true", help="save the resolved manifest in place")
    parser.add_argument("--refresh", action="store_true", help="re-resolve already pinned targets too")
    parser.add_argument("--loader", default="0.19.5", help="Fabric Loader for targets that do not name one")
    parser.add_argument("--installer", default="1.1.2", help="Fabric installer for server targets that do not name one")
    parser.add_argument("--default-paper-artifact", default="paper-1.2.0")
    parser.add_argument("--default-fabric-artifact", default="fabric-1.2.0")
    args = parser.parse_args()

    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    resolver = Resolver(data["user_agent"], HttpLimits())
    notes: list[str] = []
    if args.mode in ("all", "targets"):
        pin_targets(data, resolver, args, notes)
    if args.mode in ("all", "dependencies"):
        DependencyPinner(data, resolver, args.refresh, notes).run()
    errors = validate(data)
    text = json.dumps(data, indent=2) + "\n"
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"invalid: {error}", file=sys.stderr)
        return 1
    if args.write:
        args.manifest.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.manifest}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
