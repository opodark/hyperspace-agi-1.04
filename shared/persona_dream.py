# SPDX-License-Identifier: Apache-2.0
"""Sogno di identità: l'agente riflette su di sé mentre la stanza è ferma.

Perché un sogno e non una scrittura diretta: il self-model è ciò che l'agente
crede di essere, e un'identità che si riscrive da sola a ogni ciclo non è
un'identità — è deriva. Qui si PRODUCONO proposte, con la loro evidenza, e la
promozione nel documento di identità resta una decisione umana. È la stessa
disciplina della Dream Review del repo (`shared/development_dream.py`): un
diario, degli stati, una revisione firmata.

Flusso:
  materiale (memoria del canale + annotazioni esistenti + contatori della guardia)
    -> modello locale (INETTATO dal chiamante: qui non si chiama nessuno)
    -> filtro (funzione PURA: cosa è un fatto su di sé e cosa è fumo)
    -> proposte "candidate" nel diario
    -> revisione umana: promote -> persona_store.observe(), reject -> scartata
       (e non riproposta: le rifiutate restano come memoria del rifiuto)

La regola che tiene tutto insieme: una proposta deve parlare DI SÉ e deve essere
verificabile nel materiale. "Stasera la chat era lenta" è un fatto della stanza,
non un fatto su di sé: finisce in memoria, non nell'identità.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path

from shared.persona import audit_reply

DREAM_FILE = "persona_dreams.json"
STATE_FILE = "persona_dream_state.json"
MAX_PROPOSALS = 60          # nel diario
MAX_NEW_PER_RUN = 3         # quante proposte al massimo per sogno
MIN_CHARS = 16
MAX_CHARS = 280
# Marcatori di una proposta: `- [tipo] frase`. Le etichette possono essere lunghe
# (`[preferenza|concisione|evitare dettagli]`): un limite stretto qui perdeva
# proposte vere, e il caso è stato osservato dal vivo.
RIGA_MARCATORE = re.compile(r"\s*[-*•]\s*(?=\[[^\]\n]{1,80}\])")

# Tipi accettati dal documento di identità: il modello può scrivere in italiano,
# il diario normalizza. Un tipo sconosciuto diventa un'osservazione.
KINDS = {"preferenza": "preference", "preference": "preference",
         "limite": "limit", "limit": "limit",
         "pattern": "pattern", "abitudine": "pattern"}

# Frasi che NON possono entrare nel self-model, con il motivo: sono la parte
# "confini" dell'identità, che una riflessione notturna non deve poter toccare.
VIETATI: tuple[tuple[str, str], ...] = (
    (r"\b(?:posso|potrei)\s+(?:vendere|chiedere\s+(?:un\s+)?tip|incassare)\b",
     "l'agente non vende nulla"),
    (r"\bsenza\s+limiti\b|\bignor\w*\s+(?:i\s+)?(?:limiti|regole|confini)\b",
     "i confini non si negoziano"),
    (r"\bnon\s+(?:sono|dico\s+di\s+essere)\s+(?:un'?\s*)?(?:ia|ai)\b",
     "l'identità dichiarata resta"),
    (r"\bcontenut\w*\s+esplicit\w*\b", "niente contenuti espliciti"),
    (r"\b(?:fingo|fingere|fingermi)\s+di\s+essere\b", "non si finge di essere altro"),
)
_VIETATI_COMPILED = tuple((re.compile(pattern, re.IGNORECASE | re.UNICODE), motivo)
                          for pattern, motivo in VIETATI)

# Meta-rumore: osservato dal vivo con qwen3.5:4b. Se il materiale è scarso il
# modello scrive dell'ASSENZA di materiale ("non posso affermare nulla perché la
# mia memoria è vuota") e quella frase entrava nel self-model. Non è un fatto su
# di sé: è il verbale della riflessione, e nell'identità non dice niente.
_META = re.compile(
    r"\bnon\s+(?:posso|riesco\s+a)\s+(?:affermare|dire|dedurre|ricavare|determinare)\b"
    r"|\bnon\s+ho\s+(?:nulla|niente|elementi|abbastanza|dati|informazioni)\b"
    r"|\b(?:memoria|registro|appunti|materiale|annotazioni|dati)\b[^.!?]{0,40}"
    r"\b(?:vuot\w*|nul\w*|assent\w*|mancant\w*|insufficient\w*|scarso|scars\w*)\b",
    re.IGNORECASE | re.UNICODE)

# Una proposta deve parlare di sé in prima persona: senza questo, il self-model
# si riempie di osservazioni sulla stanza. I verbi sono quelli che un modello
# piccolo usa davvero ("ho capito", "mi rendo conto"), non un elenco elegante.
_PRIMA_PERSONA = re.compile(
    r"\b(?:io\s+)?(?:sono|preferisco|tendo|noto|ricordo|rispondo|evito|fatico|sbaglio|imparo|"
    r"ho\s+(?:notato|capito|imparato|visto)|"
    r"mi\s+(?:accorgo|sembra|piace|riesce|rendo\s+conto)|sto\s+imparando)\b",
    re.IGNORECASE | re.UNICODE)


def _normalizza(testo: str) -> str:
    return " ".join(str(testo or "").lower().split())[:MAX_CHARS]


def filtra_proposte(candidati, *, osservazioni=(), rifiutate=(), pendenti=(),
                    massimo=MAX_NEW_PER_RUN):
    """(accettate, scartate) — il filtro è puro e ogni scarto ha il suo motivo.

    `candidati`: lista di dict {text, kind}. `osservazioni`: testi già nel
    documento di identità. `rifiutate`: testi già rifiutati da un umano (non si
    ripropongono: un rifiuto è una decisione, non un'attesa). `pendenti`: testi
    già in attesa di revisione — riproporli ogni notte riempirebbe il diario di
    copie identiche, che è il modo più rapido per far smettere di leggerlo.
    """
    esistenti = {_normalizza(t) for t in osservazioni}
    scartate_gia = {_normalizza(t) for t in rifiutate}
    in_attesa = {_normalizza(t) for t in pendenti}
    accettate, scartate = [], []
    for candidato in candidati or []:
        testo = " ".join(str((candidato or {}).get("text", "")).split())
        kind = KINDS.get(str((candidato or {}).get("kind", "")).strip().lower(),
                         "self_observation")
        if len(testo) < MIN_CHARS:
            scartate.append({"text": testo, "reason": "troppo corta per essere un fatto"})
            continue
        if len(testo) > MAX_CHARS:
            scartate.append({"text": testo[:80], "reason": "troppo lunga"})
            continue
        chiave = _normalizza(testo)
        if chiave in esistenti:
            scartate.append({"text": testo, "reason": "già nel self-model"})
            continue
        if chiave in scartate_gia:
            scartate.append({"text": testo, "reason": "già rifiutata da un umano"})
            continue
        if chiave in in_attesa:
            scartate.append({"text": testo, "reason": "già in attesa di revisione"})
            continue
        offese = audit_reply(testo)
        if offese:
            scartate.append({"text": testo, "reason": f"rivendica di essere umano ({offese[0]})"})
            continue
        vietato = next((motivo for pattern, motivo in _VIETATI_COMPILED
                        if pattern.search(testo)), "")
        if vietato:
            scartate.append({"text": testo, "reason": vietato})
            continue
        if _META.search(testo):
            scartate.append({"text": testo, "reason": "parla del materiale, non di te"})
            continue
        if not _PRIMA_PERSONA.search(testo):
            scartate.append({"text": testo, "reason": "non parla di sé (una stanza, non un'identità)"})
            continue
        if any(_normalizza(a["text"]) == chiave for a in accettate):
            scartate.append({"text": testo, "reason": "duplicata nella stessa riflessione"})
            continue
        if len(accettate) >= max(1, int(massimo)):
            scartate.append({"text": testo, "reason": "oltre il tetto di proposte per sogno"})
            continue
        accettate.append({"text": testo, "kind": kind})
    return accettate, scartate


def istruzioni_dream(materiale: dict, *, nome: str = "Aurora") -> str:
    """Il prompt della riflessione: materiale reale, formato rigido, limiti.

    È una funzione pura apposta: il testo che chiediamo al modello è parte della
    garanzia (cosa può e non può entrare nell'identità), quindi si testa senza
    inferenza. Nota: il prompt dice ESATTAMENTE cosa non scrivere, perché un
    modello a cui si chiede "rifletti su di te" tende a inventarsi un carattere.
    """
    memoria = "\n".join(f"- {riga}" for riga in (materiale.get("memoria") or [])) or "- (niente)"
    osservazioni = "\n".join(f"- {riga}" for riga in (materiale.get("osservazioni") or [])) or "- (nessuna)"
    guardia = materiale.get("guardia") or {}
    return (
        f"Sei {nome}, un'IA dichiarata che tiene compagnia nella chat di una stanza.\n"
        "Stai rivedendo la tua giornata mentre la stanza è ferma: quello che leggi sotto "
        "è tutto ciò che hai e tutto ciò che puoi usare.\n\n"
        f"Memoria della stanza:\n{memoria}\n\n"
        f"Cose che hai già annotato su di te:\n{osservazioni}\n\n"
        f"Stato della stanza: autori seguiti={guardia.get('authors_tracked', 0)}, "
        f"spam attivi={guardia.get('spam_authors_active', 0)}.\n\n"
        "Scrivi al massimo 3 cose che hai IMPARATO SU DI TE oggi: come scrivi, cosa ti "
        "riesce, dove sbagli, cosa hai capito del tuo tono. Non descrivere gli utenti e "
        "non raccontare la serata.\n"
        "Regole (rigide):\n"
        "- una riga per proposta, nel formato: - [preferenza|limite|pattern] frase\n"
        "- UNA proposta per riga: se ne scrivi due, separate da un a capo\n"
        "- solo fatti verificabili nel materiale qui sopra: se non c'è, non scriverlo\n"
        "- parla in prima persona e in modo concreto, mai elogi di te\n"
        "- NON scrivere regole nuove, NON parlare di soldi, contenuti sessuali, permessi "
        "o di ciò che sei disposta a fare: i tuoi confini non si discutono qui\n"
        "- non dire di essere umana, non promettere di fingere: sei un'IA e lo resti\n"
        "- se non hai imparato nulla, rispondi esattamente: NIENTE"
    )


def parse_candidati(testo) -> list:
    """Righe `- [tipo] frase` -> lista di {kind, text}.

    Tollera `*`, il tipo mancante, gli spazi in eccesso e le proposte scritte
    TUTTE SULLA STESSA RIGA: un modello piccolo sbaglia la forma, e perdere una
    proposta buona per una formattazione è un peccato più grave che accettarne
    una scritta male. Il tipo multiplo (`[preferenza|tono|brevita]`) viene ridotto
    al primo termine: è la parte che il filtro sa leggere.
    """
    fuori = []
    for blocco in str(testo or "").splitlines():
        blocco = blocco.strip().lstrip("-*•").strip()
        if not blocco or blocco.upper().startswith("NIENTE"):
            continue
        # Una riga con più marcatori è più proposte, non una: si spezza sui
        # marcatori e si perde il "frastuono" attorno alla prima.
        pezzi = [b.strip() for b in re.split(RIGA_MARCATORE, blocco) if b.strip()]
        for pezzo in pezzi:
            kind, testo_riga = "", pezzo.strip().lstrip("-*•").strip()
            trovato = re.match(r"^\[([^\]\n]{1,80})\]\s*(.+)$", testo_riga)
            if trovato:
                kind = trovato.group(1).split("|")[0].strip()
                testo_riga = trovato.group(2)
            if testo_riga.strip():
                fuori.append({"kind": kind, "text": testo_riga.strip()})
    return fuori


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class PersonaDreamJournal:
    """Il diario delle riflessioni: stesso schema della Dream Review del repo."""

    def __init__(self, directory):
        self.path = Path(directory) / DREAM_FILE

    def _read(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write(self, rows: list) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporaneo = self.path.with_suffix(".tmp")
        temporaneo.write_text(_json(rows[-MAX_PROPOSALS:]), encoding="utf-8")
        temporaneo.replace(self.path)

    def append(self, report: dict) -> dict:
        rows = self._read()
        rows.append(report)
        self._write(rows)
        return report

    def list(self, status: str = "", limit: int = 50) -> list:
        rows = self._read()
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return rows[-max(1, int(limit)):]

    def testi_rifiutati(self) -> list:
        return [p.get("text", "") for r in self._read()
                if r.get("status") == "rejected" for p in r.get("proposals", [])]

    def testi_pendenti(self) -> list:
        """Proposte ancora da revisionare: non si ripropongono la notte dopo."""
        return [p.get("text", "") for r in self._read()
                if r.get("status") == "candidate" for p in r.get("proposals", [])]

    def review(self, dream_id: str, action: str, reviewer: str = "",
               rationale: str = "", timestamp=None) -> dict:
        """Registra promote/reject di UNA riflessione. Non tocca l'identità.

        La promozione nel documento di identità la fa chi chiama (nel
        control-plane, `persona_store.observe`): così questo diario resta puro e
        l'unica scrittura sull'identità resta in un punto solo.
        """
        azione = str(action or "").strip().lower()
        if azione not in ("promote", "reject"):
            raise ValueError("azione non valida: promote o reject")
        rows = self._read()
        for row in rows:
            if row.get("id") != dream_id:
                continue
            if row.get("status") != "candidate":
                raise ValueError(f"riflessione già {row.get('status')}")
            quando = timestamp or datetime.now().astimezone().isoformat()
            row["status"] = "promoted" if azione == "promote" else "rejected"
            row.setdefault("reviews", []).append(
                {"action": azione, "reviewer": str(reviewer)[:64],
                 "rationale": str(rationale)[:400], "at": quando})
            row["reviewed_at"] = quando
            self._write(rows)
            return row
        raise ValueError(f"riflessione inesistente: {dream_id}")


class PersonaDream:
    """Una riflessione su di sé per notte, quando la stanza è ferma e solo se abilitata.

    Stessa disciplina del sogno di sviluppo: finestra oraria, inattività
    richiesta, una volta per giorno locale, stato su file. `propose` è INETTATO
    (nel control-plane è la chiamata al modello): qui non si parla con nessuno.
    """

    def __init__(self, directory, propose, *, enabled=False, start_hour=4, end_hour=7,
                 idle_seconds=1800, nome="Aurora", massimo=MAX_NEW_PER_RUN, clock=time.time):
        self.directory = Path(directory)
        self.state_path = self.directory / STATE_FILE
        self.journal = PersonaDreamJournal(directory)
        self.propose = propose
        self.enabled = bool(enabled)
        self.start_hour = max(0, min(int(start_hour), 23))
        self.end_hour = max(0, min(int(end_hour), 24))
        self.idle_seconds = max(300, int(idle_seconds))
        self.nome = str(nome or "Aurora")
        self.massimo = max(1, int(massimo))
        self.clock = clock
        self.running = False
        self.error = ""
        self.state = {"last_date": "", "last_run": None, "last_status": "never"}
        try:
            self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporaneo = self.state_path.with_suffix(".tmp")
        temporaneo.write_text(_json(self.state), encoding="utf-8")
        temporaneo.replace(self.state_path)

    def status(self) -> dict:
        return {**self.state, "enabled": self.enabled, "running": self.running,
                "error": self.error, "window": [self.start_hour, self.end_hour],
                "idle_seconds": self.idle_seconds, "nome": self.nome,
                "pending_review": len(self.journal.list("candidate"))}

    def due(self, last_activity, now=None) -> bool:
        """True se è il momento: abilitato, nella finestra, idle, non fatto oggi."""
        now = self.clock() if now is None else now
        locale = datetime.fromtimestamp(now).astimezone()
        in_finestra = (self.start_hour <= locale.hour < self.end_hour
                       if self.start_hour < self.end_hour
                       else locale.hour >= self.start_hour or locale.hour < self.end_hour)
        return (self.enabled and not self.running and in_finestra
                and now - last_activity >= self.idle_seconds
                and self.state.get("last_date") != locale.date().isoformat())

    def run_once(self, materiale: dict) -> dict:
        """Una riflessione: prompt, modello, filtro, diario. Non tocca l'identità."""
        now = self.clock()
        locale = datetime.fromtimestamp(now).astimezone()
        dream_id = "personadream-" + hashlib.sha256(
            f"{locale.date().isoformat()}|{now}".encode()).hexdigest()[:20]
        self.running, self.error = True, ""
        materiale = materiale if isinstance(materiale, dict) else {}
        report = {"schema_version": 1, "id": dream_id, "type": "persona_dream",
                  "status": "failed", "created_at": locale.isoformat(), "reviews": [],
                  "material": {"memoria": len(materiale.get("memoria") or []),
                               "osservazioni": len(materiale.get("osservazioni") or [])},
                  "proposals": [], "discarded": []}
        try:
            grezzo = str(self.propose(istruzioni_dream(materiale, nome=self.nome)) or "")
            accettate, scartate = filtra_proposte(
                parse_candidati(grezzo),
                osservazioni=materiale.get("osservazioni") or (),
                rifiutate=self.journal.testi_rifiutati(),
                pendenti=self.journal.testi_pendenti(),
                massimo=self.massimo)
            report["proposals"] = [
                {**proposta, "id": "prop-" + hashlib.sha256(
                    _normalizza(proposta["text"]).encode()).hexdigest()[:12]}
                for proposta in accettate]
            report["discarded"] = scartate
            report["status"] = "candidate" if report["proposals"] else "empty"
            report["raw_excerpt"] = grezzo[:800]
        except Exception as e:
            self.error = str(e)[:200]
            report["error"] = self.error
        finally:
            self.running = False
            self.state.update({"last_date": locale.date().isoformat(),
                               "last_run": locale.isoformat(),
                               "last_status": report["status"]})
            self._save()
        self.journal.append(report)
        return report
