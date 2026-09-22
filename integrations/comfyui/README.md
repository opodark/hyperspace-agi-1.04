# Nodi HyperSpace per ComfyUI

Aurora **scrive il prompt** per il modello d'immagine: l'idea la dai in italiano,
il testo che arriva a CLIP lo scrive la rete HyperSpace. Il modello di linguaggio
non occupa la VRAM che serve al diffusion — con una 5060 da 8 GB è la differenza
tra generare e non generare.

Questo componente vive **solo sul nodo win11**, perché è lì che c'è ComfyUI. Il
codice sta nel repo (versionato, con i suoi test) e ComfyUI lo vede attraverso una
junction: una copia divergerebbe in silenzio.

## Installazione

```powershell
.\integrations\comfyui\install.ps1 -Check    # dice cosa farebbe, non tocca nulla
.\integrations\comfyui\install.ps1           # crea la junction nei custom_nodes
```
Poi in ComfyUI: **Refresh** del pannello nodi (o riavvio dell'app). I nodi
compaiono nella categoria **HyperSpace**.

Per togliere l'installazione senza toccare il repo:
```powershell
.\integrations\comfyui\install.ps1 -Remove
```
La junction si rimuove con `rmdir` (rimuove il collegamento, non il contenuto):
con `Remove-Item -Recurse -Force` il rischio è cancellare il sorgente.

Se hai più installazioni di ComfyUI: `-CustomNodes <percorso\custom_nodes>`.

## I due nodi

| Nodo | Ingressi | Uscite | A cosa serve |
|---|---|---|---|
| **Aurora scrive il prompt** | `idea`, `stile`, `variante` (+ opzionali) | `prompt`, `report` | il testo da collegare a `CLIPTextEncode` |
| **Stato della rete HyperSpace** | `control_plane`, `timeout_s` | `vivo`, `nodi`, `report` | vedere se lo stack risponde; `vivo` può condizionare un ramo |

Collega `prompt` al **CLIPTextEncode positivo**. Il negativo scrivilo tu: un
modello che scrive anche il negativo tende a ripetere gli stessi concetti
all'incontrario. `report` dice da quale modello è arrivato il prompt (collegalo a
un nodo di testo, o lascialo scollegato).

### `variante`: perché esiste

ComfyUI tiene in cache i nodi in base ai loro ingressi: con la stessa idea e lo
stesso stile **non richiama la rete** e ti ridà il prompt di prima. È voluto (un
grafo deve essere ripetibile): per un prompt nuovo cambia `variante`.

## Configurazione

- `control_plane` — base URL del gateway HyperSpace (default `http://127.0.0.1:8085`).
- `modello` — vuoto = il modello di default del control-plane.
- `lingua` — i modelli text-to-image rispondono meglio in **inglese** (default).
- `max_caratteri` — tetto del prompt (default 600).
- `temperatura` — 0.8 di default: più alta = più varia.

## Cosa non fa (e cosa fare se non risponde)

- **Non genera immagini da sé**: qui ComfyUI resta il regista, il control-plane
  scrive soltanto. Il contrario (Aurora che *chiede* un'immagine) è la Fase 2 di
  `docs/comfyui.md`.
- **Non usa i tool** della rete (ricerca web, memoria): la richiesta parte con
  `X-Hyperspace-Tools: off`, così è **una sola chiamata** e la latenza è
  prevedibile. Senza quel flag il control-plane inietta i suoi tool e "scrivimi
  un prompt" diventa un giro di web_search.
- **Se il control-plane è giù**, il nodo del prompt **fallisce con un errore
  visibile** (niente ripieghi silenziosi: un prompt sostituito dall'idea grezza
  produce un'immagine sbagliata senza dirlo). Il nodo dello stato invece non
  solleva: il suo mestiere è dirlo.

Controlli rapidi:
```powershell
.\scripts\start.ps1 -Check                       # lo stack è su?
curl http://127.0.0.1:8085/health                # la rete risponde?
python -m pytest tests\test_comfyui_client.py -q # la logica del client
```

## Dentro: dove sta cosa

- `hyperspace_client.py` — la logica e l'unico punto che tocca la rete: solo
  libreria standard, trasporto iniettabile, quindi si testa senza ComfyUI, senza
  torch e senza server acceso.
- `nodes.py` — i nodi: adattatori sottili, nessun `import torch`.
- `install.ps1` — la junction (crea, verifica, rimuove).
