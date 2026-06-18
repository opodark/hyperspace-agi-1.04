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

**Dashboard:** http://localhost:8085/dashboard  
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
  ┌───────────────┐   ┌──────────────────────────┐
  │ control-plane │───│       node               │
  │    :8085      │   │ :8084  ECDSA identity    │
  └───────────────┘   │ /status /peers /execute  │
                      └──────────────────────────┘
        multi-machine: ogni host ha il proprio node
        i nodi si scoprono via BOOT_PEERS + PEX
```

### Principi chiave

- **Mesh-first** — i nodi si scoprono e comunicano direttamente via `/peers` (PEX), senza registry centralizzato
- **Identità crittografica** — ogni nodo genera un keypair ECDSA secp256k1 al primo avvio; `node_id = sha256(pubkey)[:40]`
- **Ollama / LM Studio sull'host** — accesso diretto alla GPU, zero overhead Docker, modelli condivisi tra sessioni
- **Authority legacy** — mantenuta nel codice ma disabilitata di default, nascosta dalla UI

---

## Stack Docker

| Container | Porta | Descrizione |
|---|---|---|
| `node` | 8084 | Worker node — identità ECDSA, PEX, /execute |
| `control-plane` | 8085 | Dashboard mesh + orchestrazione task |
| `ollama` *(opt-in)* | 11434 | Solo con `--profile with-ollama` (legacy) |

> L'authority (`authority:8080`) è mantenuta nel codice per compatibilità ma non viene avviata di default.

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
├── node/                    # Worker node (FastAPI)
├── worker/                  # Worker legacy (FastAPI)
├──── main.py                # API: /status /peers /peer/add /execute
├── control-plane/           # Dashboard + orchestrazione (Flask)
├──── main.py                # Dashboard mesh-first, Log Viewer, Advanced Setup
├── shared/
├──── identity.py            # ECDSA secp256k1: genera node_id, sign, verify
├──── auth.py                # 🔧 v1.02: JWT ES256 inter-nodo (in sviluppo)
├──── db.py                  # 🔧 v1.02: SQLite log/nodes/tasks (in sviluppo)
├── authority/               # Registry legacy (mantenuto, non avviato di default)
├── setup.sh                 # Setup guidato macOS/Linux
├── setup.ps1                # Setup guidato Windows
├── docker-compose.prod.yml  # Compose produzione (senza ollama)
├── docker-compose.yml       # Compose sviluppo
└── .env.example             # Template variabili
```

---

## Variabili d'ambiente principali

```bash
# Node
NODE_HOSTNAME=localhost        # hostname o IP pubblico del nodo
NODE_TIER=leaf                 # leaf | hub | root
VRAM_GB=0.0                    # VRAM GPU disponibile
BOOT_PEERS=                    # peer iniziali: "ip1:8084,ip2:8084"

# Inferenza
INFERENCE_BACKEND=ollama       # ollama | lmstudio | ollama-docker
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=phi3
LMS_URL=                       # URL LM Studio (se INFERENCE_BACKEND=lmstudio)

# Control plane
NODE_ENDPOINTS=node:8084       # nodi da monitorare (separati da virgola)

# Security (v1.02)
JWT_TTL=300                    # 🔧 durata JWT in secondi (in sviluppo)
HUB_THRESHOLD=3                # 🔧 peer attivi per promozione a hub (in sviluppo)
TIER_EVAL_INTERVAL=30          # 🔧 secondi tra valutazioni tier (in sviluppo)

# Legacy
AUTHORITY_ENABLED=false
```

---

## Come funziona la mesh al boot

Quando un nodo si avvia, esegue i seguenti passi:

1. **Genera l'identità** — `shared/identity.py` crea (o carica) il keypair ECDSA secp256k1; il `node_id` è derivato come `sha256(pubkey)[:40]`.
2. **Legge `BOOT_PEERS`** — lista di peer iniziali in formato `ip:porta` da cui partire.
3. **Connessione iniziale** — il nodo contatta ogni boot peer tramite `/peers` e riceve la lista dei nodi noti a quel peer.
4. **PEX (Peer Exchange)** — la lista si propaga: ogni nodo condivide i propri peer con i nuovi arrivati, espandendo la vista della rete senza un registry centrale.
5. **Heartbeat** — il control-plane interroga periodicamente tutti i `NODE_ENDPOINTS` per aggiornare lo stato della mesh nella dashboard.

Questo schema consente a nuovi nodi di aggiungersi alla rete conoscendo solo un peer iniziale.

---

## API Reference

### Node (porta 8084)

| Method | Path | Descrizione |
|---|---|---|
| GET | `/health` | Ping rapido + uptime |
| GET | `/status` | Schema completo (node_id, tier, peers, caps…) |
| GET | `/identity` | Profilo pubblico immutabile |
| GET | `/peers` | Lista peer noti con stato PEX |
| POST | `/peer/add` | Registra un nuovo peer |
| POST | `/execute` | Esegui task LLM |
| POST | `/verify` | Verifica firma ECDSA messaggio peer |
| POST | `/auth/token` | 🔧 Emetti JWT (v1.02) |
| POST | `/peer/tier-update` | 🔧 Ricevi notifica cambio tier (v1.02) |
| POST | `/ollama/pull` | 🔧 Pull modello con stream SSE (v1.02) |
| GET | `/ollama/health` | Stato Ollama/LM Studio |
| GET | `/ollama/models` | Modelli disponibili |

### Control Plane (porta 8085)

| Method | Path | Descrizione |
|---|---|---|
| GET | `/dashboard` | Dashboard HTML |
| GET | `/mesh/nodes` | Stato aggregato nodi mesh |
| GET | `/mesh/topology` | 🔧 Grafo nodi + archi PEX (v1.02) |
| GET | `/mesh/node/<ep>/status` | Status singolo nodo |
| GET | `/mesh/node/<ep>/peers` | Peers di un nodo |
| POST | `/mesh/node/<ep>/pull` | 🔧 Proxy pull modello (v1.02) |
| GET | `/tasks` | Lista tasks |
| POST | `/task/create` | Crea task |
| POST | `/task/assign` | Assegna ed esegui task sul nodo più disponibile |
| GET | `/logs` | Stream logs (filtri: type, status, node, q, page) |
| GET | `/logs/export` | 🔧 Export log JSON/CSV (v1.02) |
| POST | `/logs/add` | Aggiungi log entry |
| POST | `/logs/clear` | Svuota log |
| GET | `/hb/status` | Stato heartbeat loop |
| GET | `/config/advanced` | Leggi config |
| POST | `/config/advanced` | Salva config |
| POST | `/config/secret/rotate` | Ruota shared secret |
| GET | `/ollama/status` | Stato Ollama/LM Studio dal control-plane |

> 🔧 = endpoint nuovo in v1.02, in sviluppo

---

## Deploy multi-macchina

Ogni macchina avvia il proprio `node`. I nodi si scoprono tramite `BOOT_PEERS`:

```bash
# Macchina A (192.168.1.10)
BOOT_PEERS=192.168.1.11:8084
docker compose -f docker-compose.prod.yml up -d --build

# Macchina B (192.168.1.11)
BOOT_PEERS=192.168.1.10:8084
docker compose -f docker-compose.prod.yml up -d --build
```

Dopo il boot i nodi si scambiano la lista peer via `/peers` (PEX leggero). Il control-plane può girare su una sola macchina e monitorare tutti i nodi tramite `NODE_ENDPOINTS`.

---

## Stato del progetto

| Feature | Stato |
|---|---|
| Identità ECDSA secp256k1 | ✅ Implementato |
| PEX gossip multi-macchina | ✅ Implementato |
| Dashboard mesh + Log Viewer | ✅ Implementato |
| Setup guidato (sh / ps1) | ✅ Implementato |
| Advanced Setup + Secret rotation | ✅ Implementato |
| Authority server | ⚠️ Legacy — disabilitato di default |
| Firma inter-nodo in produzione | 🔧 In sviluppo (v1.02) |
| JWT tra nodi | 🔧 In sviluppo (v1.02) |
| Tier dinamico leaf → hub | 🔧 In sviluppo (v1.02) |
| SQLite per persistenza log | 🔧 In sviluppo (v1.02) |
| Pull modello automatico | 🔧 In sviluppo (v1.02) |
| UI topologia grafo | 🔧 In sviluppo (v1.02) |

---

## Changelog

### v1.02 (in sviluppo)
- Firma inter-nodo ECDSA in produzione su tutte le chiamate HTTP
- JWT ES256 tra nodi (`shared/auth.py`, endpoint `/auth/token`)
- Tier dinamico leaf → hub basato su `peers_active`
- SQLite per persistenza log, nodi e task (`control-plane/db.py`)
- Pull modello automatico da dashboard con progress SSE
- UI topologia mesh con grafo interattivo (Cytoscape.js)

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
