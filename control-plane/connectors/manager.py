# SPDX-License-Identifier: Apache-2.0
"""
ConnectorManager — scoperta automatica e dispatch dei connettori esterni.

Responsabilità:
  - trovare ogni connettore nella cartella (pkgutil), istanziarlo e tenere solo
    quelli con `enabled=True` (credenziali presenti, override env rispettato);
  - esporre il catalogo dei loro tool (get_all_tools) e dispatchare (execute)
    verso il connettore che riconosce il nome;
  - dire PERCHÉ un connettore è spento (describe): un tool che non compare
    senza una ragione è illeggibile per l'operatore. `GET /connectors` usa
    describe(), che non contiene mai valori di segreti — solo nomi di env var
    mancanti.

`reload()` esiste perché le credenziali si possono impostare a caldo dalla tab
Setup del control-plane: i connettori leggono l'env nel proprio __init__, quindi
dopo un salvataggio vanno ricostruiti (e il catalogo dei tool riallineato da
`_sync_connector_tools()` in main.py). Lo stesso vale per la policy read/write.

La POLICY read/write (shared/connector_policy.py) si applica qui, in un punto
solo, su due fronti: i tool di scrittura non autorizzati non entrano nel
catalogo (get_all_tools) e non sono eseguibili (execute). Fail-closed: default
read-only, e un connettore la cui classificazione READ_TOOLS/WRITE_TOOLS non
combacia con get_tools() resta fuori dal catalogo invece di esporre un tool di
cui non si sa dire la natura.
"""
import importlib
import pkgutil
import os
import threading
import time
from .base import BaseConnector
from shared.connector_policy import ConnectorPolicy, WRITE_TOOLS_VAR

# Moduli del package che NON sono connettori (infrastruttura).
_NOT_CONNECTORS = ("base", "manager")

# Circuit breaker. Il retry dei singoli connettori è per-chiamata: senza questo,
# un servizio esterno giù (o un token scaduto) costerebbe il timeout pieno a
# OGNI chiamata del modello, per sempre. Dopo N errori consecutivi il
# connettore va "in pausa" per un cooldown, e le chiamate successive falliscono
# subito dicendo quando verrà ritentato.
FAILURE_THRESHOLD_VAR = "CONNECTOR_FAILURE_THRESHOLD"
COOLDOWN_VAR = "CONNECTOR_COOLDOWN_S"
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_S = 60.0


class ConnectorManager:
    # Un problema di esecuzione non è un problema di configurazione: se ne
    # tengono gli ultimi, per non far crescere la lista all'infinito in un
    # processo che gira per settimane.
    _MAX_PROBLEMS = 20

    def __init__(self, policy=None, on_event=None):
        self.connectors: list[BaseConnector] = []
        self.problems: list[str] = []
        self._disabled: list[dict] = []
        self._declared_write: dict[str, list[str]] = {}
        self._failures: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        # Policy iniettata (test) o dall'ambiente. Se è iniettata, reload() non
        # la sostituisce: chi l'ha passata vuole controllarla.
        self._injected_policy = policy
        self.policy: ConnectorPolicy = policy or ConnectorPolicy.from_env()
        self.failure_threshold = DEFAULT_FAILURE_THRESHOLD
        self.cooldown_s = DEFAULT_COOLDOWN_S
        self._read_breaker_limits()
        # Callback di osservabilità (main.py la collega a push_log): il manager
        # resta importabile senza control-plane, ma gli errori dei connettori
        # non spariscono più nei log di chi chiama.
        self._on_event = on_event
        # reload() riassegna self.connectors mentre il tool loop può leggere il
        # catalogo: il lock tiene una lettura coerente di tutto lo stato
        # (connectors + _disabled + problems) durante il reload. RLock e non
        # Lock perché describe() tiene il lock mentre registra i problemi che
        # trova (stessa cosa che fa _register_problem).
        self._lock = threading.RLock()
        self._load_connectors()

    def _read_breaker_limits(self) -> None:
        """Legge le soglie del breaker dall'ambiente (riviste a ogni reload)."""
        try:
            self.failure_threshold = max(1, int(os.getenv(FAILURE_THRESHOLD_VAR,
                                                          str(DEFAULT_FAILURE_THRESHOLD))))
        except (TypeError, ValueError):
            self.failure_threshold = DEFAULT_FAILURE_THRESHOLD
        try:
            self.cooldown_s = max(1.0, float(os.getenv(COOLDOWN_VAR, str(DEFAULT_COOLDOWN_S))))
        except (TypeError, ValueError):
            self.cooldown_s = DEFAULT_COOLDOWN_S

    def _cooldown_remaining(self, name: str) -> float:
        return max(0.0, self._cooldown_until.get(name, 0.0) - time.time())

    def _note_success(self, name: str) -> None:
        """Un successo azzera il contatore (e chiude il breaker se era aperto)."""
        with self._lock:
            self._failures.pop(name, None)
            riaperto = self._cooldown_until.pop(name, None) is not None
        if riaperto:
            self._emit('info', f"Connettore {name}: tornato disponibile",
                       "circuit breaker richiuso dopo un esito positivo")

    def _note_failure(self, name: str, tool_name: str, dettaglio: str) -> None:
        """Conta l'errore e, oltre soglia, mette il connettore in pausa."""
        with self._lock:
            self._failures[name] = self._failures.get(name, 0) + 1
            colpi = self._failures[name]
            apri = colpi >= self.failure_threshold
            if apri:
                self._cooldown_until[name] = time.time() + self.cooldown_s
        if apri:
            # Evento (non solo problema): l'apertura del breaker è un fatto
            # operativo, e va visto nei log senza doverli filtrare per testo.
            self._emit('error',
                       f"Connettore {name} in pausa per {int(self.cooldown_s)}s",
                       f"{colpi} errori consecutivi, ultimo su {tool_name}: {dettaglio}")


    def _register_problem(self, message: str) -> None:
        """Annota un problema a runtime, tenendo solo gli ultimi `_MAX_PROBLEMS`."""
        with self._lock:
            self.problems.append(message)
            if len(self.problems) > self._MAX_PROBLEMS:
                del self.problems[:len(self.problems) - self._MAX_PROBLEMS]

    def _emit(self, kind: str, summary: str, detail: str = "") -> None:
        """Notifica l'osservabilità esterna, se è stata collegata.

        Un errore nel callback non deve far fallire un tool: l'osservabilità è
        utile solo se non può rompere ciò che osserva.
        """
        if self._on_event is None:
            return
        try:
            self._on_event(kind, summary, detail)
        except Exception:
            pass


    def _connector_classes(self):
        """Classi connettore trovate nel package, con nome del modulo.

        Un modulo che non si importa non fa fallire il boot: finisce in
        self.problems, così /connectors può dire che *quel* modulo è rotto
        (es. dipendenza mancante) invece di mostrare un catalogo più corto e
        muto.
        """
        package = __package__
        for _, name, _ in pkgutil.iter_modules([os.path.dirname(__file__)]):
            if name.startswith("_") or name in _NOT_CONNECTORS:
                continue
            try:
                module = importlib.import_module(f".{name}", package=package)
            except Exception as e:
                self.problems.append(f"{name}: import fallito ({e})")
                continue
            for attr_name in sorted(dir(module)):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, BaseConnector)
                        and attr is not BaseConnector):
                    yield name, attr

    def _load_connectors(self):
        self.problems = []
        loaded: list[BaseConnector] = []
        disabled: list[dict] = []
        problems: list[str] = []
        declared_write: dict[str, list[str]] = {}
        # Stato del breaker azzerato: un reload è un'interruzione delle prove
        # (e anche il modo esplicito dell'operatore per togliere una pausa).
        self._failures = {}
        self._cooldown_until = {}
        for _, cls in self._connector_classes():
            try:
                connector = cls()
            except Exception as e:
                problems.append(f"{cls.__name__}: init fallito ({e})")
                continue
            declared_write[connector.name] = list(connector.WRITE_TOOLS)
            try:
                published = [t["function"]["name"] for t in connector.get_tools()
                             if t.get("function", {}).get("name")]
                malformati = connector.classification_problems(published)
            except Exception as e:
                malformati = [f"{connector.name}: get_tools fallito ({e})"]
            if not connector.enabled:
                missing = connector.missing_env()
                forced_off = os.getenv(
                    f"CONNECTOR_{connector.name.upper()}_ENABLED", "").strip().lower() == "false"
                if forced_off:
                    # L'override manuale vince su tutto: è una scelta
                    # dell'operatore, non una configurazione incompleta.
                    reason = f"disabilitato da CONNECTOR_{connector.name.upper()}_ENABLED=false"
                elif missing:
                    reason = "env mancanti: " + ", ".join(missing)
                else:
                    reason = "credenziali non disponibili (vedi is_available())"
                disabled.append({"name": connector.name, "reason": reason,
                                 "missing_env": missing,
                                 "write_tools": declared_write[connector.name]})
                print(f"[ConnectorManager] Skipped: {connector.name} ({reason})")
                # Un connettore spento con la classificazione rotta si scopre
                # ADESSO, non al primo salvataggio delle credenziali.
                problems.extend(malformati)
                continue
            if malformati:
                # Fail-closed: senza una classificazione affidabile i suoi tool
                # non entrano nel catalogo (ConnectorPolicy non saprebbe cosa
                # può scrivere).
                disabled.append({"name": connector.name,
                                 "reason": "classificazione tool incompleta: "
                                           + "; ".join(malformati),
                                 "missing_env": [],
                                 "write_tools": declared_write[connector.name]})
                problems.extend(malformati)
                print(f"[ConnectorManager] Skipped: {connector.name} "
                      f"(classificazione tool incompleta)")
                continue
            loaded.append(connector)
            print(f"[ConnectorManager] Loaded: {connector.name}")
        problems.extend(self.policy.check_against(declared_write))
        with self._lock:
            self.connectors = loaded
            self._disabled = disabled
            self._declared_write = declared_write
            self.problems = list(self.problems) + problems

    def reload(self) -> dict:
        """Ricostruisce i connettori leggendo di nuovo l'ambiente.

        Serve dopo un salvataggio delle credenziali (tab Setup -> POST
        /config/env): senza questo, token e credenziali letti nell'__init__
        restano quelli vecchi fino al riavvio del container. Vale anche per la
        policy read/write, che vive nell'env come le credenziali.
        """
        if self._injected_policy is None:
            self.policy = ConnectorPolicy.from_env()
        self._read_breaker_limits()
        self._load_connectors()
        return self.describe()


    def describe(self) -> dict:
        """Stato per l'operatore: attivi, spenti, PERCHÉ, e cosa blocca la policy.

        NESSUN valore di env var: solo nomi di tool e nomi di env var mancanti
        (stessa regola di McpAuthPolicy.describe(), che non espone mai i token).
        `write_tools_blocked` è il dato che manca di solito: dice esattamente
        cosa scrivere in CONNECTOR_WRITE_TOOLS per abilitare una scrittura.
        """
        with self._lock:
            rows = []
            for conn in list(self.connectors):
                names, kinds = [], {}
                try:
                    for tool in conn.get_tools():
                        name = ((tool or {}).get("function") or {}).get("name")
                        if name:
                            names.append(name)
                            kinds[name] = conn.tool_kind(name)
                except Exception as e:
                    # Un get_tools rotto non deve azzerare la diagnostica: lo si
                    # segnala e si prosegue con gli altri connettori.
                    self._register_problem(f"{conn.name}: get_tools fallito ({e})")
                rows.append({
                    "name": conn.name,
                    "tools": names,
                    "read_tools": [n for n in names if kinds.get(n) == "read"],
                    "write_tools": [n for n in names if kinds.get(n) == "write"],
                    "failures": self._failures.get(conn.name, 0),
                    "cooldown_remaining_s": round(self._cooldown_remaining(conn.name), 1),
                })
            disabled = [dict(d) for d in self._disabled]
            problems = list(self.problems)
            policy = self.policy.describe()
        rows = self.policy.annotate(rows)
        for problem in policy["problems"]:
            if problem not in problems:
                problems.append(problem)
        return {
            "connectors": rows,
            "disabled": disabled,
            "policy": policy,
            "breaker": {"failure_threshold": self.failure_threshold,
                        "cooldown_s": self.cooldown_s},
            "problems": problems,
            "tool_count": sum(len(r["read_tools"]) + len(r["write_tools_exposed"])
                              for r in rows),
        }

    def get_all_tools(self) -> list:
        """Catalogo ESPOSTO: i tool dei connettori attivi, già filtrati.

        Un tool di scrittura non autorizzato non compare qui, quindi il modello
        non può nemmeno chiederlo: la policy è applicata all'esposizione, non
        solo all'esecuzione.
        """
        tools = []
        for conn in list(self.connectors):
            tools.extend(self.policy.filter_tools(
                conn.name, conn.get_tools(),
                write_of=lambda name, c=conn: c.tool_kind(name) == "write"))
        return tools

    def execute(self, tool_name: str, args: dict) -> str:
        """Esegue il tool sul connettore che lo espone.

        Tre esiti distinti, e la distinzione è il punto:
          - risultato: il connettore l'ha eseguito;
          - blocco della policy / classificazione assente: NON si esegue nulla
            e lo si dice (fail-closed);
          - il connettore che possiede il tool è esploso: errore attribuito al
            connettore, non confuso con "nessuno gestisce questo tool".
        """
        errori: list[str] = []
        for conn in list(self.connectors):
            names = [((t or {}).get("function") or {}).get("name") for t in conn.get_tools()]
            if tool_name not in names:
                continue
            # Una scrittura non autorizzata non deve essere ESEGUITA: il filtro
            # del catalogo non basta (un client può chiamare un nome a memoria,
            # o averlo visto prima che la policy cambiasse).
            kind = conn.tool_kind(tool_name)
            if kind is None:
                return (f"Tool '{tool_name}' non eseguibile: il connettore "
                        f"'{conn.name}' non lo classifica come read/write "
                        f"(fail-closed, vedi docs/connectors.md).")
            if not self.policy.write_allowed(conn.name, tool_name, write=(kind == "write")):
                return (f"Tool '{tool_name}' bloccato dalla policy: è un tool di SCRITTURA "
                        f"del connettore '{conn.name}' non abilitato. Servono "
                        f"{WRITE_TOOLS_VAR}=\"{conn.name}={tool_name}\" e "
                        f"CONNECTOR_READ_ONLY=false (vedi docs/connectors.md).")
            # Breaker aperto: non si paga il timeout per la terza volta di fila.
            # Il tool resta nel catalogo (sparire sarebbe un secondo mistero) e
            # la risposta dice quando verrà ritentato.
            attesa = self._cooldown_remaining(conn.name)
            if attesa > 0:
                return (f"Tool '{tool_name}': il connettore '{conn.name}' è in pausa dopo "
                        f"{self.failure_threshold} errori consecutivi — riprova fra "
                        f"{int(attesa) + 1}s. L'ultimo errore è nei log del control-plane.")
            try:
                result = conn.execute(tool_name, args)
            except Exception as e:
                # Il connettore ESISTE e ha fallito: questo non è "tool non
                # gestito" — era l'ambiguità che rendeva invisibile un
                # connettore rotto. Il dettaglio va al chiamante (il modello può
                # reagire) e all'operatore (problems + eventi nei log).
                dettaglio = f"{type(e).__name__}: {str(e)[:160]}"
                errori.append(f"{conn.name} ({dettaglio})")
                self._register_problem(f"esecuzione di {tool_name} su {conn.name} fallita: {dettaglio}")
                self._note_failure(conn.name, tool_name, dettaglio)
                self._emit('error', f'Connettore {conn.name}: {tool_name} fallito', dettaglio)
                continue
            if result is not None:
                self._note_success(conn.name)
                return result
        if errori:
            return (f"Tool '{tool_name}': il connettore che lo espone ha fallito — "
                    + "; ".join(errori) + ". Non è un tool inesistente: il motivo è nei log "
                    "del control-plane (type=system).")
        return f"Tool '{tool_name}' non gestito da nessun connector attivo."


