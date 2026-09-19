// SPDX-License-Identifier: Apache-2.0
// web-node/src/chat.js
// Client di chat verso il control-plane (endpoint OpenAI-compatible /v1).
//
// Perche' e' un modulo e non codice dentro la pagina: la parte che si rompe e'
// il parsing dello streaming SSE, e uno script inline non si puo' testare. Qui
// il parser e' una funzione pura con i suoi test (tests/chat.test.mjs); la
// pagina si limita a disegnare quello che arriva.
//
// Formato REALE del control-plane, verificato sui byte e non dedotto:
//
//   data: {"choices":[{"delta":{"role":"assistant","content":"..."}}]}\n\n
//   data: [DONE]\n\n
//
// Due cose che un client scritto "a intuito" sbaglia:
//  - gli ERRORI a meta' stream arrivano come evento SSE con chiave `error`, non
//    come errore HTTP: chi non li guarda resta appeso ad aspettare per sempre;
//  - il contenuto non arriva per forza token per token: il ramo nativo del CP
//    manda tutto in UN solo delta con finish_reason "stop". Il parser funziona
//    in entrambi i casi senza sapere quale sta usando.

export class ChatError extends Error {
  constructor(message, { status = 0, payload = null } = {}) {
    super(message);
    this.name = "ChatError";
    this.status = status;
    this.payload = payload;
  }
}

/** Porta qualunque forma di URL del CP a una base che finisce con `/v1`. */
export function normalizeBaseUrl(raw) {
  let base = String(raw || "").trim().replace(/\/+$/, "");
  if (!base) throw new ChatError("baseUrl mancante");
  if (!/^https?:\/\//i.test(base)) {
    throw new ChatError("l'URL deve iniziare con http:// o https://");
  }
  if (!/\/v1$/i.test(base)) base += "/v1";
  return base;
}

/** Etichetta da mostrare per un id di modello. Gli id del CP arrivano decorati
 *  ("🕸️ qwen3:8b"): nella richiesta si manda l'id ORIGINALE, nella tendina si
 *  mostra senza decorazione. */
export function prettyModelId(id) {
  const raw = String(id || "");
  return raw.replace(/^[^0-9A-Za-z]+/, "").trim() || raw;
}

/** Parser incrementale di eventi SSE.
 *
 *  Un chunk di rete NON coincide con un evento: puo' spezzarsi in mezzo a una
 *  riga o contenerne tre. Si accumula in un buffer e si emettono solo gli eventi
 *  completi (chiusi da una riga vuota). I CRLF si normalizzano prima, perche'
 *  non tutti i server usano `\n\n`.
 */
export function createSseParser() {
  let buffer = "";
  return function push(chunk) {
    buffer += String(chunk ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const events = [];
    let cut;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = raw.split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("");
      if (data) events.push(data);
    }
    return events;
  };
}

/** Decodifica un evento gia' completo.
 *  `{done}` | `{error}` | `{content, role, finish}` | null (niente da mostrare). */
export function parseSseEvent(data) {
  if (data === "[DONE]") return { done: true };
  let json;
  try {
    json = JSON.parse(data);
  } catch {
    return null;                       // evento non JSON: si ignora, non esplode
  }
  if (json && json.error) return { error: String(json.error) };
  const choice = ((json && json.choices) || [])[0] || null;
  if (!choice) return null;
  const delta = choice.delta || {};
  return {
    content: typeof delta.content === "string" ? delta.content : "",
    role: delta.role || null,
    finish: choice.finish_reason || null,
  };
}

/** Itera i pezzi di testo di uno stream: browser e Node espongono la stessa API
 *  (web ReadableStream), e i test passano un finto `body` asincrono. */
export async function* readChunks(response) {
  const body = response && response.body;
  if (!body) return;
  const decoder = new TextDecoder();
  if (typeof body.getReader === "function") {
    const reader = body.getReader();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      yield decoder.decode(value, { stream: true });
    }
    return;
  }
  for await (const value of body) {
    yield typeof value === "string" ? value : decoder.decode(value, { stream: true });
  }
}

export class ChatClient {
  constructor({ baseUrl, model, fetchImpl = globalThis.fetch, timeoutMs = 180000 } = {}) {
    if (typeof fetchImpl !== "function") throw new ChatError("fetch non disponibile");
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.model = String(model || "").trim();
    // Stesso motivo di transport.js: senza il bind, `this.fetch(...)` in un
    // browser fa fallire il brand check di fetch con "Illegal invocation".
    this.fetch = fetchImpl.bind(globalThis);
    this.timeoutMs = Math.max(1000, Number(timeoutMs) || 180000);
  }

  /** Gli id dei modelli, nell'ordine in cui il CP li propone. */
  async listModels() {
    let response;
    try {
      response = await this.fetch(`${this.baseUrl}/models`);
    } catch (error) {
      throw new ChatError(`rete non raggiungibile: ${error.message || error}`);
    }
    if (!response.ok) throw new ChatError(`HTTP ${response.status}`, { status: response.status });
    const body = await response.json().catch(() => null);
    return (((body && body.data) || []).map((m) => String(m.id)).filter(Boolean));
  }

  /** Invia la conversazione e restituisce `{text, chunks}`.
   *  `onDelta(pezzo, testoCompleto)` viene chiamato a ogni pezzo per il vivo. */
  async send(messages, { onDelta } = {}) {
    if (!this.model) throw new ChatError("nessun modello selezionato");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response;
    try {
      response = await this.fetch(`${this.baseUrl}/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: this.model, messages, stream: true }),
        signal: controller.signal,
      });
    } catch (error) {
      clearTimeout(timer);
      throw new ChatError(`rete non raggiungibile: ${error.message || error}`);
    }

    try {
      if (!response.ok) throw new ChatError(`HTTP ${response.status}`, { status: response.status });
      const parser = createSseParser();
      let text = "";
      let chunks = 0;
      for await (const piece of readChunks(response)) {
        for (const event of parser(piece)) {
          const parsed = parseSseEvent(event);
          if (!parsed) continue;
          if (parsed.done) return { text, chunks };
          if (parsed.error) throw new ChatError(parsed.error);
          if (parsed.content) {
            text += parsed.content;
            chunks += 1;
            if (typeof onDelta === "function") onDelta(parsed.content, text);
          }
        }
      }
      return { text, chunks };            // stream chiuso senza [DONE]
    } finally {
      clearTimeout(timer);
    }
  }
}
