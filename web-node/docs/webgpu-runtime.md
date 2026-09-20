# Runtime WebGPU (modelli locali)

I modelli locali del web node girano in due runtime separati, entrambi **opt-in**
e indipendenti dalla mesh. Vivono in `src/webgpu.js` e si attivano solo da
`index.html` (sezioni 6 e 7), dietro consenso esplicito.

| Runtime         | Libreria                    | Serve                                   | Pesi                                  |
|-----------------|-----------------------------|-----------------------------------------|---------------------------------------|
| Transformers.js | `@huggingface/transformers` | `embed_texts`, `translate`, `summarize` | decine di MB; centinaia per translate/summarize |
| WebLLM          | `@mlc-ai/web-llm`           | chat locale on-device (pannello a se')  | GB, anche quantizzati                 |

## Rilevamento WebGPU

`hasWebGpu()` e `requestWebGpuAdapter()` leggono `navigator.gpu.requestAdapter()`:
niente adattatore -> niente runtime. I dettagli dell'adattatore (vendor,
architettura, `isFallbackAdapter`) compaiono nella sezione 6 della pagina; non
entrano nel payload di registrazione, che resta sincrono.

## Transformers.js (task)

`createTransformersRuntime({ transformers, models, onStatus })` carica una
pipeline alla volta, in modo lazy: il modulo si importa subito, i pesi si
scaricano al primo uso (gli embeddings invece si pre-caricano su click da
`index.html`).

Interfaccia esposta al `task-runner`:

- `embed(texts) -> number[][]` — `feature-extraction`, `pooling: "mean"`, `normalize: true`
- `translate(text, { source, target }) -> string` — `translation` NLLB
- `summarize(text, { max_sentences }) -> string` — `summarization`

Backend: `device: "webgpu"`. Modelli di default (sostituibili via `models`):

| Task      | Modello default                   |
|-----------|-----------------------------------|
| embed     | `Xenova/all-MiniLM-L6-v2`         |
| translate | `Xenova/nllb-200-distilled-600M`  |
| summarize | `Xenova/distilbart-cnn-6-6`       |

## WebLLM (chat locale)

`createWebLlmChat({ webllm, model, onStatus })` espone
`generate(messages, { onDelta })` con streaming. Non tocca ne' il control-plane
ne' il nodo: e' una chat locale nel browser. I modelli nel `<select>` di
`index.html` sono ID reali della lista MLC.

## Regola di onesta'

Il nodo dichiara `embed_texts` solo con un runtime che sa servirlo, e `translate`
solo con l'API nativa del browser o con un runtime iniettato:
`detectCapabilities(globalThis, { runtime })`. Senza runtime la pagina non
dichiara capacita' che non sa servire. `moderate` resta euristico di proposito:
un classificatore fragile e' peggio di un'euristica dichiarata.

## Limiti da conoscere

- **Primo download**: gli embeddings (~90MB) si pre-caricano su click; translate
  e summarize (modelli grandi) si scaricano al primo task. Se il primo task scade
  (timeout default 30s) mentre scarica, al giro dopo il modello e' gia' in cache.
- **NLLB**: la traduzione usa i codici FLORES (`eng_Latn`, `ita_Latn`, ...), con
  una mappa piccola (it/en/fr/de/es/pt/nl/ru/zh/ja). Lingue fuori mappa -> errore
  chiaro, non output sbagliato.
- **CDN**: le librerie si importano da
  `https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1` e
  `https://esm.run/@mlc-ai/web-llm`. Sono costanti (`TRANSFORMERS_CDN`,
  `WEBLLM_CDN`) da pinnare o puntare a un mirror interno se serve.

## Test

```bash
cd web-node
npm test        # include webgpu.test.mjs (14 check) e webgpu-e2e.test.mjs (5 check)
npm run check   # compila tutti i sorgenti, webgpu.js incluso
```

I test girano senza browser: adapter, pipeline e motore WebLLM sono finti e
iniettati. Lo smoke test reale (download dei pesi, WebGPU vera) va fatto aprendo
`index.html` in un browser con WebGPU e cliccando "Carica modelli locali".
