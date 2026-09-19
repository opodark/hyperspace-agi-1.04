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

## Chat — usare la mesh da questa pagina

La pagina ha due funzioni separate, e conviene tenerle tali:

| | Cosa fa | Dove sta la logica |
|---|---|---|
| **Nodo** | mette a disposizione della mesh i 5 tipi di task qui sopra | `src/index.js`, `src/task-runner.js` |
| **Chat** | usa la mesh: manda la richiesta al control-plane, che la instrada al nodo che ha quel modello | `src/chat.js` |

Il pannello 5 carica i modelli da `/v1/models`, poi invia a
`/v1/chat/completions` in streaming e disegna i pezzi man mano. L'id del modello
si manda **come lo restituisce il CP** (arriva decorato, `"🕸️ qwen3:8b"`): il CP
lo normalizza da solo — verificato — e ripulirlo nel client sarebbe una
supposizione in piu' sul server. Nel setup attuale `/v1` non chiede token
(verificato senza header: e' l'endpoint aperto per compatibilita' con Open WebUI).

**Perche' il parsing SSE e' un modulo e non codice nella pagina**: e' la parte che
si rompe, e uno script inline non si testa. `src/chat.js` copre i casi che si
vedono **solo in produzione**, tutti verificati sui **byte reali** del
control-plane invece che su esempi immaginati (`tests/chat.test.mjs`):

- un evento **spezzato a meta'** fra due chunk di rete — il caso normale su
  connessioni lente, e quello che fa comparire testo doppio o mutilato;
- gli **errori a meta' stream**, che arrivano come evento SSE con chiave `error`
  e NON come errore HTTP: un client che non li guarda resta appeso per sempre;
- il **ramo nativo**, che manda l'intera risposta in **un solo delta** con
  `finish_reason: "stop"` invece che token per token;
- `[DONE]`, eventi non JSON (keepalive), delta con il solo `role`, e CRLF.

Verificato anche contro un control-plane vivo: `listModels()` legge i 21 modelli,
e un invio reale restituisce il testo atteso. Test: `npm test` (24 + 19 check).

**Il limite da conoscere**: da una pagina in **HTTPS** il browser blocca una
richiesta verso un control-plane in `http://` (mixed content). La chat quindi
funziona aprendo la pagina in HTTP, oppure con un CP raggiungibile in HTTPS — e
per quest'ultimo caso vale l'avvertenza della sezione di deploy.

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

## Come si usa: servita dal tailnet

Il modo previsto per usare questa pagina, e l'unico che funziona senza
compromessi, e' servirla **da una macchina della mesh** e aprirla con Tailscale
connesso sul dispositivo.

```bash
cd web-node && npm run serve      # http.server su 0.0.0.0:8790
tailscale ip -4                   # l'indirizzo su cui raggiungerla
```

Poi, dal telefono o da un altro computer **con Tailscale attivo**:

```
http://<ip-tailscale>:8790
```

**Non serve configurare l'URL del control-plane.** Il fallback nel codice e'
`` `${location.protocol}//${location.hostname}:8085` ``: una pagina servita da
`<ip>:8790` cerca il CP su `<ip>:8085` e lo trova, perche' il control-plane
pubblica la 8085 su `0.0.0.0`. E' letteralmente il caso per cui quel fallback
esiste: pagina e control-plane sulla stessa macchina, tutto in HTTP.

Il campo `?cp=` resta utile quando il CP **non** e' sullo stesso host della
pagina: e' l'unico modo per puntarlo altrove.

Superficie esposta: **nessuna**. Nessun certificato, nessun tunnel, nessuna porta
in piu' di quelle che il tailnet gia' espone. `npm run serve` non e' un server di
produzione, ma per uso personale puo' restare su.

### Perche' non su un host pubblico

Una pagina servita in **HTTPS** non puo' chiamare un control-plane in `http://`:
il browser blocca la richiesta come *mixed content* prima che il codice parta.
Non e' un bug del client e non si aggira dal lato pagina. Un deploy su Vercel e'
stato provato e poi rimosso esattamente per questo: la pagina funzionava, ma da
li' non poteva raggiungere il CP.

E anche risolto il problema del protocollo resterebbe quello dell'esposizione:
`/v1/models` e `/v1/chat/completions` **non hanno autenticazione ne' rate limit**,
quindi un control-plane pubblicato e' una GPU che chiunque puo' far lavorare. Se
un giorno servisse davvero un accesso dall'esterno, la strada e' un proxy con
whitelist di **due rotte** piu' un token per utente che la pagina chiede e
conserva in `localStorage` — *non* l'esposizione della porta del CP, che
pubblica anche `/logs` e `/federation/peers` senza autenticazione.

