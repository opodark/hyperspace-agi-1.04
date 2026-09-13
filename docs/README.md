# docs/

Architecture and deployment notes for HyperSpace-AGI. Mission, lexicon and phases live in the root [VISION.md](../VISION.md) / [ROADMAP.md](../ROADMAP.md) — start there. This folder covers specific subsystems and setup guides.

## Setup / deployment

- [tailscale-mesh-setup.md](tailscale-mesh-setup.md) — cross-machine mesh over Tailscale (current transitional remote-access approach).
- [lan-demo.md](lan-demo.md) — fastest way to demo the mesh to someone on the same physical network, no tunnels.
- [onlyoffice-bridge.md](onlyoffice-bridge.md) — ONLYOFFICE ↔ HyperSpace document connector design (enterprise-network-only track).

## Subsystems

- [dreams.md](dreams.md) — automatic idle-only reflection worker (per-node, disabled by default).
- [architecture.md](architecture.md) — core layers (authority, control plane, node/worker, registry, memory graph, infra UI).
- [network-profiles.md](network-profiles.md) — Enterprise Local vs Public Hub deployment profiles.
- [web-node.md](web-node.md) — browser-first worker path design intent (see also [web-node/README.md](../web-node/README.md) for the fuller technical spec and current implementation status).
