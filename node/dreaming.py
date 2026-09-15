"""Idle-only reflections and their manual review lifecycle.

Dreams are untrusted hypotheses. Only an explicit review can promote one to a
derived insight, and every decision remains attributable and reversible.
"""
import asyncio
import hashlib
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
PROMPT_VERSION = "dream-json-v1"
KINDS = {"connection", "contradiction", "summary", "open_question"}
ACTIONS = {
    "promote": "promoted",
    "reject": "rejected",
    "defer": "deferred",
    "reopen": "hypothesis",
    "revoke": "revoked",
}
ALLOWED_ACTIONS = {
    "hypothesis": {"promote", "reject", "defer"},
    "deferred": {"promote", "reject", "reopen"},
    "promoted": {"revoke"},
    "rejected": {"reopen"},
    "revoked": {"promote", "reject", "reopen"},
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _iso_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def source_memories(entries, node_id):
    return [e for e in entries if e.get("node_id") == node_id
            and not e.get("_received_from") and e.get("type") != "dream"
            and not str(e.get("task_id", "")).startswith(("dream-", "title-"))
            and (e.get("prompt") or e.get("response") or e.get("content"))][-12:]


def source_reference(entry):
    digest = hashlib.sha256(_canonical(entry).encode()).hexdigest()
    memory_id = next((str(entry[key]) for key in
                      ("memory_id", "id", "interaction_id", "task_id")
                      if entry.get(key)), f"memory-{digest[:20]}")
    return {
        "id": memory_id,
        "sha256": digest,
        "type": str(entry.get("type", "memory")),
        "timestamp": entry.get("timestamp", entry.get("ts")),
    }


def parse_reflection(text):
    """Parse constrained model output, retaining a structured safe fallback."""
    raw = (text or "").strip()
    candidate = raw
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        payload = json.loads(candidate)
        if not isinstance(payload, dict) or not str(payload.get("summary", "")).strip():
            raise ValueError("missing summary")
        kind = payload.get("kind") if payload.get("kind") in KINDS else "open_question"
        confidence = payload.get("confidence")
        if confidence is not None:
            confidence = float(confidence)
            if not math.isfinite(confidence):
                raise ValueError("confidence must be finite")
            confidence = max(0.0, min(1.0, confidence))
        questions = payload.get("open_questions", [])
        if not isinstance(questions, list):
            questions = []
        return {
            "kind": kind,
            "summary": str(payload["summary"]).strip()[:2000],
            "confidence": confidence,
            "open_questions": [str(q).strip()[:500] for q in questions if str(q).strip()][:3],
            "format_error": "",
        }
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return {
            "kind": "open_question",
            "summary": raw[:2000],
            "confidence": None,
            "open_questions": [],
            "format_error": f"Unstructured model output: {error}",
        }


class DreamJournal:
    def __init__(self, directory):
        base = Path(directory)
        self.path = base / "dreams.jsonl"
        self.insights_path = base / "dream_insights.jsonl"
        self._lock = threading.RLock()

    @staticmethod
    def _read(path):
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except (ValueError, OSError):
                continue
        return rows

    @staticmethod
    def _write(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text("".join(_canonical(row) + "\n" for row in rows), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _normalize_dream(row):
        """Make pre-D1 text-only records reviewable without destructive migration."""
        normalized = dict(row)
        if not normalized.get("id"):
            seed = "|".join(str(normalized.get(key, "")) for key in
                            ("node_id", "fingerprint", "timestamp", "response"))
            normalized["id"] = "dream-legacy-" + hashlib.sha256(seed.encode()).hexdigest()[:20]
        normalized.setdefault("schema_version", SCHEMA_VERSION)
        normalized.setdefault("prompt_version", "legacy-free-text")
        normalized.setdefault("status", "hypothesis")
        normalized.setdefault("kind", "open_question")
        normalized.setdefault("summary", normalized.get("response", ""))
        normalized.setdefault("confidence", None)
        normalized.setdefault("open_questions", [])
        normalized.setdefault("source_memory_ids", [])
        normalized.setdefault("source_refs", [])
        normalized.setdefault("reviews", [])
        return normalized

    def append(self, report):
        with self._lock:
            rows = self._read(self.path)
            rows.append(report)
            self._write(self.path, rows[-100:])

    def list(self, status="", limit=100):
        with self._lock:
            rows = [self._normalize_dream(row) for row in self._read(self.path)]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return list(reversed(rows[-max(1, min(int(limit), 100)):]))

    def insights(self, active_only=True):
        with self._lock:
            rows = self._read(self.insights_path)
        if active_only:
            rows = [row for row in rows if row.get("status") == "active"]
        return list(reversed(rows))

    def review(self, dream_id, action, reviewer, rationale, timestamp=None):
        action = str(action or "").strip().lower()
        reviewer = str(reviewer or "operator").strip()[:120] or "operator"
        rationale = str(rationale or "").strip()[:2000]
        if action not in ACTIONS:
            raise ValueError("Unknown review action")
        if not rationale:
            raise ValueError("Review rationale is required")
        now = time.time() if timestamp is None else timestamp
        with self._lock:
            rows = [self._normalize_dream(row) for row in self._read(self.path)]
            index = next((i for i, row in enumerate(rows) if row.get("id") == dream_id), None)
            if index is None:
                raise KeyError(dream_id)
            dream = rows[index]
            old_status = dream.get("status", "hypothesis")
            if action not in ALLOWED_ACTIONS.get(old_status, set()):
                raise ValueError(f"Action {action} is not allowed from {old_status}")
            review = {
                "action": action,
                "from_status": old_status,
                "to_status": ACTIONS[action],
                "reviewer": reviewer,
                "rationale": rationale,
                "created_at": _iso_timestamp(now),
            }
            dream["status"] = ACTIONS[action]
            dream["updated_at"] = review["created_at"]
            dream.setdefault("reviews", []).append(review)
            rows[index] = dream
            self._write(self.path, rows)

            insights = self._read(self.insights_path)
            insight_id = f"insight-{dream_id}"
            insight = next((row for row in insights if row.get("id") == insight_id), None)
            if action == "promote":
                promoted = {
                    "schema_version": SCHEMA_VERSION,
                    "id": insight_id,
                    "type": "dream_insight",
                    "status": "active",
                    "node_id": dream.get("node_id"),
                    "content": dream.get("summary", dream.get("response", "")),
                    "dream_id": dream_id,
                    "source_memory_ids": dream.get("source_memory_ids", []),
                    "source_refs": dream.get("source_refs", []),
                    "confidence": dream.get("confidence"),
                    "review": review,
                    "created_at": review["created_at"],
                }
                if insight:
                    insights[insights.index(insight)] = promoted
                else:
                    insights.append(promoted)
                insight = promoted
            elif insight:
                insight["status"] = "revoked"
                insight["review"] = review
            if insight is not None:
                self._write(self.insights_path, insights)
            return dream, insight


class DreamWorker:
    def __init__(self, directory, node_id, model, read_memory, idle, acquire,
                 release, generate, publish, enabled=False, idle_seconds=120,
                 interval=900, clock=time.time, journal=None):
        self.path = Path(directory) / "dream_state.json"
        self.journal = journal or DreamJournal(directory)
        self.output = self.journal.path
        self.node_id, self.model = node_id, model
        self.read_memory, self.idle, self.acquire = read_memory, idle, acquire
        self.release, self.generate, self.publish = release, generate, publish
        self.enabled, self.idle_seconds, self.interval = enabled, idle_seconds, interval
        self.clock = clock
        self.idle_since = clock()
        self.generation = None
        self.state = {"last_attempt": 0, "fingerprint": "", "last_dream": None, "error": ""}
        if self.path.exists():
            try:
                self.state.update(json.loads(self.path.read_text()))
            except (ValueError, OSError):
                pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def interrupt(self):
        self.idle_since = self.clock()
        if self.generation and not self.generation.done():
            self.generation.cancel()

    def status(self):
        return {**self.state, "enabled": self.enabled, "running": bool(self.generation),
                "model": self.model, "node_id": self.node_id,
                "idle_seconds": self.idle_seconds, "interval_seconds": self.interval,
                "pending_review": len(self.journal.list("hypothesis"))}

    async def tick(self):
        now = self.clock()
        if not self.enabled or not self.model:
            return
        if not self.idle():
            self.idle_since = now
            return
        if now - self.idle_since < self.idle_seconds or now - self.state["last_attempt"] < self.interval:
            return
        entries = source_memories(self.read_memory(100), self.node_id)
        if not entries:
            return
        text = json.dumps(entries, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        if fingerprint == self.state["fingerprint"]:
            return
        if not await self.acquire(self.model):
            self.idle_since = now
            return
        try:
            self.state.update(last_attempt=now, error="")
            self.save()
            self.generation = asyncio.create_task(self.generate(text[:10000], self.model))
            reflection = await asyncio.wait_for(self.generation, timeout=60)
            if not reflection.strip():
                raise ValueError("Empty reflection")
            structured = parse_reflection(reflection)
            refs = [source_reference(entry) for entry in entries]
            dream_id = "dream-" + hashlib.sha256(
                f"{self.node_id}|{fingerprint}|{reflection}".encode()
            ).hexdigest()[:24]
            report = {
                "schema_version": SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "id": dream_id,
                "type": "dream",
                "status": "hypothesis",
                "node_id": self.node_id,
                "created_at": _iso_timestamp(now),
                "timestamp": now,
                "model": self.model,
                "fingerprint": fingerprint,
                "source_count": len(entries),
                "source_memory_ids": [ref["id"] for ref in refs],
                "source_refs": refs,
                "response": reflection,
                "reviews": [],
                **structured,
            }
            self.journal.append(report)
            self.state.update(fingerprint=fingerprint, last_dream=now)
            self.save()
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            self.state["error"] = "Interrupted by foreground request"
            self.save()
            return
        except Exception as error:
            self.state["error"] = str(error) or type(error).__name__
            self.save()
            return
        finally:
            self.generation = None
            await self.release(self.model)
            self.idle_since = self.clock()
        try:
            await self.publish(report)
        except Exception as error:
            self.state["error"] = "Saved locally; log delivery failed: " + str(error)
            self.save()

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.state["error"] = str(error)
            await asyncio.sleep(10)
