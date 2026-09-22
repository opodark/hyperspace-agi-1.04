# SPDX-License-Identifier: Apache-2.0
"""Canali esterni: token fail-closed, spam riconosciuto, ritmo delle risposte.

La parte delicata è la MODERAZIONE: un falso positivo colpisce una persona vera
che sta guardando la stanza, quindi i test fissano che al primo colpo non si
punisce (si smette solo di rispondere) e che gli strike decadono da soli.
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.channel import (KNOWN_CHANNELS, ChannelGuard, ChannelPolicy, ReplyPacing,  # noqa: E402
                           classifica, known_channel, normalizza, parse_clients)

MAIN_SOURCE = ROOT / "control-plane" / "main.py"
TOKEN = "t" * 40


class CatalogTests(unittest.TestCase):
    def test_telegram_e_discord_first_class(self):
        self.assertIsNotNone(known_channel("telegram"))
        self.assertTrue(known_channel("telegram")["first_class"])
        self.assertIsNotNone(known_channel("discord"))
        self.assertEqual(known_channel("nonesiste"), None)

    def test_catalogo_copre_le_piattaforme_attese(self):
        keys = {c["key"] for c in KNOWN_CHANNELS}
        self.assertTrue({"telegram", "discord", "cam4", "cb"} <= keys)

    def test_la_route_social_espone_il_catalogo(self):
        tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
        funzioni = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("channels_overview", funzioni)
        body = ast.unparse(funzioni["channels_overview"])
        self.assertIn("KNOWN_CHANNELS", body)
        self.assertIn("channel_policy", body)


class ParseTests(unittest.TestCase):
    def test_una_voce_per_canale(self):
        canali, problemi = parse_clients(f"cam4={TOKEN};cb={'c' * 33}")
        self.assertEqual(sorted(canali), ["cam4", "cb"])
        self.assertEqual(problemi, [])

    def test_token_corto_scartato_non_accettato(self):
        canali, problemi = parse_clients("cam4=corto")
        self.assertEqual(canali, {})
        self.assertTrue(any("più corto" in p for p in problemi))

    def test_duplicati_e_voci_illeggibili(self):
        canali, problemi = parse_clients(f"cam4=a;cam4={TOKEN};spazzatura")
        self.assertEqual(list(canali), ["cam4"])
        self.assertEqual(len(problemi), 2)

    def test_input_vuoto(self):
        self.assertEqual(parse_clients(""), ({}, []))
        self.assertEqual(parse_clients(None), ({}, []))


class PolicyTests(unittest.TestCase):
    def test_fail_closed_senza_token(self):
        policy = ChannelPolicy.from_env({})
        self.assertFalse(policy.configured)
        self.assertIsNone(policy.authenticate(TOKEN))
        self.assertFalse(policy.authenticate(""))

    def test_token_giusto_identifica_il_canale(self):
        policy = ChannelPolicy.from_env({"CHANNEL_CLIENTS": f"cam4={TOKEN}"})
        self.assertEqual(policy.authenticate(TOKEN), "cam4")
        self.assertIsNone(policy.authenticate("t" * 39))

    def test_disattivabile_senza_cancellare_i_token(self):
        policy = ChannelPolicy.from_env({"CHANNEL_CLIENTS": f"cam4={TOKEN}",
                                         "CHANNEL_ENABLED": "false"})
        self.assertFalse(policy.enabled)
        self.assertTrue(policy.configured)

    def test_describe_non_contiene_i_token(self):
        policy = ChannelPolicy.from_env({"CHANNEL_CLIENTS": f"cam4={TOKEN}"})
        testo = str(policy.describe())
        self.assertNotIn(TOKEN, testo)
        self.assertNotIn(TOKEN[:12], testo)
        self.assertIn("cam4", testo)


class ClassificaTests(unittest.TestCase):
    def test_link_e_promo(self):
        self.assertIn("link", classifica("vieni a trovarmi qui https://t.me/xxx"))
        self.assertIn("promo", classifica("vieni nella mia stanza, nuova ragazza online"))
        self.assertIn("link", classifica("scrivimi su telegram per un privato"))

    def test_primo_contatto_solo_con_regole_forti(self):
        forte = classifica("https://onlyfans.com/qualcuno", primo_contatto=True)
        self.assertIn("primo_contatto", forte)
        debole = classifica("CIAO A TUTTI BELLA SERATA VERO", primo_contatto=True)
        self.assertNotIn("primo_contatto", debole)

    def test_ripetizione_dentro_la_storia(self):
        storia = [normalizza("ciao bellissima"), normalizza("come stai")]
        self.assertIn("ripetizione", classifica("Ciao bellissima!!", storia=storia))

    def test_simboli_e_maiuscolo(self):
        self.assertIn("solo_simboli", classifica("🔥🔥🔥"))
        self.assertIn("tutto_maiuscolo", classifica("COMPRA ORA IL MIO PACCHETTO"))

    def test_una_persona_normale_non_scatta(self):
        for testo in ("ciao, come stai?", "sei bellissima stasera",
                      "quanto costa? quale tip per la password", "ahahah grande"):
            with self.subTest(testo=testo):
                self.assertEqual(classifica(testo), [])


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.ora = [1000.0]
        self.guard = ChannelGuard(clock=lambda: self.ora[0], strike_mute=2, strike_ban=3,
                                  flood_max=3, flood_window_s=15.0, strike_decay_s=1800,
                                  max_authors=3)

    def _spam(self, autore="bot1", testo="vieni nella mia stanza https://t.me/x"):
        return self.guard.observe(channel="cam4", surface="pm", author=autore, text=testo)

    def test_primo_colpo_non_punisce(self):
        esito = self._spam()
        self.assertEqual(esito["verdict"], "spam")
        self.assertIsNone(esito["action"], "il primo colpo non fa mutare nessuno")
        self.assertEqual(esito["strikes"], 1)

    def test_secondo_e_terzo_colpo_scalano(self):
        self._spam()
        self.assertEqual(self._spam()["action"]["action"], "mute")
        self.assertEqual(self._spam()["action"]["action"], "ban")

    def test_gli_strike_decadono(self):
        self._spam()
        self._spam()
        self.ora[0] += 1801
        esito = self._spam()
        self.assertEqual(esito["strikes"], 1, "dopo la finestra si riparte da zero")
        self.assertIsNone(esito["action"])

    def test_raffica_di_messaggi_normali(self):
        esiti = [self.guard.observe(channel="cam4", surface="chat", author="tizio",
                                    text=f"messaggio numero {i}") for i in range(6)]
        self.assertTrue(any("raffica" in e["reasons"] for e in esiti))

    def test_messaggi_normali_non_fanno_strike(self):
        esito = None
        for i in range(2):
            esito = self.guard.observe(channel="cam4", surface="chat", author="tizio",
                                       text=f"ciao, tutto bene? {i}")
        self.assertEqual(esito["verdict"], "ok")
        self.assertIsNone(esito["action"])

    def test_autori_oltre_il_tetto_vengono_dimenticati(self):
        for nome in ("a", "b", "c", "d"):
            self.guard.observe(channel="cam4", surface="chat", author=nome, text="ciao")
        self.assertLessEqual(len(self.guard._autori), 3)

    def test_snapshot_e_riconfigurazione(self):
        self._spam()
        stato = self.guard.snapshot("cam4")
        self.assertEqual(stato["authors_tracked"], 1)
        self.assertEqual(stato["spam_authors_active"], 1)
        self.guard.reconfigure(strike_mute=1, flood_max=2)
        self.assertEqual(self.guard.strike_mute, 1)
        self.assertEqual(self.guard.strike_ban, 3, "il ban non scende sotto il mute")
        self.assertEqual(self.guard.snapshot("cam4")["spam_authors_active"], 1,
                         "riconfigurare non azzera gli strike")


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.ora = [500.0]
        self.caso = [0.9]
        self.pacing = ReplyPacing(clock=lambda: self.ora[0], min_interval_s=25.0,
                                  batch_max_age_s=6.0, batch_max_messages=6,
                                  probability=1.0, random_source=lambda: self.caso[0])

    def test_niente_da_rispondere(self):
        self.assertEqual(self.pacing.decide(channel="cam4", pending=0)["action"], "wait")

    def test_cooldown_dopo_una_risposta(self):
        self.pacing.note_reply("cam4")
        esito = self.pacing.decide(channel="cam4", pending=10, oldest_age_s=60)
        self.assertEqual(esito["action"], "wait")
        self.assertIn("cooldown", esito["reason"])

    def test_batch_non_maturo_aspetta(self):
        esito = self.pacing.decide(channel="cam4", pending=2, oldest_age_s=1.0)
        self.assertEqual(esito["action"], "wait")
        self.assertIn("batch non maturo", esito["reason"])

    def test_batch_maturo_risponde(self):
        esito = self.pacing.decide(channel="cam4", pending=7, oldest_age_s=1.0)
        self.assertEqual(esito["action"], "reply")
        self.assertIn("maturo", esito["reason"])

    def test_force_ignora_cooldown_e_batch(self):
        self.pacing.note_reply("cam4")
        esito = self.pacing.decide(channel="cam4", pending=1, oldest_age_s=0.1, force=True)
        self.assertEqual(esito["action"], "reply")

    def test_probabilita_bassa_lascia_correre(self):
        self.pacing.probability = 0.5
        self.caso[0] = 0.9
        self.assertEqual(self.pacing.decide(channel="cam4", pending=9)["action"], "skip")
        self.caso[0] = 0.1
        self.assertEqual(self.pacing.decide(channel="cam4", pending=9)["action"], "reply")

    def test_vitality_riduce_l_intervallo_minimo(self):
        self.pacing.note_reply("cam4")
        self.ora[0] += 20  # 20s dall'ultima risposta
        attesa = self.pacing.decide(channel="cam4", pending=10, oldest_age_s=60,
                                    vitality={"level": 0})
        self.assertEqual(attesa["action"], "wait")
        risposta = self.pacing.decide(channel="cam4", pending=10, oldest_age_s=60,
                                      vitality={"level": 5})
        self.assertEqual(risposta["action"], "reply")

    def test_eta_dell_ultima_risposta(self):
        self.assertIsNone(self.pacing.last_reply_age_s("cam4"))
        self.pacing.note_reply("cam4")
        self.ora[0] += 10
        self.assertEqual(self.pacing.last_reply_age_s("cam4"), 10.0)


class ChannelWiringTests(unittest.TestCase):
    """Le route e i tipi di log: se qualcuno li rimuove, cade qui."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        cls.assignments = {t.id: node.value for node in tree.body
                           if isinstance(node, ast.Assign)
                           for t in node.targets if isinstance(t, ast.Name)}

    def test_le_route_dei_canali_esistono_e_autenticano(self):
        for nome in ("channel_status", "channel_ingest", "channel_reply", "channel_result"):
            with self.subTest(route=nome):
                self.assertIn(nome, self.functions)
        for nome in ("channel_ingest", "channel_reply", "channel_result"):
            with self.subTest(route=nome):
                chiamate = [c.func.id for c in ast.walk(self.functions[nome])
                            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
                self.assertIn("_channel_error", chiamate)

    def test_il_tipo_di_log_channel_e_dichiarato(self):
        self.assertIn("channel", ast.literal_eval(self.assignments["LOG_TYPES"]))

    def test_la_sezione_env_dei_canali_e_configurabile(self):
        meta = {m["key"]: m for m in ast.literal_eval(self.assignments["_ENV_META"])}
        for chiave in ("CHANNEL_CLIENTS", "CHANNEL_ENABLED", "CHANNEL_MODEL",
                       "CHANNEL_MIN_REPLY_INTERVAL_S", "CHANNEL_REPLY_PROBABILITY",
                       "CHANNEL_FLOOD_MAX", "CHANNEL_STRIKE_MUTE", "CHANNEL_STRIKE_BAN"):
            with self.subTest(key=chiave):
                self.assertEqual(meta[chiave]["section"], "Canali esterni")
        self.assertEqual(meta["CHANNEL_CLIENTS"]["type"], "password")

    def test_il_salvataggio_rilegge_la_configurazione(self):
        body = ast.unparse(self.functions["set_config_env"])
        self.assertIn("_CHANNEL_ENV_KEYS", body)
        self.assertIn("_reload_channel_config", body)

    def test_la_risposta_viene_auditata_prima_di_uscire(self):
        """Il vincolo di identità vale anche sui canali: audit prima del return."""
        body = ast.unparse(self.functions["_channel_reply"])
        self.assertIn("audit_reply", body)
        self.assertIn("should_disclose", body)
        self.assertIn("think", body, "un canale non aspetta: reasoning spento esplicito")

    def test_il_motivo_di_un_silenzio_finisce_nei_log(self):
        """"Perché non ha risposto?" deve avere una risposta nei log.

        Il driver chiede a ogni giro finché il batch non matura, quindi la riga è
        limitata nel tempo (una al minuto): senza limite sarebbe flood, senza riga
        la domanda resterebbe senza risposta — che è il caso da cui nasce.
        """
        body = ast.unparse(self.functions["channel_reply"])
        self.assertIn("_log_pacing_reason", body)
        limite = ast.unparse(self.functions["_log_pacing_reason"])
        self.assertIn("PACING_LOG_EVERY_S", limite)
        self.assertIn("push_log", limite)
        # Il motivo nel messaggio, non solo nel detail: i log mostrano il messaggio.
        self.assertIn("reason", limite)


class MemoriaTests(unittest.TestCase):
    """Tip, ondate e debounce: cosa finisce in memoria e cosa no."""

    def setUp(self):
        self.ora = [2000.0]
        self.guard = ChannelGuard(clock=lambda: self.ora[0], flood_max=2,
                                  flood_window_s=15.0, max_authors=3)

    def test_il_tip_entra_nella_nota_solo_del_suo_canale(self):
        self.guard.registra_tip(channel="cam4", author="mario", importo="100")
        nota = self.guard.nota_tip(channel="cam4")
        self.assertIn("mario", nota)
        self.assertIn("non chiedere nient'altro", nota)
        self.assertEqual(self.guard.nota_tip(channel="cb"), "",
                         "un tip su una stanza non deve comparire nell'altra")

    def test_la_nota_scade(self):
        self.guard.registra_tip(channel="cam4", author="mario")
        self.ora[0] += 181
        self.assertEqual(self.guard.nota_tip(channel="cam4"), "")

    def test_nota_vuota_senza_tip(self):
        self.assertEqual(self.guard.nota_tip(channel="cam4"), "")

    def test_ondata_solo_oltre_la_soglia(self):
        for _ in range(2):
            self.guard.observe(channel="cam4", surface="pm", author="botx",
                               text="vieni su https://t.me/x")
        self.assertIsNone(self.guard.spam_wave("cam4", soglia=5))
        for _ in range(4):
            self.guard.observe(channel="cam4", surface="pm", author="boty",
                               text="vieni su https://t.me/y")
        ondata = self.guard.spam_wave("cam4", soglia=5)
        self.assertEqual(ondata["count"], 6)
        self.assertIsNone(self.guard.spam_wave("cb", soglia=5),
                          "l'ondata è del canale in cui è successa")

    def test_debounce_della_memoria(self):
        self.assertTrue(self.guard.should_remember(key="cam4:spam_wave", window_s=600))
        self.assertFalse(self.guard.should_remember(key="cam4:spam_wave", window_s=600))
        self.assertTrue(self.guard.should_remember(key="cam4:tip:mario", window_s=600),
                        "chiavi diverse non si disturbano")
        self.ora[0] += 601
        self.assertTrue(self.guard.should_remember(key="cam4:spam_wave", window_s=600))

    def test_snapshot_mostra_tip_e_spam(self):
        self.guard.registra_tip(channel="cam4", author="mario")
        self.guard.observe(channel="cam4", surface="pm", author="botx",
                           text="vieni su https://t.me/x")
        stato = self.guard.snapshot("cam4")
        self.assertEqual(stato["tip_recenti"], 1)
        self.assertEqual(stato["spam_events_recenti"], 1)


class TokenScriptTests(unittest.TestCase):
    """Lo script del token: il posto dove si sbaglia a copiare a mano."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import channel_token
        self.script = channel_token

    def test_il_token_e_lungo_abbastanza(self):
        token = self.script.genera_token()
        self.assertEqual(len(token), 64)
        self.assertNotEqual(token, self.script.genera_token())

    def test_scrive_una_riga_nuova(self):
        testo, voci = self.script.aggiorna_env("", "cam4", "t" * 40)
        self.assertIn('CHANNEL_CLIENTS="cam4=' + "t" * 40 + '"', testo)
        self.assertEqual(voci, ["cam4"])

    def test_non_cancella_gli_altri_canali(self):
        iniziale = 'CHANNEL_CLIENTS="cb=' + "c" * 40 + '"\n'
        testo, voci = self.script.aggiorna_env(iniziale, "cam4", "t" * 40)
        self.assertEqual(voci, ["cam4", "cb"])
        self.assertIn("cb=" + "c" * 40, testo)
        self.assertIn("cam4=" + "t" * 40, testo)

    def test_rigenerare_il_token_invalida_il_vecchio(self):
        iniziale = 'CHANNEL_CLIENTS="cam4=' + "v" * 40 + '"\n'
        testo, _ = self.script.aggiorna_env(iniziale, "cam4", "n" * 40)
        self.assertNotIn("v" * 40, testo)
        self.assertEqual(testo.count("cam4="), 1)

    def test_le_altre_righe_del_env_restano(self):
        iniziale = "OLLAMA_URL=http://x\nCHANNEL_CLIENTS=\nDREAM_REVIEW_TOKEN=abc\n"
        testo, _ = self.script.aggiorna_env(iniziale, "cam4", "t" * 40)
        self.assertIn("OLLAMA_URL=http://x", testo)
        self.assertIn("DREAM_REVIEW_TOKEN=abc", testo)


class ChannelMemoryWiringTests(unittest.TestCase):
    """Il cablaggio della memoria nel control-plane."""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"))
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def test_l_ingest_accetta_i_tip_e_registra(self):
        body = ast.unparse(self.functions["channel_ingest"])
        self.assertIn("registra_tip", body)
        self.assertIn("kind", body)
        self.assertIn("_channel_remember", body)

    def test_la_risposta_usa_memoria_e_nota_tip(self):
        body = ast.unparse(self.functions["_channel_reply"])
        self.assertIn("_channel_memories", body)
        self.assertIn("nota_tip", body)

    def test_la_moderazione_riuscita_finisce_in_memoria(self):
        body = ast.unparse(self.functions["channel_result"])
        self.assertIn("_channel_remember", body)

    def test_lo_scritto_in_memoria_ha_debounce(self):
        body = ast.unparse(self.functions["_channel_remember"])
        self.assertIn("should_remember", body, "senza debounce l'ondata scriverebbe cento righe")

    def test_lo_stato_espone_ondata_e_tip(self):
        body = ast.unparse(self.functions["channel_status"])
        self.assertIn("spam_wave", body)
        self.assertIn("guard", body)


class ContestoWiringTests(unittest.TestCase):
    """Contesto del canale e memoria durevole: le manopole che si toccano davvero.

    Queste chiavi sono state aggiunte dopo una serata in stanza: il bot "non
    ricordava" cosa si era detto (contesto corto), la finestra di Ollama era il
    suo default (l'identità veniva tagliata dall'inizio) e i ricordi andavano su
    un backend spento, perdendosi in silenzio. I test difendono le tre cose.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = MAIN_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def test_il_contesto_si_legge_a_chiamata(self):
        for nome in ("_channel_context_messages", "_channel_context_chars", "_channel_num_ctx"):
            with self.subTest(nome=nome):
                self.assertIn(nome, self.functions)
        self.assertIn("_channel_context_messages()", ast.unparse(self.functions["_trascrizione"]))

    def test_la_finestra_di_contesto_e_esplicita_nel_payload(self):
        body = ast.unparse(self.functions["_channel_reply"])
        self.assertIn("_channel_num_ctx()", body)
        self.assertIn("options", body)

    def test_il_sogno_ha_un_modello_suo(self):
        # In chat una risposta lenta viene scartata dal driver; di notte no.
        # Se i due modelli tornassero a essere uno solo, il 9B finirebbe in
        # stanza (misurato: 21s contro 15s) e il bot scivolerebbe sul locale.
        self.assertIn("_dream_model", self.functions)
        self.assertIn("_dream_model()", ast.unparse(self.functions["_proponi_identita"]))
        self.assertIn("PERSONA_DREAM_MODEL", self.source)

    def test_la_memoria_legacy_puo_stare_in_un_volume(self):
        # Il default era $APP_DIR/memory.json.gz: nel container NON e' un volume,
        # quindi ogni rebuild cancellava la memoria della stanza. Ora il file si
        # puo' puntare dove e' montato.
        self.assertIn('os.getenv("MEMORY_FILE"', self.source)
        self.assertIn("MEMORY_FILE", self.source)

    def test_le_chiavi_del_contesto_sono_in_setup(self):
        sezione = self.source[self.source.index('"Canali esterni"'):
                              self.source.index("_PERSONA_ENV_SECTION") if "_PERSONA_ENV_SECTION" in self.source
                              else len(self.source)]
        for chiave in ("CHANNEL_CONTEXT_MESSAGES", "CHANNEL_CONTEXT_CHARS", "CHANNEL_NUM_CTX"):
            with self.subTest(chiave=chiave):
                self.assertIn(f'"{chiave}"', sezione)


class RuntimeTests(unittest.TestCase):
    """Stato del driver e comandi dal terminale: la coda che non si riempie di resti."""

    def _runtime(self, **kwargs):
        from shared.channel import ChannelRuntime
        self.adesso = 1000.0
        kwargs.setdefault("clock", lambda: self.adesso)
        return ChannelRuntime(**kwargs)

    def test_comando_accodato_e_ritirato_una_volta_sola(self):
        rt = self._runtime()
        accodato = rt.queue("cam4", "off", note="pausa")
        self.assertEqual(accodato["command"], "off")
        self.assertEqual(accodato["id"], "cmd-1")
        primo = rt.pending("cam4")
        self.assertEqual([c["command"] for c in primo], ["off"])
        self.assertEqual(rt.pending("cam4"), [], "un comando consegnato non si ripete")

    def test_comando_sconosciuto_non_entra_in_coda(self):
        from shared.channel import ChannelError
        rt = self._runtime()
        with self.assertRaises(ChannelError):
            rt.queue("cam4", "banna-tutti")
        self.assertEqual(rt.pending("cam4"), [])

    def test_comando_mai_ritirato_scade(self):
        rt = self._runtime(command_ttl_s=60)
        rt.queue("cam4", "off")
        self.adesso += 61
        coda = rt.pending("cam4")
        self.assertEqual([c for c in coda if c.get("command")], [])
        self.assertEqual(coda[0].get("_expired"), 1, "lo scaduto si segnala, non si esegue")

    def test_la_coda_ha_un_tetto(self):
        rt = self._runtime(max_commands=3)
        for _ in range(5):
            rt.queue("cam4", "auto")
        comandi = rt.pending("cam4")
        self.assertEqual([c["id"] for c in comandi], ["cmd-3", "cmd-4", "cmd-5"])

    def test_stato_ripulito_e_segnalato_vecchio(self):
        rt = self._runtime(state_stale_s=60)
        rt.report("cam4", {"mode": "auto", "active": True, "rate": 3.5,
                           "token": "segretissimo", "note": "x" * 500})
        stato = rt.state("cam4")
        self.assertEqual(stato["mode"], "auto")
        self.assertTrue(stato["active"])
        self.assertNotIn("token", stato, "lo stato non si porta dietro segreti")
        self.assertEqual(len(stato["note"]), 200)
        self.assertFalse(stato["stale"])
        self.adesso += 61
        self.assertTrue(rt.state("cam4")["stale"])

    def test_describe_non_consuma_la_coda(self):
        rt = self._runtime()
        rt.queue("cam4", "on")
        rt.report("cam4", {"mode": "off", "active": False})
        descrizione = rt.describe()
        self.assertEqual(descrizione["cam4"]["pending_commands"], 1)
        self.assertEqual(descrizione["cam4"]["state"]["mode"], "off")
        self.assertEqual(descrizione["cam4"]["pending_commands"], rt.describe()["cam4"]["pending_commands"])
        self.assertEqual(len(rt.pending("cam4")), 1, "describe non svuota la coda")


class RuntimeWiringTests(unittest.TestCase):
    """Le route che rendono il contratto raggiungibile da terminale e dal driver."""

    @classmethod
    def setUpClass(cls):
        cls.source = MAIN_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        cls.functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def test_route_stato_e_comandi(self):
        for nome in ("channel_state", "channel_commands"):
            with self.subTest(nome=nome):
                self.assertIn(nome, self.functions)
                self.assertIn("_channel_error()", ast.unparse(self.functions[nome]))

    def test_il_get_consuma_il_post_accoda(self):
        body = ast.unparse(self.functions["channel_commands"])
        self.assertIn("pending", body)
        self.assertIn("queue", body)
        self.assertIn("GET", body)

    def test_lo_stato_espone_il_runtime(self):
        body = ast.unparse(self.functions["channel_status"])
        self.assertIn("channel_runtime.describe()", body)

    def test_un_comando_non_previsto_e_un_400(self):
        body = ast.unparse(self.functions["channel_commands"])
        self.assertIn("400", body)


if __name__ == "__main__":
    unittest.main()
