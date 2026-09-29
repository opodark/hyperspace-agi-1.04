# SPDX-License-Identifier: Apache-2.0
"""The repository ComfyUI node must write a real JPEG and report its path."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy as np
    from PIL import Image
except ImportError:  # ComfyUI's interpreter has these; the repo test env may not.
    np = None
    Image = None


class _Tensor:
    def __init__(self):
        self.shape = (4, 5, 3)

    def cpu(self):
        return self

    def numpy(self):
        return np.full(self.shape, 0.5, dtype=np.float32)


@unittest.skipUnless(np is not None and Image is not None, "requires ComfyUI image dependencies")
class SaveJPEGTests(unittest.TestCase):
    def test_saves_jpeg_in_comfy_output_and_reports_it(self):
        path = (Path(__file__).resolve().parents[1] / "integrations" / "comfyui" /
                "custom_nodes" / "hyperspace_save_jpeg.py")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "HyperSpace"
            target.mkdir()
            folder_paths = types.SimpleNamespace(
                get_output_directory=lambda: temp,
                get_save_image_path=lambda *_args: (str(target), "bridge", 1,
                                                     "HyperSpace", "HyperSpace/bridge"))
            spec = importlib.util.spec_from_file_location("hyperspace_save_jpeg_test", path)
            module = importlib.util.module_from_spec(spec)
            with mock.patch.dict(sys.modules, {"folder_paths": folder_paths}):
                spec.loader.exec_module(module)
                result = module.HyperSpaceSaveJPEG().save_images([_Tensor()])
            saved = result["ui"]["images"][0]
            self.assertEqual(saved["filename"], "bridge_00001_.jpg")
            with Image.open(target / saved["filename"]) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (5, 4))


if __name__ == "__main__":
    unittest.main()
