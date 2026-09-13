# Mesh cross-machine via WireGuard

Sostituisce i tunnel ngrok free-tier come punto d'incontro fra Mac, Ubuntu e
Windows. Motivo del cambio: gli URL ngrok gratuiti sono effimeri (cambiano a
ogni riavvio senza un dominio a pagamento) — quelli hardcoded in `.env.mac` /
`.env.ubuntu` e persino nel default di `.env.example` erano morti
(`ERR_NGROK_3200`) al momento in cui questo documento è stato scritto,
2026-09-13.

## Topologia

**Windows (Asus) è l'hub**: sempre acceso, WireGuard gira in ascolto lì.
Mac e Ubuntu sono peer che si connettono a Windows. Non è una mesh
WireGuard piena (Mac non parla direttamente a Ubuntu via WG) — per il
routing applicativo va bene lo stesso: ogni nodo annuncia se stesso al
registry su Windows, e il control-plane di Windows fa da punto di
coordinamento per la mesh HyperSpace (`REGISTRY_PUBLIC_URL`).

```
                 ┌─────────────────────────┐
                 │   Windows (10.13.13.1)   │
                 │   hub WireGuard          │
                 │   registry + control-    │
                 │   plane + omniroute +    │
                 │   federation-gateway     │
                 └────────┬────────┬────────┘
                          │        │
                 WG peer  │        │  WG peer
                          │        │
            ┌─────────────┘        └─────────────┐
            ▼                                     ▼
   Mac (10.13.13.2)                      Ubuntu (10.13.13.3)
   nodo demo (leaf)                      nodo primario/pesante
```

Schema IP: rete privata `10.13.13.0/24`, dedicata a questa VPN (scelta per
non collidere con le LAN domestiche tipiche in `192.168.0.0/16` o
`10.0.0.0/8`). `.1` = Windows, `.2` = Mac, `.3` = Ubuntu. Porta UDP
`51820` (default WireGuard) sull'hub.

## Prerequisito da verificare prima di tutto

Windows deve accettare connessioni UDP in ingresso sulla porta 51820:

- se ha un IP pubblico statico o un DDNS → basta un port-forward UDP 51820
  sul router verso l'IP locale del PC Windows;
- se è dietro CGNAT (niente controllo sul router, o l'IP pubblico cambia
  e non c'è DDNS) → il port-forward non funziona, serve un relay (es. un
  piccolo VPS, o si torna a valutare Tailscale che gestisce il NAT
  traversal da solo). Verificalo prima di procedere: se il router non ha
  un accesso amministrativo raggiungibile, dillo prima di generare le
  chiavi sulle altre macchine.

## 1. Windows — hub

PowerShell da amministratore:

```powershell
winget install WireGuard.WireGuard
wg genkey | Tee-Object windows.key | wg pubkey | Tee-Object windows.pub
```

Configura il port-forward UDP 51820 sul router verso l'IP locale di questo
PC, poi crea `C:\HyperSpace\wireguard\wg0.conf`:

```ini
[Interface]
PrivateKey = <contenuto di windows.key>
Address = 10.13.13.1/24
ListenPort = 51820

# Mac
[Peer]
PublicKey = ZIqrw5x7SlEDe6AjURsbVm0PC96rNFRqBChHIsYk5x4=
AllowedIPs = 10.13.13.2/32

# Ubuntu — sostituisci con la chiave pubblica generata sul passo 3
[Peer]
PublicKey = <chiave pubblica di Ubuntu>
AllowedIPs = 10.13.13.3/32
```

Attiva il tunnel dal client WireGuard per Windows (importa `wg0.conf`),
oppure da PowerShell con `wireguard /installtunnelservice`.

## 2. Mac — peer (chiavi già generate in questa sessione)

Chiave privata: `~/.wireguard-hyperspace/mac.key` (permessi 600, non nel
repo). Chiave pubblica, da dare a Windows: vedi sopra, già inserita nel
`wg0.conf` d'esempio.

```bash
sudo mkdir -p /opt/homebrew/etc/wireguard
sudo tee /opt/homebrew/etc/wireguard/wg0.conf > /dev/null <<CONF
[Interface]
PrivateKey = $(cat ~/.wireguard-hyperspace/mac.key)
Address = 10.13.13.2/24

[Peer]
PublicKey = <chiave pubblica di Windows, da windows.pub>
Endpoint = <IP-o-DDNS-pubblico-di-Windows>:51820
AllowedIPs = 10.13.13.0/24
PersistentKeepalive = 25
CONF

sudo wg-quick up wg0
wg show   # conferma handshake dopo che anche Windows è su
```

`PersistentKeepalive` è necessario perché il Mac è dietro NAT lato client:
senza keepalive periodico il router droppa la sessione UDP e Windows non
riesce più a raggiungerlo di sua iniziativa.

## 3. Ubuntu — peer

```bash
sudo apt install -y wireguard
wg genkey | sudo tee /etc/wireguard/ubuntu.key | wg pubkey | sudo tee /etc/wireguard/ubuntu.pub
sudo chmod 600 /etc/wireguard/ubuntu.key
cat /etc/wireguard/ubuntu.pub   # -> dalla a Windows, aggiungila al wg0.conf del passo 1

sudo tee /etc/wireguard/wg0.conf > /dev/null <<CONF
[Interface]
PrivateKey = $(sudo cat /etc/wireguard/ubuntu.key)
Address = 10.13.13.3/24

[Peer]
PublicKey = <chiave pubblica di Windows>
Endpoint = <IP-o-DDNS-pubblico-di-Windows>:51820
AllowedIPs = 10.13.13.0/24
PersistentKeepalive = 25
CONF

sudo wg-quick up wg0
sudo systemctl enable wg-quick@wg0   # riparte da solo al boot
wg show
```

## 4. Verifica prima di toccare Docker

```bash
ping 10.13.13.1   # da Mac e da Ubuntu, deve rispondere
```

Se non risponde: il problema è la VPN, non HyperSpace. Non procedere oltre
finché questo ping non funziona da entrambi i lati.

## 5. Config applicativa

Su Windows, `.env.windows` deve avere `MESH_BIND_IP=10.13.13.1` (vedi
commento in cima a `docker-compose.windows.yml`) — altrimenti anche con la
VPN su, le porte restano legate a `127.0.0.1` e nessun peer le raggiunge
comunque.

`.env.mac`, `.env.ubuntu` e `.env.example` sono già aggiornati con questa
convenzione IP (vedi diff). Dopo `docker compose up -d --build` su tutte e
tre le macchine, verifica con `/doctor` (aggiunto da `cips` il 2026-08-06)
sul control-plane di ciascuna: i check `endpoint_reachability` e
`federation_peers` devono passare.

## Se il port-forward su Windows non è praticabile

Fermati e dillo prima di generare le chiavi sulle altre macchine: si passa
a un relay (piccolo VPS con IP pubblico che fa da hub invece di Windows) o
si riconsidera Tailscale, che le variabili già presenti in `.env.windows`
(`TS_ENABLED`, `TS_HOSTNAME`, ...) presupponevano prima di questa scelta.
