# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/config.py
# TUTTO QUELLO CHE IL CONTROL-PLANE LEGGE DALL'AMBIENTE.
#
# Un unico posto per le 130 costanti di configurazione: inferenza, routing,
# telemetria dei nodi, federazione, memoria, web node, ricerca, forge.
#
# Due regole, perche' sono le uniche cose che rendono questo modulo utile:
#
# 1. ogni valore ha il suo `os.getenv` con il default scritto qui accanto. Un
#    default in un solo posto e non anche in .env.example e nella tab Setup: la
#    tab Setup e' in cp/env_meta.py e descrive cosa e' modificabile da remoto,
#    non cosa il codice si aspetta.
# 2. nessuna di queste costanti si riassegna a caldo. Se una deve cambiare dopo
#    l'avvio, allora non e' una costante di configurazione ma stato, e va
#    dichiarata `global` e ricalcolata da una funzione (vedi cp/canali.py per
#    il caso dei canali).
#
# BASE_DIR e' rifatto qui invece di importato da main.py: questo modulo non deve
# dipendere dal monolite, e con `dirname(dirname(__file__))` arriva allo stesso
# identico percorso.

import os

from shared.hermes_memory import HermesMemoryClient

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── CONFIG ────────────────────────────────────────────────────────────────────
# I path che derivano da BASE_DIR stanno qui e non in main.py perche' servono
# anche a cp/instagram.py: main.py li calcolava alla riga 58, prima ancora
# dell'inserimento di sys.path, e duplicarli avrebbe dato due posti in cui il
# fallback di `/app/data` puo' divergere senza che nessuno se ne accorga.
VITALITY_BIG_LEVEL = max(0, int(os.getenv("VITALITY_BIG_LEVEL", "3")))
VITALITY_BIG_MODEL = os.getenv("VITALITY_BIG_MODEL", "").strip()
# ── ROUTING: PESI DELLO SCORING ─────────────────────────────────────────────
# v1.05: scoring IBRIDO metric-driven (vedi control-plane/routing.py). Il
# blocco QUALITÀ (latenza/throughput per modello, pressione VRAM motore)
# domina quando i campioni /metrics sono freschi; il blocco STRUTTURALE
# (vram/tier/uptime/backend) è il fallback quando i dati mancano. I pesi
# strutturali restano configurabili via ROUTING_WEIGHT_* (sono relativi, non
# devono sommare a 1); quelli di qualità via ROUTING_WEIGHT_LATENCY/TPUT/GPU.
# Esposti in sola lettura via /config/routing-weights.
ROUTING_WEIGHT_VRAM   = float(os.getenv("ROUTING_WEIGHT_VRAM", "0.55"))
ROUTING_WEIGHT_LOAD   = float(os.getenv("ROUTING_WEIGHT_LOAD", "0.25"))
ROUTING_WEIGHT_TIER   = float(os.getenv("ROUTING_WEIGHT_TIER", "0.10"))
ROUTING_WEIGHT_UPTIME = float(os.getenv("ROUTING_WEIGHT_UPTIME", "0.10"))
# Peso del paradigma di serving (backend_type: inference_server vs
# model_manager) nel blocco strutturale. Il punteggio per backend_type vive
# in shared/engine_profiles.py — unica fonte di verità.
ROUTING_WEIGHT_ENGINE = float(os.getenv("ROUTING_WEIGHT_ENGINE", "0.15"))
# Blocco qualità (in funzione delle metriche osservate).
ROUTING_WEIGHT_LATENCY = float(os.getenv("ROUTING_WEIGHT_LATENCY", "0.45"))
ROUTING_WEIGHT_TPUT    = float(os.getenv("ROUTING_WEIGHT_TPUT", "0.35"))
ROUTING_WEIGHT_GPU     = float(os.getenv("ROUTING_WEIGHT_GPU", "0.20"))
# Bilanciamento: penalità "ultimo scelto" — il nodo appena usato viene
# lievemente depenalizzato, con decadimento esponenziale nella finestra.
# Piccola a default: rompe i pareggi senza far perdere un nodo migliore.
ROUTING_RECENT_PENALTY  = float(os.getenv("ROUTING_RECENT_PENALTY", "0.10"))
ROUTING_RECENT_WINDOW_S = float(os.getenv("ROUTING_RECENT_WINDOW_S", "45"))

# I pesi con cui il mesh sceglie un nodo. Nessuno domina: un peso alto non esclude
# gli altri, li penalizza. La tab Setup li espone perché sono la leva che
# l'operatore ha quando un nodo viene scelto male, e `/config/routing-weights` li
# restituisce in sola lettura.
_ROUTING_WEIGHTS = {
    "vram": ROUTING_WEIGHT_VRAM,
    "load": ROUTING_WEIGHT_LOAD,
    "tier": ROUTING_WEIGHT_TIER,
    "uptime": ROUTING_WEIGHT_UPTIME,
    "backend": ROUTING_WEIGHT_ENGINE,
    "latency": ROUTING_WEIGHT_LATENCY,
    "tput": ROUTING_WEIGHT_TPUT,
    "gpu": ROUTING_WEIGHT_GPU,
    "recent_penalty": ROUTING_RECENT_PENALTY,
    "recent_window": ROUTING_RECENT_WINDOW_S,
}

# Se questo nodo partecipa alla mesh come nodo locale (per l'inferenza diretta e
# per l'annuncio) e l'endpoint con cui si annuncia. Vuoto = partecipa ma non si
# annuncia: nodo noto al suo host, ignoto agli altri.
_LOCAL_NODE_ENABLED = os.getenv("LOCAL_NODE_ENABLED", "true").lower() not in ("0", "false", "no")
_LOCAL_NODE_ENDPOINT = os.getenv("LOCAL_NODE_ENDPOINT", "")  # es. http://192.168.1.10:11434

DIARIO_IMMAGINI_DIR = os.getenv("DIARIO_IMMAGINI_DIR", "").strip() or os.path.join(
    BASE_DIR, "..", "data", "diario-immagini")
DIARIO_FILE         = os.getenv("FEED_DIARIO_FILE", "").strip() or os.path.join(
    BASE_DIR, "data", "diario.json")
TYPOGRAPHY_IMAGES_DIR = os.getenv("TYPOGRAPHY_IMAGES_DIR", "/app/data/typography-images")
INSTAGRAM_VIP_FILE  = os.getenv("INSTAGRAM_VIP_FILE", "").strip() or os.path.join(
    BASE_DIR, "data", "instagram_vips.json")
INSTAGRAM_MEMORY_FILE = os.getenv("INSTAGRAM_MEMORY_FILE", "").strip() or os.path.join(
    BASE_DIR, "data", "instagram-memory.json")
INSTAGRAM_REPLY_OUTBOX_FILE = os.getenv(
    "INSTAGRAM_REPLY_OUTBOX_FILE", "/app/data/instagram-replies.json")
INSTAGRAM_LANGUAGE_CODEX = os.getenv("INSTAGRAM_LANGUAGE_CODEX", "").strip() or \
    "/repo/data/instagram-language-codex.json"

NODE_ENDPOINTS     = [e.strip() for e in os.getenv("NODE_ENDPOINTS", "node:8084").split(",") if e.strip()]
OLLAMA_URL         = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
DEFAULT_MODEL      = os.getenv("OLLAMA_MODEL", "")
INFERENCE_BACKEND  = os.getenv("INFERENCE_BACKEND", "ollama")
REGISTRY_URL       = os.getenv("REGISTRY_URL", "http://registry:8086")
_AUTHORITY_URL     = os.getenv("AUTHORITY_URL", "http://authority:8080")
_AUTHORITY_ENABLED = os.getenv("AUTHORITY_ENABLED", "false").lower() == "true"
UI_BRIDGE_URL      = os.getenv("UI_BRIDGE_URL", "http://localhost:8099")
FORGE_DIR          = os.getenv("FORGE_DIR", "/app/data/forge")
FORGE_ADMIN_TOKEN  = os.getenv("FORGE_ADMIN_TOKEN", "").strip()
FORGE_MODEL        = os.getenv("HS_MODEL_CODER", DEFAULT_MODEL)
CODE_SERVER_PORT   = os.getenv("CODE_SERVER_PORT", "8443").strip()

MEMORY_FILE_GZ     = (os.getenv("MEMORY_FILE", "").strip()
                      or os.path.join(BASE_DIR, "memory.json.gz"))
MEMORY_TTL_DAYS    = int(os.getenv("MEMORY_TTL_DAYS", "7"))
MEMORY_MAX_ENTRIES = int(os.getenv("MEMORY_MAX_ENTRIES", "200"))
MEMORY_BACKEND     = os.getenv("MEMORY_BACKEND", "hermes").strip().lower()
_hermes_memory     = HermesMemoryClient()
# Web node (worker nel browser): non e' indirizzabile, quindi si registra e poi
# tira il lavoro con un long-poll — vedi shared/web_node.py. Tutti i limiti sono
# env, perche' un web node e' per definizione su hardware sconosciuto.
WEB_NODE_ENABLED         = os.getenv("WEB_NODE_ENABLED", "true").lower() == "true"
WEB_NODE_MAX_NODES       = int(os.getenv("WEB_NODE_MAX_NODES", "64"))
WEB_NODE_MAX_QUEUE       = int(os.getenv("WEB_NODE_MAX_QUEUE", "32"))
WEB_NODE_MAX_PAYLOAD     = int(os.getenv("WEB_NODE_MAX_PAYLOAD_BYTES", str(64 * 1024)))
WEB_NODE_TASK_TTL_S      = int(os.getenv("WEB_NODE_TASK_TTL_S", "60"))
WEB_NODE_MAX_POLL_S      = int(os.getenv("WEB_NODE_MAX_POLL_S", "30"))
WEB_NODE_HEARTBEAT_S     = int(os.getenv("WEB_NODE_HEARTBEAT_S", "30"))

# SearXNG — motore di ricerca self-hosted (container searxng nella stessa rete Docker)
# Override via env: SEARXNG_URL=http://searxng:8080
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")


# ── TELEMETRIA NODI: pull periodico di /metrics dai nodi ───────────────────
# Il control-plane interroga ogni nodo attivo sul suo /metrics (payload
# backend normalizzato, vedi node/backend_metrics.py) a cadenza indipendente
# dall'heartbeat di /status: lo stato operativo (routing) e la telemetria
# (diagnosi, score breakdown, futuri termini di scoring osservati) restano
# separati. I campioni restano in una finestra volatile in-memory
# (METRICS_WINDOW) per i mini-grafici della dashboard; lo storico persistente
# è una fase successiva (niente DB qui per ora).
METRICS_POLL_INTERVAL_S = int(os.getenv("METRICS_POLL_INTERVAL_S", "20"))
METRICS_POLL_TIMEOUT_S  = int(os.getenv("METRICS_POLL_TIMEOUT_S", "4"))
METRICS_WINDOW          = int(os.getenv("METRICS_WINDOW", "20"))
# Fetch dei nodi in PARALLELO: la raccolta seriale (timeout l'uno) sforerebbe
# l'intervallo con molti nodi. METRICS_MAX_WORKERS limita la concorrenza.
METRICS_MAX_WORKERS = int(os.getenv("METRICS_MAX_WORKERS", "8"))
# Backoff sui nodi irraggiungibili: un nodo giù NON va martellato a ogni ciclo.
# next_try_at = now + min(BASE * 2^fail_streak, MAX). Lo stale sample resta
# servito (stale=true) finché il nodo non torna raggiungibile.
METRICS_BACKOFF_BASE_S = float(os.getenv("METRICS_BACKOFF_BASE_S", "10"))
METRICS_MAX_BACKOFF_S  = float(os.getenv("METRICS_MAX_BACKOFF_S", "120"))
# Versione dello schema /metrics attesa. Deve combaciare con
# NODE_METRICS_SCHEMA_VERSION in node/backend_metrics.py: i payload con
# versione diversa (deployment eterogeneo) restano esposti ma marcati
# schema_mismatch, così il consumatore non li interpreta alla cieca.
NODE_METRICS_SCHEMA_VERSION = 3
# Quanti nodi candidati (per score decrescente) il control-plane prova in
# sequenza prima di ricadere su federazione/ollama-direct, quando un nodo
# risponde "occupato" (503 node_busy_timeout).
ROUTING_MAX_CANDIDATES = int(os.getenv("ROUTING_MAX_CANDIDATES", "3"))

# OmniRoute — gateway esterno verso 278+ provider AI (molti free-tier), usato
# come ultimo livello di fallback quando NESSUN nodo della mesh (locale o
# federato) puo' rispondere. Non sostituisce l'inferenza locale: entra in
# gioco solo quando tutto il resto ha gia' fallito. Funziona gia' con
# provider free-tier di default, senza chiave configurata; OMNIROUTE_API_KEY
# e' opzionale, per quando si collegano provider propri dalla dashboard
# (http://<host>:20128). OMNIROUTE_ENABLED=false lo disattiva del tutto.
OMNIROUTE_URL      = os.getenv("OMNIROUTE_URL", "http://omniroute:20128").rstrip("/")
OMNIROUTE_API_KEY  = os.getenv("OMNIROUTE_API_KEY", "").strip()
OMNIROUTE_MODEL    = os.getenv("OMNIROUTE_MODEL", "auto")
OMNIROUTE_ENABLED  = os.getenv("OMNIROUTE_ENABLED", "true").lower() == "true"

# Prefissi puramente cosmetici per il menu modelli di Open WebUI — 🕸️ per i
# modelli serviti dalla mesh locale, 🌐 per la voce che instrada esplicitamente
# a OmniRoute (provider esterni). Vengono aggiunti solo in /v1/models e tolti
# subito in /v1/chat/completions prima di usare il nome per il routing vero:
# nessun nodo/Ollama/OmniRoute li riconoscerebbe come nomi di modello reali.
MESH_MODEL_ICON    = "🕸️ "
OMNIROUTE_MODEL_ID = "🌐 OmniRoute (auto)"

# Compressione prompt (Caveman via OmniRoute) — opzionale, disattivata di
# default perche' comprime aggressivamente il fraseggio e puo' confondere
# modelli piccoli/quantizzati se non testata sul proprio caso d'uso. Quando
# attiva: i prompt lunghi diretti a un nodo della mesh locale passano prima
# da OmniRoute (POST /api/compression/preview, l'engine Caveman reale, non
# una reimplementazione nostra) per essere accorciati senza perdere
# sostanza; le chiamate che vanno gia' a OmniRoute (fallback o selezione
# esplicita) ricevono lo stesso trattamento gratis via header, senza
# round-trip aggiuntivo. Fail-open su qualunque errore: in caso di dubbio
# si manda il prompt originale, mai un errore all'utente per questo.
PROMPT_COMPRESSION_ENABLED   = os.getenv("PROMPT_COMPRESSION_ENABLED", "false").lower() == "true"
PROMPT_COMPRESSION_MODE      = os.getenv("PROMPT_COMPRESSION_MODE", "standard")
PROMPT_COMPRESSION_MIN_CHARS = int(os.getenv("PROMPT_COMPRESSION_MIN_CHARS", "200"))

# Nightly Development Dream: opt-in, one review-gated proposal per local day.
NIGHTLY_DEV_ENABLED      = os.getenv("NIGHTLY_DEV_ENABLED", "false").lower() == "true"
NIGHTLY_DEV_START_HOUR   = int(os.getenv("NIGHTLY_DEV_START_HOUR", "1"))
NIGHTLY_DEV_END_HOUR     = int(os.getenv("NIGHTLY_DEV_END_HOUR", "5"))
NIGHTLY_DEV_IDLE_SECONDS = int(os.getenv("NIGHTLY_DEV_IDLE_SECONDS", "3600"))
NIGHTLY_DEV_MODEL        = os.getenv("NIGHTLY_DEV_MODEL", "").strip() or DEFAULT_MODEL
NIGHTLY_DEV_DATA_DIR     = os.getenv("NIGHTLY_DEV_DATA_DIR", "/app/data")

# ── FEDERAZIONE CP-to-CP ───────────────────────────────────────────────────────
# FEDERATION_ENABLED    : true (default) — disabilita per isolare completamente il CP
# FEDERATION_PUBLIC_URL : l'URL pubblico del TUO federation-gateway (non del CP!),
#                         quello che condividi con l'admin di un altro sito per il
#                         pairing. Vuoto finché non hai un gateway pubblico attivo.
FEDERATION_ENABLED    = os.getenv("FEDERATION_ENABLED", "true").lower() == "true"
FEDERATION_PUBLIC_URL = os.getenv("FEDERATION_PUBLIC_URL", "").rstrip("/")
# FEDERATION_VIEW_ENABLED : false (default) — condivisione in LETTURA della vista
#   di questo CP verso i peer ACCOPPIATI (dashboard unica su piu' CP, vedi
#   docs/control-plane-sync.md). Spento di default perche' e' una decisione sui
#   DATI, non sull'esecuzione: /federate/execute presta a un peer il tuo calcolo,
#   la vista gli mostra le tue informazioni (nodi, modelli, task, righe di log).
#   Non e' mai anonima: risponde solo a un peer in allowlist con firma ECDSA
#   valida, la stessa verifica di /federate/execute. Nessun modello di sicurezza
#   nuovo: la scelta e' se condividere, non con chi.
FEDERATION_VIEW_ENABLED = os.getenv("FEDERATION_VIEW_ENABLED", "false").lower() == "true"
FEDERATION_VIEW_TTL_S   = int(os.getenv("FEDERATION_VIEW_TTL_S", "10"))

