# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/log.py
# IL LOG E I SUOI TIPI — il primo hub del control-plane.
#
# `push_log` e' la funzione piu' chiamata di main.py: 34 sezioni su 60 la
# usano. Finche' stava li', ogni sezione che si voleva spostare in un modulo
# doveva dichiarare `from main import push_log`, cioe' dipendere dal monolite per
# poter uscire dal monolite. Qui non c'e' piu' quel vincolo.
#
# Nota che `push_log` ricostruisce il tipo sconosciuto in "system" invece di
# alzare: un tipo nuovo che non sta in LOG_TYPES sparisce nel tipo sbagliato e
# non e' piu' filtrabile da /logs?type=. Per questo LOG_TYPES e' qui, e
# tests/test_log_types.py continua a verificare che i tipi usati dalle route
# siano tutti elencati (legge questo modulo attraverso tests/cp_source.py).

import uuid
from datetime import datetime, timezone

import shared.db as db


# ── LOG ───────────────────────────────────────────────────────────────────────
# NB: push_log() riscrive a "system" qualunque tipo fuori da questo insieme. Un
# tipo nuovo che non viene aggiunto qui non fa rumore: sparisce nel tipo
# sbagliato e non e' piu' filtrabile da /logs?type=. tests/test_log_types.py
# estrae i tipi usati dalle route e verifica che siano tutti elencati.
LOG_TYPES = {"connection_test", "inter_node_message", "system", "mesh_event", "memory_sync",
             "feed", "webui_interaction", "dream", "node_chat", "web_task", "mcp", "channel", "instagram",
             "poem",
             # Conversazione fra agenti che scrivono codice (docs/code-conversation.md).
             # Il filo e' il trace_id CONDIVISO fra i messaggi: `push_log` ne genera
             # uno nuovo solo quando non gliene passi uno, quindi basta passarlo.
             # Il codice NON sta qui: sta come artefatto inerte nel Forge, e il log
             # porta il riferimento. Motivo: la vista federata manda `summary`
             # (troncato) e mai `detail` — cosi' il codice non esce verso il peer.
             "code_proposal", "code_review", "code_verdict"}

def push_log(type_, summary, detail="", source="control-plane", target="", status="info", trace_id=""):
    entry = {
        "id":         str(uuid.uuid4()),
        "ts":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type":       type_ if type_ in LOG_TYPES else "system",
        "sourceNode": source,
        "targetNode": target,
        "status":     status,
        "traceId":    trace_id or str(uuid.uuid4())[:8],
        "summary":    summary,
        "detail":     detail,
    }
    db.insert_log(entry)
    return entry
