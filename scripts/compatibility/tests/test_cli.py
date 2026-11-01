import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run


def invoke(*args: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return run.main(list(args))


class CliTest(unittest.TestCase):
    def test_unavailable_row_is_a_blocked_result(self):
        with tempfile.TemporaryDirectory() as root:
            data = json.loads(run.HERE.joinpath("manifest.json").read_text(encoding="utf-8"))
            data["targets"]["paper/server/1.21.6"] = {
                "artifact_id": "paper-1.2.0",
                "unavailable": {"reason": "no official server build", "evidence_url": "https://example.invalid/",
                                "checked_utc": "2026-10-18T00:00:00Z"}}
            manifest = Path(root) / "manifest.json"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            out = Path(root) / "run"
            self.assertEqual(invoke("--manifest", str(manifest), "--platform", "paper", "--minecraft", "1.21.6",
                                    "--out", str(out)), 1)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["overall"], "blocked")
            self.assertEqual(result["failure_class"], "environment_or_download")
            self.assertEqual(result["profile"]["name"], "standard")
            self.assertTrue((out / "summary.md").exists())

    def test_client_row_off_windows_is_blocked_before_anything_launches(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch("egc_compat.client.win32.available", return_value=False), \
                mock.patch("egc_compat.client_row.ClientRow.acquire") as acquire:
            out = Path(root) / "run"
            self.assertEqual(invoke("--platform", "fabric", "--side", "client", "--minecraft", "1.21.5",
                                    "--profile", "modmenu", "--cache", str(Path(root) / "cache"), "--out", str(out)), 1)
            acquire.assert_not_called()
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["overall"], "blocked")
            self.assertEqual(result["evidence_source"], "automated")
            checks = {**result["checks"], **result["extended_checks"]}
            self.assertEqual({check["failure_class"] for check in checks.values()}, {"client_access"})
            self.assertIn("monitor_state", result["required_checks"])  # 1.21.5 requires the extended checks

    def test_invalid_client_invocations_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            out = str(Path(root) / "run")
            base = ("--platform", "fabric", "--minecraft", "1.21", "--out", out)
            self.assertEqual(invoke(*base, "--side", "server", "--negative-control"), 2)
            self.assertEqual(invoke(*base, "--side", "client", "--control", "--negative-control"), 2)
            self.assertEqual(invoke(*base, "--side", "client", "--profile", "missing-dependency", "--control"), 2)
            self.assertEqual(invoke(*base, "--side", "server", "--profile", "modmenu"), 2)
            self.assertEqual(invoke(*base, "--side", "client", "--limit", "server_start_s=5"), 2)
            self.assertFalse(Path(out).exists())

    def test_profile_listing(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(run.main(["--list-profiles"]), 0)
        self.assertIn("missing-dependency", output.getvalue())
        self.assertIn("Loader refuses, naming fabric-api", output.getvalue())

    def client_form(self, root: str, minecraft: str = "1.21", manifest: Path = run.HERE / "manifest.json",
                    profile: str = "modmenu") -> tuple[Path, dict]:
        template_dir = Path(root) / "form"
        invoke("--manifest", str(manifest), "--platform", "fabric", "--side", "client", "--minecraft", minecraft,
               "--profile", profile, "--client-evidence-template", "--out", str(template_dir))
        form_path = template_dir / "client-evidence.json"
        (template_dir / "latest.log").write_text("client log", encoding="utf-8")
        form = json.loads(form_path.read_text(encoding="utf-8"))
        for entry in form["checks"].values():
            entry.update(status="pass", observation="observed", evidence=["latest.log"])
        return form_path, form

    def run_form(self, root: str, form_path: Path, form: dict, minecraft: str = "1.21",
                 manifest: Path = run.HERE / "manifest.json", profile: str = "modmenu") -> dict:
        form_path.write_text(json.dumps(form), encoding="utf-8")
        out = Path(root) / "run"
        invoke("--manifest", str(manifest), "--platform", "fabric", "--side", "client", "--minecraft", minecraft,
               "--profile", profile, "--client-evidence", str(form_path), "--out", str(out))
        return json.loads((out / "result.json").read_text(encoding="utf-8"))

    def test_manual_form_is_labeled_and_a_pass_needs_an_evidence_file(self):
        with tempfile.TemporaryDirectory() as root:
            form_path, form = self.client_form(root)
            form["checks"]["placement"]["evidence"] = ["missing-screenshot.png"]
            result = self.run_form(root, form_path, form)
            self.assertEqual(result["evidence_source"], "manual")
            self.assertEqual(result["checks"]["pickup"]["status"], "pass")
            self.assertEqual(result["checks"]["placement"]["status"], "blocked")
            self.assertEqual(result["overall"], "blocked")

    def test_client_form_identity_fields_must_match_the_row(self):
        for field, value in (("minecraft", "1.21.11"), ("profile_id", "fabric-loader-0.19.5-1.21.11"),
                             ("java_major", 17)):
            with tempfile.TemporaryDirectory() as root:
                form_path, form = self.client_form(root)
                form["environment"][field] = value
                result = self.run_form(root, form_path, form)
                self.assertEqual(result["overall"], "blocked", field)
                self.assertTrue(any(field in error["message"] for error in result["run_errors"]), field)
        for field, value in (("manifest_sha256", "0" * 64), ("artifact_sha256", "0" * 64),
                             ("install_profile", "standard"), ("form_version", 1)):
            with tempfile.TemporaryDirectory() as root:
                form_path, form = self.client_form(root)
                form[field] = value
                self.assertEqual(self.run_form(root, form_path, form)["overall"], "blocked", field)

    def test_mod_menu_pass_needs_the_modmenu_profile(self):
        with tempfile.TemporaryDirectory() as root:
            form_path, form = self.client_form(root, profile="standard")
            result = self.run_form(root, form_path, form, profile="standard")
            self.assertEqual(result["checks"]["mod_menu_load"]["status"], "blocked")
            self.assertIn("installs no Mod Menu", result["checks"]["mod_menu_load"]["reason"])
            self.assertNotEqual(result["overall"], "pass")

    def test_mod_menu_pass_needs_the_pinned_jar_reported(self):
        with tempfile.TemporaryDirectory() as root:
            form_path, form = self.client_form(root)
            self.assertEqual(form["environment"]["mod_menu_artifact_id"], "modmenu-11.0.5")
            form["environment"]["mod_menu_sha256"] = "0" * 64
            result = self.run_form(root, form_path, form)
            self.assertEqual(result["checks"]["mod_menu_load"]["status"], "blocked")
            self.assertIn("does not report the pinned Mod Menu jar", result["checks"]["mod_menu_load"]["reason"])
        with tempfile.TemporaryDirectory() as root:
            form_path, form = self.client_form(root)
            result = self.run_form(root, form_path, form)
            self.assertEqual(result["checks"]["mod_menu_load"]["status"], "pass")

    def test_any_recorded_extended_failure_rejects_the_row(self):
        with tempfile.TemporaryDirectory() as root:  # 1.21.4 does not require extended checks
            form_path, form = self.client_form(root, "1.21.4")
            form["checks"]["world_transition"]["status"] = "fail"
            result = self.run_form(root, form_path, form, "1.21.4")
            self.assertEqual(result["overall"], "fail")
            self.assertEqual(result["extended_checks"]["world_transition"]["status"], "fail")

    def test_extended_checks_gate_boundary_rows(self):
        with tempfile.TemporaryDirectory() as root:
            form_path, form = self.client_form(root, "1.21.5")
            form["checks"]["monitor_state"]["status"] = "unknown"
            result = self.run_form(root, form_path, form, "1.21.5")
            self.assertIn("monitor_state", result["required_checks"])
            self.assertNotEqual(result["overall"], "pass")

    def test_non_empty_out_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "old.txt").write_text("previous run", encoding="utf-8")
            self.assertEqual(invoke("--platform", "paper", "--minecraft", "1.21", "--out", root), 2)

    def test_unknown_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(invoke("--platform", "paper", "--minecraft", "1.21", "--artifact", "nope",
                                    "--out", str(Path(root) / "run")), 2)


if __name__ == "__main__":
    unittest.main()
