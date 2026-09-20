# SPDX-License-Identifier: Apache-2.0
"""Policy read/write dei connettori: default read-only, allowlist opt-in.

La policy è pura (shared/connector_policy.py), quindi si testa direttamente,
senza Flask e senza control-plane/main.py — come tests/test_mcp_auth.py.

Quello che questi test difendono: il default deve restare fail-closed. Un
`write_allowed` che tornasse True "perché tanto il nome del tool non sembra una
scrittura", o un'allowlist scritta male interpretata come "tutti i tool",
rimetterebbero il modello in condizione di inviare email a nome
dell'organizzazione.
"""
import json
import unittest

from shared.connector_policy import (ALL, READ_ONLY_VAR, WRITE_TOOLS_VAR,
                                     ConnectorPolicy, parse_write_allowlist)

# Nomi reali dei tool di scrittura (replicati: un test di policy pura non deve
# dipendere dal package control-plane).
KNOWN = {"github": ("github_create_issue", "github_add_comment"),
         "office365": ("o365_send_email", "o365_create_event")}


class ParseTests(unittest.TestCase):
    def test_wildcard_e_elenco_esplicito(self):
        parsed, problems = parse_write_allowlist("github=github_create_issue;o365=*")
        self.assertEqual(parsed["github"], frozenset({"github_create_issue"}))
        self.assertIsNone(parsed["o365"])
        self.assertEqual(problems, [])

    def test_voce_illeggibile_e_voce_duplicata(self):
        parsed, problems = parse_write_allowlist("garbage;github=a;github=b")
        self.assertEqual(list(parsed), ["github"])
        self.assertEqual(parsed["github"], frozenset({"a"}))
        self.assertEqual(len(problems), 2)

    def test_input_vuoto_non_produce_voci_ne_problemi(self):
        for raw in ("", None, "  ; "):
            with self.subTest(raw=raw):
                self.assertEqual(parse_write_allowlist(raw), ({}, []))

    def test_valore_vuoto_significa_nessun_tool_non_tutti(self):
        parsed, _ = parse_write_allowlist("github=")
        self.assertEqual(parsed["github"], frozenset())


class PolicyTests(unittest.TestCase):
    def test_default_read_only(self):
        policy = ConnectorPolicy.from_env({})
        self.assertTrue(policy.read_only)
        self.assertFalse(policy.write_allowed("github", "github_create_issue", write=True))
        # le letture passano sempre: la policy non ha modo di bloccarle
        self.assertTrue(policy.write_allowed("github", "github_get_repo", write=False))

    def test_read_only_blocca_anche_con_allowlist(self):
        policy = ConnectorPolicy.from_env({READ_ONLY_VAR: "true", WRITE_TOOLS_VAR: "github=*"})
        self.assertFalse(policy.write_allowed("github", "github_create_issue", write=True))

    def test_read_write_abilita_solo_il_connettore_elencato(self):
        policy = ConnectorPolicy.from_env({READ_ONLY_VAR: "false", WRITE_TOOLS_VAR: "github=*"})
        self.assertTrue(policy.write_allowed("github", "github_create_issue", write=True))
        self.assertFalse(policy.write_allowed("office365", "o365_send_email", write=True))

    def test_allowlist_esplicita_non_abilita_i_tool_non_elencati(self):
        policy = ConnectorPolicy.from_env({
            READ_ONLY_VAR: "false", WRITE_TOOLS_VAR: "github=github_create_issue"})
        self.assertTrue(policy.write_allowed("github", "github_create_issue", write=True))
        self.assertFalse(policy.write_allowed("github", "github_add_comment", write=True))

    def test_read_write_senza_allowlist_e_segnalato_e_inerte(self):
        policy = ConnectorPolicy.from_env({READ_ONLY_VAR: "false"})
        self.assertFalse(policy.configured)
        self.assertTrue(any(WRITE_TOOLS_VAR in p for p in policy.problems))
        self.assertFalse(policy.write_allowed("github", "github_create_issue", write=True))

    def test_filter_tools_toglie_solo_le_scritture_negate(self):
        tools = [{"type": "function", "function": {"name": name}}
                 for name in ("github_get_repo", "github_create_issue")]
        policy = ConnectorPolicy.from_env({})
        keep = policy.filter_tools("github", tools,
                                   lambda name: name == "github_create_issue")
        self.assertEqual([t["function"]["name"] for t in keep], ["github_get_repo"])

    def test_annotate_dice_cosa_e_bloccato(self):
        rows = ConnectorPolicy.from_env({}).annotate(
            [{"name": "github", "write_tools": ["github_create_issue"]}])
        self.assertEqual(rows[0]["write_tools_exposed"], [])
        self.assertEqual(rows[0]["write_tools_blocked"], ["github_create_issue"])


class CheckAgainstTests(unittest.TestCase):
    def test_connettore_inesistente(self):
        policy = ConnectorPolicy.from_env({WRITE_TOOLS_VAR: "gitlab=*"})
        problems = policy.check_against(KNOWN)
        self.assertTrue(any("inesistente" in p and "gitlab" in p for p in problems))

    def test_tool_che_non_e_di_scrittura(self):
        policy = ConnectorPolicy.from_env({WRITE_TOOLS_VAR: "github=github_get_repo"})
        self.assertTrue(any("non sono di scrittura" in p for p in policy.check_against(KNOWN)))

    def test_voce_vuota_e_fail_closed_segnalato(self):
        policy = ConnectorPolicy.from_env({WRITE_TOOLS_VAR: "github="})
        self.assertTrue(any("nessun tool" in p for p in policy.check_against(KNOWN)))

    def test_configurazione_valida_non_produce_problemi(self):
        policy = ConnectorPolicy.from_env({
            READ_ONLY_VAR: "false",
            WRITE_TOOLS_VAR: "github=*;office365=o365_send_email"})
        self.assertEqual(policy.check_against(KNOWN), [])

    def test_describe_e_leggibile_e_senza_segreti(self):
        # Solo l'allowlist, senza READ_ONLY=false: resta read-only. È voluto —
        # servono ENTRAMBE le condizioni, e il test lo fissa.
        policy = ConnectorPolicy.from_env({WRITE_TOOLS_VAR: "github=github_create_issue"})
        described = policy.describe()
        self.assertEqual(described["write_allowlist"], {"github": ["github_create_issue"]})
        self.assertTrue(described["read_only"])
        aperto = ConnectorPolicy.from_env({
            READ_ONLY_VAR: "false", WRITE_TOOLS_VAR: "github=github_create_issue"}).describe()
        self.assertFalse(aperto["read_only"])
        wildcard = ConnectorPolicy.from_env({WRITE_TOOLS_VAR: "github=*"}).describe()
        self.assertEqual(wildcard["write_allowlist"], {"github": ALL})
        self.assertNotIn("TOKEN", json.dumps(described))


if __name__ == "__main__":
    unittest.main()
