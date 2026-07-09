import os
import requests
from .base import BaseConnector

class GitHubConnector(BaseConnector):
    name = "github"

    def __init__(self):
        super().__init__()
        self.token = os.getenv("GITHUB_TOKEN")
        self.headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github.v3+json"} if self.token else {}

    def get_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "github_search_issues",
                    "description": "Cerca issues o PR su un repository GitHub",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo": {"type": "string", "description": "owner/repo"},
                            "query": {"type": "string"}
                        },
                        "required": ["repo", "query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "github_create_issue",
                    "description": "Crea una nuova issue",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "repo": {"type": "string"},
                            "title": {"type": "string"},
                            "body": {"type": "string"}
                        },
                        "required": ["repo", "title"]
                    }
                }
            }
        ]

    def execute(self, tool_name: str, args: dict) -> str:
        if tool_name == "github_search_issues":
            repo = args.get("repo")
            q = args.get("query")
            r = requests.get(f"https://api.github.com/search/issues?q={q}+repo:{repo}", headers=self.headers, timeout=10)
            return str(r.json() if r.ok else r.text)
        elif tool_name == "github_create_issue":
            repo = args.get("repo")
            title = args.get("title")
            body = args.get("body", "")
            data = {"title": title, "body": body}
            r = requests.post(f"https://api.github.com/repos/{repo}/issues", json=data, headers=self.headers, timeout=10)
            return str(r.json() if r.ok else r.text)
        return None
