# SPDX-License-Identifier: Apache-2.0
"""Il client dei nodi ComfyUI: due cose devono restare vere.

(1) la richiesta al control-plane è UNA chiamata deterministica — `surface`
comfyui e `X-Hyperspace-Tools: off`: senza il flag il CP inietta i suoi tool e
"scrivimi un prompt" diventa un giro di web_search;
(2) il testo che arriva al modello d'immagine è pulito: una code fence o un
"Ecco il prompt:" finiscono dentro la condizionatura di CLIP e si vedono
nell'immagine.

Nessuna rete e nessun ComfyUI: il trasporto è iniettato, che è il motivo per cui
la logica sta in un modulo a parte.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "integrations" / "comfyui" / "hyperspace_client.py"


def carica_client():
    spec = importlib.util.spec_from_file_location("hyperspace_client_sotto_test", CLIENT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


CL = carica_client()


class FintoTrasporto:
    """Trasporto finto con la firma di `_http`: registra e risponde."""

    def __init__(self, status=200, risposta=None):
        self.status = status
        self.risposta = {} if risposta is None else risposta
        self.chiamate = []

    def __call__(self, url, *, payload=None, headers=None, timeout=30.0):
        self.chiamate.append({"url": url, "payload": payload, "headers": headers,
                              "timeout": timeout})
        return self.status, self.risposta

    @property
    def ultima(self):
        return self.chiamate[-1]


def risposta_chat(testo, modello="qwen3.5:4b"):
    return {"model": modello, "choices": [{"message": {"role": "assistant",
                                                       "content": testo}}]}


class RichiestaTests(unittest.TestCase):
    def test_la_richiesta_e_una_sola_chiamata_deterministica(self):
        finto = FintoTrasporto(200, risposta_chat("a cat, neon"))
        CL.chiedi_prompt("un gatto", transport=finto)
        self.assertEqual(finto.ultima["headers"]["X-Hyperspace-Tools"], "off")
        self.assertEqual(finto.ultima["payload"]["surface"], "comfyui")
        self.assertFalse(finto.ultima["payload"]["stream"])

    def test_punta_all_endpoint_openai_del_control_plane(self):
        finto = FintoTrasporto(200, risposta_chat("x"))
        CL.chiedi_prompt("idea", base_url="http://127.0.0.1:8085/", transport=finto)
        self.assertEqual(finto.ultima["url"], "http://127.0.0.1:8085/v1/chat/completions")

    def test_modello_vuoto_non_viene_proprio_mandato(self):
        # Il default del control-plane si applica se la CHIAVE manca: con
        # `"model": ""` il backend risponde 502 "model is required" (verificato
        # dal vivo). Quindi il campo si omette.
        finto = FintoTrasporto(200, risposta_chat("x"))
        CL.chiedi_prompt("idea", transport=finto)
        self.assertNotIn("model", finto.ultima["payload"])

    def test_un_modello_richiesto_viene_mandato_pulito(self):
        finto = FintoTrasporto(200, risposta_chat("x"))
        CL.chiedi_prompt("idea", modello="  qwen3.5:4b  ", transport=finto)
        self.assertEqual(finto.ultima["payload"]["model"], "qwen3.5:4b")

    def test_idea_e_stile_arrivano_al_modello(self):
        finto = FintoTrasporto(200, risposta_chat("x"))
        CL.chiedi_prompt("  una torre   al tramonto ", stile="acquerello",
                         transport=finto)
        messaggi = finto.ultima["payload"]["messages"]
        utente = [m for m in messaggi if m["role"] == "user"][0]["content"]
        self.assertIn("Idea: una torre al tramonto", utente)
        self.assertIn("Stile richiesto: acquerello", utente)

    def test_senza_stile_non_si_chiede_uno_stile(self):
        finto = FintoTrasporto(200, risposta_chat("x"))
        CL.chiedi_prompt("idea", transport=finto)
        messaggi = finto.ultima["payload"]["messages"]
        utente = [m for m in messaggi if m["role"] == "user"][0]["content"]
        self.assertNotIn("Stile richiesto", utente)

    def test_il_timeout_richiesto_arriva_al_trasporto(self):
        finto = FintoTrasporto(200, risposta_chat("x"))
        CL.chiedi_prompt("idea", timeout=42.0, transport=finto)
        self.assertEqual(finto.ultima["timeout"], 42.0)


class PuliziaTests(unittest.TestCase):
    def test_toglie_code_fence_e_prefissi(self):
        grezzo = "```\nPrompt: a blue cat, neon light\n```"
        self.assertEqual(CL.pulisci_prompt(grezzo), "a blue cat, neon light")

    def test_le_righe_diventano_una_sola(self):
        self.assertEqual(CL.pulisci_prompt("a cat\na dog\n\n"), "a cat a dog")

    def test_toglie_le_virgolette_ai_bordi(self):
        self.assertEqual(CL.pulisci_prompt('  "a cat"  '), "a cat")

    def test_tronca_al_limite_senza_spezzare_la_parola(self):
        testo = "prima parte lunga e bella, seconda parte, terza parte"
        tagliato = CL.pulisci_prompt(testo, max_caratteri=30)
        self.assertLessEqual(len(tagliato), 30)
        self.assertTrue(tagliato.startswith("prima parte"), tagliato)

    def test_il_limite_minimo_e_di_venti_caratteri(self):
        # Un limite assurdo (5) non deve produrre un prompt vuoto o mozzato a caso.
        self.assertEqual(len(CL.pulisci_prompt("a" * 100, max_caratteri=5)), 20)

    def test_toglie_il_grassetto_markdown(self):
        # Gli asterischi finiscono dentro la condizionatura di CLIP come caratteri.
        self.assertEqual(CL.pulisci_prompt("**a cat**, `neon light`"), "a cat, neon light")

    def test_un_prompt_ripetuto_non_entra_due_volte(self):
        """Verificato dal vivo: qwen3.5 ha risposto con il prompt e poi
        "**Prompt:** <di nuovo il prompt, troncato>"."""
        grezzo = ("a cat on a rooftop at dusk, neon reflections, 35mm, "
                  "cinematic light, deep shadows **Prompt:** a cat on a rooftop")
        pulito = CL.pulisci_prompt(grezzo)
        self.assertNotIn("Prompt", pulito)
        self.assertNotIn("*", pulito)
        self.assertTrue(pulito.startswith("a cat on a rooftop at dusk"), pulito)
        self.assertEqual(pulito.count("a cat on a rooftop"), 1)

    def test_una_introduzione_prima_del_prompt_non_entra(self):
        # Caso opposto: il modello chiacchiera e POI scrive il prompt. Vince la
        # parte più lunga, che è il prompt vero.
        grezzo = "Ecco qui: prompt: a lighthouse in a storm, long exposure"
        self.assertEqual(CL.pulisci_prompt(grezzo),
                         "a lighthouse in a storm, long exposure")


class EsitiTests(unittest.TestCase):
    def test_un_errore_http_non_solleva_ma_si_legge(self):
        esito = CL.chiedi_prompt("idea", transport=FintoTrasporto(
            500, {"errore": "modello non raggiungibile"}))
        self.assertFalse(esito["ok"])
        self.assertIn("HTTP 500", esito["errore"])
        self.assertIn("modello non raggiungibile", esito["errore"])

    def test_una_risposta_vuota_e_un_errore_dichiarato(self):
        esito = CL.chiedi_prompt("idea", transport=FintoTrasporto(200, {"choices": []}))
        self.assertFalse(esito["ok"])
        self.assertIn("vuota", esito["errore"])

    def test_il_control_plane_spento_e_un_errore_leggibile(self):
        esito = CL.chiedi_prompt("idea", transport=FintoTrasporto(
            0, {"errore": "control-plane non raggiungibile: connessione rifiutata"}))
        self.assertFalse(esito["ok"])
        self.assertIn("non raggiungibile", esito["errore"])

    def test_il_report_dice_da_chi_e_arrivato_il_prompt(self):
        esito = CL.chiedi_prompt("idea", transport=FintoTrasporto(
            200, risposta_chat("a cat, neon, 35mm", modello="qwen3.5:4b")))
        self.assertTrue(esito["ok"])
        self.assertEqual(esito["prompt"], "a cat, neon, 35mm")
        self.assertEqual(esito["modello"], "qwen3.5:4b")
        self.assertIn("qwen3.5:4b", esito["report"])


class StatoReteTests(unittest.TestCase):
    def test_legge_lo_stato_dal_control_plane(self):
        finto = FintoTrasporto(200, {"status": "ok", "version": "1.05.0",
                                     "nodes_active": 1, "memories": 34})
        esito = CL.stato_rete(transport=finto)
        self.assertTrue(esito["ok"])
        self.assertTrue(esito["vivo"])
        self.assertEqual(esito["nodi"], 1)
        self.assertIn("1.05.0", esito["report"])
        self.assertIn("1 nodo attivo", esito["report"])

    def test_il_plurale_del_report_e_corretto(self):
        esito = CL.stato_rete(transport=FintoTrasporto(
            200, {"status": "ok", "nodes_active": 3, "memories": 2}))
        self.assertIn("3 nodi attivi", esito["report"])

    def test_una_rete_spenta_non_solleva(self):
        esito = CL.stato_rete(transport=FintoTrasporto(0, {"errore": "giu'"}))
        self.assertFalse(esito["ok"])
        self.assertFalse(esito["vivo"])
        self.assertEqual(esito["nodi"], 0)


class SuggerimentiTests(unittest.TestCase):
    """Il nodo deve dire COSA FARE: le tre cause reali incontrate dal vivo sono
    distinte e si risolvono in tre modi diversi."""

    def test_modello_inesistente_manda_a_ollama_list(self):
        # 502 {"message": "model 'hf.co/...Q4_K_M' not found"} — successo davvero
        consiglio = CL.suggerimento("control-plane HTTP 502: model 'x:Q4_K_M' not found")
        self.assertIn("ollama list", consiglio)

    def test_rete_giu_manda_al_check_dello_stack(self):
        consiglio = CL.suggerimento("control-plane non raggiungibile: connessione rifiutata")
        self.assertIn("start.ps1 -Check", consiglio)

    def test_risposta_vuota_dice_di_riprovare(self):
        self.assertIn("riprova", CL.suggerimento("risposta vuota dal control-plane"))

    def test_un_errore_sconosciuto_non_inventa_consigli(self):
        self.assertEqual(CL.suggerimento("HTTP 418: sono una teiera"), "")


if __name__ == "__main__":
    unittest.main()
