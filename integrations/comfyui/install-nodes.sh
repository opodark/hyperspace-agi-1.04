#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Installa i nodi di terze parti che i job immagine chiedono a ComfyUI, e collega
# i nodi del repo.
#
# Perche' serve. Il 2026-09-30 il preflight del ponte diceva:
#
#   IPAdapterModelLoader.ipadapter_file: ComfyUI non dichiara nessun file
#     — la cartella di ComfyUI e' vuota: scarica il file con install-model.sh
#
# e mandava a cercare un PESO quando il problema era un NODO che non c'era:
# `IPAdapterModelLoader` non esisteva proprio, e ComfyUI risponde `{}` a
# /object_info sia per un nodo assente sia per una cartella vuota. Due rimedi
# diversi per lo stesso sintomo: `install-model.sh` e' il rimedio dei pesi,
# questo e' il rimedio dei nodi.
#
# Cosa installa, e perche' proprio due:
#
#   ComfyUI_IPAdapter_plus @ a0f451a5113cf9becb0847b92884cb10cbdec0ef (cubiq)
#     IPAdapterModelLoader + IPAdapterAdvanced: l'innesto del riferimento del
#     volto di Anna sul MODELLO (shared/image_jobs.py, nodi 465-468). Pinnato a
#     un commit perche' il grafo passa campi precisi — `weight_type: linear`,
#     `combine_embeds: concat`, `embeds_scaling: V only` — e i campi di
#     IPAdapterAdvanced cambiano nel tempo: il commit e' la versione contro cui
#     il grafo e' stato verificato. (Non e' un ramo in sviluppo: il repository è
#     in «maintenance mode» dal commit d830c5e, e a0f451a5 e' la sua testa.)
#
#   comfyui_controlnet_aux (Fannovel16)
#     DWPreprocessor: lo scheletro DWPose quando la posa viene da una FOTO
#     (`pose_image`). Qui NON si pinna: si verifica che ci sia, che esponga il
#     nodo e si stampa il commit installato — e' un nodo che l'app puo' aver
#     messo con ComfyUI-Manager, e il campo che il grafo usa
#     (`scale_stick_for_xinsr_cn`) e' dichiarato per nome. La posa da PRESET non
#     lo usa: quella la disegna HyperSpacePosePreset, nodo del repo.
#
# Nessuna dipendenza da installare a mano: ComfyUI_IPAdapter_plus non ha
# `requirements.txt` ne' `dependencies` in pyproject.toml (verificato sul commit
# pinnato). InsightFace e onnxruntime NON servono — sono per IPAdapter-FaceID, e
# il riferimento di Anna e' un disegno, non una foto da riconoscere.
#
# I nodi del repo (`custom_nodes/hyperspace_pose_preset.py`,
# `hyperspace_save_jpeg.py`) si installano come LINK al file del repository, non
# come copia: e' quello che fa gia' questa macchina, e vuol dire che una
# correzione nel repo e' attiva al riavvio di ComfyUI, senza ri-copiare niente.
#
# Uso:
#   integrations/comfyui/install-nodes.sh --check     # rapporto, non tocca niente
#   integrations/comfyui/install-nodes.sh             # installa quello che manca
#   integrations/comfyui/install-nodes.sh --forza     # riporta al pin anche se c'e'
#   integrations/comfyui/install-nodes.sh --nodes ~/ComfyUI-Installs/ComfyUI/ComfyUI/custom_nodes
#
# `--check` esce 0 anche quando manca qualcosa: e' un rapporto, non un verdetto.
# Il verdetto lo da' `comfy_bridge.py --check`, che guarda ComfyUI dal di dentro.
set -euo pipefail


# Il repository (per i nodi del repo) e la cartella dei nodi del repo dentro il repo.
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
NODI_REPO="$REPO/integrations/comfyui/custom_nodes"
# I due nodi del repo che il grafo chiede per nome.
NODI_NOSTRI="hyperspace_pose_preset.py hyperspace_save_jpeg.py"

# ComfyUI_IPAdapter_plus: il commit e' un pin, non un suggerimento (vedi in testa).
IPADAPTER_URL="${IPADAPTER_URL:-https://github.com/cubiq/ComfyUI_IPAdapter_plus}"
IPADAPTER_SHA="a0f451a5113cf9becb0847b92884cb10cbdec0ef"
IPADAPTER_DIR="ComfyUI_IPAdapter_plus"
# Il nodo che prova che il pacchetto e' quello giusto: se non espone questo, non
# e' IPAdapterAdvanced (o e' un'altra versione).
IPADAPTER_NODO="IPAdapterAdvanced"

AUX_URL="${AUX_URL:-https://github.com/Fannovel16/comfyui_controlnet_aux}"
AUX_DIR="comfyui_controlnet_aux"
AUX_NODO="DWPreprocessor"

NODES="${COMFYUI_CUSTOM_NODES:-}"
CHECK=0
FORZA=0
COMFY_URL="${COMFY_URL:-http://127.0.0.1:8188}"

usage() { awk 'NR > 1 && /^set -euo pipefail/ {exit} NR > 1 {print}' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1; shift;;
    --forza) FORZA=1; shift;;
    --nodes) NODES="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Argomento sconosciuto: $1" >&2; usage; exit 2;;
  esac
done

log()  { echo "[nodi] $*"; }
warn() { echo "[nodi] attenzione: $*" >&2; }
errore() { echo "[nodi] errore: $*" >&2; }

# Un campo da un JSON scritto dall'app. Con `jq` se c'e' (e' la stessa dipendenza
# di install-model.sh), altrimenti si legge la riga: questi file li scrive l'app,
# una chiave per riga, e vale la pena non dipendere da jq per una stringa sola.
campo_json() {
  local file="$1" chiave="$2"
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg k "$chiave" '.[$k] // ""' "$file" 2>/dev/null || true
  else
    sed -n "s/.*\"$chiave\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -1
  fi
}

# Dove stanno i nodi lo decide ComfyUI. La fonte autorevole e' la configurazione
# dell'app desktop: `installDir` e' la cartella delle installazioni, e dentro
# ognuna `ComfyUI/custom_nodes` e' quella che l'app carica. Indovinare un percorso
# significa installare un nodo che l'app non leggera' mai — e sembrera' tutto a
# posto, perche' la cartella esiste.
trova_custom_nodes() {
  if [[ -n "$NODES" ]]; then
    if [[ ! -d "$NODES" ]]; then
      errore "cartella custom_nodes indicata non trovata: $NODES"
      return 1
    fi
    printf '%s\n' "$NODES"
    return 0
  fi
  local config="$HOME/Library/Application Support/Comfy Desktop/settings.json"
  local base="" trovati="" cartella=""
  if [[ -f "$config" ]]; then
    base="$(campo_json "$config" installDir)"
  fi
  if [[ -n "$base" && -d "$base" ]]; then
    for cartella in "$base"/*/ComfyUI/custom_nodes; do
      [[ -d "$cartella" ]] && trovati="$trovati$cartella"$'\n'
    done
  fi
  if [[ -z "$trovati" ]]; then
    for cartella in "$HOME/Documents/ComfyUI/custom_nodes" \
                     "$HOME/ComfyUI/custom_nodes" \
                     "$REPO/../ComfyUI/custom_nodes"; do
      [[ -d "$cartella" ]] && trovati="$trovati$cartella"$'\n'
    done
  fi
  # Il conteggio con grep invece di un array: bash 3.2 (quello di macOS) non ha
  # gli array associativi, e il conteggio e' l'unica cosa che serve.
  local quanti="0"
  quanti="$(printf '%s' "$trovati" | grep -c . || true)"
  if [[ "$quanti" -eq 0 ]]; then
    errore "non trovo la cartella custom_nodes di ComfyUI: passala con --nodes <percorso>"
    return 1
  fi
  if [[ "$quanti" -gt 1 ]]; then
    errore "ci sono $quanti installazioni di ComfyUI: scegli quale con --nodes <percorso>"
    printf '%s' "$trovati" | sed 's/^/  /' >&2
    return 1
  fi
  printf '%s' "$trovati" | head -1
}

# Il nodo del repo si installa come LINK, non come copia: e' quello che fa questa
# macchina, e vuol dire che una correzione nel repository e' attiva al riavvio di
# ComfyUI. Una copia vera non si tocca: non si sa se e' piu' avanti o piu'
# indietro del repo, e cancellarla sarebbe decidere al posto dell'operatore.
collega_nodo_repo() { # <nome-file>
  local nome="$1" sorgente="$NODI_REPO/$1" destinazione="$NODI_DIR/$1"
  if [[ ! -f "$sorgente" ]]; then
    errore "il nodo del repo non c'e': $sorgente"
    return 1
  fi
  if [[ -L "$destinazione" ]]; then
    if [[ "$(readlink "$destinazione")" == "$sorgente" ]]; then
      log "$nome: a posto (link al repository)"
      return 0
    fi
    if [[ "$FORZA" -eq 0 ]]; then
      warn "$nome e' un link a un ALTRO file: $(readlink "$destinazione")"
      warn "  per riportarlo al repository: rilanciare con --forza"
      return 1
    fi
    rm -f "$destinazione"
  elif [[ -e "$destinazione" ]]; then
    warn "$nome esiste come file (non un link): non lo tocco"
    warn "  se vuoi il nodo del repository, spostalo e rilancia"
    return 1
  fi
  if [[ "$CHECK" -eq 1 ]]; then
    log "$nome: da collegare (ri-esegui senza --check)"
    return 1
  fi
  ln -s "$sorgente" "$destinazione"
  log "$nome: collegato al repository"
}

# Il pin si verifica con `git rev-parse HEAD`, non con un `grep` del nome del
# nodo: un pacchetto aggiornato ha lo stesso nome e campi diversi, quindi un
# controllo per nome direbbe «a posto» mentre il grafo manda valori che quella
# versione non conosce piu'.
installa_ipadapter() { # <cartella>
  local dir="$1" attuale="" quando=""
  if [[ -d "$dir" ]]; then
    if [[ ! -d "$dir/.git" ]]; then
      warn "$IPADAPTER_DIR esiste ma non e' un repository git: non lo tocco"
      warn "  una copia scompattata non ha un commit da verificare, quindi il pin"
      warn "  non si puo' controllare — spostala e rilancia per avere il commit pinnato"
      return 1
    fi
    attuale="$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
    quando="$(git -C "$dir" log -1 --format=%ci 2>/dev/null || true)"
    if [[ "$attuale" == "$IPADAPTER_SHA" ]]; then
      log "$IPADAPTER_DIR: al commit verificato ${IPADAPTER_SHA:0:8} ($quando)"
      return 0
    fi
    if [[ "$CHECK" -eq 1 || "$FORZA" -eq 0 ]]; then
      warn "$IPADAPTER_DIR e' al commit ${attuale:0:8}, non a ${IPADAPTER_SHA:0:8}"
      warn "  il grafo e' verificato su ${IPADAPTER_SHA:0:8}: per allinearlo, --forza"
      return 1
    fi
    log "$IPADAPTER_DIR: allineo da ${attuale:0:8} a ${IPADAPTER_SHA:0:8}"
    git -C "$dir" fetch --quiet origin
    git -C "$dir" checkout --quiet "$IPADAPTER_SHA"
  else
    if [[ "$CHECK" -eq 1 ]]; then
      log "$IPADAPTER_DIR: da installare (commit ${IPADAPTER_SHA:0:8}) — ri-esegui senza --check"
      return 1
    fi
    log "$IPADAPTER_DIR: clono $IPADAPTER_URL"
    # `--filter=blob:none`: si prendono i commit, non i blob, e il checkout tira
    # giu' solo i file del commit scelto (~1 MB invece dei ~30 MB con gli esempi).
    git clone --quiet --filter=blob:none "$IPADAPTER_URL" "$dir"
    git -C "$dir" checkout --quiet "$IPADAPTER_SHA"
  fi
  attuale="$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$attuale" != "$IPADAPTER_SHA" ]]; then
    errore "$IPADAPTER_DIR non e' al commit atteso dopo l'installazione: ${attuale:0:8}"
    return 1
  fi
  if ! grep -rqs "$IPADAPTER_NODO" "$dir"/*.py; then
    errore "$IPADAPTER_DIR non espone $IPADAPTER_NODO: non e' il pacchetto giusto"
    return 1
  fi
  if [[ -n "$PYTHON_COMFY" && -f "$dir/requirements.txt" ]]; then
    # Verificato sul commit pinnato: nessun requirements.txt e nessuna dipendenza
    # in pyproject.toml. Se un giorno ne comparisse una, si installa invece di
    # lasciare un nodo che non carica per un import mancante.
    log "$IPADAPTER_DIR: installo le sue dipendenze con $PYTHON_COMFY"
    "$PYTHON_COMFY" -m pip install --quiet -r "$dir/requirements.txt"
  fi
  log "$IPADAPTER_DIR: installato al commit ${IPADAPTER_SHA:0:8}, $IPADAPTER_NODO c'e'"
}

# DWPreprocessor non si pinna (vedi la testa del file): si controlla che ci sia,
# che esponga il nodo, e si STAMPA il commit installato — cosi' un domani che
# servisse un pin si sa da dove partire.
verifica_aux() { # <cartella>
  local dir="$1" attuale="" quando=""
  if [[ ! -d "$dir" ]]; then
    if [[ "$CHECK" -eq 1 ]]; then
      log "$AUX_DIR: manca (serve per la posa da FOTO, pose_image; da preset no)"
      return 1
    fi
    log "$AUX_DIR: clono $AUX_URL"
    git clone --quiet --depth 1 "$AUX_URL" "$dir"
    if [[ -f "$dir/requirements.txt" && -n "$PYTHON_COMFY" ]]; then
      log "$AUX_DIR: installo le sue dipendenze con $PYTHON_COMFY"
      "$PYTHON_COMFY" -m pip install --quiet -r "$dir/requirements.txt"
    fi
  fi
  if [[ -d "$dir/.git" ]]; then
    attuale="$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
    quando="$(git -C "$dir" log -1 --format=%ci 2>/dev/null || true)"
    log "$AUX_DIR: presente (commit ${attuale:0:8}, $quando) — non pinnato"
  else
    log "$AUX_DIR: presente (non un repository git)"
  fi
  if grep -rqs "$AUX_NODO" "$dir"; then
    log "$AUX_DIR: espone $AUX_NODO"
  else
    warn "$AUX_DIR: non espone $AUX_NODO — versione vecchia?"
    return 1
  fi
  if [[ ! -d "$dir/ckpts" ]]; then
    # yolox_l.onnx e dw-ll_ucoco_384.onnx (i due nomi che il grafo dichiara) non
    # stanno in un manifest: li scarica il nodo al primo uso (~200 MB), quindi la
    # prima posa da foto ha bisogno di rete.
    log "$AUX_DIR: i pesi DWPose li scarica il nodo al primo uso (~200 MB, serve rete)"
  fi
  return 0
}

# La prova che conta non e' un file sul disco: e' se ComfyUI li ha CARICATI.
# `/object_info` e' la stessa lista che vede il grafo, e per un nodo che non
# esiste la risposta e' `{}` — esattamente l'ambiguita' da sciogliere.
prova_nodo() { # <nome nodo>
  local nodo="$1" risposta="" corpo="" codice=""
  if ! command -v curl >/dev/null 2>&1; then
    return 2
  fi
  risposta="$(curl -sS --max-time 5 -w $'\n%{http_code}' \
              "$COMFY_URL/object_info/$nodo" 2>/dev/null || true)"
  codice="${risposta##*$'\n'}"
  corpo="${risposta%$'\n'*}"
  if [[ -z "$codice" || "$codice" == "000" ]]; then
    log "$nodo: ComfyUI non risponde su $COMFY_URL (avvialo, poi rilancia --check)"
    return 2
  fi
  if [[ "$corpo" == "{}" || "$corpo" == "null" ]]; then
    warn "$nodo: ComfyUI risponde ma NON conosce $nodo"
    warn "  i nodi si caricano all'avvio: riavvia ComfyUI dopo l'installazione"
    return 1
  fi
  log "$nodo: caricato in ComfyUI"
  return 0
}

NODI_DIR="$(trova_custom_nodes)" || exit 3
[[ -n "$NODI_DIR" ]] || exit 3
RADICE_COMFY="$(dirname "$NODI_DIR")"

# L'interprete giusto e' quello con cui ComfyUI gira davvero: nei suoi log e'
# `<radice>/.venv/bin/python3`. `standalone-env` e' l'ambiente che l'app desktop
# installa accanto (macOS). Serve solo per `pip install -r requirements.txt` dei
# nodi di terze parti: sul pacchetto pinnato non c'e' niente da installare.
PYTHON_COMFY=""
for candidato in "$RADICE_COMFY/.venv/bin/python3" \
                 "$(dirname "$RADICE_COMFY")/standalone-env/bin/python3"; do
  if [[ -x "$candidato" ]]; then PYTHON_COMFY="$candidato"; break; fi
done
if [[ -z "$PYTHON_COMFY" ]] && command -v python3 >/dev/null 2>&1; then
  PYTHON_COMFY="$(command -v python3)"
fi

log "cartella dei nodi: $NODI_DIR"
log "python di ComfyUI: ${PYTHON_COMFY:-non trovato}"
log "repo:              $REPO"
echo

problemi=0
for nome in $NODI_NOSTRI; do
  collega_nodo_repo "$nome" || problemi=$((problemi + 1))
done
echo
installa_ipadapter "$NODI_DIR/$IPADAPTER_DIR" || problemi=$((problemi + 1))
echo
verifica_aux "$NODI_DIR/$AUX_DIR" || problemi=$((problemi + 1))

echo
for nodo in HyperSpacePosePreset HyperSpaceSaveJPEG "$IPADAPTER_NODO" "$AUX_NODO"; do
  prova_nodo "$nodo" || true
done

echo
if [[ "$CHECK" -eq 1 ]]; then
  if [[ "$problemi" -eq 0 ]]; then
    log "rapporto: non manca niente da installare."
  else
    log "rapporto: $problemi cose da sistemare (ri-esegui senza --check, o con --forza)."
  fi
  exit 0
fi
if [[ "$problemi" -gt 0 ]]; then
  errore "$problemi cose non sono andate a posto: leggi le righe qui sopra."
  exit 1
fi
log "fatto. Riavvia ComfyUI (i nodi si caricano all'avvio), poi:"
log "  python integrations/comfyui/comfy_bridge.py --check"
