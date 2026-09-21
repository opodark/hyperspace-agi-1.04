# docs/

Architecture and deployment notes for HyperSpace-AGI. Mission, lexicon and phases live in the root [VISION.md](../VISION.md) / [ROADMAP.md](../ROADMAP.md) — start there. This folder covers specific subsystems and setup guides.

## Setup / deployment

- [tailscale-mesh-setup.md](tailscale-mesh-setup.md) — cross-machine mesh over Tailscale (current transitional remote-access approach).
- [lan-demo.md](lan-demo.md) — fastest way to demo the mesh to someone on the same physical network, no tunnels.
- [onlyoffice-bridge.md](onlyoffice-bridge.md) — ONLYOFFICE ↔ HyperSpace document connector design (enterprise-network-only track).

## Subsystems

- [dreams.md](dreams.md) — direction and staged roadmap for automatic idle-only memory reflection, including review and promotion rules.
- [hermes.md](hermes.md) — current integration boundary and end-to-end acceptance criteria for Hermes.
- [memory schema](../memory/schema/README.md) — the `hyperspace.memory.v1` entry contract and retention tiers (operative / project / persistent).
- [tool-skill-forge.md](tool-skill-forge.md) — controlled authoring, review and local IDE workflow for inert tool and skill drafts.
- [development-tooling-architecture.md](development-tooling-architecture.md) — one HyperSpace development experience backed by isolated offline, browser, dependency-audit and security-lab runtimes.
- [development-security-tools.md](development-security-tools.md) — current development, debugging and security capabilities, selective ECC integration, and planned specialist workers.
- [architecture.md](architecture.md) — core layers (authority, control plane, node/worker, registry, memory graph, infra UI).
- [network-profiles.md](network-profiles.md) — Enterprise Local vs Public Hub deployment profiles.
- [web-node.md](web-node.md) — browser-first worker path design intent (see also [web-node/README.md](../web-node/README.md) for the fuller technical spec and current implementation status).
