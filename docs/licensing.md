# Licenza: cosa e' stato deciso e perche'

## La decisione

**Apache License 2.0, una sola licenza, su tutto il repository.** In vigore dal
19/09/2026, in sostituzione della MIT License con cui il progetto era nato.

Concordata fra i due detentori di copyright — `opodark` (Alberto Raul Marinoni) e
`cips` — e allineata alla proposta per la parte core.

## Perche' Apache-2.0 e non MIT

- **Garantisce esplicitamente i brevetti** (§3), cosa che la MIT non fa. Questo
  progetto spedisce crittografia e protocolli di mesh: e' il punto in cui la
  differenza conta davvero.
- **E' ammessa dove GPL/AGPL sono vietate** dalle policy aziendali. Per
  infrastruttura che si vuole far girare ad altri, questo pesa piu' di una
  protezione che alla scala attuale non si potrebbe comunque far rispettare.
- **MIT → Apache-2.0 e' un ampliamento**, non una restrizione: aggiunge il
  brevetto, il NOTICE e la clausola sui marchi (§6). E' quindi facile da far
  accettare a chi ha gia' contribuito.
- **Nessuna dipendenza e' copyleft** (tutte MIT/BSD/Apache, vedi
  `THIRD-PARTY-NOTICES.md`), quindi la scelta resta libera anche in futuro.

## Cosa e' stato fatto

| Cosa | Dove |
|---|---|
| Testo integrale Apache-2.0 | `LICENSE` |
| Attribuzione e detentori | `NOTICE` |
| Componenti di terze parti | `THIRD-PARTY-NOTICES.md` |
| Header per file (`SPDX-License-Identifier`) | sorgenti `.py .js .mjs .sh .ps1` |
| Test che impone l'header | `tests/test_licensing.py` |

## Cosa NON e' stato fatto, e perche'

**Lo split open-core** (core Apache-2.0 + UI/enterprise AGPL o source-available)
**e' rimandato**, e non per pigrizia: oggi il confine **non esiste nel codice**.

Il candidato naturale alla parte "enterprise" — dashboard avanzata, governance,
billing, multi-tenant — vive dentro `control-plane/main.py`, che e' **un solo
file da oltre quattromila righe** contenente routing, MCP, web-node, dreams,
federazione e admin. Non si possono mettere due licenze in un file, e non si puo'
vendere un'eccezione su una feature che e' un ramo di codice dentro il file del
core.

**Il confine lo creano le interfacce, non le cartelle.** La sequenza corretta e':
prima si separa il codice (moduli con interfaccia esplicita, montati a parte),
poi si licenzia la parte separata. Farlo adesso produrrebbe file triplicati, un
CLA da gestire e zero benefici: il progetto ha pochissimi utenti esterni e
nessuna offerta commerciale da difendere.

### Quando tornera' a essere una decisione vera

Quando ci sara' un'offerta a pagamento, e con una scelta consapevole fra due cose
che **non sono alternative**:

| | AGPL | Source-available (BUSL/Elastic/FSL) |
|---|---|---|
| E' open source (OSI) | si | **no** |
| Impedisce il servizio concorrente | no | si |
| Consente il dual-licensing commerciale | si, se possiedi tutto il copyright | n/d (e' gia' proprietaria) |
| Costo | molte aziende la vietano per policy | fuori dalle distro, filtri nel procurement |
| Cosa difende | chi incorpora senza contribuire | chi rivende il tuo lavoro come servizio |

## Questioni aperte

1. **`O365` non dichiara la licenza** nel metadata PyPI: e' l'unica dipendenza
   non verificata, da controllare upstream prima di una distribuzione pubblica.
2. **L'accordo con i contributori e' verbale.** Oggi basta: i detentori sono due
   e d'accordo. Se il progetto accettera' contributi esterni, un CLA — o almeno
   una dichiarazione scritta per ogni contributore — diventa necessario **prima**
   di qualunque licenza commerciale, altrimenti la leva non e' utilizzabile sul
   codice altrui. Farlo quando il contributo vale poco e' molto piu' facile che
   farlo dopo.
3. **`worker/` e `authority/`** non sono citati in nessun compose: codice morto.
   Non e' un problema di licenza (e' tutto Apache-2.0), e' un problema di
   chiarezza: o si usano o si tolgono.
4. **Il marchio.** Il nome collide con un progetto omonimo con migliaia di
   stelle. Apache-2.0 **non** concede il marchio (§6): se il nome diventa un
   problema, la leva e' una trademark policy, non il copyright.
