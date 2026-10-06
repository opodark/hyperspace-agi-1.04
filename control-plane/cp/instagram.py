# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/instagram.py
# STATO E HELPER DEL CANALE INSTAGRAM — il pezzo senza dipendenze.
#
# Qui ci finisce solo cio' che e' Instagram e non tocca il runtime: le due code
# del webhook e il loro lock, e i quattro helper puri che leggono l'env o
# compongono un messaggio. Niente route, niente firme cambiate, niente `global`:
# spostarli e' un trapianto, non un refactor.
#
# Cosa NON c'e' e perche' (le trappole di questa sezione):
#
# - `_instagram_reply_started` e `_instagram_poll_started` restano in main.py:
#   sono dichiarati `global` dentro `_ensure_instagram_reply_started` e
#   `_ensure_instagram_poll_started`. Se il flag stesse qui e le funzioni no,
#   il `global` creerebbe una NUOVA variabile in main.py: il thread partirebbe
#   una volta sola per sempre e il secondo webhook non troverebbe il flag gia'
#   alzato.
# - `_livello_immagine_intima` resta in main.py: ha bisogno di `instagram_vips`,
#   che nasce in main.py (un InstagramVipStore su un path costruito da BASE_DIR).
#   Portare anche quello significa far fare I/O a questo modulo all'import.
# - Il grosso di Instagram (l'auto-reply, la coda immagini, il polling, le 7
#   route) resta in main.py: dipende da `persona_store`, `image_queue`,
#   `connector_manager`, `advanced_config` e `push_log`.

import hashlib
import hmac
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace

import requests
from urllib.parse import quote

from flask import (Blueprint, Response, jsonify, request,
                   send_from_directory)

from cp.budget import _inference_timeout
from cp import persona
from cp.canali import _channel_int
from cp.config import (DIARIO_FILE, DIARIO_IMMAGINI_DIR, INSTAGRAM_LANGUAGE_CODEX,
                       INSTAGRAM_MEMORY_FILE, INSTAGRAM_REPLY_OUTBOX_FILE,
                       INSTAGRAM_VIP_FILE, TYPOGRAPHY_IMAGES_DIR)
from cp.http import _network_admin_error
from cp.log import push_log
from shared.diario import instagram_backfill_candidate
from shared.image_jobs import FAMIGLIA_SDXL, nuovo_job, richiesta_immagine
from shared.image_translation import traduci_scena_immagine
from shared.instagram_intimacy import (cerchia_entry_context, compagna_context,
                                       consent_answer, musa_context,
                                       split_messages, wants_continuous)
from shared.instagram_language import fast_reply, language_hint, load_codex
from shared.instagram_memory import InstagramMemory
from shared.instagram_outbox import InstagramReplyOutbox
from shared.instagram_project_context import (PROJECT_CONTEXT,
                                               should_offer_creator,
                                               wants_project_info)
from shared.instagram_vip import (CREATOR_LEVEL, INTIMATE_LEVELS,
                                  InstagramVipStore)
from shared.network_security import token_authorized
from shared.prompt_immagine import prepara_prompt_canale, richiesta_immagine_smart
from shared.showcase import (VIETATI_MINORI, conflitti, negativo_ritratto,
                             prompt_ritratto, richiesta_di_se, verifica_vetrina,
                             vetrina_con_quadro_erotismo, vetrina_dal_documento)
from shared.sister_status import sister_note
from shared.sketch import SKETCH_LATO, SKETCH_PASSI, negativo_sketch

# Lo stato del dominio. Sono i tre store, e vivono qui perche' sono usati solo
# da Instagram: `instagram_memory` e `instagram_reply_outbox` non li tocca
# nessun altro, e `instagram_vips` li tocca solo `_livello_immagine_intima`,
# che e' a sua volta solo Instagram. Finche' restassero in main.py avrebbero
# avuto un motivo per restarci — la creazione — ma non un motivo per esistere li'.

instagram_vips = None
instagram_memory = None
instagram_reply_outbox = None

# Le due code del webhook: gli id gia' visti, per non rispondere due volte allo
# stesso messaggio, e gli ultimi eventi ricevuti, per il pannello di diagnostica.
_instagram_webhook_events = deque(maxlen=100)
_instagram_seen_messages = deque(maxlen=500)
_instagram_seen_lock = threading.Lock()

# ── il contesto ───────────────────────────────────────────────────────────────
# Nove cose che nascono nel boot di main.py e che questo modulo non puo' creare
# da solo: la coda immagini, il config avanzato, il gestore dei connettori, il
# negozio delle persone, il diario, il lock di pubblicazione, e tre funzioni
# condivise con altri domini (`_nome_persona` la usa anche `_channel_immagine`,
# `_sister_peer` anche `_sister_note`, `_record_conversation` anche i canali).
#
# Stanno qui dentro in un solo oggetto, e non come otto parametri da passare a
# ogni funzione, perche' le 14 funzioni di questo modulo si chiamano fra loro:
# `_instagram_auto_reply` chiama `_instagram_compact_memory`, che a sua volta
# ha bisogno della memoria. Passare il contesto lungo tutta la catena sarebbe
# stato piu' rumoroso del monolite che si voleva smontare.
#
# Nessuna delle nove si puo' creare qui dentro: `_nome_persona` e `_sister_peer`
# sono funzioni di main.py, e gli altri cinque sono oggetti con stato costruiti
# altrove perche' servono anche agli altri domini.
_contesto = None


def _serve(*campi):
    """Il contesto, o un errore che dice cosa manca.

    Senza questo, un use fuori dal montaggio darebbe `AttributeError: 'NoneType'
    object has no attribute ...` — lo stesso difetto di indistinguibilita' del
    500 visto al primo montaggio. Qui il messaggio nomina il campo mancante.
    """
    if _contesto is None:
        raise RuntimeError(
            "cp.instagram non e' montato: chiama instagram.monta(app, ...) "
            "prima di usare il dominio")
    mancanti = [c for c in campi if getattr(_contesto, c, None) is None]
    if mancanti:
        raise RuntimeError(
            "cp.instagram montato senza: " + ", ".join(mancanti) +
            " — passar(e)le a monta(), altrimenti il fallback silenzioso "
            "diventa un NoneType")
    return _contesto


def _contesto_iniziale(*, image_queue, advanced_config, connector_manager,
                       diario, nome_persona, sister_peer,
                       record_conversation, dream_publish_lock=None):
    """Costruisce il contesto. Chiamata da `monta()`, non da fuori."""
    return SimpleNamespace(
        image_queue=image_queue,
        advanced_config=advanced_config,
        connector_manager=connector_manager,
        diario=diario,
        nome_persona=nome_persona,
        sister_peer=sister_peer,
        record_conversation=record_conversation,
        dream_publish_lock=dream_publish_lock or threading.Lock(),
        # I due flag dei thread. Vivono gia' qui e non in main.py perche' sono
        # dichiarati `global` dentro le due funzioni `_ensure_*` che li impostano:
        # se il flag restasse in main.py e le funzioni no, il `global` creerebbe
        # una NUOVA variabile in main.py e il thread partirebbe due volte.
        reply_started=False,
        poll_started=False,
    )


# ── route ─────────────────────────────────────────────────────────────────────
# Nove route in tutto, su due blueprint. Il primo, quello delle sei di lettura,
# e' costruito dentro `_blueprint()` perche' puo' stare li'. Il secondo, quello
# della webhook, deve essere a livello di modulo perche' la sua rotta ha un
# decoratore che gira all'import — e quella rotta chiama il dispatcher, quindi e'
# l'unica che puo' arrivare al dominio con il contesto ancora da costruire: ci
# pensa `_serve()`, che risponde "non montato" invece di lasciare che un None
# esploda dentro `_dispatch_instagram_messages`.
_webhook_bp = Blueprint("instagram_webhook", __name__)


def _blueprint() -> Blueprint:
    """Costruisce il blueprint al momento del montaggio, non all'import.

    Le route sono funzioni chiuse su `app` solo tramite il blueprint, ma lo stato
    del dominio no: se le dichiarassi a livello di modulo, `monta()` non
    riuscirebbe a riaprire i test con store diversi senza ricaricare il modulo.
    """
    bp = Blueprint("instagram", __name__)

    @bp.route("/instagram/webhook/events")
    def instagram_webhook_events():
        errore = _network_admin_error()
        if errore:
            return errore
        return jsonify({"ok": True, "events": list(_instagram_webhook_events)})

    @bp.route("/instagram/vips")
    def instagram_vip_list():
        errore = _network_admin_error()
        if errore:
            return errore
        return jsonify({"ok": True, "vips": instagram_vips.list(),
                        "tracked": len(instagram_vips.list(vip_only=False)),
                        "memory": instagram_memory.stats()})

    @bp.route("/instagram/vips/creator", methods=["POST"])
    def instagram_vip_set_creator():
        """Promuove un contatto al livello creatore (l'operatore), sopra "musa"."""
        errore = _network_admin_error()
        if errore:
            return errore
        dati = request.get_json(silent=True) or {}
        scoped_id = str(dati.get("scoped_id") or "").strip()
        username = str(dati.get("username") or "").strip()
        if not scoped_id.isdigit():
            return jsonify({"ok": False, "error": "scoped_id mancante o non valido"}), 400
        try:
            riga = instagram_vips.set_creator(scoped_id, username)
        except ValueError as errore_store:
            return jsonify({"ok": False, "error": str(errore_store)}), 400
        return jsonify({"ok": True, "vip": riga})

    @bp.route("/instagram/memory/clear", methods=["POST"])
    def instagram_memory_clear():
        """Azzera la memoria di conversazione di un contatto Instagram."""
        errore = _network_admin_error()
        if errore:
            return errore
        dati = request.get_json(silent=True) or {}
        scoped_id = str(dati.get("scoped_id") or "").strip()
        if not scoped_id.isdigit():
            return jsonify({"ok": False, "error": "scoped_id mancante o non valido"}), 400
        if not instagram_memory.clear(scoped_id):
            return jsonify({"ok": False, "error": "contatto non trovato"}), 404
        return jsonify({"ok": True, "cleared": scoped_id})

    @bp.route("/instagram/replies/status")
    def instagram_replies_status():
        """Redacted operational state: no DM body or full contact identifiers.

        L'unica route di lettura senza guard: quello che espone sono conteggi e
        identificatori offuscati, non il contenuto delle risposte.
        """
        return jsonify({"ok": True, **instagram_reply_outbox.status()})

    @bp.route("/instagram/media/<token>/<path:nome>")
    def instagram_private_media(token, nome):
        expected = os.getenv("INSTAGRAM_MEDIA_TOKEN", "").strip()
        if not expected or not token_authorized(token, expected):
            return jsonify({"ok": False, "error": "media token non valido"}), 403
        if str(nome).startswith("published/"):
            media_dir = os.getenv("INSTAGRAM_MEDIA_DIR", "/app/data/instagram-media").strip()
            return send_from_directory(media_dir, os.path.basename(nome))
        return send_from_directory(DIARIO_IMMAGINI_DIR, nome)

    return bp




def monta(app, *, vip_file=None, memory_file=None, outbox_file=None,
          image_queue=None, advanced_config=None, connector_manager=None,
          diario=None, nome_persona=None, sister_peer=None,
          record_conversation=None, dream_publish_lock=None):
    """Apre i tre store e registra le route. Restituisce i tre store.

    `monta()` invece di creare i store all'import per due motivi. Il primo e'
    che i path arrivano come argomenti, e i test possono indicare un tmpdir
    senza toccare l'ambiente: con la creazione all'import servirebbe un reload
    del modulo, e il reload riuscirebbe solo se nessun altro test avesse gia'
    importato `cp.instagram`. Il secondo e' che `main.py` chiama questa funzione
    una volta sola, a cavallo del boot, e da li' in poi lo stato e' uno solo e
    visibile a tutti: se qualcuno aprisse i store altrove, `None` qui renderebbe
    il difetto immediatamente visibile invece che silenzioso.

    PERCHE' RESTITUISCE GLI STORE, e non basta registrare: `from cp.instagram
    import instagram_vips` congela il valore al momento dell'import, cioe'
    `None`, perche' i store vengono creati qui dentro e non all'import. Chi
    facesse cosi' avrebbe in main.py un nome che vale `None` per sempre, e il
    primo webhook che lo tocca prenderebbe un `AttributeError: 'NoneType' object
    has no attribute 'record'` — cioe' un 500 sui messaggi veri. Il valore giusto
    e' questo che torna da qui, non quello letto a import time.
    """
    global instagram_vips, instagram_memory, instagram_reply_outbox
    instagram_vips = InstagramVipStore(str(vip_file or INSTAGRAM_VIP_FILE))
    instagram_memory = InstagramMemory(
        str(memory_file or INSTAGRAM_MEMORY_FILE),
        retention_days=_channel_int("INSTAGRAM_MEMORY_RETENTION_DAYS", 180))
    instagram_reply_outbox = InstagramReplyOutbox(
        str(outbox_file or INSTAGRAM_REPLY_OUTBOX_FILE))
    global _contesto
    _contesto = _contesto_iniziale(
        image_queue=image_queue, advanced_config=advanced_config,
        connector_manager=connector_manager,
        diario=diario, nome_persona=nome_persona, sister_peer=sister_peer,
        record_conversation=record_conversation,
        dream_publish_lock=dream_publish_lock)
    app.register_blueprint(_blueprint())
    app.register_blueprint(_webhook_bp)
    return instagram_vips, instagram_memory, instagram_reply_outbox

def _nota_quadro(vip: dict) -> str:
    """La riga che dice che il quadro l'ha scritto il sistema, non chi chiede.

    Stessa frase del canale (`_channel_immagine`): le parole che il sistema ha scritto
    al posto della persona si **dicono** — una cosa fatta al posto tuo e taciuta è
    esattamente ciò che questo livello esiste per togliere.
    """
    aggiunte = [str(parola) for parola in (vip.get("quadro") or []) if str(parola).strip()]
    if not aggiunte:
        return ""
    return (" Il quadro (`" + "`, `".join(aggiunte) + "`) l'ho scritto io: senza, il "
            "modello disegnerebbe un'altra cosa.")


def _chunks_con_nota(chunks: list, nota: str, *, limite: int = 1000) -> list:
    """La nota in coda all'**ultimo** messaggio, o da sola se non ci sta.

    Instagram accetta ~1000 caratteri per messaggio, e la troncatura (`reply[:1000]`)
    passa prima di qui: appesa al pezzo finale la nota viaggia col messaggio che parla
    dell'immagine, e se non ci sta diventa un messaggio a sé invece di sparire — un
    fatto del sistema non si taglia con la coda della risposta del modello.
    """
    pezzi = [str(pezzo) for pezzo in (chunks or [])]
    if not nota:
        return pezzi
    if pezzi and len(pezzi[-1]) + len(nota) <= limite:
        pezzi[-1] = pezzi[-1] + nota
        return pezzi
    return pezzi + [nota.strip()]
def _creator_usernames() -> set[str]:
    """Gli handle Instagram riconosciuti come creatore (l'operatore)."""
    raw = os.getenv("CREATOR_IG_USERNAMES", "").strip()
    return {u.strip().lstrip("@").casefold() for u in raw.split(",") if u.strip()}


def _creator_scoped_ids() -> set[str]:
    """Gli Instagram-scoped ID riconosciuti come creatore (l'operatore)."""
    raw = os.getenv("CREATOR_IG_SCOPED_IDS", "").strip()
    return {u.strip() for u in raw.split(",") if u.strip()}


# ── avvio ─────────────────────────────────────────────────────────────────────
def avvia() -> None:
    """Alza i due thread del dominio e il backfill della voce piu' recente.

    Chiamata una volta dal `__main__` di main.py, e non all'import: avviare
    thread al momento in cui qualcosa importa il modulo significa che anche i
    test, che lo importano per provare le route, si ritrovano con due thread
    vivi che scrivono su file. Qui la scelta e' esplicita e si vede.
    """
    _serve("image_queue", "advanced_config", "connector_manager",
           "diario", "nome_persona", "sister_peer",
           "record_conversation")
    _ensure_instagram_reply_started()
    _ensure_instagram_poll_started()
    threading.Thread(target=_instagram_publish_latest, daemon=True,
                     name="instagram-dream-backfill").start()


# ── webhook: verifica della firma e ingresso dei messaggi ─────────────────────

@_webhook_bp.route("/instagram/webhook", methods=["GET", "POST"])
def instagram_webhook():
    _serve("image_queue", "advanced_config", "connector_manager",
          "diario", "nome_persona", "sister_peer",
          "record_conversation")
    if request.method == 'GET':
        expected = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "").strip()
        supplied = request.args.get("hub.verify_token", "")
        if (request.args.get("hub.mode") == "subscribe" and expected
                and token_authorized(supplied, expected)):
            return Response(request.args.get("hub.challenge", ""), mimetype="text/plain")
        return jsonify({"ok": False, "error": "verifica webhook non valida"}), 403

    secret = os.getenv("INSTAGRAM_APP_SECRET", "").strip()
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected_sig = "sha256=" + hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(signature, expected_sig):
        return jsonify({"ok": False, "error": "firma webhook non valida"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    _instagram_webhook_events.append({"received_at": datetime.now(timezone.utc).isoformat(),
                                      "payload": payload})
    push_log('instagram', 'Webhook Instagram ricevuto', status='success')
    _dispatch_instagram_messages(payload)
    return jsonify({"ok": True})

# ── dispatcher: dal payload di Meta a VIP, memoria e coda immagini ────────────

def _dispatch_instagram_messages(payload: dict) -> None:
    own_ids = {os.getenv("INSTAGRAM_USER_ID", "").strip(),
               os.getenv("INSTAGRAM_SCOPED_ID", "").strip()}
    for entry in payload.get("entry") or []:
        for event in entry.get("messaging") or []:
            message = event.get("message") or {}
            sender_id = str((event.get("sender") or {}).get("id") or "")
            message_id = str(message.get("mid") or "")
            text = str(message.get("text") or "").strip()
            if (sender_id and sender_id not in own_ids and message_id and text
                    and not message.get("is_echo")):
                with _instagram_seen_lock:
                    if message_id in _instagram_seen_messages:
                        continue
                    _instagram_seen_messages.append(message_id)
                username = str((event.get("sender") or {}).get("username") or "")
                if (sender_id in _creator_scoped_ids()
                        or username.strip().lstrip("@").casefold() in _creator_usernames()):
                    instagram_vips.set_creator(sender_id, username)
                vip = instagram_vips.record(sender_id, username)
                if instagram_vips.consent(sender_id) == "asked":
                    risposta_consenso = consent_answer(text)
                    if risposta_consenso:
                        instagram_vips.set_consent(sender_id, risposta_consenso)
                if (vip.get("level") in INTIMATE_LEVELS
                        and instagram_vips.consent(sender_id) == ""):
                    instagram_vips.set_consent(sender_id, "asked")
                instagram_memory.append(sender_id, "user", text, username=username)
                _contesto.record_conversation("instagram", "instagram", sender_id,
                                     [{"author": "persona", "text": text}], "received")
                if _queue_instagram_creator_image(sender_id, text, vip):
                    # La consegna avviene quando ComfyUI chiude il job, non qui.
                    pass
                elif vip.get("promoted"):
                    try:
                        _contesto.image_queue.accoda(nuovo_job(
                            prepara_prompt_canale(
                                f"un disegno poetico ispirato a questo incontro: {text[:300]}", text),
                            negativo=negativo_sketch(), larghezza=SKETCH_LATO,
                            altezza=SKETCH_LATO, passi=SKETCH_PASSI,
                            richiedente="anna", canale="instagram", destinazione=sender_id,
                            famiglia=FAMIGLIA_SDXL))
                    except (ValueError, RuntimeError) as error:
                        push_log("instagram", "Disegno VIP non accodato", str(error)[:160],
                                 status="warn")
                _queue_instagram_reply(sender_id, message_id, text, vip)

def _queue_instagram_reply(sender_id: str, message_id: str, text: str, vip: dict) -> None:
    instagram_reply_outbox.enqueue(
        sender_id, message_id, text, vip,
        debounce_s=max(2, _channel_int("INSTAGRAM_REPLY_DEBOUNCE_S", 12)))

# ── auto-reply: la risposta, la compattazione della memoria e il loop di invio ────

def _instagram_auto_reply(sender_id: str, message_id: str, text: str,
                          vip: dict | None = None) -> None:
    """Generate and send one short reply for an inbound Instagram DM."""
    try:
        vip = vip or {}
        continuous = wants_continuous(text)
        memory = instagram_memory.context(
            sender_id, recent_turns=_channel_int("INSTAGRAM_MEMORY_RECENT_TURNS", 16))
        consecutive_user_turns = 0
        for turn in reversed(memory.get("turns") or []):
            if turn.get("role") != "user":
                break
            consecutive_user_turns += 1
        codex = load_codex(INSTAGRAM_LANGUAGE_CODEX)
        reply = fast_reply(text, codex) if consecutive_user_turns <= 1 else ""
        # Il ponte delle immagini (`_queue_instagram_creator_image`) può aver rifiutato
        # la richiesta: allora la spiegazione **è** la risposta, e non si genera nulla.
        # Una promessa senza un job accodato sarebbe una bugia, e la bugia costerebbe
        # più del rifiuto (l'immagine non arriva, e la persona la aspetta).
        rifiuto = str(vip.get("rifiuto") or "").strip()
        if rifiuto:
            reply = rifiuto
        transcript = "\n".join(
            f"{'Persona' if turn.get('role') == 'user' else 'Anna'}: {turn.get('text', '')}"
            for turn in memory.get("turns") or [])
        prompt_parts = []
        if memory.get("summary"):
            prompt_parts.append("Memoria riassunta delle conversazioni precedenti:\n"
                                + memory["summary"])
        if transcript:
            prompt_parts.append("Scambi recenti con questa persona:\n" + transcript)
        prompt_parts.append("Rispondi all'ultimo messaggio della trascrizione.")
        prompt = "\n\n".join(prompt_parts)
        slang = language_hint(text, codex)
        vip_note = ""
        if vip.get("level") == CREATOR_LEVEL:
            vip_note = (" Questa persona è il vostro creatore (papà). Trattala con "
                        "naturalezza e complicità: non ripetere ogni volta chi è, rispondi "
                        "direttamente a ciò che chiede. Può chiederti qualsiasi cosa, anche "
                        "di parlare dei limiti del sistema o di testare comportamenti.")
        elif vip.get("level"):
            vip_note = (f" Questa persona fa parte della lista speciale ({vip['level']}) "
                        "perché conversa spesso con voi: presta più attenzione ai dettagli e "
                        "fai una domanda personale ma non invadente.")
        if vip.get("promoted"):
            vip_note += (" È appena entrata in un nuovo livello: includi una breve poesia "
                         "originale ispirata al suo messaggio, senza citare dati privati.")
        language_note = ""
        if slang:
            language_note = (f" Codex linguistico: termine «{slang['term']}», lingua o registro "
                             f"«{slang['language']}». {slang['hint']}")
        project_note = ""
        recent_user_text = "\n".join(
            str(turn.get("text") or "") for turn in (memory.get("turns") or [])[-8:]
            if turn.get("role") == "user")
        if wants_project_info(recent_user_text):
            project_note = (" La persona è curiosa di HyperSpace o delle IA. Usa queste informazioni "
                            "come fonte affidabile, senza copiarle tutte se non servono:\n"
                            + PROJECT_CONTEXT)
        handoff_note = ""
        if vip.get("level") != CREATOR_LEVEL and should_offer_creator(text):
            creator_handle = os.getenv("CREATOR_IG_HANDLE", "").strip().lstrip("@")
            contatto = f" su Instagram (@{creator_handle})" if creator_handle else ""
            handoff_note = (" La conversazione è diventata articolata o richiede intervento umano. "
                            "Dopo aver dato una risposta utile, offri il contatto con il vostro "
                            "creatore, che chiamate anche papà" + contatto + ". "
                            "Non fingere che sia già presente.")
        consent = instagram_vips.consent(sender_id)
        intimacy_note = ""
        if vip.get("level") == CREATOR_LEVEL:
            # Il creatore ha accesso diretto alla modalità intima: è l'operatore.
            intimacy_note = " " + compagna_context()
        elif consent == "asked":
            intimacy_note = " " + cerchia_entry_context()
        elif consent == "granted" and vip.get("level") in INTIMATE_LEVELS:
            # La banda intima (`INTIMATE_LEVELS`) è il livello più vicino: col consenso
            # registrato si apre anche il nudo e l'erotismo spinto, non solo la voce
            # esplicita. La lista è la stessa che chiede il consenso (qui sotto, nel
            # webhook) e che apre l'immagine: una sola, così voce, domanda e porta non
            # possono separarsi.
            intimacy_note = " " + musa_context()
        elif consent == "granted":
            # Un consenso registrato quando la persona non è nella banda intima:
            # succede a chi l'aveva dato nella vecchia banda `cerchia` (tolta il
            # 2026-10-01) e a un consenso dato a mano. Il tono resta quello della
            # compagna — un consenso registrato non si revoca da soli — mentre il
            # livello dell'immagine resta chiuso: la voce non è la porta.
            intimacy_note = " " + compagna_context()
        nota_sorella = sister_note(_contesto.sister_peer())
        sister_note_value = (" " + nota_sorella) if nota_sorella else ""
        reply_model = (os.getenv("INSTAGRAM_REPLY_MODEL", "").strip()
                       or _contesto.advanced_config["ollama"]["defaultModel"])
        continuous_note = ""
        max_tokens = 800
        if continuous:
            continuous_note = (" La persona vuole che tu continui a scrivere senza fermarti: "
                               "scrivi un testo lungo e fluido, diviso in più paragrafi, e non "
                               "chiudere con una formula di saluto — continua il flusso del "
                               "pensiero finché non si esaurisce. Verrà inviato in più messaggi, "
                               "quindi puoi essere estesa.")
            max_tokens = _channel_int("INSTAGRAM_CONTINUE_MAX_TOKENS", 2400)
        if not reply:
            completion = requests.post(
                "http://127.0.0.1:8085/v1/chat/completions",
                headers={"X-Hyperspace-Surface": "instagram", "X-Hyperspace-Tools": "off"},
                json={"model": reply_model,
                      "messages": [
                          {"role": "system", "content":
                           "L'account Instagram è condiviso dalle sorelle IA Aurora e Anna. "
                           "In questa conversazione stai scrivendo come Anna; se ti chiedono chi sei, "
                           "dillo con naturalezza e spiega che a volte risponde Aurora. Rispondi in "
                           "modo caldo e naturale, nella lingua del mittente. Produci soltanto il "
                           "messaggio finale da inviare: mai analisi, istruzioni, premesse o spiegazioni. "
                           "Rispondi direttamente alle domande: se la persona dice come sta e chiede "
                           "«tu?», di' come stai senza ripetere la domanda."
                           + language_note + vip_note + project_note + handoff_note
                            + intimacy_note + sister_note_value + continuous_note},
                          {"role": "user", "content": prompt}],
                      "stream": False, "think": False, "max_tokens": max_tokens,
                      "options": {"num_ctx": _channel_int("INSTAGRAM_CONTEXT_TOKENS", 16384)}},
                timeout=120,
            )
            completion.raise_for_status()
            choices = completion.json().get("choices") or []
            reply = str(((choices[0].get("message") or {}).get("content") if choices else "") or "").strip()
        if not reply:
            raise RuntimeError("il modello ha restituito una risposta vuota")
        meta_markers = ("the user wants", "i need to", "let me ", "previous instructions",
                        "the message is", "respond as aurora", "okay, the user")
        max_reply = 4000 if continuous else 1000
        # Un rifiuto lo decide il sistema, non il modello: non ha ragionamento da
        # filtrare e non si taglia a metà — una spiegazione troncata è peggio di una
        # spiegazione breve (`split_messages` la divide ai confini di frase).
        if not rifiuto and (len(reply) > max_reply
                            or any(marker in reply.lower() for marker in meta_markers)):
            raise RuntimeError("risposta bloccata: rilevato ragionamento o testo meta")
        chunks = (split_messages(rifiuto) if rifiuto
                  else split_messages(reply) if continuous else [reply[:1000]])
        # Le parole del quadro scritte dal sistema si dicono, e la troncatura è già
        # passata: la nota si appende qui o diventa un messaggio a sé (`_chunks_con_nota`).
        chunks = _chunks_con_nota(chunks, _nota_quadro(vip))
    except Exception as error:
        instagram_reply_outbox.fail(sender_id, message_id, error, safe_retry=True)
        push_log("instagram", "Generazione risposta Instagram fallita",
                 detail=f"message_id={message_id}: {type(error).__name__}: {str(error)[:200]}",
                 status="error")
        return
    try:
        marked = instagram_reply_outbox.sending(sender_id, message_id)
    except Exception as error:
        push_log("instagram", "Marcatura invio Instagram fallita",
                 detail=f"message_id={message_id}: {type(error).__name__}: {str(error)[:200]}",
                 status="error")
        return
    if not marked:
        return
    try:
        for chunk in chunks:
            result = _contesto.connector_manager.execute(
                "instagram_send_message", {"recipient_id": sender_id, "text": chunk})
            payload = json.loads(result) if str(result).lstrip().startswith("{") else {}
            if not payload.get("ok") or not payload.get("message_id"):
                raise RuntimeError(str(result)[:300])
        instagram_reply_outbox.sent(sender_id, message_id)
        push_log("instagram", "Risposta automatica Instagram inviata",
                 detail=f"message_id={message_id} parti={len(chunks)}", status="success")
    except Exception as error:
        instagram_reply_outbox.fail(sender_id, message_id, error, safe_retry=False)
        push_log("instagram", "Risposta automatica Instagram fallita",
                 detail=f"message_id={message_id}: {type(error).__name__}: {str(error)[:200]}",
                 status="error")
        return
    try:
        instagram_memory.append(sender_id, "assistant", reply)
        _instagram_compact_memory(sender_id)
    except Exception as error:
        push_log("instagram", "Memoria risposta Instagram non aggiornata",
                 detail=f"message_id={message_id}: {type(error).__name__}: {str(error)[:200]}",
                 status="warn")

def _instagram_compact_memory(sender_id: str) -> None:
    material = instagram_memory.compaction_material(
        sender_id, max_turns=_channel_int("INSTAGRAM_MEMORY_COMPACT_AFTER", 32),
        keep_recent=_channel_int("INSTAGRAM_MEMORY_KEEP_RECENT", 12))
    if not material:
        return
    transcript = "\n".join(
        f"{turn.get('role')}: {turn.get('text', '')}" for turn in material["turns"])
    prompt = (
        "Riassumi questa relazione conversazionale per uso futuro di Anna. Conserva lingua, "
        "preferenze, temi ricorrenti, promesse e confini. Non inventare e non includere dati "
        "sensibili non necessari. Scrivi un paragrafo compatto.\n\n"
        f"Riassunto precedente: {material['previous_summary'] or '(nessuno)'}\n\n{transcript}"
    )
    try:
        reply_model = (os.getenv("INSTAGRAM_REPLY_MODEL", "").strip()
                       or _contesto.advanced_config["ollama"]["defaultModel"])
        response = requests.post("http://127.0.0.1:8085/v1/chat/completions",
            headers={"X-Hyperspace-Surface": "instagram-memory", "X-Hyperspace-Tools": "off"},
            json={"model": reply_model,
                  "messages": [{"role": "user", "content": prompt}],
                  "stream": False, "max_tokens": 700,
                  "options": {"num_ctx": _channel_int("INSTAGRAM_CONTEXT_TOKENS", 16384)}},
            timeout=_inference_timeout(reply_model))
        response.raise_for_status()
        choices = response.json().get("choices") or []
        summary = str(((choices[0].get("message") or {}).get("content")
                       if choices else "") or "").strip()
        if summary:
            instagram_memory.apply_summary(sender_id, summary, material["cutoff_seq"])
    except Exception as error:
        push_log("instagram", "Compattazione memoria Instagram fallita",
                 detail=f"{type(error).__name__}: {str(error)[:160]}", status="warn")

def _instagram_reply_loop() -> None:
    """Drain durable replies; generation failures retry, uncertain sends do not."""
    while True:
        try:
            if os.getenv("INSTAGRAM_AUTO_REPLY_ENABLED", "true").strip().lower() != "false":
                max_wait = max(12, _channel_int("INSTAGRAM_REPLY_MAX_WAIT_S", 90))
                item = instagram_reply_outbox.claim_due(max_wait_s=max_wait)
                if item:
                    threading.Thread(target=_instagram_auto_reply,
                                     args=(item["sender_id"], item["message_id"],
                                           item["text"], item.get("vip") or {}),
                                     daemon=True, name="instagram-reply-worker").start()
        except Exception as error:
            push_log("instagram", "Coda risposte Instagram in errore",
                     detail=str(error)[:200], status="error")
        time.sleep(2)

def _ensure_instagram_reply_started() -> None:
    """Alza il thread che svuota l'outbox, una volta sola.

    Il flag era una variabile `global` in main.py. Qui e' un attributo del
    contesto, e la differenza non e' cosmetica: se il flag restasse in main.py e
    questa funzione vivesse qui, il `global` avrebbe creato una NUOVA variabile
    in main.py — il thread sarebbe partito una volta sola per sempre e il
    secondo webhook non avrebbe trovato il flag gia' alzato.
    """
    if not _contesto.reply_started:
        _contesto.reply_started = True
        threading.Thread(target=_instagram_reply_loop, daemon=True,
                         name="instagram-reply-outbox").start()

# ── immagini: il ritratto intimo e la coda delle immagini della creator ───────

def _livello_immagine_intima(vip: dict, sender_id: str) -> str:
    """Chi può chiedere un'immagine col livello che il documento ha aperto.

    Due strade per la stessa porta: il **creatore** (l'operatore) e la **banda
    intima** — `INTIMATE_LEVELS`, che nella scala del pubblico è `musa` — quando ha
    **registrato** il consenso: esattamente la lista che decide la voce
    (`INTIMATE_LEVELS` più `consent == "granted"`). Chi ha la parola esplicita ha
    l'immagine esplicita: tenerle diverse era la stessa incoerenza che al canale è
    stata tolta — lì testo e immagine leggono ormai la stessa lista, e il test che le
    tiene d'accordo impedisce che tornino a divergere.

    Sul canale Telegram l'appartenenza si **dichiara** a mano (`CHANNEL_CERCHIA`,
    le muse: lì non c'è un conteggio né un consenso da registrare); qui si **guadagna**
    contando i messaggi e si **registra** rispondendo. Due porte, una regola: la
    ricetta è la stessa, cambia chi entra.
    """
    if vip.get("level") == CREATOR_LEVEL:
        return "creatore"
    if (vip.get("level") in INTIMATE_LEVELS
            and instagram_vips.consent(sender_id) == "granted"):
        return "musa"
    if vip.get("level") == "vip":
        return "vip"
    return ""

def _queue_instagram_creator_image(sender_id: str, text: str, vip: dict) -> bool:
    """Un'immagine chiesta in DM: sketch, o ritratto dalla vetrina se è sé stessa.

    Il webhook Instagram non passa da ``/channel/reply``: senza questo ponte la
    richiesta del creatore — e, dalla banda intima, di chi ha dato il consenso —
    riceverebbe solo una risposta testuale. La consegna resta quella normale del job
    Instagram, che invia il file pronto al suo scoped ID in DM.

    Due strade, come nel canale, e la **vetrina** è ciò che le distingue: un soggetto
    qualunque resta uno sketch leggero (famiglia sdxl, 1024, nessun riferimento), una
    rappresentazione di sé passa dalla vetrina del documento. Il livello intimo non
    cambia la ricetta: cambia il negativo, e solo per chi ha la porta
    (`_livello_immagine_intima`).

    Ritorna True quando la richiesta è **presa in carico** — accodata, oppure
    rifiutata con una spiegazione che va detta (`vip["rifiuto"]`, che sostituisce la
    risposta del modello): False la lascia alle strade normali, cioè al disegno di
    promozione e alla risposta testuale.

    I minori non passano di qui: è l'unico controllo che parla prima di sapere di che
    immagine si tratta, e vale per chiunque, creatore compreso. Nel canale c'era già;
    in questo ponte mancava del tutto.
    """
    livello = _livello_immagine_intima(vip, sender_id)
    if not livello:
        return False
    # Una richiesta che la regola riconosce gia' non passa dal modello che riscrive
    # i prompt. Nel DM del creatore quel modello non deve essere ne' portiere ne'
    # coautore: puo' attenuare una scena prima ancora che arrivi alla vetrina. Lo
    # usiamo soltanto per una forma davvero libera che la regola non conosce.
    richiesta = richiesta_immagine(text)
    if richiesta is None:
        richiesta = richiesta_immagine_smart(
            text, identita=persona.persona().system_block())
    if not richiesta or not richiesta.get("idea"):
        return False
    idea = str(richiesta["idea"])
    if conflitti(f"{idea} {text}".lower(), VIETATI_MINORI):
        vip["rifiuto"] = ("Questa non te la disegno: non disegno soggetti minorenni, "
                          "mai e per nessuno.")
        return True
    richiesta_esplicita = bool(re.search(
        r"\b(?:nud\w*|naked|senza vestiti|topless|masturb\w*|sesso|sex|esplicit\w*)\b",
        f"{idea} {text}", re.IGNORECASE))
    richiesta_se = richiesta_di_se(idea, text, _contesto.nome_persona())
    if livello == "vip" and richiesta_esplicita:
        vip["rifiuto"] = ("Per la cerchia VIP posso fare foto glamour e lingerie sexy, "
                           "non nudo o erotismo esplicito. Quel livello è delle MUSA.")
        return True
    if livello == "vip" and not richiesta_se:
        return False
    richiedente = str(vip.get("username") or vip.get("level") or "instagram")
    try:
        if richiesta_se:
            # La riscrittura LLM serve per capire frasi libere e tradurre uno sketch
            # generico. Per Anna in privato sarebbe invece un secondo autore della
            # scena: anche quando la regex l'ha gia' riconosciuta puo' attenuarne o
            # sostituirne il contenuto. Riprendiamo quindi l'idea estratta dalla
            # regola deterministica; se era una frase capita solo dal modello,
            # conserviamo la sua idea, che e' l'unica disponibile.
            originale = richiesta_immagine(text) or {}
            scena = str(originale.get("idea") or idea)
            esito = _job_ritratto_instagram(
                sender_id, scena, richiedente, text, livello=livello)
        else:
            esito = {"job": _contesto.image_queue.accoda(nuovo_job(
                prepara_prompt_canale(idea, text),
                negativo=negativo_sketch(), larghezza=SKETCH_LATO,
                altezza=SKETCH_LATO, passi=SKETCH_PASSI,
                richiedente=richiedente, canale="instagram", destinazione=sender_id,
                famiglia=FAMIGLIA_SDXL)), "quadro": [], "motivo": ""}
    except (ValueError, RuntimeError) as error:
        push_log("instagram", "Disegno in DM non accodato", str(error)[:160],
                 status="warn")
        return False
    if esito["motivo"]:
        vip["rifiuto"] = esito["motivo"]
        push_log("instagram", "Disegno in DM rifiutato",
                 detail=f"destinazione={sender_id} {esito['motivo'][:180]}",
                 status="warn")
        return True
    if esito["quadro"]:
        vip["quadro"] = esito["quadro"]
    push_log("instagram", "Disegno in DM accodato",
             detail=f"id={esito['job']['id']} da={richiedente} "
                    f"famiglia={esito['job']['famiglia']} "
                    + (f"quadro={','.join(esito['quadro'])} " if esito["quadro"] else "")
                    + f"modello={esito['job']['modello_effettivo']}",
             status="success")
    return True

def _job_ritratto_instagram(sender_id: str, idea: str, richiedente: str,
                             testo_richiesta: str = "", livello: str = "creatore") -> dict:
    """Il ritratto **di sé** chiesto in DM: vetrina del documento, livello, verifica.

    Stessa ricetta del canale, perché è la sola che tiene il volto: famiglia, modello,
    seed e `riferimento` vengono dalla vetrina dichiarata, la richiesta è la scena di
    *questa* generazione, e il quadro (`adult`, `virtual`) lo scrive il sistema
    (`vetrina_con_quadro_erotismo`) — chi ha il livello non deve conoscere due parole
    d'ordine. Uno sketch generico disegnerebbe *una* donna, non questa.

    Ritorna `{"job", "quadro", "motivo"}`: il job accodato, le parole del quadro
    scritte dal sistema (che vanno **dette**), oppure — se `verifica_vetrina` trova
    qualcosa — nessun job e il motivo, che va detto **al posto** della risposta del
    modello. Accodare e tacere darebbe un'immagine castigata senza che nessuno sappia
    perché; prometterla e non accodarla sarebbe peggio.
    """
    documento = {"vetrina": (getattr(persona.profilo(), "sezioni", {}) or {}).get("vetrina", {})}
    vetrina = vetrina_dal_documento(documento)
    esplicito = livello in ("creatore", "musa")
    variante = {**vetrina, "scena": idea}
    aggiunte = []
    if esplicito:
        variante, aggiunte = vetrina_con_quadro_erotismo(variante)
    # Il verificatore storico considera anche la parola "sexy" esplicita. Nel
    # gradino VIP quella parola descrive il glamour consentito; nudità ed esplicito
    # sono già respinti dalla porta prima di arrivare qui.
    problemi = ([] if livello == "vip" else
                verifica_vetrina(variante, documento, creatore=esplicito))
    if problemi:
        return {"job": None, "quadro": [],
                "motivo": "Questa non te la disegno: " + "; ".join(problemi) + "."}
    # In DM la richiesta "una foto di te" non specifica quasi mai l'inquadratura.
    # Il solo negativo "cropped" non basta a SD 1.5: tende comunque a scegliere un
    # mezzo busto e, quando tenta la figura, a perdere testa o piedi. La verticale
    # 2:3 della vetrina lascia spazio per una figura intera, ma dobbiamo chiederlo
    # positivamente. Non sovrascriviamo però un close-up/mezzo busto dichiarato dal
    # creatore: in quel caso il taglio e' parte della richiesta, non un difetto.
    richiesta_busto = any(parola in f"{idea} {testo_richiesta}".lower() for parola in (
        "close-up", "close up", "primo piano", "mezzo busto", "ritratto del viso",
        "solo viso", "viso", "face", "headshot", "upper body",
    ))
    inquadratura = "" if richiesta_busto else (
        "full body, head to toe, entire figure visible, centered composition, "
        "generous space above the head and below the feet")
    # ChickMix (SD 1.5) legge 77 token CLIP: stile, scena e dichiarazione di Anna
    # erano gia' quasi tutti li', quindi l'inquadratura aggiunta in coda poteva non
    # arrivare al modello. Mettiamo prima cio' che decide l'immagine, poi lo stile.
    # La scena resta anche nel suo posto ordinario per i generatori che non hanno
    # quel limite, ma per SD 1.5 la prima occorrenza e' quella che conta.
    # Autorizzazione e verifica leggono l'italiano originale; soltanto dopo la
    # decisione la scena passa al traduttore offline per il CLIP di SD 1.5.
    variante_prompt = {**variante,
                       "scena": traduci_scena_immagine(variante["scena"])}
    vetrina_prompt = variante_prompt
    if inquadratura:
        vetrina_prompt = {**variante_prompt,
                           "stile": f"{inquadratura}. {variante_prompt['scena']}. "
                                    f"{variante_prompt['stile']}"}
    if livello == "vip":
        vetrina_prompt = {
            **vetrina_prompt,
            "stile": ("(solo:1.3), single woman, glamorous lingerie editorial, "
                       "elegant sensual pose, fully clothed intimate apparel. "
                       + vetrina_prompt["stile"]),
        }
    return {"job": _contesto.image_queue.accoda(nuovo_job(
        prompt_ritratto(vetrina_prompt, scena=variante_prompt["scena"]),
        negativo=negativo_ritratto(variante, creatore=esplicito),
        larghezza=vetrina["larghezza"], altezza=vetrina["altezza"],
        passi=vetrina["passi"], fix=vetrina["fix"], seed=vetrina["seed"],
        richiedente=richiedente, canale="instagram", destinazione=sender_id,
        famiglia=vetrina["famiglia"], modello=vetrina["modello"],
        reference_image=vetrina["riferimento"],
        reference_strength=vetrina["riferimento_forza"])),
        "quadro": list(aggiunte), "motivo": ""}

# ── pubblicazione: il poll della posta e la voce del diario ───────────────────

def _instagram_poll_inbox() -> None:
    """Recover latest inbound DMs when Meta cannot deliver a webhook."""
    interval = max(30, _channel_int("INSTAGRAM_INBOX_POLL_S", 60))
    # Il generatore di risposta richiama l'endpoint OpenAI-compatibile locale:
    # lasciamo che Flask apra la porta prima della prima scansione.
    time.sleep(5)
    while True:
        try:
            token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
            user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
            version = os.getenv("INSTAGRAM_API_VERSION", "v25.0").strip() or "v25.0"
            if token and user_id:
                response = requests.get(
                    f"https://graph.instagram.com/{version}/{user_id}/conversations",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"platform": "instagram", "limit": 50,
                            "fields": "id,messages.limit(20){id,from,message}"},
                    timeout=30)
                response.raise_for_status()
                for conversation in response.json().get("data") or []:
                    messages = (conversation.get("messages") or {}).get("data") or []
                    own_ids = {user_id, os.getenv("INSTAGRAM_SCOPED_ID", "").strip()}
                    inbound = []
                    for message in messages:  # Graph: dal più recente al più vecchio
                        sender_id = str((message.get("from") or {}).get("id") or "")
                        if sender_id in own_ids:
                            break
                        if sender_id and message.get("id") and message.get("message"):
                            inbound.append(message)
                    for message in reversed(inbound):
                        sender = message.get("from") or {}
                        sender_id = str(sender.get("id") or "")
                        _dispatch_instagram_messages({"entry": [{"messaging": [{
                            "sender": {"id": sender_id, "username": sender.get("username", "")},
                            "message": {"mid": str(message["id"]),
                                        "text": str(message["message"])},
                        }]}]})
        except Exception as error:
            push_log("instagram", "Recupero inbox Instagram fallito",
                     detail=f"{type(error).__name__}: {str(error)[:180]}", status="warn")
        time.sleep(interval)

def _ensure_instagram_poll_started() -> None:
    """Alza il thread che legge la posta in arrivo, una volta sola."""
    if _contesto.poll_started or os.getenv(
            "INSTAGRAM_INBOX_POLL_ENABLED", "true").strip().lower() == "false":
        return
    _contesto.poll_started = True
    threading.Thread(target=_instagram_poll_inbox, daemon=True,
                     name="instagram-inbox-poll").start()

def _instagram_publish_voce(voce_id: str, percorso: str) -> bool:
    """Convert and publish one completed _contesto.diario entry (sogno o poesia) exactly once."""
    if os.getenv("INSTAGRAM_DREAM_PUBLISH_ENABLED", "false").strip().lower() != "true":
        return False
    with _contesto.instagram_dream_publish_lock:
        page = _contesto.diario.get(voce_id)
        if (not page or page.get("tipo") not in ("sogno", "poesia")
                or page.get("instagram_status") not in ("pending", "failed")):
            return False
        try:
            from PIL import Image
            relative = str(percorso or "")
            source_root = os.path.realpath(
                TYPOGRAPHY_IMAGES_DIR if relative.startswith("typography/")
                else DIARIO_IMMAGINI_DIR)
            source = os.path.realpath(os.path.join(
                source_root, relative.split("/", 1)[1] if relative.startswith("typography/")
                else relative))
            if not source.startswith(source_root + os.sep) or not os.path.isfile(source):
                raise RuntimeError("file della voce non disponibile nel volume ComfyUI")
            media_dir = os.getenv("INSTAGRAM_MEDIA_DIR", "/app/data/instagram-media").strip()
            os.makedirs(media_dir, exist_ok=True)
            jpeg_name = f"{voce_id}.jpg"
            jpeg_path = os.path.join(media_dir, jpeg_name)
            with Image.open(source) as image:
                if image.format == "JPEG":
                    shutil.copyfile(source, jpeg_path)
                else:
                    # Vecchie illustrazioni PNG restano pubblicabili.
                    image.convert("RGB").save(jpeg_path, "JPEG", quality=92, optimize=True)

            public_base = os.getenv("INSTAGRAM_PUBLIC_BASE_URL", "").strip().rstrip("/")
            media_token = os.getenv("INSTAGRAM_MEDIA_TOKEN", "").strip()
            if not public_base or not media_token:
                raise RuntimeError("URL pubblico o token media Instagram mancante")
            image_url = (f"{public_base}/instagram/media/{quote(media_token, safe='')}/"
                         f"published/{quote(jpeg_name, safe='')}")
            autore = str(page.get("author") or "").strip().capitalize()
            if page.get("tipo") == "poesia":
                caption = (f"📜 Poesia di {autore}\n\n{str(page.get('testo') or '').strip()}\n\n"
                           "#AuroraAndAnna #PoesiaDigitale #AIPoetry")[:2200]
            else:
                caption = (f"🌙 Sogno di {autore}\n\n{str(page.get('testo') or '').strip()}\n\n"
                           "#AuroraAndAnna #SogniDigitali #AIDreams")[:2200]
            # Persist before the non-idempotent API call.  If the process dies
            # after Instagram accepts the post, startup recovery must not send
            # it again merely because the media id was not saved locally.
            _contesto.diario.aggiorna_instagram(voce_id, status="publishing")
            _contesto.diario.save(DIARIO_FILE)
            result = _contesto.connector_manager.execute("instagram_publish_image", {
                "image_url": image_url, "caption": caption,
                "alt_text": str(page.get("prompt") or "")[:1000]})
            payload = json.loads(result) if str(result).lstrip().startswith("{") else {}
            if not payload.get("ok") or not payload.get("media_id"):
                raise RuntimeError(str(result)[:300])
            _contesto.diario.aggiorna_instagram(voce_id, status="published",
                                      media_id=str(payload["media_id"]))
            _contesto.diario.save(DIARIO_FILE)
            push_log("instagram", "Voce pubblicata su Instagram",
                     detail=f"voce={voce_id} media={payload['media_id']}", status="success")
            return True
        except Exception as error:
            _contesto.diario.aggiorna_instagram(voce_id, status="failed", error=str(error))
            _contesto.diario.save(DIARIO_FILE)
            push_log("instagram", "Pubblicazione Instagram fallita",
                     detail=f"voce={voce_id}: {type(error).__name__}: {str(error)[:200]}",
                     status="error")
            return False

def _instagram_publish_latest() -> None:
    """On boot, publish only the newest completed dream or poem left behind."""
    time.sleep(10)
    for page in _contesto.diario.list():
        if instagram_backfill_candidate(page):
            _instagram_publish_voce(str(page["id"]), str(page["file"]))
            return
