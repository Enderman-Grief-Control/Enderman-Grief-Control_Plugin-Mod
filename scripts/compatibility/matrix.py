#!/usr/bin/env python3
"""Run several independent rows of one platform/side and collate their results.

Each row is a separate run.py invocation with its own output directory, so a failed or blocked
row never stops or alters the others, and every row stays replayable on its own.

Example:
  python scripts/compatibility/matrix.py --platform paper --side server \\
      --out test-servers/compat-runs/paper-1.2.0-matrix
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from egc_compat.manifest import MINECRAFT_VERSIONS  # noqa: E402
from egc_compat.procs import kill_tree  # noqa: E402


def run_row(command: list[str], timeout_s: float) -> int | None:
    """Run one row; past the outer limit, kill its whole process tree (servers and clients too)."""
    process = subprocess.Popen(command, start_new_session=sys.platform != "win32",
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
    try:
        return process.wait(timeout_s)
    except subprocess.TimeoutExpired:
        print(f"row exceeded the outer {timeout_s:g}s limit; killing its process tree", flush=True)
        kill_tree(process.pid)
        process.wait(30)
        return None
    except KeyboardInterrupt:
        # The row runs in its own process group, so it does not see Ctrl+C itself.
        kill_tree(process.pid)
        process.wait(30)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", required=True, choices=("paper", "fabric"))
    parser.add_argument("--side", default="server", choices=("server", "client"))
    parser.add_argument("--minecraft", nargs="+", choices=MINECRAFT_VERSIONS, default=list(MINECRAFT_VERSIONS))
    parser.add_argument("--artifact", help="manifest artifact ID for every row")
    parser.add_argument("--out", type=Path, required=True, help="fresh directory; each row writes <out>/<version>")
    parser.add_argument("--row-timeout", type=float, default=1500,
                        help="outer limit per row in seconds (the row's own limit is 1200s plus cleanup)")
    parser.add_argument("run_args", nargs=argparse.REMAINDER, help="extra run.py arguments after --")
    args = parser.parse_args()

    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        print(f"--out {out} is not empty", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    extra = args.run_args[1:] if args.run_args[:1] == ["--"] else args.run_args
    rows = []
    for minecraft in args.minecraft:
        row_out = out / minecraft
        command = [sys.executable, str(HERE / "run.py"), "--platform", args.platform, "--side", args.side,
                   "--minecraft", minecraft, "--out", str(row_out), *extra]
        if args.artifact:
            command += ["--artifact", args.artifact]
        print(f"=== {args.platform}/{args.side}/{minecraft}", flush=True)
        code = run_row(command, args.row_timeout)
        result_path = row_out / "result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            rows.append({"minecraft": minecraft, "overall": result["overall"],
                         "failure_class": result.get("failure_class"),
                         "checks": {name: check["status"] for name, check in result["checks"].items()},
                         "result": str(result_path.relative_to(out))})
        else:
            rows.append({"minecraft": minecraft, "overall": "blocked", "failure_class": "harness_or_protocol",
                         "checks": {}, "result": None, "note": f"run.py exited {code} without a result"})
    (out / "matrix.json").write_text(json.dumps({"platform": args.platform, "side": args.side, "rows": rows},
                                                indent=2) + "\n", encoding="utf-8")
    lines = [f"# {args.platform}/{args.side} matrix", "", "| Minecraft | Overall | Failure class | Result |",
             "| --- | --- | --- | --- |"]
    lines += [f"| {row['minecraft']} | {row['overall']} | {row['failure_class'] or ''} | {row['result'] or row.get('note')} |"
              for row in rows]
    (out / "matrix-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(row["overall"] == "pass" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
