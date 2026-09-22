# SPDX-License-Identifier: Apache-2.0
"""Layer di superficie: identità unica, contesto del mezzo separato.

Difende l'invariante del requisito: l'identità NON cambia con il mezzo, il
contesto è deterministico e non entra mai nel documento d'identità.
"""
import ast
import unittest
from dataclasses import replace
from pathlib import Path

from shared.persona import (INTRO_MAX_BOUNDARIES, SURFACE_CONTEXTS, build_introduction,
                            build_system_block, default_persona, normalize_surface,
                            surface_context)

ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = ROOT / "control-plane" / "main.py"


class NormalizeSurfaceTests(unittest.TestCase):
    def test_superficie_sconosciuta_senza_canale_e_vuota(self):
        self.assertEqual(normalize_surface("sconosciuto"), "")

    def test_superficie_nota_senza_canale(self):
        self.assertEqual(normalize_surface("openwebui"), "openwebui")
        self.assertEqual(normalize_surface("Web-Node"), "web-node")
        self.assertEqual(normalize_surface("terminal"), "terminal")

    def test_canale_riduce_a_chat_o_pm(self):
        self.assertEqual(normalize_surface("chat", channel="cam4"), "channel-chat")
        self.assertEqual(normalize_surface("pm", channel="cam4"), "channel-pm")
        self.assertEqual(normalize_surface("", channel="cam4"), "channel-chat")


class SurfaceContextTests(unittest.TestCase):
    def test_contesto_deterministico(self):
        self.assertEqual(surface_context("openwebui"), surface_context("openwebui"))

    def test_superficie_sconosciuta_non_produce_contesto(self):
        self.assertEqual(surface_context("nonesiste"), "")

    def test_canale_nomina_la_piattaforma(self):
        blocco = surface_context("chat", channel="cam4")
        self.assertIn("cam4", blocco)
        self.assertIn("(chat)", blocco)

    def test_identita_non_contiene_il_contesto_del_mezzo(self):
        identita = build_system_block(default_persona("Aurora"))
        self.assertNotIn("## Contesto del mezzo", identita)
        for chiave in SURFACE_CONTEXTS:
            with self.subTest(chiave=chiave):
                # l'identità è invariante: il contesto è un'aggiunta, non una modifica
                self.assertEqual(build_system_block(default_persona("Aurora")), identita)


class DiscordSurfaceTests(unittest.TestCase):
    def test_discord_ha_superficie_specifica(self):
        self.assertEqual(normalize_surface("chat", channel="discord"), "discord-chat")
        self.assertEqual(normalize_surface("pm", channel="discord"), "discord-pm")

    def test_contesto_discord_nomina_la_piattaforma(self):
        blocco = surface_context("chat", channel="discord")
        self.assertIn("Discord", blocco)
        self.assertIn("(chat)", blocco)


class IntroductionTests(unittest.TestCase):
    def test_intro_deterministica_e_fattuale(self):
        persona = default_persona("Aurora")
        intro = build_introduction(persona)
        self.assertEqual(intro, build_introduction(persona))
        self.assertIn("Aurora", intro)
        self.assertIn("un'IA", intro)
        self.assertIn("non sono una persona", intro)

    def test_intro_non_rivendica_umanita(self):
        intro = build_introduction(default_persona("Aurora"))
        self.assertNotIn("sono umano", intro)
        self.assertNotIn("sono una persona reale", intro)
        self.assertIn("un'IA", intro)

    def test_intro_riporta_la_provenienza(self):
        intro = build_introduction(default_persona("Aurora"))
        self.assertIn("Derivo da", intro)
        self.assertIn("HyperSpace AGI", intro)

    def test_intro_usa_solo_la_prima_frase_dello_scopo(self):
        persona = replace(default_persona("Aurora"),
                          purpose="Prima frase dello scopo. Seconda frase che non entra.")
        intro = build_introduction(persona)
        self.assertIn("Prima frase dello scopo.", intro)
        self.assertNotIn("Seconda frase", intro)

    def test_intro_dichiara_solo_i_primi_confini(self):
        """L'ordine dei confini nel documento e' la priorita' pubblica: gli altri
        restano nel blocco di identita', dove servono al modello."""
        persona = replace(default_persona("Aurora"),
                          boundaries=tuple(f"confine numero {n}" for n in range(1, 7)))
        intro = build_introduction(persona)
        for numero in range(1, INTRO_MAX_BOUNDARIES + 1):
            self.assertIn(f"confine numero {numero}.", intro)
        self.assertNotIn(f"confine numero {INTRO_MAX_BOUNDARIES + 1}", intro)


class SurfaceWiringTests(unittest.TestCase):
    """Il cablaggio in control-plane/main.py: superficie passata alla persona."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in cls.tree.body if isinstance(n, ast.FunctionDef)}

    def test_la_chat_passa_la_superficie_alla_persona(self):
        body = ast.unparse(self.functions["v1_chat_completions"])
        self.assertIn("surface=superficie", body)
        self.assertIn("X-Hyperspace-Surface", body)

    def test_il_canale_passa_superficie_e_canale_alla_persona(self):
        body = ast.unparse(self.functions["_channel_reply"])
        self.assertIn("surface=surface", body)
        self.assertIn("channel=channel", body)

    def test_la_memoria_del_canale_etichetta_la_superficie(self):
        body = ast.unparse(self.functions["_channel_remember"])
        self.assertIn("'surface'", body)
        self.assertIn("channel:{channel}", body)

    def test_la_trascrizione_usa_etichette_interne(self):
        body = ast.unparse(self.functions["_trascrizione"])
        self.assertIn("'(io)'", body)
        self.assertIn("CHANNEL_OPERATOR", body)

    def test_il_comando_presentati_usa_l_intro(self):
        body = ast.unparse(self.functions["_channel_presentazione"])
        self.assertIn("!presentati", body)
        self.assertIn("build_introduction", body)


if __name__ == "__main__":
    unittest.main()
