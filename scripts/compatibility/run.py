#!/usr/bin/env python3
"""Run one compatibility row: one platform, side, exact Minecraft version, exact artifact, and one
install profile (the exact jar set a user installs; default "standard").

Example:
  python scripts/compatibility/run.py --platform fabric --side server --minecraft 1.21 \\
      --profile standard --artifact fabric-1.2.0 --out test-servers/compat-runs/fabric-1.21-standard

Writes result.json and summary.md into --out even on crash, timeout, or interruption. The exit
code is a convenience (0 pass, 1 not pass, 2 invalid invocation); result.json is the evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from egc_compat import RUNNER_VERSION  # noqa: E402
from egc_compat.client_row import ClientLimits, ClientRow, evidence_template, run_client_form  # noqa: E402
from egc_compat.manifest import (DEFAULT_PROFILE, MINECRAFT_VERSIONS, Manifest, ManifestError,  # noqa: E402
                                 Selection, SelectionError, load_manifest, select)
from egc_compat.results import (REFUSAL_CHECKS, RESULT_SCHEMA_VERSION, SERVER_CHECKS, CheckSet,  # noqa: E402
                                compute_overall, reject_unverified_evidence, utc_now, write_result, write_summary)
from egc_compat.server_row import Limits, ServerRow  # noqa: E402


def git_identity() -> dict[str, object]:
    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                                       timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    status = git("status", "--porcelain", "--", "scripts/compatibility")
    return {"git_commit": git("rev-parse", "HEAD"), "runner_files_modified": bool(status) if status is not None else None}


def describe_target(target: dict[str, object]) -> str:
    if "unavailable" in target:
        return f"UNAVAILABLE: {target['unavailable']['reason']}"
    if "paper_build" in target:
        server = f" on the Paper {target['server_minecraft']} server" if "server_minecraft" in target else ""
        return f"Paper build {target['paper_build']} ({target['channel']}){server}"
    installer = f" / installer {target['installer']}" if "installer" in target else ""
    return f"Loader {target.get('loader')}{installer}"


def unavailable_result(manifest: Manifest, selection: Selection, out_dir: Path, run_id: str,
                       command_line: list[str], control: bool) -> dict[str, object]:
    """A documented external blocker: the lock records that no official build exists."""
    checks = CheckSet(REFUSAL_CHECKS if selection.profile.refusal_ids else SERVER_CHECKS)
    checks.block_unfinished(f"no official server build: {selection.unavailable['reason']}", "environment_or_download")
    overall, failure_class = compute_overall(checks.checks, checks.required, [])
    now = utc_now()
    return {
        "schema_version": RESULT_SCHEMA_VERSION, "run_id": run_id, "started_utc": now, "ended_utc": now,
        "runner": {"version": RUNNER_VERSION, "command": command_line, **git_identity()},
        "manifest": {"path": str(manifest.path), "sha256": manifest.sha256},
        "selection": {"target_key": selection.key, "platform": selection.platform, "side": selection.side,
                      "minecraft": selection.minecraft, "artifact_id": selection.artifact_id,
                      "artifact_overridden": selection.artifact_overridden, "control": control,
                      "profile": selection.profile.name, "fixture": selection.fixture},
        "evidence_source": "automated", "profile": {**selection.profile.to_json(), "control": control, "jars": []},
        "target_pins": selection.target, "artifact": None, "environment": None,
        "checks": {name: check.to_json() for name, check in checks.checks.items()},
        "required_checks": list(checks.required), "overall": overall, "failure_class": failure_class,
        "run_errors": [], "evidence": {"out_dir": str(out_dir), "files": []},
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=HERE / "manifest.json")
    parser.add_argument("--platform", choices=("paper", "fabric"))
    parser.add_argument("--side", choices=("server", "client"), default="server")
    parser.add_argument("--minecraft", choices=MINECRAFT_VERSIONS)
    parser.add_argument("--artifact", help="manifest artifact ID; defaults to the target's artifact_id")
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help="install profile from the manifest (default %(default)s); see --list-profiles")
    parser.add_argument("--out", type=Path, help="fresh output directory (default test-servers/compat-runs/<run id>)")
    parser.add_argument("--cache", type=Path, default=REPO / "test-servers" / "compat-cache",
                        help="download/install cache keyed by pinned hashes")
    parser.add_argument("--java", default="java", help="Java executable for the server")
    parser.add_argument("--node", default="node", help="Node.js executable for the protocol clients")
    parser.add_argument("--xmx", default="2G", help="server heap limit")
    parser.add_argument("--control", action="store_true",
                        help="control: the profile's dependencies without the project artifact")
    parser.add_argument("--negative-control", action="store_true",
                        help="client only: the core scenario without the project artifact; it must observe griefing "
                             "(a harness self-check, never a row result)")
    parser.add_argument("--limit", action="append", default=[], metavar="KEY=VALUE",
                        help="override a time/tick limit (recorded in the result), e.g. server_start_s=300")
    parser.add_argument("--assets-from", type=Path,
                        help="client only: a launcher assets folder (holding objects/) to copy SHA-1-verified objects "
                             "from instead of downloading them")
    parser.add_argument("--javac", help="client only: javac for the screen observer (default: next to --java)")
    parser.add_argument("--no-sprint", action="store_true",
                        help="client only: run the core session at normal speed instead of typing /tick sprint "
                             "once the world is set up")
    parser.add_argument("--client-evidence", type=Path,
                        help="client only: record the row from a manual evidence form instead of running it")
    parser.add_argument("--client-evidence-template", action="store_true",
                        help="write a blank manual evidence form for the selected client row and exit")
    parser.add_argument("--validate-only", action="store_true", help="validate the manifest and exit")
    parser.add_argument("--list-targets", action="store_true", help="list manifest targets and exit")
    parser.add_argument("--list-profiles", action="store_true", help="list install profiles and exit")
    return parser.parse_args(argv)


def describe_profiles(manifest: Manifest) -> list[str]:
    lines = []
    for platform, profiles in manifest.data["profiles"].items():
        for name in profiles:
            profile = manifest.profile(platform, name)
            expect = (f"Loader refuses, naming {', '.join(profile.refusal_ids)}" if profile.refusal_ids
                      else "runs the checks")
            jars = " + ".join(["project", *profile.dependencies])
            lines.append(f"{platform:7} {name:20} {'documented' if profile.documented else 'undocumented':13} "
                         f"{'/'.join(profile.sides):12} {jars:30} {expect}")
    return lines


def listing(args: argparse.Namespace, manifest: Manifest) -> int | None:
    """Handle the informational options; None when the invocation is a run."""
    if args.validate_only:
        print(f"manifest valid: {len(manifest.data['targets'])} targets, {len(manifest.data['artifacts'])} artifacts, "
              f"sha256 {manifest.sha256}")
        return 0
    if args.list_targets:
        for key, target in manifest.data["targets"].items():
            pins = ", ".join(f"{mod_id} {artifact_id}" for mod_id, artifact_id in (target.get("dependencies") or {}).items())
            print(f"{key:24} {target['artifact_id']:14} {describe_target(target)}" + (f"; {pins}" if pins else ""))
        return 0
    if args.list_profiles:
        print("\n".join(describe_profiles(manifest)))
        return 0
    return None


def invocation_problem(args: argparse.Namespace, selection: Selection) -> str | None:
    if args.side != "client":
        for option, name in ((args.negative_control, "--negative-control"), (args.client_evidence, "--client-evidence"),
                             (args.client_evidence_template, "--client-evidence-template"),
                             (args.assets_from, "--assets-from"), (args.no_sprint, "--no-sprint")):
            if option:
                return f"{name} applies to client rows only"
    if args.control and args.negative_control:
        return "--control and --negative-control are separate runs"
    if (args.control or args.negative_control) and selection.profile.refusal_ids:
        return f"controls do not apply to the {selection.profile.name!r} profile, which installs no dependency"
    if args.client_evidence and (args.control or args.negative_control):
        return "a manual evidence form records the artifact row, not a control"
    return None


def write_template(manifest: Manifest, selection: Selection, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "client-evidence.json"
    if path.exists():
        print(f"{path} already exists; not overwriting", file=sys.stderr)
        return 2
    path.write_text(json.dumps(evidence_template(manifest, selection), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


def run_server(args: argparse.Namespace, manifest: Manifest, selection: Selection, out_dir: Path, run_id: str,
               command_line: list[str]) -> dict[str, object]:
    limits, overrides = Limits.from_overrides(args.limit)
    row = ServerRow(manifest=manifest, selection=selection, out_dir=out_dir, cache_dir=args.cache.resolve(),
                    limits=limits, java=args.java, node=args.node, xmx=args.xmx, control=args.control,
                    run_id=run_id, command_line=command_line, limit_overrides=overrides, git=git_identity())
    try:
        return row.run()
    finally:
        # Last resort if anything escaped the row (for example a second Ctrl+C during cleanup).
        row.registry.kill_all()
        try:
            row.redact_credentials()
        except OSError as error:
            print(f"could not redact the RCON password: {error}", file=sys.stderr)


def run_client(args: argparse.Namespace, manifest: Manifest, selection: Selection, out_dir: Path, run_id: str,
               command_line: list[str]) -> dict[str, object]:
    if args.client_evidence:
        return run_client_form(manifest=manifest, selection=selection, out_dir=out_dir, form_path=args.client_evidence,
                               run_id=run_id, command_line=command_line, git=git_identity())
    limits, overrides = ClientLimits.from_overrides(args.limit)
    row = ClientRow(manifest=manifest, selection=selection, out_dir=out_dir, cache_dir=args.cache.resolve(),
                    limits=limits, java=args.java, javac=args.javac, control=args.control,
                    negative=args.negative_control, run_id=run_id, command_line=command_line,
                    limit_overrides=overrides, git=git_identity(),
                    assets_from=args.assets_from.resolve() if args.assets_from else None, sprint=not args.no_sprint)
    try:
        return row.run()
    finally:
        row.registry.kill_all()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as error:
        for problem in error.errors:
            print(f"manifest invalid: {problem}", file=sys.stderr)
        return 2
    listed = listing(args, manifest)
    if listed is not None:
        return listed
    if not args.platform or not args.minecraft:
        print("--platform and --minecraft are required for a run", file=sys.stderr)
        return 2
    try:
        selection = select(manifest, args.platform, args.side, args.minecraft, args.artifact, args.profile)
        (ClientLimits if args.side == "client" else Limits).from_overrides(args.limit)
    except (SelectionError, ValueError) as error:
        print(f"invalid selection: {error}", file=sys.stderr)
        return 2
    problem = invocation_problem(args, selection)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = selection.artifact_id
    if args.control or args.negative_control:
        label = "negative-control" if args.negative_control else "control"
    run_id = f"{stamp}-{args.platform}-{args.side}-{args.minecraft}-{selection.profile.name}-{label}"
    out_dir = (args.out or REPO / "test-servers" / "compat-runs" / run_id).resolve()
    if args.client_evidence_template:
        return write_template(manifest, selection, out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"--out {out_dir} is not empty; each run needs a fresh directory", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    command_line = [Path(sys.executable).name, *sys.argv]

    if selection.unavailable:
        result = unavailable_result(manifest, selection, out_dir, run_id, command_line, args.control)
    elif args.side == "client":
        result = run_client(args, manifest, selection, out_dir, run_id, command_line)
    else:
        result = run_server(args, manifest, selection, out_dir, run_id, command_line)
    if result.get("evidence_source") == "automated":
        reject_unverified_evidence(out_dir, result)
    write_result(out_dir, result)
    summary = write_summary(out_dir, result)
    print(summary.read_text(encoding="utf-8"))
    print(f"result: {out_dir / 'result.json'}")
    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
