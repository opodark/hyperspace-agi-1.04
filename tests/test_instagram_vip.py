# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.instagram_vip import InstagramVipStore, level_for  # noqa: E402


def test_levels():
    assert level_for(4) == ""
    assert level_for(5) == "vip"
    assert level_for(15) == "cerchia"
    assert level_for(30) == "musa"


def test_store_promotes_once_and_persists(tmp_path):
    path = tmp_path / "vips.json"
    store = InstagramVipStore(str(path))
    events = [store.record("123", "alice") for _ in range(6)]
    assert events[4]["promoted"] is True
    assert events[5]["promoted"] is False
    assert InstagramVipStore(str(path)).list()[0]["messages"] == 6
