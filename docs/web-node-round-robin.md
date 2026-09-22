# Web-node in round-robin tra Mac e Windows

La pagina web-node (`web-node/join.html` + `src/`) è **statica**: Mac e Windows
servono gli stessi file. Il round-robin sta nel **DNS / Load Balancer**
(Cloudflare), non nel codice: entrambe le macchine espongono la pagina, il
dominio punta a entrambe, e se una si spegne risponde l'altra.

## Topologia

```text
os.zerozerocomputer.com
        |
        v
Cloudflare Load Balancer (round-robin + health check + failover)
        |                     |
        v                     v
Mac (cloudflared → :8790)   Windows (cloudflared → :8790)
        |                     |
      serve.mjs             server statico
```

Il **gateway** (`federation-gateway:8095`) resta un endpoint separato: la pagina
lo raggiunge via `join-config.js` → `gatewayUrl`. Qui si tratta solo della pagina.

## Prerequisiti

- `cloudflared` su entrambe le macchine (Mac: `brew install cloudflared`).
- Un dominio gestito da Cloudflare (es. `zerozerocomputer.com`).
- La pagina servita su entrambe:
  - Mac: `WEB_NODE_TUNNEL=none ./scripts/start-web-node-mac.sh` (se il tunnel lo
    fa cloudflared come servizio).
  - Windows: un server statico per `web-node/` sulla porta 8790 (equivalente di
    `serve.mjs`).

## Passi

### 1. Servire la pagina su ogni macchina

```bash
# Mac
WEB_NODE_TUNNEL=none ./scripts/start-web-node-mac.sh
```

### 2. Creare un tunnel nominato su ogni macchina

```bash
cloudflared tunnel login
cloudflared tunnel create web-node-mac     # una volta per macchina
```

Config `~/.cloudflared/config.yml`:

```yaml
tunnel: web-node-mac
credentials-file: /Users/<tu>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: os.zerozerocomputer.com
    service: http://localhost:8790
  - service: http_status:404
```

### 3. Installare i tunnel come servizi (persistenti)

```bash
sudo cloudflared service install          # macOS: launchd
```

(Su Windows: `cloudflared service install`.)

### 4. Configurare il Load Balancer su Cloudflare

- **Traffic → Load Balancing → Create Load Balancer**.
- Pool "web-node" con due origin:
  - Mac → tunnel `web-node-mac` (origin type "Cloudflare Tunnel").
  - Windows → tunnel `web-node-win`.
- **Steering**: Round-robin.
- **Health check**: HTTP, path `/`, ogni 30s, unhealthy dopo 2 fallimenti.
- **Hostname**: `os.zerozerocomputer.com`.

### 5. Verifica

```bash
# Spegni il servizio su UNA macchina: il dominio deve continuare a rispondere.
curl -I https://os.zerozerocomputer.com/
```

## Fallback senza Load Balancer (DNS round-robin semplice)

Due record CNAME con lo stesso nome che puntano ai due tunnel. È round-robin "alla
buona": nessun health check, il browser riprova da solo ma il failover non è
immediato. Il Load Balancer è la via pulita.

## Note

- La pagina deve essere servita in HTTPS: `gatewayUrl` in `join-config.js` deve
  essere HTTPS, altrimenti il browser blocca il mixed content.
- Il gateway può restare singolo. Per renderlo ridondante si ripete la stessa
  tecnica sulla porta 8095 con un secondo hostname (es. `mesh.zerozerocomputer.com`).
