// SPDX-License-Identifier: Apache-2.0
// web-node/src/models.js
// Trasforma la lista piatta di /v1/models in un catalogo per la tendina.
//
// Perche' esiste: il CP pubblica lo STESSO modello una volta per nodo che ce
// l'ha, con id "modello::ref" (ref = alias del nodo, o i primi 8 caratteri del
// node_id), piu' una voce senza suffisso per il routing automatico. Appiattite
// in un <select> sembrano doppioni senza significato; in realta' dicono SU QUALE
// MACCHINA sta ogni modello — informazione che il CP mette nel blocco
// `hyperspace` di ogni voce e che la pagina buttava via.
//
//   "qwen3:8b"            -> senza suffisso: il CP sceglie il nodo
//   "qwen3:8b::macbook"   -> pin: la richiesta va su QUEL nodo
//   "🌐 OmniRoute (auto)" -> provider esterni, fuori dalla mesh
//
// Le funzioni sono pure (niente DOM, niente fetch): anche le option finali sono
// descritte qui, così il picker mobile non trasforma il modello in una
// intestazione optgroup priva del controllo di selezione.

import { prettyModelId } from "./chat.js";

/** Spezza un id del CP in icona, modello base e riferimento al nodo.
 *  "🕸️ qwen3:8b::macbook" -> { icon:"🕸️", base:"qwen3:8b", ref:"macbook" }
 *  Il taglio e' sul PRIMO "::", come fa il CP in _parse_model_node_ref. */
export function splitModelId(id) {
  const raw = String(id ?? "").trim();
  const cut = raw.indexOf("::");
  const head = cut === -1 ? raw : raw.slice(0, cut);
  const ref = cut === -1 ? "" : raw.slice(cut + 2).trim();
  const base = prettyModelId(head);
  const at = base ? raw.indexOf(base) : -1;
  return { icon: at > 0 ? raw.slice(0, at).trim() : "", base, ref };
}

/** Come si chiama la macchina, per un umano. L'alias e' l'unico nome scelto da
 *  una persona: se non c'e', si mostra il node_id troncato (quello che il CP
 *  userebbe come ref) — meglio di un "nodo 2" che non identifica niente. */
export function nodeLabel(hyperspace, ref = "") {
  const hs = hyperspace || {};
  const alias = String(hs.node_alias || "").trim();
  if (alias) return alias;
  const nodeId = String(hs.node_id || "").trim();
  if (nodeId) return nodeId.slice(0, 8);
  return String(ref || "").trim() || "nodo";
}

/** Il catalogo raggruppato: un gruppo per modello, dentro i nodi che lo hanno.
 *
 *  Ritorna `{groups, external, counts}`; i gruppi sono ordinati per nome, i nodi
 *  dentro un gruppo per etichetta — un ordine stabile, altrimenti la tendina si
 *  riordina a ogni ricarica (l'ordine di /v1/models dipende dai nodi attivi).
 */
export function buildCatalog(models) {
  const groups = new Map();
  const external = [];
  let entries = 0;

  const groupFor = (base) => {
    if (!groups.has(base)) groups.set(base, { base, genericId: "", icon: "", nodes: new Map() });
    return groups.get(base);
  };

  for (const model of Array.isArray(models) ? models : []) {
    const id = String((model && model.id) || "").trim();
    if (!id) continue;                             // voci senza id: ignorate
    entries += 1;
    const hs = (model && model.hyperspace) || null;
    const ownedBy = String((model && model.owned_by) || "");
    const { icon, base, ref } = splitModelId(id);

    if (!hs) {
      // Senza blocco `hyperspace` non e' un pin: o e' la voce automatica della
      // mesh, o e' un provider esterno (OmniRoute). Sono cose diverse: la prima
      // e' una scelta di routing, la seconda esce dalla mesh.
      if (ownedBy === "omniroute" || !base) {
        external.push({ id, base: base || id, icon });
        continue;
      }
      const group = groupFor(base);
      if (!group.genericId) group.genericId = id;   // la PRIMA vince, come il CP
      if (!group.icon) group.icon = icon;
      continue;
    }

    const baseModel = String(hs.base_model || base).trim() || base;
    if (!baseModel) continue;
    const group = groupFor(baseModel);
    if (!group.icon) group.icon = icon;
    const label = nodeLabel(hs, ref);
    const key = ref || label;
    if (!group.nodes.has(key)) {                    // due voci identiche: una sola
      group.nodes.set(key, {
        id,
        ref,
        label,
        nodeId: String(hs.node_id || ""),
        alias: String(hs.node_alias || ""),
        tier: String(hs.tier || ""),
      });
    }
  }

  const byLabel = (a, b) => (a.label < b.label ? -1 : a.label > b.label ? 1 : 0);
  const list = [...groups.values()].map((group) => ({
    base: group.base,
    genericId: group.genericId,
    icon: group.icon,
    nodes: [...group.nodes.values()].sort(byLabel),
  })).sort((a, b) => (a.base < b.base ? -1 : a.base > b.base ? 1 : 0));

  const machines = new Set();
  let pinned = 0;
  for (const group of list) {
    for (const node of group.nodes) {
      machines.add(node.label);
      pinned += 1;
    }
  }

  return {
    groups: list,
    external,
    counts: { entries, models: list.length, machines: machines.size, pinned, external: external.length },
  };
}

/** Voci realmente selezionabili nel picker nativo.
 *
 * Su Chrome Android un <optgroup> appare come una riga senza pallino. Se il
 * nome del modello vive soltanto in quell'intestazione, sembra disabilitato e
 * le option sottostanti ("Automatico", "pin") non dicono quale modello stanno
 * scegliendo. Ogni voce contiene quindi sempre modello e destinazione. */
export function selectableModelOptions(catalog) {
  const options = [];
  for (const group of (catalog && catalog.groups) || []) {
    if (group.genericId) {
      options.push({
        value: group.genericId,
        label: `${group.base} · automatico`,
        model: group.base,
        route: "auto",
      });
    }
    for (const machine of group.nodes || []) {
      const tier = machine.tier ? ` · ${machine.tier}` : "";
      options.push({
        value: machine.id,
        label: `${group.base} · pin: ${machine.label}${tier}`,
        model: group.base,
        route: "pinned",
      });
    }
  }
  for (const entry of (catalog && catalog.external) || []) {
    options.push({
      value: entry.id,
      label: `${entry.base} · provider esterni`,
      model: entry.base,
      route: "external",
    });
  }
  return options;
}
