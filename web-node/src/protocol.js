// web-node/src/protocol.js
// Contratto fra il web node e il control-plane. Nessuna dipendenza: questo
// modulo deve girare sia in una pagina sia in un service worker di estensione.

export const PROTOCOL_VERSION = "hyperspace-web-node/1";

// Deve restare allineato a WEB_SAFE_TASK_TYPES in shared/web_node.py, che e'
// la fonte autorevole: il CP rifiuta comunque ogni tipo fuori da quella lista.
// web-node/tests/web-node.test.mjs verifica che i due elenchi coincidano.
export const KNOWN_TASK_TYPES = [
  "embed_texts",
  "moderate",
  "summarize",
  "translate",
  "validate_json",
];

export class ProtocolError extends Error {}

const MAX_ID = 128;

export function assertNodeId(nodeId) {
  const value = String(nodeId ?? "").trim();
  if (!value) throw new ProtocolError("node_id mancante");
  if (value.length > MAX_ID) throw new ProtocolError("node_id troppo lungo");
  return value;
}

/** Corpo di POST /web/register. */
export function registrationPayload({ nodeId, capabilities = [], label = "", browser = "", limits = {} }) {
  const caps = capabilities
    .map((c) => String(c).trim())
    .filter((c) => KNOWN_TASK_TYPES.includes(c));
  return {
    protocol: PROTOCOL_VERSION,
    node_id: assertNodeId(nodeId),
    type: "web-node",
    capabilities: caps,
    label: String(label).slice(0, 64),
    browser: String(browser).slice(0, 128),
    limits: {
      max_context: Math.max(0, Number(limits.max_context) || 0),
      max_payload_bytes: Math.max(0, Number(limits.max_payload_bytes) || 0),
    },
  };
}

/** Valida un task ricevuto dal CP: un client non si fida del server. */
export function parseTask(raw) {
  if (!raw || typeof raw !== "object") throw new ProtocolError("task non valido");
  const taskId = String(raw.task_id ?? "").trim();
  const type = String(raw.type ?? "").trim();
  if (!taskId) throw new ProtocolError("task_id mancante");
  if (!KNOWN_TASK_TYPES.includes(type)) throw new ProtocolError(`tipo di task sconosciuto: ${type}`);
  const constraints = raw.constraints || {};
  return {
    taskId,
    type,
    payload: raw.payload && typeof raw.payload === "object" ? raw.payload : {},
    timeoutMs: Math.max(100, Math.min(120000, Number(constraints.timeout_ms) || 30000)),
    maxTokens: Math.max(0, Number(constraints.max_tokens) || 512),
  };
}

/** Corpo di POST /web/result. */
export function resultPayload({ nodeId, taskId, ok, result = null, error = "", durationMs = null }) {
  return {
    protocol: PROTOCOL_VERSION,
    node_id: assertNodeId(nodeId),
    task_id: String(taskId ?? "").trim(),
    ok: Boolean(ok),
    result: ok ? result : null,
    error: ok ? "" : String(error || "errore sconosciuto").slice(0, 500),
    duration_ms: durationMs === null || durationMs === undefined ? null : Math.max(0, Math.round(durationMs)),
  };
}
