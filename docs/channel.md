# Channels — conversations the control plane cannot reach

A **channel** is a conversation surface the control plane cannot talk to directly: the live chat of a room, its private messages, a bot on another platform. The direction of the protocol is the same as the web node's (`shared/web_node.py`): the client **pulls** decisions and reports the outcome, because on the other side there is a DOM, not an endpoint.

## What stays where

| | driver (outside the repo) | control plane |
|---|---|---|
| reads/writes the DOM, sends keys, opens tabs | ✓ | |
| knows the platform selectors and the field limits | ✓ | |
| decides **who is spam** and whether to moderate | | ✓ (`shared/channel.py`) |
| decides **when** to answer (cooldown, batch, silence) | | ✓ |
| writes the reply, with the declared identity | | ✓ (persona) |
| checks the reply does not claim to be human | | ✓ (audit) |
| logs, counters, `/channel/status` | | ✓ |

The driver keeps exactly one responsibility: **the browser**. It also keeps a local fallback: with no token, or when the control plane is unreachable, the old local path (own prompt, own Ollama) takes over — the channel is an improvement, not a dependency to go on air.

## The contract

Auth: header `X-Hyperspace-Channel-Token`, one token per channel (`CHANNEL_CLIENTS="cam4=<token>"`, >= 32 characters, constant-time comparison). Fail-closed: with no token configured every route answers **503**; a wrong token answers **401**.

| Endpoint | Request | Response |
|---|---|---|
| `POST /channel/ingest` | `{surface, events: [{author, text, key}]}` | `{accepted, spam, results: [{author, verdict, reasons, strikes, action}], actions}` |
| `POST /channel/reply` | `{surface, context: [{author, text}], pending, oldest_age_s, force, max_chars}` | `{action: "reply"\|"wait"\|"skip", text?, reason, disclosure}` |
| `POST /channel/result` | `{kind, ok, target?, error?, duration_ms?}` | `{ok}` |
| `GET /channel/status` | — | policy (names only), guard counters, pacing, model |

`GET /channel/status`, like `/connectors` and `/mcp/status`, is read-only and holds no secrets.

**No long-poll.** The driver asks when it believes the batch is ripe (it is the one measuring the room) and the control plane answers with the decision. A blocking outbox would add concurrency and timeouts for nothing: the reply is one batched sentence, not a job queue.

## What the classification does — and does not do

`verdict: "spam"` means **"do not answer, count a strike"**, not "ban". The rules are named and they land in the logs, so "why didn't it answer that person?" is a sentence, not a number:

`link`, `promo`, `solo_simboli`, `tutto_maiuscolo` (75% of >= 12 letters, the same thresholds as the room bot), `ripetizione` (same normalised text again), `raffica` (more than `CHANNEL_FLOOD_MAX` messages in the window), `primo_contatto` (first message from that author that already carries a link or a promo).

Escalation is deliberately slow: the **first** strike does not punish anyone, because a false positive hits a real viewer; from `CHANNEL_STRIKE_MUTE` the CP asks the driver to mute, from `CHANNEL_STRIKE_BAN` to ban. Strikes decay by themselves after 30 minutes without new violations, tracked authors are capped (the flood of new names is an attack, not a reason to grow), and the state never stores more than the last few texts per author.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CHANNEL_CLIENTS` | — | `cam4=<token>;cb=<token>`; empty = no channel served |
| `CHANNEL_ENABLED` | `true` | closes every route without deleting the tokens |
| `CHANNEL_MODEL` | default model | a room replies with one sentence: a small fast model is usually better |
| `CHANNEL_MAX_TOKENS` | `160` | generation budget (reasoning is off explicitly) |
| `CHANNEL_MIN_REPLY_INTERVAL_S` | `25` | minimum gap between two generated replies |
| `CHANNEL_BATCH_MAX_AGE_S` / `CHANNEL_BATCH_MAX_MESSAGES` | `6` / `6` | when a batch counts as ripe |
| `CHANNEL_REPLY_PROBABILITY` | `1.0` | below 1.0 the CP sometimes stays silent on purpose |
| `CHANNEL_FLOOD_MAX` / `CHANNEL_FLOOD_WINDOW_S` | `6` / `15` | burst detection |
| `CHANNEL_STRIKE_MUTE` / `CHANNEL_STRIKE_BAN` | `2` / `3` | escalation thresholds |

All of them are in the Setup tab under **Canali esterni** (the tokens as password fields). Saving applies immediately; strike counters are preserved (`reconfigure`, not a fresh guard).

On the driver side, three variables: `CHANNEL_URL` (default `http://127.0.0.1:8088`), `CHANNEL_TOKEN` (empty = local mode) and `CHANNEL_NAME`.

## Verify

```
python -c "import secrets; print(secrets.token_hex(32))"          # il token
# poi, nel .env del control-plane:
#   CHANNEL_CLIENTS="cam4=<token>"

curl -s -H "X-Hyperspace-Channel-Token: <token>" http://127.0.0.1:8088/channel/status
curl -s -X POST -H "X-Hyperspace-Channel-Token: <token>" -H 'Content-Type: application/json' \
     -d '{"surface":"pm","events":[{"author":"botx","text":"vieni su t.me/x"}]}' \
     http://127.0.0.1:8088/channel/ingest
```

The second call must answer `verdict: "spam"` with reasons `link` and `primo_contatto`; the answer of `GET /channel/status` must never contain the token. In the dashboard the events appear in Logs with `type=channel`.

## Context, and how much of the room the model actually sees

Four knobs decide what the model sees, and all of them are read **at call time**
(change them in Setup → *Canali esterni* and they apply immediately):

| key | default | what it does |
| --- | --- | --- |
| `CHANNEL_CONTEXT_MESSAGES` | `20` | how many of the last messages go into the prompt |
| `CHANNEL_CONTEXT_CHARS` | `400` | truncation per message (one wall of text must not eat the prompt) |
| `CHANNEL_NUM_CTX` | `8192` | the context window asked of Ollama (`num_ctx`) |
| `CHANNEL_MAX_TOKENS` | `160` | cap on the reply itself (one line, trimmed by `max_chars`) |

`CHANNEL_NUM_CTX` exists for a concrete reason: without an explicit `num_ctx`,
Ollama uses its own default (often 4096) and truncates the **beginning** of the
prompt — which is where the identity block and the boundaries live. Verified on
a live run: `ollama ps` reports `CONTEXT 8192` after a channel reply, and a fact
placed in the *first* of 20 messages is recalled by the model at the end.

**Cost of a longer context, measured** on this machine (20 messages, `num_ctx`
8192, same prompt): `qwen3.5:4b` ≈ **15 s**, a 9B Q6_K ≈ **21 s**. A live room
pays that pause, so keep a small fast model in `CHANNEL_MODEL` and leave the big
one to the identity dream (`PERSONA_DREAM_MODEL`), where nobody is waiting.

The driver's `CHANNEL_TIMEOUT_S` (default 20 s) must be **larger than the model's
latency**, otherwise the reply arrives after the driver has already given up and
fallen back to its local model — inconsistent persona without an obvious error.
`scripts/start.ps1 -Driver` sets it to 35 s and prints that it did.

## Memory: what the room leaves behind

The point of memory is that Aurora does not restart from zero every evening and repeat the same lines. What goes in, and what does not:

| written to shared memory | not written |
|---|---|
| a tip (`kind: "tip"` event) | every message |
| a spam wave (N suspicious messages in the window) | every single spam verdict |
| a successful moderation | a failed one (that is an operational problem, it belongs to the logs) |

Two mechanisms keep this clean: the **debounce** (`should_remember`) guarantees one entry per fact, so a wave lasting ten minutes leaves one line instead of a hundred; and the entries are readable sentences, not counters — `[cam4] ondata di spam: 12 messaggi sospetti in 10 minuti`, `[cam4] mario ha donato 500`.

**Where the memory actually goes matters, and it fails silently.** With
`MEMORY_BACKEND=hermes` every write needs the Hermes bridge: if it is not
running, the entry is lost, the log says so once, and the agent simply never
remembers the room — the kind of failure you notice three evenings later. On a
single machine the alternative is `MEMORY_BACKEND=legacy` with `MEMORY_FILE`
inside a **mounted** volume:

```bash
MEMORY_BACKEND=legacy
MEMORY_FILE=/app/memory/memory.json.gz   # /app/memory is volume-mounted
```

Without `MEMORY_FILE` the file lands in `/app/memory.json.gz`, which is *not* a
volume: every rebuild of `hyperspace-core` would wipe the room's memory (and the
identity dream's material along with it). Verified live: after one tip the entry
appears in `GET /memory` and the file is in the mounted directory, so it survives
a rebuild.

What comes back: `_channel_memories()` reads the last few entries for that channel (from `_load_memory()`, the same path as `/memory`, no full-text search) and puts them in the reply prompt as *"Cose che ricordi di questa stanza"*. Alongside it, the tip registry adds *"NOTA (tip appena ricevuti): mario → ringrazia a modo tuo"*: the tip no longer buys anything, but it still changes the answer, and that note is now generated by the control plane instead of the driver.

If the memory backend is not reachable (Hermes down, disk read-only) the write fails **soft**: one warning line in the logs, no exception, the reply still goes out. A room must not go silent because a memory write failed.

Inspect it with `GET /memory` — the channel entries carry `type: "channel"`, `kind` (`tip`, `spam_wave`, `moderation`) and `channel`.

## Getting started in three commands

```
# 1. token: scritto nel .env del control-plane (canale "cam4")
python scripts/channel_token.py cam4 --write

# 2. control-plane (Docker: docker compose up -d control-plane)
python control-plane/main.py

# 3. driver, sulla macchina col browser
set CHANNEL_URL=http://127.0.0.1:8088
set CHANNEL_TOKEN=<il token stampato al passo 1>
py -3 cam4_chatbot.py --check      # verifica tutto PRIMA di aprire il browser
py -3 cam4_chatbot.py              # vai in onda
```

`--check` verifica anche il canale: che il CP risponda, che elenchi `cam4` fra i canali autorizzati, e che il token sia accettato. Il token è valido se la prova autenticata riceve **400** ("events mancante o vuoto") — significa che l'autenticazione è passata; un token sbagliato si ferma prima, con **401**.

**Con un comando solo (Windows):** `.\scripts\start.ps1 -Driver` fa i tre passi —
avvia lo stack, aspetta che il control-plane risponda, stampa un verdetto
(servizi su/giù, canali configurati, identità e sogno) e lancia il driver con
`CHANNEL_URL` e `CHANNEL_TOKEN` già presi da `.env` (`CHANNEL_CLIENTS`), quindi non
c'è nessun token da incollare. Il driver si trova con `-DriverPath`, con
`CHANNEL_DRIVER_PATH`, o in `$HOME\cam4_chatbot.py`. `-Check` non avvia nulla e
riporta solo lo stato (utile quando "il bot non risponde"); `-Stop` ferma tutto
senza toccare i volumi.

In alternativa al passo 1 il token si può incollare nella tab Setup → **Canali esterni** (`CHANNEL_CLIENTS`), che vale subito senza riavviare il control-plane.

Se il driver non trova il CP, o il token non combacia, **non si rompe niente**: torna al modello locale e lo scrive all'avvio e nel log del giro (`[CANALE] reply: ...`).

## Commands, from the room or from the terminal

The bot obeys `!bot on | off | auto | status`, written **in public chat with your
own account**: `!bot off` stops it from talking, `!bot auto` returns it to the
automatic mode (mute on a busy chat, replies when it goes quiet).

If those commands "do not work", the cause is almost always the same and it is
not the command parser: the bot reads *your* messages through `msg_propri_selector`
and CAM4's wrapper class changes with the site's updates. The driver now

- tries the configured selector and then the known variants, and remembers the one
  that worked;
- prints **once** what it found (`[CAM4][COMANDI] non vedo messaggi TUOI: …`) with
  the selectors it tried and the remedy (paste the right class into
  `msg_propri_selector`), instead of staying silent.

The same three modes are reachable **without touching the browser**, and that is
the point of `hs mode`:

```bash
python scripts/hs.py mode off     # queued; the driver collects it on its next cycle
python scripts/hs.py mode         # what mode it is in right now, and since when
```

The mechanism is worth knowing: the driver **polls** (`GET /channel/commands`) and
*publishes* its state (`POST /channel/state`). No inbound port on the machine with
the browser session, and the same channel token authorizes everything. A command
nobody collects expires (15 minutes) instead of firing hours later.

`python scripts/hs.py` also gives `status`, `channels`, `logs`, `memory`,
`persona`, `dreams`, `dream run|promote|reject`, `models` and `host` — see
`python scripts/hs.py --help`. If a channel has no `CHANNEL_TOKEN` on the driver
side, `hs status` says so ("nessuno stato ricevuto"): the driver is not reporting,
which is the truth you want when the bot seems dead.

## Known limits

- **Memory writes depend on the backend.** With `MEMORY_BACKEND=hermes` a memory entry needs the Hermes service: if it is not there the entry is lost (soft failure, logged) and the agent simply does not remember it.
- **Moderation actions need the message element.** Mute/ban from the CP are executed through the message menu, so the element must be in the batch the driver just read; otherwise the action is reported as failed (visible in `/channel/status` and the logs) instead of clicking something random.
- **The reply is generated synchronously** in `/channel/reply`: a slow model makes that call slow. Reasoning is off by design and the driver uses a short timeout with a local fallback, but a channel with a heavy model will feel it.
- **Streaming is not part of the contract**: replies arrive whole.
- **The driver's local moderation is not disabled automatically.** With a channel active the CP decides; leaving `MODERAZIONE_ATTIVA=True` in the driver too would give two judges.
- **`CHANNEL_ENABLED=false` and an empty `CHANNEL_CLIENTS` look the same to the driver** (it falls back locally): if the bot seems "not using HyperSpace", read `/channel/status` first.

## Telegram

Il terzo canale, dopo CAM4/Chaturbate e Discord — ed è l'unico il cui driver vive
**nel repo**: `scripts/telegram_bot.py`. Il motivo sta nel contratto: un driver
esiste per guidare ciò che il control-plane non raggiunge, e qui non c'è un
browser ma l'API del bot (`getUpdates` in long-polling, nessun webhook, nessuna
porta in ingresso).

Due token, due posti, due ruoli — confonderli è l'errore che si manifesta come un
401 che non dice quale dei due lati è sbagliato:

| Token | Dove | Ruolo |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` (da @BotFather) | `data\telegram-bot.env`, ignorato da git | il driver pubblica su Telegram |
| token del canale | `CHANNEL_CLIENTS="cam4=…;telegram=…"` nel `.env` | il driver parla col control-plane |

Il token del bot **non** va nel `.env`: quel file arriva a tutti i container
(`env_file`) e un container non deve poter pubblicare su Telegram.

Avvio: `.\scripts\start-telegram.ps1` (o `-Check`, che non avvia nulla e dice cosa
manca: token del bot, token del canale, privacy mode, control-plane raggiungibile,
con 401 e 503 distinti). L'identità resta una sola e vive in
`data/persona-aurora.json`: il driver non la conosce, la inietta il CP.

### Privacy mode: la decisione che cambia cosa vede il bot

`getMe` riporta `can_read_all_group_messages`. Se è `false`, in gruppo il bot
riceve **solo menzioni e comandi**, non la conversazione. Si cambia da @BotFather
(`/setprivacy` → Disable) o rendendo il bot admin del gruppo: non esiste un metodo
API per farlo, quindi è un passo manuale — ed è il primo sospetto quando "il bot
non risponde".

### Due bot nello stesso gruppo

Due Aurora nella stessa stanza sono possibili, ma il driver deve sapere due cose:

- **i messaggi degli altri bot si ignorano sempre** (`from.is_bot`): senza questo
  A pubblica → B legge → B risponde → A legge, all'infinito. Non è spam, è
  cortesia: nessuna moderazione lo ferma.
- con `TELEGRAM_REQUIRE_MENTION=1` si risponde **solo** a chi ci nomina
  (`@username`) o risponde a un nostro messaggio. Gli altri messaggi restano nel
  contesto — così la risposta ha il filo della conversazione — ma non fanno
  intervenire.

La combinazione utile in un gruppo con più bot è quindi **privacy OFF** (vede
tutto, quindi ha contesto) **+ mention ON** (parla solo se chiamata). In
alternativa `python scripts/hs.py mode off` silenzia del tutto uno dei due.

### Vetrina del bot (nome, about, descrizione)

Non è il prompt: è ciò che legge una persona prima di scrivere. Quello che legge
il modello è il documento d'identità.

```powershell
python scripts\telegram_profile.py                 # legge e basta
python scripts\telegram_profile.py --apply         # scrive nome/about/descrizione
python scripts\telegram_profile.py --token-env data\telegram-bot2.env `
       --name "…" --about "…" --description "…"    # il SECONDO bot
```

Un file di segreti per bot e i tre override: i limiti dell'API (64/120/512) si
vedono prima di sbagliare, e dopo si rilegge cosa il bot risponde davvero.

### Limiti di questo driver

- `!bot on|off|auto` **non** è implementato qui: la modalità si cambia con
  `python scripts/hs.py mode off|auto` e il driver la ritira entro ~15 s (polling
  su `/channel/commands`). `!presentati` / `!intro` invece funzionano, perché li
  gestisce il control-plane.
- Il driver **consuma** gli update mentre legge: se il control-plane non risponde,
  i messaggi di quel giro si perdono (non c'è una coda persistente). Prima lo
  stack, poi il driver.
- `TELEGRAM_REQUIRE_MENTION=1` richiede `getMe` all'avvio: senza `@username` il
  driver esce invece di restare muto per sempre (un bot che non sa il proprio nome
  non riconoscerebbe nessuna chiamata).
