from flask import Flask, request, jsonify, render_template_string
import os
import threading
import time
import requests
import json
import uuid
from datetime import datetime

app = Flask(__name__)

# ----------------------------
# IN-MEMORY STATE
# ----------------------------
tasks = {}
event_logs = []          # unified log store
advanced_config = {
    "security": {
        "sharedSecret": "",
        "secretRotatedAt": None,
    },
    "authority": {
        "serverUrl": "http://authority:8080",
        "enabled": True,
        "authMode": "none",   # none | token | jwt | public-key
    },
    "mesh": {
        "enabled": False,
        "mhtEnabled": False,
        "bootstrapPeers": [],
    }
}

AUTHORITY_URL = advanced_config["authority"]["serverUrl"]
LOG_LIMIT = 500   # keep last N entries in memory


# ----------------------------
# LOG HELPERS
# ----------------------------
LOG_TYPES = {"connection_test", "inter_node_message", "dream", "node_chat", "authority_event", "system"}

def push_log(type_: str, summary: str, detail: str = "",
             source: str = "control-plane", target: str = "",
             status: str = "info", trace_id: str = ""):
    global event_logs
    entry = {
        "id": str(uuid.uuid4()),
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "type": type_ if type_ in LOG_TYPES else "system",
        "sourceNode": source,
        "targetNode": target,
        "status": status,          # success | warning | failed | info | pending
        "traceId": trace_id or str(uuid.uuid4())[:8],
        "summary": summary,
        "detail": detail,
    }
    event_logs.append(entry)
    if len(event_logs) > LOG_LIMIT:
        event_logs = event_logs[-LOG_LIMIT:]
    return entry


# ----------------------------
# LOG API
# ----------------------------
@app.route('/logs', methods=['GET'])
def get_logs():
    type_filter   = request.args.get('type', '')
    status_filter = request.args.get('status', '')
    node_filter   = request.args.get('node', '')
    search        = request.args.get('q', '').lower()
    result = event_logs[:]
    if type_filter:
        result = [l for l in result if l['type'] == type_filter]
    if status_filter:
        result = [l for l in result if l['status'] == status_filter]
    if node_filter:
        result = [l for l in result if
                  node_filter in l['sourceNode'] or node_filter in l['targetNode']]
    if search:
        result = [l for l in result if
                  search in l['summary'].lower() or search in l['detail'].lower()]
    return jsonify(list(reversed(result[-200:])))

@app.route('/logs/add', methods=['POST'])
def add_log():
    data = request.get_json(force=True, silent=True) or {}
    entry = push_log(
        type_=data.get('type', 'system'),
        summary=data.get('summary', ''),
        detail=data.get('detail', ''),
        source=data.get('sourceNode', 'unknown'),
        target=data.get('targetNode', ''),
        status=data.get('status', 'info'),
        trace_id=data.get('traceId', ''),
    )
    return jsonify(entry), 201

@app.route('/logs/clear', methods=['POST'])
def clear_logs():
    global event_logs
    event_logs = []
    return jsonify({"ok": True})


# ----------------------------
# ADVANCED CONFIG API
# ----------------------------
@app.route('/config/advanced', methods=['GET'])
def get_advanced_config():
    safe = json.loads(json.dumps(advanced_config))
    if safe["security"]["sharedSecret"]:
        safe["security"]["sharedSecret"] = "***"   # never expose in GET
    return jsonify(safe)

@app.route('/config/advanced', methods=['POST'])
def set_advanced_config():
    global advanced_config, AUTHORITY_URL
    data = request.get_json(force=True, silent=True) or {}
    sec  = data.get('security', {})
    auth = data.get('authority', {})
    mesh = data.get('mesh', {})

    if 'sharedSecret' in sec and sec['sharedSecret'] != '***':
        advanced_config['security']['sharedSecret'] = sec['sharedSecret']
        advanced_config['security']['secretRotatedAt'] = datetime.utcnow().isoformat()
    if 'serverUrl' in auth:
        advanced_config['authority']['serverUrl'] = auth['serverUrl']
        AUTHORITY_URL = auth['serverUrl']
    if 'enabled' in auth:
        advanced_config['authority']['enabled'] = bool(auth['enabled'])
    if 'authMode' in auth:
        advanced_config['authority']['authMode'] = auth['authMode']
    if 'mhtEnabled' in mesh:
        advanced_config['mesh']['mhtEnabled'] = bool(mesh['mhtEnabled'])
    if 'bootstrapPeers' in mesh:
        advanced_config['mesh']['bootstrapPeers'] = mesh['bootstrapPeers']
    if 'enabled' in mesh:
        advanced_config['mesh']['enabled'] = bool(mesh['enabled'])

    push_log('authority_event', 'Advanced config updated', json.dumps(data, default=str), status='info')
    return jsonify({"ok": True})

@app.route('/config/authority/test', methods=['POST'])
def test_authority():
    url = advanced_config['authority']['serverUrl']
    try:
        r = requests.get(f"{url}/nodes", timeout=3)
        push_log('connection_test', f'Authority reachability OK ({url})',
                 f'HTTP {r.status_code}', target='authority', status='success')
        return jsonify({"ok": True, "status": r.status_code})
    except Exception as e:
        push_log('connection_test', f'Authority unreachable ({url})',
                 str(e), target='authority', status='failed')
        return jsonify({"ok": False, "error": str(e)}), 503

@app.route('/config/secret/rotate', methods=['POST'])
def rotate_secret():
    new_secret = str(uuid.uuid4()).replace('-', '')
    advanced_config['security']['sharedSecret'] = new_secret
    advanced_config['security']['secretRotatedAt'] = datetime.utcnow().isoformat()
    push_log('authority_event', 'Shared secret rotated', status='success')
    return jsonify({"ok": True, "secret": new_secret,
                    "rotatedAt": advanced_config['security']['secretRotatedAt']})


# ----------------------------
# TASK CREATE
# ----------------------------
@app.route('/task/create', methods=['POST'])
def create_task():
    task_id = request.form.get('task_id')
    if not task_id:
        return jsonify({"error": "Missing task_id"}), 400
    tasks[task_id] = {"id": task_id, "status": "created", "worker": None, "payload": {}}
    push_log('system', f'Task created: {task_id}', status='info')
    return jsonify({"message": "Task created", "task_id": task_id}), 201


# ----------------------------
# TASK ASSIGN + EXECUTE
# ----------------------------
@app.route('/task/assign', methods=['POST'])
def assign_task():
    task_id = request.form.get('task_id')
    if not task_id:
        return jsonify({"error": "Missing task_id"}), 400
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404

    try:
        res = requests.get(f"{AUTHORITY_URL}/nodes")
        nodes = res.json()
    except Exception as e:
        push_log('connection_test', 'Authority call failed during task assign',
                 str(e), target='authority', status='failed')
        return jsonify({"error": f"authority error: {str(e)}"}), 500

    node_list = nodes.values() if isinstance(nodes, dict) else nodes
    active_nodes = [n for n in node_list if n.get("status") == "active"]

    if not active_nodes:
        return jsonify({"error": "No active nodes"}), 404

    selected = active_nodes[0]
    worker_id = selected["node_id"]
    task = tasks[task_id]
    task["status"] = "assigned"
    task["worker"] = worker_id

    tid = str(uuid.uuid4())[:8]
    push_log('inter_node_message', f'Task {task_id} dispatched to {worker_id}',
             json.dumps(task), source='control-plane', target=worker_id,
             status='pending', trace_id=tid)
    try:
        worker_url = f"http://{worker_id}:8084/execute"
        worker_response = requests.post(worker_url, json=task, timeout=5)
        task["result"] = worker_response.json()
        push_log('inter_node_message', f'Task {task_id} executed on {worker_id}',
                 json.dumps(task.get("result", {})),
                 source=worker_id, target='control-plane',
                 status='success', trace_id=tid)
    except Exception as e:
        push_log('inter_node_message', f'Execution failed on {worker_id}',
                 str(e), source=worker_id, target='control-plane',
                 status='failed', trace_id=tid)
        return jsonify({"error": f"execution failed: {str(e)}"}), 500

    return jsonify({"message": "Task assigned and executed", "task": task})


# ----------------------------
# TASKS LIST
# ----------------------------
@app.route('/tasks')
def get_tasks():
    return jsonify(tasks)


# ----------------------------
# DASHBOARD HTML
# ----------------------------
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="it" data-theme="dark">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>HyperSpace AGI v1.01 — Control Plane</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root,[data-theme="dark"]{
  --bg:#0d0e10;--surface:#141619;--surface2:#1b1d21;--surface3:#22252a;
  --border:#2a2d33;--divider:#1e2126;
  --text:#c9cdd4;--text-muted:#6b717d;--text-faint:#3d4149;
  --primary:#4f98a3;--primary-h:#2d7d8a;--primary-bg:#1a3035;
  --success:#6daa45;--success-bg:#1e3020;
  --warning:#e8af34;--warning-bg:#2e2510;
  --error:#dd6974;--error-bg:#2e1520;
  --info:#5591c7;--info-bg:#162133;
  --dream:#a86fdf;--dream-bg:#271840;
  --chat:#fdab43;--chat-bg:#2a1e08;
  --font-mono:'JetBrains Mono',monospace;
  --font-body:'Inter',sans-serif;
  --radius:6px;--radius-lg:10px;
  --transition:160ms cubic-bezier(0.16,1,0.3,1);
}
[data-theme="light"]{
  --bg:#f4f5f7;--surface:#fff;--surface2:#f8f9fb;--surface3:#edeef1;
  --border:#dde0e6;--divider:#e8eaed;
  --text:#1a1d22;--text-muted:#6b717d;--text-faint:#b0b5be;
  --primary:#016970;--primary-h:#014f55;--primary-bg:#d6eff1;
  --success:#3a6e1a;--success-bg:#d8f0cb;
  --warning:#a07000;--warning-bg:#fdf3d0;
  --error:#a12c3a;--error-bg:#fbd8db;
  --info:#225f99;--info-bg:#d0e4f7;
  --dream:#6b30b5;--dream-bg:#ebe0fb;
  --chat:#b56200;--chat-bg:#fdecd0;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:14px;-webkit-font-smoothing:antialiased}
body{font-family:var(--font-body);background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column}
header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 20px;display:flex;align-items:center;gap:16px;height:52px;position:sticky;top:0;z-index:100}
.logo{display:flex;align-items:center;gap:10px;font-family:var(--font-mono);font-weight:700;font-size:.95rem;color:var(--primary)}
.version-badge{font-size:.6rem;font-weight:700;background:var(--primary-bg);color:var(--primary);padding:2px 6px;border-radius:99px;letter-spacing:.05em}
nav{display:flex;gap:2px;margin-left:16px}
nav button{background:none;border:none;cursor:pointer;padding:6px 14px;border-radius:var(--radius);font-size:.82rem;font-weight:500;color:var(--text-muted);transition:color var(--transition),background var(--transition);font-family:var(--font-body)}
nav button.active,nav button:hover{color:var(--text);background:var(--surface3)}
nav button.active{color:var(--primary)}
.ml-auto{margin-left:auto}
#themeToggle{background:none;border:1px solid var(--border);cursor:pointer;padding:6px 8px;border-radius:var(--radius);color:var(--text-muted);transition:color var(--transition),border-color var(--transition)}
#themeToggle:hover{color:var(--text);border-color:var(--text-muted)}
main{flex:1;padding:20px;max-width:1400px;width:100%;margin:0 auto}
.panel{display:none}
.panel.active{display:flex;flex-direction:column;gap:16px}
.section-title{font-size:.7rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--text-muted);padding:0 0 8px;border-bottom:1px solid var(--divider)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px}
.card-title{font-size:.8rem;font-weight:600;color:var(--text-muted);margin-bottom:12px;display:flex;align-items:center;gap:8px}
.task-row{display:flex;gap:8px}
.task-row input{flex:1;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 12px;color:var(--text);font-family:var(--font-mono);font-size:.82rem}
.task-row input:focus{outline:none;border-color:var(--primary)}
.btn{border:none;cursor:pointer;padding:8px 16px;border-radius:var(--radius);font-size:.82rem;font-weight:500;transition:background var(--transition),color var(--transition);font-family:var(--font-body)}
.btn-primary{background:var(--primary);color:#fff}.btn-primary:hover{background:var(--primary-h)}
.btn-ghost{background:var(--surface3);color:var(--text)}.btn-ghost:hover{background:var(--border)}
.btn-danger{background:var(--error-bg);color:var(--error)}.btn-danger:hover{background:var(--error);color:#fff}
.btn-success{background:var(--success-bg);color:var(--success)}.btn-success:hover{background:var(--success);color:#fff}
.btn-warn{background:var(--warning-bg);color:var(--warning)}.btn-warn:hover{background:var(--warning);color:#000}
.btn-sm{padding:5px 10px;font-size:.75rem}
.log-tabs{display:flex;gap:4px;flex-wrap:wrap}
.log-tab{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:5px 12px;cursor:pointer;font-size:.75rem;font-weight:600;color:var(--text-muted);transition:all var(--transition);font-family:var(--font-body)}
.log-tab:hover,.log-tab.active{background:var(--surface3);color:var(--text);border-color:var(--text-muted)}
.log-tab.active.type-all{border-color:var(--primary);color:var(--primary)}
.log-tab.active.type-connection_test{border-color:var(--success);color:var(--success)}
.log-tab.active.type-inter_node_message{border-color:var(--info);color:var(--info)}
.log-tab.active.type-dream{border-color:var(--dream);color:var(--dream)}
.log-tab.active.type-node_chat{border-color:var(--chat);color:var(--chat)}
.log-tab.active.type-authority_event{border-color:var(--warning);color:var(--warning)}
.filter-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.filter-row input,.filter-row select{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:5px 10px;color:var(--text);font-size:.78rem;font-family:var(--font-body)}
.filter-row input:focus,.filter-row select:focus{outline:none;border-color:var(--primary)}
.log-stream{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden;font-family:var(--font-mono)}
.log-header-row{display:grid;grid-template-columns:130px 90px 110px 100px 80px 1fr;gap:0;font-size:.68rem;font-weight:700;color:var(--text-muted);letter-spacing:.05em;text-transform:uppercase;padding:8px 12px;border-bottom:1px solid var(--divider);background:var(--surface2)}
.log-entry{display:grid;grid-template-columns:130px 90px 110px 100px 80px 1fr;gap:0;padding:7px 12px;border-bottom:1px solid var(--divider);cursor:pointer;transition:background var(--transition);font-size:.75rem;align-items:start}
.log-entry:last-child{border-bottom:none}
.log-entry:hover{background:var(--surface3)}
.log-entry .ts{color:var(--text-muted);font-size:.68rem}
.log-entry .type-badge{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border-radius:99px;font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.tb-connection_test{background:var(--success-bg);color:var(--success)}
.tb-inter_node_message{background:var(--info-bg);color:var(--info)}
.tb-dream{background:var(--dream-bg);color:var(--dream)}
.tb-node_chat{background:var(--chat-bg);color:var(--chat)}
.tb-authority_event{background:var(--warning-bg);color:var(--warning)}
.tb-system{background:var(--surface3);color:var(--text-muted)}
.st-badge{display:inline-flex;padding:2px 7px;border-radius:99px;font-size:.65rem;font-weight:700;text-transform:uppercase}
.st-success{background:var(--success-bg);color:var(--success)}
.st-failed{background:var(--error-bg);color:var(--error)}
.st-warning{background:var(--warning-bg);color:var(--warning)}
.st-pending{background:var(--info-bg);color:var(--info)}
.st-info{background:var(--surface3);color:var(--text-muted)}
.log-entry .summary{color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log-entry .node-col{color:var(--text-muted);font-size:.7rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log-detail-row{display:none;padding:8px 12px 12px;background:var(--surface2);border-top:1px solid var(--divider);font-size:.72rem;color:var(--text-muted);white-space:pre-wrap;word-break:break-all;font-family:var(--font-mono)}
.log-detail-row.open{display:block}
.log-empty{padding:40px;text-align:center;color:var(--text-faint);font-size:.8rem}
.log-status-bar{display:flex;align-items:center;gap:12px;padding:8px 12px;background:var(--surface2);border-top:1px solid var(--divider);font-size:.72rem;color:var(--text-muted)}
.pulse{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--success);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.diag-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.diag-result{margin-top:10px;padding:10px;border-radius:var(--radius);background:var(--surface2);font-family:var(--font-mono);font-size:.72rem;color:var(--text-muted);white-space:pre-wrap;min-height:48px;border:1px solid var(--divider)}
.setup-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:700px){.setup-grid{grid-template-columns:1fr}}
.form-group{display:flex;flex-direction:column;gap:6px}
.form-label{font-size:.72rem;font-weight:600;color:var(--text-muted);letter-spacing:.04em;text-transform:uppercase}
.form-input{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:9px 12px;color:var(--text);font-size:.82rem;font-family:var(--font-body);transition:border-color var(--transition)}
.form-input:focus{outline:none;border-color:var(--primary)}
.form-select{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:9px 12px;color:var(--text);font-size:.82rem;font-family:var(--font-body)}
.form-hint{font-size:.68rem;color:var(--text-muted)}
.secret-row{display:flex;gap:8px;align-items:center}
.secret-row .form-input{flex:1;font-family:var(--font-mono);letter-spacing:.08em}
.mode-toggle{display:flex;gap:8px}
.mode-btn{flex:1;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:10px;cursor:pointer;text-align:center;font-size:.78rem;font-weight:600;color:var(--text-muted);transition:all var(--transition)}
.mode-btn.active{background:var(--primary-bg);border-color:var(--primary);color:var(--primary)}
.setup-actions{display:flex;gap:8px;justify-content:flex-end;padding-top:8px;border-top:1px solid var(--divider)}
.badge-pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:99px;font-size:.65rem;font-weight:700;background:var(--surface3);color:var(--text-muted)}
.tag-mht{background:var(--dream-bg);color:var(--dream)}
.mesh-section{opacity:.4;pointer-events:none;transition:opacity .3s}
.mesh-section.enabled{opacity:1;pointer-events:all}
#taskOut{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;font-family:var(--font-mono);font-size:.75rem;color:var(--text);white-space:pre-wrap;max-height:400px;overflow-y:auto;min-height:80px}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--surface)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>
<header>
  <div class="logo">
    <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-label="HyperSpace AGI">
      <polygon points="14,2 26,9 26,19 14,26 2,19 2,9" stroke="currentColor" stroke-width="1.5" fill="none"/>
      <circle cx="14" cy="14" r="4" fill="currentColor" opacity=".7"/>
      <line x1="14" y1="2" x2="14" y2="10" stroke="currentColor" stroke-width="1"/>
      <line x1="26" y1="9" x2="18" y2="13" stroke="currentColor" stroke-width="1"/>
      <line x1="26" y1="19" x2="18" y2="15" stroke="currentColor" stroke-width="1"/>
      <line x1="14" y1="26" x2="14" y2="18" stroke="currentColor" stroke-width="1"/>
      <line x1="2" y1="19" x2="10" y2="15" stroke="currentColor" stroke-width="1"/>
      <line x1="2" y1="9" x2="10" y2="13" stroke="currentColor" stroke-width="1"/>
    </svg>
    HyperSpace AGI <span class="version-badge">v1.01</span>
  </div>
  <nav>
    <button class="active" onclick="showPanel('tasks',this)">Tasks</button>
    <button onclick="showPanel('logs',this)">Log Viewer</button>
    <button onclick="showPanel('diag',this)">Diagnostics</button>
    <button onclick="showPanel('setup',this)">Advanced Setup</button>
  </nav>
  <div class="ml-auto" style="display:flex;align-items:center;gap:10px">
    <span id="clockBadge" style="font-family:var(--font-mono);font-size:.72rem;color:var(--text-muted)"></span>
    <button id="themeToggle" data-theme-toggle aria-label="Toggle theme">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
      </svg>
    </button>
  </div>
</header>

<main>
  <!-- TASKS -->
  <div id="panel-tasks" class="panel active">
    <div class="section-title">Task Management</div>
    <div class="card">
      <div class="task-row">
        <input id="task_id" placeholder="task_id (es. task-001)"/>
        <button class="btn btn-primary" onclick="createTask()">Create</button>
        <button class="btn btn-ghost" onclick="assignTask()">Assign &amp; Execute</button>
      </div>
    </div>
    <div id="taskOut">{}</div>
  </div>

  <!-- LOG VIEWER -->
  <div id="panel-logs" class="panel">
    <div class="section-title">Log Viewer</div>
    <div class="log-tabs">
      <button class="log-tab active type-all" onclick="setLogTab('',this)">All</button>
      <button class="log-tab type-connection_test" onclick="setLogTab('connection_test',this)">🔌 Connection Tests</button>
      <button class="log-tab type-inter_node_message" onclick="setLogTab('inter_node_message',this)">📡 Node Communication</button>
      <button class="log-tab type-dream" onclick="setLogTab('dream',this)">💭 Dreams / Autonomous</button>
      <button class="log-tab type-node_chat" onclick="setLogTab('node_chat',this)">💬 Node Chats</button>
      <button class="log-tab type-authority_event" onclick="setLogTab('authority_event',this)">🔑 Authority Events</button>
    </div>
    <div class="filter-row">
      <input id="fNode" placeholder="Filter by node…" oninput="refreshLogs()" style="width:180px"/>
      <select id="fStatus" onchange="refreshLogs()">
        <option value="">All statuses</option>
        <option value="success">Success</option>
        <option value="failed">Failed</option>
        <option value="warning">Warning</option>
        <option value="pending">Pending</option>
        <option value="info">Info</option>
      </select>
      <input id="fSearch" placeholder="🔍 Full-text search…" oninput="refreshLogs()" style="flex:1;min-width:180px"/>
      <button class="btn btn-danger btn-sm" onclick="clearLogs()">Clear</button>
      <button class="btn btn-ghost btn-sm" onclick="injectSampleLogs()">Inject samples</button>
    </div>
    <div class="log-stream">
      <div class="log-header-row">
        <span>Timestamp</span><span>Type</span><span>Source</span><span>Target</span><span>Status</span><span>Summary</span>
      </div>
      <div id="logBody"><div class="log-empty">No events yet — logs will appear here in real time.</div></div>
      <div class="log-status-bar">
        <span class="pulse"></span>
        <span id="logCount">0 events</span>
        <span style="margin-left:auto" id="logLastUpdate">—</span>
      </div>
    </div>
  </div>

  <!-- DIAGNOSTICS -->
  <div id="panel-diag" class="panel">
    <div class="section-title">Node Diagnostics</div>
    <div class="diag-grid">
      <div class="card">
        <div class="card-title">🔌 Authority Reachability Test</div>
        <p style="font-size:.75rem;color:var(--text-muted);margin-bottom:12px">Verifica la connessione verso l'authority server configurato.</p>
        <button class="btn btn-primary btn-sm" onclick="testAuthority()">Run Test</button>
        <div class="diag-result" id="diagAuth">—</div>
      </div>
      <div class="card">
        <div class="card-title">📡 Node List</div>
        <p style="font-size:.75rem;color:var(--text-muted);margin-bottom:12px">Lista nodi attivi registrati sull'authority.</p>
        <button class="btn btn-ghost btn-sm" onclick="listNodes()">Refresh Nodes</button>
        <div class="diag-result" id="diagNodes">—</div>
      </div>
      <div class="card">
        <div class="card-title">💭 Simulate Dream Event</div>
        <p style="font-size:.75rem;color:var(--text-muted);margin-bottom:12px">Inietta un evento dream/autonomous task nel log stream.</p>
        <div style="display:flex;gap:8px;margin-bottom:8px">
          <input id="dreamNode" placeholder="node-id" class="form-input" style="flex:1"/>
          <input id="dreamSummary" placeholder="Dream description…" class="form-input" style="flex:2"/>
        </div>
        <button class="btn btn-sm" style="background:var(--dream-bg);color:var(--dream)" onclick="sendDream()">Send Dream Log</button>
        <div class="diag-result" id="diagDream">—</div>
      </div>
      <div class="card">
        <div class="card-title">💬 Simulate Node Chat</div>
        <p style="font-size:.75rem;color:var(--text-muted);margin-bottom:12px">Simula un messaggio di chat tra nodi.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <input id="chatFrom" placeholder="from-node" class="form-input" style="flex:1;min-width:100px"/>
          <input id="chatTo" placeholder="to-node" class="form-input" style="flex:1;min-width:100px"/>
          <input id="chatMsg" placeholder="Message…" class="form-input" style="flex:2;min-width:180px"/>
        </div>
        <button class="btn btn-sm" style="background:var(--chat-bg);color:var(--chat)" onclick="sendChat()">Send Chat Log</button>
        <div class="diag-result" id="diagChat">—</div>
      </div>
    </div>
  </div>

  <!-- ADVANCED SETUP -->
  <div id="panel-setup" class="panel">
    <div class="section-title">Advanced Setup</div>
    <div class="card">
      <div class="card-title">🔑 Security — Shared Secret <span id="secretRotatedAt" class="badge-pill" style="margin-left:auto"></span></div>
      <div class="setup-grid">
        <div class="form-group" style="grid-column:span 2">
          <label class="form-label">Shared Secret</label>
          <div class="secret-row">
            <input id="secretInput" type="password" class="form-input" placeholder="Leave blank to keep current"/>
            <button class="btn btn-ghost btn-sm" onclick="toggleSecret()">Show</button>
            <button class="btn btn-warn btn-sm" onclick="rotateSecret()">⟳ Rotate</button>
          </div>
          <span class="form-hint">Il secret autentica i nodi sulla rete. Ruotalo periodicamente.</span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">🏛️ Authority Server</div>
      <div class="setup-grid">
        <div class="form-group">
          <label class="form-label">Server URL</label>
          <input id="authUrl" class="form-input" placeholder="http://authority:8080"/>
          <span class="form-hint">Endpoint REST dell'authority server.</span>
        </div>
        <div class="form-group">
          <label class="form-label">Auth Mode</label>
          <select id="authMode" class="form-select">
            <option value="none">None (dev)</option>
            <option value="token">Token</option>
            <option value="jwt">JWT</option>
            <option value="public-key">Public Key</option>
          </select>
          <span class="form-hint">Modalità di autenticazione verso l'authority.</span>
        </div>
        <div class="form-group">
          <label class="form-label">Enabled</label>
          <div class="mode-toggle" style="max-width:240px">
            <button class="mode-btn active" id="authEnabledOn" onclick="setAuthEnabled(true)">Enabled</button>
            <button class="mode-btn" id="authEnabledOff" onclick="setAuthEnabled(false)">Disabled</button>
          </div>
        </div>
        <div class="form-group" style="align-self:end">
          <button class="btn btn-ghost btn-sm" onclick="testAuthoritySetup()">🔌 Test Connection</button>
          <div class="diag-result" id="setupAuthTest" style="margin-top:8px;min-height:36px"></div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">🌐 Network Mode <span class="badge-pill tag-mht" style="margin-left:8px">MHT — coming soon</span></div>
      <div class="setup-grid">
        <div class="form-group" style="grid-column:span 2">
          <label class="form-label">Operating Mode</label>
          <div class="mode-toggle" style="max-width:360px">
            <button class="mode-btn active" id="modeAuthority" onclick="setNetworkMode('authority')">Authority-managed</button>
            <button class="mode-btn" id="modeMesh" onclick="setNetworkMode('mesh')">Pure Mesh (MHT)</button>
          </div>
          <span class="form-hint">Pure Mesh attiva il coordinamento P2P con Modular Hash Tree (MHT). Richiede bootstrap peers.</span>
        </div>
        <div class="form-group mesh-section" id="meshSection">
          <label class="form-label">Bootstrap Peers</label>
          <textarea id="meshPeers" class="form-input" rows="3" placeholder="node-alpha:9000&#10;node-beta:9000"></textarea>
          <span class="form-hint">Un peer per riga nel formato host:port.</span>
        </div>
        <div class="form-group mesh-section" id="meshMhtSection">
          <label class="form-label">MHT Enabled</label>
          <div class="mode-toggle" style="max-width:240px">
            <button class="mode-btn" id="mhtOn" onclick="setMht(true)">Enabled</button>
            <button class="mode-btn active" id="mhtOff" onclick="setMht(false)">Disabled</button>
          </div>
          <span class="form-hint">Abilita il Modular Hash Tree per mesh routing avanzato.</span>
        </div>
      </div>
    </div>
    <div class="setup-actions">
      <button class="btn btn-ghost" onclick="loadAdvancedConfig()">↺ Reset</button>
      <button class="btn btn-primary" onclick="saveAdvancedConfig()">💾 Save Configuration</button>
    </div>
    <div id="setupSaveMsg" style="font-size:.75rem;color:var(--success);text-align:right;min-height:18px"></div>
  </div>
</main>

<script>
(function(){
  const r=document.documentElement;
  const t=document.getElementById('themeToggle');
  let d='dark';
  r.setAttribute('data-theme',d);
  const icons={dark:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
               light:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'};
  t.innerHTML=icons[d];
  t.addEventListener('click',()=>{d=d==='dark'?'light':'dark';r.setAttribute('data-theme',d);t.innerHTML=icons[d];});
})();

function updateClock(){document.getElementById('clockBadge').textContent=new Date().toISOString().replace('T',' ').substring(0,19)+' UTC';}
setInterval(updateClock,1000);updateClock();

function showPanel(name,btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
  btn.classList.add('active');
  if(name==='logs') refreshLogs();
  if(name==='setup') loadAdvancedConfig();
}

async function createTask(){
  const id=document.getElementById('task_id').value.trim();
  if(!id)return;
  await fetch('/task/create',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`task_id=${id}`});
  refreshTasks();
}
async function assignTask(){
  const id=document.getElementById('task_id').value.trim();
  if(!id)return;
  await fetch('/task/assign',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`task_id=${id}`});
  refreshTasks();
}
async function refreshTasks(){
  const r=await fetch('/tasks');
  const d=await r.json();
  document.getElementById('taskOut').textContent=JSON.stringify(d,null,2);
}
setInterval(refreshTasks,3000);refreshTasks();

let currentLogType='';
const statusEmoji={success:'✅',failed:'❌',warning:'⚠️',pending:'⏳',info:'ℹ️'};

function setLogTab(type,btn){
  currentLogType=type;
  document.querySelectorAll('.log-tab').forEach(t=>t.classList.remove('active'));
  btn.classList.add('active');
  refreshLogs();
}

async function refreshLogs(){
  const node=document.getElementById('fNode').value.trim();
  const status=document.getElementById('fStatus').value;
  const q=document.getElementById('fSearch').value.trim();
  let url='/logs?';
  if(currentLogType) url+=`type=${currentLogType}&`;
  if(node) url+=`node=${encodeURIComponent(node)}&`;
  if(status) url+=`status=${status}&`;
  if(q) url+=`q=${encodeURIComponent(q)}&`;
  const r=await fetch(url);
  const logs=await r.json();
  renderLogs(logs);
}

function renderLogs(logs){
  const body=document.getElementById('logBody');
  document.getElementById('logCount').textContent=logs.length+' events';
  document.getElementById('logLastUpdate').textContent='Updated '+new Date().toISOString().substring(11,19)+' UTC';
  if(!logs.length){body.innerHTML='<div class="log-empty">No events match current filters.</div>';return;}
  body.innerHTML=logs.map((l,i)=>`
    <div class="log-entry" onclick="toggleDetail('d${i}')">
      <span class="ts">${l.ts.replace('T',' ').substring(0,19)}</span>
      <span><span class="type-badge tb-${l.type}">${l.type.replace(/_/g,' ')}</span></span>
      <span class="node-col">${l.sourceNode||'—'}</span>
      <span class="node-col">${l.targetNode||'—'}</span>
      <span><span class="st-badge st-${l.status}">${statusEmoji[l.status]||''} ${l.status}</span></span>
      <span class="summary">${escHtml(l.summary)}</span>
    </div>
    <div class="log-detail-row" id="d${i}">
      <b>TraceID:</b> ${l.traceId} &nbsp;|&nbsp; <b>Detail:</b>\n${escHtml(l.detail||'—')}
    </div>
  `).join('');
}

function toggleDetail(id){const el=document.getElementById(id);if(el)el.classList.toggle('open');}
function escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

async function clearLogs(){
  await fetch('/logs/clear',{method:'POST'});
  refreshLogs();
}

async function injectSampleLogs(){
  const samples=[
    {type:'connection_test',summary:'Handshake OK with node-alpha',detail:'latency: 12ms',sourceNode:'control-plane',targetNode:'node-alpha',status:'success'},
    {type:'connection_test',summary:'Handshake FAILED with node-gamma',detail:'connection refused',sourceNode:'control-plane',targetNode:'node-gamma',status:'failed'},
    {type:'inter_node_message',summary:'Task task-007 dispatched',detail:'{"task":"summarize","model":"phi3"}',sourceNode:'control-plane',targetNode:'node-beta',status:'pending'},
    {type:'inter_node_message',summary:'Result received from node-beta',detail:'{"result":"ok","tokens":512}',sourceNode:'node-beta',targetNode:'control-plane',status:'success'},
    {type:'dream',summary:'node-alpha started autonomous planning cycle',detail:'step1: explore tools\nstep2: draft plan\nstep3: execute',sourceNode:'node-alpha',status:'info'},
    {type:'dream',summary:'node-beta completed dream cycle #14',detail:'duration: 4.2s\nresult: memory updated',sourceNode:'node-beta',status:'success'},
    {type:'node_chat',summary:'node-alpha → node-beta: "can you handle summarize tasks?"',detail:'context: task-negotiation',sourceNode:'node-alpha',targetNode:'node-beta',status:'info'},
    {type:'node_chat',summary:'node-beta → node-alpha: "yes, queue has 2 slots"',detail:'',sourceNode:'node-beta',targetNode:'node-alpha',status:'info'},
    {type:'authority_event',summary:'Shared secret rotated',detail:'rotatedAt: '+new Date().toISOString(),sourceNode:'control-plane',status:'success'},
  ];
  for(const s of samples){
    await fetch('/logs/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(s)});
  }
  refreshLogs();
}

setInterval(refreshLogs,4000);

async function testAuthority(){
  document.getElementById('diagAuth').textContent='Testing…';
  const r=await fetch('/config/authority/test',{method:'POST'});
  const d=await r.json();
  document.getElementById('diagAuth').textContent=JSON.stringify(d,null,2);
}
async function listNodes(){
  document.getElementById('diagNodes').textContent='Loading…';
  try{
    const cfg=await(await fetch('/config/advanced')).json();
    const nr=await fetch(cfg.authority.serverUrl+'/nodes');
    document.getElementById('diagNodes').textContent=JSON.stringify(await nr.json(),null,2);
  }catch(e){document.getElementById('diagNodes').textContent='Error: '+e.message;}
}
async function sendDream(){
  const node=document.getElementById('dreamNode').value.trim()||'node-unknown';
  const sum=document.getElementById('dreamSummary').value.trim()||'Autonomous task started';
  const r=await fetch('/logs/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'dream',summary:sum,sourceNode:node,status:'info',detail:'Injected via Diagnostics panel'})});
  document.getElementById('diagDream').textContent=JSON.stringify(await r.json(),null,2);
  if(document.getElementById('panel-logs').classList.contains('active')) refreshLogs();
}
async function sendChat(){
  const from=document.getElementById('chatFrom').value.trim()||'node-a';
  const to=document.getElementById('chatTo').value.trim()||'node-b';
  const msg=document.getElementById('chatMsg').value.trim()||'hello';
  const r=await fetch('/logs/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'node_chat',summary:`${from} → ${to}: "${msg}"`,sourceNode:from,targetNode:to,status:'info',detail:''})});
  document.getElementById('diagChat').textContent=JSON.stringify(await r.json(),null,2);
  if(document.getElementById('panel-logs').classList.contains('active')) refreshLogs();
}

let authEnabled=true,networkMode='authority',mhtEnabled=false;

async function loadAdvancedConfig(){
  const r=await fetch('/config/advanced');
  const c=await r.json();
  document.getElementById('authUrl').value=c.authority.serverUrl||'';
  document.getElementById('authMode').value=c.authority.authMode||'none';
  authEnabled=c.authority.enabled;
  setAuthEnabled(authEnabled);
  const rs=c.security.secretRotatedAt;
  document.getElementById('secretRotatedAt').textContent=rs?'rotated '+rs.substring(0,10):'never rotated';
  mhtEnabled=c.mesh.mhtEnabled;
  setMht(mhtEnabled);
  document.getElementById('meshPeers').value=(c.mesh.bootstrapPeers||[]).join('\n');
  if(c.mesh.enabled) setNetworkMode('mesh'); else setNetworkMode('authority');
}

function setAuthEnabled(val){
  authEnabled=val;
  document.getElementById('authEnabledOn').classList.toggle('active',val);
  document.getElementById('authEnabledOff').classList.toggle('active',!val);
}
function setNetworkMode(mode){
  networkMode=mode;
  document.getElementById('modeAuthority').classList.toggle('active',mode==='authority');
  document.getElementById('modeMesh').classList.toggle('active',mode==='mesh');
  document.getElementById('meshSection').classList.toggle('enabled',mode==='mesh');
  document.getElementById('meshMhtSection').classList.toggle('enabled',mode==='mesh');
}
function setMht(val){
  mhtEnabled=val;
  document.getElementById('mhtOn').classList.toggle('active',val);
  document.getElementById('mhtOff').classList.toggle('active',!val);
}
function toggleSecret(){
  const inp=document.getElementById('secretInput');
  inp.type=inp.type==='password'?'text':'password';
}
async function rotateSecret(){
  const r=await fetch('/config/secret/rotate',{method:'POST'});
  const d=await r.json();
  if(d.ok){
    document.getElementById('secretInput').value=d.secret;
    document.getElementById('secretInput').type='text';
    document.getElementById('secretRotatedAt').textContent='rotated '+d.rotatedAt.substring(0,10);
    showSaveMsg('Secret ruotato: '+d.secret);
  }
}
async function saveAdvancedConfig(){
  const peers=document.getElementById('meshPeers').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const payload={
    security:{sharedSecret:document.getElementById('secretInput').value},
    authority:{serverUrl:document.getElementById('authUrl').value,authMode:document.getElementById('authMode').value,enabled:authEnabled},
    mesh:{enabled:networkMode==='mesh',mhtEnabled:mhtEnabled,bootstrapPeers:peers}
  };
  const r=await fetch('/config/advanced',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const d=await r.json();
  if(d.ok) showSaveMsg('Configurazione salvata ✓');
}
async function testAuthoritySetup(){
  document.getElementById('setupAuthTest').textContent='Testing…';
  const r=await fetch('/config/authority/test',{method:'POST'});
  const d=await r.json();
  document.getElementById('setupAuthTest').textContent=JSON.stringify(d,null,2);
}
function showSaveMsg(msg){
  const el=document.getElementById('setupSaveMsg');
  el.textContent=msg;
  setTimeout(()=>{el.textContent='';},4000);
}
</script>
</body>
</html>"""


@app.route('/dashboard')
def dashboard():
    return DASHBOARD_HTML


# ----------------------------
# HEARTBEAT
# ----------------------------
def heartbeat_loop():
    time.sleep(2)
    push_log('system', 'Control-plane v1.01 started', status='info')
    while True:
        try:
            pass
        except:
            pass
        time.sleep(5)


# ----------------------------
# MAIN
# ----------------------------
def main():
    print("Control-plane v1.01 starting...")
    hb = threading.Thread(target=heartbeat_loop, daemon=True)
    hb.start()
    app.run(host="0.0.0.0", port=8085)


if __name__ == "__main__":
    main()
