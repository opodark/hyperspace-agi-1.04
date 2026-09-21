# SPDX-License-Identifier: Apache-2.0
import unittest

from shared.vitality import mesh_vitality, vitality_context


def nodo(vram=0.0, active=True, web=False):
    return {"status": "active" if active else "unreachable",
            "vram_gb": vram, "is_web_node": web}


class MeshVitalityTests(unittest.TestCase):
    def test_solo_mac_e_assonnata(self):
        v = mesh_vitality([nodo(vram=16.0)])
        self.assertEqual(v["level"], 1)
        self.assertEqual(v["label"], "assonnata")

    def test_nessun_nodo_spento(self):
        v = mesh_vitality([])
        self.assertEqual(v["level"], 0)
        self.assertEqual(v["label"], "spenta")

    def test_nodi_web_non_contano(self):
        v = mesh_vitality([nodo(vram=16.0), nodo(vram=100, web=True)])
        self.assertEqual(v["level"], 1)

    def test_mesh_media_sveglia(self):
        v = mesh_vitality([nodo(vram=16.0), nodo(vram=24.0)])
        self.assertEqual(v["level"], 3)  # 40 GB -> sveglia
        self.assertEqual(v["active_nodes"], 2)

    def test_mesh_ricca_in_piena_forma(self):
        nodi = [nodo(vram=16.0)] + [nodo(vram=24.0) for _ in range(4)]
        v = mesh_vitality(nodi)
        self.assertEqual(v["level"], 5)
        self.assertEqual(v["label"], "in piena forma")

    def test_contesto_deterministico_e_coerente(self):
        self.assertEqual(vitality_context({"level": 1}), vitality_context({"level": 1}))
        self.assertIn("brev", vitality_context({"level": 1}).lower())
        self.assertIn("profond", vitality_context({"level": 5}).lower())


if __name__ == "__main__":
    unittest.main()
