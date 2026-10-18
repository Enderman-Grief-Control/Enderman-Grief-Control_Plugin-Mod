import hashlib
import io
import unittest
import zipfile
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


if __name__ == "__main__":
    unittest.main()
