# Connectors

Enterprise tool fabric of the control plane: GitHub, Microsoft 365 / Office 365 and Google Workspace become **executable capabilities**, not static integrations. This document covers what exists today, how to configure it, how to verify it, and what is deliberately still open.

## What a connector is

```
control-plane/connectors/
  base.py       BaseConnector — the contract
  manager.py    ConnectorManager — discovery, dispatch, diagnostics
  github.py     GitHub REST API (5 tools)
  google.py     Gmail + Calendar + Drive, service account (6 tools)
  office365.py  Microsoft Graph via the O365 SDK (6 tools)
```

`BaseConnector` requires:

| Member | Meaning |
|---|---|
| `name` | unique id, also the tool-name prefix (`github_*`, `o365_*`, `google_*`) |
| `REQUIRED_ENV` | env vars without which the connector cannot work (only *names* are ever published) |
| `enabled` | property: `CONNECTOR_<NAME>_ENABLED=false` forces it off, otherwise `is_available()` |
| `get_tools()` | OpenAI function-calling schemas |
| `execute(name, args)` | returns a string, or `None` when the tool is not its own |

`ConnectorManager` finds every subclass in the package at boot (`pkgutil`), instantiates it and keeps only the enabled ones. **A connector without credentials is silently absent from the catalogue — and that silence is exactly why `/connectors` and the Setup panel exist.**

## Where the tools go

One catalogue, three consumers — the tools are not re-declared anywhere:

1. **Chat tool loop** — `BUILTIN_TOOLS` is injected into `/v1/chat/completions` for tool-capable models (non-stream and stream paths).
2. **MCP** — `/mcp` `tools/list` / `tools/call`, with the per-client allowlist of `shared/mcp_auth.py`.
3. **`POST /tools/execute`** — single-shot execution for external callers, i.e. the Open WebUI bridge in `openwebui-tools/office365_tool.py`. It can execute **every** published tool, so it is not left open: it sits behind the same gate as the network-admin routes (`X-Hyperspace-Network-Token` = `NETWORK_ADMIN_TOKEN`, at least 32 characters). The bridge must be given that token in its `network_token` valve.

## Configuration

All variables live in one section of the Setup tab (or in `.env`, same keys):

| Connector | Variables | Minimum privileges |
|---|---|---|
| GitHub | `GITHUB_TOKEN`, `CONNECTOR_GITHUB_ENABLED` | PAT with `repo` scope |
| Microsoft 365 | `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID`, `CONNECTOR_OFFICE365_ENABLED` | daemon app, APPLICATION permissions: `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All` |
| Google Workspace | `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_DELEGATE_EMAIL`, `CONNECTOR_GOOGLE_ENABLED` | service account with Gmail/Calendar/Drive APIs enabled, domain-wide delegation if impersonating |

`GOOGLE_CREDENTIALS_JSON` is the whole service-account JSON **on one line**; it is entered as a password field, so in the dashboard it is masked and never echoed back — to change it you must paste the whole value again.

Optional knobs (they change latency, not behaviour):

| Variable | Default | Meaning |
|---|---|---|
| `GITHUB_TIMEOUT_S` | `10` | per-request timeout |
| `GITHUB_RETRY_S` | `1` | wait before the single retry (network / 5xx, **idempotent methods only**) |
| `GOOGLE_TIMEOUT_S` | `20` | per-request timeout (`google-auth-httplib2` required; without it the connector still works, just without a timeout) |
| `O365_TOKEN_DIR` | `$DATA_DIR/o365` | where the O365 SDK caches its token |

`O365_TOKEN_DIR` used to be hardcoded to `/tmp`: a directory that does not exist on Windows and that is not persistent inside a recreated container, so every restart re-authenticated from scratch.

Saving the Setup tab applies the change **immediately**: the control plane reloads the connectors and rebuilds `BUILTIN_TOOLS` in place, so a newly configured connector appears in the chat loop, in the MCP catalogue and in `/tools/execute` without restarting the container. Disabling one (`CONNECTOR_<NAME>_ENABLED=false`) removes its tools from **exposure**, not just from execution.

## Read/write policy

Publishing a connector's tools is not the same as authorising it to WRITE. Six of the shipped tools have side effects (`github_create_issue`, `github_add_comment`, `o365_send_email`, `o365_create_event`, `google_send_email`, `google_create_event`): by default they are **not exposed at all** — not to the chat tool loop, not to the MCP catalogue, not to `/tools/execute`. Two conditions must hold to enable one:

| Variable | Meaning |
|---|---|
| `CONNECTOR_READ_ONLY` | `true` by default. Set `false` to leave read-only mode. |
| `CONNECTOR_WRITE_TOOLS` | opt-in allowlist: `connector=tool1,tool2` or `connector=*`, entries separated by `;`. Same syntax as `MCP_CLIENT_TOOLS`. |

Example — `CONNECTOR_READ_ONLY=false` with `CONNECTOR_WRITE_TOOLS="github=github_create_issue;o365=*"` exposes the GitHub issue-creation tool plus every Office 365 write tool, and nothing else.

The classification is declared by each connector (`READ_TOOLS` / `WRITE_TOOLS` on the `BaseConnector` subclass) and is **validated against `get_tools()`**: a tool that is published but not classified makes the whole connector fall out of the catalogue (fail-closed), with the reason visible in `GET /connectors`. Better a missing connector than a tool whose nature nobody declared. The policy itself lives in `shared/connector_policy.py`, pure and unit-tested like `shared/mcp_auth.py`.

This is enforced twice, on purpose: `get_all_tools()` does not publish blocked writes, and `ConnectorManager.execute()` refuses them — a client can call a tool name it saw before the policy changed, or one it simply knows.

```
GET /connectors   (excerpt, read-only default)
"connectors": [{
  "name": "github",
  "tools": ["github_search_issues", "github_create_issue", "github_get_repo",
            "github_list_commits", "github_add_comment"],
  "read_tools": ["github_search_issues", "github_get_repo", "github_list_commits"],
  "write_tools": ["github_create_issue", "github_add_comment"],
  "write_tools_exposed": [],
  "write_tools_blocked": ["github_create_issue", "github_add_comment"]
}]
```

Blocked writes are refused with an explicit message, not silently ignored:

```
Tool 'github_create_issue' bloccato dalla policy: è un tool di SCRITTURA del connettore
'github' non abilitato. Servono CONNECTOR_WRITE_TOOLS="github=github_create_issue" e
CONNECTOR_READ_ONLY=false (vedi docs/connectors.md).
```

`write_tools_blocked` is therefore also the answer to "what do I have to type to enable this?".

## Resilience: circuit breaker

The per-connector retry is per-call; without a breaker, an external service that is down (or an expired token) would cost the full timeout on **every** model call, forever. After `CONNECTOR_FAILURE_THRESHOLD` consecutive errors (default 3) the connector is put on hold for `CONNECTOR_COOLDOWN_S` seconds (default 60):

```
Tool 'github_get_repo': il connettore 'github' è in pausa dopo 3 errori consecutivi — riprova fra 42s.
L'ultimo errore è nei log del control-plane.
```

Deliberate choices:

- **the tools stay in the catalogue** while paused. Making them disappear would be a second mystery on top of the first, and the message above tells the model (and the operator) exactly what is happening and for how long;
- **a single success resets the counter**, and closing the breaker emits an event;
- **only real failures count**: a policy refusal, an unknown tool name or a legitimate API error returned as text (e.g. `[github] Token non valido…`) do not trip it;
- **a reload clears the breaker state**, which makes any Setup save the explicit way to lift a hold immediately;
- the state is visible per connector in `GET /connectors` (`failures`, `cooldown_remaining_s`) and the thresholds in `"breaker"`.

Note that a *returned* error (a 401, a missing scope) is a working connector telling you something — that is why the breaker watches exceptions, not error messages.

## Verification

```
GET /connectors
{
  "ok": true,
  "connectors": [{"name": "github", "tools": ["github_search_issues", "..."]}],
  "disabled":  [{"name": "office365", "reason": "env mancanti: MS_CLIENT_ID, MS_CLIENT_SECRET",
                 "missing_env": ["MS_CLIENT_ID", "MS_CLIENT_SECRET"]}],
  "problems": [],
  "tool_count": 5
}
```

The same data is rendered in **Setup → Connettori** (dot per connector, reason, published tools) and the panel refreshes automatically when the env is saved.

Other useful checks:

- boot log: `[ConnectorManager] Loaded: github` / `Skipped: office365 (env mancanti: ...)`;
- `/mcp/status` → `published_tools` is the effective catalogue;
- log lines `tool_call: <name>` and `tool_result: <name>` for every connector invocation (chat and tool loop);
- `POST /tools/execute` with `{"tool_name": "github_get_repo", "args": {"repo": "owner/repo"}}` for a manual smoke test.

Nothing in `/connectors` contains a secret value: only booleans, tool names and the names of missing env vars.

## Troubleshooting

| Symptom | Cause |
|---|---|
| a connector's tools are missing, no error anywhere | credentials absent, or `CONNECTOR_<NAME>_ENABLED=false` — read `GET /connectors` |
| `problems` contains `import fallito` | the Python package of that connector is not installed in the image (see `control-plane/requirements.txt`) |
| Microsoft 365 stopped working overnight | client secret expired (Entra ID → Certificates & secrets) |
| Google returns an empty mailbox / permission errors | service account without domain-wide delegation, or `GOOGLE_DELEGATE_EMAIL` missing |
| GitHub returns 404 on a private repo | the token lacks `repo`, or it has expired |
| `[github] Token non valido o scaduto (HTTP 401)` | regenerate the PAT and save it in the Setup tab |
| `[github] Quota API esaurita (HTTP 403)` | rate limit: the message carries the reset time |
| `[github] Permessi insufficienti (HTTP 403)` | token scope, not quota — the two 403s are distinct on purpose |
| `Tool 'X': il connettore che lo espone ha fallito — …` | an exception **inside** the connector, attributed to it (not "unknown tool"): the exception type is in the message and in the log (`type=system`) |
| a working connector stopped right after a Setup save | it was reloaded: read `problems` in `GET /connectors` |
| `il connettore 'X' è in pausa dopo N errori consecutivi` | circuit breaker open: fix the underlying error, or save the Connectors section to reset it immediately |

## Known limits (open work)

- **Non-idempotent writes are never retried.** Deliberate — a lost response after a created issue must be retried by a human, not silently duplicated — but it means a transient network blip on a write surfaces as an error.
- **CI never calls a live API.** Tests cover the contract (discovery, gating, diagnostics, classification, error translation, retry policy) with fake credentials and no network, by design.
- **Microsoft 365 and Google tool bodies are not exercised against a real tenant/account in CI:** O365 is only exercised through its documented interface, Google only through the service-account path.
