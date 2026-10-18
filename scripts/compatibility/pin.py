#!/usr/bin/env python3
"""Resolve official build/loader pins and hashes into the compatibility lock manifest.

This is a maintainer tool, not part of a test run. It queries the PaperMC downloads service,
Fabric meta, and Mojang's version manifest, then prints (or with --write, saves) a manifest whose
targets carry exact builds, URLs, sizes, and hashes. Review the diff before committing it: a run
only ever uses what the committed manifest pins.

Existing pins are kept unless --refresh is given. A Paper target keeps an explicit paper_build;
without one, the newest STABLE build for that exact version is chosen, or the newest build of any
channel when no STABLE build exists (recorded with its channel for review). A Minecraft version
the PaperMC service does not know is written as an "unavailable" target, never a neighbor.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from egc_compat.acquire import AcquisitionError, HttpLimits, _open, get_json  # noqa: E402
from egc_compat.manifest import MINECRAFT_VERSIONS, SIDES, validate  # noqa: E402

PAPER_API = "https://fill.papermc.io/v3/projects/paper/versions"
FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_MAVEN = "https://maven.fabricmc.net"
MAX_LAUNCHER_BYTES = 8 * 1024 * 1024
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
PAPER_RUNNABLE_FIELDS = ("paper_build", "channel", "build_api_url", "download_url", "size", "sha256")
FIELD_ORDER = (
    "artifact_id", "unavailable", "server_minecraft", "server_basis", "adapter", "java_major", "client_protocol",
    *PAPER_RUNNABLE_FIELDS,
    "loader", "installer", "launcher_url", "launcher_size", "launcher_sha256", "loader_main_class",
    "profile_id", "profile_url", "mod_menu_artifact_id",
    "minecraft_server_url", "minecraft_server_sha1", "minecraft_server_size",
    "minecraft_client_sha1", "minecraft_client_size", "extended_checks_required", "loader_libraries",
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
        self._mojang_index: dict[str, str] | None = None

    def mojang(self, minecraft: str) -> dict:
        if minecraft not in self._mojang:
            if self._mojang_index is None:
                index = get_json(MOJANG_MANIFEST, self.user_agent, self.limits)
                self._mojang_index = {entry["id"]: entry["url"] for entry in index["versions"]}
            self._mojang[minecraft] = get_json(self._mojang_index[minecraft], self.user_agent, self.limits)
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

    def fabric_server(self, minecraft: str, target: dict) -> dict:
        loader = target["loader"]
        installer = target["installer"]
        self.fabric_loader_check(minecraft, loader)
        launcher_url = f"{FABRIC_META}/{minecraft}/{loader}/{installer}/server/jar"
        launcher_size, launcher_sha256 = self.fabric_launcher(launcher_url, minecraft, loader)
        # Official SHA-256 for every loader library the launcher installs; the runner checks the
        # installed libraries against these (and the Mojang jar's own list) before reusing them.
        profile = get_json(f"{FABRIC_META}/{minecraft}/{loader}/server/json", self.user_agent, self.limits)
        libraries = []
        for library in profile["libraries"]:
            group, artifact, version = library["name"].split(":")[:3]
            path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.jar"
            library_sha256 = library.get("sha256") or self.maven_sha256(library.get("url", ""), path)
            libraries.append({"path": path, "sha256": library_sha256})
        target.update({
            "launcher_url": launcher_url,
            "launcher_size": launcher_size,
            "launcher_sha256": launcher_sha256,
            "loader_main_class": profile["mainClass"],
            "loader_libraries": libraries,
            "java_major": 21,
        })
        self._server_jar(minecraft, target)
        return target

    def fabric_client(self, minecraft: str, target: dict) -> dict:
        loader = target["loader"]
        self.fabric_loader_check(minecraft, loader)
        info = self.mojang(minecraft)
        client = info["downloads"]["client"]
        target.update({
            "profile_id": f"fabric-loader-{loader}-{minecraft}",
            "profile_url": f"{FABRIC_META}/{minecraft}/{loader}/profile/json",
            "minecraft_client_sha1": client["sha1"],
            "minecraft_client_size": client["size"],
            "java_major": max(21, int(info["javaVersion"]["majorVersion"])),
        })
        target.setdefault("adapter", adapter_for(minecraft))
        target.setdefault("mod_menu_artifact_id", None)
        # Save/Cancel, persistence, world-transition and monitor checks gate the lower, middle
        # and upper client rows (PRD 10A/10F); a recorded failure rejects any client row.
        target.setdefault("extended_checks_required", minecraft in EXTENDED_CLIENT_ROWS)
        return target


def needs_pins(key: str, target: dict) -> bool:
    platform, side, _ = key.split("/")
    if "unavailable" in target:
        return False
    if platform == "paper":
        fields = PAPER_RUNNABLE_FIELDS + ("minecraft_server_sha1",)
    elif side == "server":
        fields = ("launcher_url", "launcher_sha256", "minecraft_server_sha1", "loader_libraries")
    else:
        fields = ("profile_url", "minecraft_client_sha1", "extended_checks_required")
    return any(target.get(field) is None for field in fields)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    targets = data.setdefault("targets", {})
    notes: list[str] = []
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
