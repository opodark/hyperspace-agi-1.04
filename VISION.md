# HyperSpace-AGI — Vision

## Missione

HyperSpace-AGI è un **runtime operativo per agenti IA locali e distribuiti**, non un framework sperimentale né un singolo chatbot.

La piattaforma orchestra agenti specializzati — assegna loro memoria, tool, policy e ruoli — e li fa collaborare su workload reali di sviluppo, automazione e knowledge work, con inferenza locale (Ollama), isolamento via Docker e collaborazione uomo-agente diretta sul filesystem di progetto.

## Obiettivo di prodotto

Un **Primary Brain** sempre acceso, nodo principale del sistema: ospita i runtime LLM locali, instrada i task verso gli agenti giusti, conserva stato e memoria, e abilita accesso remoto sicuro tramite una rete privata (Tailscale nella fase transitoria).

Il nodo deve supportare sia agenti autonomi locali sia colleghi agentici esterni al runtime — come Claude Code su Windows — che operano direttamente sui file del repository seguendo regole di progetto e confini operativi condivisi.

## Principi architetturali

1. **Runtime prima delle interfacce** — gestione processi, routing modelli, code, memoria, osservabilità e recovery vengono prima di dashboard e UX.
2. **Modelli piccoli ma cooperativi** — coppie di modelli specializzati (es. un generalista e un coder) invece di dipendere da un solo modello grande.
3. **Host-agnostic con profili espliciti** — Windows, Linux e futuri nodi edge supportati tramite profili di runtime chiari, con differenze isolate in path, networking, servizi host e bootstrap.
4. **API compatibility come standard interno** — Ollama come backend standard del runtime, sfruttando la compatibilità OpenAI per semplificare integrazione agenti, tool e componenti esterni.
5. **Teaming uomo-agente** — gli agenti non sono solo runtime interni: agenti-collaboratori come Claude Code leggono istruzioni dal progetto, modificano file locali e partecipano al ciclo di sviluppo in modo controllato.

## Lessico

| Termine | Significato |
|---|---|
| **Runtime** | Il sistema operativo di HyperSpace-AGI: processi, routing, code, memoria, recovery. |
| **Node** | Una macchina che esegue il runtime (es. Primary Brain Windows/Asus). |
| **Profile** | Configurazione host-specifica (path, networking, bootstrap) per un tipo di nodo (Windows, Linux, edge). |
| **Agent role** | Ruolo funzionale di un agente (orchestrator, planner, coder, repo-analyst, memory-agent, ops-agent). |
| **Provider** | Backend di inferenza LLM (Ollama come standard interno). |
| **Memory layer** | Il sottosistema che separa memoria operativa breve, di progetto e persistente long-term. |
| **Primary Brain** | Il nodo principale sempre acceso che orchestra runtime, routing e memoria. |
| **Teammate agent** | Agente-collaboratore esterno al runtime (es. Claude Code) che opera sul repository sotto policy condivise. |

## Non-obiettivi (per ora)

- Non è (ancora) un chatbot prodotto per utenti finali.
- Non richiede una mesh multi-nodo completa nella fase attuale — Tailscale copre l'accesso remoto transitorio.
- Non dipende da un singolo modello grande: la scommessa architetturale è su coppie di modelli piccoli/medi cooperativi.

Il dettaglio di fasi, milestone e deliverable è in [ROADMAP.md](ROADMAP.md).
