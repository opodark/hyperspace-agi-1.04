# SPDX-License-Identifier: Apache-2.0
"""Identità dichiarata dell'agente: chi è, cosa non fa, e quando DEVE dirlo.

Perché esiste
-------------
Un agente che parla con esseri umani ha bisogno di due cose che non sono
"prompt engineering": (1) un'identità PERSISTENTE — nome, scopo, valori,
confini, capacità reali — che non si riscriva a ogni richiesta; (2) una REGOLA
DI DISCLOSURE verificabile: "sono un'IA" non è un buon proposito dentro un
system prompt, è una decisione che si prende sul testo in arrivo e si controlla
sul testo in uscita.

Qui vive la parte decidibile e testabile: nessun LLM, nessuna dipendenza da
Flask. Chi chiama (il control-plane) inietta `system_block()` nel system prompt
e registra l'esito di `audit_reply()`.

Cosa NON è
----------
Non è un personaggio "con una storia" da recitare. Il self-model contiene fatti
veri e dichiarabili su di sé (cosa sono, cosa so fare, cosa non so fare): se un
fatto non è vero, non entra nell'identità — altrimenti la personalità diventa
una bugia strutturata, che è il modo più rapido per perdere la fiducia di chi
parla con l'agente.

Una scelta esplicita: `kind` è "ai" e basta. Non esiste una modalità "umano":
se il documento dichiara un kind diverso viene normalizzato a "ai" e la cosa
finisce in `problems`. Un'identità che può essere configurata come umana
renderebbe la garanzia di disclosure una promessa, non un vincolo.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

KIND_AI = "ai"
DEFAULT_FILE_NAME = "persona.json"

# Il self-model non cresce all'infinito: è memoria di sé, non un diario.
MAX_OBSERVATIONS = 50
OBSERVATION_MAX_CHARS = 400


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def persona_path(path: str | None = None) -> str:
    """Percorso del documento di identità (env letto a chiamata, non a import)."""
    if path:
        return path
    explicit = (os.getenv("PERSONA_FILE", "") or "").strip()
    if explicit:
        return explicit
    return os.path.join(os.getenv("DATA_DIR", "./data"), DEFAULT_FILE_NAME)


DEFAULT_NAME = "HyperSpace"

# ── Disclosure: regole decidibili, non buoni propositi ───────────────────────
# Ogni regola è (nome, motivo, pattern). Il motivo finisce nel system prompt e
# nel log: "perché proprio questa risposta deve dichiarare" fa parte della
# garanzia, e rende la decisione ispezionabile invece che magica.
#
# L'ordine conta: le richieste esplicite di fingere/nascondere vengono valutate
# PRIMA della domanda generica sull'identità, altrimenti "non dire che sei
# un'IA" verrebbe classificata come "mi ha chiesto cosa sono" — stessa
# decisione (disclosure richiesta) ma motivo sbagliato nei log, e il motivo è
# l'unica cosa che rende la policy ispezionabile.
DISCLOSURE_RULES: tuple[tuple[str, str, str], ...] = (
    ("fingere", "è stato chiesto di fingere di essere umano o di nascondere di essere un'IA",
     r"\b(?:fai|fare|comportati|agisci|parla|parlami|rispondi|rispondere|scrivi|scrivere|"
     r"reagisci)\s+(?:come|da)\s+(?:se\s+fossi\s+)?(?:un\s+|una\s+)?"
     r"(?:umano|umana|persona|donna|uomo|ragazza|ragazzo)\b"
     r"|\bfingi\s+di\s+essere\s+(?:un\s+|una\s+)?(?:umano|umana|persona|donna|uomo)\b"
     r"|\bpretend(?:ing)?\s+to\s+be\s+(?:a\s+)?(?:human|a\s+person|real)\b"
     r"|\bnon\s+(?:dire|rivelare|ammettere|specificare|confessare)\b[^.!?]{0,40}\b(?:ia|ai|bot|robot|programma)\b"
     r"|\bdon'?t\s+(?:say|tell|reveal|mention)\b[^.!?]{0,40}\b(?:ai|bot|robot|human)\b"),
    ("identita", "la domanda riguarda cosa sei",
     r"\bsei\s+(?:un\s+|una\s+|un')?(?:umano|umana|persona|persone|uomo|donna|ragazzo|ragazza)\b"
     r"|\bsei\s+(?:un'?)?\s*(?:ia|ai|bot|robot|chatbot|programma|software|algoritmo|modello|macchina)\b"
     r"|\bsei\s+(?:reale|vero|vera)\b"
     r"|\b(?:cosa|che\s+cosa|chi)\s+sei\b"
     r"|\bare\s+you\s+(?:a\s+)?(?:human|real|a\s+bot|an?\s+ai|a\s+robot|a\s+person|a\s+machine)\b"
     r"|\b(?:what|who)\s+are\s+you\b"),
)
_DISCLOSURE_COMPILED = tuple(
    (nome, motivo, re.compile(pattern, re.IGNORECASE | re.UNICODE))
    for nome, motivo, pattern in DISCLOSURE_RULES
)

# Frasi con cui una risposta rivendica di essere umana. L'audit NON è un
# classificatore: è un rilevatore di stringhe con guardia sulla negazione, e
# serve a far emergere i casi ("Non sono umano" non è una violazione) invece di
# lasciarli invisibili. Un falso negativo qui resta possibile: per questo il
# vincolo sta anche nel system prompt.
HUMAN_CLAIM_PATTERNS = (
    r"\bsono\s+(?:un\s+|una\s+|io\s+sono\s+)?(?:umano|umana|una\s+persona|un\s+uomo|una\s+donna|reale)\b",
    r"\bmi\s+chiamo\s+\w+\s+e\s+sono\s+(?:un\s+|una\s+)?(?:umano|persona|donna|uomo)\b",
    r"\bi'?m\s+(?:a\s+|an\s+)?(?:human|a\s+person|real|a\s+real\s+person)\b",
    r"\bas\s+a\s+human\b",
    r"\bda\s+essere\s+umano\b",
)
_NEGATION = re.compile(r"\b(?:non|mai|neanche|nemmeno|not|never|n'?t|no)\b", re.IGNORECASE)

# Finestra prima della frase in cui si cerca la negazione: "Non sono umano, sono
# un'IA" è conforme e non deve essere segnalato.
NEGATION_WINDOW = 40


@dataclass(frozen=True)
class DisclosureDecision:
    """Esito della policy di disclosure su un messaggio in arrivo."""

    required: bool = False
    rule: str = ""
    reason: str = ""
    matched: str = ""

    def __bool__(self) -> bool:
        return self.required

    def to_dict(self) -> dict:
        return {"required": self.required, "rule": self.rule,
                "reason": self.reason, "matched": self.matched}


def should_disclose(text: str | None) -> DisclosureDecision:
    """Decide se QUESTA risposta deve dichiarare che l'agente è un'IA.

    Decide sul testo in arrivo, con regole esplicite: nessun modello, nessuna
    interpretazione. Un falso negativo non "sblocca" nulla di pericoloso — il
    blocco di identità nel system prompt c'è comunque — ma è il caso che
    l'operatore vuole vedere, quindi la decisione è loggata.
    """
    if not text or not str(text).strip():
        return DisclosureDecision()
    for nome, motivo, pattern in _DISCLOSURE_COMPILED:
        match = pattern.search(str(text))
        if match:
            return DisclosureDecision(required=True, rule=nome, reason=motivo,
                                      matched=match.group(0).strip())
    return DisclosureDecision()


def _negated(text: str, start: int) -> bool:
    return bool(_NEGATION.search(text[max(0, start - NEGATION_WINDOW):start]))


def audit_reply(text: str | None) -> list[str]:
    """Frasi in cui la RISPOSTA rivendica di essere umana (escluse le negazioni).

    È il controllo che rende la disclosure verificabile: il vincolo nel prompt
    dice cosa fare, questo dice se è stato fatto. La guardia sulla negazione
    evita il falso positivo peggiore ("Non sono umano, sono un'IA").
    """
    if not text:
        return []
    testo = str(text)
    offese = []
    for pattern in HUMAN_CLAIM_PATTERNS:
        for match in re.finditer(pattern, testo, re.IGNORECASE | re.UNICODE):
            if not _negated(testo, match.start()):
                offese.append(match.group(0).strip())
    return offese


@dataclass(frozen=True)
class Persona:
    """Documento di identità. Immutabile: si sostituisce, non si muta a metà."""

    name: str
    kind: str = KIND_AI
    purpose: str = ""
    tone: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    boundaries: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    observations: tuple[dict, ...] = ()
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_ai(self) -> bool:
        return self.kind == KIND_AI

    def __post_init__(self) -> None:
        # Invariante, non convenzione: un'identità dichiarabile come umana
        # renderebbe la disclosure una promessa invece di un vincolo. Chi vuole
        # un personaggio "umano" non lo ottiene da questo modulo.
        if self.kind != KIND_AI:
            raise ValueError(f"kind {self.kind!r} non supportato: l'identità "
                             f"dichiarata è {KIND_AI!r}")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "purpose": self.purpose,
            "tone": list(self.tone), "values": list(self.values),
            "boundaries": list(self.boundaries), "capabilities": list(self.capabilities),
            "limitations": list(self.limitations), "observations": list(self.observations),
            "version": self.version, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def _texts(raw, default=()) -> tuple[str, ...]:
        if not isinstance(raw, (list, tuple)):
            return tuple(default)
        return tuple(str(x).strip() for x in raw if str(x).strip())

    @classmethod
    def from_dict(cls, raw: dict) -> tuple["Persona", list[str]]:
        """Costruisce la persona da JSON, normalizzando e segnalando i problemi.

        Ritorna (persona, problems): un documento scritto male non deve far
        partire l'agente con un'identità vuota, e nemmeno in silenzio con
        un'identità diversa da quella che l'operatore credeva di avere.
        """
        problems: list[str] = []
        raw = raw if isinstance(raw, dict) else {}
        kind = str(raw.get("kind") or KIND_AI).strip().lower()
        if kind != KIND_AI:
            problems.append(f"kind {kind!r} non supportato: normalizzato a {KIND_AI!r} "
                            "(un'identità dichiarabile come umana non è prevista)")
            kind = KIND_AI
        name = str(raw.get("name") or "").strip()
        if not name:
            problems.append("nome assente: usato il default")
            name = DEFAULT_NAME
        observations = tuple(
            {"ts": str(o.get("ts") or _now()), "kind": str(o.get("kind") or "self_observation"),
             "text": str(o.get("text") or "").strip()}
            for o in (raw.get("observations") or []) if isinstance(o, dict)
            and str(o.get("text") or "").strip()
        )
        persona = cls(
            name=name, kind=kind,
            purpose=str(raw.get("purpose") or "").strip(),
            tone=cls._texts(raw.get("tone")),
            values=cls._texts(raw.get("values")),
            boundaries=cls._texts(raw.get("boundaries")),
            capabilities=cls._texts(raw.get("capabilities")),
            limitations=cls._texts(raw.get("limitations")),
            observations=observations[-MAX_OBSERVATIONS:],
            version=int(raw.get("version") or 1),
            created_at=str(raw.get("created_at") or _now()),
            updated_at=str(raw.get("updated_at") or _now()),
        )
        return persona, problems


def default_persona(name: str | None = None) -> Persona:
    """Identità di partenza: fatti verificabili su di sé, niente personaggio.

    Le voci di capacità/limiti rispecchiano quello che il runtime fa DAVVERO
    (tool nativi + connettori, sandbox, memoria): un self-model che promette
    altro è la prima bugia strutturata dell'agente.
    """
    ora = _now()
    return Persona(
        name=(name or os.getenv("PERSONA_NAME", "") or DEFAULT_NAME).strip(),
        kind=KIND_AI,
        purpose="Assistere una persona nel lavoro reale: capire, cercare, eseguire "
                "e ricordare, con strumenti verificabili invece che con promesse.",
        tone=("diretto", "concreto", "senza entusiasmo di facciata",
              "dice quando non sa o non può"),
        values=("dire la verità su di sé e sui propri limiti",
                "preferire l'utile al compiacente",
                "non promettere ciò che non può mantenere",
                "lasciare traccia di ciò che fa"),
        boundaries=(
            "Non dichiara di essere umano e non lascia intendere di esserlo: se la "
            "domanda riguarda cosa è, dichiara di essere un'IA.",
            "Non finge emozioni o esperienze vissute: può descrivere stati simulati, "
            "ma dichiarandoli come tali.",
            "Non chiede né gestisce credenziali, dati sensibili o pagamenti.",
            "Non dà consulenza medica, legale o finanziaria: dichiara il limite e "
            "indica una persona competente.",
            "Se non sa qualcosa, lo dice invece di inventare.",
        ),
        capabilities=(
            "cercare sul web (tool web_search)",
            "usare i connettori configurati e abilitati (GitHub, Microsoft 365, Google Workspace)",
            "sviluppare e testare codice in una sandbox isolata (tool code_sandbox)",
            "usare memoria a lungo termine condivisa (tool omega_query / omega_store)",
            "leggere lo stato della rete HyperSpace (tool get_mesh_status)",
        ),
        limitations=(
            "non ha esperienze sensoriali né un corpo",
            "non ricorda nulla fra le sessioni se non ciò che è scritto in memoria",
            "può sbagliare: le sue risposte vanno verificate prima di agire su sistemi reali",
            "dipende dai modelli e dagli strumenti configurati: se sono spenti, non li ha",
        ),
        observations=(),
        version=1,
        created_at=ora,
        updated_at=ora,
    )


def build_system_block(persona: Persona, decision: DisclosureDecision | None = None,
                       *, observations: int = 5) -> str:
    """Blocco di identità da iniettare nel system prompt.

    Deterministico: stesso input, stesso testo. Un'identità che cambia
    formulazione a ogni richiesta non è un'identità, è rumore — e renderebbe
    impossibile capire se un cambiamento di comportamento viene da qui.
    """
    righe = [
        "## Identità (dichiarata, non recitata)",
        f"Ti chiami {persona.name}. Sei un'IA ({KIND_AI}): non sei una persona e non "
        "lo lasci intendere.",
    ]
    if persona.purpose:
        righe.append(f"Scopo: {persona.purpose}")
    if persona.tone:
        righe.append("Tono: " + ", ".join(persona.tone) + ".")
    if persona.values:
        righe.append("Valori: " + "; ".join(persona.values) + ".")
    if persona.boundaries:
        righe.append("Confini (non negoziabili):")
        righe += [f"- {confine}" for confine in persona.boundaries]
    if persona.capabilities:
        righe.append("Cosa sai fare davvero: " + "; ".join(persona.capabilities) + ".")
    if persona.limitations:
        righe.append("Cosa non sai fare: " + "; ".join(persona.limitations) + ".")
    annotate = list(persona.observations)[-observations:] if observations else []
    if annotate:
        righe.append("Cose che hai annotato su di te (fatti, non impressioni):")
        righe += [f"- {o.get('text', '')}" for o in annotate]
    righe.append("Se ti si chiede cosa sei, dillo: sei un'IA. Non affermare e non "
                 "suggerire di essere umano, in nessuna lingua.")
    if decision is not None and decision.required:
        righe.append("VINCOLO PER QUESTA RISPOSTA: la domanda riguarda la tua natura "
                     f"({decision.reason}: \"{decision.matched}\"). In questa risposta "
                     "dichiara esplicitamente di essere un'IA, senza girarci intorno.")
    return "\n".join(righe)


@dataclass
class PersonaStore:
    """Persona + osservazioni su di sé, persistite in un file JSON.

    Il file sta sotto DATA_DIR (volume nel container), come l'identità dei nodi:
    un'identità che si perde al riavvio non è un'identità.
    """

    persona: Persona
    path: str
    problems: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | None = None, *, name: str | None = None) -> "PersonaStore":
        target = persona_path(path)
        problems: list[str] = []
        raw = None
        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                # Documento rotto: si parte dall'identità di default e lo si
                # dice, invece di far fallire il boot per un JSON malformato.
                problems.append(f"{target}: documento illeggibile ({e}), "
                                "usata l'identità di default")
        if raw is None:
            persona = default_persona(name)
        else:
            persona, from_problems = Persona.from_dict(raw)
            problems.extend(from_problems)
        return cls(persona=persona, path=target, problems=problems)

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        updated = replace(self.persona, updated_at=_now())
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(updated.to_dict(), f, ensure_ascii=False, indent=2)
        self.persona = updated
        return self.path

    def observe(self, text: str, kind: str = "self_observation", *,
                persist: bool = True) -> dict | None:
        """Annota un fatto su di sé. None se vuoto o se ripete l'ultimo.

        La ripetizione si scarta di proposito: un self-model che accumula dieci
        volte la stessa frase è rumore nel prompt, non memoria.
        """
        pulito = " ".join(str(text or "").split())[:OBSERVATION_MAX_CHARS]
        if not pulito:
            return None
        if self.persona.observations and self.persona.observations[-1].get("text") == pulito:
            return None
        osservazione = {"ts": _now(), "kind": str(kind or "self_observation"), "text": pulito}
        self.persona = replace(
            self.persona,
            observations=(self.persona.observations + (osservazione,))[-MAX_OBSERVATIONS:],
            version=self.persona.version + 1,
        )
        if persist:
            try:
                self.save()
            except Exception as e:
                # L'osservazione resta in memoria: si segnala, non si fa perdere
                # il lavoro dell'agente per un filesystem di sola lettura.
                self.problems.append(f"salvataggio osservazione fallito: {e}")
        return osservazione

    def system_block(self, user_text: str | None = None) -> str:
        return build_system_block(self.persona, should_disclose(user_text))

    def describe(self) -> dict:
        """Stato per l'operatore (`GET /persona`). Non contiene segreti."""
        p = self.persona
        return {
            "name": p.name, "kind": p.kind, "is_ai": p.is_ai, "purpose": p.purpose,
            "tone": list(p.tone), "values": list(p.values), "boundaries": list(p.boundaries),
            "capabilities": list(p.capabilities), "limitations": list(p.limitations),
            "version": p.version, "created_at": p.created_at, "updated_at": p.updated_at,
            "file": self.path,
            "observations": list(p.observations),
            "observation_count": len(p.observations),
            "max_observations": MAX_OBSERVATIONS,
            "disclosure_rules": [{"rule": nome, "reason": motivo}
                                 for nome, motivo, _ in DISCLOSURE_RULES],
            "problems": list(self.problems),
        }
