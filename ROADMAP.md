# HyperSpace-AGI Roadmap — Runtime Agenti IA

Missione, lessico e principi architetturali sono fissati in [VISION.md](VISION.md). Questo documento copre stream di lavoro, fasi, deliverable e decisioni operative.

## Stream di lavoro

### Runtime core
- Definire il contratto tra orchestratore, agenti e provider LLM.
- Introdurre un layer di routing per ruoli modello: general, coder, planner, memory, tool-agent.
- Formalizzare code richieste, timeout, retry policy e fallback modello.
- Separare configurazione runtime, configurazione agenti e segreti operativi tramite `.env` e profili Compose.

### Agent framework
- Standardizzare il ciclo agente: goal, context load, planning, tool execution, memory writeback, result handoff.
- Distinguere agenti persistenti, agenti ephemeral e agenti delegati a task singolo.
- Introdurre subagent policy e capability manifest per limitare scope e permessi operativi.
- Definire ruoli iniziali: orchestrator, planner, coder, repo-analyst, memory-agent, ops-agent.

### Local inference layer
- Assumere Ollama come primo target supportato per Windows e Linux.
- Mantenere una coppia di modelli di riferimento nella fascia 14B per hardware consumer, con un generalista uncensored e un coder dedicato.
- Esporre sia API native Ollama sia endpoint OpenAI-compatible per compatibilità con tool terzi.
- Introdurre benchmark interni per latenza, RAM, parallelismo e stabilità su macchine 48 GB RAM e oltre.

### Memory layer
- Separare memoria operativa breve, memoria di progetto e memoria persistente long-term.
- Definire formato comune per note agente, decision log, task state e artifact metadata.
- Prevedere policy di garbage collection e summarization per evitare deriva del contesto.
- Collegare la memoria agli agenti come capability esplicita, non come side effect implicito.

### Ops e deployment
- Consolidare il profilo **Windows Primary Brain** con Ollama su host, HyperSpace in Docker e accesso remoto via Tailscale.
- Preparare il profilo **Linux Primary Brain** come target successivo, con path nativi, systemd e maggiore efficienza operativa.
- Standardizzare healthcheck, logging, restart policy, backup e recovery del nodo principale.
- Formalizzare bootstrap scripts per setup rapido di nuovi nodi.

### Human-agent collaboration
- Introdurre directory `.claude/` come punto di controllo per istruzioni, memoria di progetto e policy per Claude Code.
- Definire convenzioni condivise per editing file, naming, commit, changelog e modifiche infrastrutturali.
- Trattare Claude Code come teammate di sviluppo, non come provider runtime del cervello locale.
- Documentare chiaramente i confini: cosa può essere modificato in autonomia, cosa richiede approvazione, cosa è vietato toccare.

## Roadmap per fasi

### Fase 0 — Riallineamento strategico
- Aggiornare documentazione e naming del progetto: da framework agentico generico a runtime agenti IA.
- Definire missione, scope e casi d'uso primari.
- Allineare il repository ai nuovi concetti: runtime, node, profile, agent role, provider, memory layer.
- Redigere il manifesto operativo del Primary Brain.

### Fase 1 — Windows Primary Brain
- Stabilizzare il setup Asus con Windows come nodo principale provvisorio.
- Fissare `.env`, `docker-compose.yml`, path persistenti e accesso a Ollama host tramite `host.docker.internal`.
- Integrare Tailscale come accesso remoto sicuro al nodo e ai servizi interni.
- Validare la coppia modelli 14B e misurare comportamento reale in RAM, latenza e parallelismo.

### Fase 2 — Runtime orchestration
- Introdurre registry agenti e schema capability-based.
- Implementare task dispatcher con routing per tipo di lavoro e modello.
- Aggiungere memoria operativa, log strutturati e tracciamento esecuzioni.
- Definire protocolli di handoff tra agenti.

### Fase 3 — Tooling e collaboration layer
- Integrare ufficialmente Claude Code come agente-collaboratore di sviluppo sul repository Windows.
- Standardizzare `.claude/CLAUDE.md`, policy di editing e checklist per modifiche critiche.
- Abilitare workflow di sviluppo assistito su file locali, review diff e task decomposition.
- Separare chiaramente runtime production e workspace di sviluppo.

### Fase 4 — Multi-node e mesh
- Passare da accesso singolo via Tailscale a topologia multi-nodo più completa quando il branch mesh sarà pronto.
- Introdurre ruoli nodo: primary brain, worker node, memory node, coding node, edge node.
- Definire sync di stato, delega task inter-node e failover di servizi critici.
- Formalizzare i contratti di rete e autenticazione tra nodi.
- Questa fase è il punto di innesto per il protocollo **HyperSpace Intent Protocol (HIP)** — vedi [Visione a lungo termine](#visione-a-lungo-termine--hyperspace-intent-protocol-hip) più sotto.

### Fase 5 — Linux production profile
- Portare il Primary Brain su Linux quando l'hardware e la direzione finale saranno confermati.
- Tradurre il profilo Windows in deployment Linux con maggiore controllo su processi, filesystem e networking.
- Ottimizzare inferenza locale, startup, persistenza e monitoraggio.
- Rendere Linux il target production-first, lasciando Windows come profilo supportato per bootstrap e transizione.

## Deliverable prioritari

| Priorità | Deliverable | Scopo | Stato |
|---|---|---|---|
| P0 | `VISION.md` | Fissare missione, lessico e obiettivi del runtime | ✅ fatto |
| P0 | `ROADMAP.md` | Rendere esplicite fasi, milestone e stream di lavoro | ✅ fatto |
| P0 | `.env.windows` | Profilo ufficiale Windows Primary Brain | ✅ fatto |
| P0 | `docker-compose.windows.yml` | Stack standard per nodo Asus Windows | ✅ fatto |
| P1 | `.claude/CLAUDE.md` | Regole operative per Claude Code teammate | da fare |
| P1 | `agents/registry.yml` | Catalogo ruoli, modelli e capability agenti | da fare |
| P1 | `profiles/linux-primary/` | Base del futuro target Linux production | da fare |
| P2 | `benchmarks/` | Misure comparabili su modelli, RAM e latenza | da fare |
| P2 | `memory/schema/` | Contratti per memoria breve e persistente | da fare |
| P2 | `ops/bootstrap/` | Script di setup e recovery nodo | da fare |

## Decisioni immediate
- Mantenere **Windows sull'Asus** come piattaforma primaria temporanea.
- Usare **Ollama** come runtime modelli locale standard del progetto.
- Adottare una coppia iniziale di modelli 14B: un generalista uncensored e un coder dedicato.
- Usare **Tailscale** come soluzione di accesso remoto e collegamento sicuro in attesa della mesh completa.
- Inserire **Claude Code con Sonnet 5** come teammate di sviluppo che opera sui file locali del repository sotto policy di progetto.

## Rischi principali
- Ambiguità tra "framework di sviluppo" e "runtime operativo", con conseguente dispersione architetturale.
- Eccessiva dipendenza da un singolo host Windows prima della formalizzazione del profilo Linux.
- Crescita non controllata della complessità agentica senza capability boundaries e regole di handoff.
- Assenza iniziale di benchmark strutturati per capire il limite reale dell'hardware consumer.
- Confusione tra agenti runtime interni e agenti-collaboratori esterni al runtime.

## Criteri di successo
- Un nodo Primary Brain avviabile in modo ripetibile su Windows con setup documentato.
- Almeno due agenti specializzati funzionanti con routing modello distinto.
- Memoria e logging sufficienti a ricostruire una run end-to-end.
- Workflow collaborativo stabile tra sviluppo umano, Claude Code e runtime locale.
- Profilo Linux già disegnato, anche se non ancora target primario di produzione.

## Sintesi operativa

La roadmap posiziona HyperSpace-AGI come **runtime modulare di agenti IA locali e distribuiti**, non come semplice collezione di script o demo di agenti. Il Primary Brain Windows su Asus è il primo nodo operativo, mentre Ollama, Tailscale e Claude Code costituiscono il primo stack collaborativo concreto da stabilizzare prima dell'evoluzione verso mesh completa e target Linux production-first.

---

## Visione a lungo termine — HyperSpace Intent Protocol (HIP)

Questa sezione era il piano attivo per HyperSpace 1.04 prima del riallineamento a runtime operativo (Fase 0 sopra). Resta la direzione di lungo periodo per la **Fase 4 — Multi-node e mesh**: quando la topologia multi-nodo sarà pronta, HIP è il protocollo candidato per far cooperare runtime agentici eterogenei attraverso il Primary Brain e i nodi worker.

### Vision originale

HyperSpace evolve da piattaforma di agenti distribuiti a protocollo di coordinamento distribuito per agenti IA. Invece di accoppiare gli agenti a un framework specifico, introduce un protocollo aperto che permette a runtime eterogenei di scoprirsi, pubblicizzare capability, negoziare esecuzione e scambiarsi lavoro. L'obiettivo non è sostituire i framework esistenti, ma connetterli.

### Design Philosophy

```
                AI Model
                    │
          Agent Runtime Layer
(OpenClaw, CrewAI, LangGraph, AutoGen...)
                    │
        ==========================
         HyperSpace Intent Protocol
        ==========================
                    │
        HyperSpace Control Plane
                    │
     Distributed Worker Network
```

Ogni runtime resta libero di implementare il proprio reasoning loop; HyperSpace coordina solo l'esecuzione.

### Intent-based execution

Gli agenti pubblicano **Intent** (capability desiderata, non implementazione concreta):

```json
{
  "intent": "translate_document",
  "source_language": "it",
  "target_language": "en",
  "max_latency_ms": 3000,
  "privacy": "private",
  "budget": 0.02
}
```

La rete decide dove eseguire il lavoro.

### Capability Advertisement

Ogni nodo pubblicizza periodicamente le proprie capability:

```json
{
  "node_id": "...",
  "runtime": "OpenClaw",
  "models": ["qwen3-9b", "deepseek-lite"],
  "tools": ["github", "filesystem", "browser"],
  "gpu": true,
  "vram": 8192,
  "ram": 49152,
  "latency_ms": 25
}
```

### Intent Router

Il Control Plane introduce un Intent Router responsabile di: scoprire worker compatibili, valutare policy di esecuzione, stimare costo e latenza, scegliere il nodo ottimale, dispatchare l'esecuzione, raccogliere i risultati.

### Execution Policies (pluggable)

Lowest latency · Lowest cost · Highest accuracy · Local-first · Privacy-first · GPU preferred · CPU only · Browser-first · Enterprise only.

### Runtime Independence

Runtime possibili: OpenClaw, CrewAI, LangGraph, AutoGen, agenti Python custom, Browser Worker. Ogni runtime implementa un adapter HyperSpace leggero.

### Browser Edge Workers

Tab del browser come nodi di esecuzione leggeri (traduzione, summarization, moderazione, embeddings, OCR, inferenza locale WebGPU) — nessun Docker richiesto.

### Enterprise Connector Fabric

L'esecuzione di Intent può puntare a sistemi enterprise tramite connector (GitHub, Microsoft 365, Google Workspace, Jira, GitLab, Slack, REST API), che diventano capability eseguibili invece di integrazioni statiche.

### Future Marketplace (Experimental)

Una versione futura potrebbe introdurre negoziazione decentralizzata delle capability, con i worker che pubblicizzano modelli supportati, latenza attesa, costo stimato, profilo energetico e garanzie di privacy. Nessuna blockchain o criptovaluta richiesta; la logica di marketplace resta opzionale.

### Long-Term Vision

HyperSpace non è un altro framework di agenti: è un layer di orchestrazione e coordinamento che permette a runtime IA indipendenti di cooperare su infrastrutture eterogenee — come HTTP ha standardizzato la comunicazione tra server web, HyperSpace punta a standardizzare l'esecuzione distribuita di intent tra agenti IA.

### Status

Obiettivi di implementazione iniziale (non attivi finché non si entra in Fase 4):

- [ ] Intent schema
- [ ] Capability advertisement
- [ ] Runtime adapter API
- [ ] Intent Router
- [ ] Worker negotiation
- [ ] Browser Worker integration

> Marketplace functionality is considered experimental and will be explored after the protocol stabilizes.
