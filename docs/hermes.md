# Integrazione Hermes

Hermes è il backend di memoria cognitiva di HyperSpace ed è anche il runtime
esterno candidato a usare il control-plane come endpoint di inferenza e provider
di tool. L'integrazione memoria è operativa; quella agent/MCP resta un traguardo
separato.

## Stato corrente

- Gli endpoint HyperSpace `/v1` e `/mcp` sono raggiungibili sul nodo Windows.
- Hermes Agent 0.21.3 e' installato nativamente in
  `%LOCALAPPDATA%\hermes`; provider e credenziali modello restano da configurare.
- Il bridge autenticato `scripts/hermes_memory_bridge.py` usa direttamente le
  classi Hermes `SessionDB` e `MemoryStore`, senza modificare SQLite o Markdown
  a mano e senza montarli nei container.
- Il control-plane usa Hermes di default per `/memory`, `/memory/push`,
  `/memory/stats`, `omega_query` e `omega_store`. Il backend legacy e'
  selezionabile solo esplicitamente con `MEMORY_BACKEND=legacy` per rollback.
- I nodi leggono la vista memoria dal control-plane e inviano task, chat e
  insight approvati a Hermes; non scrivono piu' una copia cognitiva locale.
  `interactions.jsonl` resta soltanto telemetria per le metriche di serving.
- Lo smoke test reale bridge -> `state.db` -> FTS5 -> query e' passato su una
  home temporanea. I test unitari coprono autenticazione, round-trip,
  deduplica, query e import parziale.
- Il backup del 16 settembre 2026 contiene 19 record control-plane e 197 righe
  node. L'import reale ha prodotto 80 record Hermes unici (136 duplicati nel
  JSONL del nodo); il secondo passaggio ha prodotto zero nuove scritture.
- Memory graph continua a derivare la propria vista da `/memory`, ora servito
  da Hermes. La configurazione modello/MCP di Hermes non e' ancora completata;
  quindi il sistema non soddisfa ancora tutti i criteri di accettazione qui
  sotto.
- Infra-UI espone un Memory Explorer che usa la ricerca Hermes server-side e
  filtra per stato, nodo, modello e intervallo temporale. Le azioni operative
  sono revisioni append-only: `quarantine`, `restore` e `revoke` (purge
  logico). Non modificano direttamente SQLite e non coinvolgono le memorie
  curate `MEMORY.md` o il profilo utente.

## Lifecycle e pulizia

Il control-plane pubblica `POST /memory/search` e `POST /memory/lifecycle`.
Il lifecycle accetta una lista di ID, un'azione e una motivazione. La
quarantena nasconde le entry dalle viste attive ma le mantiene revisionabili;
il ripristino crea una nuova revisione attiva; il purge crea una tombstone
`revoked`. La dashboard richiede sempre una conferma operatore.

La stessa dashboard nasconde di default i nodi `unreachable`. La pulizia nodi
usa `DELETE /mesh/nodes/<node_id>` e rifiuta nodi attivi o locali; rimuove
soltanto la registrazione storica del control-plane, non arresta processi né
container remoti.

## Avvio del bridge

Generare una volta un segreto in un file ignorato da Git. Nel Compose generico
il control-plane lo legge come `/repo/data/hermes-memory.token`; nel profilo
Windows lo legge da `/app/data/hermes-memory.token`, cioe' dal path host
`%HS_DATA_DIR%\hermes-memory.token`. Il launcher deve ricevere lo stesso file.
Da PowerShell nella root del repository:

```powershell
[IO.File]::WriteAllText(
  (Join-Path $PWD 'data\hermes-memory.token'),
  ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
)
.\scripts\start_hermes_memory_bridge.ps1
```

Con i path predefiniti del profilo Windows usare invece
`.\scripts\start_hermes_memory_bridge.ps1 -TokenFile 'C:\HyperSpace\data\hermes-memory.token'`.

Il launcher usa `127.0.0.1`: su Docker Desktop `host.docker.internal` riesce a
raggiungerlo, ma la LAN no. Non allargare il bind senza una regola firewall
precisa. Verifica: `GET /health` con header `Authorization: Bearer <token>`.

## Migrazione legacy

Prima copiare `memory.json.gz` in un backup immutabile e verificarne l'hash.
Il dry-run non scrive nulla:

```powershell
python .\scripts\migrate_memory_to_hermes.py .\backup\memory.json.gz --dry-run
```

Con bridge attivo, omettere `--dry-run`. L'import calcola un ID SHA-256 stabile
quando manca un ID originale: rieseguirlo produce duplicati riconosciuti,
non nuove memorie. Il file legacy va lasciato read-only fino alla verifica di
conteggio, campionamento query e ripristino Hermes.

## Integrazione agent e tool

Il control-plane espone:

- `/v1` per l'inferenza compatibile con API OpenAI;
- `/mcp` per `initialize`, `tools/list` e `tools/call`.

Questi endpoint definiscono il punto di ingresso, ma la configurazione Hermes
Agent e il relativo test end-to-end non sono ancora completati. Il bridge di
memoria operativo e la disponibilità MCP sono componenti distinte.

## Prossimo traguardo

L'integrazione si considera funzionante quando una configurazione Hermes
riproducibile completa questi passaggi:

1. connessione e inizializzazione MCP;
2. elenco dei tool pubblicati da HyperSpace;
3. chiamata di un tool in sola lettura;
4. richiesta di inferenza attraverso `/v1`;
5. gestione osservabile di timeout, nodo non raggiungibile ed errore del tool.

Prima del test vanno fissati autenticazione, indirizzo Tailscale del
control-plane e policy dei tool concessi a Hermes.
