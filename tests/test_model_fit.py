# SPDX-License-Identifier: Apache-2.0
"""Test dell'analisi del nome modello e della stima di ingombro in VRAM.

Le fixture sono i nomi REALI presenti sulle due macchine della mesh, non
esempi inventati: sono i casi che hanno causato un errore vero (un 14B
dichiarato dove non entrava) e i casi che dimostrano perche' assumere Q4 sarebbe
sbagliato (27B in IQ2_S, 35B MoE in Q2_K_P).
"""
import unittest

from shared.model_fit import assess, describe, quant_bits, sizes

# Nomi veri, letti da /metrics e da .env.windows.
MAC = ["qwen3:8b", "qwen3:8b-original", "gemma4:e4b", "qwen2:0.5b"]
WINDOWS = [
    "hf.co/Abiray/Qwen3.5-4B-Abliterated-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q6_K",
    "hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K",
    "hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:IQ2_S",
    "hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q2_K_P",
    "qwen3.5:4b",
]


class TestSizes(unittest.TestCase):
    def test_taglia_semplice(self):
        for nome, atteso in [("qwen3:8b", 8), ("qwen3.5:4b", 4), ("qwen2:0.5b", 0.5),
                             ("deepseek-r1:8b", 8), ("qwen3-14b-uncensored", 14)]:
            with self.subTest(nome=nome):
                self.assertEqual(sizes(nome)[0], atteso)

    def test_versione_non_scambiata_per_taglia(self):
        """`qwen3.5:4b` ha 3.5 (versione) e 4 (taglia): conta la taglia."""
        self.assertEqual(sizes("qwen3.5:4b")[0], 4.0)
        self.assertEqual(sizes("hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K")[0], 9.0)
        # 3.5 e' la versione, 4 e' la taglia, 4.6 e' ancora la versione.
        self.assertEqual(
            sizes("hf.co/Abiray/Qwen3.5-4B-Abliterated-Claude-4.6-Opus"
                  "-Reasoning-Distilled-GGUF:Q6_K")[0], 4.0)

    def test_moe_la_taglia_e_il_totale_non_gli_attivi(self):
        """Il caso che inganna: l'ultimo numero-B e' 3, il modello e' da 35."""
        totale, attivi = sizes(
            "hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q2_K_P")
        self.assertEqual(totale, 35.0)
        self.assertEqual(attivi, 3.0)

    def test_denso_non_diventa_moe(self):
        """`llama3b` non e' un MoE con 3B attivi: serve un confine di parola."""
        self.assertEqual(sizes("llama3b")[1], None)
        self.assertEqual(sizes("qwen3:8b")[1], None)

    def test_taglia_nell_e(self):
        """`gemma4:e4b`: il 4 di gemma4 e' una versione, e4b e' la taglia."""
        self.assertEqual(sizes("gemma4:e4b")[0], 4.0)

    def test_nome_senza_taglia_non_inventa(self):
        self.assertEqual(sizes("modello-misterioso"), (None, None))


class TestQuantBits(unittest.TestCase):
    def test_tag_in_tabella(self):
        for nome, tag, bit in [
            ("hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K", "Q6_K", 6.6),
            ("hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:IQ2_S", "IQ2_S", 2.2),
        ]:
            with self.subTest(nome=nome):
                bit_ottenuti, tag_ottenuto = quant_bits(nome)
                self.assertEqual(tag_ottenuto, tag)
                self.assertEqual(bit_ottenuti, bit)

    def test_tag_fuori_tabella_stimato_dalla_cifra(self):
        """Q2_K_P non e' in tabella: senza stima si ricadrebbe su 4.5 bit e il
        35B risulterebbe il doppio piu' grande di quanto e'."""
        bit, tag = quant_bits(
            "hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q2_K_P")
        self.assertEqual(tag, "Q2_K_P")
        self.assertEqual(bit, 2.5)
        self.assertLess(bit, 4.5)

    def test_senza_tag_si_usa_il_default(self):
        self.assertEqual(quant_bits("qwen3:8b"), (4.5, ""))

    def test_la_taglia_non_e_una_quant(self):
        """`qwen3.5:4b` non deve essere letto come quantizzazione 4b."""
        self.assertEqual(quant_bits("qwen3.5:4b")[1], "")


class TestVerdetti(unittest.TestCase):
    """I verdetti sulla RTX 3060 da 8 GB della macchina Windows."""

    VRAM = 8.0

    def test_il_14b_dichiarato_non_entra(self):
        """Il valore che stava in .env.windows e che non era installabile.

        E' il caso che ha causato l'errore vero: il file dichiarava un 14B, la
        macchina ne aveva 8 GB, e il sintomo osservato era "il modello e' lento"
        invece di "il modello non ci sta".
        """
        for nome in ("qwen3-14b-uncensored", "qwen3-14b-abliterated",
                     "qwen2.5-coder-14b-instruct"):
            with self.subTest(nome=nome):
                v = assess(nome, self.VRAM)
                self.assertFalse(v["fits"])
                self.assertIn("split su CPU", v["verdict"])

    def test_i_modelli_che_girano_davvero_entrano(self):
        for nome in ("qwen3.5:4b", "qwen3:8b"):
            with self.subTest(nome=nome):
                v = assess(nome, self.VRAM)
                self.assertTrue(v["fits"], f"{nome}: {v['verdict']}")

    def test_i_bit_non_si_assumono(self):
        """Il 27B in IQ2_S arriva a 8.3 GB. A Q4 sarebbero 17: fuori di molto.

        E' la correzione principale alla formula di partenza, che fissava Q4.
        """
        iq2 = assess("hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:IQ2_S", self.VRAM)
        come_se_fosse_q4 = assess("modello-27b", self.VRAM)
        self.assertEqual(iq2["bits_per_weight"], 2.2)
        self.assertLess(iq2["total_gb"], come_se_fosse_q4["total_gb"] / 1.5)

    def test_moe_spiega_perche_non_va_scartato(self):
        v = assess("hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q2_K_P",
                   self.VRAM)
        self.assertFalse(v["fits"])
        self.assertEqual(v["params_b"], 35.0)
        self.assertEqual(v["active_params_b"], 3.0)
        self.assertIn("ATTIVI", v["note"])

    def test_un_denso_non_riceve_la_nota_moe(self):
        self.assertNotIn("ATTIVI", assess("qwen2.5-coder:14b-instruct", self.VRAM)["note"])

    def test_vram_non_dichiarata_e_un_verdetto_a_se(self):
        """Il caso di oggi: vram_gb=0.0 e il routing cieco sul 75% del punteggio."""
        v = assess("qwen3:8b", 0)
        self.assertFalse(v["fits"])
        self.assertIn("non dichiarata", v["verdict"])
        self.assertIn("windows-node-handoff", v["note"])

    def test_il_contesto_cambia_il_verdetto(self):
        """Stesso modello, stesso nodo: a contesto grande non entra piu'."""
        stretto = assess("qwen3:8b", self.VRAM, context_tokens=8192)
        largo = assess("qwen3:8b", self.VRAM, context_tokens=65536)
        self.assertTrue(stretto["fits"])
        self.assertFalse(largo["fits"])
        self.assertGreater(largo["kv_gb"], stretto["kv_gb"] * 4)

    def test_la_riserva_stringe_il_margine(self):
        self.assertTrue(assess("qwen3:8b", self.VRAM)["fits"])
        self.assertFalse(assess("qwen3:8b", self.VRAM, reserve_gb=4.0)["fits"])

    def test_nome_illeggibile_non_solleva(self):
        v = assess("modello-misterioso", self.VRAM)
        self.assertFalse(v["known"])
        self.assertIsNone(v["fits"])
        self.assertIn("non deducibile", describe("modello-misterioso", self.VRAM))

    def test_describe_e_una_riga_leggibile(self):
        riga = describe("qwen3-14b-uncensored", self.VRAM)
        self.assertIn("14B", riga)
        self.assertIn("non entra", riga)
        self.assertEqual(len(riga.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
