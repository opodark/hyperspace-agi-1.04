#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Driver Telegram per HyperSpace — il bot tira le decisioni dal control-plane.

Il control-plane non raggiunge Telegram, quindi questo driver fa da adattatore:
legge i messaggi via getUpdates (long-polling, nessun webhook) e chiede al CP
cosa rispondere con il contratto /channel/* (docs/channel.md).

Config (variabili d'ambiente):
  TELEGRAM_BOT_TOKEN   token del bot da @BotFather (obbligatorio)
  CHANNEL_URL          base del control-plane (default http://127.0.0.1:8085)
  CHANNEL_TOKEN        token HyperSpace del canale "telegram" (obbligatorio)
  TELEGRAM_REQUIRE_MENTION
                       1 = risponde solo se lo nominano o se rispondono a un suo
                       messaggio. Serve nei gruppi con PIÙ bot (due Aurora nello
                       stesso gruppo risponderebbero entrambe alla stessa
                       battuta); default 0 = risponde come oggi.

Operativo: per LEGGERE i messaggi il bot va aggiunto a un GRUPPO (o
supergruppo), non a un canale broadcast (lì può solo pubblicare). Di default il
bot vede solo menzioni e comandi: per vedere tutto, privacy mode disattivato da
@BotFather oppure bot admin del gruppo. Con più bot nello stesso gruppo la
combinazione utile è privacy OFF (vede tutto, quindi ha contesto) +
TELEGRAM_REQUIRE_MENTION=1 (parla solo se chiamato).
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque

import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "http://127.0.0.1:8085").rstrip("/")
CHANNEL_TOKEN = os.getenv("CHANNEL_TOKEN", "").strip()

if not TELEGRAM_TOKEN:
    sys.exit("TELEGRAM_BOT_TOKEN mancante")
if not CHANNEL_TOKEN:
    sys.exit("CHANNEL_TOKEN mancante")

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
HEADERS = {"X-Hyperspace-Channel-Token": CHANNEL_TOKEN}
MAX_CONTEXT = 20
COMMAND_POLL_S = 15.0
STATE_REPORT_S = 60.0

# In un gruppo con PIÙ bot (es. Aurora su Windows e un secondo bot sulla mesh)
# servono due regole diverse, e sono due:
#
#  1. i messaggi degli ALTRI BOT si ignorano SEMPRE. Senza questo, A pubblica ->
#     B legge -> risponde -> A legge -> risponde: un ciclo infinito che nessuna
#     moderazione ferma, perché non è spam, è cortesia. (Telegram non consegna a
#     un bot i propri messaggi, ma consegna quelli degli altri bot.)
#  2. con TELEGRAM_REQUIRE_MENTION=1 si risponde solo a chi ci nomina o risponde
#     a un nostro messaggio. Con la privacy mode ATTIVA il bot riceverebbe solo
#     menzioni ma NON vedrebbe la conversazione (e risponderebbe senza contesto):
#     la combinazione giusta è privacy OFF (vede tutto) + mention ON (parla solo
#     se chiamato), così il secondo bot può restare in ascolto senza accavallarsi.
REQUIRE_MENTION = os.getenv("TELEGRAM_REQUIRE_MENTION", "0").strip() == "1"


def tg(method, **params):
    response = requests.post(f"{API}/{method}", json=params, timeout=35)
    response.raise_for_status()
    return response.json()


def cp_post(path, payload):
    try:
        r = requests.post(f"{CHANNEL_URL}{path}", json=payload, headers=HEADERS, timeout=35)
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)[:120]}
    if r.status_code >= 400:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:120]}"}
    return r.json()


def cp_get(path):
    try:
        r = requests.get(f"{CHANNEL_URL}{path}", headers=HEADERS, timeout=10)
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)[:120]}
    if r.status_code >= 400:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:120]}"}
    return r.json()


def surface_of(chat):
    return "pm" if str(chat.get("type", "")) == "private" else "chat"


def author_of(msg):
    who = msg.get("from") or {}
    return (who.get("username") or who.get("first_name") or "anonimo")[:64]


def da_bot(msg) -> bool:
    """Vero se ha scritto un BOT (noi no: i nostri messaggi non ci arrivano).

    È la guardia contro il ciclo fra due bot nello stesso gruppo.
    """
    return bool((msg.get("from") or {}).get("is_bot"))


def testo_senza_menzione(text, username: str) -> str:
    """Toglie una menzione INIZIALE del nostro @username.

    Perché serve: con TELEGRAM_REQUIRE_MENTION il comando dell'operatore arriva
    come "@aurora_2001bot !presentati", e il CP riconosce i comandi confrontando
    il testo DALL'INIZIO — senza normalizzare, `!presentati` non scatterebbe più.
    La menzione è indirizzata a noi, quindi il modello non perde nulla: il resto
    del testo resta esattamente com'è scritto. Se il messaggio è SOLO la
    menzione, si tiene il testo originale: è comunque una chiamata per nome e
    deve poter avere una risposta.
    """
    originale = str(text or "").strip()
    if not username:
        return originale
    marchio = "@" + username.lower()
    if originale.lower().startswith(marchio):
        resto = originale[len(marchio):].strip()
        return resto or originale
    return originale


def rivolta_a_noi(msg, username: str, bot_id) -> bool:
    """Vero se il messaggio ci nomina o risponde a un nostro messaggio."""
    if not username:
        return False
    if ("@" + username.lower()) in str(msg.get("text") or "").lower():
        return True
    risposta = msg.get("reply_to_message") or {}
    return bool(bot_id) and (risposta.get("from") or {}).get("id") == bot_id


def main():
    offset = 0
    chats = {}  # chat_id -> {"surface", "messages", "batch_start", "addressed"}
    mode = "auto"
    last_commands = 0.0
    last_state = 0.0
    # Chi siamo: servono @username (per le menzioni) e id (per riconoscere le
    # risposte ai nostri messaggi). Se getMe non risponde e la modalità mention è
    # attiva si esce: un bot che non sa il proprio nome resterebbe muto per
    # sempre senza dirlo, che è il modo peggiore di fallire.
    username, bot_id = "", 0
    try:
        me = tg("getMe").get("result") or {}
        username, bot_id = str(me.get("username") or ""), me.get("id") or 0
    except requests.RequestException as e:
        if REQUIRE_MENTION:
            sys.exit(f"getMe non ha risposto ({e}): senza @username la modalità "
                     "TELEGRAM_REQUIRE_MENTION non può funzionare")
        print(f"[telegram] getMe non ha risposto: {e}", flush=True)
    print(f"[telegram] driver avviato -> {CHANNEL_URL} (mode={mode}, "
          f"bot=@{username or '?'}, mention={'richiesta' if REQUIRE_MENTION else 'no'})",
          flush=True)
    while True:
        # 1. Nuovi messaggi
        try:
            updates = tg("getUpdates", offset=offset, timeout=30,
                         allowed_updates=["message"])
        except requests.RequestException as e:
            print(f"[telegram] getUpdates: {e}", flush=True)
            time.sleep(5)
            continue
        for upd in updates.get("result") or []:
            offset = max(offset, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message")
            if not msg or not msg.get("text") or da_bot(msg):
                continue
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            entry = chats.setdefault(chat_id, {"surface": "chat",
                                               "messages": deque(maxlen=MAX_CONTEXT),
                                               "batch_start": time.time(),
                                               "addressed": False})
            entry["surface"] = surface_of(chat)
            author = author_of(msg)
            text = testo_senza_menzione(msg["text"], username)
            if rivolta_a_noi(msg, username, bot_id):
                entry["addressed"] = True
            if not entry["messages"]:
                entry["batch_start"] = time.time()
            entry["messages"].append({"author": author, "text": text})
            cp_post("/channel/ingest", {"surface": entry["surface"],
                                        "events": [{"author": author, "text": text,
                                                    "key": str(msg.get("message_id"))}]})

        # 2. Risposta: si chiede finché il batch non matura (il CP decide il ritmo)
        if mode != "off":
            for chat_id, entry in list(chats.items()):
                if not entry["messages"]:
                    continue
                # Con la mention richiesta si parla solo se qualcuno ci ha
                # chiamate: gli altri messaggi restano nel contesto (così la
                # risposta ha il filo della conversazione) ma non fanno
                # intervenire — è ciò che permette a due bot di stare nello
                # stesso gruppo senza rispondere entrambi alla stessa battuta.
                if REQUIRE_MENTION and not entry.get("addressed"):
                    continue
                adesso = time.time()
                res = cp_post("/channel/reply", {
                    "surface": entry["surface"],
                    "context": [{"author": m["author"], "text": m["text"]}
                                for m in entry["messages"]],
                    "pending": len(entry["messages"]),
                    "oldest_age_s": max(0.0, adesso - entry["batch_start"]),
                    "force": False, "max_chars": 900})
                if res.get("action") == "reply" and res.get("text"):
                    try:
                        tg("sendMessage", chat_id=chat_id, text=res["text"])
                        cp_post("/channel/result", {"kind": "reply", "ok": True,
                                                    "target": str(chat_id)})
                        entry["messages"].clear()
                        entry["batch_start"] = adesso
                        entry["addressed"] = False
                        print(f"[telegram] inviata risposta a {chat_id}", flush=True)
                    except requests.RequestException as e:
                        cp_post("/channel/result", {"kind": "reply", "ok": False,
                                                    "target": str(chat_id), "error": str(e)})
                        print(f"[telegram] invio fallito: {e}", flush=True)

        # 3. Comandi dell'operatore + stato
        now = time.time()
        if now - last_commands >= COMMAND_POLL_S:
            last_commands = now
            res = cp_get("/channel/commands")
            for cmd in (res.get("commands") or []):
                nome = str(cmd.get("command", "")).lower()
                if nome in ("on", "auto"):
                    mode = "auto"
                elif nome == "off":
                    mode = "off"
                print(f"[telegram] comando: {nome or '-'} -> mode={mode}", flush=True)
        if now - last_state >= STATE_REPORT_S:
            last_state = now
            cp_post("/channel/state", {"mode": mode, "active": True,
                                       "version": "telegram-1.0"})

        time.sleep(1)


if __name__ == "__main__":
    main()
