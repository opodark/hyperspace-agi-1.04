# SPDX-License-Identifier: Apache-2.0
"""Nightly, review-gated code experiments for HyperSpace."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


REVIEW_ACTIONS = {
    "approve": "approved",
    "reject": "rejected",
    "defer": "deferred",
    "reopen": "candidate",
}
ALLOWED_REVIEWS = {
    "candidate": {"approve", "reject", "defer"},
    "deferred": {"approve", "reject", "reopen"},
    "approved": {"reopen"},
    "rejected": {"reopen"},
    "failed": {"reopen", "reject"},
    "no_changes": {"reject"},
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DevelopmentDreamJournal:
    def __init__(self, directory):
        self.path = Path(directory) / "development_dreams.jsonl"
        self._lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except (OSError, ValueError):
                continue
        return rows

    def _write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("".join(_json(row) + "\n" for row in rows[-100:]), encoding="utf-8")
        temporary.replace(self.path)

    def append(self, report):
        with self._lock:
            rows = self._read()
            rows.append(report)
            self._write(rows)

    def list(self, status="", limit=50):
        with self._lock:
            rows = self._read()
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return list(reversed(rows[-max(1, min(int(limit), 100)):]))

    def review(self, dream_id, action, reviewer, rationale, timestamp=None):
        action = str(action or "").strip().lower()
        rationale = str(rationale or "").strip()[:2000]
        reviewer = str(reviewer or "operator").strip()[:120] or "operator"
        if action not in REVIEW_ACTIONS:
            raise ValueError("unknown development review action")
        if not rationale:
            raise ValueError("review rationale is required")
        with self._lock:
            rows = self._read()
            index = next((i for i, row in enumerate(rows) if row.get("id") == dream_id), None)
            if index is None:
                raise KeyError(dream_id)
            row = rows[index]
            previous = row.get("status", "candidate")
            if action not in ALLOWED_REVIEWS.get(previous, set()):
                raise ValueError(f"action {action} is not allowed from {previous}")
            created_at = datetime.fromtimestamp(timestamp or time.time(), timezone.utc).astimezone().isoformat()
            row["status"] = REVIEW_ACTIONS[action]
            row["updated_at"] = created_at
            row.setdefault("reviews", []).append({
                "action": action,
                "from_status": previous,
                "to_status": row["status"],
                "reviewer": reviewer,
                "rationale": rationale,
                "created_at": created_at,
            })
            rows[index] = row
            self._write(rows)
            return row


class NightlyDevelopmentDream:
    """Runs at most one isolated code proposal per local calendar day."""

    def __init__(self, directory, sandbox, agent, *, enabled=False, start_hour=1,
                 end_hour=5, idle_seconds=3600, test_argv=None, clock=time.time):
        self.directory = Path(directory)
        self.state_path = self.directory / "development_dream_state.json"
        self.journal = DevelopmentDreamJournal(directory)
        self.sandbox = sandbox
        self.agent = agent
        self.enabled = enabled
        self.start_hour = max(0, min(int(start_hour), 23))
        self.end_hour = max(0, min(int(end_hour), 24))
        self.idle_seconds = max(300, int(idle_seconds))
        self.test_argv = list(test_argv or ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"])
        self.clock = clock
        self.running = False
        self.error = ""
        self.state = {"last_date": "", "last_run": None, "last_status": "never"}
        try:
            self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(_json(self.state), encoding="utf-8")
        temporary.replace(self.state_path)

    def status(self):
        return {**self.state, "enabled": self.enabled, "running": self.running,
                "error": self.error, "window": [self.start_hour, self.end_hour],
                "idle_seconds": self.idle_seconds,
                "pending_review": len(self.journal.list("candidate")),
                "sandbox": self.sandbox.status()}

    def due(self, last_activity, now=None):
        now = self.clock() if now is None else now
        local = datetime.fromtimestamp(now).astimezone()
        in_window = (self.start_hour <= local.hour < self.end_hour if self.start_hour < self.end_hour
                     else local.hour >= self.start_hour or local.hour < self.end_hour)
        return (self.enabled and not self.running and in_window
                and now - last_activity >= self.idle_seconds
                and self.state.get("last_date") != local.date().isoformat())

    def run_once(self, objective=""):
        now = self.clock()
        local = datetime.fromtimestamp(now).astimezone()
        dream_id = "devdream-" + hashlib.sha256(
            f"{local.date().isoformat()}|{now}".encode()).hexdigest()[:20]
        primary_id = verifier_id = None
        self.running, self.error = True, ""
        report = {
            "schema_version": 1, "id": dream_id, "type": "development_dream",
            "status": "failed", "created_at": local.isoformat(), "reviews": [],
            "objective": objective or "Choose one small, low-risk improvement from the repository.",
        }
        try:
            primary = self.sandbox.call("create", {"label": dream_id})
            primary_id = primary["workspace_id"]
            report.update(workspace_id=primary_id, backend=primary.get("backend", "unknown"))
            prompt = (
                "You are HyperSpace's Nightly Development Dream. Work only through the code_sandbox "
                f"tool in the existing workspace {primary_id}. {report['objective']} "
                "Inspect before editing; make one cohesive change; do not touch authentication, secrets, "
                "sandbox policy, deployment, Docker/Compose, review gates, or the dream scheduler. "
                "Run relevant tests and finish by calling diff. Never apply, commit, push, deploy, or use "
                "network tools. Summarize hypothesis, files changed, tests, and remaining risks."
            )
            report["agent_summary"] = str(self.agent(prompt))[:12000]
            proposal = self.sandbox.call("diff", {"workspace_id": primary_id})
            report["changed_files"] = proposal.get("changed_files", [])
            report["diff"] = proposal.get("diff", "")
            report["diff_truncated"] = bool(proposal.get("truncated"))
            if not report["changed_files"]:
                report["status"] = "no_changes"
                return report
            if report["diff_truncated"] or "Binary files differ:" in report["diff"]:
                report["verification"] = {"ok": False, "error": "diff is truncated or binary"}
                return report

            verifier = self.sandbox.call("create", {"label": dream_id + "-verify"})
            verifier_id = verifier["workspace_id"]
            self.sandbox.call("write", {"workspace_id": verifier_id,
                              "path": ".hyperspace-development.patch", "content": report["diff"]})
            check = self.sandbox.call("run", {"workspace_id": verifier_id,
                    "argv": ["git", "apply", "--check", ".hyperspace-development.patch"], "timeout": 30})
            if not check.get("ok"):
                report["verification"] = {"ok": False, "stage": "apply-check", "result": check}
                return report
            applied = self.sandbox.call("run", {"workspace_id": verifier_id,
                      "argv": ["git", "apply", ".hyperspace-development.patch"], "timeout": 30})
            if not applied.get("ok"):
                report["verification"] = {"ok": False, "stage": "apply", "result": applied}
                return report
            tested = self.sandbox.call("run", {"workspace_id": verifier_id,
                     "argv": self.test_argv, "timeout": 120})
            report["verification"] = {"ok": bool(tested.get("ok")), "stage": "tests",
                                      "result": tested}
            report["status"] = "candidate" if tested.get("ok") else "failed"
            return report
        except Exception as error:
            self.error = str(error) or type(error).__name__
            report["error"] = self.error
            return report
        finally:
            if verifier_id:
                try:
                    self.sandbox.call("discard", {"workspace_id": verifier_id})
                except Exception:
                    pass
            if primary_id and report.get("status") != "candidate":
                try:
                    self.sandbox.call("discard", {"workspace_id": primary_id})
                    report["workspace_discarded"] = True
                except Exception:
                    report["workspace_discarded"] = False
            report["completed_at"] = datetime.fromtimestamp(self.clock()).astimezone().isoformat()
            self.journal.append(report)
            self.state.update(last_date=local.date().isoformat(), last_run=report["completed_at"],
                              last_status=report["status"])
            self._save()
            self.running = False
