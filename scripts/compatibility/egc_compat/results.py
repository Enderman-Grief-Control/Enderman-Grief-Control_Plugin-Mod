"""Per-check status, false-pass guards, overall outcome, and result/summary output.

Status meanings (PRD Substep 10A):
  pass     every recorded assertion held, and at least one assertion was recorded
  fail     a repeatable observed contradiction, load crash, or assertion mismatch
  blocked  attempted, but a prerequisite, missing client, upstream failure, or no observation
           within the deadline prevented the assertion
  unknown  never attempted or no evidence supplied
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("pass", "fail", "blocked", "unknown")
FAILURE_CLASSES = (
    "artifact",
    "game_or_loader",
    "harness_or_protocol",
    "environment_or_download",
    "timeout_or_no_observation",
    "client_access",
    "unclassified",
)
RESULT_SCHEMA_VERSION = 1

SERVER_CHECKS = (
    "startup",
    "pickup",
    "placement",
    "carrier_off",
    "carrier_alert",
    "carrier_auto_clear",
    "other_mob",
    "commands_permissions",
    "persistence",
    "announcements_cooldown",
)
CONTROL_CHECKS = ("startup", "other_mob")
CLIENT_CHECKS = (
    "client_startup",
    "integrated_world_load",
    "pickup",
    "placement",
    "mod_menu_load",
    "client_errors",
)
CLIENT_EXTENDED_CHECKS = (
    "mod_menu_save_cancel",
    "settings_persistence",
    "world_transition",
    "monitor_state",
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Check:
    name: str
    status: str = "unknown"
    reason: str = "not attempted"
    failure_class: str | None = None
    assertions: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    started_utc: str | None = None
    duration_s: float | None = None
    _started: float | None = None
    finished: bool = False

    def start(self) -> "Check":
        self.started_utc = utc_now()
        self._started = time.monotonic()
        self.reason = "in progress"
        return self

    def check(self, name: str, ok: bool, expected: Any, observed: Any, **context: Any) -> bool:
        record = {"name": name, "ok": bool(ok), "expected": expected, "observed": observed, "t": utc_now()}
        record.update({key: value for key, value in context.items() if value is not None})
        self.assertions.append(record)
        return bool(ok)

    def observe(self, note: str, **context: Any) -> None:
        record = {"note": note, "t": utc_now()}
        record.update({key: value for key, value in context.items() if value is not None})
        self.observations.append(record)

    def finish(self, status: str | None = None, reason: str | None = None,
               failure_class: str | None = None) -> "Check":
        """Close the check. ``status=None`` derives pass/fail from the recorded assertions.

        A pass is never accepted without at least one assertion, or with a failed one: an
        explicit "pass" with missing evidence becomes blocked, and with a failed assertion fail.
        """
        if self._started is not None:
            self.duration_s = round(time.monotonic() - self._started, 3)
        failed = [assertion["name"] for assertion in self.assertions if not assertion["ok"]]
        if status is None:
            if failed:
                status = "fail"
            elif self.assertions:
                status = "pass"
            else:
                status = "blocked"
                reason = reason or "no assertions were recorded"
                failure_class = failure_class or "timeout_or_no_observation"
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        if failure_class is not None and failure_class not in FAILURE_CLASSES:
            raise ValueError(f"unknown failure class {failure_class!r}")
        if status == "pass" and failed:
            status, reason = "fail", f"assertion(s) failed: {', '.join(failed)}"
            failure_class = failure_class or "unclassified"
        elif status == "pass" and not self.assertions:
            status, reason = "blocked", "pass was claimed without any recorded assertion"
            failure_class = "timeout_or_no_observation"
        if status == "fail" and failed and reason is None:
            reason = f"assertion(s) failed: {', '.join(failed)}"
        if status == "fail" and failure_class is None:
            failure_class = "unclassified"
        if status == "pass":
            failure_class = None
        self.status = status
        self.reason = reason or ("all assertions held" if status == "pass" else status)
        self.failure_class = failure_class
        self.finished = True
        return self

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "failure_class": self.failure_class,
            "started_utc": self.started_utc,
            "duration_s": self.duration_s,
            "assertions": self.assertions,
            "observations": self.observations,
            "evidence": self.evidence,
        }


class CheckSet:
    def __init__(self, names: tuple[str, ...]):
        self.required = names
        self.checks: dict[str, Check] = {name: Check(name) for name in names}

    def __getitem__(self, name: str) -> Check:
        return self.checks[name]

    def block_unfinished(self, reason: str, failure_class: str) -> None:
        for check in self.checks.values():
            if not check.finished:
                check.finish("blocked", reason, failure_class)


def compute_overall(checks: dict[str, Check], required: tuple[str, ...],
                    run_errors: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Return (overall, failure_class). Pass needs every required check passed and no run error."""
    ordered = [checks.get(name) for name in required]
    missing = [name for name, check in zip(required, ordered) if check is None or not check.finished]
    failed = [check for check in ordered if check is not None and check.finished and check.status == "fail"]
    blocked = [check for check in ordered if check is not None and check.finished and check.status == "blocked"]
    unknown = [check for check in ordered if check is not None and check.finished and check.status == "unknown"]
    if failed:
        return "fail", failed[0].failure_class or "unclassified"
    if blocked:
        return "blocked", blocked[0].failure_class or "unclassified"
    if run_errors:
        return "blocked", run_errors[0].get("failure_class", "harness_or_protocol")
    if missing or unknown:
        return "unknown", None
    return "pass", None


def verify_evidence(out_dir: Path, result: dict[str, Any]) -> list[str]:
    """Recompute every recorded evidence digest against the bytes now on disk."""
    from .acquire import hash_file

    problems = []
    for entry in (result.get("evidence") or {}).get("files") or []:
        path = out_dir / entry["path"]
        if not path.is_file():
            problems.append(f"{entry['path']}: missing")
        elif path.stat().st_size != entry["bytes"] or hash_file(path) != entry["sha256"]:
            problems.append(f"{entry['path']}: changed after it was hashed")
    return problems


def reject_unverified_evidence(out_dir: Path, result: dict[str, Any]) -> list[str]:
    """Turn any digest mismatch into a run error and a non-pass result."""
    problems = verify_evidence(out_dir, result)
    if problems:
        result.setdefault("run_errors", []).append({
            "stage": "evidence", "message": "; ".join(problems), "failure_class": "harness_or_protocol",
            "t": utc_now()})
        if result.get("overall") == "pass":
            result["overall"], result["failure_class"] = "blocked", "harness_or_protocol"
    return problems


def write_result(out_dir: Path, result: dict[str, Any]) -> Path:
    path = out_dir / "result.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n", encoding="utf-8", newline="\n")
    return path


def render_summary(result: dict[str, Any]) -> str:
    selection = result.get("selection") or {}
    artifact = result.get("artifact") or {}
    lines = [
        f"# Compatibility run {result.get('run_id')}",
        "",
        f"- Row: `{selection.get('target_key')}`{' (vanilla control)' if selection.get('control') else ''}",
        *([f"- Server under test: Minecraft {selection['server_minecraft']} ({selection.get('server_basis')})"]
          if selection.get("server_minecraft") not in (None, selection.get("minecraft")) else []),
        f"- Artifact: `{selection.get('artifact_id')}` installed SHA-256 `{artifact.get('installed_sha256')}`",
        f"- Overall: **{result.get('overall')}**"
        + (f" ({result.get('failure_class')})" if result.get("failure_class") else ""),
        f"- Started/ended (UTC): {result.get('started_utc')} / {result.get('ended_utc')}",
        f"- Manifest SHA-256: `{result.get('manifest', {}).get('sha256')}`",
        "",
        "| Check | Status | Reason |",
        "| --- | --- | --- |",
    ]
    for name, check in result.get("checks", {}).items():
        reason = str(check.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        status = check.get("status")
        if check.get("failure_class"):
            status = f"{status} ({check['failure_class']})"
        lines.append(f"| {name} | {status} | {reason} |")
    if result.get("run_errors"):
        lines += ["", "Run errors:", ""]
        lines += [f"- {error.get('stage')}: {error.get('message')}" for error in result["run_errors"]]
    lines += ["", "Raw result: `result.json`. Exit code alone is not evidence.", ""]
    return "\n".join(lines)


def write_summary(out_dir: Path, result: dict[str, Any]) -> Path:
    path = out_dir / "summary.md"
    path.write_text(render_summary(result), encoding="utf-8", newline="\n")
    return path
