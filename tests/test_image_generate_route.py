# SPDX-License-Identifier: Apache-2.0
"""Esegue la rotta reale senza avviare server o generazioni."""
import ast
from pathlib import Path
from types import SimpleNamespace

from shared.image_jobs import FAMIGLIA_SDXL, ImmagineQueue, nuovo_job, workflow
from shared.sketch import SKETCH_LATO, SKETCH_PASSI, negativo_sketch


def generate(payload):
    path = Path(__file__).resolve().parents[1] / 'control-plane/main.py'
    fn = next(n for n in ast.parse(path.read_text()).body
              if isinstance(n, ast.FunctionDef) and n.name == 'image_generate')
    fn.decorator_list = []
    scope = dict(FAMIGLIA_SDXL=FAMIGLIA_SDXL, SKETCH_LATO=SKETCH_LATO,
                 SKETCH_PASSI=SKETCH_PASSI, negativo_sketch=negativo_sketch,
                 nuovo_job=nuovo_job, image_queue=ImmagineQueue(),
                 request=SimpleNamespace(get_json=lambda **kw: payload),
                 _channel_error=lambda: None, _channel_name=lambda: 'webui',
                 _libera_scheda_per_immagine=lambda: '',
                 push_log=lambda *a, **kw: None, jsonify=lambda x: x)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), scope)
    return scope['image_generate']()


def test_sdxl_api_uses_cyberrealistic_and_appropriate_defaults():
    result, status = generate({'prompt': 'a lighthouse', 'famiglia': FAMIGLIA_SDXL})
    assert status == 201
    job = result['job']
    assert job['famiglia'] == FAMIGLIA_SDXL
    graph = workflow(job)
    assert graph['451']['class_type'] == 'CheckpointLoaderSimple'
    assert graph['458']['inputs']['steps'] == 30
    assert graph['456']['inputs']['width'] == 1024
    assert 'bad anatomy' in job['negativo']
    assert job['modello_effettivo'] == graph['451']['inputs']['ckpt_name']


def test_qwen_default_and_explicit_parameters_are_preserved():
    result, _ = generate({'prompt': 'a lighthouse'})
    assert result['job']['famiglia'] == 'qwen-image-2.1'
    assert result['job']['passi'] == 25
    result, _ = generate({'prompt': 'a lighthouse', 'famiglia': FAMIGLIA_SDXL,
                          'passi': 35, 'larghezza': 768, 'negativo': '',
                          'modello': 'custom.safetensors'})
    job = result['job']
    assert job['passi'] == 35
    assert job['larghezza'] == 768
    assert job['negativo'] == ''
    assert workflow(job)['451']['inputs']['ckpt_name'] == 'custom.safetensors'
