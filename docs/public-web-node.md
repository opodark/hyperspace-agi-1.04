# Web node pubblico su zerozerocomputer.it

`zerozerocomputer.it` ospita soltanto file statici. Il browser del visitatore
diventa il nodo; Aruba non esegue inferenza e non deve conoscere segreti della
mesh.

## Topologia

```text
os.zerozerocomputer.it (HTTPS, Render)
        |
        | POST register / long poll / result
        v
laptop-t9h8plht.tail453db3.ts.net (HTTPS, Tailscale Funnel)
        |
        v
federation-gateway:8095 -> control-plane:8085
```

Non pubblicare la porta `8085`. Il federation gateway inoltra soltanto
`/web/register`, `/web/poll`, `/web/result`, `/v1/models` e
`/v1/chat/completions`, applica un rate limit per IP e continua a rispondere
404 a `/web/tasks`, `/web/status`, dashboard, configurazione e log.

## Stato operativo e prestazioni

La pagina pubblica usa `think=false`, massimo 256 token e un contesto di 4096
token. Il gateway apre subito lo stream SSE con un keepalive, attende il
control-plane fino a 600 secondi e registra sia il tempo al primo byte upstream
sia la durata totale. Per rendere la latenza prevedibile, la superficie pubblica
invia `X-Hyperspace-Tools: off`: il control-plane deve rispettarlo anche nel
percorso non-stream e non deve reiniettare i tool in `_run_tool_loop`.

Misura del 22 settembre 2026 con `qwen3.5:4b`: circa 50 secondi. Ollama 0.34.2
mostrava il modello da 3,1 GB con contesto 4096 ma `100% CPU`, `0 VRAM`, mentre
la RTX 5060 Laptop da 8 GB era inattiva. Il prossimo intervento prestazionale è
quindi ripristinare l'offload CUDA di Ollama; aumentare ancora il timeout non
rende più veloce l'inferenza.

Diagnostica rapida:

```powershell
ollama ps
nvidia-smi
docker logs --since 10m hyperspace-federation-gateway
curl.exe http://127.0.0.1:8085/metrics/summary
```

## File da caricare su Aruba

Per generare un archivio pronto con l'endpoint già impostato:

```powershell
.\scripts\build-web-node-aruba.ps1 `
  -GatewayUrl https://mesh.zerozerocomputer.it
```

Il risultato è `dist/web-node-aruba.zip`. Estrarne il contenuto nella root web
del dominio tramite File Manager o FTP.

In alternativa, caricare manualmente mantenendo questa struttura:

```text
join.html
join-config.js
src/
  capabilities.js
  index.js
  protocol.js
  task-runner.js
  transport.js
  webgpu.js
```

Se la pagina deve essere la home del dominio, rinominare `join.html` in
`index.html`. Impostare in `join-config.js` l'URL pubblico HTTPS del gateway:

```js
window.HYPERSPACE_JOIN = Object.freeze({
  gatewayUrl: "https://mesh.zerozerocomputer.it",
  siteName: "ZeroZeroComputer",
  meshName: "HyperSpace",
});
```

L'hosting Aruba fornisce HTTPS sui normali piani hosting. La pagina HTTPS deve
raggiungere anche il gateway in HTTPS: un URL `http://` verrebbe bloccato dal
browser come mixed content.

## Pubblicare il gateway

Esporre `federation-gateway:8095` tramite un tunnel HTTPS o un reverse proxy sul
sottodominio scelto. Il DNS del sottodominio deve puntare al servizio usato per
il tunnel/reverse proxy, non direttamente al control-plane.

Prima di condividere la pagina verificare:

```bash
curl https://mesh.zerozerocomputer.it/health
curl -i -X POST https://mesh.zerozerocomputer.it/web/register \
  -H "Content-Type: application/json" \
  -d '{}'
```

La prima chiamata deve restituire il servizio gateway; la seconda deve
raggiungere il validatore del control-plane e rifiutare il payload incompleto.
Un `404` su `/web/tasks` conferma che l'inserimento pubblico di task è escluso.

## Dati e consenso

Il browser conserva in `localStorage` soltanto un identificativo casuale e
l'etichetta scelta. La pagina non contiene token. Il nodo parte solo dopo il
consenso esplicito e scompare chiudendo la scheda o premendo **Esci dalla mesh**.
