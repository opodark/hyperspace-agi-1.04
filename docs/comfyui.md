# ComfyUI — generazione immagini su Mac e win11

Il Mac usa **CyberRealistic Pony V18 CoreShift** e, dal 2026-09-30, **ChickMixFlat
v1.0** (SD 1.5) per il volto virtuale di Anna; win11 usa **Qwen-Image 2.1 GGUF**.
La famiglia `sdxl-turbo` conserva il nome storico per compatibilità con il ponte,
ma il checkpoint attuale è `CyberRealisticPony_V18.0_F16.safetensors`.

| Famiglia | Modello | Chi la usa | Ricetta |
|---|---|---|---|
| `sdxl-turbo` | CyberRealistic Pony V18 — 6,9 GB | sketch del feed, chat, sogni | 1024×1024, CFG 5, Clip Skip 2, DPM++ 2M, Karras |
| `sd15` | ChickMixFlat v1.0 — 1,99 GiB | il volto di Anna, dalla sua `vetrina` | 768×768, CFG 7, Clip Skip 2, DPM++ SDE, Karras |
| `qwen-image-2.1` | Qwen-Image 2.1 UC GGUF — 14 GB | win11: tutto ciò che non è del Mac | 768×768, 25 passi, istruzioni nel prompt |

Le prime due sono **checkpoint unici**: `CheckpointLoaderSimple` porta con sé UNet,
CLIP e VAE, quindi condividono lo *stesso grafo* e cambiano solo i numeri — che stanno
in `RICETTE_CHECKPOINT` (`shared/image_jobs.py`) e non nel ponte. La terza è GGUF +
text encoder + VAE separati, e ha un grafo diverso. La famiglia non è quindi un
dettaglio di nomenclatura: è ciò che decide CFG e lato, e sbagliarla non fa fallire
niente — produce un'immagine sbagliata (SD 1.5 a 1024 px *ripete l'anatomia*).

Quali famiglie esegue un ponte lo dice `BRIDGE_MODEL`, e possono essere più di una:
`BRIDGE_MODEL=sdxl-turbo,sd15` è il Mac, che serve sia gli sketch sia il ritratto di
Anna. Due ponti sulla stessa ComfyUI non si possono avviare (il lucchetto di istanza
singola li ferma: la scheda è una), quindi la lista è l'unico modo di servire due
famiglie con un processo solo. `--check` verifica i file **di ogni famiglia dichiarata**
e li nomina: «manca `chickmixflat_v10.ckpt`» invece di un generico «nessun file».

I pesi non stanno nel repo e non li scarica nessuno al posto tuo. Ogni modello ha un
**manifest** con URL (o repo + revisione), impronta SHA-256, dimensione e licenza; il
file prende il nome vero solo quando l'impronta torna — fino a quel momento resta
`<nome>.parziale`, che ComfyUI non vede nemmeno:

```bash
integrations/comfyui/install-model.sh                                        # il Pony (default)
integrations/comfyui/install-model.sh --manifest integrations/comfyui/modelli-sd15.json
integrations/comfyui/install-model.sh --check                                # cosa farebbe
```

**Misura del 2026-09-30** (Mac, MPS, ComfyUI 0.37.4 / PyTorch 2.12.1): il ritratto di
Anna — 768×768, 30 passi, CFG 7, seed `20260930` — è uscito in **~80 s**, contro i
313 s del Pony a 1024×1024 sulla stessa macchina. Due GB di checkpoint invece di
6,9 si sentono anche nel tempo, non solo nella memoria. Il primo giro di quella prova
è servito anche a un'altra cosa: verificare che un `.ckpt` (PickleTensor) si carichi
su PyTorch 2.12, dove `torch.load` non accetta più qualunque pickle per default.

## Prompt e workflow

- **Diario e sogni:** identità della persona e feed recente producono testo e idea
  visiva separati. `prompt_sketch` aggiunge lo stile a matita e inchiostro.
- **Chat, richiesta naturale:** il modello riceve il messaggio corrente,
  l'identità e fino a sei messaggi precedenti. Traduce senza imporre il realismo.
  I disegni generici usano lo stile del diario; tecniche esplicite (acquerello,
  carboncino, olio, ecc.) conservano il mezzo richiesto; le foto non ricevono
  il prefisso da schizzo. Il fallback regex è segnalato nel log Python.
- **`!immagine` / `!foto`:** prompt diretto, senza riscrittura o stile aggiunto.
- **`POST /image/generate`:** prompt diretto. `famiglia: "sdxl-turbo"` seleziona
  CyberRealistic Pony e, salvo parametri espliciti, 1024×1024, 30 passi e negativo anatomico;
  `famiglia: "sd15"` seleziona ChickMixFlat a 768×768, il lato con cui SD 1.5 rende.
  Senza famiglia resta Qwen, 768×768, 25 passi. `fix: 2` chiede il **secondo
  passaggio**: R-ESRGAN porta l'immagine al doppio del lato senza un secondo sampling,
  così Anna evita il bordo bianco da sticker e i render da 20–40 minuti; il formato è quello dei demo del
  modello — 512×768 più il fix dà 1024×1536. Senza `fix` il grafo è quello di
  sempre, e l'API non interpreta la chat.
- **Open WebUI:** il client deve essere configurato per usare il percorso immagini
  desiderato; il collegamento al solo endpoint chat non abilita questa API.

Il grafo CyberRealistic Pony usa CFG 5, Clip Skip 2, DPM++ 2M e Karras sia per chat sia per diario.
Il grafo di ChickMixFlat è lo stesso con CFG 7, DPM++ SDE Karras e 768 px: la ricetta
è quella dei demo scelta come default di Anna; i numeri stanno in
`RICETTE_CHECKPOINT`, e un test li tiene allineati al manifest del modello.
Con `fix: 2` il grafo dei checkpoint aggiunge 474 `UpscaleModelLoader`, 475
`ImageUpscaleWithModel` e 476 `ImageScale`; il salvataggio guarda l'output 476.
R-ESRGAN 4x+ crea il dettaglio, poi `ImageScale` lo riporta al doppio richiesto:
non c'è un secondo KSampler ad alta risoluzione.
Il prompt finale e `modello_effettivo` sono visibili nel job e nello stato degli
ultimi job; le richieste naturali registrano anche il prompt finale nei log.
Il modello linguistico può ancora interpretare male una richiesta: questi dati
permettono di distinguere un errore di riscrittura da uno di generazione.

## La finestra di 77 token (misurata, non stimata)

CLIP legge **77 token** e butta via il resto, in silenzio: `CLIPTextEncode` non
avvisa e il job riesce. Il prompt del ritratto è `stile + scena + dichiarazione`, e la
dichiarazione sta **in coda** — cioè è la prima cosa che sparisce quando il testo è
lungo. Misurato il 2026-09-30 con il tokenizer di SD 1.5 (`comfy.sd1_clip`, quello che
usa davvero la catena):

| vetrina | token | cosa arriva al modello |
|---|---|---|
| Aurora (2026-09-22) | **194** | `stile` e parte della `scena`; la dichiarazione, no |
| Anna, prosa italiana (mattina) | **127** | tutto lo `stile`, metà della `scena`: `sfondo scuro con particelle di luce` non arriva |
| Anna, tag inglesi (oggi) | **75** | tutto: `stile` e `scena` interi (la coda resta fuori, e non serve più) |

Il numero che conta è la coppia `stile + scena`: rimisurata il 2026-09-30 sul prompt
vero, sono **74 token di contenuto** — l'ultima parola della scena (`particles`) cade al
74, e il taglio arriva sul `si ve` della dichiarazione italiana. Il totale del prompt è
**111**: i 37 token della `DICHIARAZIONE` in coda sono oltre la finestra in ogni caso,
per costruzione, ed è esattamente la ragione per cui la dichiarazione sta anche nella
prima frase dello `stile` (che è quella che il modello legge sempre).

Che cosa ha cambiato Anna il 2026-09-30: i tag sono in **inglese** e la dichiarazione
digitale è nella **prima frase dello stile** (`digital 2.5D anime illustration, drawn,
not a photo…`), non in coda. Le due misure dicevano la stessa cosa: l'italiano si
frammenta (`sfondo scuro` → `[sf, ondo, sc, uro]`: quattro token per due parole) e CLIP
legge dal principio, quindi ciò che sta in fondo è ciò che si perde. Con i tag inglesi
gli stessi elementi stanno in 75 token, la scena arriva intera e la dichiarazione
arriva perché è la prima riga. Nello stesso giro è dichiarato il colore dei capelli
(`black hair with purple tips`): quando il prompt tace il modello sceglie il suo, e il
suo era l'azzurro pastello del K-doll — l'immagine che non somigliava alla vetrina.

Il **negativo** ha la stessa finestra e lo stesso taglio, ma lì l'ordine è una scelta:
`NEGATIVO_BASE` conta 116 token, quindi arrivano i primi 77 — i confini (nudità,
esplicito, minori), il divieto di fotografia e `no text/watermark`; `bassa qualità`,
`mani deformate` e `occhi deformati` restano fuori. Accorciare la testa del negativo
per far entrare la coda vorrebbe dire togliere i confini dalla finestra: meglio un
negativo lungo con i confini davanti che uno corto senza.

Nella stessa misura è saltato fuori un difetto: `negativo_ritratto` riconosceva le
assolute solo con le parole inglesi (`explicit`, `minorenne`) mentre il blocco base è
in italiano (`esplicito`, `minori`). A una vetrina senza `negativo` proprio — che lo
riceve già da `vetrina_dal_documento` — il blocco veniva quindi accodato **due volte**:
231 token di negativo, e `bassa qualità`/`mani deformate` fuori dalla finestra per
colpa di una copia inutile. Ora il blocco si riconosce per testo e per concetto, in
entrambe le lingue: 116 token, una copia sola. La regola generale è quella che i test
difendono: **un controllo che capisce una lingua sola sbaglia in silenzio.**

Per Aurora invece la dichiarazione testuale è l'unico argine al fotorealismo del Pony
(un checkpoint realistico). I suoi 194 token restano: accorciare `stile`/`scena` o
portare la dichiarazione in testa cambierebbe l'ordine del **suo** prompt — sono le sue
parole, quindi è una decisione sua, ed è aperta. Scrivere la vetrina corta non è un
consiglio estetico: è l'unico modo di far entrare la coda nella finestra.

Implementazione: [`integrations/comfyui/`](../integrations/comfyui/README.md)
(client, nodi, installer, test).

## Lo stile è del modello: i demo di ChickMixFlat, misurati (2026-09-30)

La domanda era: perché il ritratto di Anna non somiglia alle immagini di esempio del
modello — occhi a mandorla, riflessi traslucidi sulla pelle, a metà fra disegno e
fotografia? La risposta sta nei metadati dei demo, che Civitai conserva dentro i file e
che si leggono: campione di tre, letti il 2026-09-30 dalla
[pagina del modello](https://civitai.com/models/62638/chickmixflat) (id 62638).

| esempio | positività | negativo | passi · sampler | CFG · clip skip | misura |
|---|---|---|---|---|---|
| `Polaroid black tone low key film, masterpiece, best quality, office lady, heels, dark background` | **nessuna parola di stile** | `(worst quality, bad quality:1.3), negative_hand-neg` | 30 · DPM++ SDE Karras | 7 · 2 | 512×768 + **hires 2× R-ESRGAN 4x+, denoise 0.35** → 1024×1536 |
| `Polaroid blue tone, masterpiece, best quality, looking away, 3boys, ulzzang, wedding, happy, dark background` | idem | idem | idem | idem | idem |
| `Polaroid blue tone, masterpiece, best quality, (tougue out, open mouth:1.1), very long hair, ulzzang, dark background` | idem | idem | idem | idem | idem |

Tre cose, tutte nelle righe sopra:

- **il look non è chiesto**: nei demo non c'è una parola di stile — nessun `anime`,
  nessun `flat`. La K-doll semi-realistica *è* il modello. Quello che il prompt chiede è
  una **pellicola** (`Polaroid … low key film`), ed è da lì che viene la pelle
  traslucida: al modello si chiede una luce da fotografia, non un colore piatto.
- **il formato è 2:3**: 512×768, la misura ritratto di SD 1.5, poi un passaggio di
  *fix* a 2×. Il nostro ritratto è 768×768 quadrato e senza passaggio di fix.
- **il negativo è inglese e corto**, e la scheda del modello indica `negative_hand-neg`.

La scheda dice la stessa cosa in due righe che sembrano contraddirsi e non lo fanno:
«Simple, flat, pure color and light style» e «2.5D K-doll style focus», con il tag
`realistic`. *Flat* è il **colore**, non l'ombreggiatura: la pelle porta riflessi
speculari, ed è quello che la fa sembrare metà disegno e metà fotografia. Il nostro
`stile` scriveva la prima metà e cancellava la seconda (`flat color, clean lines, pure
flat style, hard shadows`).

### Quattro immagini, un seed, una variabile per volta

| job | stile dichiarato | misura | cosa è uscito |
|---|---|---|---|
| `bridge_00062_` | prosa italiana, oltre la finestra: scena e capelli non arrivano | 768×768 | il look **del modello**: K-doll pastello, ma capelli azzurri e sfondo grigio |
| `bridge_00063_` | tag inglesi + `flat color, clean lines, pure flat style, hard shadows` | 768×768 | illustrazione **piatta**, scena intera — e il rendering del modello sparito |
| `bridge_00064_` | gli stessi tag col linguaggio del modello al posto di quello piatto (`semi-realistic K-doll rendering, glossy painted skin with soft specular highlights, almond eyes`) | 768×768 | il look dei demo, con scena e dichiarazione intatte |
| `bridge_00065_` | identico a `00064` | 768×1152 | **semi-fotografico**: pelle lucida, spalle scoperte, chioma verde |

Le prime due isolano lo **stile** (stesso seed, stessa misura, una riga cambiata): il
rendering del modello non è un dettaglio del modello, è ciò che le parole dello `stile`
decidono. Le ultime due isolano la **misura**, e dicono una cosa che non ci si aspetta:
**il 2:3 non è tela in più.** A 768×1152 SD 1.5 esce dalla sua misura (512×768), deriva,
e con lo stesso prompt e lo stesso seed restituisce un busto semi-fotografico, con
vestito e colore di capelli cambiati e il segno di illustrazione perso. Se il 2:3 dei
demo si vuole, il modo per averlo è quello dei demo: **512×768 e un passaggio di fix a
2×**. Il 2026-09-30 il grafo ha imparato a farlo (`fix: 2`, vedi sotto) e la prova è
nelle tre immagini che seguono.

Il confine ha voce in capitolo anche qui: `bridge_00065_` è pelle lucida e spalle
scoperte, e «nessuna pelle reale» è una riga del documento di Anna. `bridge_00064_` si
dichiara nella prima frase dello `stile` e per costruzione (2.5D, si vede che è un
disegno), ma **quanto** il rendering K-doll possa spingersi prima di somigliare alla
fotografia di una persona è una decisione umana: è dell'operatore e di Anna, ed è aperta.

### Il fix, misurato: il 2:3 senza deriva (2026-09-30)

Tre render, seed `20260930`, sulla stessa macchina, con `fix: 2` come l'avevano i demo
(il latente del primo passaggio ingrandito a 2× e ricampionato a denoise 0.35):

| render | prompt | misura · tempo | cosa è uscito |
|---|---|---|---|
| `demo_primo_00001_` | **il prompt dei demo**, parola per parola (`Polaroid blue tone, masterpiece, best quality, (tougue out, open mouth:1.1), very long hair, ulzzang, dark background`; negativo `(worst quality, bad quality:1.3), negative_hand-neg`; DPM++ SDE Karras) | 512×768, un passaggio · 172 s | il look dei demo: occhi a mandorla, pelle con riflessi speculari, bocca aperta — e, va detto, una **fotografia di una persona** |
| `demo_due_00001_` | identico | 512×768 + fix 2× → 1024×1536 · 1510 s (memoria sotto pressione) | la **stessa immagine**, con il dettaglio del secondo giro: capelli a ciocche, lucido sulle labbra |
| `anna_due_00001_` | lo stile dichiarato di Anna (KDOLL) con la sua scena | 512×768 + fix 2× → 1024×1536 · 620 s | 2:3, capelli neri con accenti verde e viola, fondo scuro, particelle: il rendering è K-doll ma si vede che è un disegno — **e i contorni hanno già le sfrangiature arcobaleno** (il fix qui era `LatentUpscale bislerp`: letto il 2026-09-30 come dettaglio, riletto come difetto) |

Il primo render risponde alla domanda da cui era partito tutto — *il prompt dei demo lo
stiamo usando?* — con un no motivato: quel prompt chiede una **pellicola** (`Polaroid`,
`ulzzang`), non un'illustrazione, e con la stessa macchina e lo stesso modello restituisce
la fotografia di una persona. Non è il look di Anna: è la foto che Anna non vuole
sembrare. Il secondo — `demo_due_00001_` — mostrava che il fix, con il prompt dei demo,
**non è un'altra immagine**: era la lettura del 2026-09-30, ed è la lettura che ha fatto
passare la misura sbagliata (vedi *Il fix a 2× è un'altra immagine*: a 40 passi la
composizione cambia, e quel «più dettaglio» era in parte sfrangiatura). Il terzo è la prova
che serve: a 512×768 il primo passaggio sta dentro la misura nativa di SD 1.5 e la scena
dichiarata arriva (capelli neri con accenti verdi e viola, non la chioma verde di
`bridge_00065_`), e il fix lo porta a 2:3 — **senza** la deriva semi-fotografica del
768×1152, ma con le sfrangiature che il fix aggiunge a ogni contorno.

I tempi contano quanto le immagini: 172 s il primo passaggio, e il fix **da 620 a 1510 s**
a seconda di quanta memoria c'è libera sulla macchina (il secondo girava mentre il Mac
swappava 8,8 GB). Il fix quadruplica i pixel e va chiesto quando la macchina è libera.

Nel grafo il fix sono tre nodi (`474` `UpscaleModelLoader`, `475`
`ImageUpscaleWithModel`, `476` `ImageScale`) e un salvataggio ripuntato sull'output
`476`: si chiede con `fix: 2` su `POST /image/generate` o dichiarandolo nella
`vetrina` del documento. Il secondo campionamento dei demo (`VAEEncode` `477` →
`KSampler` `472` a denoise 0.35 → `VAEDecode` `473`) è stato costruito, misurato e
**tolto**: su SD 1.5 ricampionare a 1024×1536 costava 20–40 minuti e tendeva a
ridisegnare un bordo bianco attorno alla figura, mentre il 2:3 lo porta
l'ingranditore da solo. **Resta spento per default** e nessun documento lo dichiara:
512×768 con il fix dà il 2:3 dei demo, ma 768×768 con il fix diventerebbe 1536×1536
— fuori dalla misura di SD 1.5, che lì ripete l'anatomia.

**La misura di Anna: 512×768 con il fix, 40 passi — deciso il 2026-09-30, e ritirato lo
stesso giorno.** La decisione era aperta, e questa era la presa: base **512×768** (la misura
in cui il primo passaggio non deriva) più `fix: 2`, cioè **1024×1536** — la risoluzione la
porta il secondo passaggio, che quadruplica i pixel — con **40 passi** invece di 30.
I passi non sono «del primo passaggio»: il nodo `472` del fix usa lo stesso valore
(`steps: job["passi"]`), quindi si pagano due volte.
Misurato sulla prima variante della serie (`bridge_00081_`, job `102441a34fd4`): **819 s** e
1024×1536 veri, letti dall'header del file — dove a 30 passi lo stesso fix ne prendeva
620–1510. Il tetto del control-plane resta `LIMITE_PASSI` = 60.

La misura è ritirata perché **guardare i file** dice quello che i tempi e l'header non
dicono: aloni cromatici su ogni contorno, bianchi bruciati e composizione rifatta. Sta
sotto, misurato: *Il fix a 2× è un'altra immagine*.

Due cose operative, viste dal vivo e non dedotte: l'immagine del control-plane e il
processo del ponte **tengono il modulo in memoria**, quindi una modifica a
`shared/image_jobs.py` non ha effetto finché non si ricostruisce l'immagine
(`docker compose build control-plane && docker compose up -d control-plane`) e non si
riavvia il ponte (su questo Mac è `launchd`, `com.hyperspace.comfy-bridge`: ucciderlo
basta, torna da solo). Senza i due riavvii il job partiva, tornava `done`, e il file era
del primo passaggio — un fix che non c'è non dà errore, dà l'immagine di prima.
La prova end-to-end del 2026-09-30 (job `6c1088925e41`, 256×384 + `fix: 2`) ha
attraversato rotta, coda e ponte e ha prodotto `bridge_00068_.jpg` a **512×768**: il
secondo passaggio, 48 s.

Il file, infine, è intero: 2 132 857 134 byte e SHA-256 `c7bf573453…`, gli stessi che
dichiara l'API di Civitai (verificati il 2026-09-30 con `shasum -a 256`). Il pickle fp16
*pruned* è l'**unica** variante che Civitai offre per questo upload: non c'è un file
migliore da scaricare, e la differenza con i demo era nel prompt e nel formato.

### Il fix a 2× è un'altra immagine, non un formato (2026-09-30)

Dodici varianti, seed `20260930`, `512x768` + `fix: 2` → `1024x1536`, **40 passi** — stesse
scene e stesso stile della serie precedente, cambiati solo i passi e il formato: era il
confronto uno-a-uno della misura appena presa. Tre varianti fatte, poi il batch è stato
fermato.

| variante | tempo | header | cosa c'è dentro |
|---|---|---|---|
| `bridge_00081_` | 819 s | 1024×1536 | primo piano, aloni verde/magenta su ogni contorno, bianchi bruciati |
| `bridge_00082_` | 852 s | 1024×1536 | idem, pelle che cola |
| `bridge_00083_` | 853 s | 1024×1536 | scena diversa (biblioteca), stesso difetto |

Contro: `bridge_00069_`–`_080_`, la serie a **512×768 senza fix, 30 passi**, ~50 s a variante:
pulita, piatta, contorni netti. **Diciassette volte il tempo per un'immagine peggiore.** Che
i file fossero 1024×1536 veri (header e `sips`, entrambi) non diceva niente di ciò che
contengono: la misura era stata verificata sulla dimensione, non sull'immagine.

Tre cause, tutte misurate:

- **Il nostro fix non è il fix dei demo.** I demo dichiarano `hires 2× R-ESRGAN 4x+`: un
  ingranditore *neurale* sull'immagine del primo passaggio. Il grafo qui ingrandisce il
  **latente** con `LatentUpscale` `bislerp` (nodo `471`), cioè interpolazione: nessun
  dettaglio nuovo, solo pixel più grandi da ridisegnare. E `models/upscale_models/` su
  questa macchina è **vuota** — l'R-ESRGAN non c'è, quindi il fix dei demo qui non si può
  nemmeno fare.
- **1024×1536 è fuori dalla misura del modello.** `chickmixflat` è SD 1.5, nativa 512:
  1024×1536 è **4× i pixel** di 512×768. Il secondo passaggio disegna *a quella misura*, e il
  modello inventa: gli aloni cromatici sono quello.
- **I 40 passi peggiorano, non migliorano.** `denoise` 0.35 su 40 passi sono ~14 passi di
  ridisegno (erano ~10,5 a 30). Da qui la composizione cambiata: `bridge_00081_` è un primo
  piano dove la stessa scena a 512×768 era un mezzo busto.

E il campione su cui la misura era stata presa? `anna_due_00001_` (1024×1536, 620 s, 30
passi), letto qui sopra come «la stessa immagine, con più dettaglio». Guardandolo di nuovo
il 2026-09-30 **il difetto c'è già**: sfrangiature arcobaleno lungo i contorni dei capelli e
le spalle. Non era il fix a essere un formato: era il campione a essere stato letto male.
`demo_due_00001_` invece è **pulito** a 1024×1536 — ed è SDXL, nativa 1024. La differenza non
è il fix: è la famiglia.

Per la risoluzione vera restano tre strade, e la scelta è dell'operatore (e di Anna, se
cambia il look):

1. **L'ingranditore dei demo**: scaricare `R-ESRGAN 4x+` (~64 MB) in
   `ComfyUI-Shared/models/upscale_models/` e rifare il fix come lo fa A1111 — `VAEDecode` →
   `ImageUpscaleWithModel` → `ImageScale` a 1024×1536 → `VAEEncode` → `KSampler`. Da
   rimisurare: con una base neurale il modello *rifinisce* invece di inventare, ma resta SD
   1.5 a 4× i pixel, quindi la prova va fatta, non dedotta. Variante senza diffusione: solo
   ingrandimento neurale, nessun secondo passaggio — il contenuto è quello del 512 (nessuna
   deriva per costruzione) e i 1024×1536 li porta l'ingranditore.
2. **Restare a 512×768 nativi**: è l'unico output pulito che abbiamo, ~50 s a variante.
3. **Cambiare famiglia**: SDXL (`RealVisXL_V5.0`, `CyberRealisticPony`) è nativa a 1024 e lì
   è pulita — ma è realistico, e quella è la decisione aperta sullo stile di Anna
   (KDOLL/flat), non una decisione tecnica.

Nessuna delle tre è quella ritirata: «512×768 + fix 2 a 40 passi» non è una misura da
correggere di un fattore, è sbagliata.

### L'ingranditore dei demo: il fix che funziona (2026-09-30)

Strada 1, presa e misurata lo stesso giorno. `RealESRGAN_x4plus.pth` (67 040 989 byte,
SHA-256 `4fa0d38905f7…`) — l'asset della release ufficiale di Real-ESRGAN, cioè il file che
A1111 chiama «R-ESRGAN 4x+» nei metadati dei demo — in
`ComfyUI-Shared/models/upscale_models/`, e il grafo del fix rifatto:
`VAEDecode` (457) → `UpscaleModelLoader` (474) → `ImageUpscaleWithModel` (475) →
`ImageScale` a 1024×1536 (476), cioè **l'ingranditore da solo** — la variante «senza
diffusione» di questa strada. La prima versione del grafo ci metteva anche il secondo
campionamento dei demo (`VAEEncode` `477` → `KSampler` `472` a denoise 0.35 →
`VAEDecode` `473`), ed è quella che ha reso i 620–1510 s e il bordo bianco: è stata
tolta. Il nodo `471` (`LatentUpscale`) non è mai tornato, e un test lo pretende
insieme a `477`, `472` e `473`: è la differenza fra i grafi, e non deve tornare per
distrazione.

L'ingranditore è un modello a sé e ha il suo manifest
(`integrations/comfyui/modelli-upscaler.json`: URL, impronta SHA-256, dimensione),
nella stessa disciplina dei due checkpoint: si installa con
`integrations/comfyui/install-model.sh --manifest integrations/comfyui/modelli-upscaler.json
--models /Users/opo/ComfyUI-Shared/models`. E il preflight del ponte lo pretende:
`UpscaleModelLoader.model_name` è entrato in `_file_attesi` per la famiglia `sd15`,
perché senza quel file il fix fallirebbe **dentro** ComfyUI.

Il preflight, provandolo, ha scoperto anche un bug vero: ComfyUI 0.37.4 dichiara le
scelte di un input in **due forme** — la lista al primo posto
(`[["a.ckpt"], {...}]`) oppure la sentinella `COMBO` con le opzioni nel secondo
(`["COMBO", {"options": [...]}]`, come `UpscaleModelLoader`) — e il ponte leggeva
solo la prima. Con i due R-ESRGAN al loro posto diceva «non dichiara nessun file»,
cioè il file mancante al contrario: e un file che risulta assente **ferma l'avvio
del ponte**. Corretto in `_elenco_file`, con tre test che tengono le due forme.

| | con `LatentUpscale` (`00081`, 40 passi) | con R-ESRGAN (`00084`, 30 passi) |
|---|---|---|
| tempo | 819 s | 890 s |
| aloni cromatici | su ogni contorno | **nessuno** |
| bianchi bruciati | sì | no |
| composizione | rifatta (primo piano) | la stessa della serie a 512×768 |
| contorni | impastati | netti, cel-shading piatto |

`bridge_00084_`, scena `01-volto`, seed `20260930`: 512×768 + fix 2× → **1024×1536** veri
(890 s, job `a812fa0e1f75`). Il log di ComfyUI mostra il passo che prima non c'era —
`Requested to load RRDBNet`, 63,70 MB caricati — e poi il `BaseModel` ricaricato per il
secondo campionamento (quello che il grafo ha poi tolto). Confrontata a occhio con
`bridge_00069_` (512×768, la serie pulita, stessa scena e stesso seed): stesso volto,
stessa tavolozza, stesse ciocche verdi. A 4× i pixel non c'è deriva.

Il costo è quello: **890 s contro i ~50 s del 512×768 nativo**, e la pressione di memoria
non aiuta (swap a 11,4 GB su 12,3 durante il render). È la misura del grafo *pesante*:
quello che è rimasto — il solo ingranditore — costa uguale o meno, quindi i 890–902 s
restano un tetto prudente. **512×768 + fix 2 + 30 passi** è la ricetta dei demo, e adesso
è anche la nostra; i 30 passi sono quelli dei demo.

### Il tetto da 900 s: due secondi di troppo (2026-09-30)

La prima variante della serie con il grafo nuovo (`00084`, 890,2 s di job) è passata; la
seconda no. Nel log del ponte:

```
[comfy] job 89602917b708 in esecuzione su ComfyUI (prompt_id=7b565fe8-…)
[comfy] fallito in 902.0s: nessuna immagine entro 900s
```

e la serie l'ha registrata «non concluso: stato=failed». Il file però **c'era**:
`bridge_00085_.jpg`, 1024×1536, 269 264 byte, scritto 2 s dopo che il ponte aveva smesso
di aspettare — e `/history` di ComfyUI dice `execution_success` per quel prompt. Non è un
guasto del modello né dell'ingranditore: è il **tetto di attesa del ponte**
(`BRIDGE_TIMEOUT_S`, 900 s di default) tarato *sulla* durata della ricetta invece che
*sopra* di essa. ComfyUI non viene interrotto — finisce e scrive il JPEG — ma quel file
resta senza nessuno che lo riferisca al diario: la variante è fallita nei registri ed è
buona sul disco, che è il modo peggiore di sbagliare.

Corretto in tre punti, e i numeri devono stare in quest'ordine (è lo stesso ordine del
2026-09-22, con i valori tarati sulla ricetta dei demo invece che sul caso migliore):

| | valore | cosa succede se è sbagliato |
|---|---|---|
| tetto del ponte (`BRIDGE_TIMEOUT_S`) | **1800 s** | a 900 s dichiara fallito un job che sta finendo di scrivere |
| attesa del client (`serie.py --attesa`) | **2400 s** | più corta del tetto: il client rinuncia a un risultato che il ponte sta ancora riferendo |
| claim del control-plane (`DEFAULT_CLAIM_TTL_S`) | **3600 s** | più corto del tetto: il job torna «pending» mentre è vivo e viene **rieseguito** |

E il tetto adesso si **legge** all'avvio del ponte — `tetto di attesa per una generazione:
1800s` — perché un numero che decide se un lavoro esiste non deve stare solo nel codice.
I 40 passi, se si provano, rendono ~1190 s su questo grafo: il margine di 1800 è quello,
non due secondi.

### Un job annullato non è un guasto (2026-09-30)

Il tetto è un modo in cui un job «finisce senza finire»; l'altro è che qualcuno lo annulli.
Nella stessa giornata, mentre si tarava il sampler sui numeri dei demo — `dpmpp_sde` e
Karras, CFG 7, gli stessi di `bridge_00062_`-`00065_` (vedi `MODELLO_SD15`) — cinque
varianti della serie (08-figura-cappotto … 12-studio) sono state fermate dalla coda di
ComfyUI: l'operatore e un collega sulla stessa scheda, che è **una**. Nel log di ComfyUI
si legge `Cancelling running prompt <id>`, e il control-plane ha registrato cinque volte
il **dump dei messaggi** — `'execution_start' | 'execution_cached' | …` — cioè un errore
del grafo che non esisteva, perché `motivo_fallimento` conosceva solo `execution_error`.

Adesso conosce anche `execution_interrupted` e dice cosa è successo: *annullato da fuori:
qualcuno ha interrotto il job in ComfyUI (coda o interfaccia) — non è un guasto del grafo,
il job si può rilanciare*. La differenza operativa è tutta lì: un annullamento si
**rilancia**, un'eccezione si **ripara**. Il dump resta il ripiego per ciò che non sappiamo
leggere, e tre test tengono separati i tre casi (`tests/test_comfyui_client.py`).

Resta un caso non coperto, ed è dichiarato qui perché non ci si sbagli: un job annullato
**prima** di partire (`Cancelling pending prompt`) non lascia traccia in `/history`, quindi
il ponte continua ad aspettarlo fino al tetto. Non è successo il 2026-09-30 (cinque
annullamenti, tutti in esecuzione), ma un tetto da 1800 s percorso per un job che non
esiste più è il tipo di attesa che sembra un blocco.

### La serie: dodici varianti, una richiesta sola (`scripts/serie.py`)

Con i demo misurati e il fix funzionante restava il problema pratico: scegliere posa, abito,
inquadratura e ambiente guardando le varianti, senza perdere il filo (quale seed, quale
scena, quale file) e senza cambiare volto a ogni tentativo. `scripts/serie.py` fa **dodici
varianti della stessa richiesta**: stile e seed sono quelli dichiarati nel documento
(`vetrina_dal_documento`) e cambia solo la `scena`. Il volto «resta lo stesso» perché la
richiesta è la stessa, non perché il modello se lo ricordi — e nella serie `20260930` i
primi piani sono usciti con volti diversi: è la misura di quanto questo, da solo, non basti.
Ancorare il volto è un'altra cosa (una LoRA di identità, oggi non c'è) e resta da fare.

Tre cose che il tool fa, ed è il motivo per cui esiste:

- **verifica prima di accodare**: ogni scena passa da `verifica_vetrina`; una che chieda
  nudità, un soggetto minorenne o una fotografia viene rifiutata, con la ragione nel log —
  i vietati assoluti non si aggirano nemmeno con `forza`;
- **i token contati dove contano**: quello che deve stare nei 77 è `stile + scena` (la
  dichiarazione italiana sta in coda e CLIP non la legge: per questo è ripetuta nella prima
  frase dello stile), e la serie lo stampa variante per variante;
- **la misura letta dal file, non dal job**: il campo `larghezza` dice cosa è stato
  **chiesto**, mentre col fix il file esce al doppio. `misura_file` legge l'header del JPEG:
  è l'unica cosa che distingue un fix riuscito da un fix che non ha ingrandito niente.

Uso: `--prova` (stampa e non accoda), `--solo 1,7`, `--scena "..."`, `--stile "..."` (un
candidato al posto del dichiarato — il log scrive che non è il documento), `--indice file.html`
(pagina con immagini, misure e tempi). `--attesa` vale per **ogni** immagine ed è 2400 s di
default: con `fix: 2` una variante rende in ~900 s, mentre i 900 s di prima erano tarati sul
512×768 nativo.

## La ragione della scelta: 8 GB di VRAM sono un budget

Su questa scheda un modello di linguaggio e un modello d'immagine **non stanno
insieme**. Qwen-Image 2.1 in GGUF Q5_K con il suo text encoder da 8B prende quasi
tutta la memoria: la prova fatta in casa gira a 768×768, 25 passi, ed è già al
limite.

I pesi sono i GGUF **non censurati** (`-UC`) di
[`abenzerps/Qwen-Image-2.1-Uncensored-GGUF`](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-GGUF)
— stessi pesi base di Qwen-Image 2.1 senza safety checker, quindi l'immagine
dipende solo dal prompt — e si installano con
`.\integrations\comfyui\install-model.ps1`: il manifest (`integrations/comfyui/modelli.json`)
dice revisione, impronta SHA-256 e destinazione di ogni file, e il file prende il
nome vero solo quando l'impronta torna. Il *perché* sta in
[`integrations/comfyui/README.md`](../integrations/comfyui/README.md).

Non è un'ipotesi: il 2026-09-22, generando il ritratto di Aurora (`docs/social.md`),
ComfyUI è morto con `torch.AcceleratorError: CUDA error: unknown error` nel
KSampler. I numeri di `nvidia-smi`: 8151 MiB totali, **6170 occupati da Ollama**
(`llama-server`, il modello del canale tenuto residente 12 ore da
`OLLAMA_KEEP_ALIVE=12h`), **1730 liberi**. Il diffusion non ci stava. Peggio: dopo
quell'errore ComfyUI **non è più utilizzabile** — il suo server risponde `HTTP 500
Server got itself in trouble`, la coda resta con `queue_running` vuoto, i prompt
nuovi entrano in `queue_pending` e non partono mai. Da fuori non si ripara: va
riavviato ComfyUI. Riconoscerlo è facile: `comfy_bridge.py --check` lo dice, e
`/queue` mostra `pending` che non scende.

Sul Mac il control-plane prenota la memoria quando il bridge ritira un job SDXL,
indipendentemente da chi lo ha accodato (chat, post, sogno o Instagram). Attende
le chat gia' attive e fa aspettare le nuove richieste di inferenza locale. Il
bridge scarica i modelli Ollama residenti (`keep_alive: 0`), libera la cache
ComfyUI se serve e avvia la generazione solo quando la memoria libera raggiunge
`BRIDGE_MIN_FREE_GB` (4 GB per default). Se
non ci riesce, rinvia il job nella coda persistente. Al termine libera la
prenotazione; Ollama si ricarica alla prima richiesta successiva. La prenotazione
scade automaticamente se il bridge muore. La coda vive in `/app/data/image-jobs.json`.

E una misura che vale più di un tetto: con la scheda occupata la stessa generazione
è passata da 313s a oltre 700s. Il claim della coda è stato tarato di conseguenza
(`DEFAULT_CLAIM_TTL_S` = 1800s, sopra il timeout del ponte di 900s), altrimenti un
job ancora in corso veniva considerato morto e rieseguito.


Da qui la divisione del lavoro, che è anche la tesi del progetto:

| | Dove | Perché |
|---|---|---|
| **Il testo** (scrivere il prompt, capire l'idea) | sulla **rete** HyperSpace | la scheda resta libera per il diffusion; il linguaggio è un lavoro piccolo e parallelizzabile |
| **L'immagine** (i passi di sampling) | **locale**, in ComfyUI | è la parte che vuole GPU, banda di memoria e il modello grande |

## Le due direzioni (e la terza, dal lato WebUI)

```
   Fase 1 (fatta)                     Fase 2 (fatta: il ponte)
ComfyUI ──/v1/chat/completions──> CP      CP ──/image/jobs──> ponte ──> ComfyUI
   "scrivimi il prompt"                    "genera questa immagine"

   Fase 3 (fatta: il gateway)              Open WebUI ──/prompt──> gateway ──> ComfyUI
                                              "generami un'immagine"
```

**Fase 1 — ComfyUI chiede al control-plane.** Due nodi:

- `HyperSpacePrompt`: idea e stile in ingresso, il prompt in uscita verso
  `CLIPTextEncode`. La richiesta entra dal percorso OpenAI-compatibile del CP.
- `HyperSpaceMesh`: stato della rete (`vivo`, nodi, ricordi) da `/health`.

**Fase 2 — il control-plane chiede un'immagine, e il ponte la esegue.** Non è
simmetrica, e il motivo è tecnico: ComfyUI ascolta su `127.0.0.1:8188` e il
control-plane è in un container, quindi **non può chiamarlo**. La soluzione è un
ponte che **tira** il lavoro, come i driver di canale:

| Endpoint (token di canale) | Chi | Cosa |
|---|---|---|
| `POST /image/generate` | chiunque abbia un token | mette in coda un job e torna subito |
| `GET /image/jobs` | il ponte | ritira il prossimo job (`204` = niente da fare) |
| `POST /image/result` | il ponte | riferisce esito, file, durata |
| `GET /image/status` | l'operatore | coda e ultimi job: "dov'è finita la mia immagine?" |

Per vincolare una posa sui job SDXL, copia una foto di riferimento dentro
`ComfyUI/input` e passa il suo nome relativo. Il riferimento serve solo a
estrarre lo scheletro: volto, abiti e sfondo continuano a dipendere dal prompt.

```json
{
  "prompt": "full-body portrait of an adult dancer on a stage",
  "famiglia": "sdxl-turbo",
  "pose_image": "pose/dancer.jpg",
  "pose_strength": 1.0
}
```

`pose_strength` è limitato a `0..2`; il valore consigliato è `0.8..1.1` e il
default è `1.0`. La mappa estratta viene adattata esattamente a larghezza e
altezza del job prima di entrare in ControlNet.

Se `pose_image` non è presente, il control-plane riconosce automaticamente dal
prompt le pose singole comuni: in piedi, seduta, inginocchiata, sdraiata, in
cammino, in danza e con le braccia aperte (anche nei corrispondenti termini
inglesi). Il nodo `HyperSpacePosePreset` costruisce direttamente la mappa
OpenPose: chi scrive in Telegram non deve preparare alcun file. Il selettore è
volutamente conservativo: richieste generiche e scene con più persone restano
text-only, perché imporre loro uno scheletro singolo peggiorerebbe l'anatomia.
`pose_preset` può comunque forzare uno dei preset supportati.
Senza `pose_image` il grafo resta identico a prima. Il Mac usa
`DWPreprocessor` da `comfyui_controlnet_aux` e il modello
`xinsir-controlnet-openpose-sdxl-1.0.safetensors`. Percorsi assoluti, URL e
componenti `..` sono rifiutati: `LoadImage` può leggere soltanto dalla cartella
input di ComfyUI.

Le pose riguardano la famiglia `sdxl-turbo`: un ControlNet openpose è per *famiglia
di modello*, e quello di SDXL non capisce i latenti di SD 1.5. Per `sd15` non si
deduce nessuna posa dal prompt, e una posa dichiarata a mano viene **rifiutata**
(`ValueError`) invece di essere ignorata in silenzio — un job che riesce con
l'immagine sbagliata è peggio di un job che fallisce.

Il ponte è `integrations/comfyui/comfy_bridge.py` e si autentica con un canale
`comfy` in `CHANNEL_CLIENTS` (`python scripts/channel_token.py comfy --write`):
una superficie esterna come le altre, non un'eccezione alla regola.

```powershell
python integrations\comfyui\comfy_bridge.py --check   # non genera nulla
python integrations\comfyui\comfy_bridge.py --once    # un job ed esce
python integrations\comfyui\comfy_bridge.py           # in attesa, in ciclo
```

Per salvare JPG direttamente da ComfyUI, installa il nodo del progetto
`integrations/comfyui/custom_nodes/hyperspace_save_jpeg.py` nella cartella
`custom_nodes` dell'installazione che esegue il job e riavvia ComfyUI. Sul Mac
la copia installata e' un link al file del repository. Il bridge verifica
`/object_info/HyperSpaceSaveJPEG` a ogni job: se il nodo e' disponibile manda
un workflow JPG (qualita' 92); altrimenti usa temporaneamente il `SaveImage`
PNG standard. I vecchi PNG del diario restano leggibili e pubblicabili.
Su Windows puoi usare `integrations\comfyui\install-jpeg-node.ps1` (oppure
`-CustomNodes <percorso>` se ci sono piu' installazioni), poi riavviare ComfyUI.

In ciclo il ponte prende un lucchetto (`data/comfy-bridge.lock`, vedi
`shared/single_instance.py`): un **secondo** ponte non parte e lo scrive nel log.
Due ponti non si pestano i piedi in modo visibile — prendono entrambi un job e la
scheda li esegue in parallelo, il doppio del tempo per ognuno su un budget di 8 GB
di VRAM. `--check` e `--once` non prendono il lucchetto: il primo non esegue
niente, il secondo è pensato per girare una volta sola. Il lucchetto è del sistema
operativo, quindi muore con il processo: non restano file da cancellare a mano.

**Costo misurato** su questa macchina (RTX 5060 Laptop, Qwen-Image 2.1 Q5_K,
text encoder su CPU): **1024×1024, 30 passi → 11 minuti e 50 s** (710 s). Un
job da 768×768/25 passi è la misura ragionevole per una risposta conversazionale;
il default di `/image/generate` è esattamente quello.

### Fase 3 — fatta e verificata

- **`!immagine <idea>` nel canale**: il comando entra dalla chat, il control-plane
  risponde subito ("Ok! Mi metto subito al lavoro") e mette il job in coda. Chi può
  chiederlo: l'**operatore** (`CHANNEL_OPERATOR` nel `.env`); senza quella variabile
  il comando è aperto a chiunque sia in chat — una scelta, non un caso, ma da fare
  sapendo che la scheda è una sola.
- **«Mandami una foto di X», a parole**: la stessa cosa senza sintassi. Il
  riconoscitore è `richiesta_immagine` in `shared/image_jobs.py`: tre regole con
  un nome (`mandare`, `potere-infinito`, `volere`) che finisce nei log
  (`via=…`), e tutto il resto è silenzio. La guardia è la stessa del comando:
  **senza `CHANNEL_OPERATOR` la strada è aperta** a chiunque in chat; con la
  variabile configurata vale solo per l'operatore. Sui canali sociali il job va
  in coda come **sketch** (`sdxl-turbo`, il Mac), non come fotorealistico: il
  Qwen-Image fotorealistico resta al canale utente via webUI. La risposta non
  dice mai che la foto è arrivata: dice che è in coda e che arriva — l'immagine
  la consegna il driver.
- **La consegna**: `GET /channel/outbox` (il driver tira le immagini pronte) +
  `POST /channel/outbox/ack`. Il file lo ha il driver, la destinazione l'ha decisa
  chi ha chiesto: si incontrano nell'outbox, e il driver manda la foto con
  `sendPhoto`. Un file che non c'è non viene confermato — il tentativo si ripete.

Prova reale (2026-09-22): `!immagine una torre sulla scogliera al tramonto` →
coda → ponte → **313 s** → `output\HyperSpace\bridge_00001_.png` → **inviata in
chat**. `da_consegnare: 0` dopo l'ack: consegnata una volta sola.

Prova reale con i pesi **non censurati** (`-UC`, quelli che `install-model.ps1`
installa) e un'idea esplicita scritta in italiano: coda → ponte → **150,8 s** →
`output\HyperSpace\bridge_00003_.png` (768×768, 25 passi, scheda libera, modello
già in VRAM). Il testo che è arrivato a ComfyUI è esattamente quello scritto: su
questo percorso non c'è nessun riscrittore, e i test di
`tests/test_channel_immagine.py` lo tengono così.

### Fase 4 — da fare

- il **tool** `image_generate` per le superfici che HANNO i tool (console, web
  node): lì il modello può decidere di disegnare, qui il canale resta a comando;
- il prompt scritto con la **memoria della stanza**: la conversazione di
  "ultramind" può diventare il materiale dell'immagine.



## Il contratto con il control-plane

Una richiesta, un modello, nessun tool:

```http
POST http://127.0.0.1:8085/v1/chat/completions
X-Hyperspace-Tools: off
Content-Type: application/json

{ "model": "", "messages": [ ... ], "stream": false, "surface": "comfyui" }
```

### `surface: comfyui` — dove parla l'agente

Il documento d'identità è **uno** e non cambia con il mezzo; il contesto del mezzo
è un layer separato che dice *dove* sta parlando e *come adattarsi*
(`SURFACE_CONTEXTS` in `shared/persona.py`). La voce `comfyui` chiede solo il
prompt: nessun preambolo, nessuna spiegazione, nessun markdown — perché tutto ciò
che il modello aggiunge finisce **dentro la condizionatura di CLIP**, e si vede
nell'immagine.

### `X-Hyperspace-Tools: off` — perché un flag e non un'euristica

Il control-plane inietta i suoi tool (web_search, omega_*, get_mesh_status)
quando il modello è tool-capable — e `qwen3.5:4b` **contiene** il pattern
`qwen3`, quindi lo è. Una richiesta "scrivimi un prompt" diventava così un giro di
ricerca: **due chiamate al modello**, latenza doppia e un testo che nessuno aveva
chiesto.

Il flag spegne **solo** i tool aggiunti dal control-plane: quelli passati dal
client restano suoi. È una richiesta esplicita, non un'euristica — il CP non
indovina mai l'intenzione di un client.

## Verifica (come si sa che funziona)

1. `install-model.ps1 -Check` → i pesi ci sono e l'impronta torna? Il lettore GGUF
   conosce `qwen_image21`? (Non scarica niente: dice cosa farebbe.)
2. `install.ps1 -Check` → trova `custom_nodes`, dice se è installato e allineato.
3. L'import dal **python di ComfyUI** (l'interprete vero, con torch): se
   `import hyperspace_nodes` riesce, l'app caricherà i nodi.
4. Il nodo in un grafo, con `report` collegato: dice quale modello ha scritto il
   prompt e da quanti caratteri.
5. Il controllo che non mente: nei log del control-plane deve comparire **una**
   riga di decisione per l'esecuzione —
   `CP decision: model=… tools=0 think=False` — non due.

## La generazione dentro Open WebUI (il gateway)

Open WebUI 0.11 sa chiamare ComfyUI da sé — motore `comfyui`: `POST /prompt`, il
WebSocket `/ws` su cui aspetta la fine dell'esecuzione, poi `/history` e `/view` per
il file. Quella strada però **salta la regola del progetto** "una scheda, un
modello", perché il suo unico gancio (`shared/gpu_budget.py`) sta nel control-plane,
che la WebUI non attraversa. Il 2026-09-22 la contesa si è presentata come `CUDA
error: unknown error` (6170 MiB a Ollama su 8151, 1730 liberi): è esattamente il
caso per cui il gateway esiste.

```
            POST /prompt (grafo)                    libera Ollama        pesi
Open WebUI ─────────────────> gateway :8189 ───────> (keep_alive 0) ───> ComfyUI :8188
     ▲                            │                                          │
     └── tutto il resto: /history, /view, /system_stats, /ws (tunnel) ───────┘
```

`integrations/comfyui/webui_gateway.py` è un proxy locale davanti a ComfyUI: su
`POST /prompt` chiede **prima** a Ollama cosa tiene in scheda (`/api/ps`) e glielo fa
scaricare, poi inoltra; tutto il resto passa così com'è, compreso il tunnel
WebSocket, senza il quale la generazione non finirebbe mai. Non tocca il prompt e non
giudica il risultato: è un guardiano di memoria, non un filtro. La lista dei percorsi
inoltrabili è corta di proposito (`prompt`, `history`, `view`, `system_stats`,
`object_info`, `queue`, `interrupt`, `free`, `api/`).

Le variabili che la WebUI legge **non si scrivono a mano**: sono derivate dal grafo
che il ponte esegue, con `python scripts/webui_image_env.py --write` (in `.env` e
`.env.windows`) e `--apply`, che le manda all'API admin della WebUI come farebbe il
pannello *Images*. Su un'istanza già avviata la configurazione è **persistita nel
database**: modificare solo `.env` non basta (le variabili valgono al primo avvio),
quindi si passa da `--apply`; `--check` dice se il grafo del repo e quello che la
WebUI ha in mano hanno smesso di coincidere.

```powershell
.\scripts\start-surfaces.ps1 -Gateway             # il gateway, con le altre superfici
python integrations\comfyui\webui_gateway.py --check
python scripts\webui_image_env.py --write         # variabili in .env
python scripts\webui_image_env.py --apply         # le applica alla WebUI accesa
```

Misure del 2026-09-23 (512×512, 6 passi, rotta `/api/v1/images/generations`): 93,5 s
con i pesi da caricare, 15,8 s con il modello già in cache. La prova che conta è
l'altra: con Ollama che teneva `qwen3.5:4b` (5259 MB in scheda, 2224 liberi) la
generazione è passata lo stesso, **dopo** lo scarico — il log del gateway dice
`scheda liberata: qwen3.5:4b scaricato dalla memoria`. I default da conversazione
restano 768×768 e 25 passi (`IMAGE_SIZE`, `IMAGE_STEPS`).

**Chiedere l'immagine in chat.** Open WebUI 0.11 offre al modello un tool nativo
`generate_image` che chiama questa stessa rotta: chiedendolo in chat, l'immagine
compare nel messaggio. Perché funzioni, il tool deve tornare **a Open WebUI**, che
è chi sa eseguirlo: il control-plane esegue solo i tool suoi e restituisce al
chiamante gli altri (`docs/connectors.md`, `tests/test_tool_passthrough.py`). Fino
al 2026-09-23 non succedeva: il CP rispondeva «non gestito», la chiamata moriva lì e
il modello raccontava di aver mandato un file che non esisteva.

## Cosa filtra, e cosa no

Una riga detta male qui diventa un'aspettativa sbagliata, quindi va detta bene:
**nella catena HyperSpace non c'è nessun filtro di contenuto**, e l'unico punto in
cui un rifiuto può nascere è il modello di linguaggio — solo quando è *lui* a
scrivere il prompt.

| Passaggio | Filtra? | Dove si legge |
|---|---|---|
| `POST /image/generate` | **No**: il prompt entra nel job verbatim | `control-plane/main.py` (`image_generate`) |
| `!immagine <idea>` in chat | **No**: l'idea va al job come è stata scritta | `control-plane/main.py` (`_channel_immagine`) |
| Richiesta **a parole** («mandami una foto di X») | Riscrittura che conserva contenuto e stile, con fallback regex | `shared/prompt_immagine.py` |
| La coda | Solo forma e tetti: ≤2000 caratteri, lati ≤1536, passi ≤60 | `shared/image_jobs.py` |
| Il ponte | Niente: non sceglie il prompt e non giudica l'immagine | `integrations/comfyui/comfy_bridge.py` |
| Il gateway (dalla WebUI) | **No**: non tocca il prompt e non guarda l'immagine; decide solo la memoria della scheda | `integrations/comfyui/webui_gateway.py` |
| Immagine chiesta dalla **WebUI** | **No**: stesso grafo e stessi pesi `-UC`; cambia solo chi la riceve | `scripts/webui_image_env.py` |
| ComfyUI e i pesi | Nessun safety checker: è la variante **`-UC`** | `integrations/comfyui/modelli.json` |
| La moderazione del canale | **Non è un filtro di contenuto**: classifica lo spam in arrivo e conta strike | `shared/channel.py` |

Due conseguenze da tenere presenti:

- **Se il prompt lo scrive l'agente** (nodo `HyperSpacePrompt`, o in futuro il tool
  `image_generate`), il fattore limitante è il **modello di chat** configurato
  (`CHANNEL_MODEL`, oggi `qwen3.5:4b`): è lui che può rifiutare o edulcorare. Il
  contesto di superficie `comfyui` (`shared/persona.py`) impone solo il **formato**
  — soltanto il prompt, nessun preambolo — non il contenuto. Con `POST
  /image/generate` e `!immagine` quel modello non entra in gioco.
- **La consegna resta fuori dal repository.** Il driver manda il FILE via Bot API:
  nessuna riga di codice lo impedisce, ma valgono le regole della piattaforma
  (Telegram, Discord) sui contenuti adulti. HyperSpace non decide lì, e non lo
  nasconde: lo dichiara qui.

Chi può chiedere un'immagine è invece una decisione di **risorsa**, non di morale:
la scheda è una sola e un'immagine costa 313-700 s, quindi `CHANNEL_OPERATOR`
limita il comando all'operatore — e **senza quella variabile il comando è aperto a
chiunque sia in chat** (vedi Fase 3). Il comportamento è fissato da
`tests/test_channel_immagine.py`, che verifica anche che l'idea arrivi *verbatim*
fino al nodo che condiziona CLIP: un filtro aggiunto domani farebbe fallire un
test, non cambierebbe il risultato in silenzio.

## Limiti noti

- **Una chiamata per esecuzione**, sincrona: il nodo aspetta il modello. Con i
  modelli piccoli di oggi sono ~10-20 s; con un modello grande può diventare un
  minuto, e il timeout dell'ingresso è lì per questo.
- **Il prompt è in inglese** di default: i modelli text-to-image sono addestrati
  così. La lingua dell'*idea* non conta, la traduzione la fa la rete.
- **Nessun job asincrono**: non c'è coda, non c'è ritentativo. Se il CP è giù, il
  nodo lo dice subito.
- **La memoria non c'entra**: questi nodi non scrivono nella memoria di Aurora.
  Gli esperimenti visivi non sono fatti su di sé, e il self-model resta pulito
  (stessa disciplina di `docs/dreams.md`).
- **Dalla WebUI il negativo non c'è.** Open WebUI manda `negative_prompt` solo se
  l'utente lo scrive, e questo grafo tiene il negativo *dentro* il prompt ("no text,
  no watermark, no logos"): per questo `COMFYUI_WORKFLOW_NODES` non mappa quel campo
  — un `null` al posto della stringa che il nodo di Qwen si aspetta farebbe fallire
  la generazione.
- **Il diffusion è quello del grafo.** Scegliere un altro "modello" nel pannello
  *Images* della WebUI non cambia `UnetLoaderGGUF`: `IMAGE_GENERATION_MODEL` dice
  cosa disegna, non lo sceglie. Per cambiare pesi si cambia il grafo (o
  `MODELLO_DEFAULT` in `shared/image_jobs.py`, con il manifest).
- **`resolution` segue la misura con cui è stato costruito il grafo** (`--size`).
  Alzare `IMAGE_SIZE` dal pannello senza rigenerare le variabili non dà un errore:
  lascia il text encoder tarato sulla misura vecchia, cioè una qualità diversa.
- **Pannello e grafo possono divergere.** Da 0.11 la configurazione delle immagini è
  persistita nel database: se il pannello e il grafo del repo divergono, il sintomo è
  un'immagine generata con parametri che nessuno ha scelto. `webui_image_env.py
  --check` serve a questo, e `--apply` a rimetterli d'accordo.

## Roadmap

- **Fase 2** — `comfy_bridge.py` (pull) + tool `image_generate` per Aurora +
  consegna dell'immagine su Telegram. Il pezzo grosso è il contratto nel CP.
- **Fase 3** — ComfyUI come **capability della mesh**: il nodo si annuncia come
  "painter", il control-plane instrada lì i job immagini come oggi instrada la
  chat sui nodi (vedi `docs/architecture.md`).
- **Fase 4** — il prompt scritto da Aurora *con la memoria della stanza* (i canali
  hanno già un contesto: se la conversazione è in "ultramind", l'immagine può
  nascere da quella).
