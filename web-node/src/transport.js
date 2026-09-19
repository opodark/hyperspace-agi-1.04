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
  constructor({ baseUrl, fetchImpl = globalThis.fetch, timeoutBufferMs = 5000 } = {}) {
    if (typeof fetchImpl !== "function") throw new TransportError("fetch non disponibile");
    this.baseUrl = String(baseUrl || "").replace(/\/+$/, "");
    if (!this.baseUrl) throw new TransportError("baseUrl mancante");
    this.fetch = fetchImpl;
    this.timeoutBufferMs = Math.max(500, Number(timeoutBufferMs) || 5000);
  }

  async _post(path, body, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await this.fetch(`${this.baseUrl}${path}`, {
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
