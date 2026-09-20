// SPDX-License-Identifier: Apache-2.0
// Il pannello del web node: la rotta /web/status esisteva per la dashboard e
// nessuna UI la leggeva. Questo test la tiene collegata.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'control-plane', 'dashboard.html'), 'utf8');

assert.match(html, /id="webSummary"/);
assert.match(html, /id="webList"/);
assert.match(html, /onclick="webLoad\(\)"/);
assert.match(html, /if\(name==='nodes'\)webLoad\(\);/);

const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const start = script.indexOf('// ── WEB NODE');
const end = script.indexOf('// ── DEV SANDBOX');
assert.ok(start >= 0 && end > start, 'blocco web node presente e delimitato');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { textContent: '', innerHTML: '' });
  return elements.get(id);
}

function contesto(payload, options = {}) {
  const requests = [];
  const context = {
    document: { getElementById: element },
    fetch: async (url) => {
      requests.push(url);
      if (options.fail) throw new Error('control-plane giu');
      return { ok: true, status: 200, json: async () => payload };
    },
    escH: (value) => String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    console,
    Date: { now: () => 1_000_000 * 1000 },
  };
  vm.createContext(context);
  vm.runInContext(script.slice(start, end), context);
  return { context, requests };
}

const STATO = {
  enabled: true,
  web_nodes: 1,
  queued: 2,
  inflight: 1,
  heartbeat_interval_s: 30,
  web_safe_task_types: ['embed_texts', 'moderate', 'summarize', 'translate', 'validate_json'],
  nodes: [{
    node_id: 'web-9f4c7a21b', label: 'telefono', browser: 'Chrome 126 Android',
    capabilities: ['summarize', 'validate_json'], rejected_capabilities: ['tools'],
    tasks_done: 3, tasks_failed: 1, last_seen: 1_000_000 - 12,
  }],
  recent_results: [
    { task_id: 'web-000001-abcd1234', node_id: 'web-9f4c7a21b', ok: true, duration_ms: 900,
      error: '', completed_at: 1_000_000 - 5 },
    { task_id: 'web-000002-beef5678', node_id: 'web-9f4c7a21b', ok: false, duration_ms: 2500,
      error: 'payload troppo grande', completed_at: 1_000_000 - 3 },
  ],
};

(async () => {
  // 1. il pannello chiede la rotta giusta e riassume
  const primo = contesto(STATO);
  await primo.context.webLoad();
  assert.deepEqual(primo.requests, ['/web/status']);
  const sommario = elements.get('webSummary').textContent;
  assert.match(sommario, /1 nodi browser/);
  assert.match(sommario, /2 in coda/);
  assert.match(sommario, /1 in volo/);
  assert.match(sommario, /battito ogni 30s/);

  // 2. il nodo mostra capability, esiti e ciò che ha dichiarato ma non è web-safe
  const elenco = elements.get('webList').innerHTML;
  assert.match(elenco, /telefono/);
  assert.match(elenco, /summarize, validate_json/);
  assert.match(elenco, /3 fatti · 1 falliti/);
  assert.match(elenco, /Chrome 126 Android/);
  assert.match(elenco, /dichiarate ma non web-safe: tools/);
  assert.match(elenco, /ultimo battito: 12s fa/);

  // 3. i risultati, dal più recente, con esito leggibile e senza campi inventati
  assert.match(elenco, /web-000002-beef/);
  assert.match(elenco, /payload troppo grande/);
  assert.match(elenco, /web-000001-abcd/);
  assert.ok(elenco.indexOf('web-000002') < elenco.indexOf('web-000001'), 'prima il piu recente');

  // 4. senza nodi lo dice, invece di lasciare un riquadro vuoto
  const vuoto = contesto({ enabled: true, web_nodes: 0, queued: 0, inflight: 0,
                           heartbeat_interval_s: 30, web_safe_task_types: ['summarize'],
                           nodes: [], recent_results: [] });
  await vuoto.context.webLoad();
  assert.match(elements.get('webList').innerHTML, /Nessun tab registrato/);

  // 5. disattivato: si dice, non si finge
  const spento = contesto({ enabled: false });
  await spento.context.webLoad();
  assert.match(elements.get('webSummary').textContent, /disattivati/);

  // 6. un errore di rete non rompe la dashboard
  const rotto = contesto(STATO, { fail: true });
  await rotto.context.webLoad();
  assert.match(elements.get('webSummary').textContent, /Errore/);
  assert.equal(elements.get('webList').innerHTML, '');

  console.log('dashboard web node tests passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
