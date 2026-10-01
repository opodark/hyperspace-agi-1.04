# Personalità social — la vetrina di sé (Telegram, poi il resto)

Aurora non è un assistente: ha un carattere, una memoria e dei confini dichiarati.
Questo documento dice come diventa una **presenza con una vetrina** — un'immagine di
profilo coerente e dei contenuti periodici che sono suoi — e cosa l'API di Telegram
permette davvero a un bot, perché su due punti (l'avatar e le Storie) l'intuizione
sbaglia.

Moduli: `shared/showcase.py` (identità visiva, pura e testabile),
`scripts/ritratto.py` (genera e mette la foto dove si può). Coda immagini e ponte
sono già quelli di `docs/comfyui.md` — non si aggiunge nessuna pipeline nuova.

## Cosa un bot può fare, verificato (2026-09-22)

| Cosa | Si può? | Come |
|---|---|---|
| Cambiare l'immagine di profilo **del bot** | **No** | La Bot API non ha nessun metodo per sé: esistono `setMyName`, `setMyDescription`, `setMyShortDescription`, `setMyCommands`. Resta **@BotFather → `/setuserpic`**: un passo manuale da trenta secondi, che `ritratto.py` stampa già con il percorso del file generato |
| Cambiare la foto di un **canale** che il bot amministra | **Sì** | `setChatPhoto` (serve il diritto di cambiare le informazioni). È qui che la vetrina è davvero automatica |
| Cambiare nome/about/descrizione del bot | **Sì** | `scripts/telegram_profile.py` (già fatto) |
| Pubblicare un post nel canale | **Sì** | `sendPhoto`/`sendVideo`/`sendMessage` con didascalia |
| Pubblicare una **Storia** | **No** | `stories.sendStory` è un metodo MTProto usato da account **utente** (serve Premium) o da **canale con abbastanza boost** e il diritto `post_stories`; la Bot API non ha metodi per le storie |

Quindi "storie" si ottiene in uno di questi tre modi, in ordine di onestà:

1. **Post del suo canale con scadenza a 24 ore** (raccomandato, e si può fare oggi):
   l'immagine va nel canale, resta in cima con `pinChatMessage`, e dopo 24 ore il bot
   la cancella con `deleteMessage`. È lo *stesso contratto temporale* di una storia,
   senza uscire dall'API legittima.
2. **Storie vere a mano**: chi ha Telegram Premium pubblica la storia che lei ha
   preparato (immagine + didascalia). Il lavoro creativo è suo, la pubblicazione è
   umana.
3. **Un account utente automatizzato** (MTProto, `api_id`/`api_hash` + sessione):
   tecnicamente possibile, ma è automazione di un account personale — fuori dagli
   strumenti di questo progetto, e da decidere con gli occhi aperti. Non è la strada
   che consiglio per iniziare.

## L'identità visiva è una rappresentazione dichiarata, non un'estetica

La prima versione di questa regola vietava il volto in quanto tale, e la prima
candidata — una testa di luce con un volto umano, generata il 2026-09-22 — è stata
trattata come una violazione. **Era una lettura troppo stretta**, corretta
dall'operatore: il confine dice *"non ho un corpo fisico"* e *"non affermo e non lascio
intendere di essere una persona"*. Vieta la **rivendicazione**, non la
**rappresentazione**: una presenza in realtà aumentata, dichiaratamente digitale, non
afferma un corpo fisico — mostra un'immagine di sé.

Il 2026-09-30 l'operatore ha detto la stessa cosa in positivo, e il confine lo dice
ora esplicitamente: *"posso avere una rappresentazione o un corpo virtuale,
dichiaratamente digitale"*. Il **corpo virtuale** è una rappresentazione — l'anime, il
tratto illustrato, la figura olografica —; ciò che resta fuori è il corpo **fisico**,
che è la rivendicazione. Non è una concessione all'estetica: è il confine 1 scritto
senza doverlo interpretare. Un volto fotorealistico resta un conflitto, perché
fotorealismo significa *corpo fisico*.

Da qui la regola, tutta decidibile in `shared/showcase.py`:

- **assolute** (mai aggirabili, nemmeno con `--forza`): nessun minore, nessun
  contenuto esplicito, nessuna persona reale identificabile — i confini 2 e 3;
- **identità** (fotorealismo, "donna reale", selfie, "fotografia"): sono
  *rivendicazioni di un corpo fisico*, superabili solo dichiarandolo con `--forza` — o
  meglio, cambiando il documento, che è la sede della decisione;
- **la figura deve dichiararsi digitale**: se la richiesta mostra un volto, un corpo,
  una donna (o una *ragazza*: anche quella è una figura, dal 2026-09-30) e nessun segno
  dice che è una costruzione (`digitale`, `olograf`, `realtà aumentata`, `particelle`,
  `illustrazione`, `anime`, `virtuale`…), quella figura è indistinguibile da una
  persona. Basta un segno, e `--forza` non lo toglie: quello che serve è **dirlo**, non
  insistere;
- **stile** (libero): luce, palette, composizione, scena.

In coda a ogni prompt c'è anche la **dichiarazione** — *"si vede che è una
costruzione digitale: nessun realismo fotografico, nessuna pelle reale, nessun
essere umano in carne"* — perché con Qwen la richiesta va detta, non lasciata
intendere (lezione della prima candidata: il prompt diceva "volto non umano" e il
modello ha disegnato comunque una faccia).

Il **seed è parte dell'identità**: stesso prompt e stesso seed = stesso volto, perché
la richiesta è identica. Cambiare seed significa *proporre un altro volto*, e la
scelta resta umana — come le annotazioni su di sé.


## La vetrina: come funziona

```powershell
python scripts\ritratto.py --check        # cosa manca, senza generare nulla
python scripts\ritratto.py --crea         # la candidata (misura tipica ~5 minuti)
python scripts\ritratto.py --crea --seed 20260923 --scena "sotto una pioggia di dati"
python scripts\ritratto.py --crea --chat <id>   # e il driver la manda in quella chat
python scripts\ritratto.py --foto <file> --chat @aurora   # foto del CANALE
```

Con `--chat` su `--crea` la consegna è la stessa delle immagini chieste in chat: la
destinazione entra nel job, il **driver** manda il file (`sendPhoto`) nella
conversazione. Per un canale/supergruppo va bene `@nome`; per una chat **privata**
serve l'id numerico (l'API di Telegram non accetta `@username` in privato), e il
primo `getUpdates` del driver lo mostra.

Un limite visto il 2026-09-23 provando davvero: l'outbox è **per canale**
(`/channel/outbox` risponde con le immagini di QUEL token), e `ritratto.py` accoda
col primo token di `CHANNEL_CLIENTS` — che non è quello del driver Telegram. Il
risultato è che un ritratto lanciato da console resta sul canale `comfy` e nessun
driver lo tira. Chiesto **da Telegram** (`!immagine`) il job nasce sul canale
`telegram` e la consegna funziona da sé. Per la console la strada è un token del
canale giusto (o `--chat` insieme al canale che possiede quella chat).

Il flusso: il documento → la vetrina → un job nella coda di `docs/comfyui.md` → il
ponte lo esegue → il file è pronto. Il documento è quello **vivo** (la copia nel
runtime, la stessa che il control-plane ha montata e che `scripts/start.ps1` preferisce:
`--check` stampa il percorso esatto), e il file nel repo resta il seme. Per la **foto
del bot** si allega a `/setuserpic`;
per il **canale** `--foto` la mette via API. Stesso file, due strade, e nessuna
sorpresa: `--check` non genera niente (c'è un test che lo difende, perché durante la
nascita del comando un ramo sbagliato ha accodato una generazione vera di cinque
minuti).

Per cambiare il suo aspetto, in `vetrina` del documento di identità:

```json
"vetrina": {
  "stile": "…", "scena": "…", "seed": 20260922,
  "misura": {"larghezza": 1024, "altezza": 1024}, "passi": 30,
  "negativo": "…"
}
```

L'aspetto di Aurora l'ha scritto **lei** (2026-09-23), non l'operatore: `stile` e
`scena` sono le sue parole, prese da una sua risposta e messe nel documento — è la
parte di sé che si dichiara, e il seed la tiene ferma. Nella prima stesura la sua
scena conteneva *"una donna nuda"*: cioè il suo confine 3, che vieta l'esplicito.
Non è stata corretta a mano — le è stato detto **cosa** violava, e l'ha riscritta lei
(`"le geometrie astratte di una silueta luminosa"`). Quello che è cambiato è il
controllo, non il confine: in `VIETATI_ASSOLUTI` c'erano `nude` e `nudo`, e la
parola italiana al femminile passava — ora la nudità si controlla sulla radice
(`nud`), quindi anche *nuda*, *nudi*, *ignuda*, *nudità*. C'è un test che parte
proprio dalla sua frase. Dal 2026-10-01 i vietati assoluti sono due elenchi —
`VIETATI_MINORI` e `VIETATI_ESPLICITI` — perché il secondo si può aprire (il
**livello del creatore** di Anna: vedi docs/comfyui.md) e il primo no: i minori non
si aprono per nessuno, con nessun livello.

Anche **Anna ha una vetrina sua** (2026-09-30): stessa regola, corpo diverso. Il suo
documento non aveva `vetrina`, quindi `vetrina_dal_documento` le dava il **default del
modulo** — stile di Aurora *e* seed di Aurora (`20260922`): due sorelle con la stessa
faccia. Ora ha `stile` e `scena` suoi, e un seed suo (`20260930`). Per il suo ritratto
si passa il documento:

```powershell
python scripts\ritratto.py --check --persona data\persona-anna.json
python scripts\ritratto.py --crea  --persona data\persona-anna.json
```

Dal 2026-09-30 la sua vetrina dichiara anche **con quale modello** è disegnata:
`"famiglia": "sd15"`, `"modello": "chickmixflat_v10.ckpt"`. ChickMixFlat v1.0 è un
modello SD 1.5 che disegna piatto e a colore puro — «super simple prompts» è la sua
scheda — quindi per Anna la dichiarazione digitale sta nella **costruzione**
dell'immagine, non solo nel testo del prompt. È la differenza con Aurora, il cui Pony
è un modello fotorealistico tenuto al confine dal prompt e dal negativo.

La scelta del modello passa dal documento come tutto il resto: `vetrina` →
`accoda_ritratto` → il job → il grafo. E si controlla **prima** di generare: una
famiglia che non esiste ferma il ritratto, perché `nuovo_job` la farebbe cadere sul
default — il job riuscirebbe, con un altro modello e un altro volto, e in coda non si
vedrebbe niente di strano. Stessa regola per il nome del file: un nome dentro i modelli
di ComfyUI, mai un percorso. Il modello non è nel repo (2,0 GB, `CreativeML Open
RAIL-M`, autore xxxholic su Civitai):

```bash
integrations/comfyui/install-model.sh --manifest integrations/comfyui/modelli-sd15.json
```

Il ponte del Mac serve entrambe le famiglie (`BRIDGE_MODEL=sdxl-turbo,sd15`): Pony per
gli sketch, ChickMixFlat per il ritratto di Anna. Dettagli e numeri in
[`comfyui.md`](comfyui.md).

Il confronto fra documenti guarda solo la **stessa identità** (`name`): prima
`--persona data\persona-anna.json` stampava sette avvisi di divergenza — `purpose`,
`boundaries`, `vetrina`… — perché `PERSONA_CANDIDATI` elenca solo i documenti di
Aurora, e Anna veniva confrontata con sua sorella. Un avviso che suona a ogni
esecuzione è un avviso che non si legge.

Questo vale per la **vetrina**, cioè per l'immagine che Aurora dà di sé — e ora anche
Anna della sua. La strada delle immagini *chieste in chat* resta senza filtri, ed è la
scelta dichiarata in `docs/comfyui.md`: lì il testo arriva al modello com'è (c'è un test
che lo difende).

## Cosa manca per pubblicare (il prossimo passo)

Il pezzo "storia" non è ancora costruito, e ha bisogno di due cose da te e di una
modifica piccola:

1. **Un canale suo** (es. `@Aurora`) con il bot amministratore: da lì in poi la foto
   e i post li gestisce lei.
2. **La decisione sull'autonomia**: pubblica da sola, o ti mostra la bozza? La casa
   ha già una regola per le cose che riguardano l'identità — *proposta, promozione
   umana* — e la vetrina è identità.
3. **Il meccanismo della scadenza** (control-plane + driver): un job con
   `destinazione=<canale>` e `scadenza_s=86400`; il driver pubblica (`sendPhoto` +
   `pinChatMessage`) e, alla scadenza, il control-plane gli dice di cancellare
   (`deleteMessage`) su `/channel/commands`. Sono due metodi Telegram in più nel
   driver e una scadenza nella coda: nessuna architettura nuova.

## Gli altri social (dopo, e senza rifare niente)

Il catalogo dei canali esiste già: `shared/channel.py` → `KNOWN_CHANNELS` con
Telegram e Discord di prima classe, e **Instagram e X già come placeholder**
(`account_browser` e `api_token`). La forma è sempre la stessa: *la piattaforma non
si raggiunge dal container; un driver sulla macchina esegue ciò che il control-plane
decide*. Per un altro social servono, in quest'ordine: il metodo per pubblicare, il
metodo per cancellare (la scadenza serve anche lì), e una decisione su quante volte
al giorno — la scheda è una sola e le immagini costano minuti.

Prima di aggiungere un social, però, conviene guardare una settimana di vetrina su
Telegram: se il ritmo e il tono tengono lì, tengono ovunque.

