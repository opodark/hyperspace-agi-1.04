# SPDX-License-Identifier: Apache-2.0
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from shared import bottle


class FakePrivateKey:
    """Chiave finta: firma deterministicamente senza toccare la crypto reale
    — questi test riguardano il PoW e la logica di validazione di bottle.py,
    non l'ECDSA di shared/identity.py (che ha i suoi test altrove e richiede
    il pacchetto 'cryptography', non sempre installato in ogni ambiente)."""

    def sign(self, data: bytes, _algorithm) -> bytes:
        import hashlib
        return hashlib.sha256(data).digest()


def fake_sign_message(payload: dict, private_key) -> dict:
    import json
    msg = {k: v for k, v in payload.items() if k != "signature"}
    msg_bytes = json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()
    sig = private_key.sign(msg_bytes, None)
    return {**msg, "signature": sig.hex()}


def fake_verify_message(message: dict) -> bool:
    import json
    sig_hex = message.get("signature", "")
    if not sig_hex:
        return False
    msg = {k: v for k, v in message.items() if k != "signature"}
    msg_bytes = json.dumps(msg, sort_keys=True, ensure_ascii=False).encode()
    import hashlib
    return hashlib.sha256(msg_bytes).hexdigest() == sig_hex


class PowTests(unittest.TestCase):
    def test_found_nonce_actually_satisfies_the_target(self):
        base = {"pubkey": "aa", "endpoint": "http://x:1", "ts": 1000}
        nonce = bottle.find_nonce(base, difficulty_bits=12)
        self.assertTrue(bottle.pow_satisfied({**base, "nonce": nonce}, difficulty_bits=12))

    def test_lower_nonce_values_are_rejected_unless_they_also_satisfy_it(self):
        base = {"pubkey": "aa", "endpoint": "http://x:1", "ts": 1000}
        nonce = bottle.find_nonce(base, difficulty_bits=12)
        for candidate in range(nonce):
            if bottle.pow_satisfied({**base, "nonce": candidate}, difficulty_bits=12):
                self.fail(f"nonce {candidate} < {nonce} soddisfa gia' la difficolta': "
                          f"find_nonce non sta cercando dal piu' piccolo")

    def test_non_integer_nonce_rejected(self):
        self.assertFalse(bottle.pow_satisfied({"nonce": "12"}, difficulty_bits=1))
        self.assertFalse(bottle.pow_satisfied({"nonce": True}, difficulty_bits=1))
        self.assertFalse(bottle.pow_satisfied({}, difficulty_bits=1))

    def test_zero_difficulty_always_satisfied(self):
        self.assertTrue(bottle.pow_satisfied({"nonce": 0}, difficulty_bits=0))

    def test_changing_payload_after_mining_breaks_pow(self):
        base = {"pubkey": "aa", "endpoint": "http://x:1", "ts": 1000}
        nonce = bottle.find_nonce(base, difficulty_bits=14)
        mutated = {**base, "endpoint": "http://y:2", "nonce": nonce}
        self.assertFalse(bottle.pow_satisfied(mutated, difficulty_bits=14))


class VerifyBottleTests(unittest.TestCase):
    def setUp(self):
        self.key = FakePrivateKey()
        # bottle.py importa shared.identity dentro le funzioni: si sostituisce
        # il modulo intero per non dipendere dal pacchetto 'cryptography'.
        import types
        fake_identity = types.ModuleType("shared.identity")
        fake_identity.sign_message = fake_sign_message
        fake_identity.verify_message = fake_verify_message
        sys.modules["shared.identity"] = fake_identity
        self.addCleanup(sys.modules.pop, "shared.identity", None)

    def _bottle(self, difficulty_bits=10, **overrides):
        b = bottle.make_bottle("04" + "11" * 64, "http://100.81.234.102:8081", self.key,
                                difficulty_bits=difficulty_bits)
        b.update(overrides)
        return b

    def test_valid_bottle_passes(self):
        ok, reason = bottle.verify_bottle(self._bottle(), difficulty_bits=10)
        self.assertTrue(ok, reason)

    def test_missing_endpoint_rejected(self):
        b = self._bottle()
        del b["endpoint"]
        ok, reason = bottle.verify_bottle(b, difficulty_bits=10)
        self.assertFalse(ok)
        self.assertIn("endpoint", reason)

    def test_non_http_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            bottle.make_bottle("pk-di-prova", "file:///tmp/peer", self.key,
                               difficulty_bits=4)

    def test_expired_rejected(self):
        b = self._bottle()
        # una bottiglia vecchia rifirmata: cambiamo ts DOPO la firma, cosi'
        # il fallimento atteso e' "scaduta", non un effetto collaterale
        # della firma rotta dalla manomissione.
        b["ts"] = int(time.time()) - 10_000
        ok, reason = bottle.verify_bottle(b, difficulty_bits=10, max_age_s=3600)
        self.assertFalse(ok)
        self.assertEqual(reason, "bottiglia scaduta")

    def test_future_timestamp_rejected(self):
        b = self._bottle()
        b["ts"] = int(time.time()) + 10_000
        ok, reason = bottle.verify_bottle(b, difficulty_bits=10)
        self.assertFalse(ok)
        self.assertEqual(reason, "ts nel futuro")

    def test_insufficient_pow_rejected(self):
        b = self._bottle(difficulty_bits=8)
        ok, reason = bottle.verify_bottle(b, difficulty_bits=24)
        self.assertFalse(ok)
        self.assertEqual(reason, "proof-of-work non soddisfatto")

    def test_tampered_endpoint_after_signing_rejected(self):
        b = self._bottle()
        b["endpoint"] = "http://attaccante.evil:1"
        ok, _reason = bottle.verify_bottle(b, difficulty_bits=10)
        self.assertFalse(ok)

    def test_non_dict_rejected(self):
        ok, reason = bottle.verify_bottle("non e' un dict")
        self.assertFalse(ok)
        self.assertIn("oggetto", reason)

    def test_oversized_payload_rejected_before_crypto(self):
        b = self._bottle(extra="x" * bottle.MAX_BOTTLE_BYTES)
        ok, reason = bottle.verify_bottle(b, difficulty_bits=10)
        self.assertFalse(ok)
        self.assertIn("grande", reason)


class RealIdentityIntegrationTests(unittest.TestCase):
    def test_round_trip_with_real_secp256k1_signature(self):
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        except ImportError:
            self.skipTest("cryptography non installato")
        key = ec.generate_private_key(ec.SECP256K1())
        pubkey = key.public_key().public_bytes(
            Encoding.X962, PublicFormat.CompressedPoint
        ).hex()
        signed = bottle.make_bottle(pubkey, "https://relay.example:8085", key,
                                    difficulty_bits=8)
        ok, reason = bottle.verify_bottle(signed, difficulty_bits=8)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
