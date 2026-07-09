"""
connectors/github.py — GitHub REST API per HyperSpace-AGI v1.03
Dipendenze: requests (già presente in requirements.txt)

Env vars:
  GITHUB_TOKEN   Personal Access Token o GitHub App token
                 Scope: repo, issues (read/write)
                 https://github.com/settings/tokens
"""
from __future__ import annotations
import os
import requests
from .base import BaseConnector


class GitHubConnector(BaseConnector):
    name = "github"

    def __init__(self):
        super().__init__()
        self.token   = os.getenv("GITHUB_TOKEN", "")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.base = "https://api.github.com"

    @property
    def enabled(self) -> bool:
        return bool(self.token)

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

    def _search_issues(self, args: dict) -> str:
        repo  = args.get("repo", "")
        query = args.get("query", "")
        limit = min(int(args.get("limit", 10)), 30)
        r = requests.get(
            f"{self.base}/search/issues",
            headers=self.headers,
            params={"q": f"{query} repo:{repo}", "per_page": limit},
            timeout=10
        )
        data = r.json()
        if "items" not in data:
            return f"[github] Errore: {data.get('message', r.text[:200])}"
        lines = []
        for item in data["items"]:
            kind = "PR" if "pull_request" in item else "Issue"
            lines.append(f"[{kind} #{item['number']}] {item['title']} | {item['state']} | {item['html_url']}")
        return "\n".join(lines) if lines else "Nessun risultato."

    def _create_issue(self, args: dict) -> str:
        repo   = args.get("repo", "")
        title  = args.get("title", "")
        body   = args.get("body", "")
        labels = [l.strip() for l in args.get("labels", "").split(",") if l.strip()]
        if not repo or not title:
            return "[github] 'repo' e 'title' sono obbligatori."
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        r = requests.post(
            f"{self.base}/repos/{repo}/issues",
            headers=self.headers,
            json=payload,
            timeout=10
        )
        data = r.json()
        if r.status_code == 201:
            return f"[github] Issue creata: #{data['number']} — {data['html_url']}"
        return f"[github] Errore {r.status_code}: {data.get('message', r.text[:200])}"

    def _get_repo(self, args: dict) -> str:
        repo = args.get("repo", "")
        if not repo:
            return "[github] 'repo' è obbligatorio."
        r    = requests.get(f"{self.base}/repos/{repo}", headers=self.headers, timeout=8)
        data = r.json()
        if r.status_code != 200:
            return f"[github] Errore {r.status_code}: {data.get('message', r.text[:200])}"
        return (
            f"Repo: {data['full_name']}\n"
            f"Descrizione: {data.get('description') or '—'}\n"
            f"Lingua: {data.get('language') or '—'} | "
            f"Stars: {data['stargazers_count']} | Forks: {data['forks_count']}\n"
            f"Branch default: {data['default_branch']} | "
            f"Issue aperte: {data['open_issues_count']}\n"
            f"URL: {data['html_url']}"
        )

    def _list_commits(self, args: dict) -> str:
        repo   = args.get("repo", "")
        branch = args.get("branch", "main")
        limit  = min(int(args.get("limit", 10)), 50)
        if not repo:
            return "[github] 'repo' è obbligatorio."
        r    = requests.get(
            f"{self.base}/repos/{repo}/commits",
            headers=self.headers,
            params={"sha": branch, "per_page": limit},
            timeout=10
        )
        data = r.json()
        if isinstance(data, dict) and "message" in data:
            return f"[github] Errore: {data['message']}"
        lines = []
        for c in data:
            sha    = c["sha"][:7]
            msg    = c["commit"]["message"].split("\n")[0][:80]
            author = c["commit"]["author"]["name"]
            date   = c["commit"]["author"]["date"][:10]
            lines.append(f"{sha} {date} [{author}] {msg}")
        return "\n".join(lines) if lines else "Nessun commit trovato."

    def _add_comment(self, args: dict) -> str:
        repo   = args.get("repo", "")
        number = int(args.get("issue_number", 0))
        body   = args.get("body", "")
        if not repo or not number or not body:
            return "[github] 'repo', 'issue_number', 'body' sono obbligatori."
        r    = requests.post(
            f"{self.base}/repos/{repo}/issues/{number}/comments",
            headers=self.headers,
            json={"body": body},
            timeout=10
        )
        data = r.json()
        if r.status_code == 201:
            return f"[github] Commento aggiunto: {data['html_url']}"
        return f"[github] Errore {r.status_code}: {data.get('message', r.text[:200])}"
