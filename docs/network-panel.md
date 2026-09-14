# Pannello Network (ngrok / Tailscale / WireGuard)

Un pannello unico nella dashboard del control-plane per vedere e controllare
i tre meccanismi di rete che questo repo usa per far parlare le macchine
della mesh fra loro, invece di ricordarsi comandi diversi su tre terminali
diversi.

## Perché serve un pezzo separato (`hostctl/`)

Il control-plane gira **dentro un container Docker**. WireGuard, Tailscale e
ngrok girano **sull'host** (Mac, Windows o Ubuntu). Un container non può
lanciare `tailscale status` o `wg show` sull'host che lo ospita — non è un
limite di questo progetto, è come funziona Docker.

La soluzione è `hostctl/agent.py`: un processo Python separato, **senza
dipendenze esterne** (solo standard library), che gira nativo sulla macchina
e viene raggiunto dal control-plane via `host.docker.internal`.

```
┌─────────────────────────── una macchina ───────────────────────────┐
│                                                                      │
│   ┌── container Docker ──┐        HTTP        ┌── processo host ──┐ │
│   │   control-plane       │ ───────────────►   │  hostctl/agent.py │ │
│   │   (dashboard, /network│  host.docker.      │  (127.0.0.1:8765) │ │
│   │    /status, /action)  │  internal:8765     │                   │ │
│   └───────────────────────┘                    └─────────┬─────────┘ │
│                                                            │          │
│                                          ngrok / tailscale / wg-quick │
└────────────────────────────────────────────────────────────────────┘
```

Ogni macchina gestisce **solo se stessa**: non esiste un modo di chiedere
all'agent del Mac di toccare la rete di Ubuntu. Il pannello che vedi nella
dashboard di una macchina mostra e controlla solo quella macchina.

## Sicurezza — leggi questa sezione prima di avviarlo

Questo agent può avviare/fermare tunnel e alzare/abbassare interfacce di
rete. Tre cose lo tengono sicuro, e nessuna delle tre è opzionale:

1. **Ascolta solo su `127.0.0.1`.** Mai `0.0.0.0`. Il container lo
   raggiunge comunque, perché `host.docker.internal` instrada verso
   l'host — non serve esporlo oltre il loopback.
2. **Ogni richiesta richiede un token**, confrontato a tempo costante
   (`hmac.compare_digest`). Il bind su loopback da solo non basta: in
   certe configurazioni Docker, `host.docker.internal` è raggiungibile
   più largamente del previsto.
3. **Whitelist fissa di azioni**, ciascuna un comando precompilato (mai
   costruito concatenando input libero in una shell). Anche i parametri
   che passano (es. la porta di `ngrok_start`) sono validati rigidamente
   prima di finire in un argv — mai in una stringa di shell.

L'agent **si rifiuta di partire** finché non generi un token esplicitamente.
Non esiste un default silenzioso.

## Setup

### 1. Genera il token (una volta sola, per macchina)

```bash
cd hyperspace-agi-1.04
python3 hostctl/agent.py --generate-token
```

Scrive `HOSTCTL_TOKEN=...` nel `.env` della repo — lo stesso file che legge
il control-plane, quindi non serve copiarlo a mano da nessuna parte.

### 2. Avvia l'agent

```bash
python3 hostctl/agent.py
```

Resta in primo piano (o mettilo dietro un `screen`/`tmux`/servizio di
sistema — l'avvio automatico al boot non è ancora incluso in questa prima
versione). Se lo fermi, il pannello Network mostra "host-agent non
raggiungibile": il resto della mesh continua a funzionare normalmente,
questo pannello è solo di controllo/diagnostica.

### 3. Riavvia il control-plane

Deve rileggere il `.env` per vedere `HOSTCTL_TOKEN`:

```bash
docker compose restart control-plane
```

### 4. Apri la dashboard → tab "Network"

Tre card: ngrok, Tailscale, WireGuard. Ognuna mostra lo stato corrente e ha
i bottoni per le azioni disponibili.

## WireGuard: serve una regola sudoers

`wg-quick up/down` richiede privilegi di root. L'agent lancia
`sudo -n wg-quick ...` (`-n` = non-interattivo: se non può autenticarsi
fallisce subito con un errore chiaro invece di restare appeso ad aspettare
una password che non arriverà mai, dato che gira come processo headless).

Per farlo funzionare senza esporre l'intero comando `sudo` all'agent,
autorizza **solo** i due comandi esatti che servono. Su Linux/macOS,
`sudo visudo -f /etc/sudoers.d/hostctl` e aggiungi (sostituisci `opo` con
l'utente che lancia l'agent, e il path con quello reale di `wg-quick`
— `which wg-quick`):

```
opo ALL=(root) NOPASSWD: /opt/homebrew/bin/wg-quick up wg0, /opt/homebrew/bin/wg-quick down wg0
```

Se cambi `WIREGUARD_INTERFACE` nel `.env`, aggiorna anche questa riga: il
nome dell'interfaccia è parte del comando autorizzato.

Senza questa riga, i bottoni WireGuard del pannello restituiscono
"sudo: a password is required" — pulito, non un crash, ma non fanno nulla.

## Cosa fa ciascuna azione

| Azione | Comando reale | Note |
|---|---|---|
| `ngrok_status` | `GET http://127.0.0.1:4040/api/tunnels` | Sola lettura, l'API locale di ngrok |
| `ngrok_start` | `ngrok http <porta>` | La porta è validata (intero 1-65535) prima di finire nell'argv; rifiuta un secondo avvio se un tunnel tracciato è già vivo |
| `ngrok_stop` | invia `SIGTERM` al processo tracciato | Verifica che il PID tracciato sia *davvero* ancora un processo ngrok prima di terminarlo — un PID riusato dopo un riavvio non viene toccato |
| `tailscale_status` | `tailscale status --json` | Sola lettura |
| `tailscale_up` / `tailscale_down` | `tailscale up` / `tailscale down` | Se serve autenticazione interattiva (primo login), va fatta a mano una volta da terminale — il pannello non gestisce flussi OAuth |
| `wg_status` | `wg show` | Sola lettura, funziona anche senza sudoers |
| `wg_up` / `wg_down` | `sudo -n wg-quick up/down <interfaccia>` | Richiede la regola sudoers sopra |
| `ble_scan` | `BleakScanner.discover()` | Sola lettura, richiede `pip install bleak` (unica azione con una dipendenza esterna — vedi sotto). Non inclusa nel poll automatico di `/network/status`: dura diversi secondi, va lanciata a mano |

## Bottiglie: discovery firmato + proof-of-work

Vive nel control-plane (non nell'host-agent: non serve accesso all'host, è
logica pura + una rotta di rete), rotte `/bottles/publish`, `/bottles/list`,
`/bottles/announce`. Sostituisce l'idea originale — pubblicare annunci su
Pastebin/bacheche pubbliche generiche — scartata perché ricalca troppo da
vicino un *dead-drop resolver* da command-and-control di malware (rischio
concreto: ban, violazione ToS, finire in una IOC feed di threat-intel).

Una bottiglia è un annuncio "sono il nodo X, raggiungimi qui" firmato con
l'identità ECDSA del nodo (`shared/identity.py`, la stessa chiave già usata
per firmare le richieste inter-nodo — nessuna nuova primitiva crittografica)
più un proof-of-work stile Bitmessage (`shared/bottle.py`): un nonce che
deve portare l'hash del payload ad avere N bit a zero in testa. Il PoW rende
costoso inondare un relay di annunci falsi; la firma rende impossibile
falsificare l'identità di un nodo che non controlli.

Misurato su questo Mac (single-core, Python puro): ~600.000 hash/s. A 20 bit
(default) il mining richiede tipicamente meno di 2 secondi; il pulsante
"Annuncia questo nodo" nella dashboard blocca per quel tempo, è previsto.

Il "relay" è semplicemente un altro control-plane HyperSpace: `/bottles/announce`
senza `relay_url` pubblica sul nodo stesso (funziona anche in locale, senza
altre macchine); con `relay_url` inoltra a `/bottles/publish` su quell'altro
nodo. Storage in memoria (non su disco: sono annunci con TTL, non dati da
conservare), al più una bottiglia per pubkey — una nuova sostituisce la
precedente dello stesso nodo — con un tetto massimo di bottiglie distinte e
rate-limit per IP come difesa in profondità oltre al PoW.

## Esplorato ma non costruito: WiFi mesh, Bluetooth peripheral, ham radio

Verificato il 2026-09-14, prima di scrivere codice non testabile per davvero:

- **WiFi mesh diretto (senza router)**: su questo Mac AWDL (il livello sotto
  AirDrop) è già attivo — ma è proprietario Apple, nessuna API pubblica per
  app di terze parti, e non esiste su Windows/Linux. Un vero mesh WiFi
  cross-platform richiederebbe tre implementazioni diverse (WiFi Direct via
  wpa_supplicant su Linux, WiFi Direct API su Windows, niente di equivalente
  esposto su macOS) — non è un pomeriggio di lavoro, è un progetto a sé.
- **Bluetooth Low Energy, annuncio (peripheral/GATT server)**: `bleak`
  (unica libreria BLE seria e cross-platform in Python, MIT) fa **solo**
  scansione — è letteralmente descritta come "client", non "server". Per
  annunciarsi servirebbe CoreBluetooth via PyObjC su macOS, BlueZ via D-Bus
  su Linux, WinRT su Windows: tre percorsi nativi separati, nessuno scritto
  qui. Quello che c'è oggi (`ble_scan`) è solo la metà "vedo chi c'è vicino".
- **Ham radio (APRS/Winlink/Packet Radio-AX.25)**: nessun hardware presente
  su nessuna delle tre macchine (né SDR, né TNC, né radio) — scrivere codice
  per trasmettere/ricevere su RF senza poterlo verificare per davvero
  avrebbe rotto la regola tenuta per tutta questa fase del progetto
  (verificare sul serio, non solo scrivere). Provato **APRS-IS**, il ponte
  internet della rete APRS (nessun hardware radio richiesto per riceverne il
  traffico): un primo tentativo di connessione a `rotate.aprs2.net:14580`
  è riuscito (banner del server ricevuto), un secondo è stato respinto con
  "Login by user not allowed" — il nominativo generico `N0CALL` viene spesso
  bloccato dai singoli server del pool. Serve un nominativo radioamatore
  vero anche solo per ricevere in modo affidabile. **Trasmettere su
  frequenze radioamatoriali richiede in ogni giurisdizione una licenza da
  radioamatore**, oltre all'hardware — non aggirabile con codice.

Se in futuro arriva hardware vero (un TNC, un RTL-SDR, un nominativo
radioamatore) o tempo per il lavoro nativo per-OS sul BLE peripheral, questi
tornano costruibili con lo stesso standard di verifica del resto del
pannello. Fino ad allora, meglio onesti sul limite che codice che non si può
testare.

## Limiti di questa prima versione

- Nessun avvio automatico al boot dell'agent (va lanciato a mano, o con lo
  strumento di servizio della tua piattaforma — systemd/launchd/Task
  Scheduler non sono ancora inclusi qui).
- `ngrok_start` non salva la porta scelta da un riavvio all'altro
  dell'agent: se il processo hostctl si riavvia mentre un tunnel è attivo,
  lo stato del PID resta tracciato correttamente (`~/.hyperspace/hostctl_state.json`),
  ma la UI non "ricorda" quale porta avevi scelto nell'input.
- `tailscale up` con un flusso di login nuovo (mai autenticato su questa
  macchina) va fatto interattivamente da terminale la prima volta.

## File coinvolti

- `hostctl/agent.py` — l'agent; zero dipendenze tranne `ble_scan` (`bleak`, opzionale)
- `shared/bottle.py` — logica bottiglie: proof-of-work + validazione, nessuna dipendenza esterna
- `control-plane/main.py` — `/network/status`, `/network/action` (proxy verso l'agent), `/bottles/publish`, `/bottles/list`, `/bottles/announce`
- `control-plane/dashboard.html` — tab "Network" (card ngrok/Tailscale/WireGuard/BLE + sezione Bottiglie)
- `tests/test_hostctl.py` — test della whitelist, validazione input, auth, `ble_scan`
- `tests/test_bottle.py` — test del proof-of-work e della validazione delle bottiglie
- `.env.example` — `HOSTCTL_TOKEN`, `HOSTCTL_PORT`, `HOSTCTL_URL`, `WIREGUARD_INTERFACE`, `BLE_SCAN_SECONDS`, `BOTTLE_MAX_COUNT`, `BOTTLE_MAX_AGE_S`, `BOTTLE_RATE_MAX_PER_HOUR`, `BOTTLE_RELAY_URL`
