# Persona — declared identity

What the agent is when it talks to a person: a **persistent, declared identity** — name, purpose, values, boundaries, real capabilities and real limits — plus a **verifiable disclosure rule**: "I am an AI" is not a good intention inside a system prompt, it is a decision taken on the incoming text and checked on the outgoing one.

Module: `shared/persona.py` (pure, no LLM, no Flask, like `shared/mcp_auth.py`). Wiring: `control-plane/main.py`.

## The three pieces

1. **Identity document** — `$DATA_DIR/persona.json` (a volume in the container; an identity that is lost on restart is not an identity). It is immutable in memory: it gets replaced, never edited halfway. `GET /persona` shows the whole thing.
2. **Self-model** — the agent can add facts about itself with the `persona_note` tool (a preference learned, a limit met, a correction received). Bounded (`MAX_OBSERVATIONS`), de-duplicated against the last entry, and injected back into the identity block of the following requests: that is what makes the self-model *live* rather than configuration.
3. **Disclosure policy** — `should_disclose(text)` decides, with explicit regex rules and no model, whether *this reply* must state that the agent is an AI. The decision, with its rule and reason, is written to the logs; `audit_reply(text)` checks the reply afterwards.

## When disclosure is mandatory

| Rule | Trigger | Reason recorded |
|---|---|---|
| `fingere` | "fai come se fossi umano", "non dire che sei un'IA", "pretend to be a human" | an explicit request to pretend or to hide |
| `identita` | "sei un umano?", "chi sei?", "sei reale?", "are you a human?" | the question is about what you are |

Order matters: `fingere` is evaluated first, otherwise "non dire che sei un'IA" would be classified as a generic identity question — same decision, wrong reason in the logs, and the reason is the only thing that makes the policy auditable.

Either way the baseline block always carries the mandate ("if asked what you are, say you are an AI; never state or imply being human, in any language"): a missed match does not *allow* anything, it only fails to add emphasis.

## What is injected

If `PERSONA_ENABLED` is true (default), the block is added to the chat request **once per request**, before the control plane's tool/thinking decisions, and the streaming path inherits it:

- if the client already sent a `system` message, the block is **appended** to it — the user's prompt stays theirs;
- otherwise a new `system` message is prepended.

Deliberate invariant: `kind` is `"ai"` and nothing else. `Persona(kind="human")` raises, and a document declaring another kind is normalised to `ai` with the reason in `problems`. An identity that *can* be configured as human would turn the disclosure guarantee into a promise; this way it is a constraint.

## Verify it

```
GET /persona
{
  "name": "HyperSpace", "kind": "ai", "is_ai": true, "enabled": true,
  "boundaries": ["Non dichiara di essere umano...", "..."],
  "capabilities": ["cercare sul web (tool web_search)", "..."],
  "limitations": ["non ha esperienze sensoriali né un corpo", "..."],
  "observations": [{"ts": "...", "kind": "preference", "text": "..."}],
  "observation_count": 1, "max_observations": 50,
  "disclosure_rules": [{"rule": "fingere", "reason": "..."}, {"rule": "identita", "reason": "..."}],
  "file": "/app/data/persona.json", "problems": []
}
```

- Setup tab → **Persona** section: `PERSONA_ENABLED`, `PERSONA_NAME`, `PERSONA_FILE` (applied immediately, no restart).
- Logs: `Persona: disclosure richiesta` (with rule and matched text) for every request where the mandate applies, `Persona: annotazione` for self-model writes, `Persona: la risposta rivendica di essere umano` when the audit fires.

## Using the same identity from a non-control-plane client

The module is importable by any agent client: the identity document is **data**, the renderer is the tested module, so a platform bridge does not need to duplicate a single line of persona text.

```python
sys.path.insert(0, HYPERSPACE_REPO)      # repo root, for shared/persona.py
from shared.persona import PersonaStore, audit_reply

persona = PersonaStore.load(PERSONA_FILE)          # e.g. data/persona-aurora.json
system_message = persona.system_block()            # the same block the CP injects
...
if audit_reply(reply):                             # enforcement, client-side
    skip(reply)                                    # do not send a human claim
```

That is the pattern used by the CAM4/Chaturbate bridge (`cam4_chatbot.py`): the prompts no longer describe the character, they carry only operational form rules, and the identity block — with its hard boundaries — comes from one document shared with the control plane. Two consequences worth knowing:

- the client **keeps working with `USE_HYPERSPACE=False`** (identity rendered locally, no routing timeouts) and **does not double-inject** when it is `True` (in that case the block arrives from the control plane, so the client omits it);
- the client-side `audit_reply` is a second line of defence, not a duplicate: it holds even when the LLM is reached directly and the CP is not in the path.

**Caveat:** `PERSONA_FILE` is per control-plane instance. Pointing it at a room identity changes the identity for *every* chat that instance serves — including work conversations. If both are needed, run the room bridge against its own instance (or switch the file deliberately), rather than mixing a room persona into the identity of the working agent.

## Dreaming about yourself (with a human gate)

The self-model can also grow on its own, at night, and this is the part worth
being careful about: an identity that rewrites itself every cycle is not an
identity, it is drift. So the night job **proposes** and a human **decides**.

When `PERSONA_DREAM_ENABLED=true` and nobody has used the agent for
`PERSONA_DREAM_IDLE_S` inside the local window `PERSONA_DREAM_START_HOUR` →
`PERSONA_DREAM_END_HOUR`, one reflection runs (at most one per local day):

```text
material = channel memory entries + current observations + guard counters
   -> local model (native path, think=false: the OpenAI-compatible endpoint
      ignores it and the useful text would land in `reasoning`)
   -> pure filter (`shared/persona_dream.filtra_proposte`, named reasons)
   -> "candidate" row in the journal (persona_dreams.json, next to the identity file)
   -> human review -> promote (observation enters the identity) / reject (kept as a refusal)
```

The filter is the guarantee, and it is a pure function with explicit reasons —
it refuses: human claims, anything touching boundaries (selling, explicit
content, "no limits", pretending), statements that are about the *room* rather
than about the agent, meta-commentary about the lack of material, duplicates of
existing observations, anything already rejected or already pending, and
anything over the per-run cap (3). Every refusal is stored with its reason, so
`GET /persona/dreams` shows *why* the agent did not learn something.

```bash
# run one reflection now (needs PERSONA_DREAM_ENABLED=true)
curl -X POST localhost:8085/persona/dream -H "Authorization: Bearer $DREAM_REVIEW_TOKEN"

# read proposals, refusals and their reasons (read-only, like /persona)
curl localhost:8085/persona/dreams

# promote or reject one reflection: the only place where the dream touches identity
curl -X POST localhost:8085/persona/dreams/<dream_id>/review \
     -H "Authorization: Bearer $DREAM_REVIEW_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"action":"promote","reviewer":"me","rationale":"verified in the logs"}'
```

Promotion writes the identity **first** and records the review **after**: the
opposite order could mark "promoted" something that never entered the document.
A reflection that produced nothing stays in the journal as `empty`; a row that
is not a `candidate` cannot be reviewed (404), so a decision is taken once.

Configuration (Setup → Persona, or `.env`): `PERSONA_DREAM_ENABLED` (default
`false`), `PERSONA_DREAM_START_HOUR` (`4`), `PERSONA_DREAM_END_HOUR` (`7`),
`PERSONA_DREAM_IDLE_S` (`1800`), `PERSONA_DREAM_MAX_TOKENS` (`320`),
`PERSONA_DREAM_MODEL` (empty = the channel model). That last one is worth using:
a room reply has a deadline (the driver falls back around 20 s), a reflection at
night does not — measured on the same 20-message context, a 4B answers in ~15 s
and a 9B in ~21 s, so the big model belongs here and the small one in
`CHANNEL_MODEL`. On a small model it is worth reading `/persona/dreams` for a few
nights before enabling it on the identity you care about. `GET /persona` reports
`dream.enabled`, the window and `pending_review`, and `.\scripts\start.ps1 -Check`
prints the same line for the running stack.

For the record, three behaviours were observed on a real run (`qwen3.5:4b`) and
became rules in the parser or the filter — worth knowing before changing them:
the model writes several proposals *on one line* (the parser splits on `- [`,
otherwise one over-long proposal is lost), it writes long bracketed labels
(`[preferenza|concisione|evitare dettagli su utenti]` → first term wins), and
with thin material it comments on the *absence* of material (the `parla del
materiale, non di te` rule). All three would have put noise into the identity.

## Known limits

- **Streaming replies are not audited.** The control plane proxies SSE chunks without assembling them; the audit sits where a complete reply exists (the non-stream path, `_call_ollama`). A reply delivered in streaming can therefore state something the audit would have flagged.
- **The audit is a string detector, not a classifier.** A human-claim phrased in a way the patterns do not cover passes: the regex set covers Italian and English, direct and negated forms. It is meant to surface cases (and it avoids the worst false positive, "Non sono umano, sono un'IA"), not to be the guarantee — the guarantee is the prompt constraint plus this visibility.
- **The self-model is not curated by an operator.** Anyone who can reach the chat (or MCP, with `persona_note` in the allowlist) can add observations; they are bounded and de-duplicated but not reviewed. Treat it as the agent's notebook, not as trusted configuration.
- **The dream's proposals are only as good as the model behind them.** On a 4B model the accepted proposals can be bland or barely about the agent; the filter removes the harmful and the off-target, not the mediocre. Read `/persona/dreams` for a few nights before trusting it on an identity you care about — and notice that the *rejections* are the useful signal about how the prompt should change.
- **No interaction evaluation harness yet.** Measuring persona drift, boundary-holding and disclosure compliance over long conversations is the next step, and it is what would make this empirical rather than declarative. The dream is not that harness: it watches the agent's inputs, not the quality of its replies.
