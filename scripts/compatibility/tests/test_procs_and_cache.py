import hashlib
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from egc_compat import acquire
from egc_compat.acquire import (AcquisitionError, HttpLimits, contained, copy_local_pinned, download,
                                fetch_pinned, hash_file, verify_file)
from egc_compat.procs import ProcessRegistry

# A parent that starts a long-lived grandchild, reports its PID, then waits.
PARENT = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(f'child {child.pid}', flush=True)\n"
    "time.sleep(120)\n"
)


def pid_alive(pid: int) -> bool | None:
    """True/False when the PID can be queried; None when the host denies the query."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            error = ctypes.get_last_error()
            return False if error == 87 else None  # ERROR_INVALID_PARAMETER: no such process
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    return True


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProcessRegistry()
        # Registered before any assertion, and run before the directory is removed (LIFO).
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.registry.kill_all)
        self.root = Path(self.temp.name)

    def wait_gone(self, pid: int) -> bool | None:
        deadline = time.monotonic() + 10
        state = pid_alive(pid)
        while state and time.monotonic() < deadline:
            time.sleep(0.2)
            state = pid_alive(pid)
        return state

    def test_kill_all_ends_the_owned_process_tree(self):
        process = self.registry.start("parent", [sys.executable, "-c", PARENT], self.root, self.root / "out.log")
        found = process.output.wait_for(re.compile(r"child (\d+)"), 0, 30)
        self.assertIsNotNone(found)
        child_pid = int(found[1].group(1))
        if pid_alive(child_pid) is None:
            self.skipTest("this host does not allow querying process state")
        self.assertTrue(pid_alive(child_pid))
        killed, survivors = self.registry.kill_all()
        self.assertEqual((len(killed), survivors), (1, []))
        self.assertFalse(process.running())
        self.assertIs(self.wait_gone(child_pid), False, "grandchild survived cleanup")

    def test_wait_for_returns_none_when_process_exits(self):
        process = self.registry.start("short", [sys.executable, "-c", "print('crash')"], self.root, self.root / "o.log")
        self.assertIsNone(process.output.wait_for(re.compile("Done"), 0, 10, abort=lambda: not process.running()))

    def test_capture_failure_does_not_orphan_the_child(self):
        with mock.patch("egc_compat.procs.LineCapture", side_effect=OSError("capture failed")), \
                mock.patch("egc_compat.procs.kill_tree") as kill:
            with self.assertRaises(OSError):
                # Short-lived, because the mocked kill does not really end it.
                self.registry.start("sleeper", [sys.executable, "-c", "import time; time.sleep(1)"],
                                    self.root, self.root / "x.log")
        self.assertEqual(kill.call_count, 1)
        self.assertIn(self.wait_gone(kill.call_args[0][0]), (False, None))


class FakeResponse:
    def __init__(self, delay: float = 0.0):
        self.delay = delay

    def read(self, size: int = -1) -> bytes:
        time.sleep(self.delay)
        return b"x" * min(size if size > 0 else 1024, 1024)

    def close(self) -> None:
        pass


class CacheTest(unittest.TestCase):
    def test_wrong_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            jar = Path(root) / "a.jar"
            jar.write_bytes(b"jar bytes")
            with self.assertRaises(AcquisitionError) as raised:
                copy_local_pinned(jar, "a.jar", Path(root) / "cache", sha256="0" * 64, size=9)
            self.assertEqual(raised.exception.kind, "hash_mismatch")
            with self.assertRaises(AcquisitionError):
                verify_file(jar, size=10)

    def test_verified_cache_hit_needs_no_network(self):
        with tempfile.TemporaryDirectory() as root:
            data = b"pinned server jar"
            sha256 = hashlib.sha256(data).hexdigest()
            cached = Path(root) / "objects" / f"sha256-{sha256}" / "server.jar"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(data)
            result = fetch_pinned("https://invalid.example/server.jar", "server.jar", Path(root), "test",
                                  HttpLimits(1, 1, 0), sha256=sha256, size=len(data))
            self.assertTrue(result.from_cache)
            self.assertEqual(hash_file(result.path), sha256)

    def test_corrupt_cache_entry_is_not_served(self):
        with tempfile.TemporaryDirectory() as root:
            sha256 = hashlib.sha256(b"expected").hexdigest()
            cached = Path(root) / "objects" / f"sha256-{sha256}" / "server.jar"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"tampered")
            with self.assertRaises(AcquisitionError):
                fetch_pinned("https://127.0.0.1:9/server.jar", "server.jar", Path(root), "test",
                             HttpLimits(1, 1, 0), sha256=sha256, size=8)
            self.assertFalse(cached.exists())


class ContainmentTest(unittest.TestCase):
    """Even a filename that bypassed manifest validation cannot touch files outside the cache."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.sentinel = self.root / "outside.jar"
        self.sentinel.write_bytes(b"must survive")
        self.sha256 = hashlib.sha256(b"something else").hexdigest()

    def assert_refused(self, filename: str) -> None:
        with self.assertRaises(AcquisitionError) as raised:
            fetch_pinned("https://invalid.example/x.jar", filename, self.cache, "test", HttpLimits(1, 1, 0),
                         sha256=self.sha256, size=14)
        self.assertEqual(raised.exception.kind, "unsafe_path")
        self.assertEqual(self.sentinel.read_bytes(), b"must survive")

    def test_traversal_is_refused_before_any_deletion(self):
        self.assert_refused("../../../outside.jar")

    def test_absolute_and_drive_paths_are_refused(self):
        self.assert_refused(str(self.sentinel))
        if sys.platform == "win32":
            self.assert_refused(str(self.sentinel).replace("\\", "/"))

    def test_junction_or_symlink_escape_is_refused(self):
        key_dir = self.cache / "objects" / f"sha256-{self.sha256}"
        key_dir.parent.mkdir(parents=True)
        try:
            if sys.platform == "win32":
                import _winapi
                _winapi.CreateJunction(str(self.root), str(key_dir))
            else:
                key_dir.symlink_to(self.root, target_is_directory=True)
        except (OSError, AttributeError) as error:
            self.skipTest(f"cannot create a junction/symlink here: {error}")
        self.assert_refused("outside.jar")

    def test_contained_accepts_plain_children(self):
        self.assertTrue(contained(self.cache, "objects", "a.jar").is_relative_to(self.cache.resolve()))


class DownloadLimitTest(unittest.TestCase):
    def test_size_cap_stops_an_oversized_stream(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(acquire, "_open", return_value=FakeResponse()):
            with self.assertRaises(AcquisitionError) as raised:
                download("https://example.invalid/x", Path(root) / "x.jar", "test", HttpLimits(1, 1, 0),
                         max_bytes=10_000)
            self.assertEqual(raised.exception.kind, "size_mismatch")
            self.assertFalse((Path(root) / "x.jar.part").exists())

    def test_deadline_stops_a_trickling_stream(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(acquire, "_open", return_value=FakeResponse(delay=0.05)):
            started = time.monotonic()
            with self.assertRaises(AcquisitionError) as raised:
                download("https://example.invalid/x", Path(root) / "x.jar", "test", HttpLimits(1, 1, 0),
                         deadline=time.monotonic() + 0.3)
            self.assertEqual(raised.exception.kind, "deadline")
            self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
