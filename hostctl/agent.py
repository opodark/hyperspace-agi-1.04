#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Host-agent HyperSpace — rete host e broker ristretto Docker Sandboxes.

Perche' esiste: il control-plane gira dentro un container Docker e non puo'
lanciare `tailscale status` o `wg show` sull'host che lo ospita. Questo
processo gira NATIVO sull'host (non in Docker, apposta) ed espone quello che
serve via HTTP; il control-plane lo raggiunge via host.docker.internal.

Sicurezza, non negoziabile:
  - ascolta su un IP host specifico — mai 0.0.0.0/::; default 127.0.0.1,
    su Linux si configura esplicitamente l'IP del bridge Docker;
  - ogni richiesta deve portare il token in `Authorization: Bearer <token>`,
    confrontato a tempo costante — il bind su loopback non basta da solo,
    host.docker.internal in certe configurazioni Docker e' raggiungibile
    piu' largamente del previsto;
  - azioni da una whitelist fissa, mai un comando costruito da input libero:
    ogni azione e' un argv list precompilato, zero shell=True;
  - l'unica eccezione e' `shell_run`, che accetta un argv dal chiamante e in
    cambio paga tre pareti: allowlist di eseguibili PER NOME, cwd dentro le
    directory ammesse, output e tempo limitati — piu' SHELL_RUN_ENABLED, che
    nasce false. Gira sull'host con i permessi di chi avvia l'agent: e' una
    capability da accendere sapendo cosa significa (vedi docs/host-access.md),
    non un default;
  - controlla SOLO la macchina locale — non esiste un modo di chiedere a
    questo agent di toccare la rete di un'altra macchina della mesh.

Uso:
    python3 hostctl/agent.py --generate-token   # scrive due token distinti in .env
    python3 hostctl/agent.py                    # avvia, legge .env

Disabilitato finche' HOSTCTL_TOKEN non esiste: senza, l'avvio si rifiuta.

Nota su ble_scan: e' l'unica azione con una dipendenza esterna (`bleak`,
MIT) — tutto il resto di questo file resta stdlib puro apposta. Verificato
2026-09-14: bleak fa scansione (ruolo BLE central/client) su tutte e tre le
piattaforme, ma NON annuncio/GATT server (ruolo peripheral) — nessuna
libreria seria lo fa in modo unificato multipiattaforma oggi (macOS
servirebbe CoreBluetooth via PyObjC diretto, Linux BlueZ via D-Bus, Windows
WinRT — tre implementazioni native separate). Un mesh BLE bidirezionale
resta quindi fuori scope finche' non si scrive quella parte da zero per
ciascun OS; qui c'e' solo la meta' "vedo chi c'e' vicino", non "mi annuncio".
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from bleak import BleakScanner
    _BLEAK_AVAILABLE = True
except ImportError:
    _BLEAK_AVAILABLE = False
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = Path(os.getenv("HOSTCTL_ENV_PATH", BASE_DIR / ".env"))
STATE_PATH = Path(os.getenv("HOSTCTL_STATE_PATH", Path.home() / ".hyperspace" / "hostctl_state.json"))

_IFACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_ngrok_lock = threading.Lock()
_file_env: dict[str, str] = {}
MAX_REQUEST_BYTES = 2 * 1024 * 1024
_SBX_NAME_RE = re.compile(r"^hyperspace-[a-z0-9-]{8,48}$")
_SBX_ALLOWED_EXECUTABLES = {"python", "python3", "node", "npm", "npx", "pytest", "git"}


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


def _env_has_key(lines: list[str], key: str) -> bool:
    return any(line.partition("=")[0].strip() == key for line in lines
               if "=" in line and not line.lstrip().startswith("#"))


def generate_token(path: Path, key: str = "HOSTCTL_TOKEN") -> str:
    token = secrets.token_hex(32)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if _env_has_key(lines, key):
        print(f"{key} esiste gia' in {path} — non sovrascritto.")
        print("Cancella quella riga a mano se vuoi rigenerarlo.")
        sys.exit(1)
    lines.append(f"{key}={token}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)
    print(f"{key} scritto in {path}.")
    return token


def generate_missing_tokens(path: Path) -> dict[str, str]:
    """Genera i due segreti indipendenti senza ruotare quelli esistenti."""
    generated = {}
    for key in ("HOSTCTL_TOKEN", "NETWORK_ADMIN_TOKEN"):
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if _env_has_key(lines, key):
            print(f"{key} esiste gia' in {path} — non sovrascritto.")
            continue
        generated[key] = generate_token(path, key)
    return generated


def apply_file_env(path: Path) -> dict:
    """Carica la configurazione senza esportare segreti ai subprocess."""
    global _file_env
    values = load_env(path)
    _file_env = values
    return {**values, **os.environ}


def config_value(key: str, default: str = "") -> str:
    return os.environ.get(key, _file_env.get(key, default))


def validate_bind_address(value: str) -> str:
    """Accetta un IP host specifico, mai un wildcard bind."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("HOSTCTL_BIND deve essere un indirizzo IP esplicito") from error
    if address.is_unspecified:
        raise ValueError("HOSTCTL_BIND non può essere 0.0.0.0 o ::")
    return str(address)


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
    if _is_windows():
        result = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=5)
    else:
        result = _run(["ps", "-p", str(pid), "-o", "comm="], timeout=5)
    return result.get("ok", False) and name_fragment.lower() in result.get("stdout", "").lower()


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_program_file(folder: str, executable: str) -> str | None:
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    candidate = Path(program_files) / folder / executable
    return str(candidate) if candidate.is_file() else None


def _tailscale_executable() -> str | None:
    configured = config_value("TAILSCALE_EXECUTABLE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    return (shutil.which("tailscale.exe") or shutil.which("tailscale") or
            (_windows_program_file("Tailscale", "tailscale.exe") if _is_windows() else None))


def _wg_executable() -> str | None:
    configured = config_value("WG_EXECUTABLE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    return (shutil.which("wg.exe") or shutil.which("wg") or
            (_windows_program_file("WireGuard", "wg.exe") if _is_windows() else None))


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
    with _ngrok_lock:
        return _action_ngrok_start(params)


def _action_ngrok_start(params: dict) -> dict:
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
    with _ngrok_lock:
        return _action_ngrok_stop(_params)


def _action_ngrok_stop(_params: dict) -> dict:
    state = _read_state()
    pid = state.get("ngrok_pid")
    if not pid:
        return {"ok": False, "error": "nessun ngrok tracciato da questo agent"}
    if not _pid_alive_and_named(pid, "ngrok"):
        state.pop("ngrok_pid", None)
        _write_state(state)
        return {"ok": False, "error": "il pid tracciato non e' (piu') ngrok — stato ripulito"}
    os.kill(pid, signal.SIGTERM)
    state.pop("ngrok_pid", None)
    _write_state(state)
    return {"ok": True, "stopped_pid": pid}


def action_tailscale_status(_params: dict) -> dict:
    executable = _tailscale_executable()
    if not executable:
        return {"ok": False, "error": "tailscale non installato o non nel PATH"}
    result = _run([executable, "status", "--json"], timeout=8)
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
    executable = _tailscale_executable()
    if not executable:
        return {"ok": False, "error": "tailscale non installato o non nel PATH"}
    return _run([executable, "up"], timeout=20)


def action_tailscale_down(_params: dict) -> dict:
    executable = _tailscale_executable()
    if not executable:
        return {"ok": False, "error": "tailscale non installato o non nel PATH"}
    return _run([executable, "down"], timeout=15)


def _wg_interface() -> str:
    iface = config_value("WIREGUARD_INTERFACE", "wg0")
    return iface if _IFACE_RE.match(iface) else "wg0"


def action_wg_status(_params: dict) -> dict:
    executable = _wg_executable()
    if not executable:
        return {"ok": True, "installed": False, "active": False}
    result = _run([executable, "show"], timeout=5)
    active = bool(result.get("stdout"))
    return {"ok": True, "installed": True, "active": active, "raw": result.get("stdout", "")}


def action_wg_up(_params: dict) -> dict:
    if _is_windows():
        executable = _wireguard_windows_executable()
        config = config_value("WIREGUARD_CONFIG", "").strip()
        if not executable:
            return {"ok": False, "error": "wireguard.exe non trovato"}
        if not config:
            return {"ok": False, "error": "WIREGUARD_CONFIG non impostato"}
        config_path = Path(config).expanduser().resolve()
        if not config_path.is_file():
            return {"ok": False, "error": f"config WireGuard non trovata: {config_path}"}
        return _run([executable, "/installtunnelservice", str(config_path)], timeout=20)
    return _run(["sudo", "-n", "wg-quick", "up", _wg_interface()], timeout=15)


def action_wg_down(_params: dict) -> dict:
    if _is_windows():
        executable = _wireguard_windows_executable()
        if not executable:
            return {"ok": False, "error": "wireguard.exe non trovato"}
        return _run([executable, "/uninstalltunnelservice", _wg_interface()], timeout=20)
    return _run(["sudo", "-n", "wg-quick", "down", _wg_interface()], timeout=15)


def _wireguard_windows_executable() -> str | None:
    configured = config_value("WIREGUARD_EXECUTABLE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    discovered = shutil.which("wireguard.exe") or shutil.which("wireguard")
    if discovered:
        return discovered
    return _windows_program_file("WireGuard", "wireguard.exe")


def action_ble_scan(params: dict) -> dict:
    """Scansione BLE (solo lettura, ruolo client/central). Vedi la nota in
    cima al file: niente annuncio/peripheral, nessuna libreria seria lo fa
    in modo unificato multipiattaforma oggi."""
    if not _BLEAK_AVAILABLE:
        return {"ok": False, "error": "bleak non installato — pip install bleak (opzionale, "
                "solo questa azione ne ha bisogno)"}
    try:
        seconds = float(params.get("seconds", config_value("BLE_SCAN_SECONDS", "6")))
        seconds = max(1.0, min(seconds, 30.0))  # tetto: uno scan non deve poter appendere il server a lungo
    except (TypeError, ValueError):
        seconds = 6.0

    async def _scan():
        devices = await BleakScanner.discover(timeout=seconds)
        return [{"address": d.address, "name": d.name} for d in devices]

    try:
        found = asyncio.run(_scan())
    except Exception as error:  # bleak solleva eccezioni diverse per OS/permessi
        return {"ok": False, "error": f"scan fallita: {error}"}
    return {"ok": True, "seconds": seconds, "count": len(found), "devices": found}


def _sbx_executable() -> str | None:
    configured = config_value("SBX_EXECUTABLE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    discovered = shutil.which("sbx.exe") or shutil.which("sbx")
    if discovered:
        return discovered
    if _is_windows():
        local_app_data = os.getenv("LOCALAPPDATA", "")
        candidate = Path(local_app_data) / "DockerSandboxes" / "bin" / "sbx.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _sbx_name(value: object) -> str:
    name = str(value or "")
    if not _SBX_NAME_RE.fullmatch(name):
        raise ValueError("invalid HyperSpace sandbox name")
    return name


def _sbx_exec(executable: str, name: str, argv: list[str], timeout: int = 120,
              input_text: str | None = None) -> dict:
    try:
        proc = subprocess.run([executable, "exec", name, *argv], input=input_text,
                              capture_output=True, text=True, timeout=timeout)
        stdout, stderr = proc.stdout[-262144:], proc.stderr[-262144:]
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
                "stdout": stdout, "stderr": stderr,
                "output": (proc.stdout + proc.stderr)[-262144:],
                "truncated": len(proc.stdout) + len(proc.stderr) > 262144}
    except subprocess.TimeoutExpired as error:
        output = ((error.stdout or "") + (error.stderr or ""))[-262144:]
        return {"ok": False, "error": "command timed out", "output": output}


def _sbx_json(executable: str, name: str, code: str, args: list[str], *,
              timeout: int = 30, input_text: str | None = None) -> dict:
    result = _sbx_exec(executable, name, ["python", "-c", code, *args], timeout, input_text)
    if not result.get("ok"):
        return result
    try:
        return json.loads(result.get("stdout", ""))
    except ValueError:
        return {"ok": False, "error": "sandbox returned invalid JSON",
                "output": result.get("output", "")[:1000]}


def action_sbx_sandbox(params: dict) -> dict:
    """Narrow broker for Docker Sandboxes; arbitrary commands stay inside its microVM."""
    executable = _sbx_executable()
    operation = str(params.get("operation", "status")).lower()
    if not executable:
        return {"ok": False, "ready": False, "installed": False,
                "error": "sbx is not installed or not in PATH"}
    if operation == "status":
        version = _run([executable, "version"], timeout=8)
        listing = _run([executable, "ls", "--json"], timeout=8)
        return {"ok": bool(version.get("ok") and listing.get("ok")),
                "ready": bool(version.get("ok") and listing.get("ok")), "installed": True,
                "version": version.get("stdout", ""),
                "sandboxes": json.loads(listing.get("stdout") or "[]") if listing.get("ok") else [],
                "error": listing.get("stderr") or version.get("stderr") or ""}
    if operation == "create":
        source = Path(config_value("SBX_SOURCE_DIR", str(BASE_DIR))).expanduser().resolve()
        if not source.is_dir() or not (source / ".git").exists():
            return {"ok": False, "error": "SBX_SOURCE_DIR is not a Git repository"}
        name = "hyperspace-" + secrets.token_hex(8)
        argv = [executable, "create", "shell", str(source), "--clone", "--name", name,
                "--cpus", config_value("SBX_CPUS", "2"),
                "--memory", config_value("SBX_MEMORY", "4g"),
                "--deny-network", "**", "--quiet"]
        result = _run(argv, timeout=180)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("stderr") or result.get("error") or "sbx create failed"}
        return {"ok": True, "workspace_id": name, "label": str(params.get("label", ""))[:120],
                "network": "deny-all", "clone": "private-readonly-source"}

    name = _sbx_name(params.get("workspace_id"))
    if operation == "discard":
        result = _run([executable, "rm", name, "--force"], timeout=60)
        return {"ok": result.get("ok", False), "discarded": name,
                "error": result.get("stderr") or result.get("error", "")}
    if operation == "run":
        argv = params.get("argv")
        if not isinstance(argv, list) or not argv or len(argv) > 32:
            raise ValueError("argv must contain 1-32 items")
        argv = [str(item)[:1000] for item in argv]
        command = Path(argv[0]).name
        if argv[0] != command or command not in _SBX_ALLOWED_EXECUTABLES:
            raise ValueError("executable is not allowed")
        timeout = max(1, min(int(params.get("timeout", 30)), 120))
        return _sbx_exec(executable, name, argv, timeout)
    if operation == "diff":
        staged = _sbx_exec(executable, name, ["git", "add", "-N", "--all"], 30)
        if not staged.get("ok"):
            return staged
        changed = _sbx_exec(executable, name, ["git", "status", "--porcelain"], 30)
        diff = _sbx_exec(executable, name, ["git", "diff", "--no-ext-diff", "--binary", "--"], 30)
        files = [line[3:].strip() for line in changed.get("stdout", "").splitlines() if len(line) > 3]
        return {"ok": bool(changed.get("ok") and diff.get("ok")), "changed_files": files,
                "diff": diff.get("stdout", ""), "truncated": diff.get("truncated", False),
                "warnings": (changed.get("stderr", "") + diff.get("stderr", ""))[-4000:]}

    path_code = (
        "import json,pathlib,sys; root=pathlib.Path.cwd().resolve(); "
        "p=(root/sys.argv[1]).resolve(); "
        "assert p==root or root in p.parents, 'path escapes workspace'; "
    )
    relative = str(params.get("path", "."))
    if len(relative) > 500 or Path(relative).is_absolute():
        raise ValueError("invalid relative path")
    if operation == "read":
        code = path_code + "assert p.is_file(), 'not a file'; print(json.dumps({'ok':True,'path':sys.argv[1],'content':p.read_text(encoding='utf-8',errors='replace')}))"
        return _sbx_json(executable, name, code, [relative])
    if operation == "write":
        content = str(params.get("content", ""))
        if len(content.encode("utf-8")) > 1048576:
            raise ValueError("content exceeds write limit")
        code = path_code + "p.parent.mkdir(parents=True,exist_ok=True); data=sys.stdin.read(); p.write_text(data,encoding='utf-8'); print(json.dumps({'ok':True,'path':sys.argv[1],'bytes':len(data.encode())}))"
        return _sbx_json(executable, name, code, [relative], input_text=content)
    if operation == "replace":
        payload = json.dumps({"old": str(params.get("old", "")), "new": str(params.get("new", "")),
                              "expected": int(params.get("expected_occurrences", 1))})
        code = path_code + "d=json.loads(sys.stdin.read()); s=p.read_text(encoding='utf-8'); n=s.count(d['old']); assert d['old'] and n==d['expected'], f'expected {d[\"expected\"]}, found {n}'; p.write_text(s.replace(d['old'],d['new'],d['expected']),encoding='utf-8'); print(json.dumps({'ok':True,'path':sys.argv[1],'replacements':n}))"
        return _sbx_json(executable, name, code, [relative], input_text=payload)
    if operation == "list":
        pattern = str(params.get("pattern", "*"))[:200]
        limit = max(1, min(int(params.get("limit", 200)), 1000))
        code = "import fnmatch,json,pathlib,sys; r=pathlib.Path.cwd(); pat=sys.argv[1]; lim=int(sys.argv[2]); f=[str(p.relative_to(r)).replace('\\\\','/') for p in r.rglob('*') if p.is_file() and (fnmatch.fnmatch(str(p.relative_to(r)).replace('\\\\','/'),pat) or fnmatch.fnmatch(p.name,pat))][:lim]; print(json.dumps({'ok':True,'files':f,'truncated':len(f)>=lim}))"
        return _sbx_json(executable, name, code, [pattern, str(limit)])
    raise ValueError(f"unsupported sbx sandbox operation: {operation}")


# ── shell_run: comandi reali senza una shell (Stage 1 di docs/host-access.md) ─
# Le mani dell'agente, con le stesse pareti del resto di questo file: argv
# precalcolato (mai `shell=True`), allowlist di eseguibili PER NOME e di
# directory per il cwd, output e tempo limitati. La differenza da sbx_sandbox
# e' che qui NON c'e' una microVM: il comando gira sull'host, con i permessi di
# chi ha avviato l'agent. Per questo nasce SPENTO (SHELL_RUN_ENABLED=false):
# accenderlo e' una decisione, non un default.
_SHELL_DEFAULT_EXECUTABLES = frozenset({"python", "python3", "node", "npm", "npx", "pytest", "git"})
_SHELL_METACHARACTERS = frozenset({
    "|", "||", "&", "&&", ";", ";;", "<", ">", ">>", "<<", "1>", "2>", "2>&1", "$(", "`",
})
_SHELL_MAX_ARGS = 32
_SHELL_MAX_ARG_CHARS = 4096
_SHELL_WINDOWS_SUFFIXES = (".exe", ".cmd", ".bat", ".com")


def _shell_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(config_value(name, str(default))), maximum))
    except (TypeError, ValueError):
        return default


def _shell_allowed_executables() -> frozenset:
    configured = config_value("SHELL_ALLOWED_EXECUTABLES", "").strip()
    if not configured:
        return _SHELL_DEFAULT_EXECUTABLES
    return frozenset(item.strip().lower() for item in configured.split(",") if item.strip())


def _shell_executable_name(value: object) -> str:
    """Il nome con cui ragiona l'allowlist: basename, minuscolo, senza suffisso."""
    name = Path(str(value)).name.lower()
    for suffix in _SHELL_WINDOWS_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _shell_argv(params: dict) -> list[str]:
    """Valida l'argv. Il confine vero e' `shell=False` + allowlist: questi
    controlli servono a dire *no* in modo leggibile quando il chiamante crede di
    parlare con una shell, non a fare pattern-matching di sicurezza."""
    argv = params.get("argv")
    if isinstance(argv, str) or not isinstance(argv, (list, tuple)) or not argv:
        raise ValueError("argv deve essere una lista non vuota di argomenti (nessuna stringa di shell)")
    values = [str(item) for item in argv]
    if len(values) > _SHELL_MAX_ARGS:
        raise ValueError(f"argv troppo lungo: massimo {_SHELL_MAX_ARGS} argomenti")
    for index, item in enumerate(values):
        if not item.strip():
            raise ValueError(f"argv[{index}] e' vuoto")
        if len(item) > _SHELL_MAX_ARG_CHARS:
            raise ValueError(f"argv[{index}] supera {_SHELL_MAX_ARG_CHARS} caratteri")
        if any(char in item for char in ("\x00", "\n", "\r")):
            raise ValueError(f"argv[{index}] contiene un carattere di controllo")
        if item in _SHELL_METACHARACTERS:
            raise ValueError(f"argv[{index}] e' un operatore di shell ({item!r}): "
                             "questo percorso esegue comandi, non stringhe di shell")
    return values


def _shell_cwd(params: dict) -> Path:
    """cwd dentro una delle directory ammesse (default: la radice del repo)."""
    roots = []
    for item in config_value("SHELL_ALLOWED_DIRS", "").split(","):
        if item.strip():
            roots.append(Path(item.strip()).expanduser().resolve())
    if not roots:
        roots = [BASE_DIR]
    raw = str(params.get("cwd", "") or "").strip()
    if not raw:
        return roots[0]
    candidate = Path(raw).expanduser().resolve()
    for root in roots:
        if candidate == root or root in candidate.parents:
            if not candidate.is_dir():
                raise ValueError(f"cwd non e' una directory: {candidate}")
            return candidate
    raise ValueError(f"cwd fuori dalle directory ammesse: {candidate} (vedi SHELL_ALLOWED_DIRS)")


def _shell_cap(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = str(text or "").encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return str(text or ""), False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def _shell_run_exec(argv: list, cwd: Path, timeout: int, max_bytes: int) -> dict:
    """L'unico punto di questa azione che lancia un processo."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "GIT_TERMINAL_PROMPT": "0",
           "GIT_PAGER": "cat", "PAGER": "cat"}
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, shell=False,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        parziale, _ = _shell_cap((error.stdout or "") + (error.stderr or ""), max_bytes)
        return {"ok": False, "timed_out": True, "exit_code": None,
                "error": f"timeout dopo {timeout}s", "output": parziale}
    except FileNotFoundError:
        return {"ok": False, "error": f"comando non trovato: {argv[0]}"}
    except OSError as error:
        return {"ok": False, "error": f"esecuzione fallita: {error}"}
    stdout, out_truncated = _shell_cap(proc.stdout, max_bytes)
    stderr, err_truncated = _shell_cap(proc.stderr, max_bytes)
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": stdout, "stderr": stderr, "output": stdout + stderr,
            "truncated": out_truncated or err_truncated,
            "duration_ms": int((time.monotonic() - started) * 1000)}


def action_shell_run(params: dict) -> dict:
    """Esegue un comando come argv sull'host. Spento finche' non lo si accende."""
    if config_value("SHELL_RUN_ENABLED", "false").strip().lower() != "true":
        return {"ok": False, "enabled": False,
                "error": "shell_run disabilitato: imposta SHELL_RUN_ENABLED=true per accenderlo"}
    argv = _shell_argv(params)
    name = _shell_executable_name(argv[0])
    if name not in _shell_allowed_executables():
        raise ValueError(f"eseguibile non ammesso: {name!r} (vedi SHELL_ALLOWED_EXECUTABLES)")
    resolved = shutil.which(argv[0])
    if not resolved:
        return {"ok": False, "error": f"comando non trovato: {argv[0]}"}
    cwd = _shell_cwd(params)
    timeout = _shell_int("SHELL_MAX_TIMEOUT", 60, 1, 600)
    if params.get("timeout") is not None:
        try:
            timeout = max(1, min(int(params["timeout"]), timeout))
        except (TypeError, ValueError):
            raise ValueError("timeout deve essere un intero (secondi)")
    max_bytes = _shell_int("SHELL_MAX_OUTPUT_BYTES", 65536, 1024, 1048576)
    result = _shell_run_exec([resolved, *argv[1:]], cwd, timeout, max_bytes)
    return {"enabled": True, "argv0": name, "cwd": str(cwd), "timeout_s": timeout, **result}


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
    "ble_scan": action_ble_scan,
    "sbx_sandbox": action_sbx_sandbox,
    "shell_run": action_shell_run,
}

# Sola lettura = osserva e basta. `shell_run` NON e' qui: esegue comandi con i
# permessi dell'agent, quindi appartiene al gruppo che cambia la macchina (in
# dashboard e' il gruppo che chiede conferma).
READ_ONLY_ACTIONS = {"ngrok_status", "tailscale_status", "wg_status", "ble_scan"}


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
            if length < 0 or length > MAX_REQUEST_BYTES:
                self._json({"error": "corpo della richiesta troppo grande"}, 413)
                return
            payload = json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, OSError):
            self._json({"error": "corpo della richiesta non valido"}, 400)
            return
        if not isinstance(payload, dict):
            self._json({"error": "il corpo della richiesta deve essere un oggetto JSON"}, 400)
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
        generated = generate_missing_tokens(ENV_PATH)
        if generated:
            print("I token sono distinti: HOSTCTL_TOKEN resta server-to-server;")
            print("NETWORK_ADMIN_TOKEN va inserito nella tab Network della dashboard.")
        return

    env = apply_file_env(ENV_PATH)  # os.environ vince se sovrapposto
    token = env.get("HOSTCTL_TOKEN", "")
    if len(token) < 32:
        print("HOSTCTL_TOKEN assente o troppo corto — l'agent resta disattivato.")
        print(f"Esegui prima: python3 {sys.argv[0]} --generate-token")
        sys.exit(1)

    try:
        port = args.port or int(env.get("HOSTCTL_PORT", "8765"))
    except (TypeError, ValueError):
        print("HOSTCTL_PORT deve essere un numero intero.")
        sys.exit(1)
    if not 1 <= port <= 65535:
        print("HOSTCTL_PORT deve essere compresa fra 1 e 65535.")
        sys.exit(1)
    try:
        bind = validate_bind_address(env.get("HOSTCTL_BIND", "127.0.0.1"))
    except ValueError as error:
        print(error)
        sys.exit(1)
    Handler.token = token

    server = ThreadingHTTPServer((bind, port), Handler)
    print(f"hostctl in ascolto su http://{bind}:{port}")
    print("azioni disponibili:", ", ".join(sorted(ACTIONS)))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nfermato.")


if __name__ == "__main__":
    main()
