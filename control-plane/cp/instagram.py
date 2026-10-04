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

_instagram_webhook_events = deque(maxlen=100)
_instagram_seen_messages = deque(maxlen=500)
_instagram_seen_lock = threading.Lock()
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
