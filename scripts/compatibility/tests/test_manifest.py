import copy
import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path

from egc_compat.jarinfo import generated_jar
from egc_compat.manifest import (ManifestError, SelectionError, expected_target_keys, load_manifest,
                                 parse_manifest_bytes, select, validate)

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"


def committed() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def errors_matching(data: dict, text: str) -> list[str]:
    return [error for error in validate(data) if text in error]


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
            self.assertEqual(selection.profile.name, "standard")

    def test_every_row_runs_under_every_profile_for_its_side(self):
        manifest = load_manifest(MANIFEST)
        for key in expected_target_keys():
            platform, side, minecraft = key.split("/")
            for profile in manifest.profiles(platform, side):
                selection = select(manifest, platform, side, minecraft, profile=profile.name)
                self.assertEqual(list(selection.dependency_ids()), list(profile.dependencies), (key, profile.name))

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

    def test_documented_profiles_follow_the_2026_10_25_decision(self):
        manifest = load_manifest(MANIFEST)
        documented = {(profile.platform, side, profile.name): profile.dependencies
                      for platform, sides in (("paper", ("server",)), ("fabric", ("server", "client")))
                      for side in sides for profile in manifest.profiles(platform, side, documented_only=True)}
        self.assertEqual(documented, {
            ("paper", "server", "standard"): (),
            ("fabric", "server", "standard"): ("fabric-api",),
            ("fabric", "server", "missing-dependency"): (),
            ("fabric", "client", "standard"): ("fabric-api",),
            ("fabric", "client", "modmenu"): ("fabric-api", "modmenu"),
            ("fabric", "client", "missing-dependency"): (),
        })
        self.assertEqual(manifest.profile("fabric", "missing-dependency").refusal_ids, ("fabric-api",))
        self.assertFalse(manifest.profile("fabric", "bundled").documented)


class ValidationTest(unittest.TestCase):
    def test_missing_target_is_rejected(self):
        data = committed()
        del data["targets"]["fabric/server/1.21.4"]
        self.assertTrue(errors_matching(data, "missing fabric/server/1.21.4"))

    def test_unpinned_target_is_rejected(self):
        data = committed()
        del data["targets"]["paper/server/1.21.3"]["sha256"]
        data["targets"]["fabric/server/1.21.6"]["launcher_sha256"] = "latest"
        self.assertTrue(errors_matching(data, "paper/server/1.21.3: missing sha256"))
        self.assertTrue(errors_matching(data, "fabric/server/1.21.6: launcher_sha256"))

    def test_artifact_platform_mismatch_is_rejected(self):
        data = committed()
        data["targets"]["paper/server/1.21"]["artifact_id"] = "fabric-1.2.0"
        self.assertTrue(errors_matching(data, "is for fabric, not paper"))

    def test_other_server_release_needs_a_written_basis(self):
        data = committed()
        del data["targets"]["paper/server/1.21.2"]["server_basis"]
        data["targets"]["fabric/server/1.21.4"]["server_minecraft"] = "1.20.6"
        self.assertTrue(errors_matching(data, "paper/server/1.21.2: missing server_basis"))
        self.assertTrue(errors_matching(data, "fabric/server/1.21.4: server_minecraft"))

    def test_unsafe_artifact_filenames_are_rejected(self):
        for name in ("../../../outside.jar", "/tmp/x.jar", "C:/x.jar", "C:\\x.jar", "sub/x.jar", "a.jar:evil.jar",
                     ".hidden.jar", "CON.jar", "x.txt", "..jar"):
            data = committed()
            data["artifacts"]["paper-1.2.0"]["filename"] = name
            self.assertTrue(errors_matching(data, "plain .jar basename"), name)

    def test_unsafe_loader_library_paths_and_versions_are_rejected(self):
        data = committed()
        data["targets"]["fabric/server/1.21"]["loader_libraries"][0]["path"] = "../../evil.jar"
        data["targets"]["fabric/client/1.21"]["loader_libraries"][0]["path"] = "C:/evil.jar"
        data["targets"]["fabric/server/1.21.1"]["installer"] = "../1.1.2"
        self.assertEqual(len(errors_matching(data, "unsafe path")), 2)
        self.assertTrue(errors_matching(data, "fabric/server/1.21.1: installer"))

    def test_client_rows_declare_whether_extended_checks_gate_them(self):
        data = committed()
        del data["targets"]["fabric/client/1.21"]["extended_checks_required"]
        self.assertTrue(errors_matching(data, "extended_checks_required"))
        flags = {key: target["extended_checks_required"] for key, target in committed()["targets"].items()
                 if "/client/" in key}
        self.assertEqual([key for key, required in flags.items() if required],
                         ["fabric/client/1.21", "fabric/client/1.21.5", "fabric/client/1.21.11"])

    def test_client_rows_pin_their_own_trust_chain(self):
        for field in ("version_json_sha1", "version_json_url", "loader_main_class", "loader_libraries"):
            data = committed()
            del data["targets"]["fabric/client/1.21.3"][field]
            self.assertTrue(errors_matching(data, f"fabric/client/1.21.3: missing {field}"), field)

    def test_schema_1_mod_menu_field_is_rejected(self):
        data = committed()
        data["targets"]["fabric/client/1.21"]["mod_menu_artifact_id"] = "modmenu-11.0.5"
        self.assertTrue(errors_matching(data, "replaced by dependencies.modmenu"))

    def test_boolean_is_not_an_integer(self):
        data = committed()
        data["targets"]["paper/server/1.21"]["size"] = True
        self.assertTrue(errors_matching(data, "not boolean"))

    def test_duplicate_keys_are_rejected(self):
        raw = MANIFEST.read_text(encoding="utf-8").replace(
            '"schema_version": 2,', '"schema_version": 2, "schema_version": 2,', 1)
        self.assertIn('"schema_version": 2, "schema_version": 2,', raw)
        payload = raw.encode("utf-8")
        with self.assertRaises(ManifestError):
            parse_manifest_bytes(payload)

    def test_schema_1_is_rejected(self):
        data = committed()
        data["schema_version"] = 1
        self.assertTrue(errors_matching(data, "schema_version must be 2"))

    def test_duplicate_artifact_hash_is_rejected(self):
        data = committed()
        clone = copy.deepcopy(data["artifacts"]["paper-1.2.0"])
        data["artifacts"]["paper-copy"] = clone
        self.assertTrue(errors_matching(data, "duplicates artifact"))


class ProfileValidationTest(unittest.TestCase):
    def test_each_side_needs_a_documented_profile_and_standard_covers_every_side(self):
        data = committed()
        for profile in data["profiles"]["fabric"].values():
            if "client" in profile["sides"]:
                profile["documented"] = False
        data["profiles"]["fabric"]["standard"]["sides"] = ["server"]
        self.assertTrue(errors_matching(data, "no documented profile for the client side"))
        self.assertTrue(errors_matching(data, "'standard' profile must exist for every side"))

    def test_profile_fields_are_checked(self):
        data = committed()
        data["profiles"]["fabric"]["bundled"]["sides"] = ["server", "phone"]
        data["profiles"]["fabric"]["modmenu"]["dependencies"] = ["fabric-api", "fabric-api"]
        data["profiles"]["fabric"]["bundled-modmenu"]["expect"] = "crash"
        data["profiles"]["paper"]["standard"]["expect"] = {"loader_refusal": ["fabric-api"]}
        data["profiles"]["fabric"]["Bad Name"] = data["profiles"]["fabric"]["bundled"]
        self.assertTrue(errors_matching(data, "profiles.fabric.bundled: sides"))
        self.assertTrue(errors_matching(data, "profiles.fabric.modmenu: dependencies"))
        self.assertTrue(errors_matching(data, "profiles.fabric.bundled-modmenu: expect"))
        self.assertTrue(errors_matching(data, "profiles.paper.standard: expect"))
        self.assertTrue(errors_matching(data, "profiles.fabric.Bad Name: profile names"))

    def test_every_profile_dependency_needs_a_pin_on_each_row_and_nothing_else(self):
        data = committed()
        del data["targets"]["fabric/server/1.21.7"]["dependencies"]["fabric-api"]
        data["targets"]["fabric/server/1.21.8"]["dependencies"]["modmenu"] = "modmenu-15.0.2"
        self.assertTrue(errors_matching(data, "fabric/server/1.21.7: dependencies has no pin for 'fabric-api'"))
        self.assertTrue(errors_matching(data, "fabric/server/1.21.8: dependencies pins 'modmenu', which no server"))

    def test_dependency_pin_must_be_a_matching_fabric_dependency(self):
        data = committed()
        data["targets"]["fabric/client/1.21"]["dependencies"]["modmenu"] = "fabric-1.2.0"
        data["targets"]["fabric/client/1.21.4"]["dependencies"]["modmenu"] = "fabric-api-0.119.4-1.21.4"
        self.assertTrue(errors_matching(data, "dependencies.modmenu 'fabric-1.2.0' must name a fabric dependency"))
        self.assertTrue(errors_matching(data, "whose descriptor id is 'fabric-api'"))

    def test_dependency_pin_must_list_and_accept_the_row(self):
        data = committed()
        # The wrong-version probe of 2026-10-25: Fabric API for 1.21.4 on a 1.21.5 row.
        data["targets"]["fabric/server/1.21.5"]["dependencies"]["fabric-api"] = "fabric-api-0.119.4-1.21.4"
        data["targets"]["fabric/client/1.21.5"]["dependencies"]["fabric-api"] = "fabric-api-0.119.4-1.21.4"
        self.assertTrue(errors_matching(data, "fabric/server/1.21.5: dependencies.fabric-api "
                                              "'fabric-api-0.119.4-1.21.4' does not list Minecraft 1.21.5"))
        self.assertTrue(errors_matching(data, "which excludes 1.21.5"))

    def test_dependency_declared_loader_must_accept_the_pinned_loader(self):
        data = committed()
        data["artifacts"]["fabric-api-0.141.6-1.21.11"]["declares"]["fabricloader"] = ">=0.20.0"
        self.assertTrue(errors_matching(data, "declares fabricloader '>=0.20.0', which excludes 0.19.5"))

    def test_server_and_client_pin_the_same_fabric_api(self):
        data = committed()
        data["artifacts"]["fabric-api-0.116.17-1.21.1"]["game_versions"].append("1.21")
        data["artifacts"]["fabric-api-0.116.17-1.21.1"]["declares"]["minecraft"] = ">=1.21- <1.21.2-"
        data["targets"]["fabric/client/1.21"]["dependencies"]["fabric-api"] = "fabric-api-0.116.17-1.21.1"
        self.assertTrue(errors_matching(data, "fabric server and client 1.21 pin different Fabric API jars"))

    def test_dependency_artifacts_record_their_provenance(self):
        for field in ("sha512", "declares", "modrinth_project_id", "modrinth_version_id", "game_versions"):
            data = committed()
            del data["artifacts"]["modmenu-14.0.2"][field]
            self.assertTrue(errors_matching(data, "artifacts.modmenu-14.0.2"), field)

    def test_every_fabric_row_pins_releases_listing_that_version(self):
        data = committed()
        for key, target in data["targets"].items():
            platform, side, minecraft = key.split("/")
            if platform != "fabric":
                continue
            api = data["artifacts"][target["dependencies"]["fabric-api"]]
            self.assertEqual((api["kind"], api["descriptor"]["id"]), ("dependency", "fabric-api"), key)
            self.assertEqual(api["game_versions"], [minecraft], key)
            if side == "client":
                mod_menu = data["artifacts"][target["dependencies"]["modmenu"]]
                self.assertEqual(mod_menu["descriptor"]["id"], "modmenu", key)
                self.assertIn(minecraft, mod_menu["game_versions"], key)


class ArtifactKindTest(unittest.TestCase):
    def test_fixture_is_never_a_target_default_and_needs_a_purpose(self):
        data = committed()
        data["targets"]["fabric/server/1.21"]["artifact_id"] = "fapi-requirement-probe"
        del data["artifacts"]["fapi-requirement-probe"]["purpose"]
        self.assertTrue(errors_matching(data, "is a fixture; fixtures are selected only by --artifact"))
        self.assertTrue(errors_matching(data, "artifacts.fapi-requirement-probe: missing purpose"))

    def test_fixture_is_selectable_by_artifact_and_labeled(self):
        selection = select(load_manifest(MANIFEST), "fabric", "client", "1.21", "fapi-requirement-probe",
                           "missing-dependency")
        self.assertTrue(selection.fixture)
        self.assertTrue(selection.artifact_overridden)

    def test_candidate_build_record_is_checked(self):
        data = committed()
        candidate = copy.deepcopy(data["artifacts"]["fabric-1.2.0"])
        candidate.update(kind="candidate", sha256="1" * 64, build={
            "method": "local-build", "commands": [["mvn", "install"]], "java": "21", "build_tool": "mvn",
            "built_utc": "2026-10-25T00:00:00Z", "clean_tree": False})
        candidate.pop("release_tag")
        data["artifacts"]["fabric-1.2.1-cand-abcdef0"] = candidate
        self.assertTrue(errors_matching(data, "clean_tree must be true"))
        candidate["build"]["clean_tree"] = True
        self.assertEqual(validate(data), [])
        candidate["build"] = {"method": "registered", "registered_from": "release-jars/x.jar"}
        self.assertTrue(errors_matching(data, "build: missing registered_utc"))

    def test_generated_fixture_rebuilds_its_pinned_bytes(self):
        # CI runs this on Linux, so it also shows the bytes do not depend on the host.
        artifact = committed()["artifacts"]["fapi-requirement-probe"]
        data = generated_jar(artifact["generated"])
        self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (artifact["size"], artifact["sha256"]))
        with zipfile.ZipFile(io.BytesIO(data)) as jar:
            descriptor = json.loads(jar.read("fabric.mod.json"))
        self.assertEqual({field: descriptor[field] for field in artifact["descriptor"]}, artifact["descriptor"])
        self.assertEqual(descriptor["depends"]["fabric-api"], "*")

    def test_only_a_fixture_is_generated_and_from_one_source(self):
        data = committed()
        probe = data["artifacts"]["fapi-requirement-probe"]
        probe["url"] = "https://example.invalid/probe.jar"
        self.assertTrue(errors_matching(data, "needs exactly one of url, local_path, or generated"))
        del probe["url"]
        probe["generated"]["entry_time"] = "1979-12-31T00:00:00"
        self.assertTrue(errors_matching(data, "entry_time: must be 1980 or later"))
        published = data["artifacts"]["fabric-1.2.0"]
        del published["url"]
        published["generated"] = probe["generated"]
        self.assertTrue(errors_matching(data, "artifacts.fabric-1.2.0: only fixture artifacts may be generated"))


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST)

    def test_override_must_name_a_manifest_artifact(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "server", "1.21", "paper-9.9.9")

    def test_override_must_match_platform(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "server", "1.21", "fabric-1.2.0")

    def test_dependency_is_never_the_project_jar(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "fabric", "server", "1.21", "fabric-api-0.102.0-1.21")

    def test_no_neighboring_version_fallback(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "server", "1.21.12")

    def test_paper_has_no_client_side(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "client", "1.21")

    def test_profile_must_exist_and_apply_to_the_side(self):
        with self.assertRaises(SelectionError):
            select(self.manifest, "fabric", "server", "1.21", profile="nope")
        with self.assertRaises(SelectionError):
            select(self.manifest, "fabric", "server", "1.21", profile="modmenu")
        with self.assertRaises(SelectionError):
            select(self.manifest, "paper", "server", "1.21", profile="missing-dependency")

    def test_selected_profile_names_its_pinned_jars(self):
        selection = select(self.manifest, "fabric", "client", "1.21.5", profile="modmenu")
        self.assertEqual(selection.dependency_ids(),
                         {"fabric-api": "fabric-api-0.128.2-1.21.5", "modmenu": "modmenu-14.0.2"})
        self.assertEqual(select(self.manifest, "fabric", "client", "1.21.5", profile="bundled").dependency_ids(), {})


if __name__ == "__main__":
    unittest.main()
