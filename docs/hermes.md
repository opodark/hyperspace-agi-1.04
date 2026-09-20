# Hermes Agent come memoria unica di HyperSpace

[Hermes Agent](https://github.com/nousresearch/hermes-agent) sostituisce il
backend di memoria di HyperSpace. Non deve esistere un secondo archivio
long-term proprietario di HyperSpace sincronizzato in parallelo: Hermes e' la
source of truth per memoria personale, sessionale, semantica e procedurale.

## Responsabilita'

Hermes possiede e persiste:

- `MEMORY.md` e `USER.md`;
- sessioni e ricerca FTS5 in `state.db`;
- skill apprese e memoria procedurale;
- eventuali provider di memoria esterni configurati in Hermes.

HyperSpace conserva soltanto:

- API e adapter compatibili per nodi e applicazioni esistenti;
- policy, identita', autorizzazioni e audit;
- journal temporanei di ipotesi Dream ancora non approvate;
- viste, grafi e indici derivati dalla memoria Hermes;
- log operativi con retention limitata, che non sono memoria cognitiva.

`omega_query`, `omega_store`, memory graph e retrieval dei nodi diventano
adapter o proiezioni della memoria Hermes. Non effettuano dual-write su un
archivio HyperSpace separato.

## Gate dei sogni

Le ipotesi Dream restano fuori dalla memoria finche' non vengono revisionate.
Il journal di review e' stato transitorio, non memoria autorevole. Solo una
promozione esplicita scrive la conoscenza approvata in Hermes, con provenance,
autore della review e riferimenti alle fonti. Rifiuto e revoca devono essere
tracciabili senza lasciare il contenuto attivo nella memoria Hermes.

## Topologia prevista

```text
utente / gateway
       |
Hermes Agent ---- MEMORY.md, USER.md, state.db, skills
       |                         ^
       |                         |
       +---- modello ---- HyperSpace /v1
       |                         |
       `---- tool ------- HyperSpace /mcp
                                 |
                  policy, mesh, Dream review, adapter legacy
                                 |
                       viste derivate della memoria Hermes
```

Hermes supporta endpoint OpenAI-compatible personalizzati e server MCP HTTP
con header configurabili. Il control-plane espone `/v1` e `/mcp`: MCP richiede
ora autenticazione, identita' del chiamante e allowlist (vedi sotto), mentre
`/v1` resta senza token perche' lo consuma anche Open WebUI — va quindi esposto
solo su loopback o attraverso il tunnel Tailscale, mai direttamente in LAN.

## Accesso MCP

`/mcp` espone i tool a runtime esterni, quindi non ha un default aperto: senza
token risponde 503 e non serve nessuno.

```dotenv
# Un cliente per runtime: il nome finisce nei log di audit.
MCP_CLIENTS="hermes=<token di almeno 32 caratteri>;ops=<altro token>"
# Allowlist per cliente: "*" = tutto il catalogo pubblicato.
MCP_CLIENT_TOOLS="hermes=omega_query,omega_store,get_mesh_status;ops=*"
# true solo per sviluppo in ascolto su 127.0.0.1: e' una scorciatoia opt-in.
MCP_ALLOW_LOOPBACK=false
# false spegne MCP senza rimuovere i segreti.
MCP_ENABLED=true
```

Il token si presenta con l'header standard `Authorization: Bearer <token>`
(quello che i client MCP sanno configurare da soli) oppure con
`X-Hyperspace-Mcp-Token`. Tre proprieta' valgono la pena di essere esplicite:

1. un cliente senza voce in `MCP_CLIENT_TOOLS` non riceve NESSUN tool
   (fail-closed): meglio un cliente inerte e visibile che uno con tutti i tool
   per una svista di configurazione;
2. l'allowlist filtra anche `tools/list`, non solo `tools/call`: il client vede
   esattamente cio' che puo' usare;
3. un tool non permesso e uno inesistente danno la stessa risposta a un cliente
   con allowlist esplicita, cosi' non puo' enumerare il catalogo.

`GET /mcp/status` mostra client, tool effettivi e problemi di configurazione, e
non contiene mai i token.

Per un profilo Hermes inizialmente read-only l'allowlist minima e' quella
dell'esempio: niente `code_sandbox`, niente connettori con credenziali.

## Stato corrente

- Gli endpoint HyperSpace `/v1` e `/mcp` sono raggiungibili sul nodo Windows.
  `/mcp` richiede ora un token per cliente (vedi Accesso MCP): senza token
  configurato risponde 503 invece di servire chiunque raggiunga la porta.
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
- Infra-UI espone un Memory Explorer con ricerca Hermes server-side, filtri per
  stato, nodo, modello e intervallo temporale, oltre a selezione multipla.

## Lifecycle e pulizia

Il control-plane pubblica `POST /memory/search` e `POST /memory/lifecycle`.
Le azioni `quarantine`, `restore` e `revoke` sono revisioni append-only: non
modificano direttamente SQLite e non coinvolgono `MEMORY.md` o il profilo
utente. Il purge crea una tombstone `revoked`; la dashboard richiede sempre
una conferma operatore.

La dashboard nasconde di default i nodi `unreachable`. La pulizia usa
`DELETE /mesh/nodes/<node_id>` e rifiuta nodi attivi o locali; rimuove solo la
registrazione storica del control-plane, senza arrestare processi remoti.

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


Il launcher usa `127.0.0.1`: su Docker Desktop `host.docker.internal` riesce a
raggiungerlo, ma la LAN no. Non allargare il bind senza una regola firewall
precisa. Verifica: `GET /health` con header `Authorization: Bearer <token>`.

Una sola fonte di verita' (aggiornato 2026-09-20): il token vive in
`%HS_DATA_DIR%/hermes-memory.token` (default `data/runtime/data/hermes-memory.token`),
letto sia dal control-plane (mount `/app/data`) sia dal bridge (via `--token-file`).
Non generare un secondo file separato: disallinea i token e causa
`401 Unauthorized` sul bridge.


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

## Piano di sostituzione

1. Fare snapshot verificato di tutti gli archivi di memoria HyperSpace legacy.
2. Installare Hermes in una directory persistente separata e provarne backup e
   ripristino prima di importare dati reali.
3. Collegare Hermes a HyperSpace `/v1` e a un profilo MCP inizialmente read-only.
4. Definire un adapter unico `HermesMemoryBackend` e instradare tutte le letture
   HyperSpace verso di esso.
5. Importare la memoria legacy con ID, timestamp, fonte e hash per rendere la
   migrazione idempotente e verificabile.
6. Confrontare per un periodo le risposte legacy e Hermes senza doppia scrittura.
7. Spostare le scritture normali e le promozioni Dream su Hermes.
8. Rendere gli archivi legacy read-only, verificare recovery e poi rimuovere il
   vecchio backend soltanto con approvazione esplicita.

## Criteri di accettazione

1. Ogni API memoria HyperSpace legge e scrive attraverso Hermes.
2. Nessun percorso runtime continua a scrivere memoria cognitiva nel backend
   legacy dopo il cutover.
3. Sessioni, `MEMORY.md`, `USER.md` e skill sopravvivono a riavvio e recovery.
4. La ricerca Hermes ritrova le memorie importate e conserva provenance e date.
5. Dream non revisionati non compaiono nella memoria attiva.
6. Memory graph e nodi mostrano viste coerenti derivate da Hermes.
7. Backup, restore, rollback e migrazione idempotente hanno test automatici.
