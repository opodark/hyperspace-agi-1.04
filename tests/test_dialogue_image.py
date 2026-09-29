# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from shared.dialogue_image import compose_dialogue


class DialogueImageTests(unittest.TestCase):
    def test_crea_una_card_jpeg_quadrata(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "dialogo.jpg"
            compose_dialogue(str(target), [("anna", "una domanda"), ("aurora", "una risposta")])
            with Image.open(target) as img:
                self.assertEqual(img.format, "JPEG")
                self.assertEqual(img.size, (1080, 1080))


if __name__ == "__main__":
    unittest.main()
