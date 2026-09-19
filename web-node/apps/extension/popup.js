// popup.js — lanciatore dell'estensione. Nessun import: la CSP di MV3 consente
// solo script propri, e il runtime vero (long-poll + esecuzione) vive nella
// pagina standalone, che il service worker dell'estensione non puo' sostenere.
const DEFAULTS = {
  pageUrl: "http://localhost:8790/index.html",
  baseUrl: "http://localhost:8085",
};

const store = globalThis.chrome?.storage?.local;
const pageUrl = document.getElementById("pageUrl");
const baseUrl = document.getElementById("baseUrl");
const openButton = document.getElementById("open");

async function restore() {
  if (!store) {
    pageUrl.value = DEFAULTS.pageUrl;
    baseUrl.value = DEFAULTS.baseUrl;
    return;
  }
  const saved = await store.get(DEFAULTS);
  pageUrl.value = saved.pageUrl;
  baseUrl.value = saved.baseUrl;
}

function openNode() {
  // Il control-plane scelto viene passato alla pagina via query string: la
  // pagina lo salva e lo mostra, cosi' l'operatore vede cosa sta usando.
  const target = new URL(pageUrl.value.trim() || DEFAULTS.pageUrl);
  target.searchParams.set("cp", baseUrl.value.trim() || DEFAULTS.baseUrl);
  if (globalThis.chrome?.tabs?.create) {
    chrome.tabs.create({ url: target.toString() });
  } else {
    window.open(target.toString(), "_blank", "noopener");
  }
  window.close();
}

openButton.addEventListener("click", openNode);
restore();
