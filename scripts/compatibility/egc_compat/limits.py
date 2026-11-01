"""Time and tick limits that a run may override from the command line (recorded in the result)."""

from __future__ import annotations

from dataclasses import fields
from typing import Any


class LimitSet:
    """Base for a limits dataclass: ``KEY=VALUE`` overrides and a JSON record."""

    @classmethod
    def from_overrides(cls, overrides: list[str]) -> tuple[Any, dict[str, Any]]:
        limits = cls()
        applied: dict[str, Any] = {}
        known = {field.name: field.type for field in fields(cls)}
        for override in overrides:
            key, separator, raw = override.partition("=")
            if not separator or key not in known:
                raise ValueError(f"unknown limit override {override!r}; known: {', '.join(known)}")
            value: Any = int(raw) if known[key] in (int, "int") else float(raw)
            if value <= 0:
                raise ValueError(f"limit {key} must be positive")
            setattr(limits, key, value)
            applied[key] = value
        return limits, applied

    def to_json(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}
