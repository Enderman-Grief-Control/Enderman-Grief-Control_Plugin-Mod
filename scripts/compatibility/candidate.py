#!/usr/bin/env python3
"""Build or register a candidate jar for the next release and add it to the reviewed lock.

  candidate.py build --platform fabric --version 1.2.1 [--write]
  candidate.py register --platform fabric --version 1.2.1 --jar PATH (--source-commit SHA | --tag TAG) [--write]

``build`` refuses to run unless fabric-mod/, paper-plugin/, and enderman-grief-control-messaging/ are
clean at HEAD. It runs the release workflow's commands with test mode off, applies the workflow's
checks (test mode off in the jar, file name ends in -<version>.jar), and also checks that the
descriptor carries exactly that version. ``register`` applies the same checks to a jar built
elsewhere, such as the release workflow's ``release-jars`` output, so the pre-publish matrix can run
on that exact jar.

Either way the jar is copied to the ignored test-servers/compat-candidates/<artifact id>/ and a
``candidate`` artifact is printed, or saved with --write after the manifest validates. Candidate
builds are not byte-reproducible, so each gets its own ID: <platform>-<version>-cand-<short commit>
for a local build and <platform>-<version>-reg-<short commit> for a registered jar, with -2, -3, ...
for another jar from the same commit. Target defaults are never changed; runs select a candidate
with --artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from egc_compat.acquire import hash_file  # noqa: E402
from egc_compat.jarinfo import read_descriptor, read_test_mode  # noqa: E402
from egc_compat.manifest import COMMIT_RE, validate  # noqa: E402

SOURCE_DIRS = ("fabric-mod", "paper-plugin", "enderman-grief-control-messaging")
CANDIDATES = REPO / "test-servers" / "compat-candidates"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")


class CandidateError(Exception):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git(*args: str) -> str:
    completed = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=60,
                               check=False)
    if completed.returncode != 0:
        raise CandidateError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def dirty_sources() -> list[str]:
    return [line for line in git("status", "--porcelain", "--untracked-files=all", "--", *SOURCE_DIRS).splitlines()
            if line.strip()]


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise CandidateError(f"{name} is not on PATH")
    return found


def build_commands(platform: str, version: str) -> list[tuple[Path, list[str]]]:
    """The release workflow's build steps for one platform, as (working directory, argv)."""
    commands = [(REPO, [tool("mvn"), "-B", "-f", "enderman-grief-control-messaging/pom.xml", "install"])]
    if platform == "paper":
        commands.append((REPO, [tool("mvn"), "-B", "-f", "paper-plugin/pom.xml", "verify", f"-Drevision={version}"]))
    else:
        gradlew = REPO / "fabric-mod" / ("gradlew.bat" if os.name == "nt" else "gradlew")
        launcher = [str(gradlew)] if os.name == "nt" else ["sh", str(gradlew)]
        commands.append((REPO / "fabric-mod", [*launcher, "build", f"-Pmod_version={version}"]))
    return commands


def built_jars(platform: str) -> list[Path]:
    """The jars the workflow's "Collect release jars" step would pick up."""
    if platform == "paper":
        return sorted((REPO / "paper-plugin" / "target").glob("EndermanGriefControl-*.jar"))
    return sorted(path for path in (REPO / "fabric-mod" / "build" / "libs").glob("*.jar")
                  if not path.name.endswith("-sources.jar"))


def descriptor_for(jar: Path, platform: str) -> dict[str, str]:
    descriptor = read_descriptor(jar, platform)
    if platform == "paper":
        return {"name": descriptor.get("name"), "version": descriptor.get("version"),
                "api-version": descriptor.get("api-version")}
    return {"id": descriptor.get("id"), "version": descriptor.get("version")}


def jar_problems(jar: Path, platform: str, version: str) -> list[str]:
    """The workflow's checks plus the descriptor version; an empty list means the jar is usable."""
    problems = []
    if not jar.name.endswith(f"-{version}.jar"):
        problems.append(f"{jar.name} does not end in -{version}.jar")
    try:
        mode = read_test_mode(jar)
        descriptor = descriptor_for(jar, platform)
    except (KeyError, zipfile.BadZipFile, ValueError) as error:
        return problems + [f"{jar.name} is not a readable {platform} jar: {error}"]
    if mode != "false":
        problems.append(f"{jar.name} was built with test mode {mode!r}, not 'false'")
    if descriptor.get("version") != version:
        problems.append(f"{jar.name} declares version {descriptor.get('version')!r}, not {version!r}")
    return problems


def refuse_known_bytes(artifacts: dict, jar: Path) -> None:
    digest = hash_file(jar)
    known = next((artifact_id for artifact_id, artifact in artifacts.items() if artifact.get("sha256") == digest), None)
    if known:
        raise CandidateError(f"{jar.name} (SHA-256 {digest}) is already in the manifest as {known}")


def next_artifact_id(artifacts: dict, base: str, candidates: Path | None = None) -> str:
    """``base``, or ``base-2``, ``base-3``, ...: unused in the manifest and in the candidates folder."""
    def taken(artifact_id: str) -> bool:
        return artifact_id in artifacts or (candidates is not None and (candidates / artifact_id).exists())

    if not taken(base):
        return base
    suffix = 2
    while taken(f"{base}-{suffix}"):
        suffix += 1
    return f"{base}-{suffix}"


def stage(jar: Path, artifact_id: str, candidates: Path) -> Path:
    folder = candidates / artifact_id
    if folder.exists() and any(folder.iterdir()):
        raise CandidateError(f"{folder} already holds a jar; candidate IDs are never reused")
    folder.mkdir(parents=True, exist_ok=True)
    staged = folder / jar.name
    shutil.copy2(jar, staged)
    return staged


def candidate_entry(platform: str, staged: Path, manifest_path: Path, source_commit: str,
                    build: dict) -> dict:
    return {
        "platform": platform,
        "kind": "candidate",
        "source_commit": source_commit,
        "filename": staged.name,
        "local_path": os.path.relpath(staged, manifest_path.parent).replace("\\", "/"),
        "size": staged.stat().st_size,
        "sha256": hash_file(staged),
        "descriptor": descriptor_for(staged, platform),
        "test_mode": read_test_mode(staged),
        "build": build,
    }


def register_entry(manifest_path: Path, artifact_id: str, entry: dict, write: bool) -> int:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["artifacts"][artifact_id] = entry
    errors = validate(data)
    if errors:
        for error in errors:
            print(f"invalid: {error}", file=sys.stderr)
        return 1
    if write:
        manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"registered {artifact_id} in {manifest_path}; review the diff before committing", file=sys.stderr)
    print(json.dumps({artifact_id: entry}, indent=2))
    return 0


def portable(argv: list[str]) -> list[str]:
    """A recorded command without host paths: tool basenames, repository paths made relative."""
    out = []
    for index, arg in enumerate(argv):
        path = Path(arg)
        if path.is_absolute() and path.is_relative_to(REPO):
            out.append(path.relative_to(REPO).as_posix())
        elif index == 0:
            out.append(re.split(r"[\\/]", arg)[-1])  # Path.name keeps a Windows path whole on Linux
        else:
            out.append(arg)
    return out


def gradle_distribution() -> str | None:
    properties = REPO / "fabric-mod" / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not properties.exists():
        return None
    for line in properties.read_text(encoding="utf-8").splitlines():
        if line.startswith("distributionUrl="):
            return line.split("=", 1)[1].replace("\\:", ":")
    return None


def first_line(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0] if text else None


def cmd_build(args: argparse.Namespace) -> int:
    dirty = dirty_sources()
    if dirty:
        raise CandidateError("source directories are not clean at HEAD:\n  " + "\n  ".join(dirty))
    commit = git("rev-parse", "HEAD")
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    artifact_id = next_artifact_id(data["artifacts"], f"{args.platform}-{args.version}-cand-{commit[:7]}", args.candidates)
    log_path = args.candidates / f"{artifact_id}.build.log"
    args.candidates.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ENDERMAN_GRIEF_TEST_MODE": "false"}
    commands = build_commands(args.platform, args.version)
    for jar in built_jars(args.platform):
        jar.unlink()  # never pick up a stale jar from an earlier build
    with log_path.open("w", encoding="utf-8") as log:
        for cwd, argv in commands:
            print(f"$ {' '.join(argv)}", file=sys.stderr, flush=True)
            log.write(f"$ (cd {cwd}) {' '.join(argv)}\n")
            log.flush()
            completed = subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
            if completed.returncode != 0:
                raise CandidateError(f"{' '.join(argv)} exited {completed.returncode}; see {log_path}")
    if dirty_sources():
        raise CandidateError("the build changed tracked source files; refusing to register its output")
    jars = built_jars(args.platform)
    if len(jars) != 1:
        raise CandidateError(f"expected exactly one built jar, found {[jar.name for jar in jars]}")
    problems = jar_problems(jars[0], args.platform, args.version)
    if problems:
        raise CandidateError("; ".join(problems))
    refuse_known_bytes(data["artifacts"], jars[0])
    staged = stage(jars[0], artifact_id, args.candidates)
    build_tool = first_line([tool("mvn"), "-v"]) or "unknown"
    if args.platform == "fabric":
        build_tool += f"; Gradle wrapper {gradle_distribution() or 'unknown'}"
    build = {
        "method": "local-build",
        "commands": [portable(argv) for _cwd, argv in commands],
        "environment": {"ENDERMAN_GRIEF_TEST_MODE": "false"},
        "java": first_line(["java", "-version"]) or "unknown",
        "build_tool": build_tool,
        "built_utc": utc_now(),
        "clean_tree": True,
        "log": os.path.relpath(log_path, args.manifest.parent).replace("\\", "/"),
    }
    return register_entry(args.manifest, artifact_id, candidate_entry(args.platform, staged, args.manifest, commit, build),
                          args.write)


def cmd_register(args: argparse.Namespace) -> int:
    jar = args.jar.resolve()
    if not jar.is_file():
        raise CandidateError(f"{jar} does not exist")
    commit = args.source_commit or git("rev-parse", f"{args.tag}^{{commit}}")
    if not COMMIT_RE.match(commit or ""):
        raise CandidateError(f"source commit {commit!r} must be a full 40-character SHA")
    problems = jar_problems(jar, args.platform, args.version)
    if problems:
        raise CandidateError("; ".join(problems))
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    refuse_known_bytes(data["artifacts"], jar)
    artifact_id = next_artifact_id(data["artifacts"], f"{args.platform}-{args.version}-reg-{commit[:7]}", args.candidates)
    staged = stage(jar, artifact_id, args.candidates)
    build = {"method": "registered", "registered_from": str(jar), "registered_utc": utc_now(),
             **({"tag": args.tag} if args.tag else {})}
    return register_entry(args.manifest, artifact_id, candidate_entry(args.platform, staged, args.manifest, commit, build),
                          args.write)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "register"):
        command = sub.add_parser(name)
        command.add_argument("--platform", required=True, choices=("fabric", "paper"))
        command.add_argument("--version", required=True, help="the release version, e.g. 1.2.1")
        command.add_argument("--manifest", type=Path, default=HERE / "manifest.json")
        command.add_argument("--candidates", type=Path, default=CANDIDATES, help="where candidate jars are kept")
        command.add_argument("--write", action="store_true", help="save the artifact into the manifest")
        if name == "register":
            command.add_argument("--jar", type=Path, required=True)
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--source-commit", help="full SHA of the commit the jar was built from")
            source.add_argument("--tag", help="release tag the jar was built from, e.g. fabric-v1.2.1")
    args = parser.parse_args(argv)
    if not VERSION_RE.match(args.version):
        print(f"--version {args.version!r} is not a release version like 1.2.1", file=sys.stderr)
        return 2
    try:
        return cmd_build(args) if args.command == "build" else cmd_register(args)
    except CandidateError as error:
        print(f"candidate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
