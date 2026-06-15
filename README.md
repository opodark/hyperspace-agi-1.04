# HyperSpace AGI v1.01

Framework per agenti IA distribuiti basati su Small Language Models (SLM), eseguiti localmente tramite Docker e Ollama.

## Novità v1.01

### Log Viewer esteso
La dashboard include ora un Log Viewer categorizzato con 5 tab distinti:

| Tab | Tipo evento | Descrizione |
|-----|------------|-------------|
| 🔌 Connection Tests | `connection_test` | Handshake, latenza, esito connessioni |
| 📡 Node Communication | `inter_node_message` | Messaggi dispatch/result tra nodi |
| 💭 Dreams / Autonomous | `dream` | Task autonomi, planning, cicli dream |
| 💬 Node Chats | `node_chat` | Chat e negoziazione tra nodi |
| 🔑 Authority Events | `authority_event` | Rotazione secret, config authority |

**Filtri disponibili:** nodo sorgente/target, status, full-text search, clear log.

### Advanced Setup
Nuova sezione di configurazione avanzata con:
- **Security**: Shared Secret con rotazione manuale e automatica, show/hide
- **Authority Server**: URL, auth mode (none/token/jwt/public-key), enable/disable, test connessione
- **Network Mode**: Authority-managed vs Pure Mesh (MHT) — toggle, bootstrap peers, MHT enable/disable *(MHT full implementation coming soon)*

### Diagnostics Panel
Pannello per test operativi manuali:
- Authority Reachability Test
- Node List refresh
- Simulate Dream Event
- Simulate Node Chat

## Struttura

```
hyperspace-agi-1.01/
├── control-plane/
│   ├── main.py          # Flask app: API + Dashboard HTML
│   ├── Dockerfile
│   └── requirements.txt
├── authority/           # Authority server (node registry)
├── worker/              # Worker node
├── shared/              # Shared utilities
├── docker-compose.yml
└── README.md
```

## Quick Start

```bash
# Clone
git clone https://github.com/opodark/hyperspace-agi-1.01.git
cd hyperspace-agi-1.01

# Build e avvio
docker compose up -d --build

# Dashboard
open http://localhost:8085/dashboard
```

## API Log

```bash
# Tutti i log
GET /logs

# Filtrati per tipo
GET /logs?type=connection_test
GET /logs?type=dream&status=success
GET /logs?node=node-alpha&q=task

# Aggiungi log da worker/nodo esterno
POST /logs/add
{
  "type": "node_chat",
  "summary": "node-alpha → node-beta: 'hello'",
  "sourceNode": "node-alpha",
  "targetNode": "node-beta",
  "status": "info"
}

# Clear
POST /logs/clear
```

## API Advanced Config

```bash
# Leggi config (secret mascherato)
GET /config/advanced

# Salva config
POST /config/advanced
{
  "security": {"sharedSecret": "mysecret"},
  "authority": {"serverUrl": "http://authority:8080", "authMode": "jwt", "enabled": true},
  "mesh": {"enabled": false, "mhtEnabled": false, "bootstrapPeers": []}
}

# Ruota secret
POST /config/secret/rotate

# Test authority
POST /config/authority/test
```

## Roadmap

- [ ] Persistenza config su disco / volume Docker
- [ ] Auth middleware con sharedSecret su ogni endpoint
- [ ] WebSocket per log streaming real-time
- [ ] MHT (Modular Hash Tree) mesh routing
- [ ] Multi-node dream coordination
- [ ] Node chat UI dedicata (thread view)
