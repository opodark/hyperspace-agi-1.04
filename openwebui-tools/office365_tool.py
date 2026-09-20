# SPDX-License-Identifier: Apache-2.0
"""
title: HyperSpace Office 365
description: Email, calendario e OneDrive (Microsoft 365) via il control-plane di HyperSpace AGI.
required_open_webui_version: 0.4.0
version: 0.1.0
"""

# Import in Open WebUI: Admin Panel -> Workspace -> Tools -> "+" -> incolla
# questo file. Poi abilitalo sul modello/chat che vuoi (icona strumenti nella
# barra dei messaggi, o come default del modello in Workspace -> Models).
#
# Non reimplementa nulla del connettore Office365 (auth MS Graph, O365 SDK,
# ecc.): fa solo da ponte HTTP verso /tools/execute sul control-plane, che
# dispatcha a connectors/office365.py — stessa identica logica gia' usata
# quando e' il modello a decidere di chiamare questi tool durante una chat.
# Richiede MS_CLIENT_ID/MS_CLIENT_SECRET impostati nel .env del control-plane,
# altrimenti il connettore risponde "non disponibile" (vedi is_available()).
#
# DUE cose da configurare in questo tool (Valves):
#   1. network_token = NETWORK_ADMIN_TOKEN del control-plane. /tools/execute
#      esegue qualunque tool pubblicato, quindi sta dietro lo stesso gate delle
#      azioni di rete (header X-Hyperspace-Network-Token). Senza: 503 se il
#      control-plane non ha il token, 401 se non combacia.
#   2. I tool di SCRITTURA (invia_email, crea_evento) sono bloccati di default
#      dalla policy read-only del control-plane: per usarli servono
#      CONNECTOR_READ_ONLY=false e CONNECTOR_WRITE_TOOLS="office365=*" (o i soli
#      nomi dei tool). Vedi docs/connectors.md.

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        control_plane_url: str = Field(
            default="http://control-plane:8085",
            description="URL del control-plane HyperSpace AGI raggiungibile dal container Open WebUI (stessa rete Docker: http://control-plane:8085).",
        )
        timeout_s: int = Field(
            default=30,
            description="Timeout in secondi per le chiamate al control-plane.",
        )
        network_token: str = Field(
            default="",
            description="NETWORK_ADMIN_TOKEN del control-plane (header X-Hyperspace-Network-Token). Obbligatorio: /tools/execute e' protetto dallo stesso gate delle azioni di rete.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _headers(self) -> dict:
        token = (self.valves.network_token or "").strip()
        return {"X-Hyperspace-Network-Token": token} if token else {}

    def _call(self, tool_name: str, **args) -> str:
        try:
            r = requests.post(
                f"{self.valves.control_plane_url.rstrip('/')}/tools/execute",
                json={"tool_name": tool_name, "args": args},
                headers=self._headers(),
                timeout=self.valves.timeout_s,
            )
            if r.status_code in (401, 503):
                return ("Control-plane: /tools/execute non autorizzato "
                        f"(HTTP {r.status_code}). Compila il campo 'network_token' di questo "
                        "tool con lo stesso NETWORK_ADMIN_TOKEN del .env del control-plane "
                        "(almeno 32 caratteri).")
            r.raise_for_status()
            return r.json().get("result", "") or "(nessun risultato)"
        except Exception as e:
            return f"Errore chiamando {tool_name} sul control-plane: {e}"

    def leggi_email(self, limit: int = 10, folder: str = "inbox", query: str = "") -> str:
        """
        Legge le ultime email dalla inbox Microsoft 365/Outlook.
        :param limit: numero di email da leggere (max 25)
        :param folder: cartella: inbox, sentitems, drafts
        :param query: filtro testo libero su oggetto/mittente (opzionale)
        """
        return self._call("o365_read_emails", limit=limit, folder=folder, query=query)

    def invia_email(self, to: str, subject: str, body: str, html: bool = False) -> str:
        """
        Invia una email tramite Microsoft 365/Outlook.
        :param to: destinatario/i, email separati da virgola
        :param subject: oggetto della email
        :param body: corpo del messaggio
        :param html: True se il corpo e' HTML invece che testo semplice
        """
        return self._call("o365_send_email", to=to, subject=subject, body=body, html=html)

    def elenca_eventi(self, start_date: str = "", end_date: str = "", limit: int = 20) -> str:
        """
        Elenca gli eventi del calendario Microsoft 365 in un intervallo di date.
        :param start_date: data inizio ISO 8601 (es. 2026-07-22), vuoto = oggi
        :param end_date: data fine ISO 8601 (opzionale)
        :param limit: numero massimo di eventi
        """
        return self._call("o365_list_events", start_date=start_date, end_date=end_date, limit=limit)

    def crea_evento(
        self,
        subject: str,
        start: str,
        end: str,
        location: str = "",
        body: str = "",
        attendees: str = "",
    ) -> str:
        """
        Crea un evento nel calendario Microsoft 365.
        :param subject: titolo dell'evento
        :param start: datetime inizio ISO 8601 (es. 2026-07-22T10:00:00)
        :param end: datetime fine ISO 8601
        :param location: luogo (opzionale)
        :param body: descrizione (opzionale)
        :param attendees: email dei partecipanti separati da virgola (opzionale)
        """
        return self._call(
            "o365_create_event",
            subject=subject,
            start=start,
            end=end,
            location=location,
            body=body,
            attendees=attendees,
        )

    def elenca_file(self, path: str = "/", limit: int = 20) -> str:
        """
        Elenca file e cartelle su OneDrive for Business.
        :param path: percorso cartella OneDrive
        :param limit: numero massimo di elementi
        """
        return self._call("o365_list_files", path=path, limit=limit)

    def cerca_file(self, query: str, limit: int = 10) -> str:
        """
        Cerca file su OneDrive/SharePoint per nome o contenuto.
        :param query: testo da cercare
        :param limit: numero massimo di risultati
        """
        return self._call("o365_search_files", query=query, limit=limit)
