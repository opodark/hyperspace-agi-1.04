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

import os
from types import SimpleNamespace

from flask import Blueprint, jsonify, request

import json

from cp.config import DEFAULT_MODEL
from cp.log import push_log
from shared.channel import (COMANDI_DRIVER, KNOWN_CHANNELS, ChannelGuard,
                            ChannelPolicy, ChannelRuntime, ReplyPacing)
from shared.vitality import mesh_contributors, mesh_vitality

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
          num_ctx=None, channel_remember=None, node_list=None):
    """Registra le otto route di stato e ingestion, e tiene i riferimenti al boot."""
    global _contesto
    _contesto = SimpleNamespace(
        image_queue=image_queue,
        channel_context_messages=context_messages,
        channel_context_chars=context_chars,
        channel_num_ctx=num_ctx,
        channel_remember=channel_remember,
        node_list=node_list,
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
