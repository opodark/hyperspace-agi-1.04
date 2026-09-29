# Security Lab (Kali)

Il Security Lab è il banco di prova di sicurezza di HyperSpace: un container
**Kali Linux su rete host** che esegue strumenti di rete (`nmap`, `nikto`,
`gobuster`) sotto il controllo del control-plane.

È separato dal [Dev Sandbox](code-sandbox.md) per un motivo preciso: Nmap, ZAP
e simili generano traffico verso altri processi o host, mentre il sandbox
offline deve restare **senza rete**. La decisione architetturale completa è in
[development-tooling-architecture.md](development-tooling-architecture.md).

## Il confine di sicurezza

La rete host **non è** il confine: lo è la **target allowlist**. Il container
gira con `network_mode: host` e socket raw (`NET_RAW`/`NET_ADMIN`), quindi può
fare scansioni "vere" (`nmap -sS`, ARP discovery, sniffing); l'unica cosa che
lo frena è `KALI_TARGET_ALLOWLIST`, verificata **prima di ogni esecuzione** in
`hostctl`.

Tre pareti, in ordine:

1. **Target allowlist** — `KALI_TARGET_ALLOWLIST` accetta hostname esatti, IP
   esatti o subnet CIDR (es. `localhost,192.168.1.0/24`). Un target fuori lista
   viene rifiutato prima di toccare Docker.
2. **Allowlist eseguibili** — solo `nmap`, `nikto`, `gobuster`. Niente shell,
   niente comandi arbitrari.
3. **argv-only + cap** — l'azione è un `docker exec` con lista di argomenti
   (mai `shell=True`), output limitato a 256 KB e timeout limitato a 600 s.

Senza target dichiarati l'azione è chiusa (fail-closed).

## Architettura e flusso

```text
Agent / Claude (client MCP)
        │  tools/call → kali_scan
        ▼
Control Plane  (/mcp + /tools/execute)   ← auth MCP + allowlist tool
        │  POST hostctl /action "kali"
        ▼
hostctl/agent.py  (nativo sull'host, loopback)
        │  docker exec hyperspace_kali …
        ▼
Kali container  (network_mode: host, NET_RAW/NET_ADMIN)
```

Il tool `kali_scan` compare nel catalogo del control-plane (chat, MCP,
`/tools/execute`) solo quando l'operatore lo accende **e** l'host-agent è
configurato, esattamente come `shell_run` e i connettori: un tool presente ma
non funzionante è peggio di un tool assente.

## Topologia: tutto su un solo nodo

`hostctl` fa `docker exec` nel container Kali **locale** ed è deliberatamente
loopback-only ("controlla SOLO la macchina locale"). Quindi, su **uno stesso
nodo**, servono:

```text
Kali node
  ├── container Kali   (docker compose --profile security up -d --build kali)
  ├── hostctl/agent.py (processo nativo sull'host)
  └── il control-plane di quel nodo (espone kali_scan, parla con hostctl)
```

Il nodo Kali è quindi esso stesso un "main" della mesh: non è un worker remoto
comandato dal control-plane centrale.

## Attivazione per nodo

Nel `.env` del nodo che ospita il lab:

```dotenv
KALI_ENABLED=true
KALI_TARGET_ALLOWLIST=localhost,192.168.1.0/24
```

Poi, sullo stesso host:

```bash
# 1. Container Kali (profilo opt-in)
docker compose --profile security up -d --build kali

# 2. Host-agent (una volta, poi lasciarlo in esecuzione)
python hostctl/agent.py --generate-token
python hostctl/agent.py

# 3. Control-plane (per esporre kali_scan via MCP)
docker compose up -d --build control-plane
```

`HOSTCTL_TOKEN` e `HOSTCTL_URL` sono già usati da `shell_run`; su Linux, se il
control-plane non raggiunge `host.docker.internal`, impostare esplicitamente
`HOSTCTL_URL` (e `HOSTCTL_BIND`) come descritto in
[host-access.md](host-access.md).

## Linux vs Windows/Mac

`network_mode: host` dà la **rete vera dell'host solo su Linux nativo**:

| Host | Rete visibile da Kali |
|---|---|
| Linux nativo | ✅ la LAN reale (SYN scan, ARP, sniffing funzionano) |
| Windows / Mac (Docker Desktop) | ⚠️ la rete della VM Docker, non la LAN |

Per il pentest "vero" il nodo Kali va su **Linux**. Su Windows/Mac il lab resta
utile solo per `localhost` e gli altri container.

## Strumenti (prima dotazione)

| Tool | Uso tipico |
|---|---|
| `nmap` | scansione porte, discovery, fingerprinting |
| `nikto` | scan vulnerabilità su server web |
| `gobuster` | brute-force di directory/DNS |

ZAP, `sqlmap`, `ffuf` e altri entrano in un secondo momento, sempre come voci
esplicite dell'allowlist.

## Uso

Tool `kali_scan`, parametri:

| Parametro | Tipo | Note |
|---|---|---|
| `tool` | string | `nmap` \| `nikto` \| `gobuster` |
| `target` | string | host o IP (deve stare in `KALI_TARGET_ALLOWLIST`) |
| `args` | array di string | flag extra, es. `["-sT", "-p", "1-100"]` |
| `timeout` | integer | secondi (il server applica il suo tetto) |

Esempio via `/tools/execute`:

```json
{"tool_name": "kali_scan", "args": {"tool": "nmap", "target": "localhost", "args": ["-sT", "-p", "80,443"]}}
```

Via MCP il tool si chiama come ogni altro tool pubblicato, a patto che il
client MCP abbia `kali_scan` nella sua allowlist (`MCP_CLIENT_TOOLS`).

## Limiti e prossimi passi

- La prima dotazione è la sola triade `nmap`/`nikto`/`gobuster`; ogni nuovo
  eseguibile va aggiunto esplicitamente all'allowlist.
- Il target è verificato per hostname esatto / IP / CIDR, ma non c'è ancora un
  enforcement su DNS/redirect (i tool che seguono redirect esterni non sono
  ancora isolati): rientra nell'hardening prima di un uso oltre il laboratorio
  fidato (vedi development-tooling-architecture.md).
- Nessun report strutturato unificato per ora: l'output è quello grezzo dello
  strumento, troncato a 256 KB.
