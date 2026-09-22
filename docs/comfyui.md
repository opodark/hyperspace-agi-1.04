# ComfyUI — il volto visivo di HyperSpace sul nodo win11

ComfyUI gira sul nodo **win11** (ComfyUI Desktop 0.37, RTX 5060 Laptop, 8 GB) ed è
il posto dove nascono le immagini. Questo documento dice perché il componente vive
solo lì, qual è il contratto con il control-plane e cosa manca.

Implementazione: [`integrations/comfyui/`](../integrations/comfyui/README.md)
(client, nodi, installer, test).

## La ragione della scelta: 8 GB di VRAM sono un budget

Su questa scheda un modello di linguaggio e un modello d'immagine **non stanno
insieme**. Qwen-Image 2.1 in GGUF Q5_K con il suo text encoder da 8B prende quasi
tutta la memoria: la prova fatta in casa gira a 768×768, 25 passi, ed è già al
limite.

Da qui la divisione del lavoro, che è anche la tesi del progetto:

| | Dove | Perché |
|---|---|---|
| **Il testo** (scrivere il prompt, capire l'idea) | sulla **rete** HyperSpace | la scheda resta libera per il diffusion; il linguaggio è un lavoro piccolo e parallelizzabile |
| **L'immagine** (i passi di sampling) | **locale**, in ComfyUI | è la parte che vuole GPU, banda di memoria e il modello grande |

## Le due direzioni, e quale c'è

```
   Fase 1 (fatta)                     Fase 2 (da fare)
ComfyUI ──/v1/chat/completions──> CP      CP ──?──> ComfyUI
   "scrivimi il prompt"                    "genera questa immagine"
```

**Fase 1 — ComfyUI chiede al control-plane.** Due nodi:

- `HyperSpacePrompt`: idea e stile in ingresso, il prompt in uscita verso
  `CLIPTextEncode`. La richiesta entra dal percorso OpenAI-compatibile del CP.
- `HyperSpaceMesh`: stato della rete (`vivo`, nodi, ricordi) da `/health`.

**Fase 2 — il control-plane chiede un'immagine.** Non è simmetrica, e il motivo è
tecnico: ComfyUI ascolta su `127.0.0.1:8188` e il control-plane è in un
container, quindi **non può chiamarlo**. Le due strade:

1. esporre ComfyUI sulla rete (aprire una porta, e la logica di autorizzazione
   diventa un problema nuovo);
2. **un bridge che tira** (`comfy_bridge.py` sul host, come i driver di canale):
   interroga il CP per i job immagine, esegue il workflow via API di ComfyUI,
   pubblica l'esito. Nessuna porta in ingresso, stesso token di canale, stessa
   disciplina di `docs/channel.md`.

Il repo ha già scelto questa forma tre volte (`web_node`, i canali, il web node
del browser): **il client tira, il control-plane decide**. La Fase 2 seguirà
quella, non l'apertura di una porta.

## Il contratto con il control-plane

Una richiesta, un modello, nessun tool:

```http
POST http://127.0.0.1:8085/v1/chat/completions
X-Hyperspace-Tools: off
Content-Type: application/json

{ "model": "", "messages": [ ... ], "stream": false, "surface": "comfyui" }
```

### `surface: comfyui` — dove parla l'agente

Il documento d'identità è **uno** e non cambia con il mezzo; il contesto del mezzo
è un layer separato che dice *dove* sta parlando e *come adattarsi*
(`SURFACE_CONTEXTS` in `shared/persona.py`). La voce `comfyui` chiede solo il
prompt: nessun preambolo, nessuna spiegazione, nessun markdown — perché tutto ciò
che il modello aggiunge finisce **dentro la condizionatura di CLIP**, e si vede
nell'immagine.

### `X-Hyperspace-Tools: off` — perché un flag e non un'euristica

Il control-plane inietta i suoi tool (web_search, omega_*, get_mesh_status)
quando il modello è tool-capable — e `qwen3.5:4b` **contiene** il pattern
`qwen3`, quindi lo è. Una richiesta "scrivimi un prompt" diventava così un giro di
ricerca: **due chiamate al modello**, latenza doppia e un testo che nessuno aveva
chiesto.

Il flag spegne **solo** i tool aggiunti dal control-plane: quelli passati dal
client restano suoi. È una richiesta esplicita, non un'euristica — il CP non
indovina mai l'intenzione di un client.

## Verifica (come si sa che funziona)

1. `install.ps1 -Check` → trova `custom_nodes`, dice se è installato e allineato.
2. L'import dal **python di ComfyUI** (l'interprete vero, con torch): se
   `import hyperspace_nodes` riesce, l'app caricherà i nodi.
3. Il nodo in un grafo, con `report` collegato: dice quale modello ha scritto il
   prompt e da quanti caratteri.
4. Il controllo che non mente: nei log del control-plane deve comparire **una**
   riga di decisione per l'esecuzione —
   `CP decision: model=… tools=0 think=False` — non due.

## Limiti noti

- **Una chiamata per esecuzione**, sincrona: il nodo aspetta il modello. Con i
  modelli piccoli di oggi sono ~10-20 s; con un modello grande può diventare un
  minuto, e il timeout dell'ingresso è lì per questo.
- **Il prompt è in inglese** di default: i modelli text-to-image sono addestrati
  così. La lingua dell'*idea* non conta, la traduzione la fa la rete.
- **Nessun job asincrono**: non c'è coda, non c'è ritentativo. Se il CP è giù, il
  nodo lo dice subito.
- **La memoria non c'entra**: questi nodi non scrivono nella memoria di Aurora.
  Gli esperimenti visivi non sono fatti su di sé, e il self-model resta pulito
  (stessa disciplina di `docs/dreams.md`).

## Roadmap

- **Fase 2** — `comfy_bridge.py` (pull) + tool `image_generate` per Aurora +
  consegna dell'immagine su Telegram. Il pezzo grosso è il contratto nel CP.
- **Fase 3** — ComfyUI come **capability della mesh**: il nodo si annuncia come
  "painter", il control-plane instrada lì i job immagini come oggi instrada la
  chat sui nodi (vedi `docs/architecture.md`).
- **Fase 4** — il prompt scritto da Aurora *con la memoria della stanza* (i canali
  hanno già un contesto: se la conversazione è in "ultramind", l'immagine può
  nascere da quella).
