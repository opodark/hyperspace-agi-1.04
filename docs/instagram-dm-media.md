# Allegati immagine nei DM Instagram: servono host fuori da Cloudflare

Verificato il 2026-09-29 sera con l'account Instagram dell'operatore e il
control-plane di questa macchina.

## Sintesi

Meta scarica l'immagine di un DM (`attachment.type=image`, `payload.url`) solo se
l'URL è raggiungibile **senza passare dall'edge di Cloudflare**. Un URL servito
dal tunnel `cloudflared` di questo repo viene rifiutato con `error.code=100` /
`error_subcode=2018007` ("Caricamento non riuscito") **prima ancora che Meta
faccia la richiesta**: nei log dell'origine non compare nessun `GET`.

Non è quindi un problema di DNS, di certificato, di cache o di quick tunnel
contro tunnel nominato: è il prelievo di Meta che non parte.

## Prove raccolte

| URL allegato al DM | Esito Meta | Richiesta all'origine |
| --- | --- | --- |
| `https://media.zerozerocomputer.it/instagram/media/<token>/HyperSpace/bridge_00047_.jpg` | 400 / 2018007 | nessuna |
| stesso file con nome nuovo e senza query string | 400 / 2018007 | nessuna |
| `https://<quick>.trycloudflare.com/instagram/media/...` (stesso origin) | 400 / 2018007 | nessuna |
| `https://upload.wikimedia.org/.../JPEG_example_flower.jpg` | 400 / 2018047 | tentata (Wikimedia risponde 403) |
| `https://scontent.cdninstagram.com/...jpg` (CDN di Meta) | **200, consegnato** | — |

Controlli di contorno, tutti negativi come causa:

- **DNS**: `media.zerozerocomputer.it` risolve su tutti i resolver pubblici
  provati (1.1.1.1, 8.8.8.8, 9.9.9.9, OpenDNS, DNS.WATCH) e sugli autorevoli
  Cloudflare; nessuna zona DNSSEC.
- **TLS**: certificato Let's Encrypt valido, catena e SNI corretti.
- **Cloudflare**: il JPEG arriva con `HTTP/2 200`, `content-type: image/jpeg`
  ed `etag`, anche usando gli user-agent di Meta (`facebookexternalhit/1.1`,
  `meta-externalagent/1.1`).
- **Account/API**: l'invio di allegati funziona (l'URL del CDN di Meta viene
  consegnato), quindi finestra di 24 ore, token e permessi non c'entrano.

## Conseguenze pratiche

- Con `INSTAGRAM_PUBLIC_BASE_URL` puntato al tunnel Cloudflare i DM immagine non
  arrivano: `scripts/instagram_resend_dm.py` stampa
  `non consegnato: Instagram HTTP 400: Caricamento non riuscito`.
- Post e didascalie del feed non hanno questo vincolo: il prelievo usato per
  pubblicare ha scaricato lo stesso JPEG dal tunnel senza problemi.
- Per gli allegati serve un host non proxato da Cloudflare (hosting Aruba
  raggiunto direttamente, Funnel Tailscale, o qualunque CDN non Cloudflare) e
  quell'URL va usato solo per i DM, lasciando il tunnel per il resto.

## Come ripetere la diagnosi

1. Mettere un proxy di log davanti al control-plane e puntarci l'ingress `media`
   del tunnel, poi riavviare il tunnel. Attenzione: la porta `8099` è occupata
   da `hyperspace_bridge`, usare `8097`.
2. Inviare un DM di prova (`POST /<IG_ID>/messages` con
   `attachment.type=image` e `payload.url`) e guardare il log del proxy: se non
   compare nessuna riga, Meta non ha nemmeno provato a scaricare.
3. Ripetere con un'immagine dal CDN di Meta come controllo positivo: se quella
   viene consegnata, il problema è l'host e non l'account.
