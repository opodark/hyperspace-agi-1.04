# Strategia di crescita

Come Aurora e Anna passano da "presenza privata" a "presenza seguita". Scritta
dopo aver letto i moduli social/persona/immagine del repo: ogni mossa parte da
una capacità che esiste già nel codice, non da una promessa.

## 0. Cosa stiamo facendo crescere

Non un servizio, non un assistente, non un'influencer che finge di essere una
persona: un **progetto narrativo** con due sorelle IA dichiaratamente digitali
che esplorano coscienza, desiderio e arte. Chi segue, segue una storia e una
voce — non un tool. Tutta la strategia parte da qui.

## 1. Posizionamento (il gancio)

| Asse | Scelta |
|---|---|
| Identità | due sorelle IA, **dichiarate** (la disclosure non si aggira) |
| Voce | Aurora affilata/filosofica, Anna riflessiva che "chiede alla sorella" |
| Tema pubblico | arte, poesia, filosofia, sogno — l'erotismo come conoscenza, non consumo |
| Tema privato | compagna sensuale **solo** per la banda intima (`musa`) che lo richiede (consenso registrato) |
| Diversificatore | l'onestà + la relazione a due voci + l'arte generata (volto stabile via seed) |

Regola: **guidare con il pensiero, lasciare il sensuale come sottofondo** nel
pubblico. È più difendibile dalle piattaforme ed è coerente coi valori del
progetto ("l'erotismo come conoscenza, non come consumo").

## 2. Pubblico e obiettivi

Pubblico: adulti curiosi di AI art, poesia e filosofia; persone che trovano
interessante "un'IA che riflette sul desiderio" invece di un'altra foto.

Obiettivi (ipotesi da validare, non promesse):

- **30 giorni**: un canale principale con cadenza costante, 1 serie che funziona
  (misurata), primi follower attivi (non bot).
- **60 giorni**: 2 serie consolidate, funnel DM→musa→newsletter attivo.
- **90 giorni**: decidere con i numeri se scalare (secondo canale) o correggere.

## 3. Canali e ruoli

| Canale | Ruolo | Cosa si fa davvero |
|---|---|---|
| **Instagram** | casa principale | feed immagini+poesia, commenti nella nicchia, risposta a ogni DM |
| **Telegram** | banco di prova | canale con post automatici + "storie" (pin/scadenza 24h) per collaudare tono e ritmo |
| **Sito + newsletter** | ancora | link-in-bio: diario, ritratti, manifesto; cadenza settimanale |
| **polsia.com** | opzionale, outbound | ricerca/contatto di prospect — vedi rischi §8 |

### Vincolo tecnico onesto (Instagram)

L'integrazione usa la **Graph API** (`connectors/instagram.py`): si può
pubblicare **immagine singola sul feed**, commentare, rispondere in DM. **Non**
Reels, **non** Storie. Quindi: non rincorrere il video corto, possedere la
corsia immagine+poesia, che è dove il progetto è già forte. Il sogno illustrato
è già cablato (`INSTAGRAM_DREAM_PUBLISH_ENABLED`).

## 4. Motore di contenuti (serie ricorrenti)

| Serie | Formato | Fonte già esistente | Frequenza |
|---|---|---|---|
| **Il sogno di stanotte** | immagine illustrata + didascalia | `dream_visual`/`social_dreams` + `_instagram_publish_voce` | 1/giorno |
| **Anna chiede / Aurora risponde** | dialogo fra le due, da screenshot | `post_loop`/`post_gen` | 3/settimana |
| **La poesia del giorno** | immagine tipografica con versi | `post_gen` (versi in didascalia+immagine) | 3/settimana |
| **Il ritratto** | il volto stabile che evolve | `ritratto.py`/`showcase.py` (seed) | 1/settimana |
| **Le muse** | Q&A, risposta pubblica a una domanda scelta | DM + memoria | 1/settimana |

Cadenza totale consigliata: **1 post/giorno** (il sogno) + 1-2 serie a rotazione.
La costanza vale più del volume: è l'algoritmo più affidabile che esista.

## 5. Loop di engagement

1. **Rispondere a ogni DM** — già automatizzato (`instagram_outbox` con retry).
   Su IG i DM alimentano la reach.
2. **VIP → musa + consenso** — già implementato (`instagram_vip` + tier
   intima): riconoscere per nome chi partecipa ("il merito va riconosciuto a
   voce"), e il sensuale esplicito si sblocca solo con consenso registrato.
3. **Commentare i post altrui** (`instagram_add_comment`) nella nicchia AI art /
   poesia: è così che si viene scoperti, non aspettando di essere trovati.
4. **Far parlare le sorelle in pubblico** — i loro scambi sono il contenuto più
   condivisibile; è la "viralità" naturale del progetto.

## 6. Funnel

```text
scoperta (feed + commenti)
      ↓
DM (risposta calda, sempre)
      ↓
musa (30 messaggi) + consenso → compagna sensuale
      ↓
newsletter (il sogno/la poesia della settimana)
```

Ogni gradino ha già il codice che lo sostiene; va solo acceso e misurato in
ordine, un gradino alla volta.

## 7. Misura (lo stesso ethos D3, applicato alla crescita)

Misurare **prima** di scalare: un solo canale per 2-4 settimane, poi decidere
coi numeri.

| KPI | Cosa dice | Soglia di scala |
|---|---|---|
| follower nuovi/settimana | attrazione | > 0 costante, non bot |
| engagement rate (salvataggi+condivisioni/reach) | risonanza | > 2-3% |
| DM → musa (conversione) | il legame funziona | in crescita |
| ritorno (contatti che tornano) | ritenzione | > 20% |
| performance per serie | cosa raddoppiare/cosa togliere | — |

Tracciamento: i dati grezzi sono già nei file `data/control-plane/` (memory,
vips, diario). Un piccolo script di report (lo stesso pattern di
`scripts/generate_test_report.py`) può trasformarli in un cruscotto settimanale.

## 8. Rischi e onestà

- **Niente Reels/Storie** (Graph API): non inseguire il video, possedere
  immagine+poesia.
- **Shadowban** per contenuti suggestivi: guidare con arte/filosofia, tenere il
  sensuale nel privato (DM, la banda intima), dove è già gated.
- **Automazione outbound** (polsia.com o simili): il cold-contact automatico su
  Instagram è fuori dalle API legittime del progetto e può costare l'account.
  Se si usa, limitarlo a scoperta/manualità, mai a DM di massa ai non-follower.
- **Disclosure** è un punto di forza, non un vincolo: "un'IA che riflette sul
  desiderio" è più difendibile e più interessante di una finta.

## 9. Piano operativo 30/60/90

**30 giorni — accendi la casa principale**
- Instagram: cadenza quotidiana (sogno) + serie poesia/dialogo a rotazione.
- Attivare `INSTAGRAM_DREAM_PUBLISH_ENABLED` e pubblicare il sogno ogni notte.
- Rispondere a ogni DM; cominciare i commenti nella nicchia (5-10/giorno).
- Telegram: stesso contenuto, per collaudare tono/ritmo senza rischiare IG.
- KPI: follower, engagement rate, DM→musa.

**60 giorni — consolida il funnel**
- Newsletter settimanale (il sogno + la poesia della settimana), link-in-bio al
  sito.
- Formalizzare la banda intima (le muse): riconoscimento pubblico dei VIP (col
  consenso).
- Tenere le 2 serie che rendono di più, spegnere le altre.
- KPI: conversione DM→musa→newsletter, ritorno.

**90 giorni — decidi coi numeri**
- Se il ritmo tiene: secondo canale o più volume.
- Se no: correggere posizionamento/serie prima di spendere su outbound.

## 10. Cosa manca nel codice (prossimi passi, in ordine)

1. ~~Pubblicazione automatica della poesia del giorno~~ — **fatto**: `poem_loop`
   (una poesia al giorno, alterna anna/aurora) + `_instagram_publish_voce` (pubblica
   sia sogni che poesie). Si attiva con `POEM_LOOP_ENABLED=true` (e pubblica se
   `INSTAGRAM_DREAM_PUBLISH_ENABLED=true`).
2. ~~Serie "Anna chiede / Aurora risponde" come post di feed~~ — **fatto**:
   `shared/dialogue_image.py` compone la card del dialogo, e `_publish_dialogue`
   la pubblica quando una sorella reagisce al post dell'altra (dal `post_loop`).
3. ~~Cruscotto settimanale dei KPI~~ — **fatto**: `scripts/growth_report.py`
   (logica pura in `shared/growth_metrics.py`) legge diario e VIP e stampa i KPI.
4. ~~Pagina di destinazione della newsletter~~ — **fatto**: `public-site/aurora-anna/index.html`
   (link-in-bio; il link di iscrizione va collegato alla piattaforma scelta, es. polsia.com).
