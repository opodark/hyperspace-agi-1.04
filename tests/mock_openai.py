# SPDX-License-Identifier: Apache-2.0
"""Un backend OpenAI finito, per la baseline di `/v1/chat/completions`.

La rotta chiama Ollama davvero, via HTTP. Senza un modello dietro si fotografano
solo i rami di errore, che sono la parte facile: il percorso felice — i pezzi SSE,
la modalita' think, il passaggio dei tool al client — resterebbe scoperto, ed è
proprio la parte dove un errore non si vede.

Quindi qui c'è un server finto che parla il protocollo OpenAI e risponde a
`/v1/chat/completions`, sia in forma normale sia in forma `stream`. Si avvia
come processo separato e `OLLAMA_URL` punta a lui:

    python3 tests/mock_openai.py 8099 &
    OLLAMA_URL=http://127.0.0.1:8099 ...

Risponde a:
  - `POST /v1/chat/completions` — stream e non stream, con `think` nei `delta`
  - `GET  /v1/models` — un catalogo minimo
  - `POST /api/chat` — la forma nativa, se il backend viene scelto

La risposta dipende dalla RICHIESTA, non dall'ambiente, e questa è la parte che
rende la baseline utile. Una prima versione rispondeva sempre con la stessa
cosa, e la baseline risultava composta da tredici risposte identiche: non
provava niente, perché tutto quello che si voleva osservare — il passaggio dei
tool al client, il `reasoning` che finisce in `content`, il 404 di un modello
inesistente — restava coperto dalla risposta standard.

Quindi il finto guarda cosa gli chiedono:

  - `tools` presente e `tool_choice` diverso da `none`: risponde con
    `tool_calls`, con `index` e `id`. Sono i due campi che, sbagliati,
    fanno concatenare i pezzi nel posto sbagliato e producono una risposta
    coerente e inventata.
  - `chat_template_kwargs.thinking`: mette un `reasoning` nei `delta`, che e'
    esattamente il caso in cui il `reasoning` va rimesso in `content`.
  - un `model` che non e' quello noto: risponde 404, cosi' la rotta prende il
    ramo "modello inesistente" invece di riuscire per caso.
  - altrimenti: il testo in `MOCK_REPLY`.

Il resto dei campi si lascia ai segnali di `MOCK_REPLY` e `MOCK_MODEL`.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = os.environ.get("MOCK_REPLY", "risposta dal modello finto")
THINKING = os.environ.get("MOCK_THINKING", "").strip().lower() == "true"
TOOL = os.environ.get("MOCK_TOOL", "").strip()
# Il tool che il finto riconosce come "chiesto dal client". Vedi la nota
# sotto: se fosse nel catalogo nativo, la richiesta del client e
# catalogo del CP sarebbero indistinguibili.
TOOL_DA_CLIENTE = os.environ.get("MOCK_TOOL_CLIENTE", "tool_di_prova_client")
MODEL = os.environ.get("MOCK_MODEL", "modello-finto")


def _pezzo(delta: dict, finish=None) -> bytes:
    corpo = {"id": "chatcmpl-finto", "object": "chat.completion.chunk",
             "created": int(time.time()), "model": MODEL,
             "choices": [{"index": 0, "delta": delta}]}
    if finish:
        corpo["choices"][0]["finish_reason"] = finish
    return f"data: {json.dumps(corpo)}\n\n".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _json(self, payload: dict, status: int = 200):
        grezzo = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(grezzo)))
        self.end_headers()
        self.wfile.write(grezzo)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"object": "list", "data": [
                {"id": MODEL, "object": "model", "created": 0, "owned_by": "finto"}]})
            return
        self._json({"error": {"message": f"nessuna rotta per {self.path}"}}, 404)

    def do_POST(self):
        lunghezza = int(self.headers.get("Content-Length", "0"))
        try:
            richiesta = json.loads(self.rfile.read(lunghezza) or b"{}")
        except (json.JSONDecodeError, ValueError):
            self._json({"error": {"message": "corpo non JSON"}}, 400)
            return
        # Il modello: se il chiamante ne chiede uno che questo finto non serve,
        # si risponde 404. Serve a coprire il ramo "modello inesistente" della
        # rotta, che con un finto che accetta tutto restava scoperto.
        chiesto = str(richiesta.get("model") or "")
        if MODEL not in chiesto and not chiesto.startswith("⛁"):
            self._json({"error": {"message": f"model '{chiesto}' not found",
                                  "type": "model_not_found"}}, 404)
            return

        # Il control-plane AGGIUNGE il suo catalogo di tool nativi a ogni
        # richiesta. Quindi "la richiesta ha dei tool" è vero sempre, e un finto
        # che rispondesse tool_call su quella base farebbe thirteen risposte
        # identiche — non una baseline. Qui si risponde con tool_call solo se il
        # CLIENT ha chiesto un tool con questo nome: così il percorso dei tool si
        # prova quando il client lo chiede, e le altre richieste restano testo.
        # `TOOL_DA_CLIENTE` è di proposito un nome che il catalogo nativo non
        # contiene, altrimenti la richiesta e il catalogo si confonderebbero.
        richiesti = {str((t.get("function") or {}).get("name") or "")
                     for t in (richiesta.get("tools") or [])}
        wants_tools = TOOL_DA_CLIENTE in richiesti if TOOL_DA_CLIENTE else bool(TOOL)
        # Con `tool_choice: none` i tool vanno ignorati: e' il modo per chiedere
        # una risposta in chiaro senza rinunciare al catalogo.
        scelta = richiesta.get("tool_choice")
        if scelta == "none":
            wants_tools = False
        nome_tool = TOOL or TOOL_DA_CLIENTE or "tool_finto"

        if wants_tools:
            if richiesta.get("stream"):
                self._stream(risposta_tool=True, nome_tool=nome_tool)
            else:
                self._json(self._corpo_tool(nome_tool))
            return
        # Il `reasoning` segue la richiesta, non l'ambiente: è quello che decide
        # se il modello finisce in `content` o in un campo separato.
        thinking = bool((richiesta.get("chat_template_kwargs") or {}).get("thinking")) \
            or THINKING
        if richiesta.get("stream"):
            self._stream(risposta_tool=False, thinking=thinking)
            return
        messaggio = {"role": "assistant", "content": REPLY}
        if thinking:
            messaggio["reasoning"] = "ragionamento del modello finto"
        self._json({"id": "chatcmpl-finto", "object": "chat.completion",
                    "created": int(time.time()), "model": MODEL,
                    "choices": [{"index": 0, "message": messaggio,
                                 "finish_reason": "stop"}]})

    def _corpo_tool(self, nome_tool: str) -> dict:
        return {"id": "chatcmpl-finto", "object": "chat.completion",
                "created": int(time.time()), "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "call_finto", "type": "function", "index": 0,
                                    "function": {"name": nome_tool,
                                                 "arguments": json.dumps({"query": "prova"})}}]}}]}

    def _stream(self, risposta_tool: bool, nome_tool: str = "tool_finto",
                thinking: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            if risposta_tool:
                self.wfile.write(_pezzo({"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "call_finto", "type": "function",
                    "function": {"name": nome_tool, "arguments": ""}}]}))
                self.wfile.write(_pezzo({"tool_calls": [{"index": 0,
                    "function": {"arguments": json.dumps({"query": "prova"})}}]}))
                self.wfile.write(_pezzo({}, finish="tool_calls"))
            else:
                primo = {"role": "assistant", "content": ""}
                if thinking:
                    primo["reasoning"] = ""
                self.wfile.write(_pezzo(primo))
                if thinking:
                    self.wfile.write(_pezzo({"reasoning": "ragionamento del modello finto"}))
                for parola in REPLY.split(" "):
                    self.wfile.write(_pezzo({"content": parola + " "}))
                self.wfile.write(_pezzo({}, finish="stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    porta = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8099
    ThreadingHTTPServer(("127.0.0.1", porta), Handler).serve_forever()