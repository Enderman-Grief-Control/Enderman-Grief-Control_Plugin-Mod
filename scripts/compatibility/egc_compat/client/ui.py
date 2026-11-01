"""Posted input to the game's own window, screenshots, and observer-confirmed UI steps.

Every step is "observe, then act, then confirm": the target widget is found in the observer's
current snapshot, the click goes to the centre of its reported bounds (converted from GUI to window
coordinates with the reported sizes), and the step counts only once the observer shows the expected
next state. A step that is never confirmed raises ``UiBlocked``; the extended checks then become
``blocked (harness_or_protocol)``, never pass or fail. Every action is logged with the snapshot
sequence numbers around it, and screenshots are kept as evidence.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from ..acquire import hash_file
from ..results import utc_now
from .observer import Snapshot, Snapshots

# Virtual-key code, scan code, extended-key flag.
KEYS = {
    "Escape": (0x1B, 0x01, False), "Enter": (0x0D, 0x1C, False), "Tab": (0x09, 0x0F, False),
    "F2": (0x71, 0x3C, False), "Backspace": (0x08, 0x0E, False), "Up": (0x26, 0x48, True),
    "Down": (0x28, 0x50, True), "End": (0x23, 0x4F, True),
}


ATTEMPTS = 3  # a click or key that changed nothing on screen is sent again, at most this many times in all
MK_LBUTTON = 0x0001


class UiBlocked(Exception):
    """A UI step the observer never confirmed."""


def key_lparams(scan: int, extended: bool) -> tuple[int, int]:
    """WM_KEYDOWN and WM_KEYUP lParam: repeat count 1, scan code, extended flag; key-up adds bits 30-31."""
    down = 1 | (scan << 16) | ((1 << 24) if extended else 0)
    return down, down | (1 << 30) | (1 << 31)


def point_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def gui_to_window(gx: float, gy: float, gui: dict[str, Any]) -> tuple[int, int]:
    """GUI-scaled coordinates to window client pixels, the inverse of the game's mouse scaling."""
    return (round(gx * gui["window_w"] / gui["scaled_w"]), round(gy * gui["window_h"] / gui["scaled_h"]))


def center(box: dict[str, Any], gui: dict[str, Any]) -> tuple[int, int]:
    return gui_to_window(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2, gui)


def char_codes(text: str) -> list[int]:
    """WM_CHAR code units (UTF-16) for ``text``; only printable characters are accepted."""
    if any(not ch.isprintable() for ch in text):
        raise ValueError(f"cannot type {text!r}")
    encoded = text.encode("utf-16-le")
    return [int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)]


class UiDriver:
    def __init__(self, pid: Callable[[], int | None], snapshots: Snapshots, game_dir: Path, evidence: Path,
                 say: Callable[[str], None], step_s: float = 20, retry_s: float = 4):
        self.pid = pid
        self.snapshots = snapshots
        self.game_dir = game_dir
        self.evidence = evidence
        self.say = say
        self.step_s = step_s
        self.retry_s = retry_s
        self.actions = evidence / "ui-actions.jsonl"
        (evidence / "ui").mkdir(parents=True, exist_ok=True)
        self.shots = 0

    # Low level -------------------------------------------------------------------------------
    def record(self, action: dict[str, Any]) -> None:
        action = {"utc": utc_now(), "seq": self.snapshots.last_seq, **action}
        with self.actions.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(action) + "\n")

    def window(self) -> int:
        from . import win32
        pid = self.pid()
        hwnd = win32.game_window(pid) if pid else None
        if hwnd is None:
            raise UiBlocked("the game window was not found")
        return hwnd

    def key(self, name: str, hold_s: float = 0.06) -> None:
        from . import win32
        vk, scan, extended = KEYS[name]
        down, up = key_lparams(scan, extended)
        hwnd = self.window()
        win32.post(hwnd, win32.WM_KEYDOWN, vk, down)
        time.sleep(hold_s)
        win32.post(hwnd, win32.WM_KEYUP, vk, up)
        self.record({"action": "key", "key": name})

    def click(self, x: int, y: int, label: str) -> None:
        """Move and press back to back, so no real mouse movement is processed in between.

        The game recentres the OS cursor over its window when a screen opens from in-game, and it
        clicks wherever its last mouse-move put the cursor. Windows hands a window its posted
        messages before queued hardware input, so a move posted right before the button keeps a
        real ``WM_MOUSEMOVE`` (from the recentred cursor or the person's own mouse) from landing in
        between.
        """
        from . import win32
        hwnd = self.window()
        lparam = point_lparam(x, y)
        win32.post(hwnd, win32.WM_MOUSEMOVE, 0, lparam)
        win32.post(hwnd, win32.WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(0.05)
        win32.post(hwnd, win32.WM_MOUSEMOVE, MK_LBUTTON, lparam)
        win32.post(hwnd, win32.WM_LBUTTONUP, 0, lparam)
        time.sleep(0.1)
        win32.post(hwnd, win32.WM_MOUSEMOVE, 0, point_lparam(2, 2))  # park the cursor: no hover tooltips
        self.record({"action": "click", "x": x, "y": y, "target": label})

    def type_text(self, text: str) -> None:
        from . import win32
        hwnd = self.window()
        for code in char_codes(text):
            win32.post(hwnd, win32.WM_CHAR, code, 1)
            time.sleep(0.03)
        self.record({"action": "type", "text": text})

    def screenshot(self, label: str) -> str | None:
        """F2, then keep the game's own new screenshot as evidence; returns its path relative to evidence."""
        folder = self.game_dir / "screenshots"
        before = set(folder.glob("*.png")) if folder.exists() else set()
        self.key("F2")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            new = (set(folder.glob("*.png")) if folder.exists() else set()) - before
            if new:
                time.sleep(0.5)  # let the game finish writing it
                self.shots += 1
                target = self.evidence / "ui" / f"{self.shots:02d}-{label}.png"
                shutil.copyfile(max(new, key=lambda path: path.stat().st_mtime), target)
                self.record({"action": "screenshot", "label": label, "file": target.name,
                             "sha256": hash_file(target)})
                return f"ui/{target.name}"
            time.sleep(0.25)
        self.say(f"screenshot {label} did not appear")
        return None

    # Observer-confirmed steps ----------------------------------------------------------------
    def wait(self, what: str, predicate: Callable[[Snapshot], bool], after_seq: int = 0,
             timeout_s: float | None = None) -> Snapshot:
        found = self.snapshots.wait_for(predicate, timeout_s or self.step_s, after_seq,
                                        alive=lambda: self.pid() is not None)
        if found is None:
            current = self.snapshots.refresh()
            detail = current.data if current is not None else "no observer snapshot"
            self.record({"action": "unconfirmed", "expected": what})
            raise UiBlocked(f"not confirmed: {what} (observer: {json.dumps(detail)[:400]})")
        self.record({"action": "confirmed", "state": what, "confirmed_seq": found.seq})
        return found

    def current(self) -> Snapshot:
        return self.wait("an observer snapshot", lambda snapshot: True)

    def input_lost(self, seq: int) -> bool:
        """True only when the observer stayed live and nothing on screen changed after ``seq``.

        The agent writes a line on every change and a heartbeat at least once a second, so newer
        lines with identical content mean "live and unchanged": the input was lost, and every target
        here has a visible effect, so it is safe to send again. No newer line at all means the
        observer could not read (the game may be busy with the very action the input started), so
        nothing is resent and the step simply waits for its expected state.
        """
        base = next((snapshot.data for snapshot in reversed(self.snapshots.all) if snapshot.seq == seq), None)
        deadline = time.monotonic() + self.retry_s
        heartbeat = False
        while time.monotonic() < deadline and self.pid() is not None:
            self.snapshots.refresh()
            newer = [snapshot for snapshot in self.snapshots.all if snapshot.seq > seq]
            if any(snapshot.data != base for snapshot in newer):
                return False
            heartbeat = heartbeat or bool(newer)
            time.sleep(0.1)
        return heartbeat

    def press_widget(self, what: str, find: Callable[[Snapshot], dict[str, Any] | None],
                     then: Callable[[Snapshot], bool], expect: str, timeout_s: float | None = None) -> Snapshot:
        """Click the widget ``find`` locates in the current snapshot, then wait for ``then``.

        A click after which the observer stayed live but nothing on screen changed was lost (it did
        not reach the widget) and is retried, up to ``ATTEMPTS`` times in all; see ``input_lost``.
        """
        for attempt in range(1, ATTEMPTS + 1):
            snapshot = self.current()
            widget = find(snapshot)
            if widget is None or not widget.get("visible", True):
                raise UiBlocked(f"{what} is not on screen (messages: {snapshot.messages()[:20]})")
            if not widget.get("active", True):
                raise UiBlocked(f"{what} is shown but not active")
            self.click(*center(widget, snapshot.data["gui"]), what)
            if attempt < ATTEMPTS and self.input_lost(snapshot.seq):
                self.record({"action": "retry", "target": what, "attempt": attempt, "reason": "screen unchanged"})
                continue
            return self.wait(expect, then, after_seq=snapshot.seq, timeout_s=timeout_s)
        raise UiBlocked(f"{what}: unreachable")  # the loop always returns or raises

    def press_key(self, name: str, then: Callable[[Snapshot], bool], expect: str) -> Snapshot:
        for attempt in range(1, ATTEMPTS + 1):
            before = self.snapshots.last_seq
            self.key(name)
            if attempt < ATTEMPTS and self.input_lost(before):
                self.record({"action": "retry", "key": name, "attempt": attempt, "reason": "screen unchanged"})
                continue
            return self.wait(expect, then, after_seq=before)
        raise UiBlocked(f"{name}: unreachable")
