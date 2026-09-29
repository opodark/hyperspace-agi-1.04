# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from shared.instagram_outbox import InstagramReplyOutbox


class InstagramOutboxTests(unittest.TestCase):
    def test_persists_debounced_reply_and_retries_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [100.0]
            path = str(Path(temp) / "replies.json")
            outbox = InstagramReplyOutbox(path, now=lambda: now[0])
            outbox.enqueue("12345", "m1", "Ciao", {}, debounce_s=12)
            now[0] = 105
            outbox.enqueue("12345", "m2", "Ciao tu?", {}, debounce_s=12)
            self.assertIsNone(outbox.claim_due())
            now[0] = 117
            claimed = InstagramReplyOutbox(path, now=lambda: now[0]).claim_due()
            self.assertEqual(claimed["message_id"], "m2")
            outbox = InstagramReplyOutbox(path, now=lambda: now[0])
            outbox.fail("12345", "m2", "503", safe_retry=True)
            self.assertEqual(outbox.status()["counts"]["retry"], 1)
            self.assertIsNone(outbox.claim_due())
            now[0] = 147
            self.assertEqual(outbox.claim_due()["message_id"], "m2")

    def test_interrupted_send_requires_review_not_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [100.0]
            path = str(Path(temp) / "replies.json")
            outbox = InstagramReplyOutbox(path, now=lambda: now[0])
            outbox.enqueue("12345", "m1", "Test", {}, debounce_s=0)
            outbox.claim_due()
            outbox.sending("12345", "m1")
            restored = InstagramReplyOutbox(path, now=lambda: now[0])
            self.assertEqual(restored.status()["counts"]["needs_review"], 1)
            self.assertIsNone(restored.claim_due())

    def test_same_message_is_not_queued_twice_and_status_is_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            outbox = InstagramReplyOutbox(str(Path(temp) / "replies.json"))
            outbox.enqueue("12345", "m1", "Segreto", {})
            outbox.enqueue("12345", "m1", "Duplicato", {})
            status = outbox.status()
            self.assertEqual(status["counts"]["queued"], 1)
            self.assertNotIn("Segreto", str(status))
            self.assertNotIn("12345", str(status))

    def test_promoted_non_e_ereditato_dal_messaggio_precedente(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "replies.json")
            outbox = InstagramReplyOutbox(path, now=lambda: 100.0)
            outbox.enqueue("12345", "m1", "primo", {"level": "vip", "promoted": True})
            outbox.enqueue("12345", "m2", "secondo", {"level": "vip", "promoted": False})
            # il secondo messaggio non eredita `promoted` dal primo
            self.assertFalse(outbox._items["12345"]["vip"].get("promoted"))


if __name__ == "__main__":
    unittest.main()
