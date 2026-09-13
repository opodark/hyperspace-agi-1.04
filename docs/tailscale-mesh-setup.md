# Mesh cross-machine via Tailscale

Sostituisce sia i vecchi tunnel ngrok free-tier (morti, `ERR_NGROK_3200`
verificato 2026-09-13) sia il piano WireGuard manuale considerato lo stesso
giorno: Tailscale era già indicato in `VISION.md` come soluzione transitoria
per l'accesso remoto, ed è risultato già installato e funzionante su Mac e
Windows quando li abbiamo messi sulla stessa rete per un test — niente
port-forward da configurare sul router, Tailscale gestisce da solo il NAT
traversal (gira su WireGuard sotto, ma senza la configurazione manuale di
chiavi/peer/endpoint).

## Topologia

Stessa logica hub-and-spoke di prima, ma sull'IP Tailscale invece che su una
VPN WireGuard dedicata: **Windows è l'hub** (sempre acceso), Mac e Ubuntu
sono peer. Tailnet: `tail453db3.ts.net` (MagicDNS attivo — gli hostname
sotto funzionano, non serve ricordare gli IP).

| Macchina | IP Tailscale     | Hostname MagicDNS                          |
|----------|------------------|---------------------------------------------|
| Windows  | 100.64.31.18     | laptop-t9h8plht.tail453db3.ts.net           |
| Mac      | 100.81.234.102   | macbook-air-di-alberto.tail453db3.ts.net    |
| Ubuntu   | *non ancora nel tailnet* | —                                    |

Gli IP Tailscale sono stabili per dispositivo indipendentemente dalla rete
fisica (casa, ufficio, hotspot) — a differenza dell'IP LAN che cambia ogni
volta che cambia rete.

## 1. Ubuntu — va aggiunto al tailnet

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Autentica dal link che stampa, poi verifica da qualsiasi altra macchina:

```bash
tailscale status   # deve comparire anche Ubuntu
```

Prendi il suo IP Tailscale da lì e aggiorna `PUBLIC_ENDPOINT`/`REGISTRY_PUBLIC_URL`
in `.env.ubuntu` (oggi sono ancora placeholder in attesa di questo IP).

## 2. Esporre i servizi oltre 127.0.0.1

Il meccanismo è lo stesso creato per WireGuard e non cambia: ogni porta di
`docker-compose.windows.yml` è pubblicata su `${MESH_BIND_IP:-127.0.0.1}` —
di default resta solo-localhost, va valorizzato con l'IP Tailscale della
macchina.

Su Windows (hub), in `.env.windows`:

```
MESH_BIND_IP=100.64.31.18
```

Poi `cp .env.windows .env && docker compose -f docker-compose.windows.yml up -d`.

**Su Windows serve anche una regola firewall** (verificato 2026-09-13: senza,
le connessioni in ingresso vanno in timeout silenzioso, non "connection
refused" — sintomo facile da confondere con "non ho aperto il bind"):

```powershell
New-NetFirewallRule -DisplayName "HyperSpace mesh" -Direction Inbound -Protocol TCP -LocalPort 8088,8086,8081,8095,20128 -Action Allow -Profile Private
```

Su Mac e Ubuntu (`docker-compose.yml`) le porte sono già pubblicate senza
restrizione a `127.0.0.1` — non serve `MESH_BIND_IP` lì, funzionano già.

## 3. Verifica

```bash
# da qualsiasi macchina verso Windows
curl http://100.64.31.18:8088/health
curl http://100.64.31.18:8086/nodes

# oppure con l'hostname MagicDNS, equivalente
curl http://laptop-t9h8plht.tail453db3.ts.net:8088/health
```

Poi `/doctor` sul control-plane di ciascuna macchina: i check
`endpoint_reachability` e `federation_peers` devono passare.

## Attenzione alle porte: interna vs pubblicata

`PUBLIC_ENDPOINT` deve usare la porta **pubblicata sull'host**, non quella
interna al container — sono diverse per `node-1` (host `8081` → container
`8084`). Bug reale trovato durante il test del 2026-09-13: `PUBLIC_ENDPOINT`
era scritto con `:8084` in tutti e tre gli `.env`, il nodo si annunciava su
una porta che dall'esterno dava connessione rifiutata. Verifica sempre con
`docker compose ps` o leggendo il mapping nel compose file, non a memoria.

## Se un giorno serve indipendenza da un servizio terzo

Tailscale è un servizio di terze parti (coordination server Tailscale/Headscale
dietro le quinte, anche se il traffico applicativo resta peer-to-peer via
WireGuard). Se in futuro serve *non* dipendere da loro, l'alternativa è
[Headscale](https://github.com/juanfont/headscale) (server di coordinamento
self-hosted, stesso protocollo/client Tailscale) o tornare al setup WireGuard
manuale — stessa logica di `MESH_BIND_IP` sopra, cambia solo come le macchine
si scambiano IP e chiavi.
