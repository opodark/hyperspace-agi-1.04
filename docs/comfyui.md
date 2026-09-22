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

Non è un'ipotesi: il 2026-09-22, generando il ritratto di Aurora (`docs/social.md`),
ComfyUI è morto con `torch.AcceleratorError: CUDA error: unknown error` nel
KSampler. I numeri di `nvidia-smi`: 8151 MiB totali, **6170 occupati da Ollama**
(`llama-server`, il modello del canale tenuto residente 12 ore da
`OLLAMA_KEEP_ALIVE=12h`), **1730 liberi**. Il diffusion non ci stava. Peggio: dopo
quell'errore la coda di ComfyUI resta con `queue_running` vuoto e il job dentro
`queue_pending` — l'esecutore non riparte da solo, e ComfyUI va riavviato.

Da qui **una scheda, un modello**, che è una decisione dichiarata e non una speranza:
prima di accodare un'immagine il control-plane chiede a Ollama di scaricare
`CHANNEL_MODEL` (`keep_alive: 0`) — `shared/gpu_budget.py` decide, `/image/generate`
esegue, e l'esito compare nella risposta (`scheda`) e nei log. Ollama ricarica il
modello alla prima richiesta: si paga qualche secondo al primo messaggio dopo
un'immagine, non si paga un crash. Si spegne con `IMAGE_FREE_GPU=false`.

E una misura che vale più di un tetto: con la scheda occupata la stessa generazione
è passata da 313s a oltre 700s. Il claim della coda è stato tarato di conseguenza
(`DEFAULT_CLAIM_TTL_S` = 1800s, sopra il timeout del ponte di 900s), altrimenti un
job ancora in corso veniva considerato morto e rieseguito.


Da qui la divisione del lavoro, che è anche la tesi del progetto:

| | Dove | Perché |
|---|---|---|
| **Il testo** (scrivere il prompt, capire l'idea) | sulla **rete** HyperSpace | la scheda resta libera per il diffusion; il linguaggio è un lavoro piccolo e parallelizzabile |
| **L'immagine** (i passi di sampling) | **locale**, in ComfyUI | è la parte che vuole GPU, banda di memoria e il modello grande |

## Le due direzioni

```
   Fase 1 (fatta)                     Fase 2 (fatta: il ponte)
ComfyUI ──/v1/chat/completions──> CP      CP ──/image/jobs──> ponte ──> ComfyUI
   "scrivimi il prompt"                    "genera questa immagine"
```

**Fase 1 — ComfyUI chiede al control-plane.** Due nodi:

- `HyperSpacePrompt`: idea e stile in ingresso, il prompt in uscita verso
  `CLIPTextEncode`. La richiesta entra dal percorso OpenAI-compatibile del CP.
- `HyperSpaceMesh`: stato della rete (`vivo`, nodi, ricordi) da `/health`.

**Fase 2 — il control-plane chiede un'immagine, e il ponte la esegue.** Non è
simmetrica, e il motivo è tecnico: ComfyUI ascolta su `127.0.0.1:8188` e il
control-plane è in un container, quindi **non può chiamarlo**. La soluzione è un
ponte che **tira** il lavoro, come i driver di canale:

| Endpoint (token di canale) | Chi | Cosa |
|---|---|---|
| `POST /image/generate` | chiunque abbia un token | mette in coda un job e torna subito |
| `GET /image/jobs` | il ponte | ritira il prossimo job (`204` = niente da fare) |
| `POST /image/result` | il ponte | riferisce esito, file, durata |
| `GET /image/status` | l'operatore | coda e ultimi job: "dov'è finita la mia immagine?" |

Il ponte è `integrations/comfyui/comfy_bridge.py` e si autentica con un canale
`comfy` in `CHANNEL_CLIENTS` (`python scripts/channel_token.py comfy --write`):
una superficie esterna come le altre, non un'eccezione alla regola.

```powershell
python integrations\comfyui\comfy_bridge.py --check   # non genera nulla
python integrations\comfyui\comfy_bridge.py --once    # un job ed esce
python integrations\comfyui\comfy_bridge.py           # in attesa, in ciclo
```

In ciclo il ponte prende un lucchetto (`data/comfy-bridge.lock`, vedi
`shared/single_instance.py`): un **secondo** ponte non parte e lo scrive nel log.
Due ponti non si pestano i piedi in modo visibile — prendono entrambi un job e la
scheda li esegue in parallelo, il doppio del tempo per ognuno su un budget di 8 GB
di VRAM. `--check` e `--once` non prendono il lucchetto: il primo non esegue
niente, il secondo è pensato per girare una volta sola. Il lucchetto è del sistema
operativo, quindi muore con il processo: non restano file da cancellare a mano.

**Costo misurato** su questa macchina (RTX 5060 Laptop, Qwen-Image 2.1 Q5_K,
text encoder su CPU): **1024×1024, 30 passi → 11 minuti e 50 s** (710 s). Un
job da 768×768/25 passi è la misura ragionevole per una risposta conversazionale;
il default di `/image/generate` è esattamente quello.

### Fase 3 — fatta e verificata

- **`!immagine <idea>` nel canale**: il comando entra dalla chat, il control-plane
  risponde subito ("La disegno: 768x768, 25 passi") e mette il job in coda. Chi può
  chiederlo: l'**operatore** (`CHANNEL_OPERATOR` nel `.env`); senza quella variabile
  il comando è aperto a chiunque sia in chat — una scelta, non un caso, ma da fare
  sapendo che la scheda è una sola.
- **La consegna**: `GET /channel/outbox` (il driver tira le immagini pronte) +
  `POST /channel/outbox/ack`. Il file lo ha il driver, la destinazione l'ha decisa
  chi ha chiesto: si incontrano nell'outbox, e il driver manda la foto con
  `sendPhoto`. Un file che non c'è non viene confermato — il tentativo si ripete.

Prova reale (2026-09-22): `!immagine una torre sulla scogliera al tramonto` →
coda → ponte → **313 s** → `output\HyperSpace\bridge_00001_.png` → **inviata in
chat**. `da_consegnare: 0` dopo l'ack: consegnata una volta sola.

### Fase 4 — da fare

- il **tool** `image_generate` per le superfici che HANNO i tool (console, web
  node): lì il modello può decidere di disegnare, qui il canale resta a comando;
- il prompt scritto con la **memoria della stanza**: la conversazione di
  "ultramind" può diventare il materiale dell'immagine.



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
