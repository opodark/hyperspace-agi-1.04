// SPDX-License-Identifier: Apache-2.0
// Copia questo file insieme a join.html e src/ sullo spazio web Aruba.
// L'endpoint deve essere il gateway pubblico HTTPS, mai il control-plane.
window.HYPERSPACE_JOIN = Object.freeze({
  // Gancio stabile: oggi il tunnel Cloudflare porta al federation gateway del
  // Mac. Il client accetta una lista, quindi in futuro si puo aggiungere un
  // secondo gateway senza cambiare la pagina.
  gatewayUrls: Object.freeze([
    "https://mesh.zerozerocomputer.it",
  ]),
  siteName: "ZeroZeroComputer",
  meshName: "HyperSpace",
});
