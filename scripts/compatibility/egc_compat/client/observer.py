"""The screen observer: its name map, its agent jar, and its snapshots.

The extended checks confirm every UI step from what is actually on screen, never from a click
alone. ``ScreenObserver.java`` is a read-only Java agent attached only to extended-check sessions;
it reports the open screen, its widgets, and selection-list rows as JSON lines. It needs about
twenty intermediary member names, which come from joining the pinned client mappings (Mojang name
to obfuscated name) with the pinned intermediary jar (obfuscated name to intermediary name).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..acquire import contained, fs_path, hash_file

SOURCE = Path(__file__).resolve().parent / "agent" / "ScreenObserver.java"
AGENT_CLASS = "ScreenObserver"

CLASSES = {
    "Minecraft": "net.minecraft.client.Minecraft",
    "Window": "com.mojang.blaze3d.platform.Window",
    "Screen": "net.minecraft.client.gui.screens.Screen",
    "ContainerEventHandler": "net.minecraft.client.gui.components.events.AbstractContainerEventHandler",
    "AbstractWidget": "net.minecraft.client.gui.components.AbstractWidget",
    "EditBox": "net.minecraft.client.gui.components.EditBox",
    "AbstractSelectionList": "net.minecraft.client.gui.components.AbstractSelectionList",
    "Component": "net.minecraft.network.chat.Component",
    "PauseScreen": "net.minecraft.client.gui.screens.PauseScreen",
    "TitleScreen": "net.minecraft.client.gui.screens.TitleScreen",
    "SelectWorldScreen": "net.minecraft.client.gui.screens.worldselection.SelectWorldScreen",
}
# Interfaces that first declare members the observer calls on implementing classes. Intermediary
# lists a method only where it is first declared, and keeps that name in every implementation.
ROOTS = {
    "ContainerEventHandlerInterface": "net.minecraft.client.gui.components.events.ContainerEventHandler",
    "LayoutElement": "net.minecraft.client.gui.layouts.LayoutElement",
}
# (kind, owner the agent calls it on, Mojang name, Mojang parameter types, declaring type if not the owner)
MEMBERS = (
    ("method", "Minecraft", "getInstance", (), None),
    ("method", "Minecraft", "getWindow", (), None),
    ("field", "Minecraft", "screen", None, None),
    ("method", "Window", "getScreenWidth", (), None),
    ("method", "Window", "getScreenHeight", (), None),
    ("method", "Window", "getGuiScaledWidth", (), None),
    ("method", "Window", "getGuiScaledHeight", (), None),
    ("method", "Screen", "getTitle", (), None),
    ("method", "Screen", "children", (), "ContainerEventHandlerInterface"),
    ("method", "ContainerEventHandler", "getFocused", (), "ContainerEventHandlerInterface"),
    ("method", "AbstractWidget", "getMessage", (), None),
    ("method", "AbstractWidget", "getX", (), "LayoutElement"),
    ("method", "AbstractWidget", "getY", (), "LayoutElement"),
    ("method", "AbstractWidget", "getWidth", (), "LayoutElement"),
    ("method", "AbstractWidget", "getHeight", (), "LayoutElement"),
    ("field", "AbstractWidget", "active", None, None),
    ("field", "AbstractWidget", "visible", None, None),
    ("method", "EditBox", "getValue", (), None),
    ("method", "AbstractSelectionList", "getSelected", (), None),
    ("method", "AbstractSelectionList", "getRowTop", ("int",), None),
    ("method", "AbstractSelectionList", "getRowBottom", ("int",), None),
    ("method", "AbstractSelectionList", "getRowLeft", (), None),
    ("method", "AbstractSelectionList", "getRowWidth", (), None),
    ("method", "Component", "getString", (), None),
)
# Without these the observer cannot see a screen at all (mirrors REQUIRED in the agent).
REQUIRED = (
    "method.Minecraft.getInstance", "method.Minecraft.getWindow", "field.Minecraft.screen",
    "method.Window.getScreenWidth", "method.Window.getScreenHeight", "method.Window.getGuiScaledWidth",
    "method.Window.getGuiScaledHeight", "method.Screen.getTitle", "method.Screen.children",
    "method.AbstractWidget.getMessage", "method.AbstractWidget.getX", "method.AbstractWidget.getY",
    "method.AbstractWidget.getWidth", "method.AbstractWidget.getHeight", "field.AbstractWidget.active",
    "field.AbstractWidget.visible", "method.Component.getString",
)
PRIMITIVES = {"boolean": "Z", "byte": "B", "char": "C", "short": "S", "int": "I", "long": "J", "float": "F",
              "double": "D", "void": "V"}
PG_CLASS = re.compile(r"^(\S+) -> (\S+):$")
PG_METHOD = re.compile(r"^\s+(?:\d+:\d+:)?(\S+) ([^\s(]+)\(([^)]*)\)(?::\d+(?::\d+)?)? -> (\S+)$")
PG_FIELD = re.compile(r"^\s+(\S+) (\S+) -> (\S+)$")


@dataclass
class ProguardClass:
    obf: str
    fields: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (type, obf)
    methods: dict[tuple[str, tuple[str, ...]], tuple[str, str]] = field(default_factory=dict)  # -> (return, obf)


def parse_proguard(text: str) -> dict[str, ProguardClass]:
    classes: dict[str, ProguardClass] = {}
    current: ProguardClass | None = None
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        match = PG_CLASS.match(line)
        if match:
            current = classes.setdefault(match.group(1), ProguardClass(match.group(2)))
            continue
        if current is None:
            continue
        method = PG_METHOD.match(line)
        if method:
            params = tuple(p for p in method.group(3).split(",") if p)
            current.methods.setdefault((method.group(2), params), (method.group(1), method.group(4)))
            continue
        member = PG_FIELD.match(line)
        if member:
            current.fields.setdefault(member.group(2), (member.group(1), member.group(3)))
    return classes


def parse_tiny(text: str) -> dict[str, Any]:
    """Tiny v1 or v2 with namespaces official -> intermediary."""
    lines = text.splitlines()
    classes: dict[str, str] = {}
    members: dict[tuple[str, str, str, str], str] = {}  # (kind, owner obf, obf name, obf descriptor)
    if lines and lines[0].startswith("v1\t"):
        for line in lines[1:]:
            parts = line.split("\t")
            if parts[0] == "CLASS":
                classes[parts[1]] = parts[2]
            elif parts[0] in ("FIELD", "METHOD"):
                members[(parts[0].lower(), parts[1], parts[3], parts[2])] = parts[4]
    elif lines and lines[0].startswith("tiny\t2\t"):
        owner = None
        for line in lines[1:]:
            parts = line.split("\t")
            if parts[0] == "c":
                owner, classes[parts[1]] = parts[1], parts[2]
            elif len(parts) >= 5 and parts[0] == "" and parts[1] in ("m", "f") and owner:
                members[("method" if parts[1] == "m" else "field", owner, parts[3], parts[2])] = parts[4]
    else:
        raise ValueError("unsupported tiny mappings header")
    return {"classes": classes, "members": members}


def descriptor_type(type_name: str, class_map: dict[str, str]) -> str:
    dimensions = 0
    while type_name.endswith("[]"):
        type_name, dimensions = type_name[:-2], dimensions + 1
    base = PRIMITIVES.get(type_name) or f"L{class_map.get(type_name, type_name).replace('.', '/')};"
    return "[" * dimensions + base


def derive(mappings: str, tiny: str) -> tuple[dict[str, str], list[str]]:
    """The observer's name map (``class.X``, ``method.X.name``, ``field.X.name``) and what is missing."""
    proguard = parse_proguard(mappings)
    intermediary = parse_tiny(tiny)
    obf_of = {name: entry.obf for name, entry in proguard.items()}
    names: dict[str, str] = {}
    missing: list[str] = []
    for short, mojang in CLASSES.items():
        entry = proguard.get(mojang)
        inter = intermediary["classes"].get(entry.obf) if entry else None
        if inter:
            names[f"class.{short}"] = inter.replace("/", ".")
        else:
            missing.append(f"class.{short}")
    for kind, owner, name, params, declared_in in MEMBERS:
        key = f"{kind}.{owner}.{name}"
        entry = proguard.get(ROOTS[declared_in] if declared_in else CLASSES[owner])
        found = None
        if entry is not None and kind == "field" and name in entry.fields:
            field_type, obf = entry.fields[name]
            found = intermediary["members"].get(("field", entry.obf, obf, descriptor_type(field_type, obf_of)))
        elif entry is not None and kind == "method" and (name, params) in entry.methods:
            returns, obf = entry.methods[(name, params)]
            desc = "(" + "".join(descriptor_type(p, obf_of) for p in params) + ")" + descriptor_type(returns, obf_of)
            # A name kept by the obfuscator (a library interface method) has no intermediary entry.
            found = intermediary["members"].get(("method", entry.obf, obf, desc)) or (obf if obf == name else None)
        if found:
            names[key] = found
        else:
            missing.append(key)
    return names, missing


def tiny_from_jar(jar: Path) -> str:
    with zipfile.ZipFile(fs_path(jar)) as archive:
        return archive.read("mappings/mappings.tiny").decode("utf-8")


def source_sha256() -> str:
    return hash_file(SOURCE)


def build_agent(javac: str, cache_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Compile the agent with ``javac --release 21`` once per source hash; return the jar and its identity."""
    digest = source_sha256()
    folder = contained(cache_dir, "client", "observer", digest[:16])
    jar = folder / "screen-observer.jar"
    if not jar.is_file():
        staging = folder.with_name(folder.name + ".staging")
        shutil.rmtree(staging, ignore_errors=True)
        (staging / "classes").mkdir(parents=True)
        completed = subprocess.run([javac, "--release", "21", "-d", str(staging / "classes"), str(SOURCE)],
                                   capture_output=True, text=True, timeout=180, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"javac failed: {(completed.stdout + completed.stderr).strip()[:500]}")
        manifest = f"Manifest-Version: 1.0\nPremain-Class: {AGENT_CLASS}\nCan-Retransform-Classes: false\n"
        with zipfile.ZipFile(staging / "screen-observer.jar", "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF", manifest)
            for path in sorted((staging / "classes").rglob("*.class")):
                archive.write(path, path.relative_to(staging / "classes").as_posix())
        shutil.rmtree(staging / "classes")
        shutil.rmtree(folder, ignore_errors=True)
        staging.rename(folder)
    version = subprocess.run([javac, "-version"], capture_output=True, text=True, timeout=60, check=False)
    return jar, {"source_sha256": digest, "agent_sha256": hash_file(jar),
                 "javac": (version.stdout or version.stderr).strip()}


def write_properties(path: Path, names: dict[str, str], output: Path, interval_ms: int = 200) -> str:
    """The agent's argument file; returns its SHA-256 (the name-map identity recorded in the result)."""
    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")

    lines = [f"out={escape(str(output))}", f"interval_ms={interval_ms}"]
    lines += [f"{key}={escape(value)}" for key, value in sorted(names.items())]
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Snapshots ------------------------------------------------------------------------------------
@dataclass
class Snapshot:
    seq: int
    t_ms: int
    data: dict[str, Any]

    @property
    def status(self) -> str:
        return self.data.get("status", "missing")

    @property
    def screen_class(self) -> str | None:
        return (self.data.get("screen") or {}).get("class")

    @property
    def widgets(self) -> list[dict[str, Any]]:
        return self.data.get("widgets") or []

    @property
    def lists(self) -> list[dict[str, Any]]:
        return self.data.get("lists") or []

    def widget(self, *, message: str | None = None, prefix: str | None = None, field_name: str | None = None
               ) -> dict[str, Any] | None:
        for widget in self.widgets:
            text = widget.get("message") or ""
            if message is not None and text != message:
                continue
            if prefix is not None and not text.startswith(prefix):
                continue
            if field_name is not None and widget.get("field") != field_name:
                continue
            return widget
        return None

    def messages(self) -> list[str]:
        return [widget.get("message") or "" for widget in self.widgets]


class Snapshots:
    """Reads the agent's JSON lines incrementally; the last line is what is on screen now."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._buffer = b""
        self.all: list[Snapshot] = []

    def refresh(self) -> Snapshot | None:
        if self.path.is_file():
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
            self._offset += len(chunk)
            self._buffer += chunk
            *complete, self._buffer = self._buffer.split(b"\n")
            for raw in complete:
                if raw.strip():
                    record = json.loads(raw.decode("utf-8"))
                    self.all.append(Snapshot(record["seq"], record["t_ms"], record["snapshot"]))
        return self.all[-1] if self.all else None

    def wait_for(self, predicate: Callable[[Snapshot], bool], timeout_s: float, after_seq: int = 0,
                 alive: Callable[[], bool] | None = None) -> Snapshot | None:
        """The current state once it is newer than ``after_seq`` and matches; None on timeout or failure.

        The agent writes a line only when the screen changes, so the last line is what is on screen.
        An "unavailable" or "error" line ends the wait at once.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            current = self.refresh()
            if current is not None and current.status in ("unavailable", "error"):
                return None
            if current is not None and current.seq > after_seq and predicate(current):
                return current
            if time.monotonic() >= deadline or (alive is not None and not alive()):
                return None
            time.sleep(0.1)

    @property
    def last_seq(self) -> int:
        self.refresh()
        return self.all[-1].seq if self.all else 0
