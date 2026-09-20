#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""hs — comandi rapidi dal terminale per lo stack HyperSpace.

Perché esiste: le informazioni utili (canale, identità, sogno, memoria) stavano
solo nella dashboard o nei log, e i comandi del bot solo in chat ("!bot ...").
Da qui si fa la stessa cosa in una riga — e si sa subito *perché* qualcosa non
risponde, invece di indovinare.

Uso:
    python scripts/hs.py status                  # verdetto compatto dello stack
    python scripts/hs.py mode                    # che modo ha il driver, adesso
    python scripts/hs.py mode off                # metti in pausa (il driver lo ritira)
    python scripts/hs.py logs --type channel -n 20
    python scripts/hs.py memory -n 10
    python scripts/hs.py persona
    python scripts/hs.py dreams --status candidate
    python scripts/hs.py dream run               # una riflessione su di sé, adesso
    python scripts/hs.py dream promote <id>
    python scripts/hs.py host                    # host-agent (shell/OS): stato
    python scripts/hs.py models                  # modelli Ollama installati

Legge `.env` per URL e token (canale, revisione dei sogni, admin di rete).
Nessuna dipendenza oltre a `requests`, già richiesto dal control-plane.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 10


def leggi_env(percorso: Path | None = None) -> dict:
    """KEY=VALUE dal .env (virgolette, commenti in coda, righe senza '=')."""
    percorso = percorso or (ROOT / ".env")
    valori: dict[str, str] = {}
    try:
        testo = percorso.read_text(encoding="utf-8")
    except OSError:
        return valori
    for riga in testo.splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, valore = riga.split("=", 1)
        valore = valore.strip().strip('"').strip("'")
        if "  #" in valore:
            valore = valore.split("  #")[0].strip()
        valori[chiave.strip()] = valore
    return valori


def canale_da_clients(clients: str, nome: str = "") -> tuple[str, str]:
    """(nome, token) da CHANNEL_CLIENTS="cam4=<token>;cb=<token>"."""
    voci = []
    for pezzo in str(clients or "").split(";"):
        pezzo = pezzo.strip()
        if "=" not in pezzo:
            continue
        chiave, token = pezzo.split("=", 1)
        voci.append((chiave.strip().lower(), token.strip()))
    if not voci:
        return "", ""
    if nome:
        for chiave, token in voci:
            if chiave == nome.lower():
                return chiave, token
    return voci[0]


class Cli:
    """Trasporto verso il control-plane + accesso ai token del .env."""

    def __init__(self, args):
        self.args = args
        self.env = leggi_env()
        porta = self.env.get("CONTROL_PLANE_PORT") or "8085"
        self.base = (args.url or self.env.get("HS_CLI_URL") or
                     f"http://127.0.0.1:{porta}").rstrip("/")
        self.canale, self.token_canale = canale_da_clients(self.env.get("CHANNEL_CLIENTS", ""),
                                                           args.channel)
        self.token_review = self.env.get("DREAM_REVIEW_TOKEN", "")
        self.token_rete = self.env.get("NETWORK_ADMIN_TOKEN", "")

    # ── trasporto ───────────────────────────────────────────────────────────
    def get(self, percorso: str, **params) -> dict:
        try:
            r = requests.get(f"{self.base}{percorso}", params=params or None, timeout=TIMEOUT)
        except requests.RequestException as e:
            self.muoio(f"control-plane non raggiungibile su {self.base}: {e}\n"
                       "        avvialo con:  .\\scripts\\start.ps1")
        if r.status_code >= 400:
            self.spiega_errore(r)
        return r.json() if r.content else {}

    def post(self, percorso: str, corpo: dict, headers: dict | None = None) -> dict:
        try:
            r = requests.post(f"{self.base}{percorso}", json=corpo, headers=headers or {},
                              timeout=300)
        except requests.RequestException as e:
            self.muoio(f"control-plane non raggiungibile su {self.base}: {e}")
        if r.status_code >= 400:
            self.spiega_errore(r)
        return r.json() if r.content else {}

    def headers_canale(self) -> dict:
        if not self.token_canale:
            self.muoio("nessun canale in CHANNEL_CLIENTS (.env): serve un token per parlare "
                       "col control-plane\n"
                       "        generane uno con:  python scripts/channel_token.py cam4 --write")
        return {"X-Hyperspace-Channel-Token": self.token_canale,
                "Content-Type": "application/json"}

    def headers_review(self) -> dict:
        if len(self.token_review) < 32:
            self.muoio("DREAM_REVIEW_TOKEN assente o troppo corto (.env): la revisione delle "
                       "riflessioni resta chiusa")
        return {"Authorization": f"Bearer {self.token_review}",
                "Content-Type": "application/json"}

    def muoio(self, messaggio: str):
        print(f"[error] {messaggio}", file=sys.stderr)
        raise SystemExit(1)

    def spiega_errore(self, risposta):
        try:
            dettaglio = risposta.json()
        except ValueError:
            dettaglio = risposta.text[:200]
        errore = dettaglio.get("error") if isinstance(dettaglio, dict) else dettaglio
        self.muoio(f"HTTP {risposta.status_code} su {risposta.url}: {errore}")

    def stampa(self, dati) -> None:
        print(json.dumps(dati, ensure_ascii=False, indent=2))


# ── comandi ──────────────────────────────────────────────────────────────────
def cmd_status(cli: Cli) -> None:
    salute = cli.get("/health")
    canali = cli.get("/channel/status")
    persona = cli.get("/persona")
    memoria = cli.get("/memory", limit=1)
    if cli.args.json:
        cli.stampa({"health": salute, "channels": canali, "persona": persona, "memory": memoria})
        return
    stato = salute.get("status") or salute.get("ok") or "?"
    print(f"control-plane    {cli.base}  [{stato}]")
    politica = canali.get("policy", {})
    print(f"canali           {', '.join(politica.get('channels') or []) or 'nessuno'}  "
          f"(abilitati={politica.get('enabled')}, modello={canali.get('model')})")
    contesto = canali.get("context") or {}
    print(f"contesto         {contesto.get('messages')} messaggi x {contesto.get('chars')} char, "
          f"num_ctx={contesto.get('num_ctx')}")
    guardia = canali.get("guard") or {}
    print(f"moderazione      autori seguiti={guardia.get('authors_tracked')} "
          f"spam attivi={guardia.get('spam_authors_active')} "
          f"(mute>={guardia.get('strike_mute')} ban>={guardia.get('strike_ban')})")
    runtime = canali.get("runtime") or {}
    for nome, dati in runtime.items():
        stato_canale = dati.get("state") or {}
        vecchio = " (dato vecchio)" if stato_canale.get("stale") else ""
        print(f"driver {nome:<9} modo={stato_canale.get('mode', '?')} "
              f"{'ON' if stato_canale.get('active') else 'OFF'} "
              f"{stato_canale.get('rate', 0):.0f} msg/min  "
              f"visto {stato_canale.get('age_s')}s fa{vecchio}  "
              f"comandi in coda={dati.get('pending_commands', 0)}")
    if not runtime:
        print("driver           nessuno stato ricevuto (driver fermo, o avviato senza "
              "CHANNEL_TOKEN)")
    print(f"identità         {persona.get('name')} ({persona.get('kind')}) "
          f"attiva={persona.get('enabled')} annotazioni={persona.get('observation_count')}"
          f"/{persona.get('max_observations')}")
    sogno = persona.get("dream") or {}
    print(f"sogno            {'attivo' if sogno.get('enabled') else 'spento'} "
          f"finestra={sogno.get('window')} in attesa={sogno.get('pending_review')} "
          f"ultimo={sogno.get('last_status', 'mai')}")
    print(f"memoria          {memoria.get('total', 0)} voci")


def cmd_channels(cli: Cli) -> None:
    dati = cli.get("/channel/status")
    if cli.args.json:
        cli.stampa(dati)
        return
    politica = dati.get("policy", {})
    print(f"canali      {', '.join(politica.get('channels') or []) or 'nessuno'}")
    for problema in politica.get("problems") or []:
        print(f"  problema  {problema}")
    print(f"modello     {dati.get('model')}  max_tokens={dati.get('max_tokens')}")
    contesto = dati.get("context") or {}
    print(f"contesto    {contesto.get('messages')} x {contesto.get('chars')} char, "
          f"num_ctx={contesto.get('num_ctx')}")
    for nome, onda in (dati.get("waves") or {}).items():
        if onda:
            print(f"ondata {nome}  {onda.get('messages')} messaggi da {onda.get('authors')} autori")
    for nome, dati_canale in (dati.get("runtime") or {}).items():
        print(f"driver {nome}  {json.dumps(dati_canale, ensure_ascii=False)}")
    print(f"comandi     {', '.join(dati.get('commands_available') or [])}")


def cmd_mode(cli: Cli) -> None:
    azione = (cli.args.azione or "").strip().lower()
    canali = cli.get("/channel/status")
    runtime = (canali.get("runtime") or {}).get(cli.canale) or {}
    stato = runtime.get("state") or {}
    if not azione:
        if cli.args.json:
            cli.stampa({"channel": cli.canale, "runtime": runtime})
            return
        if not stato:
            print(f"canale {cli.canale}: il driver non ha ancora riportato il suo stato "
                  f"(fermo, o senza CHANNEL_TOKEN)")
        else:
            print(f"canale {cli.canale}: modo={stato.get('mode')} "
                  f"{'ON' if stato.get('active') else 'OFF'} "
                  f"{stato.get('rate', 0):.0f} msg/min  (visto {stato.get('age_s')}s fa)")
        if runtime.get("pending_commands"):
            print(f"  comandi in attesa di essere ritirati: {runtime['pending_commands']}")
        print(f"  disponibili: {', '.join(canali.get('commands_available') or [])}")
        return
    esito = cli.post("/channel/commands", {"command": azione, "source": "hs-cli"},
                     headers=cli.headers_canale())
    comando = esito.get("queued") or {}
    print(f"accodato: {comando.get('command')} (id={comando.get('id')}, canale={cli.canale})")
    print("il driver lo ritira al prossimo giro (entro pochi secondi). "
          "Verifica con:  python scripts/hs.py mode")


def cmd_logs(cli: Cli) -> None:
    filtro = cli.args.type or cli.args.status or cli.args.query or ""
    dati = cli.get("/logs", type=cli.args.type or "", status=cli.args.status or "",
                   q=cli.args.query or "", page=1, per_page=max(1, cli.args.number))
    righe = dati.get("logs") or []
    if cli.args.json:
        cli.stampa(dati)
        return
    for riga in reversed(righe):
        print(f"{str(riga.get('ts', ''))[11:19]}  {str(riga.get('type', '')):<12} "
              f"{str(riga.get('status', '')):<7} {str(riga.get('summary', ''))[:88]}")
        dettaglio = str(riga.get("detail", "") or "").strip()
        if dettaglio and cli.args.verbose:
            print(f"          {dettaglio[:160]}")
    print(f"({len(righe)} di {dati.get('total', 0)} righe"
          + (f", filtro: {filtro}" if filtro else "") + ")")


def cmd_memory(cli: Cli) -> None:
    dati = cli.get("/memory", limit=max(1, cli.args.number))
    if cli.args.json:
        cli.stampa(dati)
        return
    for voce in (dati.get("entries") or [])[-cli.args.number:]:
        print(f"{str(voce.get('ts', ''))[:16]}  {str(voce.get('kind', '')):<12} "
              f"{str(voce.get('content', ''))[:100]}")
    print(f"(totale in memoria: {dati.get('total', 0)})")


def cmd_persona(cli: Cli) -> None:
    persona = cli.get("/persona")
    if cli.args.json:
        cli.stampa(persona)
        return
    print(f"{persona.get('name')} ({persona.get('kind')})  file={persona.get('file')}")
    print(f"attiva={persona.get('enabled')}  versione={persona.get('version')}  "
          f"annotazioni={persona.get('observation_count')}/{persona.get('max_observations')}")
    for voce in persona.get("boundaries") or []:
        print(f"  confine   {str(voce)[:110]}")
    for nota in (persona.get("observations") or [])[-cli.args.number:]:
        print(f"  annotato  {str(nota.get('ts', ''))[:16]} [{nota.get('kind')}] "
              f"{str(nota.get('text', ''))[:100]}")
    for problema in persona.get("problems") or []:
        print(f"  problema  {problema}")


def cmd_dreams(cli: Cli) -> None:
    dati = cli.get("/persona/dreams", status=cli.args.status or "")
    if cli.args.json:
        cli.stampa(dati)
        return
    sogno = dati.get("dream") or {}
    print(f"sogno {'attivo' if sogno.get('enabled') else 'spento'} "
          f"finestra={sogno.get('window')} in attesa={sogno.get('pending_review')} "
          f"ultimo={sogno.get('last_status', 'mai')}")
    for riga in dati.get("dreams") or []:
        print(f"\n{riga.get('id')}  {str(riga.get('created_at', ''))[:16]}  "
              f"[{riga.get('status')}]  materiale={json.dumps(riga.get('material') or {})}")
        for proposta in riga.get("proposals") or []:
            print(f"    proponi  [{proposta.get('kind')}] {proposta.get('text')}")
        for scarto in riga.get("discarded") or []:
            print(f"    scarto   {scarto.get('reason')}: {str(scarto.get('text'))[:80]}")


def cmd_dream(cli: Cli) -> None:
    azione = cli.args.azione
    if azione == "run":
        esito = cli.post("/persona/dream", {}, headers=cli.headers_review())
        report = esito.get("report") or {}
        print(f"riflessione: {report.get('status')}  "
              f"materiale={json.dumps(report.get('material') or {})}")
        for proposta in report.get("proposals") or []:
            print(f"  proponi  [{proposta.get('kind')}] {proposta.get('text')}")
        for scarto in report.get("discarded") or []:
            print(f"  scarto   {scarto.get('reason')}: {str(scarto.get('text'))[:80]}")
        if report.get("error"):
            print(f"  errore   {report['error']}")
        return
    if not cli.args.id:
        cli.muoio("serve l'id della riflessione: hs dream promote|reject <id>")
    esito = cli.post(f"/persona/dreams/{cli.args.id}/review",
                     {"action": azione, "reviewer": "hs-cli", "rationale": cli.args.note or ""},
                     headers=cli.headers_review())
    print(f"{esito.get('status')}: promosse={esito.get('promoted')} "
          f"scartate={esito.get('skipped')} versione identità={esito.get('identity_version')}")


def cmd_host(cli: Cli) -> None:
    """Stato dell'host-agent: la superficie con cui il CP tocca la macchina."""
    if not cli.token_rete:
        print("host-agent: non configurato (nessun NETWORK_ADMIN_TOKEN nel .env).\n"
              "  Per abilitare l'accesso controllato a shell/OS dall'agente:\n"
              "    1) python hostctl/agent.py --generate-token   # scrive i token nel .env\n"
              "    2) avvia l'agente sull'host:  python hostctl/agent.py\n"
              "    3) riavvia il control-plane:  .\\scripts\\start.ps1 -NoBuild\n"
              "  Cosa è permesso e cosa no: docs/host-access.md")
        return
    try:
        r = requests.get(f"{cli.base}/network/status",
                         headers={"X-Hyperspace-Network-Token": cli.token_rete}, timeout=TIMEOUT)
    except requests.RequestException as e:
        cli.muoio(f"control-plane non raggiungibile: {e}")
    if r.status_code >= 400:
        cli.spiega_errore(r)
        return
    cli.stampa(r.json())


def cmd_models(cli: Cli) -> None:
    base = (cli.args.ollama or os.getenv("OLLAMA_HOST")
            or "http://127.0.0.1:11434").rstrip("/")
    try:
        r = requests.get(f"{base}/api/tags", timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        cli.muoio(f"Ollama non raggiungibile su {base}: {e}")
    modelli = r.json().get("models") or []
    if cli.args.json:
        cli.stampa({"ollama": base, "models": modelli})
        return
    print(f"Ollama {base}: {len(modelli)} modelli")
    for modello in sorted(modelli, key=lambda x: -(x.get("size") or 0)):
        print(f"  {(modello.get('size') or 0) / 1024 ** 3:6.1f} GB  {modello.get('name')}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hs",
        description="Comandi rapidi per lo stack HyperSpace: control-plane, canale, "
                    "identità, sogno.",
        epilog="esempi:\n"
               "  python scripts/hs.py status\n"
               "  python scripts/hs.py mode off        # il bot smette di scrivere in chat\n"
               "  python scripts/hs.py mode auto\n"
               "  python scripts/hs.py logs --type channel -n 20\n"
               "  python scripts/hs.py dream run\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="", help="base del control-plane (default 127.0.0.1:8085)")
    parser.add_argument("--channel", default="", help="canale (default: il primo in CHANNEL_CLIENTS)")
    parser.add_argument("--json", action="store_true", help="stampa JSON invece del riassunto")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("status", help="verdetto compatto: CP, canali, driver, identità, sogno, memoria")
    sub.add_parser("channels", help="stato completo dei canali (guardia, onde, driver, comandi)")

    modo = sub.add_parser("mode", help="modo del driver: leggi, oppure accoda on|off|auto|status")
    modo.add_argument("azione", nargs="?", default="",
                      choices=["", "on", "off", "auto", "status"])

    log = sub.add_parser("logs", help="ultime righe di log dal control-plane")
    log.add_argument("--type", default="", help="filtra per tipo (channel, system, dream, ...)")
    log.add_argument("--status", default="", help="filtra per stato (success, warn, error, info)")
    log.add_argument("-q", "--query", default="", help="ricerca nel testo")
    log.add_argument("-n", "--number", type=int, default=20, help="quante righe (default 20)")
    log.add_argument("-v", "--verbose", action="store_true", help="mostra anche il dettaglio")

    memoria = sub.add_parser("memory", help="cosa l'agente ricorda (memoria a lungo termine)")
    memoria.add_argument("-n", "--number", type=int, default=10, help="quante voci (default 10)")

    persona = sub.add_parser("persona", help="identità dichiarata, confini e annotazioni")
    persona.add_argument("-n", "--number", type=int, default=5, help="quante annotazioni (default 5)")

    sogni = sub.add_parser("dreams", help="riflessioni su di sé: proposte e scarti")
    sogni.add_argument("--status", default="", help="candidate | promoted | rejected | empty")

    sogno = sub.add_parser("dream", help="avvia una riflessione o promuovi/rifiuta una proposta")
    sogno.add_argument("azione", choices=["run", "promote", "reject"])
    sogno.add_argument("id", nargs="?", default="", help="id della riflessione (promote/reject)")
    sogno.add_argument("--note", default="", help="motivazione della revisione")

    sub.add_parser("host", help="host-agent: accesso controllato a shell/OS (stato)")

    modelli = sub.add_parser("models", help="modelli Ollama installati sulla macchina")
    modelli.add_argument("--ollama", default="", help="base di Ollama (default 127.0.0.1:11434)")

    args = parser.parse_args(argv)
    cli = Cli(args)
    if not args.channel:
        args.channel = cli.canale or "cam4"
    cli.canale = args.channel
    comandi = {"status": cmd_status, "channels": cmd_channels, "mode": cmd_mode,
               "logs": cmd_logs, "memory": cmd_memory, "persona": cmd_persona,
               "dreams": cmd_dreams, "dream": cmd_dream, "host": cmd_host,
               "models": cmd_models}
    comandi[args.comando](cli)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
