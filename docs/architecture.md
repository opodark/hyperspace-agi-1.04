# Architecture

HyperSpace-AGI is organized as a modular distributed agent system. The repo keeps the orchestration core, connector fabric, node runtimes, registry, memory layer, and infrastructure UI separated so each piece can evolve independently.

## Core layers

### Authority

The authority layer is the trust and seed boundary of the system. It is responsible for bootstrapping, node trust, and any role that should remain more tightly controlled than ordinary workers.

### Control Plane

The control plane is the orchestration core. It routes tasks, manages connectors, presents dashboards, and coordinates the agent fabric.

### Node / Worker

The node and worker layers are the execution runtime of the mesh. They handle task execution, model access, and lower-level agent behavior.

**Reasoning control on the node path (`shared/ollama_native.py`).** The control plane decides whether a request may reason (`_decide_thinking`) and puts `think` in the payload; the OpenAI-compatible endpoint of Ollama *ignores* that flag (measured: with `think=false` the answer comes back with an empty `content` and everything in `reasoning`), so on the node path `think=false` used to still produce reasoning — the model burned its token budget thinking and the reply arrived after minutes (the live-chat timeouts). For non-stream requests with an explicit `think=false` the node now translates the payload to Ollama's native `/api/chat`, calls it through **ollama-proxy** (so instrumentation, logs and shared memory are unchanged) and translates the answer back to OpenAI shape, including `tool_calls` — where the second round of the tool loop must be normalised (`arguments` as object, `tool_name` instead of `tool_call_id`), otherwise the native endpoint answers 400. The control plane uses the same module for channel replies. Streaming, `think` absent and `think=true` keep the previous path; if the native call fails the node falls back to the compatible one and says so in the log.


### Registry

The registry is the discovery and membership layer. It keeps track of active nodes and provides a shared view of who is online, what they can do, and how they should be reached.

### Memory Graph

The memory graph is the persistence and retrieval layer for shared agent memory and long-lived observations.

### Infra UI

The infra UI is the operational view of the mesh. It is separate from the control plane so that administrators and operators can inspect the network without touching execution logic.

## Connector fabric

The connector fabric lives in the control plane and is meant to make HyperSpace useful in enterprise environments. The current priority set is:

- GitHub
- Google Workspace
- Microsoft 365 / Office 365

The goal is to let the control plane behave like an enterprise agent operations center, not only a local inference runner.

## Web node path

The browser/web-node path is a lightweight client route for weaker machines. It should be able to join the mesh without Docker or heavy installation and contribute small tasks such as translation, embeddings, summarization, or moderation.

The web node should register capabilities, receive a constrained task set, and report results back to the control plane through a simple protocol.

## Separation principle

Every layer should remain independently deployable:

- authority can live behind a stricter boundary,
- control plane can run centrally,
- nodes and workers can scale horizontally,
- web nodes can join through the browser,
- the public hub can expose a free path without collapsing the enterprise boundary.
