# Handoff — nodo Windows (RTX 3060 8 GB, 48 GB RAM)

Documento per chi prende in mano il nodo Windows 11 della mesh: stato, un
problema aperto con la sua diagnosi, e cosa metterci. Scritto per essere letto
da solo, senza il contesto della sessione che l'ha prodotto.

## 0. Prima di tutto: `.env` non si aggiorna da solo

**Non e' la causa di un incidente passato**: all'handoff il `git pull` funzionava
e le commit sono state trovate regolarmente. E' una **trappola strutturale di
questo setup**, verificata in `setup.ps1:48`, che va conosciuta prima di
modificare qualsiasi variabile.

`.env.windows` **non e' la configurazione attiva**. Il compose legge `.env`, e
`setup.ps1` copia `.env.windows` → `.env` **solo se `.env` non esiste**
(`Ensure-EnvFile`). `.env` e' in `.gitignore`, quindi **nessun `git pull` lo
tocca mai**.

Conseguenza concreta: i fix di questo documento sono nel repo, ma su una macchina
gia' avviata **non arrivano al processo in esecuzione** finche' `.env` non viene
riconciliato. Vale per `VRAM_GB`/`NODE_TIER` (sezione 2) come per qualunque
variabile aggiunta in futuro.

```powershell
git fetch origin --prune
git checkout main
git pull

# mostra le differenze tra la config attiva e il template aggiornato
.\scripts\sync_env_windows.ps1

# le applica: aggiorna solo le chiavi divergenti, preserva le righe locali
.\scripts\sync_env_windows.ps1 -Apply
```

Valori che `.env` deve avere su questa macchina:

```powershell
Select-String -Path .env -Pattern 'NODE_TIER|^VRAM_GB|HS_MODEL_GENERAL|TITLER_ENABLED'
# NODE_TIER=hub          <- altrimenti il CP lo tratta come nodo piu' debole
# VRAM_GB=8              <- con 0 (o assente) la GPU non riceve lavoro
# HS_MODEL_GENERAL=qwen3:8b      <- installato e dentro gli 8 GB
# TITLER_ENABLED=false   <- non ruba VRAM al modello di servizio
```

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

> **Correzione (2026-09-19, dopo la connessione della mesh).** La premessa di
> questa sezione e' **sbagliata**: la GPU del Windows *lavora*. Il `/metrics` del
> nodo mostra `qwen3.5:4b` caricato con **5.51 GB in VRAM** a 14.47 t/s. Quello
> che non funziona e' la **dichiarazione**: il nodo annuncia `vram_gb=0.0` e
> `tier=leaf`, e questo e' vero **ancora dopo un riavvio** — quindi non e' una
> detection fallita, e' la configurazione che non arriva (sezione 0 e sezione 7).
> La conseguenza sul routing descritta sotto resta valida: con `vram_gb=0` il
> control-plane lo tratta come il nodo piu' debole della mesh. Ma il titolo
> giusto e' "il nodo dichiara male la sua GPU", non "la GPU non va".
> Lo stesso vale per il **nodo del Mac**, che dichiara `vram_gb=0.0` per lo
> stesso motivo.

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

## 5. Cosa c'e' GIA' (misurato, 2026-09-19)

Questa sezione era scritta come "cosa installarci", partendo da un'assunzione
sbagliata: che la macchina avesse `qwen3:8b` e poco altro. Il `/metrics` del nodo
dice il contrario — ha una famiglia di modelli **piu' nuova** di quella del Mac.
I numeri vanno letti, non ipotizzati.

| Modello | t/s misurati |
|---|---|
| `qwen3.5:4b` — **5.51 GB in VRAM** | **14.47** |
| `hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-...:Q2_K_P` (MoE) | **11.04** |
| `hf.co/Abiray/Qwen3.5-4B-Abliterated-...-Distilled:Q6_K` | 14.12 |
| `hf.co/Abiray/Qwen3.5-9B-abliterated-GGUF:Q6_K` | 3.00 |
| `hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:IQ2_S` | da misurare |
| `qwen2.5-coder:14b-instruct` (denso, Q4) | **0.12** |
| `huihui_ai/Qwen3.8-abliterated:latest` | 1.20 |
| `hf.co/bartowski/dolphin-2.9.4-llama3.1-8b-GGUF:Q4_K_M` | 0.08 |

Cosa dicono questi numeri:

- **Il MoE da 35B a 11 t/s batte il 9B denso a 3 t/s**, pur essendo molto piu'
  grande: l'ipotesi di questa sezione era giusta, ed e' ora **misurata** invece
  che da verificare.
- **I quant a bassissima precisione funzionano**: un 27B in `IQ2_S` e un 35B in
  `Q2_K_P` girano su 8 GB di VRAM + 48 GB di RAM. La regola "un 14B non entra,
  quindi serve un 7-8B" valeva **per Q4**, non in assoluto.
- **Smentito** che la GPU non lavori: `qwen3.5:4b` e' dentro la VRAM a 5.51 GB.
  Il problema della GPU non era la GPU. Resta da capire solo il 14.47 t/s di un
  4B su una 3060, che dovrebbe stare piu' in alto (verificare `ollama ps`).

### Da NON installare qui
- **Densi da 14B+ a Q4**: misurato su questa macchina, `qwen2.5-coder:14b-instruct`
  fa **0.12 t/s**. La trappola e' reale, e qui c'e' il numero che la dimostra.
- **`qwen3:8b`**: sta sul Mac, non qui. Tenerlo su entrambi confonde il routing
  invece di aiutarlo (vedi sezione 8).
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

## 7. Discrepanze in `.env.windows` — risolte nel repo

All'handoff quel profilo dichiarava come modelli di riferimento
`qwen3-14b-uncensored` e `qwen2.5-coder-14b-abliterated`, ma sulla macchina ci
sono `qwen3:8b`, `gemma4:e4b`, `qwen2:0.5b`. Due problemi distinti: **non erano
installati**, e **un 14B Q4 non entra in 8 GB di VRAM** (vedi sezione 5).

Corrette nel repo (commit `1eaba92`): `HS_MODEL_GENERAL=qwen3:8b`, modelli coder
commentati (nessuno installato), `HOST_NAME`/`TS_HOSTNAME` valorizzati con
l'hostname reale, `VRAM_GB=8` e `NODE_TIER=hub` dichiarati, `TITLER_ENABLED=false`
(era rimasto `true` su questo profilo: il fix precedente era arrivato solo al
compose generico).

Attenzione: sono correzioni a `.env.windows`. Perche' arrivino anche al processo
in esecuzione serve riconciliare `.env` — vedi sezione 0.

## 8. Stato della mesh al momento dell'handoff

- Control-plane sul Mac (16 GB): `MEMORY_BACKEND=legacy` locale, perche' il
  bridge Hermes vive su Windows.
- Nodo Windows raggiungibile via Tailscale su `100.81.234.102:8081`.
- Modelli **NON in comune**, come si era assunto: il Mac ha `qwen3:8b`,
  `qwen3:8b-original`, `gemma4:e4b`, `qwen2:0.5b`; il Windows ha la famiglia
  Qwen3.5/3.6/3.8 (sezione 5). Nessun modello e' presente su entrambi, quindi
  oggi il routing e' **deterministico per disponibilita'** — una richiesta puo'
  andare solo dove il modello esiste — e la scelta **tra** nodi non viene quasi
  mai esercitata. Per provare davvero il punteggio di routing serve un modello
  condiviso.
- `deepseek-r1:8b` e' stato rimosso da entrambi (su hardware senza GPU dedicata
  era inutilizzabile: misurato **0.2 t/s** e **0 caratteri utili**, perche' il
  thinking consuma il budget prima della risposta).
- **Nodo Windows**: id `fc6c821ba7c18667`, `100.64.31.18`. Porte **8081, 8086,
  8088 raggiungibili**; **8085 in timeout**: la regola firewall di
  `docs/tailscale-mesh-setup.md:65` elenca `8088,8086,8081,8095,20128` e **non
  copre la porta del control-plane**.
- **Nodo Mac**: id `d7bc05baed5b752a`, `http://100.81.234.102:8081`. Dichiara
  anche lui `vram_gb=0.0` e `tier=leaf` (il suo `.env` ha `VRAM_GB=0`): **lo
  stesso difetto del Windows, sul lato locale**. I due nodi sono quindi
  indistinguibili sul 75% del punteggio di routing.
- **Verifica cross-macchina riuscita**: una richiesta al control-plane del Mac per
  `qwen3.5:4b` (modello presente **solo** sul Windows) ha incrementato
  `requests_seen` sul nodo Windows da 1 a 2, in **1.79s** andata e ritorno. La
  catena CP-Mac → Tailscale → nodo Windows → GPU funziona. Per confronto, il nodo
  Mac serviva `qwen3:8b` con `latency_ms_ewma` di **12239 ms** e 3.69 t/s.
- Diagnostica utile: `GET /models/capabilities` sul control-plane elenca per ogni
  modello se ricevera' i tool e **perche'**; un modello nuovo che supporta il
  function calling ma non compare nei pattern perde i tool, e ora lo segnala nei
  log invece di fallire in silenzio.

