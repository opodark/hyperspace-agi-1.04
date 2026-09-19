# Sincronizzare i control-plane

Come due (o più) control-plane mostrano **gli stessi dati**. Stato attuale,
meccanismo, e cosa manca. Scritto dopo averli misurati, non dedotti.

## 1. Cosa è già condiviso, oggi

Due CP su due macchine sono due **isole di dati** con un ponte solo sui **nodi**:

| Dato | Condiviso? | Come |
|---|---|---|
| Nodi della mesh | **sì** | il CP impara dagli annunci dei suoi nodi e poi pinga ogni endpoint (`heartbeat_loop` → `_poll_mesh_nodes`) |
| Modelli disponibili | **sì** (di conseguenza) | `/v1/models` li chiede ai nodi: se il nodo è noto, il modello compare |
| Routing | **sì** (di conseguenza) | i candidati sono i nodi noti e attivi |
| Task, log, web node, alias, dream, interazioni | **no** | vivono nel DB locale del CP |
| Peer federati (allowlist) | **no** | `federated_peers` è locale: ogni CP ha la propria |

Quindi: se i due CP vedono gli stessi nodi, mostrano già gli stessi modelli.
Se non li vedono, la differenza si vede subito — ed è **asimmetrica**:

```
Mac  .env: REGISTRY_PUBLIC_URL=http://100.64.31.18:8086   → legge il registry del PC
PC   .env: REGISTRY_PUBLIC_URL=http://100.64.31.18:8086   → legge il PROPRIO registry
```

Misurato il 19/09: il registry del Mac conteneva solo il nodo del Mac, quello del
PC solo il nodo del PC. A leggere `REGISTRY_PUBLIC_URL` è il **nodo**
(`node/main.py`), non il CP: il CP non legge affatto quella variabile. Il nodo da
lì scopre i peer e li annuncia al suo CP. Per questo **il Mac vede 2 nodi e il
PC 1**.

### Fase 0 — far vedere la stessa mesh (configurazione, nessun codice)

Il Mac pubblica già tutto su `0.0.0.0`, quindi è raggiungibile dal tailnet:

```
registry  http://100.81.234.102:8086     node  http://100.81.234.102:8081
gateway   http://100.81.234.102:8095     CP    http://100.81.234.102:8085
```

Sul PC, in `.env.windows`:

```
REGISTRY_PUBLIC_URL=http://100.81.234.102:8086
```

poi `docker compose up -d`. Verifica (deve elencare **2** nodi, macbook e win11):

```bash
curl -s http://localhost:8085/nodes/active | python3 -m json.tool | grep -c node_id
```

Nota: è una **attesa**, non una certezza. Il percorso — "il nodo scopre il peer
dal registry e lo annuncia al suo CP" — è verificato leggendo `node/main.py`, ma
la conferma va presa con il comando sopra sulla sua macchina.

## 2. La vista federata (codice, per tutto il resto)

I nodi sono condivisi; il resto no. Per condividerlo in **lettura**:

```
GET /federate/view       (sul CP, dietro il gateway)   → istantanea di QUESTO CP
GET /federation/views    (solo dashboard locale)        → locale + peer, in cache
```

`/federation/views` risponde con `local`, `peers[]` (ognuno con `ok`, `error`,
`view`) e `merged`: nodi deduplicati per `node_id` con `seen_by`, modelli uniti, e
il contatore `nodes_partial` = nodi visti da un CP solo, cioè la divergenza.

### Cosa esce e cosa no

La tabella `tasks` ha le colonne `prompt` e `result` col testo delle richieste e
delle risposte; i log hanno `summary` e `detail`. **Nessuna riga del DB viene
serializzata intera**: i campi si elencano uno per uno, quindi una colonna nuova
aggiunta domani non esce da sola. I messaggi di log escono **troncati a 160
caratteri** — troncati, non mascherati: se un log di interazione contiene una
frase del prompt, quei 160 caratteri si vedono.

Per questo la condivisione è **spenta di default**:

```
FEDERATION_VIEW_ENABLED=false     # true = i peer accoppiati possono leggere
FEDERATION_VIEW_TTL_S=10          # cache della vista dei peer
```

È una decisione sui **dati**, non sull'esecuzione: `/federate/execute` presta a un
peer il tuo calcolo, la vista gli mostra le tue informazioni. Si cambia anche
dalla dashboard (sezione "Federazione CP-to-CP") e la rotta esiste comunque:
risponde 403.

### Accoppiare due CP

Solo pairing manuale, mai auto-discovery (come per i task federati: vedi il
commento su `federated_peers` in `shared/db.py`). Serve l'identità del peer:

```bash
curl -s http://100.64.31.18:8085/federation/identity        # dal Mac verso il PC
# {"peer_id": "...", "pubkey": "04...", "endpoint": ""}

curl -s -X POST http://localhost:8085/federation/peers \
  -H 'Content-Type: application/json' \
  -d '{"pubkey":"04...", "endpoint":"http://100.64.31.18:8095", "label":"win11-cp"}'
```

L'endpoint del peer è il **suo federation-gateway** (8095), non il suo CP. Il
`peer_id` è `sha256(pubkey)[:40]`: non si inventa, si legge da `/federation/identity`.

Poi:

```bash
curl -s 'http://localhost:8085/federation/views?refresh=1' | python3 -m json.tool | head -40
```

## 3. Sicurezza: cosa c'è e cosa non c'è

**C'è già** (riusato, non reinventato): firma ECDSA su `timestamp + sha256(body)`
con l'identità del CP (`shared/identity.py`), verifica **lato ricevente** sempre,
allowlist dei peer con la pubkey confrontata *esattamente*, whitelist di rotte nel
gateway, e `/federation/views` **fuori** da quella whitelist: da pubblica sarebbe
una sonda verso i peer federati per chiunque non abbia le loro chiavi.

**Non c'è**: TLS fra CP (dentro il tailnet c'è WireGuard sotto, quindi il transito
è cifrato; fuori dal tailnet serve il gateway in HTTPS), rotazione o revoca delle
chiavi, cifratura del contenuto condiviso, e un login umano sulla dashboard:
**`/logs`, `/federation/peers`, `/nodes/active` rispondono senza autenticazione**.
Chi raggiunge la 8085 di questa macchina vede e riconfigura tutto. Da qui l'ordine:
prima il login sulla dashboard, poi si estende la vista.

Nota sulle chiavi: fino al 19/09 l'identità del CP **non era persistente**. Il
container scrive `/app/data` (database, `node_identity.json`, `node_private.pem`)
e il servizio `control-plane` non montava quel percorso: ogni
`docker compose up -d --build` azzerava il DB e **rigenerava la chiave ECDSA**,
quindi cambiava il `peer_id`. Osservato dal vivo in una sessione:
`225378a217ffe488...` → `7fdd15a5491e3640...`. Con un peer già accoppiato,
l'allowlist per pubkey l'avrebbe respinto senza che nessuno capisse perché.
Ora c'è `./data/control-plane:/app/data` (come `./data/node-1` per il nodo e
`${HS_DATA_DIR}` nel compose Windows), con un test che la protegge
(`tests/test_compose_persistence.py`).

## 4. Il vincolo che blocca tutto, adesso

La vista la serve il CP, quindi **entrambi i CP devono girare la stessa
versione**: il PC gira un'immagine vecchia (`/models/capabilities` → 404) e non ha
`/federate/view`. Fino al suo `docker compose up -d --build` la fusione mostrerà
`ok: false, error: "HTTP 404"` per quel peer — che è comunque l'informazione
utile: la vista dice *perché* non ha i dati.

## 5. Prossimi passi

1. Pannello in dashboard sui dati di `/federation/views`: sorgenti, nodi con
   `seen_by`, e in evidenza i `nodes_partial`.
2. Propagazione **in catena** (un peer inoltra ciò che sa dei suoi peer): oggi è a
   un salto solo, e per 3+ CP serve un TTL sulla freschezza dei dati di seconda mano.
3. Login sulla dashboard, e poi token anche sulle rotte federate, così
   l'allowlist non resta l'unica barriera.
4. Solo dopo: sincronizzazione **in scrittura** (un task creato da un CP eseguito
   su un altro). Oggi è deliberatamente read-only.
