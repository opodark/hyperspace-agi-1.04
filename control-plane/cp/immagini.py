# SPDX-License-Identifier: Apache-2.0
# control-plane/cp/immagini.py
# LA CODA IMMAGINI E IL SUO CONTRATTO CON IL PONTE.
#
# Qui c'e' la parte di immagini che ha un contratto verso l'esterno: il ponte
# ComfyUI chiede il prossimo job a `/image/jobs` e riporta l'esito a
# `/image/result`. Nessuno dei due sa che cosa sia successo prima, quindi qui la
# forma delle risposte e' un contratto, non una convenzione interna.
#
# Cosa NON c'e', e perche':
#
# - `image_queue` e `image_memory_gate` NON sono di questo dominio. Sono
#   infrastruttura condivisa: `image_queue` ha dieci utenti in quattro domini
#   diversi (le route qui, la coda dei canali, `_channel_immagine`, gli sketch dei
#   loop autonomi), e `image_memory_gate` sette in tre. Questo modulo li riceve e
#   li usa, ma non li possiede: se li avesse creati qui, gli altri utenti
#   dovrebbero chiederli a lui, e il dominio immagini diventerebbe un centro
#   attorno al quale gira tutto il resto.
# - `_channel_immagine`, `_accoda_sketch` e `_sketch_conteggi` restano in
#   main.py. Sono un'altra cosa: non parlano col ponte, ma con i canali e con i
#   loop che generano contenuti. Stanno nel dominio dei canali, non in questo.
# - `/diario/immagini/<path:nome>` resta in main.py con il diario: serve le
#   immagini del diario, non quelle dei job.
#
# Quattro dipendenze dal boot (la coda, il gate, i connettori e il diario) stanno
# in un oggetto solo, `_contesto`, e non come quattro parametri a ogni funzione:
# `image_result` chiama `_libera_scheda_per_immagine` che accoda a `image_queue`,
# e passare il contesto lungo la catena sarebbe stato piu' rumoroso del monolite.

import os
import re
import threading
from types import SimpleNamespace
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, request

from cp.canali import _channel_error, _channel_name
from cp.config import DIARIO_FILE, DIARIO_IMMAGINI_DIR, TYPOGRAPHY_IMAGES_DIR
from cp.instagram import _instagram_publish_voce
from cp.log import push_log
from shared import gpu_budget
from shared.diario import file_da_job
from shared.image_jobs import (FAMIGLIE_CHECKPOINT, LATO_CONSIGLIATO,
                               RIFERIMENTO_FORZA_DEFAULT, nuovo_job, usa_checkpoint)
from shared.poetry_image import compose_readable, readable_excerpt
from shared.sketch import SKETCH_PASSI, negativo_sketch

# ── il contesto ───────────────────────────────────────────────────────────────
# Le quattro cose che vengono dal boot. `_serve()` esiste perche' senza, un uso
# fuori dal montaggio darebbe `AttributeError: 'NoneType' object has no
# attribute ...`: lo stesso tipo di difetto che ha già fatto rispondere 500 al
# webhook Instagram. Qui il messaggio nomina il campo che manca.
_contesto = None


def _serve(*campi):
    if _contesto is None:
        raise RuntimeError(
            "cp.immagini non e' montato: chiama immagini.monta(app, ...) "
            "prima di usare la coda")
    mancanti = [c for c in campi if getattr(_contesto, c, None) is None]
    if mancanti:
        raise RuntimeError("cp.immagini montato senza: " + ", ".join(mancanti))
    return _contesto


# ── route ─────────────────────────────────────────────────────────────────────
# Un solo blueprint, costruito all'import: le route sono funzioni chiuse sul
# contesto e non su `app`, e i test aprono la coda su un tmpdir diverse volte
# nello stesso processo usando `monta()`.
_bp = Blueprint("immagini", __name__)


def monta(app, *, image_queue=None, image_memory_gate=None, connector_manager=None,
          diario=None):
    """Registra le sei route della coda e tiene i riferimenti al boot.

    Non crea la coda: `image_queue` arriva da `main.py` perché la condivide con i
    canali e con i loop, e crearla qui sarebbe stato il modo sbagliato di
    accorgersi che è condivisa.
    """
    global _contesto
    _contesto = SimpleNamespace(
        image_queue=image_queue,
        image_memory_gate=image_memory_gate,
        connector_manager=connector_manager,
        diario=diario,
    )
    app.register_blueprint(_bp)
    return app


def smonta():
    """Dimentica il contesto. Serve ai test, che aprono più montaggi in sequenza."""
    global _contesto
    _contesto = None


# ── rotte: il contratto con il ponte ComfyUI ──────────────────────────────────

@_bp.route('/image/generate', methods=['POST'])
def image_generate():
    """Mette in coda un job immagine e torna subito con l'id."""
    errore = _channel_error()
    if errore:
        return errore
    dati = request.get_json(silent=True) or {}
    famiglia = str(dati.get("famiglia", "")).strip().lower()
    # Le famiglie a checkpoint unico — Pony per gli sketch, ChickMixFlat per il
    # volto di Anna — girano sul Mac: lì la memoria la libera il ponte al claim,
    # mentre il percorso Qwen/Windows scarica subito la sua GPU dedicata.
    checkpoint = usa_checkpoint(famiglia)
    lato = LATO_CONSIGLIATO.get(famiglia, 768)
    scheda = "" if checkpoint else _libera_scheda_per_immagine()
    try:
        job = nuovo_job(dati.get("prompt", ""),
                        negativo=dati.get("negativo", negativo_sketch() if checkpoint else ""),
                        larghezza=dati.get("larghezza", lato),
                        altezza=dati.get("altezza", lato),
                        passi=dati.get("passi", SKETCH_PASSI if checkpoint else 25),
                        fix=dati.get("fix", 0),
                        seed=dati.get("seed", 0),
                        richiedente=dati.get("richiedente", ""),
                        canale=_channel_name(),
                        destinazione=dati.get("destinazione", ""),
                        modello=dati.get("modello", ""), famiglia=famiglia,
                        pose_image=dati.get("pose_image", ""),
                        pose_preset=dati.get("pose_preset", ""),
                        pose_strength=dati.get("pose_strength", 1.0),
                        # Il volto: il riferimento che la vetrina di Anna dichiara
                        # (`riferimento`), o quello che un client passa a mano. Un nome
                        # dentro ComfyUI/input, e la famiglia deve avere l'IP-Adapter:
                        # dove non ce l'ha il job fallisce in modo visibile invece di
                        # uscire con un volto qualunque.
                        reference_image=dati.get("reference_image", ""),
                        # Il default è quello di `shared/image_jobs` e non un 0.8 scritto
                        # qui: due copie dello stesso numero sono due default che si
                        # allontanano, e a divergere sarebbe quella che si legge meno.
                        reference_strength=dati.get("reference_strength",
                                                    RIFERIMENTO_FORZA_DEFAULT),
                        lora_name=dati.get("lora_name", ""),
                        lora_strength=dati.get("lora_strength", 0.8))
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 400
    try:
        accodato = _contesto.image_queue.accoda(job)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 429
    push_log('channel', 'Job immagine in coda',
             detail=f"id={accodato['id']} {accodato['larghezza']}x{accodato['altezza']} "
                    f"passi={accodato['passi']} da={accodato['richiedente'] or '?'}"
                    + (f" · {scheda}" if scheda else ""),
             status='info')
    return jsonify({"ok": True, "job": accodato, "scheda": scheda}), 201

@_bp.route('/image/jobs')
def image_jobs():
    _serve("image_queue", "image_memory_gate")
    """Il prossimo job per il ponte. Vuoto = 204, che non è un errore.

    `?famiglia=` limita ai job di quel modello: un ponte SDXL-Turbo (il Mac)
    chiede `?famiglia=sdxl-turbo` e non prende i job Qwen-Image della win11.
    """
    errore = _channel_error()
    if errore:
        return errore
    famiglia = (request.args.get("famiglia") or "").strip()
    job = _contesto.image_queue.prossimo(capace_di=famiglia or None)
    if job is None:
        return ('', 204)
    if usa_checkpoint(job.get("famiglia")):
        if not _contesto.image_memory_gate.reserve_image(job["id"]):
            _contesto.image_queue.rinvia(job["id"])
            return jsonify({"ok": False, "error": "memoria occupata: job rinviato"}), 503
    return jsonify({"ok": True, "job": job})

@_bp.route('/image/defer', methods=['POST'])
def image_defer():
    errore = _channel_error()
    if errore:
        return errore
    job_id = str((request.get_json(silent=True) or {}).get("id", ""))
    _contesto.image_memory_gate.release_image(job_id)
    return jsonify({"ok": _contesto.image_queue.rinvia(job_id)})

@_bp.route('/image/result', methods=['POST'])
def image_result():
    _serve("image_queue", "image_memory_gate", "connector_manager",
          "diario")
    """Il ponte riferisce com'è andata: è l'unico modo per saperlo."""
    errore = _channel_error()
    if errore:
        return errore
    dati = request.get_json(silent=True) or {}
    job_id = str(dati.get("id", ""))
    chiuso = _contesto.image_queue.concludi(job_id, bool(dati.get("ok")),
                                  file=dati.get("file", ""), errore=dati.get("errore", ""),
                                  durata_ms=dati.get("durata_ms", 0))
    if chiuso is None:
        _contesto.image_memory_gate.release_image(job_id)
        return jsonify({"ok": False, "error": "job sconosciuto"}), 404
    if chiuso.pop("_already_concluded", False):
        _contesto.image_memory_gate.release_image(job_id)
        return jsonify({"ok": True, "job": chiuso})
    # Non liberare la memoria unificata fra immagini consecutive del Mac:
    # altrimenti Ollama puo' ricaricarsi nel breve intervallo result -> jobs.
    if (usa_checkpoint(chiuso.get("famiglia"))
            and _contesto.image_queue.ha_in_coda(FAMIGLIE_CHECKPOINT)):
        _contesto.image_memory_gate.continue_image_queue(job_id)
    else:
        _contesto.image_memory_gate.release_image(job_id)
    esito = chiuso["esito"]
    fatto = file_da_job(chiuso)
    if fatto:
        voce_id, percorso = fatto
        page = _contesto.diario.get(voce_id)
        if page:
            excerpt = readable_excerpt(page.get("prompt", ""), page.get("testo", ""))
            if excerpt:
                try:
                    root = os.path.realpath(DIARIO_IMMAGINI_DIR)
                    source = os.path.realpath(os.path.join(root, percorso))
                    if not source.startswith(root + os.sep) or not os.path.isfile(source):
                        raise ValueError("immagine Comfy fuori dal volume consentito")
                    name = f"{voce_id}.jpg"
                    if not re.fullmatch(r"[A-Za-z0-9_-]+\.jpg", name):
                        raise ValueError("id voce non valido")
                    compose_readable(source, os.path.join(TYPOGRAPHY_IMAGES_DIR, name), excerpt)
                    percorso = "typography/" + name
                except Exception as error:
                    push_log('feed', 'Pannello poesia non creato', detail=str(error)[:200],
                             source='post-loop', status='warn')
        if _contesto.diario.aggiorna_file(voce_id, percorso):
            page = _contesto.diario.get(voce_id)
            if page and page.get("tipo") in ("sogno", "poesia"):
                _contesto.diario.aggiorna_instagram(voce_id, status="pending")
            _contesto.diario.save(DIARIO_FILE)
            push_log('feed', 'sketch nel diario', detail=f'voce={voce_id} file={percorso}',
                     source='post-loop', status='success')
            page = _contesto.diario.get(voce_id)
            if page and page.get("tipo") in ("sogno", "poesia"):
                threading.Thread(target=_instagram_publish_voce,
                                 args=(voce_id, percorso), daemon=True).start()
    if (chiuso.get("stato") == "done" and chiuso.get("canale") == "instagram"
            and chiuso.get("destinazione") and esito.get("file")):
        public_base = os.getenv("INSTAGRAM_PUBLIC_BASE_URL", "").strip().rstrip("/")
        media_token = os.getenv("INSTAGRAM_MEDIA_TOKEN", "").strip()
        disegno = _percorso_disegno_servibile(esito.get("file"))
        if public_base and media_token and disegno:
            image_url = (f"{public_base}/instagram/media/{quote(media_token, safe='')}/"
                         f"{quote(disegno, safe='/')}")
            sent = _contesto.connector_manager.execute("instagram_send_image", {
                "recipient_id": chiuso["destinazione"], "image_url": image_url})
            if str(sent).lstrip().startswith("{"):
                _contesto.image_queue.consegnato(chiuso["id"])
                push_log('instagram', 'Disegno VIP consegnato',
                         detail=f"job={chiuso['id']}", status='success')
            else:
                push_log('instagram', 'Disegno VIP non consegnato',
                         detail=str(sent)[:200], status='warn')
        elif public_base and media_token:
            push_log('instagram', 'Disegno VIP non consegnato',
                     detail=f"file non servibile: {str(esito.get('file'))[:120]}",
                     status='warn')
    push_log('channel', 'Job immagine concluso',
             detail=(f"id={chiuso['id']} stato={chiuso['stato']} "
                     f"{esito.get('file') or esito.get('errore') or ''}")[:200],
             status=('success' if chiuso["stato"] == "done" else 'warn'))
    return jsonify({"ok": True, "job": chiuso})

@_bp.route('/image/status')
def image_status():
    """Coda, ultimi job e scadenze: serve a "dov'è finita la mia immagine?"."""
    errore = _channel_error()
    if errore:
        return errore
    return jsonify({"ok": True, **_contesto.image_queue.stato(), "memory_gate": _contesto.image_memory_gate.status()})

@_bp.route('/image/job/<job_id>')
def image_job(job_id):
    """Un job per id: "dov'è finita la mia immagine?" senza leggere tutta la coda.

    Cerca prima fra i job vivi, poi nello storico: un job già concluso e potato
    (scadenza di 15 minuti) resta leggibile finché è fra gli ultimi venti, ed è
    esattamente il caso di chi arriva un minuto dopo la fine.
    """
    errore = _channel_error()
    if errore:
        return errore
    job = _contesto.image_queue.job(job_id)
    if job:
        return jsonify({"ok": True, "job": job, "da_storico": False})
    for voce in _contesto.image_queue.stato().get("ultimi", []):
        if str(voce.get("id")) == str(job_id):
            return jsonify({"ok": True, "job": voce, "da_storico": True})
    return jsonify({"ok": False, "error": "job sconosciuto"}), 404

# ── helper: il percorso pubblicabile e la scheda libera ───────────────────────

def _percorso_disegno_servibile(percorso) -> str:
    """Percorso relativo servibile per un disegno, oppure "" se non è servibile.

    La rotta `/instagram/media/<token>/<path:nome>` serve `DIARIO_IMMAGINI_DIR`
    con il percorso relativo *completo* (`HyperSpace/bridge_00048_.jpg`): qui si
    verifica che il file esista davvero e che resti dentro il volume, così l'URL
    dato a Instagram non è mai un 404.
    """
    rel = str(percorso or "").strip().lstrip("/")
    if not rel:
        return ""
    radice = os.path.realpath(DIARIO_IMMAGINI_DIR)
    pieno = os.path.realpath(os.path.join(radice, rel))
    if not pieno.startswith(radice + os.sep) or not os.path.isfile(pieno):
        return ""
    return rel

def _libera_scheda_per_immagine() -> str:
    """Fa posto sulla scheda prima di accodare un'immagine (una scheda, un modello).

    Il diffusion di ComfyUI e il modello della chat non stanno insieme in 8 GB, e la
    contesa si presentava come `CUDA error: unknown error` (2026-09-22: 6.2 GB a
    Ollama, 1.7 liberi). Non blocca mai: se Ollama non risponde, il job si accoda lo
    stesso e il motivo resta scritto.
    """
    modello = gpu_budget.da_scaricare()
    if not modello:
        return ""
    base = (os.getenv("OLLAMA_RAW_BASE_URL", "") or "http://127.0.0.1:11434").rstrip("/")
    try:
        risposta = requests.post(f"{base}{gpu_budget.SCARICA_PATH}",
                                 json=gpu_budget.richiesta_scarico(modello), timeout=30)
    except requests.RequestException as e:
        return gpu_budget.descrivi_esito(0, errore=str(e)[:120], modello=modello)
    return gpu_budget.descrivi_esito(risposta.status_code, modello=modello)
