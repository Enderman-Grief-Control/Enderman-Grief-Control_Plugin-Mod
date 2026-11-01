import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from egc_compat.acquire import CachedFile
from egc_compat.manifest import load_manifest, select
from egc_compat.profiles import install_jars, jar_set, jar_set_assertions, profile_record
from egc_compat.server_row import Limits, ServerRow

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"

# Loader's refusal, as the 2026-10-25 probe logged it, and a start that gets past Loader.
REFUSAL_OUTPUT = """[17:11:27] [main/INFO]: Loading Minecraft 1.21 with Fabric Loader 0.19.5
[17:11:27] [main/WARN]: Mod resolution failed
[17:11:27] [main/ERROR]: Incompatible mods found!
net.fabricmc.loader.impl.FormattedException: Some of your mods are incompatible with the game or each other!
\t - Install fabric-api, any version.
\t - Mod 'Probe' (egc-requires-probe) 0.0.0 requires any version of fabric-api, which is missing!
"""
STARTED_OUTPUT = '[17:11:27] [Server thread/INFO]: Done (3.210s)! For help, type "help"\n'


def fabric_jar(mod_id: str, version: str, depends: dict | None = None, test_mode: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as jar:
        jar.writestr("fabric.mod.json", json.dumps({"id": mod_id, "version": version, "depends": depends or {}}))
        if test_mode is not None:
            jar.writestr("test-mode.properties", f"test-mode={test_mode}\n")
    return buffer.getvalue()


def synthetic_manifest(root: Path) -> Path:
    """The committed manifest, with the 1.21 server's project and Fabric API jars replaced by local fixtures."""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    jars = {"project.jar": fabric_jar("no-enderman-grief", "1.2.0", test_mode="false"),
            "api.jar": fabric_jar("fabric-api", "0.102.0+1.21"),
            "probe.jar": fabric_jar("egc-requires-probe", "0.0.0", {"fabric-api": "*"})}
    for name, data_bytes in jars.items():
        (root / name).write_bytes(data_bytes)

    def local(artifact: dict, name: str) -> None:
        artifact.pop("url", None)
        artifact.pop("generated", None)
        artifact.update(local_path=str(root / name), size=len(jars[name]),
                        sha256=hashlib.sha256(jars[name]).hexdigest())

    local(data["artifacts"]["fabric-1.2.0"], "project.jar")
    local(data["artifacts"]["fabric-api-0.102.0-1.21"], "api.jar")
    local(data["artifacts"]["fapi-requirement-probe"], "probe.jar")
    path = root / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class JarSetTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manifest = load_manifest(synthetic_manifest(self.root))

    def install(self, profile: str, control: bool = False):
        selection = select(self.manifest, "fabric", "server", "1.21", profile=profile)
        specs = jar_set(self.manifest, selection, control)
        sources = {spec.artifact_id: CachedFile(Path(spec.artifact["local_path"]), spec.artifact["sha256"],
                                                spec.artifact["size"], "fixture", False) for spec in specs}
        mods = self.root / f"mods-{profile}-{control}"
        records = install_jars(specs, sources, mods)
        return selection, specs, records, mods

    def test_standard_profile_installs_the_project_jar_and_fabric_api(self):
        selection, specs, records, mods = self.install("standard")
        self.assertEqual([(spec.role, spec.artifact_id) for spec in specs],
                         [("project", "fabric-1.2.0"), ("dependency", "fabric-api-0.102.0-1.21")])
        results = jar_set_assertions(mods, specs, records, "fabric")
        self.assertTrue(all(ok for _name, ok, _want, _got in results), results)
        record = profile_record(selection, records, control=False)
        self.assertEqual(record["name"], "standard")
        self.assertEqual([jar["installed_sha256"] for jar in record["jars"]], [spec.artifact["sha256"] for spec in specs])

    def test_control_drops_only_the_project_jar(self):
        _selection, specs, _records, _mods = self.install("standard", control=True)
        self.assertEqual([spec.artifact_id for spec in specs], ["fabric-api-0.102.0-1.21"])
        _selection, specs, _records, _mods = self.install("bundled", control=True)
        self.assertEqual(specs, [])

    def test_extra_or_tampered_jars_fail_the_jar_set_check(self):
        _selection, specs, records, mods = self.install("standard")
        (mods / "stowaway.jar").write_bytes(b"not part of the profile")
        (mods / specs[1].artifact["filename"]).write_bytes(fabric_jar("fabric-api", "0.0.1"))
        failed = [name for name, ok, _want, _got in jar_set_assertions(mods, specs, records, "fabric") if not ok]
        self.assertIn("installed jars are exactly the profile's jars", failed)
        self.assertIn("dependency fabric-api-0.102.0-1.21 SHA-256 matches lock", failed)
        self.assertIn("dependency fabric-api-0.102.0-1.21 descriptor matches pin", failed)


class ServerProfileRowTest(unittest.TestCase):
    def make_row(self, profile: str, artifact: str | None = None, control: bool = False) -> ServerRow:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        manifest = load_manifest(synthetic_manifest(root))
        row = ServerRow(manifest=manifest, selection=select(manifest, "fabric", "server", "1.21", artifact, profile),
                        out_dir=root / "run", cache_dir=root / "cache", limits=Limits(refusal_s=20), java="java",
                        node="node", xmx="1G", control=control, run_id="test", command_line=[], limit_overrides={},
                        git={})
        self.addCleanup(row.registry.kill_all)
        self.addCleanup(lambda: [log.close() for log in (row._runner_log, row._rcon_log)])
        launcher = root / "launcher.jar"
        launcher.write_bytes(b"launcher")
        return row, launcher

    def run_refusal(self, row: ServerRow, launcher: Path, output: str, exit_code: int = 1):
        def fake_acquire():
            row.jar_sources = {spec.artifact_id: CachedFile(Path(spec.artifact["local_path"]), spec.artifact["sha256"],
                                                            spec.artifact["size"], "fixture", False)
                               for spec in row.jar_specs}
            row.artifact_record.update({"source": "fixture"})
            return {"server": launcher}

        script = f"import sys; sys.stdout.write({output!r}); sys.stdout.flush(); sys.exit({exit_code})"
        with mock.patch("egc_compat.server_row.java_identity", return_value={"major": 21}), \
                mock.patch.object(row, "acquire", fake_acquire), \
                mock.patch.object(row.platform, "server_command", return_value=[sys.executable, "-c", script]):
            return row.run()

    def test_missing_dependency_profile_selects_only_the_refusal_check(self):
        row, _launcher = self.make_row("missing-dependency")
        self.assertEqual(row.checks.required, ("dependency_refusal",))
        self.assertEqual(row.jar_specs[0].role, "project")
        self.assertEqual(len(row.jar_specs), 1)

    def test_clean_refusal_from_a_jar_that_declares_fabric_api_passes(self):
        row, launcher = self.make_row("missing-dependency", "fapi-requirement-probe")
        result = self.run_refusal(row, launcher, REFUSAL_OUTPUT)
        check = result["checks"]["dependency_refusal"]
        self.assertEqual(check["status"], "pass", check)
        self.assertTrue(result["selection"]["fixture"])
        self.assertEqual(result["profile"]["name"], "missing-dependency")
        self.assertEqual([jar["artifact_id"] for jar in result["profile"]["jars"]], ["fapi-requirement-probe"])

    def test_refusal_without_a_declared_requirement_fails(self):
        row, launcher = self.make_row("missing-dependency")  # the project jar declares no fabric-api
        result = self.run_refusal(row, launcher, REFUSAL_OUTPUT)
        check = result["checks"]["dependency_refusal"]
        self.assertEqual(check["status"], "fail")
        # The reason names what did not hold, so a reader never takes a failed condition as observed.
        self.assertEqual(check["reason"],
                         "not a clean refusal; these did not hold: project descriptor declares fabric-api in depends")

    def test_a_start_that_reaches_done_fails_the_refusal(self):
        row, launcher = self.make_row("missing-dependency", "fapi-requirement-probe")
        result = self.run_refusal(row, launcher, STARTED_OUTPUT, exit_code=0)
        check = result["checks"]["dependency_refusal"]
        self.assertEqual(check["status"], "fail")
        self.assertIn("server never reached Done", check["reason"])
        self.assertEqual(result["overall"], "fail")

    def test_a_crash_is_not_a_clean_refusal(self):
        row, launcher = self.make_row("missing-dependency", "fapi-requirement-probe")
        crash = REFUSAL_OUTPUT + "---- Minecraft Crash Report ----\n"
        check = self.run_refusal(row, launcher, crash)["checks"]["dependency_refusal"]
        self.assertEqual(check["status"], "fail")
        self.assertIn("no crash report or crash marker", check["reason"])


if __name__ == "__main__":
    unittest.main()
