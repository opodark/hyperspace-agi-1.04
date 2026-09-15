# Sogni automatici

I sogni sono cicli automatici di riflessione eseguiti quando un nodo non ha
lavoro prioritario. Servono a trovare collegamenti, contraddizioni, domande
aperte e possibili sintesi nelle memorie del nodo. Non rappresentano coscienza
e non producono fatti: ogni risultato nasce e rimane un'**ipotesi candidata**.

## Direzione

Il sottosistema deve trasformare tempi di inattività in consolidamento utile
senza rallentare le richieste interattive e senza contaminare la memoria
autorevole. La separazione tra generazione e accettazione è il vincolo centrale:

```text
memorie sorgenti
      ↓
 ciclo automatico idle-only
      ↓
   hypothesis
      ↓
 revisione umana o policy validata
   ↙             ↘
promoted        rejected
   ↓
memoria/retrieval
```

Un sogno non deve influenzare risposte, decisioni o altri cicli soltanto perché
è stato generato. La promozione richiede una fase distinta e tracciata.

## Principi

1. **Il lavoro in primo piano ha sempre priorità.** Il ciclo parte soltanto a
   nodo libero e deve essere interrompibile da una nuova richiesta.
2. **Ipotesi e fatti restano separati.** I sogni vivono in uno store dedicato e
   non entrano automaticamente nella memoria o nel retrieval.
3. **Ogni risultato deve avere provenienza.** Memorie sorgenti, modello, prompt,
   parametri, timestamp e versione del formato devono essere ricostruibili.
4. **Nessuna azione autonoma.** Durante un sogno non sono disponibili tool,
   scritture esterne o operazioni sulla rete.
5. **Locale per default.** Un nodo riflette sulle proprie memorie. La mesh potrà
   scambiare soltanto artefatti promossi, secondo una policy esplicita; non
   condivide sogni grezzi o memorie sorgenti per default.
6. **Autonomia guidata da misure.** Frequenza, modello e ampiezza del contesto si
   aumentano solo dopo aver misurato valore prodotto e impatto sulle risorse.

## Stato attuale: fondazione e revisione implementate

Il worker Ollama supporta già una prima versione prudente:

- è disabilitato per default e si abilita per singolo nodo;
- controlla inattività, coda, richieste attive e stato degradato;
- usa al massimo 12 memorie proprie fra le ultime 100 e limita l'input a
  10.000 caratteri;
- esclude memorie ricevute da peer, titoli e sogni precedenti;
- deduplica gli input con un'impronta persistente;
- limita una generazione a 60 secondi e 192 token;
- interrompe il lavoro in background quando arriva una richiesta;
- salva il risultato localmente con stato `hypothesis` e pubblica un evento
  diagnostico, senza modificare la memoria originale.
- richiede al modello un artefatto JSON versionato con categoria, sintesi,
  autovalutazione di confidenza e domande aperte;
- conserva ID e hash SHA-256 delle memorie sorgenti, senza duplicarne il
  contenuto nel record del sogno;
- espone una inbox di revisione manuale con decisioni motivate e attribuite;
- promuove soltanto su richiesta esplicita una nota derivata in memoria e usa
  una tombstone per escluderla dal retrieval in caso di revoca.

La promozione manuale dimostra il ciclo tecnico, non che le ipotesi siano utili
o affidabili. Questa evidenza deve ancora arrivare dalla fase di valutazione.

## Attivazione per nodo

Nel file `.env` del worker:

```dotenv
DREAM_ENABLED=true
DREAM_MODEL=qwen2.5-coder:14b-instruct
DREAM_IDLE_SECONDS=120
DREAM_INTERVAL_SECONDS=900
DREAM_REVIEW_TOKEN=<segreto-casuale-di-almeno-32-caratteri>
```

Il modello deve essere già installato sul nodo. Il primo controllo avviene dopo
almeno 120 secondi senza lavoro; il worker ricontrolla ogni 10 secondi e separa
due tentativi di almeno 900 secondi. Per backend diversi da Ollama il worker
resta disabilitato.

`DREAM_REVIEW_TOKEN` abilita esclusivamente le azioni che cambiano lo stato di
un sogno o della memoria derivata. La dashboard lo invia al control-plane, che
lo verifica e lo sostituisce con la propria copia prima di inoltrare la richiesta
al nodo. Se manca o è più corto di 32 caratteri, la revisione è disabilitata in
modo sicuro; lettura e diagnostica restano disponibili.

L'arresto effettivo della computazione dopo la disconnessione HTTP dipende dal
backend. Il limiter non osserva lavoro avviato direttamente su Ollama al di
fuori del worker HyperSpace.

## Roadmap dedicata

Le fasi sono progressive: ciascuna deve produrre evidenze prima di sbloccare la
successiva.

| Fase | Obiettivo | Uscita verificabile | Stato |
|---|---|---|---|
| D0 — Fondazione sicura | Scheduling idle-only, interruzione, isolamento e diagnostica | Nessuna regressione sul lavoro interattivo; risultati separati dalla memoria | Implementata, da misurare |
| D1 — Artefatto strutturato | Sostituire il testo libero con categorie e provenienza | Ogni ipotesi cita le memorie sorgenti ed espone tipo, sintesi, confidenza e domande aperte | Implementata |
| D2 — Revisione | Aggiungere inbox e azioni `promote`, `reject`, `defer` | Ogni cambio di stato è attribuito, datato e reversibile | Implementata, da validare con uso reale |
| D3 — Valutazione | Costruire un piccolo dataset reale e confrontare prompt/modelli | Metriche disponibili per utilità, novità, correttezza, costo e interruzione | Da fare |
| D4 — Consolidamento | Permettere ai risultati promossi di produrre note o collegamenti persistenti | Nessuna scrittura autorevole senza promozione; provenienza preservata | Da fare |
| D5 — Mesh | Condividere tra nodi soltanto artefatti promossi e autorizzati | Policy privacy, deduplicazione globale e revoca testate end-to-end | Futuro |

### D1 — Formato candidato

Il primo cambiamento di codice dovrebbe introdurre un record versionato simile
a questo:

```json
{
  "schema_version": 1,
  "id": "dream-...",
  "node_id": "node-1",
  "status": "hypothesis",
  "kind": "connection",
  "summary": "...",
  "source_memory_ids": ["memory-1", "memory-2"],
  "confidence": 0.62,
  "open_questions": ["..."],
  "model": "qwen2.5-coder:14b-instruct",
  "created_at": "..."
}
```

Le categorie iniziali saranno limitate a `connection`, `contradiction`,
`summary` e `open_question`. La confidenza è un'autovalutazione del modello,
non una probabilità verificata; serve per ordinare la revisione, non per
promuovere automaticamente.

### D2 — Revisione e promozione

La dashboard deve mostrare una inbox filtrabile per nodo, tipo, data e stato.
L'operatore può:

- **promote**: accettare l'ipotesi come nota derivata, conservando i riferimenti
  alle fonti;
- **reject**: conservarla come risultato negativo utile alla valutazione;
- **defer**: rimandare la decisione senza cambiarne il valore epistemico.

La promozione deve creare un nuovo record di memoria e non riscrivere le fonti.
Una revoca successiva deve poter escludere il record dal retrieval senza perdere
la cronologia della decisione.

L'implementazione usa gli stati `hypothesis`, `deferred`, `promoted`,
`rejected` e `revoked`. Le azioni valide dipendono dallo stato corrente e ogni
azione richiede una motivazione non vuota. La nota promossa ha ID deterministico
`insight-<dream-id>`; promozioni e revoche vengono appese a `memory.jsonl`, dove
il retrieval considera soltanto l'ultima versione di ogni ID. Questo mantiene
la cronologia su disco senza rendere visibile una nota revocata.

### D3 — Metriche e criteri di avanzamento

Prima del consolidamento automatico misureremo:

- **utilità**: quota di ipotesi promosse o valutate utili;
- **correttezza**: quota priva di affermazioni non sostenute dalle fonti;
- **novità**: quota non equivalente a una memoria esistente;
- **tracciabilità**: quota con fonti sufficienti a ricostruire il ragionamento;
- **costo**: tempo, token e risorse per risultato utile;
- **impatto operativo**: latenza di interruzione e variazione delle prestazioni
  delle richieste in primo piano.

D4 si sblocca solo dopo una sessione di valutazione su memorie reali e una
revisione manuale dei falsi positivi. D5 richiede inoltre policy di privacy,
retention, revoca e autorizzazione tra nodi.

## Decisioni ancora aperte

- Dove conservare l'artefatto strutturato quando il memory schema comune sarà
  disponibile.
- Chi può promuovere un'ipotesi: soltanto una persona o anche una policy locale
  validata e configurabile.
- Durata di conservazione di ipotesi rifiutate e differite.
- Come evitare che memorie quasi duplicate generino cicli ripetitivi nel tempo.
- Quali segnali hardware aggiungere all'idle check, per esempio temperatura,
  alimentazione a batteria e pressione sulla memoria.
- Se e quando usare più nodi per criticare una stessa ipotesi senza esporre le
  memorie sorgenti.

## Risultati e diagnostica correnti

- `GET /dreams/status` sul worker mostra configurazione, stato e ultimo
  ciclo/errore, incluso il numero di ipotesi in attesa.
- `GET /dreams?status=hypothesis&limit=100` restituisce la inbox del nodo.
- `POST /dreams/{id}/review` applica `promote`, `reject`, `defer`, `reopen` o
  `revoke`; richiede `reviewer` e `rationale`.
- `GET /dreams/insights` restituisce le note derivate attive.
- In Dashboard Diagnostics si sceglie il nodo in Simulate Dream e si usa
  **Stato sogni automatici del nodo**.
- La **Dream Review Inbox** della dashboard filtra per stato e presenta solo le
  transizioni ammesse.
- `data/dreams.jsonl` contiene le ultime 100 riflessioni strutturate e l'audit
  delle revisioni. I vecchi record testuali ricevono a lettura un ID stabile e
  restano revisionabili senza migrazione distruttiva.
- `data/dream_insights.jsonl` contiene le note derivate e il loro stato.
- `data/dream_state.json` conserva deduplicazione e cooldown ai riavvii.
- L'evento `dream` appare nel control-plane e nella vista Live.

Una consegna del log fallita resta indicata nello stato mentre il risultato
rimane salvato localmente; non esiste ancora una coda persistente di
riconsegna. Simulate Chat e Simulate Dream sono iniezioni di eventi diagnostici,
chiaramente etichettate, e non invocazioni reali del modello.

## Verifica

```bash
python -m unittest discover -s tests -v
node tests/dashboard.test.cjs
```
