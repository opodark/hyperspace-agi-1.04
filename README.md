# HyperSpace-AGI v1.02

> Framework per agenti IA autonomi basati su SLM (Small Language Models), eseguiti localmente tramite Docker e Ollama.
> Il motore di inferenza gira **sull'host** (Ollama nativo o LM Studio) — accesso diretto alla GPU, zero overhead container.

---

## Quick Start

```bash
git clone https://github.com/opodark/hyperspace-agi-1.02.git
cd hyperspace-agi-1.02
git checkout 1.02

# macOS / Linux
chmod +x setup.sh && ./setup.sh

# oppure diretto
docker compose up -d
```

Dopo l'avvio tutti i servizi sono raggiungibili su `localhost`:

| Servizio | URL |
|---|---|
| **Open WebUI** | http://localhost:3000 |
| **Dashboard / Control Plane** | http://localhost:8085/dashboard |
| **Infra-UI Bridge** | http://localhost:8099 |
| **Registry** | http://localhost:8086 |
| **SearXNG** | http://localhost:8092 |
| **Memory Graph** | http://localhost:8090/status |
| **Obsidian GUI** | http://localhost:8091 |
| **Node 1** | http://localhost:8081/status |

**Registry pubblico:** https://sanctuary-mower-plated.ngrok-free.dev

---

## Architettura

```
  HOST MACHINE
  ┌───────────────────────────────────────────────────────┐
  │  Ollama (nativo) o LM Studio       :11434 / :1234  │
  └───────────────────────────────────────────────────────┘
           ↑ host.docker.internal
  DOCKER NETWORK: hyperspace
  ┌────────────┐  ┌─────────────────────┐  ┌───────────────┐  ┌──────────┐
  │  registry  │  │   control-plane    │  │   node-1        │  │ searxng  │
  │   :8086    │  │      :8085         │  │   :8084        │  │  :8080   │
  └────────────┘  └─────────────────────┘  └───────────────┘  └──────────┘
                       │ tool calling ↑                      ↑ web search
  ┌─────────────┐  ┌─────────────────────┐
  │infra-ui    │  │  memory-graph      │
  │bridge:8099 │  │  :8090  │obsidian │
  └─────────────┘  └─────────────────────┘
```

### Principi chiave

- **Compose unico** — un solo `docker-compose.yml` con profili GPU opzionali (`--profile cpu|nvidia|amd|intel|vulkan`)
- **OpenAI-compatible API** — il control-plane espone `/v1/chat/completions` compatibile con Open WebUI e qualsiasi client OpenAI
- **Tool calling loop** — il CP esegue un loop multi-iterazione (max 5) per `web_search`, `omega_query`, `omega_store`, `get_mesh_status`
- **Smart routing** — i task vengono instradati al nodo migliore tramite score (tier 40% + vram 30% + peers 20% + uptime 10%)
- **Nodo locale root/hub** — la macchina host si registra al boot come `root` se è l'unico nodo, `hub` se ci sono altri nodi remoti
- **Memory sync inter-nodo** — l'heartbeat sincronizza la memoria gzip tra tutti i nodi attivi ogni 2 cicli (30s)
- **SearXNG self-hosted** — nessuna API key, nessun rate limit, risultati reali aggregati da Google/Bing/DDG
- **Registry pubblico** — auto-discovery: ogni nodo si registra su `/register` al boot e scopre i peer da `/nodes/active`

---

## Stack Docker

| Container | Porta host | Descrizione |
|---|---|---|
| `registry` | 8086 | Registry pubblico — landing, dashboard nodi, auto-discovery |
| `control-plane` | 8085 | Orchestrazione mesh + OpenAI API + tool loop + memoria gzip |
| `node-1` | 8081 | Worker node — ECDSA, PEX, /execute |
| `searxng` | 8092 | Web search self-hosted (JSON API su :8080 interno) |
| `bridge` | 8099 | Infra-UI — dashboard SSE, log viewer real-time |
| `open-webui` | 3000 | Chat UI compatibile OpenAI |
| `memory-graph` | 8090 | Esportatore memoria → vault Obsidian |
| `obsidian` | 8091 | Obsidian nel browser via KasmVNC |
| `ollama-titler` | 11435 | Modello leggero per titolazione automatica note |
| `ollama` *(opt-in)* | 11434 | Solo con `--profile cpu\|nvidia\|amd\|intel\|vulkan` |

---

## Tool Calling

Il control-plane inietta automaticamente 4 tool built-in a ogni richiesta (se il modello li supporta):

| Tool | Descrizione |
|---|---|
| `web_search` | Cerca sul web via SearXNG — fallback DDG lite se SearXNG non disponibile |
| `omega_query` | Cerca nella memoria a lungo termine di HyperSpace AGI |
| `omega_store` | Salva informazioni importanti nella memoria a lungo termine |
| `get_mesh_status` | Stato della rete: nodi attivi, modelli disponibili, heartbeat |

### Modelli con tool calling abilitato (default)

`qwen3`, `qwen2.5`, `llama3.1`, `llama3.2`, `llama3.3`, `mistral-nemo`, `mistral-small`, `mixtral`, `command-r`, `phi4`, `hermes`, `functionary`

Per abilitare il tool calling su qualsiasi modello:
```bash
# nel .env
TOOL_CAPABLE_MODELS=*
```

---

## Unirsi alla mesh (per nuovi nodi / collaboratori)

### 1. Clona e configura

```bash
git clone https://github.com/opodark/hyperspace-agi-1.02.git
cd hyperspace-agi-1.02
git checkout 1.02
```

### 2. Crea il file `.env`

```env
# Registry pubblico
REGISTRY_URL=https://sanctuary-mower-plated.ngrok-free.dev
REGISTRY_PUBLIC_URL=https://sanctuary-mower-plated.ngrok-free.dev

# Esponi il node con ngrok (ngrok http 8084) e inserisci l'URL qui
PUBLIC_ENDPOINT=https://abc123.ngrok-free.app

OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=phi3
```

### 3. Avvia

```bash
docker compose up -d
```

### 4. Verifica

```bash
curl https://sanctuary-mower-plated.ngrok-free.dev/nodes/active
```

---

## Aggiornare i container dopo un `git pull`

```bash
git pull
docker compose up -d --build
```

> `--build` ricostruisce solo le immagini modificate. I volumi dati (modelli Ollama, memoria, vault) non vengono toccati.
> **Non usare** `docker compose down -v` — il flag `-v` cancella i volumi.

---

## Backend LLM supportati

| Backend | Setup | OLLAMA_URL |
|---|---|---|
| **Ollama nativo** (consigliato) | `./setup.sh` | `http://host.docker.internal:11434` |
| **LM Studio** | avvia LM Studio + Local Server | `http://host.docker.internal:1234` |
| **Ollama in Docker** | `--profile cpu\|nvidia\|amd\|intel\|vulkan` | `http://ollama:11434` |

Modelli consigliati:

| Modello | RAM / VRAM | Tool calling | Note |
|---|---|---|---|
| `qwen3:8b` | ~5 GB | ✅ | Ottimo bilanciamento qualità/velocità |
| `qwen2.5:7b` | ~5 GB | ✅ | Multilingue, veloce |
| `llama3.1:8b` | ~5 GB | ✅ | Meta, buone istruzioni |
| `phi4` | ~8 GB | ✅ | Microsoft, molto capace |
| `mistral:7b` | ~4.5 GB | ⚠️ parziale | Buona qualità |
| `phi3` | ~2.3 GB | ❌ | Velocissimo, no tool calling |

---

## Struttura del progetto

```
hyperspace-agi-1.02/
├── registry/
│   └── registry.py          # Landing pubblica, /dashboard, /nodes/active, /register
├── node/
│   └── main.py              # API node: /status /peers /execute + auto-discovery ECDSA
├── control-plane/
│   └── main.py              # OpenAI API, tool loop, smart routing, memoria gzip, OMEGA MCP
├── memory-graph/
│   └── ...                  # Esporta memoria CP → vault Obsidian, titolazione via Ollama
├── infra-ui/
│   └── ...                  # Bridge SSE: dashboard real-time, log viewer, topologia mesh
├── ollama-titler/
│   └── ...                  # Proxy Ollama leggero per titolazione automatica note memoria
├── shared/
│   ├── identity.py          # ECDSA secp256k1: genera node_id, sign, verify
│   └── db.py                # SQLite: log, nodes, tasks
├── data/
│   ├── searxng/
│   │   └── settings.yml     # Config SearXNG: JSON API abilitata, engine IT
│   └── obsidian-vault/      # Vault Obsidian montato da memory-graph e Obsidian GUI
├── setup.sh
├── setup.ps1
├── docker-compose.yml       # Compose unico con profili GPU opzionali
├── .env.example
└── README.md
```

---

## Variabili d'ambiente principali

```bash
# Registry
REGISTRY_URL=                        # URL pubblico del registry
REGISTRY_PUBLIC_URL=                 # Stesso di REGISTRY_URL
NODE_TTL=300                         # Secondi prima che un nodo sia offline

# Node
PUBLIC_ENDPOINT=                     # URL pubblico ngrok del node
BOOT_PEERS=                          # Peer iniziali opzionali
HEARTBEAT_EVERY=15
PEER_MAX_AGE_S=120

# Inferenza
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=phi3
INFERENCE_BACKEND=ollama             # ollama | lmstudio
TOOL_CAPABLE_MODELS=                 # Lista pattern modelli, o * per tutti

# Memoria
MEMORY_TTL_DAYS=7
MEMORY_MAX_ENTRIES=200

# SearXNG
SEARXNG_URL=http://searxng:8080      # URL interno Docker (non modificare)
SEARXNG_PORT=8092                    # Porta host esposta
SEARXNG_SECRET=hyperspace-searxng-secret

# Memory Graph / Titler
TITLER_ENABLED=true
TITLER_MODEL=qwen2:0.5b
MEMORY_EXPORT_INTERVAL=30
```

---

## API Reference

### Control Plane (porta 8085)

| Method | Path | Descrizione |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI-compatible — stream e non-stream, tool calling loop |
| GET | `/v1/models` | Lista modelli disponibili (formato OpenAI) |
| GET | `/dashboard` | Dashboard HTML |
| GET | `/mesh/nodes` | Nodi mesh aggregati |
| GET | `/mesh/topology` | Grafo nodi + archi PEX |
| GET | `/memory` | Memoria gzip (`?limit=N`) |
| POST | `/memory/push` | Ricevi entry da nodo remoto |
| GET | `/memory/stats` | Statistiche memoria |
| GET | `/logs` | Log paginati (`?type=&status=&node=&q=`) |
| POST | `/logs/add` | Aggiungi log entry |
| GET | `/logs/export` | Export CSV o JSON |
| POST | `/logs/clear` | Svuota log |
| GET | `/tasks` | Lista tasks |
| POST | `/task/create` | Crea task |
| POST | `/task/assign` | Assegna ed esegui task |
| GET | `/health` | Ping + stato memoria + nodi |
| GET | `/hb/status` | Stato heartbeat loop |
| POST | `/mcp` | JSON-RPC 2.0 — bridge OMEGA Obsidian |
| GET | `/config/advanced` | Leggi config runtime |
| POST | `/config/advanced` | Aggiorna config runtime |
| POST | `/config/secret/rotate` | Ruota shared secret |
| GET | `/mesh/node/<ep>/pull` | Pull modello Ollama su nodo remoto (SSE) |

### Registry (porta 8086)

| Method | Path | Descrizione |
|---|---|---|
| GET | `/` | Landing page pubblica |
| GET | `/dashboard` | Dashboard nodi live (auto-refresh 15s) |
| GET | `/nodes/active` | Nodi vivi TTL-filtered (usato da auto-discovery) |
| GET | `/nodes` | Tutti i nodi registrati |
| POST | `/register` | Registra o aggiorna un nodo |
| GET | `/health` | Ping |

### Node (porta 8084)

| Method | Path | Descrizione |
|---|---|---|
| GET | `/health` | Ping |
| GET | `/status` | Schema completo nodo |
| GET | `/peers` | Lista peer PEX |
| POST | `/execute` | Esegui task LLM |
| POST | `/announce` | Ricevi annuncio da nuovo nodo |
| GET | `/ollama/models` | Modelli disponibili |

---

## Stato del progetto

| Feature | Stato |
|---|---|
| Compose unico con profili GPU | ✅ Implementato |
| OpenAI-compatible API (`/v1/chat/completions`) | ✅ Implementato |
| Tool calling loop (max 5 iterazioni) | ✅ Implementato |
| Tool `web_search` via SearXNG self-hosted | ✅ Implementato |
| Tool `omega_query` / `omega_store` | ✅ Implementato |
| Smart routing nodi (tier/vram/peers/uptime) | ✅ Implementato |
| Nodo locale root/hub auto-promosso al boot | ✅ Implementato |
| Memory sync inter-nodo nell’heartbeat | ✅ Implementato |
| Memoria gzip con TTL + pruning | ✅ Implementato |
| Memory Graph → vault Obsidian | ✅ Implementato |
| Obsidian GUI nel browser (KasmVNC) | ✅ Implementato |
| Ollama Titler (titolazione automatica note) | ✅ Implementato |
| OMEGA Obsidian bridge (MCP JSON-RPC 2.0) | ✅ Implementato |
| Registry pubblico + auto-discovery | ✅ Implementato |
| Identità ECDSA secp256k1 | ✅ Implementato |
| Dashboard mesh + Log Viewer real-time | ✅ Implementato |
| Health check JSON-aware (zombie detection) | ✅ Implementato |
| Firma inter-nodo JWT in produzione | 🔧 In sviluppo |
| UI topologia grafo interattiva | 🔧 In sviluppo |

---

## Changelog

### v1.02 (Giugno 2026)
- **Compose unico** con profili GPU (`cpu`, `nvidia`, `amd`, `intel`, `vulkan`)
- **OpenAI-compatible API** `/v1/chat/completions` con streaming SSE e tool calling loop
- **SearXNG self-hosted** — tool `web_search` usa `/search?format=json`, nessuna API key, fallback DDG lite
- **Smart routing** — score ponderato tier/vram/peers/uptime, preferenza nodo locale
- **Nodo locale root/hub** — registrato al boot, auto-promosso se è l'unico nodo attivo
- **Memory sync** inter-nodo nell’heartbeat ogni 30s
- **Memory Graph** — esporta memoria CP su vault Obsidian con titolazione via `qwen2:0.5b`
- **Obsidian GUI** nel browser via KasmVNC (`:8091`)
- **Ollama Titler** — container dedicato per titolazione leggera note memoria
- **Health check JSON-aware** — nodi zombie ngrok (HTML 403) marcati `unreachable`
- Fix: tool loop robusto con fallback no-tools per modelli senza function calling
- Fix: DB reload al boot, status recovery, endpoint dedup

### v1.01 (Giugno 2026)
- Log Viewer esteso: tab Connection Tests, Node Communication, Dreams, Node Chats
- Advanced Setup: gestione Secret, Authority Server, Mesh dalla UI
- Task UI: create e assign task dalla dashboard
- Config avanzata: salvataggio e rotazione shared secret

### v1.0 (Giugno 2026)
- Mesh stabile multi-macchina via BOOT_PEERS + PEX
- Dashboard rinnovata con card live per ogni nodo
- Schema `/status` con `peers_active`, `vram_gb`, `capabilities`

### v0.9 (Giugno 2026)
- Identità ECDSA secp256k1 persistente
- Primo prototipo PEX funzionante
- Setup guidato `setup.sh` / `setup.ps1`
