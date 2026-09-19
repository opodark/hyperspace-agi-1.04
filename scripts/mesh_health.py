"""Diagnostica del link di mesh tra due macchine (Mac e Windows).

Serve a rispondere a una domanda sola: *la mesh si e' formata o no, e se no
perche'*. La distinzione che conta e' tra i modi in cui una porta TCP puo' non
rispondere, perche' indicano cause diverse e richiedono azioni diverse:

  - TIMEOUT  : nessuna risposta TCP, pacchetti in silenzio. E' la firma di un
               firewall che fa DROP. Sull'host c'e' un servizio che ascolta, ma
               non lo si raggiunge.
  - REFUSED  : RST in millisecondi. Nessuno ascolta su quell'indirizzo. E'
               il servizio spento, oppure in ascolto solo su 127.0.0.1 (tipico
               quando MESH_BIND_IP non e' applicato al container).
  - HTTP 200 : raggiungibile.

Un errore da non fare e' scambiare i due: "non si connette" non distingue un
firewall da un container spento, e la contromisura e' completamente diversa.

Uso (dalla macchina che si vuole monitorare):

    python scripts/mesh_health.py                 # controlla il peer noto
    python scripts/mesh_health.py --peer <ip>     # peer esplicito
    python scripts/mesh_health.py --watch 20      # in ciclo ogni 20s

Gira con la sola libreria standard: funziona anche sul Windows del collega,
dove basta lanciarlo per vedere il proprio lato dal di fuori.
"""
import argparse
import json
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# La mesh e' due macchine: questo Mac e il Windows 11 dell'hub.
MAC_IP = "100.81.234.102"
WIN_IP = "100.64.31.18"

# Le due macchine espongono gli STESSI servizi su porte DIVERSE: sul Mac il
# control-plane e' su 8085 e la dashboard su 8088; sul profilo Windows il
# control-plane e' pubblicato su 8088 (host 8088 -> container 8085). Etichettare
# per numero di porta e' quindi sbagliato — l'etichetta si deduce dalla risposta,
# vedi describe().
PORTS = {
    8081: "nodo",
    8085: "control-plane (profilo Mac; non pubblicato sul Windows)",
    8086: "registry",
    8088: "control-plane (Windows) / dashboard (Mac)",
    8095: "federation gateway",
}
TCP_TIMEOUT = 4.0


def describe(host, port):
    """Cosa risponde su quella porta, dedotto DALLA RISPOSTA e non dal numero.

    Il signature check del control-plane e' `engine == "hyperspace-agi"`: senza
    quel controllo il /health di un nodo (che riporta engine=ollama) verrebbe
    scambiato per un control-plane.
    """
    status, data = http_json(f"http://{host}:{port}/health", timeout=3)
    if status == 200 and isinstance(data, dict) and data.get("engine") == "hyperspace-agi":
        return (f"control-plane v{data.get('version', '?')} "
                f"(memorie {data.get('memories', '?')}, nodi {data.get('nodes_active', '?')})")
    status, data = http_json(f"http://{host}:{port}/status", timeout=3)
    if status == 200 and isinstance(data, dict) and data.get("node_id"):
        return f"nodo {str(data['node_id'])[:16]} tier={data.get('tier')} vram={data.get('vram_gb')}"
    status, data = http_json(f"http://{host}:{port}/nodes", timeout=3)
    if status == 200 and isinstance(data, list):
        return f"registry ({len(data)} nodo/i)"
    status, data = http_json(f"http://{host}:{port}/federation/identity", timeout=3)
    if status == 200 and isinstance(data, dict) and data.get("peer_id"):
        ep = data.get("endpoint") or "(endpoint VUOTO: FEDERATION_PUBLIC_URL non configurato)"
        return f"federation gateway, peer {str(data['peer_id'])[:16]}, {ep}"
    return None


def is_windows():
    return platform.system() == "Windows"


def ping(host, count=2):
    """Liveness ICMP. None se 'ping' non c'e' (es. container minimale)."""
    exe = shutil.which("ping")
    if not exe:
        return None
    flags = ["-n", str(count), "-w", "2000"] if is_windows() else ["-c", str(count), "-W", "2000"]
    try:
        r = subprocess.run([exe, *flags, host], capture_output=True, text=True, timeout=count * 3 + 3)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0


def probe(host, port, timeout=TCP_TIMEOUT):
    """('open'|'timeout'|'refused'|'error', secondi, dettaglio) — vedi docstring."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open", time.monotonic() - t0, None
    except (socket.timeout, TimeoutError):
        return "timeout", time.monotonic() - t0, None
    except ConnectionRefusedError:
        return "refused", time.monotonic() - t0, None
    except OSError as exc:
        return "error", time.monotonic() - t0, str(exc)


def http_json(url, timeout=5.0):
    """(status, oggetto json) — status None se non si e' raggiunto nulla."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return None, None


def node_summary(host):
    """Legge /status di un nodo. None se non risponde."""
    status, data = http_json(f"http://{host}:8081/status")
    if status != 200 or not isinstance(data, dict):
        return None
    return {
        "id": str(data.get("node_id", ""))[:16],
        "endpoint": data.get("endpoint"),
        "tier": data.get("tier"),
        "vram_gb": data.get("vram_gb"),
        "version": data.get("version"),
        "engine": data.get("engine"),
        "uptime_s": data.get("uptime_s"),
        "peers_active": data.get("peers_active"),
        "peers_total": data.get("peers_total"),
    }


def report_local():
    """Stato del lato locale: nodo, peer visti, registry, control-plane."""
    out = {"node": node_summary("127.0.0.1"), "peers": None, "registry_nodes": None, "cp_nodes": None}

    status, data = http_json("http://127.0.0.1:8081/peers")
    if status == 200 and isinstance(data, dict):
        out["peers"] = data.get("peers") or []

    status, data = http_json("http://127.0.0.1:8086/nodes")
    if status == 200 and isinstance(data, list):
        out["registry_nodes"] = data

    status, data = http_json("http://127.0.0.1:8085/nodes/active")
    if status == 200:
        if isinstance(data, dict):      # la route puo' incapsulare la lista
            data = data.get("nodes") or data.get("active") or data.get("items")
        if isinstance(data, list):
            out["cp_nodes"] = data
    return out


def report_peer(host):
    """Stato del peer: ICMP, ogni porta, e /status se il nodo risponde."""
    probes = {p: probe(host, p) for p in sorted(PORTS)}
    return {
        "host": host,
        "ping": ping(host),
        "probes": probes,
        "node": node_summary(host) if probes.get(8081, ("",))[0] == "open" else None,
    }


def verdict(local, peer):
    """(esito, [righe di diagnosi]). L'esito decide l'exit code."""
    lines = []
    states = {p: st for p, (st, _, _) in peer["probes"].items()}
    opened = [p for p, st in states.items() if st == "open"]
    timeouts = [p for p, st in states.items() if st == "timeout"]
    refused = [p for p, st in states.items() if st in ("refused", "error")]

    local_peers = local["peers"]
    n_peers = len(local_peers) if local_peers is not None else None

    if not opened:
        if timeouts and not refused:
            if peer["ping"]:
                lines.append(
                    "ICMP risponde ma nessuna porta TCP: e' un DROP del firewall, non\n"
                    "  un servizio spento. Uno spento risponderebbe RST, cioe'\n"
                    "  'connection refused' in millisecondi, non un timeout di 4s."
                )
                lines.append(
                    "  Azione lato peer: regola inbound per "
                    + ", ".join(str(p) for p in sorted(PORTS))
                    + " sul\n  profilo Private, come documentato in .env.windows."
                )
            else:
                lines.append(
                    "Ne' ICMP ne' TCP: l'host non e' raggiungibile affatto (Tailscale\n"
                    "  giu', macchina spenta, o IP cambiato)."
                )
        elif refused:
            lines.append(
                "TCP rifiutato subito: nessun servizio in ascolto su quell'indirizzo.\n"
                "  Cause tipiche: container fermi, oppure in ascolto solo su 127.0.0.1\n"
                "  perche' MESH_BIND_IP non e' arrivato al .env del compose."
            )
        else:
            lines.append("Nessuna porta risponde e la causa non e' classificabile dai probe.")
        return "PEER NON RAGGIUNGIBILE", lines

    lines.append("Porte raggiungibili: " + ", ".join(f"{p} ({PORTS[p]})" for p in opened))
    if refused or timeouts:
        lines.append(
            "  Altre porte non rispondono ("
            + ", ".join(f"{p}={states[p]}" for p in sorted(states) if states[p] != "open")
            + "): l'host\n  e' raggiungibile, quindi non e' la rete ma il servizio/bind di quel container."
        )

    if peer["node"]:
        n = peer["node"]
        lines.append(
            f"  Nodo peer: {n['id']} tier={n['tier']} vram_gb={n['vram_gb']} "
            f"v={n['version']} engine={n['engine']} uptime={n['uptime_s']}s"
        )
        if not n["vram_gb"]:
            lines.append(
                "  vram_gb=0 -> il control-plane lo classifica come il piu' debole della\n"
                "  mesh (VRAM 0.55 + GPU 0.20 = 75% del punteggio di routing): la GPU\n"
                "  non riceve lavoro. Vedi docs/windows-node-handoff.md, sezione 2."
            )
        if n["tier"] == "leaf" and peer["host"] != MAC_IP:
            lines.append("  tier=leaf su una macchina con GPU: attendersi hub.")

    # Immagine vecchia: una rotta recente che manca. Il codice e' COTTO
    # nell'immagine, quindi `up -d` senza --build ricrea il container ma con il
    # codice di prima: il servizio risponde, sembra sano, e non ha le funzioni
    # nuove. E' una trappola che ha gia' falsato una verifica, quindi la si
    # controlla invece di sperarci.
    for port in sorted(PORTS):
        if peer["probes"][port][0] != "open":
            continue
        # Solo i control-plane: un nodo non ha /models/capabilities e verrebbe
        # segnalato a sproposito come "immagine vecchia".
        what = describe(peer["host"], port)
        if not what or not what.startswith("control-plane"):
            continue
        cst, _ = http_json(f"http://{peer['host']}:{port}/models/capabilities", timeout=4)
        if cst == 404:
            lines.append(
                f"  Il control-plane su {port} risponde ma NON ha /models/capabilities: gira\n"
                "  un'immagine piu' vecchia del repo. Serve `docker compose up -d --build`,\n"
                "  non un semplice `up -d`."
            )
        break

    if n_peers == 0:
        lines.append(
            "Il nodo LOCALE vede 0 peer: la mesh NON e' formata. Avere le porte aperte\n"
            "  non basta, i nodi devono anche registrarsi e scoprirsi a vicenda."
        )
        return "MESH NON FORMATA", lines
    if n_peers is None:
        lines.append("Non sono riuscito a leggere /peers del nodo locale.")
        return "INDETERMINATO", lines

    lines.append(f"Il nodo locale vede {n_peers} peer: " + ", ".join(str(p)[:16] for p in local_peers))
    if peer["node"] and any(str(peer["node"]["id"]) in str(p) for p in local_peers):
        return "MESH FORMATA", lines
    lines.append("  Il peer raggiunto non compare tra i peer del nodo locale.")
    return "MESH PARZIALE", lines


def run(peer_ip):
    local = report_local()
    peer = report_peer(peer_ip)
    esito, lines = verdict(local, peer)

    print(f"\n{'=' * 68}")
    print(f"  MESH CHECK — {time.strftime('%Y-%m-%d %H:%M:%S')} — locale: {platform.node()}")
    print(f"{'=' * 68}")

    print("\nLATO LOCALE")
    n = local["node"]
    if n:
        print(f"  nodo      {n['id']} tier={n['tier']} vram_gb={n['vram_gb']} v={n['version']}")
        print(f"            endpoint={n['endpoint']} peers_active={n['peers_active']}")
    else:
        print("  nodo      non risponde su 127.0.0.1:8081 (container fermo?)")
    if local["registry_nodes"] is not None:
        print(f"  registry  {len(local['registry_nodes'])} nodo/i registrato/i")
    if local["cp_nodes"] is not None:
        print(f"  CP        {len(local['cp_nodes'])} nodo/i attivo/i")

    print(f"\nPEER {peer_ip}")
    p = peer["ping"]
    print(f"  icmp      {'ok' if p else 'nessuna risposta' if p is False else 'non disponibile'}")
    for port in sorted(PORTS):
        st, secs, detail = peer["probes"][port]
        print(f"  tcp/{str(port):<5} {st:<8} {secs:.2f}s  ({PORTS[port]})" + (f" {detail}" if detail else ""))
        if st == "open":
            what = describe(peer_ip, port)
            if what:
                print(f"           -> {what}")
    if peer["node"]:
        pn = peer["node"]
        print(f"  nodo      {pn['id']} tier={pn['tier']} vram_gb={pn['vram_gb']} v={pn['version']}")

    print(f"\nDIAGNOSI — {esito}")
    for line in lines:
        print(f"  {line}")

    return 0 if esito == "MESH FORMATA" else 1


def main():
    default_peer = MAC_IP if is_windows() else WIN_IP
    ap = argparse.ArgumentParser(description="Diagnostica del link di mesh tra le due macchine.")
    ap.add_argument("--peer", default=default_peer, help=f"IP Tailscale del peer (default {default_peer})")
    ap.add_argument("--watch", type=int, metavar="SEC", help="ripete il check ogni SEC secondi")
    args = ap.parse_args()

    if not args.watch:
        return run(args.peer)
    try:
        while True:
            run(args.peer)
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\ninterrotto.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
