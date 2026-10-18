"""Owned child processes, line-captured output, and process-tree cleanup.

Every process the runner starts is registered with a ``ProcessRegistry`` so cleanup kills only
PIDs this run started, never a pre-existing server.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Iterable


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def kill_tree(pid: int) -> str | None:
    """Forcefully end a process and all of its descendants; returns an error description or None."""
    if sys.platform == "win32":
        try:
            completed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                                       text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"taskkill failed: {error}"
        # 128: the process had already exited.
        if completed.returncode not in (0, 128):
            return f"taskkill exited {completed.returncode}: {(completed.stdout + completed.stderr).strip()[:200]}"
        return None
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        return None
    except PermissionError as error:
        return f"killpg failed: {error}"
    return None


@dataclass(frozen=True)
class Line:
    index: int
    monotonic: float
    wall: float
    text: str


class LineCapture:
    """Reads a child's output on a thread, mirrors it to a file, and keeps it searchable.

    Offsets are line indexes, so a check can ask for lines produced after a given moment.
    """

    def __init__(self, stream: IO[bytes], mirror_path: Path,
                 on_line: Callable[[Line], None] | None = None):
        self._stream = stream
        self._lines: list[Line] = []
        self._condition = threading.Condition()
        self._closed = False
        self._mirror = mirror_path.open("a", encoding="utf-8", newline="\n")
        self._on_line = on_line
        self._thread = threading.Thread(target=self._pump, name=f"capture-{mirror_path.name}", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            for raw in iter(self._stream.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                with self._condition:
                    line = Line(len(self._lines), time.monotonic(), time.time(), text)
                    self._lines.append(line)
                    self._mirror.write(text + "\n")
                    self._mirror.flush()
                    self._condition.notify_all()
                if self._on_line is not None:
                    self._on_line(line)
        finally:
            with self._condition:
                self._closed = True
                self._mirror.close()
                self._condition.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    def offset(self) -> int:
        with self._condition:
            return len(self._lines)

    def lines(self, since: int = 0, until: int | None = None) -> list[Line]:
        with self._condition:
            return self._lines[since:until]

    def find(self, pattern: re.Pattern[str], since: int = 0, until: int | None = None) -> list[tuple[Line, re.Match[str]]]:
        found = []
        for line in self.lines(since, until):
            match = pattern.search(line.text)
            if match:
                found.append((line, match))
        return found

    def wait_for(self, pattern: re.Pattern[str], since: int, timeout_s: float,
                 abort: Callable[[], bool] | None = None) -> tuple[Line, re.Match[str]] | None:
        deadline = time.monotonic() + timeout_s
        cursor = since
        while True:
            with self._condition:
                fresh = self._lines[cursor:]
                cursor = len(self._lines)
                closed = self._closed
            for line in fresh:
                match = pattern.search(line.text)
                if match:
                    return line, match
            remaining = deadline - time.monotonic()
            if remaining <= 0 or closed or (abort is not None and abort()):
                return None
            with self._condition:
                if len(self._lines) == cursor and not self._closed:
                    self._condition.wait(min(remaining, 0.25))

    def join(self, timeout_s: float) -> None:
        self._thread.join(timeout_s)


class ManagedProcess:
    def __init__(self, name: str, command: list[str], cwd: Path, output_path: Path,
                 env: dict[str, str] | None = None, on_line: Callable[[Line], None] | None = None):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.output_path = output_path
        self.started_wall = time.time()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self.popen = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=sys.platform != "win32",
        )
        self.killed = False
        self.kill_error: str | None = None
        try:
            self.output = LineCapture(self.popen.stdout, output_path, on_line)
        except BaseException:
            # The child exists but cannot be tracked: end it before giving up.
            kill_tree(self.popen.pid)
            try:
                self.popen.wait(10)
            except subprocess.TimeoutExpired:
                pass
            for stream in (self.popen.stdin, self.popen.stdout):
                if stream is not None:
                    stream.close()
            raise

    @property
    def pid(self) -> int:
        return self.popen.pid

    @property
    def exit_code(self) -> int | None:
        return self.popen.poll()

    def running(self) -> bool:
        return self.popen.poll() is None

    def send_line(self, text: str) -> bool:
        if not self.running() or self.popen.stdin is None:
            return False
        try:
            self.popen.stdin.write((text + "\n").encode("utf-8"))
            self.popen.stdin.flush()
            return True
        except OSError:
            return False

    def wait(self, timeout_s: float) -> int | None:
        try:
            return self.popen.wait(timeout_s)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> bool:
        """Kill the process tree; True when the process is confirmed to have exited."""
        if self.running():
            self.killed = True
            self.kill_error = kill_tree(self.pid)
            self.wait(10)
        return not self.running()

    def close_streams(self) -> None:
        self.output.join(5)
        for stream in (self.popen.stdin, self.popen.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


class ProcessRegistry:
    def __init__(self) -> None:
        self._processes: list[ManagedProcess] = []

    def start(self, *args, **kwargs) -> ManagedProcess:
        process = ManagedProcess(*args, **kwargs)
        self._processes.append(process)
        return process

    def owned(self) -> Iterable[ManagedProcess]:
        return list(self._processes)

    def kill_all(self) -> tuple[list[str], list[str]]:
        """End every owned process still running. Returns (killed, survivors); never raises."""
        killed: list[str] = []
        survivors: list[str] = []
        for process in reversed(self._processes):
            label = f"{process.name} (pid {process.pid})"
            try:
                if process.running():
                    if process.kill():
                        killed.append(label)
                    else:
                        survivors.append(f"{label}: {process.kill_error or 'still running after kill'}")
            except Exception as error:  # noqa: BLE001 - keep going for the remaining processes
                survivors.append(f"{label}: {type(error).__name__}: {error}")
            try:
                process.close_streams()
            except Exception:  # noqa: BLE001
                pass
        return killed, survivors
