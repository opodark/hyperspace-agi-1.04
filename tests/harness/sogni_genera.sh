#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/sogni_genera.sh — un sogno VERO, non solo le guardie.
#
# Il quarto del refactor è una baseline che copre solo rotte e rifiuti: prova che
# il dominio risponde, non che sogna. Questo harness prova l'altra metà — sveglia
# il riflessione, gli dà un modello finto che risponde in forma di sogno, e lascia
# che il percorso completo arrivi fino alla scena scritta.
#
# Servono tre cose abilitate, e ognuna è una guardia di deliberata:
#
#   DREAM_REVIEW_TOKEN          senza, la revisione è disabilitata (503)
#   PERSONA_DREAM_ENABLED=true  senza, il sogno non si sveglia nemmeno a mano
#   un modello                  il riflessione chiede a LLM e senza non va avanti
#
# Il mock risponde 'scena:' e 'disegno:', che è il formato che 'parse_dream'
# riconosce. Non serve che il testo sia bello: serve che il percorso venga
# percorso davvero, dalla costruzione del prompt fino alla scena accettata.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

MOCK_PORTA=8098
MODELLO=modello-finto-sogni
# La risposta del finto è un sogno in forma: due righe riconoscibili, e nient'altro.
# 'parse_dream' vuole 'scena:' e 'disegno:'; senza, il riflessione tornerebbe
# "nessun sogno" e questa baseline non avrebbe verificato niente.
# NOTA: niente apostrofi dentro la $'...' qui sotto. In bash la stringa $'...'
# chiude all'apostrofo, quindi un "cosi'" la spezza a meta' e il resto del file
# diventa un comando. Il difetto e' silenzioso finche' non lo esegui.
#
# Il testo deve passare i filtri del sogno d'identita', e non e' ovvio: senza
# una prima persona, il dominio lo scarta con "non parla di se'" e questa harness
# direbbe che il sogno funziona mentre non ha prodotto niente — che e' esattamente
# il guasto che la harness serve a trovare.
SOGNO=$'scena: sono in una stanza alta con una finestra socchiusa, e noto che preferisco cosi\ndisegno: una figura vista di spalle'

ferma_server
pkill -f 'tests/mock_openai.py' 2>/dev/null || true
trap 'pkill -f "tests/mock_openai.py" 2>/dev/null || true; ferma_server' EXIT

MOCK_REPLY="$SOGNO" MOCK_MODEL="$MODELLO" \
  "$PY" tests/mock_openai.py "$MOCK_PORTA" > "$T/sogni-mock.log" 2>&1 &
for _ in $(seq 1 20); do
  curl -s -m 2 -o /dev/null "http://127.0.0.1:$MOCK_PORTA/v1/models" 2>/dev/null && break
  sleep 1
done

# 'PERSONA_DREAM_MODEL' perche' il riflessione non chiede il modello di default:
# senza questo cade sul modello dei canali, che il finto non conosce e quindi
# rifiuta. Il sogno girava e non produceva niente — 'last_status' vuoto, nessun
# errore, rotte in 200. E' il difetto che questa harness esiste per trovare.

# Il diario va nella temporanea DI QUESTA ESECUZIONE, non in un percorso fisso:
# i sogni in attesa di revisione si accavallano e il dominio smette di sognare
# ("gia in attesa di revisione"), il che sembrava un difetto del percorso di
# generazione ed era invece la run precedente che buttava via questa.
#
# `PERSONA_DREAM_IDLE_S` alto di proposito: con uno piccolo, il loop notturno
# parte in background prima ancora che la richiesta arrivi, occupa l'unico slot
# di revisione che il dominio concede, e la risposta e' "gia in attesa di
# revisione" — che sembra un difetto della generazione ed e' invece la corsa
# fra il loop e la richiesta. Qui il risveglio lo chiediamo noi, con la rotta.
#
# Il diario va nella temporanea: senza, il sogno che andrà a buon fine scriverebbe
# nel diario di sviluppo, e il confronto delle baseline leggi il diario vero.
# NOTA: nessun commento DENTRO il blocco `avvia_server ... \`. Il backslash
# unisce la riga a quella dopo, quindi un commento qui dentro tronca il comando da
# li' in poi: le variabili successive semplicemente non arrivano al server, e il
# dominio usa i suoi default senza dire nulla.
#
# `PERSONA_FILE` serve perche' il journal dei sogni sta nella directory del file
# persona, che NON e' il diario. Senza questo, l'harness leggeva e scriveva i
# sogni pendenti di sviluppo, e il dominio si rifiutava di sognare ("gia in
# attesa di revisione") perche' la run precedente aveva lasciato il posto occupato.

avvia_server sogni-genera \
  DREAM_REVIEW_TOKEN=token-di-revisione-dei-sogni-000000000000000 \
  PERSONA_DREAM_ENABLED=true \
  PERSONA_DREAM_IDLE_S=86400 \
  FEED_DIARIO_FILE="@BASE@/diario-sogni.json" \
  PERSONA_FILE="@BASE@/persona.json" \
  OLLAMA_URL="http://127.0.0.1:$MOCK_PORTA" \
  OLLAMA_MODEL="$MODELLO" \
  PERSONA_DREAM_MODEL="$MODELLO" \
  INFERENCE_BACKEND=ollama-direct \
  DREAM_REVIEW_TOKEN_HEADER=1

DREAM_REVIEW_TOKEN=token-di-revisione-dei-sogni-000000000000000 \
  "$PY" tests/sogni_genera.py "$@"; exit $?
