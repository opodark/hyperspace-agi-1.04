# Le baseline HTTP del control-plane

Una baseline fa tre cose: avvia il control-plane, gli invia un po' di richieste e
confronta le risposte con un file di riferimento in `tests/fixtures/`. Se una
risposta cambia, la baseline si accende.

I file di riferimento sono nel repo. **Gli script che li producono no**, e fino a
poco fa stavano in una cartella temporanea che il sistema svuota fra una sessione e
l'altra: chi non li trovava doveva ricostruire a memoria l'ambiente di ogni
dominio, e la tentazione era saltare la verifica.

## Uso

    tests/harness/mesh.sh               scrive la fixture
    tests/harness/mesh.sh --confronta   confronta con quella già scritta

I cinque domini:

| script | rotte coperte | cosa serve perché funzioni |
|---|---|---|
| `mesh.sh` | registro, alias, topologia, pesi, metriche, nodi web | niente |
| `canali.sh` | stato e ingestion dei canali | un cliente configurato |
| `instagram.sh` | webhook, VIP, outbox risposte | i due segreti, da **entrambi** i lati |
| `immagini.sh` | coda immagini e contratto col ponte | un cliente configurato |
| `chat.sh` | il contratto OpenAI | un modello finto e `TOOL_CAPABLE_MODELS` |

`_comune.sh` contiene le tre operazioni uguali per tutti (avvio, attesa,
esecuzione) e le variabili che il server riceve: file temporanei, token di
amministrazione, cliente dei canali.

## La regola che le ha fatte fallire

Una baseline che passa senza provare niente è peggio di una che non esiste,
perché il giorno dopo le estrazioni sembra tutto a posto.

Le tre trappole incontrate, in ordine di costo:

- **Token di amministrazione corto.** Il server lo rifiuta e ogni route protetta
  risponde `401`: la baseline è verde e non ha toccato una riga di codice utile.
  `_comune.sh` ne genera uno da 40 caratteri a caso.
- **Segreti solo da un lato.** La firma del webhook è calcolata con lo stesso
  segreto dal server e dallo script. Se i due non coincidono, quattro richieste su
  dieci rispondono `401`/`403` — e le quattro "diverse" dipendono l'una dall'altra,
  perché il VIP e l'outbox non si popolano se il webhook è stato respinto.
- **`TOOL_CAPABLE_MODELS` assente.** Il control-plane toglie i tool dalla richiesta
  quando il modello non è dichiarato capace. Il caso `con_tools` risponde `200` lo
  stesso, ma senza `tool_calls`: la verifica del percorso dei tool è verde e falsa.

C'è anche il caso più subdolo: una richiesta verso una URL che non esiste risponde
`404` con una pagina HTML, che è stabile quanto un `200`. La baseline era verde da
prima che la rotta venisse spostata, e non lo avrebbe notato. Le rotte citate
nelle baseline sono quindi tutte esistenti, e vale la pena controllarlo quando se ne
aggiunge una.

## Quando si aggiunge una rotta a un dominio

1. Aggiungerla a `tests/<dominio>_baseline.py`, con una chiave che descriva il
   caso (`web_enqueue_nodo_inesistente`, non `test_3`).
2. Se la risposta ha un orario, un contatore o un identificatore derivato, metterlo
   in `VOLATILI` in quello script: la baseline confronta il testo esatto.
3. Rigenerare la fixture **sulla versione di prima dell'estrazione**, non dopo.
   `git stash` delle modifiche, harness, `git stash pop` — altrimenti si registra
   come "atteso" il comportamento rotto che si sta per correggere, e la verifica
   non accorgersi di niente.
4. Se la risposta è un `500`, la baseline rifiuta di scrivere la fixture. Un `500`
   non è un comportamento, è un crash.
