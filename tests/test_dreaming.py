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
        source = (Path(__file__).parents[1]/"node/main.py").read_text()
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

if __name__ == "__main__": unittest.main()
