"""
connectors/google.py — Google Workspace per HyperSpace-AGI v1.03
Libreria: google-api-python-client>=2.120.0
         google-auth-httplib2>=0.2.0
         google-auth-oauthlib>=1.2.0

Env vars:
  GOOGLE_CREDENTIALS_JSON   Contenuto JSON del service account (escaped su una riga)
                            Scarica da: https://console.cloud.google.com
                            → IAM & Admin → Service Accounts → Keys → Add Key → JSON
  GOOGLE_DELEGATE_EMAIL     Email utente da impersonare (richiede domain-wide delegation)

API abilitate nel progetto GCP:
  Gmail API, Google Calendar API, Google Drive API
"""
from __future__ import annotations
import os
import json
from .base import BaseConnector


def _build_service(api: str, version: str):
    """Crea un servizio Google API autenticato via service account."""
    import google.auth
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    info       = json.loads(creds_json)
    delegate   = os.getenv("GOOGLE_DELEGATE_EMAIL", "")

    scopes = {
        "gmail":    ["https://www.googleapis.com/auth/gmail.readonly",
                     "https://www.googleapis.com/auth/gmail.send"],
        "calendar": ["https://www.googleapis.com/auth/calendar"],
        "drive":    ["https://www.googleapis.com/auth/drive.readonly"],
    }
    scope_list = scopes.get(api, [])

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=scope_list
    )
    if delegate:
        credentials = credentials.with_subject(delegate)

    return build(api, version, credentials=credentials, cache_discovery=False)


class GoogleWorkspaceConnector(BaseConnector):
    name = "google"

    @property
    def enabled(self) -> bool:
        return bool(os.getenv("GOOGLE_CREDENTIALS_JSON"))

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "google_read_emails",
                    "description": "Legge le ultime email da Gmail. Ritorna mittente, oggetto, data e snippet.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit":  {"type": "integer", "default": 10, "description": "Numero email (max 25)."},
                            "query":  {"type": "string",  "description": "Query Gmail (es. 'is:unread from:boss@company.com')."}
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "google_send_email",
                    "description": "Invia una email tramite Gmail.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to":      {"type": "string", "description": "Destinatario email."},
                            "subject": {"type": "string", "description": "Oggetto."},
                            "body":    {"type": "string", "description": "Corpo del messaggio (testo plain)."}
                        },
                        "required": ["to", "subject", "body"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "google_list_events",
                    "description": "Elenca eventi da Google Calendar in un intervallo di date.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_date": {"type": "string", "description": "Data inizio ISO 8601 (es. 2026-07-09)."},
                            "end_date":   {"type": "string", "description": "Data fine ISO 8601."},
                            "limit":      {"type": "integer", "default": 20},
                            "calendar_id":{"type": "string",  "default": "primary", "description": "ID calendario (default: primary)."}
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "google_create_event",
                    "description": "Crea un evento su Google Calendar.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary":     {"type": "string", "description": "Titolo evento."},
                            "start":       {"type": "string", "description": "Datetime inizio ISO 8601."},
                            "end":         {"type": "string", "description": "Datetime fine ISO 8601."},
                            "description": {"type": "string", "description": "Descrizione (opzionale)."},
                            "location":    {"type": "string", "description": "Luogo (opzionale)."},
                            "attendees":   {"type": "string", "description": "Email partecipanti separati da virgola."},
                            "calendar_id": {"type": "string",  "default": "primary"}
                        },
                        "required": ["summary", "start", "end"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "google_list_drive_files",
                    "description": "Elenca file su Google Drive.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string",  "description": "Query Drive (es. \"mimeType='application/pdf'\"). Vuoto = tutti."},
                            "limit": {"type": "integer", "default": 20}
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "google_search_drive",
                    "description": "Cerca file su Google Drive per nome o contenuto.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string",  "description": "Testo da cercare nel nome o full-text."},
                            "limit": {"type": "integer", "default": 10}
                        },
                        "required": ["query"]
                    }
                }
            },
        ]

    def execute(self, tool_name: str, args: dict) -> str | None:
        dispatch = {
            "google_read_emails":    self._read_emails,
            "google_send_email":     self._send_email,
            "google_list_events":    self._list_events,
            "google_create_event":   self._create_event,
            "google_list_drive_files": self._list_drive_files,
            "google_search_drive":   self._search_drive,
        }
        fn = dispatch.get(tool_name)
        return fn(args) if fn else None

    # ── GMAIL ─────────────────────────────────────────────────────────────────
    def _read_emails(self, args: dict) -> str:
        limit = min(int(args.get("limit", 10)), 25)
        query = args.get("query", "")
        try:
            service  = _build_service("gmail", "v1")
            result   = service.users().messages().list(
                userId="me", q=query, maxResults=limit
            ).execute()
            messages = result.get("messages", [])
            if not messages:
                return "Nessuna email trovata."
            lines = []
            for msg in messages:
                m       = service.users().messages().get(
                    userId="me", id=msg["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"]
                ).execute()
                headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
                snippet = m.get("snippet", "")[:200]
                lines.append(
                    f"Da: {headers.get('From', '—')} | Oggetto: {headers.get('Subject', '—')} | "
                    f"Data: {headers.get('Date', '—')}\n  {snippet}"
                )
            return "\n---\n".join(lines)
        except Exception as e:
            return f"[google] Errore lettura email: {e}"

    def _send_email(self, args: dict) -> str:
        import base64
        from email.mime.text import MIMEText
        to      = args.get("to", "")
        subject = args.get("subject", "")
        body    = args.get("body", "")
        if not to or not subject:
            return "[google] 'to' e 'subject' sono obbligatori."
        try:
            message = MIMEText(body)
            message["to"]      = to
            message["subject"] = subject
            raw     = base64.urlsafe_b64encode(message.as_bytes()).decode()
            service = _build_service("gmail", "v1")
            sent    = service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            return f"[google] Email inviata a {to} | ID: {sent.get('id')}"
        except Exception as e:
            return f"[google] Errore invio email: {e}"

    # ── CALENDAR ─────────────────────────────────────────────────────────────
    def _list_events(self, args: dict) -> str:
        from datetime import datetime, timezone
        limit       = min(int(args.get("limit", 20)), 50)
        calendar_id = args.get("calendar_id", "primary")
        start_date  = args.get("start_date") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_date    = args.get("end_date", "")
        try:
            service = _build_service("calendar", "v3")
            params  = {
                "calendarId": calendar_id,
                "timeMin": start_date if "T" in start_date else f"{start_date}T00:00:00Z",
                "maxResults": limit,
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if end_date:
                params["timeMax"] = end_date if "T" in end_date else f"{end_date}T23:59:59Z"
            events = service.events().list(**params).execute().get("items", [])
            lines  = []
            for ev in events:
                start = ev["start"].get("dateTime", ev["start"].get("date", ""))
                end   = ev["end"].get("dateTime",   ev["end"].get("date", ""))
                lines.append(f"• {ev.get('summary', '(senza titolo)')}\n  {start} → {end}  | {ev.get('location', '')}")
            return "\n".join(lines) if lines else "Nessun evento trovato."
        except Exception as e:
            return f"[google] Errore lettura calendario: {e}"

    def _create_event(self, args: dict) -> str:
        summary     = args.get("summary", "")
        start       = args.get("start", "")
        end         = args.get("end", "")
        description = args.get("description", "")
        location    = args.get("location", "")
        attendees   = args.get("attendees", "")
        calendar_id = args.get("calendar_id", "primary")
        if not summary or not start or not end:
            return "[google] 'summary', 'start', 'end' sono obbligatori."
        try:
            def _dt(s): return {"dateTime": s, "timeZone": "UTC"} if "T" in s else {"date": s}
            event = {
                "summary":  summary,
                "start":    _dt(start),
                "end":      _dt(end),
            }
            if description: event["description"] = description
            if location:    event["location"]    = location
            if attendees:
                event["attendees"] = [{"email": a.strip()} for a in attendees.split(",") if a.strip()]
            service  = _build_service("calendar", "v3")
            created  = service.events().insert(calendarId=calendar_id, body=event).execute()
            return f"[google] Evento creato: '{summary}' | {created.get('htmlLink')}"
        except Exception as e:
            return f"[google] Errore creazione evento: {e}"

    # ── DRIVE ─────────────────────────────────────────────────────────────────
    def _list_drive_files(self, args: dict) -> str:
        query = args.get("query", "")
        limit = min(int(args.get("limit", 20)), 100)
        try:
            service = _build_service("drive", "v3")
            params  = {
                "pageSize": limit,
                "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink)",
            }
            if query:
                params["q"] = query
            files = service.files().list(**params).execute().get("files", [])
            lines = []
            for f in files:
                size = f"{int(f.get('size', 0)) // 1024} KB" if f.get("size") else "—"
                lines.append(f"📄 {f['name']} | {f['mimeType'].split('.')[-1]} | {size} | {f.get('modifiedTime', '')[:10]}")
            return "\n".join(lines) if lines else "Nessun file trovato."
        except Exception as e:
            return f"[google] Errore lista Drive: {e}"

    def _search_drive(self, args: dict) -> str:
        query = args.get("query", "")
        limit = min(int(args.get("limit", 10)), 50)
        if not query:
            return "[google] 'query' è obbligatoria."
        try:
            service = _build_service("drive", "v3")
            q       = f"name contains '{query}' or fullText contains '{query}'"
            files   = service.files().list(
                q=q, pageSize=limit,
                fields="files(id,name,mimeType,size,modifiedTime,webViewLink)"
            ).execute().get("files", [])
            lines   = []
            for f in files:
                size = f"{int(f.get('size', 0)) // 1024} KB" if f.get("size") else "—"
                lines.append(f"📄 {f['name']} | {size} | {f.get('webViewLink', '—')}")
            return "\n".join(lines) if lines else f"Nessun file trovato per '{query}'."
        except Exception as e:
            return f"[google] Errore ricerca Drive: {e}"
