# Handoff — nodo Windows (RTX 3060 8 GB, 48 GB RAM)

Documento per chi prende in mano il nodo Windows 11 della mesh: stato, un
problema aperto con la sua diagnosi, e cosa metterci. Scritto per essere letto
da solo, senza il contesto della sessione che l'ha prodotto.

## 1. Aggiornare (branch `main`, e `--build` obbligatorio)

```bash
git fetch origin && git checkout main && git pull
docker compose up -d --build
```

Il branch e' **`main`**: dal 19/09/2026 contiene tutto (i branch precedenti —
`network-panel`, `code-sandbox`, `dream-review-lifecycle`,
`network-panel-hardening`, `refactor/general` — sono stati assorbiti e rimossi
dal remoto).

**Il `--build` non e' opzionale.** Il control-plane esegue `/app/main.py`
**baked nell'immagine**, non il repo montato: senza `--build` gira codice
vecchio senza alcun avviso. In una sessione di verifica questo ha falsato un
esito (un fix sembrava non funzionare mentre era solo non caricato).

## 2. Problema aperto: il nodo dichiara di non avere GPU

### Evidenza

```bash
curl -s http://localhost:8084/status | python -m json.tool | grep -E 'vram_gb|tier|engine'
```

Al momento dell'handoff restituisce **`vram_gb: 0.0`** e **`tier: leaf`**, su una
macchina con una RTX 3060 da 8 GB. Controprova: `qwen3:8b` misurato a **9.67
t/s** (13 richieste campionate), lo stesso valore di un MacBook Air da 16 GB
senza ventole — non di una macchina con GPU dedicata.

### Perche' conta piu' di quanto sembri

Nei pesi di routing del control-plane:

| Peso | Valore |
|---|---|
| `ROUTING_WEIGHT_VRAM` | **0.55** |
| `ROUTING_WEIGHT_GPU` | **0.20** |
| `ROUTING_WEIGHT_TIER` | 0.10 |

VRAM + GPU sono **il 75% del punteggio**. Un nodo che si annuncia con VRAM zero
viene classificato come il piu' debole della mesh: **la GPU non riceve lavoro**,
e il sintomo (throughput da CPU) e' coerente.

### Causa

`detect_vram_gb()` in `node/main.py` esegue
`nvidia-smi --query-gpu=memory.total` **dentro il container del nodo**. Su
Windows senza GPU passthrough quel comando fallisce e la funzione ritorna `0.0`.
A cascata `calculate_tier()` richiede `vram >= 4.0` per `hub`, quindi il nodo
resta `leaf` (il default).

### Fix A — immediato (dichiarare i valori)

Il profilo Windows legge un **`.env` letterale accanto al compose file** (vedi il
commento in `docker-compose.windows.yml`), non `.env.windows`. In quel file:

```dotenv
VRAM_GB=8
NODE_TIER=hub
```

Sono le variabili gia' previste dal compose (`${NODE_TIER:-leaf}`,
`${VRAM_GB:-0.0}`) e documentate in `.env.example`. Poi `docker compose up -d
--build` e ricontrollare `/status`.

### Fix B — quello corretto (far vedere la GPU al container)

Aggiungere al servizio del nodo lo stesso blocco che ha il servizio
`ollama-nvidia` in `docker-compose.yml`:

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

Cosi' `nvidia-smi` funziona nel container e la detection e' **reale** invece che
dichiarata a mano.

## 3. Verificare che la GPU lavori DAVVERO

Il Fix A dichiara la VRAM, **non la usa**. Serve una verifica separata:

```powershell
nvidia-smi                      # il driver vede la 3060?
ollama ps                       # con un modello caricato: deve dire 100% GPU
```

Se `ollama ps` mostra uno split CPU/GPU, la GPU non e' in uso: controllare i log
di avvio di Ollama (elenca il device CUDA rilevato), che il driver NVIDIA sia
recente, e che non sia impostato `OLLAMA_LLM_LIBRARY=cpu`.

## 4. Criterio di accettazione

```bash
curl -s http://localhost:8084/metrics | python -m json.tool | grep -A3 qwen3
python -m unittest discover -s tests     # atteso: tutti verdi
```

Un 8B su 3060 deve fare **25-40 t/s**, non ~10. Se resta a ~10 la GPU non e' in
uso e il problema **non** e' chiuso.

## 5. Cosa installarci (8 GB VRAM + 48 GB RAM)

| Cosa | Perche' |
|---|---|
| **7-8B a Q4** — `qwen3:8b` (gia' presente, 5.2 GB), `qwen2.5:7b-instruct`, `llama3.1:8b-instruct` (~4.7 GB) | Entrano **interi** in 8 GB con ~8k di contesto. E' il sweet spot |
| **MoE con pochi parametri attivi** — es. `qwen3:30b-a3b` (Q4 ~18 GB) | Non entra in VRAM, ma **solo ~3B parametri sono attivi per token**: l'offload su CPU costa molto meno che su un modello denso. **E' il modo migliore di usare i 48 GB.** Da misurare, non da assumere |
| **3B** — `qwen2.5:3b`, `llama3.2:3b` | Task brevi della mesh, molto veloci |
| `qwen2:0.5b` (gia' presente) | Titler |
| **Vision** | L'encoder aggiunge GB: con 8 GB e' stretto (`gemma4:e4b` pesa 9.6 GB e non entra) |

### Cosa NON metterci

- **Modelli densi da 14B+**: a Q4 sono ~9 GB, non entrano in 8 GB di VRAM, Ollama
  fa uno split su CPU e diventano **piu' lenti** di un 8B interamente su GPU. E'
  la trappola classica.
- **ds4 / DwarfStar**: il target piu' piccolo e' ~81 GiB di modello, e il suo
  supporto CUDA punta ad Ada Lovelace e DGX Spark. La 3060 e' **Ampere**: non e'
  questa la macchina per ds4, a prescindere dalla RAM.

## 6. Cambiamenti recenti che possono sorprendere

- **`/mcp` e' fail-closed**: senza un token configurato risponde **503** (prima
  era aperto a chiunque raggiungesse la porta). Se ci sono client MCP, servono
  `MCP_CLIENTS` e `MCP_CLIENT_TOOLS`; l'allowlist filtra anche `tools/list`, e un
  cliente senza voce di allowlist non riceve nessun tool.
- **Timeout per modello**: i 180s fissi facevano fallire sempre i modelli
  reasoning. Ora `INFERENCE_TIMEOUT_S=180` per i diretti e
  `INFERENCE_TIMEOUT_REASONING_S=600` per i reasoning (qwen3, deepseek-r1,
  deepseek-v4, magistral, glm-5, qwen3.8), piu' un budget totale di richiesta.
  I default sono corretti: non serve configurare nulla. Override da
  `REASONING_MODELS`.
- **Un errore non esce piu' come successo**: prima un fallimento poteva arrivare
  come HTTP 200 con un corpo d'errore e il task marcato `done`. Ora il task e'
  `failed` e lo status e' coerente (502/504).
- **`.env` locale non va committato**: contiene scelte di macchina. Su Windows il
  backend memoria di default e' **Hermes** (per il quale serve il bridge
  attivo), non `legacy`.

## 7. Discrepanze note in `.env.windows`

All'handoff quel profilo dichiara come modelli di riferimento
`qwen3-14b-uncensored` e `qwen2.5-coder-14b-abliterated`, ma sulla macchina ci
sono `qwen3:8b`, `gemma4:e4b`, `qwen2:0.5b`. Due problemi distinti: **non sono
installati**, e **un 14B Q4 non entra in 8 GB di VRAM** (vedi sezione 5). Presente
anche `HOST_NAME=changeme-host`, mai valorizzato. Da allineare alla realta'.

## 8. Stato della mesh al momento dell'handoff

- Control-plane sul Mac (16 GB): `MEMORY_BACKEND=legacy` locale, perche' il
  bridge Hermes vive su Windows.
- Nodo Windows raggiungibile via Tailscale su `100.81.234.102:8081`.
- Modelli presenti su entrambi: `qwen3:8b`, `qwen3:8b-original`, `gemma4:e4b`,
  `qwen2:0.5b`. `deepseek-r1:8b` e' stato rimosso da entrambi (su hardware senza
  GPU dedicata era inutilizzabile: misurato **0.2 t/s** e **0 caratteri utili**,
  perche' il thinking consuma il budget prima della risposta).
- Diagnostica utile: `GET /models/capabilities` sul control-plane elenca per ogni
  modello se ricevera' i tool e **perche'**; un modello nuovo che supporta il
  function calling ma non compare nei pattern perde i tool, e ora lo segnala nei
  log invece di fallire in silenzio.

