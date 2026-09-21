# HyperSpace AGI 1.04

HyperSpace-AGI is an **operational runtime for local and distributed AI agents** — not an experimental framework or a single chatbot. It orchestrates specialized agents (memory, tools, policy, roles) on real dev/automation/knowledge workloads, with local inference (Ollama), Docker isolation, and direct human-agent collaboration on the project filesystem.

Mission, lexicon and architectural principles are fixed in [VISION.md](VISION.md); phases, milestones and deliverables are in [ROADMAP.md](ROADMAP.md) — read those first, this README covers what's in the repo and how to run it.

**Current phase**: a Windows **Primary Brain** node — always-on, hosts the local LLM runtimes, routes tasks, keeps state/memory, and exposes secure remote access over a private network (Tailscale, transitional — see [docs/tailscale-mesh-setup.md](docs/tailscale-mesh-setup.md)). Full multi-node mesh and the **HyperSpace Intent Protocol (HIP)** below are the long-term direction (Phase 4 in the roadmap), not the current architecture.

## Long-term direction (Phase 4 — not active yet)

```text
                AI Model
                    │
          Agent Runtime Layer
                    │
        ==========================
         HyperSpace Intent Protocol (HIP)
        ==========================
                    │
        HyperSpace Control Plane
                    │
     Distributed Worker Network
```

See the [HIP section of ROADMAP.md](ROADMAP.md#visione-a-lungo-termine--hyperspace-intent-protocol-hip) for the full design (intent schema, capability advertisement, intent router, runtime independence).

## What is in this repo

- `registry/` — service discovery, node registry, public landing/dashboard.
- `control-plane/` — orchestration, OpenAI-compatible API, tool calling loop, connector fabric, dashboard, task management.
- Tool & Skill Forge — dashboard workflow for generating, editing, validating and explicitly approving inert tool or skill drafts; see [`docs/tool-skill-forge.md`](docs/tool-skill-forge.md).
- Dev Sandbox and development tooling — one HyperSpace workflow for editing, tests, debugging, security review and future laboratory pentest workers, with execution isolated by runtime; see [`docs/development-tooling-architecture.md`](docs/development-tooling-architecture.md).
- `node/` — agent worker runtime (ECDSA identity, PEX, `/execute`).
- `memory-graph/` — exports control-plane memory to the Obsidian vault; note titling is routed through the control-plane task queue (`/task/create` + `/task/assign`), reusing the same node scoring as any other task.
- `obsidian/` — Obsidian in the browser (KasmVNC) for browsing the memory vault.
- `federation-gateway/` — the only component meant to be exposed publicly for confederated control planes; forwards solely the whitelisted `/federate/execute` and `/federation/identity` routes to the internal control-plane.
- `infra-ui/` — real-time dashboard bridge (SSE, log viewer, mesh topology).
- `authority/`, `worker/` — trust/seed services and execution workers from the earlier multi-worker layout (`legacy/docker-compose-2full.yml`).
- `web-node/` — browser-side node subtree for lightweight clients.
- `shared/` — shared models, events, identity, database helpers, registry client.
- `docs/` — architecture and deployment notes.
- `data/` — mounted volumes (Obsidian vault, SearXNG config, node data).

## Current focus (Phase 1 — Windows Primary Brain)
- Stabilize the Asus Windows host as the primary node.
- `.env`, `docker-compose.windows.yml`, persistent paths, Ollama via `host.docker.internal`.
- Tailscale as secure remote access to the node and its internal services.
- Validate the 14B model pair (generalist + coder) under real RAM/latency/concurrency.

Full stream-by-stream detail (runtime core, agent framework, memory layer, ops) is in [ROADMAP.md](ROADMAP.md).

## Connector fabric

The control plane includes an enterprise connector layer for agents, focused on:

- GitHub
- Google Workspace
- Microsoft 365 / Office 365

These connectors are meant to turn HyperSpace into an **Enterprise Connector Fabric for Agents**, not just another agent runner.

## Network profiles

### Enterprise Local

Use this mode when the deployment must stay inside a private organization boundary.

- Seed nodes stay internal.
- Discovery is constrained to the tenant/network.
- Suitable for private corporate fleets.
- Recommended for controlled, auditable deployments.

### Public Hub

Use this mode when you want a public landing hub that distributes the free browser node.

- Landing page acts as the bootstrap point.
- Browser nodes join explicitly.
- Lowest-risk tasks can be routed to lightweight web nodes.
- Good for demos, adoption, and community propagation.

## Browser / web node

The browser node is a first-class path for weaker devices that cannot run Docker or heavier local services.

Planned responsibilities:

- register capabilities from the browser tab / extension,
- execute simple tasks such as translation, embeddings, summarization, and moderation,
- provide lightweight worker capacity to the mesh,
- keep the control plane reachable from a web-first client.

## Quick Start

```bash
git clone https://github.com/opodark/hyperspace-agi-1.04.git
cd hyperspace-agi-1.04
cp .env.example .env   # or .env.mac / .env.ubuntu / .env.windows depending on host

./setup.sh   # macOS / Linux
# or
.\setup.ps1  # Windows

.\scripts\start.ps1  # Windows - one command for the whole stack
# containers, wait, verdict (services, channels, identity). Also:
#   .\scripts\start.ps1 -Profile nvidia   # GPU profile for a dockerized Ollama
#   .\scripts\start.ps1 -Check            # do not start anything: report what runs
#   .\scripts\start.ps1 -Driver           # ...and start the channel driver too
#   .\scripts\start.ps1 -Stop             # stop everything (volumes stay)
#
# quick commands from the terminal (status, mode, logs, memory, dreams, host):
#   python scripts/hs.py status
#   python scripts/hs.py mode off          # make the room bot stop talking

# or directly, once .env is in place:
docker compose up -d --build
```

GPU profiles for a dockerized Ollama instead of a native one (pick one, optional):

```bash
docker compose --profile cpu up -d
docker compose --profile nvidia up -d
docker compose --profile amd up -d
docker compose --profile intel up -d
docker compose --profile vulkan up -d
```

After boot, services are reachable on `localhost`:

| Service                | URL                            |
| ----------------------- | ------------------------------- |
| Open WebUI              | http://localhost:3000           |
| Control Plane Dashboard | http://localhost:8085/dashboard (`8088` compatibility alias on Windows) |
| Infra-UI Bridge         | http://localhost:8099           |
| Registry                | http://localhost:8086/nodes     |
| Node 1                  | http://localhost:8081/status    |
| Memory Graph            | http://localhost:8090/status    |
| Obsidian GUI             | http://localhost:8091           |
| SearXNG                 | http://localhost:8092           |
| Federation Gateway       | http://localhost:8095           |

On Windows, use `docker-compose.windows.yml` instead (see comments at the top of that file for the `.env.windows` → `.env` copy step it expects).

The optional browser IDE used by the Tool & Skill Forge can be started separately:

```powershell
docker compose -f docker-compose.windows.yml --profile ide up -d hyperspace-ide
```

**Development** (Node/TS side of the control plane — HIP intent router, not the Python `main.py` service that actually ships in the containers):
```bash
npm run setup
npx ts-node control-plane/index.ts
```

## Product Principle

HyperSpace-AGI is not another chat app. It's an operational runtime: process management, model routing, queues, memory, observability and recovery come before dashboards and UX (see [VISION.md](VISION.md) principle #1). The Intent Protocol above is where the runtime is headed once multi-node mesh becomes the active phase — it does not describe today's architecture.

## Suggested next steps

1. Wire the browser node into the control plane registry.
2. Split docs so the architecture of `Enterprise Local` and `Public Hub` is explicit.
3. Decide whether the browser node should ship as a web app, extension, or both.
4. Reconcile the Node/TS HIP skeleton (`control-plane/index.ts`) with the Python `main.py` service that the Dockerfiles actually build, so there is a single source of truth for the control plane.

See [ROADMAP.md](ROADMAP.md#deliverable-prioritari) for the full prioritized deliverable list.

## Notes

This repository is the 1.04 evolution of the HyperSpace stack: the working 1.02/1.03 mesh (registry, control-plane, node workers, memory graph, Obsidian, SearXNG) repositioned as an operational agent runtime, with Open WebUI as the default interface and the Intent Protocol (HIP) as long-term direction rather than current architecture.

## Licenza

**Apache License 2.0** — testo integrale in [LICENSE](LICENSE).

Copyright 2026 the HyperSpace-AGI authors — opodark (Alberto Raul Marinoni),
cips, e altri contributori. Vedi [NOTICE](NOTICE).

- Componenti di terze parti e loro licenze: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)
- Perche' questa licenza, cosa non copre, e cosa e' stato rimandato (lo split
  open-core): [docs/licensing.md](docs/licensing.md)

Ogni file sorgente porta in testa `SPDX-License-Identifier: Apache-2.0`, e
`tests/test_licensing.py` lo verifica: la convenzione e' imposta, non ricordata.
