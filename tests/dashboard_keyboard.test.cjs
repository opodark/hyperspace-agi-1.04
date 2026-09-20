// SPDX-License-Identifier: Apache-2.0
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../infra-ui/dashboard.html'), 'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(js);
const start = js.indexOf('function handleDashboardKeydown(');
const end = js.indexOf("window.addEventListener('keydown',handleDashboardKeydown)", start);
const actions = [];
const context = {
  sidebarOpen: false,
  toggleSidebar() { actions.push('toggle'); context.sidebarOpen = !context.sidebarOpen; },
  switchTab(tab) { actions.push(tab); },
  fetchMeshNodes() { actions.push('refresh'); },
  closeModal() { actions.push('close'); },
};
vm.createContext(context);
vm.runInContext(js.slice(start, end), context);
const plainTarget = { isContentEditable: false, closest: () => null };
function press(key, overrides = {}) {
  actions.length = 0;
  context.sidebarOpen = false;
  context.handleDashboardKeydown({ key, target: plainTarget, ...overrides });
  return [...actions];
}
for (const [key, tab] of Object.entries({t:'tasks', d:'dreams', m:'memory', c:'chat'})) {
  assert.deepEqual(press(key), ['toggle', tab]);
  assert.deepEqual(press(key.toUpperCase()), ['toggle', tab]);
}
assert.deepEqual(press('n'), ['refresh']);
assert.deepEqual(press('Escape'), ['close']);
for (const key of ['t', 'd', 'm', 'c', 'n', 'Escape']) {
  for (const flag of ['ctrlKey', 'metaKey', 'altKey', 'isComposing', 'defaultPrevented']) {
    assert.deepEqual(press(key, { [flag]: true }), [], `${flag}: ${key}`);
  }
  for (const tag of ['input', 'textarea', 'select', '[role="textbox"]']) {
    const target = { closest: selector => selector.split(',').includes(tag) ? {} : null };
    assert.deepEqual(press(key, { target }), [], `${tag}: ${key}`);
  }
  assert.deepEqual(press(key, { target: { isContentEditable: true } }), []);
}
console.log('PASS dashboard shortcuts, typing, composition and Mac/Windows/Linux modifiers');
