"""Resolve an install profile to its exact jar set, install it, and assert what was installed.

A profile is what a user installs: the project jar plus the pinned dependency jars the profile
names (Fabric API, Mod Menu). Server and client rows share this module, so both sides install and
check jars the same way, and every result records which profile and which jars produced it.
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acquire import (AcquisitionError, CachedFile, HttpLimits, contained, copy_local_pinned, fetch_pinned, hash_file,
                      store_generated_pinned)
from .jarinfo import check_artifact, generated_jar, read_descriptor
from .manifest import Manifest, Selection


@dataclass(frozen=True)
class JarSpec:
    role: str  # "project" or "dependency"
    mod_id: str  # Fabric mod ID, or the Paper plugin name
    artifact_id: str
    artifact: dict[str, Any]


def jar_set(manifest: Manifest, selection: Selection, control: bool = False) -> list[JarSpec]:
    """The jars the selected profile installs; a control drops only the project jar."""
    specs: list[JarSpec] = []
    if not control:
        descriptor = selection.artifact["descriptor"]
        specs.append(JarSpec("project", descriptor.get("id") or descriptor.get("name"), selection.artifact_id,
                             selection.artifact))
    for mod_id, artifact_id in selection.dependency_ids().items():
        specs.append(JarSpec("dependency", mod_id, artifact_id, manifest.artifact(artifact_id)))
    return specs


def acquire_jar(spec: JarSpec, manifest: Manifest, cache_dir: Path, http: HttpLimits,
                deadline: float | None) -> CachedFile:
    """A verified cached copy of a pinned jar, from its URL, its local path, or its manifest description."""
    artifact = spec.artifact
    if "url" in artifact:
        return fetch_pinned(artifact["url"], artifact["filename"], cache_dir, manifest.user_agent, http,
                            sha256=artifact["sha256"], size=artifact["size"], deadline=deadline)
    if "generated" in artifact:
        return store_generated_pinned(generated_jar(artifact["generated"]), artifact["filename"], cache_dir,
                                      sha256=artifact["sha256"], size=artifact["size"],
                                      source="generated from the manifest")
    local = Path(artifact["local_path"])
    if not local.is_absolute():
        local = manifest.path.parent / local
    return copy_local_pinned(local, artifact["filename"], cache_dir, sha256=artifact["sha256"], size=artifact["size"])


def install_jars(specs: list[JarSpec], sources: dict[str, CachedFile], directory: Path) -> list[dict[str, Any]]:
    """Copy each jar into ``directory`` (``mods/`` or ``plugins/``) and record what was written."""
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for spec in specs:
        installed = contained(directory, spec.artifact["filename"])
        if installed.exists():
            raise AcquisitionError(f"two profile jars share the file name {installed.name}", "unsafe_path")
        shutil.copy2(sources[spec.artifact_id].path, installed)
        records.append({"role": spec.role, "mod_id": spec.mod_id, "artifact_id": spec.artifact_id,
                        "kind": spec.artifact["kind"], "filename": spec.artifact["filename"],
                        "expected_sha256": spec.artifact["sha256"], "installed_sha256": hash_file(installed),
                        "source": sources[spec.artifact_id].source})
    return records


def jar_set_assertions(directory: Path, specs: list[JarSpec], records: list[dict[str, Any]],
                       platform: str) -> list[tuple[str, bool, Any, Any]]:
    """(name, ok, expected, observed) checks that ``directory`` holds exactly the profile's jars.

    Each installed jar is re-hashed against its pin; the project jar's descriptor and test-mode
    flag are checked, and each dependency's descriptor must name the pinned mod and version.
    """
    results: list[tuple[str, bool, Any, Any]] = []
    for spec in specs:
        installed = directory / spec.artifact["filename"]
        expected = spec.artifact["sha256"]
        actual = hash_file(installed) if installed.is_file() else None
        for record in records:
            if record["artifact_id"] == spec.artifact_id:
                record["installed_sha256"] = actual
        results.append((f"{spec.role} {spec.artifact_id} SHA-256 matches lock", actual == expected, expected, actual))
        if actual is None:
            continue
        if spec.role == "project":
            results += [(f"project {name}", ok, want, got)
                        for name, ok, want, got in check_artifact(installed, platform, spec.artifact)]
            continue
        try:
            descriptor = read_descriptor(installed, "fabric")
            observed = (descriptor.get("id"), descriptor.get("version"))
        except (KeyError, zipfile.BadZipFile, ValueError) as error:
            observed = (f"unreadable: {type(error).__name__}", None)
        pinned = (spec.mod_id, spec.artifact["descriptor"]["version"])
        results.append((f"dependency {spec.artifact_id} descriptor matches pin", observed == pinned,
                        list(pinned), list(observed)))
    present = sorted(path.name for path in directory.glob("*.jar")) if directory.is_dir() else []
    wanted = sorted(spec.artifact["filename"] for spec in specs)
    results.append(("installed jars are exactly the profile's jars", present == wanted, wanted, present))
    return results


def profile_record(selection: Selection, records: list[dict[str, Any]], control: bool) -> dict[str, Any]:
    """The result's ``profile`` block: the profile and every jar installed under it."""
    return {**selection.profile.to_json(), "control": control, "jars": records}
