# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.social_dreams import social_dream_inspirations  # noqa: E402


def test_social_inspirations_remove_identity_and_trivial_greetings():
    turns = [
        {"channel": "instagram", "surface": "instagram", "chat": "42",
         "messages": [{"author": "mario", "text": "@mario sogna spesso il mare d'inverno https://example.test"}]},
        {"channel": "instagram", "surface": "instagram", "chat": "43",
         "messages": [{"author": "luca", "text": "Ciao!"}]},
    ]
    result = social_dream_inspirations(turns)
    assert result == ["una persona sogna spesso il mare d'inverno [link]"]
    assert "mario" not in result[0].lower()


def test_social_inspirations_diversify_chats_and_deduplicate():
    turns = [
        {"channel": "telegram", "surface": "telegram", "chat": "a",
         "messages": [{"text": "Ho paura delle stanze vuote"}]},
        {"channel": "telegram", "surface": "telegram", "chat": "a",
         "messages": [{"text": "Vorrei attraversare una foresta"}]},
        {"channel": "instagram", "surface": "instagram", "chat": "b",
         "messages": [{"text": "Ho paura delle stanze vuote"}]},
    ]
    assert social_dream_inspirations(turns, limit=5) == ["Ho paura delle stanze vuote"]
