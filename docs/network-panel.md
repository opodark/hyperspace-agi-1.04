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

- `hostctl/agent.py` — l'agent, unico file, zero dipendenze
- `control-plane/main.py` — `/network/status`, `/network/action` (proxy verso l'agent)
- `control-plane/dashboard.html` — tab "Network"
- `tests/test_hostctl.py` — test della whitelist, validazione input, auth
- `.env.example` — `HOSTCTL_TOKEN`, `HOSTCTL_PORT`, `HOSTCTL_URL`, `WIREGUARD_INTERFACE`
