# SPDX-License-Identifier: Apache-2.0
"""Instagram API with Instagram Login: account, publishing and messaging."""
from __future__ import annotations

import json
import os
import time
from urllib.parse import urlparse

import requests

from .base import BaseConnector


class InstagramConnector(BaseConnector):
    name = "instagram"
    REQUIRED_ENV = ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID")
    READ_TOOLS = ("instagram_account_status",)
    WRITE_TOOLS = ("instagram_publish_image", "instagram_send_message",
                   "instagram_send_image", "instagram_add_comment")

    def __init__(self):
        self.token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
        self.user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
        version = os.getenv("INSTAGRAM_API_VERSION", "v25.0").strip() or "v25.0"
        self.base = f"https://graph.instagram.com/{version}"
        self.timeout = max(5.0, float(os.getenv("INSTAGRAM_TIMEOUT_S", "20")))
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def get_tools(self) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": "instagram_account_status",
                "description": "Verifica l'account Instagram professionale collegato.",
                "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {
                "name": "instagram_publish_image",
                "description": "Pubblica su Instagram un'immagine HTTPS già approvata.",
                "parameters": {"type": "object", "properties": {
                    "image_url": {"type": "string", "description": "URL HTTPS pubblico del JPEG."},
                    "caption": {"type": "string", "description": "Didascalia, massimo 2200 caratteri."},
                    "alt_text": {"type": "string", "description": "Descrizione accessibile dell'immagine."}},
                    "required": ["image_url", "caption"]}}},
            {"type": "function", "function": {
                "name": "instagram_send_message",
                "description": "Risponde a un utente che ha già scritto all'account Instagram.",
                "parameters": {"type": "object", "properties": {
                    "recipient_id": {"type": "string", "description": "Instagram-scoped ID ricevuto dal webhook."},
                    "text": {"type": "string", "description": "Testo della risposta, massimo 1000 caratteri."}},
                    "required": ["recipient_id", "text"]}}},
            {"type": "function", "function": {
                "name": "instagram_add_comment",
                "description": "Aggiunge un testo o una poesia come commento a un post Instagram.",
                "parameters": {"type": "object", "properties": {
                    "media_id": {"type": "string", "description": "ID del post Instagram."},
                    "text": {"type": "string", "description": "Commento da pubblicare."}},
                    "required": ["media_id", "text"]}}},
            {"type": "function", "function": {
                "name": "instagram_send_image",
                "description": "Invia in DM un'immagine disponibile a un URL HTTPS pubblico.",
                "parameters": {"type": "object", "properties": {
                    "recipient_id": {"type": "string"},
                    "image_url": {"type": "string"}},
                    "required": ["recipient_id", "image_url"]}}},
        ]

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = requests.request(method, f"{self.base}/{path.lstrip('/')}",
                                    headers=self.headers, timeout=self.timeout, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            message = str((data.get("error") or {}).get("message") or response.text)[:300]
            raise RuntimeError(f"Instagram HTTP {response.status_code}: {message}")
        return data

    def _status(self) -> str:
        data = self._request("GET", "me",
                             params={"fields": "id,user_id,username,account_type"})
        return json.dumps({"ok": True, "username": data.get("username"),
                           "account_type": data.get("account_type"),
                           "user_id": data.get("user_id")}, ensure_ascii=False)

    def _publish(self, args: dict) -> str:
        image_url = str(args.get("image_url") or "").strip()
        parsed = urlparse(image_url)
        if parsed.scheme != "https" or not parsed.netloc:
            return "[instagram] image_url deve essere un URL HTTPS pubblico."
        caption = str(args.get("caption") or "").strip()[:2200]
        if not caption:
            return "[instagram] Didascalia vuota: pubblicazione non eseguita."
        payload = {"image_url": image_url, "caption": caption}
        alt_text = str(args.get("alt_text") or "").strip()
        if alt_text:
            payload["alt_text"] = alt_text[:1000]
        container = self._request("POST", f"{self.user_id}/media", data=payload)
        creation_id = str(container.get("id") or "")
        if not creation_id:
            raise RuntimeError("Instagram non ha restituito il container ID")
        for _ in range(10):
            status = self._request("GET", creation_id,
                                   params={"fields": "status_code,status"})
            code = str(status.get("status_code") or "")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"container Instagram {code}: {status.get('status', '')}")
            time.sleep(2)
        else:
            raise RuntimeError("container Instagram non pronto entro 20 secondi")
        published = self._request("POST", f"{self.user_id}/media_publish",
                                  data={"creation_id": creation_id})
        return json.dumps({"ok": True, "media_id": published.get("id"),
                           "creation_id": creation_id}, ensure_ascii=False)

    def _send_message(self, args: dict) -> str:
        recipient_id = str(args.get("recipient_id") or "").strip()
        text = str(args.get("text") or "").strip()[:1000]
        if not recipient_id or not recipient_id.isdigit():
            return "[instagram] recipient_id mancante o non valido."
        if not text:
            return "[instagram] Testo vuoto: messaggio non inviato."
        sent = self._request("POST", f"{self.user_id}/messages", json={
            "recipient": {"id": recipient_id}, "message": {"text": text}})
        return json.dumps({"ok": True, "recipient_id": sent.get("recipient_id"),
                           "message_id": sent.get("message_id")}, ensure_ascii=False)

    def _add_comment(self, args: dict) -> str:
        media_id = str(args.get("media_id") or "").strip()
        text = str(args.get("text") or "").strip()[:2200]
        if not media_id or not media_id.isdigit():
            return "[instagram] media_id mancante o non valido."
        if not text:
            return "[instagram] Testo vuoto: commento non pubblicato."
        comment = self._request("POST", f"{media_id}/comments", data={"message": text})
        return json.dumps({"ok": True, "media_id": media_id,
                           "comment_id": comment.get("id")}, ensure_ascii=False)

    def _send_image(self, args: dict) -> str:
        recipient_id = str(args.get("recipient_id") or "").strip()
        image_url = str(args.get("image_url") or "").strip()
        parsed = urlparse(image_url)
        if not recipient_id.isdigit():
            return "[instagram] recipient_id mancante o non valido."
        if parsed.scheme != "https" or not parsed.netloc:
            return "[instagram] image_url deve essere un URL HTTPS pubblico."
        sent = self._request("POST", f"{self.user_id}/messages", json={
            "recipient": {"id": recipient_id},
            "message": {"attachment": {"type": "image", "payload": {"url": image_url}}},
        })
        return json.dumps({"ok": True, "recipient_id": sent.get("recipient_id"),
                           "message_id": sent.get("message_id")}, ensure_ascii=False)

    def execute(self, tool_name: str, args: dict) -> str | None:
        if tool_name == "instagram_account_status":
            return self._status()
        if tool_name == "instagram_publish_image":
            return self._publish(args or {})
        if tool_name == "instagram_send_message":
            return self._send_message(args or {})
        if tool_name == "instagram_add_comment":
            return self._add_comment(args or {})
        if tool_name == "instagram_send_image":
            return self._send_image(args or {})
        return None
