# SPDX-License-Identifier: Apache-2.0
"""D3 evaluation metrics for the Dream subsystem.

Measures a set of Dream records against the criteria in ``docs/dreams.md``:
utility, novelty, traceability, correctness, cost and operational impact.
Metrics that need human annotation or runtime telemetry are reported with their
coverage, so the result never pretends to measure what the data cannot support.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Token-overlap threshold above which a dream summary is considered a duplicate
# of an existing memory for the novelty metric (Jaccard over whitespace tokens).
NOVELTY_OVERLAP_THRESHOLD = 0.8


def _tokens(text: str) -> set:
    return {token for token in (text or "").lower().split() if token}


def _overlap(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _metric(value: Optional[float], coverage: float,
            numerator: Optional[float] = None, denominator: Optional[float] = None,
            note: str = "") -> Dict[str, Any]:
    return {
        "value": value,
        "coverage": round(float(coverage), 4),
        "numerator": numerator,
        "denominator": denominator,
        "note": note,
    }


def read_jsonl(path) -> List[Dict[str, Any]]:
    """Read a JSONL file, skipping malformed lines (same contract as the journal)."""
    if not path or not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except (ValueError, OSError):
            continue
    return rows


def load_dataset(directory) -> Dict[str, List[Dict[str, Any]]]:
    """Read ``dreams.jsonl``, ``dream_insights.jsonl`` and ``memories.jsonl``."""
    base = Path(directory)
    return {
        "dreams": read_jsonl(base / "dreams.jsonl"),
        "insights": read_jsonl(base / "dream_insights.jsonl"),
        "memories": read_jsonl(base / "memories.jsonl"),
    }


def _status_counts(dreams: List[Dict[str, Any]]):
    counts = {"hypothesis": 0, "deferred": 0, "promoted": 0, "rejected": 0, "revoked": 0}
    reviewed = 0
    for dream in dreams:
        status = str(dream.get("status") or "hypothesis")
        if status in counts:
            counts[status] += 1
        if dream.get("reviews"):
            reviewed += 1
    return counts, reviewed


def evaluate_dreams(dreams, insights=None, memories=None) -> Dict[str, Any]:
    """Compute D3 metrics over Dream records.

    ``dreams`` are the hypothesis records (``dreams.jsonl``), ``insights`` the
    promoted notes (``dream_insights.jsonl``) and ``memories`` the existing
    memory corpus used to measure novelty.
    """
    dreams = list(dreams or [])
    insights = list(insights or [])
    memories = list(memories or [])
    total = len(dreams)
    counts, reviewed = _status_counts(dreams)
    promoted = counts["promoted"]

    # Utility: share of hypotheses promoted (or annotated useful) over the total.
    useful_annotated = sum(
        1 for dream in dreams
        if dream.get("status") != "promoted"
        and any((review.get("quality") == "useful") for review in (dream.get("reviews") or []))
    )
    utility_numerator = promoted + useful_annotated
    utility = _metric(
        utility_numerator / total if total else None,
        reviewed / total if total else 0.0,
        utility_numerator, total,
        "share of hypotheses promoted or annotated useful; coverage is the share that was reviewed",
    )

    # Traceability: share with at least one cited source and a non-empty summary.
    traceable = sum(
        1 for dream in dreams
        if (dream.get("source_refs") or dream.get("source_memory_ids"))
        and str(dream.get("summary") or dream.get("response") or "").strip()
    )
    traceability = _metric(
        traceable / total if total else None,
        1.0,
        traceable, total,
        "share of hypotheses with cited source memories",
    )

    # Novelty: share not equivalent to an existing memory (token-overlap proxy).
    if memories:
        novel = 0
        for dream in dreams:
            summary = str(dream.get("summary") or dream.get("response") or "")
            if not _tokens(summary):
                continue
            duplicate = any(
                _overlap(summary, str(memory.get("content") or memory.get("summary") or ""))
                >= NOVELTY_OVERLAP_THRESHOLD
                for memory in memories
            )
            if not duplicate:
                novel += 1
        novelty = _metric(
            novel / total if total else None, 1.0, novel, total,
            f"share not near-duplicate of an existing memory (Jaccard >= {NOVELTY_OVERLAP_THRESHOLD})",
        )
    else:
        novelty = _metric(
            None, 0.0, None, None,
            "no memory corpus supplied; provide memories.jsonl to measure novelty",
        )

    # Correctness: needs reviewer annotation (review.quality in correct/incorrect).
    annotated = [
        dream for dream in dreams
        if any(review.get("quality") in {"correct", "incorrect"} for review in (dream.get("reviews") or []))
    ]
    if annotated:
        correct = sum(
            1 for dream in annotated
            if any(review.get("quality") == "correct" for review in (dream.get("reviews") or []))
        )
        correctness = _metric(
            correct / len(annotated), len(annotated) / total if total else 0.0,
            correct, len(annotated),
            "share of annotated reviews judged correct; annotate reviews with quality: correct|incorrect",
        )
    else:
        correctness = _metric(
            None, 0.0, None, None,
            "no review.quality annotations yet; annotate reviews to measure correctness",
        )

    # Cost: needs duration_ms / tokens instrumentation on dream records.
    timed = [dream for dream in dreams if dream.get("duration_ms") is not None]
    if timed:
        total_ms = sum(int(dream.get("duration_ms") or 0) for dream in timed)
        useful = max(promoted, 1)
        cost = _metric(
            total_ms / useful, len(timed) / total if total else 0.0,
            total_ms, useful,
            "average generation ms per promoted hypothesis",
        )
    else:
        cost = _metric(
            None, 0.0, None, None,
            "no duration_ms instrumentation; add timing to the Dream worker to measure cost",
        )

    # Operational impact: requires foreground-interruption telemetry.
    impact = _metric(
        None, 0.0, None, None,
        "not derivable from dream records; requires foreground-interruption latency telemetry",
    )

    evaluated_on_real_memories = bool(memories)
    manual_false_positive_review = reviewed >= 1 and bool(annotated)
    return {
        "schema_version": 1,
        "dataset": {
            "dreams": total,
            "reviewed": reviewed,
            "insights": len(insights),
            "memories": len(memories),
            **counts,
        },
        "metrics": {
            "utility": utility,
            "traceability": traceability,
            "novelty": novelty,
            "correctness": correctness,
            "cost": cost,
            "impact": impact,
        },
        "ready_for_d4": {
            "evaluated_on_real_memories": evaluated_on_real_memories,
            "manual_false_positive_review": manual_false_positive_review,
            "ready": evaluated_on_real_memories and manual_false_positive_review,
            "note": "D4 unlocks after an evaluation session on real memories and a manual review of false positives (docs/dreams.md)",
        },
    }
