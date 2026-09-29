# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.instagram_intimacy import (  # noqa: E402
    CONSENT_DENIED, CONSENT_GRANTED, cerchia_entry_context, compagna_context,
    consent_answer, split_messages, wants_continuous,
)


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
