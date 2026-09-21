# Architettura unificata degli strumenti di sviluppo

## Decisione

Tool di sviluppo, debug, test, security review e pentest fanno parte di un
unico prodotto: **HyperSpace**. Utenti e coding agent usano lo stesso catalogo,
la stessa UI, lo stesso contratto dei job e gli stessi report. L'esecuzione non
avviene però in un unico container: ogni classe di capacità gira nel runtime
più adatto al suo accesso a filesystem, rete e browser.

```text
HyperSpace
├── Skill Forge
├── Dev Sandbox
├── Test e benchmark
├── Debug e profiling
├── Security review
├── Browser testing
└── Pentest di laboratorio
       │
       ├── runner offline
       ├── dependency-audit worker
       ├── browser worker
       └── security-lab worker
```

Questa struttura permette a un coding agent di richiedere un solo workflow:

```text
modifica → lint → test → analisi statica → fixture applicativa
         → test browser/security → diff → proposta Forge
```

Il control-plane orchestra i passaggi e sceglie il worker. Skill e modelli
descrivono il metodo o propongono un job; non cambiano da soli rete, target o
privilegi del runtime.

## Runtime previsti

| Runtime | Capacità | Accesso previsto | Stato |
|---|---|---|---|
| Dev Sandbox | Ruff, Bandit, pytest, unittest, cProfile, modifica e diff | copia usa-e-getta del repository, rete disabilitata | implementato |
| Forge | skill ECC, tool, patch e lifecycle di review | artefatti inerti, nessuna installazione automatica | implementato |
| Benchmark runner | sonde ripetibili sui modelli e confronto con/senza skill | fixture locali e verificatori nella sandbox | prossimo |
| Dependency audit | pip-audit, inventario e futura SBOM | accesso controllato alle fonti advisory | pianificato |
| Browser worker | Playwright, screenshot e Trace Viewer | browser preinstallato, URL del job | pianificato |
| Security Lab | Nmap, ZAP e diagnostica di rete | rete privata/laboratorio, target dichiarati | pianificato |

Bandit e gli altri controlli statici restano nella Dev Sandbox. Nmap e ZAP
sono utili anche durante lo sviluppo, ma appartengono al Security Lab perché
generano traffico verso altri processi o host. Playwright usa un browser worker
separato per non appesantire l'immagine offline di base.

## Contratto comune

Tutti i worker convergono sullo stesso vocabolario:

- `tool_id` e versione effettivamente eseguita;
- `workspace_id` per il codice oppure `scope_id` per browser e rete;
- input strutturati, timeout e limite output;
- `traceId` per collegare job, finding, log e proposta;
- `completed` distinto da `passed`;
- finding con regola, messaggio, severità e posizione o target;
- artefatti come diff, JSON, HTML, screenshot e trace;
- provenienza e data dei database advisory quando applicabile.

Una UI comune può quindi mostrare risultati diversi senza perdere la semantica:
un processo terminato con finding è `completed: true` e può essere
`passed: false`; un timeout o un report illeggibile non diventa un successo.

## Threat model attuale

Durante questa fase HyperSpace è un ambiente privato, single-tenant, usato da
due operatori fidati. L'hardening completo di `/tools/execute` e del percorso
chat non blocca i prossimi esperimenti, ma resta debito tecnico esplicito.

Restano invarianti alcune regole di sviluppo:

- il codice generato gira nella sandbox offline;
- il checkout operativo non riceve write-back automatico;
- diff e artefatti passano da review;
- i worker con rete restano nel laboratorio privato e accettano target
  dichiarati nel job;
- nessun nuovo ingresso pubblico viene aperto per aggiungere un tool.

Prima di un uso pubblico, multiutente o autonomo vanno completati identità,
allowlist e audit end-to-end su `/tools/execute`, chat e MCP, inclusi scope DNS,
redirect e target dei worker di rete.

## Organizzazione del repository

Per ora il monorepo è il confine corretto:

```text
sandbox/                  # sviluppo e controlli offline
workers/browser/          # Playwright e artefatti browser (previsto)
workers/security-lab/     # Nmap e ZAP (previsto)
shared/tool_contract.py   # contratto job comune (previsto)
shared/report_schema.py   # finding e artefatti normalizzati (previsto)
vendor/ecc/               # skill importate con provenienza
```

Un worker passa in un repository separato soltanto quando ha un ciclo di
rilascio, dipendenze, distribuzione o manutentori realmente indipendenti. La
separazione di processo e privilegi non richiede oggi una separazione Git.

## Ordine di lavoro

1. Spostare il verdetto di `scripts/model_bench.py` nella sandbox e confrontare
   modelli con e senza skill ECC su fixture verificabili.
2. Persistenza e visualizzazione dei report, con redazione dei dati sensibili
   prima di consegnarli al modello.
3. Dependency audit con pip-audit e metadati sulla copertura advisory.
4. Browser worker con Playwright, screenshot e Trace Viewer.
5. Security Lab privato con Nmap e ZAP Baseline, target dichiarati e report
   normalizzati.
6. Hardening completo prima di esposizione pubblica, multiutente o esecuzione
   autonoma oltre il laboratorio fidato.

