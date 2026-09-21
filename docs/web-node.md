# Web Node

The web node is the browser-first worker path for HyperSpace-AGI. It exists so the mesh can include weaker devices that cannot run Docker or heavy local services.

## Purpose

The browser node should make the system feel more distributed and more accessible. A user should be able to open a webpage or extension, consent to resource sharing, and immediately contribute small capabilities to the mesh.

## Target capabilities

The first useful tasks for the web node are intentionally small:

- translation,
- embeddings,
- summarization,
- moderation,
- simple validation steps.

## Expected shape

The web node should be a subtree or package that can be reused in multiple forms:

- a standalone web app,
- a browser extension,
- a side panel / popup client,
- a lightweight task worker.

## Runtime model

The browser node should not assume Docker, local model installs, or privileged system access. It should rely on browser-safe primitives and a strict task envelope so the control plane can route only safe work to it.

The current web node remains a lightweight mesh participant. The planned
Playwright browser worker in the [development tooling architecture](development-tooling-architecture.md)
is a separate runtime for reproducible UI tests, screenshots, and traces; it is
not executed inside an end user's browser tab.

## Interface with the control plane

The web node cannot be addressed by the control plane: a browser tab has no
inbound HTTP endpoint. The direction is therefore inverted — the node
registers, then *pulls* work with a long poll and publishes results. This is
the same pattern the offline code sandbox runner already uses.

Implemented endpoints (all JSON, all under the control plane):

| Endpoint         | Caller       | Purpose                                              |
|------------------|--------------|------------------------------------------------------|
| `POST /web/register` | node     | node id, capabilities, limits; also appears in the mesh list |
| `POST /web/poll`     | node     | long poll (`timeout_s`), returns one task or `null`  |
| `POST /web/result`   | node     | success or failure for the task that was in flight   |
| `POST /web/tasks`    | operator | enqueue a web-safe task (needs `X-Hyperspace-Network-Token`) |
| `GET  /web/status`   | dashboard| nodes, queue depth, in-flight and recent results     |

Four rules are enforced on the control plane side, not trusted to the client:

1. only task types in the web-safe whitelist can be enqueued, so heavy
   inference can never land on a browser tab;
2. a node only receives types it declared, and declarations are intersected
   with the whitelist at registration;
3. at most one task is in flight per node, so a slow tab can never be given
   the same work twice;
4. every queue, payload, task TTL and poll duration is bounded.

Long polling costs a control-plane thread for up to `WEB_NODE_MAX_POLL_S`
seconds, so the server must run multi-threaded; otherwise two web nodes block
each other. `_best_endpoint` returns an empty string for `browser://` nodes,
which keeps them out of every addressable-node filter — chat routing included.

## Deployment modes

### Enterprise deployment

The web node can be distributed inside a company as a managed browser tool. In that mode the same control plane can coordinate both local runtime nodes and browser nodes.

### Public deployment

The web node can also be published from a central landing page as the free/public entry point into the mesh.

## Design rule

The browser node should stay small, safe, and optional. It is an addition to the mesh, not a replacement for the main execution runtimes.

## Status (2026-09-19)

Implemented: registration with capability negotiation, the full pull cycle,
the control-plane queue with its whitelist and limits, the consent gate, and a
Node test suite that runs without a browser.

Honest limits, stated here so they are not discovered later:

- `summarize` is extractive (term frequency), not abstractive;
- `validate_json` covers a minimal subset of structural validation, not JSON
  Schema;
- `moderate` is a pattern heuristic over secrets and personal data — it is
  **not** a content classifier and must not be used as a policy filter;
- `translate` and `embed_texts` exist only when the host injects a runtime, or
  the browser exposes its own translation API; the node never declares a
  capability it cannot serve;
- the extension is a launcher: MV3 service workers are suspended, so they
  cannot hold the long poll. The supported runtime is the standalone page.

Verification:

```bash
node web-node/tests/web-node.test.mjs      # 24 check, no browser needed
node tests/dashboard.test.cjs
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/verify_web_node_e2e.py   # sull'app vera, richiede flask+cryptography
```

Local models (WebGPU, opt-in): `web-node/src/webgpu.js` detects the real adapter
and offers two runtimes — Transformers.js for embeddings/translation/
summarization (`device: "webgpu"`) and WebLLM for a separate on-device chat panel.
Weights download from Hugging Face on demand (embeddings eagerly on click); the
node declares `embed_texts`/`translate` only once the runtime is actually loaded.
`moderate` stays heuristic on purpose.
