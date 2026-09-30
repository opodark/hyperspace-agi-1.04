# Componenti di terze parti

Librerie di terze parti distribuite con HyperSpace-AGI nei propri container, con
la licenza dichiarata. I testi integrali accompagnano ciascun pacchetto
installato (`*.dist-info/LICENSE*`) e non sono duplicati qui: questo file dice
*cosa* c'e' e *con quale licenza*, non ricopia le licenze.

La colonna **fonte** dice da dove viene il dato, e la differenza conta:

- `meta` — letto dal metadata del pacchetto **installato** (`.venv`): e' il dato
  piu' affidabile, ed e' quello da preferire se si rifa' la verifica;
- `pypi` — dichiarato dal progetto sulla propria pagina PyPI, non verificato
  localmente.

Verifica del 19/09/2026. **Nessuna dipendenza e' copyleft**: tutte MIT, BSD o
Apache-2.0, quindi non impongono nulla sulle scelte di licenza del progetto.

## control-plane — `control-plane/requirements.txt`

| Pacchetto | Licenza | Fonte |
|---|---|---|
| Flask | BSD-3-Clause | meta |
| flask-cors | MIT | meta |
| requests | Apache-2.0 | meta |
| O365 | **non dichiarata** | pypi |
| google-api-python-client | Apache-2.0 | pypi |
| google-auth-httplib2 | Apache-2.0 | pypi |
| google-auth-oauthlib | Apache-2.0 | pypi |

> **Aperto:** `O365` non dichiara la licenza nel metadata PyPI. E' l'unica
> dipendenza di questo progetto di cui non si conosce la licenza, e va verificata
> nel repository upstream **prima** di una distribuzione pubblica. Nota che e'
> proprio uno dei connettori con credenziali protetti dall'allowlist MCP.

## node — `node/requirements.txt`

| Pacchetto | Licenza | Fonte |
|---|---|---|
| fastapi | MIT | pypi |
| uvicorn[standard] | BSD-3-Clause | pypi |
| httpx | BSD-3-Clause | meta |
| requests | Apache-2.0 | meta |
| cryptography | Apache-2.0 OR BSD-3-Clause | meta |

## sandbox — `sandbox/requirements.txt`

cryptography, fastapi, flask, flask-cors, httpx, requests, uvicorn (le stesse
licenze qui sopra); `pytest` (MIT) e' presente solo per i test del runner.

Il preset di analisi Python include `bandit==1.8.6` (Apache-2.0), con
`stevedore` (Apache-2.0), `PyYAML` (MIT), `rich` (MIT) e `pygments`
(BSD-2-Clause): licenze verificate nel metadata locale il 20/09/2026.
`markdown-it-py` e `mdurl` sono MIT, verificati nei file LICENSE installati.
Il preset di lint include `ruff==0.14.10` (MIT), verificato nel metadata locale
e nella licenza del progetto Astral.

## web-node

Nessuna dipendenza: solo codice del progetto (`package.json` non ha
`dependencies`). I test girano con il runtime Node.

## Immagini dei servizi di terze parti

Open WebUI, SearXNG, Obsidian (KasmVNC), Caddy, Ollama e OmniRoute sono
**scaricate** dalle rispettive registry a runtime e **non ridistribuite** da
questo repository: ciascuna immagine porta con se' la propria licenza, e la
responsabilita' di rispettarla e' di chi la esegue.

## ECC — skill testuali selezionate

`vendor/ecc` contiene `security-review` e `verification-loop` dal repository
ufficiale [affaan-m/ECC](https://github.com/affaan-m/ECC), commit
`9ac593b55cba44c8b20152a5c7f28d300a67ec7e`, sotto licenza MIT.
Copyright (c) 2026 Affaan Mustafa. La licenza integrale è conservata in
`vendor/ecc/LICENSE`; provenienza e SHA-256 sono in `vendor/ecc/manifest.json`.
Le skill sono distribuite senza modifiche; l'adattamento a Hyperspace è aggiunto
dal loader come contesto separato. Nessun hook o runtime ECC è incluso.

## Non coperto da questo file

- **I modelli** (pesi GGUF/Ollama) scaricati dai nodi: ognuno ha la licenza del
  proprio autore. Non sono distribuiti con il progetto: si scaricano a parte.
  Quello del nodo d'immagine e' `abenzerps/Qwen-Image-2.1-Uncensored-GGUF`
  (quantizzazioni GGUF di `Qwen/Qwen-Image-2.1`, revisione
  `1206d38bb47ef93961bfb77bc2c700d43a25860e`) sotto **Qwen Research License**:
  `integrations/comfyui/install-model.ps1` lo scarica da Hugging Face e confronta
  l'impronta SHA-256 con `integrations/comfyui/modelli.json` — la verifica e' del
  *file*, non una dichiarazione di licenza, che resta di chi lo scarica.
  I due checkpoint del Mac seguono la stessa regola, con un manifest ciascuno:
  `integrations/comfyui/modelli-sdxl.json` (`cyberdelia/CyberRealisticPony`,
  revisione `bf85880a58614199d0391c93151211be2832bb8c`) e
  `integrations/comfyui/modelli-sd15.json` (ChickMixFlat v1.0, autore `xxxholic`,
  versione v1.0 del 2023-05-10). Entrambi sono **CreativeML Open RAIL-M**: una
  licenza che ammette l'uso commerciale con restrizioni d'uso (niente contenuti
  illeciti o dannosi) e che resta dell'autore dei pesi. Due differenze da sapere,
  e sono scritte nel manifest invece che qui: ChickMixFlat e' un upload **Civitai**
  — non c'e' un repository con revisioni, quindi il pin e' l'impronta SHA-256 — e
  Civitai non dichiara per esso un permesso di uso commerciale esplicito: il dato
  certo e' la licenza del modello. Il file e' inoltre un **PickleTensor** (Civitai
  non ne offre la variante SafeTensor): lo scan di Civitai lo dichiara senza
  import di pickle, ma la fiducia in quel formato sta li' e non in noi.
  Un terzo file, dal 2026-09-30, e' l'ingranditore del fix dei ritratti di Anna:
  `integrations/comfyui/modelli-upscaler.json` (Real-ESRGAN 4x+, autore Xintao
  Wang, release v0.1.0) sotto **BSD 3-Clause** — una licenza di software, non d'uso
  dei pesi: e' il file che A1111 chiama «R-ESRGAN 4x+» nei metadati dei demo di
  ChickMixFlat, e senza di lui il secondo passaggio del fix non si costruisce.
  Anche qui il pin e' l'impronta SHA-256, e anche qui i pesi non sono distribuiti
  con HyperSpace.
- **Le dipendenze di sviluppo** (vitest, typescript nel web-node; pytest nella
  sandbox): presenti solo negli ambienti di test, non nelle immagini di servizio.
