# Host access — shell and OS from the agent (and what stands in the way)

Written for the next step: giving the agent (or an external runtime such as
OpenClaw) real access to the underlying machine. The point of this document is
that **half of it already exists**, deliberately shaped, and the missing half is
the dangerous one — so it deserves a plan instead of a flag.

## What already exists

| piece | where | what it does |
| --- | --- | --- |
| **host agent** | `hostctl/agent.py` | HTTP server on the host (`HOSTCTL_PORT`, default 8765). `GET /status`, `POST /action` |
| **actions** | same file, `ACTIONS` | `ngrok_*`, `tailscale_up/down/status`, `wg_up/down/status`, `ble_scan`, `sbx_sandbox`, `shell_run` |
| **shell_run** | `hostctl/agent.py` + `control-plane/main.py` | one-shot commands as argv, never a shell string: executable allowlist, `cwd` allowlist, output and time caps. The CP publishes the tool only when it is enabled *and* the host agent is configured |
| **command policy** | `shared/shell_policy.py` | names what a command is (`read`, `write`, `destructive`) and what that implies: destructive commands need `confirm: true`, `SHELL_BLOCK_RISK` can refuse them outright. Pure policy, no dependencies, shared by agent and CP |
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

**Stage 1 — `shell_run` (one-shot, argv-only) — IMPLEMENTED (2026-09-20).**
The honest middle step, now in the code: `hostctl/agent.py` exposes the action
`shell_run` (`{argv, cwd, timeout}`), the control-plane publishes the tool
`shell_run` only when it is both wanted and possible, and every call is logged by
the CP (`Shell run: <cmd>`). An external runtime gets real commands (`git`,
`npm`, `python`, your own scripts) without getting a shell.

| knobs (in the host agent's `.env`) | default | what it does |
| --- | --- | --- |
| `SHELL_RUN_ENABLED` | `false` | the action refuses until you turn it on; while off, the tool is not even in the catalogue (chat, MCP, `/tools/execute`) |
| `SHELL_ALLOWED_EXECUTABLES` | `python`, `python3`, `node`, `npm`, `npx`, `pytest`, `git` | matched on the **name** (basename, lowercase, Windows suffix stripped) |
| `SHELL_ALLOWED_DIRS` | the repository root | `cwd` is resolved and must stay inside one of these |
| `SHELL_MAX_TIMEOUT` | `60` | ceiling; the caller can ask for less, never more |
| `SHELL_MAX_OUTPUT_BYTES` | `65536` | per stream, with an explicit `truncated` flag |

What it refuses, and why those refusals are honest: an `argv` that is a string
(or empty, or absurdly long), arguments that *are* shell operators (`;`, `&&`,
`>`, `$(`, …) because that means the caller expects a shell, control characters,
an executable outside the allowlist, a `cwd` outside the allowed directories.
The boundary, though, is **not** this pattern list: it is `shell=False` plus the
allowlist. Two limits worth stating: the allowlist is **name-based**, so it
trusts `PATH` (the same trust the sandbox already places in it), and there is
**still no policy for destructive commands** — `git push`, `npm publish` and
`rm -rf` are all "just argv" at this stage. That is Stage 2's job, and the
`--confirm` idea below is where it starts.

Two more deliberate choices: the child gets a scrubbed environment
(`GIT_TERMINAL_PROMPT=0`, `PAGER=cat`, UTF-8) and `stdin` closed, so a command
that waits for input fails fast instead of hanging until the timeout; and the
audit log carries the argv and the exit code but **not** the output, which can be
large and can contain project data (the log DB is not the place for it).

**Stage 2a — the confirmation step — IMPLEMENTED (2026-09-20).**
`shared/shell_policy.py` classifies a command (`read`, `write`, `destructive`)
with **named rules**, and the host agent applies the verdict where the process is
born: `git push` / `rm -r` / `npm publish` / `docker system prune` /
`systemctl restart` / `chmod -R` and friends are refused unless the caller passes
`confirm: true`. Two knobs, each failing closed in its own direction:

| knob | default | what it does |
| --- | --- | --- |
| `SHELL_CONFIRM_RISK` | `destructive` | the level from which a command asks. `write` also asks for plain writes (`npm install`), `none` never asks |
| `SHELL_BLOCK_RISK` | `none` | the level refused **even with** `confirm: true` — for an unattended runtime that must not be able to destroy anything |

`GET /network/status` carries the policy (`confirm_at`, `block_at`, the rule
names, the allowed executables and directories), so "why is it asking me?" has an
answer in the panel and not only in the logs. And the honest part: a confirmation
is a **decision, not a boundary** — a destructive command no rule matches still
goes through. The allowlist stays the real wall; this layer exists so that an
accident becomes a decision.

**Stage 2b — sessions (the "total access" part) — still to do.** A `shell_session`
action (`open`, `input`, `read`, `close`) backed by a PTY, one session per id,
idle auto-close, output ring buffer with explicit truncation, and the CP holding
the per-session audit. This is what an OpenClaw-like runtime actually needs when
a single `cd`-then-run is not enough.

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
