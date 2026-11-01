#!/usr/bin/env python3
"""Run several independent rows of one platform/side, under one or more install profiles.

Each run is a separate run.py invocation with its own output directory (<out>/<version>/<profile>),
so a failed or blocked run never stops or alters the others, and every run stays replayable on its
own. A row's verdict is "pass" only when every documented profile for that side ran and passed;
results under undocumented profiles are listed but never count, and a fixture artifact's rows are
labeled "fixture" rather than given a verdict that could support a claim.

Example:
  python scripts/compatibility/matrix.py --platform fabric --side server --profiles documented \\
      --out test-servers/compat-runs/fabric-server-matrix
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from egc_compat.manifest import (DEFAULT_PROFILE, MINECRAFT_VERSIONS, Manifest, ManifestError,  # noqa: E402
                                 load_manifest)
from egc_compat.procs import kill_tree  # noqa: E402
from egc_compat.server_row import redact_rcon_password  # noqa: E402


def stop_row(process: subprocess.Popen, row_out: Path) -> str | None:
    """Kill a row's whole process tree, then redact the RCON password the killed run could not."""
    problem = kill_tree(process.pid)
    process.wait(30)
    redact_rcon_password(row_out / "server" / "server.properties")
    return problem


def run_row(command: list[str], timeout_s: float, row_out: Path) -> tuple[int | None, str | None]:
    """Run one row; past the outer limit, kill its whole process tree (servers and clients too).

    Returns the exit code (None when killed) and any problem stopping the process tree.
    """
    process = subprocess.Popen(command, start_new_session=sys.platform != "win32",
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
    try:
        return process.wait(timeout_s), None
    except subprocess.TimeoutExpired:
        print(f"row exceeded the outer {timeout_s:g}s limit; killing its process tree", flush=True)
        return None, stop_row(process, row_out)
    except KeyboardInterrupt:
        # The row runs in its own process group, so it does not see Ctrl+C itself.
        stop_row(process, row_out)
        raise


def row_record(minecraft: str, out: Path, row_out: Path, code: int | None, problem: str | None) -> dict:
    """Summarize one run from its result.json; a run without one is blocked, never passed."""
    result_path = row_out / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        checks = {**result.get("checks", {}), **result.get("extended_checks", {})}
        return {"minecraft": minecraft, "overall": result["overall"], "failure_class": result.get("failure_class"),
                "checks": {name: check["status"] for name, check in checks.items()},
                "result": str(result_path.relative_to(out))}
    note = (f"run.py exited {code} without a result" if code is not None
            else "killed at the outer row limit before writing a result")
    return {"minecraft": minecraft, "overall": "blocked", "failure_class": "harness_or_protocol",
            "checks": {}, "result": None, "note": f"{note}; {problem}" if problem else note}


def choose_profiles(manifest: Manifest, platform: str, side: str, spec: str) -> list[str]:
    """``documented``, ``all``, or a comma-separated list of profile names for this side."""
    applicable = manifest.profiles(platform, side)
    if spec == "documented":
        return [profile.name for profile in applicable if profile.documented]
    if spec == "all":
        return [profile.name for profile in applicable]
    names = [name.strip() for name in spec.split(",") if name.strip()]
    unknown = [name for name in names if name not in {profile.name for profile in applicable}]
    if unknown or not names:
        raise ValueError(f"no {platform} {side} profile named {', '.join(unknown) or spec!r}; expected "
                         f"documented, all, or some of {', '.join(profile.name for profile in applicable)}")
    return names


def row_verdict(manifest: Manifest, platform: str, side: str, runs: dict[str, dict], fixture: bool) -> str:
    """Pass only when every documented profile for the side ran and passed."""
    if fixture:
        return "fixture"
    documented = [profile.name for profile in manifest.profiles(platform, side, documented_only=True)]
    missing = [name for name in documented if name not in runs]
    outcomes = [runs[name]["overall"] for name in documented if name in runs]
    for status in ("fail", "blocked", "unknown"):
        if status in outcomes:
            return status
    return "incomplete" if missing else "pass"


def render(platform: str, side: str, profiles: list[str], rows: list[dict]) -> list[str]:
    header = f"| Minecraft | Verdict | {' | '.join(profiles)} |"
    lines = [f"# {platform}/{side} matrix", "",
             "Verdict: pass only when every documented profile ran and passed; undocumented profiles never count.",
             "", header, "|" + " --- |" * (len(profiles) + 2)]
    for row in rows:
        cells = []
        for name in profiles:
            run = row["runs"].get(name) or {}
            cells.append(f"{run.get('overall', '')}"
                         + (f" ({run['failure_class']})" if run.get("failure_class") else ""))
        lines.append(f"| {row['minecraft']} | {row['verdict']} | {' | '.join(cells)} |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=HERE / "manifest.json")
    parser.add_argument("--platform", required=True, choices=("paper", "fabric"))
    parser.add_argument("--side", default="server", choices=("server", "client"))
    parser.add_argument("--minecraft", nargs="+", choices=MINECRAFT_VERSIONS, default=list(MINECRAFT_VERSIONS))
    parser.add_argument("--artifact", help="manifest artifact ID for every row")
    parser.add_argument("--profiles", default=DEFAULT_PROFILE,
                        help="documented, all, or comma-separated profile names (default %(default)s)")
    parser.add_argument("--out", type=Path, required=True, help="fresh directory; each run writes <out>/<version>/<profile>")
    parser.add_argument("--row-timeout", type=float, default=1500,
                        help="outer limit per run in seconds (the run's own limit is 1200s plus cleanup)")
    parser.add_argument("run_args", nargs=argparse.REMAINDER, help="extra run.py arguments after --")
    args = parser.parse_args()

    try:
        manifest = load_manifest(args.manifest)
        profiles = choose_profiles(manifest, args.platform, args.side, args.profiles)
    except (ManifestError, ValueError) as error:
        print(f"invalid: {error}", file=sys.stderr)
        return 2
    artifact = manifest.data["artifacts"].get(args.artifact) if args.artifact else None
    fixture = bool(artifact and artifact.get("kind") == "fixture")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        print(f"--out {out} is not empty", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    extra = args.run_args[1:] if args.run_args[:1] == ["--"] else args.run_args
    rows = []
    for minecraft in args.minecraft:
        runs = {}
        for profile in profiles:
            row_out = out / minecraft / profile
            command = [sys.executable, str(HERE / "run.py"), "--manifest", str(args.manifest.resolve()),
                       "--platform", args.platform, "--side", args.side, "--minecraft", minecraft,
                       "--profile", profile, "--out", str(row_out), *extra]
            if args.artifact:
                command += ["--artifact", args.artifact]
            print(f"=== {args.platform}/{args.side}/{minecraft} profile {profile}", flush=True)
            code, problem = run_row(command, args.row_timeout, row_out)
            runs[profile] = row_record(minecraft, out, row_out, code, problem)
        rows.append({"minecraft": minecraft, "verdict": row_verdict(manifest, args.platform, args.side, runs, fixture),
                     "runs": runs})
    (out / "matrix.json").write_text(json.dumps({
        "platform": args.platform, "side": args.side, "artifact": args.artifact, "fixture": fixture,
        "profiles": profiles, "manifest_sha256": manifest.sha256, "rows": rows}, indent=2) + "\n", encoding="utf-8")
    lines = render(args.platform, args.side, profiles, rows)
    (out / "matrix-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(row["verdict"] == "pass" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
