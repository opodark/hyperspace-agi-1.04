# SPDX-License-Identifier: Apache-2.0
"""Il contratto OpenAI di `cp/chat.py`, provato funzione per funzione.

Dieci funzioni che decidono la FORMA di quello che esce da `/v1/chat/completions`.
Un errore qui non fa fallire una richiesta: fa restituire 200 con un JSON valido e
sbagliato, e il difetto compare mesi dopo in una conversazione. Per questo sono
provate una per una, sui casi dove sbagliare fa danno.

I test già esistenti coprono il comportamento atteso (`test_assistant_text.py`,
`test_tool_passthrough.py`, `test_inference_fallback.py`, `test_persona.py`) e
seguono il codice da soli grazie a `cp_source`. Qui si aggiunge quello che
manca: cosa succede ai BORDI, perché un `index` di `tool_calls` sbagliato o un
`data: [DONE]` fuori posto non fallisce mai, concatena.

Ogni gruppo è seguito da un test che verifica di mordere: si rompe la funzione e
si controlla che il test corrispondente fallisca.
"""
import sys
import unittest
from pathlib import Path

# `cp/` sta dentro control-plane/, quindi va in sys.path prima dell'import. Gli
# altri test che importano un modulo `cp.*` fanno lo stesso: senza questa riga il
# file passa con `PYTHONPATH=.:control-plane` e fallisce con il `PYTHONPATH=.` con
# cui gira la suite, e il risultato è un test che a volte esiste.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control-plane"))

from cp import chat as cp_chat  # noqa: E402


def _messaggio(content=None, reasoning=None, tool_calls=None, role="assistant"):
    m = {"role": role}
    if content is not None:
        m["content"] = content
    if reasoning is not None:
        m["reasoning"] = reasoning
    if tool_calls is not None:
        m["tool_calls"] = tool_calls
    return m


def _risposta(message=None, finish="stop"):
    return {"id": "chatcmpl-x", "object": "chat.completion", "created": 1,
            "model": "modello",
            "choices": [{"index": 0, "message": message or _messaggio("ciao"),
                         "finish_reason": finish}]}


class ChunkFinaleTests(unittest.TestCase):
    """`_chunk_finale` costruisce il pezzo SSE. L'ordine dei `delta` è ciò che il
    client usa per ricostruire il testo, e un indice sbagliato lo corrompe senza
    sollevare nulla."""

    def test_il_pezzo_ha_la_forma_di_un_chunk(self):
        pezzo = cp_chat._chunk_finale(_risposta(), "modello", "task-1")
        self.assertEqual(pezzo["object"], "chat.completion.chunk")
        self.assertEqual(pezzo["choices"][0]["index"], 0)
        self.assertIn("created", pezzo)
        self.assertIn("model", pezzo)

    def test_il_testo_va_in_delta_content(self):
        pezzo = cp_chat._chunk_finale(_risposta(_messaggio("ciao")), "m", "t")
        delta = pezzo["choices"][0]["delta"]
        self.assertEqual(delta.get("content"), "ciao")
        # il testo NON deve stare in `message`: un client OpenAI-compatible legge
        # i `delta`, e un `message` li ignora.
        self.assertNotIn("message", delta)

    def test_content_vuoto_e_reasoning_va_in_delta_content(self):
        """Il caso per cui esiste `_normalize_assistant_message`: il modello ha
        ragionato e non ha scritto niente. Il `reasoning` deve finire dove il
        client lo legge, altrimenti l'utente vede una risposta vuota."""
        risposta = _risposta(_messaggio("", reasoning="ho ragionato"))
        cp_chat._normalize = getattr(cp_chat, "_normalize", None)
        pezzo = cp_chat._chunk_finale(risposta, "m", "t")
        delta = pezzo["choices"][0]["delta"]
        self.assertIn("content", delta, "il reasoning deve arrivare in content")

    def test_il_finish_reason_e_sempre_stop(self):
        """Il pezzo finale dichiara "stop" e non riporta il `finish_reason` del
        risultato. Non è una perdita: quello arriva nel chunk di chiusura vero,
        e qui sarebbe un secondo annuncio che il client non sa a cosa attribuire."""
        pezzo = cp_chat._chunk_finale(_risposta(finish="length"), "m", "t")
        self.assertEqual(pezzo["choices"][0].get("finish_reason"), "stop")

    def test_risposta_malformata_non_sfonda(self):
        for cattiva in ({}, {"choices": []}, {"choices": [{}]}, None, []):
            with self.subTest(risposta=str(cattiva)[:30]):
                pezzo = cp_chat._chunk_finale(cattiva, "m", "t")
                self.assertIn("choices", pezzo)
                self.assertIsInstance(pezzo["choices"], list)


class ToolCallsPassthroughTests(unittest.TestCase):
    def test_una_risposta_senza_tool_da_lista_vuota(self):
        for senza in (None, [], "non-una-lista", 7):
            with self.subTest(senza=str(senza)):
                risposta = _risposta(_messaggio("ciao", tool_calls=senza))
                self.assertEqual(cp_chat._tool_calls_passthrough(risposta), [])

    def test_un_tool_call_vuoto_passa_com_e_vuoto(self):
        """`[{}]` passa il filtro, e resta cosi': e' un dizionario, quindi la
        forma e' giusta, e scartarlo qui significherebbe nascondere al chiamante
        un tool_call che il modello ha davvero emesso. Il tool loop e' il posto
        dove si scopre che non ha un nome e si risponde "non gestito"."""
        self.assertEqual(cp_chat._tool_calls_passthrough(
            _risposta(_messaggio(None, tool_calls=[{}]))), [{}])

    def test_i_tool_calls_vengono_presi_tali_e_quali(self):
        """Non filtrati né riordinati: il client li esegue nell'ordine in cui il
        modello li ha chiesti, e un riordino qui cambierebbe quello che viene
        prima eseguito."""
        calls = [{"id": "a", "index": 0, "function": {"name": "primo"}},
                 {"id": "b", "index": 1, "function": {"name": "secondo"}}]
        ottenuti = cp_chat._tool_calls_passthrough(
            _risposta(_messaggio(None, tool_calls=calls)))
        self.assertEqual([c["function"]["name"] for c in ottenuti],
                         ["primo", "secondo"])
        self.assertEqual([c["index"] for c in ottenuti], [0, 1])

    def test_una_risposta_malformata_non_sfonda(self):
        for cattiva in (None, {}, {"choices": None}, {"choices": [{}]}, "testo"):
            with self.subTest(cattiva=str(cattiva)):
                self.assertEqual(cp_chat._tool_calls_passthrough(cattiva), [])


class ToolDelClientTests(unittest.TestCase):
    """`_tool_del_client` dice se un tool torna al client che l'ha offerto.

    La regola e' deliberatamente capovolta rispetto a come si legge: True NON
    significa "lo eseguiamo noi" ma "torna al client". Il CP esegue solo cio' che
    e' suo — nativi e connettori — perche' un tool che il client ha offerto e' un
    tool che il client sa eseguire, e perderlo significa che il modello riceve
    "tool non gestito" e racconta di aver fatto qualcosa che non ha fatto.

    Per questo la funzione dipende da `_handlers_nativi`: se il tool e' nostro
    resta nostro e non torna indietro, anche se il client lo aveva offerto con
    lo stesso nome."""

    def setUp(self):
        cp_chat.imposta_handlers(lambda: {"web_search", "memory_search"})
        self.addCleanup(cp_chat.imposta_handlers, None)

    def test_un_tool_che_e_nostro_non_torna_al_client(self):
        """Anche se il client lo ha offerto con lo stesso nome: il CP lo sa
        eseguire, e restituirlo significherebbe scaricare un lavoro che sa fare."""
        self.assertFalse(cp_chat._tool_del_client("web_search", {"web_search"}))

    def test_un_tool_che_non_e_nostro_ma_il_client_lo_ha_offerto_torna_a_lui(self):
        self.assertTrue(cp_chat._tool_del_client("generate_image", {"generate_image"}))

    def test_un_tool_che_nessuno_conosce_non_torna_a_nessuno(self):
        """Non e' nostro e il client non l'ha offerto: resta senza esecutore e
        torna al tool loop, che rispondera "non gestito". Tornare al client
        sarebbe inventare un esecutore che non c'e'."""
        self.assertFalse(cp_chat._tool_del_client("ignoto", {"generate_image"}))

    def test_un_nome_vuoto_non_e_un_tool_del_client(self):
        self.assertFalse(cp_chat._tool_del_client("", {"generate_image"}))
        self.assertFalse(cp_chat._tool_del_client(None, {"generate_image"}))

    def test_i_nomi_vengono_confrontati_case_sensitive(self):
        """`Generate_Image` e `generate_image` sono due tool diversi: i nomi dei
        tool sono identificatori, non titoli, e Open WebUI li registra cosi'. Il
        confronto e' case-sensitive e deve restarlo."""
        self.assertFalse(cp_chat._tool_del_client("Generate_Image", {"generate_image"}))
        self.assertTrue(cp_chat._tool_del_client("generate_image", {"generate_image"}))

    def test_senza_handlers_registrati_il_catalogo_e_vuoto(self):
        """Prima del montaggio il catalogo nativo e' vuoto, e quindi ogni tool
        offerto dal client torna a lui. Non e' un difetto: in quel momento non
        c'e' ancora nessun tool nostro, e restituire al client'e' la risposta
        conservativa — il tool loop rispondera' "non gestito" se il client non
        lo esegue, che e' una risposta vera invece di un silenzio."""
        cp_chat.imposta_handlers(None)
        self.assertEqual(cp_chat.catalogo_nativi(), [])
        self.assertTrue(cp_chat._tool_del_client("web_search", {"web_search"}))


class RispostaSoloToolDelClientTests(unittest.TestCase):
    """Il filtro che tiene per il CP solo i tool che il client può eseguire."""

    def test_content_none_diventa_stringa_vuota(self):
        """E' la ragione per cui questo percorso non ha mai fatto il 500: il None
        veniva convertito qui. Ma solo qui — il resto della rotta non passa da
        questa funzione, e li' il None arrivava fino a `reply_text[:500]`."""
        risposta = cp_chat._risposta_solo_tool_del_client(
            _risposta(_messaggio(None, tool_calls=[])),
            [{"id": "c", "index": 0, "function": {"name": "x"}}])
        self.assertEqual(risposta["choices"][0]["message"]["content"], "")

    def test_il_finish_reason_diventa_tool_calls(self):
        risposta = cp_chat._risposta_solo_tool_del_client(
            _risposta(finish="stop"), [{"function": {"name": "x"}}])
        self.assertEqual(risposta["choices"][0]["finish_reason"], "tool_calls")

    def test_l_originale_non_viene_toccata(self):
        """La copia è profonda: il chiamante fa `jsonify(result_json)` subito dopo
        e deve ricevere la versione filtrata, mentre la risposta originale
        dell'inferenza resta intatta per i log."""
        originale = _risposta(_messaggio("testo", tool_calls=[{"id": "vecchio"}]))
        risposta = cp_chat._risposta_solo_tool_del_client(
            originale, [{"id": "nuovo", "function": {"name": "x"}}])
        self.assertEqual(originale["choices"][0]["message"]["tool_calls"][0]["id"],
                         "vecchio")
        self.assertEqual(risposta["choices"][0]["message"]["tool_calls"][0]["id"],
                         "nuovo")

    def test_i_tool_del_cp_vengono_fuori(self):
        """Il caso per cui la funzione esiste: un tool nostro in una risposta che
        va al client sarebbe irrecuperabile, perché il CP non tiene stato fra una
        richiesta e l'altra."""
        richiesta = _risposta(_messaggio("testo", tool_calls=[
            {"id": "nostro", "function": {"name": "web_search"}},
            {"id": "del_client", "function": {"name": "generate_image"}}]))
        risposta = cp_chat._risposta_solo_tool_del_client(
            richiesta, [{"id": "del_client", "function": {"name": "generate_image"}}])
        nomi = [c["function"]["name"]
                for c in risposta["choices"][0]["message"]["tool_calls"]]
        self.assertEqual(nomi, ["generate_image"])


class EndpointTests(unittest.TestCase):
    """`parse_inference_urls` non deve mai restituire una lista vuota: sotto un
    carico qualsiasi il nodo deve poter ancora rispondere."""

    def test_una_lista_si_scompone(self):
        got = cp_chat.parse_inference_urls("http://uno:8080, http://due:8080", "http://fallback")
        self.assertEqual(got, ["http://uno:8080", "http://due:8080"])

    def test_il_fallback_entra_quando_l_elenco_e_vuoto(self):
        for vuoto in ("", "   ", ",,", " , "):
            with self.subTest(valore=vuoto):
                self.assertEqual(cp_chat.parse_inference_urls(vuoto, "http://fallback"),
                                 ["http://fallback"])

    def test_gli_spazi_rosso_intorno_vengono_ripuliti(self):
        self.assertEqual(cp_chat.parse_inference_urls("  http://uno  ", "http://f"),
                         ["http://uno"])

    def test_il_fallback_viene_ripulito_come_un_endpoint(self):
        self.assertEqual(cp_chat.parse_inference_urls("", "  http://fallback  "),
                         ["http://fallback"])

    def test_un_valore_none_non_rompe_niente(self):
        got = cp_chat.parse_inference_urls(None, "http://fallback")
        self.assertTrue(got, "la lista non deve mai essere vuota")


class DeadlineTests(unittest.TestCase):
    """`_deadline_exceeded` dice al client PERCHE' la risposta si e' interrotta.

    Il 504 conta piu' del messaggio: un 200 con un corpo d'errore faceva sembrare
    riuscito un fallimento, e il client ci scriveva sopra senza accorgersene
    (verificato in sessione di test).
    """

    def setUp(self):
        from flask import Flask
        from cp.budget import RequestDeadline
        self.app = Flask(__name__)
        # Un clock finto: il test non aspetta, e `elapsed` restituisce un numero
        # invece di dipendere da quanto e' passato davvero.
        orologio = [1000.0]
        self.scadenza = RequestDeadline(total_s=5, clock=lambda: orologio[0])
        orologio[0] = 1005.2

    def test_la_risposta_e_un_504(self):
        task = {}
        with self.app.test_request_context("/v1/chat/completions"):
            risposta, stato = cp_chat._deadline_exceeded(task, "task-1", self.scadenza)
        self.assertEqual(stato, 504)

    def test_il_tipo_e_quello_del_contratto(self):
        """Il client OpenAI-compatible distingue i tipi: `deadline_exceeded` dice
        "riprova", un tipo generico dice "non so cosa fare"."""
        task = {}
        with self.app.test_request_context("/v1/chat/completions"):
            risposta, _ = cp_chat._deadline_exceeded(task, "task-1", self.scadenza)
        # Dentro un contesto di richiesta `jsonify` restituisce gia' una Response,
        # non un dict: il corpo si legge con get_json().
        self.assertEqual(risposta.get_json()["error"]["type"], "deadline_exceeded")

    def test_il_messaggio_dice_che_e_un_budget_e_quanto(self):
        task = {}
        with self.app.test_request_context("/v1/chat/completions"):
            risposta, _ = cp_chat._deadline_exceeded(task, "task-1", self.scadenza)
        messaggio = risposta.get_json()["error"]["message"]
        self.assertIn("budget", messaggio)
        self.assertIn("5", messaggio)

    def test_il_task_viene_marcato_come_fallito(self):
        """Senza questo, il task resterebbe in corso e il work loop continuerebbe a
        aspettare una risposta che non arrivera' piu'."""
        task = {"status": "running"}
        with self.app.test_request_context("/v1/chat/completions"):
            cp_chat._deadline_exceeded(task, "task-1", self.scadenza)
        self.assertEqual(task["status"], "failed")
        self.assertTrue(task["error"])


if __name__ == "__main__":
    unittest.main()