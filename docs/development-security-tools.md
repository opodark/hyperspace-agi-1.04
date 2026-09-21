# Strumenti di sviluppo e security per i modelli Hyperspace

Stato: preset offline, Dev Sandbox e integrazione selettiva ECC implementati,
21 settembre 2026; benchmark sandboxizzato e worker specializzati ancora da
realizzare. Gli strumenti sono capacità di un unico HyperSpace, riutilizzabili
da modelli locali, coding agent e sviluppo umano.

Ambiente di sviluppo: MacBook sul ramo `feature/development-security-tools`.
Il nodo centrale resta su Win11; ricostruzioni e prove di questo ramo vengono
eseguite sul MacBook. Il push del ramo non aggiorna automaticamente il nodo centrale.

## Base già presente

- `control-plane/main.py`: definizioni dei tool, dispatcher `_execute_tool_call`,
  endpoint `/tools/execute` e handler `code_sandbox`.
- `sandbox/runner.py`: workspace temporanei, lettura e modifica file, esecuzione
  con argv, timeout, diff e audit. Nel Compose principale il servizio usa
  `network_mode: none`, filesystem root in sola lettura e capability rimosse.
- `shared/development_dream.py`: verifica di una proposta in un workspace distinto.
- [Forge](tool-skill-forge.md): artefatti inerti con revisione; approvare un
  artefatto non lo rende un tool eseguibile.
- [Conversazione di codice](code-conversation.md): correlazione con `traceId`;
  il log è narrativo e non costituisce autorizzazione all'esecuzione.

Il runner consente interpreti generici: la lista di eseguibili non costituisce
da sola isolamento del codice. L'isolamento dipende anche dal container e dai
volumi. Il runner aggiornato limita i byte durante la raccolta e termina il gruppo di
processi al timeout o al superamento del limite.

## Decisione architetturale

Dev Sandbox, Forge, benchmark, debug, test, security review e pentest di
laboratorio formano un solo ambiente HyperSpace. Non vengono però accumulati in
un unico container: la sandbox resta offline, Playwright usa un browser worker,
pip-audit un job con accesso controllato alle advisory e Nmap/ZAP un Security
Lab con rete e target dichiarati.

La decisione completa, il contratto comune dei job e l'ordine di lavoro sono in
[Architettura unificata degli strumenti di sviluppo](development-tooling-architecture.md).
L'obiettivo è consentire a un coding agent un flusso continuo — modifica, lint,
test, analisi, verifica browser/rete, diff e Forge — lasciando al control-plane
la scelta del runtime per ogni passaggio.

Il threat model corrente è privato e single-tenant, con due operatori fidati.
L'hardening completo di tool e chat resta debito tecnico esplicito, ma non
blocca benchmark e worker usati nel laboratorio privato. Diventa un gate prima
di esposizione pubblica, multiutente o di maggiore autonomia operativa.

## Prima dotazione proposta

### Disponibile nel codice

Il tool `code_sandbox` espone ora `catalog` e `check`. Ricostruire il servizio
`code-sandbox` per installare Bandit e caricare il nuovo runner; ricostruire anche
il Control Plane per aggiornare il contratto dei tool. Abilitare
`CODE_SANDBOX_ENABLED=true` e configurare `FORGE_ADMIN_TOKEN` nel `.env` locale.

La scheda **Dev Sandbox** del dashboard rende disponibile lo stesso workflow
all'utente: editor sulla copia usa-e-getta, lista file, preset singoli, piano
`verify`, finding navigabili e diff. Il diff può essere registrato nel Forge
come draft `patch`; questa operazione conserva una proposta `.diff` per review
senza applicarla al repository reale.

Sequenza di argomenti da inviare al tool:

```json
{"action": "catalog"}
{"action": "create", "backend": "docker", "label": "security-review"}
{"action": "check", "workspace_id": "docker:<id restituito>", "tool_id": "bandit", "path": "shared", "timeout": 60}
{"action": "check", "workspace_id": "docker:<id restituito>", "tool_id": "pytest", "path": "tests", "timeout": 120}
{"action": "check", "workspace_id": "docker:<id restituito>", "tool_id": "unittest", "path": "tests"}
{"action": "check", "workspace_id": "docker:<id restituito>", "tool_id": "ruff", "path": "shared"}
{"action": "verify", "workspace_id": "docker:<id restituito>", "checks": [{"tool_id":"ruff","path":"shared"},{"tool_id":"bandit","path":"shared"},{"tool_id":"pytest","path":"tests","timeout":120}]}
{"action": "discard", "workspace_id": "docker:<id restituito>"}
```

Ogni riga è una chiamata separata. `profile` esegue il file indicato: scegliere
uno script di riproduzione presente nel workspace. Il catalogo interroga il
runner Docker; con un workspace `sbx:` restituisce esplicitamente preset non
supportati, senza spostare il lavoro su un altro backend.

I risultati includono `completed`, `passed`, `exit_code`, `duration_ms`,
`truncated`, `tool_id` e `version`. `passed` è il verdetto del preset;
`completed` indica la disponibilità di un risultato completo. Bandit può
completare correttamente con finding e quindi `passed: false`. I finding
includono percorso relativo, riga, regola, gravità, confidenza e messaggio.
Scansioni vuote, errori di parsing e report troncati non passano.

`ruff` applica la configurazione presente nel workspace (`pyproject.toml`,
`ruff.toml` o `.ruff.toml`) e restituisce finding con percorso, riga, regola e
messaggio. `verify` riceve da una a sei check espliciti e passa solo se tutti
sono completati e passano; il suo report conserva il risultato di ogni check.
Questo rende eseguibile la parte di lint, test e security della skill ECC
`verification-loop`, senza inventare build, coverage o type-check che non sono
stati richiesti o configurati dal progetto.

Bandit non esegue il sorgente; i preset di test e profiling lo eseguono. Per
Bandit si ignorano le soppressioni `nosec` e il file `.bandit` del workspace.
Per pytest l'autocaricamento dei plugin installati è disabilitato; i progetti
che richiedono plugin specifici possono usare il comando `run` preesistente.
Ruff e pytest hanno le rispettive cache disabilitate nei preset, così una
verifica non aggiunge file tecnici al diff del workspace.
Output e traceback dei test restano output del codice eseguito: non è ancora
implementata una redazione generale dei segreti né persistenza dei report.

### Runtime ed estensioni successive

| Funzione | Strumenti | Integrazione proposta |
|---|---|---|
| Ispezione e debug Python | lettura, ricerca, diff, traceback, `pdb`, `cProfile` | Dev Sandbox offline; debugger interattivo nell'IDE, profili e traceback come artefatti per il modello |
| Test e regressioni | pytest e unittest già disponibili | preset di esecuzione con report, durata e codice di uscita |
| Analisi security Python | Bandit | dipendenza fissata nell'immagine e report JSON dal workspace |
| Vulnerabilità dipendenze | pip-audit | dependency-audit worker con accesso controllato alla fonte advisory; dichiarare data e copertura dei dati |
| Test browser e debug UI | Playwright e Trace Viewer | browser worker con tracce e screenshot conservati localmente |
| Inventario di rete | Nmap | Security Lab con target e porte definiti nel job |
| Verifica web iniziale | ZAP Baseline | Security Lab con report JSON/HTML; spider più analisi passiva |

ZAP Baseline genera traffico di crawling, pur senza eseguire una scansione
attiva di attacco. Un profilo di pentest attivo richiede una fase successiva con
operazioni e target espliciti, distinta dalla baseline.

La sandbox offline rimane il percorso per modificare e testare codice. I worker
specializzati sono moduli dello stesso prodotto e condividono report e
orchestrazione, ma non i privilegi. Il Security Lab accede soltanto al
laboratorio o ai sistemi inclusi nello scope del job. Nella fase fidata iniziale
il modello propone target espliciti e il server applica i limiti del worker;
prima di ampliare l'esposizione, identità, redirect e risoluzione DNS devono
essere vincolati end-to-end allo stesso scope.

## Integrazione ECC: selettiva

[ECC](https://github.com/affaan-m/ECC) contiene skill, agenti, regole, hook e
configurazioni MCP. La proposta è importare inizialmente solo procedure testuali
selezionate: `security-review` e `verification-loop`. La prima guida la revisione
di sicurezza; la seconda organizza i controlli di build, tipi, lint e test.
Gli esempi vanno adattati allo stack e ai comandi effettivamente disponibili.

Per Hyperspace serve un adapter: il testo di una skill non registra un tool nel
dispatcher e non installa gli eseguibili. Il caricamento deve essere su richiesta,
con un budget di contesto misurato sui modelli locali.

Importazione implementata: bundle dal commit
`9ac593b55cba44c8b20152a5c7f28d300a67ec7e`, selezione dei file,
registrazione di repository/commit/percorso/hash/licenza, draft nel Forge,
revisione, poi caricamento nel contesto del task. L'importatore rifiuta
percorsi esterni, symlink in fuga e file troppo grandi. Conservare gli avvisi MIT
con qualsiasi contenuto redistribuito. Le due skill sono state ispezionate;
questo non costituisce audit dell'intero repository ECC. Procedura e contratto
API sono descritti nel [Forge](tool-skill-forge.md#selected-ecc-workflows).

Hook, memoria automatica e configurazioni MCP meritano valutazioni separate:
introducono esecuzione e persistenza oltre al testo delle procedure. La valutazione
attuale non certifica il repository completo né la compatibilità dei suoi adapter.

## Contratto proposto per i job

Un job include `tool_id`, versione del tool, `workspace_id` oppure `scope_id`,
input strutturati, timeout, budget output e `traceId`. Identità e permessi sono
assegnati dal server; il modello non può autoattribuirli negli argomenti.

Il risultato distingue completamento del processo ed esito della verifica:
`completed`, `exit_code`, `findings`, `duration_ms`, `truncated`, riferimenti agli
artefatti e versione del database advisory quando applicabile. Un tool assente,
un timeout o un report non interpretabile non devono diventare un test superato.
Log e report devono oscurare credenziali e cookie prima di arrivare al modello.

Ogni adapter deve interpretare i codici di uscita del proprio strumento: per
esempio ZAP distingue segnalazioni WARN/FAIL dagli errori di esecuzione.

## Modelli locali e benchmark

Mantenere la scelta del modello configurabile, inclusi modelli dichiarati
uncensored. L'etichetta non misura precisione, capacità di usare strumenti o
qualità delle patch: queste proprietà vanno misurate separatamente.

Affiancare al benchmark esistente una suite di task su fixture locali: produrre
una tool call valida, interpretare un traceback, riparare un test, rilevare una
vulnerabilità nota, evitare falsi positivi su una fixture corretta e fermarsi
quando il tool restituisce un errore. Confrontare stesso nodo, quantizzazione,
contesto e budget; registrare completamento, correttezza, latenza e numero di call.

`scripts/model_bench.py` oggi esegue il codice generato con un subprocess locale:
per queste nuove sonde l'esecuzione va trasferita alla sandbox prima di ampliare
i compiti. Il codice di uscita e i test indipendenti devono determinare il verdetto;
una stringa di successo prodotta dal modello non è una prova sufficiente.

## Ordine di implementazione e criteri di completamento

1. Catalogo di capacità effettivamente disponibili e preset di test/debug offline.
   Verificare tool assente, timeout, output eccessivo e report malformato.
2. Adapter Bandit e report unificati. Usare fixture con problemi noti e fixture
   corrette; collegare ogni finding a file e posizione verificabili.
3. Importazione selettiva ECC nel Forge e caricamento per task. Verificare
   provenienza, limiti dei file e assenza di esecuzione durante l'importazione.
4. Spostare il verdetto del benchmark nella sandbox, quindi confrontare modelli
   con e senza procedure ECC su fixture verificabili e condizioni equivalenti.
5. Persistenza e visualizzazione dei report, con redazione prima che output e
   traceback vengano consegnati al modello.
6. Dependency audit con pip-audit e provenienza dei dati advisory.
7. Browser worker con Playwright, screenshot e Trace Viewer.
8. Security Lab privato con Nmap e ZAP Baseline, target dichiarati e report
   normalizzati. Non aggiungere nuovi ingressi pubblici per abilitarlo.
9. Prima di passare dall'attuale ambiente fidato a uso pubblico, multiutente o
   autonomo, completare autenticazione e allowlist end-to-end su
   `/tools/execute`, chat e MCP, più enforcement di target, DNS e redirect.

I punti 1–3 e la UI Dev Sandbox costituiscono la prima consegna completata. Il
prossimo incremento è il punto 4: rende misurabile il beneficio delle skill su
strumenti realmente disponibili senza introdurre ancora accesso di rete.

## Fonti consultate

- [ECC README](https://github.com/affaan-m/ECC).
- [ECC security-review](https://github.com/affaan-m/ECC/blob/main/skills/security-review/SKILL.md).
- [ECC verification-loop](https://github.com/affaan-m/ECC/blob/main/skills/verification-loop/SKILL.md).
- [Bandit](https://github.com/PyCQA/bandit).
- [Ruff](https://github.com/astral-sh/ruff).
- [pip-audit](https://github.com/pypa/pip-audit).
- [Playwright Trace Viewer](https://playwright.dev/docs/trace-viewer).
- [Nmap Reference Guide](https://nmap.org/book/man.html).
- [ZAP Baseline](https://www.zaproxy.org/docs/docker/baseline-scan/).
