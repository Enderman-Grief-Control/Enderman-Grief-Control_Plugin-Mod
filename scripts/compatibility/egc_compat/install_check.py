"""Verify server install trees (libraries, launcher output, game jars) against official hashes.

Neither a cached install nor a fresh one is trusted by location. Every file must match a hash
taken from a pinned source:

- Paper: the pinned ``paper.jar`` lists each file Paperclip writes, with its SHA-256, in
  ``META-INF/libraries.list``, ``versions.list``, ``download-context`` and ``patches.list``.
- Fabric: the Mojang server jar (pinned SHA-1) lists its bundled libraries and version jar with
  SHA-256 in ``META-INF/libraries.list`` and ``versions.list``. The manifest pins every loader
  library's official SHA-256 from Fabric meta or Fabric Maven. The launcher's generated launch jar
  is checked structurally: only a manifest and a properties file, its class path limited to the
  verified loader libraries, and the pinned main class.

A file that is not listed, or whose hash differs, is a problem. Missing files are not: Paperclip
deletes patch inputs, and the launcher recreates anything absent from its own official source.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Callable

from .acquire import fs_path, hash_file

FABRIC_LAUNCH_MAIN = "net.fabricmc.loader.impl.launch.server.FabricServerLauncher"


def _bundle_list(jar: zipfile.ZipFile, member: str, prefix: str) -> dict[str, str]:
    try:
        text = jar.read(member).decode("utf-8")
    except KeyError:
        return {}
    expected = {}
    for line in text.splitlines():
        if line.strip():
            digest, _identity, path = line.split("\t")
            expected[f"{prefix}/{path}"] = digest.lower()
    return expected


def expected_paper(paper_jar: Path) -> dict[str, str]:
    with zipfile.ZipFile(fs_path(paper_jar)) as jar:
        expected = _bundle_list(jar, "META-INF/libraries.list", "libraries")
        expected.update(_bundle_list(jar, "META-INF/versions.list", "versions"))
        expected.update(_bundle_list(jar, "META-INF/download-context", "cache"))
        try:
            patches = jar.read("META-INF/patches.list").decode("utf-8")
        except KeyError:
            patches = ""
    for line in patches.splitlines():
        if line.strip():
            kind, original, _patch, output, original_path, _patch_path, output_path = line.split("\t")
            expected[f"{kind}/{original_path}"] = original.lower()
            expected[f"{kind}/{output_path}"] = output.lower()
    return expected


def expected_fabric(mojang_jar: Path, loader_libraries: list[dict[str, str]]) -> dict[str, str]:
    with zipfile.ZipFile(fs_path(mojang_jar)) as jar:
        expected = _bundle_list(jar, "META-INF/libraries.list", "libraries")
        expected.update(_bundle_list(jar, "META-INF/versions.list", "versions"))
    for library in loader_libraries:
        expected[f"libraries/{library['path']}"] = library["sha256"]
    return expected


def fabric_launch_jar_problem(path: Path, loader_libraries: list[dict[str, str]], main_class: str) -> str | None:
    """Structural check for the launcher's generated ``fabric-loader-server-*.jar``."""
    allowed = {f"../../libraries/{library['path']}" for library in loader_libraries}
    with zipfile.ZipFile(fs_path(path)) as jar:
        names = sorted(name for name in jar.namelist() if not name.endswith("/"))
        if names != ["META-INF/MANIFEST.MF", "fabric-server-launch.properties"]:
            return f"unexpected entries {names}"
        manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8").replace("\r\n", "\n").replace("\n ", "")
        properties = jar.read("fabric-server-launch.properties").decode("utf-8")
    fields = dict(line.split(": ", 1) for line in manifest.splitlines() if ": " in line)
    if fields.get("Main-Class") != FABRIC_LAUNCH_MAIN:
        return f"Main-Class {fields.get('Main-Class')!r}"
    class_path = fields.get("Class-Path", "").split()
    outside = [entry for entry in class_path if entry not in allowed]
    if outside or not class_path:
        return f"class path entries outside the verified loader libraries: {outside or 'none listed'}"
    launch = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
    if launch.get("launch.mainClass") != main_class:
        return f"launch.mainClass {launch.get('launch.mainClass')!r}, expected {main_class!r}"
    return None


def check_tree(root: Path, tops: list[str], expected: dict[str, str],
               special: Callable[[str, Path], str | None] | None = None) -> list[str]:
    """Problems for every file under ``root/<top>`` that is unlisted or has a different hash.

    ``special(relative, path)`` handles files verified another way; it returns None when the file
    is not special, "" when it passed, or a problem description.
    """
    problems: list[str] = []
    base = fs_path(root)
    for top in tops:
        directory = base / top
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                problems.append(f"{path.relative_to(base).as_posix()}: symbolic link")
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(base).as_posix()
            outcome = special(relative, path) if special else None
            if outcome is not None:
                if outcome:
                    problems.append(f"{relative}: {outcome}")
                continue
            if relative not in expected:
                problems.append(f"{relative}: not listed by any pinned source")
            elif hash_file(path) != expected[relative]:
                problems.append(f"{relative}: SHA-256 does not match the pinned source")
    return problems


def verify_install(root: Path, platform: str, target: dict[str, Any], server_minecraft: str) -> list[str]:
    """Check an install tree (a workspace or a cached seed) rooted at ``root``."""
    if platform == "paper":
        paper_jar = root / "paper.jar"
        if not fs_path(paper_jar).is_file():
            return ["paper.jar is missing"]
        if hash_file(paper_jar) != target["sha256"]:
            return ["paper.jar does not match the pinned Paper build"]
        return check_tree(root, ["libraries", "versions", "cache"], expected_paper(paper_jar))

    mojang = root / ".fabric" / "server" / f"{server_minecraft}-server.jar"
    if not fs_path(mojang).is_file():
        return [f".fabric/server/{server_minecraft}-server.jar is missing"]
    if hash_file(mojang, "sha1") != target["minecraft_server_sha1"]:
        return [f".fabric/server/{server_minecraft}-server.jar does not match the pinned Mojang SHA-1"]
    libraries = target["loader_libraries"]
    launch_name = f".fabric/server/fabric-loader-server-{target['loader']}-minecraft-{server_minecraft}.jar"

    def special(relative: str, path: Path) -> str | None:
        if relative == f".fabric/server/{server_minecraft}-server.jar":
            return ""
        if relative == launch_name:
            return fabric_launch_jar_problem(path, libraries, target["loader_main_class"]) or ""
        return None

    return check_tree(root, [".fabric/server", "libraries", "versions"],
                      expected_fabric(mojang, libraries), special)
