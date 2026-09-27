# Recupero runtime e memoria

Il control plane usa `SQLITE_JOURNAL_MODE=DELETE` per default, commit sincronizzati
e accessi serializzati nel processo. Questo evita il file shared-memory di WAL
sui bind mount di Docker Desktop. `WAL` rimane selezionabile su filesystem che
supportano correttamente mmap e locking. I crash nativi producono una traccia
Python nei log grazie a faulthandler.

Entrambi i profili Compose impostano `MEMORY_FILE=/app/data/memory.json.gz`,
salvo override. La coda `memory-outbox.jsonl` sta accanto al mirror e condivide
il volume persistente. Prima di ricreare un container precedente, fermare il
control plane e copiare `/app/memory.json.gz` e `/app/memory-outbox.jsonl`
dentro il volume. Conservare anche una copia di backup; non sovrascrivere
un mirror o una coda già esistenti senza confrontarli.

`/health` è un controllo locale: conta le voci del mirror e mostra `memory_pending`
senza interrogare Hermes. Lo stato del bridge remoto resta in `/memory/stats`.
Un timeout di connessione a Hermes non viene ripetuto tre volte. Dopo un errore
di trasporto il client evita nuovi tentativi per 15 secondi, poi riprova; la
memoria resta nel mirror e nella coda. Il timeout di connessione rimane breve
anche per le importazioni, che hanno un tempo di lettura più lungo. Quando il nodo
Hermes è spento, mantenere la coda persistente e ripristinare quel servizio prima
di trasferire l'autorità della memoria su un altro host.

Le API `/config/env`, `/config/advanced` e `/config/secret/rotate` richiedono
`NETWORK_ADMIN_TOKEN` (almeno 32 caratteri), header `X-Hyperspace-Network-Token`.
La scheda Setup consente di inserire lo stesso token usato da Network e lo
conserva nel sessionStorage. Non esporre direttamente il CP su Internet:
le altre superfici interne richiedono ancora una rete fidata; per l'accesso
pubblico utilizzare il federation gateway e la sua whitelist.

LM Studio/MLX usano il protocollo OpenAI anche con modelli Qwen. Per disabilitare
esplicitamente il percorso Ollama nativo usare `NATIVE_CHAT_FALLBACK_MODELS=off`.
Vuoto mantiene i pattern predefiniti per un singolo endpoint Ollama; una lista
di endpoint misti usa il protocollo OpenAI. Il catalogo include anche i modelli
diretti senza inventare nodi fissabili nel selettore. Per le richieste non-stream
il fallback prova l'inferenza diretta prima di federazione e OmniRoute, così un
modello locale disponibile non aspetta il timeout di un servizio remoto.

Controlli: `npm test` alla radice esegue dashboard e web node; la suite Python è
`PYTHONPATH=. python -m unittest discover -s tests -p 'test_*.py'`. La CI include
anche `bash -n` sugli script tracciati.
