// SPDX-License-Identifier: Apache-2.0
// web-node/serve.mjs
// Server statico a dipendenza zero per il web node. Sostituisce
// `python3 -m http.server 8790`, che su Windows non e' sempre disponibile
// (python3 puo' non esistere). Serve la cartella del web node su 0.0.0.0:8790
// con i MIME type giusti per i moduli ES (text/javascript), cosi' la pagina e i
// suoi `import "./src/*.js"` funzionano identici su Windows/macOS/Linux.
//
// Non e' un server di produzione: nessun TLS, nessun rate limit. Serve solo per
// uso personale, come documentato in README.md.
//
//   node serve.mjs            # oppure: npm run serve
//   PORT=8791 node serve.mjs  # porta alternativa
import http from "node:http";
import os from "node:os";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL(".", import.meta.url)));
const PORT = Number(process.env.PORT) || 8790;
const HOST = process.env.HOST || "0.0.0.0";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".map": "application/json",
};

/** Indirizzi IPv4 non interni (include la Tailscale IP quando attiva). */
function localAddresses() {
  const out = [];
  for (const list of Object.values(os.networkInterfaces())) {
    for (const iface of list || []) {
      if (iface.family === "IPv4" && !iface.internal) out.push(iface.address);
    }
  }
  return out;
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost");
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === "/") pathname = "/index.html";
    const file = resolve(ROOT, "." + pathname);
    // Blocca la path traversal: serve solo file DENTRO la cartella del web node.
    if (file !== ROOT && !file.startsWith(ROOT + sep)) {
      res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("forbidden");
      return;
    }
    let info;
    try {
      info = await stat(file);
    } catch {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("not found");
      return;
    }
    if (!info.isFile()) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("not found");
      return;
    }
    res.writeHead(200, {
      "Content-Type": MIME[extname(file).toLowerCase()] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    createReadStream(file).pipe(res);
  } catch {
    res.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("bad request");
  }
});

server.listen(PORT, HOST, () => {
  console.log(`web node servito su http://localhost:${PORT}`);
  for (const addr of localAddresses()) {
    console.log(`                    http://${addr}:${PORT}`);
  }
  console.log(`in ascolto su ${HOST}:${PORT} — il control-plane lo trova da solo su http://<ip>:8085`);
});
