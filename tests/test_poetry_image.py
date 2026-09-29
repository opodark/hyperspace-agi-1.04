# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from shared.poetry_image import compose_readable, readable_excerpt


class PoetryImageTests(unittest.TestCase):
    def test_only_typographic_images_get_a_panel(self):
        self.assertEqual(readable_excerpt("un bosco di stelle", "Una poesia"), "")
        self.assertEqual(readable_excerpt("poesia scritta su carta", "Luce nella notte"),
                         "Luce nella notte")

    def test_quoted_words_must_come_from_caption(self):
        self.assertEqual(readable_excerpt('versi: "Luce nella notte"',
                                          "Luce nella notte, poi alba"), "Luce nella notte")
        self.assertEqual(readable_excerpt('versi: "parole inventate"',
                                          "Luce nella notte"), "Luce nella notte")

    def test_creates_instagram_sized_jpeg_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "original.png", Path(temp) / "poem.jpg"
            Image.new("RGB", (100, 100), "#00aacc").save(source)
            compose_readable(source, target, "Luce nella notte")
            with Image.open(target) as result:
                self.assertEqual(result.size, (1024, 1280))
                self.assertEqual(result.format, "JPEG")
            with Image.open(source) as original:
                self.assertEqual(original.size, (100, 100))


if __name__ == "__main__":
    unittest.main()
