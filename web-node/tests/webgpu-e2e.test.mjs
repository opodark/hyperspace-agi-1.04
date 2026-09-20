// SPDX-License-Identifier: Apache-2.0
// web-node/tests/webgpu-e2e.test.mjs
// Flusso end-to-end SIMULATO del runtime WebGPU: nessun browser e nessuna
// libreria reale, ma i moduli veri (WebNode, task-runner, webgpu) piu' un
// transformers finto. Verifica anche che index.html cabli il runtime nel nodo.
//
//   node web-node/tests/webgpu-e2e.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { detectCapabilities } from "../src/capabilities.js";
import { WebNode } from "../src/index.js";
import { createTransformersRuntime } from "../src/webgpu.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_NODE = path.resolve(HERE, "..");

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

/** transformers finto: solo feature-extraction, come lo userebbe embed. */
const fakeTransformers = {
  calls: [],
  pipeline: async (task, model, options) => {
    fakeTransformers.calls.push({ task, model, options });
    return async (texts) => ({
      tolist: () => (Array.isArray(texts) ? texts : [texts]).map(() => [0.1, 0.2]),
    });
  },
};

/** fetch finto: risponde in sequenza e registra le richieste (come web-node.test). */
function fakeFetch(responses) {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    const next = responses.shift() ?? { ok: true, json: async () => ({ ok: true, task: null }) };
    if (next.throw) throw new Error(next.throw);
    return { ok: next.ok !== false, status: next.status ?? 200, json: async () => next.body ?? {} };
  };
  return { impl, calls };
}

console.log("== index.html cabla il runtime ==");
await check("index.html importa webgpu.js e inietta il runtime nel nodo", () => {
  const html = fs.readFileSync(path.join(WEB_NODE, "index.html"), "utf8");
  assert.match(html, /from "\.\/src\/webgpu\.js"/);
  assert.match(html, /runtime: modelRuntime/);
  assert.match(html, /detectCapabilities\(globalThis, \{ runtime: modelRuntime \}\)/);
  assert.match(html, /id="loadLocalModels"/);
  assert.match(html, /id="loadLlm"/);
});

console.log("== flusso: carica runtime -> capability -> task embed ==");
await check("un runtime Transformers.js fa dichiarare embed_texts e translate", () => {
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  const caps = detectCapabilities({}, { runtime });
  assert.ok(caps.includes("embed_texts"));
  assert.ok(caps.includes("translate"));
});

await check("WebNode con runtime dichiara embed_texts anche nella registrazione", () => {
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  const node = new WebNode({ baseUrl: "http://cp:8085", consent: true, runtime,
                             fetchImpl: async () => {} });
  assert.ok(node.capabilities.includes("embed_texts"));
  assert.ok(node.registration.capabilities.includes("embed_texts"));
});

await check("tick() esegue un embed_texts reale e pubblica i vettori con l'engine", async () => {
  fakeTransformers.calls.length = 0;
  const runtime = createTransformersRuntime({ transformers: fakeTransformers });
  const task = { task_id: "te1", type: "embed_texts", payload: { texts: ["ciao", "mondo"] },
                 constraints: { timeout_ms: 5000, max_tokens: 128 } };
  const { impl, calls } = fakeFetch([
    { body: { ok: true, task } },
    { body: { ok: true, result: { matched: true } } },
  ]);
  const node = new WebNode({ baseUrl: "http://cp:8085", nodeId: "web-e2e", consent: true,
                             runtime, fetchImpl: impl });
  const handled = await node.tick();
  assert.equal(handled.type, "embed_texts");
  assert.equal(node.state.tasksDone, 1);
  assert.ok(calls[1].body.ok, "il risultato pubblicato deve essere ok");
  assert.equal(calls[1].body.result.engine, "transformers.js");
  assert.equal(calls[1].body.result.vectors.length, 2);
  assert.equal(fakeTransformers.calls[0].task, "feature-extraction");
  assert.equal(fakeTransformers.calls[0].options.device, "webgpu");
});

await check("senza runtime il nodo NON dichiara embed_texts (onesto)", () => {
  const node = new WebNode({ baseUrl: "http://cp:8085", consent: true, fetchImpl: async () => {} });
  assert.ok(!node.capabilities.includes("embed_texts"));
});

console.log();
if (process.exitCode) {
  console.error("TEST WEBGPU E2E: FALLITI");
} else {
  console.log(`PASS webgpu-e2e: ${passed} check sul flusso runtime -> capability -> task`);
}
