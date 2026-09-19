import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest import TestCase, mock

from shared.hermes_memory import HermesMemoryClient, HermesMemoryError


ROOT = Path(__file__).resolve().parents[1]


class HermesMemoryClientTests(TestCase):
    @mock.patch("shared.hermes_memory.requests.request")
    def test_client_sends_bearer_token_and_stores_entry(self, request):
        response = mock.Mock()
        response.json.return_value = {"ok": True, "stored": True, "id": "m1"}
        request.return_value = response

        result = HermesMemoryClient("http://bridge:8098", "secret-token").store({"content": "remember"})

        self.assertTrue(result["stored"])
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(request.call_args.kwargs["json"]["entry"]["content"], "remember")

    @mock.patch("shared.hermes_memory.requests.request")
    def test_client_fails_closed_on_transport_error(self, request):
        request.side_effect = RuntimeError("connection refused")
        with self.assertRaises(RuntimeError):
            HermesMemoryClient("http://bridge", "token").health()


class FakeDB:
    def __init__(self):
        self.sessions = {}
        self.next_id = 1

    def ensure_session(self, session_id, source="unknown", **kwargs):
        self.sessions.setdefault(session_id, {"source": source, "messages": [], **kwargs})
        return session_id

    def append_message(self, session_id, role, content=None, **kwargs):
        message = {"id": self.next_id, "session_id": session_id, "role": role,
                   "content": content, **kwargs}
        self.next_id += 1
        self.sessions[session_id]["messages"].append(message)
        return message["id"]

    def search_sessions(self, source=None, limit=20, offset=0, **kwargs):
        rows = [{"id": sid, "source": value["source"],
                 "message_count": len(value["messages"]), "started_at": 0}
                for sid, value in self.sessions.items() if not source or value["source"] == source]
        return rows[offset:offset + limit]

    def get_messages(self, session_id, **kwargs):
        return list(self.sessions[session_id]["messages"])

    def search_messages(self, query, source_filter=None, limit=20, **kwargs):
        needle = query.lower()
        rows = []
        for sid, session in self.sessions.items():
            if source_filter and session["source"] not in source_filter:
                continue
            rows.extend(message for message in session["messages"]
                        if needle in str(message.get("content", "")).lower())
        return rows[:limit]


def load_bridge_module():
    fake_state = types.ModuleType("hermes_state")
    fake_state.SessionDB = FakeDB
    fake_memory = types.ModuleType("tools.memory_tool")
    store = types.SimpleNamespace(memory_entries=[], user_entries=[],
                                  add=lambda target, content: {"success": True})
    fake_memory.load_on_disk_store = lambda: store
    old_modules = {name: sys.modules.get(name) for name in ("hermes_state", "tools", "tools.memory_tool")}
    sys.modules["hermes_state"] = fake_state
    sys.modules.setdefault("tools", types.ModuleType("tools"))
    sys.modules["tools.memory_tool"] = fake_memory
    try:
        spec = importlib.util.spec_from_file_location("hyperspace_hermes_bridge", ROOT / "scripts" / "hermes_memory_bridge.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in old_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class HermesBridgeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = load_bridge_module()

    def test_store_is_idempotent_and_round_trips_metadata(self):
        memory = self.bridge.HermesMemory(FakeDB())
        entry = {"ts": "2026-09-16T01:02:03Z", "type": "insight",
                 "content": "Use Hermes as the only memory", "source": "dream"}

        first = memory.store(entry)
        second = memory.store(entry)
        entries = memory.entries()

        self.assertTrue(first["stored"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["content"], entry["content"])

    def test_query_uses_hermes_search_and_event_filter(self):
        memory = self.bridge.HermesMemory(FakeDB())
        memory.store({"content": "nightly sandbox passed", "type": "test", "source": "dream"})
        memory.store({"content": "nightly model note", "type": "note", "source": "dream"})

        results = memory.query("nightly", 10, event_type="test")

        self.assertEqual([item["content"] for item in results], ["nightly sandbox passed"])

    def test_new_revision_replaces_and_revocation_hides_memory(self):
        memory = self.bridge.HermesMemory(FakeDB())
        memory.store({"id": "insight-1", "content": "first", "status": "active"})
        changed = memory.store({"id": "insight-1", "content": "improved", "status": "active"})
        self.assertTrue(changed["stored"])
        self.assertEqual([item["content"] for item in memory.entries()], ["improved"])

        revoked = memory.store({"id": "insight-1", "content": "improved", "status": "revoked"})
        self.assertTrue(revoked["stored"])
        self.assertEqual(memory.entries(), [])

    def test_lifecycle_quarantine_restore_and_revoke(self):
        memory = self.bridge.HermesMemory(FakeDB())
        memory.store({"id": "cleanup-1", "content": "temporary smoke test"})

        quarantined = memory.lifecycle(["cleanup-1"], "quarantine", "test noise")
        self.assertEqual(quarantined["changed"], ["cleanup-1"])
        self.assertEqual(memory.entries()[0]["status"], "quarantined")

        memory.lifecycle(["cleanup-1"], "restore", "keep it")
        self.assertEqual(memory.entries()[0]["status"], "active")

        memory.lifecycle(["cleanup-1"], "revoke", "confirmed purge")
        self.assertEqual(memory.entries(), [])

    def test_query_filters_node_model_status_and_date(self):
        memory = self.bridge.HermesMemory(FakeDB())
        memory.store({"id": "a", "content": "alpha", "node_id": "win", "model": "qwen",
                      "ts": "2026-09-19T10:00:00Z"})
        memory.store({"id": "b", "content": "beta", "node_id": "mac", "model": "llama",
                      "ts": "2026-09-20T10:00:00Z"})

        results = memory.query("", 10, mode="browse", node_id="mac", model="llama",
                               date_from="2026-09-20", status="active")
        self.assertEqual([item["id"] for item in results], ["b"])

    def test_import_reports_invalid_entries_without_losing_valid_ones(self):
        memory = self.bridge.HermesMemory(FakeDB())
        result = memory.import_entries([{"content": "valid"}, {"content": ""}])
        self.assertEqual(result["stored"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertFalse(result["ok"])
