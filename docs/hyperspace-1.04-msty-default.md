# HyperSpace 1.04 — HIP + Msty Default

## Direction Update

HyperSpace 1.04 adopts Msty as the default local AI workspace and agent runtime, while HIP remains the coordination protocol above heterogeneous runtimes.

This means:
- Msty Studio becomes the default desktop workspace for local and hybrid agent workflows.
- Msty Claw becomes the default autonomous runtime for single-host and edge agent execution.
- Msty Nexus becomes the default gateway layer for local/cloud model access and OpenAI-compatible integration.
- Ollama remains supported as a backend inference layer, but not as the primary product experience.
- HyperSpace stays protocol-first: discovery, capability advertisement, intent routing, worker negotiation, and connector fabric remain the core.

## Architectural Positioning

HyperSpace sits above individual runtimes.

```text
                AI Model
                    │
          Agent Runtime Layer
     (Msty Claw, custom agents, others)
                    │
        ==========================
         HyperSpace Intent Protocol
        ==========================
                    │
        HyperSpace Control Plane
                    │
     Distributed Worker Network
```

Msty provides the default execution surface.
HyperSpace provides the coordination layer.

## Implementation Notes

Initial 1.04 implementation goals:
- Intent schema
- Capability advertisement
- Runtime adapter API
- Intent Router
- Worker negotiation
- Browser Worker integration
- Msty/Nexus adapter

## Product Principle

HyperSpace is not another chat app.
HyperSpace is the coordination protocol that lets independent AI runtimes cooperate across heterogeneous infrastructures.
