# ONLYOFFICE ↔ HyperSpace Bridge

Design del connettore documentale per la track "enterprise network only" (vedi [[project_team_split_1.04_demo]] / [ROADMAP.md](../ROADMAP.md)). Sostituisce, per la demo 1.04, l'approccio via Microsoft Graph (`control-plane/connectors/office365.py`, ancora stub e non implementato): niente App Registration Azure AD, niente dipendenza cloud — ONLYOFFICE self-hosted + Ollama locale restano completamente on-premise.

## Perché ONLYOFFICE

ONLYOFFICE espone un plugin AI (editor Documenti/Fogli/Presentazioni/PDF) che supporta provider compatibili OpenAI e provider locali via Ollama (`http://localhost:11434`). Questo lo rende un frontend documentale pronto all'uso, da collegare a HyperSpace come motore di orchestrazione invece di reimplementare un office layer da zero.

## Architettura

```
ONLYOFFICE (editor)
      │  evento: selezione / prompt / salvataggio
      ▼
  Adapter ONLYOFFICE      — legge contesto, selezione, task dal documento
      │
      ▼
  Router HyperSpace       — sceglie l'agente giusto (summarizer, coder, planner...)
      │
      ▼
  Policy layer            — approvazioni e limiti di scrittura
      │
      ▼
  Writer                  — reinserisce l'output nel documento (testo, commento, sidebar)
      ▼
ONLYOFFICE (editor)
```

Bridge event-driven: evento ONLYOFFICE → normalizzazione → task HyperSpace → risposta nel documento.

## Event map

Eventi iniziali (pochi, ad alto impatto):

| Evento | Caso d'uso |
|---|---|
| `document.summary_requested` | Riassunto |
| `document.action_items_requested` | Estrazione compiti/task da verbali o report |
| `document.rewrite_requested` | Riscrittura testo |
| `document.selection_analyzed` | Analisi della selezione corrente |
| `sheet.table_extract_requested` | Estrazione tabelle da fogli di calcolo |
| `slides.outline_requested` | Outline da presentazioni |
| `pdf.extract_requested` | Estrazione contenuto da PDF |

## Roadmap tecnica

- **Fase A — POC**: collegare ONLYOFFICE Desktop a Ollama locale (AI plugin → provider locale, URL `http://localhost:11434`), verificare il round-trip base selezione → task → output. Nessun codice HyperSpace richiesto.
- **Fase B — Assistenti custom**: usare gli assistenti AI personalizzati di ONLYOFFICE (prompt custom, accesso da toolbar) per task ripetibili: summary, rewrite, action extraction, draft. Ancora nessun bridge service.
- **Fase C — Bridge HyperSpace**: servizio bridge separato che traduce i task office in job HyperSpace — qui entra l'orchestrazione vera (routing modello, memoria, audit trail, permessi). Vedi API sotto.
- **Fase D — Team use**: multi-workspace, permessi, review umana, documenti condivisi (DocSpace supporta agenti AI con provider/istruzioni/strumenti MCP dedicati).

## Fase A — Checklist di setup e verifica

### A1. Ollama pronto sul host
```powershell
ollama list                                # verifica che i modelli ci siano
ollama pull qwen3-14b-uncensored           # HS_MODEL_GENERAL, se manca
ollama pull qwen2.5-coder-14b-abliterated  # HS_MODEL_CODER, se manca
curl http://localhost:11434/api/tags       # deve rispondere con la lista modelli
```

### A2. Tuning host — non arriva da `.env.windows`
Le variabili `OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_MAX_QUEUE` in `.env.windows` valgono solo per i container Docker — l'Ollama nativo su Windows le legge dalle variabili d'ambiente **di sistema**, non dal file del repo. Per farle valere anche per l'istanza nativa usata da ONLYOFFICE:
```powershell
setx OLLAMA_HOST "0.0.0.0:11434"
setx OLLAMA_KEEP_ALIVE "12h"
setx OLLAMA_NUM_PARALLEL "2"
setx OLLAMA_MAX_LOADED_MODELS "2"
setx OLLAMA_MAX_QUEUE "64"
```
Riavviare il servizio/app Ollama dopo le `setx` — non si applicano al processo già in esecuzione.

### A3. ONLYOFFICE Desktop Editors
- Installare la versione Desktop (free) — non Document Server, non serve Docker per questa fase.
- Aprire un documento di test.
- Impostazioni del plugin AI (Plugins → AI, o Settings → AI Assistant a seconda della versione).
- Aggiungere provider: se è disponibile "Ollama" come opzione nativa → URL `http://localhost:11434`. Se il plugin espone solo "custom OpenAI-compatible" → URL `http://localhost:11434/v1`, campo API key con un placeholder qualsiasi (Ollama non lo verifica).
- Selezionare modello: `qwen3-14b-uncensored`.

### A4. Round-trip test
- Selezionare un paragrafo di testo nel documento.
- Lanciare l'azione AI (summarize o prompt custom).
- Verificare: risposta coerente, tempo di risposta, nessun errore di connessione.
- Ripetere una volta con `qwen2.5-coder-14b-abliterated` su un pezzo di codice/testo tecnico, per un secondo data point.

### A5. Cosa annotare (decide la Fase B/C)
- Latenza percepita per risposta corta vs lunga.
- Se il plugin regge il context window di 8192 token o va in timeout.
- Se serve `OLLAMA_REQUEST_TIMEOUT`/`OLLAMA_LOAD_TIMEOUT` più alti di quelli in `.env.windows` (300s / 15m).

## API del bridge (Fase C — MVP)

MVP a 4 funzioni, corrispondenti ai 4 endpoint principali. Copre `document.summary_requested`, `document.rewrite_requested`, `document.action_items_requested` e una classificazione generica; `sheet.table_extract_requested`, `slides.outline_requested` e `pdf.extract_requested` restano fuori dall'MVP e vengono aggiunti in una fase successiva una volta stabilizzato l'envelope.

### Envelope richiesta comune

```json
{
  "event": "document.summary_requested",
  "document": {
    "id": "string",
    "type": "text | spreadsheet | presentation | pdf",
    "title": "string"
  },
  "selection": {
    "text": "string",
    "range": "string | null"
  },
  "context": {
    "user": "string",
    "workspace": "string | null"
  },
  "options": {}
}
```

### Envelope risposta comune

```json
{
  "status": "ok | error",
  "agent": "summarizer | rewriter | planner | classifier",
  "output": {
    "text": "string",
    "format": "plain | markdown | comment"
  },
  "error": "string | null"
}
```

### `POST /office/summarize`
- Evento sorgente: `document.summary_requested`
- `options`: `{ "length": "short | medium | long" }`
- Agente: summarizer

### `POST /office/rewrite`
- Evento sorgente: `document.rewrite_requested`
- `options`: `{ "style": "formal | plain | concise", "target_language": "string | null" }`
- Agente: rewriter

### `POST /office/extract-actions`
- Evento sorgente: `document.action_items_requested`
- `options`: `{ "assignees_hint": "boolean" }`
- Agente: planner
- `output.text`: lista di action item in markdown (`- [ ] ...`)

### `POST /office/classify`
- Evento sorgente: `document.selection_analyzed`
- `options`: `{ "taxonomy": ["string", ...] }`
- Agente: classifier
- `output.text`: etichetta/e scelte dalla taxonomy fornita

### `POST /office/answer-with-citations`
- Non MVP, previsto per Fase C avanzata — risposta con citazioni dal documento/knowledge base. Richiede accesso alla knowledge base DocSpace (Fase D).

## Relazione con il connector Microsoft Graph esistente

`control-plane/connectors/office365.py` (stub, non implementato, richiede App Registration Azure AD) resta de-prioritizzato per la demo 1.04 a favore di questo bridge, che non richiede credenziali cloud. Non viene rimosso: può tornare utile in futuro se serve integrazione con tenant Microsoft 365 reali invece che documenti locali/self-hosted.
