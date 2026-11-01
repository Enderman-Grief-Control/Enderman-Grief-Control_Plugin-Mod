"""One client process: start it, wait for an end condition, close it gracefully, keep its evidence.

The process is registered with the row's ``ProcessRegistry``, so the row watchdog and cleanup kill
only processes this run started. A session ends on its scenario's end condition, process exit, or
its deadline. The client is then closed with WM_CLOSE to its own window, which saves the world;
the process tree is killed only when no window is left (a crash or Loader's error screen) or the
client does not exit in time.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..procs import ManagedProcess, ProcessRegistry
from ..results import utc_now


@dataclass
class SessionRecord:
    name: str
    started_utc: str
    end_reason: str = "not started"
    seconds_to_end: float | None = None
    closed_by: str | None = None
    exit_code: int | None = None
    ended_utc: str | None = None
    crash_reports: list[str] = field(default_factory=list)
    pid: int | None = None

    def to_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


class ClientSession:
    def __init__(self, registry: ProcessRegistry, name: str, command: list[str], game_dir: Path, evidence_dir: Path,
                 say: Callable[[str], None]):
        self.registry = registry
        self.name = name
        self.command = command
        self.game_dir = game_dir
        self.evidence_dir = evidence_dir
        self.say = say
        self.process: ManagedProcess | None = None
        self.record = SessionRecord(name=name, started_utc=utc_now())
        self._started = 0.0

    def start(self) -> ManagedProcess:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._started = time.monotonic()
        self.process = self.registry.start(f"client-{self.name}", self.command, self.game_dir,
                                           self.evidence_dir / "stdout.log")
        self.record.pid = self.process.pid
        self.say(f"client session {self.name} started (pid {self.process.pid})")
        return self.process

    def lines(self, since: int = 0) -> list[str]:
        return [line.text for line in self.process.output.lines(since)] if self.process else []

    def running(self) -> bool:
        return self.process is not None and self.process.running()

    def wait_for_end(self, end: Callable[[list[str]], str | None], timeout_s: float,
                     deadline: float | None = None) -> str:
        """Poll the log until ``end`` names a reason, the process exits, or the time runs out."""
        limit = time.monotonic() + timeout_s
        if deadline is not None:
            limit = min(limit, deadline)
        while True:
            if not self.running():
                reason = end(self.lines()) or "process exited"
                break
            reason = end(self.lines())
            if reason:
                break
            if time.monotonic() >= limit:
                reason = "deadline"
                break
            time.sleep(0.5)
        self.record.end_reason = reason
        self.record.seconds_to_end = round(time.monotonic() - self._started, 1)
        return reason

    def close(self, settle_s: float, crashed: bool, close_s: float = 90, crash_exit_s: float = 45) -> None:
        """WM_CLOSE first (it saves the world); kill the tree only when that is impossible or ignored."""
        process = self.process
        if process is None:
            return
        if crashed and process.running():
            process.wait(crash_exit_s)  # let the game finish writing its crash report and exit itself
        if process.running():
            time.sleep(settle_s)
            from . import win32
            try:
                if win32.close(process.pid):
                    self.record.closed_by = "WM_CLOSE"
                    process.wait(close_s)
            except OSError as error:
                self.say(f"WM_CLOSE failed: {error}")
        if process.running():
            process.kill()
            self.record.closed_by = (f"{self.record.closed_by} then " if self.record.closed_by else "") + "taskkill"
        process.close_streams()
        self.record.exit_code = process.exit_code
        self.record.ended_utc = utc_now()

    def collect(self, extra: dict[str, Path] | None = None) -> None:
        """Copy the client's logs, crash reports, and named files into the evidence folder."""
        for name in ("latest.log",):
            source = self.game_dir / "logs" / name
            if source.is_file():
                shutil.copyfile(source, self.evidence_dir / name)
        crashes = self.game_dir / "crash-reports"
        if crashes.is_dir():
            for report in sorted(crashes.glob("*.txt")):
                shutil.copyfile(report, self.evidence_dir / report.name)
                self.record.crash_reports.append(report.name)
        for label, path in (extra or {}).items():
            if path.is_file():
                shutil.copyfile(path, self.evidence_dir / label)
