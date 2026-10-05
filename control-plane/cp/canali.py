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

from flask import jsonify, request

from cp.log import push_log
from shared.channel import ChannelGuard, ChannelPolicy, ChannelRuntime, ReplyPacing

_CHANNEL_TOKEN_HEADER = "X-Hyperspace-Channel-Token"


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


channel_policy = ChannelPolicy.from_env()
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

    Ritorna i due oggetti perché il chiamante deve rilegarseli: il `global` qui
    dentro aggiorna questo modulo, non il namespace di chi chiama.
    """
    global channel_policy, channel_pacing
    channel_policy = ChannelPolicy.from_env()
    channel_pacing = ReplyPacing(
        min_interval_s=_channel_float("CHANNEL_MIN_REPLY_INTERVAL_S", 25.0),
        batch_max_age_s=_channel_float("CHANNEL_BATCH_MAX_AGE_S", 6.0),
        batch_max_messages=_channel_int("CHANNEL_BATCH_MAX_MESSAGES", 6),
        probability=_channel_float("CHANNEL_REPLY_PROBABILITY", 1.0),
    )
    return channel_policy, channel_pacing
