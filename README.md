# HyperSpace AGI 1.04 — HIP (HyperSpace Intent Protocol)

HyperSpace is a coordination protocol that lets independent AI runtimes cooperate across heterogeneous infrastructure — not another chat app, but the layer that sits above individual agent runtimes and routes intents to the workers best suited to handle them.

The inference engine (Ollama, LM Studio, or any OpenAI-compatible backend) runs wherever you point it — natively on a host for direct GPU access, or in Docker via the optional GPU profiles. HyperSpace itself stays protocol-first: discovery, capability advertisement, intent routing, worker negotiation, and connector fabric are the core.

## Architectural Positioning

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

## What is in this repo

- `registry/` — service discovery, node registry, public landing/dashboard.
- `control-plane/` — orchestration, OpenAI-compatible API, tool calling loop, connector fabric, dashboard, task management.
- `node/` — agent worker runtime (ECDSA identity, PEX, `/execute`).
- `memory-graph/` — exports control-plane memory to the Obsidian vault; note titling is routed through the control-plane task queue (`/task/create` + `/task/assign`), reusing the same node scoring as any other task.
- `obsidian/` — Obsidian in the browser (KasmVNC) for browsing the memory vault.
- `federation-gateway/` — the only component meant to be exposed publicly for confederated control planes; forwards solely the whitelisted `/federate/execute` and `/federation/identity` routes to the internal control-plane.
- `infra-ui/` — real-time dashboard bridge (SSE, log viewer, mesh topology).
- `authority/`, `worker/` — trust/seed services and execution workers from the earlier multi-worker layout (`docker-compose-2full.yml`).
- `web-node/` — browser-side node subtree for lightweight clients.
- `shared/` — shared models, events, identity, database helpers, registry client.
- `docs/` — architecture and deployment notes.
- `data/` — mounted volumes (Obsidian vault, SearXNG config, node data).

## Implementation Goals for 1.04
- Intent schema (HIP)
- Capability advertisement
- Runtime adapter API
- Intent Router
- Worker negotiation
- Browser Worker integration

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
git clone https://github.com/opodark/opodark-hyperspace-agi-1.03.git
cd opodark-hyperspace-agi-1.03
cp .env.example .env   # or .env.mac / .env.ubuntu / .env.windows depending on host

./setup.sh   # macOS / Linux
# or
.\setup.ps1  # Windows

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
| Control Plane Dashboard | http://localhost:8085/dashboard |
| Infra-UI Bridge         | http://localhost:8099           |
| Registry                | http://localhost:8086/nodes     |
| Node 1                  | http://localhost:8081/status    |
| Memory Graph            | http://localhost:8090/status    |
| Obsidian GUI             | http://localhost:8091           |
| SearXNG                 | http://localhost:8092           |
| Federation Gateway       | http://localhost:8095           |

On Windows, use `docker-compose.windows.yml` instead (see comments at the top of that file for the `.env.windows` → `.env` copy step it expects).

**Development** (Node/TS side of the control plane — HIP intent router, not the Python `main.py` service that actually ships in the containers):
```bash
npm run setup
npx ts-node control-plane/index.ts
```

## Product Principle

HyperSpace is not another chat app.
HyperSpace is the coordination protocol that lets independent AI runtimes cooperate across heterogeneous infrastructures.

## Suggested next steps

1. Wire the browser node into the control plane registry.
2. Split docs so the architecture of `Enterprise Local` and `Public Hub` is explicit.
3. Decide whether the browser node should ship as a web app, extension, or both.
4. Reconcile the Node/TS HIP skeleton (`control-plane/index.ts`) with the Python `main.py` service that the Dockerfiles actually build, so there is a single source of truth for the control plane.

## Notes

This repository is the 1.04 evolution of the HyperSpace stack, layering the HyperSpace Intent Protocol (HIP) on top of the working 1.02/1.03 mesh (registry, control-plane, node workers, memory graph, Obsidian, SearXNG), with Open WebUI as the default interface.
