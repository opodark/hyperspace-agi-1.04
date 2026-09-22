#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Memoria locale-prima: mirror su disco e coda di ciò che Hermes non ha ancora visto.

Perché esiste (2026-09-22): con `MEMORY_BACKEND=hermes` su due macchine, se quella
che ospita Hermes è spenta ogni scrittura che non arriva al bridge si perde — e si
perde **in silenzio**, che è il modo peggiore. Qui una scrittura che non arriva a
Hermes torna comunque ok, perché non si perde: va nel mirror locale (il gzip, che
si scrive sempre) e nella coda (`memory-outbox.jsonl`), che verrà riconsegnata
quando Hermes torna.

Il mirror serve a tre cose, tutte pratiche:
  - leggerlo a occhio quando si mette in dubbio cosa la macchina sa davvero;
  - rispondere alle letture quando il peer è spento, invece di un 503;
  - restare la fonte del rollback (`MEMORY_BACKEND=legacy` legge quel file).

Contratto: quello che finisce in coda è **già** nella forma `hyperspace.memory.v1`
(`shared/memory_schema.normalize_entry`), quindi la riconsegna non rimappa niente —
è lo stesso percorso che la migrazione ha già dimostrato, e la riconsegna è
idempotente perché l'identità è stabile (id esplicito o hash del contenuto). Un
nodo che riconsegna due volte la stessa voce non crea doppioni: il bridge risponde
`duplicate`.

Cosa questo modulo NON è: non è una replica fra nodi. La coda va solo *verso*
Hermes, in una direzione: nessun nodo riceve la vista dell'altro, quindi non
esistono anelli di sincronizzazione — il motivo per cui `_sync_memory_across_nodes`
è spento quando il backend è `hermes`.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from shared.hermes_memory import HermesMemoryError
from shared.memory_schema import entry_id, normalize_entry, validate_entry

# Sopra questa coda la memoria non è "in ritardo", è un peer spento da giorni: la
# riga di avviso serve a dirlo una volta, non a ogni scrittura.
OUTBOX_WARN = 500
# Quanto si aspetta fra due tentativi di riconsegna (la coda si svuota alla prima
# chiamata utile: non c'è un thread, non c'è un timer).
FLUSH_EVERY_S = 60.0


def _atomic(path: Path, data: bytes) -> None:
    """Scrive e sostituisce: o c'è il file vecchio, o c'è quello nuovo, mai un mezzo.

    Serve nei due momenti in cui la memoria conta di più: il processo ucciso a metà
    scrittura e il ritorno della connessione. `os.replace` è atomico anche su
    Windows, e il file temporaneo sta nella stessa cartella (se fosse altrove,
    sarebbe una copia, non una sostituzione).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = path.with_suffix(path.suffix + ".tmp")
    with open(temporaneo, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporaneo, path)


class MemoryOutbox:
    """Coda append-only (JSONL) delle voci che Hermes non ha ancora accettato."""

    def __init__(self, path):
        self.path = Path(path)
        self._ids: Optional[set] = None

    def pending(self) -> List[Dict[str, Any]]:
        """Le voci in attesa, in ordine di arrivo.

        Una riga illeggibile (processo ucciso durante la scrittura) si salta: una
        riga rotta non deve costare le altre, che sono memoria vera.
        """
        if not self.path.exists():
            return []
        voci: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as file:
            for riga in file:
                riga = riga.strip()
                if not riga:
                    continue
                try:
                    voce = json.loads(riga)
                except ValueError:
                    continue
                if isinstance(voce, dict):
                    voci.append(voce)
        return voci

    def ids(self) -> set:
        """Gli id in coda (per non accodare due volte la stessa voce).

        È una cache di processo: se un ALTRO processo accoda, qui non si vede e si
        accoda un doppione. Non è un problema — Hermes risponde `duplicate` — ed è
        il motivo per cui la riconsegna è idempotente invece di essere "una volta
        sola".
        """
        if self._ids is None:
            self._ids = {entry_id(voce) for voce in self.pending()}
        return self._ids

    def append(self, entry) -> bool:
        """Accoda una voce normalizzata. False se quell'id era già in coda."""
        voce = normalize_entry(entry)
        identificativo = entry_id(voce)
        if identificativo in self.ids():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(voce, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        self._ids.add(identificativo)
        return True

    def replace(self, entries: List[Dict[str, Any]]) -> None:
        """Riscrive la coda con quello che resta (vuoto = coda svuotata)."""
        righe = "".join(json.dumps(voce, ensure_ascii=False) + "\n" for voce in entries)
        _atomic(self.path, righe.encode("utf-8"))
        self._ids = None

    def clear(self) -> None:
        self.replace([])

    def count(self) -> int:
        return len(self.pending())

    def oldest_ts(self) -> Optional[float]:
        """Da quando la voce più vecchia aspetta (epoch); None se la coda è vuota."""
        voci = self.pending()
        if not voci:
            return None
        for chiave in ("timestamp", "ts", "created_at"):
            valore = voci[0].get(chiave)
            if isinstance(valore, (int, float)):
                return float(valore)
            if valore:
                try:
                    return datetime.fromisoformat(str(valore).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
        return None


class MemoryMirror:
    """Il gzip locale: memoria leggibile a occhio, e leggibile quando Hermes tace.

    Segue le stesse regole del vecchio backend `legacy` (TTL e numero massimo di
    voci): è una **vista** locale di quello che questa macchina sa, non l'archivio —
    l'archivio è Hermes. Le voci si scrivono già normalizzate, così il file resta
    rileggibile sia da `MEMORY_BACKEND=legacy` sia da `scripts/memory_migrate.py`
    (che ritrova gli stessi id e quindi non crea doppioni).
    """

    def __init__(self, path, *, ttl_days: int = 7, max_entries: int = 200):
        self.path = Path(path)
        self.ttl_days = int(ttl_days)
        self.max_entries = int(max_entries)

    def load(self) -> List[Dict[str, Any]]:
        """Le voci dal file. Un file rotto non deve far cadere chi chiede memoria."""
        if not self.path.exists():
            return []
        try:
            with gzip.open(str(self.path), "rt", encoding="utf-8") as file:
                dati = json.load(file)
        except (OSError, ValueError):
            return []
        if isinstance(dati, list):
            return [voce for voce in dati if isinstance(voce, dict)]
        if isinstance(dati, dict):
            for chiave in ("entries", "memory", "items"):
                if isinstance(dati.get(chiave), list):
                    return [voce for voce in dati[chiave] if isinstance(voce, dict)]
        return []

    def append(self, entry) -> int:
        """Aggiunge una voce al mirror e ritorna quante voci restano."""
        voce = normalize_entry(entry)
        voci = self.load()
        identificativo = entry_id(voce)
        if not any(entry_id(esistente) == identificativo for esistente in voci):
            voci.append(voce)
        voci = self.prune(voci)
        _atomic(self.path, gzip.compress(
            json.dumps(voci, ensure_ascii=False).encode("utf-8")))
        return len(voci)

    def merge(self, entries: List[Dict[str, Any]]) -> int:
        """Unisce nel mirror delle voci che arrivano da Hermes. Ritorna il totale.

        Serve a fare del file locale un **mirror vero**: cosa questa macchina
        ricorda, non solo cosa ha scritto lei. Le voci locali che Hermes non ha
        ancora (la coda) restano: il mirror è un'unione, non una copia che
        sovrascrive — altrimenti una lettura cancellerebbe la memoria in attesa.
        """
        voci = self.load()
        conosciute = {entry_id(voce) for voce in voci}
        aggiunte = 0
        for voce in entries:
            if not isinstance(voce, dict):
                continue
            normalizzata = normalize_entry(voce)
            if entry_id(normalizzata) in conosciute:
                continue
            conosciute.add(entry_id(normalizzata))
            voci.append(normalizzata)
            aggiunte += 1
        if not aggiunte:
            return len(voci)
        voci = self.prune(voci)
        _atomic(self.path, gzip.compress(
            json.dumps(voci, ensure_ascii=False).encode("utf-8")))
        return len(voci)

    def prune(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """TTL e tetto: oltre quelli la vista locale non serve più (Hermes ricorda)."""
        taglio = datetime.now(timezone.utc) - timedelta(days=self.ttl_days)
        fresche = []
        for voce in entries:
            istante = self._epoch(voce)
            if istante is None or istante >= taglio.timestamp():
                fresche.append(voce)
        fresche.sort(key=lambda voce: self._epoch(voce) or 0.0, reverse=True)
        return fresche[: self.max_entries]

    @staticmethod
    def _epoch(entry: Dict[str, Any]) -> Optional[float]:
        for chiave in ("timestamp", "ts", "created_at"):
            valore = entry.get(chiave)
            if isinstance(valore, (int, float)):
                return float(valore)
            if valore:
                try:
                    return datetime.fromisoformat(str(valore).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
        return None

    def count(self) -> int:
        return len(self.load())

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def info(self) -> Dict[str, Any]:
        """Cosa si può dire del file senza aprirlo: serve nelle statistiche."""
        return {"file": str(self.path), "entries": self.count(),
                "size_bytes": self.size_bytes(),
                "size_kb": round(self.size_bytes() / 1024, 2),
                "ttl_days": self.ttl_days, "max_entries": self.max_entries}


class MemorySync:
    """Scritture e letture con la rete come preferenza e il disco come pavimento.

    - `write`: scrive **sempre** il mirror, poi prova Hermes. Se Hermes non risponde,
      la voce va in coda e la scrittura è comunque riuscita: è già su disco.
    - `flush`: riconsegna la coda a Hermes. Idempotente (identità stabile): si può
      chiamare quando si vuole, e chiamarla due volte non crea doppioni.
    - `read`: legge da Hermes; se Hermes tace legge il mirror e lo **dichiara**
      (`degraded`), invece di restituire zero voci come se la memoria fosse vuota.
    - `stats`: lo stato del pavimento — coda, mirror, e il perché se Hermes tace.
    """

    def __init__(self, client, *, mirror: Optional[MemoryMirror] = None,
                 outbox: Optional[MemoryOutbox] = None, read_fallback: str = "local",
                 log: Optional[Callable[..., None]] = None,
                 flush_every_s: float = FLUSH_EVERY_S, clock: Callable[[], float] = time.time):
        self.client = client
        self.mirror = mirror
        self.outbox = outbox
        self.read_fallback = (read_fallback or "local").strip().lower()
        self.log = log
        self.flush_every_s = float(flush_every_s)
        self.clock = clock
        self._ultimo_flush = 0.0
        self._ultimo_assorbimento = 0.0
        self._avvisi: set = set()

    # ── util ──────────────────────────────────────────────────────────────────
    def _avvisa(self, chiave: str, messaggio: str, *, detail: str = "",
                status: str = "warn") -> None:
        """Logga una volta per tipo: un peer spento non deve inondare i log."""
        if not self.log or chiave in self._avvisi:
            return
        self._avvisi.add(chiave)
        try:
            self.log("memory_sync", messaggio, detail=detail, status=status)
        except TypeError:  # una firma di log più semplice va bene lo stesso
            self.log("memory_sync", messaggio)

    def pending(self) -> int:
        return self.outbox.count() if self.outbox else 0

    # ── scrittura ─────────────────────────────────────────────────────────────
    def write(self, entry) -> Dict[str, Any]:
        """Scrive la memoria. Ritorna sempre un esito: `deferred` quando è in coda.

        Il mirror si scrive **prima** di provare Hermes: se la macchina muore fra le
        due cose, la voce è comunque su disco (e la migrazione la ritroverà).
        """
        voce = normalize_entry(entry)
        problemi = validate_entry(voce)
        esito: Dict[str, Any] = {"ok": True, "backend": "hermes", "id": entry_id(voce)}
        if problemi:
            esito["contract_warnings"] = problemi
            self._avvisa("contratto", "memoria: voce fuori contratto salvata comunque",
                         detail="; ".join(problemi), status="info")
        if self.mirror is not None:
            try:
                esito["mirror_entries"] = self.mirror.append(voce)
                esito["mirrored"] = True
            except OSError as exc:
                esito["mirrored"] = False
                self._avvisa("mirror", f"memoria: mirror locale non scritto ({exc})")
        try:
            risposta = self.client.store(voce)
            esito["stored"] = bool(risposta.get("stored", True))
            esito["duplicate"] = bool(risposta.get("duplicate"))
            self.flush()
            return esito
        except HermesMemoryError as exc:
            esito["stored"] = False
            esito["deferred"] = True
            esito["error"] = str(exc)
            if self.outbox is not None:
                try:
                    esito["queued"] = self.outbox.append(voce)
                except OSError as exc:
                    # Né Hermes né la coda: questo è l'unico caso in cui una voce si
                    # perde davvero, e chi ha scritto deve saperlo subito.
                    esito["ok"] = False
                    esito["queued"] = False
                    esito["error"] = f"{esito['error']}; coda non scrivibile: {exc}"
                    self._avvisa("coda-ko", "memoria: voce NON salvata (coda non scrivibile)",
                                 detail=str(exc))
                    return esito
                esito["pending"] = self.pending()
                if esito["pending"] >= OUTBOX_WARN:
                    self._avvisa("coda-piena",
                                 f"memoria: {esito['pending']} voci in attesa di Hermes",
                                 detail="il peer che ospita Hermes è raggiungibile?")
            return esito

    # ── riconsegna ────────────────────────────────────────────────────────────
    def flush(self, *, force: bool = False) -> Dict[str, Any]:
        """Riconsegna la coda a Hermes. Idempotente, quindi sicura da ripetere."""
        if self.outbox is None:
            return {"ok": True, "sent": 0, "pending": 0, "skipped": "coda non configurata"}
        attesa = self.outbox.pending()
        if not attesa:
            return {"ok": True, "sent": 0, "pending": 0}
        adesso = self.clock()
        if not force and (adesso - self._ultimo_flush) < self.flush_every_s:
            return {"ok": True, "sent": 0, "pending": len(attesa), "skipped": "troppo presto"}
        self._ultimo_flush = adesso
        try:
            risposta = self.client.import_entries(attesa)
        except HermesMemoryError as exc:
            return {"ok": False, "sent": 0, "pending": len(attesa), "error": str(exc)}
        presi = int(risposta.get("stored") or 0) + int(risposta.get("duplicates") or 0)
        if presi >= len(attesa):
            self.outbox.clear()
            self._avvisi.discard("coda-piena")
            self._avvisi.discard("coda-parziale")
            if self.log:
                self.log("memory_sync",
                         f"memoria: {len(attesa)} voci riconsegnate a Hermes",
                         detail=f"prese {presi} su {len(attesa)}", status="success")
            return {"ok": True, "sent": len(attesa), "pending": 0,
                    "stored": int(risposta.get("stored") or 0),
                    "duplicates": int(risposta.get("duplicates") or 0)}
        # Riconsegna parziale: si tiene TUTTO in coda e si riprova. Ripetere è
        # gratis (idempotente), perdere una voce no.
        self._avvisa("coda-parziale",
                     f"memoria: riconsegna parziale ({presi} su {len(attesa)}), riprovo",
                     detail=str(risposta.get("failed") or risposta.get("error") or ""))
        return {"ok": False, "sent": presi, "pending": len(attesa),
                "error": "riconsegna parziale"}

    # ── lettura ───────────────────────────────────────────────────────────────
    def read(self, limit: int = 200) -> Dict[str, Any]:
        """Voci da Hermes; se Hermes tace, dal mirror locale — dichiarandolo."""
        try:
            entries = self.client.entries(limit)
            self._assorbi(entries)
            return {"entries": entries, "degraded": False, "source": "hermes"}
        except HermesMemoryError as exc:
            if self.read_fallback != "local" or self.mirror is None:
                raise
            voci = self.mirror.load()[:limit]
            self._avvisa("lettura-degradata",
                         "memoria: Hermes non raggiungibile, leggo il mirror locale",
                         detail=f"{len(voci)} voci locali; {exc}")
            return {"entries": voci, "degraded": True, "source": "mirror",
                    "reason": str(exc)}

    def read_local(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Solo il mirror, senza sollevare: per le ricerche quando Hermes tace."""
        if self.mirror is None:
            return []
        return self.mirror.load()[:limit]

    def _assorbi(self, entries: List[Dict[str, Any]]) -> int:
        """Porta nel mirror quello che Hermes ha appena detto.

        Senza questo il file locale conterrebbe solo ciò che *questa* macchina ha
        scritto, e una lettura degradata sarebbe una memoria a metà. Con questo è un
        mirror vero — cosa la macchina ricorda, incluse le voci arrivate dagli altri
        nodi. Costa una riscrittura gzip, quindi ha lo stesso intervallo della
        riconsegna: non a ogni lettura.
        """
        if self.mirror is None or not entries:
            return 0
        adesso = self.clock()
        if (adesso - self._ultimo_assorbimento) < self.flush_every_s:
            return 0
        self._ultimo_assorbimento = adesso
        try:
            return self.mirror.merge(entries)
        except OSError as exc:
            self._avvisa("mirror-merge", f"memoria: mirror non aggiornato ({exc})")
            return 0

    def stats(self) -> Dict[str, Any]:
        """Lo stato del pavimento: coda, mirror, e cosa dice Hermes se risponde."""
        esito: Dict[str, Any] = {"backend": "hermes", "degraded": False,
                                 "source": "hermes"}
        try:
            esito.update(self.client.stats())
            self.flush()
        except HermesMemoryError as exc:
            esito.update({"ok": False, "degraded": True, "source": "mirror",
                          "error": str(exc)})
            if self.mirror is not None:
                # La dashboard legge `entries`: meglio il numero locale che nessun
                # numero, perché un vuoto si legge come "non c'è memoria".
                esito["entries"] = self.mirror.count()
        attesa = self.pending()
        piu_vecchia = self.outbox.oldest_ts() if self.outbox else None
        esito["outbox"] = {
            "pending": attesa,
            "file": str(self.outbox.path) if self.outbox else "",
            "oldest_seconds": (round(self.clock() - piu_vecchia, 1) if piu_vecchia else None),
        }
        if self.mirror is not None:
            esito["mirror"] = self.mirror.info()
        return esito


def from_env(client, log: Optional[Callable[..., None]] = None,
             *, memory_file: Optional[str] = None) -> MemorySync:
    """Costruisce il MemorySync dall'ambiente, con default sensati.

    La coda sta accanto al file di memoria: stesso volume, quindi non sparisce a un
    rebuild, ed è il file che si guarda per primo quando si dubita di cosa la
    macchina ha davvero in mano.
    """
    percorso_memoria = Path(memory_file or os.getenv("MEMORY_FILE", "")
                            or "memory.json.gz")
    coda = os.getenv("MEMORY_OUTBOX_FILE", "").strip() or str(
        percorso_memoria.with_name("memory-outbox.jsonl"))
    acceso = ("1", "true", "yes", "on")
    # Senza MEMORY_FILE il percorso è quello di default: accanto all'app, e in un
    # container significa FUORI dal volume. Lì il mirror e la coda si perdono a ogni
    # rebuild — cioè proprio la cosa che questa coda esiste per non far succedere.
    if log and not os.getenv("MEMORY_FILE", "").strip():
        try:
            log("memory_sync",
                "memoria: MEMORY_FILE non impostato — mirror e coda stanno fuori dal volume "
                "e si perdono a ogni ricostruzione del container",
                detail="imposta MEMORY_FILE su un percorso dentro un volume "
                       "(es. /app/memory/memory.json.gz)", status="warn")
        except TypeError:
            log("memory_sync", "memoria: MEMORY_FILE non impostato (mirror e coda fuori volume)")
    return MemorySync(
        client,
        mirror=MemoryMirror(percorso_memoria,
                            ttl_days=int(os.getenv("MEMORY_TTL_DAYS", "7")),
                            max_entries=int(os.getenv("MEMORY_MAX_ENTRIES", "200")))
        if os.getenv("MEMORY_MIRROR", "true").strip().lower() in acceso else None,
        outbox=MemoryOutbox(coda)
        if os.getenv("MEMORY_OUTBOX", "true").strip().lower() in acceso else None,
        read_fallback=os.getenv("MEMORY_READ_FALLBACK", "local"),
        log=log,
        flush_every_s=float(os.getenv("MEMORY_SYNC_FLUSH_S", str(FLUSH_EVERY_S))),
    )


