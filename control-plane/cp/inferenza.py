# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/inferenza.py
# LE CHIAMATE ALL'INFERENZA: al nodo, a Ollama, e al modello nativo.
#
# Tre funzioni e mezzo, 94 righe, e sono il punto in cui il control-plane parla con
# qualcosa che può rispondere lentamente, male, o essere occupato. Quindi non è
# solo "il trasporto": qui si distinguono tre cose che si somigliano e che è
# importante non confondere.
#
# - `_call_ollama` prova i candidati uno dopo l'altro, e distingue un nodo che
#   risponde "occupato" da un nodo che è rotto: il primo è un tentativo perso e
#   si passa al successivo, il secondo è un errore e va detto al client.
# - `_call_ollama_one` fa la chiamata vera e firma con l'identità del CP quando il
#   destinatario è un nodo della mesh. Firma e no-firma NON sono una differenza di
#   stile: i nodi richiedono la firma su quel path, e Ollama diretto non la
#   capirebbe.
# - `_local_model_post` è il post locale, e conta le chiamate che arrivano dai
#   loop in background — sogni, post, sketch — perché quelle non hanno un cliente
#   che aspetta e non devono saturare la GPU mentre una chat è in corso.
#
# Due dipendenze dal boot, entrambe INIETTATE e nessuna delle due riassegnata dopo
# l'avvio, che è la condizione che rende sicuro iniettarle una volta sola:
# `_cp_private_key` (la chiave di firma, impostata alla riga 373 e mai più toccata)
# e `image_memory_gate` (il contatore delle chiamate locali).
#
# Cosa NON c'è:
#
# - Il routing dei nodi. `_call_ollama` riceve già la lista di endpoint da
#   provare; scegliere QUALE nodo è il dominio mesh, e lo fa `mesh/` quando
#   esisterà. Mescolare le due cose diede a entrambe una metà delle responsabilità.
# - Il timeout: `_inference_timeout` sta già in `cp/budget.py`, perché è un
#   budget di richiesta e non un dettaglio del trasporto.
# - Il tool loop e la rotta di `/v1/chat/completions`: sono in `main.py`, e
#   `_stream_gen` è annidata dentro la rotta.

import json

import requests

from cp.budget import _inference_timeout
from cp.log import push_log
from shared.identity import make_request_headers
from shared.persona import audit_reply

# La chiave con cui si firma verso i nodi, e il gate che tiene conto delle
# chiamate locali. Le imposta `collega()`, una volta sola dal boot: nessuna delle
# due cambia dopo, quindi iniettarle qui non può congelare un valore.
_chiave_privata = None
_gate_memoria = None


def collega(chiave_privata, gate_memoria, node_id, pubkey, audit) -> None:
    """Registra le dipendenze di boot. Va chiamata una volta, all'avvio.

    `audit` e' `_audit_persona_reply`, che registra se la risposta rivendica di
    essere umano. Sta qui perche' ha un solo chiamante in tutto il control-plane —
    questa catena — e non perche' appartenga all'inferenza: e' un controllo di
    persona. Il suo corpo e' in fondo al modulo, subito dopo le tre funzioni che lo
    chiamano.

    `NodeBusyError` invece e' qui perche' e' un segnale del protocollo fra CP e
    nodi, non un errore generico: lo solleva un `503 node_busy_timeout` e vuol dire
    "prova il prossimo candidato", laddove ogni altro errore va detto al client.
    Chi lo cattura (`_run_tool_loop`, `v1_chat_completions`) lo importa da qui.
    """
    global _chiave_privata, _gate_memoria, _node_id, _pubkey, _audit
    _chiave_privata = chiave_privata
    _gate_memoria = gate_memoria
    _node_id = node_id
    _pubkey = pubkey
    _audit = audit


def _serve():
    if _chiave_privata is None:
        raise RuntimeError(
            "cp.inferenza non e' collegato: chiama inferenza.collega(chiave, gate) "
            "prima di usare l'inferenza")
    return _chiave_privata, _gate_memoria

# ── il segnale: un nodo occupato non è un nodo rotto ──────────────────────────

class NodeBusyError(Exception):
    """Il nodo ha risposto 503 node_busy_timeout: la sua coda interna è
    rimasta satura oltre il timeout configurato lato nodo. Il chiamante
    prova il prossimo nodo migliore invece di aspettare o fallire subito."""
    def __init__(self, node_id: str, message: str = ""):
        self.node_id = node_id
        super().__init__(message or f"nodo {node_id} occupato (coda satura)")
# ── le chiamate locali, e quelle che arrivano dai loop ────────────────────────

def _local_model_post(url: str, **kwargs):
    """Count background Ollama calls too, including dream and post loops."""
    if not _gate_memoria.enter_chat():
        raise RuntimeError("Il Mac sta completando un'immagine")
    try:
        return requests.post(url, **kwargs)
    finally:
        _gate_memoria.leave_chat()
# ── la chiamata vera, con o senza firma ───────────────────────────────────────

def _call_ollama_one(ollama_base: str, payload: dict, sign: bool = False, node_id: str = "") -> dict:
    """Chiama /v1/chat/completions. Se sign=True (target = un nodo della
    mesh), firma la richiesta con l'identita' ECDSA del CP — il nodo ora
    richiede questa firma su questo path (vedi node/main.py SIGNED_PATHS).
    Se sign=False (target = Ollama diretto, fallback), nessuna firma:
    Ollama non la capirebbe comunque.

    Se il nodo risponde 503 node_busy_timeout (la sua coda interna è rimasta
    satura oltre il timeout), solleva NodeBusyError invece di trattarlo come
    un errore generico: il chiamante (_run_tool_loop / route) può così
    provare il prossimo nodo candidato senza far fallire subito il task."""
    if sign:
        body = json.dumps(payload, sort_keys=True).encode()
        headers = make_request_headers(_node_id, _pubkey, _chiave_privata, body)
        headers["Content-Type"] = "application/json"
        r = requests.post(f"{ollama_base}/v1/chat/completions", data=body, headers=headers,
                          timeout=_inference_timeout(payload.get("model", "")))
    else:
        r = _local_model_post(f"{ollama_base}/v1/chat/completions", json=payload,
                          timeout=_inference_timeout(payload.get("model", "")))

    if r.status_code == 503:
        try:
            err = r.json().get("error", {})
        except Exception:
            err = {}
        if err.get("type") == "node_busy_timeout":
            raise NodeBusyError(node_id, err.get("message", ""))

    raw = r.text.strip()
    if not raw:
        raise ValueError(f"Ollama body vuoto (HTTP {r.status_code})")
    try:
        parsed = r.json()
    except Exception:
        raise ValueError(f"Risposta non-JSON da Ollama (HTTP {r.status_code}): {raw[:200]}")
    # Unico punto da cui passa ogni risposta NON-stream (nodo, ollama-direct,
    # fallback): è qui che l'audit di disclosure vede il testo dell'agente.
    _audit_persona_reply(parsed)
    return parsed
# ── i candidati, uno dopo l'altro ─────────────────────────────────────────────

def _call_ollama(ollama_base, payload: dict, sign: bool = False, node_id: str = "") -> dict:
    """Inoltra a /v1/chat/completions con FALLBACK sugli endpoint diretti.

    `ollama_base` può essere una stringa (un solo endpoint, es. un nodo) o una
    LISTA (endpoint diretti da provare in ordine). Il fallback scatta SOLO su
    errore di rete (connessione/timeout): se un endpoint risponde (anche con un
    HTTP di errore o un body non-JSON) l'errore si propaga e non si prova un
    altro endpoint. Con una lista il fallback è per il caso diretto (sign=False):
    un nodo specifico non va confuso con un altro.
    """
    bases = [ollama_base] if isinstance(ollama_base, str) else list(ollama_base or [])
    last_network_error = None
    for base in bases:
        try:
            return _call_ollama_one(base, payload, sign=sign, node_id=node_id)
        except requests.RequestException as e:
            last_network_error = e
            continue
    if last_network_error is not None:
        raise last_network_error
    return {"error": {"message": "nessun endpoint di inferenza diretto disponibile",
                      "type": "server_error"}}


def _audit_persona_reply(response: dict) -> None:
    """Registra se la RISPOSTA rivendica di essere umano.

    Non blocca e non riscrive nulla: il vincolo sta nel prompt, questo è il
    controllo che lo rende verificabile. Copre il percorso non-stream, da cui
    passa ogni risposta completa; in streaming il CP inoltra i chunk senza
    comporli, quindi lì il controllo non si applica — ed è scritto, non
    sottinteso (vedi docs/persona.md).
    """
    try:
        content = response["choices"][0]["message"].get("content") or ""
    except Exception:
        return
    offese = audit_reply(content)
    if offese:
        push_log('system', 'Persona: la risposta rivendica di essere umano',
                 detail="; ".join(offese)[:160], status='warn')
