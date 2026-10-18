"""Check observed denial announcements against the shipped rate-limiter contract.

The limiter (``DenialRateLimiter``) announces the first denial of a type immediately as "x1",
tallies later denials silently until the cooldown has passed since the last announcement, then
announces the tally (including the triggering denial) on the next denial. So for announcements
a_1..a_m with counts n_1..n_m, a_j is triggered by log line number n_1 + ... + n_j.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Announcement:
    count: int
    received_s: float  # monotonic receipt time


@dataclass(frozen=True)
class Verdict:
    ok: bool
    problems: tuple[str, ...]
    triggering_lines: tuple[int, ...]


def evaluate(denial_lines: int, announcements: list[Announcement], cooldown_s: float,
             paused_after_line: int | None = None, paused_window: tuple[float, float] | None = None,
             tolerance_s: float = 1.0) -> Verdict:
    """Validate announcements of one denial type in one world.

    ``denial_lines`` is the number of individual denial log lines of that type so far.
    ``paused_after_line`` is the number of lines logged before the denial source was paused
    for longer than the cooldown, during ``paused_window`` (monotonic start, end). Nothing may
    be announced inside the pause, and the first announcement after it must be triggered by the
    next line, rolling up every pending denial.
    """
    problems: list[str] = []
    if not announcements:
        return Verdict(False, ("no announcement was received",), ())
    if announcements[0].count != 1:
        problems.append(f"first announcement was x{announcements[0].count}, expected x1")
    triggering: list[int] = []
    total = 0
    for announcement in announcements:
        if announcement.count < 1:
            problems.append(f"announcement count x{announcement.count} is not positive")
        total += announcement.count
        triggering.append(total)
    if total > denial_lines:
        problems.append(f"announced {total} denials but only {denial_lines} were logged")
    for previous, current in zip(announcements, announcements[1:]):
        gap = current.received_s - previous.received_s
        if gap < cooldown_s - tolerance_s:
            problems.append(f"announcements {gap:.2f}s apart, inside the {cooldown_s:g}s cooldown")
    if paused_window is not None:
        inside = [a for a in announcements if paused_window[0] < a.received_s < paused_window[1]]
        if inside:
            problems.append(f"{len(inside)} announcement(s) arrived while denials were paused")
    if paused_after_line is not None:
        after = [line for line in triggering if line > paused_after_line]
        if not after:
            problems.append("no announcement followed the paused interval")
        elif after[0] != paused_after_line + 1:
            problems.append(
                f"first announcement after the pause covers line {after[0]}, expected "
                f"line {paused_after_line + 1} to flush every pending denial")
    return Verdict(not problems, tuple(problems), tuple(triggering))
