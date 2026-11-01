"""Fabric Loader version predicates, enough to check a pinned dependency against its row.

A dependency jar declares which Minecraft and Loader versions it accepts (``depends.minecraft`` and
``depends.fabricloader`` in ``fabric.mod.json``). A pin is rejected when those predicates exclude
the row's Minecraft version or its pinned Loader, so a jar Loader would refuse never becomes part of
a documented install. This follows Loader's semantic-version rules: numeric components padded with
zero, a pre-release sorting below its release (an empty pre-release, as in ``1.21.5-``, below every
other), build metadata ignored, space-separated terms combined with AND, and a list combined with OR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

WILDCARDS = {"x", "X", "*"}
OPERATORS = (">=", "<=", ">", "<", "=", "~", "^")
NUMERIC = re.compile(r"^\d+$")


@dataclass(frozen=True)
class Version:
    parts: tuple[int | None, ...]  # None marks a wildcard component (predicates only)
    pre: tuple[str, ...] | None  # None: a release; ("",) for a trailing "-"


def parse_version(text: str, allow_wildcard: bool = False) -> Version:
    core, _plus, _build = text.strip().partition("+")
    core, dash, pre = core.partition("-")
    if not core:
        raise ValueError(f"empty version {text!r}")
    parts: list[int | None] = []
    for component in core.split("."):
        if component in WILDCARDS and allow_wildcard:
            parts.append(None)
        elif NUMERIC.match(component):
            if parts and parts[-1] is None:
                raise ValueError(f"{text!r}: a number cannot follow a wildcard")
            parts.append(int(component))
        else:
            raise ValueError(f"{text!r} is not a semantic version")
    return Version(tuple(parts), tuple(pre.split(".")) if dash else None)


def _compare_pre(left: tuple[str, ...] | None, right: tuple[str, ...] | None) -> int:
    if left is None or right is None:
        return (left is None) - (right is None)  # a release sorts above any pre-release
    for a, b in zip(left, right):
        if a == b:
            continue
        a_num, b_num = bool(NUMERIC.match(a)), bool(NUMERIC.match(b))
        if a_num and b_num:
            return (int(a) > int(b)) - (int(a) < int(b))
        if a_num != b_num:
            return -1 if a_num else 1
        return (a > b) - (a < b)
    return (len(left) > len(right)) - (len(left) < len(right))


def compare(left: Version, right: Version) -> int:
    for index in range(max(len(left.parts), len(right.parts))):
        a = left.parts[index] if index < len(left.parts) else 0
        b = right.parts[index] if index < len(right.parts) else 0
        if a is None or b is None:
            return 0  # a wildcard matches the rest
        if a != b:
            return (a > b) - (a < b)
    return _compare_pre(left.pre, right.pre)


def _term_matches(term: str, version: Version) -> bool:
    if term == "*":
        return True
    operator = next((op for op in OPERATORS if term.startswith(op)), "=")
    bound = parse_version(term[len(operator):] if term.startswith(operator) else term,
                          allow_wildcard=operator == "=")
    order = compare(version, bound)
    if operator == "=":
        return order == 0
    if operator == ">=":
        return order >= 0
    if operator == "<=":
        return order <= 0
    if operator == ">":
        return order > 0
    if operator == "<":
        return order < 0
    numbers = [part or 0 for part in bound.parts]
    same_major = (version.parts[0] if version.parts else 0) == numbers[0]
    if operator == "^":
        return order >= 0 and same_major
    # "~": the same major and minor (or just the major when only one component is given).
    if len(numbers) < 2:
        return order >= 0 and same_major
    minor = version.parts[1] if len(version.parts) > 1 else 0
    return order >= 0 and same_major and minor == numbers[1]


def matches(predicate: Any, version: str) -> bool:
    """Whether ``version`` satisfies a ``fabric.mod.json`` dependency value (string or list)."""
    if predicate is None:
        return True  # nothing declared
    alternatives = predicate if isinstance(predicate, list) else [predicate]
    if not alternatives or not all(isinstance(item, str) for item in alternatives):
        raise ValueError(f"unsupported predicate {predicate!r}")
    parsed = parse_version(version)
    return any(all(_term_matches(term, parsed) for term in alternative.split()) for alternative in alternatives)
