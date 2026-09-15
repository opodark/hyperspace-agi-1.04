import unittest

from shared.network_security import (
    normalize_http_base,
    sign_client_ip,
    token_authorized,
    verify_client_ip,
)


class TokenAuthorizationTests(unittest.TestCase):
    def test_matching_non_empty_tokens_are_authorized(self):
        self.assertTrue(token_authorized("secret", "secret"))

    def test_empty_or_different_tokens_are_rejected(self):
        self.assertFalse(token_authorized("", ""))
        self.assertFalse(token_authorized(None, "secret"))
        self.assertFalse(token_authorized("wrong", "secret"))


class HttpBaseValidationTests(unittest.TestCase):
    def test_accepts_http_and_https_bases(self):
        self.assertEqual(normalize_http_base("https://relay.example:8443/"),
                         "https://relay.example:8443")
        self.assertEqual(normalize_http_base("http://100.64.0.2:8085"),
                         "http://100.64.0.2:8085")

    def test_rejects_non_http_or_ambiguous_urls(self):
        for value in (
            "file:///etc/passwd",
            "http://user:pass@relay.example",
            "http://relay.example/private",
            "http://relay.example?next=http://internal",
            "not-an-url",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_http_base(value)


class ClientIpAttestationTests(unittest.TestCase):
    def setUp(self):
        self.secret = "s" * 32
        self.ip = "203.0.113.7"
        self.ts = "1000"
        self.signature = sign_client_ip(self.secret, self.ip, self.ts)

    def test_accepts_valid_recent_signature(self):
        self.assertTrue(verify_client_ip(
            self.secret, self.ip, self.ts, self.signature, now=1010,
        ))

    def test_rejects_tampering_stale_timestamp_and_short_secret(self):
        self.assertFalse(verify_client_ip(
            self.secret, "203.0.113.8", self.ts, self.signature, now=1010,
        ))
        self.assertFalse(verify_client_ip(
            self.secret, self.ip, self.ts, self.signature, now=1031,
        ))
        self.assertFalse(verify_client_ip(
            "too-short", self.ip, self.ts, self.signature, now=1010,
        ))

    def test_rejects_invalid_timestamp_without_raising(self):
        self.assertFalse(verify_client_ip(
            self.secret, self.ip, "not-a-time", self.signature, now=1010,
        ))


if __name__ == "__main__":
    unittest.main()
