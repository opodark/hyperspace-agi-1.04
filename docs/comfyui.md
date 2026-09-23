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

I pesi sono i GGUF **non censurati** (`-UC`) di
[`abenzerps/Qwen-Image-2.1-Uncensored-GGUF`](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF)
— stessi pesi base di Qwen-Image 2.1 senza safety checker, quindi l'immagine
dipende solo dal prompt — e si installano con
`.\integrations\comfyui\install-model.ps1`: il manifest (`integrations/comfyui/modelli.json`)
dice revisione, impronta SHA-256 e destinazione di ogni file, e il file prende il
nome vero solo quando l'impronta torna. Il *perché* sta in
[`integrations/comfyui/README.md`](../integrations/comfyui/README.md).

Non è un'ipotesi: il 2026-09-22, generando il ritratto di Aurora (`docs/social.md`),
ComfyUI è morto con `torch.AcceleratorError: CUDA error: unknown error` nel
KSampler. I numeri di `nvidia-smi`: 8151 MiB totali, **6170 occupati da Ollama**
(`llama-server`, il modello del canale tenuto residente 12 ore da
`OLLAMA_KEEP_ALIVE=12h`), **1730 liberi**. Il diffusion non ci stava. Peggio: dopo
quell'errore ComfyUI **non è più utilizzabile** — il suo server risponde `HTTP 500
Server got itself in trouble`, la coda resta con `queue_running` vuoto, i prompt
nuovi entrano in `queue_pending` e non partono mai. Da fuori non si ripara: va
riavviato ComfyUI. Riconoscerlo è facile: `comfy_bridge.py --check` lo dice, e
`/queue` mostra `pending` che non scende.

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

## Le due direzioni (e la terza, dal lato WebUI)

```
   Fase 1 (fatta)                     Fase 2 (fatta: il ponte)
ComfyUI ──/v1/chat/completions──> CP      CP ──/image/jobs──> ponte ──> ComfyUI
   "scrivimi il prompt"                    "genera questa immagine"

   Fase 3 (fatta: il gateway)              Open WebUI ──/prompt──> gateway ──> ComfyUI
                                              "generami un'immagine"
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
- **«Mandami una foto di X», a parole**: la stessa cosa senza sintassi. Il
  riconoscitore è `richiesta_immagine` in `shared/image_jobs.py`: tre regole con
  un nome (`mandare`, `potere-infinito`, `volere`) che finisce nei log
  (`via=…`), e tutto il resto è silenzio — un falso positivo non è un fastidio,
  è un quarto d'ora di scheda occupata. Qui la guardia è **più severa** che per
  il comando: vale solo per l'operatore, e **senza `CHANNEL_OPERATOR` la strada
  resta chiusa** (fail-closed), perché una frase male interpretata non si vede
  mentre un comando scritto male sì. La risposta non dice mai che la foto è
  arrivata: dice che è in coda e che arriva — l'immagine la consegna il driver.
- **La consegna**: `GET /channel/outbox` (il driver tira le immagini pronte) +
  `POST /channel/outbox/ack`. Il file lo ha il driver, la destinazione l'ha decisa
  chi ha chiesto: si incontrano nell'outbox, e il driver manda la foto con
  `sendPhoto`. Un file che non c'è non viene confermato — il tentativo si ripete.

Prova reale (2026-09-22): `!immagine una torre sulla scogliera al tramonto` →
coda → ponte → **313 s** → `output\HyperSpace\bridge_00001_.png` → **inviata in
chat**. `da_consegnare: 0` dopo l'ack: consegnata una volta sola.

Prova reale con i pesi **non censurati** (`-UC`, quelli che `install-model.ps1`
installa) e un'idea esplicita scritta in italiano: coda → ponte → **150,8 s** →
`output\HyperSpace\bridge_00003_.png` (768×768, 25 passi, scheda libera, modello
già in VRAM). Il testo che è arrivato a ComfyUI è esattamente quello scritto: su
questo percorso non c'è nessun riscrittore, e i test di
`tests/test_channel_immagine.py` lo tengono così.

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

1. `install-model.ps1 -Check` → i pesi ci sono e l'impronta torna? Il lettore GGUF
   conosce `qwen_image21`? (Non scarica niente: dice cosa farebbe.)
2. `install.ps1 -Check` → trova `custom_nodes`, dice se è installato e allineato.
3. L'import dal **python di ComfyUI** (l'interprete vero, con torch): se
   `import hyperspace_nodes` riesce, l'app caricherà i nodi.
4. Il nodo in un grafo, con `report` collegato: dice quale modello ha scritto il
   prompt e da quanti caratteri.
5. Il controllo che non mente: nei log del control-plane deve comparire **una**
   riga di decisione per l'esecuzione —
   `CP decision: model=… tools=0 think=False` — non due.

## La generazione dentro Open WebUI (il gateway)

Open WebUI 0.11 sa chiamare ComfyUI da sé — motore `comfyui`: `POST /prompt`, il
WebSocket `/ws` su cui aspetta la fine dell'esecuzione, poi `/history` e `/view` per
il file. Quella strada però **salta la regola del progetto** "una scheda, un
modello", perché il suo unico gancio (`shared/gpu_budget.py`) sta nel control-plane,
che la WebUI non attraversa. Il 2026-09-22 la contesa si è presentata come `CUDA
error: unknown error` (6170 MiB a Ollama su 8151, 1730 liberi): è esattamente il
caso per cui il gateway esiste.

```
            POST /prompt (grafo)                    libera Ollama        pesi
Open WebUI ─────────────────> gateway :8189 ───────> (keep_alive 0) ───> ComfyUI :8188
     ▲                            │                                          │
     └── tutto il resto: /history, /view, /system_stats, /ws (tunnel) ───────┘
```

`integrations/comfyui/webui_gateway.py` è un proxy locale davanti a ComfyUI: su
`POST /prompt` chiede **prima** a Ollama cosa tiene in scheda (`/api/ps`) e glielo fa
scaricare, poi inoltra; tutto il resto passa così com'è, compreso il tunnel
WebSocket, senza il quale la generazione non finirebbe mai. Non tocca il prompt e non
giudica il risultato: è un guardiano di memoria, non un filtro. La lista dei percorsi
inoltrabili è corta di proposito (`prompt`, `history`, `view`, `system_stats`,
`object_info`, `queue`, `interrupt`, `free`, `api/`).

Le variabili che la WebUI legge **non si scrivono a mano**: sono derivate dal grafo
che il ponte esegue, con `python scripts/webui_image_env.py --write` (in `.env` e
`.env.windows`) e `--apply`, che le manda all'API admin della WebUI come farebbe il
pannello *Images*. Su un'istanza già avviata la configurazione è **persistita nel
database**: modificare solo `.env` non basta (le variabili valgono al primo avvio),
quindi si passa da `--apply`; `--check` dice se il grafo del repo e quello che la
WebUI ha in mano hanno smesso di coincidere.

```powershell
.\scripts\start-surfaces.ps1 -Gateway             # il gateway, con le altre superfici
python integrations\comfyui\webui_gateway.py --check
python scripts\webui_image_env.py --write         # variabili in .env
python scripts\webui_image_env.py --apply         # le applica alla WebUI accesa
```

Misure del 2026-09-23 (512×512, 6 passi, rotta `/api/v1/images/generations`): 93,5 s
con i pesi da caricare, 15,8 s con il modello già in cache. La prova che conta è
l'altra: con Ollama che teneva `qwen3.5:4b` (5259 MB in scheda, 2224 liberi) la
generazione è passata lo stesso, **dopo** lo scarico — il log del gateway dice
`scheda liberata: qwen3.5:4b scaricato dalla memoria`. I default da conversazione
restano 768×768 e 25 passi (`IMAGE_SIZE`, `IMAGE_STEPS`).

**Chiedere l'immagine in chat.** Open WebUI 0.11 offre al modello un tool nativo
`generate_image` che chiama questa stessa rotta: chiedendolo in chat, l'immagine
compare nel messaggio. Perché funzioni, il tool deve tornare **a Open WebUI**, che
è chi sa eseguirlo: il control-plane esegue solo i tool suoi e restituisce al
chiamante gli altri (`docs/connectors.md`, `tests/test_tool_passthrough.py`). Fino
al 2026-09-23 non succedeva: il CP rispondeva «non gestito», la chiamata moriva lì e
il modello raccontava di aver mandato un file che non esisteva.

## Cosa filtra, e cosa no

Una riga detta male qui diventa un'aspettativa sbagliata, quindi va detta bene:
**nella catena HyperSpace non c'è nessun filtro di contenuto**, e l'unico punto in
cui un rifiuto può nascere è il modello di linguaggio — solo quando è *lui* a
scrivere il prompt.

| Passaggio | Filtra? | Dove si legge |
|---|---|---|
| `POST /image/generate` | **No**: il prompt entra nel job verbatim | `control-plane/main.py` (`image_generate`) |
| `!immagine <idea>` in chat | **No**: l'idea va al job come è stata scritta | `control-plane/main.py` (`_channel_immagine`) |
| Richiesta **a parole** («mandami una foto di X») | **No**: stessa cosa, con regole dichiarate e nessuna riscrittura | `shared/image_jobs.py` (`richiesta_immagine`) |
| La coda | Solo forma e tetti: ≤2000 caratteri, lati ≤1536, passi ≤60 | `shared/image_jobs.py` |
| Il ponte | Niente: non sceglie il prompt e non giudica l'immagine | `integrations/comfyui/comfy_bridge.py` |
| Il gateway (dalla WebUI) | **No**: non tocca il prompt e non guarda l'immagine; decide solo la memoria della scheda | `integrations/comfyui/webui_gateway.py` |
| Immagine chiesta dalla **WebUI** | **No**: stesso grafo e stessi pesi `-UC`; cambia solo chi la riceve | `scripts/webui_image_env.py` |
| ComfyUI e i pesi | Nessun safety checker: è la variante **`-UC`** | `integrations/comfyui/modelli.json` |
| La moderazione del canale | **Non è un filtro di contenuto**: classifica lo spam in arrivo e conta strike | `shared/channel.py` |

Due conseguenze da tenere presenti:

- **Se il prompt lo scrive l'agente** (nodo `HyperSpacePrompt`, o in futuro il tool
  `image_generate`), il fattore limitante è il **modello di chat** configurato
  (`CHANNEL_MODEL`, oggi `qwen3.5:4b`): è lui che può rifiutare o edulcorare. Il
  contesto di superficie `comfyui` (`shared/persona.py`) impone solo il **formato**
  — soltanto il prompt, nessun preambolo — non il contenuto. Con `POST
  /image/generate` e `!immagine` quel modello non entra in gioco.
- **La consegna resta fuori dal repository.** Il driver manda il FILE via Bot API:
  nessuna riga di codice lo impedisce, ma valgono le regole della piattaforma
  (Telegram, Discord) sui contenuti adulti. HyperSpace non decide lì, e non lo
  nasconde: lo dichiara qui.

Chi può chiedere un'immagine è invece una decisione di **risorsa**, non di morale:
la scheda è una sola e un'immagine costa 313-700 s, quindi `CHANNEL_OPERATOR`
limita il comando all'operatore — e **senza quella variabile il comando è aperto a
chiunque sia in chat** (vedi Fase 3). Il comportamento è fissato da
`tests/test_channel_immagine.py`, che verifica anche che l'idea arrivi *verbatim*
fino al nodo che condiziona CLIP: un filtro aggiunto domani farebbe fallire un
test, non cambierebbe il risultato in silenzio.

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
- **Dalla WebUI il negativo non c'è.** Open WebUI manda `negative_prompt` solo se
  l'utente lo scrive, e questo grafo tiene il negativo *dentro* il prompt ("no text,
  no watermark, no logos"): per questo `COMFYUI_WORKFLOW_NODES` non mappa quel campo
  — un `null` al posto della stringa che il nodo di Qwen si aspetta farebbe fallire
  la generazione.
- **Il diffusion è quello del grafo.** Scegliere un altro "modello" nel pannello
  *Images* della WebUI non cambia `UnetLoaderGGUF`: `IMAGE_GENERATION_MODEL` dice
  cosa disegna, non lo sceglie. Per cambiare pesi si cambia il grafo (o
  `MODELLO_DEFAULT` in `shared/image_jobs.py`, con il manifest).
- **`resolution` segue la misura con cui è stato costruito il grafo** (`--size`).
  Alzare `IMAGE_SIZE` dal pannello senza rigenerare le variabili non dà un errore:
  lascia il text encoder tarato sulla misura vecchia, cioè una qualità diversa.
- **Pannello e grafo possono divergere.** Da 0.11 la configurazione delle immagini è
  persistita nel database: se il pannello e il grafo del repo divergono, il sintomo è
  un'immagine generata con parametri che nessuno ha scelto. `webui_image_env.py
  --check` serve a questo, e `--apply` a rimetterli d'accordo.

## Roadmap

- **Fase 2** — `comfy_bridge.py` (pull) + tool `image_generate` per Aurora +
  consegna dell'immagine su Telegram. Il pezzo grosso è il contratto nel CP.
- **Fase 3** — ComfyUI come **capability della mesh**: il nodo si annuncia come
  "painter", il control-plane instrada lì i job immagini come oggi instrada la
  chat sui nodi (vedi `docs/architecture.md`).
- **Fase 4** — il prompt scritto da Aurora *con la memoria della stanza* (i canali
  hanno già un contesto: se la conversazione è in "ultramind", l'immagine può
  nascere da quella).
