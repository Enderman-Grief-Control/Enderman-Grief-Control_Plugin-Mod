import copy
import json
import unittest
from pathlib import Path

from egc_compat.manifest import (ManifestError, SelectionError, expected_target_keys, load_manifest,
                                 parse_manifest_bytes, select, validate)

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"


def committed() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class CommittedManifestTest(unittest.TestCase):
    def test_committed_manifest_is_valid(self):
        manifest = load_manifest(MANIFEST)
        self.assertEqual(len(manifest.data["targets"]), 36)
        self.assertEqual(list(manifest.data["targets"]), expected_target_keys())

    def test_every_version_is_selectable_without_code_changes(self):
        manifest = load_manifest(MANIFEST)
        for key in expected_target_keys():
            platform, side, minecraft = key.split("/")
            selection = select(manifest, platform, side, minecraft)
            self.assertEqual(selection.key, key)
            self.assertEqual(selection.artifact["platform"], platform)

    def test_minecraft_1_21_2_runs_on_the_paper_server_its_players_join(self):
        selection = select(load_manifest(MANIFEST), "paper", "server", "1.21.2")
        self.assertIsNone(selection.unavailable)
        self.assertEqual(selection.minecraft, "1.21.2")
        self.assertEqual(selection.server_minecraft, "1.21.3")
        self.assertIn("protocol", selection.server_basis)
        self.assertEqual(selection.target["client_protocol"], "1.21.3")  # protocol 768, shared by 1.21.2

    def test_every_other_row_runs_its_own_release(self):
        manifest = load_manifest(MANIFEST)
        for key in expected_target_keys():
            platform, side, minecraft = key.split("/")
            if key != "paper/server/1.21.2":
                self.assertEqual(select(manifest, platform, side, minecraft).server_minecraft, minecraft)


class ValidationTest(unittest.TestCase):
    def test_missing_target_is_rejected(self):
        data = committed()
        del data["targets"]["fabric/server/1.21.4"]
        self.assertTrue(any("missing fabric/server/1.21.4" in error for error in validate(data)))

    def test_unpinned_target_is_rejected(self):
        data = committed()
        del data["targets"]["paper/server/1.21.3"]["sha256"]
        data["targets"]["fabric/server/1.21.6"]["launcher_sha256"] = "latest"
        errors = validate(data)
        self.assertTrue(any("paper/server/1.21.3: missing sha256" in error for error in errors))
        self.assertTrue(any("fabric/server/1.21.6: launcher_sha256" in error for error in errors))

    def test_artifact_platform_mismatch_is_rejected(self):
        data = committed()
        data["targets"]["paper/server/1.21"]["artifact_id"] = "fabric-1.2.0"
        self.assertTrue(any("is for fabric, not paper" in error for error in validate(data)))

    def test_other_server_release_needs_a_written_basis(self):
        data = committed()
        del data["targets"]["paper/server/1.21.2"]["server_basis"]
        data["targets"]["fabric/server/1.21.4"]["server_minecraft"] = "1.20.6"
        errors = validate(data)
        self.assertTrue(any("paper/server/1.21.2: missing server_basis" in error for error in errors))
        self.assertTrue(any("fabric/server/1.21.4: server_minecraft" in error for error in errors))

    def test_unsafe_artifact_filenames_are_rejected(self):
        for name in ("../../../outside.jar", "/tmp/x.jar", "C:/x.jar", "C:\\x.jar", "sub/x.jar", "a.jar:evil.jar",
                     ".hidden.jar", "CON.jar", "x.txt", "..jar"):
            data = committed()
            data["artifacts"]["paper-1.2.0"]["filename"] = name
            self.assertTrue(any("plain .jar basename" in error for error in validate(data)), name)

    def test_unsafe_loader_library_paths_and_versions_are_rejected(self):
        data = committed()
        data["targets"]["fabric/server/1.21"]["loader_libraries"][0]["path"] = "../../evil.jar"
        data["targets"]["fabric/server/1.21.1"]["installer"] = "../1.1.2"
        errors = validate(data)
        self.assertTrue(any("unsafe path" in error for error in errors))
        self.assertTrue(any("fabric/server/1.21.1: installer" in error for error in errors))

    def test_client_rows_declare_whether_extended_checks_gate_them(self):
        data = committed()
        del data["targets"]["fabric/client/1.21"]["extended_checks_required"]
        self.assertTrue(any("extended_checks_required" in error for error in validate(data)))
        flags = {key: target["extended_checks_required"] for key, target in committed()["targets"].items()
                 if "/client/" in key}
        self.assertEqual([key for key, required in flags.items() if required],
                         ["fabric/client/1.21", "fabric/client/1.21.5", "fabric/client/1.21.11"])

    def test_boolean_is_not_an_integer(self):
        data = committed()
        data["targets"]["paper/server/1.21"]["size"] = True
        self.assertTrue(any("not boolean" in error for error in validate(data)))

    def test_duplicate_keys_are_rejected(self):
        raw = MANIFEST.read_text(encoding="utf-8").replace(
            '"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1)
        with self.assertRaises(ManifestError):
            parse_manifest_bytes(raw.encode("utf-8"))

    def test_duplicate_artifact_hash_is_rejected(self):
        data = committed()
        clone = copy.deepcopy(data["artifacts"]["paper-1.2.0"])
        data["artifacts"]["paper-copy"] = clone
        self.assertTrue(any("duplicates artifact" in error for error in validate(data)))


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST)

    def test_override_must_name_a_manifest_artifact(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "server", "1.21", "paper-9.9.9")

    def test_override_must_match_platform(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "server", "1.21", "fabric-1.2.0")

    def test_no_neighboring_version_fallback(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "server", "1.21.12")

    def test_paper_has_no_client_side(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "client", "1.21")


if __name__ == "__main__":
    unittest.main()
