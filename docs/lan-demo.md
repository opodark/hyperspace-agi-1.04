# Demo su rete locale (LAN / hotspot)

Guida per fare una demo della mesh HyperSpace nel modo più veloce possibile,
quando tu e chi partecipa siete sulla stessa rete fisica — WiFi di ufficio o
casa, oppure l'hotspot del tuo telefono. **Niente ngrok, niente tunnel**: chi
è sulla stessa rete raggiunge il tuo laptop direttamente tramite il suo
indirizzo IP.

## Quando usarla

- Vuoi mostrare la mesh a qualcuno nella stessa stanza (o collegato al tuo
  hotspot) senza perdere tempo a configurare tunnel pubblici.
- Va bene anche se cambi rete spesso (casa → ufficio → hotspot): l'unica cosa
  che cambia è un indirizzo IP, che lo script rileva da solo.

## Prerequisiti

- Il tuo laptop e chi partecipa devono essere **sulla stessa rete** (stessa
  WiFi, o tutti collegati allo stesso hotspot).
- Lo stack pesante di HyperSpace deve essere già su (`./setup.sh` una volta,
  o `docker compose up -d --build`). Se hai già lavorato su questa macchina
  probabilmente è già acceso — lo script del passo successivo te lo dice.

## Passo 1 — lancia lo script

```bash
./demo-lan.sh
```

Non avvia né ricostruisce nulla da solo: guarda cosa gira già e ti stampa
esattamente cosa dare in mano a chi partecipa. Se qualcosa non è acceso, ti
dice il comando preciso per accenderlo.

L'output ha tre parti:

1. **Rete rilevata** — il tuo indirizzo IP su questa rete adesso (es.
   `10.129.63.44`). È quello che darai agli ospiti.
2. **Stato dei servizi** — cosa è già acceso (✓) e cosa manca (✗), con il
   comando per accenderlo.
3. **Cosa dare agli ospiti** — i link/istruzioni pronti da copiare e
   incollare in una chat o mostrare a voce.

## Passo 2 — scegli cosa mostrare

Ci sono tre modi diversi in cui qualcuno può partecipare alla demo. Puoi
usarne uno solo o combinarli.

### A. Solo chattare (il più semplice, zero setup per l'ospite)

Chi partecipa apre semplicemente:

```
http://<il-tuo-IP>:3000
```

È Open WebUI, già in esecuzione su questa macchina — non serve avviare o
ricostruire nulla, l'unica cosa che serve è l'indirizzo IP giusto. Chiunque
sulla stessa rete lo raggiunge da telefono, tablet o laptop, senza installare
niente.

### B. Nodo browser leggero (zero installazione, mostra la mesh che si popola)

Chi partecipa:

1. Apre l'URL del web-node (il pacchetto minimesh — se non sai dove sia
   deployato, chiedi a chi organizza la demo, di solito qualcosa come
   `http://<IP-di-chi-lo-ospita>:3001`).
2. Nel pannello, va su **Node panel**, trova il campo `control_plane_url` e
   incolla:
   ```
   http://<il-tuo-IP>:8085
   ```
3. Clicca **save & reload**, poi **▶ Join the mesh**.

Il suo tab del browser diventa un nodo della mesh, visibile nella dashboard e
in `infra-ui`. Non installa niente, non scarica modelli pesanti.

**Cosa funziona oggi:** il nodo si registra, manda heartbeat, e può ricevere
un task creato manualmente (via `/task/create` + `/task/assign`) mettendosi
in coda per lui e restituendo un risultato. **Cosa NON è ancora reale:**
quando il nodo browser esegue un task ricevuto dalla mesh, oggi risponde con
un risultato-stub ("Executed by browser stub..."), non con inferenza WebLLM
vera — quella gira solo in modalità **Standalone** del web-node (offline, non
mesh). Per la demo: usa "Join the mesh" per mostrare la mesh che si popola in
tempo reale; usa "Standalone" nello stesso web-node per mostrare inferenza
WebLLM reale sul dispositivo.

### C. Nodo Docker pesante (colleghi tecnici, con Docker/Ollama già installati)

Chi partecipa, sul proprio laptop:

```bash
WEB_NODE_URL=http://<url-del-web-node-se-lo-vuole-anche-lui> ./join-mesh.sh
```

Risponde:
- `1` (nodo Docker pesante) al primo bivio
- `2` (hub locale) quando lo script chiede quale hub raggiungere, e conferma
  o incolla l'IP del laptop che fa da hub (il tuo, quello dove hai lanciato
  `demo-lan.sh`)

Da lì in poi il flusso è il solito: verifica dipendenze, endpoint,
backend LLM, avvio. Serve Docker installato sul suo laptop — più lento da
preparare, ma mostra un nodo di inferenza reale, non solo browser.

## Risoluzione problemi

**Un ospite non riesce a raggiungere il tuo IP.**
1. Verifica che sia davvero sulla stessa rete/hotspot (non su dati mobili).
2. **macOS chiede il permesso "Rete locale"** la prima volta che
   un'app (Docker Desktop, Terminal) viene raggiunta da un altro
   dispositivo sulla rete. Vai in **Impostazioni di Sistema › Privacy e
   sicurezza › Rete locale** e assicurati che Docker/Terminal siano
   abilitati — altrimenti l'ospite si blocca anche con l'IP corretto.
3. **Hotspot del telefono**: la maggior parte dei telefoni NON isola i
   dispositivi connessi tra loro (si comporta come una WiFi normale), ma
   alcuni operatori/modelli lo fanno. Se il problema persiste solo su
   hotspot, prova a far pingare l'IP all'ospite (`ping <il-tuo-IP>`) per
   isolare se è un problema di isolamento client.

**Ho cambiato rete a metà demo (es. da WiFi a hotspot).**
L'IP cambia. Rilancia `./demo-lan.sh`, prendi il nuovo IP, e ridallo a chi
è già collegato (deve aggiornare anche `control_plane_url` se aveva un nodo
browser attivo).

**`demo-lan.sh` segnala che il proxy Caddy di minimesh occupa la porta 8085.**
Quel proxy (`control-plane-proxy` nel repo minimesh) ha senso solo quando i
servizi del control plane sono microservizi separati raggiungibili con i nomi
che si aspetta (`control-plane-core:9001`, ecc.) — non è il caso qui, dove il
control-plane di 1.04 è un unico servizio Flask su una porta sola. Per questa
demo va fermato così il control-plane vero può pubblicarsi sulla 8085:
```bash
docker stop minimesh-control-plane-proxy-1
```
Poi ricrea il control-plane per far applicare il mapping di porta già
dichiarato in `docker-compose.yml`:
```bash
docker compose up -d control-plane
```

**Come faccio a vedere chi si è unito in tempo reale?**
`infra-ui` (dashboard 3D, `http://localhost:8099` sul tuo laptop) mostra i
nodi che entrano/escono dalla mesh in tempo reale — ottimo da proiettare
durante la demo.

## Perché funziona senza tunnel

Tutti i servizi del control plane (`control-plane`, `registry`, `node-1`,
Open WebUI) sono già bindati su `0.0.0.0` dentro Docker — cioè accettano
connessioni da qualunque indirizzo, non solo da `localhost`. Quando pubblichi
una porta con `"8085:8085"` in `docker-compose.yml`, chiunque sulla stessa
rete fisica che conosce il tuo IP può raggiungerla direttamente. Il CORS sul
control-plane è già aperto (`Access-Control-Allow-Origin: *`), quindi anche
le chiamate del browser da un'altra macchina passano senza configurazione
aggiuntiva. L'unica cosa che manca "di default" è *sapere* il proprio IP sulla
rete corrente — è esattamente quello che automatizza `demo-lan.sh`.
