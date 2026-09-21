# Code Sandbox

HyperSpace può sviluppare e testare codice, incluso il proprio, senza ricevere
accesso in scrittura al checkout operativo. Il sandbox è un runner separato e
offline che lavora soltanto su copie usa-e-getta del codice incluso nella sua
immagine.

È il runtime offline dell'[ambiente di sviluppo unificato](development-tooling-architecture.md):
gli utenti e i coding agent lo vedono insieme a Forge, benchmark e strumenti di
security, mentre browser e pentest con rete girano in worker separati.

Il backend preferito è Docker Sandboxes (`sbx`), eseguito dall'host-agent in
una microVM con clone privato della repository sorgente montata in sola
lettura. Se `sbx` non è installato, autenticato o disponibile, la creazione di
un nuovo workspace ricade automaticamente sul runner container offline. Un
workspace già creato resta sempre vincolato al proprio backend.

## Confine di sicurezza

```text
control-plane
    │ job/result JSON su volume condiviso
    ▼
code-sandbox container
    ├── rete disabilitata
    ├── nessun Docker socket
    ├── filesystem root in sola lettura
    ├── capability Linux rimosse
    ├── utente non-root
    ├── limiti CPU, RAM, PID, timeout e output
    └── workspace persistenti su volume dedicato
```

Il control-plane non riceve il socket Docker né la possibilità di eseguire
comandi sull'host. Per `sbx` chiama la sola azione `sbx_sandbox` della whitelist
di `hostctl`; nomi, percorsi, operazioni ed eseguibili sono validati prima di
invocare la CLI. Solo il comando richiesto viene eseguito dentro la microVM.

Il codice sorgente viene copiato nell'immagine durante la build. `.env*`,
`.git`, `data/`, worktree, cache e dipendenze locali sono esclusi dal build
context; il runner non può quindi leggere i segreti runtime dell'host. Ogni
workspace contiene una baseline e una copia modificabile. Il risultato
consegnabile è un diff per revisione umana.

Non esistono azioni per applicare il diff al repository reale, creare commit,
fare push, accedere alla rete o distribuire servizi. Queste operazioni restano
un workflow distinto e autorizzato dall'operatore.

## Attivazione

Nel `.env`:

```dotenv
CODE_SANDBOX_ENABLED=true
SBX_SANDBOX_ENABLED=true
SANDBOX_MAX_WORKSPACES=6
SANDBOX_MAX_TIMEOUT=120
SANDBOX_MAX_FILE_BYTES=1048576
SANDBOX_JOB_TIMEOUT=240
```

Sul computer host, installare `sbx`, completare `sbx login`, avviare il daemon
e riavviare `hostctl/agent.py`. `SBX_SOURCE_DIR` può sovrascrivere la repository
da clonare; per default hostctl usa la root di HyperSpace.

Ricostruire `control-plane` e `code-sandbox`. Lo stato è disponibile su
`GET /sandbox/status`; il tool `code_sandbox` compare anche via MCP.

La dashboard espone lo stesso contratto nella scheda **Dev Sandbox**. Da lì
l'operatore può creare un workspace Docker, esplorare e modificare file,
eseguire Ruff, Bandit, pytest, unittest e cProfile, lanciare un piano `verify`,
leggere i finding per file e riga e ispezionare il diff. **Salva diff nel
Forge** registra la patch come draft inerte `.diff`; non modifica il checkout
reale e non installa né esegue l'artefatto.

## Workflow del tool

Il tool usa una singola API con azioni esplicite:

1. `create` crea una copia pulita e restituisce `workspace_id`;
2. `list` e `read` esplorano il codice;
3. `write` crea o sostituisce un file, mentre `replace` richiede un numero
   esatto di occorrenze per evitare modifiche ambigue;
4. `run` esegue un vettore `argv` senza shell. Gli eseguibili iniziali sono
   Python, Node/npm, pytest e Git; l'immagine include inoltre il sottoinsieme
   locale delle dipendenze Python usate dai servizi HyperSpace. La rete resta
   assente anche per questi processi;
5. `catalog`, `check` e `verify` espongono i preset di sviluppo installati e i
   relativi report strutturati;
6. `diff` restituisce file modificati e patch unificata;
7. `discard` elimina il workspace.

I workspace sono limitati e non vengono promossi automaticamente. Prima di
applicare una proposta al repository operativo vanno verificati diff e test da
un essere umano o da un futuro gate di review separato.

## Limiti della prima versione

- Un workspace usa il codice presente al momento della build dell'immagine.
- Le dipendenze non già disponibili nell'immagine non possono essere scaricate,
  perché il runner non ha rete.
- Il runner è sequenziale e applica limiti a livello di container; non è ancora
  un pool di microVM per task ostili multi-tenant.
- Nessun merge automatico: è deliberatamente fuori dal perimetro iniziale.
- Browser, Nmap e ZAP non vengono aggiunti a questa immagine: appartengono ai
  worker dedicati descritti nell'architettura del tooling.

Il ciclo notturno è documentato in [nightly-development-dream.md](nightly-development-dream.md).
