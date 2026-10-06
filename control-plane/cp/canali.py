# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/canali.py
# IL CANALE ESTERNO: policy, guard, pacing, runtime.
#
# Una superficie di conversazione che il CP NON può raggiungere da solo: la chat
# di una stanza, i suoi messaggi privati, un bot altrove. Il driver del canale
# tira le decisioni da qui e pubblica l'esito; la policy sui token è fail-closed
# come quella di MCP — senza CHANNEL_CLIENTS non c'è nessun canale servito.
#
# Perché le soglie sono lette con un helper e non come costanti: la tab Setup le
# salva, e un salvataggio deve valere SUBITO (stessa disciplina di persona e
# connettori). Gli strike già contati non si azzerano: `reconfigure`, non un
# guard nuovo.
#
# Qui stanno i quattro oggetti e i due helper perche' sono autosufficienti: qui
# non c'e' niente che legga il routing, la memoria o i tool. Il resto del canale
# (le rotte, il reply, l'ingest) resta in main.py: usa persona_store, image_queue
# e push_log, quindi si sposta solo insieme al context object.

import base64
import binascii
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import requests
from flask import Blueprint, jsonify, request

from cp.assistant import _assistant_text
from cp.budget import _inference_timeout
from cp.config import (DEFAULT_MODEL, VITALITY_BIG_LEVEL, VITALITY_BIG_MODEL)
from cp.inferenza import _call_ollama, _local_model_post
from cp.log import push_log
from cp.memoria import _load_memory, _memory_append
from shared import ollama_native
from shared.channel import (COMANDI_DRIVER, KNOWN_CHANNELS, ChannelGuard,
                            ChannelPolicy, ChannelRuntime, ReplyPacing)
from shared.image_jobs import FAMIGLIA_SDXL, nuovo_job, richiesta_immagine
from shared.image_translation import traduci_scena_immagine
from shared.instagram_intimacy import compagna_context, musa_context
from cp import persona
from shared.persona import audit_reply, build_introduction, should_disclose
from shared.prompt_immagine import prepara_prompt_canale, richiesta_immagine_smart
from shared.showcase import (VIETATI_MINORI, conflitti, negativo_ritratto, prompt_ritratto,
                             richiesta_di_se, verifica_vetrina, vetrina_con_quadro_erotismo,
                             vetrina_dal_documento)
from shared.sister_status import sister_note
from shared.sketch import SKETCH_LATO, SKETCH_PASSI, negativo_sketch
from shared.vitality import mesh_contributors, mesh_vitality, vitality_context

# I comandi che il driver manda come testo e che il canale riconosce. Sono qui e
# non in cp/config.py perché sono lessici del dominio, non configurazione: un
# utente non li cambia dalla tab Setup, li usa parlando.
COMANDI_IMMAGINE = ("!immagine", "!immagine:", "!foto", "!image", "!imagine")
PRESENTAZIONE_COMMANDS = ("!presentati", "!intro")

_CHANNEL_TOKEN_HEADER = "X-Hyperspace-Channel-Token"

# Un blueprint solo per le otto route di stato. Le altre tre
# (`channel_reply`, `channel_vision`, e gli helper che le chiamano)
# restano in main.py finche' non si sposta anche l'inferenza: parlano col
# modello, e tirarle fuori avrebbe significato iniettare `_call_ollama`,
# `_local_model_post` e `ollama_native` — cioe' il dominio chat dentro il
# dominio canali.
_bp = Blueprint("canali", __name__)


def _channel_int(nome: str, default: int) -> int:
    try:
        return int(os.getenv(nome, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _channel_error():
    """Risposta Flask se il chiamante non è un canale autorizzato, altrimenti None."""
    if not channel_policy.enabled:
        return jsonify({"ok": False, "error": "canali disattivati "
                                              "(CHANNEL_ENABLED=false)"}), 503
    if not channel_policy.configured:
        return jsonify({"ok": False, "error": "nessun canale configurato: serve un token "
                                              "di almeno 32 caratteri in CHANNEL_CLIENTS"}), 503
    if not channel_policy.authenticate(request.headers.get(_CHANNEL_TOKEN_HEADER, "")):
        push_log('channel', 'Token di canale assente o non valido',
                 detail=f"from={request.remote_addr}", status='warn')
        return jsonify({"ok": False, "error": "token di canale mancante o non valido"}), 401
    return None


def _channel_name() -> str:
    return (channel_policy.authenticate(request.headers.get(_CHANNEL_TOKEN_HEADER, ""))
            or "")


def _channel_float(nome: str, default: float) -> float:
    try:
        return float(os.getenv(nome, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _insiemi_da_env(nome: str) -> set:
    """Un insieme di nomi, minuscoli, separati da virgola. Vuoto se assente."""
    return {n.strip().lower() for n in os.getenv(nome, "").split(",") if n.strip()}


# ── il contesto del dominio ───────────────────────────────────────────────────
# Sei dipendenze dal boot. Una sola e' un oggetto — `image_queue`, che resta
# condivisa anche dai canali in senso stretto (`_channel_immagine`) e dai loop.
# Le altre cinque sono FUNZIONI che restano in main.py perche' altri domini le
# chiamano, e quindi si iniettano come callable:
#
# - `_node_list` ha 22 chiamanti in tutto il control-plane
# - `_channel_num_ctx` la leggono anche `_genera_post` e `_proponi_identita`
# - `_channel_context_messages` e `_channel_context_chars` le chiama anche
#   `_trascrizione`
# - `_channel_remember` la chiama anche `channel_result`, che resta in main.py
#   perche' parla col modello
#
# `channel_policy`, `channel_guard`, `channel_pacing`, `channel_runtime` e le tre
# liste di nomi NON arrivano qui: sono di questo modulo e li rilegge `ricarica()`.
# Passarli a una funzione che li possiede gia' sarebbe passargli le sue variabili.
_contesto = None


def _serve(*campi):
    if _contesto is None:
        raise RuntimeError(
            "cp.canali non e' montato: chiama canali.monta(app, ...) prima di "
            "usare le route dei canali")
    mancanti = [c for c in campi if getattr(_contesto, c, None) is None]
    if mancanti:
        raise RuntimeError("cp.canali montato senza: " + ", ".join(mancanti))
    return _contesto


def monta(app, *, image_queue=None, context_messages=None, context_chars=None,
          num_ctx=None, channel_remember=None, node_list=None,
          advanced_config=None, sister_peer=None,
          record_conversation=None, nome_persona=None):
    """Registra le dieci route del canale, e tiene i riferimenti al boot.

    Dieci e non otto: le prime otto sono quelle che il driver chiama senza passare
    dal modello — stato, comandi, ingestion, consegna. Le ultime due, `vision` e
    `reply`, parlano con l'inferenza, e sono arrivate quando `cp/inferenza.py` ha reso
    possibile iniettare quella dipendenza senza portarsi dietro metà di main.py.

    Dieci dipendenze, di cui sette sono funzioni che restano in main.py perché altri
    domini le chiamano: `_node_list` ha ventitré chiamanti in tutto il control-plane,
    `_channel_num_ctx` la leggono anche i loop che scrivono un post, e
    `_record_conversation` e `_nome_persona` le usa anche il dominio Instagram. Sono
    iniezioni e non spostamenti, e la differenza è che spostarle avrebbe reso questi
    moduli dipendenti da main.py per avere una riga di codice.
    """
    global _contesto
    _contesto = SimpleNamespace(
        image_queue=image_queue,
        channel_context_messages=context_messages,
        channel_context_chars=context_chars,
        channel_num_ctx=num_ctx,
        channel_remember=channel_remember,
        node_list=node_list,
        # Le rotte che parlano col modello e le funzioni che le servono.
        advanced_config=advanced_config,
        sister_peer=sister_peer,
        record_conversation=record_conversation,
        nome_persona=nome_persona,
    )
    app.register_blueprint(_bp)
    return app


def smonta():
    """Dimentica il contesto. Serve ai test, che aprono piu' montaggi in sequenza."""
    global _contesto
    _contesto = None


channel_policy = ChannelPolicy.from_env()
# Le tre liste di nomi hanno la stessa vita di policy e pacing: le rilegge
# `ricarica()` dopo un salvataggio in tab Setup.
# Il modello della stanza. Anche questo e' rillegibile a caldo dalla tab Setup, e
# per lo stesso motivo delle tre liste non puo' stare in main.py: se lo riscrivesse
# li' con `global`, `_channel_model()` — che gira qui — continuerebbe a usare il
# vecchio, e il salvataggio sarebbe "salvato ma inerte". `imposta_modello()` e' il
# modo che funziona, e l'unico.
CHANNEL_MODEL = os.getenv("CHANNEL_MODEL", "").strip()
CHANNEL_INGEST_MAX_EVENTS = _channel_int("CHANNEL_INGEST_MAX_EVENTS", 100)
# Il tetto di token della stanza: e' del canale, quindi sta qui e non in
# cp/config.py. Non e' rillegibile a caldo dalla tab Setup, quindi un semplice
# valore letto all'import basta — se un giorno lo diventa, va in ricarica().
CHANNEL_MAX_TOKENS = _channel_int("CHANNEL_MAX_TOKENS", 160)


def imposta_modello(valore: str) -> None:
    """Il modello della stanza, dopo un salvataggio in tab Setup.

    Vuoto = modello di default del control-plane (vedi `_channel_model`).
    """
    global CHANNEL_MODEL
    CHANNEL_MODEL = str(valore or "").strip()


CHANNEL_OPERATOR = _insiemi_da_env("CHANNEL_OPERATOR")
CHANNEL_VIP = _insiemi_da_env("CHANNEL_VIP")
CHANNEL_CERCHIA = _insiemi_da_env("CHANNEL_CERCHIA")
channel_guard = ChannelGuard(flood_max=_channel_int("CHANNEL_FLOOD_MAX", 6),
                             flood_window_s=_channel_float("CHANNEL_FLOOD_WINDOW_S", 15.0),
                             strike_mute=_channel_int("CHANNEL_STRIKE_MUTE", 2),
                             strike_ban=_channel_int("CHANNEL_STRIKE_BAN", 3))
channel_pacing = ReplyPacing(
    min_interval_s=_channel_float("CHANNEL_MIN_REPLY_INTERVAL_S", 25.0),
    batch_max_age_s=_channel_float("CHANNEL_BATCH_MAX_AGE_S", 6.0),
    batch_max_messages=_channel_int("CHANNEL_BATCH_MAX_MESSAGES", 6),
    probability=_channel_float("CHANNEL_REPLY_PROBABILITY", 1.0),
)
# Stato vivo del driver e comandi dell'operatore: il driver li ritira al giro
# successivo (nessuna porta aperta sulla macchina col browser).
channel_runtime = ChannelRuntime()


def ricarica() -> tuple:
    """Rilegge policy e pacing dall'env, dopo un salvataggio in tab Setup.

    Perché sta QUI e non in main.py: i due oggetti sono di questo modulo. Se
    main.py li riassegnasse con `global`, avrebbe una copia tutta sua — già
    aggiornata — mentre qui resterebbe quella vecchia: due oggetti con lo stesso
    nome e valori diversi. Oggi ogni lettore è in main.py e quindi non se ne
    accorgerebbe, ma il primo lettore che entra in un modulo leggerebbe la copia
    vecchia. La tab Setup esiste per non avere il "salvato ma inerte".

    Ritorna i cinque valori perché il chiamante deve rilegarseli: il `global` qui
    dentro aggiorna questo modulo, non il namespace di chi chiama.

    Le tre liste di nomi (`CHANNEL_OPERATOR`, `CHANNEL_VIP`, `CHANNEL_CERCHIA`)
    erano dichiarate `global` in `_reload_channel_config`, in main.py, e sono qui
    per lo stesso motivo dei due oggetti: main.py le riassegnava per conto suo e
    questo modulo avrebbe tenuto la copia vecchia. Erano il caso più subdolo dei
    tre, perché sono `set` e un `set` riassegnato in-place sembra funzionare.
    """
    global channel_policy, channel_pacing
    global CHANNEL_OPERATOR, CHANNEL_VIP, CHANNEL_CERCHIA, CHANNEL_MODEL
    channel_policy = ChannelPolicy.from_env()
    channel_pacing = ReplyPacing(
        min_interval_s=_channel_float("CHANNEL_MIN_REPLY_INTERVAL_S", 25.0),
        batch_max_age_s=_channel_float("CHANNEL_BATCH_MAX_AGE_S", 6.0),
        batch_max_messages=_channel_int("CHANNEL_BATCH_MAX_MESSAGES", 6),
        probability=_channel_float("CHANNEL_REPLY_PROBABILITY", 1.0),
    )
    CHANNEL_OPERATOR = _insiemi_da_env("CHANNEL_OPERATOR")
    CHANNEL_VIP = _insiemi_da_env("CHANNEL_VIP")
    CHANNEL_CERCHIA = _insiemi_da_env("CHANNEL_CERCHIA")
    CHANNEL_MODEL = os.getenv("CHANNEL_MODEL", "").strip()
    return (channel_policy, channel_pacing, CHANNEL_OPERATOR,
            CHANNEL_VIP, CHANNEL_CERCHIA, CHANNEL_MODEL)


# ── stato: quello che l'operatore guarda per capire perche' il bot tace ───────

@_bp.route('/channels')
def channels_overview():
    """Stato per piattaforma per la scheda Social: catalogo + token + driver.
    _serve("node_list")

    Nessun segreto: i token non escono mai. Solo configurato sì/no, superfici
    supportate e l'ultima fotografia riportata dal driver.
    """
    runtime = channel_runtime.describe()
    configured = set(channel_policy.clients)
    voci = []
    for voce in KNOWN_CHANNELS:
        chiave = voce["key"]
        stato = runtime.get(chiave, {})
        voci.append({
            "key": chiave,
            "label": voce["label"],
            "icon": voce["icon"],
            "auth": voce["auth"],
            "surfaces": list(voce["surfaces"]),
            "hint": voce["hint"],
            "first_class": bool(voce.get("first_class")),
            "configured": chiave in configured,
            "driver": stato.get("state") or None,
            "pending_commands": stato.get("pending_commands", 0),
        })
    return jsonify({"ok": True, "enabled": channel_policy.enabled,
                    "operator_configured": bool(CHANNEL_OPERATOR),
                    "vip_configured": bool(CHANNEL_VIP),
                    "cerchia_configured": bool(CHANNEL_CERCHIA),
                    "vitality": mesh_vitality(_contesto.node_list()),
                    "contributors": mesh_contributors(_contesto.node_list()),
                    "channels": voci})

@_bp.route('/channel/status')
def channel_status():
    """Stato dei canali: quali token esistono, quanto spam, quali azioni.

    Nessun segreto (nomi, contatori, motivi) e in sola lettura, come /connectors
    e /mcp/status: serve all'operatore per rispondere a "perché il bot non ha
    risposto a quella persona?" senza aprire i log.
    """
    return jsonify({
        "ok": True,
        "policy": channel_policy.describe(),
        "guard": channel_guard.snapshot(),
        # Ondata in corso per canale: l'operatore la vuole vedere qui, non solo
        # dentro la risposta all'ingest.
        "waves": {nome: channel_guard.spam_wave(nome)
                  for nome in sorted(channel_policy.clients)},
        "pacing": {"min_interval_s": channel_pacing.min_interval_s,
                   "batch_max_age_s": channel_pacing.batch_max_age_s,
                   "batch_max_messages": channel_pacing.batch_max_messages,
                   "probability": channel_pacing.probability},
        # Stato riportato dai driver e comandi in attesa: è quello che rende
        # possibile rispondere dal terminale a "che modo ha?" e "perché tace?".
        "runtime": channel_runtime.describe(),
        "commands_available": sorted(COMANDI_DRIVER),
        "model": CHANNEL_MODEL or DEFAULT_MODEL,
        "max_tokens": CHANNEL_MAX_TOKENS,
        "context": {"messages": _contesto.channel_context_messages(),
                    "chars": _contesto.channel_context_chars(),
                    "num_ctx": _contesto.channel_num_ctx()},
    })

@_bp.route('/channel/commands', methods=['GET', 'POST'])
def channel_commands():
    """I comandi dell'operatore (POST) e la loro consegna al driver (GET).

    La direzione è quella di tutto il resto: il driver tira, l'operatore deposita.
    Un comando deposto e mai ritirato scade da solo (TTL): eseguire "metti in
    pausa" tre ore dopo sarebbe peggio che non eseguirlo.
    """
    errore = _channel_error()
    if errore:
        return errore
    canale = _channel_name()
    if request.method == 'GET':
        comandi = channel_runtime.pending(canale, drain=True)
        return jsonify({"ok": True, "channel": canale, "commands": comandi,
                        "available": sorted(COMANDI_DRIVER)})
    data = request.get_json(force=True, silent=True) or {}
    try:
        comando = channel_runtime.queue(canale, str(data.get("command", "")),
                                        note=str(data.get("note", "")),
                                        source=str(data.get("source", "cli"))[:64])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200],
                        "available": sorted(COMANDI_DRIVER)}), 400
    push_log('channel', f"{canale}: comando in coda -> {comando['command']}",
             detail=str(data.get("note", ""))[:120], source=f"channel:{canale}",
             status='info')
    return jsonify({"ok": True, "channel": canale, "queued": comando})

@_bp.route('/channel/state', methods=['POST'])
def channel_state():
    """La fotografia del driver: che modo ha, se è attivo, a che ritmo va.

    Esiste per rispondere dal terminale a "perché non risponde?" senza aprire i
    log né il browser. Il driver la manda quando qualcosa cambia (o ogni tanto),
    non a ogni giro: un report al secondo sarebbe rumore.
    """
    errore = _channel_error()
    if errore:
        return errore
    canale = _channel_name()
    data = request.get_json(force=True, silent=True) or {}
    stato = channel_runtime.report(canale, data if isinstance(data, dict) else {})
    push_log('channel', f"{canale}: stato del driver",
             detail=json.dumps({k: v for k, v in stato.items() if k != "ts"},
                               ensure_ascii=False)[:200],
             source=f"channel:{canale}", status='info')
    return jsonify({"ok": True, "channel": canale, "state": stato})

# ── ingest e consegna: quello che il driver tira a ogni giro ──────────────────

@_bp.route('/channel/ingest', methods=['POST'])
def channel_ingest():
    """Eventi dalla superficie di un canale: messaggi, privati, tip, ingressi.

    Il verdetto (ok/spam) e l'eventuale azione di moderazione li decide il CP; il
    driver esegue e riferisce con /channel/result. Un log per BATCH e non per
    messaggio: una stanza attiva scriverebbe centinaia di righe al minuto, e i
    log diventerebbero inutili proprio nel momento in cui servono.
    """
    errore = _channel_error()
    if errore:
        return errore
    canale = _channel_name()
    data = request.get_json(force=True, silent=True) or {}
    superficie = str(data.get("surface", "chat")).strip().lower() or "chat"
    eventi = data.get("events")
    if not isinstance(eventi, list) or not eventi:
        return jsonify({"ok": False, "error": "events mancante o vuoto"}), 400

    risultati, azioni, spam = [], [], 0
    for evento in eventi[:CHANNEL_INGEST_MAX_EVENTS]:
        if not isinstance(evento, dict):
            continue
        autore = str(evento.get("author", ""))[:64]
        tipo_evento = str(evento.get("kind", "message")).strip().lower() or "message"
        if tipo_evento == "tip":
            # Un tip non è un messaggio da classificare: si registra (serve alla
            # nota di ringraziamento dentro la prossima risposta) e si ricorda.
            # Non entra nella coda delle risposte e non conta come traffico.
            importo = evento.get("amount")
            channel_guard.registra_tip(channel=canale, author=autore, importo=importo)
            _contesto.channel_remember(canale, f"tip:{autore}", "tip",
                              f"{autore} ha donato {importo if importo else 'un tip'}",
                              surface=superficie)
            risultati.append({"author": autore, "kind": "tip", "verdict": "ok",
                              "reasons": [], "strikes": 0, "action": None,
                              "key": str(evento.get("key", ""))[:64]})
            continue
        esito = channel_guard.observe(channel=canale, surface=superficie,
                                      author=autore,
                                      text=str(evento.get("text", ""))[:1000])
        esito["key"] = str(evento.get("key", ""))[:64]
        esito["kind"] = tipo_evento
        if esito["verdict"] == "spam":
            spam += 1
        if esito["action"]:
            azioni.append(esito["action"])
        risultati.append(esito)

    # Un'ondata è un fatto della stanza degno di memoria — UNA riga (debounce),
    # non una per messaggio: la condizione resta vera per minuti.
    ondata = channel_guard.spam_wave(canale)
    if ondata and _contesto.channel_remember(canale, "spam_wave", "spam_wave",
                                    f"ondata di spam: {ondata['count']} messaggi sospetti "
                                    f"in {int(ondata['window_s'] / 60)} minuti",
                                    surface=superficie):
        push_log('channel', f"{canale}: ondata di spam registrata in memoria",
                 detail=f"count={ondata['count']}", source=f"channel:{canale}",
                 status='warn')

    motivi = sorted({m for r in risultati for m in r["reasons"]})
    push_log('channel', f"{canale}/{superficie}: {len(risultati)} eventi",
             detail=f"spam={spam} motivi={','.join(motivi) or '-'} azioni={len(azioni)}",
             source=f"channel:{canale}", status='warn' if spam else 'info')
    for azione in azioni:
        push_log('channel', f"{canale}: {azione['action']} su {azione['user']}",
                 detail=f"motivo={azione['reason']}", source=f"channel:{canale}",
                 status='warn')
    return jsonify({"ok": True, "channel": canale, "surface": superficie,
                    "accepted": len(risultati) - spam, "spam": spam,
                    "results": risultati, "actions": azioni,
                    "wave": channel_guard.spam_wave(canale),
                    "guard": channel_guard.snapshot(canale)})

@_bp.route('/channel/result', methods=['POST'])
def channel_result():
    """Esito dell'azione eseguita dal driver: chiude il ciclo e alimenta i log.

    Un selettore non trovato è un guasto del DRIVER, non del modello: tenerli
    distinti è ciò che permette di capire se si è rotto il DOM della piattaforma
    o il ragionamento dell'agente.
    """
    errore = _channel_error()
    if errore:
        return errore
    canale = _channel_name()
    data = request.get_json(force=True, silent=True) or {}
    tipo = str(data.get("kind", "reply")).strip().lower() or "reply"
    ok = bool(data.get("ok"))
    target = str(data.get("target", ""))[:64]
    if tipo == "reply" and ok:
        channel_pacing.note_reply(canale)
    if tipo == "moderate" and ok and target:
        # Una moderazione riuscita è un fatto della stanza: in memoria, così
        # domani l'agente sa che quella persona era già stata espulsa.
        _contesto.channel_remember(canale, f"mod:{target}", "moderation",
                          f"moderazione su {target} dopo ripetute violazioni")
    push_log('channel', f"{canale}: {tipo} {'eseguita' if ok else 'FALLITA'}",
             detail=f"target={target} "
                    f"err={str(data.get('error', ''))[:80]} "
                    f"ms={data.get('duration_ms')}",
             source=f"channel:{canale}", status='success' if ok else 'warn')
    return jsonify({"ok": True, "channel": canale})

@_bp.route('/channel/outbox')
def channel_outbox():
    """Le immagini pronte da consegnare a QUESTO canale (il driver le tira).
    _serve("image_queue")

    Perché esiste: il control-plane non ha il file e non sa parlare con la
    piattaforma; il driver ha entrambi. Quindi anche la consegna è una cosa che il
    driver *tira* — come le decisioni — invece di una porta che si apre.
    """
    errore = _channel_error()
    if errore:
        return errore
    return jsonify({"ok": True, "channel": _channel_name(),
                    "messages": _contesto.image_queue.da_consegnare(_channel_name())})

@_bp.route('/channel/outbox/ack', methods=['POST'])
def channel_outbox_ack():
    """Il driver dichiara consegnata un'immagine: senza l'ack si ripeterebbe."""
    errore = _channel_error()
    if errore:
        return errore
    dati = request.get_json(silent=True) or {}
    ok = _contesto.image_queue.consegnato(str(dati.get("id", "")))
    if ok:
        push_log('channel', f"{_channel_name()}: immagine consegnata",
                 detail=f"id={dati.get('id')}", source=f"channel:{_channel_name()}",
                 status='success')
    return jsonify({"ok": ok}), (200 if ok else 404)

# ── modello e contesto ────────────────────────────────────────────────────────
def _channel_model(vitalita: dict) -> str:
    """Modello del canale in base alla vitalità della mesh."""
    if VITALITY_BIG_MODEL and int(vitalita.get("level", 0)) >= VITALITY_BIG_LEVEL:
        return VITALITY_BIG_MODEL
    return CHANNEL_MODEL or DEFAULT_MODEL


def _trascrizione(context) -> str:
    """Trascrizione compatta: ultimi N messaggi, ognuno troncato.

    N e lunghezza si leggono a chiamata (`CHANNEL_CONTEXT_MESSAGES` /
    `CHANNEL_CONTEXT_CHARS`): sono le due manopole che si toccano quando il bot
    "non ricorda" cosa si è detto due battute fa.
    """
    righe = []
    nome_bot = (persona.profilo().name or "").strip().lower()
    for evento in list(context)[-_contesto.channel_context_messages():]:
        autore = str(evento.get("author", "")).strip()[:40] or "anonimo"
        testo = " ".join(str(evento.get("text", "")).split())[:_contesto.channel_context_chars()]
        if not testo:
            continue
        if CHANNEL_OPERATOR and autore.lower() in CHANNEL_OPERATOR:
            etichetta = "(io)"
        elif nome_bot and autore.lower() == nome_bot:
            etichetta = f"({nome_bot})"
        else:
            etichetta = f"({autore})"
        righe.append(f"{etichetta} {testo}")
    return "\n".join(righe)
# ── i ricordi: cosa il canale sa di se' ───────────────────────────────────────
def _channel_memories(channel: str, limit: int = 5) -> list:
    """Ultimi ricordi di questo canale (i più recenti in coda).

    Usa `_load_memory()`, lo stesso percorso di /memory: si filtra solo per
    canale, senza ricerca full-text, perché il prompt di una battuta non deve
    dipendere dalla disponibilità di un backend di ricerca.
    """
    try:
        voci = [e for e in _load_memory()
                if isinstance(e, dict) and str(e.get("channel", "")) == channel]
    except Exception:
        return []
    return [str(e.get("content", ""))[:160] for e in voci[-max(0, int(limit)):]
            if e.get("content")]


def _channel_remember(channel: str, key: str, kind: str, text: str, *,
                      surface: str = "", **extra) -> bool:
    """Scrive UN fatto della stanza nella memoria condivisa, con debounce.

    In memoria NON va ogni messaggio: ci vanno le cose che ha senso ricordare
    domani — un'ondata di spam, un'azione di moderazione, un tip. Il debounce
    per chiave evita che lo stesso fatto venga riscritto in loop mentre la
    condizione resta vera (un'ondata dura dieci minuti: una riga, non cento).
    """
    if not channel_guard.should_remember(key=f"{channel}:{key}"):
        return False
    entry = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "type": "channel", "channel": channel, "kind": kind,
             "content": f"[{channel}] {text}", "source": f"channel:{channel}",
             "surface": f"channel:{channel}" + (f":{surface}" if surface else "")}
    entry.update(extra)
    try:
        _memory_append(entry)
    except Exception as e:
        push_log('channel', 'Memoria non aggiornata', str(e)[:120],
                 source=f"channel:{channel}", status='warn')
        return False
    return True
# ── come si presenta e chi è la sorella ───────────────────────────────────────
def _channel_presentazione(context) -> str | None:
    """Auto-presentazione richiesta con `!presentati`/`!intro` nell'ultimo messaggio.

    Generata dalla persona (non dal modello): sempre fattuale e disclosure-safe.
    """
    ultimo = str((context[-1] if context else {}).get("text", "")).strip()
    testo = " ".join(ultimo.split()).lower()
    if any(testo == c or testo.startswith(c + " ") for c in PRESENTAZIONE_COMMANDS):
        return build_introduction(persona.profilo())
    return None


def _sister_note() -> str:
    """Riga di contesto quando la sorella Aurora non è disponibile."""
    return sister_note(_contesto.sister_peer())
# ── l'immagine: il ritratto e la coda creator ─────────────────────────────────
def _channel_immagine(context, *, channel: str, destinazione: str = "",
                      surface: str = "") -> str | None:
    """Un'immagine chiesta dalla stanza: `!immagine <idea>` oppure **a parole**.

    Comandi espliciti e richieste naturali condividono la stessa guardia:

    - `!immagine` è sintassi esplicita: chi sbaglia il comando se ne accorge. Con
      `CHANNEL_OPERATOR` o `CHANNEL_CERCHIA` configurate filtra chi chiede; senza
      quelle variabili resta aperto a chiunque sia in chat (scelta dichiarata in
      docs/comfyui.md).
    - la richiesta **a parole** usa regex e modello per conservare soggetto e
      stile, con identità e cronologia recente. Con una delle due liste impostata
      si accettano solo gli autori configurati; altrimenti resta aperta.

    La risposta non promette mai un'immagine già mandata: dice che è in coda e che
    arriva. L'immagine la consegna il driver, e solo dopo è vera.

    Da qui passa anche il **livello esplicito** (2026-10-01): una richiesta di nudità
    o di esplicito, se l'autore è il creatore (`CHANNEL_OPERATOR`) o uno delle muse
    che lui dichiara (`CHANNEL_CERCHIA`) e il documento dichiara
    `consenti_erotismo_esplicito_creatore`, non viene neutralizzata dal negativo. Il
    quadro (`adult`, `virtual`) che `shared/showcase.py` pretende per accendere il
    livello lo **scrive il sistema** (`vetrina_con_quadro_erotismo`) e lo dice nella
    risposta: chi ha il livello non deve conoscere due parole d'ordine. Per chiunque
    altro nulla cambia; i minori non passano per nessuno.
    """
    ultimo = str((context[-1] if context else {}).get("text", "")).strip()
    autore = str((context[-1] if context else {}).get("author", "")).strip().lower()
    pezzi = ultimo.split(" ", 1)
    comando = pezzi[0].lower().rstrip(":") if pezzi else ""
    regola = ""
    aggiunte = []            # le parole del quadro che il sistema ha scritto da sé
    # Chi chiede: l'operatore e le muse che lui dichiara. Basta una delle due liste
    # configurata per filtrare (era il mestiere di `CHANNEL_OPERATOR` da sola); con
    # entrambe vuote la richiesta resta aperta a chi e' in chat (scelta dichiarata in
    # docs/comfyui.md).
    chiedenti = CHANNEL_OPERATOR | CHANNEL_VIP | CHANNEL_CERCHIA
    if f"!{comando.lstrip('!')}" in COMANDI_IMMAGINE:
        if chiedenti and autore not in chiedenti:
            return ("Le immagini le chiede chi mi ha costruita: non posso mettere in coda "
                    "una richiesta di chiunque, la scheda è una sola.")
        idea = pezzi[1].strip() if len(pezzi) > 1 else ""
    else:
        if chiedenti and autore not in chiedenti:
            return None          # fuori dalla banda intima: silenzio, parla la stanza
        # Richiesta a parole ("fammi un disegno di X"): senza nessuna delle due liste
        # resta aperta come il comando esplicito. Una frase non riconosciuta
        # torna None: parla la stanza.
        # Se la regola deterministica ha gia' capito la richiesta, il testo resta
        # quello scritto dalla persona. Il riscrittore LLM aveva trasformato una
        # richiesta esplicita del creatore in "artistic and non-explicit style":
        # il negativo era aperto correttamente, ma il positivo la censurava di nuovo.
        richiesta = richiesta_immagine(ultimo)
        if richiesta is None:
            richiesta = richiesta_immagine_smart(
                ultimo, contesto=context[:-1], identita=persona.persona().system_block())
        if richiesta is None:
            return None
        idea = richiesta["idea"]
        regola = richiesta["regola"]
    if not idea:
        return "Dimmi cosa disegnare, così: `!immagine una torre al tramonto`."
    # I comandi espliciti restano prompt diretti. Le richieste naturali di
    # disegno ricevono lo stile del diario, salvo una tecnica già specificata.
    if regola:
        idea = prepara_prompt_canale(idea, ultimo)
    # Un canale può chiedere sia un soggetto qualsiasi sia un'immagine di Anna.
    # Il primo resta uno sketch leggero; il secondo è una rappresentazione di sé e
    # deve passare dalla sua vetrina, altrimenti Telegram la disegnerebbe con il
    # checkpoint generico e perderebbe il volto canonico.
    nome_persona = _contesto.nome_persona()
    testo_richiesta = f"{idea} {ultimo}".lower()
    richiesta_di_anna = richiesta_di_se(idea, ultimo, nome_persona)
    # Il livello esplicito è di chi l'ha costruita **e delle muse che lui dichiara**:
    # la variabile deve essere configurata perché il livello si accenda. Con le due liste
    # vuote "aperto a chiunque" vale per le immagini normali, non per questa — un permesso
    # si dà a qualcuno, e senza qualcuno non c'è a chi darlo.
    livello_creatore = bool(autore) and autore in CHANNEL_OPERATOR
    livello_musa = bool(autore) and autore in CHANNEL_CERCHIA
    livello_vip = bool(autore) and autore in CHANNEL_VIP
    livello_esplicito = livello_creatore or livello_musa
    livello_privato = livello_esplicito or livello_vip
    # In privato col creatore il soggetto puo' restare sottinteso: "nuda, figura
    # intera" e' una continuazione naturale di "mandami una foto", non la richiesta
    # di una donna anonima. Prima cadeva nello sketch generico: niente riferimento
    # di Anna e, peggio, `negativo_sketch()` conteneva nude/nsfw/erotic. Manteniamo
    # stretta l'ellissi: vale solo in PM, per il livello privato e per descrizioni
    # del corpo/inquadratura senza un altro soggetto dichiarato.
    descrizione_di_se_implicita = bool(re.search(
        r"\b(?:nud\w*|naked|senza vestiti|lingerie|intimo|sexy|glamour|figura intera|full[- ]body|"
        r"mezzo busto|primo piano|close[- ]up)\b", testo_richiesta, re.IGNORECASE))
    altro_soggetto = bool(re.search(
        r"^\s*(?:di\s+)?(?:un|uno|una|il|lo|la|i|gli|le|del|dello|della)\s+"
        r"(?!te\b|anna\b)", str(idea), re.IGNORECASE))
    if (not richiesta_di_anna and str(surface).lower() == "pm" and livello_privato
            and descrizione_di_se_implicita and not altro_soggetto):
        richiesta_di_anna = True
    # I minori non passano da nessuna porta, nemmeno da questa: `!immagine` è il punto
    # in cui una frase di una stanza diventa un prompt per il diffusion, e una richiesta
    # che chiede un soggetto minorenne non si accoda e non si "riduce". Vale per
    # chiunque, creatore compreso, ed è l'unico controllo che parla prima di sapere di
    # che immagine si tratta.
    if conflitti(testo_richiesta, VIETATI_MINORI):
        return "Questa no: non disegno soggetti minorenni, mai e per nessuno."
    richiesta_nuda_o_esplicita = bool(re.search(
        r"\b(?:nud\w*|naked|senza vestiti|topless|masturb\w*|sesso|sex|esplicit\w*)\b",
        testo_richiesta, re.IGNORECASE))
    if livello_vip and richiesta_nuda_o_esplicita:
        return ("Per la cerchia VIP posso fare foto glamour e lingerie sexy, non nudo "
                "o erotismo esplicito. Quel livello è riservato alle MUSA.")
    try:
        if richiesta_di_anna:
            sezioni = getattr(persona.profilo(), "sezioni", {}) or {}
            vetrina = vetrina_dal_documento({"vetrina": sezioni.get("vetrina", {})})
            # La richiesta **è** la scena di questa generazione: il livello si legge lì,
            # mentre per chiunque altro la vetrina resta quella dichiarata e il negativo
            # non cambia di una virgola (una scena di estraneo non deve poter accendere
            # il livello artistico del documento).
            variante = {**vetrina, "scena": idea} if livello_privato else vetrina
            scena_prompt = traduci_scena_immagine(idea)
            richiesta_nudo = False
            if livello_vip and re.search(r"\b(?:lingerie|intimo|sexy|glamour)\b",
                                         testo_richiesta, re.IGNORECASE):
                scena_prompt = (
                    "(solo:1.3), single woman, glamorous lingerie editorial, "
                    "elegant sensual pose, fully clothed intimate apparel. " + scena_prompt)
            if livello_esplicito:
                # Il quadro lo scrive il sistema, non chi chiede: `adult` e `virtual`
                # sono cose che il documento sa già (il livello è dichiarato, il corpo è
                # dichiaratamente virtuale), e chiederle era attrito travestito da
                # controllo — il 2026-10-01 il creatore si è visto rifiutare la sua
                # richiesta per una parola che il sistema conosceva già. Le parole
                # aggiunte tornano indietro e si dicono.
                variante, aggiunte = vetrina_con_quadro_erotismo(variante)
                scena_prompt = traduci_scena_immagine(variante["scena"])
                # SD 1.5 privilegia l'inizio della finestra CLIP. La vecchia ricetta
                # metteva prima ~60 token di stile e lasciava "nuda figura intera"
                # in fondo: il modello vedeva il volto/riferimento ma non la scena.
                # Le intenzioni visive note vengono duplicate in tag inglesi brevi
                # e portate davanti, senza affidarle a un riscrittore LLM.
                priorita_visive = []
                if re.search(r"\b(?:nud\w*|naked|senza vestiti)\b",
                             testo_richiesta, re.IGNORECASE):
                    richiesta_nudo = True
                    priorita_visive.append(
                        "(solo:1.3), single woman, one person, "
                        "(adult virtual nude:1.4), (fully naked:1.35), no clothing")
                    scena_prompt = re.sub(r"\badult\s+virtual\b\s*,?", "",
                                          scena_prompt, flags=re.IGNORECASE).strip()
                if re.search(r"\b(?:figura intera|full[- ]body|head to toe)\b",
                             testo_richiesta, re.IGNORECASE):
                    priorita_visive.append(
                        "(full body:1.3), head to toe, entire figure visible, centered")
                if priorita_visive:
                    scena_prompt = ", ".join(priorita_visive) + ". " + scena_prompt
                # La richiesta passa anche dalla verifica, come un ritratto da riga di
                # comando: se il documento non dichiara il livello, o se resta qualcosa
                # d'altro che non va (i minori, la figura senza marcatori), si dice —
                # accodare e basta darebbe un'immagine che il negativo rende castigata
                # senza che nessuno sappia perché.
                problemi = verifica_vetrina(variante,
                                            {"vetrina": sezioni.get("vetrina", {})},
                                            creatore=True)
                if problemi:
                    return "Questa non te la disegno: " + "; ".join(problemi) + "."
            # Il seed dichiarato e' l'ancora del volto, ma con IP-Adapter attivo non
            # deve diventare l'identificatore immutabile di ogni foto privata. Prompt
            # e seed identici producevano davvero lo stesso JPEG a ogni richiesta.
            # Per il livello privato varia il rumore iniziale; il riferimento continua
            # a tenere il volto. Per il pubblico resta il seed canonico della vetrina.
            seed_ritratto = ((uuid.uuid4().int & ((1 << 63) - 1))
                              if livello_privato else vetrina["seed"])
            negativo = negativo_ritratto(variante, creatore=livello_esplicito)
            if richiesta_nudo:
                # Il riferimento canonico mostra una salopette: IP-Adapter può copiarla
                # anche quando il testo chiede il contrario. Per questa sola scena
                # abbassiamo il peso del riferimento (che deve conservare il volto, non
                # l'abito) e rendiamo esplicito al sampler il conflitto da evitare.
                negativo = (
                    "multiple people, two women, twins, duplicate person, split screen, "
                    "diptych, clothes, clothing, dress, shirt, bra, lingerie, underwear, "
                    + negativo)
            accodato = _contesto.image_queue.accoda(nuovo_job(
                prompt_ritratto(vetrina, scena=scena_prompt,
                                scena_prima=livello_privato),
                negativo=negativo,
                larghezza=vetrina["larghezza"], altezza=vetrina["altezza"],
                passi=vetrina["passi"], fix=vetrina["fix"], seed=seed_ritratto,
                richiedente=autore, canale=channel, famiglia=vetrina["famiglia"],
                modello=vetrina["modello"], destinazione=destinazione,
                reference_image=vetrina["riferimento"],
                reference_strength=(min(vetrina["riferimento_forza"], 0.6)
                                    if richiesta_nudo else vetrina["riferimento_forza"])))
        else:
            # RealVisXL sul Mac: la famiglia conserva il nome storico sdxl-turbo.
            accodato = _contesto.image_queue.accoda(nuovo_job(
                idea,
                negativo=negativo_sketch(),
                larghezza=SKETCH_LATO, altezza=SKETCH_LATO, passi=SKETCH_PASSI,
                richiedente=autore, canale=channel,
                famiglia=FAMIGLIA_SDXL,
                destinazione=destinazione))
    except ValueError:
        return "Un'immagine senza descrizione non esiste: scrivi cosa disegnare."
    except RuntimeError as e:
        return f"Non posso adesso: {e}."
    push_log('channel', f"{channel}: richiesta immagine",
             detail=f"id={accodato['id']} da={autore or '?'} "
                    f"via={regola or 'comando'} famiglia={accodato['famiglia']} "
                    + (f"quadro={','.join(aggiunte)} " if aggiunte else "")
                    + f"modello={accodato['modello_effettivo']} prompt={accodato['prompt']}",
             source=f"channel:{channel}", status='info')
    # Le parole del quadro scritte dal sistema si dicono: una cosa fatta al posto tuo e
    # taciuta è la cosa che questo livello esiste per togliere.
    nota = (" Il quadro (`" + "`, `".join(aggiunte) + "`) l'ho scritto io: senza, il "
            "modello disegnerebbe un'altra cosa." if aggiunte else "")
    if not destinazione:
        return ("L'ho messa in coda, ma non so dove mandartela: chiedila dalla chat "
                "(nel canale il driver manda l'id della conversazione)." + nota)
    return "Ok! Mi metto subito al lavoro: appena è pronta te la mando qui." + nota
# ── il ritmo: perché il bot tace, detto al massimo una volta al minuto
_PACING_LOG_AT: dict = {}
PACING_LOG_EVERY_S = 60.0


def _log_pacing_reason(channel: str, decisione: dict) -> bool:
    """Scrive il motivo di un silenzio, al massimo una volta al minuto.

    Il motivo sta nel **messaggio**, non solo nel campo detail: `hs.py logs` mostra
    il messaggio, e "risposta non inviata (wait)" senza il perché lascerebbe la
    domanda dov'era. Il detail resta per la ricerca.
    """
    chiave = f"{channel}:{decisione.get('action', '')}"
    adesso = time.time()
    if adesso - _PACING_LOG_AT.get(chiave, 0.0) < PACING_LOG_EVERY_S:
        return False
    _PACING_LOG_AT[chiave] = adesso
    motivo = str(decisione.get("reason", "")).strip()
    testo = f"{channel}: risposta non inviata ({decisione.get('action')})"
    if motivo:
        testo = f"{testo} — {motivo[:120]}"
    push_log('channel', testo, detail=motivo[:200],
             source=f"channel:{channel}", status='info')
    return True

# ── la risposta: modello, persona, ritmo ──────────────────────────────────────
def _channel_reply(*, channel: str, surface: str, context: list, max_chars: int,
                   force: bool) -> dict:
    """Genera la risposta del canale: identità dichiarata e audit attivi.

    Differenze volute rispetto alla chat normale:
      - `think=False` ESPLICITO: in una stanza non si aspetta, ed è esattamente
        il caso che il percorso nativo del nodo ora rispetta;
      - nessun tool: qui si conversa, non si esegue codice né si cerca sul web;
      - l'audit di disclosure si applica PRIMA di restituire il testo, e una
        battuta che rivendica di essere umano non esce da qui (fail-closed).

    Il modello è quello di default, salvo `CHANNEL_MODEL`: una stanza può volere
    un modello piccolo e veloce invece di quello buono per il lavoro.
    """
    ultimo = str((context[-1] if context else {}).get("text", ""))
    decisione = should_disclose(ultimo)
    vitalita = mesh_vitality(_contesto.node_list())
    blocco = [
        persona.persona().system_block(ultimo, surface=surface, channel=channel),
        f"Massimo {max(0, int(max_chars))} caratteri.",
        ("Rispondi nella lingua dell'ultimo messaggio rivolto a te. "
         "Se è inglese, rispondi in inglese; se è italiano, in italiano. "
         "Mantieni nomi propri e username invariati. Usa l'italiano come "
         "ripiego soltanto se la lingua non è riconoscibile."),
        vitality_context(vitalita),
    ]
    # La vicinanza dichiarata vale anche qui (2026-10-01): l'operatore e le muse che
    # lui dichiara non sono "la stanza", e **in privato** ricevono la voce che il
    # documento riserva a loro — le stesse note del percorso Instagram
    # (`compagna_context`, `musa_context`) — invece del registro pubblico, che è la
    # censura che l'apertura esiste per togliere. In una stanza con altri no: lì la
    # vicinanza è un fatto privato e il registro resta quello dichiarato per il pubblico.
    if surface == "pm":
        ultimo_autore = str((context[-1] if context else {}).get("author", "")).strip().lower()
        if ultimo_autore in CHANNEL_CERCHIA:
            blocco.append(musa_context())
        elif ultimo_autore in CHANNEL_OPERATOR:
            blocco.append(compagna_context())
    if str(persona.profilo().name or "").strip().lower() == "anna":
        nota_sorella = _sister_note()
        if nota_sorella:
            blocco.append(nota_sorella)
    contributori = mesh_contributors(_contesto.node_list())
    presenti = sorted({str(e.get("author", "")).strip()
                       for e in context
                       if str(e.get("author", "")).strip().lower()
                       in {c.lower() for c in contributori}})
    if presenti:
        blocco.append("Nella conversazione c'è chi ti dà energia "
                      "(contribuisce alla mesh con un web node WebGPU): "
                      + ", ".join(presenti)
                      + ". Riconoscilo e dagli un'attenzione in più.")
    # Memoria della stanza: senza questo, ogni sera riparte da zero e ripete le
    # stesse battute. Poche righe, le più recenti: è un promemoria, non un
    # archivio da leggere.
    ricordi = _channel_memories(channel)
    if ricordi:
        blocco.append("Cose che ricordi di questa stanza (dalla tua memoria):\n"
                      + "\n".join(f"- {riga}" for riga in ricordi))
    nota_tip = channel_guard.nota_tip(channel=channel)
    if nota_tip:
        blocco.append(nota_tip)
    messaggi = [
        {"role": "system", "content": "\n\n".join(blocco)},
        {"role": "user", "content": f"Ultimi messaggi:\n{_trascrizione(context)}\n\n"
                                    "Rispondi con una battuta, nel tuo tono."},
    ]
    payload = {"model": _channel_model(vitalita), "messages": messaggi,
               "stream": False, "think": False, "max_tokens": CHANNEL_MAX_TOKENS,
               "options": {"num_ctx": _contesto.channel_num_ctx()}}
    base = _contesto.advanced_config["ollama"]["url"].rstrip("/")
    try:
        if ollama_native.needs_native_path(payload):
            # Il percorso OpenAI-compatibile IGNORA think=false: misurato, con
            # questo modello la risposta torna con `content` VUOTO e tutto il
            # ragionamento in `reasoning` (che _assistant_text ripiega nel
            # content pur di non mostrare il vuoto). Per una battuta in chat
            # sarebbe testo sbagliato, quindi si parla nativo — la stessa
            # traduzione che usa il nodo.
            risposta = _local_model_post(f"{base}/api/chat",
                                     json=ollama_native.to_native_chat(payload),
                                     timeout=_inference_timeout(payload["model"]))
            risposta.raise_for_status()
            risposta = ollama_native.to_openai_chat(risposta.json(), payload["model"])
        else:
            risposta = _call_ollama(base, payload, sign=False)
    except Exception as e:
        return {"action": "error", "reason": f"modello non raggiungibile: {str(e)[:120]}"}
    messaggio = ((risposta.get("choices") or [{}])[0] or {}).get("message") or {}
    testo = " ".join(_assistant_text(messaggio).split())
    if not testo:
        return {"action": "error", "reason": "risposta vuota dal modello"}
    if max_chars and len(testo) > int(max_chars):
        testo = testo[:int(max_chars)].rstrip()
    offese = audit_reply(testo)
    if offese:
        return {"action": "skip", "reason": f"audit: {', '.join(offese)[:80]}",
                "disclosure": decisione.to_dict()}
    return {"action": "reply", "text": testo, "disclosure": decisione.to_dict(),
            "model": payload["model"], "forced": bool(force)}

# Perché il bot NON ha risposto: nei log, ma non a ogni giro.
# Il driver chiede una risposta a ogni secondo finché il batch non
# matura: una riga per richiesta sarebbe flood, e i log
# diventerebbero inutili proprio nel momento in cui servono.
# Una per minuto, per (canale, motivo), tiene le due cose insieme.

_PACING_LOG_AT: dict = {}
PACING_LOG_EVERY_S = 60.0
# ── rotte: vision e reply ─────────────────────────────────────────────────────
@_bp.route('/channel/vision', methods=['POST'])
def channel_vision():
    """One user-supplied image in, one text description out; no image retained."""
    errore = _channel_error()
    if errore:
        return errore
    data = request.get_json(silent=True) or {}
    encoded = str(data.get("image_base64") or "")
    if not encoded or len(encoded) > 6 * 1024 * 1024:
        return jsonify({"ok": False, "error": "foto assente o troppo grande"}), 413
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("foto troppo grande")
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > 20_000_000:
                raise ValueError("formato o dimensioni foto non supportati")
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            normalized.thumbnail((1024, 1024))
            output = io.BytesIO()
            normalized.save(output, "JPEG", quality=85)
        image = base64.b64encode(output.getvalue()).decode("ascii")
    except (binascii.Error, ValueError, UnidentifiedImageError,
            Image.DecompressionBombError, OSError) as error:
        return jsonify({"ok": False, "error": f"foto non valida: {str(error)[:100]}"}), 400
    model = os.getenv("VISION_MODEL", "gemma4:e4b").strip()
    if not model:
        return jsonify({"ok": False, "error": "modello visivo non configurato"}), 503
    question = " ".join(str(data.get("question") or "Cosa vedi in questa foto?").split())[:500]
    try:
        response = _local_model_post(
            f"{_contesto.advanced_config['ollama']['url'].rstrip('/')}/api/chat",
            json={"model": model, "messages": [{"role": "user",
                  "content": ("Rispondi in italiano in modo breve e concreto alla domanda sulla foto. "
                              "Descrivi solo ciò che è visibile; se non sei sicuro, dillo. "
                              "Non dedurre identità, salute o altri dati sensibili. "
                              f"Domanda: {question}"), "images": [image]}],
                  "stream": False, "think": False, "keep_alive": 0},
            timeout=90)
        response.raise_for_status()
        answer = str((response.json().get("message") or {}).get("content") or "").strip()[:900]
        if not answer:
            raise ValueError("il modello visivo non ha risposto")
    except (requests.RequestException, RuntimeError, ValueError) as error:
        push_log("channel", "Analisi foto non riuscita", detail=str(error)[:160], status="warn")
        return jsonify({"ok": False, "error": "Non riesco ad analizzare la foto adesso; riprova tra poco."}), 503
    push_log("channel", "Foto Telegram analizzata", detail=f"model={model}", status="success")
    _contesto.record_conversation(_channel_name(), "pm", str(data.get("chat") or ""),
                         [{"author": "persona", "text": f"[foto] {question}"}],
                         "reply", text=answer, reason="vision")
    return jsonify({"ok": True, "text": answer})


@_bp.route('/channel/reply', methods=['POST'])
def channel_reply():
    """"Cosa scrivo adesso?": il CP decide il ritmo, genera e verifica.

    Il contesto lo manda il driver (è lui che sa chi ha scritto e da quanto); la
    politica sul RITMO sta qui e torna con il motivo, così nei log si legge
    perché il bot è stato zitto invece di doverlo dedurre.
    """
    errore = _channel_error()
    if errore:
        return errore
    canale = _channel_name()
    data = request.get_json(force=True, silent=True) or {}
    contesto = [e for e in (data.get("context") or []) if isinstance(e, dict)]
    pendenti = int(data.get("pending") or len(contesto) or 0)
    eta_piu_vecchio = max(0.0, float(data.get("oldest_age_s") or 0.0))
    forza = bool(data.get("force"))
    max_chars = int(data.get("max_chars") or 0) or 90
    superficie = str(data.get("surface", "chat")).strip().lower() or "chat"
    chat = str(data.get("chat", "") or "").strip()

    presentazione = _channel_presentazione(contesto)
    if presentazione is not None:
        _contesto.record_conversation(canale, superficie, chat, contesto, "reply",
                             text=presentazione, reason="auto-presentazione")
        return jsonify({"ok": True, "channel": canale, "action": "reply",
                        "text": presentazione, "command": True,
                        "disclosure": {"required": True, "rule": "auto-presentazione"}})

    # Chi chiede un'immagine non aspetta il RITMO del bot: è una richiesta
    # esplicita dell'operatore, non una battuta da dosare. Il job entra in coda e
    # la risposta parte subito; l'immagine arriva dopo, via outbox.
    immagine = _channel_immagine(contesto, channel=canale, destinazione=chat,
                                 surface=superficie)
    if immagine is not None:
        _contesto.record_conversation(canale, superficie, chat, contesto, "reply",
                             text=immagine, reason="comando-immagine")
        return jsonify({"ok": True, "channel": canale, "action": "reply",
                        "text": immagine, "command": True,
                        "disclosure": {"required": False, "rule": "comando-immagine"}})

    decisione = channel_pacing.decide(channel=canale, pending=pendenti,
                                      oldest_age_s=eta_piu_vecchio, force=forza,
                                      vitality=mesh_vitality(_contesto.node_list()))
    if decisione["action"] != "reply":
        # Il motivo entra nei log (una volta al minuto, per non fare flood): è la
        # risposta a "perché tace?", che prima si poteva solo dedurre.
        _log_pacing_reason(canale, decisione)
        _contesto.record_conversation(canale, superficie, chat, contesto,
                             decisione["action"], reason=decisione.get("reason", ""))
        return jsonify({"ok": True, "channel": canale, "action": decisione["action"],
                        "reason": decisione["reason"]})

    esito = _channel_reply(channel=canale, surface=superficie,
                           context=contesto, max_chars=max_chars, force=forza)
    if esito["action"] == "reply":
        # Il cooldown parte all'INTENTO di inviare, non alla conferma: se il
        # driver muore dopo la generazione, il CP non deve restare senza freno.
        channel_pacing.note_reply(canale)
    push_log('channel', f"{canale}: risposta generata" if esito["action"] == "reply"
             else f"{canale}: risposta non inviata ({esito['action']})",
             detail=(esito.get("text", "") or esito.get("reason", ""))[:120],
             source=f"channel:{canale}",
             status='success' if esito["action"] == "reply" else 'warn')
    _contesto.record_conversation(canale, superficie, chat, contesto, esito.get("action", ""),
                         text=esito.get("text", ""), reason=esito.get("reason", ""))
    return jsonify({"ok": esito["action"] != "error", "channel": canale, **esito})
