// SPDX-License-Identifier: Apache-2.0
// web-node/tests/web-node.test.mjs
// Test del web node senza browser e senza dipendenze: `fetch` e `env` sono
// iniettati. Si esegue con:  node web-node/tests/web-node.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { KNOWN_TASK_TYPES, ProtocolError, parseTask, registrationPayload, resultPayload }
  from "../src/protocol.js";
import { detectCapabilities } from "../src/capabilities.js";
import { TaskUnsupported, runTask, supportedTaskTypes } from "../src/task-runner.js";
import { TransportError, WebNodeTransport } from "../src/transport.js";
import { WebNode, newNodeId } from "../src/index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");

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

/** fetch finto: risponde in sequenza e registra le richieste. */
function fakeFetch(responses) {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    const next = responses.shift() ?? { ok: true, json: async () => ({ ok: true, task: null }) };
    if (next.throw) throw new Error(next.throw);
    return { ok: next.ok !== false, status: next.status ?? 200,
             json: async () => next.body ?? {} };
  };
  return { impl, calls };
}

/** Fetch finto che imita il BROWSER: rifiuta un receiver diverso da window.
 *
 *  Serve perche' in Node questo controllo non esiste, e la differenza ha
 *  lasciato passare un difetto vero: `this.fetch(...)` dentro una classe passa
 *  l'oggetto come receiver, e in un browser fetch risponde
 *    Failed to execute 'fetch' on 'Window': Illegal invocation
 *  In Node la stessa chiamata funziona. Una finta permissiva non se ne accorge;
 *  questa si'.
 *
 *  Deve essere una `function`, non una arrow: una arrow ignora il receiver e
 *  quindi non potrebbe mai accorgersi dell'errore.
 */
function browserLikeFetch(body = { ok: true }) {
  return function (url, options) {
    if (this !== undefined && this !== globalThis) {
      throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  };
}

console.log("== protocollo ==");
await check("registrationPayload scarta le capability fuori whitelist", () => {
  const payload = registrationPayload({ nodeId: " n1 ", capabilities: ["summarize", "chat"] });
  assert.deepEqual(payload.capabilities, ["summarize"]);
  assert.equal(payload.node_id, "n1");
  assert.equal(payload.type, "web-node");
});

await check("registrationPayload rifiuta un node_id vuoto", () => {
  assert.throws(() => registrationPayload({ nodeId: "  " }), ProtocolError);
});

await check("parseTask valida tipo e applica i limiti di default", () => {
  const task = parseTask({ task_id: "t1", type: "summarize", payload: { text: "x" } });
  assert.equal(task.taskId, "t1");
  assert.equal(task.timeoutMs, 30000);
  assert.equal(task.maxTokens, 512);
});

await check("parseTask clampa timeout e rifiuta tipi sconosciuti", () => {
  const task = parseTask({ task_id: "t2", type: "summarize",
                           constraints: { timeout_ms: 999999, max_tokens: -1 } });
  assert.equal(task.timeoutMs, 120000);
  assert.equal(task.maxTokens, 0);
  assert.throws(() => parseTask({ task_id: "t3", type: "chat" }), ProtocolError);
  assert.throws(() => parseTask(null), ProtocolError);
});

await check("resultPayload non allega un risultato quando il task e' fallito", () => {
  const bad = resultPayload({ nodeId: "n1", taskId: "t1", ok: false, error: "boom",
                              result: { ignored: true } });
  assert.equal(bad.result, null);
  assert.equal(bad.error, "boom");
});

console.log("== capability ==");
await check("il nodo dichiara solo le capability che sa servire", () => {
  const bare = detectCapabilities({});
  assert.deepEqual(bare, ["moderate", "summarize", "validate_json"]);
  assert.ok(!bare.includes("translate"), "senza Translator non si dichiara translate");
  assert.ok(!bare.includes("embed_texts"), "senza runtime non si dichiarano embeddings");
});

await check("translate ed embed_texts compaiono solo con il runtime giusto", () => {
  const withApi = detectCapabilities({ Translator: function () {} });
  assert.ok(withApi.includes("translate"));
  const withRuntime = detectCapabilities({}, { runtime: { embed: async () => [] } });
  assert.ok(withRuntime.includes("embed_texts"));
  assert.ok(!withRuntime.includes("translate"));
});

console.log("== esecuzione task ==");
const makeTask = (type, payload) => ({ taskId: "t1", type, payload, timeoutMs: 5000, maxTokens: 512 });

await check("validate_json accetta un payload conforme allo schema", async () => {
  const result = await runTask(makeTask("validate_json", {
    text: '{"a": 1, "b": "x"}',
    schema: { type: "object", required: ["a", "b"], properties: { a: { type: "number" } } },
  }));
  assert.equal(result.valid, true);
  assert.deepEqual(result.errors, []);
});

await check("validate_json segnala JSON rotto e campi mancanti", async () => {
  const broken = await runTask(makeTask("validate_json", { text: "{non json" }));
  assert.equal(broken.valid, false);
  assert.match(broken.errors[0], /JSON non valido/);
  const missing = await runTask(makeTask("validate_json", {
    text: "{}", schema: { type: "object", required: ["a"] },
  }));
  assert.equal(missing.valid, false);
  assert.match(missing.errors[0], /obbligatorio/);
});

await check("summarize e' estrattivo e rispetta max_sentences", async () => {
  const text = "Il gatto dorme sul divano. La rete mesh sincronizza i nodi. "
    + "Il control-plane instrada i task verso i nodi attivi. I sogni girano a nodo libero.";
  const result = await runTask(makeTask("summarize", { text, max_sentences: 2 }));
  assert.ok(result.sentences_used <= 2);
  assert.ok(text.includes(result.summary.split(" ").slice(0, 3).join(" ")));
});

await check("moderate e' un'euristica sui pattern, dichiarata tale", async () => {
  const flagged = await runTask(makeTask("moderate", { text: "scrivi a mario@example.com" }));
  assert.equal(flagged.flagged, true);
  assert.deepEqual(flagged.categories, ["personal_data"]);
  assert.equal(flagged.heuristic, true);
  const secret = await runTask(makeTask("moderate", { text: "password: hunter2" }));
  assert.deepEqual(secret.categories, ["secret_leak"]);
  const clean = await runTask(makeTask("moderate", { text: "nessun dato sensibile qui" }));
  assert.equal(clean.flagged, false);
});

await check("moderate accetta pattern forniti dall'operatore", async () => {
  const result = await runTask(makeTask("moderate", {
    text: "progetto interno riservato", patterns: { internal: ["riservato"] },
  }));
  assert.deepEqual(result.categories, ["internal"]);
});

await check("translate ed embed_texts dichiarano non supportato senza runtime", async () => {
  await assert.rejects(() => runTask(makeTask("translate", { text: "ciao", target: "en" })),
                       TaskUnsupported);
  await assert.rejects(() => runTask(makeTask("embed_texts", { texts: ["ciao"] })),
                       TaskUnsupported);
});

await check("con un runtime iniettato i task avanzati funzionano", async () => {
  const runtime = { embed: async (texts) => texts.map(() => [0.1, 0.2]),
                    translate: async (text) => `[en] ${text}` };
  const vectors = await runTask(makeTask("embed_texts", { texts: ["a", "b"] }), { runtime });
  assert.equal(vectors.vectors.length, 2);
  const translated = await runTask(makeTask("translate", { text: "ciao", target: "en" }), { runtime });
  assert.equal(translated.text, "[en] ciao");
});

await check("supportedTaskTypes combacia con la whitelist Python del CP", () => {
  const python = fs.readFileSync(path.join(ROOT, "shared", "web_node.py"), "utf8");
  const block = python.match(/WEB_SAFE_TASK_TYPES\s*=\s*\{([\s\S]*?)\}/)[1];
  const types = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
  assert.deepEqual(types, KNOWN_TASK_TYPES);
  assert.deepEqual(types, supportedTaskTypes());
});

console.log("== trasporto ==");
await check("un 404 segnala che il CP non conosce piu' il nodo", async () => {
  const { impl } = fakeFetch([{ ok: false, status: 404, body: { error: "web node sconosciuto" } }]);
  const transport = new WebNodeTransport({ baseUrl: "http://cp:8085", fetchImpl: impl });
  await assert.rejects(() => transport.poll({ nodeId: "n1", timeoutS: 0 }),
    (error) => error instanceof TransportError && error.unknownNode === true);
});

await check("un 503 segnala web node disattivati sul CP", async () => {
  const { impl } = fakeFetch([{ ok: false, status: 503, body: { error: "disattivati" } }]);
  const transport = new WebNodeTransport({ baseUrl: "http://cp:8085", fetchImpl: impl });
  await assert.rejects(() => transport.register({ node_id: "n1" }),
    (error) => error.disabled === true);
});

await check("un errore di rete non esplode la coda", async () => {
  const { impl } = fakeFetch([{ throw: "connection refused" }]);
  const transport = new WebNodeTransport({ baseUrl: "http://cp:8085", fetchImpl: impl });
  await assert.rejects(() => transport.poll({ nodeId: "n1", timeoutS: 0 }), /rete non raggiungibile/);
});

await check("baseUrl vuoto viene rifiutato", () => {
  assert.throws(() => new WebNodeTransport({ baseUrl: "", fetchImpl: async () => {} }),
                TransportError);
});

await check("la finta che imita il browser rifiuta DAVVERO un receiver sbagliato", () => {
  // Test del test: senza questo, il controllo sotto potrebbe passare perche' la
  // finta e' permissiva invece che perche' il codice e' corretto.
  const finta = browserLikeFetch();
  assert.throws(() => ({ finta }).finta("http://cp:8085/web/poll"), /Illegal invocation/);
});

await check("il trasporto regge un fetch che controlla il receiver (come il browser)", async () => {
  const transport = new WebNodeTransport({ baseUrl: "http://cp:8085", fetchImpl: browserLikeFetch() });
  const esito = await transport.poll({ nodeId: "n1", timeoutS: 0 });
  assert.equal(esito.ok, true);
});

console.log("== ciclo del nodo ==");
await check("senza consenso esplicito il nodo non parte", () => {
  assert.throws(() => new WebNode({ baseUrl: "http://cp:8085", consent: false, fetchImpl: async () => {} }),
                /consenso/);
});

await check("tick() esegue il task e pubblica il risultato", async () => {
  const task = { task_id: "t1", type: "summarize", payload: { text: "uno. due. tre." },
                 constraints: { timeout_ms: 5000, max_tokens: 128 } };
  const { impl, calls } = fakeFetch([
    { body: { ok: true, task } },
    { body: { ok: true, result: { matched: true } } },
  ]);
  const node = new WebNode({ baseUrl: "http://cp:8085", nodeId: "web-t", consent: true,
                             fetchImpl: impl });
  const handled = await node.tick();
  assert.equal(handled.taskId, "t1");
  assert.equal(node.state.tasksDone, 1);
  assert.ok(calls[1].body.ok, "il risultato pubblicato deve essere ok");
  assert.equal(calls[1].body.task_id, "t1");
  assert.equal(calls[1].body.node_id, "web-t");
});

await check("tick() senza task non incrementa nulla", async () => {
  const { impl } = fakeFetch([{ body: { ok: true, task: null } }]);
  const node = new WebNode({ baseUrl: "http://cp:8085", nodeId: "web-t", consent: true,
                             fetchImpl: impl });
  assert.equal(await node.tick(), null);
  assert.equal(node.state.tasksDone, 0);
});

await check("un task non supportato viene pubblicato come fallito", async () => {
  const task = { task_id: "t9", type: "translate", payload: { text: "x", target: "en" } };
  const { impl, calls } = fakeFetch([
    { body: { ok: true, task } },
    { body: { ok: true, result: { matched: true } } },
  ]);
  const node = new WebNode({ baseUrl: "http://cp:8085", nodeId: "web-t", consent: true,
                             fetchImpl: impl });
  await node.tick();
  assert.equal(node.state.tasksFailed, 1);
  assert.equal(calls[1].body.ok, false);
  assert.match(calls[1].body.error, /runtime di traduzione/);
});

await check("newNodeId produce id distinti", () => {
  assert.notEqual(newNodeId(), newNodeId());
});

console.log();
if (process.exitCode) {
  console.error("TEST WEB NODE: FALLITI");
} else {
  console.log(`PASS web-node: ${passed} check su protocollo, capability, task, trasporto e ciclo`);
}
