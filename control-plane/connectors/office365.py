"""
connectors/office365.py — Microsoft 365 / Graph API per HyperSpace-AGI v1.03
Libreria: O365>=2.0.35  (pip install O365)

Env vars (.env):
  MS_CLIENT_ID       App registration client_id
  MS_CLIENT_SECRET   App registration client_secret
  MS_TENANT_ID       Tenant ID (default: "common")

Auth: OAuth2 client credentials (server-to-server / daemon app).
Permissions: Mail.Read Mail.Send Calendars.ReadWrite Files.ReadWrite.All

App registration Azure:
  https://portal.azure.com → Entra ID → App registrations → New
  → Authentication: no redirect URI (daemon)
  → Certificates & secrets: new client secret
  → API permissions: Microsoft Graph → Application permissions (non Delegated)
"""
from __future__ import annotations
import os
from .base import BaseConnector


def _get_account():
    from O365 import Account, FileSystemTokenBackend
    credentials = (os.environ["MS_CLIENT_ID"], os.environ["MS_CLIENT_SECRET"])
    tenant_id   = os.getenv("MS_TENANT_ID", "common")
    backend     = FileSystemTokenBackend(token_path="/tmp", token_filename="o365_token.json")
    account = Account(
        credentials,
        auth_flow_type="credentials",
        tenant_id=tenant_id,
        token_backend=backend,
    )
    if not account.is_authenticated:
        account.authenticate()
    return account


class Office365Connector(BaseConnector):
    name = "office365"

    @property
    def enabled(self) -> bool:
        return bool(os.getenv("MS_CLIENT_ID") and os.getenv("MS_CLIENT_SECRET"))

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "o365_read_emails",
                    "description": "Legge le ultime email dalla inbox di Microsoft 365/Outlook. Ritorna mittente, oggetto, anteprima e data.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit":  {"type": "integer", "default": 10, "description": "Numero email (max 25)."},
                            "folder": {"type": "string",  "default": "inbox", "description": "Cartella: inbox, sentitems, drafts."},
                            "query":  {"type": "string",  "description": "Filtro testo libero su oggetto/mittente (opzionale)."}
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "o365_send_email",
                    "description": "Invia una email tramite Microsoft 365/Outlook.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to":      {"type": "string", "description": "Destinatario/i (email, separati da virgola)."},
                            "subject": {"type": "string", "description": "Oggetto."},
                            "body":    {"type": "string", "description": "Corpo del messaggio."},
                            "html":    {"type": "boolean", "default": False, "description": "True se il corpo è HTML."}
                        },
                        "required": ["to", "subject", "body"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "o365_list_events",
                    "description": "Elenca gli eventi del calendario Microsoft 365 in un intervallo di date.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_date": {"type": "string", "description": "Data inizio ISO 8601 (es. 2026-07-09)."},
                            "end_date":   {"type": "string", "description": "Data fine ISO 8601."},
                            "limit":      {"type": "integer", "default": 20}
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "o365_create_event",
                    "description": "Crea un evento nel calendario Microsoft 365.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subject":   {"type": "string", "description": "Titolo evento."},
                            "start":     {"type": "string", "description": "Datetime inizio ISO 8601 (es. 2026-07-10T10:00:00)."},
                            "end":       {"type": "string", "description": "Datetime fine ISO 8601."},
                            "location":  {"type": "string", "description": "Luogo (opzionale)."},
                            "body":      {"type": "string", "description": "Descrizione (opzionale)."},
                            "attendees": {"type": "string", "description": "Email partecipanti separati da virgola (opzionale)."}
                        },
                        "required": ["subject", "start", "end"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "o365_list_files",
                    "description": "Elenca file e cartelle su OneDrive for Business.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path":  {"type": "string",  "default": "/", "description": "Percorso cartella OneDrive."},
                            "limit": {"type": "integer", "default": 20}
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "o365_search_files",
                    "description": "Cerca file su OneDrive/SharePoint per nome o contenuto.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string",  "description": "Testo da cercare."},
                            "limit": {"type": "integer", "default": 10}
                        },
                        "required": ["query"]
                    }
                }
            },
        ]

    def execute(self, tool_name: str, args: dict) -> str | None:
        dispatch = {
            "o365_read_emails":  self._read_emails,
            "o365_send_email":   self._send_email,
            "o365_list_events":  self._list_events,
            "o365_create_event": self._create_event,
            "o365_list_files":   self._list_files,
            "o365_search_files": self._search_files,
        }
        fn = dispatch.get(tool_name)
        return fn(args) if fn else None

    # ── MAIL ─────────────────────────────────────────────────────────────────
    def _read_emails(self, args: dict) -> str:
        limit  = min(int(args.get("limit", 10)), 25)
        folder = args.get("folder", "inbox")
        query  = args.get("query", "")
        try:
            account  = _get_account()
            mailbox  = account.mailbox()
            folder_o = mailbox.get_folder(folder_name=folder)
            messages = folder_o.get_messages(limit=limit, query=query or None)
            lines = []
            for m in messages:
                lines.append(
                    f"Da: {m.sender.address} | Oggetto: {m.subject} | "
                    f"Data: {m.received} | Letto: {not m.is_read}\n"
                    f"  {(m.body_preview or '')[:200]}"
                )
            return "\n---\n".join(lines) if lines else "Nessuna email trovata."
        except Exception as e:
            return f"[o365] Errore lettura email: {e}"

    def _send_email(self, args: dict) -> str:
        to      = args.get("to", "")
        subject = args.get("subject", "")
        body    = args.get("body", "")
        is_html = bool(args.get("html", False))
        if not to or not subject:
            return "[o365] 'to' e 'subject' sono obbligatori."
        try:
            account = _get_account()
            message = account.mailbox().new_message()
            for addr in [a.strip() for a in to.split(",") if a.strip()]:
                message.to.add(addr)
            message.subject   = subject
            message.body      = body
            message.body_type = "HTML" if is_html else "Text"
            message.send()
            return f"[o365] Email inviata a {to} | Oggetto: {subject}"
        except Exception as e:
            return f"[o365] Errore invio email: {e}"

    # ── CALENDAR ─────────────────────────────────────────────────────────────
    def _list_events(self, args: dict) -> str:
        from datetime import datetime
        limit      = min(int(args.get("limit", 20)), 50)
        start_date = args.get("start_date") or datetime.now().strftime("%Y-%m-%d")
        end_date   = args.get("end_date", "")
        try:
            account  = _get_account()
            calendar = account.schedule().get_default_calendar()
            q = calendar.new_query("start").greater_equal(datetime.fromisoformat(start_date))
            if end_date:
                q = q.chain("and").on_attribute("end").less_equal(datetime.fromisoformat(end_date))
            events = calendar.get_events(query=q, limit=limit)
            lines  = []
            for ev in events:
                lines.append(
                    f"• {ev.subject}\n"
                    f"  Inizio: {ev.start} | Fine: {ev.end}\n"
                    f"  Luogo: {ev.location or '—'}"
                )
            return "\n".join(lines) if lines else "Nessun evento trovato."
        except Exception as e:
            return f"[o365] Errore lettura calendario: {e}"

    def _create_event(self, args: dict) -> str:
        from datetime import datetime
        subject   = args.get("subject", "")
        start     = args.get("start", "")
        end       = args.get("end", "")
        location  = args.get("location", "")
        body_text = args.get("body", "")
        attendees = args.get("attendees", "")
        if not subject or not start or not end:
            return "[o365] 'subject', 'start', 'end' sono obbligatori."
        try:
            account  = _get_account()
            calendar = account.schedule().get_default_calendar()
            event    = calendar.new_event()
            event.subject = subject
            event.start   = datetime.fromisoformat(start)
            event.end     = datetime.fromisoformat(end)
            if location:  event.location = location
            if body_text: event.body = body_text
            for addr in [a.strip() for a in attendees.split(",") if a.strip()]:
                event.attendees.add(addr)
            event.save()
            return f"[o365] Evento creato: '{subject}' {start} → {end}"
        except Exception as e:
            return f"[o365] Errore creazione evento: {e}"

    # ── ONEDRIVE ─────────────────────────────────────────────────────────────
    def _list_files(self, args: dict) -> str:
        path  = args.get("path", "/")
        limit = min(int(args.get("limit", 20)), 100)
        try:
            account = _get_account()
            drive   = account.storage().get_default_drive()
            folder  = drive.get_root_folder() if path in ("/", "") else drive.get_item_by_path(path)
            items   = list(folder.get_items())[:limit]
            lines   = []
            for item in items:
                kind = "📁" if item.is_folder else "📄"
                size = f"{item.size // 1024} KB" if not item.is_folder else ""
                lines.append(f"{kind} {item.name}  {size}")
            return "\n".join(lines) if lines else "Cartella vuota."
        except Exception as e:
            return f"[o365] Errore lista file: {e}"

    def _search_files(self, args: dict) -> str:
        query = args.get("query", "")
        limit = min(int(args.get("limit", 10)), 50)
        if not query:
            return "[o365] 'query' è obbligatoria."
        try:
            account = _get_account()
            drive   = account.storage().get_default_drive()
            items   = drive.search(query, limit=limit)
            lines   = []
            for item in items:
                lines.append(f"📄 {item.name} | Path: {item.parent_path or 'root'} | {item.size // 1024} KB")
            return "\n".join(lines) if lines else f"Nessun file trovato per '{query}'."
        except Exception as e:
            return f"[o365] Errore ricerca file: {e}"
