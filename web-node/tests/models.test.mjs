// SPDX-License-Identifier: Apache-2.0
// web-node/tests/models.test.mjs
// Test del catalogo modelli: raggruppamento, etichette delle macchine, dedupe.
//
// Le voci sono quelle REALI di /v1/models, copiate dall'output del CP e non
// costruite a mano: e' l'unico modo perche' il test verifichi il formato che
// c'e' davvero (inclusi i nomi di modello che contengono ':' come
// "hf.co/.../Qwen3.5-9B-abliterated-GGUF:Q6_K", dove un parser che spezza sul
// primo ':' sbaglia tutto).
//
//   node web-node/tests/models.test.mjs
import assert from "node:assert/strict";

import { buildCatalog, nodeLabel, splitModelId } from "../src/models.js";

let passed = 0;
function check(label, fn) {
  try {
    fn();
    passed += 1;
    console.log(`ok   ${label}`);
  } catch (error) {
    console.error(`FAIL ${label}: ${error.message}`);
    process.exitCode = 1;
  }
}

const MESH = "🕸️";
const MAC = "d7bc05baed5b752aeab6ba2624243b59fc333b9f";
const WIN = "fc6c821ba7c1866768010e1b4baa04507a4157da";

/** Una voce pinnata come la costruisce il CP (blocco `hyperspace`). */
const pinned = (base, ref, { node = MAC, alias = "", tier = "leaf" } = {}) => ({
  id: `${MESH} ${base}::${ref}`,
  object: "model",
  owned_by: "hyperspace-agi",
  hyperspace: { base_model: base, node_id: node, node_alias: alias, tier },
});
const bare = (base) => ({ id: `${MESH} ${base}`, object: "model", owned_by: "hyperspace-agi" });

/** L'elenco reale: i modelli del Mac, quelli del portatile Windows, le voci
 *  automatiche e OmniRoute. Le 4 voci del nodo locale pseudo-registrato
 *  (endpoint vuoto) NON ci sono piu': il CP non le pubblica, perche' un nodo
 *  non chiamabile non puo' servire un modello. */
const REAL = [
  bare("gemma4:e4b"), bare("qwen2:0.5b"), bare("qwen3.5:4b"), bare("qwen3:8b"),
  bare("qwen3:8b-original"),
  bare("hf.co/Abiray/Qwen3.5-4B-Abliterated-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q6_K"),
  bare("hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K"),
  bare("hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:IQ2_S"),
  pinned("gemma4:e4b", "d7bc05ba"), pinned("qwen2:0.5b", "d7bc05ba"),
  pinned("qwen3:8b", "d7bc05ba"), pinned("qwen3:8b-original", "d7bc05ba"),
  pinned("qwen3.5:4b", "fc6c821b", { node: WIN }),
  pinned("hf.co/Abiray/Qwen3.5-4B-Abliterated-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q6_K", "fc6c821b", { node: WIN }),
  pinned("hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K", "fc6c821b", { node: WIN }),
  pinned("hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:IQ2_S", "fc6c821b", { node: WIN }),
  { id: "🌐 OmniRoute (auto)", object: "model", owned_by: "omniroute" },
];

check("splitModelId separa un nome che contiene ':' senza romperlo", () => {
  const { icon, base, ref } = splitModelId(`${MESH} hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K::fc6c821b`);
  assert.equal(icon, MESH);
  assert.equal(base, "hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K");
  assert.equal(ref, "fc6c821b");
});

check("splitModelId distingue la voce automatica da quella pinnata", () => {
  assert.deepEqual(splitModelId(`${MESH} qwen3:8b`), { icon: MESH, base: "qwen3:8b", ref: "" });
  assert.deepEqual(splitModelId(`${MESH} qwen3:8b::d7bc05ba`), { icon: MESH, base: "qwen3:8b", ref: "d7bc05ba" });
  assert.deepEqual(splitModelId(""), { icon: "", base: "", ref: "" });
});

check("le 17 voci reali diventano 8 modelli su 2 macchine, piu' OmniRoute", () => {
  const { groups, external, counts } = buildCatalog(REAL);
  assert.equal(counts.entries, 17);
  assert.equal(counts.models, 8);
  assert.equal(counts.machines, 2);          // non 3: il nodo fantasma non c'e' piu'
  assert.equal(counts.pinned, 8);            // 4 modelli sul Mac, 4 sul Windows
  assert.equal(counts.external, 1);
  assert.equal(groups.length, 8);
  assert.deepEqual(external.map((e) => e.id), ["🌐 OmniRoute (auto)"]);
});

check("ogni modello dice su quale macchina sta", () => {
  const byBase = Object.fromEntries(buildCatalog(REAL).groups.map((g) => [g.base, g]));
  // qwen3:8b e' solo sul Mac: il PC Windows non puo' servirlo, e la tendina lo dice.
  assert.deepEqual(byBase["qwen3:8b"].nodes.map((n) => n.label), ["d7bc05ba"]);
  assert.deepEqual(byBase["qwen3.5:4b"].nodes.map((n) => n.label), ["fc6c821b"]);
  assert.equal(byBase["gemma4:e4b"].nodes.length, 1);
  assert.equal(byBase["gemma4:e4b"].genericId, `${MESH} gemma4:e4b`);
});

check("l'alias batte l'id troncato, e 'modello' e 'modello su X' restano distinti", () => {
  const withAlias = [
    bare("qwen3:8b"),
    pinned("qwen3:8b", "macbook", { alias: "macbook" }),
    pinned("qwen3:8b", "win11", { node: WIN, alias: "win11", tier: "hub" }),
  ];
  const [group] = buildCatalog(withAlias).groups;
  assert.deepEqual(group.nodes.map((n) => n.label), ["macbook", "win11"]);
  assert.equal(group.genericId, `${MESH} qwen3:8b`);       // il percorso automatico resta
  assert.equal(group.nodes[1].tier, "hub");                // il tier arriva dalla voce
  assert.equal(nodeLabel({}, "fallback"), "fallback");     // senza nulla: il ref
});

check("nodi e modelli hanno un ordine stabile (la tendina non si riordina)", () => {
  const { groups } = buildCatalog(REAL);
  assert.deepEqual(groups.map((g) => g.base), [...groups.map((g) => g.base)].sort());
  const multi = buildCatalog([
    bare("m"), pinned("m", "zzz", { alias: "zeta" }), pinned("m", "aaa", { alias: "alfa" }),
  ]).groups[0];
  assert.deepEqual(multi.nodes.map((n) => n.label), ["alfa", "zeta"]);
});

check("voci identiche ripetute non producono due option", () => {
  const dup = [pinned("m", "macbook", { alias: "macbook" }), pinned("m", "macbook", { alias: "macbook" })];
  const [group] = buildCatalog(dup).groups;
  assert.equal(group.nodes.length, 1);
  assert.equal(group.genericId, "");          // modello senza via automatica: solo pin
});

check("una voce esterna senza blocco hyperspace non inquina i gruppi mesh", () => {
  const { groups, external } = buildCatalog([
    { id: "🌐 OmniRoute (auto)", owned_by: "omniroute" },
    bare("qwen3:8b"),
  ]);
  assert.deepEqual(groups.map((g) => g.base), ["qwen3:8b"]);
  assert.equal(external.length, 1);
});

check("input malformato non fa esplodere la pagina", () => {
  for (const bad of [null, undefined, [], "x", [{}, { id: "" }, { id: "   " }]]) {
    const { groups, counts } = buildCatalog(bad);
    assert.ok(Array.isArray(groups));
    assert.ok(counts.entries >= 0);
  }
  const { groups, counts } = buildCatalog([{ id: `${MESH} qwen3:8b`, owned_by: "hyperspace-agi" }, null]);
  assert.equal(counts.entries, 1);
  assert.equal(groups[0].genericId, `${MESH} qwen3:8b`);
});

console.log(`\nPASS modelli: ${passed} check su raggruppamento, etichette e dedupe`);
if (process.exitCode) process.exit(process.exitCode);
