// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'control-plane', 'dashboard.html'), 'utf8');

assert.match(html, /id="panel-sandbox"/);
assert.match(html, /showPanel\('sandbox',this\)/);
assert.match(html, /onclick="sandboxSaveDiffToForge\(\)"/);

const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const start = script.indexOf('// ── DEV SANDBOX');
const end = script.indexOf('// ── TOOL & SKILL FORGE');
assert.ok(start >= 0 && end > start, 'sandbox JavaScript block is present');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: id === 'sandboxCheckPath' ? '.' : '',
    checked: ['sandboxVerifyRuff', 'sandboxVerifyBandit'].includes(id),
    textContent: '', innerHTML: '', disabled: false,
    focus() {}, setSelectionRange() {},
  });
  return elements.get(id);
}
const requests = [];
const context = {
  window: { confirm: () => true },
  document: { getElementById: element },
  fetch: async (url, options) => {
    requests.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ result: '{"ok":true,"workspace_id":"docker:test"}' }) };
  },
  forgeEsc: (value) => String(value),
  forgeRequest: async () => ({ id: 'patch-1', validation: { valid: true } }),
  console,
};
vm.createContext(context);
vm.runInContext(script.slice(start, end), context);

(async () => {
  const result = await context.sandboxRequest({ action: 'create', backend: 'docker' });
  assert.equal(result.workspace_id, 'docker:test');
  const sent = JSON.parse(requests[0].options.body);
  assert.deepEqual(sent, { tool_name: 'code_sandbox', args: { action: 'create', backend: 'docker' } });

  assert.deepEqual(JSON.parse(JSON.stringify(context.sandboxVerifyPlan())), [
    { tool_id: 'ruff', path: '.', timeout: 120 },
    { tool_id: 'bandit', path: '.', timeout: 120 },
  ]);
  console.log('dashboard sandbox tests passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
