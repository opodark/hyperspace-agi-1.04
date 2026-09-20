# Host access — shell and OS from the agent (and what stands in the way)

Written for the next step: giving the agent (or an external runtime such as
OpenClaw) real access to the underlying machine. The point of this document is
that **half of it already exists**, deliberately shaped, and the missing half is
the dangerous one — so it deserves a plan instead of a flag.

## What already exists

| piece | where | what it does |
| --- | --- | --- |
| **host agent** | `hostctl/agent.py` | HTTP server on the host (`HOSTCTL_PORT`, default 8765). `GET /status`, `POST /action` |
| **actions** | same file, `ACTIONS` | `ngrok_*`, `tailscale_up/down/status`, `wg_up/down/status`, `ble_scan`, `sbx_sandbox` |
| **read-only split** | `READ_ONLY_ACTIONS` | tells apart what only observes from what changes the machine |
| **sandbox** | `sbx_sandbox` + `shared/code_sandbox.py` | workspaces: `create`, `run`, `diff`, `read`, `write`, `replace`, `list` |
| **CP side** | `control-plane/main.py` | `GET /network/status`, `POST /network/action`: the agent never talks to the model directly |
| **tokens** | `hostctl/agent.py --generate-token` | writes `HOSTCTL_TOKEN` and `NETWORK_ADMIN_TOKEN` (≥32 chars) into `.env` |

Invariants already enforced in that code, worth naming because they are the
reason the surface is usable at all:

- **argv, never a shell string.** `sbx_sandbox run` takes a vector and checks
  `Path(argv[0]).name` against `_SBX_ALLOWED_EXECUTABLES`; no interpolation, so an
  argument can not become a command. `docs/network-panel.md` states the same rule
  for the panel.
- **confinement.** Paths are resolved and asserted to stay inside the workspace
  root (`path escapes workspace`); content writes are capped (1 MB).
- **tokens with constant-time comparison**, one per surface (`HOSTCTL_TOKEN` for
  the agent, `NETWORK_ADMIN_TOKEN` for the CP routes), and nothing happens while
  they are missing (503 on both sides).
- **every action is logged** by the CP (`push_log('system', 'Network action: …')`)
  with the result, so "who ran what" has an answer.

## What is missing — and why it is the hard part

There is **no generic `shell`/`exec` action**. That is not an oversight: an
argv-only allowlist is the difference between "the agent can run a tool" and "the
agent can run anything as the user who starts the host agent". A terminal for an
external runtime (OpenClaw-style) needs three things that are not in place yet:

1. **sessions** — a persistent shell (PTY) with an id, so the runtime can `cd`,
   run a command, read output, and keep state; today every action is a one-shot.
2. **a policy for destructive commands** — `rm -rf`, service restarts, disk
   operations. An allowlist alone cannot distinguish `ls -la` from `rm -rf /`.
3. **output handling** — streaming, caps, and truncation that the caller can
   detect (today: `truncated` flags on sandbox calls only).

## Staged plan

**Stage 0 — see what you have (no code).**
`python scripts/hs.py host` reports whether the agent is configured and
reachable. To turn it on:

```bash
python hostctl/agent.py --generate-token   # writes HOSTCTL_TOKEN + NETWORK_ADMIN_TOKEN
python hostctl/agent.py                    # leave it running on the host
.\scripts\start.ps1 -NoBuild               # the CP picks up the tokens
python scripts/hs.py host                  # must answer, not 503
```

**Stage 1 — `shell_run` (one-shot, argv-only).** The honest middle step: a new
action that takes `{argv, cwd, timeout}`, refuses shell metacharacters, resolves
`cwd` against an allowlist of directories, caps output and timeout, and logs every
call. It gives an external runtime real commands (`git`, `npm`, `python`, your own
scripts) without giving it a shell.

**Stage 2 — sessions (the "total access" part).** A `shell_session` action
(`open`, `input`, `read`, `close`) backed by a PTY, one session per id, idle
auto-close, output ring buffer with explicit truncation, and the CP holding the
per-session audit. This is what an OpenClaw-like runtime actually needs — and the
first place where a confirmation step matters: destructive commands should be able
to require `--confirm`, not because a prompt is a security boundary, but because
it turns an accident into a decision.

**Stage 3 — the adapter.** The `ROADMAP` already frames the shape: each runtime
implements a *light HyperSpace adapter*. Concretely: the runtime speaks its own
tool protocol, the adapter translates to `POST /network/action`, and **tokens stay
in the CP**, never in the runtime's process. The agent gains hands; the runtime
gains nothing to steal.

## Before enabling anything, decide these

- **which user** runs the host agent: its permissions are the agent's permissions.
  Not Administrator, on a machine you also work on.
- **the allowlist**: which executables and directories are in, and who edits it.
- **the blast radius**: what a mistaken `action` call can destroy, and what is
  backed up.
- **the audit**: where `/network/action` results go, and how long they stay.

The rest of the repository assumes this shape: the code sandbox for code, the
host agent for network/machine operations, the CP in the middle for policy and
logging. Adding a shell is not "opening a door" — it is adding a room to a house
that already has walls, and the walls are the reason the room can be safe.
