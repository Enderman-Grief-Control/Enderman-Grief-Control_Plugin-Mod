import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from egc_compat.bot import BOT_DIR, client_preflight, node_major


def bot_dir(root: Path, installed: str, locked: str = "4.39.0") -> Path:
    (root / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}}), encoding="utf-8")
    packages = {f"node_modules/{name}": {"version": version}
                for name, version in (("mineflayer", locked), ("minecraft-protocol", "1.68.0"))}
    (root / "package-lock.json").write_text(json.dumps({"packages": packages}), encoding="utf-8")
    for name, version in (("mineflayer", installed), ("minecraft-protocol", "1.68.0")):
        path = root / "node_modules" / name / "package.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"version": version}), encoding="utf-8")
    return root


class PreflightTest(unittest.TestCase):
    def test_declared_node_requirement_matches_the_locked_packages(self):
        package = json.loads((BOT_DIR / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((BOT_DIR / "package-lock.json").read_text(encoding="utf-8"))
        declared = node_major(package["engines"]["node"].lstrip(">= ") + ".")
        for name in ("mineflayer", "minecraft-protocol"):
            required = node_major(lock["packages"][f"node_modules/{name}"]["engines"]["node"].lstrip(">= ") + ".")
            self.assertGreaterEqual(declared, required, name)

    def test_non_node_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            result = client_preflight(sys.executable, bot_dir(Path(root), "4.39.0"))
            self.assertTrue(any("below the required" in problem for problem in result["problems"]))

    def test_installed_package_must_match_the_lock(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        with tempfile.TemporaryDirectory() as root:
            result = client_preflight(node, bot_dir(Path(root), "4.38.0"))
            self.assertTrue(any("lock pins 4.39.0" in problem for problem in result["problems"]))

    def test_missing_executable_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            result = client_preflight(str(Path(root) / "no-such-node"), bot_dir(Path(root), "4.39.0"))
            self.assertTrue(any("cannot run" in problem for problem in result["problems"]))


if __name__ == "__main__":
    unittest.main()
