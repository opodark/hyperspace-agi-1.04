import threading
import time
import unittest
from pathlib import Path

from shared.web_node import (
    STALE_NODE_S, WEB_SAFE_TASK_TYPES, WebNodeRegistry, WebNodeUnknown, WebTaskRejected,
)

SOURCE = Path(__file__).parents[1] / "shared" / "web_node.py"


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.registry = WebNodeRegistry()
        self.node = "web-test"

    def register(self, **kwargs):
        params = {"capabilities": ["summarize", "validate_json"], "label": "test",
                  "browser": "Test/1.0"}
        params.update(kwargs)
        return self.registry.register(self.node, **params)


class RegistrationTests(RegistryTestCase):
    def test_declared_capabilities_are_intersected_with_web_safe_whitelist(self):
        record = self.register(capabilities=["summarize", "chat", "full_inference"])
        self.assertEqual(record["capabilities"], ["summarize"])
        self.assertEqual(record["rejected_capabilities"], ["chat", "full_inference"])

    def test_never_accepts_a_capability_outside_the_whitelist(self):
        record = self.register(capabilities=["anything_else"])
        self.assertEqual(record["capabilities"], [])
        self.assertTrue(set(record["capabilities"]) <= WEB_SAFE_TASK_TYPES)

    def test_reregistration_keeps_counters_and_refreshes_last_seen(self):
        self.registry.register(self.node, capabilities=["summarize"], now=100.0)
        task = self.registry.enqueue(self.node, "summarize", {"text": "x"}, now=100.0)
        self.registry.poll(self.node, timeout_s=0, now=100.0)
        self.registry.complete(self.node, task["task_id"], ok=True, now=100.0)
        again = self.registry.register(self.node, capabilities=["summarize"], now=105.0)
        self.assertEqual(again["tasks_done"], 1)
        self.assertEqual(again["registered_at"], 100.0)
        self.assertEqual(again["last_seen"], 105.0)

    def test_node_limit_is_enforced(self):
        registry = WebNodeRegistry(max_nodes=1)
        registry.register("a", capabilities=["summarize"])
        with self.assertRaises(WebTaskRejected):
            registry.register("b", capabilities=["summarize"])

    def test_heartbeat_requires_a_known_node(self):
        self.register()
        self.assertEqual(self.registry.heartbeat(self.node)["node_id"], self.node)
        with self.assertRaises(WebNodeUnknown):
            self.registry.heartbeat("ghost")

    def test_payload_ceiling_is_clamped_to_the_registry_limit(self):
        registry = WebNodeRegistry(max_payload_bytes=100)
        record = registry.register("a", capabilities=["summarize"],
                                   limits={"max_payload_bytes": 10_000})
        self.assertEqual(record["limits"]["max_payload_bytes"], 100)


class EnqueueTests(RegistryTestCase):
    def test_rejects_a_task_type_that_is_not_web_safe(self):
        self.register()
        with self.assertRaises(WebTaskRejected) as ctx:
            self.registry.enqueue(self.node, "chat", {"messages": []})
        self.assertIn("non web-safe", str(ctx.exception))

    def test_rejects_a_capability_the_node_did_not_declare(self):
        self.register(capabilities=["summarize"])
        with self.assertRaises(WebTaskRejected) as ctx:
            self.registry.enqueue(self.node, "translate", {"text": "x"})
        self.assertIn("capability", str(ctx.exception))

    def test_rejects_an_unknown_node(self):
        with self.assertRaises(WebNodeUnknown):
            self.registry.enqueue("ghost", "summarize", {"text": "x"})

    def test_rejects_an_oversized_payload(self):
        registry = WebNodeRegistry(max_payload_bytes=64)
        registry.register("a", capabilities=["summarize"])
        with self.assertRaises(WebTaskRejected) as ctx:
            registry.enqueue("a", "summarize", {"text": "x" * 500})
        self.assertIn("payload troppo grande", str(ctx.exception))

    def test_rejects_when_the_node_queue_is_full(self):
        registry = WebNodeRegistry(max_queue=1)
        registry.register("a", capabilities=["summarize"])
        registry.enqueue("a", "summarize", {"text": "1"})
        with self.assertRaises(WebTaskRejected) as ctx:
            registry.enqueue("a", "summarize", {"text": "2"})
        self.assertIn("coda piena", str(ctx.exception))

    def test_constraints_are_clamped(self):
        self.register()
        task = self.registry.enqueue(self.node, "summarize", {"text": "x"},
                                     constraints={"timeout_ms": 10_000_000, "max_tokens": -5})
        self.assertEqual(task["constraints"]["timeout_ms"], 120_000)
        self.assertEqual(task["constraints"]["max_tokens"], 0)

class PollAndCompleteTests(RegistryTestCase):
    def test_poll_returns_none_immediately_when_idle(self):
        self.register()
        started = time.monotonic()
        self.assertIsNone(self.registry.poll(self.node, timeout_s=0))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_poll_returns_the_queued_task_and_holds_it_inflight(self):
        self.register()
        task = self.registry.enqueue(self.node, "summarize", {"text": "ciao"})
        delivered = self.registry.poll(self.node, timeout_s=0)
        self.assertEqual(delivered["task_id"], task["task_id"])
        self.assertEqual(delivered["type"], "summarize")
        self.assertEqual(delivered["payload"], {"text": "ciao"})

    def test_poll_does_not_deliver_a_second_task_while_one_is_inflight(self):
        self.register()
        self.registry.enqueue(self.node, "summarize", {"text": "1"})
        self.registry.enqueue(self.node, "summarize", {"text": "2"})
        self.assertIsNotNone(self.registry.poll(self.node, timeout_s=0))
        self.assertIsNone(self.registry.poll(self.node, timeout_s=0))

    def test_completion_releases_the_slot_for_the_next_task(self):
        self.register()
        first = self.registry.enqueue(self.node, "summarize", {"text": "1"})
        second = self.registry.enqueue(self.node, "summarize", {"text": "2"})
        self.registry.poll(self.node, timeout_s=0)
        entry = self.registry.complete(self.node, first["task_id"], ok=True,
                                       result={"summary": "s"}, duration_ms=12)
        self.assertTrue(entry["matched"])
        nxt = self.registry.poll(self.node, timeout_s=0)
        self.assertEqual(nxt["task_id"], second["task_id"])
        node = self.registry.nodes()[0]
        self.assertEqual(node["tasks_done"], 1)

    def test_late_result_is_recorded_but_flagged_unmatched(self):
        registry = WebNodeRegistry(task_ttl_s=10)
        registry.register("a", capabilities=["summarize"], now=1000.0)
        task = registry.enqueue("a", "summarize", {"text": "x"}, now=1000.0)
        registry.poll("a", timeout_s=0, now=1000.0)
        registry.poll("a", timeout_s=0, now=1015.0)      # TTL scaduto: inflight droppato
        entry = registry.complete("a", task["task_id"], ok=False, error="troppo tardi",
                                  now=1016.0)
        self.assertFalse(entry["matched"])
        self.assertEqual(entry["error"], "troppo tardi")
        self.assertEqual(registry.results()[-1]["task_id"], task["task_id"])

    def test_poll_from_an_unknown_node_raises(self):
        with self.assertRaises(WebNodeUnknown):
            self.registry.poll("ghost", timeout_s=0)

    def test_poll_wakes_up_when_a_task_arrives(self):
        registry = WebNodeRegistry()
        registry.register("a", capabilities=["summarize"])
        box = {}

        def waiter():
            box["task"] = registry.poll("a", timeout_s=3)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)
        registry.enqueue("a", "summarize", {"text": "sveglia"})
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(box["task"])
        self.assertEqual(box["task"]["payload"]["text"], "sveglia")


class ExpiryTests(unittest.TestCase):
    def test_expired_queued_task_is_dropped(self):
        registry = WebNodeRegistry(task_ttl_s=10)
        registry.register("a", capabilities=["summarize"], now=1000.0)
        registry.enqueue("a", "summarize", {"text": "x"}, now=1000.0)
        self.assertIsNone(registry.poll("a", timeout_s=0, now=1011.0))
        self.assertEqual(registry.status()["queued"], 0)

    def test_expired_inflight_task_is_dropped_and_never_requeued(self):
        registry = WebNodeRegistry(task_ttl_s=10)
        registry.register("a", capabilities=["summarize"], now=1000.0)
        registry.enqueue("a", "summarize", {"text": "x"}, now=1000.0)
        self.assertIsNotNone(registry.poll("a", timeout_s=0, now=1000.0))
        self.assertIsNone(registry.poll("a", timeout_s=0, now=1011.0))
        self.assertIsNone(registry.poll("a", timeout_s=0, now=1012.0))
        self.assertEqual(registry.status()["inflight"], 0)

    def test_node_without_heartbeat_is_dropped_with_its_queue(self):
        registry = WebNodeRegistry()
        registry.register("a", capabilities=["summarize"], now=1000.0)
        registry.enqueue("a", "summarize", {"text": "x"}, now=1000.0)
        with self.assertRaises(WebNodeUnknown):
            registry.poll("a", timeout_s=0, now=1000.0 + STALE_NODE_S + 1)
        self.assertEqual(registry.status()["web_nodes"], 0)
        self.assertEqual(registry.status()["queued"], 0)


class CancelAndStatusTests(RegistryTestCase):
    def test_cancel_removes_a_queued_task(self):
        self.register()
        task = self.registry.enqueue(self.node, "summarize", {"text": "x"})
        self.assertTrue(self.registry.cancel(task["task_id"]))
        self.assertIsNone(self.registry.poll(self.node, timeout_s=0))
        self.assertFalse(self.registry.cancel(task["task_id"]))

    def test_status_reports_counts_and_the_whitelist(self):
        self.register()
        self.registry.enqueue(self.node, "summarize", {"text": "x"})
        status = self.registry.status()
        self.assertEqual(status["web_nodes"], 1)
        self.assertEqual(status["queued"], 1)
        self.assertEqual(status["inflight"], 0)
        self.assertEqual(status["web_safe_task_types"], sorted(WEB_SAFE_TASK_TYPES))

    def test_result_history_is_bounded(self):
        registry = WebNodeRegistry(result_history=2)
        registry.register("a", capabilities=["summarize"])
        for index in range(5):
            task = registry.enqueue("a", "summarize", {"text": str(index)})
            registry.poll("a", timeout_s=0)
            registry.complete("a", task["task_id"], ok=True)
        self.assertEqual(len(registry.results(limit=50)), 2)


if __name__ == "__main__":
    unittest.main()
