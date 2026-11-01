import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pin
from egc_compat.acquire import AcquisitionError, HttpLimits

LOADER_SHA256 = "93044e4dd46de5d8136701292f05e868da096d2c9fddb4793e4fdbcc63efc695"
PROFILE = {"mainClass": "net.fabricmc.loader.impl.launch.knot.KnotServer", "libraries": [
    {"name": "net.fabricmc:fabric-loader:0.19.5", "url": "https://maven.fabricmc.net/", "sha256": LOADER_SHA256}]}
MOJANG = {"downloads": {"server": {"url": "https://piston-data.mojang.com/x/server.jar", "sha1": "a" * 40,
                                   "size": 123}}, "javaVersion": {"majorVersion": 21}}


def launcher(game: str, loader: str = "0.19.5") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as jar:
        jar.writestr("install.properties", f"fabric-loader-version={loader}\ngame-version={game}")
        jar.writestr("net/fabricmc/installer/ServerLauncher.class", b"\xca\xfe\xba\xbe")
    return buffer.getvalue()


class Body:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def close(self) -> None:
        pass


class FabricPinTest(unittest.TestCase):
    def resolve(self, body: bytes) -> dict:
        resolver = pin.Resolver("test", HttpLimits(1, 1, 0))
        with mock.patch.object(pin, "_open", return_value=Body(body)), \
                mock.patch.object(pin, "get_json", return_value=PROFILE), \
                mock.patch.object(resolver, "fabric_loader_check"), \
                mock.patch.object(resolver, "mojang", return_value=MOJANG):
            return resolver.fabric_server("1.21", {"loader": "0.19.5", "installer": "1.1.2"})

    def test_launcher_pin_is_the_launcher_hash_not_a_library_hash(self):
        body = launcher("1.21")
        target = self.resolve(body)
        self.assertEqual(target["launcher_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(target["launcher_size"], len(body))
        self.assertNotEqual(target["launcher_sha256"], LOADER_SHA256)
        self.assertEqual(target["loader_libraries"][0]["sha256"], LOADER_SHA256)

    def test_error_page_is_never_pinned(self):
        with self.assertRaises(AcquisitionError):
            self.resolve(b"<html>rate limited</html>")

    def test_launcher_for_another_game_version_is_never_pinned(self):
        with self.assertRaises(AcquisitionError):
            self.resolve(launcher("1.21.1"))


def mod_jar(mod_id: str, version: str, minecraft: str, loader: str = ">=0.16.10") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as jar:
        jar.writestr("fabric.mod.json", json.dumps({"id": mod_id, "version": version,
                                                    "depends": {"minecraft": minecraft, "fabricloader": loader}}))
    return buffer.getvalue()


def release(version: str, body: bytes, game_versions: list[str], published: str, version_type: str = "release") -> dict:
    return {"id": f"v-{version}", "project_id": "P7dR8mSH", "version_number": version, "version_type": version_type,
            "date_published": published, "game_versions": game_versions, "loaders": ["fabric"],
            "files": [{"primary": True, "url": f"https://cdn.modrinth.com/{version}.jar", "filename": f"fapi-{version}.jar",
                       "size": len(body), "hashes": {"sha512": hashlib.sha512(body).hexdigest(),
                                                     "sha1": hashlib.sha1(body).hexdigest()}}]}


class DependencyPinTest(unittest.TestCase):
    def test_newest_release_listing_the_exact_version_wins(self):
        body = b"x"
        versions = [release("0.128.0+1.21.5", body, ["1.21.5"], "2025-06-01T00:00:00Z"),
                    release("0.128.2+1.21.5", body, ["1.21.5"], "2025-08-08T00:00:00Z"),
                    release("0.129.0-beta+1.21.5", body, ["1.21.5"], "2025-09-01T00:00:00Z", "beta"),
                    release("0.130.0+1.21.6", body, ["1.21.6"], "2025-10-01T00:00:00Z")]
        self.assertEqual(pin.newest_release(versions, "1.21.5")["version_number"], "0.128.2+1.21.5")
        with self.assertRaises(AcquisitionError):
            pin.newest_release(versions, "1.21.4")

    def test_a_verified_jar_becomes_a_dependency_artifact(self):
        body = mod_jar("fabric-api", "0.128.2+1.21.5", ">=1.21.5- <1.21.6-")
        entry = release("0.128.2+1.21.5", body, ["1.21.5"], "2025-08-08T00:00:00Z")
        artifact = pin.describe_dependency("fabric-api", entry, entry["files"][0], body)
        self.assertEqual(artifact["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(artifact["declares"], {"minecraft": ">=1.21.5- <1.21.6-", "fabricloader": ">=0.16.10"})
        self.assertEqual(pin.accepts(artifact, "1.21.5", "0.19.5"), [])
        self.assertTrue(pin.accepts(artifact, "1.21.6", "0.19.5"))
        self.assertTrue(pin.accepts(artifact, "1.21.5", "0.16.9"))
        self.assertEqual(pin.dependency_artifact_id("fabric-api", "0.128.2+1.21.5"), "fabric-api-0.128.2-1.21.5")

    def test_a_jar_that_is_not_the_listed_release_is_refused(self):
        body = mod_jar("fabric-api", "0.128.2+1.21.5", "*")
        entry = release("0.128.2+1.21.5", body, ["1.21.5"], "2025-08-08T00:00:00Z")
        with self.assertRaises(AcquisitionError):
            pin.describe_dependency("fabric-api", entry, entry["files"][0], body + b"tampered")
        other = mod_jar("modmenu", "0.128.2+1.21.5", "*")
        listed = release("0.128.2+1.21.5", other, ["1.21.5"], "2025-08-08T00:00:00Z")
        with self.assertRaises(AcquisitionError):
            pin.describe_dependency("fabric-api", listed, listed["files"][0], other)

    def test_pinner_keeps_complete_pins_and_drops_unused_ones(self):
        data = json.loads((Path(pin.__file__).parent / "manifest.json").read_text(encoding="utf-8"))
        data["artifacts"]["fabric-api-0.0.1-stale"] = dict(data["artifacts"]["fabric-api-0.102.0-1.21"], sha256="0" * 64)
        resolver = mock.Mock()
        notes: list[str] = []
        pin.DependencyPinner(data, resolver, refresh=False, notes=notes).run()
        resolver.modrinth_release.assert_not_called()
        self.assertNotIn("fabric-api-0.0.1-stale", data["artifacts"])
        self.assertIn("dropped unused dependency pin fabric-api-0.0.1-stale", notes)
        self.assertEqual(pin.validate(data), [])


if __name__ == "__main__":
    unittest.main()
