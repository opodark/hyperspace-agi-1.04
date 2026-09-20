# Tool & Skill Forge

The Forge is the controlled authoring area exposed by the Control Plane dashboard.
Generated code is always stored as an inert draft under the persistent Control Plane
data directory; it is never imported or executed automatically.

## Workflow

1. Describe a tool or skill in the **Forge** dashboard tab.
2. Generate with the configured coder model, or enter source manually.
3. Inspect static validation warnings and open the source in the browser IDE.
4. Move the artifact to `review`.
5. Approve it explicitly with `FORGE_ADMIN_TOKEN`, or disable it.

Approval records intent but does not install the artifact into Open WebUI. Export and
publication adapters for Open WebUI, MCP, and OpenAPI are deliberately separate future
steps so generated source cannot silently become executable server code.

Approved Markdown skills can now be selected explicitly for a chat request with
`hyperspace_skills: ["artifact-id"]`. The Control Plane verifies the approval's
source hash and adds the reference text to that request only. This does not
execute code, activate hooks, or grant tool permissions. Updating an artifact
resets approval. Older approvals without a source hash need renewed approval.

## Selected ECC workflows

In the Forge dashboard, enter the existing admin token and choose **Importa
skill ECC come draft**. This imports the bundled `security-review` and
`verification-loop` files from a pinned upstream commit, checking their SHA-256
hashes and preserving their MIT license and provenance. Repeating the import
leaves existing artifacts, including local edits, unchanged. Review and approve
the desired drafts, then use their displayed IDs in a request:

```json
{
  "model": "your-local-model",
  "messages": [{"role": "user", "content": "Review this task's code changes"}],
  "hyperspace_skills": ["ecc-security-review-9ac593b55cba"]
}
```

The extension is consumed by `/v1/chat/completions` before routing, so both
streaming and non-streaming paths receive the same context. At most two skills
and 24,000 UTF-8 bytes of injected context are allowed; oversized selections
are rejected rather than truncated. This is a byte budget, not a tokenizer or
model context-window guarantee. Skills are sent to the backend selected for
that request, like other messages. The Hyperspace adaptation asks the model to
use available sandbox presets and report unavailable checks as not run.

## Browser IDE

The optional IDE is code-server and is bound to localhost only. Start it with:

```powershell
docker compose -f docker-compose.windows.yml --profile ide up -d hyperspace-ide
```

Use **Apri IDE** in the Forge dashboard, or open `http://127.0.0.1:<CODE_SERVER_PORT>`,
and use `CODE_SERVER_PASSWORD` from the local `.env`. Artifact sources are available
inside the IDE under `/home/coder/forge` as `.py` or `.md` files. The repository and
the Forge directory are mounted read/write, but the Docker socket is not mounted. Do not
expose this port publicly; use a trusted authenticated tunnel if remote access is needed.

The artifact JSON managed by the dashboard is the authoritative record. Source sidecars
are provided for inspection in the IDE; edits made only to a sidecar are not imported
back into the registry and can be replaced by a later dashboard save.

## API

- `GET /forge/artifacts` — list artifacts.
- `GET /forge/config` — return the local IDE URL and artifact directory.
- `POST /forge/artifacts` — create an inert manual draft.
- `POST /forge/import/ecc` — import the bundled workflows as inert drafts;
  requires `X-Hyperspace-Forge-Token`. No network fetch or installer runs.
- `PUT /forge/artifacts/<id>` — update a draft and increment its version.
- `POST /forge/generate` — generate and persist a draft through local Ollama.
- `POST /forge/artifacts/<id>/status` — change lifecycle state; approval requires
  the `X-Hyperspace-Forge-Token` header.
