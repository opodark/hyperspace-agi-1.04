#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""La vita simulata di Anna e Aurora: un giorno — o trenta — in pochi minuti.

Perché esiste: i loop che fanno vivere le due (post, sogno, poesia) girano nel
control-plane, che è la macchina di **produzione** — lì ci sono Instagram e il
canale vero, e `INSTAGRAM_DREAM_PUBLISH_ENABLED=true` vuol dire che il sogno
illustrato di stanotte finisce davvero sul feed. Qui si fa la stessa giornata su
file isolati e su un pubblico finto (`shared/vita_sandbox.py`), col modello locale
che scrive **davvero** i testi: è l'unico posto dove si può provare "cosa
scriverebbero per trenta giorni" senza spendere un account.

Cosa NON fa, ed è la parte che conta:
  - non legge e non scrive i file del control-plane (tutto dentro `--dir`);
  - non accoda niente a ComfyUI: i disegni sono un passo separato (`serie.py`);
  - non ha un percorso che pubblichi su Instagram: non esiste proprio;
  - verso Telegram scrive solo con `--invia`, e con un bot **suo**
    (`TELEGRAM_SANDBOX_TOKEN`), non quello che serve la produzione.

Il tempo è compresso di default: un giorno di vita per qualche secondo, trenta
giorni in un caffè. Con `--giorno-secondi` si rallenta (es. 86400 = vita reale).

Uso:
    python scripts/vita_sandbox.py --prova --giorni 7
    python scripts/vita_sandbox.py --giorni 7 --dir data/sandbox-vita --seed 20261001
    python scripts/vita_sandbox.py --giorni 3 --invia --chat @anna_aurora_sandbox
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.conversation_log import ConversationLog  # noqa: E402
from shared.diario import Diario, voce  # noqa: E402
from shared.dream_visual import build_dream_prompt, filtra_dream, parse_dream  # noqa: E402
from shared.feed import Feed, nuovo_post  # noqa: E402
from shared.persona import PersonaStore  # noqa: E402
from shared.post_gen import (MOTIVO_ECO, build_poem_prompt, build_post_prompt,  # noqa: E402
                             filtra_post, parse_post, prossima_mossa)
from shared.social_dreams import social_dream_inspirations  # noqa: E402
from shared.vita_sandbox import ingaggio, kpi, materiale_da_sognare, pubblico  # noqa: E402

OLLAMA_DEFAULT = "http://127.0.0.1:11434"
MODELLO_DEFAULT = os.getenv("VITA_SANDBOX_MODEL", "").strip() or os.getenv(
    "OLLAMA_MODEL", "").strip() or "qwen3-8b-abliterated"
DOCUMENTI = {"anna": "data/persona-anna.json", "aurora": "data/persona-aurora.json"}
AUTORI = ("anna", "aurora")


def blocco_identita(autore: str) -> str:
    """Il blocco di sistema della persona: identità + dichiarazione.

    Si legge il documento VERO (`data/persona-*.json`): la voce del sandbox deve
    essere la loro, altrimenti non si sta provando la loro vita — si sta provando
    un altro personaggio con gli stessi nomi.
    """
    percorso = ROOT / DOCUMENTI.get(autore, "")
    try:
        return PersonaStore.load(str(percorso)).system_block()
    except Exception as errore:                      # pragma: no cover - diagnostica
        print(f"[vita] identità {autore} non caricata: {errore}", file=sys.stderr)
        return ""


def chiedi_al_modello(prompt: str, *, modello: str, url: str = OLLAMA_DEFAULT,
                      max_tokens: int = 220, timeout: float = 300.0) -> str:
    """UNA chat col modello locale, via API nativa (`think: false`).

    Stesso patto del control-plane: il testo utile non deve finire nel
    `reasoning` dei modelli che ragionano, quindi `think` si spegne qui.
    """
    import requests

    risposta = requests.post(
        f"{url.rstrip('/')}/api/chat",
        json={"model": modello, "stream": False, "think": False,
              "messages": [{"role": "user", "content": prompt}],
              "options": {"num_ctx": int(os.getenv("VITA_SANDBOX_NUM_CTX", "8192")),
                          "num_predict": int(max_tokens)}},
        timeout=timeout)
    risposta.raise_for_status()
    return str((risposta.json().get("message") or {}).get("content") or "")


class Regista:
    """La giornata di vita su file isolati: chi scrive, chi posta, chi sogna.

    Tiene in mano i tre store veri (`ConversationLog`, `Feed`, `Diario`) ma su file
    suoi: sono gli stessi oggetti del control-plane, puntati altrove. È la stessa
    scelta del Code Sandbox — le stesse macchine, un altro volume.
    """

    def __init__(self, *, cartella, finti, modello, start=None, rng=None,
                 modello_url: str = OLLAMA_DEFAULT, giornalista=print):
        self.cartella = Path(cartella)
        self.pubblico = finti
        self.modello = modello
        self.modello_url = modello_url
        self.rng = rng if rng is not None else random.Random(0)
        self.start = start or datetime.now(timezone.utc)
        self.journal = giornalista
        self.identita = {autore: blocco_identita(autore) for autore in AUTORI}
        self.conversazioni = ConversationLog()
        self.feed = Feed()
        self.diario = Diario()
        self.posti: list[dict] = []          # i post del periodo, per l'ingaggio

    # ── servizi ──────────────────────────────────────────────────────────────
    def quando(self, giorno: int, ora: int = 10, minuto: int = 0) -> str:
        """L'istante simulato: il giorno N alle ore `ora`, in ISO UTC."""
        istante = (self.start + timedelta(days=max(0, int(giorno)))
                   ).replace(hour=int(ora) % 24, minute=int(minuto) % 60,
                             second=0, microsecond=0)
        return istante.astimezone(timezone.utc).isoformat(timespec="seconds")

    def _genera(self, prompt: str, *, tetto: int = 220) -> str:
        if not self.modello:                 # --senza-modello: solo l'impianto
            return ("DIDASCALIA: la notte mi ha lasciato un sogno che non so scrivere\n"
                    "IMMAGINE: una stanza di luce")
        try:
            return chiedi_al_modello(prompt, modello=self.modello,
                                     url=self.modello_url, max_tokens=tetto)
        except Exception as errore:
            self.journal(f"  [modello] non ha risposto: {type(errore).__name__}: {errore}")
            return ""

    def memorie(self) -> list[str]:
        """Gli echi anonimi della stanza simulata: lo stesso motore della produzione."""
        return social_dream_inspirations(
            materiale_da_sognare(self.conversazioni.list(limit=60), self.pubblico),
            limit=5)

    # ── il pubblico ──────────────────────────────────────────────────────────
    def il_pubblico_scrive(self, giorno: int) -> list[dict]:
        """Chi scrive oggi (e chi stanotte): entra nel log isolato, e basta.

        Il log del sandbox è l'unica destinazione: la stanza di produzione non è
        raggiungibile da qui, ed è la ragione per cui una persona inventata non
        può finire nella memoria vera.
        """
        scritti = list(self.pubblico.giornata(giorno, start=self.start))
        scritti += list(self.pubblico.giornata(giorno, start=self.start, notte=True))
        for voce_ in scritti:
            self.conversazioni.add(voce_)
        if scritti:
            self.journal(f"  pubblico: {len(scritti)} battute "
                         f"({len({v['chat'] for v in scritti})} persone)")
        return scritti

    # ── i contenuti ──────────────────────────────────────────────────────────
    def _proposta(self, sistema: str, mossa: dict) -> tuple[dict | None, str]:
        """Il post proposto dalla persona: una volta, e una seconda se è stata un'eco.

        Ritorna `(candidato, motivo)`: il motivo è vuoto quando il post va bene,
        altrimenti è lo scarto da scrivere nel giornale (le stesse parole della
        produzione, perché è lo stesso filtro).

        Il secondo tentativo esiste **solo** per l'eco. Un meta-rumore, un
        doppione o una didascalia vuota si riproporrebbero identici — è il modello
        che non ha niente da dire, e insistere è solo rumore. La reazione che
        ricopia il post della sorella invece non è una mancanza di materiale: è
        il modello che non sa di aver ricopiato. Glielo si dice, una volta.
        """
        autore, reazione = mossa["autore"], mossa["replica_a"]

        def chiedi(insisti: bool) -> tuple[dict | None, str]:
            candidato = parse_post(self._genera(build_post_prompt(
                sistema, memorie=self.memorie(), feed_recente=self.feed.list(5),
                replica_a=reazione, insisti=insisti), tetto=200))
            if candidato is None:
                return None, ""
            ok, motivo = filtra_post(candidato, autore=autore,
                                     feed=self.feed.list(20), replica_a=reazione)
            return (candidato, "") if ok else (None, motivo)

        candidato, motivo = chiedi(insisti=False)
        if candidato is not None or motivo != MOTIVO_ECO:
            return candidato, motivo
        self.journal(f"  {autore}: la reazione ricopiava il post, chiedo di nuovo")
        return chiedi(insisti=True)

    def posta(self, giorno: int, turno: int) -> dict | None:
        mossa = prossima_mossa(self.feed.list(10), turno=turno)
        autore = mossa["autore"]
        sistema = self.identita.get(autore, "")
        if not sistema:
            return None
        candidato, motivo = self._proposta(sistema, mossa)
        if candidato is None:
            self.journal(f"  {autore}: post scartato ({motivo})" if motivo
                         else f"  {autore}: nessun post")
            return None
        reazione = mossa["replica_a"]
        post = nuovo_post(autore, candidato["caption"],
                          kind="reaction" if reazione else "post",
                          image_prompt=candidato.get("image_prompt", ""),
                          reply_to=(reazione or {}).get("id", ""),
                          adesso=self.quando(giorno, ora=11 + (turno % 6)))
        self.feed.add(post)
        self.posti.append(post)
        self.diario.add(voce(id=post["id"], author=autore, tipo="post",
                             testo=candidato["caption"],
                             prompt=candidato.get("image_prompt", ""),
                             ts=post["ts"]))
        etichetta = f"replica a {reazione.get('author')}" if reazione else "post"
        self.journal(f"  {autore} ({etichetta}): {candidato['caption'][:110]}")
        return post

    def poesia(self, giorno: int, autore: str) -> dict | None:
        sistema = self.identita.get(autore, "")
        if not sistema:
            return None
        candidato = parse_post(self._genera(build_poem_prompt(
            sistema, feed_recente=self.feed.list(5)), tetto=220))
        if candidato is None:
            self.journal(f"  {autore}: nessuna poesia")
            return None
        ok, motivo = filtra_post(candidato, autore=autore, feed=self.feed.list(20))
        if not ok:
            self.journal(f"  {autore}: poesia scartata ({motivo})")
            return None
        identificativo = f"poesia-{giorno:02d}-{autore}"
        self.diario.add(voce(id=identificativo, author=autore, tipo="poesia",
                             testo=candidato["caption"],
                             prompt=candidato.get("image_prompt", ""),
                             ts=self.quando(giorno, ora=18)))
        self.journal(f"  {autore} (poesia): {candidato['caption'][:110]}")
        return candidato

    def sogna(self, giorno: int, autore: str) -> dict | None:
        sistema = self.identita.get(autore, "")
        if not sistema:
            return None
        candidato = parse_dream(self._genera(build_dream_prompt(
            sistema, memorie=self.memorie(), feed_recente=self.feed.list(5)),
            tetto=240))
        if candidato is None:
            self.journal(f"  {autore}: nessun sogno")
            return None
        ok, motivo = filtra_dream(candidato, autore=autore, diario=self.diario.list(20))
        if not ok:
            self.journal(f"  {autore}: sogno scartato ({motivo})")
            return None
        identificativo = f"sogno-{giorno:02d}-{autore}"
        self.diario.add(voce(id=identificativo, author=autore, tipo="sogno",
                             testo=candidato["scena"],
                             prompt=candidato.get("disegno", ""),
                             ts=self.quando(giorno, ora=2)))
        self.journal(f"  {autore} (sogno): {candidato['scena'][:110]}")
        return candidato

    # ── la giornata intera ───────────────────────────────────────────────────
    def giornata(self, giorno: int, *, post=2, poesia=True, sogni=2) -> dict:
        """Venti-quattr'ore di vita: il pubblico scrive, loro pubblicano e sognano."""
        self.journal(f"\n── giorno {giorno + 1} ({self.quando(giorno, ora=9)[:10]}) ──")
        self.il_pubblico_scrive(giorno)
        for turno in range(max(0, int(post))):
            self.posta(giorno, turno)
        if poesia:
            self.poesia(giorno, AUTORI[giorno % len(AUTORI)])
        for indice in range(max(0, int(sogni))):
            self.sogna(giorno, AUTORI[(giorno + indice) % len(AUTORI)])
        oggi = [p for p in self.posti if p["ts"][:10] == self.quando(giorno, ora=9)[:10]]
        esiti = ingaggio(oggi, self.pubblico, rng=self.rng)
        return {"giorno": giorno + 1, "post": len(oggi), "ingaggio": esiti}

    def salva(self) -> dict:
        """Scrive i file del sandbox e torna i percorsi: niente esce da questa cartella."""
        self.cartella.mkdir(parents=True, exist_ok=True)
        percorsi = {
            "feed": str(self.feed.save(str(self.cartella / "feed.json"))),
            "diario": str(self.diario.save(str(self.cartella / "diario.json"))),
        }
        self.conversazioni.save(str(self.cartella / "conversations.json"))
        percorsi["conversations"] = str(self.cartella / "conversations.json")
        stato = {"profili": self.pubblico.profili,
                 "soglia_musa": self.pubblico.soglia_musa}
        (self.cartella / "pubblico.json").write_text(
            json.dumps(stato, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.cartella / "posti.json").write_text(
            json.dumps(self.posti, ensure_ascii=False, indent=2), encoding="utf-8")
        percorsi["pubblico"] = str(self.cartella / "pubblico.json")
        percorsi["posti"] = str(self.cartella / "posti.json")
        return percorsi


def voci_del_giorno(diario, giorno_iso: str, tipo: str = "") -> list[dict]:
    """Le voci del diario di un giorno simulato (e solo di quello), in ordine."""
    quando = str(giorno_iso)[:10]
    voci = [v for v in diario.list() if str(v.get("ts", ""))[:10] == quando]
    if tipo:
        voci = [v for v in voci if str(v.get("tipo")) == tipo]
    return list(reversed(voci))


def testo_per_telegram(voce_: dict) -> str:
    """Come si legge una voce su Telegram: chi parla, e cosa ha scritto."""
    autore = str(voce_.get("author") or "").capitalize()
    tipo = str(voce_.get("tipo") or "post")
    corpo = " ".join(str(voce_.get("testo") or "").split())
    if tipo == "sogno":
        return f"🌙 Sogno di {autore}\n\n{corpo}"
    if tipo == "poesia":
        return f"📜 Poesia di {autore}\n\n{corpo}"
    if tipo == "dialogo":
        return f"💬 Anna e Aurora\n\n{corpo}"
    return f"✍️ {autore}\n\n{corpo}"


def invia_su_telegram(chat: str, testi, *, token: str, pausa: float = 1.0) -> int:
    """Pubblica su UNA chat, con un bot che è solo del sandbox.

    Perché un token separato e non il driver di produzione: il canale vero serve
    le persone vere, e un esperimento non deve avere la possibilità di scrivere
    lì dentro. Se il token manca lo si dice, non si inventa un mittente.
    """
    import requests

    if not token:
        print("[vita] TELEGRAM_SANDBOX_TOKEN non impostato: non ho inviato niente",
              file=sys.stderr)
        return 0
    inviati = 0
    for testo in testi:
        try:
            risposta = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": testo, "disable_notification": True},
                timeout=20).json()
        except Exception as errore:                     # pragma: no cover - rete
            print(f"[vita] invio interrotto: {type(errore).__name__}: {errore}",
                  file=sys.stderr)
            return inviati
        if not risposta.get("ok"):
            print(f"[vita] Telegram ha rifiutato: {str(risposta)[:200]}", file=sys.stderr)
            return inviati
        inviati += 1
        time.sleep(max(0.0, float(pausa)))
    return inviati


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="La vita simulata di Anna e Aurora, su file isolati.")
    parser.add_argument("--giorni", type=int, default=7,
                        help="giorni di vita da vivere (default 7)")
    parser.add_argument("--dir", default="data/sandbox-vita",
                        help="la cartella del sandbox (l'unica cosa che si scrive)")
    parser.add_argument("--seed", type=int, default=20261001,
                        help="seed del pubblico e dell'ingaggio: stesso seed, stessa vita")
    parser.add_argument("--pubblico", type=int, default=120,
                        help="quanti profili nel pubblico simulato")
    parser.add_argument("--modello", default=MODELLO_DEFAULT,
                        help=f"modello Ollama (default {MODELLO_DEFAULT})")
    parser.add_argument("--ollama", default=OLLAMA_DEFAULT,
                        help=f"base di Ollama (default {OLLAMA_DEFAULT})")
    parser.add_argument("--senza-modello", action="store_true",
                        help="prova l'impianto senza chiamare il modello")
    parser.add_argument("--post-al-giorno", type=int, default=2)
    parser.add_argument("--sogni-al-giorno", type=int, default=2)
    parser.add_argument("--niente-poesia", dest="poesia", action="store_false",
                        help="salta la poesia del giorno")
    parser.add_argument("--giorno-secondi", type=float, default=0.0,
                        help="pausa fra i giorni (86400 = tempo reale)")
    parser.add_argument("--invia", action="store_true",
                        help="pubblica su Telegram (serve TELEGRAM_SANDBOX_TOKEN)")
    parser.add_argument("--chat", default="", help="chat o canale del sandbox")
    parser.add_argument("--prova", action="store_true",
                        help="stampa il piano e non scrive niente")
    args = parser.parse_args(argv)

    finti = pubblico(args.pubblico, seed=args.seed)
    start = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    if args.prova:
        print(f"modello:   {args.modello if not args.senza_modello else '(nessuno)'}")
        print(f"pubblico:  {len(finti)} profili (seed {args.seed})")
        print(f"giorni:    {args.giorni} × ({args.post_al_giorno} post, "
              f"{'1 poesia, ' if args.poesia else ''}{args.sogni_al_giorno} sogni)")
        print(f"cartella:  {args.dir}")
        print(f"telegram:  {'—' if not args.invia else (args.chat or '(chat mancante)')}")
        print("\n(non ho scritto niente: togli --prova per vivere la vita)")
        return 0

    righe: list[str] = []

    def giornalista(testo: str) -> None:
        print(testo)
        righe.append(str(testo))

    regista = Regista(cartella=args.dir, finti=finti,
                      modello="" if args.senza_modello else args.modello,
                      start=start, rng=random.Random(args.seed),
                      modello_url=args.ollama, giornalista=giornalista)
    giornalista(f"── sandbox vita: {args.giorni} giorni, seed {args.seed}, "
                f"modello {regista.modello or '(nessuno)'} ──")
    for autore in AUTORI:
        giornalista(f"  identità {autore}: "
                    f"{'caricata' if regista.identita.get(autore) else 'MANCANTE'}")

    esiti_totali: dict[str, dict] = {}
    for giorno in range(max(1, args.giorni)):
        esito = regista.giornata(giorno, post=args.post_al_giorno,
                                 poesia=args.poesia, sogni=args.sogni_al_giorno)
        esiti_totali.update(esito["ingaggio"])
        if args.invia and args.chat:
            voci = voci_del_giorno(regista.diario, regista.quando(giorno, ora=9))
            inviati = invia_su_telegram(
                args.chat, [testo_per_telegram(v) for v in voci],
                token=os.getenv("TELEGRAM_SANDBOX_TOKEN", "").strip())
            giornalista(f"  telegram: {inviati}/{len(voci)} messaggi")
        if args.giorno_secondi > 0:
            time.sleep(args.giorno_secondi)

    numeri = kpi(finti, regista.posti, esiti_totali, giorni=args.giorni)
    percorsi = regista.salva()
    (Path(args.dir) / "kpi.json").write_text(
        json.dumps(numeri, ensure_ascii=False, indent=2), encoding="utf-8")
    percorsi["kpi"] = str(Path(args.dir) / "kpi.json")
    (Path(args.dir) / "vita.log").write_text("\n".join(righe) + "\n", encoding="utf-8")
    percorsi["log"] = str(Path(args.dir) / "vita.log")

    giornalista("\n── come è andata ──")
    for chiave in ("giorni", "post", "per_autore", "follower_nuovi", "ritorno",
                   "ritorno_rate", "muse", "reach", "engagement_rate"):
        valore = numeri.get(chiave)
        if isinstance(valore, float):
            valore = f"{valore:.3f}"
        giornalista(f"  {chiave}: {valore}")
    giornalista("\nfile scritti (niente altro è stato toccato):")
    for nome, percorso in sorted(percorsi.items()):
        giornalista(f"  {nome:14} {percorso}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




