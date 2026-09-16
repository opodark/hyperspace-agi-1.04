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
con header configurabili. Il control-plane espone gia' `/v1` e `/mcp`, ma MCP
deve ricevere autenticazione, identita' del chiamante e allowlist prima di
essere usato fuori dal loopback.

## Stato corrente

- Gli endpoint HyperSpace `/v1` e `/mcp` sono raggiungibili sul nodo Windows.
- Hermes Agent non e' ancora installato o configurato sul nodo.
- La memoria legacy HyperSpace e' ancora attiva: il cutover non e' iniziato.
- Non esistono ancora adapter Hermes per `omega_query`, `omega_store`, Dream e
  memory graph.
- Non e' stato eseguito un test end-to-end con il client Hermes reale.

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
