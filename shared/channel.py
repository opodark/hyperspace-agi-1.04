# SPDX-License-Identifier: Apache-2.0
"""Canali esterni: chi può parlare col control-plane, e cosa fare dei messaggi.

Un "canale" è un client che vede e muove una superficie di conversazione che il
control-plane NON può raggiungere da solo: la chat di una stanza live, i suoi
messaggi privati, un bot su un'altra piattaforma. La direzione è la stessa del
web node (shared/web_node.py): il client *tira* le decisioni e pubblica l'esito,
perché dall'altra parte c'è un DOM, non un endpoint.

Qui vive solo la parte decidibile e testabile — nessuna dipendenza da Flask,
nessuna chiamata di rete, orologio iniettabile:

  - la POLICY dei token (fail-closed: senza token il canale non è servito);
  - la CLASSIFICAZIONE dei messaggi in arrivo (i bot promozionali che sommergono
    i privati) e l'escalation a strike con decadimento;
  - il RITMO delle risposte (intervallo minimo, batch maturo, probabilità), che
    oggi è duplicato nel driver della piattaforma.

Tre scelte che vale la pena conoscere prima di leggere il codice:

1. **La classificazione non punisce.** Un verdetto "spam" significa "non
   rispondere e conta lo strike", non "banna subito": un falso positivo qui
   colpisce una persona vera che sta guardando la stanza, e nessuna euristica
   vale quel prezzo. Le azioni di moderazione scattano solo su strike ripetuti
   dentro la finestra.
2. **Lo stato è limitato.** La memoria per autore, il numero di autori seguiti e
   la cronologia sono tetti espliciti: la pioggia di messaggi è un evento
   avversario, non un caso limite.
3. **Il CP decide, il driver misura.** Il driver sa quante persone stanno
   scrivendo e da quanto; la politica su *cosa farne* sta qui, così vale per
   qualunque canale e si può testare senza un browser.
"""
from __future__ import annotations

import hmac
import os
import random as _random
import re
import threading
import time

MIN_TOKEN_LENGTH = 32
CHANNELS_VAR = "CHANNEL_CLIENTS"
ENABLED_VAR = "CHANNEL_ENABLED"

# Tetti espliciti: la stanza può essere inondata, la memoria del CP no.
DEFAULT_MAX_AUTHORS = 500
DEFAULT_HISTORY = 8
DEFAULT_STRIKE_DECAY_S = 1800
DEFAULT_STRIKE_MUTE = 2
DEFAULT_STRIKE_BAN = 3
DEFAULT_MIN_REPLY_INTERVAL_S = 25.0
DEFAULT_BATCH_MAX_AGE_S = 6.0
DEFAULT_BATCH_MAX_MESSAGES = 6

# ── Regole di classificazione ────────────────────────────────────────────────
# Ogni regola ha un nome leggibile perché finisce nei log e in /channel/status:
# "perché non ho risposto a questo utente" deve essere una frase, non un numero.
SPAM_RULES: tuple[tuple[str, str], ...] = (
    ("link", r"(?:https?://|www\.|\bt\.me/|\btelegram\b|\bwa\.me/|\bsnap(?:chat)?\b\s*[:@]"
             r"|\bkik\b|\binstagram\b|\bonlyfans\b|\bskype\b)"),
    ("promo", r"\b(?:vieni\s+(?:nella\s+)?mia\s+(?:stanza|room)|join\s+my\s+(?:room|show)|"
              r"free\s+(?:show|chat|token)|private\s+show|privato\s+gratis|"
              r"(?:guarda|vedi)\s+il\s+mio\s+profilo|check\s+my\s+profile|"
              r"nuova\s+ragazza\s+online|new\s+girl\s+online)\b"),
    ("solo_simboli", r"^[\W\d_]+$"),
)

_SPAM_COMPILED = tuple((nome, re.compile(pattern, re.IGNORECASE | re.UNICODE))
                       for nome, pattern in SPAM_RULES)

# Regole che valgono anche alla PRIMA occorrenza: un privato da chi non ha mai
# scritto, con dentro un link o un invito a seguirsi altrove, è il profilo del
# bot promozionale. Le altre (ripetizione, raffica, maiuscolo) hanno bisogno di
# contesto e da sole non bastano.
_RULES_FORTI = frozenset({"link", "promo"})

_PUNTEGGIATURA = re.compile(r"[\W_]+", re.UNICODE)


class ChannelError(ValueError):
    """Configurazione o richiesta non valida, rispondibile con un 4xx."""


def parse_clients(text) -> tuple[dict, list[str]]:
    """`"cam4=<token>;cb=<token>"` -> `({nome: token}, problemi)`.

    Stessa forma di MCP_CLIENTS: una sintassi sola per le superfici esterne. Un
    token corto viene SCARTATO (non accettato "tanto è locale"): la soglia è la
    stessa di MCP e NETWORK_ADMIN.
    """
    canali: dict = {}
    problemi: list[str] = []
    for pezzo in str(text or "").split(";"):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        nome, sep, token = pezzo.partition("=")
        nome, token = nome.strip().lower(), token.strip()
        if not sep or not nome:
            problemi.append(f"{CHANNELS_VAR}: voce illeggibile {pezzo!r} (atteso nome=token)")
            continue
        if nome in canali:
            problemi.append(f"{CHANNELS_VAR}: canale duplicato {nome!r}, tenuto il primo")
            continue
        if len(token) < MIN_TOKEN_LENGTH:
            problemi.append(f"{CHANNELS_VAR}: token di {nome!r} più corto di "
                            f"{MIN_TOKEN_LENGTH} caratteri, canale ignorato")
            continue
        canali[nome] = token
    return canali, problemi


# ── Catalogo delle piattaforme social ─────────────────────────────────────────
# La scheda "Social" del dashboard mostra una card per piattaforma: qui vive il
# catalogo dichiarativo. Aggiungere un social = aggiungere una voce, senza
# toccare il resto del canale (token, moderazione e ritmo sono già per-nome).
KNOWN_CHANNELS: tuple[dict, ...] = (
    {"key": "telegram", "label": "Telegram", "icon": "✈️",
     "auth": "bot_token", "surfaces": ("chat", "pm", "thread"),
     "hint": "Bot token da @BotFather; long-polling getUpdates, nessun intent privilegiato.",
     "first_class": True},
    {"key": "discord", "label": "Discord", "icon": "🎮",
     "auth": "bot_token", "surfaces": ("chat", "pm", "thread"),
     "hint": "Bot applicazione + intent Message Content; gateway WebSocket.",
     "first_class": True},
    {"key": "cam4", "label": "CAM4", "icon": "📹",
     "auth": "account_browser", "surfaces": ("chat", "pm"),
     "hint": "Account + driver browser (DOM), come cam4_chatbot.py.",
     "first_class": True},
    {"key": "cb", "label": "Chaturbate", "icon": "🎥",
     "auth": "account_browser", "surfaces": ("chat", "pm"),
     "hint": "Account + driver browser (DOM).",
     "first_class": True},
    {"key": "instagram", "label": "Instagram", "icon": "📸",
     "auth": "account_browser", "surfaces": ("dm",),
     "hint": "Placeholder: DM via account/browser.",
     "first_class": False},
    {"key": "x", "label": "X (Twitter)", "icon": "🐦",
     "auth": "api_token", "surfaces": ("dm",),
     "hint": "Placeholder: API token.",
     "first_class": False},
)


def known_channel(key: str) -> dict | None:
    """Voce del catalogo per chiave, o None se sconosciuta."""
    chiave = str(key or "").strip().lower()
    return next((v for v in KNOWN_CHANNELS if v["key"] == chiave), None)


class ChannelPolicy:
    """Chi può usare le route /channel/*."""

    def __init__(self, clients=None, *, enabled=True, problems=None):
        self.clients = dict(clients or {})
        self.enabled = bool(enabled)
        self.problems = list(problems or [])

    @classmethod
    def from_env(cls, environ=None) -> "ChannelPolicy":
        env = os.environ if environ is None else environ
        clients, problems = parse_clients(env.get(CHANNELS_VAR, ""))
        enabled = str(env.get(ENABLED_VAR, "true")).strip().lower() != "false"
        return cls(clients, enabled=enabled, problems=problems)

    @property
    def configured(self) -> bool:
        return bool(self.clients)

    def authenticate(self, provided):
        """Nome del canale per quel token, o None.

        Si confrontano TUTTI i token senza interrompere al primo match: come in
        shared/mcp_auth.py, il tempo di risposta non deve dire a un attaccante
        quale token ha sfiorato.
        """
        if not isinstance(provided, str) or not provided:
            return None
        trovato = None
        for nome, token in self.clients.items():
            if hmac.compare_digest(provided, token):
                trovato = nome
        return trovato

    def describe(self) -> dict:
        """Diagnostica senza segreti: nomi e booleani, mai i token."""
        return {"enabled": self.enabled, "configured": self.configured,
                "channels": sorted(self.clients), "min_token_length": MIN_TOKEN_LENGTH,
                "problems": list(self.problems)}


# ── Classificazione e stato per autore ───────────────────────────────────────
def normalizza(testo) -> str:
    """Testo ridotto all'osso per confrontare ripetizioni vere."""
    ripulito = _PUNTEGGIATURA.sub(" ", str(testo or "").lower())
    return " ".join(ripulito.split())[:200]


# Soglie delle urla: le stesse usate dalla moderazione del bot della stanza
# (12 caratteri minimi, 75% maiuscole). Il rapporto e non una regex di N
# maiuscole consecutive, altrimenti "COMPRA ORA IL MIO PACCHETTO" — che urla
# parola per parola — passerebbe liscia, come è successo al primo tentativo.
URLA_MIN_LETTERE = 12
URLA_RATIO = 0.75


def _urla(testo: str) -> bool:
    lettere = [c for c in testo if c.isalpha()]
    if len(lettere) < URLA_MIN_LETTERE:
        return False
    return sum(1 for c in lettere if c.isupper()) / len(lettere) >= URLA_RATIO


def classifica(testo, *, storia=(), primo_contatto=False) -> list[str]:
    """Nomi delle regole scattate sul testo (vuoto = nessuna).

    `storia`: testi normalizzati già visti da questo autore, dal più vecchio.
    `primo_contatto`: l'autore non aveva mai scritto su questa superficie.
    """
    testo = str(testo or "")
    regole = [nome for nome, pattern in _SPAM_COMPILED if pattern.search(testo)]
    if _urla(testo):
        regole.append("tutto_maiuscolo")
    normalizzato = normalizza(testo)
    if normalizzato:
        if normalizzato in list(storia)[:-1]:
            regole.append("ripetizione")
        if storia and normalizzato == list(storia)[-1]:
            regole.append("ripetizione")
    if primo_contatto and any(r in _RULES_FORTI for r in regole):
        regole.append("primo_contatto")
    # Ordine stabile e senza duplicati: finisce nei log.
    return sorted(set(regole))


class ChannelGuard:
    """Stato per autore: cronologia recente, raffica, strike con decadimento.

    Thread-safe (Flask serve le richieste da più thread) e con orologio
    iniettabile, così i test non fanno sleep veri.
    """

    def __init__(self, *, clock=time.time, max_authors=DEFAULT_MAX_AUTHORS,
                 history=DEFAULT_HISTORY, strike_decay_s=DEFAULT_STRIKE_DECAY_S,
                 strike_mute=DEFAULT_STRIKE_MUTE, strike_ban=DEFAULT_STRIKE_BAN,
                 flood_max=6, flood_window_s=15.0):
        self.clock = clock
        self.max_authors = max(1, int(max_authors))
        self.history = max(1, int(history))
        self.strike_decay_s = max(1.0, float(strike_decay_s))
        self.strike_mute = max(1, int(strike_mute))
        self.strike_ban = max(self.strike_mute, int(strike_ban))
        self.flood_max = max(1, int(flood_max))
        self.flood_window_s = max(1.0, float(flood_window_s))
        self._lock = threading.RLock()
        self._autori: dict = {}
        self._azioni: list = []          # cronologia diagnostica, limitata
        self._tips: dict = {}            # (canale, autore) -> {"ts", "importo"}
        self._spam: dict = {}            # canale -> [timestamp verdetti spam]
        self._memoria: dict = {}         # chiave -> ultima scrittura in memoria

    # ── stato interno ────────────────────────────────────────────────────────
    def _stato(self, chiave, momento):
        stato = self._autori.get(chiave)
        if stato is None:
            if len(self._autori) >= self.max_authors:
                # Si dimentica l'autore più vecchio: la pioggia di nomi nuovi è
                # un attacco, non un motivo per crescere all'infinito.
                piu_vecchio = min(self._autori, key=lambda k: self._autori[k]["ultimo"])
                self._autori.pop(piu_vecchio, None)
            stato = {"storia": [], "tempi": [], "messaggi": 0, "ultimo": momento,
                     "strike": 0, "ultimo_strike": 0.0}
            self._autori[chiave] = stato
        return stato

    def _strike(self, stato, momento) -> int:
        """Strike ancora validi: oltre la finestra di decadimento si riparte da 0."""
        if momento - stato["ultimo_strike"] > self.strike_decay_s:
            return 0
        return int(stato["strike"])

    def _azione(self, strike, autore, regole):
        """Cosa fare con questo autore. Nessuna punizione al primo colpo.

        Un avviso scritto in chat a un bot è rumore; a una persona vera è un
        torto, ed è il falso positivo che costa di più. Al primo colpo quindi si
        sta zitti (non si risponde), dal secondo si agisce.
        """
        motivo = ",".join(regole)[:80]
        if strike >= self.strike_ban:
            return {"kind": "moderate", "action": "ban", "user": autore, "reason": motivo}
        if strike >= self.strike_mute:
            return {"kind": "moderate", "action": "mute", "user": autore, "reason": motivo}
        return None

    # ── API ──────────────────────────────────────────────────────────────────
    def observe(self, *, channel, surface, author, text, now=None) -> dict:
        """Registra un messaggio e ritorna verdetto, motivi e (forse) un'azione."""
        momento = self.clock() if now is None else now
        with self._lock:
            stato = self._stato((channel, surface, author), momento)
            regole = classifica(text, storia=stato["storia"],
                               primo_contatto=stato["messaggi"] == 0)
            recenti = [t for t in stato["tempi"] if momento - t <= self.flood_window_s]
            recenti.append(momento)
            stato["tempi"] = recenti[-2 * self.flood_max:]
            if len(recenti) > self.flood_max:
                regole.append("raffica")
            stato["storia"] = (stato["storia"] + [normalizza(text)])[-self.history:]
            stato["messaggi"] += 1
            stato["ultimo"] = momento
            azione = None
            if regole:
                self._nota_spam(momento, channel)
                strike = self._strike(stato, momento) + 1
                stato["strike"] = strike
                stato["ultimo_strike"] = momento
                azione = self._azione(strike, author, sorted(set(regole)))
                if azione:
                    self._azioni = (self._azioni + [{**azione, "ts": momento,
                                                     "channel": channel}])[-30:]
            return {"author": author, "surface": surface,
                    "verdict": "spam" if regole else "ok",
                    "reasons": sorted(set(regole)),
                    "strikes": self._strike(stato, momento), "action": azione}

    def snapshot(self, channel=None) -> dict:
        """Stato leggibile per /channel/status: nessun testo degli utenti."""
        with self._lock:
            chiavi = [k for k in self._autori if channel is None or k[0] == channel]
            spam = sum(1 for k, v in self._autori.items()
                       if k in chiavi and v["strike"] and
                       self.clock() - v["ultimo_strike"] <= self.strike_decay_s)
            return {"authors_tracked": len(chiavi), "spam_authors_active": spam,
                    "max_authors": self.max_authors,
                    "strike_mute": self.strike_mute, "strike_ban": self.strike_ban,
                    "strike_decay_s": self.strike_decay_s,
                    "spam_events_recenti": len(self._spam.get(channel, [])
                                               if channel else
                                               [t for coda in self._spam.values()
                                                for t in coda]),
                    "tip_recenti": len([k for k in self._tips
                                        if channel is None or k[0] == channel]),
                    "recent_actions": [dict(a) for a in self._azioni
                                       if channel is None or a["channel"] == channel]}

    def reconfigure(self, *, strike_mute=None, strike_ban=None, flood_max=None,
                    flood_window_s=None) -> None:
        """Aggiorna le soglie SENZA perdere lo stato (strike già contati).

        Serve al salvataggio della tab Setup: cambiare le soglie non deve
        graziare chi era già a due strike.
        """
        with self._lock:
            if strike_mute is not None:
                self.strike_mute = max(1, int(strike_mute))
            if strike_ban is not None:
                self.strike_ban = max(self.strike_mute, int(strike_ban))
            if flood_max is not None:
                self.flood_max = max(1, int(flood_max))
            if flood_window_s is not None:
                self.flood_window_s = max(1.0, float(flood_window_s))

    # ── tip e promemoria ─────────────────────────────────────────────────────
    def registra_tip(self, *, channel, author, importo=None, now=None) -> None:
        """Segna che questa persona ha appena donato, SU QUESTO canale.

        Non compra nulla (l'agente non vende), ma cambia la risposta: chi ha
        appena tippato si ringrazia e non gli si chiede altro. Il registro vive
        qui perché la nota deve entrare nel prompt della risposta GENERATA dal
        CP, e la chiave è (canale, autore): un tip su una stanza non deve
        comparire nella risposta di un'altra.
        """
        momento = self.clock() if now is None else now
        with self._lock:
            self._tips[(channel, author)] = {"ts": momento, "importo": importo}
            if len(self._tips) > self.max_authors:
                piu_vecchio = min(self._tips, key=lambda k: self._tips[k]["ts"])
                self._tips.pop(piu_vecchio, None)

    def nota_tip(self, *, channel, entro_s=180.0, now=None) -> str:
        """Riga da mettere nel prompt quando qualcuno ha appena tippato.

        Vuota se nessun tip è recente: la nota non deve comparire per sbaglio.
        """
        momento = self.clock() if now is None else now
        with self._lock:
            recenti = [autore for (cha, autore), info in sorted(
                self._tips.items(), key=lambda kv: kv[1]["ts"])
                if cha == channel and momento - info["ts"] <= float(entro_s)]
        if not recenti:
            return ""
        return ("NOTA (tip appena ricevuti): " + ", ".join(recenti)
                + " → ringrazia a modo tuo e non chiedere nient'altro.")

    # ── ondate di spam e memoria ─────────────────────────────────────────────
    def _nota_spam(self, momento, channel: str) -> None:
        coda = self._spam.get(channel, []) + [momento]
        self._spam[channel] = coda[-200:]

    def spam_wave(self, channel: str, *, window_s=600.0, soglia=8, now=None) -> dict | None:
        """Un'ondata di spam SU QUESTO canale, o None.

        Una riga in memoria per messaggio sarebbe rumore; una riga per ondata è
        un fatto della stanza che l'agente può ricordare domani.
        """
        momento = self.clock() if now is None else now
        with self._lock:
            recenti = [t for t in self._spam.get(channel, [])
                       if momento - t <= float(window_s)]
        if len(recenti) < max(1, int(soglia)):
            return None
        return {"count": len(recenti), "window_s": float(window_s)}

    def should_remember(self, *, key: str, window_s=900.0, now=None) -> bool:
        """Debounce per chiave: evita di riscrivere la stessa memoria in loop."""
        momento = self.clock() if now is None else now
        with self._lock:
            ultimo = self._memoria.get(key)
            if ultimo is not None and momento - ultimo < float(window_s):
                return False
            self._memoria[key] = momento
            return True


class ReplyPacing:
    """Quando vale la pena scrivere: intervallo minimo, batch maturo, probabilità.

    Nel driver questa politica era mescolata al loop (cooldown, coda matura,
    `PROB_RISPOSTA_BATCH`): qui è una decisione sola, CON IL MOTIVO, così nei log
    si legge perché il bot è stato zitto invece di doverlo dedurre.
    """

    def __init__(self, *, clock=time.time, min_interval_s=DEFAULT_MIN_REPLY_INTERVAL_S,
                 batch_max_age_s=DEFAULT_BATCH_MAX_AGE_S,
                 batch_max_messages=DEFAULT_BATCH_MAX_MESSAGES,
                 probability=1.0, random_source=_random.random):
        self.clock = clock
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.batch_max_age_s = max(0.0, float(batch_max_age_s))
        self.batch_max_messages = max(1, int(batch_max_messages))
        self.probability = min(1.0, max(0.0, float(probability)))
        self.random_source = random_source
        self._lock = threading.RLock()
        self._ultima: dict = {}

    def note_reply(self, channel, now=None) -> None:
        """Registra che una risposta è stata inviata (il driver lo conferma)."""
        with self._lock:
            self._ultima[channel] = self.clock() if now is None else now

    def last_reply_age_s(self, channel, now=None):
        with self._lock:
            ultima = self._ultima.get(channel)
        if ultima is None:
            return None
        return max(0.0, (self.clock() if now is None else now) - ultima)

    def decide(self, *, channel, pending, oldest_age_s=0.0, force=False, now=None) -> dict:
        """`{"action": "reply"|"wait"|"skip", "reason": str}`.

        `force` è il caso "qualcuno ha chiamato l'agente per nome": allora non
        valgono né il cooldown né l'attesa del batch né il silenzio casuale —
        ignorare una domanda diretta è l'unico errore che si nota davvero.
        """
        if pending <= 0:
            return {"action": "wait", "reason": "nessun messaggio in attesa"}
        eta = self.last_reply_age_s(channel, now)
        if not force and eta is not None and eta < self.min_interval_s:
            return {"action": "wait",
                    "reason": f"cooldown: ultima risposta {int(eta)}s fa "
                              f"(minimo {int(self.min_interval_s)}s)"}
        if (not force and pending < self.batch_max_messages
                and oldest_age_s < self.batch_max_age_s):
            return {"action": "wait",
                    "reason": f"batch non maturo: {pending} messaggi, il più vecchio "
                              f"{oldest_age_s:.1f}s"}
        if not force and self.probability < 1.0 and self.random_source() > self.probability:
            return {"action": "skip", "reason": "silenzio volontario (probabilità)"}
        return {"action": "reply", "reason": "batch maturo"}


# ── Stato del driver e comandi dell'operatore ────────────────────────────────
# Perché servono: l'operatore vuole due cose che finora non aveva — dare comandi
# dal terminale ("metti il bot in pausa") e capire cosa sta facendo il driver
# ("perché non risponde?"). Il driver però non accetta connessioni in ingresso:
# è lui che fa polling verso il control-plane. Quindi i comandi si ACCODANO qui e
# il driver li ritira al giro successivo; lo stato viaggia nella direzione
# opposta. Nessuna porta aperta sulla macchina col browser, e la stessa
# autorizzazione di tutto il resto (token di canale).
COMANDI_DRIVER: tuple[str, ...] = ("on", "off", "auto", "status")
DEFAULT_MAX_COMMANDS = 20
DEFAULT_COMMAND_TTL_S = 900


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class ChannelRuntime:
    """Stato vivo per canale: l'ultima fotografia del driver + i comandi in coda.

    Un comando che nessuno ritira entro il TTL viene **scartato**: eseguire
    "metti in pausa" tre ore dopo, quando l'operatore non se lo ricorda più, è
    peggio che non eseguirlo. Vale la stessa disciplina per lo stato: se il
    driver tace da troppo, `stale` lo dice invece di mostrare un dato vecchio
    come se fosse fresco.
    """

    def __init__(self, *, clock=time.time, max_commands=DEFAULT_MAX_COMMANDS,
                 command_ttl_s=DEFAULT_COMMAND_TTL_S, state_stale_s=180.0):
        self.clock = clock
        self.max_commands = max(1, int(max_commands))
        self.command_ttl_s = max(30.0, float(command_ttl_s))
        self.state_stale_s = max(30.0, float(state_stale_s))
        self._lock = threading.Lock()
        self._stati: dict[str, dict] = {}
        self._coda: dict[str, list] = {}
        self._contatore = 0

    # ── stato riportato dal driver ───────────────────────────────────────────
    def report(self, channel: str, state: dict) -> dict:
        """Registra la fotografia del driver (valori ripuliti: qui non ci si fida)."""
        canale = str(channel or "").strip().lower() or "?"
        pulito = {}
        for chiave in ("mode", "active", "rate", "note", "error", "applied", "version"):
            if chiave not in (state or {}):
                continue
            valore = state[chiave]
            if isinstance(valore, (bool, int, float)):
                pulito[chiave] = valore
            elif valore is not None:
                pulito[chiave] = " ".join(str(valore).split())[:200]
        adesso = self.clock()
        with self._lock:
            precedente = self._stati.get(canale, {})
            self._stati[canale] = {"at": _iso(adesso), "ts": adesso, **pulito,
                                   "reports": int(precedente.get("reports", 0)) + 1}
            return dict(self._stati[canale])

    def state(self, channel: str, *, now=None) -> dict | None:
        adesso = self.clock() if now is None else now
        with self._lock:
            stato = self._stati.get(str(channel or "").strip().lower())
            if not stato:
                return None
            eta = max(0.0, adesso - stato.get("ts", adesso))
            return {**stato, "age_s": round(eta, 1), "stale": eta > self.state_stale_s}

    # ── comandi dell'operatore ──────────────────────────────────────────────
    def queue(self, channel: str, command: str, *, note: str = "", source: str = "") -> dict:
        canale = str(channel or "").strip().lower() or "?"
        nome = str(command or "").strip().lower()
        if nome not in COMANDI_DRIVER:
            raise ChannelError(f"comando non previsto: {nome!r} (disponibili: "
                               f"{', '.join(COMANDI_DRIVER)})")
        adesso = self.clock()
        with self._lock:
            self._contatore += 1
            comando = {"id": f"cmd-{self._contatore}", "command": nome,
                       "note": " ".join(str(note or "").split())[:200],
                       "source": str(source or "")[:64], "at": _iso(adesso), "ts": adesso}
            coda = self._coda.setdefault(canale, [])
            coda.append(comando)
            # Il tetto è sulla CODA: se nessuno ritira, i comandi vecchi cadono.
            del coda[:max(0, len(coda) - self.max_commands)]
            return dict(comando)

    def pending(self, channel: str, *, drain=True, now=None) -> list:
        """Comandi da consegnare al driver (e, con drain, già considerati consegnati)."""
        adesso = self.clock() if now is None else now
        canale = str(channel or "").strip().lower() or "?"
        with self._lock:
            coda = self._coda.get(canale, [])
            vivi = [c for c in coda if adesso - c.get("ts", adesso) <= self.command_ttl_s]
            scaduti = len(coda) - len(vivi)
            self._coda[canale] = [] if drain else vivi
            fuori = [dict(c) for c in vivi]
        if scaduti:
            fuori.append({"_expired": scaduti})
        return fuori

    def describe(self, *, now=None) -> dict:
        """Per /channel/status: nessun segreto, solo stato e conteggi."""
        adesso = self.clock() if now is None else now
        fuori = {}
        for canale in sorted(set(self._stati) | set(self._coda)):
            stato = self.state(canale, now=adesso) or {}
            fuori[canale] = {"state": {k: v for k, v in stato.items() if k != "ts"},
                             "pending_commands": len([c for c in
                                                      self.pending(canale, drain=False, now=adesso)
                                                      if "command" in c])}
        return fuori
