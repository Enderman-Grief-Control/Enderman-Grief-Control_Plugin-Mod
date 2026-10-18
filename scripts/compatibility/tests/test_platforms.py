import tempfile
import unittest
from pathlib import Path

from egc_compat.platforms import (CHAT_DENIED, FabricPlatform, PaperPlatform, normalize_policy,
                                  parse_bukkit_yaml)
from egc_compat.server_row import TEST_PASSED, Limits, in_box

PAPER_ARTIFACT = {"descriptor": {"name": "EndermanGriefControl", "version": "1.2.0"}}
FABRIC_ARTIFACT = {"descriptor": {"id": "no-enderman-grief", "version": "1.2.0"}}

PAPER_CONFIG = """# comment
default-enabled: false
worlds:
  world: true
default-held-block-handling: alert
held-block-worlds:
  world: 'off'
  world_nether: off
logging:
  enabled: false
  removals: false
  denial-rate-limit-seconds: 10
messages:
  prefix: '[Enderman] '
"""


class PaperSettingsTest(unittest.TestCase):
    def setUp(self):
        self.platform = PaperPlatform(PAPER_ARTIFACT)

    def test_yaml_quoted_off_is_off_and_bare_off_falls_back(self):
        flat = parse_bukkit_yaml(PAPER_CONFIG)
        self.assertEqual(flat["held-block-worlds.world"], ("off", True))
        self.assertEqual(flat["held-block-worlds.world_nether"], ("off", False))
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "plugins" / "EndermanGriefControl" / "config.yml"
            config.parent.mkdir(parents=True)
            config.write_text(PAPER_CONFIG, encoding="utf-8")
            settings = self.platform.read_config(Path(root))
        self.assertEqual(settings, {"default_enabled": False, "log_denials": False, "log_removals": False,
                                    "policy_default": "alert", "world_enabled": True, "policy_world": "off"})

    def test_status_parsing_with_and_without_separators(self):
        lines = ["Default: disabled, log denials: disabled, log removals: disabled, held-block: alert",
                 "  world: enabled", "  world held-block: off"]
        expected = {"default_enabled": False, "log_denials": False, "log_removals": False,
                    "policy_default": "alert", "world_enabled": True, "policy_world": "off"}
        self.assertEqual(self.platform.parse_status("\n".join(lines)), expected)
        self.assertEqual(self.platform.parse_status("".join(lines)), expected)
        self.assertIsNone(self.platform.parse_status("Unknown command"))

    def test_log_patterns(self):
        line = "[12:00:00 INFO]: [EndermanGriefControl] Denied pickup at (6, 100, -6)."
        match = self.platform.denial_log.search(line)
        self.assertEqual(match.groups(), ("pickup", "6", "100", "-6"))
        self.assertTrue(self.platform.clear_log.search("[EndermanGriefControl] cleared a holder at (8, 100, 8)."))

    def test_commands(self):
        self.assertTrue(self.platform.policy("off").matches("Held-block handling for world 'world' is now off.\n"))
        self.assertFalse(self.platform.policy("off").matches("Held-block handling for world 'world' is now alert."))
        self.assertTrue(self.platform.guest_status.matches("You do not have permission to use this command."))


class FabricSettingsTest(unittest.TestCase):
    def setUp(self):
        self.platform = FabricPlatform(FABRIC_ARTIFACT)

    def test_status_and_disk(self):
        status = self.platform.parse_status(
            "Prevention: disabled, log denials: enabled, log removals: disabled, held-block: alert")
        self.assertEqual(status, {"enabled": False, "log_denials": True, "log_removals": False, "policy": "alert"})
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config" / "no-enderman-grief.json"
            config.parent.mkdir()
            config.write_text('{"enabled": false, "loggingEnabled": true, "logRemovals": false, '
                              '"heldBlockHandling": "ALERT"}', encoding="utf-8")
            self.assertEqual(self.platform.read_config(Path(root)), status)

    def test_guest_denial_and_logs(self):
        self.assertTrue(self.platform.guest_status.matches("Unknown or incomplete command, see below for error"))
        line = "[12:00:00] [Server thread/INFO] (no-enderman-grief) [Enderman] holding cleared at (8, 100, 8)."
        self.assertTrue(self.platform.clear_log.search(line))
        self.assertFalse(self.platform.denial_log.search("[Enderman] Denied pickup x3."))


class ServerIdentityTest(unittest.TestCase):
    """Banner lines recorded from real Paper/Fabric runs on 2026-10-18."""

    PAPER_121 = ("[13:40:17 INFO]: This server is running Paper version 1.21-130-master@b1b5d4c "
                 "(2024-08-10T10:02:42Z) (Implementing API version 1.21-R0.1-SNAPSHOT)")
    PAPER_12111 = ("[13:44:20 INFO]: This server is running Paper version 1.21.11-132-ver/1.21.11@c5eb079 "
                   "(2026-05-11T11:43:09Z) (Implementing API version 1.21.11-R0.1-SNAPSHOT)")
    FABRIC_121 = "[13:45:50] [main/INFO]: Loading Minecraft 1.21 with Fabric Loader 0.19.5"

    def identity(self, platform, line, target, server_minecraft):
        return platform.server_identity(platform.banner_pattern().search(line), target, server_minecraft)[0]

    def test_paper_banner_must_name_the_pinned_release_and_build(self):
        paper = PaperPlatform(PAPER_ARTIFACT)
        self.assertTrue(self.identity(paper, self.PAPER_121, {"paper_build": 130}, "1.21"))
        self.assertTrue(self.identity(paper, self.PAPER_12111, {"paper_build": 132}, "1.21.11"))
        self.assertFalse(self.identity(paper, self.PAPER_12111, {"paper_build": 132}, "1.21.1"))
        self.assertFalse(self.identity(paper, self.PAPER_121, {"paper_build": 13}, "1.21"))

    def test_fabric_banner_must_name_the_pinned_release_and_loader(self):
        fabric = FabricPlatform(FABRIC_ARTIFACT)
        self.assertTrue(self.identity(fabric, self.FABRIC_121, {"loader": "0.19.5"}, "1.21"))
        self.assertFalse(self.identity(fabric, self.FABRIC_121, {"loader": "0.19.5"}, "1.21.1"))
        self.assertFalse(self.identity(fabric, self.FABRIC_121, {"loader": "0.20.0"}, "1.21"))


class SharedTest(unittest.TestCase):
    def test_policy_normalization_matches_from_config(self):
        self.assertEqual(normalize_policy("AUTO_CLEAR", None), "auto-clear")
        self.assertEqual(normalize_policy("bogus", "auto-clear"), "auto-clear")

    def test_chat_count(self):
        self.assertEqual(CHAT_DENIED["pickup"].fullmatch("[Enderman] Denied pickup x12.").group(1), "12")

    def test_query_output_forms(self):
        self.assertEqual(TEST_PASSED.match("Test passed, count: 567").group(1), "567")
        self.assertEqual(TEST_PASSED.match("Test passed. Count: 567").group(1), "567")
        self.assertIsNotNone(TEST_PASSED.match("Test passed"))
        self.assertIsNone(TEST_PASSED.match("Test passed, maybe"))
        self.assertIsNone(TEST_PASSED.match("No entity was found"))

    def test_arena_bounds(self):
        self.assertTrue(in_box((8, 100, 8), (4, 99, 4), (12, 105, 12)))
        self.assertFalse(in_box((8, 100, 13), (4, 99, 4), (12, 105, 12)))

    def test_limit_overrides(self):
        limits, applied = Limits.from_overrides(["server_start_s=300", "trials=2"])
        self.assertEqual((limits.server_start_s, limits.trials), (300.0, 2))
        self.assertEqual(applied, {"server_start_s": 300.0, "trials": 2})
        with self.assertRaises(ValueError):
            Limits.from_overrides(["unknown=1"])
        with self.assertRaises(ValueError):
            Limits.from_overrides(["trials=0"])


if __name__ == "__main__":
    unittest.main()
