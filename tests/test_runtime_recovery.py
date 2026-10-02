# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Mac runtime audit: auth, liveness, native routing, DB."""
import ast

from tests import cp_source
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, jsonify, request
from shared import db
from shared.network_security import token_authorized




def load(names, scope):
    nodes = [n for n in cp_source.albero().body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "cp", "exec"), scope)
    return scope


class ConfigurationAccessTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.scope = load({"_protect_configuration", "_network_admin_error"}, {
            "app": self.app, "request": request, "jsonify": jsonify,
            "NETWORK_ADMIN_TOKEN": "x" * 32,
            "_NETWORK_ADMIN_HEADER": "X-Hyperspace-Network-Token",
            "token_authorized": token_authorized,
        })
        self.writes = []
        def endpoint():
            self.writes.append(request.path)
            return jsonify(ok=True)
        for i, path in enumerate(("/config/advanced", "/config/env", "/config/secret/rotate")):
            self.app.add_url_rule(path, str(i), endpoint, methods=["GET", "POST"])
        self.client = self.app.test_client()

    def test_requests_without_token_never_reach_configuration(self):
        for path in ("/config/advanced", "/config/env", "/config/secret/rotate"):
            for method in ("get", "post"):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401)
        self.assertEqual(self.writes, [])

    def test_wrong_token_rejected_and_correct_token_allowed(self):
        for token, status in (("bad", 401), ("x" * 32, 200)):
            self.assertEqual(self.client.post("/config/env", headers={
                "X-Hyperspace-Network-Token": token}).status_code, status)
        self.assertEqual(self.writes, ["/config/env"])

    def test_missing_server_token_fails_closed(self):
        self.scope["NETWORK_ADMIN_TOKEN"] = ""
        self.assertEqual(self.client.get("/config/env").status_code, 503)


class LivenessTests(unittest.TestCase):
    def test_health_counts_local_memory_without_contacting_hermes(self):
        remote = Mock(side_effect=AssertionError("network called"))
        sync = Mock()
        sync.read_local.return_value = [{"content": "preserved"}]
        scope = load({"_health_memory_count"}, {
            "time": time, "_health_memory_cache": {"ts": 0, "count": 0},
            "_health_memory_lock": threading.Lock(), "MEMORY_BACKEND": "hermes",
            "MEMORY_MAX_ENTRIES": 200, "memory_sync": sync, "_load_memory": remote,
        })
        self.assertEqual(scope["_health_memory_count"](), 1)
        remote.assert_not_called()


class NativeRoutingTests(unittest.TestCase):
    def test_native_only_for_single_ollama_endpoint(self):
        scope = load({"_native_direct_enabled", "_use_native_chat_fallback"}, {
            "INFERENCE_BACKEND": "ollama", "_inference_urls": lambda: ["http://ollama"],
            "_NATIVE_CHAT_FALLBACK_OVERRIDE": "", "_NATIVE_CHAT_FALLBACK_PATTERNS": ["qwen3"],
        })
        native = scope["_native_direct_enabled"]
        self.assertTrue(native("qwen3:8b"))
        for backend in ("lmstudio", "mlx", "vllm"):
            scope["INFERENCE_BACKEND"] = backend
            self.assertFalse(native("qwen3:8b"))
        scope["INFERENCE_BACKEND"] = "ollama"
        scope["_inference_urls"] = lambda: ["http://ollama", "http://lmstudio"]
        self.assertFalse(native("qwen3:8b"))

    def test_explicit_off_disables_native_patterns(self):
        scope = load({"_use_native_chat_fallback"}, {
            "_NATIVE_CHAT_FALLBACK_OVERRIDE": "off", "_NATIVE_CHAT_FALLBACK_PATTERNS": ["qwen3"],
        })
        self.assertFalse(scope["_use_native_chat_fallback"]("qwen3:8b"))


class DatabaseRecoveryTests(unittest.TestCase):
    def test_existing_wal_database_migrates_and_keeps_concurrent_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.db")
            con = sqlite3.connect(path)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
            con.execute("INSERT INTO items VALUES (0)")
            con.commit()
            con.close()
            with patch.object(db, "DB_PATH", path), patch.dict(os.environ, {"SQLITE_JOURNAL_MODE": "DELETE"}):
                def write(i):
                    with db._conn() as connection:
                        connection.execute("INSERT INTO items VALUES (?)", (i,))
                with ThreadPoolExecutor(max_workers=8) as executor:
                    list(executor.map(write, range(1, 101)))
                with db._conn() as connection:
                    self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(connection.execute("SELECT count(*) FROM items").fetchone()[0], 101)
                with self.assertRaises(RuntimeError):
                    with db._conn() as connection:
                        connection.execute("INSERT INTO items VALUES (101)")
                        raise RuntimeError("rollback")
                with db._conn() as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM items").fetchone()[0], 101)


class LocalFirstTests(unittest.TestCase):
    def run_fallback(self, direct):
        # fresco=True: qui sotto il nodo viene RINOMINATO e privato dei
        # decorator per eseguire solo il ramo di fallback.
        route = cp_source.nodo("v1_chat_completions", fresco=True)
        start = next(i for i, n in enumerate(route.body) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == "not deadline.allows()")
        route.name = "fallback"
        route.decorator_list = []
        route.body = route.body[start:]
        fed = Mock(return_value=({"choices": []}, {"peer_id": "peer", "label": "peer"}))
        scope = {
            "deadline": Mock(allows=Mock(return_value=True)), "task": {}, "task_id": "test",
            "db": Mock(), "ollama_base": "http://local", "model": "small", "data": {}, "prompt": "OK",
            "_inference_urls": lambda: ["http://local"], "_run_tool_loop": Mock(return_value=direct),
            "_is_error_payload": lambda value: bool(value.get("error")),
            "_finalize_task": Mock(), "_respond_result": lambda value: value,
            "_try_federated_execution": fed, "_try_omniroute_fallback": Mock(), "push_log": Mock(),
        }
        exec(compile(ast.Module(body=[route], type_ignores=[]), "cp", "exec"), scope)
        return scope["fallback"](), scope

    def test_available_local_model_does_not_wait_for_remote_fallback(self):
        answer = {"choices": [{"message": {"content": "OK"}}]}
        result, scope = self.run_fallback(answer)
        self.assertEqual(result, answer)
        scope["_try_federated_execution"].assert_not_called()
        scope["_try_omniroute_fallback"].assert_not_called()

    def test_local_failure_still_allows_federation(self):
        result, scope = self.run_fallback({"error": "offline"})
        self.assertEqual(result, {"choices": []})
        scope["_try_federated_execution"].assert_called_once()
