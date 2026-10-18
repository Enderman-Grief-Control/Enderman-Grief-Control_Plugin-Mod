import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from egc_compat.install_check import FABRIC_LAUNCH_MAIN, verify_install


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class PaperInstallTest(unittest.TestCase):
    LIB = b"library bytes"
    MOJANG = b"mojang server"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        paper = self.root / "paper.jar"
        with zipfile.ZipFile(paper, "w") as jar:
            jar.writestr("META-INF/libraries.list", f"{sha256(self.LIB)}\tg:a:1\tg/a/1/a-1.jar\n")
            jar.writestr("META-INF/download-context", f"{sha256(self.MOJANG)}\thttps://x/server.jar\tmojang_1.21.jar\n")
            jar.writestr("META-INF/versions.list", "")
        self.target = {"sha256": sha256(paper.read_bytes())}
        write(self.root / "libraries/g/a/1/a-1.jar", self.LIB)
        write(self.root / "cache/mojang_1.21.jar", self.MOJANG)

    def problems(self):
        return verify_install(self.root, "paper", self.target, "1.21")

    def test_official_tree_passes(self):
        self.assertEqual(self.problems(), [])

    def test_modified_library_is_rejected(self):
        write(self.root / "libraries/g/a/1/a-1.jar", b"tampered")
        self.assertTrue(any("does not match" in problem for problem in self.problems()))

    def test_unlisted_file_is_rejected(self):
        write(self.root / "libraries/evil/evil.jar", b"extra")
        self.assertTrue(any("not listed" in problem for problem in self.problems()))

    def test_paper_jar_must_be_the_pinned_build(self):
        self.target["sha256"] = "0" * 64
        self.assertEqual(self.problems(), ["paper.jar does not match the pinned Paper build"])


class FabricInstallTest(unittest.TestCase):
    BUNDLED = b"bundled mojang library"
    LOADER = b"fabric loader"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        mojang = self.root / ".fabric/server/1.21-server.jar"
        mojang.parent.mkdir(parents=True)
        with zipfile.ZipFile(mojang, "w") as jar:
            jar.writestr("META-INF/libraries.list", f"{sha256(self.BUNDLED)}\tm:b:1\tm/b/1/b-1.jar\n")
            jar.writestr("META-INF/versions.list", "")
        self.target = {
            "minecraft_server_sha1": hashlib.sha1(mojang.read_bytes()).hexdigest(),
            "loader": "0.19.5",
            "loader_main_class": "net.fabricmc.loader.impl.launch.knot.KnotServer",
            "loader_libraries": [{"path": "net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar",
                                  "sha256": sha256(self.LOADER)}],
        }
        write(self.root / "libraries/m/b/1/b-1.jar", self.BUNDLED)
        write(self.root / "libraries/net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar", self.LOADER)
        self.write_launch_jar("../../libraries/net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar")

    def write_launch_jar(self, class_path: str, main: str = FABRIC_LAUNCH_MAIN, extra: bool = False) -> None:
        path = self.root / ".fabric/server/fabric-loader-server-0.19.5-minecraft-1.21.jar"
        with zipfile.ZipFile(path, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\nMain-Class: {main}\nClass-Path: {class_path}\n")
            jar.writestr("fabric-server-launch.properties", f"launch.mainClass={self.target['loader_main_class']}\n")
            if extra:
                jar.writestr("evil/Payload.class", b"\xca\xfe")

    def problems(self):
        return verify_install(self.root, "fabric", self.target, "1.21")

    def test_official_tree_passes(self):
        self.assertEqual(self.problems(), [])

    def test_modified_bundled_library_is_rejected(self):
        write(self.root / "libraries/m/b/1/b-1.jar", b"tampered")
        self.assertTrue(any("does not match" in problem for problem in self.problems()))

    def test_launch_jar_with_extra_classes_is_rejected(self):
        self.write_launch_jar("../../libraries/net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar", extra=True)
        self.assertTrue(any("unexpected entries" in problem for problem in self.problems()))

    def test_launch_jar_class_path_outside_verified_libraries_is_rejected(self):
        self.write_launch_jar("../../libraries/evil/evil.jar")
        self.assertTrue(any("class path" in problem for problem in self.problems()))

    def test_mojang_jar_must_match_pin(self):
        self.target["minecraft_server_sha1"] = "0" * 40
        self.assertTrue(self.problems()[0].endswith("does not match the pinned Mojang SHA-1"))


if __name__ == "__main__":
    unittest.main()
