// SPDX-License-Identifier: Apache-2.0
// web-node/src/task-runner.js
// Esecuzione dei task web-safe nel browser. Nessun modello scaricato qui: gli
// handler che richiedono un runtime di modelli (embeddings, traduzione) lo
// pretendono iniettato, altrimenti dichiarano il task non supportato invece di
// restituire un risultato finto.

import { ProtocolError } from "./protocol.js";

export class TaskUnsupported extends Error {}

const STOPWORDS = new Set(("a ad al alla allo ai agli alle anche come con cui da dal dalla " +
  "dei del della di e ed gli ha hai hanno i il in io la le lo ma mi ne nei nel nella no non " +
  "o per piu' piu se si sono su sul sulla tra un una uno vi voi che chi cosa dove quando " +
  "the a an and or of to in is are was were for on with at by from this that it as be").split(" "));

/** Limita l'attesa di un handler asincrono. Non puo' interrompere lavoro
 *  sincrono gia' in corso nel thread: e' un tetto, non una prelazione. */
function withTimeout(promise, timeoutMs) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new ProtocolError(`task oltre il timeout di ${timeoutMs}ms`)), timeoutMs)),
  ]);
}

// ── validate_json ─────────────────────────────────────────────────────────────
// Sottoinsieme minimo di validazione strutturale: type/required/properties/items.
// NON e' JSON Schema completo e non lo pretende: per gli schemi complessi il
// payload va validato sul nodo, non in una scheda del browser.
const TYPES = {
  object: (v) => v !== null && typeof v === "object" && !Array.isArray(v),
  array: Array.isArray,
  string: (v) => typeof v === "string",
  number: (v) => typeof v === "number" && Number.isFinite(v),
  integer: (v) => Number.isInteger(v),
  boolean: (v) => typeof v === "boolean",
};

function validateShape(value, schema, path, errors) {
  if (!schema || typeof schema !== "object") return;
  if (schema.type && TYPES[schema.type] && !TYPES[schema.type](value)) {
    errors.push(`${path}: atteso ${schema.type}`);
    return;
  }
  if (schema.type === "object" || schema.properties) {
    for (const key of schema.required || []) {
      if (!value || !(key in value)) errors.push(`${path}.${key}: campo obbligatorio mancante`);
    }
    for (const [key, sub] of Object.entries(schema.properties || {})) {
      if (value && key in value) validateShape(value[key], sub, `${path}.${key}`, errors);
    }
  }
  if (schema.type === "array" && schema.items && Array.isArray(value)) {
    value.forEach((item, index) => validateShape(item, schema.items, `${path}[${index}]`, errors));
  }
}

function runValidateJson(task) {
  const { text, schema } = task.payload;
  if (typeof text !== "string") throw new ProtocolError("validate_json: payload.text mancante");
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { valid: false, parsed: null, errors: [`JSON non valido: ${error.message}`] };
  }
  const errors = [];
  if (schema) validateShape(parsed, schema, "$", errors);
  return { valid: errors.length === 0, parsed, errors };
}

// ── summarize ─────────────────────────────────────────────────────────────────
// Estrattivo, senza modello: frequenza dei termini con stopword rimosse.
// Dichiarato per quello che e': non e' un riassunto astrattivo.
function splitSentences(text) {
  return text.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

function runSummarize(task) {
  const { text, max_sentences: maxSentences } = task.payload;
  if (typeof text !== "string") throw new ProtocolError("summarize: payload.text mancante");
  const limit = Math.max(1, Math.min(10, Number(maxSentences) || 3));
  const sentences = splitSentences(text);
  if (sentences.length <= limit) return { summary: text.trim(), sentences_used: sentences.length };
  const frequency = new Map();
  for (const word of text.toLowerCase().match(/[a-z0-9']+/g) || []) {
    if (word.length < 3 || STOPWORDS.has(word)) continue;
    frequency.set(word, (frequency.get(word) || 0) + 1);
  }
  const scored = sentences.map((sentence, index) => {
    const words = sentence.toLowerCase().match(/[a-z0-9']+/g) || [];
    const score = words.reduce((sum, w) => sum + (frequency.get(w) || 0), 0) / Math.max(1, words.length);
    return { sentence, index, score };
  });
  const best = scored.slice().sort((a, b) => b.score - a.score).slice(0, limit);
  best.sort((a, b) => a.index - b.index);
  return { summary: best.map((s) => s.sentence).join(" "), sentences_used: best.length };
}

// ── moderate ──────────────────────────────────────────────────────────────────
// EURISTICA DI PATTERN, dichiarata tale: NON e' un classificatore di contenuti
// e non va usata come filtro di policy. Il default cerca fughe di segreti e
// dati personali (utile e verificabile senza modelli); l'operatore puo' passare
// `payload.patterns` {categoria: [regex]} per le proprie policy. Una
// moderazione vera richiede un modello o una lista curata dal cliente.
const DEFAULT_PATTERNS = {
  secret_leak: ["\\b(api[_-]?key|secret|password|passwd|token)\\b\\s*[:=]\\s*\\S+",
                "-----BEGIN [A-Z ]*PRIVATE KEY-----"],
  personal_data: ["\\b[\\w.+-]+@[\\w-]+\\.[\\w.]{2,}\\b",
                  "\\b\\d{4}[ -]?\\d{4}[ -]?\\d{4}[ -]?\\d{4}\\b",
                  "\\b[A-Z]{2}\\d{2}[A-Z0-9]{10,30}\\b"],
};

function runModerate(task) {
  const { text, patterns } = task.payload;
  if (typeof text !== "string") throw new ProtocolError("moderate: payload.text mancante");
  const catalogue = patterns && typeof patterns === "object" ? patterns : DEFAULT_PATTERNS;
  const categories = [];
  const matches = [];
  for (const [category, list] of Object.entries(catalogue)) {
    for (const pattern of list || []) {
      let regex;
      try {
        regex = new RegExp(pattern, "gi");
      } catch {
        throw new ProtocolError(`moderate: pattern non valido in ${category}`);
      }
      const found = text.match(regex);
      if (found) {
        categories.push(category);
        matches.push({ category, hits: found.length });
      }
    }
  }
  return {
    flagged: categories.length > 0,
    categories: [...new Set(categories)].sort(),
    matches,
    // La confidenza NON e' una probabilita': e' solo quante categorie hanno
    // colpito, per ordinare la revisione umana.
    heuristic: true,
  };
}

// ── translate / embed_texts ───────────────────────────────────────────────────
// Richiedono un runtime di modelli che il web node NON include: se l'host non
// lo inietta, il task e' non supportato invece di restituire un finto risultato.
async function runTranslate(task, { runtime }) {
  const { text, target, source } = task.payload;
  if (typeof text !== "string") throw new ProtocolError("translate: payload.text mancante");
  if (runtime && typeof runtime.translate === "function") {
    return { text: await runtime.translate(text, { target, source }), engine: "host-runtime" };
  }
  if (typeof globalThis.Translator !== "undefined") {
    const translator = await globalThis.Translator.create({ sourceLanguage: source, targetLanguage: target });
    const translated = await translator.translate(text);
    return { text: translated, engine: "browser-translator-api" };
  }
  throw new TaskUnsupported("translate: nessun runtime di traduzione disponibile");
}

async function runEmbedTexts(task, { runtime }) {
  const { texts } = task.payload;
  if (!Array.isArray(texts)) throw new ProtocolError("embed_texts: payload.texts deve essere una lista");
  if (!runtime || typeof runtime.embed !== "function") {
    throw new TaskUnsupported("embed_texts: nessun runtime di embeddings iniettato");
  }
  return { vectors: await runtime.embed(texts), engine: "host-runtime" };
}

const HANDLERS = {
  embed_texts: runEmbedTexts,
  moderate: runModerate,
  summarize: runSummarize,
  translate: runTranslate,
  validate_json: runValidateJson,
};

/** Tipi effettivamente servibili da questo modulo (deve combaciare con le
 *  capability dichiarate: dichiarare piu' di cosi' sarebbe disonesto). */
export function supportedTaskTypes() {
  return Object.keys(HANDLERS).sort();
}

export async function runTask(task, { runtime } = {}) {
  const handler = HANDLERS[task.type];
  if (!handler) throw new TaskUnsupported(`nessun handler per il tipo ${task.type}`);
  return await withTimeout(Promise.resolve().then(() => handler(task, { runtime })), task.timeoutMs);
}
