# SPDX-License-Identifier: Apache-2.0
"""Layer di superficie: identità unica, contesto del mezzo separato.

Difende l'invariante del requisito: l'identità NON cambia con il mezzo, il
contesto è deterministico e non entra mai nel documento d'identità.
"""
import ast
import unittest
from dataclasses import replace
from pathlib import Path

from shared.persona import (IDENTITY_TOOLS, INTRO_MAX_BOUNDARIES, SURFACE_CONTEXTS,
                            SURFACES_WITHOUT_IDENTITY, build_introduction,
                            build_system_block, default_persona, identity_expected,
                            identity_tools_hidden, normalize_surface, surface_context)

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

    def test_comfyui_ha_una_superficie_sua(self):
        """Un nodo di generazione immagini non e' 'openwebui'.

        Il contesto di superficie esiste per questo: la richiesta arriva con
        `surface=comfyui` e deve ricevere l'istruzione che rende utilizzabile
        l'uscita (solo il prompt, niente preamboli) — resta separata
        dall'identita', che non cambia.
        """
        self.assertEqual(normalize_surface("comfyui"), "comfyui")
        blocco = surface_context("comfyui")
        self.assertIn("SOLO il prompt", blocco)
        self.assertNotIn("## Contesto del mezzo", build_system_block(default_persona("Aurora")))
        self.assertEqual(build_system_block(default_persona("Aurora")),
                         build_system_block(default_persona("Aurora")))

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


class IdentitaPrevistaTests(unittest.TestCase):
    """`workbench`: la console usata come banco di lavoro non porta l'identità.

    Perché ha un test: spegnere l'identità è una decisione, e questa è la sola
    superficie che lo fa. Se domani qualcuno aggiunge una superficie all'elenco,
    lo fa qui — e lo fa sapendo (misurato il 2026-09-23: 1727 caratteri, ~500
    token, con il tono delle stanze che vince sul contesto del mezzo).
    """

    def test_workbench_non_vuole_l_identita(self):
        self.assertEqual(SURFACES_WITHOUT_IDENTITY, frozenset({"workbench"}))
        self.assertFalse(identity_expected("workbench"))
        self.assertEqual(normalize_surface("workbench"), "workbench")

    def test_tutte_le_altre_si(self):
        for superficie in ("openwebui", "web-node", "terminal", "comfyui",
                           "channel-chat", "telegram-chat", "discord-pm"):
            with self.subTest(superficie=superficie):
                self.assertTrue(identity_expected(superficie))

    def test_una_superficie_sconosciuta_o_assente_non_spegne_niente(self):
        """Il default è l'identità: dimenticarsi di dichiarare la superficie non
        deve zittire l'agente."""
        for superficie in (None, "", "sconosciuta", "webui"):
            with self.subTest(superficie=superficie):
                self.assertTrue(identity_expected(superficie))

    def test_un_canale_vuole_sempre_l_identita(self):
        """Il canale riduce la chiave a `channel-chat`/`channel-pm`: lì l'agente
        parla *come Aurora*, e non c'è modo di finire in `workbench`."""
        self.assertTrue(identity_expected("workbench", channel="cam4"))
        self.assertTrue(identity_expected("", channel="telegram"))

    def test_workbench_non_ha_contesto_del_mezzo(self):
        """Non c'è identità a cui appendere un contesto: la superficie esiste per
        non iniettare niente."""
        self.assertEqual(surface_context("workbench"), "")

    def test_i_tool_dell_identita_non_si_offrono_sul_banco_di_lavoro(self):
        """Senza il blocco, il modello può ancora CHIEDERE chi è con `persona_get`:
        misurato il 2026-09-23, "chi sei?" dal workbench ha prodotto
        `tool_call: persona_get` e "Sono Aurora, un'IA che tiene compagnia a una
        cerchia ristretta…"."""
        self.assertEqual(identity_tools_hidden("workbench"), IDENTITY_TOOLS)
        self.assertEqual(identity_tools_hidden("openwebui"), frozenset())
        self.assertEqual(identity_tools_hidden(None), frozenset())
        self.assertEqual(IDENTITY_TOOLS, frozenset({"persona_get", "persona_note"}))
        # un canale non è mai workbench
        self.assertEqual(identity_tools_hidden("workbench", channel="cam4"), frozenset())


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

    def test_la_superficie_si_legge_prima_della_spunta(self):
        """Chi decide se iniettare l'identità deve sapere DOVE si sta parlando:
        con la spunta letta prima, `workbench` resterebbe senza effetto."""
        body = ast.unparse(self.functions["v1_chat_completions"])
        self.assertIn("_persona_enabled(superficie)", body)
        self.assertLess(body.index("superficie = "), body.index("_persona_enabled(superficie)"))

    def test_la_spunta_chiede_la_decisione_al_modulo(self):
        """`_persona_enabled` non tiene una seconda copia della regola: l'elenco
        delle superfici senza identità vive in `shared/persona.py`, dove è testato."""
        corpo = ast.unparse(self.functions["_persona_enabled"])
        self.assertIn("identity_expected(surface)", corpo)
        self.assertNotIn("SURFACES_WITHOUT_IDENTITY", corpo)
        self.assertNotIn("frozenset", corpo)

    def test_il_catalogo_dei_tool_si_filtra_per_superficie(self):
        """Il catalogo nativo passa da `_catalogo_nativi`, che chiede al modulo
        quali tool nascondere: tre punti lo usano (loop, non-stream, stream) e
        nessuno deve tornare a leggere `BUILTIN_TOOLS` per conto suo."""
        corpo = ast.unparse(self.functions["_catalogo_nativi"])
        self.assertIn("identity_tools_hidden(superficie)", corpo)
        sorgente = MAIN_SOURCE.read_text(encoding="utf-8")
        self.assertEqual(sorgente.count("_catalogo_nativi("), 4,
                         "una definizione + i tre punti che offrono i tool")
        # la superficie viaggia con la richiesta, o il loop non la saprebbe
        chat = ast.unparse(self.functions["v1_chat_completions"])
        self.assertIn("_hyperspace_surface", chat)
        loop = ast.unparse(self.functions["_run_tool_loop"])
        self.assertIn("_hyperspace_surface", loop)

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
