// web-node/src/index.js
// Orchestrazione del web node: registrazione, long-poll, esecuzione, risultato.
// Nessuna dipendenza: gira in una pagina o in un service worker di estensione.
//
// Il consenso esplicito e' obbligatorio e non aggirabile: il node non parte
// finche' l'utente non ha accettato di condividere risorse con la mesh.

import { PROTOCOL_VERSION, parseTask, registrationPayload, resultPayload } from "./protocol.js";
import { detectCapabilities, describeEnvironment } from "./capabilities.js";
import { TransportError, WebNodeTransport } from "./transport.js";
import { TaskUnsupported, runTask, supportedTaskTypes } from "./task-runner.js";

export { PROTOCOL_VERSION, TransportError, TaskUnsupported, supportedTaskTypes };
export { detectCapabilities, describeEnvironment };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function newNodeId() {
  const uuid = globalThis.crypto?.randomUUID?.();
  return uuid ? `web-${uuid}` : `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export class WebNode {
  constructor({ baseUrl, nodeId, label = "", consent = false, runtime = null,
                fetchImpl, env = globalThis, pollTimeoutS = 25, maxBackoffMs = 30000,
                onState = null } = {}) {
    if (!consent) {
      throw new Error("consenso esplicito richiesto: nessuna risorsa viene condivisa senza");
    }
    this.nodeId = String(nodeId || newNodeId());
    this.capabilities = detectCapabilities(env, { runtime });
    this.environment = describeEnvironment(env);
    this.transport = new WebNodeTransport({ baseUrl, fetchImpl });
    this.registration = registrationPayload({
      nodeId: this.nodeId,
      capabilities: this.capabilities,
      label,
      browser: this.environment.browser,
      limits: this.environment.limits,
    });
    this.runtime = runtime;
    this.pollTimeoutS = Math.max(0, Math.min(55, Number(pollTimeoutS) || 25));
    this.maxBackoffMs = Math.max(1000, Number(maxBackoffMs) || 30000);
    this.onState = typeof onState === "function" ? onState : null;
    this.running = false;
    this.attempts = 0;
    this.state = { running: false, nodeId: this.nodeId, capabilities: this.capabilities,
                   registered: false, lastTask: null, lastError: "", tasksDone: 0,
                   tasksFailed: 0, lastResultAt: null };
  }

  _emit() {
    if (this.onState) this.onState({ ...this.state });
  }

  async register() {
    const payload = await this.transport.register(this.registration);
    this.state.registered = true;
    this.attempts = 0;
    this.state.lastError = "";
    this._emit();
    return payload;
  }

  /** Un ciclo completo: poll -> esegui -> pubblica. Esposto a parte perche' i
   *  test possano verificarlo senza avviare il loop. */
  async tick() {
    const response = await this.transport.poll({ nodeId: this.nodeId, timeoutS: this.pollTimeoutS });
    if (!response.task) return null;
    const task = parseTask(response.task);
    this.state.lastTask = { taskId: task.taskId, type: task.type };
    this._emit();
    const startedAt = Date.now();
    try {
      const result = await runTask(task, { runtime: this.runtime });
      await this.transport.sendResult(resultPayload({
        nodeId: this.nodeId, taskId: task.taskId, ok: true, result,
        durationMs: Date.now() - startedAt,
      }));
      this.state.tasksDone += 1;
    } catch (error) {
      // Un task fallito viene COMUNQUE pubblicato: il CP deve sapere che il
      // lavoro e' finito male, altrimenti resta in volo fino al TTL.
      await this.transport.sendResult(resultPayload({
        nodeId: this.nodeId, taskId: task.taskId, ok: false,
        error: error?.message || String(error), durationMs: Date.now() - startedAt,
      }));
      this.state.tasksFailed += 1;
      this.state.lastError = error?.message || String(error);
    }
    this.state.lastResultAt = Date.now();
    this._emit();
    return task;
  }

  async start() {
    if (this.running) return this.state;
    await this.register();
    this.running = true;
    this.state.running = true;
    this._emit();
    this._loop = this._run();
    return this.state;
  }

  stop() {
    this.running = false;
    this.state.running = false;
    this._emit();
  }

  async _run() {
    while (this.running) {
      try {
        const handled = await this.tick();
        if (!handled) await sleep(1000);   // nessun task: breve pausa e si ri-poll
      } catch (error) {
        if (error instanceof TransportError && error.disabled) {
          this.state.lastError = error.message;
          this._emit();
          this.stop();
          return;
        }
        // Il CP non ci conosce piu' (TTL del registry scaduto o riavvio): ci
        // ri-registriamo, poi riprendiamo con un backoff.
        if (error instanceof TransportError && error.unknownNode) {
          this.state.registered = false;
          try {
            await this.register();
          } catch {
            /* il prossimo giro riprova */
          }
        }
        this.state.lastError = error?.message || String(error);
        this._emit();
        this.attempts += 1;
        const backoff = Math.min(this.maxBackoffMs, 1000 * 2 ** this.attempts);
        await sleep(backoff / 2 + Math.random() * (backoff / 2));
      }
    }
  }
}

/** Compatibilita' con la firma dello stub precedente. */
export async function registerWebNode(config = {}) {
  const node = new WebNode({ ...config, consent: config.consent !== false });
  return node.register();
}

/** Compatibilita' con la firma dello stub precedente. */
export async function handleTask(envelope, { runtime } = {}) {
  return runTask(parseTask(envelope), { runtime });
}