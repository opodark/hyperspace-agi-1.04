#!/usr/bin/env python3
"""Host-agent HyperSpace — stato/controllo di ngrok, Tailscale e WireGuard.

Perche' esiste: il control-plane gira dentro un container Docker e non puo'
lanciare `tailscale status` o `wg show` sull'host che lo ospita. Questo
processo gira NATIVO sull'host (non in Docker, apposta) ed espone quello che
serve via HTTP; il control-plane lo raggiunge via host.docker.internal.

Sicurezza, non negoziabile:
  - ascolta SOLO su 127.0.0.1 — mai 0.0.0.0, in nessun caso;
  - ogni richiesta deve portare il token in `Authorization: Bearer <token>`,
    confrontato a tempo costante — il bind su loopback non basta da solo,
    host.docker.internal in certe configurazioni Docker e' raggiungibile
    piu' largamente del previsto;
  - azioni da una whitelist fissa, mai un comando costruito da input libero:
    ogni azione e' un argv list precompilato, zero shell=True;
  - controlla SOLO la macchina locale — non esiste un modo di chiedere a
    questo agent di toccare la rete di un'altra macchina della mesh.

Uso:
    python3 hostctl/agent.py --generate-token   # scrive HOSTCTL_TOKEN in .env
    python3 hostctl/agent.py                    # avvia, legge .env

Disabilitato finche' HOSTCTL_TOKEN non esiste: senza, l'avvio si rifiuta.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = Path(os.getenv("HOSTCTL_ENV_PATH", BASE_DIR / ".env"))
STATE_PATH = Path(os.getenv("HOSTCTL_STATE_PATH", Path.home() / ".hyperspace" / "hostctl_state.json"))

_IFACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


# ── config: letta da .env, mai inventata a runtime ─────────────────────────

def load_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def generate_token(path: Path) -> str:
    token = secrets.token_hex(32)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if any(l.startswith("HOSTCTL_TOKEN=") for l in lines):
        print(f"HOSTCTL_TOKEN esiste gia' in {path} — non sovrascritto.")
        print("Cancella quella riga a mano se vuoi rigenerarlo.")
        sys.exit(1)
    lines.append(f"HOSTCTL_TOKEN={token}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Token scritto in {path}.")
    print("Ricordati di mettere lo STESSO valore nel .env che legge il")
    print("control-plane (e' lo stesso file, se lanci l'agent dalla repo).")
    return token


# ── stato persistente minimo: solo il PID di ngrok fra un riavvio e l'altro ─

def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ── helper di esecuzione: argv fisso, mai shell=True ───────────────────────

def _run(argv: list, timeout: int = 15) -> dict:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except FileNotFoundError:
        return {"ok": False, "error": f"comando non trovato: {argv[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout dopo {timeout}s — {argv[0]} potrebbe aspettare input interattivo"}


def _pid_alive_and_named(pid: int, name_fragment: str) -> bool:
    """Controlla che il PID sia vivo E sia davvero il processo che pensiamo —
    evita di terminare un PID riusato da qualcos'altro dopo un riavvio."""
    result = _run(["ps", "-p", str(pid), "-o", "comm="], timeout=5)
    return result.get("ok", False) and name_fragment in result.get("stdout", "")


# ── azioni: whitelist fissa, ognuna un argv precompilato ───────────────────

def action_ngrok_status(_params: dict) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        tunnels = [{"public_url": t.get("public_url"), "proto": t.get("proto"),
                    "addr": t.get("config", {}).get("addr")} for t in data.get("tunnels", [])]
        return {"ok": True, "running": True, "tunnels": tunnels}
    except (urllib.error.URLError, OSError, ValueError):
        return {"ok": True, "running": False, "tunnels": []}


def action_ngrok_start(params: dict) -> dict:
    if not shutil.which("ngrok"):
        return {"ok": False, "error": "ngrok non installato o non nel PATH"}
    try:
        port = int(params.get("port", ""))
        if not (1 <= port <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        return {"ok": False, "error": "port mancante o non valido (1-65535)"}

    state = _read_state()
    existing_pid = state.get("ngrok_pid")
    if existing_pid and _pid_alive_and_named(existing_pid, "ngrok"):
        return {"ok": False, "error": f"ngrok gia' in esecuzione (pid {existing_pid}) — fermalo prima"}

    proc = subprocess.Popen(["ngrok", "http", str(port)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    state["ngrok_pid"] = proc.pid
    _write_state(state)
    time.sleep(2)  # tempo di avviarsi prima che /api/tunnels risponda
    return {"ok": True, "pid": proc.pid, "status": action_ngrok_status({})}


def action_ngrok_stop(_params: dict) -> dict:
    state = _read_state()
    pid = state.get("ngrok_pid")
    if not pid:
        return {"ok": False, "error": "nessun ngrok tracciato da questo agent"}
    if not _pid_alive_and_named(pid, "ngrok"):
        state.pop("ngrok_pid", None)
        _write_state(state)
        return {"ok": False, "error": "il pid tracciato non e' (piu') ngrok — stato ripulito"}
    os.kill(pid, 15)  # SIGTERM
    state.pop("ngrok_pid", None)
    _write_state(state)
    return {"ok": True, "stopped_pid": pid}


def action_tailscale_status(_params: dict) -> dict:
    result = _run(["tailscale", "status", "--json"], timeout=8)
    if not result["ok"]:
        return result
    try:
        data = json.loads(result["stdout"])
    except ValueError:
        return {"ok": False, "error": "output di tailscale status non e' JSON valido"}
    peers = [{"hostname": p.get("HostName"), "ips": p.get("TailscaleIPs"),
              "online": p.get("Online")} for p in data.get("Peer", {}).values()]
    self_info = data.get("Self", {})
    return {"ok": True, "self": {"hostname": self_info.get("HostName"),
            "ips": self_info.get("TailscaleIPs")}, "peers": peers,
            "backend_state": data.get("BackendState")}


def action_tailscale_up(_params: dict) -> dict:
    return _run(["tailscale", "up"], timeout=20)


def action_tailscale_down(_params: dict) -> dict:
    return _run(["tailscale", "down"], timeout=15)


def _wg_interface() -> str:
    iface = os.getenv("WIREGUARD_INTERFACE", "wg0")
    return iface if _IFACE_RE.match(iface) else "wg0"


def action_wg_status(_params: dict) -> dict:
    if not shutil.which("wg"):
        return {"ok": True, "installed": False, "active": False}
    result = _run(["wg", "show"], timeout=5)
    active = bool(result.get("stdout"))
    return {"ok": True, "installed": True, "active": active, "raw": result.get("stdout", "")}


def action_wg_up(_params: dict) -> dict:
    return _run(["sudo", "-n", "wg-quick", "up", _wg_interface()], timeout=15)


def action_wg_down(_params: dict) -> dict:
    return _run(["sudo", "-n", "wg-quick", "down", _wg_interface()], timeout=15)


ACTIONS = {
    "ngrok_status": action_ngrok_status,
    "ngrok_start": action_ngrok_start,
    "ngrok_stop": action_ngrok_stop,
    "tailscale_status": action_tailscale_status,
    "tailscale_up": action_tailscale_up,
    "tailscale_down": action_tailscale_down,
    "wg_status": action_wg_status,
    "wg_up": action_wg_up,
    "wg_down": action_wg_down,
}

READ_ONLY_ACTIONS = {"ngrok_status", "tailscale_status", "wg_status"}


# ── HTTP ────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    token = ""  # impostato da main() prima di avviare il server

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:], self.token)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            self._json({"error": "token mancante o non valido"}, 401)
            return
        if self.path == "/status":
            self._json({
                "ngrok": action_ngrok_status({}),
                "tailscale": action_tailscale_status({}),
                "wireguard": action_wg_status({}),
            })
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            self._json({"error": "token mancante o non valido"}, 401)
            return
        if self.path != "/action":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, OSError):
            self._json({"error": "corpo della richiesta non valido"}, 400)
            return

        action = payload.get("action")
        handler = ACTIONS.get(action)
        if not handler:
            self._json({"error": f"azione sconosciuta: {action!r}",
                        "available": sorted(ACTIONS)}, 400)
            return
        try:
            result = handler(payload)
        except Exception as error:  # difensivo: un'azione non deve mai far cadere il server
            result = {"ok": False, "error": str(error)}
        self._json(result)

    def log_message(self, fmt, *args):
        print(f"[hostctl] {self.address_string()} - {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--generate-token", action="store_true",
                         help="genera HOSTCTL_TOKEN e lo scrive in .env, poi esce")
    parser.add_argument("--port", type=int, default=None, help="override di HOSTCTL_PORT")
    args = parser.parse_args()

    if args.generate_token:
        generate_token(ENV_PATH)
        return

    env = {**load_env(ENV_PATH), **os.environ}  # os.environ vince se sovrapposto
    token = env.get("HOSTCTL_TOKEN", "")
    if not token:
        print("HOSTCTL_TOKEN non impostato — l'agent resta disattivato.")
        print(f"Esegui prima: python3 {sys.argv[0]} --generate-token")
        sys.exit(1)

    port = args.port or int(env.get("HOSTCTL_PORT", "8765"))
    Handler.token = token

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"hostctl in ascolto su http://127.0.0.1:{port} (solo loopback)")
    print("azioni disponibili:", ", ".join(sorted(ACTIONS)))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nfermato.")


if __name__ == "__main__":
    main()
