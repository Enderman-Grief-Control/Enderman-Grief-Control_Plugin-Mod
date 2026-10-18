import unittest

from egc_compat.ratelimit import Announcement, evaluate
from egc_compat.results import SERVER_CHECKS, Check, CheckSet, compute_overall


def passed(name: str) -> Check:
    check = Check(name).start()
    check.check("observed", True, "x", "x")
    return check.finish()


class CheckGuardTest(unittest.TestCase):
    def test_pass_without_assertions_is_blocked(self):
        check = Check("pickup").start().finish("pass")
        self.assertEqual(check.status, "blocked")
        self.assertEqual(check.failure_class, "timeout_or_no_observation")

    def test_derived_status_without_assertions_is_blocked(self):
        self.assertEqual(Check("pickup").start().finish().status, "blocked")

    def test_explicit_pass_with_failed_assertion_is_fail(self):
        check = Check("placement").start()
        check.check("arena unchanged", False, True, False)
        check.finish("pass")
        self.assertEqual(check.status, "fail")
        self.assertIn("arena unchanged", check.reason)

    def test_all_assertions_held_is_pass(self):
        self.assertEqual(passed("startup").status, "pass")


class OverallTest(unittest.TestCase):
    def all_passed(self) -> dict:
        return {name: passed(name) for name in SERVER_CHECKS}

    def test_every_required_check_passed(self):
        self.assertEqual(compute_overall(self.all_passed(), SERVER_CHECKS, []), ("pass", None))

    def test_missing_check_is_not_pass(self):
        checks = self.all_passed()
        del checks["persistence"]
        self.assertEqual(compute_overall(checks, SERVER_CHECKS, [])[0], "unknown")

    def test_run_error_rejects_pass(self):
        overall, failure_class = compute_overall(self.all_passed(), SERVER_CHECKS,
                                                 [{"stage": "stop", "message": "exit 1", "failure_class": "unclassified"}])
        self.assertEqual((overall, failure_class), ("blocked", "unclassified"))

    def test_fail_outranks_blocked(self):
        checks = self.all_passed()
        checks["pickup"] = Check("pickup").start().finish("blocked", "no attempt", "timeout_or_no_observation")
        failed = Check("startup").start()
        failed.check("Done", False, "Done", "crash")
        checks["startup"] = failed.finish("fail", "crashed", "artifact")
        self.assertEqual(compute_overall(checks, SERVER_CHECKS, []), ("fail", "artifact"))

    def test_block_unfinished_marks_only_unfinished(self):
        checks = CheckSet(SERVER_CHECKS)
        checks.checks["startup"] = passed("startup")
        checks.block_unfinished("startup crashed", "artifact")
        self.assertEqual(checks["startup"].status, "pass")
        self.assertEqual(checks["pickup"].status, "blocked")
        self.assertEqual(compute_overall(checks.checks, SERVER_CHECKS, []), ("blocked", "artifact"))


class RateLimitTest(unittest.TestCase):
    def test_burst_then_flush_after_pause(self):
        # 10 denials in a burst, paused past the cooldown, one more denial flushes x10.
        verdict = evaluate(11, [Announcement(1, 0.0), Announcement(10, 12.0)], 10,
                           paused_after_line=10, paused_window=(1.0, 11.5))
        self.assertTrue(verdict.ok, verdict.problems)
        self.assertEqual(verdict.triggering_lines, (1, 11))

    def test_first_announcement_must_be_x1(self):
        self.assertFalse(evaluate(5, [Announcement(5, 0.0)], 10).ok)

    def test_announcement_inside_cooldown_is_rejected(self):
        verdict = evaluate(3, [Announcement(1, 0.0), Announcement(2, 3.0)], 10)
        self.assertFalse(verdict.ok)

    def test_flush_must_roll_up_every_pending_denial(self):
        verdict = evaluate(11, [Announcement(1, 0.0), Announcement(4, 12.0)], 10, paused_after_line=10)
        self.assertFalse(verdict.ok)

    def test_more_announced_than_logged_is_rejected(self):
        self.assertFalse(evaluate(1, [Announcement(1, 0.0), Announcement(3, 12.0)], 10).ok)

    def test_no_announcement_is_rejected(self):
        self.assertFalse(evaluate(4, [], 10).ok)

    def test_announcement_during_pause_is_rejected(self):
        verdict = evaluate(11, [Announcement(1, 0.0), Announcement(10, 12.0)], 10,
                           paused_after_line=10, paused_window=(11.0, 13.0))
        self.assertFalse(verdict.ok)


if __name__ == "__main__":
    unittest.main()
