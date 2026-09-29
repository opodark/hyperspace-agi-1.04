# SPDX-License-Identifier: Apache-2.0
"""ComfyUI output node used by HyperSpace to save shareable JPEG images."""
from __future__ import annotations

import os

import folder_paths
import numpy as np
from PIL import Image


class HyperSpaceSaveJPEG:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "filename_prefix": ("STRING", {"default": "HyperSpace/bridge"}),
            "quality": ("INT", {"default": 92, "min": 1, "max": 100}),
        }}

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    CATEGORY = "HyperSpace/image"

    def save_images(self, images, filename_prefix="HyperSpace/bridge", quality=92):
        output_dir = folder_paths.get_output_directory()
        folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, output_dir, images[0].shape[1], images[0].shape[0])
        results = []
        for batch_number, image in enumerate(images):
            pixels = np.clip(image.cpu().numpy() * 255, 0, 255).astype(np.uint8)
            name = filename.replace("%batch_num%", str(batch_number))
            file = f"{name}_{counter:05}_.jpg"
            Image.fromarray(pixels).convert("RGB").save(
                os.path.join(folder, file), "JPEG", quality=int(quality),
                optimize=True, subsampling=0)
            results.append({"filename": file, "subfolder": subfolder, "type": "output"})
            counter += 1
        return {"ui": {"images": results}}


NODE_CLASS_MAPPINGS = {"HyperSpaceSaveJPEG": HyperSpaceSaveJPEG}
NODE_DISPLAY_NAME_MAPPINGS = {"HyperSpaceSaveJPEG": "HyperSpace Save JPEG"}
