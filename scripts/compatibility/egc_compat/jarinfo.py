"""Read the descriptor, test-mode flag, and Mixin configs embedded in a project jar."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any


def _simple_yaml_scalars(text: str) -> dict[str, str]:
    """Top-level ``key: value`` scalars of a plugin.yml; nested sections are skipped."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw[0] in " \t#":
            continue
        key, separator, value = raw.partition(":")
        value = value.strip()
        if separator and value:
            values[key.strip()] = value.strip("'\"")
    return values


def _properties(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        key, _separator, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def read_descriptor(jar_path: Path, platform: str) -> dict[str, Any]:
    with zipfile.ZipFile(jar_path) as jar:
        if platform == "paper":
            return _simple_yaml_scalars(jar.read("plugin.yml").decode("utf-8"))
        return json.loads(jar.read("fabric.mod.json").decode("utf-8"))


def read_test_mode(jar_path: Path) -> str | None:
    with zipfile.ZipFile(jar_path) as jar:
        try:
            return _properties(jar.read("test-mode.properties").decode("utf-8")).get("test-mode")
        except KeyError:
            return None


def _mixin_names(descriptor: dict[str, Any]) -> list[str]:
    names = []
    for entry in descriptor.get("mixins", []):
        names.append(entry if isinstance(entry, str) else entry.get("config", ""))
    return [name for name in names if name]


def fabric_mod_tree(jar_path: Path) -> list[dict[str, Any]]:
    """The jar's own mod plus every nested (jar-in-jar) mod, with each one's Mixin configs."""
    found: list[dict[str, Any]] = []

    def visit(data: bytes, origin: str) -> None:
        with zipfile.ZipFile(io.BytesIO(data)) as jar:
            try:
                descriptor = json.loads(jar.read("fabric.mod.json").decode("utf-8"))
            except KeyError:
                return
            found.append({
                "id": descriptor.get("id"),
                "version": descriptor.get("version"),
                "origin": origin,
                "mixins": _mixin_names(descriptor),
            })
            for nested in descriptor.get("jars", []):
                path = nested.get("file")
                if path:
                    try:
                        visit(jar.read(path), path)
                    except KeyError:
                        continue

    visit(jar_path.read_bytes(), jar_path.name)
    return found


def check_artifact(jar_path: Path, platform: str, artifact: dict[str, Any]) -> list[tuple[str, bool, Any, Any]]:
    """Return (name, ok, expected, observed) identity assertions for an installed artifact."""
    results: list[tuple[str, bool, Any, Any]] = []
    try:
        descriptor = read_descriptor(jar_path, platform)
    except (KeyError, zipfile.BadZipFile, ValueError) as error:
        return [("descriptor readable", False, "descriptor present", f"{type(error).__name__}: {error}")]
    for field, expected in artifact["descriptor"].items():
        observed = descriptor.get(field)
        results.append((f"descriptor {field}", observed == expected, expected, observed))
    if "test_mode" in artifact:
        observed_mode = read_test_mode(jar_path)
        results.append(("test-mode flag", observed_mode == artifact["test_mode"], artifact["test_mode"], observed_mode))
    return results
