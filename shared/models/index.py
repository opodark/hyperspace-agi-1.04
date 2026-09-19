# SPDX-License-Identifier: Apache-2.0
# shared/models/index.py
from typing import Dict, Any, List
from datetime import datetime
import uuid

def generate_id() -> str:
    return str(uuid.uuid4())

# --- NODE MODEL ---
class Node:
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('node_id', generate_id())
        self.hostname = data.get('hostname', 'unknown')
        self.model = data.get('model', 'generic')
        self.role = data.get('role', 'auto')
        self.status = data.get('status', 'online')
        self.last_seen = data.get('last_seen', datetime.utcnow().isoformat())
        self.capacity = {
            'cpu': data.get('capacity', {}).get('cpu', 1),
            'memory_gb': data.get('memory', {}).get('memory_gb', 4)
        }
        self.load = {
            'cpu': data.get('load', {}).get('cpu', 0),
            'memory_gb': data.get('load', {}).get('memory_gb', 0)
        }

    def is_healthy(self) -> bool:
        return self.status == 'online'

    def get_available_cpu(self) -> float:
        return self.capacity['cpu'] - self.load['cpu']

    def get_available_memory(self) -> float:
        return self.capacity['memory_gb'] - self.load['memory_gb']

    def set_status(self, new_status: str):
        self.status = new_status
        self.last_seen = datetime.utcnow().isoformat()

    def update_load(self, cpu: float, memory_gb: float):
        self.load['cpu'] = cpu
        self.load['memory_gb'] = memory_gb
        self.last_seen = datetime.utcnow().isoformat()

# --- TASK MODEL ---
class Task:
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id', generate_id())
        self.title = data.get('title', 'Untitled Task')
        self.status = data.get('status', 'queued')
        self.assigned_to = data.get('assigned_to', None)
        self.result = data.get('result', None)
        self.entity = data.get('entity', 'unknown')
        self.timestamp = data.get('timestamp', datetime.utcnow().isoformat())

    def is_queued(self) -> bool:
        return self.status == 'queued'

    def is_running(self) -> bool:
        return self.status == 'running'

    def is_done(self) -> bool:
        return self.status in ['done', 'failed']

    def set_status(self, new_status: str):
        self.status = new_status
        self.timestamp = datetime.utcnow().isoformat()

# --- WORKFLOW MODEL ---
class Workflow:
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id', generate_id())
        self.name = data.get('name', 'Unnamed Workflow')
        self.status = data.get('status', 'queued')
        self.tasks = data.get('tasks', [])
        self.entity = data.get('entity', 'unknown')
        self.timestamp = data.get('timestamp', datetime.utcnow().isoformat())

    def is_queued(self) -> bool:
        return self.status == 'queued'

    def is_running(self) -> bool:
        return self.status == 'running'

    def is_completed(self) -> bool:
        return self.status in ['completed', 'failed']

    def set_status(self, new_status: str):
        self.status = new_status
        self.timestamp = datetime.utcnow().isoformat()
