# SPDX-License-Identifier: Apache-2.0
"""Esegue la rotta reale senza avviare server o generazioni."""
import ast
from types import SimpleNamespace

from tests import cp_source

from shared.image_jobs import (FAMIGLIA_SD15, FAMIGLIA_SDXL, LATO_CONSIGLIATO,
                               MODELLO_SD15, RIFERIMENTO_FORZA_DEFAULT,
                               ImmagineQueue, nuovo_job, usa_checkpoint, workflow)
from shared.sketch import SKETCH_LATO, SKETCH_PASSI, negativo_sketch


def generate(payload):
    # Non più `main.py`: la funzione ora sta in cp/immagini.py, e `cp_source`
    # sa già dove guardare. Il resto del test non cambia.
    fn = cp_source.funzioni()['image_generate']
    fn.decorator_list = []
    scope = dict(FAMIGLIA_SDXL=FAMIGLIA_SDXL, FAMIGLIA_SD15=FAMIGLIA_SD15,
                 LATO_CONSIGLIATO=LATO_CONSIGLIATO, usa_checkpoint=usa_checkpoint,
                 RIFERIMENTO_FORZA_DEFAULT=RIFERIMENTO_FORZA_DEFAULT,
                 SKETCH_LATO=SKETCH_LATO,
                 SKETCH_PASSI=SKETCH_PASSI, negativo_sketch=negativo_sketch,
                 nuovo_job=nuovo_job,
                 # la coda arriva dal boot attraverso il contesto del dominio
                 _contesto=SimpleNamespace(image_queue=ImmagineQueue(),
                                           image_memory_gate=None,
                                           connector_manager=None, diario=None),
                 request=SimpleNamespace(get_json=lambda **kw: payload),
                 _channel_error=lambda: None, _channel_name=lambda: 'webui',
                 _libera_scheda_per_immagine=lambda: '',
                 push_log=lambda *a, **kw: None, jsonify=lambda x: x)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'cp/immagini.py', 'exec'),
         scope)
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


def test_la_famiglia_sd15_ha_i_suoi_default_e_il_suo_modello():
    """SD 1.5 a 1024 px ripete l'anatomia e a CFG 5 resta tiepido: i default della
    famiglia non possono essere quelli di SDXL. E il file non e' quello del Pony.
    """
    result, status = generate({'prompt': 'una ragazza illustrata',
                               'famiglia': FAMIGLIA_SD15})
    assert status == 201
    job = result['job']
    assert job['famiglia'] == FAMIGLIA_SD15
    assert job['larghezza'] == 768 == LATO_CONSIGLIATO[FAMIGLIA_SD15]
    assert job['modello_effettivo'] == MODELLO_SD15['ckpt']
    graph = workflow(job)
    assert graph['451']['inputs']['ckpt_name'] == MODELLO_SD15['ckpt']
    assert graph['458']['inputs']['cfg'] == 7.0
    assert 'bad anatomy' in job['negativo'], 'anche SD 1.5 usa il negativo di qualità'


def test_il_fix_e_una_scelta_del_chiamante_e_arriva_al_grafo():
    """Il formato dei demo (512x768 piu' il fix a 2x) si chiede dalla rotta, e
    nessuno lo interpreta per strada: l'unico posto che lo legge e' il grafo."""
    result, status = generate({'prompt': 'una ragazza illustrata',
                               'famiglia': FAMIGLIA_SD15, 'larghezza': 512,
                               'altezza': 768, 'fix': 2})
    assert status == 201
    job = result['job']
    assert job['fix'] == 2
    assert (job['larghezza'], job['altezza']) == (512, 768)
    graph = workflow(job)
    assert graph['476']['inputs']['width'] == 1024
    assert graph['476']['inputs']['width'] == 1024
    assert graph['470']['inputs']['images'] == ['476', 0]
    for node in ('472', '473', '477'):
        assert node not in graph


def test_il_peso_del_riferimento_e_quello_di_image_jobs_non_uno_scritto_qui():
    """La rotta non tiene una copia del default: se ce l'avesse, sarebbero due numeri
    che si allontanano, e a divergere sarebbe quella che si legge meno.
    """
    result, status = generate({'prompt': 'una ragazza illustrata',
                               'famiglia': FAMIGLIA_SD15,
                               'reference_image': 'anna-volto.png'})
    assert status == 201
    job = result['job']
    assert job['reference_image'] == 'anna-volto.png'
    assert job['reference_strength'] == RIFERIMENTO_FORZA_DEFAULT
    graph = workflow(job)
    assert graph['465']['inputs']['image'] == 'anna-volto.png'
    assert graph['468']['inputs']['weight'] == RIFERIMENTO_FORZA_DEFAULT
    result, _ = generate({'prompt': 'una ragazza illustrata',
                          'famiglia': FAMIGLIA_SD15,
                          'reference_image': 'anna-volto.png',
                          'reference_strength': 0.5})
    assert result['job']['reference_strength'] == 0.5
    assert workflow(result['job'])['468']['inputs']['weight'] == 0.5


def test_un_riferimento_per_la_famiglia_senza_adattatore_non_passa_dal_grafo():
    """Il campo si accetta come tutti gli altri (la rotta mette in coda, non giudica),
    ma il grafo non lo ignora: la famiglia senza IP-Adapter si ferma, perche' un
    ritratto «riuscito» con un altro volto e' il difetto che il riferimento toglie.
    """
    result, status = generate({'prompt': 'una ragazza illustrata',
                               'famiglia': FAMIGLIA_SDXL,
                               'reference_image': 'anna-volto.png'})
    assert status == 201
    try:
        workflow(result['job'])
    except ValueError as errore:
        assert 'IP-Adapter' in str(errore)
    else:  # pragma: no cover - fallisce se il grafo torna a tacere
        raise AssertionError('il riferimento su SDXL deve fermarsi, non sparire')


def test_senza_fix_la_rotta_non_cambia_nulla():
    result, _ = generate({'prompt': 'a lighthouse', 'famiglia': FAMIGLIA_SDXL})
    assert result['job']['fix'] == 0
    grafo = workflow(result['job'])
    for nodo in ('471', '474', '475', '476', '477'):
        assert nodo not in grafo
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
