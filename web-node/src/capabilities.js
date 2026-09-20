// SPDX-License-Identifier: Apache-2.0
// web-node/src/capabilities.js
// Rilevamento ONESTO delle capability del browser. Regola: una capability si
// dichiara solo se il nodo sa davvero servirla. Meglio un nodo che dichiara
// poco e lavora, di uno che promette embeddings e restituisce rumore — il CP
// instrada i task in base a questa dichiarazione.

import { KNOWN_TASK_TYPES } from "./protocol.js";

export const ALWAYS_AVAILABLE = ["moderate", "summarize", "validate_json"];

/**
 * @param {object} env    iniettabile nei test (default: contesto reale)
 * @param {object} opts   `runtime` opzionale con embed()/translate()
 * @returns {string[]}    sottoinsieme di KNOWN_TASK_TYPES, in ordine stabile
 */
export function detectCapabilities(env = globalThis, opts = {}) {
  const found = new Set(ALWAYS_AVAILABLE);

  // Traduzione: o l'API nativa del browser (Chrome/Edge), o un runtime di
  // modelli iniettato (es. Transformers.js). Senza nessuno dei due non la
  // dichiariamo: non e' garantita su tutti i motori.
  if (env && (typeof env.Translator !== "undefined" || typeof env.translation !== "undefined")) {
    found.add("translate");
  }
  if (opts.runtime && typeof opts.runtime.translate === "function") {
    found.add("translate");
  }

  // Embeddings: servono davvero un runtime di modelli (transformers.js, WebLLM)
  // fornito dall'host. Senza, il nodo NON dichiara la capability.
  if (opts.runtime && typeof opts.runtime.embed === "function") {
    found.add("embed_texts");
  }

  return KNOWN_TASK_TYPES.filter((type) => found.has(type));
}

export function describeEnvironment(env = globalThis) {
  const nav = (env && env.navigator) || {};
  const agent = nav.userAgent || "unknown";
  return {
    browser: `${agent} | wasm=${env && typeof env.WebAssembly !== "undefined"}`
      + ` | webgpu=${Boolean(nav.gpu)}`,
    // Il contesto decide limiti prudenti: una scheda di browser non ha la RAM
    // di un nodo con GPU dedicata.
    limits: {
      max_context: 4096,
      max_payload_bytes: Number(env?.__WEB_NODE_MAX_PAYLOAD__) || 64 * 1024,
    },
  };
}
