# SPDX-License-Identifier: Apache-2.0
import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("dreaming", Path(__file__).parents[1] / "node/dreaming.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class DreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.busy = False
        self.released = 0
        self.published = []
        self.generated = 0
        self.entries = [{"node_id": "node", "task_id": "task1", "prompt": "hello", "response": "world"}]
        async def acquire(model): return not self.busy
        async def release(model): self.released += 1
        async def generate(text, model):
            self.generated += 1
            return "IPOTESI: connection"
        async def publish(report): self.published.append(report)
        self.worker = mod.DreamWorker(self.temp.name, "node", "model", lambda n:self.entries,
            lambda:not self.busy, acquire, release, generate, publish, enabled=True,
            idle_seconds=30, interval=60, clock=lambda:self.now)

    async def test_idle_cooldown_and_no_repeat(self):
        await self.worker.tick()
        self.assertEqual(self.generated, 0)
        self.now += 31
        await self.worker.tick()
        self.assertEqual(self.generated, 1)
        self.assertEqual(self.released, 1)
        self.assertEqual(self.published[0]["status"], "hypothesis")
        self.assertEqual(self.published[0]["schema_version"], 1)
        self.assertEqual(self.published[0]["source_memory_ids"], ["task1"])
        self.assertTrue(self.published[0]["id"].startswith("dream-"))
        self.assertFalse((Path(self.temp.name)/"memory.jsonl").exists())
        self.now += 100
        await self.worker.tick()
        self.assertEqual(self.generated, 1)
        self.entries.append({"node_id":"node", "content":"new evidence"})
        await self.worker.tick()
        self.assertEqual(self.generated, 2)

    async def test_busy_and_empty_memory_skip(self):
        self.now += 31
        self.busy = True
        await self.worker.tick()
        self.busy = False
        await self.worker.tick()
        self.assertEqual(self.generated, 0)
        self.now += 31
        self.entries = []
        await self.worker.tick()
        self.assertEqual(self.generated, 0)

    async def test_foreground_interrupt_releases_slot_without_publishing(self):
        started = asyncio.Event()
        async def wait(text, model):
            started.set()
            await asyncio.sleep(100)
        self.worker.generate = wait
        self.now += 31
        task = asyncio.create_task(self.worker.tick())
        await started.wait()
        self.worker.interrupt()
        await task
        self.assertEqual(self.released, 1)
        self.assertEqual(self.published, [])
        self.assertIn("Interrupted", self.worker.state["error"])

    async def test_generation_failure_releases_slot_and_backs_off(self):
        async def fail(text, model): raise ValueError("backend down")
        self.worker.generate = fail
        self.now += 31
        await self.worker.tick()
        self.assertEqual(self.released, 1)
        self.assertEqual(self.worker.state["error"], "backend down")
        self.now += 31
        await self.worker.tick()
        self.assertEqual(self.released, 1)

    async def test_restart_restores_fingerprint(self):
        self.now += 31
        await self.worker.tick()
        old = self.worker
        restored = mod.DreamWorker(self.temp.name, "node", "model", old.read_memory,
            old.idle, old.acquire, old.release, old.generate, old.publish,
            enabled=True, idle_seconds=30, interval=60, clock=lambda:self.now)
        self.now += 100
        await restored.tick()
        self.assertEqual(self.generated, 1)

    async def test_real_limiter_idle_acquisition(self):
        import ast
        source = (Path(__file__).parents[1]/"node/main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_AdaptiveLoadLimiter")
        scope = {"asyncio":asyncio, "LOAD_SEED_CONCURRENCY":4, "LOAD_MIN_CONCURRENCY":1,
            "LOAD_MAX_CONCURRENCY":16, "LOAD_INCREASE_STEP":1, "LOAD_SHRINK_FACTOR":0.5}
        exec(compile(ast.Module(body=[klass], type_ignores=[]), "limiter", "exec"), scope)
        limiter=scope["_AdaptiveLoadLimiter"]("model_manager")
        self.assertTrue(await limiter.acquire_idle("model"))
        self.assertFalse(await limiter.acquire_idle("model"))
        await limiter.release("model")
        limiter._queued = 1
        self.assertFalse(await limiter.acquire_idle("model"))
        limiter._queued = 0
        limiter.degraded = True
        self.assertFalse(await limiter.acquire_idle("model"))

    async def test_source_isolation(self):
        entries = self.entries + [{"node_id":"other", "content":"private"},
            {"node_id":"node", "content":"hypothesis", "type":"dream"},
            {"node_id":"node", "content":"imported", "_received_from":"other"},
            {"node_id":"node", "task_id":"title-abc", "content":"title"}]
        self.assertEqual(mod.source_memories(entries, "node"), self.entries)


class StructuredDreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = mod.DreamJournal(self.temp.name)
        self.dream = {
            "schema_version": 1,
            "id": "dream-1",
            "type": "dream",
            "status": "hypothesis",
            "node_id": "node",
            "summary": "Due eventi potrebbero essere collegati.",
            "kind": "connection",
            "confidence": 0.6,
            "source_memory_ids": ["memory-1", "memory-2"],
            "source_refs": [{"id": "memory-1"}, {"id": "memory-2"}],
            "reviews": [],
        }
        self.journal.append(self.dream)

    def test_parses_json_and_clamps_self_reported_confidence(self):
        parsed = mod.parse_reflection(
            '{"kind":"connection","summary":"legame",'
            '"confidence":1.5,"open_questions":["perché?"]}'
        )
        self.assertEqual(parsed["kind"], "connection")
        self.assertEqual(parsed["confidence"], 1.0)
        self.assertEqual(parsed["open_questions"], ["perché?"])
        self.assertEqual(parsed["format_error"], "")

    def test_unstructured_output_remains_a_reviewable_hypothesis(self):
        parsed = mod.parse_reflection("IPOTESI: forse esiste un legame")
        self.assertEqual(parsed["kind"], "open_question")
        self.assertIsNone(parsed["confidence"])
        self.assertIn("Unstructured", parsed["format_error"])

    def test_non_finite_confidence_uses_safe_fallback(self):
        parsed = mod.parse_reflection(
            '{"kind":"summary","summary":"x","confidence":"NaN"}'
        )
        self.assertIsNone(parsed["confidence"])
        self.assertIn("finite", parsed["format_error"])

    def test_review_requires_rationale_and_valid_transition(self):
        with self.assertRaises(ValueError):
            self.journal.review("dream-1", "promote", "alice", "")
        with self.assertRaises(ValueError):
            self.journal.review("dream-1", "reopen", "alice", "non ancora")

    def test_promote_and_revoke_are_attributed_and_reversible(self):
        promoted, insight = self.journal.review(
            "dream-1", "promote", "alice", "Fonti sufficienti", timestamp=1000,
        )
        self.assertEqual(promoted["status"], "promoted")
        self.assertEqual(promoted["reviews"][0]["reviewer"], "alice")
        self.assertEqual(insight["status"], "active")
        self.assertEqual(insight["source_memory_ids"], ["memory-1", "memory-2"])
        self.assertEqual(self.journal.insights(), [insight])

        revoked, revoked_insight = self.journal.review(
            "dream-1", "revoke", "alice", "Nuova evidenza contraria", timestamp=1010,
        )
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(revoked_insight["status"], "revoked")
        self.assertEqual(self.journal.insights(), [])
        self.assertEqual(len(self.journal.insights(active_only=False)), 1)

    def test_reject_defer_and_reopen_preserve_history(self):
        deferred, _ = self.journal.review(
            "dream-1", "defer", "bob", "Servono altre fonti", timestamp=1000,
        )
        self.assertEqual(deferred["status"], "deferred")
        reopened, _ = self.journal.review(
            "dream-1", "reopen", "bob", "Fonti aggiunte", timestamp=1010,
        )
        self.assertEqual(reopened["status"], "hypothesis")
        self.assertEqual(len(reopened["reviews"]), 2)

    def test_legacy_free_text_dreams_receive_stable_reviewable_ids(self):
        legacy = {"type": "dream", "node_id": "node", "timestamp": 50,
                  "status": "hypothesis", "response": "vecchia ipotesi"}
        self.journal._write(self.journal.path, [legacy])
        first = self.journal.list()[0]
        second = self.journal.list()[0]
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["id"].startswith("dream-legacy-"))
        reviewed, _ = self.journal.review(
            first["id"], "reject", "alice", "non supportata", timestamp=100,
        )
        self.assertEqual(reviewed["status"], "rejected")

if __name__ == "__main__": unittest.main()
