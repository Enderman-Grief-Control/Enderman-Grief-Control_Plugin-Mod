"""Fabric client/integrated-world rows.

The runner cannot drive a graphical client, so a client row is recorded from a manual evidence
form. Without a form every client check is ``blocked`` with ``client_access``; a dedicated-server
result is never copied into a client row.

The form is rejected unless its identity fields (row, manifest, artifact ID and SHA-256, Minecraft
version, Loader, profile, Java major, and Mod Menu jar when one is claimed) match the selected
manifest target. A form "pass" needs an observation and at least one existing evidence file, or it
is downgraded to blocked. Mod Menu checks are blocked while the row pins no Mod Menu jar. The
extended checks are required where the manifest says so, and any recorded failure rejects the row.
Everything here is manual evidence: the reported jar hash is the observer's claim, not a hash the
runner computed from an installed file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import RUNNER_VERSION
from .acquire import hash_file
from .manifest import Manifest, Selection
from .results import CLIENT_CHECKS, CLIENT_EXTENDED_CHECKS, STATUSES, Check, compute_overall, utc_now

FORM_VERSION = 1
MOD_MENU_CHECKS = ("mod_menu_load", "mod_menu_save_cancel")


def evidence_template(manifest: Manifest, selection: Selection) -> dict[str, Any]:
    target = selection.target
    mod_menu_id = target.get("mod_menu_artifact_id")
    return {
        "form_version": FORM_VERSION,
        "target_key": selection.key,
        "artifact_id": selection.artifact_id,
        "artifact_sha256": selection.artifact["sha256"],
        "manifest_sha256": manifest.sha256,
        "observer": "",
        "observed_utc": "",
        "environment": {
            "minecraft": selection.minecraft,
            "loader": target.get("loader"),
            "profile_id": target.get("profile_id"),
            "java_major": target.get("java_major"),
            "java": "",
            "launcher": "",
            "mod_menu_artifact_id": mod_menu_id,
            "mod_menu_sha256": manifest.artifact(mod_menu_id)["sha256"] if mod_menu_id else None,
            "os": "",
        },
        "checks": {
            name: {"status": "unknown", "observation": "", "evidence": []}
            for name in CLIENT_CHECKS + CLIENT_EXTENDED_CHECKS
        },
        "extended_checks_required": target.get("extended_checks_required"),
        "instructions": (
            "Install exactly the listed artifact (verify SHA-256) in a fresh profile with the pinned "
            "Loader. Set status to pass/fail/blocked/unknown per check, describe what you saw, and list "
            "evidence files (logs, screenshots) relative to this form. A pass without an observation and "
            "an existing evidence file is recorded as blocked. Mod Menu checks need the pinned Mod Menu jar."
        ),
    }


def form_problems(form: dict[str, Any], manifest: Manifest, selection: Selection) -> list[str]:
    target = selection.target
    environment = form.get("environment") or {}
    expected = {
        "form_version": (form.get("form_version"), FORM_VERSION),
        "target_key": (form.get("target_key"), selection.key),
        "artifact_id": (form.get("artifact_id"), selection.artifact_id),
        "artifact_sha256": (form.get("artifact_sha256"), selection.artifact["sha256"]),
        "manifest_sha256": (form.get("manifest_sha256"), manifest.sha256),
        "environment.minecraft": (environment.get("minecraft"), selection.minecraft),
        "environment.loader": (environment.get("loader"), target.get("loader")),
        "environment.profile_id": (environment.get("profile_id"), target.get("profile_id")),
        "environment.java_major": (environment.get("java_major"), target.get("java_major")),
    }
    return [f"{field} is {got!r}, expected {want!r}" for field, (got, want) in expected.items() if got != want]


def mod_menu_problem(form: dict[str, Any], manifest: Manifest, selection: Selection) -> str | None:
    mod_menu_id = selection.target.get("mod_menu_artifact_id")
    if mod_menu_id is None:
        return "this row pins no Mod Menu jar, so Mod Menu coverage cannot be claimed yet"
    environment = form.get("environment") or {}
    pinned = manifest.artifact(mod_menu_id)["sha256"]
    if environment.get("mod_menu_artifact_id") != mod_menu_id or environment.get("mod_menu_sha256") != pinned:
        return f"form does not report the pinned Mod Menu jar {mod_menu_id} ({pinned})"
    return None


def record_check(check: Check, entry: dict[str, Any], form_dir: Path, blocked_reason: str | None) -> list[dict]:
    status = entry.get("status", "unknown")
    observation = (entry.get("observation") or "").strip()
    files = []
    for relative in entry.get("evidence") or []:
        path = (form_dir / relative).resolve()
        if path.is_file() and path.is_relative_to(form_dir.resolve()):
            files.append({"path": relative, "sha256": hash_file(path), "bytes": path.stat().st_size})
    check.evidence = files
    if observation:
        check.observe(observation)
    if status not in STATUSES:
        check.finish("blocked", f"form status {status!r} is not recognized", "client_access")
    elif status == "fail":
        # A reported failure always counts, even where the check would otherwise be blocked.
        check.finish("fail", observation or "reported as failed", "unclassified")
    elif blocked_reason is not None:
        check.finish("blocked", blocked_reason, "client_access")
    elif status == "pass":
        check.check("manual observation recorded", bool(observation), "observation", observation)
        check.check("evidence file present", bool(files), "at least one existing file inside the form directory",
                    [f["path"] for f in files])
        if observation and files:
            check.finish()
        else:
            check.finish("blocked", "pass without observation/evidence", "client_access")
    elif status == "blocked":
        check.finish("blocked", observation or "reported as blocked", "client_access")
    else:
        check.finish("unknown", observation or "not recorded")
    return files


def run_client_row(*, manifest: Manifest, selection: Selection, out_dir: Path, form_path: Path | None,
                   run_id: str, command_line: list[str], git: dict[str, Any]) -> dict[str, Any]:
    started = utc_now()
    names = CLIENT_CHECKS + CLIENT_EXTENDED_CHECKS
    checks = {name: Check(name) for name in names}
    extended_required = bool(selection.target.get("extended_checks_required"))
    required = names if extended_required else CLIENT_CHECKS
    run_errors: list[dict[str, Any]] = []
    form: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = []
    if form_path is None:
        reason = ("automated client driving is not available; record the manual evidence form "
                  "(--client-evidence-template) and rerun with --client-evidence")
        for check in checks.values():
            check.finish("blocked", reason, "client_access")
    else:
        form = json.loads(form_path.read_text(encoding="utf-8"))
        problems = form_problems(form, manifest, selection)
        if problems:
            run_errors += [{"stage": "client_evidence", "message": problem, "failure_class": "client_access"}
                           for problem in problems]
            for check in checks.values():
                check.finish("blocked", "manual evidence form rejected: " + "; ".join(problems), "client_access")
        else:
            mod_menu_blocked = mod_menu_problem(form, manifest, selection)
            for name, check in checks.items():
                entry = (form.get("checks") or {}).get(name) or {}
                blocked = mod_menu_blocked if name in MOD_MENU_CHECKS else None
                evidence += record_check(check.start(), entry, form_path.parent, blocked)
    overall, failure_class = compute_overall(checks, required, run_errors)
    failed = [check for check in checks.values() if check.status == "fail"]
    if failed and overall != "fail":
        overall, failure_class = "fail", failed[0].failure_class
    return {
        "schema_version": 1,
        "run_id": run_id,
        "started_utc": started,
        "ended_utc": utc_now(),
        "runner": {"version": RUNNER_VERSION, "command": command_line, **git},
        "manifest": {"path": str(manifest.path), "sha256": manifest.sha256},
        "selection": {"target_key": selection.key, "platform": selection.platform, "side": selection.side,
                      "minecraft": selection.minecraft, "artifact_id": selection.artifact_id,
                      "artifact_overridden": selection.artifact_overridden, "control": False},
        "target_pins": selection.target,
        "artifact": {"artifact_id": selection.artifact_id, "filename": selection.artifact["filename"],
                     "expected_sha256": selection.artifact["sha256"],
                     "reported_sha256": form.get("artifact_sha256") if form else None,
                     "note": "manual evidence: the reported hash is the observer's claim, not computed by the runner"},
        "environment": (form or {}).get("environment"),
        "client_evidence_form": str(form_path) if form_path else None,
        "checks": {name: checks[name].to_json() for name in CLIENT_CHECKS},
        "extended_checks": {name: checks[name].to_json() for name in CLIENT_EXTENDED_CHECKS},
        "required_checks": list(required),
        "overall": overall,
        "failure_class": failure_class,
        "run_errors": run_errors,
        "evidence": {"out_dir": str(out_dir), "files": evidence},
    }
