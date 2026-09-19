# SPDX-License-Identifier: Apache-2.0
import ast
import unittest
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"


class NetworkRouteGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.functions = {
            node.name: node for node in cls.tree.body if isinstance(node, ast.FunctionDef)
        }

    def test_privileged_routes_call_network_admin_guard(self):
        for name in ("network_status", "network_action", "bottles_announce"):
            with self.subTest(route=name):
                calls = [
                    node.func.id
                    for node in ast.walk(self.functions[name])
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                ]
                self.assertIn("_network_admin_error", calls)

    def test_public_bottle_routes_do_not_require_admin_token(self):
        for name in ("bottles_publish", "bottles_list"):
            with self.subTest(route=name):
                calls = [
                    node.func.id
                    for node in ast.walk(self.functions[name])
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                ]
                self.assertNotIn("_network_admin_error", calls)

    def test_announce_rejects_client_selected_targets(self):
        source = ast.unparse(self.functions["bottles_announce"])
        self.assertIn("'endpoint' in data", source)
        self.assertIn("'relay_url' in data", source)
        self.assertIn("normalize_http_base", source)

    def test_federate_view_verifica_firma_e_allowlist(self):
        """La rotta che condivide i dati di questo CP non deve poter esistere
        senza le stesse quattro verifiche di /federate/execute: federazione
        attiva, flag di condivisione, peer in allowlist, firma valida."""
        calls = []
        for node in ast.walk(self.functions["federate_view"]):
            if not isinstance(node, ast.Call):
                continue
            # `verify_request_headers(...)` e' una Name, `db.get_federated_peer(...)`
            # un Attribute: la guardia deve vedere entrambe le forme.
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
        self.assertIn("verify_request_headers", calls)
        self.assertIn("get_federated_peer", calls)
        self.assertIn("touch_federated_peer", calls)
        names = [n.id for n in ast.walk(self.functions["federate_view"])
                 if isinstance(n, ast.Name)]
        self.assertIn("FEDERATION_VIEW_ENABLED", names)
        self.assertIn("FEDERATION_ENABLED", names)

    def test_la_vista_aggregata_non_e_federata(self):
        """`/federation/views` parla verso i peer ma non si fa chiamare da
        fuori: da pubblica diventerebbe una sonda verso i peer federati per
        chiunque, senza avere le loro chiavi."""
        self.assertIn("app", {n.id for n in ast.walk(self.functions["federation_views"])
                              if isinstance(n, ast.Name)})
        gateway = ast.parse((Path(__file__).parents[1] / "federation-gateway" / "main.py")
                            .read_text(encoding="utf-8"))
        routes = next(ast.literal_eval(n.value) for n in gateway.body
                      if isinstance(n, ast.Assign)
                      and getattr(n.targets[0], "id", "") == "ALLOWED_ROUTES")
        paths = {path for _method, path in routes}
        self.assertIn("/federate/view", paths)
        for forbidden in ("/federation/views", "/federation/peers", "/logs", "/config/env",
                          "/tasks", "/task/create", "/bottles/announce"):
            self.assertNotIn(forbidden, paths, f"{forbidden} non deve essere raggiungibile dal gateway")


if __name__ == "__main__":
    unittest.main()
