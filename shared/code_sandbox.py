"""File-queue client for the offline code sandbox runner."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class SandboxUnavailable(RuntimeError):
    pass


class CodeSandboxClient:
    def __init__(self, exchange_dir=None, enabled=None):
        self.exchange = Path(exchange_dir or os.getenv("SANDBOX_EXCHANGE_DIR", "/sandbox-exchange"))
        self.enabled = (os.getenv("CODE_SANDBOX_ENABLED", "false").lower() == "true"
                        if enabled is None else bool(enabled))
        self.default_timeout = max(1, int(os.getenv("SANDBOX_JOB_TIMEOUT", "140")))

    def status(self):
        heartbeat = self.exchange / "heartbeat"
        age = None
        if heartbeat.exists():
            try:
                age = max(0.0, time.time() - float(heartbeat.read_text(encoding="ascii")))
            except (OSError, ValueError):
                pass
        return {"enabled": self.enabled, "available": bool(age is not None and age < 5),
                "heartbeat_age_s": round(age, 3) if age is not None else None}

    def call(self, action, arguments=None, timeout=None):
        if not self.enabled:
            raise SandboxUnavailable("code sandbox is disabled")
        status = self.status()
        if not status["available"]:
            raise SandboxUnavailable("code sandbox runner is unavailable")
        arguments = dict(arguments or {})
        job_id = str(uuid.uuid4())
        job = {**arguments, "job_id": job_id, "action": action}
        jobs = self.exchange / "jobs"
        results = self.exchange / "results"
        jobs.mkdir(parents=True, exist_ok=True)
        results.mkdir(parents=True, exist_ok=True)
        destination = jobs / f"{job_id}.json"
        temporary = jobs / f".{job_id}.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)
        result_path = results / f"{job_id}.json"
        deadline = time.monotonic() + (timeout or self.default_timeout)
        while time.monotonic() < deadline:
            if result_path.exists():
                try:
                    return json.loads(result_path.read_text(encoding="utf-8"))
                finally:
                    result_path.unlink(missing_ok=True)
            time.sleep(0.05)
        destination.unlink(missing_ok=True)
        raise TimeoutError(f"sandbox job timed out: {action}")
