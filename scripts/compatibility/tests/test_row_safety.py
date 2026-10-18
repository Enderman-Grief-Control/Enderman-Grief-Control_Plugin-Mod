import sys
import tempfile
import time
import unittest
from pathlib import Path

from egc_compat.manifest import load_manifest, select
from egc_compat.results import verify_evidence
from egc_compat.server_row import Limits, ServerRow

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


class RowSafetyTest(unittest.TestCase):
    def make_row(self, **limit_overrides) -> ServerRow:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        manifest = load_manifest(MANIFEST)
        limits = Limits()
        for key, value in limit_overrides.items():
            setattr(limits, key, value)
        row = ServerRow(manifest=manifest, selection=select(manifest, "paper", "server", "1.21"),
                        out_dir=Path(temp.name) / "run", cache_dir=Path(temp.name) / "cache", limits=limits,
                        java="java", node="node", xmx="1G", control=False, run_id="test", command_line=[],
                        limit_overrides={}, git={})
        self.addCleanup(row.registry.kill_all)  # runs before the temporary directory is removed
        self.addCleanup(lambda: [log.close() for log in (row._runner_log, row._rcon_log)])
        return row

    def test_cleanup_kills_and_redacts_even_when_stop_fails(self):
        row = self.make_row()
        row.server_dir.mkdir(parents=True)
        properties = row.server_dir / "server.properties"
        properties.write_text(f"rcon.password={row.rcon_password}\n", encoding="utf-8")
        process = row.registry.start("server-run1", SLEEPER, row.out_dir, row.out_dir / "server-run1.log")

        def failing_stop():
            raise OSError("fixture: stop or snapshot failed")

        row.stop_current_server = failing_stop
        row.cleanup()
        self.assertFalse(process.running())
        self.assertNotIn(row.rcon_password, properties.read_text(encoding="utf-8"))
        self.assertIn("REDACTED", properties.read_text(encoding="utf-8"))
        stages = [error["stage"] for error in row.run_errors]
        self.assertIn("cleanup", stages)

    def test_missing_jar_identity_rejects_a_would_be_pass(self):
        row = self.make_row()
        row.server_runs = [{"run": 1, "done": {"line": "Done"}}]
        identity = row.verify_identity_and_install()
        self.assertIsNone(identity["matches_pin"])
        self.assertTrue(any("not observed" in error["message"] for error in row.run_errors))

    def test_missing_jar_identity_does_not_mask_an_earlier_startup_failure(self):
        row = self.make_row()
        row.server_runs = [{"run": 1, "done": None, "exit_code": 1}]
        row.verify_identity_and_install()
        self.assertFalse(any("not observed" in error["message"] for error in row.run_errors))

    def test_recorded_evidence_digests_match_the_final_files(self):
        row = self.make_row()
        row.say("a line before finish")
        result = row.finish()
        runner_log = [entry for entry in result["evidence"]["files"] if entry["path"] == "runner.log"]
        self.assertEqual(len(runner_log), 1)
        self.assertEqual(verify_evidence(row.out_dir, result), [])
        self.assertNotEqual(result["overall"], "pass")

    def test_watchdog_stops_owned_processes_at_the_row_deadline(self):
        row = self.make_row(row_s=0.5)
        process = row.registry.start("server-run1", SLEEPER, row.out_dir, row.out_dir / "server-run1.log")
        row.start_watchdog()
        deadline = time.monotonic() + 15
        while process.running() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(row.deadline_hit.is_set())
        self.assertFalse(process.running())
        with self.assertRaises(Exception) as raised:
            row.ensure_time()
        self.assertIn("row time limit", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
