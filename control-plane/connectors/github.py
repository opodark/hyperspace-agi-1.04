# SPDX-License-Identifier: Apache-2.0
"""
connectors/github.py — GitHub REST API per HyperSpace-AGI
Dipendenze: requests (già presente in requirements.txt)

Env vars:
  GITHUB_TOKEN       Personal Access Token o GitHub App token
                     Scope: repo (issues e PR in lettura/scrittura)
                     https://github.com/settings/tokens
  GITHUB_TIMEOUT_S   Timeout per singola chiamata (default 10)
  GITHUB_RETRY_S     Attesa prima dell'unico ritentativo su 5xx/rete (default 1)

Cosa NON fa: non ritenta i 4xx. Un 401/403/404/422 non migliora con un secondo
tentativo — allungherebbe solo l'attesa prima dello stesso errore.
"""
from __future__ import annotations
import os
import time
from datetime import datetime, timezone
import requests
from .base import BaseConnector

# Limiti delle API GitHub: oltre questi valori `per_page`/`per_page` di ricerca
# vengono ignorati dal server, che risponde con un numero diverso da quello
# chiesto — e il modello crede di aver ricevuto tutto.
MAX_SEARCH_RESULTS = 30
MAX_PER_PAGE = 50


def _clamp(value, default: int, maximum: int) -> int:
    """Intero in [1, maximum]. Un `limit` fuori scala veniva passato com'era."""
    try:
        numero = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(numero, maximum))


def _http_error(response) -> str:
    """Messaggio leggibile per un errore HTTP di GitHub.

    "Errore 403" non dice niente all'operatore: non distingue uno scope mancante
    da un token scaduto o dalla quota API esaurita, che sono tre azioni diverse.
    """
    try:
        body = response.json()
    except ValueError:
        body = {}
    messaggio = str((body or {}).get("message")
                    or getattr(response, "text", "")[:200] or "").strip()
    status = response.status_code
    if status == 401:
        return ("[github] Token non valido o scaduto (HTTP 401): rigenera GITHUB_TOKEN "
                "(github.com/settings/tokens) e aggiornalo nella tab Setup.")
    if status == 403:
        if str(response.headers.get("x-ratelimit-remaining", "")) == "0":
            reset = str(response.headers.get("x-ratelimit-reset", ""))
            quando = ""
            if reset.isdigit():
                quando = " (reset alle " + datetime.fromtimestamp(
                    int(reset), timezone.utc).strftime("%H:%M:%SZ") + " UTC)"
            return f"[github] Quota API esaurita (HTTP 403){quando}: riprova più tardi."
        return ("[github] Permessi insufficienti (HTTP 403): al token manca lo scope 'repo' "
                f"o l'accesso a questo repository. Dettaglio: {messaggio}")
    if status == 404:
        return ("[github] Risorsa non trovata (HTTP 404): repository o issue inesistente, "
                "oppure token senza accesso a un repository privato.")
    if status == 422:
        return f"[github] Dati non validi (HTTP 422): {messaggio}"
    return f"[github] Errore HTTP {status}: {messaggio}"


class GitHubConnector(BaseConnector):
    name = "github"

    # Senza token non c'è nulla da chiamare: il connettore resta spento e i suoi
    # tool non vengono né pubblicati né eseguiti (vedi GET /connectors).
    REQUIRED_ENV = ("GITHUB_TOKEN",)

    # Letture: sempre esponibili. Scritture: solo con una allowlist esplicita
    # (CONNECTOR_READ_ONLY=false + CONNECTOR_WRITE_TOOLS), vedi
    # shared/connector_policy.py.
    READ_TOOLS = ("github_search_issues", "github_get_repo", "github_list_commits")
    WRITE_TOOLS = ("github_create_issue", "github_add_comment")

    def __init__(self):
        super().__init__()
        self.token   = os.getenv("GITHUB_TOKEN", "")
        self.base    = "https://api.github.com"
        self.timeout_s    = float(os.getenv("GITHUB_TIMEOUT_S", "10"))
        self.retry_wait_s = float(os.getenv("GITHUB_RETRY_S", "1"))
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            # GitHub richiede uno User-Agent esplicito.
            "User-Agent": "hyperspace-agi-control-plane",
        }

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "github_search_issues",
                    "description": "Cerca issue e pull request su GitHub.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo":  {"type": "string",  "description": "owner/repo (es. opodark/hyperspace-agi-1.02)."},
                            "query": {"type": "string",  "description": "Query di ricerca (es. 'bug is:open')."},
                            "limit": {"type": "integer", "default": 10}
                        },
                        "required": ["repo", "query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "github_create_issue",
                    "description": "Crea una nuova issue in un repository GitHub.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo":   {"type": "string", "description": "owner/repo"},
                            "title":  {"type": "string", "description": "Titolo issue."},
                            "body":   {"type": "string", "description": "Descrizione Markdown."},
                            "labels": {"type": "string", "description": "Labels separati da virgola (opzionale)."}
                        },
                        "required": ["repo", "title"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "github_get_repo",
                    "description": "Ottieni info su un repository GitHub: stars, forks, lingua, branch default, issue aperte.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo": {"type": "string", "description": "owner/repo"}
                        },
                        "required": ["repo"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "github_list_commits",
                    "description": "Elenca i commit recenti di un branch con SHA, autore, data e messaggio.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo":   {"type": "string",  "description": "owner/repo"},
                            "branch": {"type": "string",  "default": "main", "description": "Nome branch."},
                            "limit":  {"type": "integer", "default": 10}
                        },
                        "required": ["repo"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "github_add_comment",
                    "description": "Aggiunge un commento a una issue o pull request GitHub.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo":         {"type": "string",  "description": "owner/repo"},
                            "issue_number": {"type": "integer", "description": "Numero issue o PR."},
                            "body":         {"type": "string",  "description": "Testo commento (Markdown)."}
                        },
                        "required": ["repo", "issue_number", "body"]
                    }
                }
            },
        ]

    def execute(self, tool_name: str, args: dict) -> str | None:
        dispatch = {
            "github_search_issues": self._search_issues,
            "github_create_issue":  self._create_issue,
            "github_get_repo":      self._get_repo,
            "github_list_commits":  self._list_commits,
            "github_add_comment":   self._add_comment,
        }
        fn = dispatch.get(tool_name)
        return fn(args) if fn else None

    def _request(self, method: str, path: str, *, params=None, payload=None):
        """Chiamata GitHub con UN ritentativo sugli errori transitori.

        Tre regole, tutte deliberate:
          - non si ritentano i 4xx: sono definitivi, un secondo tentativo non
            cambia l'esito e raddoppia solo l'attesa prima dello stesso errore;
          - non si ritentano i metodi NON idempotenti (POST): se la risposta si
            perde dopo che il server ha già creato la issue, ritentare ne crea
            una seconda. Meglio un errore da rilanciare a mano che un doppione
            silenzioso in un repository o in una casella di posta;
          - si ritentano una volta sola rete e 5xx, con attesa breve.
        """
        url = f"{self.base}{path}"
        idempotente = method.upper() in ("GET", "HEAD")
        tentativi = (1, 2) if idempotente else (1,)
        ultimo = "errore sconosciuto"
        for tentativo in tentativi:
            try:
                r = requests.request(method, url, headers=self.headers, params=params,
                                     json=payload, timeout=self.timeout_s)
            except requests.RequestException as e:
                ultimo = f"rete non raggiungibile ({type(e).__name__}: {str(e)[:80]})"
            else:
                if r.status_code < 400:
                    try:
                        return r.json(), None
                    except ValueError:
                        return None, f"[github] Risposta non JSON (HTTP {r.status_code})."
                if r.status_code < 500:
                    return None, _http_error(r)
                ultimo = f"HTTP {r.status_code} dal server GitHub"
            if tentativo != tentativi[-1]:
                time.sleep(self.retry_wait_s)
        etichetta = "dopo due tentativi" if idempotente else "senza ritentare (POST non idempotente)"
        return None, f"[github] Errore {etichetta}: {ultimo}"

    def _search_issues(self, args: dict) -> str:
        repo  = str(args.get("repo", "")).strip()
        query = str(args.get("query", "")).strip()
        if not repo or not query:
            return "[github] 'repo' e 'query' sono obbligatori."
        limit = _clamp(args.get("limit", 10), 10, MAX_SEARCH_RESULTS)
        data, errore = self._request("GET", "/search/issues",
                                     params={"q": f"{query} repo:{repo}", "per_page": limit})
        if errore:
            return errore
        items = (data or {}).get("items")
        if items is None:
            return "[github] Risposta inattesa dalla ricerca: manca il campo 'items'."
        lines = []
        for item in items:
            kind = "PR" if "pull_request" in item else "Issue"
            lines.append(f"[{kind} #{item['number']}] {item['title']} | {item['state']} | {item['html_url']}")
        return "\n".join(lines) if lines else "Nessun risultato."

    def _create_issue(self, args: dict) -> str:
        repo   = str(args.get("repo", "")).strip()
        title  = str(args.get("title", "")).strip()
        body   = str(args.get("body", ""))
        labels = [l.strip() for l in str(args.get("labels", "")).split(",") if l.strip()]
        if not repo or not title:
            return "[github] 'repo' e 'title' sono obbligatori."
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        data, errore = self._request("POST", f"/repos/{repo}/issues", payload=payload)
        if errore:
            return errore
        return (f"[github] Issue creata: #{data.get('number', '?')} — "
                f"{data.get('html_url', '(url non disponibile)')}")

    def _get_repo(self, args: dict) -> str:
        repo = str(args.get("repo", "")).strip()
        if not repo:
            return "[github] 'repo' è obbligatorio."
        data, errore = self._request("GET", f"/repos/{repo}")
        if errore:
            return errore
        return (
            f"Repo: {data.get('full_name', repo)}\n"
            f"Descrizione: {data.get('description') or '—'}\n"
            f"Lingua: {data.get('language') or '—'} | "
            f"Stars: {data.get('stargazers_count', 0)} | Forks: {data.get('forks_count', 0)}\n"
            f"Branch default: {data.get('default_branch', '—')} | "
            f"Issue aperte: {data.get('open_issues_count', 0)}\n"
            f"URL: {data.get('html_url', '')}"
        )

    def _list_commits(self, args: dict) -> str:
        repo   = str(args.get("repo", "")).strip()
        branch = str(args.get("branch", "main") or "main").strip()
        limit  = _clamp(args.get("limit", 10), 10, MAX_PER_PAGE)
        if not repo:
            return "[github] 'repo' è obbligatorio."
        data, errore = self._request("GET", f"/repos/{repo}/commits",
                                     params={"sha": branch, "per_page": limit})
        if errore:
            return errore
        if not isinstance(data, list):
            return "[github] Risposta inattesa: elenco commit non disponibile."
        lines = []
        for c in data:
            # Nei commit di merge (o senza autore tracciato) i campi possono
            # mancare: un KeyError qui diventerebbe un errore generico, e la
            # serie di commit si perderebbe per un solo elemento anomalo.
            commit = c.get("commit") or {}
            autore = (commit.get("author") or {})
            msg    = str(commit.get("message") or "").split("\n")[0][:80]
            lines.append(f"{str(c.get('sha', ''))[:7]} {str(autore.get('date') or '')[:10]} "
                         f"[{autore.get('name') or '?'}] {msg}")
        return "\n".join(lines) if lines else "Nessun commit trovato."

    def _add_comment(self, args: dict) -> str:
        repo = str(args.get("repo", "")).strip()
        body = str(args.get("body", ""))
        try:
            number = int(args.get("issue_number", 0))
        except (TypeError, ValueError):
            number = 0
        if not repo or not number or not body:
            return "[github] 'repo', 'issue_number', 'body' sono obbligatori."
        data, errore = self._request("POST", f"/repos/{repo}/issues/{number}/comments",
                                     payload={"body": body})
        if errore:
            return errore
        return f"[github] Commento aggiunto: {data.get('html_url', '(url non disponibile)')}"

