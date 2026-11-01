import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import candidate
from egc_compat.manifest import load_manifest

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"
COMMIT = "a" * 40


def write_jar(path: Path, platform: str, version: str, test_mode: str | None = "false") -> Path:
    with zipfile.ZipFile(path, "w") as jar:
        if platform == "fabric":
            jar.writestr("fabric.mod.json", json.dumps({"id": "no-enderman-grief", "version": version}))
        else:
            jar.writestr("plugin.yml", f"name: EndermanGriefControl\nversion: '{version}'\napi-version: '1.21'\n")
        if test_mode is not None:
            jar.writestr("test-mode.properties", f"test-mode={test_mode}\n")
    return path


class JarCheckTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_release_checks_accept_a_release_build(self):
        jar = write_jar(self.root / "EndermanGriefControl-mc_1.21-fabric-1.2.1.jar", "fabric", "1.2.1")
        self.assertEqual(candidate.jar_problems(jar, "fabric", "1.2.1"), [])
        paper = write_jar(self.root / "EndermanGriefControl-mc_1.21-paper-1.2.1.jar", "paper", "1.2.1")
        self.assertEqual(candidate.jar_problems(paper, "paper", "1.2.1"), [])

    def test_release_checks_reject_test_mode_a_wrong_name_or_version(self):
        test_mode = write_jar(self.root / "x-1.2.1.jar", "fabric", "1.2.1", test_mode="true")
        self.assertTrue(any("test mode" in p for p in candidate.jar_problems(test_mode, "fabric", "1.2.1")))
        missing = write_jar(self.root / "y-1.2.1.jar", "fabric", "1.2.1", test_mode=None)
        self.assertTrue(any("test mode" in p for p in candidate.jar_problems(missing, "fabric", "1.2.1")))
        named = write_jar(self.root / "z-1.2.1-SNAPSHOT.jar", "fabric", "1.2.1")
        self.assertTrue(any("does not end in -1.2.1.jar" in p for p in candidate.jar_problems(named, "fabric", "1.2.1")))
        version = write_jar(self.root / "w-1.2.1.jar", "fabric", "1.2.0")
        self.assertTrue(any("declares version '1.2.0'" in p for p in candidate.jar_problems(version, "fabric", "1.2.1")))

    def test_ids_are_never_reused(self):
        artifacts = {"fabric-1.2.1-cand-abcdef0": {}, "fabric-1.2.1-cand-abcdef0-2": {}}
        self.assertEqual(candidate.next_artifact_id(artifacts, "fabric-1.2.1-cand-abcdef0"), "fabric-1.2.1-cand-abcdef0-3")
        self.assertEqual(candidate.next_artifact_id({}, "paper-1.2.1-cand-1234567"), "paper-1.2.1-cand-1234567")
        (self.root / "paper-1.2.1-cand-1234567").mkdir()  # staged by an unwritten earlier run
        self.assertEqual(candidate.next_artifact_id({}, "paper-1.2.1-cand-1234567", self.root), "paper-1.2.1-cand-1234567-2")

    def test_recorded_commands_carry_no_host_paths(self):
        argv = [str(candidate.REPO / "fabric-mod" / "gradlew.bat"), "build", "-Pmod_version=1.2.1"]
        self.assertEqual(candidate.portable(argv), ["fabric-mod/gradlew.bat", "build", "-Pmod_version=1.2.1"])
        self.assertEqual(candidate.portable([r"C:\Program Files\Maven\bin\mvn.cmd", "-B"]), ["mvn.cmd", "-B"])


class RegisterTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
        self.candidates = self.root / "candidates"

    def register(self, jar: Path, *extra: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = candidate.main(["register", "--platform", "fabric", "--version", "1.2.1", "--jar", str(jar),
                                   "--source-commit", COMMIT, "--manifest", str(self.manifest),
                                   "--candidates", str(self.candidates), *extra])
        return code, out.getvalue() + err.getvalue()

    def test_registering_a_ci_jar_adds_a_valid_candidate(self):
        jar = write_jar(self.root / "EndermanGriefControl-mc_1.21-fabric-1.2.1.jar", "fabric", "1.2.1")
        code, output = self.register(jar, "--write")
        self.assertEqual(code, 0, output)
        manifest = load_manifest(self.manifest)
        entry = manifest.artifact("fabric-1.2.1-reg-aaaaaaa")
        self.assertEqual((entry["kind"], entry["source_commit"], entry["test_mode"]), ("candidate", COMMIT, "false"))
        self.assertEqual(entry["build"]["method"], "registered")
        self.assertTrue((self.manifest.parent / entry["local_path"]).is_file())
        self.assertEqual(manifest.target("fabric/server/1.21")["artifact_id"], "fabric-1.2.0")  # defaults unchanged
        code, output = self.register(jar, "--write")
        self.assertEqual(code, 1)
        self.assertIn("already in the manifest as fabric-1.2.1-reg-aaaaaaa", output)
        rebuilt = self.root / "rebuild" / jar.name  # another build of the same commit: different bytes
        rebuilt.parent.mkdir()
        write_jar(rebuilt, "fabric", "1.2.1")
        with zipfile.ZipFile(rebuilt, "a") as archive:
            archive.writestr("build-stamp.txt", "second build")
        code, output = self.register(rebuilt, "--write")
        self.assertEqual(code, 0, output)
        self.assertIn("fabric-1.2.1-reg-aaaaaaa-2", load_manifest(self.manifest).data["artifacts"])

    def test_without_write_the_manifest_is_unchanged(self):
        before = self.manifest.read_bytes()
        jar = write_jar(self.root / "EndermanGriefControl-mc_1.21-fabric-1.2.1.jar", "fabric", "1.2.1")
        code, output = self.register(jar)
        self.assertEqual(code, 0)
        self.assertIn("fabric-1.2.1-reg-aaaaaaa", output)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_a_test_mode_jar_is_refused(self):
        jar = write_jar(self.root / "EndermanGriefControl-mc_1.21-fabric-1.2.1.jar", "fabric", "1.2.1", "true")
        code, output = self.register(jar, "--write")
        self.assertEqual(code, 1)
        self.assertIn("test mode", output)
        self.assertFalse(self.candidates.exists())


if __name__ == "__main__":
    unittest.main()
