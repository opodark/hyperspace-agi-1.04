# Web Node — Technical Specification

The **Web Node** is the browser-first execution path for HyperSpace-AGI.

It allows devices that cannot run Docker or heavy local runtimes to participate in the mesh by executing lightweight, safe tasks directly in the browser.

## Goals

- Enable participation from low-resource devices (laptops, tablets, managed corporate browsers)
- Provide a low-friction onboarding path (no Docker, no model downloads)
- Execute constrained, safe tasks only
- Register capabilities dynamically with the Control Plane

## Target Tasks (Phase 1)

| Task                | Type          | Notes                              |
|---------------------|---------------|------------------------------------|
| Translation         | Text          | Small context windows              |
| Embeddings          | Vector        | Short documents                    |
| Summarization       | Text          | Extractive or abstractive (small)  |
| Moderation          | Classification| Content safety / policy checks     |
| Simple Validation   | Structured    | JSON schema validation, etc.       |

**Important**: Heavy inference or long-running tasks are **not** routed to web nodes.

## Architecture

```
Browser (Web Node)
    │
    ├── Registers with Control Plane (via Registry or direct)
    ├── Declares capabilities + resource limits
    ├── Receives Task Envelope (JSON)
    ├── Executes task locally (WebAssembly / Transformers.js / WebLLM / etc.)
    └── Returns result + metadata
```

## Communication Protocol (Planned)

The web node communicates with the Control Plane over:

- WebSocket (preferred for low latency)
- Or HTTPS + long polling (fallback)

### Registration

```json
POST /register
{
  "node_id": "web-uuid-xxx",
  "type": "web-node",
  "capabilities": ["translate", "summarize", "embed"],
  "max_context": 4096,
  "browser": "Chrome 126",
  "public_endpoint": null
}
```

### Task Envelope (example)

```json
{
  "task_id": "t-abc123",
  "type": "summarize",
  "payload": {
    "text": "...",
    "max_length": 200
  },
  "constraints": {
    "timeout_ms": 30000,
    "max_tokens": 512
  }
}
```

## Runtime Options

| Runtime                    | Status     | Use Case                     |
|---------------------------|------------|------------------------------|
| Transformers.js           | Recommended| Embeddings + small models    |
| WebLLM / WebGPU           | Future     | On-device LLMs (when stable) |
| Native browser APIs       | Basic      | Translation via Web API      |
| WASM modules              | Supported  | Custom lightweight models    |

## Folder Structure

Target layout above is not built yet. What actually exists today (2026-09-19):

```
web-node/
├── README.md
├── package.json                 # "type": "module", script test/check
├── index.html                   # UI di consenso, configurazione e stato
├── src/
│   ├── protocol.js              # envelope, validazione, versione protocollo
│   ├── capabilities.js          # rilevamento ONESTO delle capability del browser
│   ├── task-runner.js           # handler web-safe (summarize/validate_json/moderate/...)
│   ├── transport.js             # register/poll/result, fetch iniettabile
│   └── index.js                 # WebNode: consenso, long-poll, ciclo di lavoro
├── apps/extension/
│   ├── manifest.json            # MV3 con popup e host_permissions
│   ├── popup.html
│   └── popup.js                 # lanciatore: il runtime resta la pagina
└── tests/
    └── web-node.test.mjs        # 24 check, senza browser e senza dipendenze
```

Il runtime supportato e' la **pagina** (`index.html`): un service worker MV3
viene sospeso dal browser e non puo' sostenere il long-poll che tiene il nodo
vivo nella mesh. Per questo l'estensione apre e controlla la pagina invece di
ospitare il nodo — e' una scelta deliberata, non una scorciatoia.

Lato control-plane: `shared/web_node.py` (registry e coda dei task), le route
`/web/register`, `/web/poll`, `/web/result`, `/web/tasks`, `/web/status`, e i
test `tests/test_web_node.py` + `tests/test_web_node_routes.py`. La verifica
end-to-end sull'app vera sta in `scripts/verify_web_node_e2e.py`.

## Security & Constraints

- All tasks must be **stateless** or use ephemeral state
- No access to local filesystem or sensitive APIs
- Strict timeout and token limits enforced by Control Plane
- Only tasks explicitly marked as "web-safe" are routed here
- Capability declaration must be honest (Control Plane can audit)

## Next Steps (Implementation)

1. Basic registration + heartbeat
2. Task envelope receiver
3. Integration with Transformers.js for embeddings + summarization
4. Browser Extension packaging
5. Capability declaration UI (for user consent)

## Status (as of 2026-09-19)

- Registrazione con capability, heartbeat e ciclo di lavoro: implementati
- Coda lato control-plane con whitelist dei task web-safe, limiti di coda,
  payload e TTL, e un solo task in volo per nodo (mai doppia consegna)
- Un web node non e' indirizzabile e `_best_endpoint` lo esclude dal routing
  chat: prima annunciava `browser://<id>` e il control-plane provava davvero a
  chiamare `http://browser://<id>/v1/chat/completions`
- Handler implementati: `summarize` (estrattivo), `validate_json` (sottoinsieme
  minimo di validazione), `moderate` (euristica sui pattern, dichiarata tale).
  `translate` ed `embed_texts` girano solo con un runtime iniettato: il nodo non
  dichiara capability che non sa servire
- Consenso esplicito obbligatorio: `new WebNode({consent: false})` non parte
- Test: 24 check JS (`npm test`, senza browser) + 33 Python nella suite
  principale + `scripts/verify_web_node_e2e.py` sull'app vera
- Non implementato: WebSocket (il long-poll e' la scelta deliberata, l'estensione
  non puo' sostenere una connessione persistente), pubblicazione sullo store,
  e embeddings reali senza portare un runtime di modelli nel browser

This component is intentionally kept small and optional. It is an **addition** to the mesh, not a core dependency.

## Deploy di questa pagina (e cosa NON sta altrove)

Questo componente e' un **sito statico**: `index.html` + `src/*.js`, nessun
server, nessuna fase di build, e la configurazione arriva a runtime. Per questo
e' l'unico pezzo del progetto che sta su un host di pagine statiche (Vercel,
Netlify, Cloudflare Pages, GitHub Pages). Il resto no: vedi l'ultima sezione.

### Vercel — import e deploy

Il componente da deployare e' `web-node/`. La configurazione sta in
`web-node/vercel.json`, che dichiara di non fare ne' install ne' build (non ci
sono dipendenze: `npm test` e `npm run check` girano con Node puro) e imposta gli
header. Non serve nessun `vercel.json` alla radice, e non va toccata.

**Dalla dashboard**

1. vercel.com → **Add New…** → **Project**
2. **Import Git Repository** → autorizza GitHub se non l'hai gia' fatto → scegli
   `opodark/hyperspace-agi-1.04`
3. Nella schermata di configurazione, **prima** di premere Deploy:
   - **Root Directory** → `Edit` → `web-node`  ← *e' il passo che evita l'errore*
   - **Framework Preset** → `Other`
   - **Build and Output Settings** → lascia `Build Command` e `Output Directory`
     come sono: li governa `web-node/vercel.json`. In particolare
     `outputDirectory` e' `.` (la cartella stessa), perche' qui non c'e' una
     build che generi un `public/` o un `dist/`: senza quella riga Vercel cerca
     `public/`, non lo trova, e il deploy fallisce con *"No Output Directory
     named public found after the Build completed"*.
   - **Environment Variables** → nessuna: un sito statico non le legge a runtime
4. **Deploy**

**Dalla riga di comando** (piu' corto: la root del progetto e' la cartella
corrente, quindi non c'e' niente da configurare a mano):

```bash
cd web-node
npx vercel login
npx vercel --prod
```

**Verifica dopo il deploy**

```bash
curl -sI https://<progetto>.vercel.app/ | grep -iE 'referrer|cache-control'
# atteso: referrer-policy: no-referrer
#         cache-control: no-cache, must-revalidate
```

Poi apri la pagina: deve mostrare un `nodeId` e l'elenco delle capability che
quel browser sa servire (`detectCapabilities`), con sotto una nota se non ne
trova nessuna. Infine puntala al control-plane:

```
https://<progetto>.vercel.app/?cp=https://<il-tuo-control-plane>
```

**Perche' gli header sono questi e non altri.** `Referrer-Policy: no-referrer`
perche' l'URL del control-plane viaggia in `?cp=`: senza, finirebbe nel `Referer`
regalato a qualunque risorsa di terze parti. `Cache-Control: no-cache` perche' con
un `src/index.js` in cache e un `index.html` no si vede la versione vecchia dopo
un deploy — la stessa classe di problema dell'immagine Docker vecchia. **Non c'e'
un Content-Security-Policy**, e qui sarebbe teatro: la pagina ha uno
`<script type="module">` **inline** (servirebbe `'unsafe-inline'`, o un hash che
cambia a ogni modifica) e deve poter chiamare un control-plane su **un'origine
arbitraria scelta dall'utente** (servirebbe `connect-src *`). Un CSP con quelle
due eccezioni non protegge da niente. Diventerebbe utile il giorno in cui lo
script inline uscisse dal file e il CP avesse un'origine fissa.

**Rebuild inutili.** Vercel ricostruisce a ogni push su `main`, anche quando qui
non e' cambiato nulla. Per saltare: *Settings → Git → Ignored Build Step* →
`git diff --quiet HEAD^ HEAD -- .`

**La Root Directory e' il punto delicato.** Lasciandola
alla radice del repository, Vercel cerca un `Dockerfile` alla radice e non lo
trova (i Dockerfile stanno dentro le cartelle dei singoli servizi). Puntandola su
`sandbox` si ottiene l'errore:

```
Error: building at STEP "COPY sandbox/requirements.txt /app/requirements.txt":
copier: stat: "/sandbox/requirements.txt": no such file or directory
```

Quel `Dockerfile` e' scritto per avere la **radice del repo** come contesto di
build — il compose lo fa con `context: .`, e deve, perche' contiene `COPY . /seed`
(la sandbox semina l'intero progetto in `/seed`). Vercel invece usa come contesto
la cartella del Dockerfile, quindi `sandbox/requirements.txt` diventa
`sandbox/sandbox/requirements.txt`. Non e' correggibile dall'interno del
Dockerfile: nessun percorso puo' risalire sopra il contesto.

### Puntare la pagina al control-plane

L'URL si sceglie a runtime, in ordine di priorita' (`index.html`, righe 104-107):

1. `?cp=...` nella URL — es. `https://<deploy>.vercel.app/?cp=https://cp.example.com`
2. `localStorage` (`hyperspace.webNodeUrl`): quello scritto l'ultima volta
3. fallback `` `${location.protocol}//${location.hostname}:8085` ``

### Il vincolo che frega dopo: HTTPS contro HTTP

Il fallback (3) su una pagina servita in **HTTPS** non funziona **mai**: punterebbe
a `https://<deploy>.vercel.app:8085`, e comunque il browser blocca una richiesta
`http://` da una pagina `https://` come **mixed content**. Quindi su un deploy
Vercel il control-plane deve essere raggiungibile in **HTTPS** (Tailscale Funnel,
un tunnel Cloudflare, o un reverse proxy con certificato). In locale, dove servi
la pagina in HTTP, il problema non esiste.

L'estensione MV3 in `apps/extension/` non passa da qui: si carica *unpacked* nel
browser, e Vercel non la deploya.

### Perche' il resto non sta su Vercel

Il repository e' un compose con **24 servizi** (control-plane, registry, node,
memory-graph, onboarding, searxng, bridge, obsidian, quattro varianti di Ollama,
open-webui, code-sandbox, omniroute, federation-gateway), 6 volumi, e una mesh
Tailscale/WireGuard. Il control-plane tiene lo stato in **SQLite su file**
(`shared/db.py`, `DB_PATH=./data/hyperspace.db`) e ha loop che vivono a lungo
(heartbeat, dream worker, monitor QoS); il nodo ha bisogno di **Ollama e di una
GPU**. Vercel esegue asset statici e funzioni di breve durata, con filesystem
effimero e senza processi persistenti: non e' un Dockerfile da sistemare, e' il
carico che non e' quello. Per avere lo stack intero online serve un host che
esegua `docker compose up -d` (una VPS, Fly, Railway, una VM Oracle Free).
