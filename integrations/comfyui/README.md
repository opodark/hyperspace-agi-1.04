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

## I pesi (il modello d'immagine)

I nodi HyperSpace scrivono il prompt; **il modello d'immagine** sono i pesi di
Qwen-Image 2.1, e si installano con lo script che li verifica:

```powershell
.\integrations\comfyui\install-model.ps1 -Check     # cosa farebbe: non scarica nulla
.\integrations\comfyui\install-model.ps1            # ~14 GB, una volta sola
```

Sono i GGUF **non censurati** (variante `-UC`) di
[`abenzerps/Qwen-Image-2.1-Uncensored-GGUF`](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF):
gli stessi pesi base di Qwen-Image 2.1, senza safety checker, quindi l'immagine
dipende solo dal prompt. Il quadro completo di **cosa filtra la catena e cosa no**
(nessun anello filtra: l'unico punto dove può nascere un rifiuto è il modello che
scrive il prompt) è in [`docs/comfyui.md`](../../docs/comfyui.md#cosa-filtra-e-cosa-no).
`modelli.json`, in questo componente, è il manifest
(revisione, file, impronte SHA-256, licenza) e dice dove va ogni file:

| Ruolo | File | Destinazione in `models\` | Peso |
|---|---|---|---|
| diffusion (GGUF) | `qwen-image-2.1-UC-Q5_K_M.gguf` | `diffusion_models\` | 4,86 GB |
| text encoder | `qwen3vl_8b_int8_convrot.safetensors` | `text_encoders\` | 8,71 GB |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | `vae\` | 0,63 GB |

`-Quant Q4_K_M` (4,29 GB) o `-Quant Q4_0` (3,87 GB) scaricano una quantizzazione
più piccola: è la scelta di `docs/comfyui.md` — con 8 GB di VRAM devono starci
insieme il diffusion in scheda e il text encoder in RAM.

Tre cose non ovvie, e il perché:

- **Il file prende il nome vero solo a impronta verificata.** Prima resta
  `<nome>.parziale`, che ComfyUI non vede: un GGUF troncato non dà un errore, dà
  un'immagine rumorosa o un OOM a metà campionamento. Un download interrotto si
  riprende rilanciando lo script (`curl -C -`), non ricomincia da zero.
- **Il lettore GGUF si controlla prima di scaricare.** Serve il fork
  [leejet/ComfyUI-GGUF](https://github.com/leejet/ComfyUI-GGUF): il vecchio
  city96 non conosce l'architettura `qwen_image21` e il grafo fallirebbe con
  `Unknown model architecture!` *dopo* i 14 GB.
  Va installato **nell'installazione che ComfyUI sta usando davvero**, che non è
  per forza la prima trovata: il 2026-09-23 i pesi c'erano ma il nodo no, e il
  grafo non partiva (`UnetLoaderGGUF` assente dall'elenco dei nodi). Si vede da
  `GET /object_info/UnetLoaderGGUF`: se la risposta è `{}` — è quello che ComfyUI
  risponde per un nodo che non ha — il fork manca:

  ```powershell
  $install = "$env:LOCALAPPDATA\Comfy-Desktop\ComfyUI-Installs\<nome>\ComfyUI"
  git clone --depth 1 https://github.com/leejet/ComfyUI-GGUF "$install\custom_nodes\ComfyUI-GGUF"
  & "$install\.venv\Scripts\python.exe" -m pip install -r "$install\custom_nodes\ComfyUI-GGUF\requirements.txt"
  # poi si riavviano i nodi di ComfyUI (ComfyUI-Manager: Restart, o l'app Desktop)
  ```
- **La cartella dei pesi si rileva, non si indovina**: si legge da
  `settings.json` di ComfyUI Desktop (`modelsDirs`). Con più installazioni si
  passa `-Modelli <percorso\models>`; se lo script non la trova, lo dice.

ComfyUI legge l'elenco dei file **all'avvio**: dopo l'installazione va riavviato
(o aggiornato), altrimenti il nome nuovo non compare fra le sue scelte.

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
- `modelli.json` — il manifest dei pesi: revisione, impronte SHA-256, licenza,
  destinazione di ogni file. Lo leggono `install-model.ps1` e i test.
- `install-model.ps1` — scarica e **verifica** i pesi (riprende un download
  interrotto, non installa un file la cui impronta non torna).
- `webui_gateway.py` — il proxy che Open WebUI attraversa per disegnare: libera la
  scheda prima del diffusion. Vedi la sezione qui sotto.
- `start-gateway.ps1` — l'avvio del gateway con le chiavi lette da `.env`
  (`CHANNEL_MODEL`, `IMAGE_FREE_GPU`): senza, lo scarico non parte e non lo dice.
- `tests/test_comfyui_modelli.py` (nel repo) — tiene il manifest e il grafo di
  `shared/image_jobs.py` allineati: un file rinominato da un lato solo fa fallire
  un test, non un job dopo tredici minuti di sampling.
- `tests/test_webui_gateway.py` (nel repo) — il gancio prima di `/prompt`, la lista
  dei percorsi inoltrabili e le intestazioni: il primo tentativo vero è fallito su
  un `Authorization: Bearer ` vuoto che httpx rifiuta.

## Il ponte (Fase 2): il control-plane chiede un'immagine, e ComfyUI la fa

`comfy_bridge.py` tira i job dal control-plane (ComfyUI ascolta su `127.0.0.1` e
il CP è in un container: **non può chiamarlo**) ed esegue il grafo che ha
funzionato su questa macchina — Qwen-Image 2.1 GGUF (variante `-UC`, non
censurata) con il text encoder da 8B **sulla CPU**, che è ciò che lascia VRAM al
diffusion. I pesi si installano con `install-model.ps1` (sezione precedente).

```powershell
python scripts\channel_token.py comfy --write     # una volta: crea il canale del ponte
$env:CHANNEL_TOKEN = "<token comfy>"

python integrations\comfyui\comfy_bridge.py --check   # ComfyUI, i pesi e il CP
python integrations\comfyui\comfy_bridge.py --once    # un job, poi esce
python integrations\comfyui\comfy_bridge.py           # in attesa, in ciclo
```

Chi chiede un'immagine mette un job in coda e va avanti — non aspetta il disegno:

```powershell
# mette in coda (torna subito con l'id del job)
Invoke-RestMethod -Uri http://127.0.0.1:8085/image/generate -Method Post `
  -Headers @{"X-Hyperspace-Channel-Token"=$env:CHANNEL_TOKEN} `
  -ContentType application/json -Body '{"prompt":"un faro nella tempesta, lunga esposizione"}'

# dov'è finita? coda e ultimi job
Invoke-RestMethod -Uri http://127.0.0.1:8085/image/status `
  -Headers @{"X-Hyperspace-Channel-Token"=$env:CHANNEL_TOKEN}
```

**Costo misurato**: 1024×1024, 30 passi = **11 min 50 s** su questa 5060. Il
default (768×768, 25 passi) è pensato per una risposta conversazionale.

## Il gateway (Fase 3): l'immagine chiesta da Open WebUI

Open WebUI ha un motore `comfyui` che chiama ComfyUI da sé, e quella strada **salta
la regola del progetto** — "una scheda, un modello": il suo unico gancio
(`shared/gpu_budget.py`) sta nel control-plane, che la WebUI non attraversa. Con il
modello del canale in scheda (5259 MB su 8151, misurato il 2026-09-23) il diffusion
non ci sta, e il risultato non è un errore chiaro: è `CUDA error: unknown error`, e
ComfyUI che poi non riparte da solo.

`webui_gateway.py` è il proxy che sta in mezzo e fa una cosa sola in più rispetto a
ComfyUI: su `POST /prompt` chiede prima a Ollama cosa tiene in scheda (`/api/ps`) e
glielo fa scaricare (`keep_alive: 0`), poi inoltra. `/history`, `/view`,
`/system_stats`, `/object_info`, `/api/upload/image` e il WebSocket `/ws` passano
così com'è — il WebSocket serve a Open WebUI per sapere quando l'esecuzione è finita,
e senza tunnel la generazione non tornerebbe mai.

```powershell
.\scripts\start-surfaces.ps1 -Gateway                       # con le altre superfici
python integrations\comfyui\webui_gateway.py --check        # ComfyUI e scheda
.\integrations\comfyui\start-gateway.ps1                    # chiavi lette da .env
```

La WebUI si punta qui e non a ComfyUI: `COMFYUI_BASE_URL=http://host.docker.internal:8189`.
Le variabili (grafo compreso) le scrive `scripts\webui_image_env.py` — `--write` in
`.env`, `--apply` per mandarle all'istanza accesa (da 0.11 la configurazione delle
immagini è persistita nel database, quindi `.env` da solo vale solo al primo avvio),
`--check` per accorgersi se grafo del repo e grafo configurato hanno smesso di
coincidere.

Misure del 2026-09-23 (512×512, 6 passi): **93,5 s** a freddo, **15,8 s** con il
modello in cache. Con Ollama che teneva il modello del canale la generazione è
passata lo stesso — prima lo scarico, poi il diffusion.


