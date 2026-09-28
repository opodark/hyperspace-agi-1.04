# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.instagram_memory import InstagramMemory  # noqa: E402


def test_memories_are_isolated_and_persistent(tmp_path):
    path = tmp_path / "memory.json"
    memory = InstagramMemory(str(path))
    memory.append("111", "user", "Mi piace il mare", username="alice")
    memory.append("222", "user", "Preferisco i monti", username="bob")
    assert "mare" in InstagramMemory(str(path)).context("111")["turns"][0]["text"]
    assert "monti" not in memory.context("111")["turns"][0]["text"]


def test_compaction_keeps_new_turns_and_summary(tmp_path):
    memory = InstagramMemory(str(tmp_path / "memory.json"))
    for index in range(8):
        memory.append("111", "user" if index % 2 == 0 else "assistant", f"turno {index}")
    material = memory.compaction_material("111", max_turns=6, keep_recent=3)
    assert material and len(material["turns"]) == 5
    memory.append("111", "user", "arrivato durante il riassunto")
    memory.apply_summary("111", "Ama parlare del tempo.", material["cutoff_seq"])
    context = memory.context("111", recent_turns=10)
    assert context["summary"] == "Ama parlare del tempo."
    assert context["turns"][-1]["text"] == "arrivato durante il riassunto"
