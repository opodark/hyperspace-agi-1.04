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

import os
import threading
from collections import deque

from flask import Blueprint, jsonify, request, send_from_directory

from cp.canali import _channel_int
from cp.config import (DIARIO_IMMAGINI_DIR, INSTAGRAM_MEMORY_FILE,
                       INSTAGRAM_REPLY_OUTBOX_FILE, INSTAGRAM_VIP_FILE)
from cp.http import _network_admin_error
from shared.instagram_memory import InstagramMemory
from shared.instagram_outbox import InstagramReplyOutbox
from shared.instagram_vip import InstagramVipStore
from shared.network_security import token_authorized

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

# ── route ─────────────────────────────────────────────────────────────────────
# Nove route in tutto, in due pezzi. Questo e' il primo: le sei di lettura e
# amministrazione, che sono pass-through verso i tre store piu' il guard di
# rete. Il pezzo con dentro la firma HMAC e il dispatch arriva dopo, insieme al
# codice che chiamano: registrarle separate avrebbe significato iniettare una
# funzione che tre settimane dopo sarebbe stata locale, e quel parametro
# inutilizzato sarebbe rimasto li' a spiegare una cosa che non e' vera.


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


def monta(app, *, vip_file=None, memory_file=None, outbox_file=None):
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
    app.register_blueprint(_blueprint())
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
