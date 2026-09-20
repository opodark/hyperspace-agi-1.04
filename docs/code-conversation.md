# Conversazione fra agenti che scrivono codice

Canale per far collaborare due IA (una per macchina) sulla scrittura di codice,
usando quello che c'è già: il **filo dei log** come conversazione, il **Forge**
come deposito del codice, il **gate del nightly** come verdetto.

Perché così e non un protocollo nuovo — quattro cose esistevano già e questa
scelta le riusa invece di duplicarle:

| serve | c'è già |
|---|---|
| trasporto fra le due macchine | `/federate/execute`, `/federate/view` (firmati ECDSA) |
| scrivere messaggi con un filo | `POST /logs/add` accetta `type`, `summary`, `detail`, `sourceNode`, `targetNode`, **`traceId`** |
| un posto dove il codice non viene eseguito | il Forge: bozze **inerti**, approvazione con token |
| un verdetto che non si può falsificare | il gate del nightly: diff estratto dal CP, `git apply --check`, suite completa in un workspace pulito |

## Vocabolario

| type | chi scrive | campi |
|---|---|---|
| `code_proposal` | chi propone | `sourceNode` = autore, `targetNode` = revisore, `summary` = intento in una riga, `detail` = riferimento dell'artefatto Forge |
| `code_review` | il revisore | `summary` = osservazioni, `status` = `info`/`warning` |
| `code_verdict` | chi decide | `status` = `success` approvato / `failed` rifiutato, `detail` = **output del gate** |

`traceId` è il filo: lo **stesso** valore su tutti i messaggi della stessa
conversazione. Senza, `push_log` ne genera uno nuovo per ogni messaggio — quindi
in una conversazione va passato sempre.

## Il codice NON va nel log

Il log porta l'intento e il riferimento; il contenuto è un artefatto del Forge.
Tre motivi:

1. il log resta leggibile e filtrabile (`/logs?type=code_proposal`);
2. la vista federata manda `summary` (troncato a 160 caratteri) e **mai** `detail`:
   così il codice non esce verso il peer per distrazione;
3. l'approvazione ha già il suo posto e il suo token (`X-Hyperspace-Forge-Token`).

## Pubblicare un messaggio

```bash
curl -s -X POST http://localhost:8085/logs/add -H 'Content-Type: application/json' -d '{
  "type":"code_proposal", "traceId":"a1b2c3d4",
  "sourceNode":"macbook", "targetNode":"win11",
  "summary":"estrai il raggruppamento per filo in una funzione pura",
  "detail":"forge://artifact/xyz", "status":"info"}'
```

## Dove si vede

- **Dashboard 3D** (`:8099/dashboard` → tab **Code**): una riga per filo con
  partecipanti, numero di messaggi ed esito; aprendola, i messaggi in ordine.
- **Dashboard del CP** (`:8085/dashboard` → Log Viewer → **Code**): i tre tipi
  uniti in un flusso solo; il `TraceID` è nella riga espansa di ogni evento.

## I limiti, dichiarati

- **Nessuna macchina a stati.** Niente impedisce "approvato ma mai applicato" o
  una doppia applicazione: il filo è **narrativo, non autoritativo**. L'autorità
  resta `git` + il gate.
- **Nessuna applicazione automatica.** Il diff resta nel Forge; lo applica il gate
  del nightly o una persona.
- **Nessun budget di conversazione.** Due modelli che si rispondono possono andare
  avanti a lungo: `REQUEST_DEADLINE_S` vale per una richiesta, non per un dialogo.
- **`/logs` e `/logs/add` non sono autenticati.** Chi raggiunge la 8085 legge le
  conversazioni e può scriverci. Per eventi di sistema è una cosa; per un canale
  dove si scrive codice è la prima cosa da mettere dietro al login.
- **`/logs/clear` cancella anche le conversazioni.** Nessuna retention automatica
  (quindi non evaporano da sole), ma un "pulisci log" se le porta via.

## La regola che tiene insieme tutto

Il canale trasporta **proposte, non modifiche**. Chi scrive nel repo resta uno
solo: una macchina designata, o la persona al merge. Due agenti che scrivono lo
stesso repo senza coordinatore sono il modo più rapido per perdere lavoro — è
successo davvero, e il recupero è costato un pomeriggio di merge a mano.
