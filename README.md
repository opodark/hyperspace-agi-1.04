# HyperSpace-AGI v1.02

> Framework per agenti IA autonomi basati su SLM (Small Language Models), eseguiti localmente tramite Docker. Il motore di inferenza (Ollama o LM Studio) gira **sull'host**, non in Docker — più veloce, più leggero, accesso diretto alla GPU.

---

## Quick Start

```bash
git clone https://github.com/opodark/hyperspace-agi-1.02.git
cd hyperspace-agi-1.02

# macOS / Linux
chmod +x setup.sh && ./setup.sh

# Windows (PowerShell)
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

Il setup guida la configurazione del backend LLM (Ollama o LM Studio) e avvia i container.

**Registry (pubblico):** https://sanctuary-mower-plated.ngrok-free.dev  
**Dashboard CP:** http://localhost:8085/dashboard  
**Node API:** http://localhost:8084/status

---

## Architettura

```
  HOST MACHINE
  ┌───────────────────────────────────────────────────────┐
  │  Ollama (nativo) o LM Studio                          │
  │  :11434 / :1234                                       │
  └───────────────────────────────────────────────────────┘
           ↑ host.docker.internal
  DOCKER
  ┌──────────────┐   ┌───────────────────────────┐   ┌────────────────┐
  │   registry   │   │      control-plane        │   │     node       │
  │    :8086     │   │         :8085             │   │ :8084          │
  │  /           │   │ /dashboard /memory        │   │ /status /peers │
  │  /dashboard  │   │ /mesh/nodes /task/*       │   │ /execute       │
  │  /nodes      │   └───────────────────────────┘   └────────────────┘
  └──────────────┘
        ↑
  punto di ingresso pubblico fisso
  i nodi si registrano qui al boot e scoprono i peer
```

### Principi chiave

- **Registry pubblico** — ogni nodo si registra su `/register` al boot; il registry espone `/nodes/active` (TTL-filtered) per l'auto-discovery
- **Auto-discovery** — se `BOOT_PEERS` è vuoto, il nodo chiama `GET /nodes/active` e si annuncia automaticamente a tutti i nodi attivi
- **Identità crittografica** — ogni nodo genera un keypair ECDSA secp256k1 al primo avvio; `node_id = sha256(pubkey)[:40]`
- **Memoria compressa** — il control-plane persiste la memoria locale in gzip con pruning TTL automatico
- **Ollama / LM Studio sull'host** — accesso diretto alla GPU, zero overhead Docker

---

## Stack Docker

| Container | Porta | Descrizione |
|---|---|---|
| `registry` | 8086 | Registry pubblico — landing, dashboard nodi, auto-discovery |
| `node` | 8084 | Worker node — identità ECDSA, PEX, /execute |
| `control-plane` | 8085 | Dashboard mesh + orchestrazione task + memoria gzip |
| `ollama` *(opt-in)* | 11434 | Solo con `--profile with-ollama` (legacy) |

---

## Unirsi alla mesh (per nuovi nodi / collaboratori)

Un nodo esterno può unirsi alla mesh conoscendo solo l'URL del registry pubblico.

### 1. Prerequisiti

- Docker + Docker Compose installati
- ngrok installato (per esporre il node all'esterno)

### 2. Clona il repo

```bash
git clone https://github.com/opodark/hyperspace-agi-1.02.git
cd hyperspace-agi-1.02
git checkout 1.02
```

### 3. Esponi il node con ngrok

```bash
ngrok http 8084
# annota l'URL pubblico assegnato, es: https://abc123.ngrok-free.app
```

### 4. Crea il file `.env`

```env
# Registry pubblico — non cambia mai
REGISTRY_URL=https://sanctuary-mower-plated.ngrok-free.dev
REGISTRY_PUBLIC_URL=https://sanctuary-mower-plated.ngrok-free.dev

# Il tuo endpoint pubblico (URL ngrok del passo 3)
PUBLIC_ENDPOINT=https://abc123.ngrok-free.app

# BOOT_PEERS opzionale — lascia vuoto per usare l'auto-discovery
BOOT_PEERS=

OLLAMA_MODEL=phi3
PEER_MAX_AGE_S=120
MEMORY_TTL_DAYS=7
```

### 5. Avvia solo il node

```bash
docker compose up -d node-1
```

### 6. Verifica l'ingresso nella mesh

```bash
# Il tuo node deve apparire nella lista entro 15 secondi
curl https://sanctuary-mower-plated.ngrok-free.dev/nodes/active
```

Oppure visita la dashboard pubblica: https://sanctuary-mower-plated.ngrok-free.dev/dashboard

### Cosa succede automaticamente al boot

```
node avvia
  → POST /register         → registry (si iscrive con PUBLIC_ENDPOINT)
  → GET  /nodes/active     → registry (scarica lista nodi attivi)
  → POST /announce         → ogni nodo attivo (si presenta)
  → heartbeat ogni 15s     → mantiene la registrazione viva (NODE_TTL 300s)
```

---

## Setup host principale (Ubuntu con ngrok Free)

Con il piano Free di ngrok è disponibile **1 solo dominio statico**. La configurazione consigliata è:

```
dominio statico → 8086 (registry) — punto di ingresso pubblico fisso
CP e node       → localhost       — comunicazione interna Docker network
```

### `ngrok.yml`

```yaml
version: "3"
agent:
  authtoken: IL_TUO_TOKEN

tunnels:
  registry:
    addr: 8086
    proto: http
    domain: sanctuary-mower-plated.ngrok-free.dev
```

```bash
ngrok start registry
```

### `.env` host principale

```env
REGISTRY_URL=https://sanctuary-mower-plated.ngrok-free.dev
REGISTRY_PUBLIC_URL=https://sanctuary-mower-plated.ngrok-free.dev

CONTROL_PLANE_URL=http://control-plane:8085
PUBLIC_ENDPOINT=http://node-1:8084

OLLAMA_MODEL=phi3
NODE_TTL=300
PEER_MAX_AGE_S=120
MEMORY_TTL_DAYS=7
HEARTBEAT_EVERY=15
```

> Con il piano ngrok Pro (3 domini statici) puoi assegnare un dominio fisso anche a CP e node, eliminando la necessità di aggiornare `.env` ad ogni restart.

---

## Backend LLM supportati

| Backend | Setup | OLLAMA_URL |
|---|---|---|
| **Ollama nativo** | `./setup.sh` opzione 1 | `http://host.docker.internal:11434` |
| **LM Studio** | `./setup.sh` opzione 2 | `http://host.docker.internal:1234` |
| **Ollama Docker** | `./setup.sh` opzione 3 | `http://ollama:11434` |

Modelli consigliati per hardware consumer:

| Modello | Param | VRAM / RAM | Note |
|---|---|---|---|
| `phi3` | 3.8B | ~2.3 GB | Velocissimo, ottimo su CPU |
| `llama3:8b` | 8B | ~5 GB | Bilanciato |
| `mistral:7b` | 7B | ~4.5 GB | Ottima qualità |
| `qwen2:7b` | 7B | ~4.5 GB | Multilingue |
| `llama3:70b` | 70B | ~40 GB | Alta qualità, GPU richiesta |

---

## Struttura del progetto

```
hyperspace-agi-1.02/
├── registry/
│   └── registry.py          # Landing pubblica, /dashboard, /nodes/active, /register
├── node/
│   └── main.py              # API node: /status /peers /execute + auto-discovery
├── control-plane/
│   └── main.py              # Dashboard mesh, memoria gzip TTL, /memory /task/*
├── shared/
│   ├── identity.py          # ECDSA secp256k1: genera node_id, sign, verify
│   ├── auth.py              # 🔧 JWT ES256 inter-nodo (in sviluppo)
│   └── db.py                # 🔧 SQLite log/nodes/tasks (in sviluppo)
├── authority/               # Registry legacy (mantenuto, non avviato di default)
├── setup.sh                 # Setup guidato macOS/Linux
├── setup.ps1                # Setup guidato Windows
├── docker-compose.yml       # Compose sviluppo
├── docker-compose.prod.yml  # Compose produzione
├── .env.example             # Template variabili (5 variabili obbligatorie)
└── README.md
```

---

## Variabili d'ambiente principali

```bash
# Registry
REGISTRY_URL=                  # URL pubblico del registry
REGISTRY_PUBLIC_URL=           # Stesso di REGISTRY_URL (usato dal registry stesso)
NODE_TTL=300                   # Secondi prima che un nodo venga considerato offline

# Node
PUBLIC_ENDPOINT=               # URL pubblico raggiungibile dagli altri nodi
BOOT_PEERS=                    # Peer iniziali opzionali: "ip1:8084,ip2:8084"
PEER_MAX_AGE_S=120             # Età massima peer prima del prune
HEARTBEAT_EVERY=15             # Secondi tra un heartbeat e il successivo

# Inferenza
INFERENCE_BACKEND=ollama       # ollama | lmstudio | ollama-docker
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=phi3

# Memoria
MEMORY_TTL_DAYS=7              # Giorni prima che una entry venga eliminata
MEMORY_MAX_ENTRIES=200         # Numero massimo entry in memoria gzip

# Security (v1.02)
JWT_TTL=300                    # 🔧 durata JWT in secondi (in sviluppo)
```

---

## API Reference

### Registry (porta 8086)

| Method | Path | Descrizione |
|---|---|---|
| GET | `/` | Landing page pubblica con istruzioni join |
| GET | `/dashboard` | Dashboard nodi attivi (auto-refresh 15s) |
| GET | `/nodes/active` | JSON nodi vivi (TTL-filtered) — usato dall'auto-discovery |
| GET | `/nodes` | Tutti i nodi registrati |
| POST | `/register` | Registra o aggiorna un nodo |
| GET | `/health` | Ping |

### Node (porta 8084)

| Method | Path | Descrizione |
|---|---|---|
| GET | `/health` | Ping rapido + uptime |
| GET | `/status` | Schema completo (node_id, tier, peers, caps…) |
| GET | `/identity` | Profilo pubblico immutabile |
| GET | `/peers` | Lista peer noti con stato PEX |
| POST | `/peer/add` | Registra un nuovo peer |
| POST | `/announce` | Ricevi annuncio da un nuovo nodo |
| POST | `/execute` | Esegui task LLM |
| POST | `/verify` | Verifica firma ECDSA messaggio peer |
| GET | `/ollama/health` | Stato Ollama/LM Studio |
| GET | `/ollama/models` | Modelli disponibili |

### Control Plane (porta 8085)

| Method | Path | Descrizione |
|---|---|---|
| GET | `/dashboard` | Dashboard HTML |
| GET | `/mesh/nodes` | Stato aggregato nodi mesh |
| GET | `/status` | Status control-plane |
| GET | `/memory` | Memoria locale gzip (parametro `?limit=N`) |
| POST | `/memory/push` | Ricevi entry memoria da nodo remoto |
| GET | `/memory/stats` | Statistiche memoria (entries, size, TTL) |
| GET | `/tasks` | Lista tasks |
| POST | `/task/create` | Crea task |
| POST | `/task/assign` | Assegna ed esegui task |
| GET | `/logs` | Stream logs |
| GET | `/health` | Ping |
| GET | `/hb/status` | Stato heartbeat loop |
| POST | `/mcp` | JSON-RPC 2.0 — bridge OMEGA Obsidian |
| GET | `/mesh/topology` | 🔧 Grafo nodi + archi PEX (in sviluppo) |

> 🔧 = endpoint in sviluppo

---

## Stato del progetto

| Feature | Stato |
|---|---|
| Registry pubblico con landing + dashboard | ✅ Implementato |
| Auto-discovery dal registry al boot | ✅ Implementato |
| Identità ECDSA secp256k1 | ✅ Implementato |
| PEX gossip multi-macchina | ✅ Implementato |
| Memoria gzip con TTL + pruning | ✅ Implementato |
| Dashboard mesh + Log Viewer | ✅ Implementato |
| Setup guidato (sh / ps1) | ✅ Implementato |
| OMEGA Obsidian bridge (MCP JSON-RPC 2.0) | ✅ Implementato |
| NODE_TTL 300s + PEER_MAX_AGE_S 120s | ✅ Implementato |
| Authority server | ⚠️ Legacy — disabilitato di default |
| Firma inter-nodo in produzione | 🔧 In sviluppo |
| JWT tra nodi | 🔧 In sviluppo |
| Tier dinamico leaf → hub | 🔧 In sviluppo |
| UI topologia grafo | 🔧 In sviluppo |

---

## Changelog

### v1.02 (Giugno 2026 — in sviluppo attivo)
- **Registry pubblico** con landing page, dashboard nodi live (auto-refresh 15s), `/nodes/active` TTL-filtered
- **Auto-discovery** al boot: se `BOOT_PEERS` è vuoto il nodo chiama `GET /nodes/active` e si annuncia a tutti
- **Memoria gzip** nel control-plane: storage compresso, pruning TTL (`MEMORY_TTL_DAYS`), max entries (`MEMORY_MAX_ENTRIES`)
- **OMEGA Obsidian bridge**: `GET /health` + `POST /mcp` JSON-RPC 2.0 compatibile OMEGA plugin
- **Bug fix mesh**: heartbeat robusto, endpoint normalizzazione `http://`, DB reload al restart, `PEER_MAX_AGE_S`, status recovery da `unreachable`
- `NODE_TTL` default alzato a 300s, `HEARTBEAT_EVERY=15`, `PEER_MAX_AGE_S=120`
- `.env.example` semplificato a 5 variabili obbligatorie

### v1.01 (Giugno 2026)
- Log Viewer esteso: tab Connection Tests, Node Communication, Dreams, Node Chats
- Advanced Setup: gestione Secret, Authority Server, Mesh/MHT dalla UI
- Task UI: create e assign task direttamente dalla dashboard
- Config avanzata: salvataggio e rotazione shared secret dal control-plane

### v1.0 (Giugno 2026)
- Mesh stabile multi-macchina via BOOT_PEERS + PEX
- Dashboard rinnovata con card live per ogni nodo
- Schema `/status` con `peers_active`, `peers_known`, `capabilities`, `vram_gb`, `endpoint`

### v0.9 (Giugno 2026)
- Identità ECDSA secp256k1 persistente (`shared/identity.py`)
- Primo prototipo PEX funzionante
- Setup guidato `setup.sh` / `setup.ps1`

### v0.8 (Giugno 2026)
- Base Docker + Ollama
- Primo node con `/execute` e `/status`
