"""Python side of the Mineflayer companion: start it, send chat/commands, and query its events."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .procs import Line, ProcessRegistry

BOT_DIR = Path(__file__).resolve().parents[1] / "bot"
LOCKED_PACKAGES = ("mineflayer", "minecraft-protocol")


def _json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def node_major(version_text: str | None) -> int | None:
    match = re.match(r"v?(\d+)\.", (version_text or "").strip())
    return int(match.group(1)) if match else None


def client_preflight(node: str, bot_dir: Path = BOT_DIR) -> dict[str, Any]:
    """Check Node and the installed client packages against the lock before any server starts."""
    problems: list[str] = []
    package = _json(bot_dir / "package.json") or {}
    lock = _json(bot_dir / "package-lock.json") or {}
    minimum = node_major((package.get("engines") or {}).get("node", "").lstrip(">= ") + ".")
    try:
        completed = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30, check=False)
        node_version = completed.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired) as error:
        node_version = None
        problems.append(f"cannot run {node}: {error}")
    major = node_major(node_version)
    if node_version is not None and (major is None or minimum is None or major < minimum):
        problems.append(f"Node {node_version} is below the required {package.get('engines', {}).get('node')}")
    installed: dict[str, str | None] = {}
    for name in LOCKED_PACKAGES:
        locked = ((lock.get("packages") or {}).get(f"node_modules/{name}") or {}).get("version")
        present = (_json(bot_dir / "node_modules" / name / "package.json") or {}).get("version")
        installed[name] = present
        if present is None:
            problems.append(f"{name} is not installed; run `npm ci --prefix scripts/compatibility/bot`")
        elif present != locked:
            problems.append(f"{name} {present} is installed but the lock pins {locked}; run `npm ci`")
    return {"node": node_version, "required_node": (package.get("engines") or {}).get("node"),
            "packages": installed, "problems": problems}


@dataclass
class BotEvent:
    index: int
    monotonic: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


class BotClient:
    def __init__(self, registry: ProcessRegistry, node: str, username: str, host: str, port: int,
                 version: str, transcript_path: Path):
        self.username = username
        self.version = version
        self._events: list[BotEvent] = []
        self._condition = threading.Condition()
        self._next_request = 1
        command = [node, str(BOT_DIR / "bot.js"), "--host", host, "--port", str(port),
                   "--username", username, "--version", version]
        self.process = registry.start(f"bot-{username}", command, BOT_DIR, transcript_path,
                                      on_line=self._on_line)

    def _on_line(self, line: Line) -> None:
        try:
            payload = json.loads(line.text)
            kind = payload.pop("event")
        except (ValueError, KeyError, AttributeError):
            payload, kind = {"text": line.text}, "raw"
        with self._condition:
            self._events.append(BotEvent(len(self._events), line.monotonic, kind, payload))
            self._condition.notify_all()

    def offset(self) -> int:
        with self._condition:
            return len(self._events)

    def events(self, kind: str | None = None, since: int = 0) -> list[BotEvent]:
        with self._condition:
            return [event for event in self._events[since:] if kind is None or event.kind == kind]

    def chats(self, since: int = 0) -> list[BotEvent]:
        return self.events("chat", since)

    def wait_for(self, predicate: Callable[[BotEvent], bool], since: int, timeout_s: float) -> BotEvent | None:
        deadline = time.monotonic() + timeout_s
        cursor = since
        while True:
            with self._condition:
                fresh = self._events[cursor:]
                cursor = len(self._events)
            for event in fresh:
                if predicate(event):
                    return event
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.process.running():
                return None
            with self._condition:
                if len(self._events) == cursor:
                    self._condition.wait(min(remaining, 0.25))

    def wait_spawn(self, timeout_s: float) -> BotEvent | None:
        return self.wait_for(lambda event: event.kind in ("spawn", "end", "kicked"), 0, timeout_s)

    def wait_chat(self, pattern: re.Pattern[str], since: int, timeout_s: float) -> BotEvent | None:
        return self.wait_for(
            lambda event: event.kind == "chat" and bool(pattern.fullmatch(event.data.get("text", "").strip())),
            since, timeout_s)

    def spawned(self) -> bool:
        return any(event.kind == "spawn" for event in self.events())

    def disconnected(self, since: int = 0) -> list[BotEvent]:
        return [event for event in self.events(since=since) if event.kind in ("end", "kicked")]

    def protocol(self) -> dict[str, Any] | None:
        logins = self.events("login")
        return logins[0].data if logins else None

    def _request(self, payload: dict[str, Any]) -> bool:
        payload["id"] = self._next_request
        self._next_request += 1
        return self.process.send_line(json.dumps(payload))

    def chat(self, text: str) -> bool:
        return self._request({"cmd": "chat", "text": text})

    def quit(self, timeout_s: float = 5.0) -> None:
        if self.process.running():
            self._request({"cmd": "quit"})
            self.process.wait(timeout_s)
