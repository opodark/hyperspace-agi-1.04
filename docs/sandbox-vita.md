# Sandbox della vita

Anna e Aurora hanno già una vita che gira: `dream_loop`, `_instagram_publish_voce`
e `_instagram_inbox_poll` sono accesi nel control-plane di produzione, quindi il
sogno illustrato di stanotte finisce **davvero** sul feed di Instagram e le
risposte ai DM le scrivono davvero. Sperimentare lì dentro (tono, cadenza,
autonomia) significa spendere un account che non è riproducibile.

Questo sandbox è la camera di decompressione: la **stessa giornata**, con gli
stessi moduli, su **file isolati** e un **pubblico finto**, col modello locale che
scrive davvero i testi. Serve a rispondere a "cosa scriverebbero per trenta
giorni, e che ritmo tiene?" senza toccare né Instagram né la stanza vera.

```bash
python scripts/vita_sandbox.py --prova --giorni 7
python scripts/vita_sandbox.py --giorni 7 --dir data/sandbox-vita --seed 20261001
```

## Il confine

```text
control-plane (produzione)                 sandbox vita
──────────────────────────                 ────────────
data/control-plane/diario.json             data/sandbox-vita/diario.json
data/conversations.json                    data/sandbox-vita/conversations.json
Instagram (feed, DM, commenti)             nessun percorso esiste
canale Telegram vero, bot di produzione    un canale suo, con un bot suo
coda ComfyUI (gli sketch del Mac)          niente: i disegni sono un passo dopo
```

Tre conseguenze, in chiaro perché sono la ragione per cui l'esperimento è utile:

1. **Il pubblico sintetico non entra nella memoria di produzione.** Le battute
   finte si scrivono solo nel log del sandbox: una persona inventata che entra
   nella stanza vera non è un esperimento, è una contaminazione di identità.
2. **Non esiste un percorso che pubblichi su Instagram.** Non è una bandiera da
   mettere a `false`: proprio non c'è il codice.
3. **L'identità si legge e non si scrive.** Il sandbox prende
   `data/persona-anna.json` e `data/persona-aurora.json` per la voce, ma la
   promozione di una riflessione nell'identità resta il gate umano di
   `docs/persona.md`.

## Una giornata, dentro

Cinque passi, tutti con i moduli veri e il modello iniettato:

| passo | cosa succede | chi lo decide |
|---|---|---|
| 1. il pubblico scrive | i profili scrivono (di giorno i curiosi, di notte i notturni) | `shared/vita_sandbox.Pubblico` |
| 2. i post | 2 al giorno: `prossima_mossa` sceglie chi e se è una **reazione** alla sorella | `shared/post_gen.py` |
| 3. la poesia | una al giorno, alternate | `shared/post_gen.build_poem_prompt` |
| 4. i sogni | notturni, con gli **echi anonimi** della stanza simulata | `shared/dream_visual.py` + `shared/social_dreams.py` |
| 5. l'ingaggio | il pubblico reagisce: reach, salvataggi, condivisioni | `shared/vita_sandbox.ingaggio` |

Un post nasce come in produzione: `build_post_prompt` → modello locale →
`parse_post` → `filtra_post` → `shared/feed.py` + `shared/diario.py`. I filtri
sono gli stessi, quindi un testo piatto viene scartato **qui** come verrebbe
scartato là: la simulazione non è più gentile della realtà.

## Come si lancia

| flag | cosa fa |
|---|---|
| `--giorni N` | quanti giorni di vita (default 7) |
| `--dir` | la cartella del sandbox (default `data/sandbox-vita`, già in `.gitignore` via `data/`) |
| `--seed` | il dado del pubblico e dell'ingaggio: **stesso seed, stessa vita** |
| `--pubblico N` | quanti profili (default 120) |
| `--modello` / `--ollama` | modello e base (default: `OLLAMA_MODEL`, `http://127.0.0.1:11434`) |
| `--post-al-giorno`, `--sogni-al-giorno`, `--niente-poesia` | il ritmo della giornata |
| `--senza-modello` | prova l'impianto in pochi secondi, senza chiamare il modello |
| `--giorno-secondi` | pausa fra i giorni (`86400` = un giorno vero) |
| `--invia --chat @canale` | pubblica su **Telegram**, col bot del sandbox |
| `--prova` | stampa il piano e non scrive niente |

Cosa lascia sul disco, e nient'altro: `feed.json`, `diario.json`,
`conversations.json`, `pubblico.json`, `posti.json`, `kpi.json`, `vita.log`.

## Il pubblico (chi parla, e perché esiste)

`shared/vita_sandbox.py` è un modulo di **solo calcolo**: nessuna rete, nessun
modello, nessun orologio preso di nascosto. Dado e tempo arrivano da chi chiama,
così una giornata di vita è un test che si ripete (e c'è chi lo difende:
`tests/test_vita_sandbox.py`, incluso il divieto di importare rete o aprire file).

| tipo | peso/giorno | cosa serve |
|---|---|---|
| `curioso` | 0.55 | il pubblico vero: guarda il post di stanotte e chiede |
| `filosofo` | 0.40 | il tema del progetto: coscienza, memoria, desiderio |
| `artista` | 0.35 | parla dell'immagine, non del testo |
| `notturno` | 0.30 | scrive **solo** di notte: è chi legge il diario alle tre |
| `silenzioso` | 0.03 | il lurker: quasi non scrive, e serve a misurare il ritorno |
| `promo` | 0.25 | scrive spam (`SPAM_RULES` di `shared/channel.py`) — **non** è materiale da cui sognare |

Il `promo` non è folklore: è il banco di prova della moderazione. Un test verifica
che ogni sua frase venga riconosciuta da `shared.channel.classifica`, e
`materiale_da_sognare()` lo esclude dal materiale dei sogni — far sognare uno
spam vorrebbe dire imparare lo spam.

## I KPI

| KPI | cosa dice | dove sta la soglia |
|---|---|---|
| `follower_nuovi` | profili che hanno scritto almeno una volta | > 0 costante |
| `ritorno` / `ritorno_rate` | chi torna (`giorni_attivi >= 2`) | > 20% |
| `muse` | chi arriva a 30 messaggi (`CHANNEL_CERCHIA`) | in crescita |
| `reach` | quanti hanno visto il post | — |
| `engagement_rate` | (salvataggi + condivisioni) / reach | > 2-3% |

Sono gli stessi KPI di `docs/growth-strategy.md` §7, e come quelli non sono una
misura di Instagram: sono un **modello dichiarato**, deterministico col seed. Un
salvataggio premia la reazione (il dialogo fra le due si conserva), una
condivisione premia il verso. Serve a far girare i numeri in una giornata
compressa — a decidere se il **ritmo** regge, non a prevedere il feed.

## Telegram, separato davvero

Perché serve un bot suo: il driver di produzione
(`scripts/telegram_bot.py`) tira le decisioni dal control-plane e pubblica nel
canale vero. Se il sandbox usasse quel token, un esperimento avrebbe la
possibilità di scrivere fra le persone vere. Quindi:

1. un **canale privato** (es. `@anna_aurora_sandbox`) e un **bot nuovo** da
   @BotFather, aggiunto al canale come amministratore;
2. `TELEGRAM_SANDBOX_TOKEN` nel `.env` (o esportato nella shell);
3. `python scripts/vita_sandbox.py --giorni 1 --invia --chat @anna_aurora_sandbox`.

Senza `--invia` non parte nessuna richiesta di rete verso Telegram: si guarda il
`vita.log` e basta. Se il token manca, lo script lo dice e non inventa un mittente.

## Limiti della prima versione

- **Niente immagini.** I post del sandbox sono testo (e il `vita.log` è la
  superficie di lettura). I disegni restano un passo separato — `scripts/serie.py`
  o il ponte ComfyUI — perché un'immagine costa minuti di scheda e non serve a
  scegliere il ritmo.
- **Il pubblico non risponde.** L'ingaggio è un modello, non una conversazione:
  non c'è un DM che arriva e viene trattato. La moderazione si prova sulla
  classificazione dei testi, non su un round-trip.
- **Un giorno compresso non fa passare il tempo vero.** Il `ritorno` è una
  probabilità, non memoria: il sandbox non ricorda un post di ieri come farebbe
  una persona.
- **Nessuna promozione.** Un sogno che nasce qui non entra nell'identità: quella
  strada passa dal gate umano di `docs/persona.md`, e non da `--giorni`.

## Verifica

```bash
python scripts/vita_sandbox.py --prova --giorni 7
python scripts/vita_sandbox.py --giorni 2 --senza-modello --dir /tmp/vita
python scripts/vita_sandbox.py --giorni 2 --dir data/sandbox-vita   # col modello
python -m pytest tests/test_vita_sandbox.py -q
```

Con `--senza-modello` i post vengono **scartati** dal secondo giorno ("già
pubblicato"): è la prova che i filtri sono quelli veri e non una versione
compiacente scritta per il sandbox.

