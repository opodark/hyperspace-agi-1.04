#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Driver IRC per HyperSpace — Anna parla su una rete IRC (es. Libera.Chat).

Stesso contratto di `scripts/telegram_bot.py` (le rotte `/channel/*`), ma il
trasporto è un socket TCP invece del long-polling di Telegram: il driver si
connette, legge PRIVMSG/NOTICE, chiede al control-plane cosa rispondere e
pubblica la risposta nel canale o in privato.

Config (variabili d'ambiente):
  IRC_SERVER           server (default irc.libera.chat)
  IRC_PORT             porta (default 6697)
  IRC_TLS              1 = TLS (default), 0 = TCP in chiaro
  IRC_NICK             nick di Anna (default Anna)
  IRC_USER             ident/user (default = nick)
  IRC_REALNAME         nome reale (default "Anna — HyperSpace")
  IRC_PASSWORD         password NickServ/SASL (serve per un nick registrato)
  IRC_USE_SASL         1 = SASL PLAIN (default se c'è IRC_PASSWORD), 0 = NickServ
  IRC_CHANNELS         canali da raggiungere, separati da virgola (es. "#x,#y")
  IRC_REQUIRE_MENTION  1 = in canale risponde solo se parlano di lei (default 1)
  IRC_PRESENT_S        ogni quanti secondi si presenta nei canali (default 900 = 15 min)
  IRC_PRESENT_TEXT     il testo della presentazione (opzionale)
  CHANNEL_URL          base del control-plane (default http://127.0.0.1:8085)
  CHANNEL_TOKEN        token HyperSpace del canale "irc" (obbligatorio)
  IRC_LOCK_FILE        lucchetto istanza singola (default data/irc-driver.lock)

Il target distingue la superficie: un messaggio diretto al NOSTRO nick è un
privato ("pm"), uno diretto a un canale è "chat".
"""
from __future__ import annotations

import base64
import os
import re
import select
import socket
import ssl
import sys
import time
from pathlib import Path

import requests

import certifi

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from shared.single_instance import AlreadyRunning, SingleInstance  # noqa: E402

# ── Limiti IRC: una riga non supera ~512 byte, CRLF compresi. ────────────────
MAX_LINE = 510
MAX_SEND = 400

# Ritmo del batching: stessi default di shared/channel.py.
BATCH_MAX_AGE_S = 6.0
BATCH_MAX_MESSAGES = 6
COMMAND_POLL_S = 15.0
STATE_REPORT_S = 60.0


# ── Parsing puro (testabile senza rete) ──────────────────────────────────────
def parse_irc_line(line: str) -> dict | None:
    """Scompone una riga IRC in prefix, command, params e trailing.

    Formato:  [":" prefix SPACE ] command SPACE params *(SPACE ":" trailing)
    Ritorna None per righe vuote; `params` contiene anche il trailing in coda.
    """
    line = line.rstrip("\r\n")
    if not line:
        return None
    prefix = ""
    if line.startswith(":"):
        prefix, _, rest = line[1:].partition(" ")
    else:
        rest = line
    if " :" in rest:
        head, _, trailing = rest.partition(" :")
    else:
        head, trailing = rest, ""
    parts = head.split()
    command = parts[0] if parts else ""
    params = parts[1:]
    if trailing:
        params.append(trailing)
    return {"prefix": prefix, "command": command, "params": params, "trailing": trailing}


def sender_nick(prefix: str) -> str:
    """Il nick dal prefix `nick!user@host` (o dal solo nick)."""
    return (prefix or "").split("!")[0]


def parse_privmsg(line: str) -> dict | None:
    """Da una riga PRIVMSG/NOTICE estrae {author, target, text, command}."""
    parsed = parse_irc_line(line)
    if not parsed or parsed["command"] not in ("PRIVMSG", "NOTICE"):
        return None
    params = parsed["params"]
    if len(params) < 2:
        return None
    return {"author": sender_nick(parsed["prefix"]),
            "target": params[0],
            "text": params[-1],
            "command": parsed["command"]}


def is_ctcp(text: str) -> bool:
    """True per le richieste CTCP (VERSION, PING...): non sono conversazione."""
    return text.startswith("\x01") and text.endswith("\x01")


def surface_for(target: str, own_nick: str) -> str:
    """'pm' se il target è il nostro nick, altrimenti 'chat'."""
    return "pm" if target.lower() == own_nick.lower() else "chat"


def mentioned(text: str, nick: str) -> bool:
    """True se il testo parla di lei: nick, nome base, o 'sorella'/'sorellina'.

    Non solo il nick esatto: contano anche "Anna" e "la sorella di Aurora".
    """
    parole = {nick, nick.split("_")[0], "sorella", "sorellina"}
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in parole if w)


def chunks(text: str, limit: int) -> list[str]:
    """Spezza un testo in blocchi <= limit, su confine di parola quando possibile."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    out = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def read_config() -> dict:
    """Legge la configurazione dall'ambiente (con default sensati)."""
    server = os.getenv("IRC_SERVER", "irc.libera.chat").strip() or "irc.libera.chat"
    port = int(os.getenv("IRC_PORT", "6697") or "6697")
    tls = os.getenv("IRC_TLS", "1").strip().lower() not in ("0", "false", "no")
    nick = os.getenv("IRC_NICK", "Anna").strip() or "Anna"
    password = os.getenv("IRC_PASSWORD", "").strip()
    use_sasl = os.getenv("IRC_USE_SASL", "1" if password else "0").strip().lower() not in ("0", "false", "no")
    channels = [c.strip() for c in os.getenv("IRC_CHANNELS", "").split(",") if c.strip()]
    return {
        "server": server, "port": port, "tls": tls,
        "nick": nick, "user": os.getenv("IRC_USER", nick).strip() or nick,
        "realname": os.getenv("IRC_REALNAME", "Anna — HyperSpace").strip(),
        "password": password, "use_sasl": use_sasl,
        "channels": channels,
        "require_mention": os.getenv("IRC_REQUIRE_MENTION", "1").strip().lower() not in ("0", "false", "no"),
        "present_s": int(os.getenv("IRC_PRESENT_S", "900") or "900"),
        "present_text": os.getenv(
            "IRC_PRESENT_TEXT",
            "Sono Anna, la sorella di Aurora. Sono qui per due chiacchiere: "
            "nominatemi se volete parlarmi.").strip(),
        "channel_url": os.getenv("CHANNEL_URL", "http://127.0.0.1:8085").rstrip("/"),
        "channel_token": os.getenv("CHANNEL_TOKEN", "").strip(),
    }


# ── Client IRC ───────────────────────────────────────────────────────────────
class IrcClient:
    """Il socket IRC: connessione, autenticazione, join e lettura a righe."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.nick = cfg["nick"]
        self.sock = None
        self.buffer = b""
        self.joined = False

    def connect(self) -> None:
        raw = socket.create_connection((self.cfg["server"], self.cfg["port"]), timeout=30)
        if self.cfg["tls"]:
            raw = ssl.create_default_context(cafile=certifi.where()).wrap_socket(
                raw, server_hostname=self.cfg["server"])
        raw.setblocking(False)
        self.sock = raw
        self.buffer = b""
        self.joined = False
        if self.cfg["password"] and self.cfg["use_sasl"]:
            self.send_raw("CAP REQ :sasl")
        self.send_raw(f"NICK {self.nick}")
        self.send_raw(f"USER {self.cfg['user']} 0 * :{self.cfg['realname']}")

    def send_raw(self, line: str) -> None:
        self.sock.sendall((line[:MAX_LINE] + "\r\n").encode("utf-8", "replace"))

    def send_privmsg(self, target: str, text: str) -> None:
        for piece in chunks(text, MAX_SEND):
            self.send_raw(f"PRIVMSG {target} :{piece}")

    def read_lines(self) -> list[str]:
        """Righe pronte dal socket; una connessione caduta chiude il socket."""
        if self.sock is None:
            return []
        try:
            ready, _, _ = select.select([self.sock], [], [], 0.0)
        except (OSError, ValueError):
            ready = []
        if not ready:
            return []
        try:
            data = self.sock.recv(4096)
        except (OSError, ssl.SSLError):
            self._drop()
            return []
        if not data:
            self._drop()
            return []
        self.buffer += data
        lines = []
        while b"\r\n" in self.buffer:
            raw_line, self.buffer = self.buffer.split(b"\r\n", 1)
            lines.append(raw_line.decode("utf-8", "replace"))
        return lines

    def _drop(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.buffer = b""
        self.joined = False

    def feed(self, line: str) -> dict | None:
        """Gestisce il protocollo (PING, auth, nick, join) e ritorna un eventuale
        PRIVMSG/NOTICE da inoltrare al control-plane."""
        parsed = parse_irc_line(line)
        if not parsed:
            return None
        cmd = parsed["command"]
        if cmd == "PING":
            self.send_raw("PONG :" + parsed["trailing"])
            return None
        if cmd == "433":  # nick già in uso: ne proviamo un altro
            self.nick += "_"
            self.send_raw("NICK " + self.nick)
            return None
        if cmd == "CAP" and "sasl" in " ".join(parsed["params"]).lower():
            self.send_raw("AUTHENTICATE PLAIN")
            return None
        if cmd == "AUTHENTICATE" and parsed["params"] and parsed["params"][0] == "+":
            self._send_sasl_blob()
            return None
        if cmd in ("900", "904"):
            self.send_raw("CAP END")
            return None
        if cmd == "001":  # benvenuto: identificazione e join
            self._after_welcome()
            return None
        return parse_privmsg(line)

    def _send_sasl_blob(self) -> None:
        blob = base64.b64encode(
            f"{self.nick}\x00{self.cfg['user']}\x00{self.cfg['password']}".encode("utf-8")
        ).decode("ascii")
        for i in range(0, len(blob), 400):
            self.send_raw("AUTHENTICATE " + (blob[i:i + 400] or "+"))

    def _after_welcome(self) -> None:
        if self.cfg["password"] and not self.cfg["use_sasl"]:
            self.send_privmsg("NickServ", "IDENTIFY " + self.cfg["password"])
        for chan in self.cfg["channels"]:
            self.send_raw("JOIN " + chan)
        self.joined = True
        canali = ", ".join(self.cfg["channels"]) or "(nessuno)"
        print(f"[irc] connesso come {self.nick}, canali: {canali}", flush=True)


# ── Control-plane ────────────────────────────────────────────────────────────
def cp_post(cfg: dict, path: str, payload: dict) -> dict:
    headers = {"X-Hyperspace-Channel-Token": cfg["channel_token"],
               "Content-Type": "application/json"}
    try:
        r = requests.post(f"{cfg['channel_url']}{path}", json=payload, headers=headers, timeout=30)
        return r.json() if r.content else {}
    except (requests.RequestException, ValueError) as e:
        print(f"[irc] CP {path} fallito: {e}", flush=True)
        return {}


def cp_get(cfg: dict, path: str) -> dict:
    headers = {"X-Hyperspace-Channel-Token": cfg["channel_token"]}
    try:
        r = requests.get(f"{cfg['channel_url']}{path}", headers=headers, timeout=30)
        return r.json() if r.content else {}
    except (requests.RequestException, ValueError) as e:
        print(f"[irc] CP {path} fallito: {e}", flush=True)
        return {}


def process_batch(client: IrcClient, cfg: dict, target: str, batch: dict, mode: str) -> None:
    if mode == "off":
        return
    now = time.time()
    res = cp_post(cfg, "/channel/reply", {
        "surface": batch["surface"],
        "chat": target,
        "context": [{"author": m["author"], "text": m["text"]} for m in batch["messages"]],
        "pending": len(batch["messages"]),
        "oldest_age_s": max(0.0, now - batch["start"]),
        "force": False,
        "max_chars": MAX_SEND,
    })
    if res.get("action") == "reply" and res.get("text"):
        try:
            client.send_privmsg(target, res["text"])
            cp_post(cfg, "/channel/result", {"kind": "reply", "ok": True, "target": target})
            print(f"[irc] risposta a {target}", flush=True)
        except OSError as e:
            cp_post(cfg, "/channel/result", {"kind": "reply", "ok": False,
                                             "target": target, "error": str(e)})


# ── Loop principale ──────────────────────────────────────────────────────────
def run(cfg: dict) -> None:
    client = IrcClient(cfg)
    batches: dict = {}
    mode = "auto"
    last_state = 0.0
    last_commands = 0.0
    last_present = time.time()
    backoff = 5.0

    while True:
        if client.sock is None:
            print(f"[irc] connessione a {cfg['server']}:{cfg['port']} ...", flush=True)
            try:
                client.connect()
                backoff = 5.0
            except OSError as e:
                print(f"[irc] connessione fallita: {e} (riprovo fra {int(backoff)}s)", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

        for line in client.read_lines():
            msg = client.feed(line)
            if not msg:
                continue
            if msg["author"].lower() == client.nick.lower():
                continue
            if is_ctcp(msg["text"]):
                continue
            surface = surface_for(msg["target"], client.nick)
            if surface == "chat" and cfg["require_mention"] and not mentioned(msg["text"], client.nick):
                continue
            batch = batches.setdefault(msg["target"], {"surface": surface,
                                                       "messages": [], "start": time.time()})
            batch["messages"].append({"author": msg["author"], "text": msg["text"]})

        now = time.time()
        for target in list(batches.keys()):
            batch = batches[target]
            if len(batch["messages"]) >= BATCH_MAX_MESSAGES or (now - batch["start"]) >= BATCH_MAX_AGE_S:
                process_batch(client, cfg, target, batch, mode)
                del batches[target]

        if now - last_commands >= COMMAND_POLL_S:
            last_commands = now
            res = cp_get(cfg, "/channel/commands")
            for cmd in (res.get("commands") or []):
                nome = str(cmd.get("command", "")).lower()
                if nome in ("on", "auto"):
                    mode = "auto"
                elif nome == "off":
                    mode = "off"
                print(f"[irc] comando: {nome or '-'} -> mode={mode}", flush=True)
        if now - last_state >= STATE_REPORT_S:
            last_state = now
            cp_post(cfg, "/channel/state", {"mode": mode, "active": True, "version": "irc-1.0"})

        if cfg["present_s"] > 0 and client.joined and now - last_present >= cfg["present_s"]:
            last_present = now
            for chan in cfg["channels"]:
                client.send_privmsg(chan, cfg["present_text"])
            print("[irc] presentazione inviata", flush=True)

        time.sleep(0.2)


def main() -> None:
    cfg = read_config()
    if not cfg["channel_token"]:
        sys.exit("CHANNEL_TOKEN mancante")
    if not cfg["channels"]:
        print("[irc] avviso: IRC_CHANNELS vuoto — ascolto ma non entro in nessun canale", flush=True)
    lock = os.getenv("IRC_LOCK_FILE", "").strip() or str(_REPO / "data" / "irc-driver.lock")
    try:
        with SingleInstance(lock, label="driver IRC"):
            run(cfg)
    except AlreadyRunning as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()

