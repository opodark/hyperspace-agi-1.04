# SPDX-License-Identifier: Apache-2.0
import unittest

from shared.instagram_project_context import should_offer_creator, wants_project_info


class InstagramProjectContextTests(unittest.TestCase):
    def test_riconosce_domande_su_mesh_e_chatbot(self):
        self.assertTrue(wants_project_info("Come funziona la vostra mesh?"))
        self.assertTrue(wants_project_info("Come siete fatte come chatbot?"))
        self.assertFalse(wants_project_info("Che musica ascolti?"))

    def test_handoff_esplicito_o_conversazione_articolata(self):
        self.assertTrue(should_offer_creator([], "Vorrei parlare con il creatore"))
        turns = [{"role": "user", "text": "x" * 120} for _ in range(3)]
        self.assertTrue(should_offer_creator(turns, turns[-1]["text"]))
        self.assertFalse(should_offer_creator([{"role": "user", "text": "ciao"}], "ciao"))


if __name__ == "__main__":
    unittest.main()
