# DS4 in impresa — orchestrare con 128 GB

Roadmap per usare **DwarfStar (ds4)** su hardware da 128 GB come **orchestratore
centrale** di un deployment HyperSpace enterprise. Documento di lavoro, non
un'annunciazione: ogni fase dichiara cosa non e' ancora vero e come si verifica.

Riferimenti esterni: [antirez/ds4](https://github.com/antirez/ds4) —
`docs/SERVER.md`, `docs/MODELS.md`, `docs/DISTRIBUTED.md`, `docs/PERFORMANCE.md`.

## 1. L'idea

Il modello piu' capace non e' "un worker in piu'": e' il **cervello** che
pianifica, decompone e decide. La mesh esegue.

```text
                    DS4 su 128 GB (tier=root)
                    DeepSeek V4 Flash residente
                             |
              pianifica / decompone / critica
                             |
                   HyperSpace control-plane
                (policy, routing, audit, allowlist)
                             |
      +----------------------+----------------------+
      |                      |                      |
  nodi GPU/Metal         web node              code sandbox
  (inferenza media)      (task leggeri)        (sviluppo agentico)
```

Il control-plane resta **l'autorita'**: il modello propone, il CP dispone. E' il
principio gia' scritto in `VISION.md` e gia' implementato per i tool MCP
(allowlist per cliente) e per la federazione (identita' ECDSA).

## 2. Perche' ds4

Fatti verificati sulla documentazione del progetto, non impressioni:

| Proprieta' | Valore per l'impresa |
|---|---|
| Narrow per scelta | pochi modelli tenuti eccellenti, non un runner generico |
| Modello **residente** + slot indipendenti (`--batched-session N`) | paradigma `inference_server`: concorrenza reale, niente load/unload |
| API **OpenAI-compatible** su `:8000` | la mesh la parla gia': nessun protocollo nuovo |
| **Reasoning separato** dal testo | l'orchestratore puo' essere ispezionato: si vede cosa ha pensato |
| **Cache KV su disco** | prefissi condivisi fra sessioni e riavvii: contesti lunghi praticabili |
| **SSD streaming** | far girare modelli piu' grandi della RAM |
| **TP/RDMA** fra due Mac, pipeline parallelism | si somma la RAM invece di comprare una macchina enorme |
| Baseline pubblicata: 8x L40S, ~126 t/s aggregati su 16 sessioni | ordine di grandezza per il dimensionamento |

Contro, da mettere in conto: ds4 **non ha autenticazione**, i suoi GGUF **non sono
intercambiabili** con llama.cpp, e la scelta dei modelli e' dichiaratamente
"opportunistica" (un modello puo' essere rimosso quando ne arriva uno migliore).
Sono vincoli di progetto, non difetti temporanei.

## 3. Cosa entra davvero in 128 GB

I numeri sono del progetto ds4, non stime nostre. La colonna "128 GB" e' il
verdetto per una macchina singola da 128 GB con contesto ragionevole.

| Target | Peso | 128 GB |
|---|---|---|
| `ds4f-q2` — DeepSeek V4 Flash Q2 | ~81 GiB | **residente: e' il punto di partenza** |
| `ds4f-q2-q4` — ultimi layer esperti a Q4 | > 81 GiB | memoria piu' larga |
| `ds4f-q4` / `ds4f-mxfp4` | piu' grande | macchina piu' grande o distribuito |
| `ds41f-q2` — V4.1 Flash | 341 GiB su disco, 152 GiB di pesi | solo con `--ssd-streaming` |
| `ds41f-q4` | 483 GiB su disco, 294 GiB di pesi | no |
| `glm53-q2` — GLM 5.3 Flash | vicino al budget | si, con contesto misurato |
| `glm53-q4` | 178 GiB | no |
| `pro-q2-imatrix` — V4 PRO | 512 GB residenti | no (streaming) |

Conseguenza operativa: **128 GB non e' "comodo", e' il minimo.** Pesi, contesto e
buffer di runtime competono per la stessa RAM. La configurazione di partenza e'
`ds4f-q2` con contesto esplicito; `--ctx` e numero di slot vanno scelti
**insieme**, perche' un contesto che entra una volta non entra quattro.

## 4. Fasi

Ogni fase ha un criterio di accettazione verificabile. Nessuna fase si apre
senza che la precedente abbia prodotto evidenza.

### F0 — Contratto backend (fatta)

Integrato il *contratto*, non l'inferenza: senza hardware non si testa altro.

- profilo capability `ds4` = `inference_server` (modello residente, batching a
  slot, nessun load a runtime);
- `DS4MetricsProvider`: stato da `GET /v1/models`, throughput e latenza
  **osservati** dal log interazioni, schema `runtime` identico agli altri motori;
- `DS4_URL` e sezione dedicata in `.env.example` con l'avvertenza sull'auth;
- 11 test contro un `ds4-server` finto, senza hardware e senza GGUF da 81 GiB.

*Accettazione:* `python -m unittest discover -s tests` verde; il provider
risponde `up: false` senza eccezioni quando il server e' giu'.

### F1 — Nodo con `INFERENCE_BACKEND=ds4`

Il nodo deve poter parlare a ds4 come backend di inferenza, non solo leggerne le
metriche.

- instradare la chat del nodo su `DS4_URL` (il percorso OpenAI-compatibile e'
  gia' quello giusto: cambia la base URL e il nome modello);
- **escludere le chiamate Ollama-specifiche** quando il backend e' ds4:
  `/api/ps`, `/api/tags`, e soprattutto il **fallback nativo `/api/chat`**, che
  ds4 non ha (oggi `_NATIVE_CHAT_FALLBACK_MODELS` e' model-driven: deve
  diventare backend-driven);
- mappare la concorrenza del nodo sugli slot di `--batched-session N`
  (una env `DS4_SESSION_SLOTS` come limite di capacita');
- sogni: oggi sono gated a `INFERENCE_BACKEND == "ollama"`, vanno resi
  back-end agnostici con una capability esplicita.

*Accettazione:* una chat con `INFERENCE_BACKEND=ds4` contro il server finto
produce streaming, tool call e un risultato non vuoto (le stesse quattro prove
gia' usate su qwen3 e deepseek-r1).

### F2 — Il nodo centrale diventa `tier=root`

- il CP deve preferire il nodo ds4 per il lavoro difficile: il campo `tier`
  esiste ed entra gia' nello scoring, ma oggi la scelta e' guidata dalla
  metrica osservata. Serve una **policy esplicita di ruolo**, non solo un
  punteggio piu' alto, altrimenti un nodo veloce e piccolo puo' rubare lavoro
  che richiede contesto.
- dichiarare la capacita' del nodo in modo che il CP possa **escluderlo dai
  task leggeri** (un modello da 81 GiB non deve servire una traduzione).

*Accettazione:* due nodi attivi, uno ds4: con un task lungo il CP sceglie ds4 e
con uno banale sceglie l'altro. Verificabile dai log di routing.

### F3 — L'orchestratore

Qui il modello cambia **ruolo**, non solo potenza: non esegue soltanto, decide.

- ruolo `planner`/`orchestrator` nel lessico gia' previsto da `ROADMAP.md`
  (routing per ruoli modello: general, coder, planner, memory, tool-agent);
- il CP puo' chiedere a ds4 di **decomporre** un obiettivo in task, e poi
  eseguirli sulla mesh;
- l'output del pianificatore e' **una proposta con provenienza** (modello,
  nodo, versione, prompt), non un effetto: la stessa disciplina dei sogni
  (`hypothesis` -> review -> promoted/rejected) applicata alle decisioni;
- ogni decisione automatica deve essere **reversibile e tracciabile**: chi ha
  deciso cosa, con quale modello, su quale evidenza.

*Accettazione:* un obiettivo in linguaggio naturale produce un piano con task
eseguiti su nodi diversi, e ogni passo e' ricostruibile dai log di audit.

### F4 — Multi-tenant, autenticazione, audit

ds4 **non ha autenticazione**: in impresa non puo' stare su una rete raggiungibile.

- ds4 dietro un proxy che aggiunge auth e TLS, oppure solo su loopback raggiunto
  dal nodo; mai esposto su LAN o Tailscale "nudo";
- `/mcp` ha gia' token per cliente + allowlist dei tool + audit con
  `type=mcp` e `source=mcp:<cliente>`: la stessa disciplina va estesa a `/v1`
  (oggi senza token perche' lo consuma Open WebUI: decisione da prendere);
- quote per tenant sugli slot di ds4 (una sessione occupata e' una capacita'
  consumata, non un dettaglio tecnico);
- separazione della memoria per tenant: Hermes e' gia' la fonte unica, ma la
  partizione per cliente e' un requisito nuovo.

*Accettazione:* due client diversi non vedono i tool ne' i dati dell'altro, e
ogni chiamata e' attribuita a un cliente nei log.

### F5 — Scala

- **due Mac da 128 GB in TP/RDMA**: si somma la RAM invece di comprare una
  macchina da 512 GB. Ogni rank ha bisogno del GGUF completo su disco. Un
  contesto TP non si ripristina istantaneamente: il caricamento da cache
  ricostruisce il prefisso su entrambi i rank;
- **CUDA multi-GPU**: la baseline del progetto e' 8x L40S con ~126 t/s
  aggregati su 16 sessioni — un riferimento per il dimensionamento, non una
  promessa per ogni workload;
- **federazione fra siti**: esiste gia' (identita' ECDSA + allowlist), con la
  differenza che oggi scambia task, non capacita' di modello. Per l'impresa
  serve dichiarare *quale* capacita' un sito remoto puo' servire.

*Accettazione:* un secondo sito esegue un task che il nodo locale non puo'
servire, con provenienza e costo visibili.

### F6 — Continuita' operativa

- **cache KV su disco** (`--kv-disk-dir`): prefissi condivisi fra sessioni e
  riavvii. Contiene prompt e stato del modello: la directory e' un **dato
  riservato**, va trattata come i backup di memoria;
- **il GGUF e' il dato prezioso**: hash e copia verificata prima di contare su
  un riavvio (il download piu' piccolo e' ~81 GiB);
- **valutazione**: `ds4-eval` del progetto come regressione di capability, piu'
  le metriche di sezione 6;
- **rollback**: il percorso Ollama deve restare funzionante. Oggi lo e' per
  costruzione (backend separato), ma va **testato** periodicamente, altrimenti
  si scopre rotto il giorno in cui serve.

*Accettazione:* riavvio completo del nodo centrale con recupero del prefisso KV
e nessuna perdita di memoria autorevole; rollback a Ollama provato.

## 5. Dove si tocca il codice

| Punto | File | Cosa |
|---|---|---|
| Profilo + metriche ds4 | `node/backend_metrics.py` | **fatto** (F0) |
| Gateway di inferenza | `node/main.py` | base URL, capacita' slot, esclusione chiamate Ollama-only (F1) |
| Sogni | `node/main.py` | togliere il gate `INFERENCE_BACKEND == "ollama"` (F1) |
| Pattern modelli | `control-plane/main.py` | alias ds4 in `_TOOL_CAPABLE_PATTERNS`: `deepseek-v4`, `glm-5`, `qwen3.8` (F1) |
| Fallback nativo | `control-plane/main.py` | da model-driven a backend-driven (F1) |
| Ruolo/tier | `control-plane/main.py` + routing | policy di ruolo, non solo punteggio (F2) |
| Orchestrazione | control-plane + agent framework | ruolo planner, piano come proposta con provenienza (F3) |
| Auth esterna | `control-plane/main.py`, `shared/` | token e allowlist su `/v1` come su `/mcp` (F4) |
| Federazione | `control-plane/main.py` | capacita' di modello dichiarate per sito (F5) |

## 6. Cosa si misura (non cosa si spera)

| Asse | Metrica | Dove |
|---|---|---|
| Prestazioni | TTFT e tok/s per modello | gia' esposti da `runtime` nel nodo |
| Concorrenza | sessioni servite senza degrado | slot di `--batched-session` |
| Qualita' | regressione di capability | `ds4-eval` + task reali |
| Costo | tok/s per watt e per euro; `--power` come trade-off esplicito | misure + bolletta |
| Affidabilita' | uptime, tempo di recovery, prefisso KV ritrovato dopo riavvio | log + prova F6 |
| Sicurezza | chiamate attribuite e rifiutate, latenza di revoca di un token | log `type=mcp` (esteso a `/v1`) |
| Orchestrazione | piani eseguiti senza intervento, tool call riuscite, latenza di interruzione | log di routing + audit |

## 7. Rischi accettati

1. **ds4 e' narrow e opportunistico**: un modello puo' sparire quando ne arriva
   uno migliore. Il contratto F0 lo assorbe (il ruolo e' del backend, non del
   modello), ma il GGUF va archiviato con hash: e' l'unico artefatto non
   ricostruibile in fretta.
2. **I GGUF non sono intercambiabili** con llama.cpp: non esiste un piano B
   immediato sullo stesso file. Il piano B e' un altro modello su un altro
   engine, non lo stesso file altrove.
3. **Nessuna autenticazione**: dipendenza da un proxy. Va scritto nell'ordine di
   servizio, non scoperto in incidente.
4. **128 GB e' il minimo, non il comodo**: il contesto compete con i pesi: la
   configurazione va misurata, non stimata.
5. **SSD streaming**: utile per far entrare modelli piu' grandi, ma la latenza
   va misurata sul disco reale.
6. **Progetto giovane** (772 commit, sviluppo dichiaratamente assistito da AI):
   si adotta per il valore, tenendo il percorso Ollama come rete.
7. **Un solo orchestratore e' un punto singolo di guasto**: se il nodo centrale
   non risponde, si perde il planner, non la mesh. Il CP deve saper **degradare**
   (routing senza orchestratore) invece di fermarsi.

## 8. Cosa ha gia' insegnato la sessione DeepSeek

Prima di comprare hardware abbiamo provato la famiglia DeepSeek sull'hardware
che c'e' (`deepseek-r1:8b`, gia' presente su entrambi i nodi). I test **non sono
conclusivi sulle capacita' del modello**, ma hanno prodotto tre scoperte che
cambiano i requisiti di F1.

1. **Il timeout di 180s e' troppo stretto per i modelli reasoning.**
   `deepseek-r1:8b` con 600 token di risposta non conclude entro 180s, **ne'
   sul nodo Windows ne' su Ollama locale**. Verificato dal testo d'errore lato
   server, non per impressione:
   ```text
   HTTPConnectionPool(host='100.81.234.102', port=8081): Read timed out. (read timeout=180)
   HTTPConnectionPool(host='host.docker.internal', port=11434): Read timed out. (read timeout=180)
   ```
   Il timeout e' cablato in piu' punti (nodo, proxy, CP). Con ds4 il **thinking
   e' acceso di default**: la policy dei timeout deve essere per modello/backend
   e configurabile, non un 180s globale.

2. **Un fallimento arriva come successo.** Dopo i timeout il task e' stato
   marcato `done` e il client ha ricevuto `HTTP 200` con corpo
   `{"error": {...}}`. Un errore travestito da risposta e' peggio di un 5xx:
   nei log non si distingue da un esito positivo se non leggendo il corpo.

3. **La catena di fallback costa ~3 minuti** prima di ammettere di aver fallito
   (timeout sul nodo 180s -> OmniRoute 400 -> timeout su Ollama 180s). In un
   ciclo interattivo e' inaccettabile, e con un orchestratore che pianifica
   diventa inaccettabile al quadrato.

Conseguenza per il piano: **un 8B reasoning su hardware debole non e' un
orchestratore.** Non serve a validare il ruolo di F2/F3, e non e' un
ripiego praticabile nel frattempo. Il ruolo di orchestratore richiede il
modello capace con hardware adeguato — cioe' esattamente cio' che DS4 su 128 GB
promette — mentre la mesh attuale resta adatta ai task brevi e paralleli.

F1 va quindi esteso con: timeout **per backend e per modello**, distinzione
netta fra successo, fallimento e degradazione, e nessun fallback a catena
silenzioso in un loop interattivo.

## 9. Stato attuale

- **F0 fatta e verificata**: profilo `ds4` + `DS4MetricsProvider` + 11 test
  contro un `ds4-server` finto; suite a 226 test verdi.
- **Pipeline mesh validata** su qwen3:8b con prove reali (chat, streaming,
  tool call eseguita con risultato, fallback del reasoning). Non e' validata
  sui modelli DeepSeek: troppo lenti sull'hardware attuale.
- **Decisione aperta**: `/v1` e' ancora senza token (lo consuma Open WebUI).
  Prima di F4 quella decisione va presa, non rimandata.
- **Da correggere prima di F1**: i tre punti della sezione 8.




