# Integrazione Hermes

Hermes è un runtime esterno candidato a usare HyperSpace come endpoint di
inferenza e come provider di tool. Questa integrazione è indipendente dal
sottosistema dei sogni.

## Stato corrente

Il control-plane espone:

- `/v1` per l'inferenza compatibile con API OpenAI;
- `/mcp` per `initialize`, `tools/list` e `tools/call`.

Questi endpoint definiscono il punto di ingresso, ma il repository non contiene
ancora un adapter dedicato a Hermes né un test end-to-end. La disponibilità MCP
non dimostra da sola che Hermes sia collegato.

## Prossimo traguardo

L'integrazione si considera funzionante quando una configurazione Hermes
riproducibile completa questi passaggi:

1. connessione e inizializzazione MCP;
2. elenco dei tool pubblicati da HyperSpace;
3. chiamata di un tool in sola lettura;
4. richiesta di inferenza attraverso `/v1`;
5. gestione osservabile di timeout, nodo non raggiungibile ed errore del tool.

Prima del test vanno fissati autenticazione, indirizzo Tailscale del
control-plane e policy dei tool concessi a Hermes.
