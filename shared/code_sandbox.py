# SPDX-License-Identifier: Apache-2.0
"""File-queue client for the offline code sandbox runner."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import requests


class SandboxUnavailable(RuntimeError):
    pass


class CodeSandboxClient:
    def __init__(self, exchange_dir=None, enabled=None):
        self.exchange = Path(exchange_dir or os.getenv("SANDBOX_EXCHANGE_DIR", "/sandbox-exchange"))
        self.enabled = (os.getenv("CODE_SANDBOX_ENABLED", "false").lower() == "true"
                        if enabled is None else bool(enabled))
        self.default_timeout = max(1, int(os.getenv("SANDBOX_JOB_TIMEOUT", "240")))

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


class SbxSandboxClient:
    """Client for Docker Sandboxes through the native, tokenized host agent."""

    def __init__(self, url=None, token=None, enabled=None):
        self.url = (url or os.getenv("HOSTCTL_URL", "http://host.docker.internal:8765")).rstrip("/")
        self.token = token if token is not None else os.getenv("HOSTCTL_TOKEN", "")
        self.enabled = (os.getenv("SBX_SANDBOX_ENABLED", "true").lower() == "true"
                        if enabled is None else bool(enabled))
        self.default_timeout = max(1, int(os.getenv("SANDBOX_JOB_TIMEOUT", "240")))

    def _request(self, payload, timeout=8):
        if not self.enabled or len(self.token) < 32:
            raise SandboxUnavailable("Docker Sandboxes host backend is not configured")
        try:
            response = requests.post(
                f"{self.url}/action",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"action": "sbx_sandbox", **payload},
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            raise SandboxUnavailable(f"Docker Sandboxes host backend unavailable: {error}") from error
        return result

    def status(self):
        if not self.enabled or len(self.token) < 32:
            return {"enabled": self.enabled, "available": False, "backend": "sbx",
                    "configured": False}
        try:
            result = self._request({"operation": "status"}, timeout=5)
            return {**result, "enabled": True, "available": bool(result.get("ok") and result.get("ready")),
                    "backend": "sbx", "configured": True}
        except SandboxUnavailable as error:
            return {"enabled": True, "available": False, "backend": "sbx",
                    "configured": True, "error": str(error)}

    def call(self, action, arguments=None, timeout=None):
        result = self._request({"operation": action, **dict(arguments or {})},
                               timeout=timeout or self.default_timeout)
        if not result.get("ok"):
            raise SandboxUnavailable(result.get("error", f"sbx {action} failed"))
        return result


class HybridCodeSandboxClient:
    """Prefer Docker Sandboxes microVMs and fall back to the offline container."""

    def __init__(self, primary=None, fallback=None, enabled=None):
        self.primary = primary or SbxSandboxClient()
        self.fallback = fallback or CodeSandboxClient()
        self.enabled = ((os.getenv("CODE_SANDBOX_ENABLED", "false").lower() == "true")
                        if enabled is None else bool(enabled))
        self.default_timeout = max(self.primary.default_timeout, self.fallback.default_timeout)

    def status(self):
        primary = self.primary.status()
        fallback = self.fallback.status()
        selected = "sbx" if primary.get("available") else ("docker" if fallback.get("available") else None)
        return {"enabled": self.enabled, "available": bool(self.enabled and selected),
                "backend": selected, "preferred": "sbx", "primary": primary, "fallback": fallback}

    @staticmethod
    def _split_workspace(value):
        value = str(value or "")
        if value.startswith("sbx:"):
            return "sbx", value[4:]
        if value.startswith("docker:"):
            return "docker", value[7:]
        return "docker", value

    @staticmethod
    def _tag(result, backend):
        result = dict(result)
        if result.get("workspace_id"):
            result["workspace_id"] = f"{backend}:{result['workspace_id']}"
        result["backend"] = backend
        return result

    def call(self, action, arguments=None, timeout=None):
        if not self.enabled:
            raise SandboxUnavailable("code sandbox is disabled")
        arguments = dict(arguments or {})
        if action == "status":
            return self.status()
        if action == "create":
            if self.primary.status().get("available"):
                try:
                    return self._tag(self.primary.call(action, arguments, timeout), "sbx")
                except SandboxUnavailable:
                    pass
            return self._tag(self.fallback.call(action, arguments, timeout), "docker")
        backend, raw_id = self._split_workspace(arguments.get("workspace_id"))
        arguments["workspace_id"] = raw_id
        client = self.primary if backend == "sbx" else self.fallback
        return self._tag(client.call(action, arguments, timeout), backend)
