// SPDX-License-Identifier: Apache-2.0
// web-node/src/transport.js
// Trasporto HTTP verso il control-plane. `fetchImpl` e' iniettabile: il modulo
// resta testabile in Node senza un browser. Nessuna dipendenza esterna.

export class TransportError extends Error {
  constructor(message, { status = 0, payload = null, unknownNode = false, disabled = false } = {}) {
    super(message);
    this.name = "TransportError";
    this.status = status;
    this.payload = payload;
    this.unknownNode = unknownNode;   // il CP non ci conosce piu': serve re-register
    this.disabled = disabled;         // web node spenti sul CP: non ha senso ritentare
  }
}

export class WebNodeTransport {
  constructor({ baseUrl, baseUrls, fetchImpl = globalThis.fetch, timeoutBufferMs = 5000 } = {}) {
    if (typeof fetchImpl !== "function") throw new TransportError("fetch non disponibile");
    // baseUrls (lista) ha la precedenza su baseUrl (singolo): il fallback prova
    // gli URL in ordine e passa al successivo solo su errore di rete/timeout.
    const list = Array.isArray(baseUrls) && baseUrls.length ? baseUrls : (baseUrl ? [baseUrl] : []);
    this.baseUrls = list.map((u) => String(u || "").replace(/\/+$/, "")).filter(Boolean);
    if (!this.baseUrls.length) throw new TransportError("baseUrl mancante");
    // `fetch` va invocata con `this` = window. Chiamarla come `this.fetch(...)`
    // le passerebbe QUESTO oggetto come receiver, e il browser la rifiuta con
    // "Failed to execute 'fetch' on 'Window': Illegal invocation" — che arriva
    // qui come "rete non raggiungibile" e sembra un problema di rete.
    // In Node non succede (undici non controlla il receiver), quindi un test che
    // inietta un fetch finto non se ne accorge: il bind lo rende esplicito.
    this.fetch = fetchImpl.bind(globalThis);
    this.timeoutBufferMs = Math.max(500, Number(timeoutBufferMs) || 5000);
  }

  /** Prova gli URL in ordine: passa al successivo solo su errore di rete/timeout
   *  (TransportError con status 0). Se il CP risponde (status > 0) l'errore e'
   *  reale e non c'e' fallback: un altro CP non darebbe esito diverso. */
  async _post(path, body, timeoutMs) {
    let lastError = null;
    for (const base of this.baseUrls) {
      try {
        return await this._postOne(base, path, body, timeoutMs);
      } catch (error) {
        lastError = error;
        if (error instanceof TransportError && error.status > 0) throw error;
      }
    }
    throw lastError;
  }

  async _postOne(base, path, body, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await this.fetch(`${base}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (error) {
      throw new TransportError(`rete non raggiungibile: ${error.message || error}`);
    } finally {
      clearTimeout(timer);
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const detail = (payload && payload.error) || `HTTP ${response.status}`;
      throw new TransportError(detail, {
        status: response.status,
        payload,
        unknownNode: response.status === 404,
        disabled: response.status === 503,
      });
    }
    if (!payload || payload.ok === false) {
      throw new TransportError((payload && payload.error) || "risposta non valida",
                               { status: response.status, payload });
    }
    return payload;
  }

  register(registrationPayload) {
    return this._post("/web/register", registrationPayload, 15000);
  }

  /** Long-poll: il timeout lato client supera quello lato server, altrimenti
   *  abortiremmo la richiesta proprio mentre il CP sta rispondendo "nessun
   *  task". */
  poll({ nodeId, timeoutS }) {
    const seconds = Math.max(0, Number(timeoutS) || 0);
    return this._post("/web/poll", { node_id: nodeId, timeout_s: seconds },
                      seconds * 1000 + this.timeoutBufferMs);
  }

  sendResult(payload) {
    return this._post("/web/result", payload, 20000);
  }
}
