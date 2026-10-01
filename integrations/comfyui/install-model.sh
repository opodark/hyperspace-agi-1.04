#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Installa un checkpoint di ComfyUI a partire dal suo manifest.
#
# Quattro manifest, e non solo modelli:
#   modelli-sdxl.json         CyberRealistic Pony V18 (SDXL/Pony) — gli sketch del Mac
#   modelli-sd15.json         ChickMixFlat v1.0 (SD 1.5)          — il volto di Anna
#   modelli-upscaler.json     Real-ESRGAN 4x+                     — il fix dei suoi ritratti
#   modelli-riferimento.json  ControlNet openpose + IP-Adapter + CLIP-ViT-H — la posa
#                             e il riferimento del volto di Anna (tre file, due repo)
#
# La forma del manifest: o UN file (`.checkpoint`, la forma dei tre manifest dei
# modelli) o PIU' file (`.file`, un elenco). I tre file del riferimento si
# installano insieme perche' un grafo con due dei tre non esiste: con tre comandi
# separati, «meta' installazione» sarebbe lo stato piu' probabile. Il ciclo che
# scarica e verifica e' uno solo, e la forma singola diventa una riga dello stesso
# elenco — cosi' i tre manifest dei modelli restano leggibili com'erano.
#
# Perche' uno script e non un download a mano: i file sono da ~0,1 a 7 GB e un
# errore qui non si vede. Un checkpoint troncato non da' un errore: da' un'immagine
# rumorosa o un OOM a meta' campionamento. Qui il file prende il NOME VERO solo
# dopo che l'impronta SHA-256 corrisponde a quella del manifest: fino a quel
# momento resta `<nome>.parziale`, che ComfyUI non vede nemmeno.
#
# Il manifest e' letto anche da `tests/test_comfyui_modelli_sdxl.py` e
# `tests/test_comfyui_modelli_sd15.py`, che lo confrontano con `MODELLO_SDXL` e
# `MODELLO_SD15` del grafo: rinominare il file da un lato solo fa fallire un test,
# non un job.
#
# La sorgente la decide il manifest: `repo`+`revisione` per Hugging Face, `url`
# per un upload Civitai (che un commit non ce l'ha). Il pin e' lo SHA-256 in
# entrambi i casi.
#
# Uso:
#   integrations/comfyui/install-model.sh --check              # non scarica
#   integrations/comfyui/install-model.sh                      # scarica + verifica
#   integrations/comfyui/install-model.sh --manifest integrations/comfyui/modelli-sd15.json
#   integrations/comfyui/install-model.sh --manifest integrations/comfyui/modelli-riferimento.json
#   integrations/comfyui/install-model.sh --models ~/ComfyUI/models
#
# La cartella dei modelli, se non e' data con --models, si legge dalla
# configurazione di ComfyUI Desktop (`modelsDirs`), che e' l'unica fonte
# autorevole: i pesi devono finire dove *quel* ComfyUI li cerca. Poi i candidati
# noti: ~/ComfyUI-Shared/models, ~/Documents/ComfyUI/models, ~/ComfyUI/models.
#
# Un download interrotto si RIPRENDE (curl -C -): rilanciare continua dal byte
# dove era arrivato, non ricomincia da zero.
set -euo pipefail

MANIFEST="$(cd "$(dirname "$0")" && pwd)/modelli-sdxl.json"
CHECK=0
FORZA=0
MODELLI="${COMFYUI_MODELS_DIR:-}"

usage() { awk 'NR > 1 && /^set -euo pipefail/ {exit} NR > 1 {print}' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1; shift;;
    --force) FORZA=1; shift;;
    --manifest) MANIFEST="$2"; shift 2;;
    --models) MODELLI="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Argomento sconosciuto: $1" >&2; usage; exit 2;;
  esac
done

if ! command -v jq >/dev/null 2>&1; then
  echo "[modelli] errore: serve 'jq' per leggere il manifest (brew install jq)" >&2
  exit 3
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "[modelli] errore: serve 'curl'" >&2
  exit 3
fi
if [[ ! -f "$MANIFEST" ]]; then
  echo "[modelli] errore: manifest non trovato: $MANIFEST" >&2
  exit 3
fi

# La sorgente di OGNI file: o un repository Hugging Face pinnato a un commit
# (`repo` + `revisione`), o un URL diretto (`url`, per gli upload Civitai che un
# commit non ce l'hanno). In entrambi i casi il pin vero e' lo SHA-256: senza
# quello il file non prende il nome vero. Nel manifest multi-file i due campi
# stanno dentro ogni voce, perche' i tre file del riferimento vengono da due
# repository diversi.
LICENZA="$(jq -r '.licenza' "$MANIFEST")"
SORGENTE_MANIFEST="$(jq -r '.sorgente // ""' "$MANIFEST")"
# Nel manifest dei modelli `sorgente` non c'e': li' la sorgente e' per-file
# (`repo`+`revisione`, stampati nella riga «da:» di ognuno). Meglio dirlo che
# stampare una riga vuota che sembra un campo dimenticato.
SORGENTE_MANIFEST="${SORGENTE_MANIFEST:-vedi la riga «da:» di ogni file}"

# L'elenco dei file in una forma sola — una riga per file, campi separati da
# `\x1f` (US, unit separator):
#   url  repo  revisione  file(locale)  file_remoto  destinazione  sha256  byte  ruolo
# Perché non un TAB: in `read` i separatori di IFS che sono anche spazi (TAB,
# spazio) vengono COLLASSATI, quindi un campo vuoto — l'`url` assente di un file
# preso da Hugging Face, o il `file_remoto` uguale al nome locale — fa scivolare
# tutte le colonne di uno. Si vede subito: la destinazione diventa un'impronta.
ELENCO="$(jq -r '
  def riga: join("\u001f");
  if (.file | type) == "array" then
    .file[] | [(.url // ""), (.repo // ""), (.revisione // ""), .file,
               (.file_remoto // .file), .destinazione, .sha256,
               (.byte | tostring), (.ruolo // .destinazione)] | riga
  else
    [(.url // ""), (.repo // ""), (.revisione // ""), .checkpoint.file,
     (.checkpoint.file_remoto // .checkpoint.file), .checkpoint.destinazione,
     .checkpoint.sha256, (.checkpoint.byte | tostring),
     (.checkpoint.ruolo // .checkpoint.destinazione)] | riga
  end' "$MANIFEST")"
if [[ -z "$ELENCO" ]]; then
  echo "[modelli] errore: il manifest non dichiara nessun file: $MANIFEST" >&2
  exit 3
fi

# Dove stanno i pesi lo decide ComfyUI, non lo script. La fonte piu' autorevole e'
# la configurazione dell'app desktop, che dichiara le sue cartelle (`modelsDirs`):
# su questo Mac e' ~/ComfyUI-Shared/models, NON ~/Documents/ComfyUI/models dei
# default storici — e 3 GB nella cartella sbagliata si scoprono al primo job, con
# ComfyUI che dice che il file non c'e'. Poi i candidati noti, in ordine.
if [[ -z "$MODELLI" ]]; then
  CONFIG_DESKTOP="$HOME/Library/Application Support/Comfy Desktop/settings.json"
  if [[ -f "$CONFIG_DESKTOP" ]]; then
    MODELLI="$(jq -r '.modelsDirs[0] // ""' "$CONFIG_DESKTOP" 2>/dev/null || true)"
  fi
fi
if [[ -z "$MODELLI" ]]; then
  for candidato in "$HOME/ComfyUI-Shared/models" "$HOME/Documents/ComfyUI/models" \
                   "$HOME/ComfyUI/models" "$PWD/ComfyUI/models"; do
    [[ -d "$candidato" ]] && MODELLI="$candidato" && break
  done
fi
if [[ -z "$MODELLI" || ! -d "$MODELLI" ]]; then
  echo "[modelli] errore: non trovo la cartella modelli di ComfyUI." >&2
  echo "  Passala con --models <percorso>/models, oppure imposta COMFYUI_MODELS_DIR." >&2
  exit 3
fi

echo "[modelli] cartella modelli: $MODELLI"
echo "[modelli] manifest:         $(basename "$MANIFEST")"
echo "[modelli] sorgente:         $SORGENTE_MANIFEST"
echo "[modelli] licenza:          $LICENZA"
echo "[modelli] file nell'elenco: $(printf '%s\n' "$ELENCO" | wc -l | tr -d ' ')"

mancanti=0
# Una riga = un file. Ogni file si verifica DA SOLO, con la sua impronta: se il
# primo c'e' e il secondo no, il primo non si riscarica (sono 2,5 GB).
while IFS=$'\x1f' read -r URL_FILE REPO REV FILE REMOTO DEST SHA BYTE RUOLO; do
  [[ -n "${FILE:-}" ]] || continue
  DEST_DIR="$MODELLI/$DEST"
  TARGET="$DEST_DIR/$FILE"
  PARTIAL="$DEST_DIR/$FILE.parziale"
  if [[ -n "$URL_FILE" ]]; then
    URL="$URL_FILE"
    SORGENTE="$RUOLO (URL diretto)"
  elif [[ -n "$REPO" ]]; then
    if [[ -z "$REV" ]]; then
      echo "[modelli] errore: $FILE non dichiara una revisione (il pin deve essere un commit)." >&2
      exit 3
    fi
    URL="https://huggingface.co/$REPO/resolve/$REV/$REMOTO"
    SORGENTE="$REPO @ ${REV:0:8}"
  else
    echo "[modelli] errore: $FILE non dichiara ne' un repository ne' un URL." >&2
    exit 3
  fi
  # Un decimale: la divisione intera diceva "1 GB" di un file da 1,99 GiB, e la
  # dimensione e' proprio il numero su cui si decide se scaricare.
  echo
  echo "[modelli] $RUOLO: $FILE ($(awk -v b="$BYTE" 'BEGIN { printf "%.2f", b / 1024 / 1024 / 1024 }') GB)"
  echo "[modelli]   in: $DEST/"
  echo "[modelli]   da: $SORGENTE"
  if [[ -f "$TARGET" && "$FORZA" -eq 0 ]]; then
    attuale="$(shasum -a 256 "$TARGET" | awk '{print $1}')"
    if [[ "$attuale" == "$SHA" ]]; then
      echo "[modelli]   pronto: c'e' e l'impronta torna."
      continue
    fi
    echo "[modelli]   il file presente non torna: lo ricalcolo/riscarico."
    rm -f "$TARGET"
  fi

  if [[ "$CHECK" -eq 1 ]]; then
    echo "[modelli]   da scaricare."
    mancanti=$((mancanti + 1))
    continue
  fi

  mkdir -p "$DEST_DIR"
  echo "[modelli]   scarico ..."
  curl -fL -C - --retry 6 --retry-delay 3 \
       --speed-limit 1000000 --speed-time 60 -o "$PARTIAL" "$URL"

  attuale="$(shasum -a 256 "$PARTIAL" | awk '{print $1}')"
  if [[ "$attuale" != "$SHA" ]]; then
    echo "[modelli] errore: impronta di $FILE non corrisponde." >&2
    echo "  attesa:   $SHA" >&2
    echo "  ricevuta: $attuale" >&2
    echo "  il file parziale resta in $PARTIAL: rilanciare riprende il download." >&2
    exit 1
  fi
  mv "$PARTIAL" "$TARGET"
  echo "[modelli]   pronto: installato e verificato."
done <<< "$ELENCO"

echo
if [[ "$CHECK" -eq 1 ]]; then
  if [[ "$mancanti" -eq 0 ]]; then
    echo "[modelli] pronto: tutti i file ci sono e le impronte tornano."
  else
    echo "[modelli] da scaricare: $mancanti file — ri-esegui senza --check."
  fi
  exit 0
fi
echo "[modelli] fatto."
echo "[modelli] ComfyUI legge l'elenco dei file all'avvio: riavvialo prima di generare."
