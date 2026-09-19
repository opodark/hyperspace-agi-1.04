"""Registry e coda dei task per i nodi browser (web node).

Il web node e' l'unico worker della mesh che il control-plane NON puo'
indirizzare: una scheda del browser non ha un endpoint HTTP in ingresso. Qui
si inverte quindi la direzione, esattamente come fa shared/code_sandbox.py con
il runner offline: il browser si registra, poi *tira* il lavoro con un
long-poll e pubblica il risultato.

Vincoli di progetto (gli stessi del resto del repo):
- solo i task dichiarati "web-safe" possono essere accodati qui, cosi' nessuna
  inferenza pesante puo' finire su una scheda del browser;
- lo stato vive solo in memoria: un web node e' effimero per natura (la scheda
  si chiude) e non deve mai essere fonte di verita';
- tutto e' limitato (profondita' coda, dimensione payload, TTL dei task,
  durata del poll) perche' un client distratto o ostile non possa esaurire il
  control-plane.
"""
from __future__ import annotations

import threading
import time
import uuid

# Tipi di task ammessi sui web node. Un nodo puo' dichiarare un sottoinsieme di
# questi, mai qualcosa fuori da qui: l'intersezione e' fatta in register().
WEB_SAFE_TASK_TYPES = {
    "validate_json",   # validazione strutturata, nessun modello coinvolto
    "summarize",       # estrattivo e limitato
    "translate",       # Translation API del browser, quando disponibile
    "moderate",        # euristica dichiarata tale: NON e' un modello
    "embed_texts",     # richiede un runtime che il nodo deve saper dichiarare
}

DEFAULT_MAX_NODES      = 64
DEFAULT_MAX_QUEUE      = 32
DEFAULT_MAX_PAYLOAD    = 64 * 1024
DEFAULT_TASK_TTL_S     = 60
DEFAULT_MAX_POLL_S     = 30
DEFAULT_RESULT_HISTORY = 64
STALE_NODE_S           = 120


class WebNodeError(ValueError):
    """Errore di protocollo rispondibile al client con un 4xx."""


class WebNodeUnknown(WebNodeError):
    """Chiamata da un node_id che non si e' mai registrato (o gia' scaduto)."""


class WebTaskRejected(WebNodeError):
    """Task non accodabile: tipo non web-safe, capability assente, limite, coda piena."""


def _clean_id(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WebNodeError(f"{field} mancante")
    if len(text) > 128:
        raise WebNodeError(f"{field} troppo lungo")
    return text


class WebNodeRegistry:
    """Stato condiviso fra le route /web/* del control-plane.

    E' thread-safe (Flask serve le richieste da piu' thread) e iniettabile
    nell'orologio (`clock`) per essere testata senza sleep reali.
    """

    def __init__(self, *, max_nodes=DEFAULT_MAX_NODES, max_queue=DEFAULT_MAX_QUEUE,
                 max_payload_bytes=DEFAULT_MAX_PAYLOAD, task_ttl_s=DEFAULT_TASK_TTL_S,
                 max_poll_s=DEFAULT_MAX_POLL_S, result_history=DEFAULT_RESULT_HISTORY,
                 clock=time.time):
        self.max_nodes = max(1, int(max_nodes))
        self.max_queue = max(1, int(max_queue))
        self.max_payload_bytes = max(1, int(max_payload_bytes))
        self.task_ttl_s = max(1, int(task_ttl_s))
        self.max_poll_s = max(1, int(max_poll_s))
        self.result_history = max(1, int(result_history))
        self.clock = clock
        self._lock = threading.Condition()
        self._nodes: dict = {}
        self._queues: dict = {}
        self._inflight: dict = {}    # node_id -> task in volo
        self._tasks: dict = {}       # task_id -> task (per complete/cancel)
        self._results: list = []     # cronologia diagnostica, limitata
        self._counter = 0

    # ── nodi ──────────────────────────────────────────────────────────────────
    def register(self, node_id, *, capabilities=None, label="", browser="",
                 limits=None, now=None):
        """Registra (o ri-registra) un web node e ne dichiara le capability.

        Le capability vengono INTERSECATE con WEB_SAFE_TASK_TYPES: un nodo non
        puo' auto-assegnarsi lavoro che un web node non deve ricevere.
        """
        node_id = _clean_id(node_id, "node_id")
        declared = {str(c).strip() for c in (capabilities or []) if str(c).strip()}
        accepted = sorted(declared & WEB_SAFE_TASK_TYPES)
        rejected = sorted(declared - WEB_SAFE_TASK_TYPES)
        limits = dict(limits or {})
        moment = self.clock() if now is None else now
        with self._lock:
            if node_id not in self._nodes and len(self._nodes) >= self.max_nodes:
                raise WebTaskRejected("troppi web node registrati")
            previous = self._nodes.get(node_id) or {}
            record = {
                "node_id": node_id,
                "type": "web-node",
                "label": str(label or "")[:64],
                "browser": str(browser or "")[:128],
                "capabilities": accepted,
                "rejected_capabilities": rejected,
                "limits": {
                    "max_context": max(0, int(limits.get("max_context", 0) or 0)),
                    "max_payload_bytes": min(
                        self.max_payload_bytes,
                        max(0, int(limits.get("max_payload_bytes", self.max_payload_bytes)
                                or self.max_payload_bytes)),
                    ),
                },
                "registered_at": previous.get("registered_at", moment),
                "last_seen": moment,
                "tasks_done": previous.get("tasks_done", 0),
                "tasks_failed": previous.get("tasks_failed", 0),
            }
            self._nodes[node_id] = record
            self._queues.setdefault(node_id, [])
            self._lock.notify_all()
            return dict(record)

    def heartbeat(self, node_id, now=None):
        node_id = _clean_id(node_id, "node_id")
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                raise WebNodeUnknown(f"web node sconosciuto: {node_id}")
            node["last_seen"] = self.clock() if now is None else now
            return dict(node)

    def nodes(self):
        with self._lock:
            return [dict(n) for n in self._nodes.values()]

    # ── task ──────────────────────────────────────────────────────────────────
    def enqueue(self, node_id, task_type, payload=None, *, constraints=None, now=None):
        """Accoda un task web-safe per un nodo specifico.

        Rifiuta se il tipo non e' web-safe, se il nodo non l'ha dichiarato, se
        il payload eccede il limite o se la coda del nodo e' piena.
        """
        node_id = _clean_id(node_id, "node_id")
        task_type = str(task_type or "").strip()
        if task_type not in WEB_SAFE_TASK_TYPES:
            raise WebTaskRejected(f"tipo di task non web-safe: {task_type or '(vuoto)'}")
        payload = payload if isinstance(payload, dict) else {}
        size = len(repr(payload).encode("utf-8"))
        moment = self.clock() if now is None else now
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                raise WebNodeUnknown(f"web node sconosciuto: {node_id}")
            if task_type not in node["capabilities"]:
                raise WebTaskRejected(
                    f"il nodo {node_id} non ha dichiarato la capability {task_type}")
            ceiling = node["limits"]["max_payload_bytes"] or self.max_payload_bytes
            if size > min(ceiling, self.max_payload_bytes):
                raise WebTaskRejected(f"payload troppo grande: {size} byte")
            if len(self._queues.setdefault(node_id, [])) >= self.max_queue:
                raise WebTaskRejected(f"coda piena per {node_id}")
            self._counter += 1
            constraints = dict(constraints or {})
            task = {
                "task_id": f"web-{self._counter:06d}-{uuid.uuid4().hex[:8]}",
                "type": task_type,
                "payload": payload,
                "constraints": {
                    "timeout_ms": max(100, min(120_000, int(constraints.get("timeout_ms", 30_000)))),
                    "max_tokens": max(0, int(constraints.get("max_tokens", 512))),
                },
                "node_id": node_id,
                "created_at": moment,
                "expires_at": moment + self.task_ttl_s,
            }
            self._queues[node_id].append(task)
            self._tasks[task["task_id"]] = task
            self._lock.notify_all()
            return dict(task)

    def poll(self, node_id, *, timeout_s=None, now=None):
        """Long-poll: restituisce il prossimo task o None allo scadere.

        Un nodo con un task gia' in volo riceve None: un browser e'
        single-threaded, e servirgli due volte lo stesso task rischierebbe una
        doppia esecuzione.
        """
        node_id = _clean_id(node_id, "node_id")
        budget = self.max_poll_s if timeout_s is None else max(0, min(self.max_poll_s, int(timeout_s)))
        with self._lock:
            if node_id not in self._nodes:
                raise WebNodeUnknown(f"web node sconosciuto: {node_id}")
            self._purge_locked(now)
            deadline = time.monotonic() + budget
            while True:
                self._purge_locked(now)
                if node_id not in self._nodes:
                    raise WebNodeUnknown(f"web node scaduto: {node_id}")
                self._nodes[node_id]["last_seen"] = self.clock() if now is None else now
                if node_id not in self._inflight:
                    queue = self._queues.get(node_id) or []
                    if queue:
                        task = queue.pop(0)
                        task["started_at"] = self.clock() if now is None else now
                        self._inflight[node_id] = task
                        return dict(task)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or budget == 0:
                    return None
                self._lock.wait(remaining)

    def complete(self, node_id, task_id, *, ok, result=None, error="", duration_ms=None,
                 now=None):
        """Chiude il task in volo del nodo e ne conserva l'esito.

        Chiamare complete() per un task che non e' piu' in volo (TTL scaduto,
        risposta arrivata dopo un reload) non e' un errore: l'esito viene
        comunque registrato in cronologia con matched=False, cosi' la diagnosi
        non perde l'evento.
        """
        node_id = _clean_id(node_id, "node_id")
        task_id = _clean_id(task_id, "task_id")
        with self._lock:
            node = self._nodes.get(node_id)
            inflight = self._inflight.get(node_id)
            matched = bool(inflight and inflight.get("task_id") == task_id)
            if matched:
                del self._inflight[node_id]
            if node:
                node["last_seen"] = self.clock() if now is None else now
                node["tasks_done" if ok else "tasks_failed"] += 1
            self._tasks.pop(task_id, None)
            entry = {
                "task_id": task_id,
                "node_id": node_id,
                "ok": bool(ok),
                "result": result,
                "error": str(error or "")[:500],
                "duration_ms": int(duration_ms) if duration_ms is not None else None,
                "matched": matched,
                "completed_at": self.clock() if now is None else now,
            }
            self._results.append(entry)
            del self._results[:-self.result_history]
            self._lock.notify_all()
            return dict(entry)

    def cancel(self, task_id):
        task_id = _clean_id(task_id, "task_id")
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if not task:
                return False
            node_id = task["node_id"]
            self._queues[node_id] = [
                t for t in (self._queues.get(node_id) or []) if t["task_id"] != task_id]
            inflight = self._inflight.get(node_id)
            if inflight and inflight.get("task_id") == task_id:
                del self._inflight[node_id]
            self._lock.notify_all()
            return True

    # ── diagnostica ───────────────────────────────────────────────────────────
    def results(self, limit=20):
        with self._lock:
            return [dict(r) for r in self._results[-max(1, int(limit)):]]

    def status(self):
        with self._lock:
            self._purge_locked(None)
            return {
                "enabled": True,
                "web_nodes": len(self._nodes),
                "queued": sum(len(q) for q in self._queues.values()),
                "inflight": len(self._inflight),
                "max_queue_per_node": self.max_queue,
                "max_payload_bytes": self.max_payload_bytes,
                "task_ttl_s": self.task_ttl_s,
                "max_poll_s": self.max_poll_s,
                "web_safe_task_types": sorted(WEB_SAFE_TASK_TYPES),
                "nodes": [dict(n) for n in self._nodes.values()],
            }

    # ── interni ───────────────────────────────────────────────────────────────
    def _trim_nodes_locked(self, now):
        """Un web node e' effimero: senza heartbeat viene tolto, con la sua coda."""
        moment = self.clock() if now is None else now
        stale = [nid for nid, n in self._nodes.items()
                 if moment - n["last_seen"] > STALE_NODE_S]
        for nid in stale:
            self._drop_node_locked(nid)

    def _drop_node_locked(self, node_id):
        self._nodes.pop(node_id, None)
        for task in self._queues.pop(node_id, []):
            self._tasks.pop(task["task_id"], None)
        inflight = self._inflight.pop(node_id, None)
        if inflight:
            self._tasks.pop(inflight["task_id"], None)

    def _purge_locked(self, now):
        """Scarta i task scaduti (in coda o in volo) e i nodi senza heartbeat.

        Un task in volo scaduto viene DROPPATO, non riaccodato: riaccodarlo
        rischierebbe una doppia esecuzione su un client che in realta' sta solo
        rispondendo lentamente.
        """
        moment = self.clock() if now is None else now
        self._trim_nodes_locked(moment)
        for node_id, queue in list(self._queues.items()):
            alive = []
            for task in queue:
                if task["expires_at"] <= moment:
                    self._tasks.pop(task["task_id"], None)
                else:
                    alive.append(task)
            self._queues[node_id] = alive
        for node_id, task in list(self._inflight.items()):
            if task.get("expires_at", 0) <= moment:
                del self._inflight[node_id]
                self._tasks.pop(task["task_id"], None)
