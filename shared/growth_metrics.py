# SPDX-License-Identifier: Apache-2.0
"""Metriche di crescita, puro: dai dati grezzi al report settimanale.

Legge le strutture già persistite (diario, VIP Instagram) e produce i KPI della
strategia (docs/growth-strategy.md): volume per serie, stato di pubblicazione,
conversione DM→cerchia e ritenzione. Puro e testabile: nessun filesystem, nessun
modello, nessuna rete — il chiamante passa i dati già caricati e riceve un dict.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

SERIES = ("sogno", "poesia", "dialogo", "post")


def _parse_ts(value: str, now: datetime) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(now.tzinfo or timezone.utc)


def compute_growth(voci, people, *, days: int = 7,
                   now: datetime | None = None) -> dict:
    """Report dei KPI dalle strutture raw (voci del diario, people dei VIP)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    soglia = now - timedelta(days=max(1, int(days)))

    series = {s: {"total": 0, "recent": 0, "authors": {}} for s in SERIES}
    published: dict[str, int] = {}
    for v in (voci or []):
        tipo = str(v.get("tipo") or "")
        if tipo not in series:
            tipo = "post"
        series[tipo]["total"] += 1
        ts = _parse_ts(v.get("ts", ""), now)
        if ts and ts >= soglia:
            series[tipo]["recent"] += 1
        author = str(v.get("author") or "?").lower()
        series[tipo]["authors"][author] = series[tipo]["authors"].get(author, 0) + 1
        status = str(v.get("instagram_status") or "")
        if status:
            published[status] = published.get(status, 0) + 1

    people_list = list((people or {}).values()) if isinstance(people, dict) else list(people or [])
    levels = {"vip": 0, "cerchia": 0, "musa": 0, "none": 0}
    active_recent = 0
    total_messages = 0
    for p in people_list:
        level = str(p.get("level") or "")
        levels[level if level in levels else "none"] += 1
        total_messages += int(p.get("messages", 0))
        last = _parse_ts(p.get("last_seen", ""), now)
        if last and last >= soglia:
            active_recent += 1

    return {
        "window_days": int(days),
        "series": series,
        "published": published,
        "vips": {
            "total_contacts": len(people_list),
            "levels": levels,
            "total_messages": total_messages,
            "active_last_n_days": active_recent,
        },
    }
