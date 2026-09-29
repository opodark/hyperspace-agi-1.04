# SPDX-License-Identifier: Apache-2.0
import threading
import time
import unittest
from unittest import mock

from integrations.comfyui import comfy_bridge
from shared.image_memory_gate import ImageMemoryGate


class ImageMemoryGateTests(unittest.TestCase):
    def test_image_waits_for_existing_chat_and_blocks_new_chat(self):
        gate = ImageMemoryGate()
        self.assertTrue(gate.enter_chat())
        result = []
        worker = threading.Thread(target=lambda: result.append(gate.reserve_image("image-1", 1)))
        worker.start()
        time.sleep(0.02)
        self.assertFalse(gate.enter_chat(timeout=0.01))
        gate.leave_chat()
        worker.join(1)
        self.assertEqual(result, [True])
        self.assertFalse(gate.enter_chat(timeout=0.01))
        gate.release_image("image-1")
        self.assertTrue(gate.enter_chat(timeout=0.1))
        gate.leave_chat()

    def test_bridge_unloads_resident_model_before_checking_memory(self):
        calls = []

        def request(url, *, payload=None, timeout=10):
            calls.append((url, payload))
            if url.endswith("/api/ps"):
                return 200, {"models": [{"name": "qwen:8b"}]}
            if url.endswith("/api/generate"):
                return 200, {}
            return 200, {"devices": [{"vram_free": 6 * 1073741824}]}

        with mock.patch.object(comfy_bridge, "_richiesta", side_effect=request):
            ready, reason = comfy_bridge.prepara_memoria_mac(
                "http://comfy", "http://ollama", timeout_s=1)
        self.assertTrue(ready, reason)
        self.assertEqual(calls[1][1]["keep_alive"], 0)
        self.assertTrue(calls[2][0].endswith("/system_stats"))

    def test_abandoned_image_lease_expires(self):
        now = [100.0]
        gate = ImageMemoryGate(clock=lambda: now[0], lease_s=10)
        self.assertTrue(gate.reserve_image("image-1"))
        now[0] += 11
        self.assertTrue(gate.enter_chat(timeout=0.01))
        gate.leave_chat()


if __name__ == "__main__":
    unittest.main()
