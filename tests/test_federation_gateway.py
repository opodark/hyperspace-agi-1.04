# SPDX-License-Identifier: Apache-2.0
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from shared.network_security import verify_client_ip


SOURCE = Path(__file__).parents[1] / "federation-gateway" / "main.py"
SPEC = importlib.util.spec_from_file_location("federation_gateway_main", SOURCE)
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


class FederationGatewayTests(unittest.TestCase):
    def setUp(self):
        gateway._rate_hits.clear()
        self.client = gateway.app.test_client()

    def test_public_surface_includes_only_safe_bottle_routes(self):
        self.assertIn(("POST", "/bottles/publish"), gateway.ALLOWED_ROUTES)
        self.assertIn(("GET", "/bottles/list"), gateway.ALLOWED_ROUTES)
        self.assertNotIn(("POST", "/bottles/announce"), gateway.ALLOWED_ROUTES)
        response = self.client.post("/bottles/announce")
        self.assertEqual(response.status_code, 404)

    def test_public_web_node_surface_has_worker_and_chat_routes(self):
        for path in ("/web/register", "/web/poll", "/web/result"):
            self.assertIn(("POST", path), gateway.ALLOWED_ROUTES)
            self.assertIn(("OPTIONS", path), gateway.ALLOWED_ROUTES)
            self.assertIn(path, gateway._RATE_LIMITS)
        self.assertNotIn(("POST", "/web/tasks"), gateway.ALLOWED_ROUTES)
        self.assertNotIn(("GET", "/web/status"), gateway.ALLOWED_ROUTES)
        self.assertIn(("GET", "/v1/models"), gateway.ALLOWED_ROUTES)
        self.assertIn(("OPTIONS", "/v1/models"), gateway.ALLOWED_ROUTES)
        self.assertIn(("POST", "/v1/chat/completions"), gateway.ALLOWED_ROUTES)
        self.assertIn(("OPTIONS", "/v1/chat/completions"), gateway.ALLOWED_ROUTES)
        self.assertIn("/v1/models", gateway._RATE_LIMITS)
        self.assertIn("/v1/chat/completions", gateway._RATE_LIMITS)
        self.assertNotIn(("GET", "/nodes/active"), gateway.ALLOWED_ROUTES)
        self.assertNotIn(("GET", "/logs"), gateway.ALLOWED_ROUTES)

    def test_web_node_preflight_does_not_consume_rate_limit(self):
        upstream = Mock(
            content=b"", status_code=204,
            headers={"Access-Control-Allow-Origin": "*"},
        )
        with patch.object(gateway, "_rate_check") as rate_check, \
             patch.object(gateway.requests, "request", return_value=upstream):
            response = self.client.options("/web/register")
        self.assertEqual(response.status_code, 204)
        rate_check.assert_not_called()

    def test_public_chat_is_forwarded_as_a_stream(self):
        upstream = Mock(
            status_code=200,
            headers={"Content-Type": "text/event-stream"},
        )
        upstream.iter_content.return_value = iter([b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n"])
        with patch.object(gateway, "_rate_check", return_value=True), \
             patch.object(gateway.requests, "request", return_value=upstream) as request_call:
            response = self.client.post(
                "/v1/chat/completions", json={"model": "qwen3.5:4b"}, buffered=False)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(request_call.call_args.kwargs["stream"])
        response.close()

    def test_spoofed_attestation_is_replaced_with_gateway_signature(self):
        secret = "g" * 32
        upstream = Mock(
            content=b'{}', status_code=200,
            headers={"Content-Type": "application/json"},
        )
        with patch.dict(os.environ, {"BOTTLE_GATEWAY_SECRET": secret}), \
             patch.object(gateway, "_rate_check", return_value=True), \
             patch.object(gateway.time, "time", return_value=1000.0), \
             patch.object(gateway.requests, "request", return_value=upstream) as request_call:
            response = self.client.post(
                "/bottles/publish",
                headers={
                    "X-Hs-Client-Ip": "198.51.100.99",
                    "X-Hs-Client-Ts": "1",
                    "X-Hs-Client-Sig": "forged",
                },
                environ_base={"REMOTE_ADDR": "203.0.113.7"},
            )

        self.assertEqual(response.status_code, 200)
        headers = request_call.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Hs-Client-Ip"], "203.0.113.7")
        self.assertTrue(verify_client_ip(
            secret,
            headers["X-Hs-Client-Ip"],
            headers["X-Hs-Client-Ts"],
            headers["X-Hs-Client-Sig"],
            now=1000,
        ))

    def test_rate_limits_are_independent_and_keep_their_own_windows(self):
        old_limits = gateway._RATE_LIMITS
        gateway._RATE_LIMITS = {
            "/bottles/publish": (1, 3600),
            "/bottles/list": (1, 60),
        }
        try:
            with patch.object(gateway.time, "time", return_value=1000):
                self.assertTrue(gateway._rate_check("/bottles/publish", "client"))
            with patch.object(gateway.time, "time", return_value=1100):
                self.assertTrue(gateway._rate_check("/bottles/list", "client"))
                self.assertFalse(gateway._rate_check("/bottles/publish", "client"))
            with patch.object(gateway.time, "time", return_value=1161):
                self.assertTrue(gateway._rate_check("/bottles/list", "client"))
                self.assertFalse(gateway._rate_check("/bottles/publish", "client"))
        finally:
            gateway._RATE_LIMITS = old_limits
            gateway._rate_hits.clear()


if __name__ == "__main__":
    unittest.main()
