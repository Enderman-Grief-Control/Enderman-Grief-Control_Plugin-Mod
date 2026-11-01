"""Reviewed client WARN/ERROR lines: each entry gives its reason and the run that showed it.

``client_errors`` passes only when every WARN, ERROR, or FATAL line of a core session matches an
entry here. An unmatched line makes the check ``unknown``, which blocks a row pass until the line
is reviewed and, with evidence from a control, added. Paths in ``shown_by`` are relative to the
ignored ``test-servers/compat-client/runs/`` folder of Substep 10F; "vanilla" controls installed no
mod at all, and "modmenu-only" controls installed only the row's Mod Menu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Entry:
    pattern: re.Pattern[str]
    reason: str
    shown_by: str
    follows: re.Pattern[str] | None = None  # must appear within the next three lines


ALLOWED = (
    Entry(re.compile(r"\[Download-\d+/ERROR\]: Failed to fetch user properties$"),
          "offline dummy access token (HTTP 401)", "m10f-controls/<1.21.2-1.21.11>/vanilla"),
    Entry(re.compile(r"\[Download-\d+/ERROR\]: Failed to fetch Realms feature flags$"),
          "offline account; Realms is unreachable without a session", "m10f-controls/<1.21.5-1.21.11>/vanilla"),
    Entry(re.compile(r"\[Render thread/WARN\]: Missing sound for event: minecraft:"
                     r"(item\.goat_horn\.play|entity\.goat\.screaming\.horn_break|block\.spawner\.fall)$"),
          "vanilla sound-event warning", "m10f-negative/1.21 and m10f-controls/1.21.2/vanilla"),
    Entry(re.compile(r"\[Render thread/WARN\]: Shader rendertype_entity_translucent_emissive could not find sampler "
                     r"named Sampler2 in the specified shader program\.$"),
          "vanilla shader warning", "m10f-negative/1.21"),
    Entry(re.compile(r"\[Render thread/WARN\]: minecraft:pipeline/entity_translucent_emissive shader program does not "
                     r"use sampler Sampler2 defined in the pipeline\. This might be a bug\.$"),
          "vanilla shader-pipeline warning", "m10f-controls/1.21.5/vanilla"),
    Entry(re.compile(r"\[main/WARN\]: Warnings were found!$"),
          "Loader recommendation from the project jar's bundled Adventure module (optional dependency; only "
          "without Fabric API installed)", "m10f/1.21/egc (published 1.2.0; the 10E server rows log the same)",
          follows=re.compile(r"\(adventure-platform-fabric\) \S+ recommends any version of fabric-networking-api-v1")),
    Entry(re.compile(r"\[IO-Worker-\d+/ERROR\]: Couldn't connect to realms$"),
          "title screen with an offline account (extended sessions only; core sessions use quick play)",
          "m10f-ui/1.21-s1 (no control visits the title screen; 10F3 confirms it with a Loader-only session)"),
)


def classify(lines: list[str], warn_error: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split WARN/ERROR records into (reviewed with their reason, unreviewed)."""
    known, unknown = [], []
    for record in warn_error:
        entry = next((candidate for candidate in ALLOWED if candidate.pattern.search(record["text"])
                      and (candidate.follows is None
                           or any(candidate.follows.search(line) for line in lines[record["line"] + 1:record["line"] + 4]))),
                     None)
        if entry is None:
            unknown.append(record)
        else:
            known.append({**record, "reason": entry.reason, "shown_by": entry.shown_by})
    return known, unknown
