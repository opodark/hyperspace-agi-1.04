"""Idle-only reflections. Outputs are hypotheses, never source memories."""
import asyncio
import hashlib
import json
import time
from pathlib import Path


def source_memories(entries, node_id):
    return [e for e in entries if e.get("node_id") == node_id
            and not e.get("_received_from") and e.get("type") != "dream"
            and not str(e.get("task_id", "")).startswith(("dream-", "title-"))
            and (e.get("prompt") or e.get("response") or e.get("content"))][-12:]


class DreamWorker:
    def __init__(self, directory, node_id, model, read_memory, idle, acquire,
                 release, generate, publish, enabled=False, idle_seconds=120,
                 interval=900, clock=time.time):
        self.path = Path(directory) / "dream_state.json"
        self.output = Path(directory) / "dreams.jsonl"
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
                "idle_seconds": self.idle_seconds, "interval_seconds": self.interval}

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
            report = {"type": "dream", "status": "hypothesis", "node_id": self.node_id,
                      "timestamp": now, "model": self.model, "fingerprint": fingerprint,
                      "source_count": len(entries), "response": reflection}
            existing = self.output.read_text(encoding="utf-8").splitlines() if self.output.exists() else []
            temporary = self.output.with_suffix(".tmp")
            temporary.write_text("\n".join(existing[-99:] + [json.dumps(report, ensure_ascii=False)]) + "\n", encoding="utf-8")
            temporary.replace(self.output)
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
