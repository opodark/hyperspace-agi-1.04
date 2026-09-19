# Nightly Development Dream

Il Nightly Development Dream trasforma un periodo di inattività in un solo
esperimento di manutenzione software al giorno. È distinto dal DreamWorker dei
nodi: quello riflette sulla memoria; questo produce esclusivamente una proposta
di codice revisionabile.

## Sequenza e gate

1. Il control-plane verifica finestra oraria, inattività e limite giornaliero.
2. Crea un workspace con Docker Sandboxes; se indisponibile usa il runner
   container offline.
3. Espone al modello soltanto `code_sandbox`: niente web, connettori, MCP o
   strumenti host.
4. Il modello ispeziona, modifica e testa una sola piccola proposta.
5. Il control-plane estrae il diff indipendentemente dal resoconto del modello.
6. Crea un secondo workspace pulito, applica la patch con `git apply --check`
   e ripete l'intera suite di test.
7. Elimina il verificatore. Conserva il workspace originale soltanto se i test
   passano e registra la proposta nella development dream inbox.
8. La review umana può approvare, rifiutare o rinviare. Anche `approve` registra
   soltanto la decisione: non applica, non committa, non pusha e non distribuisce.

Modifiche a autenticazione, segreti, policy del sandbox, Docker/Compose,
deployment, gate di review e allo stesso scheduler sono vietate nel prompt
iniziale. Questo è un limite aggiuntivo, non il confine di sicurezza principale:
il vero confine resta la microVM o il container offline senza write-back.

## Configurazione

```dotenv
CODE_SANDBOX_ENABLED=true
SBX_SANDBOX_ENABLED=true
NIGHTLY_DEV_ENABLED=true
NIGHTLY_DEV_START_HOUR=1
NIGHTLY_DEV_END_HOUR=5
NIGHTLY_DEV_IDLE_SECONDS=3600
NIGHTLY_DEV_MODEL=
```

La finestra usa l'ora locale del container. `NIGHTLY_DEV_MODEL` vuoto usa il
modello predefinito; conviene indicare il modello coder locale. Lo stato è su
`GET /development-dreams/status`, le proposte su `GET /development-dreams`.
Una corsa manuale di collaudo è disponibile con
`POST /development-dreams/run`, protetta da `DREAM_REVIEW_TOKEN`.

Gli artefatti persistono in `development_dreams.jsonl` e comprendono obiettivo,
backend, workspace, file cambiati, diff, sintesi del modello e risultato della
verifica pulita. Il limite è una proposta per data locale, anche dopo riavvio.
