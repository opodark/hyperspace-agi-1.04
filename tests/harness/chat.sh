#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/chat.sh — il contratto OpenAI di /v1/chat/completions.
#
# È la baseline più dipendente dall'ambiente, e per tre motivi che si sono
# scoperti uno alla volta. Vale la pena lasciarli scritti:
#
# 1. Serve un modello. `tests/mock_openai.py` fa da backend finto e `OLLAMA_URL`
#    punta a lui. Senza, il server non ha niente da chiamare.
# 2. `TOOL_CAPABLE_MODELS` è indispensabile per `con_tools`. Il control-plane
#    toglie i tool dalla richiesta quando il modello non è dichiarato capace
#    (`payload.pop("tools", None)`), quindi il caso passerebbe comunque — ma
#    sarebbe una risposta senza tool_calls, cioè la verifica del percorso dei tool
#    non avrebbe provato niente.
# 3. `MOCK_TOOL_CLIENTE` dice al finto di rispondere con tool_call solo quando è
#    il CLIENT a aver chiesto un tool con quel nome. Senza, il finto risponderebbe
#    tool a ogni richiesta — perché il CP aggiunge il suo catalogo a tutte — e la
#    baseline diventerebbe tredici risposte identiche.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

MOCK_PORTA=8099
MODELLO=modello-finto

ferma_server
pkill -f 'tests/mock_openai.py' 2>/dev/null || true
trap 'pkill -f "tests/mock_openai.py" 2>/dev/null || true; ferma_server' EXIT

MOCK_TOOL_CLIENTE=tool_di_prova_client MOCK_MODEL="$MODELLO" \
  "$PY" tests/mock_openai.py "$MOCK_PORTA" > /tmp/hyperspace-mock-openai.log 2>&1 &
for _ in $(seq 1 20); do
  curl -s -m 2 -o /dev/null "http://127.0.0.1:$MOCK_PORTA/v1/models" 2>/dev/null && break
  sleep 1
done

export MOCK_MODEL="$MODELLO"

avvia_server chat \
  OLLAMA_URL="http://127.0.0.1:$MOCK_PORTA" \
  OLLAMA_MODEL="$MODELLO" \
  TOOL_CAPABLE_MODELS="$MODELLO" \
  INFERENCE_BACKEND=ollama-direct
esegui_baseline chat_baseline.py "$@"
