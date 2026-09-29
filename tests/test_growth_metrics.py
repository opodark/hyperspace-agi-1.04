# SPDX-License-Identifier: Apache-2.0
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.growth_metrics import compute_growth  # noqa: E402


def test_conta_le_serie_e_lo_stato_pubblicazione():
    voci = [
        {"id": "1", "tipo": "sogno", "author": "anna",
         "ts": "2026-09-29T10:00:00+00:00", "instagram_status": "published"},
        {"id": "2", "tipo": "poesia", "author": "aurora",
         "ts": "2026-09-29T11:00:00+00:00"},
        {"id": "3", "tipo": "post", "author": "anna",
         "ts": "2026-09-20T10:00:00+00:00"},
    ]
    now = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
    r = compute_growth(voci, {}, days=7, now=now)
    assert r["series"]["sogno"]["total"] == 1
    assert r["series"]["poesia"]["total"] == 1
    assert r["series"]["post"]["total"] == 1
    # solo sogno e poesia cadono negli ultimi 7 giorni
    assert r["series"]["sogno"]["recent"] == 1
    assert r["series"]["post"]["recent"] == 0
    assert r["published"]["published"] == 1


def test_conta_i_livelli_della_cerchia_e_la_ritenzione():
    people = {
        "1": {"level": "vip", "messages": 6, "last_seen": "2026-09-29T09:00:00+00:00"},
        "2": {"level": "cerchia", "messages": 15, "last_seen": "2026-09-10T09:00:00+00:00"},
        "3": {"level": "", "messages": 2, "last_seen": "2026-09-29T09:00:00+00:00"},
    }
    now = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
    r = compute_growth([], people, days=7, now=now)
    assert r["vips"]["total_contacts"] == 3
    assert r["vips"]["levels"]["vip"] == 1
    assert r["vips"]["levels"]["cerchia"] == 1
    assert r["vips"]["levels"]["none"] == 1
    assert r["vips"]["active_last_n_days"] == 2
    assert r["vips"]["total_messages"] == 23
