# HyperSpace-AGI — Vision

## Missione

HyperSpace-AGI è un **runtime operativo per agenti IA locali e distribuiti**. Coordina modelli, strumenti e nodi eterogenei per svolgere lavoro reale di sviluppo, automazione e gestione della conoscenza, con **memoria verificabile e autonomia governata da policy**.

La piattaforma assegna agli agenti ruoli, contesto e capability, instrada il lavoro secondo le risorse disponibili e rende osservabili esecuzioni e risultati. L'inferenza locale con Ollama resta il riferimento; nodi browser, servizi esterni e runtime agentici possono contribuire attraverso interfacce e confini operativi espliciti.

## Obiettivo di prodotto

Un **Primary Brain** sempre acceso costituisce il centro operativo: coordina inferenza, task, strumenti, stato e accesso alla memoria. Il profilo Windows su Asus resta il punto di partenza da consolidare, con accesso remoto tramite rete privata e Tailscale nella fase transitoria.

Il sistema deve permettere a persone e agenti di lavorare attraverso interfacce web, canali esterni e integrazioni con sistemi aziendali. Le dashboard rendono visibili risorse, code, risultati e controlli; i canali mantengono un'identità agentica dichiarata e policy condivise con il control plane.

Gli agenti possono operare sull'host attraverso capability abilitate esplicitamente, con limiti, audit e conferme per le operazioni distruttive. Lo sviluppo autonomo del runtime avviene in workspace isolati: produce modifiche e risultati di test da revisionare prima di applicarli al checkout operativo. I collaboratori esterni, come Claude Code e Codex, partecipano al ciclo di sviluppo secondo le istruzioni del progetto e le autorizzazioni dell'operatore.

## Principi architetturali

1. **Runtime prima delle interfacce** — routing, code, gestione delle risorse, memoria, osservabilità e recovery sono il fondamento del prodotto. Le interfacce devono rendere queste capacità utilizzabili e controllabili.
2. **Modelli specializzati e risorse esplicite** — favorire modelli piccoli e medi cooperativi, scegliendo il modello in funzione del compito, delle capability e dei limiti reali di memoria e calcolo del nodo. La coppia generalista/coder è una configurazione di riferimento, non un vincolo permanente.
3. **Nodi eterogenei, profili chiari** — isolare le differenze tra host Windows, Linux e altri profili in configurazione e bootstrap. Includere worker browser per i carichi compatibili, senza richiedere Docker a ogni partecipante.
4. **Interoperabilità attraverso contratti espliciti** — Ollama resta il backend locale di riferimento; API OpenAI-compatible, MCP e connector permettono di integrare strumenti e runtime esterni. Routing e policy restano responsabilità del control plane.
5. **Autonomia delimitata e verificabile** — ogni capability operativa deve avere scope, autorizzazioni, limiti e tracciabilità. L'accesso all'host e ai sistemi esterni passa attraverso superfici controllate.
6. **Sviluppo isolato e revisione umana** — gli agenti di sviluppo interni lavorano nel Code Sandbox e consegnano diff e verifiche. Applicazione al repository operativo, pubblicazione e deployment restano passaggi distinti e autorizzati.
7. **Memoria con provenienza** — distinguere fatti, stato operativo e ipotesi. I risultati dei Dream restano candidati fino a revisione e promozione esplicita; fonti e decisioni devono essere ricostruibili.
8. **Estensione controllata delle capacità** — Tool & Skill Forge prepara draft da validare e approvare esplicitamente. Generare uno strumento o una skill non equivale ad abilitarne l'esecuzione o pubblicarlo.

## Memoria ed evoluzione del sistema

La memoria deve sostenere continuità tra sessioni, progetti e agenti, mantenendo chiaro ciò che è autorevole e ciò che è ancora da verificare. Dream e sperimentazione di sviluppo consentono di proporre nuova conoscenza e miglioramenti nei periodi disponibili, senza promuoverli o applicarli automaticamente.

La direzione architetturale definita per la memoria cognitiva è **Hermes come backend unico**, con HyperSpace responsabile di adapter, policy, review e viste derivate, senza un archivio cognitivo parallelo in dual-write. **La migrazione è pianificata e non va considerata completata**; contratto e responsabilità sono descritti in [docs/hermes.md](docs/hermes.md).

## Capacità attuali e direzione futura

Il repository comprende già capacità di orchestrazione, connector enterprise, canali esterni, accesso controllato all'host, Code Sandbox e fondazioni per Dream review e Tool & Skill Forge. Il percorso web node include registrazione e scambio di task con il control plane, inferenza locale nel browser tramite WebGPU e una dashboard dedicata. Disponibilità effettiva e attivazione dipendono da configurazione, risorse e profilo di deployment.

Queste capacità costituiscono una base distribuita, ma non implicano una mesh completa con sincronizzazione dello stato e failover generalizzato. La mesh completa e l'**HyperSpace Intent Protocol (HIP)** restano la direzione di lungo periodo per coordinare runtime indipendenti attraverso contratti comuni di capability ed esecuzione.

## Lessico

| Termine | Significato |
|---|---|
| **Runtime** | Il sistema che coordina processi, modelli, task, strumenti, memoria e recovery. |
| **Control plane** | Il componente che governa routing, policy, integrazioni e tracciamento delle esecuzioni. |
| **Node** | Un partecipante che offre risorse o capacità di esecuzione, su host oppure nel browser. |
| **Profile** | Configurazione di runtime e deployment per un tipo di host o nodo. |
| **Agent role** | Ruolo funzionale di un agente, come orchestrator, planner, coder, memory-agent o ops-agent. |
| **Capability** | Capacità dichiarata e soggetta a policy, utilizzabile per assegnare o eseguire lavoro. |
| **Provider** | Backend di inferenza LLM; Ollama è il riferimento locale. |
| **Memory layer** | Accesso alla memoria con provenienza e regole di revisione, distinto da log e stato operativo temporaneo. |
| **Primary Brain** | Il nodo principale sempre acceso che coordina il sistema e l'accesso alle sue risorse. |
| **Web node** | Partecipante browser che offre capacità compatibili con le risorse e i vincoli del browser. |
| **Code Sandbox** | Ambiente isolato per sviluppo e test agentici, con risultati e diff destinati alla review. |
| **Dream** | Ciclo di elaborazione in background che produce ipotesi candidate, separate dalla memoria autorevole. |
| **Teammate agent** | Collaboratore esterno al runtime che opera sul progetto secondo istruzioni e autorizzazioni condivise. |

## Non-obiettivi (per ora)

- Costruire un chatbot generalista come unico prodotto: chat e canali sono superfici di accesso al runtime.
- Richiedere una mesh completa per usare il sistema o presentare HIP come architettura già operativa.
- Concedere agli agenti accesso indiscriminato all'host, ai dati o ai sistemi collegati.
- Applicare automaticamente codice generato o trasformare ipotesi Dream in memoria autorevole senza revisione.
- Vincolare il sistema a un singolo modello, a una taglia fissa o a un unico tipo di nodo.

Il dettaglio di fasi, milestone e deliverable è in [ROADMAP.md](ROADMAP.md). Le specifiche operative sono raccolte in [docs/](docs/).
