# docs/

Architecture and deployment notes for HyperSpace-AGI. Mission, lexicon and phases live in the root [VISION.md](../VISION.md) / [ROADMAP.md](../ROADMAP.md) — start there. This folder covers specific subsystems and setup guides.

## Setup / deployment

- [tailscale-mesh-setup.md](tailscale-mesh-setup.md) — cross-machine mesh over Tailscale (current transitional remote-access approach).
- [lan-demo.md](lan-demo.md) — fastest way to demo the mesh to someone on the same physical network, no tunnels.
- [onlyoffice-bridge.md](onlyoffice-bridge.md) — ONLYOFFICE ↔ HyperSpace document connector design (enterprise-network-only track).
- One command to start everything (Windows): `.\scripts\start.ps1` — containers, wait-for-health, then a verdict on services, channels and identity (`-Check`, `-Driver`, `-Stop`; see the root [README](../README.md#quick-start)).
- Quick commands from the terminal: `python scripts/hs.py status|mode|logs|memory|persona|dreams|dream|host|models` — what the stack is doing, and `hs mode off|auto` to command the room bot without touching the browser.
- [host-access.md](host-access.md) — the surface that reaches the machine (host agent, sandbox, argv-only execution) and the staged plan for real shell/terminal access from an external runtime.

## Subsystems

- [dreams.md](dreams.md) — direction and staged roadmap for automatic idle-only memory reflection, including review and promotion rules.
- [hermes.md](hermes.md) — current integration boundary and end-to-end acceptance criteria for Hermes.
- [memory schema](../memory/schema/README.md) — the `hyperspace.memory.v1` entry contract and retention tiers (operative / project / persistent).
- [tool-skill-forge.md](tool-skill-forge.md) — controlled authoring, review and local IDE workflow for inert tool and skill drafts.
- [connectors.md](connectors.md) — enterprise connector fabric (GitHub, Microsoft 365, Google Workspace): contract, configuration, diagnostics and open limits.
- [persona.md](persona.md) — declared identity of the agent: identity document, live self-model, the verifiable disclosure policy ("I am an AI"), and the human-gated identity dream that proposes self-observations at night.
- [channel.md](channel.md) — external conversation surfaces (a room's chat and private messages): channel tokens, spam classification, moderation escalation and reply pacing decided by the control plane.
- [development-tooling-architecture.md](development-tooling-architecture.md) — one HyperSpace development experience backed by isolated offline, browser, dependency-audit and security-lab runtimes.
- [comfyui.md](comfyui.md) — ComfyUI sul nodo win11 come volto visivo di HyperSpace: i nodi `HyperSpacePrompt`/`HyperSpaceMesh`, il contratto `/v1/chat/completions` con `surface=comfyui` e `X-Hyperspace-Tools: off`, e il ponte "che tira" previsto per la generazione su richiesta.
- [development-security-tools.md](development-security-tools.md) — current development, debugging and security capabilities, selective ECC integration, and planned specialist workers.
- [architecture.md](architecture.md) — core layers (authority, control plane, node/worker, registry, memory graph, infra UI).
- [network-profiles.md](network-profiles.md) — Enterprise Local vs Public Hub deployment profiles.
- [web-node.md](web-node.md) — browser-first worker path design intent (see also [web-node/README.md](../web-node/README.md) for the fuller technical spec and current implementation status).
