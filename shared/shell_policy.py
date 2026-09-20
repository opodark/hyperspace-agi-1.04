# SPDX-License-Identifier: Apache-2.0
"""Classificazione del rischio di un comando, e chi deve confermarlo.

Problema che risolve: finche' `shell_run` esegue "solo argv", `git status` e
`git push --force` sono la stessa cosa per il server. L'allowlist di eseguibili
non distingue le due — `git` e' `git` — quindi la distinzione va fatta sul
COMANDO, con regole che hanno un nome.

Qui vive solo la POLICY: pura e testabile come shared/connector_policy.py,
senza dipendenze e senza riferimenti a control-plane/main.py. Chi esegue
(hostctl/agent.py) chiama `check()`, applica il verdetto e mette la decisione
nei log; il control-plane la usa per CHIEDERE la conferma invece di eseguire.

Scelte, in ordine di importanza:
- le regole hanno un nome e il perche' sta nel verdetto: "perche' mi chiedi di
  confermare?" deve essere una frase, non un punteggio;
- il default e' conservativo ma non paralizzante: leggere e scrivere normale
  non chiedono nulla, distruggere chiede una conferma esplicita;
- `SHELL_BLOCK_RISK` esiste per il caso opposto: un runtime non presidiato che
  non deve poter distruggere nemmeno volendo;
- non e' un confine di sicurezza, e non lo finge. Una conferma e' una decisione,
  non una barriera: un comando distruttivo che non compare in queste regole
  passa. Le regole coprono cio' che si fa davvero, non pretendono di essere
  complete — ed e' per questo che l'allowlist resta la parete vera.
"""
from __future__ import annotations

import os

CONFIRM_VAR = "SHELL_CONFIRM_RISK"
BLOCK_VAR = "SHELL_BLOCK_RISK"
RISK_READ = "read"
RISK_WRITE = "write"
RISK_DESTRUCTIVE = "destructive"

# Soglie: `none` significa "non chiedere mai" / "non bloccare mai".
_LEVELS = {"none": 99, RISK_READ: 0, RISK_WRITE: 1, RISK_DESTRUCTIVE: 2}
_RANK = {RISK_READ: 0, RISK_WRITE: 1, RISK_DESTRUCTIVE: 2}


def _is_flag(item: str) -> bool:
    """True se l'argomento e' un flag: `-r`, `--force`, `-rf`, `/s` (Windows).

    La distinzione fra un flag Windows e un percorso assoluto POSIX (`/usr/bin`)
    e' euristica — corto e senza altri separatori — ed e' dichiarata qui invece
    che sparsa nelle regole.
    """
    if item.startswith("-") and item != "-":
        return True
    return (item.startswith("/") and 1 < len(item) <= 4
            and "/" not in item[1:] and "\\" not in item)


def _flags(argv) -> set:
    """Gli argomenti flag, compatti e minuscoli: `-rf` -> `-rf`, `-r`, `-f`."""
    flags = set()
    for item in list(argv)[1:]:
        if not _is_flag(item):
            continue
        flags.add(item.lower())
        if item.startswith("--"):
            continue
        if not item.startswith("/"):
            flags.update("-" + char.lower() for char in item[1:])
    return flags


def _positional(argv) -> list:
    return [item for item in list(argv)[1:] if not _is_flag(item)]


def _version_only(argv) -> bool:
    rest = list(argv)[1:]
    return bool(rest) and all(item in {"--version", "-V", "version"} for item in rest)


def _rule_rm(executable: str, argv: list) -> str | None:
    flags = _flags(argv)
    if executable in {"rm", "rmdir"}:
        if executable == "rmdir" or flags & {"-r", "--recursive"}:
            return "rm_ricorsivo"
        if any(item in {"*", "/", "~", ".", "..", "./*", "/*"} for item in _positional(argv)):
            return "rm_globale"
    if executable in {"del", "erase"} and "/s" in flags:
        return "del_ricorsivo"
    if executable in {"remove-item", "ri"} and {"-recurse", "-force"} & flags:
        return "remove_item_ricorsivo"
    return None


def _rule_git(executable: str, argv: list) -> str | None:
    if executable != "git":
        return None
    flags = _flags(argv)
    rest = _positional(argv)
    sub = rest[0] if rest else ""
    if sub == "push":
        return "git_push"
    if sub == "reset" and "--hard" in flags:
        return "git_reset_hard"
    if sub == "clean" and flags & {"-f", "--force"}:
        return "git_clean"
    if sub in {"filter-branch", "filter-repo"}:
        return "git_riscrittura_storia"
    if sub == "branch" and flags & {"-d"}:
        return "git_branch_delete"
    if sub == "stash" and len(rest) > 1 and rest[1] in {"drop", "clear"}:
        return "git_stash_drop"
    if sub == "checkout" and "--" in argv and "." in argv:
        return "git_checkout_scarta_modifiche"
    return None


def _rule_disco(executable: str, argv: list) -> str | None:
    if executable in {"mkfs", "fdisk", "diskpart", "format", "dd", "wipefs"} \
            or executable.startswith("mkfs."):
        return "operazione_su_disco"
    return None


def _rule_publish(executable: str, argv: list) -> str | None:
    rest = _positional(argv)
    sub = rest[0] if rest else ""
    if executable == "npm" and sub in {"publish", "unpublish"}:
        return "pubblicazione_pacchetto"
    if executable in {"twine", "cargo", "poetry"} and sub in {"upload", "publish", "release"}:
        return "pubblicazione_pacchetto"
    return None


def _rule_docker(executable: str, argv: list) -> str | None:
    if executable != "docker":
        return None
    rest = _positional(argv)
    sub = rest[0] if rest else ""
    if sub in {"volume", "network", "image", "container"} and "prune" in rest[1:2]:
        return "docker_prune"
    if sub in {"prune"} or (sub == "system" and "prune" in rest[1:2]):
        return "docker_prune"
    if sub in {"rm", "rmi", "kill"}:
        return "docker_distruttivo"
    return None


def _rule_permessi(executable: str, argv: list) -> str | None:
    flags = _flags(argv)
    if executable in {"chmod", "chown", "chgrp", "icacls", "takeown"} \
            and flags & {"-r", "--recursive", "/t"}:
        return "permessi_ricorsivi"
    return None


def _rule_processi(executable: str, argv: list) -> str | None:
    if executable in {"kill", "killall", "pkill", "taskkill", "stop-process"}:
        return "terminazione_processi"
    return None


def _rule_sistema(executable: str, argv: list) -> str | None:
    if executable in {"shutdown", "reboot", "poweroff", "halt"}:
        return "spegnimento"
    return None


def _rule_servizi(executable: str, argv: list) -> str | None:
    rest = _positional(argv)
    sub = rest[0] if rest else ""
    if executable == "systemctl" and sub in {"stop", "restart", "disable", "mask"}:
        return "servizio_di_sistema"
    if executable in {"service", "net", "sc"} and sub in {"stop", "restart"}:
        return "servizio_di_sistema"
    return None


# L'ordine conta: il primo che riconosce qualcosa decide il nome della regola.
DESTRUCTIVE_RULES = (
    _rule_rm, _rule_git, _rule_disco, _rule_publish, _rule_docker,
    _rule_permessi, _rule_processi, _rule_sistema, _rule_servizi,
)

_READ_EXECUTABLES = frozenset({
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "find", "grep", "rg", "wc",
    "sort", "uniq", "stat", "file", "tree", "du", "df", "free", "ps", "tasklist",
    "whoami", "uname", "hostname", "date", "echo", "where", "which", "git", "env",
})
_GIT_READ_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "rev-parse", "remote", "describe",
    "ls-files", "ls-tree", "blame", "shortlog", "stash", "cat-file", "config", "tag",
})
# Il VOCABOLARIO delle regole: ogni nome che questo modulo puo' produrre. E'
# dichiarato invece che dedotto perche' una funzione puo' restituire piu' nomi
# (`_rule_git` ne ha sei): il test verifica che ogni nome distruttivo sia
# raggiungibile da un comando vero e che nessuna regola ne produca fuori elenco.
DESTRUCTIVE_NAMES = frozenset({
    "rm_ricorsivo", "rm_globale", "del_ricorsivo", "remove_item_ricorsivo",
    "git_push", "git_reset_hard", "git_clean", "git_riscrittura_storia",
    "git_branch_delete", "git_stash_drop", "git_checkout_scarta_modifiche",
    "operazione_su_disco", "pubblicazione_pacchetto", "docker_prune",
    "docker_distruttivo", "permessi_ricorsivi", "terminazione_processi",
    "spegnimento", "servizio_di_sistema",
})
LEVEL_NAMES = frozenset({"versione", "comando_di_lettura", "git_non_lettura",
                         "comando_di_scrittura"})
RULE_NAMES = DESTRUCTIVE_NAMES | LEVEL_NAMES


class Verdict:
    """Cosa e' un comando, perche', e se puo' partire adesso."""

    __slots__ = ("risk", "rules", "requires_confirmation", "blocked", "allowed", "error")

    def __init__(self, risk: str, rules=(), *, requires_confirmation=False,
                 blocked=False, allowed=True, error: str = ""):
        self.risk = risk
        self.rules = list(rules)
        self.requires_confirmation = bool(requires_confirmation)
        self.blocked = bool(blocked)
        self.allowed = bool(allowed)
        self.error = error

    def to_dict(self) -> dict:
        return {"risk": self.risk, "rules": list(self.rules),
                "requires_confirmation": self.requires_confirmation,
                "blocked": self.blocked, "allowed": self.allowed}

    def __repr__(self) -> str:
        return (f"Verdict(risk={self.risk!r}, rules={self.rules!r}, "
                f"confirm={self.requires_confirmation}, blocked={self.blocked}, "
                f"allowed={self.allowed})")


class ShellPolicy:
    """A che livello un comando chiede conferma, e a che livello viene bloccato."""

    def __init__(self, *, confirm_at: str = RISK_DESTRUCTIVE, block_at: str = "none",
                 problems=None):
        self.confirm_at = confirm_at if confirm_at in _LEVELS else RISK_DESTRUCTIVE
        self.block_at = block_at if block_at in _LEVELS else "none"
        self.problems = list(problems or [])

    @classmethod
    def from_env(cls, environ=None) -> "ShellPolicy":
        env = os.environ if environ is None else environ
        problems = []
        confirm_at = str(env.get(CONFIRM_VAR, RISK_DESTRUCTIVE)).strip().lower()
        block_at = str(env.get(BLOCK_VAR, "none")).strip().lower()
        for var, value in ((CONFIRM_VAR, confirm_at), (BLOCK_VAR, block_at)):
            if value not in _LEVELS:
                problems.append(f"{var}: valore {value!r} non riconosciuto "
                                f"({', '.join(sorted(_LEVELS))}); uso il default")
        return cls(confirm_at=confirm_at, block_at=block_at, problems=problems)

    @property
    def confirm_threshold(self) -> int:
        return _LEVELS[self.confirm_at]

    @property
    def block_threshold(self) -> int:
        return _LEVELS[self.block_at]

    def classify(self, argv) -> Verdict:
        """Il rischio di un comando: `destructive`, `write` o `read`.

        Il default, quando nessuna regola legge, e' `write`: eseguire un
        programma puo' sempre scrivere qualcosa, e fingere che sia `read`
        sarebbe la bugia piu' comoda di questo modulo.
        """
        argv = [str(item) for item in (argv or [])]
        if not argv:
            raise ValueError("argv vuoto: nessun comando da classificare")
        executable = argv[0]
        name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
        for suffix in (".exe", ".cmd", ".bat", ".com"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        if _version_only(argv):
            return Verdict(RISK_READ, ["versione"])
        for rule in DESTRUCTIVE_RULES:
            matched = rule(name, argv)
            if matched:
                return Verdict(RISK_DESTRUCTIVE, [matched])
        if name in _READ_EXECUTABLES:
            rest = _positional(argv)
            if name == "git" and rest and rest[0] not in _GIT_READ_SUBCOMMANDS:
                return Verdict(RISK_WRITE, ["git_non_lettura"])
            return Verdict(RISK_READ, ["comando_di_lettura"])
        return Verdict(RISK_WRITE, ["comando_di_scrittura"])

    def check(self, argv, *, confirm: bool = False) -> Verdict:
        """Il verdetto, con la decisione: si puo' partire?"""
        verdict = self.classify(argv)
        rank = _RANK[verdict.risk]
        if rank >= self.block_threshold:
            verdict.blocked = True
            verdict.allowed = False
            verdict.error = (f"comando bloccato da {BLOCK_VAR}={self.block_at}: "
                             f"{', '.join(verdict.rules)}")
            return verdict
        if rank >= self.confirm_threshold:
            verdict.requires_confirmation = True
            if not confirm:
                verdict.allowed = False
                verdict.error = (f"richiede conferma esplicita ({', '.join(verdict.rules)}): "
                                 "decidi, poi riprova con confirm=true")
                return verdict
        return verdict

    def describe(self) -> dict:
        """Senza segreti: per il pannello, i log e la dashboard."""
        return {
            "confirm_at": self.confirm_at,
            "block_at": self.block_at,
            "rules": sorted(RULE_NAMES),
            "problems": list(self.problems),
        }


