# SPDX-License-Identifier: Apache-2.0
# shared/models/__init__.py
# Questo rende 'shared.models' un pacchetto Python importabile.

from .index import Node, Task, Workflow, generate_id

__all__ = ['Node', 'Task', 'Workflow', 'generate_id']
