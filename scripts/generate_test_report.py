#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Genera un report PDF dei test eseguiti su questo repository.

Non racconta test: li ESEGUE e riporta quello che esce, con il comando usato e
l'esito. Un report che dice "tutto ok" senza dire come e' stato verificato non
serve a niente, quindi ogni riquadro porta il comando e l'output reale.

I test che richiedono un peer in rete degradano in "non eseguito" invece di far
fallire il report: la macchina Windows puo' essere spenta, e un report di test
deve comunque uscire.

Uso:

    python3 scripts/generate_test_report.py            # HTML + PDF
    python3 scripts/generate_test_report.py --html-only
    python3 scripts/generate_test_report.py --peer <ip>

Il PDF si genera con Chrome in headless (print-to-pdf): nessuna dipendenza
Python, la resa CSS e' quella di un browser vero.
"""
import argparse
import html
import json
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN_IP = "100.64.31.18"
PORTS = {8081: "nodo", 8086: "registry", 8088: "control-plane (Windows)"}

results = []          # {sezione, nome, esito, dettaglio}


def add(sezione, nome, esito, dettaglio=""):
    """esito: ok | fail | warn | skip"""
    results.append({"sezione": sezione, "nome": nome, "esito": esito,
                    "dettaglio": dettaglio.strip()})


def sh(cmd, cwd=None, timeout=300):
    """Esegue e restituisce (rc, stdout+stderr). Non solleva: il report deve uscire."""
    try:
        p = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True,
                           timeout=timeout, shell=isinstance(cmd, str))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timeout dopo {timeout}s"
    except OSError as exc:
        return 127, str(exc)


def http_json(url, timeout=6):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return None, None


def probe(host, port, timeout=4.0):
    """('open'|'timeout'|'refused'|'error', secondi).

    La distinzione non e' cosmetica: TIMEOUT = pacchetti in silenzio (firewall
    che fa DROP), REFUSED = RST in millisecondi (nessuno ascolta su quell'IP).
    Vedi scripts/mesh_health.py per il perche' conta.
    """
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open", time.monotonic() - t0
    except (socket.timeout, TimeoutError):
        return "timeout", time.monotonic() - t0
    except ConnectionRefusedError:
        return "refused", time.monotonic() - t0
    except OSError:
        return "error", time.monotonic() - t0


def py():
    """L'interprete del venv se c'e', altrimenti quello corrente."""
    v = ROOT / ".venv" / "bin" / "python"
    return str(v) if v.exists() else sys.executable


def test_suites():
    """Le suite automatiche del repo: quelle che devono restare verdi sempre."""
    code, out = sh(f"PYTHONPATH=. {py()} -m unittest discover -s tests -p 'test_*.py'", timeout=600)
    m = re.search(r"^Ran (\d+) tests?", out, re.M)
    n = m.group(1) if m else "?"
    ok = code == 0 and re.search(r"^OK", out, re.M)
    add("Suite automatiche", f"Python unittest — {n} test",
        "ok" if ok else "fail",
        f"PYTHONPATH=. {py()} -m unittest discover -s tests -p 'test_*.py'\n"
        + "\n".join(l for l in out.splitlines() if re.match(r"^(OK|FAILED|Ran |ERROR:|FAIL:)", l)))

    code, out = sh("npm run check", cwd=ROOT / "web-node", timeout=120)
    add("Suite automatiche", "web-node — node --check sui moduli",
        "ok" if code == 0 else "fail",
        "npm run check\n" + (out.strip()[-400:] or "(nessun output: tutti i file compilano)"))

    code, out = sh("npm test", cwd=ROOT / "web-node", timeout=180)
    ultima = ([l for l in out.splitlines() if l.strip()] or [""])[-1]
    add("Suite automatiche", "web-node — " + ultima.strip()[:60],
        "ok" if code == 0 and "PASS" in out else "fail",
        "npm test\n" + out.strip()[-500:])


def test_mesh(peer):
    """Il link verso l'altra macchina: ICMP, porta per porta, e chi risponde."""
    ping = shutil.which("ping")
    if ping:
        code, out = sh(f"{ping} -c 2 -W 2000 {peer}", timeout=20)
        add("Connettività mesh", f"ICMP verso {peer}", "ok" if code == 0 else "fail",
            out.strip().splitlines()[-1] if out.strip() else "")

    righe, aperti = [], 0
    for port in sorted(PORTS):
        st, secs = probe(peer, port)
        chi = ""
        if st == "open":
            aperti += 1
            hs, d = http_json(f"http://{peer}:{port}/health", 4)
            ss, ds = http_json(f"http://{peer}:{port}/status", 4)
            if hs == 200 and isinstance(d, dict) and d.get("engine") == "hyperspace-agi":
                chi = (f"control-plane v{d.get('version')} "
                       f"(memorie {d.get('memories')}, nodi {d.get('nodes_active')})")
            elif ss == 200 and isinstance(ds, dict) and ds.get("node_id"):
                chi = f"nodo {str(ds['node_id'])[:16]} tier={ds.get('tier')} vram={ds.get('vram_gb')}"
        righe.append(f"tcp/{port:<5} {st:<8} {secs:.2f}s  ({PORTS[port]})" + (f"  -> {chi}" if chi else ""))
    add("Connettività mesh", f"Probe TCP su {len(PORTS)} porte", "ok" if aperti else "fail", "\n".join(righe))

    for etichetta, url in (("nodo locale — /status", "http://127.0.0.1:8081/status"),
                           ("nodo locale — /peers", "http://127.0.0.1:8081/peers"),
                           ("control-plane locale — /nodes/active", "http://127.0.0.1:8085/nodes/active")):
        status, d = http_json(url)
        if status != 200:
            add("Connettività mesh", etichetta, "skip", f"{url} -> {status}")
            continue
        if "/peers" in url:
            n = len(d.get("peers") or []) if isinstance(d, dict) else "?"
            add("Connettività mesh", etichetta, "ok" if n else "warn", f"{n} peer visti")
        elif "/status" in url:
            add("Connettività mesh", etichetta, "ok",
                f"id={str(d.get('node_id'))[:16]} tier={d.get('tier')} vram_gb={d.get('vram_gb')} "
                f"peers_active={d.get('peers_active')}")
        else:
            nodi = d if isinstance(d, list) else d.get("nodes", [])
            add("Connettività mesh", etichetta, "ok", f"{len(nodi)} nodi attivi")



def test_e2e(peer):
    """Inferenza vera attraverso la mesh: CP locale -> peer -> GPU del peer.

    Sceglie un modello che esiste SOLO sul peer: cosi' la richiesta non puo' che
    passare dalla rete. Se non ne trova, salta invece di inventarsi un test.
    """
    _, d = http_json(f"http://{peer}:8081/metrics", 8)
    if not isinstance(d, dict):
        add("Test end-to-end", "Inferenza cross-macchina", "skip", f"{peer}:8081 non raggiungibile")
        return

    def carico(dati, nome):
        rr = (dati.get("runtime") or {}).get(nome) or {}
        return rr.get("requests_seen"), rr.get("tokens_per_sec_ewma")

    remoto = set(d.get("server", {}).get("models_available") or [])
    _, dl = http_json("http://127.0.0.1:8081/metrics", 6)
    locale = set((dl or {}).get("server", {}).get("models_available") or [])
    solo_remoto = sorted(remoto - locale)
    if not solo_remoto:
        add("Test end-to-end", "Inferenza cross-macchina", "skip",
            f"nessun modello presente solo sul peer (remoto {len(remoto)}, locale {len(locale)})")
        return

    # Preferisci un modello GIA' caricato sul peer: uno da caricare porta il tempo
    # di caricamento nel primo giro (16.5s misurati su un 4B Q6).
    caricati = set(d.get("server", {}).get("models_loaded") or [])
    modello = next((m for m in solo_remoto if m in caricati), solo_remoto[0])
    prima, _ = carico(d, modello)
    corpo = json.dumps({"model": modello, "max_tokens": 20, "stream": False,
                        "messages": [{"role": "user", "content": "Rispondi con una parola."}]})
    t0 = time.monotonic()
    _, out = sh(["curl", "-s", "-m", "120", "-X", "POST",
                 "http://localhost:8085/v1/chat/completions",
                 "-H", "Content-Type: application/json", "-d", corpo], timeout=150)
    durata = time.monotonic() - t0
    _, d2 = http_json(f"http://{peer}:8081/metrics", 8)
    dopo, tps = carico(d2 if isinstance(d2, dict) else d, modello)

    # Criterio di riuscita: la RISPOSTA. Il delta dei contatori e' una conferma in
    # piu', non la prova — per un modello caricato su richiesta il campionamento
    # EWMA di /metrics puo' non essersi ancora aggiornato quando rileggiamo, e
    # usarlo come criterio fa fallire un test su inferenza che ha funzionato.
    try:
        risposta = json.loads(out)
        scelte = risposta.get("choices") or []
        servito = bool(scelte) and bool(scelte[0].get("message"))
    except Exception:
        risposta, servito = {}, False
    conferma = prima is not None and dopo is not None and dopo > prima

    add("Test end-to-end", "Inferenza cross-macchina", "ok" if servito else "fail",
        f"modello richiesto: {modello}  (presente solo sul peer)\n"
        f"risposta del control-plane: {'valida' if servito else 'NON valida'} — "
        f"modello eco: {risposta.get('model')}\n"
        f"requests_seen sul peer: {prima} -> {dopo} "
        f"({'conferma' if conferma else 'non ancora aggiornato: campionamento EWMA'})\n"
        f"andata e ritorno: {durata:.2f}s   tokens/s stimati: {tps}\n"
        f"risposta (troncata): {out.strip()[:200]}")


def test_config():
    """Il compose e il template: la parte che si rompe in silenzio."""
    base = "docker compose --env-file .env.windows -f docker-compose.windows.yml"
    for nome, cmd in (("solo profilo Windows", f"{base} config -q"),
                      ("+ override Tailscale",
                       f"TAILSCALE_BIND_IP=100.64.31.18 {base} -f docker-compose.tailscale.yml config -q"),
                      ("+ override WireGuard",
                       f"WIREGUARD_BIND_IP=10.99.0.1 {base} -f docker-compose.wireguard.yml config -q")):
        code, out = sh(cmd, timeout=120)
        errs = [l for l in out.splitlines() if "level=warning" not in l and l.strip()]
        add("Configurazione e integrità", f"docker compose config — {nome}",
            "ok" if code == 0 else "fail",
            cmd + "\n" + (errs[0] if errs else "(nessun errore)"))

    code, out = sh(f"{base} config", timeout=120)
    if code != 0:
        add("Configurazione e integrità", "Servizi che pubblicano porte", "skip", "config non risolvibile")
    else:
        servizi, svc = [], None
        for l in out.splitlines():
            m = re.match(r"^  ([a-z0-9_-]+):\s*$", l)
            if m:
                svc = m.group(1)
            if re.match(r"^    ports:\s*$", l) and svc not in servizi:
                servizi.append(svc)
        vietati = [s for s in ("hyperspace-core", "hyperspace-registry", "hyperspace-node-1") if s in servizi]
        add("Configurazione e integrità",
            "core/registry/node-1 non pubblicano porte (le prende il gateway)",
            "ok" if not vietati else "fail",
            "pubblicano: " + ", ".join(servizi) +
            ("\nVIOLAZIONE: " + ", ".join(vietati) if vietati else "\nnessuna violazione"))



    # Il template puo' essere cambiato senza che il compose se ne accorga: qui si
    # controlla che i valori attesi ci siano ancora.
    attesi = {"VRAM_GB": "8", "NODE_TIER": "hub", "HS_MODEL_GENERAL": "qwen3.5:4b",
              "TITLER_ENABLED": "false", "MESH_BIND_IP": "100.64.31.18"}
    testo = (ROOT / ".env.windows").read_text(encoding="utf-8")
    righe, mancanti = [], []
    for chiave, valore in attesi.items():
        m = re.search(rf"(?m)^{chiave}=(.+)$", testo)
        got = m.group(1).strip() if m else None
        righe.append(f"{chiave}={got}" + ("" if got == valore else f"   ATTESO {valore}"))
        if got != valore:
            mancanti.append(chiave)
    add("Configurazione e integrità",
        f"Template .env.windows — {len(attesi) - len(mancanti)}/{len(attesi)} chiavi attese",
        "ok" if not mancanti else "fail", "\n".join(righe))

    _, h = sh("git rev-parse --short HEAD")
    _, subj = sh("git log -1 --format=%s")
    _, dirty = sh("git status --porcelain -uall")
    _, anc = sh("git merge-base --is-ancestor origin/winZOZ main && echo assorbito || echo divergente")
    modificati = len([l for l in dirty.splitlines() if l.strip()])
    add("Configurazione e integrità", "Stato del repository",
        "ok" if not modificati else "warn",
        f"HEAD: {h.strip()}  ({subj.strip()})\n"
        f"working tree: {modificati} file modificati\n"
        f"branch winZOZ: {anc.strip()}")


def test_measures(peer):
    """Cosa dichiarano e quanto rendono davvero i due nodi."""
    for etichetta, url in (("nodo locale (Mac)", "http://127.0.0.1:8081/metrics"),
                           (f"nodo peer ({peer})", f"http://{peer}:8081/metrics")):
        _, d = http_json(url, 8)
        if not isinstance(d, dict):
            add("Misure", etichetta, "skip", f"{url} non raggiungibile")
            continue
        srv = d.get("server", {})
        righe = [f"modelli disponibili: {len(srv.get('models_available') or [])}",
                 f"caricati: {', '.join(srv.get('models_loaded') or []) or 'nessuno'}"]
        for nome, rr in (d.get("runtime") or {}).items():
            if rr.get("loaded"):
                righe.append(f"  {nome}: vram_gb={rr.get('vram_gb')} "
                             f"{rr.get('tokens_per_sec_ewma')} t/s  "
                             f"latenza={rr.get('latency_ms_ewma')}ms  "
                             f"richieste={rr.get('requests_seen')}")
        add("Misure", etichetta, "ok", "\n".join(righe))

    for etichetta, url in (("nodo locale (Mac)", "http://127.0.0.1:8081/status"),
                           (f"nodo peer ({peer})", f"http://{peer}:8081/status")):
        _, d = http_json(url, 8)
        if not isinstance(d, dict):
            continue
        add("Misure", f"{etichetta} — capacità dichiarata",
            "warn" if not d.get("vram_gb") else "ok",
            f"tier={d.get('tier')}  vram_gb={d.get('vram_gb')}  engine={d.get('engine')}  "
            f"uptime={d.get('uptime_s')}s  versione={d.get('version')}")


# Cosa questo report NON dimostra. Scritto a mano di proposito: i limiti non si
# deducono da un esito verde, e un report che tace i propri buchi e' peggio di
# uno incompleto.
LIMITI = [
    "scripts/sync_env_windows.ps1 non e' mai stato ESEGUITO: su questa macchina "
    "non c'e' PowerShell (pwsh assente). La logica di riconciliazione e' stata "
    "validata portandola in Python su un caso sintetico, la sintassi PowerShell no.",
    "Nessun test gira sulla macchina Windows: qui si osserva il suo nodo e il suo "
    "control-plane dall'esterno (porte, /status, /metrics, rotte). Cosa esegua "
    "davvero Docker su quell'host non e' verificabile da qui.",
    "Il control-plane della macchina Windows risponde ma non ha /models/capabilities "
    "ne' /sandbox/status: gira un'immagine piu' vecchia del repository. Le prove di "
    "integrita' di quel lato non valgono per il codice corrente.",
    "Le misure t/s e latenza sono EWMA riportate dai nodi, non benchmark controllati: "
    "modelli diversi non sono confrontabili fra loro, e un campione caricato una volta "
    "porta con se' il tempo di caricamento del modello.",
    "I due nodi non hanno alcun modello in comune, quindi il routing per disponibilita' "
    "e' deterministico e il punteggio di routing non viene quasi mai esercitato: la "
    "scelta FRA nodi non e' provata da questi test.",
    "Entrambi i nodi dichiarano vram_gb=0.0 mentre le GPU lavorano (5.51 GB in VRAM sul "
    "nodo Windows, 7.41 GB su quello Mac): la dichiarazione e' sbagliata, non l'uso. "
    "Finche' resta cosi', il 75% del punteggio di routing e' cieco.",
]



ETICHETTA = {"ok": "OK", "fail": "FALLITO", "warn": "ATTENZIONE", "skip": "NON ESEGUITO"}

CSS = """
@page { size: A4; margin: 15mm 14mm; }
* { box-sizing: border-box; }
body { font: 10pt/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; color: #1c1c1e; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 2mm; letter-spacing: -0.2pt; }
h2 { font-size: 12.5pt; margin: 8mm 0 2mm; padding-bottom: 1.2mm; border-bottom: 1.6pt solid #1c1c1e; }
.sub { color: #555; font-size: 9pt; margin: 0 0 4mm; }
.meta { background: #f5f5f7; border: 1px solid #e0e0e4; border-radius: 3px; padding: 3mm 4mm; margin-bottom: 5mm; font-size: 9pt; }
.riga { display: flex; justify-content: space-between; padding: 0.4mm 0; }
.card { border: 1px solid #dcdce0; border-left-width: 3.4pt; border-radius: 3px; padding: 2.4mm 3mm; margin-bottom: 2.6mm; page-break-inside: avoid; }
.card.ok   { border-left-color: #1f8a4c; }
.card.fail { border-left-color: #c62828; }
.card.warn { border-left-color: #c77700; }
.card.skip { border-left-color: #9a9aa0; }
.head { font-weight: 600; font-size: 9.8pt; }
.badge { display: inline-block; font-size: 7.4pt; font-weight: 700; letter-spacing: 0.4pt; padding: 0.3mm 1.6mm; border-radius: 2px; color: #fff; vertical-align: 1px; }
.ok .badge { background: #1f8a4c; } .fail .badge { background: #c62828; }
.warn .badge { background: #c77700; } .skip .badge { background: #9a9aa0; }
pre { font: 8.2pt/1.4 "SF Mono", Menlo, Consolas, monospace; background: #fafafa; border: 1px solid #eee; border-radius: 2px; padding: 1.6mm 2mm; margin: 1.6mm 0 0; white-space: pre-wrap; word-break: break-word; }
.riepilogo { display: flex; gap: 3mm; margin-bottom: 4mm; }
.pill { flex: 1; border: 1px solid #dcdce0; border-radius: 3px; padding: 2.4mm; text-align: center; }
.pill .n { font-size: 17pt; font-weight: 700; display: block; }
.pill .l { font-size: 7.6pt; text-transform: uppercase; letter-spacing: 0.5pt; color: #555; }
.pill.ok .n { color: #1f8a4c; } .pill.fail .n { color: #c62828; }
.pill.warn .n { color: #c77700; } .pill.skip .n { color: #9a9aa0; }
ul.limiti { padding-left: 5mm; } ul.limiti li { margin-bottom: 1.6mm; }
footer { margin-top: 8mm; padding-top: 2mm; border-top: 1px solid #dcdce0; color: #666; font-size: 8pt; }
"""


def render(peer, durata):
    conteggio = {k: 0 for k in ETICHETTA}
    for r in results:
        conteggio[r["esito"]] += 1
    _, head = sh("git rev-parse --short HEAD")
    _, subj = sh("git log -1 --format=%s")

    p = ['<!doctype html><html lang="it"><head><meta charset="utf-8">',
         "<title>Report dei test — HyperSpace-AGI 1.04</title>",
         f"<style>{CSS}</style></head><body>",
         "<h1>Report dei test — HyperSpace-AGI 1.04</h1>",
         f'<p class="sub">Eseguiti il {time.strftime("%d/%m/%Y alle %H:%M:%S")} su '
         f'{html.escape(platform.node())}, peer in rete {html.escape(peer)}, '
         f"durata complessiva {durata:.0f}s.</p>",
         '<div class="meta">',
         f'<div class="riga"><span>Commit</span><span>{html.escape(head.strip())} — {html.escape(subj.strip())}</span></div>',
         f'<div class="riga"><span>Macchina</span><span>{html.escape(platform.platform())}</span></div>',
         f'<div class="riga"><span>Python</span><span>{sys.version.split()[0]}</span></div>',
         '<div class="riga"><span>Come leggerlo</span><span>ogni riquadro porta il comando eseguito e il suo output</span></div>',
         "</div>", '<div class="riepilogo">']
    for chiave in ("ok", "warn", "fail", "skip"):
        p.append(f'<div class="pill {chiave}"><span class="n">{conteggio[chiave]}</span>'
                 f'<span class="l">{ETICHETTA[chiave]}</span></div>')
    p.append("</div>")

    for sezione in dict.fromkeys(r["sezione"] for r in results):
        p.append(f"<h2>{html.escape(sezione)}</h2>")
        for r in results:
            if r["sezione"] != sezione:
                continue
            p.append(f'<div class="card {r["esito"]}"><div class="head">'
                     f'<span class="badge">{ETICHETTA[r["esito"]]}</span> {html.escape(r["nome"])}</div>')
            if r["dettaglio"]:
                p.append(f"<pre>{html.escape(r['dettaglio'])}</pre>")
            p.append("</div>")

    p.append('<h2>Limiti — cosa questo report NON dimostra</h2><ul class="limiti">')
    p.extend(f"<li>{html.escape(l)}</li>" for l in LIMITI)
    p.append("</ul>")
    p.append(f"<footer>Generato da scripts/generate_test_report.py — {conteggio['ok']} superati, "
             f"{conteggio['warn']} da guardare, {conteggio['fail']} falliti, "
             f"{conteggio['skip']} non eseguiti.</footer>")
    p.append("</body></html>")
    return "\n".join(p)



BROWSER = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]


def to_pdf(sorgente, destinazione):
    """(ok, messaggio). Chrome headless: nessuna dipendenza Python, resa CSS vera."""
    exe = (next((p for p in BROWSER if Path(p).exists()), None)
           or shutil.which("google-chrome") or shutil.which("chromium")
           or shutil.which("chromium-browser"))
    if not exe:
        return False, ("nessun browser headless trovato: apri l'HTML e stampa in PDF "
                       "con Cmd+P, oppure installa Chrome o Chromium")
    code, out = sh([exe, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={destinazione}", sorgente.as_uri()], timeout=180)
    if destinazione.exists() and destinazione.stat().st_size > 1000:
        return True, f"{Path(exe).name} — {destinazione.stat().st_size // 1024} KB"
    return False, f"conversione fallita (rc={code}): {out.strip()[-300:]}"


def main():
    ap = argparse.ArgumentParser(description="Genera un report PDF dei test eseguiti.")
    ap.add_argument("--peer", default=WIN_IP, help=f"IP del peer in rete (default {WIN_IP})")
    ap.add_argument("--out", default=str(ROOT / "reports"), help="directory di output")
    ap.add_argument("--html-only", action="store_true", help="salta la conversione in PDF")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    inizio = time.monotonic()

    for nome, fn in (("suite automatiche", lambda: test_suites()),
                     ("connettività mesh", lambda: test_mesh(args.peer)),
                     ("test end-to-end", lambda: test_e2e(args.peer)),
                     ("configurazione", lambda: test_config()),
                     ("misure", lambda: test_measures(args.peer))):
        print(f"  {nome}...", flush=True)
        try:
            fn()
        except Exception as exc:            # un test rotto non deve impedire il report
            add("Errori del generatore", nome, "skip", f"{type(exc).__name__}: {exc}")

    durata = time.monotonic() - inizio
    html_path = outdir / f"report-test-{time.strftime('%Y%m%d-%H%M')}.html"
    html_path.write_text(render(args.peer, durata), encoding="utf-8")

    esiti = {k: sum(1 for r in results if r["esito"] == k) for k in ETICHETTA}
    print(f"\nrisultati: {esiti}\nHTML: {html_path}")

    if not args.html_only:
        pdf_path = html_path.with_suffix(".pdf")
        ok, msg = to_pdf(html_path, pdf_path)
        print(f"PDF:  {pdf_path}\n      {msg}" if ok else f"PDF:  non generato — {msg}")

    for r in results:
        if r["esito"] == "fail":
            print(f"  FALLITO: {r['nome']}")
    return 1 if esiti["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())

