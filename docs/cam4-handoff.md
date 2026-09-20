# Handoff Windows — chatbot CAM4 e HyperSpace

Conversazione del 20 settembre 2026, iniziata nella sessione sul MacBook Air.

## Richiesta dell'utente

Riprendere il vecchio progetto chatbot CAM4, che si trova **su Windows**, e
valutare l'integrazione in HyperSpace per mettere alla prova memoria, agenti,
connessione e dreams attraverso l'adattamento ai cambiamenti del DOM del sito.
Il percorso del progetto non è ancora noto. Il codice non è stato ancora letto
in questa sessione e nessuna integrazione è stata implementata.

L'utente passa al computer Windows e ha chiesto di salvare la conversazione.

## Proposta discussa, ancora da verificare sul codice

Usare il chatbot come connettore sperimentale con un ciclo osservabile:

1. Rilevare il fallimento di un'azione e raccogliere il frammento DOM necessario,
   escludendo messaggi e dati personali non necessari.
2. Recuperare dalla memoria i precedenti riconoscimenti del controllo.
3. Far proporre a un agente un nuovo modo di individuare l'elemento.
4. Verificare su una copia della pagina che la proposta trovi il controllo
   previsto, prima di considerarla affidabile.
5. Salvare l'adattamento verificato; usare i dreams per analizzare fallimenti
   ricorrenti e proporre miglioramenti da sottoporre a validazione.

Separare osservazioni, ipotesi e adattamenti verificati, mantenendo provenienza
e possibilità di revoca. Un tentativo non deve diventare automaticamente un
ricordo affidabile.

## Stress test proposto

- Pagine salvate e variazioni riproducibili del DOM come primo ambiente di prova.
- Successo dopo una modifica, tempo di recupero, tentativi, token consumati e
  falsi riconoscimenti come metriche.
- Disconnessioni simulate e riavvii, verificando la ripresa senza duplicare azioni.
- Confronto con memoria e dreams attivati/disattivati per misurarne il contributo.
- Successiva fase sul sito in sola osservazione; invio di messaggi da definire
  separatamente con limiti espliciti, non autorizzato da questa proposta.

Questi sono suggerimenti dell'assistente: l'utente non ha ancora confermato
il disegno tecnico né scelto le modalità del test.

## Da fare nella sessione Windows

Individuare e leggere il vecchio progetto, le sue istruzioni e i meccanismi già
implementati per browser, selettori, recupero dagli errori e memoria. Poi mappare
il codice sulle API effettive di HyperSpace e concordare un primo esperimento
piccolo e riproducibile. Evitare di ricostruire componenti già esistenti.

Il lavoro sui connettori HyperSpace è in corso in un'altra sessione: controllare
lo stato Git e coordinare eventuali modifiche ai file condivisi.

## Stato delle modifiche UI appena completate

Commit `02d9486`, già pubblicato su `origin/main`: leggibilità dashboard,
scorciatoie rispettose dei campi di scrittura e dei modificatori Ctrl/Cmd,
collegamento Desktop → Live 3D corretto e test delle scorciatoie.
I container `control-plane` e `bridge` sono stati ricostruiti sul MacBook Air;
questo non aggiorna automaticamente i container Windows.

## Messaggio per riprendere

«Leggi docs/cam4-handoff.md. Il vecchio chatbot CAM4 è su questo Windows:
individuiamolo e leggiamo come si adatta al DOM, poi prepariamo l'integrazione
con HyperSpace come stress test per memoria, agenti, connessione e dreams,
coordinandoci con il lavoro in corso sui connettori.»
