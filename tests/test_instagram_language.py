# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.instagram_language import fast_reply, language_hint, load_codex, normalize_slang  # noqa: E402


# Fixture tracciata, non il codex di produzione: quello sta in data/ (gitignored,
# e cresce con l'uso reale), quindi su un checkout pulito load_codex() restituirebbe
# {} e questi test fallirebbero senza che nessuno lo veda. La fixture contiene le
# voci che il test asserisce, cosi' resta deterministica e verifica comunque il
# load_codex vero.
CODEX = Path(__file__).resolve().parent / "fixtures" / "instagram-language-codex.json"


def test_normalizes_short_slang_with_punctuation_and_emoji():
    assert normalize_slang("  WeWe!!! 👋 ") == "wewe"


def test_known_greetings_get_language_hints():
    codex = load_codex(str(CODEX))
    assert language_hint("Ei!", codex)["language"] == "portoghese"
    assert language_hint("Wee", codex)["language"] == "italiano colloquiale"
    assert "ambiguo" in language_hint("Bro", codex)["language"]


def test_long_messages_do_not_use_short_slang_override():
    codex = load_codex(str(CODEX))
    assert language_hint("Bro oggi vorrei raccontarti una cosa", codex) is None


def test_fast_reply_covers_greetings_and_status_inversions():
    codex = load_codex(str(CODEX))
    assert fast_reply("Ciao!", codex) == "Ciao! 😊 Come stai?"
    assert fast_reply("bene, tu?", codex) == "Bene anch'io, grazie 😊 Che fai di bello?"
    assert fast_reply("Hola", codex) == "¡Hola! 😊 ¿Cómo estás?"
    assert fast_reply("Buonanotte!", codex) == "Buonanotte! 😊 Sogni d'oro 🌙"


def test_fast_reply_never_fires_on_compound_or_long_messages():
    codex = load_codex(str(CODEX))
    assert fast_reply("ciao come stai", codex) == ""
    assert fast_reply("Ciao vorrei parlarti di una cosa", codex) == ""
    assert fast_reply("", codex) == ""
