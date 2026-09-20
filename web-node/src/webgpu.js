// SPDX-License-Identifier: Apache-2.0
// web-node/src/webgpu.js
// Rilevamento WebGPU e runtime di modelli locali nel browser.
//
// Due runtime, entrambi OPT-IN e separati dai task estrattivi/euristici:
//   - Transformers.js (@huggingface/transformers) per i task piccoli
//     (embeddings, traduzione, summarization) con backend WebGPU;
//   - WebLLM (@mlc-ai/web-llm) per una chat locale on-device, in un pannello a se'.
//
// Regola ereditata da capabilities.js: il nodo dichiara una capability SOLO
// quando il runtime che la serve e' davvero caricato. Le librerie si importano
// dinamicamente (import()) da un URL configurabile: niente build, il web node
// resta una pagina statica servita dal tailnet. I pesi dei modelli si scaricano
// da Hugging Face la prima volta (o su click per gli embeddings), dietro consenso.

export class ModelRuntimeError extends Error {}

export const TRANSFORMERS_CDN = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1";
export const WEBLLM_CDN = "https://esm.run/@mlc-ai/web-llm";

// ── WebGPU: rilevamento reale ────────────────────────────────────────────────
export function hasWebGpu(env = globalThis) {
  const gpu = env && env.navigator && env.navigator.gpu;
  return Boolean(gpu && typeof gpu.requestAdapter === "function");
}

export async function requestWebGpuAdapter(env = globalThis) {
  if (!hasWebGpu(env)) return null;
  try {
    const adapter = await env.navigator.gpu.requestAdapter();
    if (!adapter) return null;
    const info = adapter.info || {};
    return {
      available: true,
      isFallbackAdapter: Boolean(adapter.isFallbackAdapter),
      vendor: String(info.vendor || ""),
      architecture: String(info.architecture || ""),
      description: String(info.description || ""),
      device: String(info.device || ""),
    };
  } catch {
    return null;
  }
}

// ── Modelli di default (piccoli, sostituibili) ──────────────────────────────
export const DEFAULT_EMBED_MODEL = "Xenova/all-MiniLM-L6-v2";
export const DEFAULT_TRANSLATE_MODEL = "Xenova/nllb-200-distilled-600M";
export const DEFAULT_SUMMARIZE_MODEL = "Xenova/distilbart-cnn-6-6";

// NLLB usa i codici FLORES (eng_Latn, ita_Latn...), non gli ISO 639-1. Copre le
// lingue comuni; per il resto si dichiara il limite invece di tradurre male.
const NLLB_CODES = {
  it: "ita_Latn", en: "eng_Latn", fr: "fra_Latn", de: "deu_Latn",
  es: "spa_Latn", pt: "por_Latn", nl: "nld_Latn", ru: "rus_Cyrl",
  zh: "zho_Hans", ja: "jpn_Jpan",
};

export function nllbLangCode(code) {
  return NLLB_CODES[String(code || "").toLowerCase()] || "";
}

// ── Loader: import dinamico nel browser ─────────────────────────────────────
export function loadTransformers(url = TRANSFORMERS_CDN) {
  return import(url);
}

export function loadWebLlm(url = WEBLLM_CDN) {
  return import(url);
}

// ── Transformers.js ─────────────────────────────────────────────────────────
export function createTransformersRuntime({ transformers, models = {}, onStatus = null } = {}) {
  if (!transformers || typeof transformers.pipeline !== "function") {
    throw new ModelRuntimeError("modulo Transformers.js non disponibile");
  }
  const config = {
    embed: models.embed || DEFAULT_EMBED_MODEL,
    translate: models.translate || DEFAULT_TRANSLATE_MODEL,
    summarize: models.summarize || DEFAULT_SUMMARIZE_MODEL,
  };
  const emit = typeof onStatus === "function" ? onStatus : () => {};
  const loaded = new Map();

  async function ensure(key, task, model) {
    if (loaded.has(key)) return loaded.get(key);
    emit({ phase: "loading", task, model });
    const pipe = await transformers.pipeline(task, model, { device: "webgpu" });
    loaded.set(key, pipe);
    emit({ phase: "ready", task, model });
    return pipe;
  }

  return {
    kind: "transformers.js",

    async embed(texts) {
      const pipe = await ensure("embed", "feature-extraction", config.embed);
      const output = await pipe(Array.isArray(texts) ? texts : [texts],
                                { pooling: "mean", normalize: true });
      return typeof output.tolist === "function" ? output.tolist() : Array.from(output.data);
    },

    async translate(text, { target, source } = {}) {
      const tgt = nllbLangCode(target);
      if (!tgt) throw new ModelRuntimeError(`lingua target non supportata da NLLB: ${target}`);
      const src = nllbLangCode(source) || nllbLangCode("en");
      const pipe = await ensure("translate", "translation", config.translate);
      const out = await pipe(text, { src_lang: src, tgt_lang: tgt });
      const first = Array.isArray(out) ? out[0] : out;
      return String((first && first.translation_text) || "");
    },

    async summarize(text, { max_sentences } = {}) {
      const maxTokens = Math.max(16, Math.min(256, Number(max_sentences || 3) * 24));
      const pipe = await ensure("summarize", "summarization", config.summarize);
      const out = await pipe(text, { max_new_tokens: maxTokens });
      const first = Array.isArray(out) ? out[0] : out;
      return String((first && first.summary_text) || "");
    },
  };
}

// ── WebLLM ──────────────────────────────────────────────────────────────────
export const DEFAULT_WEBLLM_MODEL = "Llama-3.2-1B-Instruct-q4f32_1-MLC";

export function createWebLlmChat({ webllm, model = DEFAULT_WEBLLM_MODEL, onStatus = null } = {}) {
  if (!webllm || typeof webllm.CreateMLCEngine !== "function") {
    throw new ModelRuntimeError("modulo WebLLM non disponibile");
  }
  const emit = typeof onStatus === "function" ? onStatus : () => {};
  let engine = null;

  async function ensureEngine() {
    if (engine) return engine;
    emit({ phase: "loading", model });
    engine = await webllm.CreateMLCEngine(model, {
      initProgressCallback: (report) => emit({ phase: "progress", model, ...(report || {}) }),
    });
    emit({ phase: "ready", model });
    return engine;
  }

  return {
    kind: "web-llm",
    model,

    async generate(messages, { onDelta } = {}) {
      const e = await ensureEngine();
      const reply = await e.chat.completions.create({
        messages,
        stream: typeof onDelta === "function",
      });
      if (typeof onDelta === "function") {
        let text = "";
        for await (const chunk of reply) {
          const delta = chunk && chunk.choices && chunk.choices[0] && chunk.choices[0].delta
            ? chunk.choices[0].delta.content || ""
            : "";
          if (delta) {
            text += delta;
            onDelta(delta, text);
          }
        }
        return text;
      }
      const message = reply && reply.choices && reply.choices[0] && reply.choices[0].message;
      return String((message && message.content) || "");
    },
  };
}
