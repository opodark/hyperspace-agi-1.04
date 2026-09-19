# SPDX-License-Identifier: Apache-2.0
"""Client per la registrazione dinamica dei nodi al HyperSpace Registry."""
import os
import socket
import threading
import time
import logging
import requests

logger = logging.getLogger("registry_client")

REGISTRY_URL = os.getenv("REGISTRY_URL", "http://registry:8086")
NODE_ID = os.getenv("NODE_ID", os.getenv("NODE_HOSTNAME", socket.gethostname()))
PUBLIC_ADDRESS = os.getenv("PUBLIC_ENDPOINT", "")
ROLE = os.getenv("NODE_TIER", "worker")
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "30"))


class RegistryClient:
    def __init__(
        self,
        registry_url: str = REGISTRY_URL,
        node_id: str = NODE_ID,
        public_address: str = PUBLIC_ADDRESS,
        role: str = ROLE,
    ):
        self.registry_url = registry_url.rstrip("/")
        self.node_id = node_id
        self.public_address = public_address
        self.role = role
        self._heartbeat_thread: threading.Thread | None = None
        self._running = False

    def register(self, metadata: dict | None = None) -> dict:
        if not self.public_address:
            raise ValueError("PUBLIC_ENDPOINT non impostato: impossibile registrarsi")
        payload = {
            "node_id": self.node_id,
            "public_address": self.public_address,
            "role": self.role,
            "metadata": metadata or {},
        }
        r = requests.post(f"{self.registry_url}/register", json=payload, timeout=10)
        r.raise_for_status()
        logger.info(f"[registry] Nodo registrato: {self.node_id} @ {self.public_address}")
        return r.json()

    def heartbeat(self) -> dict:
        r = requests.post(
            f"{self.registry_url}/heartbeat",
            params={"node_id": self.node_id},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def deregister(self) -> dict:
        r = requests.delete(
            f"{self.registry_url}/nodes/{self.node_id}", timeout=10
        )
        r.raise_for_status()
        logger.info(f"[registry] Nodo deregistrato: {self.node_id}")
        return r.json()

    def get_nodes(self) -> list:
        r = requests.get(f"{self.registry_url}/nodes", timeout=10)
        r.raise_for_status()
        return r.json()

    def start_heartbeat(self, interval: int = HEARTBEAT_INTERVAL):
        """Avvia un thread in background che manda heartbeat periodici."""
        self._running = True

        def _loop():
            while self._running:
                try:
                    self.heartbeat()
                except Exception as e:
                    logger.warning(f"[registry] Heartbeat fallito: {e}")
                time.sleep(interval)

        self._heartbeat_thread = threading.Thread(target=_loop, daemon=True)
        self._heartbeat_thread.start()
        logger.info(f"[registry] Heartbeat avviato ogni {interval}s")

    def stop_heartbeat(self):
        self._running = False
