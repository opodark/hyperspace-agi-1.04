# SPDX-License-Identifier: Apache-2.0
"""Pure scheduling helpers for the illustrated dream loop."""

from __future__ import annotations


def in_hour_window(hour: int, start: int, end: int) -> bool:
    """Support normal windows (1..7) and windows crossing midnight (22..6)."""
    hour, start, end = int(hour) % 24, int(start) % 24, int(end) % 24
    if start == end:
        return True
    return start <= hour < end if start < end else hour >= start or hour < end


def choose_author(counts: dict, *, maximum: int, turn: int = 0) -> str | None:
    """Choose the least-used sister, alternating ties, within the daily cap."""
    order = ("anna", "aurora") if int(turn) % 2 == 0 else ("aurora", "anna")
    available = [name for name in order if int(counts.get(name, 0)) < int(maximum)]
    if not available:
        return None
    return min(available, key=lambda name: int(counts.get(name, 0)))
