// SPDX-License-Identifier: Apache-2.0
// web-node/tests/webgpu.test.mjs
// Test del rilevamento WebGPU e dei runtime di modelli locali, SENZA browser e
// senza librerie reali: adapter, pipeline e motore WebLLM sono finti e iniettati.
//
//   node web-node/tests/webgpu.test.mjs
import assert from "node:assert/strict";

import { ModelRuntimeError, createTransformersRuntime, createWebLlmChat,
         hasWebGpu, nllbLangCode, requestWebGpuAdapter } from "../src/webgpu.js";

let passed = 0;
async function check(label, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`ok   ${label}`);
  } catch (error) {
    console.error(`FAIL ${label}: ${error.message}`);
    process.exitCode = 1;
  }
}

const envWithGpu = (adapter) => ({ navigator: { gpu: { requestAdapter: async () => adapter } } });
const envWithoutGpu = { navigator: {} };

console.log("== rilevamento WebGPU ==");
await check("hasWebGpu riconosce un navigator.gpu reale", () => {
  assert.equal(hasWebGpu(envWithGpu({})), true);
  assert.equal(hasWebGpu(envWithoutGpu), false);
  assert.equal(hasWebGpu(null), false);
});

await check("requestWebGpuAdapter restituisce i dettagli dell'adattatore", async () => {
  const info = await requestWebGpuAdapter(envWithGpu({
    isFallbackAdapter: false,
    info: { vendor: "nvidia", architecture: "ada", description: "RTX 4070", device: "gpu" },
  }));
  assert.equal(info.available, true);
  assert.equal(info.vendor, "nvidia");
  assert.equal(info.description, "RTX 4070");
  assert.equal(info.isFallbackAdapter, false);
});

await check("requestWebGpuAdapter senza adattatore o con errore torna null", async () => {
  assert.equal(await requestWebGpuAdapter(envWithoutGpu), null);
  assert.equal(await requestWebGpuAdapter(envWithGpu(null)), null);
  assert.equal(await requestWebGpuAdapter({
    navigator: { gpu: { requestAdapter: async () => { throw new Error("no"); } } },
  }), null);
});

console.log("== codici lingua NLLB ==");
await check("nllbLangCode mappa le lingue comuni e rifiuta il resto", () => {
  assert.equal(nllbLangCode("it"), "ita_Latn");
  assert.equal(nllbLangCode("EN"), "eng_Latn");
  assert.equal(nllbLangCode("zh"), "zho_Hans");
  assert.equal(nllbLangCode("xx"), "");
  assert.equal(nllbLangCode(""), "");
});

// ── Transformers.js runtime ────────────────────────────────────────────────
const fakeTransformers = {
  calls: [],
  pipeline: async (task, model, options) => {
    fakeTransformers.calls.push({ task, model, options });
    return async () => {
      if (task === "feature-extraction") return { tolist: () => [[1, 2, 3]] };
      if (task === "translation") return [{ translation_text: "ciao" }];
      if (task === "summarization") return [{ summary_text: "riassunto" }];
      throw new Error(`task finto sconosciuto: ${task}`);
    };
  },
};

console.log("== runtime Transformers.js ==");
await check("createTransformersRuntime rifiuta un modulo senza pipeline", () => {
  assert.throws(() => createTransformersRuntime({ transformers: {} }), ModelRuntimeError);
  assert.throws(() => createTransformersRuntime({}), ModelRuntimeError);
});

await check("embed usa feature-extraction su WebGPU e restituisce vettori", async () => {
  fakeTransformers.calls.length = 0;
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  const vectors = await runtime.embed(["a", "b"]);
  assert.deepEqual(vectors, [[1, 2, 3]]);
  assert.equal(fakeTransformers.calls.length, 1);
  assert.equal(fakeTransformers.calls[0].task, "feature-extraction");
  assert.equal(fakeTransformers.calls[0].options.device, "webgpu");
});

await check("la pipeline viene caricata una sola volta (cache)", async () => {
  fakeTransformers.calls.length = 0;
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  await runtime.embed(["x"]);
  await runtime.embed(["y"]);
  assert.equal(fakeTransformers.calls.length, 1);
});

await check("translate mappa i codici lingua e restituisce il testo", async () => {
  fakeTransformers.calls.length = 0;
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  assert.equal(await runtime.translate("hola", { source: "es", target: "it" }), "ciao");
  assert.equal(fakeTransformers.calls[0].task, "translation");
});

await check("translate rifiuta una lingua target non mappata", async () => {
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  await assert.rejects(() => runtime.translate("x", { target: "xx" }), ModelRuntimeError);
});

await check("summarize usa summarization e restituisce il riassunto", async () => {
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  assert.equal(await runtime.summarize("testo lungo", { max_sentences: 2 }), "riassunto");
});

await check("onStatus segnala loading e ready in ordine", async () => {
  fakeTransformers.calls.length = 0;
  const seen = [];
  const runtime = createTransformersRuntime({
    transformers: fakeTransformers,
    onStatus: (s) => seen.push(s.phase),
  });
  await runtime.embed(["x"]);
  assert.deepEqual(seen, ["loading", "ready"]);
});

// ── WebLLM chat ────────────────────────────────────────────────────────────
const fakeWebLlm = {
  engineCalls: [],
  CreateMLCEngine: async (model) => {
    fakeWebLlm.engineCalls.push({ model });
    return {
      chat: { completions: { create: async ({ stream }) => {
        if (stream) return (async function* () {
          yield { choices: [{ delta: { content: "ok" } }] };
        })();
        return { choices: [{ message: { content: "risposta" } }] };
      } } },
    };
  },
};

console.log("== WebLLM ==");
await check("createWebLlmChat rifiuta un modulo senza CreateMLCEngine", () => {
  assert.throws(() => createWebLlmChat({ webllm: {} }), ModelRuntimeError);
});

await check("generate senza stream restituisce il contenuto e crea il motore una volta", async () => {
  fakeWebLlm.engineCalls.length = 0;
  const chat = createWebLlmChat({ webllm: fakeWebLlm, model: "m" });
  assert.equal(await chat.generate([{ role: "user", content: "x" }]), "risposta");
  assert.equal(await chat.generate([{ role: "user", content: "y" }]), "risposta");
  assert.equal(fakeWebLlm.engineCalls.length, 1);
  assert.equal(fakeWebLlm.engineCalls[0].model, "m");
});

await check("generate in streaming emette i pezzi e restituisce il testo intero", async () => {
  const chat = createWebLlmChat({ webllm: fakeWebLlm, model: "m" });
  const seen = [];
  const text = await chat.generate([{ role: "user", content: "x" }], {
    onDelta: (piece, whole) => seen.push([piece, whole]),
  });
  assert.equal(text, "ok");
  assert.deepEqual(seen, [["ok", "ok"]]);
});

console.log();
if (process.exitCode) {
  console.error("TEST WEBGPU: FALLITI");
} else {
  console.log(`PASS webgpu: ${passed} check su rilevamento WebGPU, Transformers.js e WebLLM`);
}
