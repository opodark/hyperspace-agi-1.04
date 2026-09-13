# Sogni automatici (prima implementazione)

Ogni worker Ollama puo riflettere sulle proprie memorie quando non serve
richieste. Non e un sistema cosciente: produce ipotesi e domande, non fatti.

## Attivazione per nodo

Nel .env del worker (non abilita automaticamente gli altri PC):

```dotenv
DREAM_ENABLED=true
DREAM_MODEL=qwen2.5-coder:14b-instruct
DREAM_IDLE_SECONDS=120
DREAM_INTERVAL_SECONDS=900
```

Usare un modello gia installato su quel nodo. Il default e disabilitato.
Il primo controllo avviene dopo almeno 120 secondi senza lavoro; i controlli
avvengono ogni 10 secondi. Ogni tentativo e separato da almeno 900 secondi.
Per altri backend il worker sogni resta disabilitato.

Il worker usa al massimo 12 memorie proprie fra le ultime 100, esclude
memorie ricevute da peer, titoli e sogni, e limita l'input a 10.000 caratteri.
Salva un'impronta persistente per non riflettere nuovamente sugli stessi dati.
Acquisisce uno slot solo se non esistono richieste attive/in coda e il nodo
non e degradato. La generazione dura al massimo 60 secondi / 192 token.
Una nuova richiesta al worker interrompe la generazione in background e
libera lo slot. L'arresto effettivo della computazione dopo disconnessione
HTTP dipende dal backend. Lavoro avviato direttamente su Ollama, bypassando
il worker HyperSpace, non e osservato dal limiter.

## Risultati e diagnostica

- `GET /dreams/status` sul worker: configurazione, stato, ultimo ciclo/errori.
- Dashboard Diagnostics: scegliere il nodo in Simulate Dream, poi
  **Stato sogni automatici del nodo**.
- `data/dreams.jsonl`: ultime 100 riflessioni, con stato `hypothesis`, modello,
  timestamp, impronta e numero delle memorie sorgenti.
- `data/dream_state.json`: deduplicazione e cooldown, conservati al riavvio.
- Evento `dream` nel control-plane e nella vista Live; nessuna scrittura nella
  memoria originale e nessuna esecuzione di tool o azioni esterne.

Una consegna log fallita resta indicata nello stato, con risultato salvato
localmente; per ora non esiste una coda persistente di riconsegna.
Simulate Chat e Simulate Dream restano iniezioni di eventi diagnostici,
chiaramente etichettate: non sono invocazioni reali del modello.

## Hermes

Il control-plane espone `/v1` per inferenza e `/mcp` per i tool (initialize,
tools/list, tools/call). E il punto di integrazione per runtime esterni.
Il repository non contiene ancora un adapter dedicato o un test end-to-end
Hermes: la disponibilita MCP non dimostra che Hermes sia collegato.

## Verifica

```bash
python -m unittest discover -s tests -v
node tests/dashboard.test.cjs
```
