# SPDX-License-Identifier: Apache-2.0
"""Il creatore può chiedere un disegno in DM, non solo ricevere testo."""
import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.image_jobs import FAMIGLIA_SDXL, nuovo_job  # noqa: E402
from shared.prompt_immagine import prepara_prompt_canale, richiesta_immagine_smart  # noqa: E402
from shared.sketch import SKETCH_LATO, SKETCH_PASSI, negativo_sketch  # noqa: E402
from shared.instagram_vip import CREATOR_LEVEL  # noqa: E402

SOURCE = ROOT / "control-plane" / "main.py"


class Queue:
    def __init__(self):
        self.jobs = []

    def accoda(self, job):
        self.jobs.append(job)
        return job


def load(queue):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_queue_instagram_creator_image")
    scope = {
        "CREATOR_LEVEL": CREATOR_LEVEL,
        "richiesta_immagine_smart": richiesta_immagine_smart,
        "persona_store": SimpleNamespace(system_block=lambda: "Sono Anna"),
        "image_queue": queue,
        "nuovo_job": nuovo_job,
        "prepara_prompt_canale": prepara_prompt_canale,
        "negativo_sketch": negativo_sketch,
        "SKETCH_LATO": SKETCH_LATO,
        "SKETCH_PASSI": SKETCH_PASSI,
        "FAMIGLIA_SDXL": FAMIGLIA_SDXL,
        "push_log": lambda *args, **kwargs: None,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope["_queue_instagram_creator_image"]


class CreatorImageTests(unittest.TestCase):
    def test_creator_dm_request_is_queued_for_instagram_delivery(self):
        queue = Queue()
        request_image = load(queue)

        self.assertTrue(request_image(
            "123", "fammi un disegno di un faro", {"level": CREATOR_LEVEL}))
        self.assertEqual(len(queue.jobs), 1)
        self.assertEqual(queue.jobs[0]["canale"], "instagram")
        self.assertEqual(queue.jobs[0]["destinazione"], "123")
        self.assertEqual(queue.jobs[0]["richiedente"], "creatore")

    def test_non_creator_cannot_enqueue_an_image_through_creator_path(self):
        queue = Queue()

        self.assertFalse(load(queue)(
            "123", "fammi un disegno di un faro", {"level": "musa"}))
        self.assertEqual(queue.jobs, [])
