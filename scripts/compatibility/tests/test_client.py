import json
import tempfile
import unittest
from pathlib import Path

from egc_compat.acquire import AcquisitionError
from egc_compat.client import scenario
from egc_compat.client.allowlist import classify
from egc_compat.client.launch import (ClientFiles, Library, check_profile, launch_arguments, library_key, maven_path,
                                      offline_uuid, plan_libraries, quote_arg, rules_allow)
from egc_compat.client.world import pack_mcmeta, write_pack
from egc_compat.client_row import client_check_names
from egc_compat.manifest import load_manifest, select

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"
TARGET = {"profile_id": "fabric-loader-0.19.5-1.21", "loader_main_class": "net.fabricmc.loader.impl.launch.knot.KnotClient",
          "loader_libraries": [{"path": "net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar", "sha256": "a" * 64},
                               {"path": "org/ow2/asm/asm/9.10.1/asm-9.10.1.jar", "sha256": "b" * 64}]}
PROFILE = {"id": "fabric-loader-0.19.5-1.21", "inheritsFrom": "1.21",
           "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
           "arguments": {"jvm": ["-DFabricMcEmu= net.minecraft.client.main.Main "], "game": []},
           "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5", "url": "https://maven.fabricmc.net/"},
                         {"name": "org.ow2.asm:asm:9.10.1", "url": "https://maven.fabricmc.net/"}]}


def mojang_library(name: str, rules: list | None = None) -> dict:
    entry = {"name": name, "downloads": {"artifact": {"path": maven_path(name), "url": f"https://libraries/{maven_path(name)}",
                                                      "sha1": "c" * 40, "size": 10}}}
    if rules:
        entry["rules"] = rules
    return entry


VERSION = {
    "libraries": [mojang_library("org.ow2.asm:asm:9.6"),  # the Fabric copy wins
                  mojang_library("org.lwjgl:lwjgl:3.3.3"),
                  mojang_library("org.lwjgl:lwjgl:3.3.3:natives-windows", [{"action": "allow", "os": {"name": "windows"}}]),
                  mojang_library("org.lwjgl:lwjgl:3.3.3:natives-linux", [{"action": "allow", "os": {"name": "linux"}}]),
                  mojang_library("org.lwjgl:lwjgl:3.3.3:natives-windows-arm64",
                                 [{"action": "allow", "os": {"name": "windows", "arch": "arm64"}}])],
    "arguments": {
        "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}",
                {"rules": [{"action": "allow", "os": {"name": "windows"}}], "value": "-XX:HeapDumpPath=x.heapdump"}],
        "game": ["--username", "${auth_player_name}", "--gameDir", "${game_directory}",
                 {"rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
                  "value": ["--width", "${resolution_width}", "--height", "${resolution_height}"]},
                 {"rules": [{"action": "allow", "features": {"is_quick_play_singleplayer": True}}],
                  "value": ["--quickPlaySingleplayer", "${quickPlaySingleplayer}"]},
                 {"rules": [{"action": "allow", "features": {"is_demo_user": True}}], "value": "--demo"}],
    },
}


class LaunchTest(unittest.TestCase):
    def test_rules(self):
        windows_only = [{"action": "allow", "os": {"name": "windows"}}]
        self.assertTrue(rules_allow(None))
        self.assertTrue(rules_allow(windows_only, os_name="windows", arch="x86_64"))
        self.assertFalse(rules_allow(windows_only, os_name="linux", arch="x86_64"))
        self.assertFalse(rules_allow([{"action": "allow", "os": {"name": "windows", "arch": "arm64"}}],
                                     os_name="windows", arch="x86_64"))
        disallow_osx = [{"action": "allow"}, {"action": "disallow", "os": {"name": "osx"}}]
        self.assertFalse(rules_allow(disallow_osx, os_name="osx"))
        self.assertTrue(rules_allow(disallow_osx, os_name="windows"))
        self.assertFalse(rules_allow([{"action": "allow", "features": {"is_demo_user": True}}], {}))

    def test_maven_paths_and_identity(self):
        self.assertEqual(maven_path("org.lwjgl:lwjgl:3.3.3:natives-windows"),
                         "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-windows.jar")
        self.assertEqual(library_key("org.ow2.asm:asm:9.10.1"), library_key("org.ow2.asm:asm:9.6"))
        self.assertNotEqual(library_key("org.lwjgl:lwjgl:3.3.3"), library_key("org.lwjgl:lwjgl:3.3.3:natives-windows"))

    def test_library_plan_puts_pinned_fabric_libraries_first_and_drops_duplicates(self):
        planned = plan_libraries(PROFILE, VERSION, TARGET, os_name="windows", arch="x86_64")
        self.assertEqual([lib.name for lib in planned],
                         ["net.fabricmc:fabric-loader:0.19.5", "org.ow2.asm:asm:9.10.1", "org.lwjgl:lwjgl:3.3.3",
                          "org.lwjgl:lwjgl:3.3.3:natives-windows"])
        self.assertEqual(planned[0].sha256, "a" * 64)  # from the target's pin, not the fetched profile
        self.assertEqual(planned[2].sha1, "c" * 40)

    def test_an_unpinned_fabric_library_is_refused(self):
        profile = {**PROFILE, "libraries": [*PROFILE["libraries"],
                                            {"name": "net.fabricmc:sponge-mixin:0.17.4", "url": "https://maven.fabricmc.net/"}]}
        with self.assertRaises(AcquisitionError):
            plan_libraries(profile, VERSION, TARGET, os_name="windows", arch="x86_64")

    def test_fetched_profile_must_match_the_pins(self):
        self.assertEqual(check_profile(PROFILE, TARGET, "1.21"), [])
        self.assertTrue(check_profile({**PROFILE, "mainClass": "evil.Main"}, TARGET, "1.21"))
        self.assertTrue(check_profile({**PROFILE, "libraries": PROFILE["libraries"][:1]}, TARGET, "1.21"))
        self.assertTrue(check_profile(PROFILE, TARGET, "1.21.1"))

    def test_launch_arguments(self):
        files = ClientFiles(VERSION, PROFILE, Path("C:/cache/client.jar"),
                            [Library("a:b:1", "a/b/1/b-1.jar", "u", "mojang", file=Path("C:/cache/b-1.jar"))],
                            Path("C:/cache/assets"), "17", {})
        args = launch_arguments(files, Path("C:/run/game"), Path("C:/run/natives"), "egc-client-check",
                                ["-javaagent:obs.jar=names.properties"])
        self.assertEqual(args[:3], ["-Xmx2G", "-Xms512M", "-javaagent:obs.jar=names.properties"])
        main = args.index(PROFILE["mainClass"])
        self.assertIn("-DFabricMcEmu= net.minecraft.client.main.Main ", args[:main])
        self.assertIn(str(Path("C:/run/natives")), args[3])
        self.assertEqual(args[args.index("--username") + 1], "EGCClient")
        self.assertEqual(args[args.index("--quickPlaySingleplayer") + 1], "egc-client-check")
        self.assertNotIn("--demo", args)
        self.assertIn("--width", args)
        without = launch_arguments(files, Path("C:/run/game"), Path("C:/run/natives"), None)
        self.assertNotIn("--quickPlaySingleplayer", without)

    def test_offline_identity_and_argfile_quoting(self):
        self.assertEqual(offline_uuid("Notch"), "b50ad385829d3141a2167e7d7539ba7f")
        self.assertEqual(quote_arg('C:\\a b\\"c"'), '"C:\\\\a b\\\\\\"c\\""')


class WorldTest(unittest.TestCase):
    def test_pack_metadata_follows_the_pack_format_change(self):
        self.assertEqual(pack_mcmeta({"resource": 34, "data": 48}, "d")["pack"]["pack_format"], 48)
        meta = pack_mcmeta({"resource_major": 69, "data_major": 88, "data_minor": 0}, "d")["pack"]
        self.assertEqual((meta["min_format"], meta["max_format"]), ([88, 0], [88, 0]))

    def test_write_pack_and_path_guard(self):
        with tempfile.TemporaryDirectory() as root:
            hashes = write_pack(Path(root), "egc", {"load": ["say hi"], "tick": []}, {"data": 48}, "d")
            self.assertIn("data/egc/function/load.mcfunction", hashes)
            tick = json.loads((Path(root) / "datapacks/egc/data/minecraft/tags/function/tick.json").read_text())
            self.assertEqual(tick, {"values": ["egc:tick"]})
            with self.assertRaises(AcquisitionError):
                write_pack(Path(root) / ("x" * 240), "egc", {"placement_start": []}, {"data": 48}, "d")


def log(*lines: str) -> list[str]:
    return list(lines)


BANNER = "[18:50:50] [main/INFO]: Loading Minecraft 1.21 with Fabric Loader 0.19.5"
MODS = ["[18:50:50] [main/INFO]: Loading 3 mods:", "\t- minecraft 1.21", "\t- modmenu 11.0.5",
        "\t- no-enderman-grief 1.2.0", "[18:50:51] [main/INFO]: next"]
STARTED = ["[18:50:55] [Render thread/INFO]: EndermanGriefControl has been initialized.",
           "[18:51:00] [Server thread/INFO]: Starting integrated minecraft server version 1.21",
           "[18:51:05] [Server thread/INFO]: EGCClient joined the game",
           "[18:51:06] [Render thread/INFO]: [CHAT] EGC-RESULT setup arena=567 grass=8 endermen=1 carry=0 "
           "rule_griefing=1 rule_spawning=0"]
PROJECT = {"id": "no-enderman-grief", "version": "1.2.0"}


class ScenarioTest(unittest.TestCase):
    def facts(self, *extra: str) -> scenario.ClientFacts:
        return scenario.facts([BANNER, *MODS, *STARTED, *extra], "EGCClient")

    def test_startup_passes_only_with_banner_listing_world_and_clean_close(self):
        found = self.facts()
        status, _reason, assertions = scenario.startup_verdict(found, "1.21", "0.19.5", PROJECT, 0, "WM_CLOSE")
        self.assertIsNone(status)
        self.assertTrue(all(ok for _n, ok, _e, _o in assertions))
        _status, _reason, killed = scenario.startup_verdict(found, "1.21", "0.19.5", PROJECT, 1, "taskkill")
        self.assertFalse(all(ok for _n, ok, _e, _o in killed))
        status, _reason, _a = scenario.startup_verdict(scenario.facts([BANNER, *MODS], "EGCClient"), "1.21", "0.19.5",
                                                       PROJECT, 0, "WM_CLOSE")
        self.assertEqual(status, "blocked")  # never started, nothing failed: missing observation

    def test_a_mixin_failure_or_crash_fails_startup(self):
        crashed = self.facts("[main/ERROR]: Mixin apply for mod a failed a.mixins.json:M from mod a -> x: boom",
                             "[main/ERROR]: Minecraft has crashed!")
        self.assertEqual(scenario.startup_verdict(crashed, "1.21", "0.19.5", PROJECT, 1, "taskkill")[0], "fail")

    def test_pickup_and_placement_verdicts(self):
        denial = "[Server thread/INFO]: [Enderman] Denied pickup at (8, 100, 8)."
        good = "[Render thread/INFO]: [CHAT] EGC-RESULT pickup arena=567 grass=8 present=1 carry=0 endermen=1"
        self.assertIsNone(scenario.pickup_verdict(self.facts(denial, good))[0])
        self.assertEqual(scenario.pickup_verdict(self.facts(good))[0], "blocked")
        changed = good.replace("carry=0", "carry=1")
        self.assertEqual(scenario.pickup_verdict(self.facts(denial, changed))[0], "fail")
        outside = "[Server thread/INFO]: [Enderman] Denied pickup at (40, 100, 8)."
        self.assertEqual(scenario.pickup_verdict(self.facts(outside, good))[0], "blocked")
        start = "[CHAT] EGC-RESULT placement-start arena=567 holding=4 endermen=4 items=0"
        round_ok = "[CHAT] EGC-RESULT placement round=1 arena=567 holding=4 endermen=4 items=0"
        placed = "[Server thread/INFO]: [Enderman] Denied placement at (8, 100, 8)."
        self.assertIsNone(scenario.placement_verdict(self.facts(start, placed, round_ok))[0])
        lost = round_ok.replace("holding=4", "holding=3")
        self.assertEqual(scenario.placement_verdict(self.facts(start, placed, lost))[0], "fail")
        self.assertEqual(scenario.placement_verdict(self.facts(start.replace("holding=4", "holding=2"), round_ok))[0],
                         "blocked")

    def test_negative_control_must_see_griefing(self):
        picked = "[CHAT] EGC-RESULT pickup arena=0 grass=7 present=1 carry=1 endermen=1"
        status, _reason, assertions = scenario.griefing_verdict(self.facts(picked))
        self.assertIsNone(status)
        self.assertTrue(assertions[0][1])
        protected = "[CHAT] EGC-RESULT pickup arena=567 grass=8 present=1 carry=0 endermen=1"
        self.assertFalse(scenario.griefing_verdict(self.facts(protected))[2][0][1])

    def test_mod_menu_verdict(self):
        self.assertTrue(all(ok for _n, ok, _e, _o in scenario.mod_menu_verdict(self.facts(), "11.0.5", "pass")[2]))
        self.assertFalse(scenario.mod_menu_verdict(self.facts(), "12.0.1", "pass")[2][0][1])
        self.assertEqual(scenario.mod_menu_verdict(self.facts(), "11.0.5", "fail")[0], "blocked")

    def test_end_conditions(self):
        self.assertEqual(scenario.core_end(["[CHAT] EGC-RESULT done"]), "all placement rounds reported")
        self.assertEqual(scenario.core_end(["Minecraft has crashed!"]), "crash or resolution failure")
        self.assertIsNone(scenario.core_end(STARTED))
        self.assertEqual(scenario.setup_end(STARTED), "setup reported")
        self.assertEqual(scenario.refusal_end(["[main/ERROR]: Incompatible mods found!"]), "Loader refusal")
        self.assertEqual(scenario.refusal_end(STARTED), "integrated server started")

    def test_holder_worlds_keep_their_holders_on_their_own_row(self):
        a = scenario.holder_functions({"mob_spawning": "a", "daylight_cycle": "b", "mob_griefing": "c",
                                       "random_tick_speed": "d"}, 1)
        b = scenario.holder_functions({"mob_spawning": "a", "daylight_cycle": "b", "mob_griefing": "c",
                                       "random_tick_speed": "d"}, 2)
        self.assertIn(f"100 {scenario.holder_z(1)}.5", a["summon_holder"][0])
        self.assertIn(f"100 {scenario.holder_z(2)}.5", b["summon_holder"][0])
        self.assertNotEqual(scenario.holder_z(1), scenario.holder_z(2))
        self.assertTrue(any("time query gametime" in line for line in a["load"]))


class AllowlistTest(unittest.TestCase):
    def test_only_reviewed_lines_pass_and_context_is_required(self):
        lines = ["[18:50:58] [Download-2/ERROR]: Failed to fetch user properties",
                 "[18:50:59] [main/WARN]: Warnings were found!",
                 " - Mod 'adventure-platform-fabric' (adventure-platform-fabric) 5.14.0 recommends any version of "
                 "fabric-networking-api-v1, which is missing!",
                 "[18:51:00] [main/WARN]: Warnings were found!",
                 " - Mod 'other' (other) 1.0 recommends any version of something, which is missing!",
                 "[18:51:01] [Render thread/ERROR]: Something new"]
        found = scenario.facts(lines, "EGCClient")
        known, unknown = classify(lines, found.warn_error)
        self.assertEqual([record["line"] for record in known], [0, 1])
        self.assertEqual([record["line"] for record in unknown], [3, 5])
        self.assertTrue(all(record["shown_by"] for record in known))


class CheckSetTest(unittest.TestCase):
    def test_each_profile_and_mode_requires_its_own_checks(self):
        manifest = load_manifest(MANIFEST)

        def names(minecraft: str, profile: str, control: bool = False, negative: bool = False):
            return client_check_names(select(manifest, "fabric", "client", minecraft, profile=profile), control, negative)

        self.assertEqual(names("1.21", "standard")[1], ())
        self.assertNotIn("mod_menu_load", names("1.21", "standard")[0])
        self.assertIn("mod_menu_load", names("1.21.1", "modmenu")[0])
        self.assertEqual(names("1.21.1", "modmenu")[1], ())  # 1.21.1 does not require the extended checks
        self.assertIn("monitor_state", names("1.21.11", "modmenu")[1])
        self.assertIn("monitor_state", names("1.21.5", "bundled-modmenu")[1])
        self.assertEqual(names("1.21", "missing-dependency"), (("dependency_refusal",), ()))
        self.assertEqual(names("1.21", "standard", control=True)[0], ("client_startup", "integrated_world_load"))
        self.assertIn("griefing_observed", names("1.21", "standard", negative=True)[0])


if __name__ == "__main__":
    unittest.main()
