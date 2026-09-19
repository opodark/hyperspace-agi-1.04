# SPDX-License-Identifier: Apache-2.0
"""Policy di accesso a /mcp: token, identita' del chiamante, allowlist dei tool.

La policy e' pura (shared/mcp_auth.py), quindi si testa direttamente. Le
guardie sulle route usano ast come tests/test_network_route_guards.py, cosi'
girano anche senza flask installato.
"""
import ast
import json
import unittest
from pathlib import Path

from shared.mcp_auth import (
    MIN_TOKEN_LENGTH, McpAuthPolicy, McpClient, parse_clients, parse_tool_allowlist,
)

SOURCE = Path(__file__).parents[1] / "control-plane" / "main.py"
TOKEN = "t" * MIN_TOKEN_LENGTH


class AllowlistParsingTests(unittest.TestCase):
    def test_parses_explicit_and_wildcard_entries(self):
        parsed = parse_tool_allowlist("hermes=web_search,get_mesh_status;ops=*")
        self.assertEqual(parsed["hermes"], frozenset({"web_search", "get_mesh_status"}))
        self.assertIsNone(parsed["ops"])

    def test_ignores_malformed_chunks(self):
        self.assertEqual(parse_tool_allowlist("garbage;=x; ;a="), {"a": frozenset()})

    def test_empty_input_is_empty_mapping(self):
        self.assertEqual(parse_tool_allowlist(""), {})
        self.assertEqual(parse_tool_allowlist(None), {})


class ClientParsingTests(unittest.TestCase):
    def test_builds_named_clients(self):
        clients, problems = parse_clients(f"hermes={TOKEN};ops={'o' * 40}", "ops=*")
        self.assertEqual([c.name for c in clients], ["hermes", "ops"])
        self.assertEqual(clients[1].tools, None)
        # hermes non ha allowlist: fail-closed, e il problema va segnalato
        self.assertEqual(clients[0].tools, frozenset())
        self.assertTrue(any("nessun tool" in p for p in problems))

    def test_short_token_is_dropped_not_raised(self):
        clients, problems = parse_clients("hermes=toocorto", "")
        self.assertEqual(clients, [])
        self.assertTrue(any("piu' corto" in p for p in problems))

    def test_duplicate_name_keeps_the_first(self):
        clients, problems = parse_clients(f"a={TOKEN};a={'b' * 40}", "a=*")
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].token, TOKEN)
        self.assertTrue(any("duplicato" in p for p in problems))

    def test_single_token_creates_client_named_mcp_with_all_tools(self):
        clients, _ = parse_clients("", "", TOKEN)
        self.assertEqual([c.name for c in clients], ["mcp"])
        self.assertIsNone(clients[0].tools)

    def test_single_token_respects_its_allowlist(self):
        clients, _ = parse_clients("", "mcp=web_search", TOKEN)
        self.assertEqual(clients[0].tools, frozenset({"web_search"}))

    def test_short_single_token_is_dropped(self):
        clients, problems = parse_clients("", "", "short")
        self.assertEqual(clients, [])
        self.assertTrue(any("MCP_TOKEN" in p for p in problems))

    def test_allowlist_for_unknown_client_is_reported(self):
        _, problems = parse_clients(f"a={TOKEN}", "ghost=web_search")
        self.assertTrue(any("inesistente" in p for p in problems))


class ClientScopeTests(unittest.TestCase):
    CATALOGUE = ["get_mesh_status", "web_search", "code_sandbox"]

    def test_wildcard_client_can_use_every_published_tool(self):
        client = McpClient("mcp", TOKEN, None)
        self.assertTrue(all(client.allows(t, self.CATALOGUE) for t in self.CATALOGUE))
        self.assertEqual(client.allowed_names(self.CATALOGUE), sorted(self.CATALOGUE))

    def test_explicit_allowlist_limits_the_visible_tools(self):
        client = McpClient("hermes", TOKEN, frozenset({"web_search"}))
        self.assertEqual(client.allowed_names(self.CATALOGUE), ["web_search"])
        self.assertFalse(client.allows("code_sandbox", self.CATALOGUE))

    def test_empty_allowlist_denies_everything(self):
        client = McpClient("hermes", TOKEN, frozenset())
        self.assertEqual(client.allowed_names(self.CATALOGUE), [])

    def test_wildcard_client_has_no_restriction_at_all(self):
        """Un client "*" non deve trasformare un tool INESISTENTE in "non
        permesso": la route risponde -32602 Unknown tool, non -32001. Chi ha
        visibilita' totale non ha nulla da enumerare."""
        client = McpClient("mcp", TOKEN, None)
        self.assertTrue(client.allows("tool_che_non_esiste", self.CATALOGUE))

    def test_repr_and_dict_never_expose_the_token(self):
        client = McpClient("hermes", "SUPERSECRETTOKENVALUE1234567890")
        self.assertNotIn("SUPERSECRETTOKEN", repr(client))
        self.assertNotIn("SUPERSECRETTOKEN", json.dumps(client.to_dict()))

class PolicyTests(unittest.TestCase):
    def test_authentication_matches_the_right_client(self):
        policy = McpAuthPolicy.from_env({
            "MCP_CLIENTS": f"hermes={TOKEN};ops={'o' * 40}",
            "MCP_CLIENT_TOOLS": "hermes=web_search;ops=*",
        })
        self.assertTrue(policy.configured)
        self.assertEqual(policy.authenticate(TOKEN).name, "hermes")
        self.assertEqual(policy.authenticate("o" * 40).name, "ops")
        self.assertIsNone(policy.authenticate("x" * 40))
        self.assertIsNone(policy.authenticate(""))
        self.assertIsNone(policy.authenticate(None))
        self.assertIsNone(policy.authenticate(12345))

    def test_missing_configuration_leaves_mcp_closed(self):
        policy = McpAuthPolicy.from_env({})
        self.assertFalse(policy.configured)
        self.assertTrue(policy.enabled)

    def test_kill_switch_disables_even_with_tokens(self):
        policy = McpAuthPolicy.from_env({"MCP_TOKEN": TOKEN, "MCP_ENABLED": "false"})
        self.assertFalse(policy.enabled)
        self.assertTrue(policy.configured)

    def test_loopback_is_opt_in_and_only_for_local_addresses(self):
        policy = McpAuthPolicy.from_env({"MCP_ALLOW_LOOPBACK": "true"})
        self.assertTrue(policy.allow_loopback)
        self.assertTrue(policy.is_loopback("127.0.0.1"))
        self.assertTrue(policy.is_loopback("::1"))
        self.assertFalse(policy.is_loopback("10.0.0.7"))
        self.assertFalse(policy.is_loopback(None))
        default = McpAuthPolicy.from_env({})
        self.assertFalse(default.allow_loopback)

    def test_loopback_without_tokens_is_reported_as_a_problem(self):
        policy = McpAuthPolicy.from_env({"MCP_ALLOW_LOOPBACK": "true"})
        self.assertTrue(any("loopback" in p for p in policy.problems))

    def test_allows_is_false_without_a_client(self):
        policy = McpAuthPolicy.from_env({"MCP_TOKEN": TOKEN})
        self.assertFalse(policy.allows(None, "web_search", ["web_search"]))

    def test_describe_never_leaks_a_token(self):
        secret = "SECRET_TOKEN_VALUE_FOR_TEST_0123456789"
        policy = McpAuthPolicy.from_env({"MCP_TOKEN": secret, "MCP_CLIENT_TOOLS": "mcp=*"})
        blob = json.dumps(policy.describe())
        self.assertNotIn(secret, blob)
        self.assertNotIn(secret[:12], blob)
        self.assertEqual(policy.describe()["clients"][0]["name"], "mcp")


    def test_describe_with_catalogue_lists_effective_tools(self):
        policy = McpAuthPolicy.from_env({
            "MCP_CLIENTS": f"hermes={TOKEN}",
            "MCP_CLIENT_TOOLS": "hermes=web_search",
        })
        self.assertEqual(policy.describe(["web_search", "code_sandbox"])["clients"][0]["effective_tools"],
                         ["web_search"])
        # Senza catalogo il campo resta None: non inventiamo un elenco.
        self.assertIsNone(policy.describe()["clients"][0]["effective_tools"])


class McpRouteGuardTests(unittest.TestCase):
    """Le route devono usare il gate: se qualcuno lo rimuove, il test cade."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    @staticmethod
    def _called_names(node):
        return [c.func.attr for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]

    def test_mcp_route_authenticates_before_doing_anything(self):
        source = ast.unparse(self.functions["omega_mcp"])
        self.assertIn("_mcp_policy.authenticate", source)
        self.assertIn("_mcp_policy.configured", source)
        # il gate deve precedere tools/call e tools/list nel corpo
        self.assertLess(source.index("_mcp_policy.authenticate"),
                        source.index("'tools/call'"))

    def test_tools_list_is_filtered_by_the_allowlist(self):
        source = ast.unparse(self.functions["omega_mcp"])
        self.assertIn("_mcp_policy.allows", source)
        self.assertIn("visible", source)

    def test_permission_is_checked_before_existence(self):
        body = ast.unparse(self.functions["omega_mcp"])
        self.assertLess(body.index("_mcp_policy.allows(client, tool_name"),
                        body.index("tool_name not in catalogue"))

    def test_protocol_version_is_not_simply_echoed(self):
        body = ast.unparse(self.functions["omega_mcp"])
        self.assertIn("MCP_PROTOCOL_VERSION", body)

    def test_status_route_exists_and_uses_describe(self):
        self.assertIn("mcp_status", self.functions)
        source = ast.unparse(self.functions["mcp_status"])
        self.assertIn("_mcp_policy.describe", source)

    def test_policy_is_built_from_env_once(self):
        module = SOURCE.read_text(encoding="utf-8")
        self.assertIn("_mcp_policy = McpAuthPolicy.from_env()", module)


if __name__ == "__main__":
    unittest.main()
