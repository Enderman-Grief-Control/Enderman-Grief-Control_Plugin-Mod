import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import matrix
from egc_compat.server_row import redact_rcon_password

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


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
