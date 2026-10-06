#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# tests/harness/bottles.sh — le bottiglie: il passaporto firmato di un nodo.
#
# Il percorso felice di `/bottles/announce` costa circa un secondo e mezzo: crea
# una bottiglia, e crearla significa trovare un nonce che soddisfa la proof-of-work
# (20 bit). Va bene — ma è il motivo per cui questa baseline mette in coda il caso
# felice DOPO quelli che non lo richiedono: se il server è rotto, i primi dicono
# perché, e non si aspetta un secondo e mezzo per scoprirlo.

source "$(dirname "${BASH_SOURCE[0]}")/_comune.sh"

ferma_server
trap ferma_server EXIT

# PUBLIC_ENDPOINT e' l'indirizzo con cui questo nodo si presenta agli altri. Senza,
# `/bottles/announce` risponde "nessun endpoint da annunciare" e il caso felice
# non arriva manco alla proof-of-work.
avvia_server bottles PUBLIC_ENDPOINT=http://127.0.0.1:11434
esegui_baseline bottles_baseline.py "$@"
