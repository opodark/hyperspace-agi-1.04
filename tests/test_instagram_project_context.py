# SPDX-License-Identifier: Apache-2.0
import unittest

from shared.instagram_project_context import should_offer_creator, wants_project_info


class InstagramProjectContextTests(unittest.TestCase):
    def test_riconosce_domande_su_mesh_e_chatbot(self):
        self.assertTrue(wants_project_info("Come funziona la vostra mesh?"))
        self.assertTrue(wants_project_info("Come siete fatte come chatbot?"))
        self.assertFalse(wants_project_info("Che musica ascolti?"))

    def test_handoff_solo_se_richiesto(self):
        self.assertTrue(should_offer_creator("Vorrei parlare con il creatore"))
        self.assertTrue(should_offer_creator("Come posso contattarti, papà?"))
        # una conversazione lunga o un problema tecnico NON è una richiesta
        self.assertFalse(should_offer_creator("Ho un bug e una configurazione da sistemare"))
        self.assertFalse(should_offer_creator("ciao"))


if __name__ == "__main__":
    unittest.main()
