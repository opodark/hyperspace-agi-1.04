# SPDX-License-Identifier: Apache-2.0
"""Generate deterministic OpenPose maps for common text-requested poses."""
from __future__ import annotations

import numpy as np
import torch
from PIL import Image, ImageDraw


PRESETS = {
    "standing": [(0,.50,.12),(1,.50,.23),(2,.40,.25),(3,.37,.42),(4,.36,.58),(5,.60,.25),(6,.63,.42),(7,.64,.58),(8,.44,.52),(9,.43,.72),(10,.42,.94),(11,.56,.52),(12,.57,.72),(13,.58,.94)],
    "arms_open": [(0,.50,.12),(1,.50,.23),(2,.40,.25),(3,.27,.31),(4,.12,.26),(5,.60,.25),(6,.73,.31),(7,.88,.26),(8,.44,.52),(9,.43,.72),(10,.42,.94),(11,.56,.52),(12,.57,.72),(13,.58,.94)],
    "seated": [(0,.50,.13),(1,.50,.24),(2,.40,.26),(3,.37,.42),(4,.45,.54),(5,.60,.26),(6,.63,.42),(7,.55,.54),(8,.43,.54),(9,.34,.68),(10,.49,.84),(11,.57,.54),(12,.66,.68),(13,.51,.84)],
    "kneeling": [(0,.50,.12),(1,.50,.23),(2,.40,.25),(3,.36,.42),(4,.43,.55),(5,.60,.25),(6,.64,.42),(7,.57,.55),(8,.44,.52),(9,.39,.70),(10,.29,.86),(11,.56,.52),(12,.61,.70),(13,.71,.86)],
    "lying": [(0,.16,.45),(1,.27,.48),(2,.29,.39),(3,.42,.32),(4,.55,.28),(5,.29,.57),(6,.43,.62),(7,.56,.65),(8,.52,.45),(9,.68,.40),(10,.86,.35),(11,.52,.55),(12,.69,.60),(13,.87,.67)],
    "walking": [(0,.50,.12),(1,.50,.23),(2,.40,.25),(3,.33,.39),(4,.27,.53),(5,.60,.25),(6,.67,.38),(7,.72,.49),(8,.44,.52),(9,.52,.70),(10,.65,.91),(11,.56,.52),(12,.50,.72),(13,.38,.92)],
    "dancing": [(0,.50,.12),(1,.50,.23),(2,.40,.25),(3,.30,.16),(4,.22,.08),(5,.60,.25),(6,.70,.15),(7,.76,.07),(8,.44,.52),(9,.36,.69),(10,.22,.88),(11,.56,.52),(12,.62,.69),(13,.73,.86)],
}
LIMBS = [(1,2),(2,3),(3,4),(1,5),(5,6),(6,7),(1,8),(8,9),(9,10),(1,11),(11,12),(12,13),(0,1)]
COLORS = [(255,0,0),(255,85,0),(255,170,0),(255,255,0),(170,255,0),(85,255,0),(0,255,0),(0,255,85),(0,255,170),(0,255,255),(0,170,255),(0,85,255),(0,0,255)]


class HyperSpacePosePreset:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "preset": (list(PRESETS),),
            "width": ("INT", {"default": 768, "min": 64, "max": 1536, "step": 8}),
            "height": ("INT", {"default": 1024, "min": 64, "max": 1536, "step": 8}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "render"
    CATEGORY = "HyperSpace/conditioning"

    def render(self, preset="standing", width=768, height=1024):
        width, height = int(width), int(height)
        points = {i: (int(x * width), int(y * height))
                  for i, x, y in PRESETS.get(preset, PRESETS["standing"])}
        image = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(image)
        thickness = max(8, int(max(width, height) * .018))
        radius = max(5, thickness // 2)
        for (a, b), color in zip(LIMBS, COLORS):
            if a in points and b in points:
                draw.line((points[a], points[b]), fill=color, width=thickness)
        for index, point in points.items():
            color = COLORS[min(index, len(COLORS) - 1)]
            draw.ellipse((point[0]-radius, point[1]-radius,
                          point[0]+radius, point[1]+radius), fill=color)
        array = np.asarray(image, dtype=np.float32) / 255.0
        return (torch.from_numpy(array).unsqueeze(0),)


NODE_CLASS_MAPPINGS = {"HyperSpacePosePreset": HyperSpacePosePreset}
NODE_DISPLAY_NAME_MAPPINGS = {"HyperSpacePosePreset": "HyperSpace Pose Preset"}
