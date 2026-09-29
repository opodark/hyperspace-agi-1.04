# Allegati immagine nei DM Instagram: quali host Meta accetta

Verificato il 2026-09-29 notte con l'account Instagram dell'operatore e il
control-plane di questa macchina.

## Sintesi

Per un allegato immagine in DM (`attachment.type=image`, `payload.url`) Meta
**scarica l'immagine solo da host convenzionali**. Gli URL serviti dai tunnel —
il `cloudflared` di questo repo, un quick tunnel `trycloudflare`, un Funnel
Tailscale — vengono rifiutati con `error.code=100` / `error_subcode=2018007`
("Caricamento non riuscito") **prima di qualsiasi richiesta**: nei log
dell'origine non arriva nessun `GET` e la risposta torna in meno di 1,5 secondi.

Non è quindi un problema di DNS, certificato, cache, home page, percorso o CDN:
è il prelievo di Meta che non parte. La stessa `2018007` non dipende da
Cloudflare: host dietro Cloudflare come `cdn.discordapp.com` e `imgs.xkcd.com`
vengono serviti senza problemi.

## Prove raccolte

Controlli positivi (immagine arrivata in DM):

| URL allegato | host | esito Meta |
| --- | --- | --- |
| `https://scontent.cdninstagram.com/...jpg` | CDN di Meta | consegnata |
| `https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=800&q=80` | Unsplash (Fastly) | consegnata |
| `https://cdn.discordapp.com/embed/avatars/0.png` | Discord (Cloudflare) | consegnata |
| `https://imgs.xkcd.com/comics/standards.png` | xkcd (Cloudflare) | consegnata |
| `https://files.supersite.aruba.it/media/...png` | Aruba | consegnata |
| `https://files.catbox.moe/9gba27.png` | catbox.moe | consegnata |
| `https://upload.wikimedia.org/.../JPEG_example_flower.jpg` | Wikimedia | richiesta tentata (Wikimedia risponde 403) |

Rifiuti, tutti con `2018007` e **nessuna richiesta all'origine**:

| URL allegato | host | esito Meta |
| --- | --- | --- |
| `https://media.zerozerocomputer.it/instagram/media/<token>/HyperSpace/bridge_00047_.jpg` | tunnel cloudflared | rifiutata |
| stesso host, nome file nuovo e nessuna query string | tunnel cloudflared | rifiutata |
| stesso host, `/health` (percorso corto, senza estensione) | tunnel cloudflared | rifiutata |
| stesso host, pagina vera su `/` (200) | tunnel cloudflared | rifiutata |
| `https://<quick>.trycloudflare.com/instagram/media/...` | quick tunnel | rifiutata |
| `https://macbook-air-di-alberto.tail453db3.ts.net:10000/...` | Funnel Tailscale | rifiutata |

## Controlli di contorno (tutti negativi come causa)

- **DNS**: `media.zerozerocomputer.it` risolve su tutti i resolver pubblici
  provati (1.1.1.1, 8.8.8.8, 9.9.9.9, OpenDNS, DNS.WATCH) e sugli autorevoli
  Cloudflare; nessuna zona DNSSEC.
- **TLS**: certificato Let's Encrypt valido, catena e SNI corretti, HTTP/2 e 200.
- **Raggiungibilità dall'esterno**: un fetcher indipendente (`r.jina.ai`) ha
  scaricato `/health` **da entrambi** i nostri host (tunnel Cloudflare e Funnel
  Tailscale). Gli URL sono pubblici: è Meta che non li chiede.
- **Cache/edge**: le prove sono state ripetute con un proxy di log davanti al
  control-plane, quindi l'origine avrebbe visto ogni prelievo; non ne è arrivato
  nessuno, nemmeno su URL mai visti prima (nessun effetto di cache).
- **Forma dell'URL**: irrilevante (rifiutato sia il percorso completo con token
  sia `/health`, con e senza query string, con e senza estensione).
- **Account/API**: l'invio di allegati funziona, la finestra di 24 ore e i
  permessi sono a posto (le immagini degli host accettati arrivano).

## Conseguenze pratiche

- Con `INSTAGRAM_PUBLIC_BASE_URL` puntato al tunnel i DM immagine non partono:
  l'allegato deve stare su un host accettato. `scripts/instagram_resend_dm.py`
  stampa `non consegnato: Instagram HTTP 400: Caricamento non riuscito`.
- Pontile di emergenza già usato: `scripts/instagram_send_drawings_via_catbox.py`
  carica il JPEG su `files.catbox.moe` e manda il DM con quell'URL, poi conferma
  la coda al control-plane. Il link è pubblico (non indicizzato): usarlo solo
  come ponte finché non si serve da un host proprio accettato (l'hosting Aruba
  funziona, vedi `scripts/instagram_upload_aruba.py`).
- Post e didascalie del feed non hanno questo vincolo: il prelievo usato per
  pubblicare ha scaricato lo stesso JPEG dal tunnel senza problemi, quindi il
  tunnel resta la via buona per il feed e per il diario.
- Su questa macchina la zona `zerozerocomputer.it` è interamente in proxy
  Cloudflare: `ftp.zerozerocomputer.it` risolve su IP Cloudflare e la porta 21
  non passa dal proxy, quindi per caricare via FTP serve mettere quella voce
  (e il sottodominio che serve i file) in **DNS only**.
- La radice `media` è servita dal tunnel (`cloudflared tunnel route dns` ha
  sostituito il record precedente): se serve di nuovo Aruba su quel nome, va
  ripristinato in dashboard.

## Come ripetere la diagnosi

1. Mettere un proxy di log davanti al control-plane (ascolto su `127.0.0.1:8097`
   → `127.0.0.1:8085`) e puntarci l'ingress `media` del tunnel, poi riavviare
   `cloudflared`. Attenzione: la porta `8099` è occupata da `hyperspace_bridge`.
2. Inviare un DM di prova (`POST /<IG_ID>/messages` con `attachment.type=image`)
   e guardare il log del proxy: se non compare nessuna riga, Meta non ha nemmeno
   provato a scaricare e non c'è configurazione da sistemare, va cambiato host.
3. Tenere sempre un controllo positivo nella stessa sessione (URL dal CDN di Meta
   o da Unsplash): se quello arriva, il problema è l'host dell'allegato.
