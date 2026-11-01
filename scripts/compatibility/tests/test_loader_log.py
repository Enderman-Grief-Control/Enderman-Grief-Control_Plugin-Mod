import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from egc_compat import loader_log

# Shapes copied from the 10E server and 10F client logs.
MIXIN_LINE = ("[16:34:59] [main/ERROR]: Mixin apply for mod fabric-entity-events-v1 failed "
              "fabric-entity-events-v1.mixins.json:EntityMixin from mod fabric-entity-events-v1 -> "
              "net.minecraft.class_1297: org.spongepowered.asm.mixin.injection.throwables.InvalidInjectionException "
              "Critical injection failure: @Inject annotation on afterEntityTeleportedToWorld could not find any targets")


def tree_log(events_parent: str, events_version: str) -> list[str]:
    """A "Loading N mods:" listing with fabric-entity-events-v1 under ``events_parent``."""
    project = ["\t- no-enderman-grief 1.2.0",
               "\t   |-- adventure-platform-fabric 5.14.0",
               "\t   |    \\-- net_kyori_adventure-api 4.17.0",
               "\t   |-- fabric-api-base 0.4.42+6573ed8cd1"]
    api = ["\t- fabric-api 0.128.2+1.21.5",
           "\t   |-- fabric-api-base 0.4.62+73a52b4b49"]
    events = f"\t   \\-- fabric-entity-events-v1 {events_version}"
    if events_parent == "no-enderman-grief":
        project.append(events)
    else:
        api.append(events)
    return ["[16:34:57] [main/INFO]: Loading Minecraft 1.21.5 with Fabric Loader 0.19.5",
            "[16:34:57] [main/INFO]: Loading 24 mods:",
            *api,
            "\t- fabricloader 0.19.5",
            "\t   \\-- mixinextras 0.5.5",
            "\t- java 21",
            "\t- minecraft 1.21.5",
            *project,
            "[16:34:57] [main/INFO]: SpongePowered MIXIN Subsystem Version=0.8.7",
            MIXIN_LINE]


def jar_bytes(mod_id: str, version: str, nested: list[bytes] = (), mixins: list[str] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as jar:
        jars = []
        for index, data in enumerate(nested):
            path = f"META-INF/jars/nested-{index}.jar"
            jar.writestr(path, data)
            jars.append({"file": path})
        jar.writestr("fabric.mod.json", json.dumps({"id": mod_id, "version": version, "jars": jars,
                                                    "mixins": list(mixins)}))
    return buffer.getvalue()


class LogFactsTest(unittest.TestCase):
    def test_mod_tree_records_each_entry_with_its_top_level_jar(self):
        entries = {entry["id"]: entry for entry in loader_log.mod_tree(tree_log("no-enderman-grief", "1.6.12"))}
        self.assertEqual(entries["no-enderman-grief"]["root"], "no-enderman-grief")
        self.assertEqual(entries["net_kyori_adventure-api"]["root"], "no-enderman-grief")
        self.assertEqual(entries["net_kyori_adventure-api"]["parent"], "adventure-platform-fabric")
        self.assertEqual(entries["mixinextras"]["root"], "fabricloader")
        self.assertEqual(entries["fabric-entity-events-v1"]["version"], "1.6.12")
        self.assertEqual(loader_log.banner(tree_log("fabric-api", "2.0.1")), ("1.21.5", "0.19.5"))

    def test_mixin_failure_names_the_mod_and_config(self):
        failures = loader_log.mixin_failures([MIXIN_LINE])
        self.assertEqual((failures[0]["mod"], failures[0]["config"]),
                         ("fabric-entity-events-v1", "fabric-entity-events-v1.mixins.json"))

    def test_refusal_facts_from_the_2026_10_25_probe_log(self):
        lines = ["[17:11:27] [main/WARN]: Mod resolution failed",
                 "[17:11:27] [main/ERROR]: Incompatible mods found!",
                 "\t - Install fabric-api, any version.",
                 "\t - Mod 'Fabric API requirement probe' (egc-requires-probe) 0.0.0 requires any version of "
                 "fabric-api, which is missing!"]
        facts = loader_log.refusal_facts(lines, ("fabric-api",))
        self.assertTrue(facts["incompatible_mods_found"])
        self.assertIsNotNone(facts["named"]["fabric-api"])
        self.assertEqual((facts["mixin_errors"], facts["crash_markers"]), ([], []))
        other = loader_log.refusal_facts(lines, ("fabric-api-base",))
        self.assertIsNone(other["named"]["fabric-api-base"])


class AttributionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        bundled = jar_bytes("fabric-entity-events-v1", "1.6.12", mixins=["fabric-entity-events-v1.mixins.json"])
        newer = jar_bytes("fabric-entity-events-v1", "2.0.1", mixins=["fabric-entity-events-v1.mixins.json"])
        (self.root / "project.jar").write_bytes(jar_bytes("no-enderman-grief", "1.2.0", [bundled]))
        (self.root / "api.jar").write_bytes(jar_bytes("fabric-api", "0.128.2+1.21.5", [newer]))
        (self.root / "modmenu.jar").write_bytes(jar_bytes("modmenu", "16.0.1", [
            jar_bytes("fabric-resource-loader-v0", "3.2.2+3388a3b287")]))
        records = [{"role": "project", "artifact_id": "fabric-1.2.0", "filename": "project.jar"},
                   {"role": "dependency", "artifact_id": "fabric-api-0.128.2-1.21.5", "filename": "api.jar"},
                   {"role": "dependency", "artifact_id": "modmenu-16.0.1", "filename": "modmenu.jar"}]
        self.jars = loader_log.installed_jars(records, self.root)

    def test_failure_in_the_project_tree_is_the_artifact(self):
        attribution = loader_log.attribute(tree_log("no-enderman-grief", "1.6.12"), self.jars)
        self.assertEqual((attribution.role, attribution.supplied_by), ("project", "fabric-1.2.0"))
        self.assertEqual(loader_log.failure_class(attribution, control=False)[0], "artifact")

    def test_same_named_module_loaded_from_fabric_api_is_not_blamed_on_the_project(self):
        attribution = loader_log.attribute(tree_log("fabric-api", "2.0.1"), self.jars)
        self.assertEqual((attribution.role, attribution.supplied_by), ("dependency", "fabric-api-0.128.2-1.21.5"))
        self.assertEqual(loader_log.failure_class(attribution, control=False)[0], "unclassified")
        self.assertEqual(loader_log.failure_class(attribution, control=True)[0], "game_or_loader")

    def test_without_a_tree_the_failing_version_picks_the_jar(self):
        lines = [MIXIN_LINE]
        self.assertIsNone(loader_log.attribute(lines, self.jars).supplied_by)  # no version: ambiguous
        resolution = ["[17:55:05] [main/ERROR]: Incompatible mods found!",
                      "\t - Mod 'Fabric Resource Loader (v0)' (fabric-resource-loader-v0) 3.2.2+3388a3b287 requires "
                      "any version of fabric-resource-loader-v1, which is missing!"]
        attribution = loader_log.attribute(resolution, self.jars)
        self.assertEqual((attribution.kind, attribution.supplied_by), ("resolution", "modmenu-16.0.1"))

    def test_resolution_failure_in_the_project_is_the_artifact(self):
        lines = ["[17:11:27] [main/ERROR]: Incompatible mods found!",
                 "\t - Mod 'Enderman Grief Control' (no-enderman-grief) 1.2.0 requires any version of fabric-api, "
                 "which is missing!"]
        attribution = loader_log.attribute(lines, self.jars)
        self.assertEqual(loader_log.failure_class(attribution, control=False)[0], "artifact")

    def test_no_failure_is_no_attribution(self):
        self.assertIsNone(loader_log.attribute(["[main/INFO]: Done (1.0s)!"], self.jars))
        self.assertEqual(loader_log.failure_class(None, control=False)[0], "unclassified")


if __name__ == "__main__":
    unittest.main()
