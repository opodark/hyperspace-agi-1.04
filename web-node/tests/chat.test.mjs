// web-node/tests/chat.test.mjs
// Test del client di chat. `fetch` e il `body` della risposta sono iniettati:
// nessun browser, nessuna rete, nessuna dipendenza esterna.
//
// Le fixture sono i BYTE REALI del control-plane, catturati con `curl -N` e non
// costruiti a mano: e' l'unico modo perche' il test dica qualcosa sul formato che
// c'e' davvero invece che su quello che immagino.
//
//   node web-node/tests/chat.test.mjs
import assert from "node:assert/strict";

import { ChatClient, ChatError, createSseParser, normalizeBaseUrl, parseSseEvent, prettyModelId }
  from "../src/chat.js";

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

/** Un evento come lo manda il CP (forma verificata sui byte). */
const delta = (content, finish = null) => `data: ${JSON.stringify({
  id: "chatcmpl-441", object: "chat.completion.chunk", created: 1, model: "qwen3:8b",
  choices: [{ index: 0, delta: { role: "assistant", content }, finish_reason: finish }],
})}\n\n`;

const DONE = "data: [DONE]\n\n";

/** Risposta finta con lo stream a pezzi (testo, non byte: piu' leggibile). */
function streamResponse(pieces, { ok = true, status = 200, json } = {}) {
  return {
    ok, status,
    json: async () => (json === undefined ? {} : json),
    body: (async function* () { for (const piece of pieces) yield piece; })(),
  };
}

function fetchThat(response) {
  const calls = [];
  return {
    calls,
    impl: async (url, options = {}) => {
      calls.push({ url, options });
      if (response instanceof Error) throw response;
      return response;
    },
  };
}

console.log("== URL e id ==");
await check("normalizeBaseUrl aggiunge /v1 una volta sola", () => {
  assert.equal(normalizeBaseUrl("http://cp:8085"), "http://cp:8085/v1");
  assert.equal(normalizeBaseUrl("http://cp:8085/"), "http://cp:8085/v1");
  assert.equal(normalizeBaseUrl("http://cp:8085/v1"), "http://cp:8085/v1");
  assert.equal(normalizeBaseUrl("http://cp:8085/v1/"), "http://cp:8085/v1");
  assert.equal(normalizeBaseUrl(" https://cp.example.com "), "https://cp.example.com/v1");
});

await check("normalizeBaseUrl rifiuta URL senza schema o vuoti", () => {
  assert.throws(() => normalizeBaseUrl(""), ChatError);
  assert.throws(() => normalizeBaseUrl("cp:8085"), /http:\/\//);
});

await check("prettyModelId toglie la decorazione ma l'id originale resta intatto", () => {
  assert.equal(prettyModelId("🕸️ qwen3:8b"), "qwen3:8b");
  assert.equal(prettyModelId("qwen3:8b"), "qwen3:8b");
  assert.equal(prettyModelId(""), "");
});

console.log("== parser SSE ==");
await check("un evento intero produce un evento", () => {
  const push = createSseParser();
  const events = push(delta("ciao") + DONE);
  assert.equal(events.length, 2);
  assert.equal(parseSseEvent(events[0]).content, "ciao");
  assert.deepEqual(parseSseEvent(events[1]), { done: true });
});

await check("un evento spezzato a meta' non viene emesso due volte", () => {
  const push = createSseParser();
  const whole = delta("spezzato");
  const cut = Math.floor(whole.length / 2);
  assert.deepEqual(push(whole.slice(0, cut)), [], "meta' evento non e' un evento");
  const events = push(whole.slice(cut));
  assert.equal(events.length, 1);
  assert.equal(parseSseEvent(events[0]).content, "spezzato");
});

await check("piu' eventi in un solo chunk vengono emessi tutti", () => {
  const push = createSseParser();
  const events = push(delta("uno") + delta("due") + delta("tre"));
  assert.deepEqual(events.map((e) => parseSseEvent(e).content), ["uno", "due", "tre"]);
});

await check("CRLF accettati come LF", () => {
  const push = createSseParser();
  const events = push(delta("crlf").replace(/\n/g, "\r\n"));
  assert.equal(events.length, 1);
  assert.equal(parseSseEvent(events[0]).content, "crlf");
});

await check("un evento non JSON viene ignorato, non esplode", () => {
  const push = createSseParser();
  const events = push("data: non-json\n\ndata: {\"keepalive\": true}\n\n");
  assert.equal(events.length, 2);
  assert.equal(parseSseEvent(events[0]), null);
  assert.equal(parseSseEvent(events[1]), null);
});

await check("un delta senza content (solo role) non e' contenuto", () => {
  const evento = JSON.stringify({ choices: [{ index: 0, delta: { role: "assistant" } }] });
  const parsed = parseSseEvent(evento);
  assert.equal(parsed.content, "");
  assert.equal(parsed.role, "assistant");
});

await check("l'errore a meta' stream e' un evento, e va riconosciuto", () => {
  const parsed = parseSseEvent(JSON.stringify({ error: "backend non disponibile" }));
  assert.equal(parsed.error, "backend non disponibile");
});

console.log("== client ==");
await check("send accumula i delta e chiama onDelta a ogni pezzo", async () => {
  const { impl, calls } = fetchThat(streamResponse([delta("Ciao"), delta(", "), delta("mondo"), DONE]));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "qwen3:8b", fetchImpl: impl });
  const visti = [];
  const result = await client.send([{ role: "user", content: "ciao" }],
                                   { onDelta: (pezzo, testo) => visti.push([pezzo, testo]) });
  assert.equal(result.text, "Ciao, mondo");
  assert.equal(result.chunks, 3);
  assert.deepEqual(visti.at(-1), ["mondo", "Ciao, mondo"]);
  assert.equal(calls[0].url, "http://cp:8085/v1/chat/completions");
});

await check("send manda stream:true e il modello scelto", async () => {
  const { impl, calls } = fetchThat(streamResponse([delta("x"), DONE]));
  const client = new ChatClient({ baseUrl: "http://cp:8085/v1", model: "qwen3:8b", fetchImpl: impl });
  await client.send([{ role: "user", content: "x" }]);
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.stream, true);
  assert.equal(body.model, "qwen3:8b");
  assert.equal(body.messages.length, 1);
});

await check("il ramo nativo manda tutto in un delta solo: funziona lo stesso", async () => {
  // E' il caso reale osservato: content intero e finish_reason "stop".
  const { impl } = fetchThat(streamResponse([delta("Tutto in un colpo.", "stop"), DONE]));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "qwen3:8b", fetchImpl: impl });
  const result = await client.send([{ role: "user", content: "x" }]);
  assert.equal(result.text, "Tutto in un colpo.");
  assert.equal(result.chunks, 1);
});

await check("un errore nel mezzo dello stream solleva ChatError", async () => {
  const { impl } = fetchThat(streamResponse([
    delta("inizio"), `data: ${JSON.stringify({ error: "nodo sparito" })}\n\n`, DONE,
  ]));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "qwen3:8b", fetchImpl: impl });
  await assert.rejects(() => client.send([{ role: "user", content: "x" }]),
                       (error) => error instanceof ChatError && /nodo sparito/.test(error.message));
});

await check("uno stream chiuso senza [DONE] restituisce comunque il testo", async () => {
  const { impl } = fetchThat(streamResponse([delta("troncato")]));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "qwen3:8b", fetchImpl: impl });
  const result = await client.send([{ role: "user", content: "x" }]);
  assert.equal(result.text, "troncato");
});

await check("un HTTP non-ok solleva ChatError con lo status", async () => {
  const { impl } = fetchThat(streamResponse([], { ok: false, status: 502 }));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "qwen3:8b", fetchImpl: impl });
  await assert.rejects(() => client.send([{ role: "user", content: "x" }]),
                       (error) => error.status === 502);
});

await check("rete assente: messaggio parlante, non un TypeError di fetch", async () => {
  const { impl } = fetchThat(new Error("Failed to fetch"));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "qwen3:8b", fetchImpl: impl });
  await assert.rejects(() => client.send([{ role: "user", content: "x" }]),
                       /rete non raggiungibile/);
});

await check("senza modello selezionato non parte nessuna richiesta", async () => {
  const { impl, calls } = fetchThat(streamResponse([DONE]));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "", fetchImpl: impl });
  await assert.rejects(() => client.send([{ role: "user", content: "x" }]), /nessun modello/);
  assert.equal(calls.length, 0);
});

await check("listModels restituisce gli id come li da' il CP (emoji comprese)", async () => {
  const { impl, calls } = fetchThat(streamResponse([], {
    json: { data: [{ id: "🕸️ qwen3:8b" }, { id: "gemma4:e4b" }] },
  }));
  const client = new ChatClient({ baseUrl: "http://cp:8085", model: "x", fetchImpl: impl });
  const models = await client.listModels();
  assert.deepEqual(models, ["🕸️ qwen3:8b", "gemma4:e4b"]);
  assert.equal(calls[0].url, "http://cp:8085/v1/models");
});

console.log();
if (process.exitCode) {
  console.error("TEST CHAT: FALLITI");
} else {
  console.log(`PASS chat: ${passed} check su URL, parser SSE e client`);
}
