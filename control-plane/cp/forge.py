# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/forge.py
# IL FORGE: DOVE IL CONTROL-PLANE SCRIVE GLI ARTEFATTI.
#
# Il forge è il magazzino di tool e skill: piccoli pezzi di codice che il
# control-plane può scrivere, rileggere e usare. Un artefatto non è un plugin
# installato — è un file JSON in una directory, e questo dice due cose: si può
# guardare con qualsiasi editor, e si può perdere con un rm.
#
# La cosa da capire prima di tutto il resto è che il forge non protegge tutto con
# il token, e non è una dimenticanza. Il token protegge il PASSAGGIO DI STATO, non
# la scrittura: creare una bozza e correggerla si può fare senza token; quello che
# richiede `FORGE_ADMIN_TOKEN` è approvare, e importare l'ECC.
#
# Il motivo è che una bozza non viene eseguita. Senza approvazione resta in
# `review`, e nessun modulo la carica. Il confine quindi non è "chi può scrivere" ma
# "chi può rendere eseguibile", che è la seconda domanda e quella giusta: il forge
# è pensato perché l'operatore possa lavorare dal browser, e chiedere un token per
# ogni bozza renderebbe il browser inutilizzabile.
#
# Il rovescio della medaglia è che la risposta a "chi può scrivere" è "chiunque
# raggiunga la porta". È accettabibile finché il control-plane è sulla rete locale,
# ed è il motivo per cui l'approvazione non è una formalità.

import ast
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from cp.config import (CODE_SERVER_PORT, FORGE_ADMIN_TOKEN, FORGE_DIR,
                       FORGE_MODEL, OLLAMA_URL)
from cp.inferenza import _local_model_post
from shared.forge_skills import ECC_BUNDLE_DIR, load_ecc_bundle, source_hash
from cp.log import push_log

_bp = Blueprint("forge", __name__)

# I tipi e gli stati validi. Sono qui e non in `cp/config.py` perché non sono
# configurabili: sono il vocabolario del forge, e cambiare una lista di questo
# genere per un errore di battitura lascerebbe metà degli artefatti esistenti
# irraggiungibili senza dirlo.
_FORGE_TYPES = {"tool", "skill", "patch"}
_FORGE_STATES = {"draft", "review", "approved", "disabled"}

# Il lock del negozio. Le rotte girano su più thread e il forge è su disco: due
# scritture sullo stesso artefatto senza lock lasciano un file a metà.
_forge_lock = threading.Lock()

# L'app, per il request context. `forge_generate` riusa `forge_create` costruendo un
# contesto di richiesta invece di duplicarne la validazione: due copie della stessa
# validazione divergono, e quella che diverge e' quella che crea artefatti.
_app = None


def monta(app):
    """Registra le rotte /forge/*.

    Nessuna iniezione: il forge non chiama il modello, non tocca la rete e non ha
    bisogno di nessuno stato del boot. E' il dominio più autonomo del repository, e
    per questo non ha nemmeno un contesto.
    """
    global _app
    _app = app
    app.register_blueprint(_bp)
    return app

# ── il negozio: dove stanno gli artefatti e chi li puo' toccare ───────────

_forge_lock = threading.Lock()


def _forge_slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug[:64] or f"artifact-{uuid.uuid4().hex[:8]}"


def _forge_path(artifact_id):
    safe_id = re.sub(r"[^a-z0-9-]", "", str(artifact_id).lower())
    if not safe_id or safe_id != artifact_id:
        raise ValueError("invalid artifact id")
    return os.path.join(FORGE_DIR, safe_id + ".json")


def _forge_read_all():
    os.makedirs(FORGE_DIR, exist_ok=True)
    items = []
    for filename in os.listdir(FORGE_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(FORGE_DIR, filename), "r", encoding="utf-8") as handle:
                items.append(json.load(handle))
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)


def _forge_write(item):
    os.makedirs(FORGE_DIR, exist_ok=True)
    path = _forge_path(item["id"])
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(item, handle, ensure_ascii=False, indent=2)
    extension = {"tool": ".py", "patch": ".diff"}.get(item.get("type"), ".md")
    source_path = os.path.join(FORGE_DIR, item["id"] + extension)
    source_temporary = source_path + ".tmp"
    with open(source_temporary, "w", encoding="utf-8") as handle:
        handle.write(item.get("source", ""))
    os.replace(source_temporary, source_path)
    os.replace(temporary, path)


def _forge_authorized():
    if not FORGE_ADMIN_TOKEN:
        return False
    supplied = request.headers.get("X-Hyperspace-Forge-Token", "")
    return hashlib.sha256(supplied.encode()).digest() == hashlib.sha256(FORGE_ADMIN_TOKEN.encode()).digest()

# ── la validazione, che e' cio' che distingue un artefatto usabile ────────

def _forge_validate(kind, source):
    issues, warnings = [], []
    source = source or ""
    if not source.strip():
        issues.append("source is empty")
    if len(source.encode("utf-8")) > 256_000:
        issues.append("source exceeds 256 KB")
    if kind == "tool" and source.strip():
        try:
            tree = ast.parse(source)
            classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
            if "Tools" not in classes:
                warnings.append("Open WebUI tools normally expose a top-level Tools class")
            risky = {"subprocess", "socket", "ctypes"}
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
            found = sorted(imports & risky)
            if found:
                warnings.append("security review required for imports: " + ", ".join(found))
        except SyntaxError as error:
            issues.append(f"python syntax error at line {error.lineno}: {error.msg}")
    if kind == "skill" and source.strip():
        if not source.lstrip().startswith(("#", "---\n", "---\r\n")):
            warnings.append("skill should start with a Markdown heading")
        if len(source.split()) < 20:
            warnings.append("skill instructions are unusually short")
    if kind == "patch" and source.strip():
        has_headers = "--- a/" in source and "+++ b/" in source
        has_hunk = re.search(r"^@@ .* @@", source, re.MULTILINE) is not None
        if not has_headers or not has_hunk:
            issues.append("patch must be a unified text diff with file headers and a hunk")
        if "Binary files differ:" in source:
            issues.append("binary changes cannot be represented by this Forge patch")
    return {"valid": not issues, "issues": issues, "warnings": warnings}

# ── le rotte: leggere e scrivere ──────────────────────────────────────────

@_bp.route('/forge/artifacts')
def forge_list():
    return jsonify({"artifacts": _forge_read_all(), "approval_configured": bool(FORGE_ADMIN_TOKEN)})


@_bp.route('/forge/config')
def forge_config():
    port = CODE_SERVER_PORT if CODE_SERVER_PORT.isdigit() else "8443"
    return jsonify({"ide_url": f"http://127.0.0.1:{port}", "ide_artifacts_dir": "/home/coder/forge"})


@_bp.route('/forge/artifacts', methods=['POST'])
def forge_create():
    data = request.get_json(force=True, silent=True) or {}
    kind = str(data.get("type", "skill")).lower()
    if kind not in _FORGE_TYPES:
        return jsonify({"error": "type must be tool, skill, or patch"}), 400
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    source = str(data.get("source", ""))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    item = {
        "id": _forge_slug(name) + "-" + uuid.uuid4().hex[:6],
        "name": name[:120], "type": kind, "description": str(data.get("description", ""))[:1000],
        "source": source, "permissions": list(data.get("permissions") or []),
        "status": "draft", "version": 1, "created_at": now, "updated_at": now,
        "validation": _forge_validate(kind, source), "generator": data.get("generator") or "human",
    }
    with _forge_lock:
        _forge_write(item)
    push_log('system', f'Forge draft created: {item["id"]}', status='info')
    return jsonify(item), 201


@_bp.route('/forge/artifacts/<artifact_id>', methods=['PUT'])
def forge_update(artifact_id):
    data = request.get_json(force=True, silent=True) or {}
    try:
        path = _forge_path(artifact_id)
        with _forge_lock:
            with open(path, "r", encoding="utf-8") as handle:
                item = json.load(handle)
            kind = str(data.get("type", item.get("type", "skill"))).lower()
            if kind not in _FORGE_TYPES:
                return jsonify({"error": "type must be tool, skill, or patch"}), 400
            name = str(data.get("name", item.get("name", ""))).strip()
            if not name:
                return jsonify({"error": "name is required"}), 400
            source = str(data.get("source", item.get("source", "")))
            item.update({
                "name": name[:120],
                "type": kind,
                "description": str(data.get("description", item.get("description", "")))[:1000],
                "source": source,
                "permissions": list(data.get("permissions", item.get("permissions", [])) or []),
                "status": "draft",
                "version": int(item.get("version", 1)) + 1,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "validation": _forge_validate(kind, source),
                "generator": data.get("generator") or item.get("generator") or "human",
            })
            item.pop("approved_source_sha256", None)
            _forge_write(item)
    except (OSError, ValueError, TypeError):
        return jsonify({"error": "artifact not found"}), 404
    push_log('system', f'Forge artifact updated: {artifact_id} v{item["version"]}', status='info')
    return jsonify(item)


@_bp.route('/forge/artifacts/<artifact_id>/status', methods=['POST'])
def forge_status(artifact_id):
    data = request.get_json(force=True, silent=True) or {}
    state = str(data.get("status", ""))
    if state not in _FORGE_STATES:
        return jsonify({"error": "invalid status"}), 400
    if state == "approved" and not _forge_authorized():
        return jsonify({"error": "approval requires FORGE_ADMIN_TOKEN"}), 403
    try:
        path = _forge_path(artifact_id)
        with _forge_lock:
            with open(path, "r", encoding="utf-8") as handle:
                item = json.load(handle)
            validation = _forge_validate(item["type"], item.get("source", ""))
            if state == "approved" and not validation["valid"]:
                return jsonify({"error": "artifact is not valid", "validation": validation}), 409
            item.update({"status": state, "validation": validation,
                         "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            item.pop("approved_source_sha256", None)
            if state == "approved":
                item["approved_source_sha256"] = source_hash(item.get("source", ""))
            _forge_write(item)
    except (OSError, ValueError):
        return jsonify({"error": "artifact not found"}), 404
    push_log('system', f'Forge artifact {artifact_id} -> {state}', status='success')
    return jsonify(item)


@_bp.route('/forge/generate', methods=['POST'])
def forge_generate():
    data = request.get_json(force=True, silent=True) or {}
    kind = str(data.get("type", "skill")).lower()
    description = str(data.get("description", "")).strip()
    if kind not in {"tool", "skill"} or not description:
        return jsonify({"error": "generation supports tool or skill and requires a description"}), 400
    if kind == "tool":
        instruction = ("Return only Python source for an Open WebUI Workspace Tool. "
                       "Expose a top-level class Tools with typed public methods and docstrings. "
                       "Do not use shell commands, subprocess, dynamic execution, or embedded secrets.")
    else:
        instruction = ("Return only a reusable Markdown skill. Start with a clear title, then purpose, "
                       "constraints, step-by-step method, validation checks, and failure handling.")
    payload = {"model": data.get("model") or FORGE_MODEL, "stream": False, "think": False,
               "messages": [{"role": "system", "content": instruction},
                            {"role": "user", "content": description}],
               "options": {"temperature": 0.2, "num_predict": 1400, "num_ctx": 8192}}
    try:
        response = _local_model_post(f"{OLLAMA_URL.rstrip('/')}/api/chat", json=payload, timeout=240)
        response.raise_for_status()
        source = response.json().get("message", {}).get("content", "").strip()
        source = re.sub(r"^```(?:python|markdown|md)?\s*|\s*```$", "", source,
                        flags=re.IGNORECASE | re.DOTALL).strip()
    except Exception as error:
        return jsonify({"error": str(error)}), 502
    generated = dict(data, source=source, generator=payload["model"], name=data.get("name") or description[:60])
    with _app.test_request_context(json=generated):
        return forge_create()


@_bp.route('/forge/import/ecc', methods=['POST'])
def forge_import_ecc():
    if not _forge_authorized():
        return jsonify({"error": "ECC import requires FORGE_ADMIN_TOKEN"}), 403
    try:
        artifacts = load_ecc_bundle(ECC_BUNDLE_DIR)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = []
        with _forge_lock:
            for item in artifacts:
                if os.path.exists(_forge_path(item["id"])):
                    results.append({"id": item["id"], "created": False})
                    continue
                item.update(status="draft", version=1, created_at=now, updated_at=now,
                            validation=_forge_validate("skill", item["source"]))
                _forge_write(item)
                results.append({"id": item["id"], "created": True})
        return jsonify({"artifacts": results})
    except (OSError, ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400

# ── la lettura di un artefatto come skill, per la chat ────────────────────

def _forge_read_skill(artifact_id):
    with _forge_lock:
        with open(_forge_path(artifact_id), encoding="utf-8") as stream:
            return json.load(stream)
