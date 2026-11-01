import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import matrix
from egc_compat.client_row import pack_paths
from egc_compat.manifest import MINECRAFT_VERSIONS, load_manifest
from egc_compat.server_row import redact_rcon_password

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
MANIFEST = Path(matrix.__file__).parent / "manifest.json"


def run(overall: str) -> dict:
    return {"overall": overall}


class RowVerdictTest(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST)

    def verdict(self, side: str, runs: dict, fixture: bool = False) -> str:
        return matrix.row_verdict(self.manifest, "fabric", side, runs, fixture)

    def test_profile_choice(self):
        self.assertEqual(matrix.choose_profiles(self.manifest, "fabric", "client", "documented"),
                         ["standard", "modmenu", "missing-dependency"])
        self.assertEqual(matrix.choose_profiles(self.manifest, "fabric", "server", "documented"),
                         ["standard", "missing-dependency"])
        self.assertEqual(matrix.choose_profiles(self.manifest, "paper", "server", "documented"), ["standard"])
        self.assertIn("bundled", matrix.choose_profiles(self.manifest, "fabric", "server", "all"))
        with self.assertRaises(ValueError):
            matrix.choose_profiles(self.manifest, "fabric", "server", "modmenu")

    def test_a_row_passes_only_when_every_documented_profile_passed(self):
        passing = {"standard": run("pass"), "missing-dependency": run("pass")}
        self.assertEqual(self.verdict("server", passing), "pass")
        self.assertEqual(self.verdict("server", {**passing, "missing-dependency": run("fail")}), "fail")
        self.assertEqual(self.verdict("server", {"standard": run("pass")}), "incomplete")
        self.assertEqual(self.verdict("server", {**passing, "standard": run("blocked")}), "blocked")

    def test_undocumented_profiles_never_count(self):
        runs = {"standard": run("pass"), "missing-dependency": run("pass"), "bundled": run("fail")}
        self.assertEqual(self.verdict("server", runs), "pass")
        self.assertEqual(self.verdict("server", {"bundled": run("pass")}), "incomplete")

    def test_fixture_rows_are_labeled_not_judged(self):
        self.assertEqual(self.verdict("server", {"missing-dependency": run("pass")}, fixture=True), "fixture")


class ClientPathPreflightTest(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST)

    def problems(self, out: Path, versions: list[str], profiles: list[str], extra: list[str] | None = None) -> list[str]:
        return matrix.client_path_problems(self.manifest, versions, profiles, None, out, extra or [])

    def test_a_folder_too_deep_for_windows_is_refused_before_any_run(self):
        runs = Path("C:/Temp/CS/MyWork/Professional/Enderman-Grief-Control/Enderman-Grief-Control_Plugin-Mod/"
                    "test-servers/compat-runs")
        documented = ["standard", "modmenu", "missing-dependency"]
        self.assertEqual(self.problems(runs / "matrix-10f4-fabric-client-1.2.1", list(MINECRAFT_VERSIONS), documented),
                         [])
        deep = self.problems(runs / ("m" * 60), ["1.21.10"], documented)
        self.assertTrue(deep)
        self.assertTrue(all("characters, over the 250 allowed" in problem for problem in deep))

    def test_extended_worlds_are_checked_only_where_they_are_written(self):
        core_tail, ui_tail, _ = (len(str(path)) for path in pack_paths(Path("."), True))
        self.assertGreater(ui_tail, core_tail)
        # <out>/1.21.11/modmenu/<tail> is len(out) + 17 + len(tail): the holder worlds go 1 over, the core world fits.
        out = Path("C:/" + "r" * (250 - 17 - ui_tail + 1 - 3))
        self.assertEqual(len(self.problems(out, ["1.21.11"], ["modmenu"])), 2)
        self.assertEqual(self.problems(out, ["1.21.11"], ["modmenu"], ["--control"]), [])
        self.assertEqual(self.problems(out, ["1.21.1"], ["modmenu"]), [])  # no extended checks on 1.21.1


class MatrixKillTest(unittest.TestCase):
    def test_redaction_by_key_keeps_other_lines_and_line_endings(self):
        with tempfile.TemporaryDirectory() as root:
            properties = Path(root) / "server.properties"
            properties.write_bytes(b"enable-rcon=true\r\nrcon.password=s3cret-value\r\nrcon.port=25575\r\n")
            self.assertTrue(redact_rcon_password(properties))
            self.assertEqual(properties.read_bytes(),
                             b"enable-rcon=true\r\nrcon.password=REDACTED\r\nrcon.port=25575\r\n")
            self.assertFalse(redact_rcon_password(properties))
            self.assertFalse(redact_rcon_password(Path(root) / "missing.properties"))

    def test_outer_limit_kills_the_row_and_redacts_its_password(self):
        with tempfile.TemporaryDirectory() as root:
            row_out = Path(root) / "1.21"
            (row_out / "server").mkdir(parents=True)
            properties = row_out / "server" / "server.properties"
            properties.write_bytes(b"rcon.password=s3cret-value\n")
            with contextlib.redirect_stdout(io.StringIO()):
                code, problem = matrix.run_row(SLEEPER, 0.5, row_out)
            self.assertIsNone(code)
            self.assertIsNone(problem)
            self.assertEqual(properties.read_bytes(), b"rcon.password=REDACTED\n")
            record = matrix.row_record("1.21", Path(root), row_out, code, problem)
            self.assertEqual(record["overall"], "blocked")
            self.assertIn("outer row limit", record["note"])


if __name__ == "__main__":
    unittest.main()
