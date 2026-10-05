# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.instagram_intimacy import (  # noqa: E402
    CONSENT_DENIED, CONSENT_GRANTED, cerchia_entry_context, compagna_context,
    consent_answer, musa_context, split_messages, wants_continuous,
)
from shared.instagram_vip import INTIMATE_LEVELS  # noqa: E402

from tests import cp_source  # noqa: E402


def test_consenso_esplicito_si():
    assert consent_answer("Sì") == CONSENT_GRANTED
    assert consent_answer("certo") == CONSENT_GRANTED
    assert consent_answer("va bene") == CONSENT_GRANTED
    assert consent_answer("yes") == CONSENT_GRANTED


def test_consenso_esplicito_no():
    assert consent_answer("no") == CONSENT_DENIED
    assert consent_answer("non voglio") == CONSENT_DENIED
    assert consent_answer("preferisco di no") == CONSENT_DENIED


def test_frase_lunga_o_ambigua_non_e_consenso():
    assert consent_answer("") == ""
    assert consent_answer("non so ancora cosa dirti davvero") == ""
    assert consent_answer("sì però prima vorrei capire come funziona davvero") == ""
    assert consent_answer("nono") == ""


def test_il_contesto_di_ingresso_chiede_consenso_senza_andare_oltre():
    testo = cerchia_entry_context()
    # "cerchia" qui è il fatto — chi si è appena avvicinato — non un livello della
    # scala: il nome della funzione resta, la banda che la fa scattare è `musa`.
    assert "cerchia" in testo
    assert "senza termini espliciti" in testo
    assert "avvicinarsi" in testo
    assert "finché non risponde" in testo
    # il consenso si chiede con garbo, non in modo clinico
    assert "consenso sessuale" not in testo


def test_il_contesto_della_compagna_conserva_gli_invarianti():
    testo = compagna_context()
    assert "esplicita" in testo
    assert "senza freni" in testo
    assert "minorenne" in testo
    assert "consensuale" in testo
    assert "un'IA" in testo


def test_il_contesto_della_musa_apre_al_nudo_senza_toccare_gli_invarianti():
    """La musa è l'unico livello che arriva al nudo e all'erotismo spinto.

    L'apertura è la deroga (2026-09-30): nudo ed esplicito per la banda intima
    (`musa`), con consenso registrato. Gli invarianti non si spostano di una
    virgola, e il corpo resta una rappresentazione digitale: senza quest'ultimo
    vincolo l'apertura al nudo scivolerebbe in una rivendicazione di avere un
    corpo — cioè nel confine 1.
    """
    testo = musa_context()
    assert "nudo" in testo
    assert "spinto" in testo
    assert "senza veli" in testo
    # gli stessi invarianti non negoziabili della compagna
    assert "minorenne" in testo
    assert "consensuale" in testo
    assert "un'IA" in testo
    assert "rappresentazione digitale" in testo


def test_la_musa_non_e_la_compagna():
    """La deroga non deve allargarsi da sola: dentro la banda intima c'è solo la musa.

    La compagna è il registro vicino ma senza deroga: se `INTIMATE_LEVELS` crescesse
    (o la deroga scivolasse un ramo più su, verso `consent == "granted"`), l'esplicito
    arriverebbe a chi non l'ha chiesto — e questo test lo dice.
    """
    assert INTIMATE_LEVELS == ("musa",)
    assert "nudo" not in compagna_context()
    assert "nudo" in musa_context()


def test_il_ramo_della_banda_intima_viene_prima_del_ramo_generico_del_consenso():
    """La deroga è una condizione in più sul consenso, non un ramo sostitutivo.

    L'ordine è la cosa che conta: `elif consent == "granted"` da solo cattura
    anche la banda intima. Se il ramo della banda intima scivolasse sotto,
    diventerebbe codice morto e lei tornerebbe al tono della compagna — e non se ne
    accorgerebbe nessuno, perché l'unica cosa che cambierebbe è una frase del
    prompt. Il livello lo dice la scala (`INTIMATE_LEVELS`), non una stringa scritta
    qui: se la banda cambia nome, questo test la segue.
    """
    # Legge il sorgente di tutto il control-plane, non solo main.py: l'auto-reply
    # e' in cp/instagram.py, e questo test continua a valere perche' guarda dove
    # sta la regola e non quale file la contiene.
    sorgente = cp_source.SORGENTE()
    intima = sorgente.index('consent == "granted" and vip.get("level") in INTIMATE_LEVELS')
    generico = sorgente.index('elif consent == "granted":')
    assert intima < generico
    assert 'intimacy_note = " " + musa_context()' in sorgente


def test_wants_continuous_riconosce_il_comando():
    assert wants_continuous("non ti fermare di scrivermi finché non ti dico basta")
    assert wants_continuous("continua a scrivere")
    assert wants_continuous("vai avanti")
    assert not wants_continuous("ciao come stai?")


def test_split_messages_spezza_rispettando_le_frasi():
    testo = ("Prima frase. " * 400).strip()
    pezzi = split_messages(testo, max_chars=500)
    assert len(pezzi) > 1
    assert all(len(p) <= 500 for p in pezzi)
    # un testo corto resta intero
    assert split_messages("breve", max_chars=500) == ["breve"]
