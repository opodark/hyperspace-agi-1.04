import unittest

from shared.network_security import normalize_http_base, token_authorized


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


if __name__ == "__main__":
    unittest.main()
