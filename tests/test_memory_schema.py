# SPDX-License-Identifier: Apache-2.0
import unittest

from shared.memory_schema import (
    MEMORY_SCHEMA_VERSION,
    OPERATIVE,
    PERSISTENT,
    PROJECT,
    classify_retention,
    entry_id,
    normalize_entry,
    validate_entry,
)


class MemorySchemaNormalizationTests(unittest.TestCase):
    def test_normalize_maps_legacy_fields_to_canonical_envelope(self):
        entry = {
            "ts": "2026-09-19T10:00:00Z",
            "event_type": "vault_note",
            "content": "decisione registrata",
            "plugin": "omega-memory",
            "status": "active",
        }
        normalized = normalize_entry(entry)
        self.assertEqual(normalized["schema"], MEMORY_SCHEMA_VERSION)
        self.assertEqual(normalized["type"], "vault_note")
        self.assertEqual(normalized["content"], "decisione registrata")
        self.assertEqual(normalized["source"], "omega-memory")
        self.assertEqual(normalized["priority"], 3)
        self.assertTrue(normalized["id"])

    def test_normalize_derives_content_from_prompt_and_response(self):
        normalized = normalize_entry({"prompt": "domanda", "response": "risposta"})
        self.assertEqual(normalized["content"], "User: domanda\nAssistant: risposta")

    def test_normalize_clamps_priority_and_defaults_status(self):
        normalized = normalize_entry({"content": "x", "priority": 99, "status": "unknown"})
        self.assertEqual(normalized["priority"], 5)
        self.assertEqual(normalized["status"], "active")

    def test_entry_id_is_stable_for_equivalent_entries(self):
        first = entry_id({"content": "stessa cosa"})
        second = entry_id({"content": "stessa cosa"})
        self.assertEqual(first, second)
        self.assertEqual(entry_id({"id": "m1", "content": "x"}), "m1")


class MemorySchemaValidationTests(unittest.TestCase):
    def test_empty_content_is_invalid(self):
        self.assertTrue(validate_entry({"type": "memory"}))
        self.assertFalse(validate_entry({"type": "memory", "content": "ok"}))

    def test_unknown_type_and_status_are_reported(self):
        errors = validate_entry({"content": "x", "type": "nope", "status": "nope"})
        self.assertEqual(errors[0], "unknown type: nope")
        self.assertEqual(errors[1], "unknown status: nope")

    def test_unparseable_timestamp_is_reported(self):
        self.assertTrue(validate_entry({"content": "x", "ts": "not-a-date"}))


class MemorySchemaRetentionTests(unittest.TestCase):
    def test_persistent_by_type(self):
        self.assertEqual(classify_retention({"type": "dream_insight"}), PERSISTENT)
        self.assertEqual(classify_retention({"type": "vault_note"}), PERSISTENT)

    def test_operative_by_type(self):
        self.assertEqual(classify_retention({"type": "memory"}), OPERATIVE)
        self.assertEqual(classify_retention({"type": "task_state"}), OPERATIVE)

    def test_project_by_default(self):
        self.assertEqual(classify_retention({"type": "dream"}), PROJECT)
        self.assertEqual(classify_retention({"type": "note"}), PROJECT)

    def test_explicit_retention_overrides_type(self):
        self.assertEqual(classify_retention({"type": "memory", "retention": "persistent"}), PERSISTENT)


if __name__ == "__main__":
    unittest.main()
