import sys
import tempfile
import time
import unittest
from pathlib import Path

from egc_compat.manifest import load_manifest, select
from egc_compat.results import verify_evidence
from egc_compat.server_row import Limits, ServerRow, StageBlocked

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

    def announcement_state(self, flush_count, ok=True):
        def chat(text):
            return {"CompatOp": text, "CompatGuest": text}

        verdict = {"ok": ok, "problems": [] if ok else ["fixture"], "counts": [1, flush_count],
                   "triggering_lines": [1, 1 + flush_count], "flush_count": flush_count}
        return {"cooldown": {"first": chat("[Enderman] Denied pickup x1."),
                             "verdicts": {"CompatOp": verdict, "CompatGuest": dict(verdict)}},
                "placement_chat": chat("[Enderman] Denied placement x1."),
                "alert_chat": chat("[Enderman] holding a block at (8, 100, 8)."),
                "clear_chat": chat("[Enderman] holding block cleared x1.")}

    def test_cooldown_without_a_held_back_denial_is_blocked_not_passed(self):
        row = self.make_row()
        row.state.update(self.announcement_state(flush_count=1))
        row.check_announcements()
        check = row.checks["announcements_cooldown"]
        self.assertEqual(check.status, "blocked")
        self.assertIn("held back by the cooldown", check.reason)

    def test_cooldown_with_a_rolled_up_flush_passes(self):
        row = self.make_row()
        row.state.update(self.announcement_state(flush_count=4))
        row.check_announcements()
        self.assertEqual(row.checks["announcements_cooldown"].status, "pass")

    def test_cooldown_contract_violation_still_fails(self):
        row = self.make_row()
        row.state.update(self.announcement_state(flush_count=1, ok=False))
        row.check_announcements()
        self.assertEqual(row.checks["announcements_cooldown"].status, "fail")

    def test_restart_killed_by_the_watchdog_is_a_timeout_not_a_failed_start(self):
        row = self.make_row()
        row.deadline_hit.set()
        row.stop_server = lambda run_index: 0
        row.start_server = lambda run_index, check: False
        row.server_runs = [{"run": 2, "exit_code": 1}]
        check = row.checks["persistence"].start()
        with self.assertRaises(StageBlocked) as raised:
            row.restart(check)
        self.assertEqual(raised.exception.failure_class, "timeout_or_no_observation")
        self.assertFalse([assertion for assertion in check.assertions if not assertion["ok"]])

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
