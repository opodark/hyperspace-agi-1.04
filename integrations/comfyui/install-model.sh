#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Installa il checkpoint SDXL-Turbo (lo sketch del Mac) dentro la cartella
# modelli di ComfyUI.
#
# Perche' uno script e non un download a mano: il file e' ~6.9 GB e un errore qui
# non si vede. Un checkpoint troncato non da' un errore: da' un'immagine rumorosa
# o un OOM a meta' campionamento. Qui il file prende il NOME VERO solo dopo che
# l'impronta SHA-256 corrisponde a quella del manifest: fino a quel momento resta
# `<nome>.parziale`, che ComfyUI non vede nemmeno.
#
# Il manifest (`modelli-sdxl.json`) e' letto anche da
# `tests/test_comfyui_modelli_sdxl.py`, che lo confronta con `MODELLO_SDXL` del
# grafo: rinominare il file da un lato solo fa fallire un test, non un job.
#
# Uso:
#   integrations/comfyui/install-model.sh --check              # non scarica
#   integrations/comfyui/install-model.sh                      # scarica + verifica
#   integrations/comfyui/install-model.sh --models ~/ComfyUI/models
#
# Un download interrotto si RIPRENDE (curl -C -): rilanciare continua dal byte
# dove era arrivato, non ricomincia da zero.
set -euo pipefail

MANIFEST="$(cd "$(dirname "$0")" && pwd)/modelli-sdxl.json"
CHECK=0
FORZA=0
MODELLI="${COMFYUI_MODELS_DIR:-}"

usage() { sed -n '1,30p' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1; shift;;
    --force) FORZA=1; shift;;
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

REPO="$(jq -r '.repo' "$MANIFEST")"
REV="$(jq -r '.revisione' "$MANIFEST")"
FILE="$(jq -r '.checkpoint.file' "$MANIFEST")"
DEST="$(jq -r '.checkpoint.destinazione' "$MANIFEST")"
SHA="$(jq -r '.checkpoint.sha256' "$MANIFEST")"
BYTE="$(jq -r '.checkpoint.byte' "$MANIFEST")"
LICENZA="$(jq -r '.licenza' "$MANIFEST")"

# Dove stanno i pesi lo decide ComfyUI, non lo script. ComfyUI Desktop sul Mac
# usa ~/Documents/ComfyUI/models; il clone git/pip usa <repo>/models. Si chiede
# esplicitamente con --models se nessuno dei default esiste.
if [[ -z "$MODELLI" ]]; then
  for candidato in "$HOME/Documents/ComfyUI/models" "$HOME/ComfyUI/models" "$PWD/ComfyUI/models"; do
    [[ -d "$candidato" ]] && MODELLI="$candidato" && break
  done
fi
if [[ -z "$MODELLI" || ! -d "$MODELLI" ]]; then
  echo "[modelli] errore: non trovo la cartella modelli di ComfyUI." >&2
  echo "  Passala con --models <percorso>/models, oppure imposta COMFYUI_MODELS_DIR." >&2
  exit 3
fi

DEST_DIR="$MODELLI/$DEST"
TARGET="$DEST_DIR/$FILE"
PARTIAL="$DEST_DIR/$FILE.parziale"
URL="https://huggingface.co/$REPO/resolve/$REV/$FILE"

echo "[modelli] cartella modelli: $MODELLI"
echo "[modelli] sorgente:         $REPO @ ${REV:0:8}"
echo "[modelli] licenza:          $LICENZA"
echo "[modelli] checkpoint:       $FILE ($(( BYTE / 1024 / 1024 / 1024 )) GB)"

if [[ -f "$TARGET" && "$FORZA" -eq 0 ]]; then
  attuale="$(shasum -a 256 "$TARGET" | awk '{print $1}')"
  if [[ "$attuale" == "$SHA" ]]; then
    echo "[modelli] pronto: il checkpoint c'e' e l'impronta torna."
    exit 0
  fi
  echo "[modelli] il checkpoint presente non torna: ricalcolo/riscarico."
  rm -f "$TARGET"
fi

if [[ "$CHECK" -eq 1 ]]; then
  echo "[modelli] da scaricare: $FILE (ri-esegui senza --check per scaricare)."
  exit 0
fi

mkdir -p "$DEST_DIR"
echo "[modelli] scarico $FILE ..."
curl -fL -C - --retry 6 --retry-delay 3 \
     --speed-limit 1000000 --speed-time 60 -o "$PARTIAL" "$URL"

attuale="$(shasum -a 256 "$PARTIAL" | awk '{print $1}')"
if [[ "$attuale" != "$SHA" ]]; then
  echo "[modelli] errore: impronta non corrisponde." >&2
  echo "  attesa:   $SHA" >&2
  echo "  ricevuta: $attuale" >&2
  echo "  il file parziale resta in $PARTIAL: rilanciare riprende il download." >&2
  exit 1
fi
mv "$PARTIAL" "$TARGET"
echo "[modelli] pronto: checkpoint installato e verificato."
echo "[modelli] ComfyUI legge l'elenco dei file all'avvio: riavvialo prima di generare."
