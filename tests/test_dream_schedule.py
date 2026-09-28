# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.dream_schedule import choose_author, in_hour_window  # noqa: E402


def test_night_window_supports_regular_and_midnight_ranges():
    assert in_hour_window(3, 0, 8)
    assert not in_hour_window(12, 0, 8)
    assert in_hour_window(23, 22, 6)
    assert in_hour_window(2, 22, 6)


def test_choose_author_balances_and_stops_at_cap():
    assert choose_author({"anna": 0, "aurora": 0}, maximum=2, turn=0) == "anna"
    assert choose_author({"anna": 1, "aurora": 0}, maximum=2, turn=0) == "aurora"
    assert choose_author({"anna": 2, "aurora": 1}, maximum=2, turn=0) == "aurora"
    assert choose_author({"anna": 2, "aurora": 2}, maximum=2, turn=0) is None
