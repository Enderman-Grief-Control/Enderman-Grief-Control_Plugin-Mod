import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

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
            self.assertTrue((out / "summary.md").exists())

    def test_client_row_without_evidence_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "run"
            self.assertEqual(invoke("--platform", "fabric", "--side", "client", "--minecraft", "1.21.5",
                                    "--out", str(out)), 1)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["overall"], "blocked")
            self.assertEqual({check["failure_class"] for check in result["checks"].values()}, {"client_access"})

    def test_client_pass_without_evidence_file_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as root:
            template_dir = Path(root) / "form"
            self.assertEqual(invoke("--platform", "fabric", "--side", "client", "--minecraft", "1.21",
                                    "--client-evidence-template", "--out", str(template_dir)), 0)
            form_path = template_dir / "client-evidence.json"
            form = json.loads(form_path.read_text(encoding="utf-8"))
            (template_dir / "latest.log").write_text("client log", encoding="utf-8")
            for name, entry in form["checks"].items():
                entry["status"] = "pass"
                entry["observation"] = f"{name} observed"
                entry["evidence"] = ["latest.log"]
            form["checks"]["placement"]["evidence"] = ["missing-screenshot.png"]
            form_path.write_text(json.dumps(form), encoding="utf-8")
            out = Path(root) / "run"
            self.assertEqual(invoke("--platform", "fabric", "--side", "client", "--minecraft", "1.21",
                                    "--client-evidence", str(form_path), "--out", str(out)), 1)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["checks"]["pickup"]["status"], "pass")
            self.assertEqual(result["checks"]["placement"]["status"], "blocked")
            self.assertEqual(result["overall"], "blocked")

    def test_client_form_for_another_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            template_dir = Path(root) / "form"
            invoke("--platform", "fabric", "--side", "client", "--minecraft", "1.21",
                   "--client-evidence-template", "--out", str(template_dir))
            form_path = template_dir / "client-evidence.json"
            form = json.loads(form_path.read_text(encoding="utf-8"))
            form["artifact_sha256"] = "0" * 64
            form_path.write_text(json.dumps(form), encoding="utf-8")
            out = Path(root) / "run"
            invoke("--platform", "fabric", "--side", "client", "--minecraft", "1.21",
                   "--client-evidence", str(form_path), "--out", str(out))
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["overall"], "blocked")
            self.assertTrue(result["run_errors"])

    def client_form(self, root: str, minecraft: str = "1.21") -> tuple[Path, dict]:
        template_dir = Path(root) / "form"
        invoke("--platform", "fabric", "--side", "client", "--minecraft", minecraft,
               "--client-evidence-template", "--out", str(template_dir))
        form_path = template_dir / "client-evidence.json"
        (template_dir / "latest.log").write_text("client log", encoding="utf-8")
        form = json.loads(form_path.read_text(encoding="utf-8"))
        for entry in form["checks"].values():
            entry.update(status="pass", observation="observed", evidence=["latest.log"])
        return form_path, form

    def run_form(self, root: str, form_path: Path, form: dict, minecraft: str = "1.21") -> dict:
        form_path.write_text(json.dumps(form), encoding="utf-8")
        out = Path(root) / "run"
        invoke("--platform", "fabric", "--side", "client", "--minecraft", minecraft,
               "--client-evidence", str(form_path), "--out", str(out))
        return json.loads((out / "result.json").read_text(encoding="utf-8"))

    def test_client_form_identity_fields_must_match_the_row(self):
        for field, value in (("minecraft", "1.21.11"), ("profile_id", "fabric-loader-0.19.5-1.21.11"),
                             ("java_major", 17)):
            with tempfile.TemporaryDirectory() as root:
                form_path, form = self.client_form(root)
                form["environment"][field] = value
                result = self.run_form(root, form_path, form)
                self.assertEqual(result["overall"], "blocked", field)
                self.assertTrue(any(field in error["message"] for error in result["run_errors"]), field)
        with tempfile.TemporaryDirectory() as root:
            form_path, form = self.client_form(root)
            form["manifest_sha256"] = "0" * 64
            self.assertEqual(self.run_form(root, form_path, form)["overall"], "blocked")

    def test_mod_menu_pass_needs_a_pinned_mod_menu_jar(self):
        with tempfile.TemporaryDirectory() as root:
            form_path, form = self.client_form(root)
            result = self.run_form(root, form_path, form)
            self.assertEqual(result["checks"]["mod_menu_load"]["status"], "blocked")
            self.assertIn("pins no Mod Menu jar", result["checks"]["mod_menu_load"]["reason"])
            self.assertNotEqual(result["overall"], "pass")

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
