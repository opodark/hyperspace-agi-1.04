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

Operativo: per LEGGERE i messaggi il bot va aggiunto a un GRUPPO (o
supergruppo), non a un canale broadcast (lì può solo pubblicare). Di default il
bot vede solo menzioni e comandi: per vedere tutto, privacy mode disattivato da
@BotFather oppure bot admin del gruppo.
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


def main():
    offset = 0
    chats = {}  # chat_id -> {"surface": str, "context": deque, "dirty": bool}
    mode = "auto"
    last_commands = 0.0
    last_state = 0.0
    print(f"[telegram] driver avviato -> {CHANNEL_URL} (mode={mode})", flush=True)
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
            if not msg or not msg.get("text"):
                continue
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            entry = chats.setdefault(chat_id, {"surface": "chat",
                                               "context": deque(maxlen=MAX_CONTEXT),
                                               "dirty": False})
            entry["surface"] = surface_of(chat)
            author, text = author_of(msg), msg["text"]
            entry["context"].append({"author": author, "text": text})
            entry["dirty"] = True
            cp_post("/channel/ingest", {"surface": entry["surface"],
                                        "events": [{"author": author, "text": text,
                                                    "key": str(msg.get("message_id"))}]})

        # 2. Risposta per chat con novità
        if mode != "off":
            for chat_id, entry in list(chats.items()):
                if not entry["dirty"] or not entry["context"]:
                    continue
                entry["dirty"] = False
                res = cp_post("/channel/reply", {"surface": entry["surface"],
                                                 "context": list(entry["context"]),
                                                 "pending": len(entry["context"]),
                                                 "oldest_age_s": 0.0, "force": False,
                                                 "max_chars": 900})
                if res.get("action") == "reply" and res.get("text"):
                    try:
                        tg("sendMessage", chat_id=chat_id, text=res["text"])
                        cp_post("/channel/result", {"kind": "reply", "ok": True,
                                                    "target": str(chat_id)})
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
