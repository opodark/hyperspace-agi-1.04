#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Authenticated host bridge making Hermes the HyperSpace memory backend.

Run this with the Python environment installed by Hermes Agent.  It uses the
official Hermes storage classes, so SQLite WAL/FTS and MEMORY.md locking remain
owned by Hermes itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

from hermes_state import SessionDB
from tools.memory_tool import load_on_disk_store


SOURCE = "hyperspace"
MAX_BODY_BYTES = 8 * 1024 * 1024
_session_re = re.compile(r"[^a-zA-Z0-9_.-]+")


def _iso(value: Any = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _timestamp(value: Any = None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return datetime.now(timezone.utc).timestamp()


def _content(entry: Dict[str, Any]) -> str:
    direct = entry.get("content") or entry.get("summary") or entry.get("detail")
    if direct:
        return str(direct).strip()
    prompt = str(entry.get("prompt") or "").strip()
    response = str(entry.get("response") or "").strip()
    if prompt and response:
        return f"User: {prompt}\nAssistant: {response}"
    return prompt or response


def _entry_id(entry: Dict[str, Any]) -> str:
    explicit = entry.get("id") or entry.get("memory_id")
    if explicit:
        return str(explicit)[:200]
    canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _revision_id(entry: Dict[str, Any]) -> str:
    canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _session_id(entry: Dict[str, Any]) -> str:
    stamp = datetime.fromtimestamp(
        _timestamp(entry.get("ts") or entry.get("timestamp")), tz=timezone.utc
    ).strftime("%Y-%m-%d")
    origin = str(entry.get("source") or entry.get("node_id") or "mesh")[:64]
    return f"hyperspace-{_session_re.sub('-', origin).strip('-') or 'mesh'}-{stamp}"


class HermesMemory:
    def __init__(self, db: Optional[SessionDB] = None):
        self.db = db or SessionDB()
        self.lock = threading.RLock()

    @staticmethod
    def _metadata(entry: Dict[str, Any], memory_id: str, revision_id: str) -> Dict[str, Any]:
        return {"hyperspace_entry": entry, "hyperspace_memory_id": memory_id,
                "hyperspace_revision_id": revision_id,
                "schema": "hyperspace.memory.v1"}

    @staticmethod
    def _from_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        metadata = message.get("display_metadata") or {}
        entry = metadata.get("hyperspace_entry") if isinstance(metadata, dict) else None
        if not isinstance(entry, dict):
            return None
        normalized = dict(entry)
        normalized.setdefault("id", metadata.get("hyperspace_memory_id"))
        normalized.setdefault("ts", _iso(message.get("timestamp")))
        return normalized

    def _sessions(self) -> Iterable[Dict[str, Any]]:
        offset = 0
        while True:
            page = self.db.search_sessions(source=SOURCE, limit=200, offset=offset)
            yield from page
            if len(page) < 200:
                return
            offset += len(page)

    def entries(self, limit: int = 200) -> List[Dict[str, Any]]:
        found = []
        for session in self._sessions():
            for message in self.db.get_messages(session["id"]):
                entry = self._from_message(message)
                if entry is not None:
                    found.append((int(message.get("id", 0)), entry))
        found.sort(key=lambda item: item[0], reverse=True)
        visible, seen = [], set()
        for _, entry in found:
            memory_id = str(entry.get("id") or _entry_id(entry))
            if memory_id in seen:
                continue
            seen.add(memory_id)
            if entry.get("status") == "revoked":
                continue
            visible.append(entry)
            if len(visible) >= limit:
                break
        return visible

    def _known_revisions(self, session_id: str) -> set:
        known = set()
        for message in self.db.get_messages(session_id):
            metadata = message.get("display_metadata") or {}
            if isinstance(metadata, dict):
                revision = metadata.get("hyperspace_revision_id")
                if not revision and isinstance(metadata.get("hyperspace_entry"), dict):
                    revision = _revision_id(metadata["hyperspace_entry"])
                if revision:
                    known.add(str(revision))
        return known

    def store(self, entry: Dict[str, Any], curated_target: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(entry, dict):
            raise ValueError("entry must be an object")
        text = _content(entry)
        if not text:
            raise ValueError("entry has no searchable content")
        if curated_target:
            if curated_target not in {"memory", "user"}:
                raise ValueError("curated_target must be memory or user")
            result = load_on_disk_store().add(curated_target, text)
            if not result.get("success"):
                raise ValueError(result.get("error", "Hermes curated-memory write failed"))
            return {"ok": True, "curated": curated_target, "result": result}

        memory_id = _entry_id(entry)
        revision_id = _revision_id(entry)
        session_id = _session_id(entry)
        with self.lock:
            self.db.ensure_session(session_id, source=SOURCE, display_name="HyperSpace memory")
            if revision_id in self._known_revisions(session_id):
                return {"ok": True, "stored": False, "duplicate": True, "id": memory_id}
            self.db.append_message(
                session_id, role="user", content=text,
                timestamp=_timestamp(entry.get("ts") or entry.get("timestamp")),
                platform_message_id=f"{memory_id}:{revision_id[:16]}",
                display_kind="hyperspace_memory",
                display_metadata=self._metadata(dict(entry), memory_id, revision_id),
            )
        return {"ok": True, "stored": True, "id": memory_id, "session_id": session_id}

    def import_entries(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        stored = duplicates = failed = 0
        errors = []
        for index, entry in enumerate(entries):
            try:
                result = self.store(entry)
                if result.get("duplicate"):
                    duplicates += 1
                else:
                    stored += 1
            except Exception as exc:
                failed += 1
                errors.append({"index": index, "error": str(exc)})
        return {"ok": failed == 0, "stored": stored, "duplicates": duplicates,
                "failed": failed, "errors": errors[:20]}

    def query(self, query: str, limit: int, event_type: str = "", mode: str = "semantic",
              node_id: str = "", source: str = "", model: str = "", status: str = "active",
              date_from: str = "", date_to: str = "", offset: int = 0) -> List[Dict[str, Any]]:
        candidates = self.entries(limit=5000) if mode == "browse" or not query else None
        if candidates is None:
            hits = self.db.search_messages(query, source_filter=[SOURCE], limit=min(limit * 4, 400))
            wanted = {int(hit["id"]) for hit in hits if hit.get("id") is not None}
            matched_ids = set()
            for session in self._sessions():
                for message in self.db.get_messages(session["id"]):
                    if int(message.get("id", -1)) in wanted:
                        entry = self._from_message(message)
                        if entry is not None:
                            matched_ids.add(str(entry.get("id") or _entry_id(entry)))
            active_entries = self.entries(limit=5000)
            needle = query.lower()
            matched_ids.update(str(entry.get("id") or _entry_id(entry)) for entry in active_entries
                               if needle in _content(entry).lower())
            candidates = [entry for entry in active_entries
                          if str(entry.get("id") or _entry_id(entry)) in matched_ids]
        if event_type:
            needle = event_type.lower()
            candidates = [entry for entry in candidates if needle in str(
                entry.get("type") or entry.get("event_type") or "memory").lower()]
        def matches(entry: Dict[str, Any]) -> bool:
            entry_status = str(entry.get("status") or "active")
            stamp = str(entry.get("ts") or entry.get("timestamp") or "")
            return (not status or entry_status == status) and \
                (not node_id or node_id.lower() in str(entry.get("node_id") or entry.get("sourceNode") or "").lower()) and \
                (not source or source.lower() in str(entry.get("source") or "").lower()) and \
                (not model or model.lower() in str(entry.get("model") or "").lower()) and \
                (not date_from or stamp >= date_from) and (not date_to or stamp <= date_to + "T23:59:59Z")
        candidates = [entry for entry in candidates if matches(entry)]
        return candidates[max(0, offset):max(0, offset) + limit]

    def lifecycle(self, ids: List[str], action: str, reason: str = "") -> Dict[str, Any]:
        if action not in {"quarantine", "restore", "revoke"}:
            raise ValueError("action must be quarantine, restore or revoke")
        wanted = {str(value) for value in ids if value}
        if not wanted or len(wanted) > 500:
            raise ValueError("ids must contain between 1 and 500 values")
        current = {str(entry.get("id")): entry for entry in self.entries(limit=5000)}
        changed, missing = [], []
        for memory_id in wanted:
            original = current.get(memory_id)
            if not original:
                missing.append(memory_id)
                continue
            revision = dict(original)
            revision["status"] = {"quarantine": "quarantined", "restore": "active", "revoke": "revoked"}[action]
            revision["lifecycle"] = {"action": action, "reason": reason, "at": _iso()}
            result = self.store(revision)
            if result.get("stored"):
                changed.append(memory_id)
        return {"ok": not missing, "action": action, "changed": changed, "missing": missing}

    def stats(self) -> Dict[str, Any]:
        sessions = list(self._sessions())
        count = sum(int(session.get("message_count") or 0) for session in sessions)
        store = load_on_disk_store()
        return {"ok": True, "backend": "hermes", "sessions": len(sessions), "entries": count,
                "curated_memory_entries": len(store.memory_entries),
                "user_profile_entries": len(store.user_entries)}


class Handler(BaseHTTPRequestHandler):
    server_version = "HyperSpaceHermesBridge/1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _authorized(self) -> bool:
        expected = self.server.token
        supplied = self.headers.get("Authorization", "")
        return bool(expected) and secrets.compare_digest(supplied, f"Bearer {expected}")

    def _send(self, status: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def _dispatch(self) -> None:
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        memory = self.server.memory
        if self.command == "GET" and parsed.path == "/health":
            self._send(200, {"ok": True, "backend": "hermes", "source": SOURCE})
        elif self.command == "GET" and parsed.path == "/entries":
            limit = int(parse_qs(parsed.query).get("limit", ["200"])[0])
            entries = memory.entries(max(1, min(limit, 5000)))
            self._send(200, {"ok": True, "entries": entries, "total": len(entries)})
        elif self.command == "GET" and parsed.path == "/stats":
            self._send(200, memory.stats())
        elif self.command == "POST" and parsed.path == "/store":
            body = self._json()
            self._send(200, memory.store(body.get("entry"), body.get("curated_target")))
        elif self.command == "POST" and parsed.path == "/import":
            body = self._json()
            entries = body.get("entries")
            if not isinstance(entries, list) or len(entries) > 50000:
                raise ValueError("entries must be a list of at most 50000 items")
            result = memory.import_entries(entries)
            self._send(200 if result["ok"] else 422, result)
        elif self.command == "POST" and parsed.path == "/query":
            body = self._json()
            entries = memory.query(str(body.get("query", "")), max(1, min(int(body.get("limit", 10)), 100)),
                                   str(body.get("event_type", "")), str(body.get("mode", "semantic")),
                                   str(body.get("node_id", "")), str(body.get("source", "")),
                                   str(body.get("model", "")), str(body.get("status", "active")),
                                   str(body.get("date_from", "")), str(body.get("date_to", "")),
                                   max(0, int(body.get("offset", 0))))
            self._send(200, {"ok": True, "entries": entries})
        elif self.command == "POST" and parsed.path == "/lifecycle":
            body = self._json()
            ids = body.get("ids")
            if not isinstance(ids, list):
                raise ValueError("ids must be a list")
            result = memory.lifecycle(ids, str(body.get("action", "")), str(body.get("reason", "")))
            self._send(200 if result["ok"] else 207, result)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_GET(self) -> None:
        try:
            self._dispatch()
        except (ValueError, TypeError) as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})

    do_POST = do_GET


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("HERMES_MEMORY_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("HERMES_MEMORY_PORT", "8098")))
    parser.add_argument("--token", default=os.getenv("HERMES_MEMORY_TOKEN", ""))
    parser.add_argument("--token-file", default=os.getenv("HERMES_MEMORY_TOKEN_FILE", ""))
    args = parser.parse_args()
    if not args.token and args.token_file:
        try:
            args.token = open(args.token_file, encoding="utf-8").read().strip()
        except OSError as exc:
            parser.error(f"cannot read token file: {exc}")
    if len(args.token) < 32:
        parser.error("HERMES_MEMORY_TOKEN/--token must contain at least 32 characters")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.token = args.token
    server.memory = HermesMemory()
    print(f"Hermes memory bridge listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
